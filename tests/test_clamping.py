# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Clamping methods (SCREW / CLIP / BAND / NONE) on every mold type; renders a few to check visually."""
import sys, importlib, time, math
import bpy, bmesh
from mathutils import Vector
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from shapes import make_pumpkin


def candle():
    bpy.ops.mesh.primitive_cylinder_add(radius=15, depth=40, location=(0, 0, 20))
    return bpy.context.active_object


def render(name, cam_loc, target, lens=45):
    sc = bpy.context.scene
    for o in bpy.data.objects:
        if o.type == 'MESH' and (o.hide_get() or "Silicone" in o.name or "Master" in o.name):
            o.hide_render = True
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


def run(label, make, shot=None, **settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = make()
    p = bpy.context.scene.easymold_props
    for k, v in settings.items():
        setattr(p, k, v)
    t = time.time()
    try:
        r = bpy.ops.object.easymold_generate()
    except Exception as e:
        print(f"## {label}: EXC {str(e).splitlines()[-1]}")
        return
    print(f"## {label}: {r} in {time.time() - t:.1f}s")
    coll = bpy.data.collections[f"EasyMold_{obj.name}"]
    for o in coll.objects:
        bm = bmesh.new(); bm.from_mesh(o.data)
        nm = sum(1 for e in bm.edges if not e.is_manifold); vol = bm.calc_volume(); bm.free()
        print(f"     {o.name:28s} nonmanifold={nm:3d} vol={vol:9.0f}")
    if shot:
        render(label, *shot)


for method in ('SCREW', 'CLIP', 'BAND', 'NONE'):
    run(f"box_{method}", candle, ((75, -95, 70), (0, 0, 25)) if method != 'NONE' else None,
        mold_type='SILICONE_BOX', clamp_method=method, explode=False)
for method in ('SCREW', 'CLIP', 'BAND'):
    run(f"shell2_{method}", candle, ((80, -100, 60), (0, 0, 20)) if method == 'CLIP' else None,
        mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X', clamp_method=method)
run("pumpkin4_CLIP", make_pumpkin, ((95, -110, 60), (0, 0, 22)), mold_type='SILICONE_SHELL', mold_parts='4',
    clamp_method='CLIP')
for method in ('SCREW', 'CLIP', 'BAND'):
    run(f"direct2_{method}", candle, ((90, -110, 60), (0, 0, 20)), mold_type='DIRECT', mold_parts='2',
        split_axis='X', clamp_method=method)
for method in ('SCREW', 'BAND'):
    run(f"direct4_{method}", candle, ((90, -110, 60), (0, 0, 20)) if method == 'SCREW' else None,
        mold_type='DIRECT', mold_parts='4', split_axis='X', clamp_method=method)
run("direct2Z_BAND", candle, ((90, -110, 60), (0, 0, 20)), mold_type='DIRECT', mold_parts='2', split_axis='Z',
    clamp_method='BAND')
