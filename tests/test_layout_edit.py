# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Layout editing (session 6): elements added in a free valid spot, kept on their plane / the object while moved,
radius in mm, select / delete, errors where an element doesn't apply, blockers over open holes, and Generate
uses what was edited (pieces closed)."""
import sys, importlib, math
import bpy, bmesh
from mathutils import Vector
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from EasyMold import utils
failures = []


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


def egg():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=12, segments=32, ring_count=16, location=(0, 0, 20))
    obj = bpy.context.active_object
    obj.scale = (1.6, 1, 1)
    obj.rotation_euler = (0, math.radians(20), 0)
    bpy.ops.object.transform_apply(scale=True, rotation=True)
    return obj


def ring():
    bpy.ops.mesh.primitive_torus_add(major_radius=14, minor_radius=5, location=(0, 0, 5))
    return bpy.context.active_object


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
    return obj, utils.find_layout(obj)


def markers(lay, role):
    return sorted((o for o in lay.objects if o.get(utils.ROLE) == role), key=lambda o: o.name)


def add(role):
    before = set(bpy.data.objects)
    try:
        result = bpy.ops.object.easymold_add_marker(role=role)
    except RuntimeError as e:
        return None, str(e)
    new = [o for o in bpy.data.objects if o not in before]
    return (new[0] if new else None), str(result)


def update():
    bpy.context.view_layer.update()


def on_object(lay, pt):
    master = next(o for o in lay.objects if o.get(utils.ROLE) == 'MASTER')
    bvh = utils.MoldBuilder._bvh(master)
    hit = bvh.ray_cast(Vector((pt.x, pt.y, 1000)), Vector((0, 0, -1)))[0]
    return hit is not None and abs(hit.z - pt.z) < 1e-4


def gap(lay, obj):
    """Room between a marker and the closest other marker (radii included)."""
    others = [o for o in lay.objects if o.get(utils.ROLE) in utils.MARKERS and o != obj]
    return min((obj.location - o.location).length - utils.marker_radius(obj) - utils.marker_radius(o) for o in others)


def closed(coll):
    bad = []
    for o in coll.objects:
        if o.type != 'MESH' or "preview" in o.name or o.name.endswith("_Master"):
            continue
        bm = bmesh.new(); bm.from_mesh(o.data)
        nm = sum(1 for e in bm.edges if not e.is_manifold)
        bm.free()
        print(f"     {o.name:34s} nonmanifold={nm:4d}")
        if nm:
            bad.append(o.name)
    return bad


# 1. Direct mold in 2 parts: keys added on the split plane, in free spots.
obj, lay = setup(cup, mold_type='DIRECT', mold_parts='2', split_axis='X', clamp_method='SCREW')
cx = lay[utils.LAYOUT_CENTER][0]
n = len(markers(lay, 'KEY'))
for k in range(3):
    key, msg = add('KEY')
    check("add key", key is not None and abs(key.location.x - cx) < 1e-6 and bpy.context.active_object == key
          and key.select_get(), f"#{k + 1} at {tuple(round(c, 2) for c in key.location)} on x={cx:.2f}, "
          f"selected, gap {gap(lay, key):.1f}")
    check("add key", gap(lay, key) > 0.5, "in a free spot")
check("add key", len(markers(lay, 'KEY')) == n + 3, f"{n} -> {len(markers(lay, 'KEY'))} keys")

# Moving a key keeps it on the plane; along the plane it moves freely.
key = markers(lay, 'KEY')[0]
y0 = key.location.y
key.location.x += 4.0
key.location.y += 2.0
update()
check("move key", abs(key.location.x - cx) < 1e-6 and abs(key.location.y - (y0 + 2.0)) < 1e-6,
      f"snapped back to x={key.location.x:.3f}, y moved {key.location.y - y0:.2f}")

block = list(lay[utils.LAYOUT_BLOCK])
key.location.y = block[1] - 30.0  # dragged far out of the block
update()
check("move key", abs(key.location.y - (block[1] + utils.marker_radius(key) + 1.0)) < 1e-6
      and abs(key.location.x - cx) < 1e-6, f"kept inside the block (y={key.location.y:.2f}, block from {block[1]:.2f})")
key.location.y = y0
update()

# Radius in mm.
key.easymold_radius = 4.5
check("radius", abs(key.empty_display_size - 4.5) < 1e-6 and tuple(key.scale) == (1, 1, 1), "set in mm")
key.scale = (2, 2, 2)
check("radius", abs(key.easymold_radius - 9.0) < 1e-6, f"scaling it doubles it ({key.easymold_radius:g})")
key.easymold_radius = 3.0

# Bolts, vents, pour hole (entry from below on a vertical split plane).
bolt, msg = add('BOLT')
check("add bolt", bolt is not None and abs(bolt.location.x - cx) < 1e-6, f"{msg} at x={bolt.location.x if bolt else 0:.2f}")
vent, msg = add('VENT')
check("add vent", vent is not None and on_object(lay, vent.location), f"{msg}, on the object")
print("vent at", tuple(round(c, 2) for c in vent.location))
vent.location.x -= math.copysign(2.0, vent.location.x)  # inward (it may be on the rim)
update()
check("move vent", on_object(lay, vent.location), f"follows the object (z={vent.location.z:.2f})")
pour, msg = add('POUR')
check("add pour", pour is not None and abs(pour.location.x - cx) < 1e-6, f"{msg}: on the split plane (from below)")

# Select / delete.
bpy.ops.object.easymold_select_element(name=vent.name)
check("select", bpy.context.active_object == vent and list(bpy.context.selected_objects) == [vent], "only it")
name = vent.name
bpy.ops.object.easymold_delete_element()
check("delete", name not in bpy.data.objects and bpy.context.active_object.get(utils.ROLE) == 'MASTER',
      "removed, master selected")

# Not used by this mold: an error, nothing added.
bpy.context.scene.easymold_props.clamp_method = 'CLIP'
count = len(lay.objects)
bolt, msg = add('BOLT')
check("errors", bolt is None and "Screws" in msg and len(lay.objects) == count, msg.splitlines()[0])
bpy.context.scene.easymold_props.clamp_method = 'SCREW'

# Generate uses the edited layout.
keys_in_layout = len(markers(lay, 'KEY'))
b = utils.MoldBuilder(obj, bpy.context.scene.easymold_props, 'PLAN')
b.prepare(use_layout=True)
axes = b.split_axes()
b.spec_from_layout(axes)
check("generate", len(b.keys) + 0 >= keys_in_layout - 2, f"{len(b.keys)} keys used of {keys_in_layout} "
      "(those in the way of the pour channel are left out)")
b.discard()
print("GEN", bpy.ops.object.easymold_generate())
bad = closed(utils.find_result(obj))
check("generate", not bad, f"pieces closed {bad}")

# 2. Pour from the top: on the object, and follows it.
obj, lay = setup(cup, mold_type='DIRECT', mold_parts='2', split_axis='X', pour_entry='TOP')
for o in markers(lay, 'POUR'):
    bpy.data.objects.remove(o)
pour, msg = add('POUR')
check("top pour", pour is not None and on_object(lay, pour.location), f"{msg}, on the object at "
      f"{tuple(round(c, 1) for c in pour.location)}")

# 3. Curved parting: keys on the surface; 2-part mold: keys on the parting line (also after moving it).
obj, lay = setup(egg, mold_type='DIRECT', mold_parts='2', split_axis='Y', curved_parting=True)
surface = utils._layout_surface(next(o for o in lay.objects if o.get(utils.ROLE) == 'PARTING_SURFACE'))
key, msg = add('KEY')
check("curved", key is not None and abs(key.location.y - surface.at(key.location)) < 1e-6, "key on the surface")
obj, lay = setup(egg, mold_type='SILICONE_TWO_PART')
line = next(o for o in lay.objects if o.get(utils.ROLE) == 'PARTING')
line.location.z += 2.0
key, msg = add('KEY')
check("two part", key is not None and abs(key.location.z - line.location.z) < 1e-6,
      f"key on the moved parting line z={line.location.z:.2f}")
key.location.z += 5
update()
check("two part", abs(key.location.z - line.location.z) < 1e-6, "moved key stays on it")
print("GEN", bpy.ops.object.easymold_generate())
bad = closed(utils.find_result(obj))
check("two part generate", not bad, f"pieces closed {bad}")

# 4. Shell: keys on the flanges.
obj, lay = setup(egg, mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X', clamp_method='SCREW')
key, msg = add('KEY')
bolt, msg2 = add('BOLT')
check("shell", key is not None and bolt is not None and abs(key.location.x - lay[utils.LAYOUT_CENTER][0]) < 1e-6,
      f"{msg} / {msg2}")
# Box: no keys.
obj, lay = setup(cup, mold_type='SILICONE_BOX')
key, msg = add('KEY')
check("box", key is None and "no registration keys" in msg, msg.splitlines()[0])

# 5. Blockers: over a hole that is still open, otherwise in the middle with a note.
obj, lay = setup(ring, mold_type="SILICONE_BOX", fill_holes=True, flat_base=False)  # a plug would close the hole
blockers = markers(lay, 'BLOCKER')
check("blocker", len(blockers) == 1, f"{len(blockers)} in the layout")
center = sum((v.co for v in blockers[0].data.vertices), Vector()) / len(blockers[0].data.vertices)
bpy.ops.object.easymold_select_element(name=blockers[0].name)
bpy.ops.object.easymold_delete_element()
blocker, msg = add('BLOCKER')
again = sum((v.co for v in blocker.data.vertices), Vector()) / len(blocker.data.vertices) if blocker else Vector()
check("blocker", blocker is not None and (again - center).length < 0.5, f"back over the hole ({msg})")
blocker2, msg = add('BLOCKER')
check("blocker", blocker2 is not None, f"none open any more: {msg}")
print("GEN", bpy.ops.object.easymold_generate())
bad = closed(utils.find_result(obj))
check("blocker generate", not bad, f"pieces closed {bad}")

print("FAILURES:", "none" if not failures else "")
for f in failures:
    print("  ", f)
