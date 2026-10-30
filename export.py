# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Write the printable pieces of a mold to STL / 3MF, each one turned so its best flat face is on the bed.

1 Blender unit = 1 mm. No bpy operators: works the same from the UI and from scripts.
"""
import os
import struct
import zipfile
from xml.sax.saxutils import quoteattr

import numpy as np
from mathutils import Matrix, Vector

MASTER_COPY = "easymold_master_copy"  # on the plain copies of the user's object (result and layout master)
DOWN = "easymold_down"  # on printable pieces: candidate directions (flattened xyz) to put on the bed, by preference
GAP = 10.0  # mm between pieces laid out on the 3MF plate


def set_down(obj, directions):
    obj[DOWN] = [c for d in directions for c in d]


def printable(collection, include_master=False):
    """Pieces of a result collection that get printed (not the silicone preview)."""
    return [o for o in collection.objects if o.type == 'MESH' and DOWN in o
            and (include_master or not (o.get(MASTER_COPY) or o.name.endswith("_Master")))]  # name: older results


def _arrays(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    co = np.empty(len(mesh.vertices) * 3, np.float64)
    mesh.vertices.foreach_get("co", co)
    tris = np.empty(len(mesh.loop_triangles) * 3, np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    return co.reshape(-1, 3), tris.reshape(-1, 3)


def _apply(matrix, co):
    m = np.array(matrix, np.float64)
    return co @ m[:3, :3].T + m[:3, 3]


def support_area(co, tris, d, tol=0.05):
    """Area of the triangles lying flat on the plane that touches the piece on the side of direction d."""
    d = np.asarray(d, np.float64)
    v = co[tris]
    cross = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    area = np.linalg.norm(cross, axis=1) / 2
    with np.errstate(invalid='ignore', divide='ignore'):
        normal = cross / (2 * area[:, None])
    reach = (co @ d).max()
    flat = (normal @ d > 0.995) & ((v @ d).min(axis=1) > reach - tol)
    return float(area[flat].sum())


def print_matrix(obj, co=None, tris=None):
    """World -> print matrix: the chosen face down, the piece's corner at the origin (z = 0 on the bed).

    Candidates are tried in order; the first one that rests on at least half the area of the best one
    wins (e.g. a shell half rests on its cut face unless the registration pegs stick out of it).
    """
    if co is None:
        co, tris = _arrays(obj)
    world = _apply(obj.matrix_world, co)
    flat = list(obj.get(DOWN, (0, 0, -1)))
    candidates = [Vector(flat[i:i + 3]) for i in range(0, len(flat), 3)] or [Vector((0, 0, -1))]
    areas = [support_area(world, tris, d) for d in candidates]
    best = max(areas)
    down = next(d for d, a in zip(candidates, areas) if a >= best * 0.5 - 1e-9)
    rot = down.rotation_difference(Vector((0, 0, -1))).to_matrix()
    if all(abs(c - round(c)) < 1e-6 for row in rot for c in row):
        rot = Matrix([[round(c) for c in row] for row in rot])  # axis turns: exact, no 1e-8 drift
    rot = rot.to_4x4()
    turned = _apply(rot, world)
    low = turned.min(axis=0)
    return Matrix.Translation(Vector(-low)) @ rot @ obj.matrix_world, down


def drop_degenerate(co, tris, eps=1e-9):
    """Leave out zero-area triangles (booleans leave some along straight cuts) and the ones that collapse
    once written as 32-bit floats; slicers warn about them."""
    v = co[tris]
    area = np.linalg.norm(np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]), axis=1)
    # Vertices as they will be written: two that round to the same float32 point are the same vertex.
    _unique, ids = np.unique(co.astype(np.float32), axis=0, return_inverse=True)
    t = ids.reshape(-1)[tris]
    keep = (area > eps) & (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    # ... and so can two triangles: keep one of each.
    _rows, first = np.unique(np.sort(t, axis=1), axis=0, return_index=True)
    once = np.zeros(len(tris), bool)
    once[first] = True
    return tris[keep & once]


def write_stl(path, co, tris):
    v = co[tris].astype(np.float32)
    n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    length = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, length, out=np.zeros_like(n), where=length > 0)
    data = np.zeros(len(tris), dtype=[('n', '<f4', 3), ('v', '<f4', (3, 3)), ('attr', '<u2')])
    data['n'] = n
    data['v'] = v
    with open(path, 'wb') as f:
        f.write(b"EasyMold binary STL (mm)".ljust(80, b" "))
        f.write(struct.pack('<I', len(tris)))
        f.write(data.tobytes())


_CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                  '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                  '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                  '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                  '</Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8"?>\n'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
         'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')


def write_3mf(path, pieces):
    """pieces: [(name, co, tris)] already placed on the plate."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n<resources>\n']
    for index, (name, co, tris) in enumerate(pieces, 1):
        parts.append(f'<object id="{index}" name={quoteattr(name)} type="model"><mesh><vertices>\n')
        parts.append("\n".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in co.tolist()))
        parts.append("\n</vertices><triangles>\n")
        parts.append("\n".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris.tolist()))
        parts.append("\n</triangles></mesh></object>\n")
    parts.append("</resources>\n<build>\n")
    parts.extend(f'<item objectid="{index}"/>\n' for index in range(1, len(pieces) + 1))
    parts.append("</build>\n</model>\n")
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", "".join(parts))


def _safe(name):
    return "".join(c if c.isalnum() or c in "-_+." else "_" for c in name)


def export_pieces(objects, folder, fmt, base_name):
    """Write the pieces turned for printing. Returns (written paths, [(name, size xyz, down)])."""
    os.makedirs(folder, exist_ok=True)
    placed, info, paths = [], [], []
    x = 0.0
    for obj in objects:
        co, tris = _arrays(obj)
        matrix, down = print_matrix(obj, co, tris)
        co = _apply(matrix, co)
        size = co.max(axis=0)
        info.append((obj.name, tuple(size), tuple(down)))
        if fmt == 'STL':
            path = os.path.join(folder, _safe(obj.name) + ".stl")
            write_stl(path, co, drop_degenerate(co, tris))
            paths.append(path)
        else:
            co[:, 0] += x  # side by side on the plate
            x += size[0] + GAP
            placed.append((obj.name, co, drop_degenerate(co, tris)))
    if fmt == '3MF':
        path = os.path.join(folder, _safe(base_name) + ".3mf")
        write_3mf(path, placed)
        paths.append(path)
    return paths, info
