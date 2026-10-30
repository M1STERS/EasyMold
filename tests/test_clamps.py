# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import sys, importlib, time, math
import bpy, bmesh
from mathutils import Vector
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from shapes import make_pumpkin


def render(name, cam_loc, target, lens=50):
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'
    sc.display.shading.color_type = 'MATERIAL'
    sc.display.shading.show_cavity = True
    sc.render.resolution_x, sc.render.resolution_y = 900, 700
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    cam.data.lens = lens
    sc.collection.objects.link(cam)
    cam.location = cam_loc
    cam.rotation_euler = (Vector(target) - Vector(cam_loc)).to_track_quat('-Z', 'Y').to_euler()
    sc.camera = cam
    sc.render.filepath = f"{OUT}\\{name}.png"
    bpy.ops.render.render(write_still=True)


def run(label, make, settings, shot=None):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = make()
    p = bpy.context.scene.easymold_props
    for k, v in settings.items():
        setattr(p, k, v)
    t = time.time()
    r = bpy.ops.object.easymold_generate()
    print(f"## {label}: {r} in {time.time() - t:.1f}s")
    coll = bpy.data.collections[f"EasyMold_{obj.name}"]
    for o in coll.objects:
        bm = bmesh.new(); bm.from_mesh(o.data)
        nm = sum(1 for e in bm.edges if not e.is_manifold); vol = bm.calc_volume(); bm.free()
        print(f"     {o.name:28s} nonmanifold={nm:3d} vol={vol:9.0f}")
    if shot:
        for o in coll.objects:
            if "Silicone" in o.name or "Master" in o.name:
                o.hide_render = True
        render(label, *shot)


def candle():
    bpy.ops.mesh.primitive_cylinder_add(radius=15, depth=40, location=(0, 0, 20))
    return bpy.context.active_object


run("clamp_box", candle, {"mold_type": 'SILICONE_BOX', "explode": False},
    shot=((45, -55, 25), (20, -20, 2), 60))
run("clamp_shell2", candle, {"mold_type": 'SILICONE_SHELL', "mold_parts": '2', "split_axis": 'X'},
    shot=((60, -70, 35), (18, -14, 2), 55))
run("clamp_pumpkin4", make_pumpkin, {
    "mold_type": 'SILICONE_SHELL', "mold_parts": '4', "silicone_thickness": 2.5, "wall_thickness": 2.0,
    "base_thickness": 2.0, "lip_height": 0.6, "flat_base": False, "voxel_size": 0.4, "flange_width": 2.0,
    "flange_thickness": 2.0, "key_shape": 'TAPERED', "key_radius": 1.2, "sprue_radius": 2.0, "sprue_height": 5.0},
    shot=((70, -90, 50), (0, 0, 5), 40))
