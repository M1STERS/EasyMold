# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import traceback

import bpy
from bl_operators.presets import AddPresetBase
from bpy.app.handlers import persistent
from mathutils import Vector

from . import export, preview, utils
from .properties import EasyMoldProperties

# Events that still reach the viewport while a mold is being built, so the user can look around.
NAVIGATION_EVENTS = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE',
                     'TRACKPADPAN', 'TRACKPADZOOM', 'NDOF_MOTION'}


def _source_poll(context):
    return utils.resolve_source(context.active_object) is not None and not context.window_manager.easymold_running


class _StepRunner:
    """Runs a MoldBuilder generator step by step with a progress bar (Esc cancels).

    From scripts (execute) the steps run synchronously.
    """
    kind = 'RESULT'

    def _make(self, context):
        source = utils.resolve_source(context.active_object)
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        preview.stop_for_generate()
        props = context.scene.easymold_props
        extras = utils.tray_members(source, context.selected_objects, props)
        builder = utils.MoldBuilder(source, props, self.kind, extras)
        run = builder.run_layout if self.kind == 'LAYOUT' else builder.run_generate
        return builder, utils.steps(builder, run)

    def execute(self, context):
        try:
            builder, steps = self._make(context)
            for _ in steps:
                pass
        except utils.MoldError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self._report_done(builder)
        return {'FINISHED'}

    def invoke(self, context, event):
        try:
            self._builder, self._steps = self._make(context)
        except utils.MoldError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        wm = context.window_manager
        wm.easymold_running = True
        wm.easymold_progress = 0.0
        wm.easymold_status = "Starting..."
        wm.progress_begin(0, 100)
        self._timer = wm.event_timer_add(0.02, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self._steps.close()
            self._builder.abort()  # removes everything created so far (also if no step ran yet)
            self._end(context)
            self.report({'WARNING'}, "Cancelled")
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'} if event.type in NAVIGATION_EVENTS else {'RUNNING_MODAL'}

        try:
            progress, text = next(self._steps)
        except StopIteration:
            self._end(context)
            self._report_done(self._builder)
            return {'FINISHED'}
        except utils.MoldError as e:
            self._end(context)
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except Exception as e:
            traceback.print_exc()
            self._end(context)
            self.report({'ERROR'}, f"EasyMold failed: {e}")
            return {'CANCELLED'}

        wm = context.window_manager
        wm.easymold_progress = progress
        wm.easymold_status = f"{text}..."
        wm.progress_update(int(progress * 100))
        context.workspace.status_text_set(f"EasyMold: {text}... {progress:.0%}   (Esc to cancel)")
        for area in context.screen.areas:
            area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _end(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        wm.progress_end()
        wm.easymold_running = False
        wm.easymold_status = ""
        context.workspace.status_text_set(None)
        for area in context.screen.areas:
            area.tag_redraw()

    def _report_done(self, builder):
        for warning in builder.warnings:
            self.report({'WARNING'}, warning)
        msg = self._done_message(builder)
        if builder.notes:
            msg += " (" + ", ".join(dict.fromkeys(builder.notes)) + ")"  # the same note once (tray objects)
        self.report({'INFO'}, msg)


class EASYMOLD_OT_generate(_StepRunner, bpy.types.Operator):
    bl_idname = "object.easymold_generate"
    bl_label = "Generate Mold"
    bl_description = "Generate the mold for the active object (uses the edited layout if there is one)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _source_poll(context)

    def _done_message(self, builder):
        msg = f"Mold created in collection '{builder.collection.name}'"
        if builder.silicone_volume:
            need = utils.silicone_needs(builder.props, builder.silicone_volume)
            msg += (f". Silicone: {need['ml']:.0f} ml = {need['g']:.0f} g (A {need['a']:.0f} {need['unit']} + "
                    f"B {need['b']:.0f} {need['unit']}, with {builder.props.waste:g}% extra)")
        return msg


class EASYMOLD_OT_edit_layout(_StepRunner, bpy.types.Operator):
    bl_idname = "object.easymold_edit_layout"
    bl_label = "Edit Layout"
    bl_description = ("Show the master, the mold outline, the split planes and editable markers for the keys, "
                      "pour hole and vents before generating")
    bl_options = {'REGISTER', 'UNDO'}
    kind = 'LAYOUT'

    reset: bpy.props.BoolProperty(name="Reset", description="Recreate the layout from the current settings",
                                  default=False)

    @classmethod
    def poll(cls, context):
        return _source_poll(context)

    def _show_existing(self, context):
        """Without Reset, an existing layout is only shown (never rebuilt: that would drop the user's edits).
        Returns whether there was one."""
        layout = utils.find_layout(utils.resolve_source(context.active_object))
        if layout is None or self.reset:
            return False
        utils.set_collection_visible(layout, True)
        for obj in layout.objects:
            if obj.get(utils.ROLE) in utils.MARKERS and obj.type == 'EMPTY':
                utils.restyle_marker(obj)  # layouts made before: cones and cubes
                if obj.get(utils.ROLE) == 'BOLT':
                    utils.snap_marker(obj, context.scene.easymold_props)  # turns it across its axis
        return True

    def execute(self, context):
        # Also from scripts and the Adjust Last Operation panel.
        return {'FINISHED'} if self._show_existing(context) else super().execute(context)

    def invoke(self, context, event):
        return {'FINISHED'} if self._show_existing(context) else super().invoke(context, event)

    def _done_message(self, builder):
        return "Layout ready: move, scale (= size), duplicate or delete the markers, then Generate Mold"


class EASYMOLD_OT_add_marker(bpy.types.Operator):
    bl_idname = "object.easymold_add_marker"
    bl_label = "Add Element"
    bl_description = "Add an element to the layout in a free spot where it works; then move it if you want"
    bl_options = {'REGISTER', 'UNDO'}

    role: bpy.props.EnumProperty(items=[
        ('KEY', "Key", "Registration key (stays on the split plane)"),
        ('BOLT', "Bolt", "Bolt that clamps the parts together (stays on the split plane)"),
        ('POUR', "Pour Hole", "Pour hole (stays on the object)"),
        ('VENT', "Vent", "Air vent (stays on the object)"),
        ('BLOCKER', "Blocker", "Thin wall joined to the master, to close a hole the silicone would lock "
                               "around (goes over a hole that is still open)"),
    ])

    @classmethod
    def poll(cls, context):
        source = utils.resolve_source(context.active_object)
        return source is not None and utils.find_layout(source) is not None

    def execute(self, context):
        source = utils.resolve_source(context.active_object)
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            marker, note = utils.add_layout_item(source, self.role, context.scene.easymold_props)
        except utils.MoldError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        _select_only(context, marker)
        if note:
            self.report({'WARNING'}, note)
        return {'FINISHED'}


def _select_only(context, obj):
    for other in context.view_layer.objects.selected:
        if other is not None:
            other.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _layout_element(context):
    obj = context.active_object
    if obj is None or utils.layout_of(obj) is None or obj.get(utils.ROLE) not in utils.ROLE_NAMES:
        return None
    return obj


def _view3d(context):
    """(area, region) of the 3D view to run a viewport operator in (the panel's own, or the biggest one)."""
    areas = [context.area] if context.area is not None and context.area.type == 'VIEW_3D' else []
    areas += sorted((a for a in context.screen.areas if a.type == 'VIEW_3D'), key=lambda a: -a.width * a.height)
    for area in areas:
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region is not None:
            return area, region
    return None, None


class EASYMOLD_OT_select_element(bpy.types.Operator):
    bl_idname = "object.easymold_select_element"
    bl_label = "Select Element"
    bl_description = "Select this layout element and show it in the view"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None:
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        _select_only(context, obj)
        area, _region = _view3d(context)
        if area is not None:
            # Centered in the view, without zooming in on it (the rest of the mold stays in sight).
            if obj.type == 'MESH' and obj.data.vertices:
                center = obj.matrix_world @ (sum((Vector(c) for c in obj.bound_box), Vector()) / 8)
            else:
                center = obj.matrix_world.translation
            area.spaces.active.region_3d.view_location = center
        return {'FINISHED'}


class EASYMOLD_OT_transform_element(bpy.types.Operator):
    bl_idname = "object.easymold_transform_element"
    bl_label = "Transform Element"
    bl_description = "Move, resize or turn the selected layout element with the mouse (click to finish, " \
                     "right click or Esc to cancel)"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(items=[('MOVE', "Move", "Move it (G)"), ('SIZE', "Size", "Make it bigger or "
                                        "smaller (S)"), ('ROTATE', "Rotate", "Turn it (R)")])

    @classmethod
    def poll(cls, context):
        return _layout_element(context) is not None

    def invoke(self, context, event):
        obj = _layout_element(context)
        _select_only(context, obj)
        area, region = _view3d(context)
        if area is None:
            return {'CANCELLED'}
        op = {'MOVE': bpy.ops.transform.translate, 'SIZE': bpy.ops.transform.resize,
              'ROTATE': bpy.ops.transform.rotate}[self.mode]
        with context.temp_override(area=area, region=region):
            op('INVOKE_DEFAULT')
        return {'FINISHED'}


class EASYMOLD_OT_delete_element(bpy.types.Operator):
    bl_idname = "object.easymold_delete_element"
    bl_label = "Delete Element"
    bl_description = "Remove the selected layout element (Generate uses what is left)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _layout_element(context)
        return obj is not None and obj.get(utils.ROLE) in utils.MARKERS + ('BLOCKER', 'CORE')

    def execute(self, context):
        obj = _layout_element(context)
        layout = utils.layout_of(obj)
        mesh = obj.data if obj.type == 'MESH' else None
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        master = next((o for o in layout.objects if o.get(utils.ROLE) == 'MASTER'), None)
        if master is not None:
            _select_only(context, master)
        return {'FINISHED'}


class EASYMOLD_OT_clear_cache(bpy.types.Operator):
    bl_idname = "object.easymold_clear_cache"
    bl_label = "Clear Cache"
    bl_description = ("Free the memory of what Generate kept to regenerate faster (the prepared object and its "
                      "offset surfaces). Only kept while Blender is open, never saved in the file")

    def execute(self, context):
        _objects, _surfaces, size = utils.cache_info()
        utils.clear_cache()
        self.report({'INFO'}, f"Cache cleared ({size / 2 ** 20:.0f} MB)")
        return {'FINISHED'}


_snapping = False


@persistent
def snap_moved_markers(scene, depsgraph):
    """Keep layout markers where they will be used while they are moved (keys on the split plane, pour holes and
    vents on the object)."""
    global _snapping
    if _snapping:
        return
    moved = [u.id.original for u in depsgraph.updates
             if isinstance(u.id, bpy.types.Object) and u.is_updated_transform
             and u.id.original.get(utils.ROLE) in utils.MARKERS]
    if not moved:
        return
    _snapping = True
    try:
        for obj in moved:
            utils.snap_marker(obj, scene.easymold_props)
    except ReferenceError:
        pass
    finally:
        _snapping = False


class EASYMOLD_OT_remove_layout(bpy.types.Operator):
    bl_idname = "object.easymold_remove_layout"
    bl_label = "Remove Layout"
    bl_description = "Delete the edited layout and go back to automatic placement"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        source = utils.resolve_source(context.active_object)
        return source is not None and utils.find_layout(source) is not None

    def execute(self, context):
        source = utils.resolve_source(context.active_object)
        layout = utils.find_layout(source)
        for obj in utils.tray_extras(layout):
            obj.hide_set(False)
        utils.delete_collection(layout)
        source.hide_set(False)
        context.view_layer.objects.active = source
        return {'FINISHED'}


class EASYMOLD_OT_export(bpy.types.Operator):
    bl_idname = "object.easymold_export"
    bl_label = "Export for Printing"
    bl_description = ("Write every printable piece of the generated mold to STL/3MF, each one turned so its "
                      "best flat face is on the bed")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        source = utils.resolve_source(context.active_object)
        return (source is not None and utils.find_result(source) is not None
                and not context.window_manager.easymold_running)

    def execute(self, context):
        props = context.scene.easymold_props
        source = utils.resolve_source(context.active_object)
        folder = props.export_folder
        if folder.startswith("//") and not bpy.data.filepath:
            self.report({'ERROR'}, "Save the .blend file first, or choose an absolute export folder")
            return {'CANCELLED'}
        folder = bpy.path.abspath(folder)
        pieces = export.printable(utils.find_result(source), props.export_master)
        if not pieces:
            self.report({'ERROR'}, "Nothing to export: generate the mold again")
            return {'CANCELLED'}
        try:
            paths, _info = export.export_pieces(pieces, folder, props.export_format, source.name)
        except OSError as e:
            self.report({'ERROR'}, f"Could not write the files: {e}")
            return {'CANCELLED'}
        where = paths[0] if len(paths) == 1 else folder
        self.report({'INFO'}, f"Exported {len(pieces)} piece(s) to {where}")
        return {'FINISHED'}


PRESET_SUBDIR = "easymold"  # presets/easymold/ in the add-on (built-in) and in the user's scripts folder
PRESET_SKIP = {"core_object", "export_folder"}  # what belongs to one scene / file


class EASYMOLD_OT_add_preset(AddPresetBase, bpy.types.Operator):
    """Save the current settings as a preset (or remove the selected one of yours)"""
    bl_idname = "easymold.add_preset"
    bl_label = "Add EasyMold Preset"
    preset_menu = "EASYMOLD_MT_presets"
    preset_subdir = PRESET_SUBDIR
    preset_defines = ["props = bpy.context.scene.easymold_props"]
    preset_values = [f"props.{name}" for name in EasyMoldProperties.__annotations__ if name not in PRESET_SKIP]

    def remove(self, _context, filepath):
        # Blender only protects the presets in its own folders: the ones shipped with the add-on are kept too.
        builtin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets", PRESET_SUBDIR)
        if os.path.normcase(os.path.dirname(os.path.abspath(filepath))) == os.path.normcase(builtin):
            self.report({'WARNING'}, "Built-in presets can't be removed (only the ones you saved)")
            return
        os.remove(filepath)


classes = (
    EASYMOLD_OT_add_preset,
    EASYMOLD_OT_generate,
    EASYMOLD_OT_edit_layout,
    EASYMOLD_OT_add_marker,
    EASYMOLD_OT_select_element,
    EASYMOLD_OT_transform_element,
    EASYMOLD_OT_delete_element,
    EASYMOLD_OT_clear_cache,
    EASYMOLD_OT_remove_layout,
    EASYMOLD_OT_export,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.app.handlers.depsgraph_update_post.append(snap_moved_markers)


def unregister():
    if snap_moved_markers in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(snap_moved_markers)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
