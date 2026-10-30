# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import sys, importlib, time, math
import bpy, bmesh
from mathutils import Vector
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from EasyMold import utils
from shapes import make_pumpkin


def union(a, b):
    m = a.modifiers.new("u", 'BOOLEAN'); m.object = b; m.operation = 'UNION'; m.solver = 'EXACT'
    bpy.context.view_layer.objects.active = a
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(b)
    return a


def mug():
    bpy.ops.mesh.primitive_cylinder_add(radius=14, depth=36, location=(0, 0, 18), vertices=48)
    body = bpy.context.active_object
    bpy.ops.mesh.primitive_torus_add(major_radius=9, minor_radius=2.5, location=(15, 0, 18), rotation=(math.pi / 2, 0, 0))
    body = union(body, bpy.context.active_object)
    body.name = "Mug"
    return body


def peanut():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=12, location=(-10, 0, 12))
    a = bpy.context.active_object
    bpy.ops.mesh.primitive_uv_sphere_add(radius=10, location=(10, 0, 10))
    a = union(a, bpy.context.active_object)
    a.name = "Peanut"
    return a


def tee():
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 20))
    top = bpy.context.active_object; top.scale = (40, 10, 8); bpy.ops.object.transform_apply(scale=True)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 8))
    stem = bpy.context.active_object; stem.scale = (10, 10, 16); bpy.ops.object.transform_apply(scale=True)
    t = union(top, stem)
    t.name = "Tee"
    return t


def monkey():
    bpy.ops.mesh.primitive_monkey_add(size=40, location=(0, 0, 20))
    bpy.context.active_object.modifiers.new("s", 'SUBSURF').levels = 2
    return bpy.context.active_object


def genus(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    chi = len(bm.verts) - len(bm.edges) + len(bm.faces)
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    return (2 - chi) / 2, nm


def report(coll):
    for o in coll.objects:
        if o.type != 'MESH':
            continue
        bm = bmesh.new(); bm.from_mesh(o.data)
        nm = sum(1 for e in bm.edges if not e.is_manifold); vol = bm.calc_volume(); bm.free()
        print(f"     {o.name:30s} nonmanifold={nm:3d} vol={vol:9.0f} role={o.get('easymold_role')}")


def scene(make, **settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = make()
    p = bpy.context.scene.easymold_props
    for k, v in settings.items():
        setattr(p, k, v)
    for o in bpy.context.view_layer.objects.selected:
        if o is not None:
            o.select_set(False)
    bpy.context.view_layer.objects.active = obj
    return obj, p


def run(label, op, **kw):
    t = time.time()
    r = op(**kw)
    print(f"## {label}: {r} in {time.time() - t:.1f}s")


# 3) Through-hole blockers: mug handle (hole along Y)
obj, p = scene(mug, mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X')
print("mug genus before:", genus(obj))
run("mug shell (auto blockers)", bpy.ops.object.easymold_generate)
coll = bpy.data.collections["EasyMold_Mug"]
report(coll)
print("master genus after:", genus(coll.objects["Mug_Master"]))

# 3b) Same in the layout: blockers become editable objects; deleting one keeps the hole
obj, p = scene(mug, mold_type='SILICONE_BOX')
run("mug layout", bpy.ops.object.easymold_edit_layout, reset=True)
lay = bpy.data.collections["EasyMold_Mug_Layout"]
print("layout blockers:", [o.name for o in lay.objects if o.get('easymold_role') == 'BLOCKER'])
master = next(o for o in lay.objects if o.get('easymold_role') == 'MASTER')
print("layout master materials:", [m.name for m in master.data.materials])
run("mug from layout", bpy.ops.object.easymold_generate)
print("master genus (layout path):", genus(bpy.data.collections["EasyMold_Mug"].objects["Mug_Master"]))

# 4) Smart vents: peanut, split X -> pour on the saddle, vents on the two tops
obj, p = scene(peanut, mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X', flat_base=True)
b = utils.MoldBuilder(obj, p); b.prepare(True); b.auto_spec(b.split_axes())
print("PEANUT pour", [tuple(round(c, 1) for c in pt) for pt, r in b.pours],
      "vents", [tuple(round(c, 1) for c in pt) for pt, r in b.vents]); b.abort()

# 3c) Undercut analysis: T shape, direct mold split Z -> the underside of the T bar is fine (bottom part
#     moves down), split X -> the bar's underside is an undercut? (no: parts move along X)
for axis in ('Z', 'Y'):
    obj, p = scene(tee, mold_type='DIRECT', mold_parts='2', split_axis=axis)
    b = utils.MoldBuilder(obj, p); b.prepare(True); b.analyze_undercuts(b.split_axes())
    import numpy as np
    counts = np.bincount(b.face_classes, minlength=3)
    print(f"TEE split {axis}: ok={counts[0]} no_draft={counts[1]} undercut={counts[2]} notes={b.notes}"); b.abort()

# 1+2) Bolts and anchors on the pumpkin
obj, p = scene(make_pumpkin, mold_type='SILICONE_SHELL', mold_parts='4', silicone_thickness=2.5, wall_thickness=2.0,
               base_thickness=2.0, lip_height=0.6, flat_base=False, voxel_size=0.4, flange_width=2.0,
               flange_thickness=2.0, key_shape='TAPERED', key_radius=1.2, sprue_radius=2.0, sprue_height=5.0)
b = utils.MoldBuilder(obj, p); b.prepare(True); axes = b.split_axes(); b.auto_spec(axes)
print("PUMPKIN keys", len(b.keys), "bolts", len(b.bolts), "anchors", len(b.anchor_spheres(axes)),
      "vents", [tuple(round(c, 1) for c in pt) for pt, r in b.vents])
s = p.silicone_thickness
nut_r = b._bolt()['nut_r']
for axis, c in b.bolts:
    d = b.bvh.find_nearest(c)[3]
    print(f"BOLT {axis} z={c.z:5.1f} dist={d:5.2f} nut_to_silicone={(d - s) - nut_r:5.2f}")
b.abort()
run("pumpkin full", bpy.ops.object.easymold_generate)
report(bpy.data.collections["EasyMold_Pumpkin"])

# 5) Repair: Suzanne rebuilt with voxels
obj, p = scene(monkey, mold_type='SILICONE_BOX', repair_mode='REMESH')
run("monkey rebuild", bpy.ops.object.easymold_generate)
report(bpy.data.collections["EasyMold_Suzanne"])
