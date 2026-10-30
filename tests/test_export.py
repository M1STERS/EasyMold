# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Export: every mold type to STL and 3MF; checks orientation, volume, file validity, re-import."""
import sys, importlib, os, zipfile, tempfile, math
import xml.etree.ElementTree as ET
import bpy, bmesh
import numpy as np
from mathutils import Vector
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
from shapes import make_pumpkin
EasyMold.register()
from EasyMold import export, utils


OUT = os.path.join(tempfile.gettempdir(), "easymold_export_test")
NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def candle():
    bpy.ops.mesh.primitive_cylinder_add(radius=15, depth=40, location=(0, 0, 20))
    return bpy.context.active_object


def volume(co, tris):
    v = co[tris]
    return abs(np.einsum('ij,ij->i', v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() / 6)


failures = []


def check(label, ok, msg):
    if not ok:
        failures.append(f"{label}: {msg}")
        print("   FAIL", msg)


def run(label, make, **settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = make()
    p = bpy.context.scene.easymold_props
    for k, v in settings.items():
        setattr(p, k, v)
    print(f"## {label}: {bpy.ops.object.easymold_generate()}")
    coll = utils.find_result(obj)
    pieces = export.printable(coll)
    names = sorted(o.name for o in pieces)
    check(label, not any("preview" in n or n.endswith("_Master") for n in names), f"wrong pieces {names}")
    folder = os.path.join(OUT, label)
    for fmt in ('STL', '3MF'):
        p.export_format, p.export_folder = fmt, folder
        bpy.context.view_layer.objects.active = pieces[0]
        r = bpy.ops.object.easymold_export()
        check(label, r == {'FINISHED'}, f"{fmt} export {r}")
    for o in pieces:
        co, tris = export._arrays(o)
        matrix, down = export.print_matrix(o, co, tris)
        placed = export._apply(matrix, co)
        world = export._apply(o.matrix_world, co)
        area = export.support_area(placed, tris, (0, 0, -1))
        dims = placed.max(axis=0) - placed.min(axis=0)
        print(f"     {o.name:26s} down={tuple(round(c) for c in down)} bed area={area:7.0f} "
              f"size={tuple(round(float(d)) for d in dims)}")
        check(label, abs(placed[:, 2].min()) < 1e-5 and placed[:, :2].min() > -1e-5, f"{o.name} not at origin (min {placed.min(axis=0)})")
        check(label, area > 0, f"{o.name} has nothing flat on the bed")
        check(label, abs(volume(placed, tris) - volume(world, tris)) < 1e-3 * volume(world, tris),
              f"{o.name} volume changed")
    # STL: one per piece, re-import and compare triangle counts.
    for o in pieces:
        path = os.path.join(folder, export._safe(o.name) + ".stl")
        check(label, os.path.exists(path), f"missing {path}")
        before = set(bpy.data.objects)
        bpy.ops.wm.stl_import(filepath=path)
        new = [x for x in bpy.data.objects if x not in before][0]
        with open(path, 'rb') as f:
            f.seek(80)
            written = int.from_bytes(f.read(4), 'little')
        got = len(new.data.polygons)
        # The importer also merges the odd duplicate triangle a boolean leaves.
        check(label, got == written, f"{o.name} STL triangles {got} vs {written}")
        z = min((new.matrix_world @ v.co).z for v in new.data.vertices)
        check(label, abs(z) < 1e-3, f"{o.name} STL min z {z}")
    # 3MF: one object per piece, valid XML, sensible triangle indices.
    path = os.path.join(folder, export._safe(obj.name) + ".3mf")
    with zipfile.ZipFile(path) as z:
        check(label, {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"} <= set(z.namelist()), "3MF parts")
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    objects = root.findall(f"{NS}resources/{NS}object")
    check(label, len(objects) == len(pieces) and root.get("unit") == "millimeter", "3MF objects/unit")
    xs = []
    for ob in objects:
        verts = ob.findall(f"{NS}mesh/{NS}vertices/{NS}vertex")
        tris = ob.findall(f"{NS}mesh/{NS}triangles/{NS}triangle")
        top = max(int(t.get(k)) for t in tris for k in ("v1", "v2", "v3"))
        check(label, top < len(verts), "3MF index out of range")
        x = [float(v.get("x")) for v in verts]
        xs.append((min(x), max(x)))
    xs.sort()
    check(label, all(a[1] <= b[0] for a, b in zip(xs, xs[1:])), "3MF pieces overlap on the plate")
    print(f"     files: {sorted(os.listdir(folder))}")


run("shell2", candle, mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X')
run("shell2_clip", candle, mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X', clamp_method='CLIP')
run("pumpkin4", make_pumpkin, mold_type='SILICONE_SHELL', mold_parts='4')
run("box", candle, mold_type='SILICONE_BOX')
run("direct1", candle, mold_type='DIRECT', mold_parts='1')
run("direct2", candle, mold_type='DIRECT', mold_parts='2', split_axis='X')
run("direct2Z", candle, mold_type='DIRECT', mold_parts='2', split_axis='Z')
run("direct4", candle, mold_type='DIRECT', mold_parts='4', split_axis='X')
run("two_part", candle, mold_type='SILICONE_TWO_PART')

# Unsaved .blend + relative folder: clear error, no crash.
bpy.context.scene.easymold_props.export_folder = "//x"
print("UNSAVED:", bpy.ops.object.easymold_export() if bpy.ops.object.easymold_export.poll() else "no poll")
print("FAILURES:", failures or "none")
