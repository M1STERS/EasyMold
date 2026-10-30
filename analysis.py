# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Geometry analysis used to prepare a master: through-holes, undercuts and air traps.

Pure functions working on mesh data / BVH trees, no scene changes.
"""
from collections import deque

import numpy as np
from mathutils import Vector

AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}

# Face classes for the undercut analysis.
OK, NO_DRAFT, UNDERCUT = 0, 1, 2


def mesh_arrays(mesh):
    """Vertex positions, vertex normals and edges of a mesh as numpy arrays."""
    n = len(mesh.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    normals = np.empty(n * 3, dtype=np.float64)
    mesh.vertex_normals.foreach_get("vector", normals)
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", edges)
    return co.reshape(-1, 3), normals.reshape(-1, 3), edges.reshape(-1, 2)


def through_holes(bvh, bmin, bmax, axis, cell, min_cells=4):
    """Holes that go right through the object along `axis` (seen as gaps enclosed by its silhouette).

    Yields (outline points in the plane of the other two axes, depth along `axis`) where a blocker
    wall would close the hole: the median middle depth of the object around the hole.
    """
    i = AXIS_INDEX[axis]
    j, k = [n for n in range(3) if n != i]
    nj = int((bmax[j] - bmin[j]) / cell) + 3
    nk = int((bmax[k] - bmin[k]) / cell) + 3
    oj, ok = bmin[j] - cell, bmin[k] - cell
    forward = Vector((0, 0, 0))
    forward[i] = 1.0

    middle = {}  # occupied cell -> middle depth of the object there
    for a in range(nj):
        for b in range(nk):
            origin = Vector((0, 0, 0))
            origin[j] = oj + (a + 0.5) * cell
            origin[k] = ok + (b + 0.5) * cell
            origin[i] = bmin[i] - 1
            entry = bvh.ray_cast(origin, forward)[0]
            if entry is None:
                continue
            origin[i] = bmax[i] + 1
            exit_ = bvh.ray_cast(origin, -forward)[0]
            middle[(a, b)] = (entry[i] + (exit_[i] if exit_ is not None else entry[i])) / 2

    def neighbors4(c):
        a, b = c
        for na, nb in ((a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)):
            if 0 <= na < nj and 0 <= nb < nk:
                yield (na, nb)

    # Empty cells reachable from the border are outside; the other empty cells are holes.
    outside = set()
    queue = deque(c for c in ((a, b) for a in range(nj) for b in (0, nk - 1)) if c not in middle)
    queue.extend(c for c in ((a, b) for a in (0, nj - 1) for b in range(nk)) if c not in middle)
    outside.update(queue)
    while queue:
        for n in neighbors4(queue.popleft()):
            if n not in middle and n not in outside:
                outside.add(n)
                queue.append(n)

    seen = set()
    for a in range(nj):
        for b in range(nk):
            start = (a, b)
            if start in middle or start in outside or start in seen:
                continue
            component = [start]
            seen.add(start)
            queue = deque([start])
            while queue:
                for n in neighbors4(queue.popleft()):
                    if n not in middle and n not in seen:
                        seen.add(n)
                        component.append(n)
                        queue.append(n)
            if len(component) < min_cells:
                continue
            ring = {(ca + da, cb + db) for ca, cb in component for da in (-1, 0, 1) for db in (-1, 0, 1)}
            depths = sorted(middle[c] for c in ring if c in middle)
            if not depths:
                continue
            points = [(oj + (ca + 0.5) * cell, ok + (cb + 0.5) * cell) for ca, cb in component]
            yield points, depths[len(depths) // 2]


def face_arrays(mesh):
    """Face centers and normals as numpy arrays."""
    n = len(mesh.polygons)
    centers = np.empty(n * 3, dtype=np.float64)
    mesh.polygons.foreach_get("center", centers)
    normals = np.empty(n * 3, dtype=np.float64)
    mesh.polygons.foreach_get("normal", normals)
    return centers.reshape(-1, 3), normals.reshape(-1, 3)


def classify_faces(bvh, centers, normals, pull_options, ignore, min_draft=0.035):
    """Classify every face as OK / NO_DRAFT / UNDERCUT.

    pull_options: list of (N, 3) arrays; option k gives, per face, the direction in which the mold
    surface touching it moves away from the master. A face releases if for some option it faces that
    way with some draft (min_draft = sin of the angle) and nothing of the object is in the way.
    ignore: (N,) bool array of faces that touch no mold part (e.g. the open side) -> always OK.
    """
    best = np.full(len(centers), UNDERCUT, dtype=np.int8)
    for dirs in pull_options:
        facing = np.einsum('ij,ij->i', normals, dirs)
        best[(facing >= -min_draft) & (facing < min_draft)] = np.minimum(
            best[(facing >= -min_draft) & (facing < min_draft)], NO_DRAFT)
        # Facing the pull direction: OK unless the object itself is in the way.
        for index in np.nonzero((facing >= min_draft) & (best > OK))[0]:
            origin = Vector(centers[index] + normals[index] * 1e-3)
            if bvh.ray_cast(origin, Vector(dirs[index]))[0] is None:
                best[index] = OK
    best[ignore] = OK
    return best


def air_traps(co, normals, edges, radius):
    """Points where air gets trapped when filling from below: upward-facing local height maxima.

    Neighboring maxima closer than `radius` (horizontally) are merged, keeping the highest.
    Returned highest first.
    """
    z = co[:, 2]
    eps = 1e-6
    is_max = np.ones(len(co), dtype=bool)
    za, zb = z[edges[:, 0]], z[edges[:, 1]]
    is_max[edges[za < zb - eps, 0]] = False
    is_max[edges[zb < za - eps, 1]] = False
    candidates = np.nonzero(is_max & (normals[:, 2] > 0.3))[0]
    candidates = candidates[np.argsort(-z[candidates])]

    kept = []
    for index in candidates:
        p = co[index]
        if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 > radius * radius for q in kept):
            kept.append(p)
            if len(kept) > 200:
                break
    return [Vector(p) for p in kept]
