# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shading of the generated pieces (session 7: the NozzleSockTest pieces looked streaky). The user's object had
custom normals; the booleans carried them into every piece and the new faces (cut faces, block sides) got
meaningless ones. Objects with custom normals: no generated piece may keep them (only the plain master copy),
cut faces must be shaded flat, steep edges sharp, curved surfaces smooth."""
import sys, importlib
import bpy, bmesh, numpy as np
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


def deviation(mesh):
    """Per corner: (angle in degrees between its shading normal and its face's normal, face index)."""
    n = len(mesh.polygons)
    start = np.empty(n, dtype=np.int64)
    total = np.empty(n, dtype=np.int64)
    mesh.polygons.foreach_get("loop_start", start)
    mesh.polygons.foreach_get("loop_total", total)
    order = np.argsort(start)
    face = np.repeat(order, total[order])
    bm = bmesh.new()
    bm.from_mesh(mesh)
    fn = np.array([f.normal for f in bm.faces])
    bm.free()
    cn = np.empty(len(mesh.loops) * 3)
    mesh.corner_normals.foreach_get("vector", cn)
    cn = cn.reshape(-1, 3)
    return np.degrees(np.arccos(np.clip(np.sum(cn * fn[face], axis=1), -1, 1))), face


def with_custom_normals(obj):
    """Custom normals as a modeller leaves them: smooth vertex normals (here, from the sphere's centre)."""
    me = obj.data
    me.normals_split_custom_set_from_vertices([v.co.normalized() for v in me.vertices])


def sock():  # like the user's nozzle sock: a short tube with a nozzle hole through it
    bpy.ops.mesh.primitive_cylinder_add(radius=6, depth=5, vertices=64, location=(0, 0, 2.5))
    obj = bpy.context.active_object
    bpy.ops.mesh.primitive_cylinder_add(radius=2.5, depth=8, vertices=48, location=(0, 0, 2.5))
    hole = bpy.context.active_object
    mod = obj.modifiers.new("hole", 'BOOLEAN')
    mod.operation, mod.object = 'DIFFERENCE', hole
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="hole")
    bpy.data.objects.remove(hole)
    obj.select_set(True)
    return obj


for label, make, settings in (
        ("sock, direct 2 parts, bands, core", 'SOCK', dict(mold_type='DIRECT', clamp_method='BAND', auto_core=True)),
        ("sphere, direct 2 parts, top pour", 'SPHERE', dict(mold_type='DIRECT', pour_entry='TOP', clamp_method='CLIP')),
        ("sphere, shell 2 parts", 'SPHERE', dict(mold_type='SILICONE_SHELL', clamp_method='SCREW')),
        ("sphere, 2-part silicone", 'SPHERE', dict(mold_type='SILICONE_TWO_PART')),
        ("sock, box", 'SOCK', dict(mold_type='SILICONE_BOX'))):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if make == 'SPHERE':
        bpy.ops.mesh.primitive_uv_sphere_add(radius=12, segments=64, ring_count=32, location=(0, 0, 15))
        src = bpy.context.active_object
    else:
        src = sock()
    with_custom_normals(src)
    before = [tuple(v.co) for v in src.data.vertices]
    p = bpy.context.scene.easymold_props
    p.mold_parts, p.split_axis, p.explode, p.voxel_size = '2', 'X', False, 0.3
    for k, v in settings.items():
        setattr(p, k, v)
    print(f"## {label}", bpy.ops.object.easymold_generate())
    check(label, [tuple(v.co) for v in src.data.vertices] == before and src.data.has_custom_normals,
          "original untouched (custom normals too)")
    coll = utils.find_result(src)
    for obj in sorted(coll.objects, key=lambda o: o.name):
        mesh = obj.data
        if obj.name.endswith("_Master"):
            check(label, mesh.has_custom_normals, f"{obj.name} keeps the object's custom normals")
            continue
        check(label, not mesh.has_custom_normals, f"{obj.name}: no custom normals")
        dev, face = deviation(mesh)
        bm = bmesh.new()
        bm.from_mesh(mesh)
        steep = sum(1 for e in bm.edges if len(e.link_faces) == 2 and e.smooth and e.calc_face_angle(0) > 0.62)
        bm.free()
        check(label, steep == 0, f"{obj.name}: {steep} edges steeper than 35° left smooth")
        if "_Mold_X" in obj.name:
            minus = next(o for o in coll.objects if o.name.endswith("_Mold_X-"))
            plane = max(v.co.x for v in minus.data.vertices)  # the X- piece ends on it (pegs stick out of X+)
            cut = [k for k, f in enumerate(mesh.polygons) if abs(abs(f.normal.x) - 1) < 1e-3
                   and abs(f.center.x - plane) < 1e-3]
            on_cut = np.isin(face, cut)
            worst = float(dev[on_cut].max()) if on_cut.any() else 0.0
            check(label, len(cut) > 4 and worst < 0.5,
                  f"{obj.name}: cut face ({len(cut)} faces) shaded flat, worst corner {worst:.2f}°")
        print(f"     {obj.name:36s} nonmanifold={utils.open_edges(mesh):4d}")

print("FAILURES:", "none" if not failures else "")
for f in failures:
    print("  ", f)
