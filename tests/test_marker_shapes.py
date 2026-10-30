# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""The layout markers are drawn with their real shape, also with Preview off (only them); the
selected one brighter, a marker being moved / scaled carries its shape; the empties are a sphere (key) or a circle
(pour hole, vent: flat; bolt: across its axis); layouts made before (cones, cubes) are restyled."""
import sys, importlib, math
import bpy, bmesh
from mathutils import Vector
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from EasyMold import utils, preview
failures = []
preview.PLAN_DELAY = 0.0


def check(label, ok, msg):
    print(f"   {'ok  ' if ok else 'FAIL'} {label}: {msg}")
    if not ok:
        failures.append(f"{label}: {msg}")


def cup():
    bpy.ops.mesh.primitive_cylinder_add(vertices=40, radius=15, depth=30, location=(0, 0, 15))
    outer = bpy.context.active_object
    bpy.ops.mesh.primitive_cylinder_add(vertices=40, radius=12, depth=30, location=(0, 0, 20))
    hole = bpy.context.active_object
    m = outer.modifiers.new("b", 'BOOLEAN'); m.object = hole; m.operation = 'DIFFERENCE'
    bpy.context.view_layer.objects.active = outer
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(hole)
    return outer


def ball():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=12, segments=32, ring_count=16, location=(0.031, 0.029, 14))
    return bpy.context.active_object


def step():  # a low step beside a tall wall (the wall's face at x = 4), as in test_tips
    outline = [(-10, 0), (10, 0), (10, 20), (4, 20), (4, 5), (-10, 5)]
    bm = bmesh.new()
    front = [bm.verts.new((x, -10, z)) for x, z in outline]
    back = [bm.verts.new((x, 10, z)) for x, z in outline]
    bm.faces.new(front)
    bm.faces.new(list(reversed(back)))
    for i in range(len(outline)):
        j = (i + 1) % len(outline)
        bm.faces.new((front[i], back[i], back[j], front[j]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new("Step")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Step", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def setup(make, **settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = make()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    p = bpy.context.scene.easymold_props
    p.explode = False
    for k, v in settings.items():
        setattr(p, k, v)
    bpy.ops.object.easymold_edit_layout(reset=True)
    lay = utils.find_layout(obj)
    bpy.context.window_manager.easymold_preview = False
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj, lay


def markers(lay):
    return sorted((o for o in lay.objects if o.get(utils.ROLE) in utils.MARKERS), key=lambda o: o.name)


def refresh():
    bpy.context.view_layer.update()
    preview.update(bpy.context)


def draws():
    """_marker_draws() for the current plan, without GPU batches (the batch is the marker's name)."""
    plan = preview._State.plan
    fake = [(name, m['origin'], m['radius'], name, *preview.STYLE[m['kind']]) for name, m in plan['markers'].items()]
    return {batch: (color, width, matrix) for batch, color, width, matrix in preview._marker_draws(fake)}


def local_y(obj):
    return (obj.matrix_world.to_3x3() @ Vector((0, 1, 0))).normalized()


CASES = [
    ("direct screws", cup, dict(mold_type='DIRECT', mold_parts='2', split_axis='X', clamp_method='SCREW')),
    ("direct below", cup, dict(mold_type='DIRECT', mold_parts='2', split_axis='X', pour_entry='BOTTOM',
                               pour_tip=1.0)),
    ("direct away", ball, dict(mold_type='DIRECT', mold_parts='4', vent_route='AWAY', vent_tip=0.5)),
    ("vent leaning", step, dict(mold_type='DIRECT', mold_parts='2', split_axis='Y', pour_entry='TOP', vent_count=1,
                                vent_radius=1.5, vent_tip=0.5, vent_route='AWAY', margin=10.0)),
    ("shell", cup, dict(mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X', clamp_method='SCREW')),
    ("two part", ball, dict(mold_type='SILICONE_TWO_PART', pour_tip=1.0)),
    ("box", cup, dict(mold_type='SILICONE_BOX')),
]

for label, make, settings in CASES:
    print(label)
    obj, lay = setup(make, **settings)
    refresh()
    plan = preview._State.plan
    check(label, plan is not None and preview._State.markers_only, "plan made with Preview off (markers only)")
    if plan is None:
        continue
    used = markers(lay)
    roles = {o.name: o.get(utils.ROLE) for o in used}
    drawn = plan['markers']
    check(label, set(drawn) == set(roles), f"every marker drawn: {sorted(drawn)} / {sorted(roles)}")
    check(label, all(drawn[n]['kind'] == roles[n] for n in drawn if n in roles), "drawn as its own kind (all used)")
    check(label, all(len(m['lines']) >= 8 and len(m['lines']) % 2 == 0 for m in drawn.values()),
          "shapes are segment pairs")
    check(label, not any(plan['lines'].get(k) for k in utils.MARKERS), "marker shapes not drawn twice")
    if label == "shell":
        check(label, 'OUTLINE' not in plan['tris'], "the shell's outline is not made for the markers alone")
    # The empties.
    for o in used:
        role = o.get(utils.ROLE)
        want = 'SPHERE' if role == 'KEY' else 'CIRCLE'
        check(label, o.empty_display_type == want, f"{o.name}: {o.empty_display_type}")
        if role in ('POUR', 'VENT'):
            check(label, abs(local_y(o).z) > 0.999, f"{o.name} circle flat on the object")
        if role == 'BOLT':
            axes = "Z" if settings['mold_type'] == 'SILICONE_TWO_PART' else lay[utils.LAYOUT_AXES]
            check(label, any(abs(local_y(o).dot(utils.AXIS_VECTOR[a])) > 0.999 for a in axes),
                  f"{o.name} circle across its axis {tuple(round(c, 3) for c in local_y(o))}")
            check(label, abs(utils.marker_radius(o) - bpy.context.scene.easymold_props.bolt_diameter / 2) < 1e-6,
                  f"{o.name} circle = bolt radius ({utils.marker_radius(o):.2f})")
    # Pour hole entering from below: the elbow of its channel is part of its shape.
    if label == "direct below":
        pour = next(o for o in used if o.get(utils.ROLE) == 'POUR')
        z = [q.z for q in drawn[pour.name]['lines']]
        check(label, max(z) - min(z) > 10, f"pour shape spans the sprue and the gate ({max(z) - min(z):.1f} mm)")
        # A key dropped on the pour channel is left out by the build: drawn as unused (a faint red ball).
        key = next(o for o in used if o.get(utils.ROLE) == 'KEY')
        kept = key.location.copy()
        top = max(drawn[pour.name]['lines'], key=lambda q: q.z)
        key.location = (top.x, top.y, top.z - 4)
        refresh()
        m = preview._State.plan['markers'][key.name]
        check(label, m['kind'] == 'UNUSED' and preview.STYLE['UNUSED'], f"key on the channel drawn as {m['kind']}")
        key.location = kept
        refresh()
        check(label, preview._State.plan['markers'][key.name]['kind'] == 'KEY', "back in place: a key again")
        drawn = preview._State.plan['markers']
    if label == "vent leaning":
        # The vent beside the wall leans away from it: its drawn shape does too (the empty stays a circle).
        vent = next(o for o in used if o.get(utils.ROLE) == 'VENT')
        vent.location = (3.0, 0, 25)
        refresh()
        drawn = preview._State.plan['markers']
        m = drawn[vent.name]
        far = max((q.xy - m['origin'].xy).length for q in m['lines'])
        check(label, far > 3 * m['radius'] + 1, f"vent shape leans ({far:.1f} mm off its marker)")
    if not used:
        utils.delete_collection(lay)
        continue
    # Selection: the selected marker brighter and wider, the others fainter.
    first = used[0]
    obj.select_set(False)
    first.select_set(True)
    d = draws()
    color, width, _m = d[first.name]
    check(label, color[3] == 1.0 and width == preview.STYLE[roles[first.name]][1] * 2, f"selected: width {width}")
    others = [d[n] for n in d if n != first.name]
    check(label, all(c[3] < preview.STYLE[roles[n]][0][3] for n, (c, _w, _m) in d.items() if n != first.name),
          f"{len(others)} others fainter")
    first.select_set(False)
    d = draws()
    check(label, all(c[3] == preview.STYLE[roles[n]][0][3] for n, (c, _w, _m) in d.items()), "none selected: all alike")
    # Moving (before the plan is recomputed, as while dragging): the shape goes along with the marker.
    before = first.matrix_world.translation.copy()
    first.location += Vector((0, 2.0, 0))
    bpy.context.view_layer.update()
    shift = first.matrix_world.translation - before
    _c, _w, matrix = draws()[first.name]
    q = drawn[first.name]['lines'][0]
    check(label, ((matrix @ q) - (q + shift)).length < 1e-5, f"shape carried by the move {tuple(round(c, 2) for c in shift)}")
    refresh()
    origin = preview._State.plan['markers'][first.name]['origin']
    check(label, (origin - first.matrix_world.translation).length < 1e-5, "redrawn where it was dropped")
    # Scaling: the shape grows around the marker.
    if roles[first.name] != 'BOLT':
        first.scale = (1.5, 1.5, 1.5)
        bpy.context.view_layer.update()
        _c, _w, matrix = draws()[first.name]
        c = first.matrix_world.translation
        q = preview._State.plan['markers'][first.name]['lines'][0]
        check(label, abs((matrix @ q - c).length - 1.5 * (q - c).length) < 1e-4, "shape scaled with the marker")
        refresh()
    # Preview on: the whole mold and the markers; off again: the markers alone.
    bpy.context.window_manager.easymold_preview = True
    refresh()
    plan = preview._State.plan
    check(label, not preview._State.markers_only and (bool(plan['lines'].get('OUTLINE'))
                                                      or plan['tris'].get('OUTLINE') is not None), "Preview on: outline too")
    check(label, set(plan['markers']) == set(roles), "Preview on: markers drawn")
    bpy.context.window_manager.easymold_preview = False
    refresh()
    check(label, preview._State.markers_only and preview._State.plan is not None, "Preview off: markers alone again")
    # No layout (removed): nothing drawn with Preview off.
    utils.delete_collection(lay)
    bpy.context.view_layer.objects.active = obj
    refresh()
    check(label, preview._State.plan is None, "layout removed: nothing drawn")

# Layouts made before: cones and cubes become circles (a bolt's half as wide: the cube was the bolt's width).
print("restyle")
obj, lay = setup(cup, mold_type='DIRECT', mold_parts='2', split_axis='X', clamp_method='SCREW')
bolt = next(o for o in markers(lay) if o.get(utils.ROLE) == 'BOLT')
vent = next(o for o in markers(lay) if o.get(utils.ROLE) == 'VENT')
d = bpy.context.scene.easymold_props.bolt_diameter
bolt.empty_display_type, bolt.empty_display_size, bolt.rotation_euler = 'CUBE', d, (0, 0, 0)
vent.empty_display_type = 'CONE'
for o in markers(lay):
    utils.restyle_marker(o)
    if o.get(utils.ROLE) == 'BOLT':
        utils.snap_marker(o, bpy.context.scene.easymold_props)
bpy.context.view_layer.update()
check("restyle", bolt.empty_display_type == 'CIRCLE' and abs(bolt.empty_display_size - d / 2) < 1e-6
      and abs(local_y(bolt).x) > 0.999, f"bolt {bolt.empty_display_type} {bolt.empty_display_size:.2f}")
check("restyle", vent.empty_display_type == 'CIRCLE' and abs(local_y(vent).z) > 0.999, "vent circle")
check("restyle", not any(utils.restyle_marker(o) for o in markers(lay)), "a second time changes nothing")
# Generate still makes closed pieces from the layout.
bpy.context.view_layer.objects.active = obj
bpy.ops.object.easymold_generate()
pieces = [o for o in utils.find_result(obj).objects if o.type == 'MESH' and "preview" not in o.name
          and not o.name.endswith("_Master")]
bad = [o.name for o in pieces if utils.open_edges(o.data)]
check("generate", pieces and not bad, f"{len(pieces)} pieces, open: {bad}")

print("FAILURES:", failures if failures else "none")
