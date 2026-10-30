# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import sys, time, traceback, importlib
import bpy, bmesh
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()


def make_shape(kind):
    if kind == "candle":
        bpy.ops.mesh.primitive_cylinder_add(radius=15, depth=40, location=(5, 3, 20))
    elif kind == "monkey":
        bpy.ops.mesh.primitive_monkey_add(size=40, location=(0, 0, 20))
        bpy.context.active_object.modifiers.new("s", 'SUBSURF').levels = 2
    elif kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=25, segments=128, ring_count=64, location=(0, 0, 25))
    elif kind == "lying":
        bpy.ops.mesh.primitive_cylinder_add(radius=15, depth=40, location=(0, 0, 15), rotation=(1.5708, 0, 0))
    elif kind == "tilted":
        bpy.ops.mesh.primitive_cube_add(size=30, location=(0, 0, 20), rotation=(0.5, 0.3, 0))
    elif kind == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=20, minor_radius=6, location=(0, 0, 6))
    elif kind == "sock":
        # Hollow box open at the top (like a heater block sock) + core object
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
        outer = bpy.context.active_object
        outer.scale = (24, 18, 14)
        bpy.ops.object.transform_apply(scale=True)
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 3))
        hole = bpy.context.active_object
        hole.scale = (20, 14, 14)
        bpy.ops.object.transform_apply(scale=True)
        m = outer.modifiers.new("b", 'BOOLEAN'); m.object = hole; m.operation = 'DIFFERENCE'
        bpy.context.view_layer.objects.active = outer
        bpy.ops.object.modifier_apply(modifier=m.name)
        # core sticks out of the top so it can be held
        hole.name = "Core"
        hole.scale = (1, 1, 22 / 14)
        hole.location.z = 7  # z -4..18: fills the hollow and sticks out of the block top
        bpy.context.view_layer.objects.active = outer
        bpy.context.scene.easymold_props.core_object = hole
        return outer
    return bpy.context.active_object


def check(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    bad = [e for e in bm.edges if not e.is_manifold]
    nm = len(bad)
    if bad:
        print("        nonmanifold at", [tuple(round(c, 1) for c in (e.verts[0].co + e.verts[1].co) / 2) for e in bad[:4]])
    vol = bm.calc_volume()
    islands = len(set(f.index for f in bm.faces))  # placeholder
    bm.free()
    return nm, vol


cases = [
    ("candle", "SILICONE_BOX", {}),
    ("candle", "SILICONE_SHELL", {"mold_parts": '2', "split_axis": 'X'}),
    ("candle", "SILICONE_SHELL", {"mold_parts": '1'}),
    ("torus", "SILICONE_SHELL", {"mold_parts": '2', "split_axis": 'Y'}),
    ("candle", "DIRECT", {"mold_parts": '2', "split_axis": 'X'}),
    ("torus", "DIRECT", {"mold_parts": '2', "split_axis": 'Z'}),
    ("candle", "DIRECT", {"mold_parts": '4', "split_axis": 'X'}),
    ("candle", "DIRECT", {"mold_parts": '1'}),
    ("sock", "DIRECT", {"mold_parts": '2', "split_axis": 'X'}),
    ("candle", "SILICONE_SHELL", {"mold_parts": '2', "split_axis": 'Z'}),  # expected error
    ("monkey", "DIRECT", {"mold_parts": '2', "split_axis": 'Y'}),  # non-manifold input -> EXACT + warning
    ("monkey", "SILICONE_BOX", {}),
    ("sphere", "SILICONE_SHELL", {"mold_parts": '2', "split_axis": 'Y', "voxel_size": 0.3}),
    ("sphere", "SILICONE_BOX", {}),
    ("lying", "SILICONE_BOX", {}),
    ("tilted", "SILICONE_SHELL", {"mold_parts": '1'}),
    ("monkey", "SILICONE_SHELL", {"mold_parts": '2', "split_axis": 'X'}),
]

for shape, mode, extra in cases:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = make_shape(shape)
    props = bpy.context.scene.easymold_props
    props.mold_type = mode
    for k, v in extra.items():
        setattr(props, k, v)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    t = time.time()
    try:
        r = bpy.ops.object.easymold_generate()
    except Exception as e:
        print(f"## {shape} {mode} {extra}: EXC {str(e).splitlines()[-1] if str(e) else e}")
        continue
    print(f"## {shape} {mode} {extra}: {r} in {time.time()-t:.1f}s")
    col = bpy.data.collections.get(f"EasyMold_{obj.name}")
    if col:
        for o in col.objects:
            nm, vol = check(o)
            print(f"     {o.name:28s} verts={len(o.data.vertices):7d} nonmanifold={nm:4d} vol={vol:10.1f} loc={tuple(round(c,1) for c in o.location)}")
    print("     leftover objects:", [o.name for o in bpy.data.objects if not o.users_collection or o.users_collection[0].name == 'Scene Collection'])
    print("     node groups:", list(bpy.data.node_groups.keys()))
