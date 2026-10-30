# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Cut faces filled right (a past bug: with the pour from the top, the cut faces of two objects covered the cavities).
A cut near a pole of the object's mesh leaves clusters of nearly coincident corners on the cut face, which Blender's
triangulation filled wrongly. Dense spheres with their poles on the split plane, several molds/settings: on each
piece's cut face (sampled on a grid) nothing may be covered twice and nothing may cover the cavity."""
import sys, importlib, math
import bpy, numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
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


def spheres(count):
    objs = []
    for n in range(count):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=15, segments=256, ring_count=128, location=(0, n * 40, 20))
        objs.append(bpy.context.active_object)  # poles at z = 5 and 35, on the plane x = 0
    return objs


def array(mesh):
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def cut_check(label, coll, axis_piece):
    pieces = {o.name[-2:]: o for o in coll.objects if "_Mold_X" in o.name}
    master = next(o for o in coll.objects if o.name.endswith("_Master"))
    bvh = BVHTree.FromPolygons([master.matrix_world @ v.co for v in master.data.vertices],
                               [tuple(f.vertices) for f in master.data.polygons])
    off = master.matrix_world.translation
    plane = array(pieces["X-"].data)[:, 0].max()
    co_all = array(pieces["X-"].data)
    Y, Z = np.meshgrid(np.linspace(co_all[:, 1].min(), co_all[:, 1].max(), 160),
                       np.linspace(co_all[:, 2].min(), co_all[:, 2].max(), 120))
    P = np.stack([Y.ravel(), Z.ravel()], 1)
    inside_obj = np.zeros(len(P), bool)
    for n, (y, z) in enumerate(P):
        q = Vector((plane, y, z)) + off
        hits = 0
        for _ in range(20):
            h = bvh.ray_cast(q, Vector((0, 0, 1)))[0]
            if h is None:
                break
            hits += 1
            q = h + Vector((0, 0, 1e-4))
        inside_obj[n] = hits % 2 == 1
    for side, obj in sorted(pieces.items()):
        co = array(obj.data)
        cover = np.zeros(len(P), int)
        for f in obj.data.polygons:
            if abs(f.normal.x) < 0.999 or abs(f.center.x - plane) > 0.01:
                continue
            ring = co[list(f.vertices)][:, 1:]
            inside = np.zeros(len(P), bool)
            for (y0, z0), (y1, z1) in zip(ring, np.roll(ring, -1, 0)):
                cond = (z0 > P[:, 1]) != (z1 > P[:, 1])
                with np.errstate(divide='ignore', invalid='ignore'):
                    x = y0 + (P[:, 1] - z0) * (y1 - y0) / (z1 - z0)
                inside ^= cond & (P[:, 0] < x)
            cover += inside
        overlap, over = int((cover > 1).sum()), int((inside_obj & (cover > 0)).sum())
        check(label, overlap == 0 and over == 0 and utils.open_edges(obj.data) == 0,
              f"{side}: overlap {overlap}, over the cavity {over} (of {int(inside_obj.sum())}), "
              f"open {utils.open_edges(obj.data)}")


for label, count, settings in (
        ("one, top pour", 1, dict(pour_entry='TOP')),
        ("one, from below", 1, dict(pour_entry='BOTTOM')),
        ("two, top pour, clips", 2, dict(pour_entry='TOP', clamp_method='CLIP')),
        ("two, top pour, screws", 2, dict(pour_entry='TOP', clamp_method='SCREW')),
        ("two, no pour", 2, dict(use_sprues=False, clamp_method='NONE'))):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    objs = spheres(count)
    p = bpy.context.scene.easymold_props
    p.mold_type, p.mold_parts, p.split_axis, p.explode, p.voxel_size = 'DIRECT', '2', 'X', False, 0.3
    p.tray_arrange = 'KEEP'
    for k, v in settings.items():
        setattr(p, k, v)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    print(f"## {label}", bpy.ops.object.easymold_generate())
    cut_check(label, utils.find_result(objs[0]), 'X')

print("FAILURES:", "none" if not failures else "")
for f in failures:
    print("  ", f)
