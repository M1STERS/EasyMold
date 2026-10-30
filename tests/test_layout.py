# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import sys; sys.stdout.reconfigure(line_buffering=True)
import sys, time, importlib
import bpy, bmesh
from mathutils import Vector
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from EasyMold import utils

_orig_steps = utils.steps
def _traced_steps(builder, run):
    for frac, text in _orig_steps(builder, run):
        print(f"   step {frac:.2f} {text}  t={time.time():.1f}")
        yield frac, text
utils.steps = _traced_steps
_orig_bool = utils.MoldBuilder.boolean
def _traced_bool(self, obj, cutter, op):
    t = time.time()
    _orig_bool(self, obj, cutter, op)
    print(f"      bool {op} {obj.name} by {cutter.name}: {time.time()-t:.2f}s verts={len(obj.data.vertices)}")
utils.MoldBuilder.boolean = _traced_bool


def check(coll):
    for o in coll.objects:
        if o.type != 'MESH':
            print(f"     {o.name:28s} EMPTY role={o.get('easymold_role')} loc={tuple(round(c,1) for c in o.location)} size={o.empty_display_size:.2f}" if o.type == 'EMPTY' else o.name)
            continue
        bm = bmesh.new(); bm.from_mesh(o.data)
        nm = sum(1 for e in bm.edges if not e.is_manifold); vol = bm.calc_volume(); bm.free()
        print(f"     {o.name:28s} verts={len(o.data.vertices):7d} nonmanifold={nm:4d} vol={vol:10.1f} role={o.get('easymold_role')}")


def scene(kind):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if kind == "candle":
        bpy.ops.mesh.primitive_cylinder_add(radius=15, depth=40, location=(5, 3, 20))
    elif kind == "monkey":
        bpy.ops.mesh.primitive_monkey_add(size=40, location=(0, 0, 20))
        bpy.context.active_object.modifiers.new("s", 'SUBSURF').levels = 2
    elif kind == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=20, minor_radius=6, location=(0, 0, 6))
    return bpy.context.active_object


def run(label, op, **kw):
    t = time.time()
    r = op(**kw)
    print(f"## {label}: {r} in {time.time()-t:.1f}s")


# 1) Shell with 4 parts, tapered keys, straight pour hole
obj = scene("candle")
p = bpy.context.scene.easymold_props
p.mold_type = 'SILICONE_SHELL'; p.mold_parts = '4'; p.key_shape = 'TAPERED'; p.pour_style = 'HOLE'
run("shell 4 parts tapered", bpy.ops.object.easymold_generate)
check(bpy.data.collections["EasyMold_Cylinder"])

# 2) Pour hole on the split plane (direct, X split): the hole must be at x = center (5)
obj = scene("candle")
p = bpy.context.scene.easymold_props
p.mold_type = 'DIRECT'; p.mold_parts = '2'; p.split_axis = 'X'
b = utils.MoldBuilder(obj, p); b.prepare(True); b.auto_spec(b.split_axes())
print("POUR", [(tuple(round(c, 2) for c in pt), r) for pt, r in b.pours], "VENTS", len(b.vents)); b.abort()

# 3) Layout workflow: edit layout, move a key, delete a vent, add a key, regenerate
obj = scene("candle")
p = bpy.context.scene.easymold_props
p.mold_type = 'DIRECT'; p.mold_parts = '2'; p.split_axis = 'X'
run("edit layout", bpy.ops.object.easymold_edit_layout, reset=True)
lay = bpy.data.collections["EasyMold_Cylinder_Layout"]
check(lay)
keys = [o for o in lay.objects if o.get("easymold_role") == 'KEY']
vents = [o for o in lay.objects if o.get("easymold_role") == 'VENT']
keys[0].location.x += 2.5       # off the plane: must snap back
keys[0].scale = (1.5, 1.5, 1.5)  # bigger key
bpy.data.objects.remove(vents[0])
bpy.context.scene.cursor.location = (5, 3, 20)
bpy.context.view_layer.objects.active = keys[1]   # active = a marker: must resolve to the source
run("add key", bpy.ops.object.easymold_add_marker, role='KEY')
run("generate from layout", bpy.ops.object.easymold_generate)
check(bpy.data.collections["EasyMold_Cylinder"])
print("collections:", [c.name for c in bpy.data.collections])
run("generate again (replaces)", bpy.ops.object.easymold_generate)
print("collections:", [c.name for c in bpy.data.collections])
run("remove layout", bpy.ops.object.easymold_remove_layout)
print("collections:", [c.name for c in bpy.data.collections], "original hidden:", obj.hide_get())

# 4) Non-manifold shell (EXACT) with the pour hole on the split plane
obj = scene("monkey")
p = bpy.context.scene.easymold_props
p.mold_type = 'SILICONE_SHELL'; p.mold_parts = '2'; p.split_axis = 'X'
run("monkey shell", bpy.ops.object.easymold_generate)
check(bpy.data.collections["EasyMold_Suzanne"])

# 5) Error inside a run removes everything created so far
obj = scene("torus")
p = bpy.context.scene.easymold_props
p.mold_type = 'SILICONE_SHELL'; p.mold_parts = '2'; p.split_axis = 'Z'  # MoldError after the work copy exists
try:
    bpy.ops.object.easymold_generate()
except Exception as e:
    print("EXPECTED ERROR:", str(e).splitlines()[0])
print("after error collections:", [c.name for c in bpy.data.collections], "objects:", [o.name for o in bpy.data.objects])
