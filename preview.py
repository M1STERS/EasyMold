# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Live preview.

Preview: an overlay drawn in the viewport (nothing is added to the scene) with the outline of the mold, the split
planes or the curved parting surface, keys, bolts, pour holes and vents where the build would put them, and in red
the pieces that don't fit the print bed. It follows every change of the settings, of the object and of the layout
markers.

With an edited layout, its markers (keys, bolts, pour holes, vents) are always drawn with their real shape, also
with Preview off: the selected one brighter, and a marker being moved or scaled carries its shape along.

Auto Preview: shortly after the last change, the real mold is built at a coarse resolution into its own collection
(EasyMold_<object>_Preview), one step at a time so Blender stays responsive; a new change restarts it.

Both are off by default. A timer polls a cheap signature of everything they depend on.
"""
import atexit
import time
import traceback

import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

from . import utils

TICK = 0.1           # seconds between checks
PLAN_DELAY = 0.15    # the overlay waits this long after the last change
BUILD_DELAY = 0.7    # Auto Preview waits this long after the last change
COARSE_STEPS = 80    # Auto Preview resolution: object size / this (or the Resolution setting if coarser)
MAX_FACES = 100000   # Auto Preview is not built for objects with more faces than this

# Settings that change nothing the previews show.
IGNORED = {"rna_type", "export_format", "export_folder", "export_master", "silicone_density", "mix_a", "mix_b",
           "mix_by", "silicone_price", "waste", "cast_density", "cast_price"}


# kind: (RGBA, line width)
STYLE = {
    'OUTLINE': ((0.30, 0.65, 1.00, 0.9), 2.0),
    'SPLIT': ((1.00, 0.80, 0.25, 0.8), 1.5),
    'KEY': ((0.35, 0.90, 0.45, 1.0), 2.0),
    'BOLT': ((0.80, 0.80, 0.85, 1.0), 2.0),
    'POUR': ((1.00, 0.45, 0.20, 1.0), 2.0),
    'VENT': ((0.30, 0.90, 0.95, 1.0), 1.5),
    'BLOCKER': ((0.70, 0.50, 1.00, 1.0), 1.5),
    'MISFIT': ((1.00, 0.15, 0.15, 1.0), 3.0),
    'UNUSED': ((1.00, 0.25, 0.25, 0.5), 1.0),  # a layout marker the mold won't use
}
FILL = {'OUTLINE': (0.30, 0.65, 1.00, 0.12), 'MASTER': (0.95, 0.95, 0.90, 0.35)}


class _State:
    draw_handle = None
    timer_on = False
    signature = None
    prepared_key = None   # cache key of the overlay's prepared working copy
    watched = set()       # names of the objects and meshes the previews depend on
    epoch = 0             # counts edits of their geometry
    changed_at = None     # when the signature last changed
    plan_due = False      # the overlay must be recomputed
    build_due = False     # the Auto Preview must be rebuilt
    plan = None           # MoldBuilder.plan_shapes() shown by the overlay
    batches = None        # its GPU batches, made when first drawn: ([(batch, color, width)], [(batch, color)],
                          #   [(marker name, origin, radius, [(batch, color, width)])])
    markers_only = False  # the plan was made for the layout markers alone (Preview off)
    summary = ""
    misfit = []
    error = ""            # why the overlay or the Auto Preview can't be shown
    job = None            # running Auto Preview: (builder, steps, signature)
    progress = 0.0
    status = ""


# --- what the previews depend on -----------------------------------------------

def _value(props, name):
    value = getattr(props, name)
    if isinstance(value, bpy.types.ID):
        return value.name
    return tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value


def _object_print(obj):
    """Where an object is and how big (an edit of its mesh is counted by _on_depsgraph instead)."""
    out = [obj.name, tuple(round(c, 6) for row in obj.matrix_world for c in row)]
    if obj.type == 'EMPTY':
        out.append(round(obj.empty_display_size, 6))
    elif obj.type == 'MESH':
        out += [obj.data.name, len(obj.data.vertices), len(obj.data.polygons)]
    return tuple(out)


def _context_objects(context):
    view_layer = context.view_layer
    active = view_layer.objects.active
    return active, [o for o in view_layer.objects.selected if o is not None]


def _signature(context):
    """Everything the previews show depends on, and (second) everything the prepared working copy depends on,
    or (None, None) without a source object."""
    active, selected = _context_objects(context)
    source = utils.resolve_source(active)
    if source is None:
        _State.watched = set()
        return None, None
    props = context.scene.easymold_props
    extras = utils.tray_members(source, selected, props)
    shaped = [source] + extras
    layout = utils.find_layout(source)
    markers = []
    if layout is not None:
        objects = sorted(layout.objects, key=lambda o: o.name)
        shaped += [o for o in objects if o.type == 'MESH']
        markers = [o for o in objects if o.type != 'MESH']
    others = [props.core_object] if props.core_object is not None else []
    _State.watched = {o.name for o in shaped + others} | {o.data.name for o in shaped + others if o.type == 'MESH'}
    values = tuple(_value(props, name) for name in props.bl_rna.properties.keys() if name not in IGNORED)
    shapes = tuple(_object_print(o) for o in shaped)
    # The overlay keeps its prepared copy while these settings and the objects stay the same.
    names = utils.PREPARE + (utils.PREPARE_TRAY if extras else ())
    prepared = (source.name, tuple(_value(props, name) for name in names), shapes, _State.epoch)
    return (values, shapes, tuple(_object_print(o) for o in markers + others), _State.epoch), prepared


def _source(context):
    active, selected = _context_objects(context)
    source = utils.resolve_source(active)
    if source is None:
        return None, []
    return source, utils.tray_members(source, selected, context.scene.easymold_props)


# --- overlay ---------------------------------------------------------------------

def _clear_overlay():
    _State.plan, _State.batches, _State.summary, _State.misfit = None, None, "", []


atexit.register(_clear_overlay)  # GPU batches still held when Blender quits are reported as leaks


def _has_layout(context):
    return utils.find_layout(utils.resolve_source(context.view_layer.objects.active)) is not None


def _compute_plan(context):
    source, extras = _source(context)
    _clear_overlay()
    _State.error = ""
    if source is None:
        return
    _State.markers_only = not context.window_manager.easymold_preview
    builder = utils.MoldBuilder(source, context.scene.easymold_props, 'PLAN', extras)
    try:
        plan = builder.run_plan(_State.prepared_key, _State.markers_only)
    except utils.MoldError as e:
        _State.error = str(e)
        return
    finally:
        builder.discard()
    _State.plan = plan
    _State.summary = plan['summary']
    _State.misfit = plan['misfit']


def _make_batches(plan):
    line_shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    fill_shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    lines, fills = [], []
    for kind, points in plan['lines'].items():
        if points:
            color, width = STYLE[kind]
            lines.append((batch_for_shader(line_shader, 'LINES', {"pos": [tuple(q) for q in points]}), color, width))
    for kind, corners in plan['tris'].items():
        if len(corners):
            fills.append((batch_for_shader(fill_shader, 'TRIS', {"pos": corners}), FILL[kind]))
    markers = []
    for name, m in plan['markers'].items():
        color, width = STYLE[m['kind']]
        batch = batch_for_shader(line_shader, 'LINES', {"pos": [tuple(q) for q in m['lines']]})
        markers.append((name, m['origin'], m['radius'], batch, color, width))
    return lines, fills, markers


def _marker_draws(markers):
    """(batch, color, width, matrix) of each marker shape: the selected markers brighter and wider, the others
    fainter (all alike if none is selected); one being moved or scaled carries its shape (redrawn for where it is
    once it's dropped)."""
    shown = []
    for name, origin, radius, batch, color, width in markers:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        shown.append((obj, origin, radius, batch, color, width))
    picked = any(obj.select_get() for obj, *_rest in shown)
    out = []
    for obj, origin, radius, batch, color, width in shown:
        if picked and obj.select_get():
            color, width = (*color[:3], 1.0), width * 2.0
        elif picked:
            color = (*color[:3], color[3] * 0.45)
        k = utils.marker_radius(obj) / radius if radius > 1e-9 else 1.0
        matrix = Matrix.Translation(obj.matrix_world.translation) @ Matrix.Scale(k, 4) @ Matrix.Translation(-origin)
        out.append((batch, color, width, matrix))
    return out


def _draw():
    if _State.plan is None:
        return
    wm = bpy.context.window_manager
    if wm.easymold_running or not (wm.easymold_preview or _State.markers_only):
        return
    if _State.batches is None:
        _State.batches = _make_batches(_State.plan)
    lines, fills, markers = _State.batches
    if not wm.easymold_preview:
        lines, fills = [], []  # only the layout markers
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_mask_set(False)
    fill_shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.depth_test_set('LESS_EQUAL')
    for batch, color in fills:
        fill_shader.uniform_float("color", color)
        batch.draw(fill_shader)
    line_shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    line_shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    # Twice: what is hidden behind the object faint, the rest bright.
    for depth, fade in (('NONE', 0.3), ('LESS_EQUAL', 1.0)):
        gpu.state.depth_test_set(depth)
        for batch, color, width in lines:
            line_shader.uniform_float("lineWidth", width)
            line_shader.uniform_float("color", (*color[:3], color[3] * fade))
            batch.draw(line_shader)
        for batch, color, width, matrix in _marker_draws(markers):
            line_shader.uniform_float("lineWidth", width)
            line_shader.uniform_float("color", (*color[:3], color[3] * fade))
            with gpu.matrix.push_pop():
                gpu.matrix.multiply_matrix(matrix)
                batch.draw(line_shader)
    gpu.state.depth_test_set('NONE')
    gpu.state.depth_mask_set(True)
    gpu.state.blend_set('NONE')


# --- Auto Preview ----------------------------------------------------------------

class _CoarseProps:
    """The settings with a coarser resolution (and no bed cuts: those come with the real mold)."""

    def __init__(self, props, voxel):
        self._props = props
        self.voxel_size = voxel
        self.split_to_fit = False

    def __getattr__(self, name):
        return getattr(self._props, name)


def previews(source=None):
    return [c for c in bpy.data.collections
            if c.get(utils.ROLE) == 'PREVIEW' and (source is None or c.get(utils.SOURCE) == source)]


def remove_previews(show_results=True):
    """Delete the Auto Preview collections (the real results they hid are shown again)."""
    for coll in previews():
        source = coll.get(utils.SOURCE)
        utils.delete_collection(coll)
        if show_results and source is not None:
            for result in utils._collections_of(source, 'RESULT'):
                utils.set_collection_visible(result, True)


def cancel_build():
    if _State.job is not None:
        builder, steps, _sig = _State.job
        _State.job = None
        steps.close()
        builder.abort()
    _State.progress, _State.status = 0.0, ""


def stop_for_generate():
    """Before Generate Mold / Edit Layout: no Auto Preview build may run alongside, and the real result replaces
    the preview."""
    cancel_build()
    remove_previews()
    _State.build_due = False


def _faces(objects):
    return sum(len(o.data.polygons) for o in objects if o.type == 'MESH')


def _start_build(context):
    source, extras = _source(context)
    _State.error = ""
    if source is None:
        return
    faces = _faces([source] + extras)
    if faces > MAX_FACES:
        _State.error = f"Auto Preview: too detailed ({faces:,} faces), use Generate Mold"
        return
    props = context.scene.easymold_props
    size = max(max(o.dimensions) for o in [source] + extras)
    coarse = _CoarseProps(props, max(props.voxel_size, size / COARSE_STEPS))
    builder = utils.MoldBuilder(source, coarse, 'PREVIEW', extras)
    _State.job = (builder, utils.steps(builder, builder.run_generate), _State.signature)
    _State.progress, _State.status = 0.0, "Starting"


def _step_build():
    builder, steps, _sig = _State.job
    try:
        _State.progress, _State.status = next(steps)
    except StopIteration:
        _State.job = None
        _State.progress, _State.status = 0.0, ""
    except utils.MoldError as e:
        _State.job = None
        _State.error = f"Auto Preview: {e}"
    except Exception as e:
        traceback.print_exc()
        _State.job = None
        _State.error = f"Auto Preview failed: {e}"


# --- the timer -------------------------------------------------------------------

def _redraw(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _busy(context):
    """Edit Mode (the mesh being edited is only updated when leaving it) or a move / rotate / scale being dragged
    (nothing may be added or removed under it): wait."""
    active = context.view_layer.objects.active
    if active is not None and active.mode != 'OBJECT':
        return True
    for window in context.window_manager.windows:
        for op in getattr(window, "modal_operators", ()):
            if op.bl_idname.startswith("TRANSFORM_OT"):
                return True
    return False


def update(context):
    """One check: recompute what changed (also called by the tests, without the timer)."""
    wm = context.window_manager
    if wm.easymold_running:
        cancel_build()
        return
    now = time.monotonic()
    sig, _State.prepared_key = _signature(context)
    if sig != _State.signature:
        _State.signature = sig
        _State.changed_at = now
        _State.plan_due = _State.build_due = True
        if _State.job is not None and _State.job[2] != sig:
            cancel_build()  # restarted once the changes stop
    if sig is None:
        cancel_build()
        _clear_overlay()
        _State.error = ""
        return
    waited = now - (_State.changed_at or 0.0)
    busy = _busy(context)
    changed = False
    wanted = wm.easymold_preview or _has_layout(context)
    if wanted and _State.plan_due and waited >= PLAN_DELAY and not busy:
        _State.plan_due = False
        try:
            _compute_plan(context)
        except Exception as e:
            traceback.print_exc()
            _clear_overlay()
            _State.error = f"Preview failed: {e}"
        changed = True
    elif not wanted and _State.plan is not None:
        _clear_overlay()
        changed = True
    if wm.easymold_auto_preview and not busy:
        if _State.job is not None:
            _step_build()
            changed = True
        elif _State.build_due and waited >= BUILD_DELAY:
            _State.build_due = False
            _start_build(context)
            changed = True
    if changed:
        _redraw(context)


def _tick():
    wm = bpy.context.window_manager
    try:
        if not (wm.easymold_preview or wm.easymold_auto_preview or _has_layout(bpy.context)):
            # Nothing to follow (it keeps polling: an edited layout shows its markers without Preview).
            if _State.plan is not None:
                _clear_overlay()
                _redraw(bpy.context)
            return TICK
    except Exception:
        traceback.print_exc()
        return TICK
    try:
        update(bpy.context)
    except Exception:
        traceback.print_exc()
    # While a build runs, its steps follow each other quickly.
    return 0.01 if _State.job is not None else TICK


def _ensure_timer():
    if not _State.timer_on:
        _State.timer_on = True
        bpy.app.timers.register(_tick, first_interval=0.01, persistent=True)  # also after opening a file


def on_toggle_preview(wm, context):
    _State.plan_due = True
    _State.changed_at = 0.0
    if not wm.easymold_preview:
        _clear_overlay()
        utils.clear_plan_cache()
        _State.error = ""
    else:
        _ensure_timer()
    _redraw(context)


def on_toggle_auto(wm, context):
    if wm.easymold_auto_preview:
        _State.build_due = True
        _State.changed_at = 0.0
        _ensure_timer()
    else:
        cancel_build()
        remove_previews()
        _State.error = ""
    _redraw(context)


# --- file / undo handlers: a build must never outlive the data it works on --------

@persistent
def _on_depsgraph(_scene, depsgraph):
    """Count edits of the geometry the previews depend on (cheaper than looking at every vertex each tick)."""
    if not _State.watched:
        return
    for u in depsgraph.updates:
        if u.is_updated_geometry and isinstance(u.id, (bpy.types.Object, bpy.types.Mesh)) \
                and u.id.original.name in _State.watched:
            _State.epoch += 1
            return


@persistent
def _before_undo(*_args):
    cancel_build()


@persistent
def _after_undo(*_args):
    utils.clear_plan_cache()  # its mesh may have been replaced
    if not bpy.context.window_manager.easymold_auto_preview:
        remove_previews()  # undo may bring back an old one
    _State.signature = None  # rebuilt / recomputed from what is there now
    _State.epoch += 1


@persistent
def _before_save(*_args):
    # Previews are not part of the work: they are not saved (Auto Preview rebuilds them).
    cancel_build()
    remove_previews()
    utils.clear_plan_cache()
    utils.cache_saving(True)  # the session cache stays, but isn't written


@persistent
def _after_save(*_args):
    utils.cache_saving(False)
    _State.build_due = True


@persistent
def _before_load(*_args):
    cancel_build()
    utils.clear_cache()
    _clear_overlay()
    _State.signature = None


@persistent
def _after_load(*_args):
    _State.plan_due = _State.build_due = True
    _ensure_timer()


HANDLERS = (
    (bpy.app.handlers.depsgraph_update_post, _on_depsgraph), (bpy.app.handlers.load_post, _after_load),
    (bpy.app.handlers.undo_pre, _before_undo), (bpy.app.handlers.redo_pre, _before_undo),
    (bpy.app.handlers.undo_post, _after_undo), (bpy.app.handlers.redo_post, _after_undo),
    (bpy.app.handlers.save_pre, _before_save), (bpy.app.handlers.save_post, _after_save),
    (bpy.app.handlers.load_pre, _before_load),
)


class EASYMOLD_OT_stop_preview(bpy.types.Operator):
    bl_idname = "object.easymold_stop_preview"
    bl_label = "Stop Preview"
    bl_description = "Stop building the Auto Preview (it starts again on the next change)"

    def execute(self, context):
        cancel_build()
        _State.build_due = False
        _redraw(context)
        return {'FINISHED'}


def status():
    """(summary, misfit sizes, error, building, progress, step) for the panel."""
    return (_State.summary, _State.misfit, _State.error, _State.job is not None, _State.progress, _State.status)


def register():
    bpy.utils.register_class(EASYMOLD_OT_stop_preview)
    wm = bpy.types.WindowManager
    wm.easymold_preview = bpy.props.BoolProperty(
        name="Preview", default=False, update=on_toggle_preview,
        description="Draw the mold in the viewport as you change the settings or the layout: outline, split "
                    "planes, keys, bolts, pour holes and vents (red: pieces too big for the bed). Nothing is added "
                    "to the scene")
    wm.easymold_auto_preview = bpy.props.BoolProperty(
        name="Auto Preview", default=False, update=on_toggle_auto,
        description="Build the real mold at a coarse resolution shortly after each change, in its own collection "
                    "(not saved with the file). Generate Mold makes the final one")
    for handlers, func in HANDLERS:
        handlers.append(func)
    _State.draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')
    _ensure_timer()


def unregister():
    cancel_build()
    utils.clear_cache()
    if _State.draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_State.draw_handle, 'WINDOW')
        _State.draw_handle = None
    if _State.timer_on and bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _State.timer_on = False
    for handlers, func in HANDLERS:
        if func in handlers:
            handlers.remove(func)
    _clear_overlay()
    wm = bpy.types.WindowManager
    del wm.easymold_auto_preview
    del wm.easymold_preview
    bpy.utils.unregister_class(EASYMOLD_OT_stop_preview)
