# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Print bed: warning when a piece doesn't fit; Split to Fit cuts it with filament pin holes."""
import sys, importlib, math
import bpy, bmesh
from mathutils import Vector
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from EasyMold import export, utils
failures = []


def check(label, ok, msg):
    if not ok:
        failures.append(f"{label}: {msg}")
        print("   FAIL", msg)


def candle(r=15, h=40):
    def make():
        bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=h, location=(0, 0, h / 2))
        return bpy.context.active_object
    return make


def islands(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    seen, count = set(), 0
    for f in bm.faces:
        if f in seen:
            continue
        count += 1
        stack = [f]; seen.add(f)
        while stack:
            for e in stack.pop().edges:
                for g in e.link_faces:
                    if g not in seen:
                        seen.add(g); stack.append(g)
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    return count, nm


def run(label, make, bed, shot=None, **settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = make()
    p = bpy.context.scene.easymold_props
    p.bed_x, p.bed_y, p.bed_z = bed
    for k, v in settings.items():
        setattr(p, k, v)
    sys.stdout.flush()
    print(f"## {label}: {bpy.ops.object.easymold_generate()}")
    coll = utils.find_result(obj)
    builder = utils.MoldBuilder.__new__(utils.MoldBuilder)
    builder.props = p
    pieces = export.printable(coll)
    for o in sorted(pieces, key=lambda o: o.name):
        j, size = builder._misfit(o)
        n, nm = islands(o)
        print(f"     {o.name:30s} fits={j is None} size={tuple(round(float(s)) for s in size)} "
              f"islands={n} nonmanifold={nm}")
        if p.split_to_fit:
            check(label, j is None, f"{o.name} still too big")
        check(label, n == 1 and nm == 0, f"{o.name} islands={n} nonmanifold={nm}")
    if shot:
        sc = bpy.context.scene
        sc.render.engine = 'BLENDER_WORKBENCH'; sc.display.shading.color_type = 'MATERIAL'
        sc.display.shading.show_cavity = True
        sc.render.resolution_x, sc.render.resolution_y = 900, 700
        for o in bpy.data.objects:
            o.hide_render = o.name not in coll.objects or "preview" in o.name or "Master" in o.name
        cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam")); cam.data.lens = 40
        sc.collection.objects.link(cam)
        cam.location, target = shot
        cam.rotation_euler = (Vector(target) - Vector(cam.location)).to_track_quat('-Z', 'Y').to_euler()
        sc.camera = cam
        sc.render.filepath = f"{OUT}\\bed_{label}.png"
        bpy.ops.render.render(write_still=True)
    return len(pieces)


# Fits: no warning, nothing cut.
run("fits", candle(), (220, 220, 250), mold_type='SILICONE_SHELL', mold_parts='2')
# Too tall for a 40 mm high printer: warning only.
n_warn = run("warn", candle(15, 80), (220, 220, 40), mold_type='SILICONE_SHELL', mold_parts='2')
# Same with Split to Fit.
n_split = run("split_shell", candle(15, 80), (220, 220, 40),
              ((120, -150, 110), (0, 0, 30)), mold_type='SILICONE_SHELL', mold_parts='2', split_to_fit=True)
check("split_shell", n_split > n_warn, f"no pieces were added ({n_warn} -> {n_split})")
run("split_direct", candle(15, 80), (60, 60, 60), ((140, -170, 120), (0, 0, 40)),
    mold_type='DIRECT', mold_parts='2', split_to_fit=True)
run("split_box", candle(40, 40), (60, 60, 250), ((150, -180, 120), (0, 0, 20)),
    mold_type='SILICONE_BOX', split_to_fit=True)
print("FAILURES:", failures or "none")
