# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import heapq
import math
import re

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import convex_hull_2d

from . import analysis, export

AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}
AXIS_VECTOR = {'X': Vector((1, 0, 0)), 'Y': Vector((0, 1, 0)), 'Z': Vector((0, 0, 1))}
OFFSET_TREE_NAME = "EasyMold_Offset"

# Custom properties that tie generated data to its source object.
ROLE = "easymold_role"      # on collections: RESULT / LAYOUT; on layout objects: MASTER / KEY / POUR / VENT / ...
SOURCE = "easymold_source"  # on collections: the original object
EXTRA = "easymold_extra_"   # on collections: easymold_extra_0, _1... the other objects of a mold for several
TRAY = "easymold_tray"      # on a layout master: bounds (min xyz, max xyz) of each object of a tray
AUTO_CORE = "easymold_auto_core"  # on a layout: made with Auto Core (its CORE objects are the cores)
PRINTS = "easymold_prints"  # on a layout core: where it comes out of the block, see encode_prints()
GRID = "easymold_grid"      # on a layout parting surface: its grid (PartingSurface.grid_info())
LAYOUT_AXES = "easymold_axes"            # on a layout: its split axes ("XY"...), for snapping the markers
LAYOUT_CENTER = "easymold_center"        # on a layout: where the flat split planes are
LAYOUT_PARTING_Z = "easymold_parting_z"  # on a 2-part mold layout: its parting line when made
LAYOUT_BLOCK = "easymold_block"          # on a direct mold layout: the block (min xyz, max xyz), markers stay in it

# name: (RGB, alpha)
MATERIALS = {
    'PART_A': ((0.25, 0.55, 0.85), 1.0),
    'PART_B': ((0.95, 0.60, 0.20), 1.0),
    'BASE': ((0.50, 0.52, 0.56), 1.0),
    'CORE': ((0.95, 0.82, 0.30), 1.0),
    'MASTER': ((0.90, 0.90, 0.88), 1.0),
    'SILICONE': ((0.90, 0.30, 0.40), 0.35),
    'OUTLINE': ((0.30, 0.60, 1.00), 0.15),
    'BLOCKER': ((0.60, 0.40, 0.90), 1.0),
    'PARTING': ((0.95, 0.75, 0.20), 0.55),
    # Undercut analysis, in the order of analysis.OK / NO_DRAFT / UNDERCUT.
    'RELEASES': ((0.30, 0.75, 0.35), 1.0),
    'NO_DRAFT': ((0.95, 0.80, 0.20), 1.0),
    'UNDERCUT': ((0.90, 0.25, 0.25), 1.0),
}


class MoldError(Exception):
    """Error shown to the user as a report instead of a traceback."""


# ---------------------------------------------------------------------------
# Mesh builders (bmesh, so they don't depend on the UI context)
# ---------------------------------------------------------------------------

def _mesh_from_bmesh(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def box_mesh(name, lo, hi, bevel=0.0):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    size = hi - lo
    center = (lo + hi) / 2
    for v in bm.verts:
        v.co = center + Vector((v.co.x * size.x, v.co.y * size.y, v.co.z * size.z))
    if bevel > 0:
        bmesh.ops.bevel(bm, geom=list(bm.edges) + list(bm.verts), offset=bevel, segments=4,
                        profile=0.5, affect='EDGES')
    return _mesh_from_bmesh(name, bm)


def prism_mesh(name, polygon, z0, z1, axis='Z'):
    """Extrusion of a polygon along `axis` between z0 and z1.

    The polygon lies in the plane of the two other axes, in X, Y, Z order (for Z: a counter-clockwise
    XY polygon).
    """
    i = AXIS_INDEX[axis]
    j, k = [n for n in range(3) if n != i]

    def point(u, v, w):
        co = [0.0, 0.0, 0.0]
        co[i], co[j], co[k] = w, u, v
        return co

    bm = bmesh.new()
    bottom = [bm.verts.new(point(x, y, z0)) for x, y in polygon]
    top = [bm.verts.new(point(x, y, z1)) for x, y in polygon]
    bm.faces.new(list(reversed(bottom)))
    bm.faces.new(top)
    n = len(polygon)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((bottom[i], bottom[j], top[j], top[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return _mesh_from_bmesh(name, bm)


def plane_mesh(name, center, axis, size):
    """Square plane through `center`, perpendicular to `axis` (used to show split planes)."""
    i = AXIS_INDEX[axis]
    j, k = [n for n in range(3) if n != i]
    bm = bmesh.new()
    verts = []
    for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        co = center.copy()
        co[j] += a * size / 2
        co[k] += b * size / 2
        verts.append(bm.verts.new(co))
    bm.faces.new(verts)
    return _mesh_from_bmesh(name, bm)


def lathe_mesh(name, items, segments=32):
    """Solids of revolution around the local Z axis, given as (matrix, [(z, radius), ...] bottom to top)."""
    bm = bmesh.new()
    # Half-step offset: no vertex lands exactly on a split plane through the axis (degenerate for booleans).
    angles = [2 * math.pi * (k + 0.5) / segments for k in range(segments)]
    for matrix, profile in items:
        rings = [[bm.verts.new(matrix @ Vector((r * math.cos(a), r * math.sin(a), z))) for a in angles]
                 for z, r in profile]
        bm.faces.new(list(reversed(rings[0])))
        bm.faces.new(rings[-1])
        for lower, upper in zip(rings, rings[1:]):
            for i in range(segments):
                j = (i + 1) % segments
                bm.faces.new((lower[i], lower[j], upper[j], upper[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return _mesh_from_bmesh(name, bm)


def tube_mesh(name, points, radius, segments=24):
    """Closed tube along a polyline (a pour channel, a vent): `radius` is one value or one per point."""
    radii = radius if isinstance(radius, (list, tuple)) else [radius] * len(points)
    bm = bmesh.new()
    rings = []
    normal = None
    for n, (pt, radius) in enumerate(zip(points, radii)):
        tangent = (points[min(n + 1, len(points) - 1)] - points[max(n - 1, 0)]).normalized()
        if normal is None:
            helper = Vector((0, 0, 1)) if abs(tangent.z) < 0.9 else Vector((1, 0, 0))
            normal = tangent.cross(helper).normalized()
        # Keep the ring's frame turning with the path (no twist).
        normal = (normal - tangent * normal.dot(tangent)).normalized()
        binormal = tangent.cross(normal)
        rings.append([bm.verts.new(pt + (normal * math.cos(a) + binormal * math.sin(a)) * radius)
                      for a in (2 * math.pi * (k + 0.5) / segments for k in range(segments))])
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[-1])
    for lower, upper in zip(rings, rings[1:]):
        for i in range(segments):
            j = (i + 1) % segments
            bm.faces.new((lower[i], lower[j], upper[j], upper[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return _mesh_from_bmesh(name, bm)


class PartingSurface:
    """Curved parting surface: along `axis` (index a) its position is a smooth function of the two other
    coordinates (u, v, in X, Y, Z order), sampled on a regular grid; bilinear in between."""

    def __init__(self, axis, origin, cell, values):
        self.axis = axis
        self.u_axis, self.v_axis = [i for i in range(3) if i != axis]
        self.origin = (float(origin[0]), float(origin[1]))
        self.cell = float(cell)
        self.values = np.asarray(values, dtype=np.float64)

    def at_many(self, us, vs):
        nu, nv = self.values.shape
        fu = np.clip((np.asarray(us, np.float64) - self.origin[0]) / self.cell, 0, nu - 1)
        fv = np.clip((np.asarray(vs, np.float64) - self.origin[1]) / self.cell, 0, nv - 1)
        iu = np.minimum(fu.astype(int), nu - 2)
        iv = np.minimum(fv.astype(int), nv - 2)
        tu, tv = fu - iu, fv - iv
        h = self.values
        return ((h[iu, iv] * (1 - tu) + h[iu + 1, iv] * tu) * (1 - tv)
                + (h[iu, iv + 1] * (1 - tu) + h[iu + 1, iv + 1] * tu) * tv)

    def at(self, pt):
        """Position along the axis of the surface over a point."""
        return float(self.at_many([pt[self.u_axis]], [pt[self.v_axis]])[0])

    def on(self, pt):
        """The point moved along the axis onto the surface."""
        q = Vector(pt)
        q[self.axis] = self.at(pt)
        return q

    def _node(self, iu, iv, value):
        co = Vector()
        co[self.u_axis] = self.origin[0] + iu * self.cell
        co[self.v_axis] = self.origin[1] + iv * self.cell
        co[self.axis] = value
        return co

    def _grid(self, bm):
        nu, nv = self.values.shape
        verts = [[bm.verts.new(self._node(iu, iv, self.values[iu, iv])) for iv in range(nv)] for iu in range(nu)]
        for iu in range(nu - 1):
            for iv in range(nv - 1):
                # Fixed diagonals: the board and the silicone halves must share exactly the same surface.
                a, b, c, d = verts[iu][iv], verts[iu + 1][iv], verts[iu + 1][iv + 1], verts[iu][iv + 1]
                bm.faces.new((a, b, c))
                bm.faces.new((a, c, d))
        return verts

    def surface_mesh(self, name):
        bm = bmesh.new()
        self._grid(bm)
        return _mesh_from_bmesh(name, bm)

    def solid_mesh(self, name, side, far):
        """Closed solid between the surface and the plane axis = far, on the `side` (+1/-1) of the surface."""
        bm = bmesh.new()
        verts = self._grid(bm)
        nu, nv = self.values.shape
        ring = ([(iu, 0) for iu in range(nu)] + [(nu - 1, iv) for iv in range(1, nv)]
                + [(iu, nv - 1) for iu in range(nu - 2, -1, -1)] + [(0, iv) for iv in range(nv - 2, 0, -1)])
        top = [verts[iu][iv] for iu, iv in ring]
        cap = [bm.verts.new(self._node(iu, iv, far)) for iu, iv in ring]
        for i in range(len(ring)):
            j = (i + 1) % len(ring)
            bm.faces.new((top[i], top[j], cap[j], cap[i]))
        bm.faces.new(cap)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        return _mesh_from_bmesh(name, bm)

    def grid_info(self):
        nu, nv = self.values.shape
        return [self.axis, self.origin[0], self.origin[1], self.cell, nu, nv]


def harmonic_fill(values, fixed, iterations):
    """Smooth (Laplace) values for the nodes that aren't fixed: each one the average of its neighbours."""
    h = values.copy()
    for _ in range(iterations):
        p = np.pad(h, 1, mode='edge')
        h = np.where(fixed, h, (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4)
    return h


def _join_meshes(name, meshes):
    """One mesh with the geometry of all `meshes` (which are deleted)."""
    bm = bmesh.new()
    for mesh in meshes:
        bm.from_mesh(mesh)
        bpy.data.meshes.remove(mesh)
    return _mesh_from_bmesh(name, bm)


def spheres_mesh(name, spheres):
    """Spheres given as (center, radius); they must not overlap each other."""
    # create_uvsphere slows down with the geometry already in the bmesh: build one unit sphere and copy it.
    unit = bmesh.new()
    bmesh.ops.create_uvsphere(unit, u_segments=24, v_segments=12, radius=1.0)
    template_verts = [v.co.copy() for v in unit.verts]
    template_faces = [[v.index for v in f.verts] for f in unit.faces]
    unit.free()
    bm = bmesh.new()
    for center, r in spheres:
        verts = [bm.verts.new(center + co * r) for co in template_verts]
        for face in template_faces:
            bm.faces.new([verts[i] for i in face])
    return _mesh_from_bmesh(name, bm)


def key_mesh(name, axis, center, radius, shape, grow=0.0):
    """One registration key centered on its split plane; `grow` adds clearance for the hole side."""
    if shape == 'TAPERED':
        # Double cone along the split axis: each part gets a tapered peg or socket.
        rot = AXIS_VECTOR['Z'].rotation_difference(AXIS_VECTOR[axis]).to_matrix().to_4x4()
        h = radius + grow
        profile = [(-h, radius * 0.55 + grow), (0.0, radius + grow), (h, radius * 0.55 + grow)]
        return lathe_mesh(name, [(Matrix.Translation(center) @ rot, profile)])
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=radius + grow,
                              matrix=Matrix.Translation(center))
    return _mesh_from_bmesh(name, bm)


def rounded_rect(lo, hi, radius, segments=8):
    """Counter-clockwise XY outline of a rectangle with rounded corners."""
    radius = min(radius, (hi.x - lo.x) / 2, (hi.y - lo.y) / 2) * 0.999
    if radius < 1e-3:
        return [(lo.x, lo.y), (hi.x, lo.y), (hi.x, hi.y), (lo.x, hi.y)]
    corners = [
        ((hi.x - radius, lo.y + radius), -90),
        ((hi.x - radius, hi.y - radius), 0),
        ((lo.x + radius, hi.y - radius), 90),
        ((lo.x + radius, lo.y + radius), 180),
    ]
    points = []
    for (cx, cy), start in corners:
        for k in range(segments + 1):
            a = math.radians(start + 90 * k / segments)
            points.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return points


def rounded_hull(points, brim, segments=24):
    """Counter-clockwise convex outline around XY points, grown by `brim` with round corners."""
    hull = [points[i] for i in convex_hull_2d(points)]
    angles = [2 * math.pi * k / segments for k in range(segments)]
    grown = [Vector((p[0] + brim * math.cos(a), p[1] + brim * math.sin(a))) for p in hull for a in angles]
    outline = [tuple(grown[i]) for i in convex_hull_2d(grown)]
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1]))
    return outline if area > 0 else outline[::-1]


def _polygon_area(polygon):
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]))) / 2


def open_edges(mesh):
    """Number of non-manifold edges: those not shared by exactly two faces (as BMEdge.is_manifold)."""
    edge_of_loop = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("edge_index", edge_of_loop)
    return int(np.count_nonzero(np.bincount(edge_of_loop, minlength=len(mesh.edges)) != 2))


def triangulate_ngons(mesh):
    """Split the faces of more than 4 sides into triangles. A boolean cut leaves the cut face as one huge
    n-gon wrapped around the cavities (1500+ corners); Blender's quick tessellation of such a face, used to
    draw and export it, can come out as a fan of wrong triangles. bmesh's triangulation handles it.

    Booleans also leave tiny clusters of nearly coincident vertices: their triangles are dissolved again. A cut
    near a pole of the object's mesh leaves dozens of corners within 0.0001 on the cut face, and then no
    triangulation fills it right (two bears, pour from the top): those are merged first, unless merging leaves
    two shells joined at a single vertex (a sprue cup resting on the block). If the mesh ends up less closed than
    before, it is left as it was."""
    sides = np.empty(len(mesh.polygons), dtype=np.int64)
    mesh.polygons.foreach_get("loop_total", sides)
    big = np.nonzero(sides > 4)[0]
    if not len(big):
        return  # nothing to do (a big master often has only triangles: no need to load it into bmesh)
    before = open_edges(mesh)
    backup = mesh.copy()
    bm = bmesh.new()
    bm.from_mesh(backup)
    bm.faces.ensure_lookup_table()
    ngons = [bm.faces[i] for i in big]
    region = {v for f in ngons for v in f.verts}
    pinched = sum(1 for v in region if not v.is_manifold)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-4, edges=list({e for f in ngons for e in f.edges}))
    if sum(1 for v in region if v.is_valid and not v.is_manifold) <= pinched:
        ngons = list({f for v in region if v.is_valid for f in v.link_faces if len(f.verts) > 4})
    else:  # it came apart: start again without merging
        bm.free()
        bm = bmesh.new()
        bm.from_mesh(backup)
        bm.faces.ensure_lookup_table()
        ngons = [bm.faces[i] for i in big]
    bmesh.ops.triangulate(bm, faces=ngons, quad_method='BEAUTY', ngon_method='BEAUTY')
    bmesh.ops.dissolve_degenerate(bm, dist=1e-4, edges=bm.edges[:])
    bm.to_mesh(mesh)
    bm.free()
    if open_edges(mesh) > before:  # worse: back as it was
        bm = bmesh.new()
        bm.from_mesh(backup)
        bm.to_mesh(mesh)
        bm.free()
    mesh.update()
    bpy.data.meshes.remove(backup)


def face_islands(bm):
    """Faces of a bmesh grouped by connection: [(volume, faces)] (volume of a closed island)."""
    seen, islands = set(), []
    for face in bm.faces:
        if face in seen:
            continue
        seen.add(face)
        stack, island = [face], []
        while stack:
            cur = stack.pop()
            island.append(cur)
            for edge in cur.edges:
                for other in edge.link_faces:
                    if other not in seen:
                        seen.add(other)
                        stack.append(other)
        # Signed volume of a closed island (divergence theorem), from its triangles.
        volume = 0.0
        for f in island:
            a = f.verts[0].co
            for i in range(1, len(f.verts) - 1):
                volume += a.dot(f.verts[i].co.cross(f.verts[i + 1].co)) / 6
        islands.append((abs(volume), island))
    return islands


def faces_mesh(name, faces):
    """New mesh with a copy of some bmesh faces (a doubled face, left by Exact on an open mesh, only once)."""
    out = bmesh.new()
    verts = {}
    for f in faces:
        for v in f.verts:
            if v not in verts:
                verts[v] = out.verts.new(v.co)
        try:
            out.faces.new([verts[v] for v in f.verts])
        except ValueError:
            pass
    return _mesh_from_bmesh(name, out)


def _segment_distance(pt, a, b):
    """Distance from a point to the segment a-b."""
    ab = b - a
    t = 0.0 if ab.length_squared < 1e-12 else min(max((pt - a).dot(ab) / ab.length_squared, 0.0), 1.0)
    return (pt - (a + ab * t)).length


_ODD_WAY = Vector((0.5773, 0.5779, 0.5767)).normalized()  # not along any face of a mesh made of boxes


def _inside(bvh, pt):
    """Whether a point is inside a closed mesh: an odd number of crossings along a ray. (The normal of the nearest
    face is wrong next to an edge where a wall meets another face at a sharp angle.)"""
    crossings, start = 0, pt
    for _ in range(200):
        hit = bvh.ray_cast(start, _ODD_WAY)[0]
        if hit is None:
            break
        crossings += 1
        start = hit + _ODD_WAY * 1e-4
    return crossings % 2 == 1


def mesh_volume(mesh):
    bm = bmesh.new()
    bm.from_mesh(mesh)
    vol = bm.calc_volume(signed=False)
    bm.free()
    return vol


def silicone_needs(props, volume):
    """What to mix for `volume` mm3 of silicone: {'ml', 'g', 'a', 'b', 'unit', 'cost'} (with the extra %)."""
    ml = volume / 1000 * (1 + props.waste / 100)  # 1 unit = 1 mm -> mm3 / 1000 = ml
    grams = ml * props.silicone_density
    parts = props.mix_a + props.mix_b
    total, unit = (grams, "g") if props.mix_by == 'WEIGHT' else (ml, "ml")
    share = (props.mix_a / parts, props.mix_b / parts) if parts > 0 else (1.0, 0.0)
    return {'ml': ml, 'g': grams, 'a': total * share[0], 'b': total * share[1], 'unit': unit,
            'cost': grams / 1000 * props.silicone_price}


def cast_needs(props, volume):
    """Material for one cast of `volume` mm3 in the finished silicone mold: {'ml', 'g', 'cost'}."""
    ml = volume / 1000
    grams = ml * props.cast_density
    return {'ml': ml, 'g': grams, 'cost': grams / 1000 * props.cast_price}


def get_material(kind):
    name = f"EasyMold_{kind.title()}"
    color, alpha = MATERIALS[kind]
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (*color, 1.0)
            bsdf.inputs["Alpha"].default_value = alpha
        if alpha < 1.0:
            mat.surface_render_method = 'BLENDED'
    # Viewport (Solid mode) color, alpha included.
    mat.diffuse_color = (*color, alpha)
    return mat


def offset_tree(source, distance, voxel_size, adaptivity=0.0):
    """Geometry Nodes group: object -> SDF grid -> mesh at `distance` (exact offset surface; with adaptivity,
    big faces where it is nearly flat)."""
    ng = bpy.data.node_groups.new(OFFSET_TREE_NAME, 'GeometryNodeTree')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')

    nodes, links = ng.nodes, ng.links
    group_out = nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeObjectInfo')
    info.transform_space = 'RELATIVE'
    info.inputs["Object"].default_value = source
    to_sdf = nodes.new('GeometryNodeMeshToSDFGrid')
    to_sdf.inputs["Voxel Size"].default_value = voxel_size
    to_sdf.inputs["Band Width"].default_value = int(math.ceil(abs(distance) / voxel_size)) + 3
    to_mesh = nodes.new('GeometryNodeGridToMesh')
    to_mesh.inputs["Threshold"].default_value = distance
    to_mesh.inputs["Adaptivity"].default_value = adaptivity

    links.new(info.outputs["Geometry"], to_sdf.inputs["Mesh"])
    links.new(to_sdf.outputs["SDF Grid"], to_mesh.inputs["Grid"])
    links.new(to_mesh.outputs["Mesh"], group_out.inputs["Geometry"])
    return ng


# ---------------------------------------------------------------------------
# Line shapes of the live preview overlay (lists of segment end points)
# ---------------------------------------------------------------------------

def _box_lines(lo, hi):
    corners = [Vector((x, y, z)) for z in (lo.z, hi.z) for y in (lo.y, hi.y) for x in (lo.x, hi.x)]
    edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
    return [corners[k] for e in edges for k in e]


def _prism_lines(polygon, z0, z1, sides=True):
    """Outline of a vertical prism: its two rings and (sides) a few vertical edges."""
    pts = [Vector((q[0], q[1], 0)) for q in polygon]
    out = []
    for z in (z0, z1):
        for a, b in zip(pts, pts[1:] + pts[:1]):
            out += [Vector((a.x, a.y, z)), Vector((b.x, b.y, z))]
    if sides:
        step = max(1, len(pts) // 8)
        for q in pts[::step]:
            out += [Vector((q.x, q.y, z0)), Vector((q.x, q.y, z1))]
    return out


def _polygon_box(polygon, z0, z1):
    return (Vector((min(q[0] for q in polygon), min(q[1] for q in polygon), z0)),
            Vector((max(q[0] for q in polygon), max(q[1] for q in polygon), z1)))


def _circle_lines(center, i, r, segments=24):
    """Circle around axis index i."""
    j, k = [n for n in range(3) if n != i]
    pts = []
    for n in range(segments):
        q = center.copy()
        a = 2 * math.pi * n / segments
        q[j] += math.cos(a) * r
        q[k] += math.sin(a) * r
        pts.append(q)
    return [q for a, b in zip(pts, pts[1:] + pts[:1]) for q in (a, b)]


def _sphere_lines(center, r):
    return _circle_lines(center, 0, r) + _circle_lines(center, 1, r) + _circle_lines(center, 2, r)


def _profile_lines(pt, profile):
    """A vertical solid of revolution at pt, [(z, radius), ...]: its rings and four side lines."""
    out = []
    for z, r in profile:
        out += _circle_lines(Vector((pt.x, pt.y, z)), 2, r)
    for a in (0.25, 0.75, 1.25, 1.75):
        side = [Vector((pt.x + math.cos(a * math.pi) * r, pt.y + math.sin(a * math.pi) * r, z)) for z, r in profile]
        out += [q for s0, s1 in zip(side, side[1:]) for q in (s0, s1)]
    return out


def _tube_lines(points, radii):
    """A tube along a polyline (a vent, a pour channel): a ring at each bend and four lines along it."""
    out = []
    frames = []
    side = None
    for n, (pt, r) in enumerate(zip(points, radii)):
        tangent = (points[min(n + 1, len(points) - 1)] - points[max(n - 1, 0)]).normalized()
        if side is None:
            side = tangent.cross(Vector((0, 0, 1)) if abs(tangent.z) < 0.9 else Vector((1, 0, 0))).normalized()
        side = (side - tangent * side.dot(tangent)).normalized()  # turns with the path (no twist)
        frames.append((pt, r, side, tangent.cross(side)))
    for n, (pt, r, u, v) in enumerate(frames):
        if n in (0, len(frames) - 1) or (frames[n][0] - frames[n - 1][0]).length > 1.0:
            ring = [pt + (u * math.cos(a) + v * math.sin(a)) * r for a in (2 * math.pi * k / 16 for k in range(17))]
            out += [q for s0, s1 in zip(ring, ring[1:]) for q in (s0, s1)]
    for a in (0, 0.5, 1, 1.5):
        side = [pt + (u * math.cos(a * math.pi) + v * math.sin(a * math.pi)) * r for pt, r, u, v in frames]
        out += [q for s0, s1 in zip(side, side[1:]) for q in (s0, s1)]
    return out


def _plane_lines(axis, value, lo, hi, cells=8):
    """A split plane over the box lo..hi: its border and a coarse grid."""
    i = AXIS_INDEX[axis]
    j, k = [n for n in range(3) if n != i]
    out = []

    def point(u, v):
        q = Vector()
        q[i], q[j], q[k] = value, u, v
        return q

    for n in range(cells + 1):
        u = lo[j] + (hi[j] - lo[j]) * n / cells
        v = lo[k] + (hi[k] - lo[k]) * n / cells
        out += [point(u, lo[k]), point(u, hi[k]), point(lo[j], v), point(hi[j], v)]
    return out


def _surface_lines(surface, lo, hi, cells=16, samples=48):
    """The curved parting surface over the box lo..hi, as a grid of lines on it."""
    ua, va = surface.u_axis, surface.v_axis
    out = []
    for n in range(cells + 1):
        for fixed, moving in ((ua, va), (va, ua)):
            c = lo[fixed] + (hi[fixed] - lo[fixed]) * n / cells
            run = np.linspace(lo[moving], hi[moving], samples)
            us, vs = (np.full(samples, c), run) if fixed == ua else (run, np.full(samples, c))
            values = surface.at_many(us, vs)
            pts = []
            for u, v, h in zip(us, vs, values):
                q = Vector()
                q[ua], q[va], q[surface.axis] = u, v, h
                pts.append(q)
            out += [q for a, b in zip(pts, pts[1:]) for q in (a, b)]
    return out


def _mesh_triangles(mesh):
    """Corner positions of every triangle of a mesh, as an (n, 3) array."""
    mesh.calc_loop_triangles()
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    tri = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tri)
    return co.reshape(-1, 3)[tri]


def _fits_bed(size, props):
    """Whether a piece of this size fits the bed turned some way (the overlay doesn't know how it is printed)."""
    a, b, c = size
    return any(x <= props.bed_x and y <= props.bed_y and z <= props.bed_z
               for x, y, z in ((a, b, c), (b, a, c), (a, c, b), (c, a, b), (b, c, a), (c, b, a)))


# ---------------------------------------------------------------------------
# Session cache (roadmap item 17): what Generate computed and can use again while nothing it depends on changed.
# Only in memory: the meshes have a fake user (so removing the pieces doesn't free them), lose it while the file is
# saved (so they aren't written) and are found again by name + token (undo replaces every mesh).
# ---------------------------------------------------------------------------

# Settings the prepared working copy depends on (MoldBuilder.prepare); with several objects also how they are lined up.
PREPARE = ("mold_type", "mold_parts", "core_object", "repair_mode", "voxel_size", "boolean_solver", "auto_orient",
           "shrinkage", "flat_base", "plug_height", "plug_min_width", "fill_holes", "blocker_thickness")
PREPARE_TRAY = ("split_axis", "tray_arrange", "tray_gap", "silicone_thickness", "parting_auto", "parting_height")
CACHE_TAG = "easymold_cache"   # on a cached mesh: its token
MAX_PREPARED = 3               # prepared copies kept (e.g. Generate + Auto Preview)
MAX_OFFSETS = 16               # offset surfaces kept
_PREPARED = {}                 # key: state (attributes of MoldBuilder after prepare, mesh by name)
_OFFSETS = {}                  # (prepared key, distance, voxel, adaptivity): (mesh name, token)
_PARTINGS = {}                 # (prepared key, axis, extent, cell): heights of a curved parting surface
_TOKENS = [0]


def _prop_value(props, name):
    value = getattr(props, name)
    return value.name if isinstance(value, bpy.types.ID) else value


def _cache_mesh(mesh):
    """Keep a mesh for the session cache: (name, token) to find it again."""
    _TOKENS[0] += 1
    mesh.use_fake_user = True
    mesh[CACHE_TAG] = _TOKENS[0]
    return mesh.name, _TOKENS[0]


def _cached_mesh(ref):
    name, token = ref
    mesh = bpy.data.meshes.get(name)
    return mesh if mesh is not None and mesh.get(CACHE_TAG) == token else None


def _drop_mesh(ref):
    mesh = _cached_mesh(ref)
    if mesh is not None:
        mesh.use_fake_user = False
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _trim(store, limit, ref_of):
    while len(store) > limit:
        _drop_mesh(ref_of(store.pop(next(iter(store)))))


def _mesh_bytes(mesh):
    """Rough memory of a mesh (and of a BVH tree over it)."""
    return (len(mesh.vertices) * 24 + len(mesh.edges) * 16 + len(mesh.loops) * 16 + len(mesh.polygons) * 24)


def cache_info():
    """(prepared objects, offset surfaces, approximate bytes) held by the session cache (and the overlay's)."""
    meshes = [_cached_mesh(state['ref']) for state in _PREPARED.values()]
    meshes += [_cached_mesh(ref) for ref in _OFFSETS.values()]
    for state in _PLAN_CACHE.values():
        meshes += [state['mesh']] + list(state['offsets'].values())
    meshes += [m for m in meshes[:len(_PREPARED)] if m is not None]  # prepared copies also keep a BVH tree
    size = 0
    for mesh in meshes:
        try:
            size += _mesh_bytes(mesh)
        except (ReferenceError, AttributeError):
            pass  # gone (undo) or None
    size += sum(values.nbytes for values in _PARTINGS.values())
    return len(_PREPARED), len(_OFFSETS) + len(_PARTINGS), size


def clear_cache():
    """Forget everything Generate and the preview overlay kept (Clear Cache button)."""
    for state in _PREPARED.values():
        _drop_mesh(state['ref'])
    for ref in _OFFSETS.values():
        _drop_mesh(ref)
    _PREPARED.clear()
    _OFFSETS.clear()
    _PARTINGS.clear()
    clear_plan_cache()


def cache_saving(saving):
    """While the file is written the cached meshes have no fake user, so they aren't saved with it."""
    refs = [s['ref'] for s in _PREPARED.values()] + list(_OFFSETS.values())
    for ref in refs:
        mesh = _cached_mesh(ref)
        if mesh is not None:
            mesh.use_fake_user = not saving


def _geometry_print(obj, depsgraph):
    """Hash of an object's evaluated geometry in world space (any edit, modifier or move changes it)."""
    import hashlib
    mesh = obj.evaluated_get(depsgraph).data
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    loops = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loops)
    h = hashlib.blake2b(digest_size=16)
    for part in (co, loops, np.array(obj.matrix_world, dtype=np.float64)):
        h.update(part.tobytes())
    return h.hexdigest()


# The prepared working copy of the last overlay plan: {key: state}, see MoldBuilder.run_plan().
_PLAN_CACHE = {}


def clear_plan_cache():
    """Forget the cached working copy (also needed after undo or loading: its mesh may be gone)."""
    for state in _PLAN_CACHE.values():
        for mesh in [state['mesh']] + list(state['offsets'].values()):
            try:
                mesh.use_fake_user = False
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            except ReferenceError:
                pass
    _PLAN_CACHE.clear()


# ---------------------------------------------------------------------------
# Lookup of generated data
# ---------------------------------------------------------------------------

def _collections_of(source, role):
    return [c for c in bpy.data.collections if c.get(ROLE) == role and c.get(SOURCE) == source]


def find_layout(source):
    found = _collections_of(source, 'LAYOUT')
    return found[0] if found else None


def find_result(source):
    found = _collections_of(source, 'RESULT')
    return found[0] if found else None


def tray_extras(coll):
    """The other objects of a mold made for several objects (stored on its result/layout collection)."""
    return [coll[k] for k in sorted(coll.keys()) if k.startswith(EXTRA) and coll[k] is not None]


def tray_members(source, selected, props):
    """The other objects that go into the same mold as `source`: the other selected meshes, or (nothing else
    selected) the ones of its existing layout/result."""
    extras = [o for o in selected if o is not None and o != source and o.type == 'MESH'
              and o.get(ROLE) is None and o != props.core_object]
    if extras:
        return extras
    for coll in _collections_of(source, 'LAYOUT') + _collections_of(source, 'RESULT'):
        if found := tray_extras(coll):
            return found
    return []


def resolve_source(obj):
    """The object a mold is made for: the object itself, or the source of a layout/result it belongs to (also
    from the other objects of a mold for several objects)."""
    if obj is None:
        return None
    if obj.get(ROLE) is not None:
        for coll in obj.users_collection:
            if SOURCE in coll:
                return coll[SOURCE]  # None once the original is deleted: the piece is not a source itself
    if obj.type != 'MESH':
        return None
    for coll in bpy.data.collections:
        if coll.get(SOURCE) is not None and obj in tray_extras(coll):
            return coll[SOURCE]
    return obj


def _layer_collection(layer, coll):
    if layer.collection == coll:
        return layer
    for child in layer.children:
        found = _layer_collection(child, coll)
        if found is not None:
            return found
    return None


def set_collection_visible(coll, visible):
    layer = _layer_collection(bpy.context.view_layer.layer_collection, coll)
    if layer is not None:
        layer.hide_viewport = not visible


def _make_active(obj):
    view_layer = bpy.context.view_layer
    for other in view_layer.objects.selected:
        if other is not None:  # entries of just-deleted objects can be None until the next update
            other.select_set(False)
    obj.select_set(True)
    view_layer.objects.active = obj


def delete_collection(coll):
    for obj in list(coll.objects):
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    bpy.data.collections.remove(coll)


MARKERS = ('KEY', 'BOLT', 'POUR', 'VENT')  # layout empties placed by the user
ROLE_NAMES = {'KEY': "Key", 'BOLT': "Bolt", 'POUR': "Pour Hole", 'VENT': "Vent", 'BLOCKER': "Blocker",
              'CORE': "Core", 'PARTING_SURFACE': "Parting Surface", 'PARTING': "Parting Line"}


def marker_radius(obj):
    """Size of a layout marker: its display size times its scale (scaling it with S changes it)."""
    return obj.empty_display_size * max(abs(s) for s in obj.scale)


def layout_of(obj):
    """The layout collection an object belongs to, or None."""
    return next((c for c in obj.users_collection if c.get(ROLE) == 'LAYOUT'), None)


def add_layout_item(source, role, props):
    """Add a key / bolt / pour hole / vent marker or a hole blocker to an existing layout, in a free spot where
    the automatic placement would put one (snapped like the others). Returns (object, note or "")."""
    layout = find_layout(source)
    if layout is None:
        raise MoldError("Create the layout first (Edit Layout)")
    b = MoldBuilder(source, props, 'PLAN', tray_extras(layout))
    try:
        b.prepare(use_layout=True)
        axes = b.split_axes()
        if b._curved_axis():
            b.parting = b._make_parting()
        if role == 'BLOCKER':
            return b._place_blocker(layout)
        if b._wants_auto_core() and layout.get(AUTO_CORE):
            b._load_or_find_cores(axes)
        b.auto_spec(axes)
        return b._place_marker(layout, role, axes)
    finally:
        b.discard()


def _new_marker_like(layout, role, location, auto_size, props):
    same = [o for o in layout.objects if o.get(ROLE) == role and o.type == 'EMPTY']
    if role == 'BOLT':
        size = props.bolt_diameter / 2
    elif same:
        size = marker_radius(same[0])
    else:
        size = auto_size
    return _new_marker(layout, role, location, size)


def _axis_matrix(axis, center):
    """Matrix placing local Z along `axis`, at `center`."""
    rot = AXIS_VECTOR['Z'].rotation_difference(AXIS_VECTOR[axis]).to_matrix().to_4x4()
    return Matrix.Translation(center) @ rot


def _new_marker(coll, role, location, size):
    names = {'KEY': "Key", 'BOLT': "Bolt", 'POUR': "Pour Hole", 'VENT': "Vent"}
    obj = bpy.data.objects.new(names[role], None)
    obj.empty_display_size = size
    obj.location = location
    obj[ROLE] = role
    restyle_marker(obj)
    coll.objects.link(obj)
    return obj


# The circle empty lies across its local Y: these turn Y onto the axis of a pour hole / vent (Z) or of a bolt.
_CIRCLE_FACING = {0: (0, 0, -math.pi / 2), 1: (0, 0, 0), 2: (math.pi / 2, 0, 0)}


def restyle_marker(obj, axis=None):
    """Markers are drawn with their real shape by the overlay: the empty is only a sphere (key) or a circle
    around where it is (pour hole and vent: flat on the object; bolt: across its axis, index `axis`). Returns
    whether anything changed (layouts made before used cones and cubes)."""
    role = obj.get(ROLE)
    kind = 'SPHERE' if role == 'KEY' else 'CIRCLE'
    turn = None
    if role in ('POUR', 'VENT'):
        turn = _CIRCLE_FACING[2]
    elif role == 'BOLT' and axis is not None:
        turn = _CIRCLE_FACING[axis]
    changed = obj.empty_display_type != kind
    if changed:
        if role == 'BOLT' and obj.empty_display_type == 'CUBE':
            obj.empty_display_size /= 2  # the cube was as wide as the bolt; the circle is its radius
        obj.empty_display_type = kind
    if turn is not None and any(abs(a - b) > 1e-6 for a, b in zip(obj.rotation_euler, turn)):
        obj.rotation_euler = turn
        changed = True
    return changed


def encode_prints(prints):
    """Core prints (see MoldBuilder._find_cores) as a flat list of numbers, for a custom property."""
    flat = []
    for pr in prints:
        flat += [pr['axis'], pr['sign'], pr['start'], pr['top'], len(pr['outline'])]
        flat += [c for point in pr['outline'] for c in point]
    return flat


def decode_prints(flat, shift):
    """Core prints from encode_prints(), moved by `shift` (the core was moved in the layout)."""
    flat = list(flat)
    prints, n = [], 0
    while n + 5 <= len(flat):
        a, sign, start, top, count = int(flat[n]), int(flat[n + 1]), flat[n + 2], flat[n + 3], int(flat[n + 4])
        j, k = [i for i in range(3) if i != a]
        points = flat[n + 5:n + 5 + 2 * count]
        prints.append({'axis': a, 'sign': sign, 'start': start + shift[a], 'top': top + shift[a],
                       'outline': [(u + shift[j], v + shift[k]) for u, v in zip(points[::2], points[1::2])]})
        n += 5 + 2 * count
    return prints


_MASTER_BVH = {}  # layout name: (what it was made from, BVH of its master in world space, top height)


def _layout_master_bvh(layout):
    master = next((o for o in layout.objects if o.get(ROLE) == 'MASTER' and o.type == 'MESH'), None)
    if master is None:
        return None, 0.0
    mesh = master.data
    key = (mesh.name, len(mesh.vertices), len(mesh.polygons), tuple(c for row in master.matrix_world for c in row))
    kept = _MASTER_BVH.get(layout.name)
    if kept is not None and kept[0] == key:
        return kept[1], kept[2]
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    m = np.array(master.matrix_world)
    co = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
    bvh = BVHTree.FromPolygons(co.tolist(), [tuple(p.vertices) for p in mesh.polygons])
    _MASTER_BVH[layout.name] = (key, bvh, float(co[:, 2].max()))
    return bvh, float(co[:, 2].max())


def _layout_surface(obj):
    """The layout's Parting Surface object as a PartingSurface (vertices as placed: moved or edited), or None."""
    a, _u0, _v0, cell, nu, nv = list(obj[GRID])
    a, nu, nv = int(a), int(nu), int(nv)
    mesh = obj.data
    if len(mesh.vertices) != nu * nv:
        return None
    co = np.empty(nu * nv * 3)
    mesh.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world)
    co = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
    u, v = [i for i in range(3) if i != a]
    return PartingSurface(a, (co[0, u], co[0, v]), cell, co[:, a].reshape(nu, nv))


def snap_marker(obj, props):
    """Put a layout marker where Generate will use it: keys and bolts on the nearest split plane (or the curved
    parting surface, or the 2-part parting line); pour holes and vents on the top of the master under them; a
    pour hole entering from below on the nearest vertical split plane. Returns whether it moved."""
    role = obj.get(ROLE)
    layout = layout_of(obj)
    if role not in MARKERS or layout is None or LAYOUT_CENTER not in layout:
        return False
    axes = [AXIS_INDEX[a] for a in layout.get(LAYOUT_AXES, "")]
    center = Vector(layout[LAYOUT_CENTER])
    parting_z = layout.get(LAYOUT_PARTING_Z)
    surface = None
    for o in layout.objects:
        if o.get(ROLE) == 'PARTING':
            parting_z = o.matrix_world.translation.z
        elif o.get(ROLE) == 'PARTING_SURFACE' and o.type == 'MESH' and o.get(GRID):
            surface = _layout_surface(o)

    def value(i, pt):
        if surface is not None and surface.axis == i:
            return surface.at(pt)
        if i == 2 and parting_z is not None:
            return parting_z
        return center[i]

    pt = obj.matrix_world.translation.copy()
    q = pt.copy()
    if LAYOUT_BLOCK in layout:
        # Inside the block of a direct mold (out of it, it would cut nothing).
        block = list(layout[LAYOUT_BLOCK])
        room = marker_radius(obj) + 1.0
        for i in range(3):
            lo, hi = block[i] + room, block[i + 3] - room
            if lo < hi:
                q[i] = min(max(q[i], lo), hi)
    vertical = [i for i in axes if i != 2]
    turned = False
    if role in ('KEY', 'BOLT'):
        i = None
        if props.mold_type == 'SILICONE_TWO_PART':
            i = 2
            q.z = value(2, q)
        elif axes:
            i = min(axes, key=lambda i: abs(q[i] - value(i, q)))
            q[i] = value(i, q)
        if role == 'BOLT':
            turned = restyle_marker(obj, i)
    elif role == 'POUR' and props.mold_type == 'DIRECT' and props.pour_entry == 'BOTTOM' and vertical:
        i = min(vertical, key=lambda i: abs(q[i] - value(i, q)))
        q[i] = value(i, q)
    else:
        bvh, top = _layout_master_bvh(layout)
        hit = bvh.ray_cast(Vector((q.x, q.y, top + 1)), Vector((0, 0, -1)))[0] if bvh is not None else None
        if hit is not None:
            q.z = hit.z
    if (q - pt).length < 1e-6:
        return turned
    obj.location += q - pt
    return True


def _new_blocker(coll, name, mesh):
    """Editable thin wall that is joined to the master to close a through-hole."""
    obj = bpy.data.objects.new(name, mesh)
    obj[ROLE] = 'BLOCKER'
    mesh.materials.append(get_material('BLOCKER'))
    coll.objects.link(obj)
    return obj


# ---------------------------------------------------------------------------
# Builder
#
# The run_* methods are generators that yield (progress 0..1, message) between the slow
# steps, so the operator can show a progress bar and keep Blender responsive.
# ---------------------------------------------------------------------------

class MoldBuilder:
    def __init__(self, target, props, kind='RESULT', extras=()):
        self.original = target
        self.extras = list(extras)  # more objects in the same mold (tray)
        self.tray = []  # per object of a tray, once arranged: {'bvh', 'bmin', 'bmax', 'high_verts'}
        self.tray_parting = None  # parting height the tray objects were lined up on (2-part mold)
        self.props = props
        self.kind = kind
        self.name = target.name
        self.warnings = []
        self.notes = []
        self.silicone_volume = None
        self.cast_volume = None
        self.layout = None
        self.keys = []    # (axis, center, radius)
        self.bolts = []   # (axis, center)
        self.pours = []   # (point on the object's surface, radius)
        self.vents = []
        self.gates = []   # entry from below: (sprue point, which is also in pours; end inside the part; radius)
        self.marker_of = {}  # id() of a point in keys / bolts / pours / vents: (layout marker it is from, the point)
        self.blockers = []  # meshes of walls that close through-holes (layout: editable objects)
        self.face_classes = None  # undercut analysis of the working copy, per face
        self.axes = []  # split axes, set by split_axes()
        self.parting_z = None  # 2-part silicone mold: height where the two halves meet
        self.parting = None    # curved parting surface (PartingSurface), replacing the split plane of its axis
        self.core_solid = None  # automatic cores of a direct mold (temporary object), prints included
        self.core_prints = []   # where each one comes out of the block: see _find_cores()
        self.core_bvh = None
        self.core_hole = None   # while building: the space the cores take in the block
        self._temp = []
        self._styled = []  # (object, material kind)
        self._offsets = {}
        self._aborted = False
        self.reshaped = False  # the working copy no longer matches the original (turned, plug, shrinkage)
        self.blocker_lines = []  # PLAN: edges of the blockers found (segment end points)
        self._plan_state = None  # PLAN: the cached preparation in use (see run_plan)
        self._session_key = None  # Generate: key of the prepared copy in the session cache (see prepare)
        self.reused = []          # what came from the session cache, for the final message
        self.reused_offsets = 0

        # PREVIEW: Auto Preview build (coarse, replaced on every change); PLAN: live preview overlay, nothing kept.
        suffix = {'LAYOUT': "_Layout", 'PREVIEW': "_Preview", 'PLAN': "_Plan"}.get(kind, "")
        self.collection = bpy.data.collections.new(f"EasyMold_{self.name}{suffix}")
        self.collection[ROLE] = kind
        self.collection[SOURCE] = target
        for n, obj in enumerate(self.extras):
            self.collection[f"{EXTRA}{n}"] = obj
        bpy.context.scene.collection.children.link(self.collection)

    # --- entry points ----------------------------------------------------

    def run_generate(self):
        p = self.props
        yield 0.02, "Preparing object"
        self.prepare(use_layout=True)
        axes = self.split_axes()
        if self._curved_axis():
            yield 0.05, "Finding the parting surface"
            self.parting = self._make_parting()
        builders = {
            'SILICONE_BOX': self.build_silicone_box,
            'SILICONE_SHELL': self.build_silicone_shell,
            'SILICONE_TWO_PART': self.build_two_part,
            'DIRECT': self.build_direct,
        }
        yield from builders[p.mold_type](axes)
        yield 0.92, "Measuring"
        # The closed surface of the master: the raw volume of an open or overlapping mesh is wrong.
        master = mesh_volume(self.master_solid().data)
        if p.mold_type == 'DIRECT':
            self.silicone_volume = master  # the cast part itself
        else:
            self.cast_volume = master      # what each copy cast in the silicone mold takes
        yield 0.93, "Checking the print bed"
        self.fit_bed()
        yield 0.97, "Finishing"
        reused = self.reused + ([f"{self.reused_offsets} surface(s)"] if self.reused_offsets else [])
        if reused and self.kind == 'RESULT':
            self.notes.append("reused from the cache: " + " and ".join(reused))
        if self.kind == 'PREVIEW':
            self._finish_preview()
        else:
            self._finish_result()

    def run_layout(self):
        yield 0.05, "Preparing object"
        self.prepare(use_layout=False)
        axes = self.split_axes()
        if self._curved_axis():
            yield 0.1, "Finding the parting surface"
            self.parting = self._make_parting()
        if self._wants_auto_core():
            yield 0.2, "Finding hollows for cores"
            self._find_cores(axes)
        yield 0.3, "Placing keys, bolts and pour holes"
        self.auto_spec(axes)
        if self.props.analyze_undercuts:
            yield 0.5, "Analyzing undercuts"
            self.analyze_undercuts(axes)
        yield 0.8, "Creating the editable layout"
        self._create_layout(axes)

    def run_plan(self, cache_key=None, markers_only=False):
        """Where everything goes, without building anything (live preview overlay): returns plan_shapes(). The
        cores are only shown from an edited layout (finding them takes as long as a build).

        With a cache_key (everything the preparation depends on), the prepared working copy of the last run with
        the same key is used again: on big meshes preparing takes seconds."""
        cached = _PLAN_CACHE.get(cache_key) if cache_key is not None else None
        if cached is not None:
            self._restore_prepared(cached)
        else:
            self.prepare(use_layout=True)
            # Blockers found now are only drawn (with a layout they are already part of its master).
            self.blocker_lines = [mesh.vertices[k].co.copy() for mesh in self.blockers for e in mesh.edges
                                  for k in e.vertices]
            for mesh in self.blockers:
                bpy.data.meshes.remove(mesh)
            self.blockers = []
            if cache_key is not None:
                self._keep_prepared(cache_key)
        axes = self.split_axes()
        if self._curved_axis():
            self.parting = self._make_parting()
        if self._wants_auto_core() and self.layout is not None and self.layout.get(AUTO_CORE):
            self._load_or_find_cores(axes)
        self._place_spec(axes)
        return self.plan_shapes(axes, markers_only)

    def discard(self):
        """Remove everything a PLAN run made (nothing of the scene is shown or hidden)."""
        self._aborted = True
        self._temp.clear()
        self._offsets.clear()
        if self.collection.name in bpy.data.collections:
            delete_collection(self.collection)
        self.cleanup()

    PREPARED = ('bvh', 'bmin', 'bmax', 'center', 'size', 'high_verts', '_non_manifold', 'solver', 'target_volume',
                'tray', 'tray_parting', 'reshaped', 'blocker_lines', 'notes', 'warnings')

    def _keep_prepared(self, key):
        clear_plan_cache()
        mesh = self.target.data
        mesh.use_fake_user = True  # stays when the PLAN objects are removed
        state = {name: getattr(self, name) for name in self.PREPARED}
        state['notes'], state['warnings'] = list(self.notes), list(self.warnings)
        state['mesh'] = mesh
        state['offsets'] = {}  # offset meshes by distance, filled by offset_source()
        _PLAN_CACHE[key] = state
        self._plan_state = state

    def _restore_prepared(self, state):
        self._plan_state = state
        self.layout = find_layout(self.original)
        self.target = self.add_object("EasyMold_Work", state['mesh'], temp=True)
        for name in self.PREPARED:
            setattr(self, name, state[name])
        self.notes, self.warnings = list(state['notes']), list(state['warnings'])

    # --- preparation -----------------------------------------------------

    def prepare(self, use_layout):
        """Build the working copy every operation uses: oriented and with a pour plug if needed.

        The original object is never modified. If an edited layout exists, its master is used as is. Generate
        (and the Auto Preview) take it from the session cache when nothing it depends on changed, and then also
        the offset surfaces made from it (see offset_source)."""
        key = self._prepare_key() if self.kind in ('RESULT', 'PREVIEW') and use_layout else None
        state = _PREPARED.get(key) if key is not None else None
        mesh = _cached_mesh(state['ref']) if state is not None else None
        if mesh is not None:
            _PREPARED[key] = _PREPARED.pop(key)  # most recently used last
            self.layout = find_layout(self.original)
            self.target = self.add_object("EasyMold_Work", mesh, temp=True)
            for name in self.PREPARED:
                setattr(self, name, state[name])
            self.notes, self.warnings = list(state['notes']), list(state['warnings'])
            self._session_key = key
            self.reused.append("the prepared object")
            return
        self._prepare(use_layout)
        if key is not None:
            state = {name: getattr(self, name) for name in self.PREPARED}
            state['notes'], state['warnings'] = list(self.notes), list(self.warnings)
            state['ref'] = _cache_mesh(self.target.data)
            _PREPARED.pop(key, None)
            _PREPARED[key] = state
            _trim(_PREPARED, MAX_PREPARED, lambda s: s['ref'])
            self._session_key = key

    def _prepare_key(self):
        """Everything the preparation depends on: the settings and the geometry it starts from (the layout's master
        and blockers, or the objects)."""
        p = self.props
        dg = bpy.context.evaluated_depsgraph_get()
        layout = find_layout(self.original)
        if layout is not None and any(o.get(ROLE) == 'MASTER' for o in layout.objects):
            objects = sorted((o for o in layout.objects if o.get(ROLE) in ('MASTER', 'BLOCKER') and o.type == 'MESH'),
                             key=lambda o: o.name)
        else:
            objects = [self.original] + self.extras
        names = PREPARE + (PREPARE_TRAY if self.extras else ())
        return (tuple(_prop_value(p, n) for n in names), tuple(o.name for o in objects),
                tuple(_geometry_print(o, dg) for o in objects))

    def _prepare(self, use_layout):
        p = self.props
        self.layout = find_layout(self.original) if use_layout else None
        masters = [o for o in self.layout.objects if o.get(ROLE) == 'MASTER'] if self.layout else []
        if masters:
            self.target = self.world_copy(masters[0], "EasyMold_Work", temp=True)
            # Bounds of each object of a tray (the pour holes come from the markers, so no BVH is needed).
            flat = list(masters[0].get(TRAY, []))
            self.tray = [{'bmin': Vector(flat[k:k + 3]), 'bmax': Vector(flat[k + 3:k + 6])}
                         for k in range(0, len(flat), 6)]
            self._read_target()
            # Blockers edited in the layout become part of the master.
            for blocker in [o for o in self.layout.objects if o.get(ROLE) == 'BLOCKER' and o.type == 'MESH']:
                self.boolean(self.target, blocker, 'UNION')
                self._read_target()
            return

        self.layout = None
        if not self.extras:
            self._prepare_one(self.original)
            return
        # Several objects: each one is prepared on its own, then they are lined up and joined.
        sources = [self.original] + self.extras
        self._tray_axes()  # fails early for molds a tray can't use
        prepared = []
        for source in sources:
            first_blocker = len(self.blockers)
            self._prepare_one(source)
            parting = None
            if p.mold_type == 'SILICONE_TWO_PART':
                height = self.bmax.z - self.bmin.z
                parting = (self._widest_section() if p.parting_auto
                           else self.bmin.z + height * p.parting_height / 100)
            prepared.append((self.target, self.bmin.copy(), self.bmax.copy(), parting, first_blocker))
            self._clear_offsets()
        self._arrange(prepared)

    def _clear_offsets(self):
        for obj in list(self._offsets.values()):
            self.remove(obj)
        self._offsets.clear()

    def _tray_axes(self):
        """(row axis, axes to line the centers up on) for a tray: the split planes must go through every
        object, so the row runs along them."""
        p = self.props
        parts = int(p.mold_parts)
        if p.mold_type in ('SILICONE_BOX', 'SILICONE_TWO_PART') or parts == 1:
            return 'X', ['Y'], False
        if parts == 4:
            raise MoldError("Several objects can go in a mold split in 2 parts at most")
        axis = p.split_axis
        if axis == 'Z':  # direct mold split top/bottom
            return 'X', ['Y', 'Z'], True
        return ('Y' if axis == 'X' else 'X'), [axis], True

    def _arrange(self, prepared):
        """Line the prepared objects up (row or grid, the first one stays in place) and join them into the
        working copy. Heights: same parting line (2-part), same top (open direct mold), same center (direct
        split top/bottom), otherwise the same bottom (they all stand on the base)."""
        p = self.props
        row, centered, split = self._tray_axes()
        first_min, first_max, first_parting = prepared[0][1], prepared[0][2], prepared[0][3]
        first_center = (first_min + first_max) / 2
        # Spacing is the closest distance between the objects' surfaces; in a shell, between their silicone
        # layers (0 = the layers touch; negative = they overlap, down to one silicone thickness, so the objects
        # are never closer than that). Other molds keep at least 1 mm of material between the cavities.
        if p.mold_type == 'SILICONE_SHELL':
            s = p.silicone_thickness
            if p.tray_gap < -s:
                self.warnings.append(f"Spacing can't go below -{s:g} (the silicone thickness): used -{s:g}")
            gap = 2 * s + max(p.tray_gap, -s)
        else:
            if p.tray_gap < 1.0:
                self.notes.append("objects kept 1 mm apart (negative spacing is only for shell molds)")
            gap = max(p.tray_gap, 1.0)
        arrange = p.tray_arrange
        if arrange == 'GRID' and split:
            arrange = 'ROW'
            self.notes.append("objects in a row (a split mold can't take a grid)")
        cols = math.ceil(math.sqrt(len(prepared)))
        cell = Vector([max(b[2][i] - b[1][i] for b in prepared) + gap for i in range(3)])
        offsets = []
        placed = []  # (vertices, faces) of the objects already in the row, moved
        i = AXIS_INDEX[row]
        cursor = first_max[i] + gap
        for n, (obj, lo, hi, parting, _first_blocker) in enumerate(prepared):
            center = (lo + hi) / 2
            move = Vector()
            if arrange != 'KEEP' and n > 0:
                if arrange == 'GRID':
                    move.x = first_center.x + (n % cols) * cell.x - center.x
                    move.y = first_center.y - (n // cols) * cell.y - center.y
                else:
                    move[i] = cursor - lo[i]
                    for axis in centered:
                        j = AXIS_INDEX[axis]
                        move[j] = first_center[j] - center[j]
                if p.mold_type == 'SILICONE_TWO_PART':
                    move.z = first_parting - parting
                elif p.mold_type == 'DIRECT' and p.mold_parts == '1':
                    move.z = first_max.z - hi.z
                elif not (split and 'Z' in centered):
                    move.z = first_min.z - lo.z
                if arrange == 'ROW':
                    # Placed by the bounding boxes, the surfaces are at least `gap` apart: slide it back towards
                    # the others until they are exactly that close.
                    move[i] -= self._slide_back(placed, obj, move, i, gap, hi[i] - lo[i])
                    cursor = hi[i] + move[i] + gap
            if arrange == 'ROW':
                placed.append(([v.co + move for v in obj.data.vertices], [tuple(f.vertices) for f in obj.data.polygons]))
            offsets.append(move)
        if p.mold_type == 'SILICONE_TWO_PART' and (arrange != 'KEEP' or len({b[3] for b in prepared}) == 1):
            self.tray_parting = first_parting

        bm = bmesh.new()
        boxes = []
        ends = [b[4] for b in prepared[1:]] + [len(self.blockers)]
        for (obj, lo, hi, _parting, first_blocker), move, end in zip(prepared, offsets, ends):
            obj.data.transform(Matrix.Translation(move))
            for mesh in self.blockers[first_blocker:end]:
                mesh.transform(Matrix.Translation(move))
            verts = [v.co.copy() for v in obj.data.vertices]
            self.tray.append({
                'bvh': BVHTree.FromPolygons(verts, [tuple(f.vertices) for f in obj.data.polygons]),
                'bmin': lo + move, 'bmax': hi + move,
                'high_verts': heapq.nlargest(5000, verts, key=lambda v: v.z),
            })
            boxes.append((lo + move, hi + move))
            bm.from_mesh(obj.data)
            self.remove(obj)
        for a in range(len(boxes) if arrange == 'KEEP' else 0):  # a row or grid keeps them apart
            for b in range(a):
                if all(boxes[a][0][k] < boxes[b][1][k] and boxes[b][0][k] < boxes[a][1][k] for k in range(3)):
                    self.warnings.append("Some objects overlap: move them apart or use Arrange: Row")
                    break
            else:
                continue
            break
        self.target = self.add_object("EasyMold_Work", _mesh_from_bmesh("EasyMold_Work", bm), temp=True)
        self._read_target()
        self.notes.append(f"{len(prepared)} objects in one mold")

    @staticmethod
    def _slide_back(placed, obj, move, i, gap, length):
        """How far back along axis i an object (moved by `move`) can go before its surface comes closer than
        `gap` to the objects already placed (its vertices, measured to their surfaces; inside counts as 0)."""
        verts, faces = [], []
        for v, f in placed:
            faces += [tuple(k + len(verts) for k in face) for face in f]
            verts += v
        bvh = BVHTree.FromPolygons(verts, faces)
        mesh_verts = obj.data.vertices
        step = max(1, len(mesh_verts) // 20000)  # a sample is enough on big meshes
        points = [mesh_verts[k].co + move for k in range(0, len(mesh_verts), step)]
        back = Vector()

        def closest(t):
            back[i] = t
            best = math.inf
            for q in points:
                q = q - back
                loc, normal, _index, dist = bvh.find_nearest(q, best)
                if loc is None:
                    continue
                if (q - loc).dot(normal) < 0:
                    return 0.0  # inside another object
                best = min(best, dist)
            return best

        # At most until the bounding boxes overlap as much as the shorter one is long (surely too close).
        extent = max(v[i] for v in verts) - min(v[i] for v in verts)
        lo, hi = 0.0, gap + min(length, extent)
        for _ in range(30):
            mid = (lo + hi) / 2
            if closest(mid) >= gap:
                lo = mid
            else:
                hi = mid
        return lo

    def _prepare_one(self, source):
        """Working copy of one object: repaired, oriented, enlarged for shrinkage, with pour plug and blockers."""
        p = self.props
        self.target = self.world_copy(source, "EasyMold_Work", temp=True)
        self._read_target()
        if self._non_manifold and p.repair_mode != 'NONE':
            self._repair()
        if non_manifold := self._non_manifold:
            name = f"{source.name} is" if self.extras else "Object is"
            self.warnings.append(f"{name} not closed ({non_manifold} non-manifold edges): results may be wrong. "
                                 "Try Repair: Rebuild")

        silicone = p.mold_type != 'DIRECT'
        # A 2-part mold keeps the master as it is: it is surrounded by silicone, no side rests on a base.
        two_part = p.mold_type == 'SILICONE_TWO_PART'
        if p.auto_orient and not two_part:
            if silicone:
                self._orient(Vector((0, 0, -1)))
            elif p.mold_parts == '1' and p.core_object is None:
                self._orient(Vector((0, 0, 1)))
        if p.shrinkage > 0:
            self._compensate_shrinkage()
        if silicone and p.flat_base and not two_part:
            self._add_pour_plug()
        if silicone and p.fill_holes:
            found = self._find_blockers()
            # Generating directly: join them now. For a layout they become editable objects instead (a plan draws
            # them).
            if self.kind in ('RESULT', 'PREVIEW'):
                for mesh in found:
                    self.boolean_temp(self.target, mesh, 'UNION')
                if found:
                    self._read_target()
            else:
                self.blockers += found

    def _repair(self):
        """Fix the working copy: merge duplicates, drop loose parts, fill holes; optionally voxel rebuild."""
        mesh = self.target.data
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=max(self.size * 1e-6, 1e-6))
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context='VERTS')
        boundary = [e for e in bm.edges if e.is_boundary]
        if boundary:
            bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bm.to_mesh(mesh)
        bm.free()
        before = self._non_manifold
        self._read_target()

        if self.props.repair_mode == 'REMESH':
            rebuilt = self.offset_source(0.0)
            mesh = self.target.data
            self.target.data = rebuilt.data.copy()
            bpy.data.meshes.remove(mesh)
            # Offsets were computed from the old surface.
            for obj in list(self._offsets.values()):
                self.remove(obj)
            self._offsets.clear()
            self._read_target()
            self.notes.append("rebuilt a watertight surface")
        elif self._non_manifold < before:
            self.notes.append(f"repaired {before - self._non_manifold} open edges")

    def _compensate_shrinkage(self):
        """Enlarge the working copy so a cast that shrinks by `shrinkage` % comes out at the original size.
        It grows from the middle of its bottom, so it still rests on the base."""
        s = self.props.shrinkage
        factor = 1 / (1 - s / 100)
        pivot = Vector((self.center.x, self.center.y, self.bmin.z))
        self.target.data.transform(Matrix.Translation(pivot) @ Matrix.Scale(factor, 4) @ Matrix.Translation(-pivot))
        for obj in list(self._offsets.values()):
            self.remove(obj)
        self._offsets.clear()
        self._read_target()
        self.reshaped = True
        self.notes.append(f"enlarged the master {(factor - 1) * 100:.2f}% for {s:g}% shrinkage")

    def _find_blockers(self):
        """Thin walls (meshes) that close holes going right through the object (along X, Y or Z)."""
        p = self.props
        cell = self.size / 160
        thickness = p.blocker_thickness
        found = []
        for axis in ('X', 'Y', 'Z'):
            for points, depth in analysis.through_holes(self.bvh, self.bmin, self.bmax, axis, cell):
                # Grow the outline a little so the wall overlaps the object around the hole.
                outline = rounded_hull(points, cell * 1.5)
                found.append(prism_mesh("Blocker", outline, depth - thickness / 2, depth + thickness / 2, axis=axis))
        if found:
            self.notes.append(f"closed {len(found)} through-hole(s) with thin walls: trim them off the casts")
        return found

    def split_axes(self):
        self.axes = self._split_axes()
        if self.props.mold_type == 'SILICONE_TWO_PART':
            self.parting_z = self._parting_z()
        return self.axes

    def _split_axes(self):
        p = self.props
        parts = int(p.mold_parts)
        if self._wants_auto_core() and parts == 1:
            # An open-top block has nothing to hold a core in place.
            raise MoldError("Auto Core needs a mold in 2 or 4 parts")
        if p.mold_type in ('SILICONE_BOX', 'SILICONE_TWO_PART') or parts == 1:
            return []
        if p.mold_type == 'SILICONE_SHELL':
            s = p.silicone_thickness
            if p.key_tolerance >= s:
                raise MoldError("Key tolerance must be smaller than the silicone thickness")
            if p.lip_height >= s:
                raise MoldError("Lip height must be smaller than the silicone thickness")
            if p.clamp_method == 'BAND':
                raise MoldError("Elastic bands can't hold the base of a shell mold: use Clips or Screws")
            if parts == 4:
                return ['X', 'Y']
            if p.split_axis == 'Z':
                raise MoldError("Split a shell mold along X or Y (Z is the base/pour direction)")
            return [p.split_axis]
        axes = [p.split_axis]
        if parts == 4:
            axes.append(next(a for a in ('X', 'Y', 'Z') if a != p.split_axis))
        return axes

    # --- target analysis -------------------------------------------------

    def _read_target(self):
        # The working copy is already in world space and has no modifiers.
        # numpy throughout: imported meshes can have millions of vertices.
        mesh = self.target.data
        mesh.update()
        co = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        if not len(mesh.polygons):
            raise MoldError("The object has no faces")
        loops = np.empty(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get("vertex_index", loops)
        sides = np.empty(len(mesh.polygons), dtype=np.int64)
        mesh.polygons.foreach_get("loop_total", sides)
        if np.all(sides == sides[0]):
            polys = loops.reshape(-1, int(sides[0])).tolist()
        else:
            polys = [tuple(p.vertices) for p in mesh.polygons]

        non_manifold = open_edges(mesh)
        mesh.calc_loop_triangles()
        tri = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
        mesh.loop_triangles.foreach_get("vertices", tri)
        a, b, c = (co[tri[k::3]] for k in range(3))
        self.target_volume = abs(float(np.einsum('ij,ij->', a, np.cross(b, c)))) / 6

        self._non_manifold = non_manifold
        self.solver = self.props.boolean_solver
        if self.solver == 'AUTO':
            self.solver = 'EXACT' if non_manifold else 'MANIFOLD'

        self.bmin = Vector(co.min(axis=0))
        self.bmax = Vector(co.max(axis=0))
        self.center = (self.bmin + self.bmax) / 2
        self.size = max(self.bmax - self.bmin)
        # Highest first; equal heights in vertex order.
        top = np.argsort(-co[:, 2], kind='stable')[:5000]
        self.high_verts = [Vector(c) for c in co[top]]
        self.bvh = BVHTree.FromPolygons(co.tolist(), polys)

    def _face_arrays(self):
        """(normals, centers, areas, vertex positions) of the working copy (world space), as arrays."""
        polygons, verts = self.target.data.polygons, self.target.data.vertices
        normals, centers = np.empty(len(polygons) * 3), np.empty(len(polygons) * 3)
        areas, co = np.empty(len(polygons)), np.empty(len(verts) * 3)
        polygons.foreach_get("normal", normals)
        polygons.foreach_get("center", centers)
        polygons.foreach_get("area", areas)
        verts.foreach_get("co", co)
        return normals.reshape(-1, 3), centers.reshape(-1, 3), areas, co.reshape(-1, 3)

    @staticmethod
    def _support_area(faces, n, tol):
        """Area of the faces lying on the supporting plane in direction n (where the object would rest)."""
        normals, centers, areas, co = faces
        n = np.array(n, dtype=np.float64)
        dmax = (co @ n).max()
        return float(areas[(normals @ n > 0.995) & (centers @ n > dmax - tol)].sum())

    def _orient(self, want):
        """Rotate the working copy so its largest flat supporting face points along `want`."""
        faces = self._face_arrays()
        normals, _centers, areas, _co = faces
        tol = max(self.size * 1e-3, 0.01)

        # Candidate directions: face normals grouped in ~3 degree bins, largest total area first (equal ones in
        # the order they first appear).
        keys = np.round(normals * 20).astype(np.int64) + 20  # 0..40 per component: one number per bin
        keys = (keys[:, 0] * 41 + keys[:, 1]) * 41 + keys[:, 2]
        _unique, first, which = np.unique(keys, return_index=True, return_inverse=True)
        which = which.reshape(-1)
        sums = np.zeros((len(first), 3))
        np.add.at(sums, which, normals * areas[:, None])
        totals = np.bincount(which, weights=areas, minlength=len(first))
        by_appearance = np.argsort(first)
        order = by_appearance[np.argsort(-totals[by_appearance], kind='stable')][:12]

        best_n = want
        best = self._support_area(faces, want, tol)
        current = best
        for b in order:
            acc = Vector(sums[b])
            if acc.length < 1e-9:
                continue
            n = acc.normalized()
            area = self._support_area(faces, n, tol)
            if area > best:
                best_n, best = n, area
        # Keep the user's orientation unless another one is clearly better and big enough to be useful
        # (a tiny facet of a round object is not worth a rotation; the pour plug handles those).
        min_area = math.pi * (self.props.plug_min_width / 2) ** 2
        if best_n == want or best <= current * 1.1 or best < min_area:
            return

        old_center = self.center.copy()
        old_z = self.bmin.z
        rot = best_n.rotation_difference(want).to_matrix().to_4x4()
        mesh = self.target.data
        mesh.transform(Matrix.Translation(old_center) @ rot @ Matrix.Translation(-old_center))
        self._read_target()
        # Keep it in place: same XY center, same bottom height.
        shift = Vector((old_center.x - self.center.x, old_center.y - self.center.y, old_z - self.bmin.z))
        mesh.transform(Matrix.Translation(shift))
        mesh.update()
        self._read_target()
        angle = math.degrees(best_n.angle(want))
        self.reshaped = True
        self.notes.append(f"rotated {angle:.0f}° to rest on its largest flat face")

    def _add_pour_plug(self):
        """Add a flat pedestal under the object if its bottom has no flat face of the minimum width."""
        p = self.props
        min_r = p.plug_min_width / 2
        min_area = math.pi * min_r ** 2
        tol = max(self.size * 1e-3, 0.01)
        faces = self._face_arrays()
        flat = self._support_area(faces, Vector((0, 0, -1)), tol)
        if flat >= min_area:
            return

        # Grow a band from the lowest point until its outline is as wide as the minimum opening.
        co = faces[3]
        depth = 0.25
        while True:
            band = [Vector(c) for c in co[co[:, 2] <= self.bmin.z + depth][:, :2]]
            if len(band) >= 3 and _polygon_area(rounded_hull(band, 0.01)) >= min_area:
                break
            if depth >= p.plug_height:
                break
            depth *= 1.5
        center = sum(band, Vector((0, 0))) / len(band)
        circle = [center + Vector((math.cos(a), math.sin(a))) * min_r
                  for a in (2 * math.pi * k / 24 for k in range(24))]
        outline = rounded_hull(band + circle, 0.3)

        # The plug top goes a bit into the object so the union is solid.
        plug = prism_mesh("EasyMold_Plug", outline, self.bmin.z - p.plug_height, self.bmin.z + depth * 2 + 0.2)
        self.boolean_temp(self.target, plug, 'UNION')
        self._read_target()
        self.reshaped = True
        self.notes.append(f"added a {p.plug_height:g} mm pour plug for a flat opening")

    # --- object bookkeeping ----------------------------------------------

    def add_object(self, name, mesh, temp=False, style=None):
        obj = bpy.data.objects.new(name, mesh)
        self.collection.objects.link(obj)
        if temp:
            self._temp.append(obj)
        if style:
            self._styled.append((obj, style))
        return obj

    def world_copy(self, source, name, temp=False, style=None):
        """Unmodified copy of an object (modifiers applied) in world space."""
        dg = bpy.context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(source.evaluated_get(dg))
        mesh.transform(source.matrix_world)
        return self.add_object(name, mesh, temp, style)

    def remove(self, obj):
        if obj in self._temp:
            self._temp.remove(obj)
        self._styled = [(o, s) for o, s in self._styled if o != obj]
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)

    def cleanup(self):
        while self._temp:
            self.remove(self._temp[-1])
        self._offsets.clear()
        for ng in [ng for ng in bpy.data.node_groups if ng.name.startswith(OFFSET_TREE_NAME)]:
            bpy.data.node_groups.remove(ng)

    def abort(self):
        if self._aborted:
            return
        self._aborted = True
        self._temp.clear()
        self._offsets.clear()
        if self.collection.name in bpy.data.collections:
            delete_collection(self.collection)
        self.cleanup()
        if self.kind in ('PREVIEW', 'PLAN'):
            return  # a preview never hid them
        for obj in [self.original] + self.extras:
            obj.hide_set(False)

    def _replace_previous(self):
        """Delete older results/layouts of the same object and take over their name."""
        name = self.collection.name
        for coll in _collections_of(self.original, self.kind):
            if coll != self.collection:
                name = coll.name
                delete_collection(coll)
        self.collection.name = name
        # The new pieces were named while the old ones still existed ("_Master.001"): give them the plain
        # names back (export tells the master apart by its name).
        for obj in self.collection.objects:
            base = re.sub(r"\.\d{3}$", "", obj.name)
            if base != obj.name and base not in bpy.data.objects:
                obj.name = base

    # --- geometry operations ---------------------------------------------

    def _bake_modifier(self, obj, mod):
        dg = bpy.context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
        obj.modifiers.remove(mod)
        old = obj.data
        obj.data = mesh
        mesh.name = old.name
        if old.users == 0:
            bpy.data.meshes.remove(old)

    def boolean(self, obj, cutter, operation):
        # In Auto mode only geometry that touched an open (non-manifold) user mesh needs the Exact solver;
        # everything built from offsets and primitives is closed and gets the faster, cleaner Manifold.
        dirty = self._is_dirty(obj) or self._is_dirty(cutter)
        if dirty:
            self._mark_dirty(obj)
        mod = obj.modifiers.new("EasyMold", 'BOOLEAN')
        mod.operation = operation
        mod.object = cutter
        mod.solver = self.solver if dirty or self.props.boolean_solver != 'AUTO' else 'MANIFOLD'
        self._bake_modifier(obj, mod)

    def _is_dirty(self, obj):
        return self._non_manifold and (obj == self.target or obj.get("easymold_dirty", False))

    @staticmethod
    def _mark_dirty(obj):
        obj["easymold_dirty"] = True

    def boolean_temp(self, obj, cutter_mesh, operation):
        cutter = self.add_object("EasyMold_Cutter", cutter_mesh, temp=True)
        self.boolean(obj, cutter, operation)
        self.remove(cutter)

    def boolean_many(self, obj, items, operation):
        """Apply many small cutters: items are (mesh, center, bounding radius).

        Cutters that don't overlap are joined into one mesh and applied with a single boolean (each
        boolean on a big mold costs about the same however small the cutter); overlapping ones (e.g.
        markers placed by hand) go into separate batches.
        """
        batches = []
        for item in items:
            for batch in batches:
                if all((item[1] - other[1]).length > item[2] + other[2] for other in batch):
                    batch.append(item)
                    break
            else:
                batches.append([item])
        for batch in batches:
            self.boolean_temp(obj, _join_meshes("EasyMold_Cutters", [mesh for mesh, _c, _r in batch]), operation)

    ADAPTIVITY = 0.05

    def offset_source(self, distance):
        """Temporary object whose surface lies exactly `distance` outside the target (computed once)."""
        key = round(distance, 6)
        obj = self._offsets.get(key)
        if obj is not None:
            return obj
        kept = self._plan_state['offsets'] if self._plan_state is not None else None
        if kept is not None and key in kept:
            obj = self.add_object(f"EasyMold_Offset_{key:g}", kept[key], temp=True)
            self._offsets[key] = obj
            return obj
        voxel = self.props.voxel_size
        if self.kind == 'PLAN' and distance >= 1.0:
            voxel = max(voxel, self.size / 100)  # the overlay only needs where the shell's flanges are
        # Beyond ~800 voxels across, each offset takes tens of seconds and gigabytes of memory.
        if self.size / voxel > 800:
            raise MoldError(f"Resolution too fine for this object size: use at least {self.size / 800:.2f}")
        # Mold walls (1 mm and more out) don't need every voxel: an adaptive mesh has ~20x fewer faces (and every
        # boolean on it is that much faster) for ~0.02 mm mean / 0.1 mm worst error at 0.3 mm resolution.
        # Surfaces right on the master (closed copy, board cavity, rebuild) keep all the detail.
        adaptivity = self.ADAPTIVITY if self.props.fast_surfaces and distance >= 1.0 else 0.0
        session = (self._session_key, key, round(voxel, 6), adaptivity) if self._session_key is not None else None
        ref = _OFFSETS.get(session) if session is not None else None
        mesh = _cached_mesh(ref) if ref is not None else None
        if mesh is not None:
            _OFFSETS[session] = _OFFSETS.pop(session)  # most recently used last
            obj = self.add_object(f"EasyMold_Offset_{key:g}", mesh, temp=True)
            self._offsets[key] = obj
            self.reused_offsets += 1
            return obj
        obj = self.sdf_offset(self.target, distance, voxel, adaptivity, f"EasyMold_Offset_{key:g}")
        if not obj.data.vertices:
            raise MoldError(f"Offset of {distance:g} produced no geometry (is the object closed?)")
        self._offsets[key] = obj
        if session is not None:
            _OFFSETS.pop(session, None)
            _OFFSETS[session] = _cache_mesh(obj.data)
            _trim(_OFFSETS, MAX_OFFSETS, lambda r: r)
        if kept is not None:
            obj.data.use_fake_user = True
            kept[key] = obj.data
            while len(kept) > 6:  # dragging a slider makes a new one per value
                old = kept.pop(next(iter(kept)))
                old.use_fake_user = False
                if old.users == 0:
                    bpy.data.meshes.remove(old)
        return obj

    def sdf_offset(self, source, distance, voxel, adaptivity=0.0, name="EasyMold_Offset"):
        """New temporary object with the surface `distance` outside a closed (world space) object."""
        obj = self.add_object(name, bpy.data.meshes.new("Offset"), temp=True)
        mod = obj.modifiers.new("EasyMold_Offset", 'NODES')
        ng = offset_tree(source, distance, voxel, adaptivity)
        mod.node_group = ng
        self._bake_modifier(obj, mod)
        bpy.data.node_groups.remove(ng)
        return obj

    def master_solid(self):
        """The master to subtract from the silicone preview. Exact on an open or self-overlapping mesh (like
        the cup) can flip inside/outside and leave a wrong preview; the offset surface right on it is closed."""
        if not self._non_manifold:
            return self.target
        return self.offset_source(self.props.voxel_size * 0.05)

    def offset(self, name, distance, temp=False, style=None):
        """New, editable object with the offset surface at `distance`."""
        return self.add_object(name, self.offset_source(distance).data.copy(), temp, style)

    def halfspace_mesh(self, axis, value, side):
        """Huge box covering one side (+1/-1) of the plane axis = value."""
        far = self.size * 10 + 100
        lo = self.center - Vector((far, far, far))
        hi = self.center + Vector((far, far, far))
        i = AXIS_INDEX[axis]
        if side > 0:
            lo[i] = value
        else:
            hi[i] = value
        return box_mesh("EasyMold_Halfspace", lo, hi)

    def slab_mesh(self, axis, value, half_thickness):
        far = self.size * 10 + 100
        lo = self.center - Vector((far, far, far))
        hi = self.center + Vector((far, far, far))
        i = AXIS_INDEX[axis]
        lo[i] = value - half_thickness
        hi[i] = value + half_thickness
        return box_mesh("EasyMold_Slab", lo, hi)

    # --- curved parting surface ------------------------------------------

    def _curved_axis(self):
        """Axis of the curved parting surface ('X', 'Y', 'Z'), or None (flat split planes)."""
        p = self.props
        if not p.curved_parting:
            return None
        if p.mold_type == 'SILICONE_TWO_PART':
            return 'Z'
        if p.mold_type == 'DIRECT' and p.mold_parts == '2':
            return p.split_axis
        return None

    def _plane_value(self, axis, pt):
        """Where the split along `axis` is, at a point: on the curved surface, or the flat plane."""
        i = AXIS_INDEX[axis]
        if self.parting is not None and self.parting.axis == i:
            return self.parting.at(pt)
        if self.parting_z is not None and axis == 'Z':
            return self.parting_z
        return self.center[i]

    def _plane_values(self, i, points):
        """_plane_value for many points (numpy rows) along axis index i."""
        if self.parting is not None and self.parting.axis == i:
            return self.parting.at_many(points[:, self.parting.u_axis], points[:, self.parting.v_axis])
        return np.full(len(points), self.parting_z if i == 2 and self.parting_z is not None else self.center[i])

    def _make_parting(self):
        layout = [o for o in self.layout.objects if o.get(ROLE) == 'PARTING_SURFACE' and o.type == 'MESH'] \
            if self.layout else []
        if layout and layout[0].get(GRID) and int(layout[0][GRID][0]) == AXIS_INDEX[self._curved_axis()]:
            return self._parting_from_layout(layout[0])
        return self._compute_parting(AXIS_INDEX[self._curved_axis()])

    def _parting_extent(self, a):
        """(flat value, inner lo, inner hi, outer lo, outer hi, span) of the surface along axis index a: inside the
        inner rectangle it follows the object; outside, it is flat (the mold's rim, flanges, keys); the grid covers
        the outer one, beyond everything it cuts (sprue funnels included); span: the range it must stay in."""
        p = self.props
        j, k = [i for i in range(3) if i != a]
        if p.mold_type == 'DIRECT':
            lo, hi = self._block_bounds()
            flat = self.center[a]
            half = p.margin / 2
            inner_lo, inner_hi = (lo[j] + half, lo[k] + half), (hi[j] - half, hi[k] - half)
            span = (lo[a] + 0.5, hi[a] - 0.5)
            reach = (self._clip_flange_width() if p.clamp_method == 'CLIP' else 0.0) + p.sprue_height + 12
            outer_lo, outer_hi = (lo[j] - reach, lo[k] - reach), (hi[j] + reach, hi[k] + reach)
        else:
            s = p.silicone_thickness
            flat = self.parting_z
            inner_lo, inner_hi = (self.bmin.x - s, self.bmin.y - s), (self.bmax.x + s, self.bmax.y + s)
            bottom, top = self._frame_range()
            span = (bottom + 0.5, top - 0.5)
            reach = p.wall_thickness + self._clip_flange_width() + 12
            outer_lo = (inner_lo[0] - reach, inner_lo[1] - reach)
            outer_hi = (inner_hi[0] + reach, inner_hi[1] + reach)
        return flat, inner_lo, inner_hi, outer_lo, outer_hi, span

    def _compute_parting(self, a):
        """The object's outline as seen along axis index a: from each grid node, a ray along the axis; where it
        crosses the object, the surface goes halfway between where it enters first and leaves last (at the
        outline both meet: the widest edge). Around the object it is smoothed out (harmonic) to flat at the rim."""
        flat, inner_lo, inner_hi, outer_lo, outer_hi, span = self._parting_extent(a)
        j, k = [i for i in range(3) if i != a]
        coarse = 3 if self.kind == 'PLAN' else 1  # the overlay only shows it: a coarser grid is ~10x faster
        cell = max(self.size / 120, 0.25, max(outer_hi[0] - outer_lo[0], outer_hi[1] - outer_lo[1]) / 260) * coarse
        session = None
        if self._session_key is not None:
            session = (self._session_key, a, round(flat, 6), tuple(round(c, 6) for c in inner_lo + inner_hi + outer_lo
                                                                  + outer_hi + span), round(cell, 6))
            if session in _PARTINGS:
                _PARTINGS[session] = _PARTINGS.pop(session)
                self.reused.append("the parting surface")
                return PartingSurface(a, outer_lo, cell, _PARTINGS[session].copy())
        surface = self._trace_parting(a, j, k, flat, inner_lo, inner_hi, outer_lo, outer_hi, span, cell)
        if session is not None:
            _PARTINGS[session] = surface.values.copy()
            while len(_PARTINGS) > MAX_OFFSETS:
                _PARTINGS.pop(next(iter(_PARTINGS)))
        return surface

    def _trace_parting(self, a, j, k, flat, inner_lo, inner_hi, outer_lo, outer_hi, span, cell):
        nu = int(math.ceil((outer_hi[0] - outer_lo[0]) / cell)) + 1
        nv = int(math.ceil((outer_hi[1] - outer_lo[1]) / cell)) + 1
        values = np.full((nu, nv), flat)
        fixed = np.ones((nu, nv), dtype=bool)
        way = Vector()
        way[a] = 1.0
        start = self.bmin[a] - 1.0
        for iu in range(nu):
            u = outer_lo[0] + iu * cell
            for iv in range(nv):
                v = outer_lo[1] + iv * cell
                if not (inner_lo[0] < u < inner_hi[0] and inner_lo[1] < v < inner_hi[1]):
                    continue  # the flat rim
                origin = Vector()
                origin[a], origin[j], origin[k] = start, u, v
                hits = []
                for _ in range(64):
                    hit = self.bvh.ray_cast(origin, way)[0]
                    if hit is None:
                        break
                    hits.append(hit[a])
                    origin = hit + way * 1e-4
                if hits:
                    values[iu, iv] = min(max((hits[0] + hits[-1]) / 2, span[0]), span[1])
                else:
                    fixed[iu, iv] = False
        values = harmonic_fill(values, fixed, min(6000, max(400, (max(nu, nv) ** 2) // 6)))
        return PartingSurface(a, outer_lo, cell, values)

    def _parting_from_layout(self, obj):
        """The surface as edited in the layout, sampled again on its grid (rays along its axis)."""
        a, u0, v0, cell, nu, nv = list(obj[GRID])
        a, nu, nv = int(a), int(nu), int(nv)
        j, k = [i for i in range(3) if i != a]
        dg = bpy.context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
        mesh.transform(obj.matrix_world)
        co = [v.co.copy() for v in mesh.vertices]
        bvh = BVHTree.FromPolygons(co, [tuple(f.vertices) for f in mesh.polygons])
        bpy.data.meshes.remove(mesh)
        low = min(c[a] for c in co) - 10
        high = max(c[a] for c in co) + 10
        values = np.zeros((nu, nv))
        fixed = np.zeros((nu, nv), dtype=bool)
        for iu in range(nu):
            for iv in range(nv):
                for start, sign in ((low, 1.0), (high, -1.0)):
                    origin, way = Vector(), Vector()
                    origin[a], origin[j], origin[k] = start, u0 + iu * cell, v0 + iv * cell
                    way[a] = sign
                    hit = bvh.ray_cast(origin, way)[0]
                    if hit is not None:
                        values[iu, iv], fixed[iu, iv] = hit[a], True
                        break
        if not fixed.any():
            raise MoldError("The Parting Surface of the layout is empty: reset the layout")
        values[~fixed] = values[fixed].mean()
        values = harmonic_fill(values, fixed, min(6000, max(400, (max(nu, nv) ** 2) // 6)))
        return PartingSurface(a, (u0, v0), cell, values)

    def parting_solid(self, side):
        """Closed solid on one side of the curved parting surface (replaces a half-space)."""
        far = self.center[self.parting.axis] + side * (self.size * 10 + 100)
        return self.parting.solid_mesh("EasyMold_PartingSide", side, far)

    # --- splitting & keys ------------------------------------------------

    def split(self, pieces, axis):
        """Split every (object, signs) piece by the plane through the center perpendicular to axis."""
        value = self.center[AXIS_INDEX[axis]]
        if self.parting is not None and self.parting.axis == AXIS_INDEX[axis]:
            cutter = self.add_object("EasyMold_Cutter", self.parting_solid(+1), temp=True)
        else:
            cutter = self.add_object("EasyMold_Cutter", self.halfspace_mesh(axis, value, +1), temp=True)
        result = []
        for obj, signs in pieces:
            minus = self.add_object(obj.name, obj.data.copy())
            if self._is_dirty(obj):
                self._mark_dirty(minus)
            self.boolean(obj, cutter, 'INTERSECT')
            self.boolean(minus, cutter, 'DIFFERENCE')
            for part, side in ((obj, +1), (minus, -1)):
                if not part.data.vertices:
                    self.remove(part)
                    continue
                part_signs = dict(signs)
                part_signs[axis] = side
                result.append((part, part_signs))
        self.remove(cutter)
        return result

    def add_keys(self, pieces):
        """Peg on the + side of each key's split plane, socket (+tolerance) on the - side.

        On a horizontal split the pegs go on the lower part instead: it prints on its bottom with the pegs
        up, and the upper part can lie on its flat cut face.
        """
        tol = self.props.key_tolerance
        shape = self.props.key_shape
        for obj, signs in pieces:
            pegs, sockets = [], []
            for axis, p, r in self.keys:
                if axis not in signs or not self._same_region(signs, axis, p):
                    continue
                if signs[axis] * (-1 if axis == 'Z' else 1) > 0:
                    pegs.append((key_mesh("EasyMold_Peg", axis, p, r, shape), p, r * 1.5))
                else:
                    sockets.append((key_mesh("EasyMold_Socket", axis, p, r, shape, grow=tol), p, (r + tol) * 1.5))
            self.boolean_many(obj, pegs, 'UNION')
            self.boolean_many(obj, sockets, 'DIFFERENCE')

    def _same_region(self, signs, axis, p):
        for other, side in signs.items():
            if other == axis:
                continue
            i = AXIS_INDEX[other]
            if (p[i] - self.center[i]) * side <= 0:
                return False
        return True

    def explode(self, pieces):
        dist = self.size * 0.15
        for obj, signs in pieces:
            for axis, side in signs.items():
                obj.location[AXIS_INDEX[axis]] += side * dist

    def drop_fragments(self, obj, keep=0.02):
        """Delete loose islands smaller than `keep` × the biggest one (slivers left by cuts can't be printed
        or would fall into the mold). Returns how many were removed."""
        return len(self.take_islands(obj, keep)[1])

    def take_islands(self, obj, keep=0.02, name="Islands"):
        """Remove the loose islands smaller than `keep` × the biggest one from obj; returns (mesh with them or
        None, their volumes)."""
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        islands = face_islands(bm)
        if len(islands) < 2:
            bm.free()
            return None, []
        biggest = max(v for v, _ in islands)
        small = [(v, isl) for v, isl in islands if v < biggest * keep]
        taken = None
        if small:
            faces = [f for _v, isl in small for f in isl]
            taken = faces_mesh(name, faces)
            # Faces only: an island touching the main part at a single vertex must not take it along.
            bmesh.ops.delete(bm, geom=faces, context='FACES')
            bm.to_mesh(obj.data)
            obj.data.update()
        bm.free()
        return taken, [v for v, _isl in small]

    def finish_pieces(self, pieces, prefix, back_down=False):
        """Name the pieces after their side of each split, alternate their colors, drop loose slivers, and
        record how they can lie on the print bed.

        Bed: a shell piece on its cut face (flat flange), unless pegs stick out of it; a block piece
        (back_down) on its back, so the cavity faces up and needs no supports; otherwise upright.
        """
        dropped = sum(self.drop_fragments(obj) for obj, _signs in pieces)
        if dropped:
            self.notes.append(f"removed {dropped} loose fragment(s) left by the cuts")
        for obj, signs in pieces:
            suffix = "_".join(f"{axis}{'+' if side > 0 else '-'}" for axis, side in sorted(signs.items()))
            obj.name = f"{prefix}_{suffix}" if suffix else prefix
            self._styled.append((obj, 'PART_A' if math.prod(signs.values()) > 0 else 'PART_B'))
            cuts = [AXIS_VECTOR[axis] * -side for axis, side in sorted(signs.items())]
            backs = [-d for d in cuts] if back_down else []
            export.set_down(obj, backs + cuts + [Vector((0, 0, -1))])

    # --- keys, pour hole & vents placement -------------------------------

    def _floor(self):
        # Sink the master slightly into the base so the union is solid (avoids coplanar faces).
        return self.bmin.z + min(0.2, self.props.silicone_thickness * 0.05)

    def _block_bounds(self):
        m = self.props.margin
        lo = self.bmin - Vector((m, m, m))
        hi = self.bmax + Vector((m, m, m))
        if self.props.mold_parts == '1':
            # Open-top mold: the top of the object is the open face.
            hi.z = self.bmax.z - min(0.01, self.size * 1e-3)
        return lo, hi

    def _key_radius(self, auto):
        return self.props.key_radius if self.props.key_radius > 0 else auto

    def auto_spec(self, axes):
        """Automatic positions of keys, bolts, pour hole and vents (clips grip the flanges anywhere)."""
        p = self.props
        self.keys, self.bolts, self.pours, self.vents, self.gates = [], [], [], [], []
        if p.mold_type == 'SILICONE_BOX':
            return
        if p.mold_type == 'SILICONE_TWO_PART':
            if p.use_keys:
                self.keys = self._parting_keys()
        elif p.mold_type == 'DIRECT':
            if not axes:
                return
            lo, hi = self._block_bounds()
            if p.use_keys:
                self.keys = self._block_keys(axes, lo, hi)
            if p.clamp_method == 'SCREW':
                self.bolts = self._block_edge_points(axes, lo, hi)
            if self.core_prints:
                self._clear_core_prints()
        elif axes:
            s, w, fw = p.silicone_thickness, p.wall_thickness, p.flange_width
            self._place_flange_parts(self._bvh(self.offset_source(s + w + fw)), axes)
        if p.use_sprues:
            r = p.sprue_radius
            whole = self.bvh
            for _ in self._focused():  # each object of a tray gets its own
                vents = p.vent_count
                gate = self._plan_gate(r, whole) if self._entry_from_below() else None
                if gate is not None:
                    self._add_gate(gate, r)
                    # Filled from below, the air leaves at the top: it needs a vent there.
                    vents = max(vents, 1)
                else:
                    pt = self._auto_pour_point(axes)
                    if self.core_bvh is not None:
                        pt = self._pour_beside_cores(pt, r)
                    self.pours.append((pt, r))
                self.vents += [(v, p.vent_radius) for v in self._auto_vents(vents, p.vent_radius)]
            if self.gates:
                self._clear_gates()

    def _focused(self):
        """Loop once per object of a tray, with the analysis data (BVH, bounds, top vertices) of that object
        alone; once, unchanged, for a single object. The split planes stay those of the whole mold."""
        if not self.tray:
            yield
            return
        saved = (self.bvh, self.bmin, self.bmax, self.center, self.size, self.high_verts)
        try:
            for part in self.tray:
                self.bvh, self.bmin, self.bmax, self.high_verts = (part['bvh'], part['bmin'], part['bmax'],
                                                                   part['high_verts'])
                self.center = (self.bmin + self.bmax) / 2
                for axis in self.axes:
                    self.center[AXIS_INDEX[axis]] = saved[3][AXIS_INDEX[axis]]
                self.size = max(self.bmax - self.bmin)
                yield
        finally:
            self.bvh, self.bmin, self.bmax, self.center, self.size, self.high_verts = saved

    def _top_of(self, pt):
        """Top of the object under a point (of its tray object if there are several)."""
        for part in self.tray:
            lo, hi = part['bmin'], part['bmax']
            if lo.x - 1e-3 <= pt.x <= hi.x + 1e-3 and lo.y - 1e-3 <= pt.y <= hi.y + 1e-3:
                return hi.z
        return self.bmax.z

    def spec_from_layout(self, axes):
        """Keys, fasteners, pour holes and vents from the markers of an edited layout."""
        self.keys, self.bolts, self.pours, self.vents, self.gates = [], [], [], [], []
        self.marker_of = {}
        method = self.props.clamp_method
        for obj in self.layout.objects:
            role = obj.get(ROLE)
            p = obj.matrix_world.translation.copy()
            self.marker_of[id(p)] = (obj.name, p)
            r = marker_radius(obj) if obj.type == 'EMPTY' else 0
            if role == 'KEY' and self.props.mold_type == 'SILICONE_TWO_PART':
                if self.props.use_keys:
                    p.z = self._plane_value('Z', p)  # onto the parting line
                    self.keys.append(('Z', p, r))
            elif role in ('KEY', 'BOLT') and axes:
                # Snap onto the nearest split plane (or the curved parting surface).
                axis = min(axes, key=lambda a: abs(p[AXIS_INDEX[a]] - self._plane_value(a, p)))
                p[AXIS_INDEX[axis]] = self._plane_value(axis, p)
                if role == 'KEY' and self.props.use_keys:
                    self.keys.append((axis, p, r))
                elif role == 'BOLT' and method == 'SCREW':
                    self.bolts.append((axis, p))
            elif role == 'POUR' and self.props.use_sprues and self._entry_from_below() and \
                    (gate := self._gate_from_marker(p, r)) is not None:
                # The marker is where the channel enters the part, on the nearest vertical split plane.
                self._add_gate(gate, r)
                self.marker_of[id(gate[0])] = (obj.name, gate[0])
            elif role in ('POUR', 'VENT') and self.props.use_sprues:
                if role == 'POUR' and self._entry_from_below():
                    # No channel from below fits there (or the layout had none): poured from the top over it.
                    self.notes.append("no pour entry from below at the Pour marker: poured from the top above it")
                hit, _n, _i, _d = self.bvh.ray_cast(Vector((p.x, p.y, self.bmax.z + 1)), Vector((0, 0, -1)))
                if hit is not None:
                    self.marker_of[id(hit)] = (obj.name, hit)
                (self.pours if role == 'POUR' else self.vents).append((hit if hit is not None else p, r))
        if self.gates:
            self._clear_gates()

    # --- entry from below on the parting line (direct mold) -------------------

    GATE_MIN_R = 0.8  # narrowest pour channel worth making

    def _entry_from_below(self):
        p = self.props
        return p.mold_type == 'DIRECT' and p.pour_entry == 'BOTTOM' and any(a != 'Z' for a in self.axes)

    def _plan_gate(self, r, whole):
        """Automatic entry from below: on a vertical split plane, from the side where the hole is widest, then
        lowest, then shortest. None (with a note) if there is no room: the caller pours from the top."""
        found = [gate for axis in self.axes if axis != 'Z' for side in (-1, 1)
                 if (gate := self._gate_at(AXIS_INDEX[axis], side, r, whole)) is not None]
        if not found:
            self.notes.append("no room for the pour entry from below: poured from the top")
            return None
        return min(found, key=lambda g: (-round(g[2], 3), round(g[0].z, 2), (g[1] - g[0]).length))

    def _gate_from_marker(self, p, r):
        """Entry from below at a layout Pour marker: on the nearest vertical split plane, at its height."""
        vertical = [AXIS_INDEX[a] for a in self.axes if a != 'Z']
        a = min(vertical, key=lambda i: abs(p[i] - self.center[i]))
        bounds = next(((part['bmin'], part['bmax']) for part in self.tray
                       if all(part['bmin'][i] - 1.0 <= p[i] <= part['bmax'][i] + 1.0 for i in range(3))),
                      (self.bmin, self.bmax))
        side = 1 if p[1 - a] >= (bounds[0][1 - a] + bounds[1][1 - a]) / 2 else -1
        return self._gate_at(a, side, r, self.bvh, z=p.z, bounds=bounds)

    def _gate_at(self, a, side, r, whole, z=None, bounds=None):
        """Pour channel on the vertical split plane of axis index `a`: a sprue straight down through the block
        margin beside the object (on the `side` of the other horizontal axis), then a gate along the plane into
        the object's side. Returns (sprue point at the gate's height, gate end just inside the object, radius,
        path of the gate from the sprue to its end) or None. Without `z`, at the lowest height where the whole
        hole lands on the object; the radius shrinks (down to GATE_MIN_R) when it can't fit. On a curved parting
        surface the gate follows it."""
        t = 1 - a
        axis = "XYZ"[a]
        bmin, bmax = bounds or (self.bmin, self.bmax)
        lo, hi = self._block_bounds()
        edge = bmax[t] if side > 0 else bmin[t]
        # Free margin beside the object: to the side of the block, or to the next object of a tray.
        room = abs((hi[t] if side > 0 else lo[t]) - edge)
        out = Vector()
        out[t] = side
        for k in range(5):
            q = Vector()
            q[t], q.z = edge + side * 0.01, bmin.z + (bmax.z - bmin.z) * (k + 0.5) / 5
            q[a] = self._plane_value(axis, q)
            hit = whole.ray_cast(q, out)
            if hit[0] is not None:
                room = min(room, hit[3])
        radius = min(r, room / 2 - 1.0)
        sprue = Vector()
        sprue[t] = edge + side * room / 2
        way = -out
        while radius >= self.GATE_MIN_R:
            if z is not None:
                heights = [z]
            else:
                step = max(0.2, (bmax.z - bmin.z) / 100)
                heights = [bmin.z + radius * 1.1 + k * step
                           for k in range(max(int((bmax.z - bmin.z - 2.2 * radius) / step), 0) + 1)]
            for h in heights:
                sprue.z = h
                sprue[a] = self._plane_value(axis, sprue)
                found = self._gate_entry(sprue, way, a, radius, whole, bmin, bmax)
                if found is not None and self._gate_clear(sprue, found[1], a, radius, whole, hi.z):
                    entry, path = found
                    end = entry + (path[-1] - path[-2]).normalized() * min(0.3, radius * 0.5)
                    return sprue.copy(), end, radius, path[:-1] + [end]
            if radius <= self.GATE_MIN_R:
                break
            radius = max(radius * 0.8, self.GATE_MIN_R)
        return None

    def _gate_entry(self, origin, way, a, radius, whole, bmin, bmax):
        """Where a gate of this radius from `origin` along `way` (on the split plane, or following the curved
        parting surface) lands on the object: (point, path from origin to it), if all of it lands on it (not
        partly past its bottom or edge) and no core is in the way."""
        path = [origin.copy()]
        curved = self.parting is not None and self.parting.axis == a
        if curved:
            step = max(self.parting.cell, 0.25)
            limit = (bmax - bmin).length + abs(way.dot(origin - (bmin + bmax) / 2)) + 1
            hit = None
            for n in range(1, int(limit / step) + 2):
                q = self.parting.on(origin + way * (n * step))
                segment = q - path[-1]
                hit, _n, _i, d = whole.ray_cast(path[-1], segment.normalized(), segment.length)
                if hit is not None:
                    break
                path.append(q)
            if hit is None:
                return None
            start, way = path[-1], segment.normalized()
        else:
            start = origin
            hit, _n, _i, d = whole.ray_cast(start, way)
            if hit is None:
                return None
        if not all(bmin[i] - 0.01 <= hit[i] <= bmax[i] + 0.01 for i in range(3)):
            return None
        if self.core_bvh is not None:
            core = self.core_bvh.ray_cast(start, way)
            if core[0] is not None and core[3] < d + 0.5:
                return None
        for k in range(8):
            angle = 2 * math.pi * k / 8
            q = start.copy()
            q[a] += math.cos(angle) * radius
            q.z += math.sin(angle) * radius
            rim = whole.ray_cast(q, way)
            if rim[0] is None or rim[3] > d + radius:
                return None
        return hit, path + [hit]

    def _gate_clear(self, sprue, path, a, radius, whole, top):
        """Nothing above the sprue (the object, a core) and no core print on the way of the sprue or the gate."""
        up = Vector((0, 0, 1))
        t = 1 - a
        for k in range(9):
            q = sprue.copy()
            if k:
                angle = 2 * math.pi * k / 8
                q[a] += math.cos(angle) * radius
                q[t] += math.sin(angle) * radius
            if whole.ray_cast(q, up)[0] is not None:
                return False
            if self.core_bvh is not None and self.core_bvh.ray_cast(q, up)[0] is not None:
                return False
        if self.core_prints:
            top_pt = Vector((sprue.x, sprue.y, top))
            segments = [(sprue, top_pt)] + list(zip(path, path[1:]))
            n = 10
            if any(self._in_core_print(s0.lerp(s1, k / n), radius) for s0, s1 in segments for k in range(n + 1)):
                return False
            # Nor next to a core's peg (its socket is in the block).
            gap = radius + self.props.key_tolerance + self.KEY_MARGIN
            if any(min(_segment_distance(c, s0, s1) for s0, s1 in segments) < r + gap
                   for _a, c, r in self._core_pegs(self.axes)):
                return False
        return True

    def _add_gate(self, gate, wanted):
        sprue, _end, radius, _path = gate
        if radius < wanted - 1e-6:
            self.warnings.append(f"The pour channel is {radius:.1f} mm in radius instead of {wanted:g} to fit (in the "
                                 "block margin, on the side of the part)")
        self.pours.append((sprue, radius))
        self.gates.append(gate)

    def _clear_gates(self):
        """Leave out the keys and bolts that the pour channels run into."""
        lo, hi = self._block_bounds()
        paths = []
        for sprue, _end, r, path in self.gates:
            paths += [(sprue, Vector((sprue.x, sprue.y, hi.z + 1)), r)] + [(s0, s1, r) for s0, s1 in zip(path, path[1:])]

        def near(pt, extra):
            return any(_segment_distance(pt, s0, s1) < r + extra for s0, s1, r in paths)

        p = self.props
        keys = [key for key in self.keys if not near(key[1], key[2] + p.key_tolerance + 1.0)]
        nut_r = self._bolt()['nut_r'] + 1.0

        def crosses(axis, c):
            i = AXIS_INDEX[axis]
            for n in range(41):
                q = c.copy()
                q[i] = lo[i] + (hi[i] - lo[i]) * n / 40
                if near(q, nut_r):
                    return True
            return False

        bolts = [bolt for bolt in self.bolts if not crosses(*bolt)]
        dropped = len(self.keys) - len(keys) + len(self.bolts) - len(bolts)
        if dropped:
            self.notes.append(f"left out {dropped} key(s)/bolt(s) in the way of the pour channel")
        self.keys, self.bolts = keys, bolts

    def _auto_pour_point(self, axes):
        """Highest point of the object on the vertical split plane(s), so the hole is shared by the parts."""
        height = self.bmax.z - self.bmin.z
        vertical = [a for a in axes if a != 'Z']
        if len(vertical) == 2:
            # 4 parts: right where they all meet, so each part gets a quarter of the hole and no funnel
            # crosses a split. If that spot is lower than the top, cut_pours raises a tube above the mold.
            hit = self.bvh.ray_cast(Vector((self.center.x, self.center.y, self.bmax.z + 1)), Vector((0, 0, -1)))[0]
            if hit is not None:
                return hit
            self.notes.append("pour hole off the center: the object has a hole there")
        hits = []
        for axis in vertical:
            i = AXIS_INDEX[axis]
            j = 1 - i
            for k in range(41):
                q = self.center.copy()
                q[j] = self.bmin[j] + (self.bmax[j] - self.bmin[j]) * k / 40
                q.z = self.bmax.z
                q[i] = self._plane_value(axis, q)
                hit, _n, _i, _d = self.bvh.ray_cast(Vector((q.x, q.y, self.bmax.z + 1)), Vector((0, 0, -1)))
                if hit is not None:
                    hits.append(hit)
        # On the split plane only if that is (nearly) the top of the object: pouring lower than the top
        # leaves the part above the pour level unfilled.
        tolerance = max(height * 0.05, self.props.sprue_radius)
        if hits and max(h.z for h in hits) >= self.bmax.z - tolerance:
            top = max(h.z for h in hits)
            high = [h for h in hits if h.z >= top - height * 0.03]
            return min(high, key=lambda h: (h.xy - self.center.xy).length)
        if hits:
            self.notes.append("pour hole off the split line: the top of the object is not on it")

        # No vertical split: center of a flat top if there is one, otherwise the highest vertex.
        band = max(height * 0.03, self.props.sprue_radius * 0.5)
        top_verts = [v for v in self.high_verts if v.z >= self.bmax.z - band]
        cx = sum(v.x for v in top_verts) / len(top_verts)
        cy = sum(v.y for v in top_verts) / len(top_verts)
        hit, _n, _i, _d = self.bvh.ray_cast(Vector((cx, cy, self.bmax.z + 1)), Vector((0, 0, -1)))
        if hit is not None and hit.z >= self.bmax.z - band:
            return hit
        return self.high_verts[0]

    def _auto_vents(self, count, vent_r):
        """Vents where air gets trapped while filling: upward-facing local peaks, clear of the pour hole."""
        if count <= 0:
            return []
        co, normals, edges = analysis.mesh_arrays(self.target.data)
        traps = analysis.air_traps(co, normals, edges, radius=max(vent_r * 4, self.size * 0.08))
        vents = []
        for v in traps:
            if len(vents) >= count:
                break
            if not all(self.bmin[i] - 1e-3 <= v[i] <= self.bmax[i] + 1e-3 for i in range(3)):
                continue  # another object of a tray
            if v.z < self.bmin.z + self.size * 0.02:
                continue  # resting on the base: nothing to fill above it
            if self.bvh.ray_cast(v + Vector((0, 0, 0.05)), Vector((0, 0, 1)))[0] is not None:
                continue  # under another part of the object: a vertical vent can't reach it
            if self._core_blocked(v, vent_r):
                continue  # under a core, which closes the hollow
            if any((v.xy - pt.xy).length < self._pour_clearance(pt, r, v, vent_r) for pt, r in self.pours):
                continue
            if all((v.xy - other.xy).length > vent_r * 4 for other in vents):
                vents.append(v)
        return vents

    def _pour_wall(self):
        p = self.props
        return p.margin if p.mold_type == 'DIRECT' else p.wall_thickness

    @staticmethod
    def _funnel_size(radius, wall):
        return min(radius * 1.5, wall * 0.6)

    @staticmethod
    def _hole_chamfer(radius):
        """Depth of the 45° chamfer at the mouth of a straight pour hole."""
        return min(0.3 * radius + 0.3, 1.5)

    def _sprue_shape(self, r, pt=None):
        """(mouth radius, cup wall thickness) of the raised sprue funnel, or None without one.

        With the pour point given, the mouth is narrowed so the cup doesn't reach across a split plane
        that doesn't go through the hole (that strip would end up as a loose piece of the other part).
        """
        p = self.props
        if p.pour_style != 'FUNNEL' or p.sprue_height <= 0:
            return None
        thickness = min(max(self._pour_wall() * 0.6, 1.6), 3.0)
        mouth = r + p.sprue_height * 0.6  # ~31 degree funnel
        if pt is not None:
            for axis in self.axes:
                i = AXIS_INDEX[axis]
                distance = abs(pt[i] - self._plane_value(axis, pt))
                if distance > 0.5:  # the plane doesn't go through the hole
                    mouth = min(mouth, distance - thickness - 0.5)
            if p.mold_type == 'DIRECT':
                # Within the block's outline (a sprue beside the part is near its side).
                lo, hi = self._block_bounds()
                for i in (0, 1):
                    mouth = min(mouth, pt[i] - lo[i] - thickness, hi[i] - pt[i] - thickness)
            mouth = max(mouth, r)
        return mouth, thickness

    def _surface_z(self, pt):
        """Height of the outer mold surface above a point of the object (where holes come out)."""
        p = self.props
        if p.mold_type == 'DIRECT':
            return self._block_bounds()[1].z
        return pt.z + p.silicone_thickness + p.wall_thickness

    def _pour_lift(self, pt):
        """Height where the pour tube ends and the funnel (if any) starts.

        Normally the mold surface over the hole. A hole lower than the top of the object (e.g. at the center
        of a 4-part mold, inside a cup) gets a straight tube up to the top of the mold: poured any lower, the
        silicone would spill out before reaching the highest parts, and the funnel would hit the mold.
        """
        top = self._surface_z(pt)
        p = self.props
        if p.mold_type == 'DIRECT':
            return top  # the block's top is above the whole cavity
        peak = self._top_of(pt) + p.silicone_thickness + max(p.wall_thickness, 2.0)
        return peak if peak - top > 0.5 else top

    def _hole_ring(self):
        """Material kept around a hole through a shell."""
        return max(1.6, min(self.props.wall_thickness, 3.0)) if self.props.mold_type != 'DIRECT' else 0.0

    def _pour_clearance(self, pour, r, vent, vent_r):
        """Minimum horizontal distance between a vent and the pour hole, so the vent doesn't cut the hole,
        its funnel, or the raised sprue cup (as wide as it is at the height where the vent comes out)."""
        if self.props.mold_type == 'SILICONE_TWO_PART':
            return max(rr for _z, rr in self._sprue_profile(pour, r)) + vent_r + 1.0
        ring = self._hole_ring()
        sprue = self._sprue_shape(r, pour)
        if sprue is None:
            return r + max(self._funnel_size(r, self._pour_wall()) + 1, ring) + vent_r + 0.5
        mouth, thickness = sprue
        thickness = max(thickness, ring)  # reinforced cup base
        rise = (self._surface_z(vent) + 1 - self._pour_lift(pour)) / self.props.sprue_height
        cup = r + thickness + (mouth - r) * min(max(rise, 0.0), 1.0)
        return cup + vent_r + 0.5

    # --- where the holes touch the cast part: narrow tips, a round elbow, vents leaning away ---------------

    def _tip(self, r, tip):
        """Radius where a hole of radius r touches the cast part (only molds that cast it: direct, 2-part)."""
        if self.props.mold_type not in ('DIRECT', 'SILICONE_TWO_PART') or tip <= 0:
            return r
        return min(max(tip, 0.3), r)

    @staticmethod
    def _taper(r, tip):
        """Length of the cone from a narrow tip out to the full radius (about 25° to its axis)."""
        return max(1.0, (r - tip) * 2.2) if tip < r else 0.0

    def _tip_profile(self, z, r, tip, room):
        """Bottom of a vertical hole that touches the part at height z, [(z, radius), ...]: r deep into the part
        as before, or a tip as deep as it is wide that widens in a cone (no longer than `room`)."""
        if tip >= r:
            return [(z - r, r)]
        return [(z - tip, tip), (z + min(self._taper(r, tip), room), r)]

    def _gate_tube(self, gate):
        """Axis points and radii of an entry channel from below, and the elbow's radius: down the sprue, round
        the elbow (radius about twice the channel's, instead of a sharp L) and along the gate, narrowing to the
        entry tip at its end inside the part."""
        sprue, end, r, path = gate
        up = Vector((0, 0, 1))
        length = sum((b - a).length for a, b in zip(path, path[1:]))
        bend = min(2 * r, 0.45 * length)
        way = path[1] - path[0]
        way.z = 0
        way.normalize()
        center = sprue + up * bend + way * bend
        arc = [center - way * bend * math.cos(a) - up * bend * math.sin(a)
               for a in (math.pi / 2 * k / 8 for k in range(9))]
        # The rest of the gate, resampled so the taper gets its own rings.
        step = max(0.25, r / 3)
        dense = [path[0]]
        for a, b in zip(path, path[1:]):
            n = max(1, math.ceil((b - a).length / step))
            dense += [a.lerp(b, k / n) for k in range(1, n + 1)]
        run, rest = 0.0, []
        for a, b in zip(dense, dense[1:]):
            run += (b - a).length
            if run > bend + step * 0.5:
                rest.append(b)
        if not rest:
            rest = [end]
        # Down the sprue a little first (narrower: it ends inside the sprue hole, no surfaces on top of its own).
        points = [sprue + up * (bend + r)] + arc + rest
        tip = self._tip(r, self.props.pour_tip)
        inside = min(0.3, r * 0.5)  # how far the gate goes past the part's surface
        # The cone takes the straight stretch after the elbow (steeper on a short gate, none if no room).
        taper = min(self._taper(r, tip), max(length - bend - inside, 0.0))
        if taper < 0.3:
            taper = 0.0
        radii, to_end = [], 0.0
        ends = [to_end]
        for a, b in zip(reversed(points[1:]), reversed(points[:-1])):
            to_end += (a - b).length
            ends.append(to_end)
        for d in reversed(ends):
            if taper <= 0 or d > inside + taper:
                radii.append(r)
            elif d <= inside:
                radii.append(tip)
            else:
                radii.append(tip + (r - tip) * (d - inside) / taper)
        radii[0] = r * 0.96
        return points, radii, bend

    def _vent_axis(self, pt, r, top):
        """Axis points and radii of a vent from the part at pt up to `top`."""
        p = self.props
        tip = self._tip(r, p.vent_tip)
        profile = self._tip_profile(pt.z, r, tip, (top - pt.z) * 0.5)
        if tip >= r:
            profile.append((pt.z, r))  # the vent leans from the part's surface
        points = [Vector((pt.x, pt.y, z)) for z, _r in profile]
        radii = [rr for _z, rr in profile]
        if p.mold_type == 'DIRECT' and p.vent_route == 'AWAY':
            route = self._vent_route(pt, r, points[-1], top)
        else:
            route = [points[-1], Vector((pt.x, pt.y, top))]
        return points + route[1:], radii + [r] * (len(route) - 1)

    def _vent_route(self, pt, r, start, top):
        """Axis of a vent that leans away from the part, from `start` (where it is fully wide) to `top`: straight
        up if that stays clear, otherwise at 45° to the nearest spot from where it does (along a split plane it
        sits on first), then up. Clear: the wide vent never comes closer to the part than to the point where it
        touches it, nor than r + 0.5 farther off; inside the block, away from the other holes, keys and bolts."""
        up = Vector((0, 0, 1))
        lo, hi = self._block_bounds()
        edge = r + 0.8
        others = [(q, Vector((q.x, q.y, hi.z + 1)), rq + r + 1.0) for q, rq in self.pours]
        others += [(q, Vector((q.x, q.y, hi.z + 1)), rq + r + 1.0) for q, rq in self.vents if q is not pt]
        for _sprue, _end, rq, path in self.gates:
            others += [(a, b, rq + r + 1.0) for a, b in zip(path, path[1:])]
        others += [(c, c, rc + self.props.key_tolerance + r + 0.8) for _a, c, rc in self.keys]
        if self.bolts:
            nut = self._bolt()['nut_r'] + r + 0.8
            for axis, c in self.bolts:
                i = AXIS_INDEX[axis]
                a, b = c.copy(), c.copy()
                a[i], b[i] = lo[i], hi[i]
                others.append((a, b, nut))
        solids = [self.bvh] + ([self.core_bvh] if self.core_bvh is not None else [])

        def distance(q):
            return min((d for bvh in solids if (d := bvh.find_nearest(q)[3]) is not None), default=math.inf)

        first = distance(start)

        def clear(axis):
            run = 0.0
            for a, b in zip(axis, axis[1:]):
                n = max(1, math.ceil((b - a).length / 0.25))
                for k in range(n + 1):
                    q = a.lerp(b, k / n)
                    if not (lo.x + edge <= q.x <= hi.x - edge and lo.y + edge <= q.y <= hi.y - edge):
                        return False
                    # Moving away from the part as it goes (or far enough already).
                    if distance(q) < min(r + 0.5, first + 0.5 * (run + (b - a).length * k / n)) - 1e-6:
                        return False
                    if any(_segment_distance(q, s0, s1) < gap for s0, s1, gap in others):
                        return False
                    if self.core_prints and self._in_core_print(q, r + 0.5):
                        return False
                run += (b - a).length
            return True

        straight = [start, Vector((start.x, start.y, top))]
        if clear(straight):
            return straight
        along = []  # on a vertical split plane, leaning along it keeps the vent shared by the parts
        for axis in self.axes:
            i = AXIS_INDEX[axis]
            if axis != 'Z' and abs(pt[i] - self._plane_value(axis, pt)) < r:
                for side in (1, -1):
                    way = Vector()
                    way[1 - i] = side
                    along.append(way)
        ways = along +[Vector((math.cos(a), math.sin(a), 0)) for a in (2 * math.pi * k / 16 for k in range(16))]
        for s in (r, 1.5 * r, 2 * r, 3 * r, 4 * r, 5 * r, 6 * r):
            if start.z + s > top - r:
                break
            for way in ways:
                bend = start + way * s + up * s
                axis = [start, bend, Vector((bend.x, bend.y, top))]
                if clear(axis):
                    return axis
        self.notes.append("a vent couldn't lean clear of the part: it goes straight up")
        return straight

    def cut_pours(self, obj):
        """Cut the pour hole(s) and vents; with the funnel style, first add a raised sprue cup.

        Done before splitting, so a pour hole on the split plane is shared by the parts. Returns the pour
        holes as (matrix, profile).
        """
        p = self.props
        wall = self._pour_wall()
        surface_z = self._surface_z
        shell = p.mold_type != 'DIRECT'
        ring = self._hole_ring()
        bosses, holes = [], []  # (matrix, profile) solids of revolution around / for each hole
        gate_tubes = {id(gate[0]): self._gate_tube(gate) for gate in self.gates}
        for pt, r in self.pours:
            top = surface_z(pt)
            lift = self._pour_lift(pt)
            if id(pt) in gate_tubes:
                # The sprue of an entry from below ends above the round elbow, which the gate's tube makes.
                bottom = [(pt.z + gate_tubes[id(pt)][2] + 0.5 * r, r)]
            else:
                bottom = self._tip_profile(pt.z, r, self._tip(r, p.pour_tip), (lift - pt.z) * 0.4)
            if lift > top:
                self.notes.append(f"raised the pour hole {lift - top:.0f} mm so the silicone reaches the top")
            at = Matrix.Translation((pt.x, pt.y, 0))
            sprue = self._sprue_shape(r, pt)
            if sprue is not None:
                mouth, thickness = sprue
                if mouth < self._sprue_shape(r)[0] - 0.01:
                    self.notes.append("narrowed the sprue funnel so it doesn't cross a split line")
                for axis in self.axes:
                    i = AXIS_INDEX[axis]
                    if 0.5 < abs(pt[i] - self._plane_value(axis, pt)) < r + thickness + 0.5:
                        self.warnings.append("The pour hole is too wide for its spot: it crosses a split line. "
                                             "Use a smaller radius or move it in the layout")
                        break
                h = p.sprue_height
                # Cup body: from the cavity (trimmed off it) up through the mold wall, then the funnel.
                start = pt.z if shell else top - wall * 0.9
                outer = r + max(thickness, ring)
                bosses.append((at, [(start, outer), (lift, outer), (lift + h, max(outer, mouth + thickness))]))
                profile = bottom + [(lift, r), (lift + h + 1, mouth + 0.6)]
            else:
                if shell:
                    bosses.append((at, [(pt.z, r + ring), (lift, r + ring)]))
                if p.pour_style == 'FUNNEL':
                    funnel = self._funnel_size(r, wall)
                    if lift > top:
                        funnel = min(funnel, ring - 1.0)  # stays inside the tube's wall
                    profile = bottom + [(lift - funnel, r), (lift + 1, r + funnel + 1)]
                else:
                    # A small 45° chamfer at the mouth: easier to pour into, no sharp rim.
                    chamfer = min(self._hole_chamfer(r), (lift - bottom[-1][0]) * 0.5)
                    profile = bottom + [(lift - chamfer, r), (lift + 1, r + chamfer + 1)]
            holes.append((at, profile))
        tubes = []  # (mesh, center, reach) of the vents that lean and the gates
        for pt, r in self.vents:
            at = Matrix.Translation((pt.x, pt.y, 0))
            if shell:
                bosses.append((at, [(pt.z, r + ring), (surface_z(pt), r + ring)]))
            points, radii = self._vent_axis(pt, r, surface_z(pt) + 1)
            if all((q.xy - pt.xy).length < 1e-6 for q in points):
                holes.append((at, [(q.z, rr) for q, rr in zip(points, radii)]))
            else:
                tubes.append(self._tube_cutter("EasyMold_Vent", points, radii))
        # Each boolean on a big mold costs about the same however small the cutter: all the bosses first, then
        # all the holes, each in as few booleans as possible (markers placed by hand may overlap).
        self._reinforce_many(obj, bosses)
        self.boolean_many(obj, [(lathe_mesh("EasyMold_Hole", [item]), *self._lathe_reach(item)) for item in holes],
                          'DIFFERENCE')
        # Entry from below: round the elbow from the bottom of each sprue into the gate, into the side of the part.
        tubes += [self._tube_cutter("EasyMold_Gate", points, radii) for points, radii, _bend in gate_tubes.values()]
        self.boolean_many(obj, tubes, 'DIFFERENCE')
        return holes[:len(self.pours)]

    @staticmethod
    def _tube_cutter(name, points, radii):
        """(mesh, center, reach) of a tube for boolean_many."""
        center = sum(points, Vector()) / len(points)
        return tube_mesh(name, points, radii), center, max((q - center).length for q in points) + max(radii)

    @staticmethod
    def _lathe_reach(item):
        """(center, radius) of a vertical solid of revolution for boolean_many: vertical solids overlap only if
        they are closer than their radii horizontally."""
        at, profile = item
        return Vector((at.translation.x, at.translation.y, 0)), max(radius for _z, radius in profile)

    def _reinforce_many(self, obj, items):
        """Join solids of revolution (at, profile) around holes, so cutting a hole through a thin part of the
        mold (e.g. the narrow shell over a cup's rim) leaves a ring of material instead of loose slivers.

        They are trimmed by the cavity first (the silicone space of a shell, the part in a direct mold), so they
        never fill it. Bosses that don't overlap go in the same pair of booleans.
        """
        batches = []
        for item in items:
            center, radius = self._lathe_reach(item)
            for batch in batches:
                if all((center - c).length > radius + r for _i, c, r in batch):
                    batch.append((item, center, radius))
                    break
            else:
                batches.append([(item, center, radius)])
        cavity = self.offset_source(self.props.silicone_thickness) if self.props.mold_type != 'DIRECT' else self.target
        for batch in batches:
            boss = self.add_object("EasyMold_HoleBoss", lathe_mesh("Boss", [item for item, _c, _r in batch]), temp=True)
            self.boolean(boss, cavity, 'DIFFERENCE')
            if self.core_hole is not None and boss.data.vertices:
                self.boolean(boss, self.core_hole, 'DIFFERENCE')  # the raised sprue cup can't fill a core's place
            if boss.data.vertices:
                self.boolean(obj, boss, 'UNION')
            self.remove(boss)

    KEY_MARGIN = 1.0  # material left around a key socket by its lug

    def _lug_radius(self, key_radius):
        return key_radius + self.props.key_tolerance + self.KEY_MARGIN

    def _lug_half_thickness(self, key_radius):
        # Thick enough for the peg/socket depth along the split axis, at least the flange thickness.
        return max(self.props.flange_thickness, key_radius + self.props.key_tolerance + 0.6)

    def add_lugs(self, obj):
        """Disc around every key and bolt on its split plane, so they never break out of a narrow flange.

        With a wide flange the lug stays hidden inside it. Done before hollowing the shell, so a lug can
        never fill the silicone gap.
        """
        bolt = self._bolt()
        discs = [(axis, center, self._lug_radius(r), self._lug_half_thickness(r)) for axis, center, r in self.keys]
        discs += [(axis, center, bolt['tab_r'], self._bolt_lug_half()) for axis, center in self.bolts]
        items = [(lathe_mesh("EasyMold_Lug", [(_axis_matrix(axis, center), [(-half, radius), (half, radius)])]),
                  center, math.hypot(radius, half)) for axis, center, radius, half in discs]
        self.boolean_many(obj, items, 'UNION')

    def _bolt_lug_half(self):
        # Each side holds the bolt; the - side also the nut.
        return max(self.props.flange_thickness, self._bolt()['nut_h'] + 1.2)

    def cut_bolt_holes(self, obj):
        """Bolt hole through both flanges; hexagonal nut pocket on the outer face of the - side."""
        bolt = self._bolt()
        half = self._bolt_lug_half()
        holes, nuts = [], []
        for axis, center in self.bolts:
            at = _axis_matrix(axis, center)
            hole = [(-half - 1, bolt['hole_r']), (half + 1, bolt['hole_r'])]
            holes.append((lathe_mesh("EasyMold_BoltHole", [(at, hole)]), center, half + 1))
            nut = [(-half - 1, bolt['nut_r']), (-half + bolt['nut_h'], bolt['nut_r'])]
            nuts.append((lathe_mesh("EasyMold_NutTrap", [(at, nut)], segments=6), center, half + bolt['nut_r'] + 1))
        # A nut pocket overlaps its own hole, so holes and pockets are cut in separate passes.
        self.boolean_many(obj, holes, 'DIFFERENCE')
        self.boolean_many(obj, nuts, 'DIFFERENCE')

    def _place_flange_parts(self, bvh, axes):
        """Bolts along each split plane of a shell, then keys where there is room.

        Bolts go first, near the bottom and the top of each side. On small objects there may be no room
        left for a key between them: it is left out, since the bolt aligns the parts there too.
        """
        p = self.props
        s, w, fw = p.silicone_thickness, p.wall_thickness, p.flange_width
        floor, flange_top = self._floor(), self.bmax.z + s
        taken = []  # (axis, center, radius) of everything placed on the flanges

        def free(axis, c, radius):
            return all(a != axis or (c - o).length > radius + r + 0.5 for a, o, r in taken)

        if p.clamp_method == 'SCREW':
            bolt = self._bolt()
            # The nut pocket must stay out of the silicone.
            middle = max(s + w + fw / 2, s + bolt['nut_r'] + self.KEY_MARGIN)
            for axis in axes:
                for _side, c in self._flange_points(bvh, axis, (0.1, 0.92), bolt['tab_r'], middle, floor, flange_top):
                    if free(axis, c, bolt['tab_r']):
                        self.bolts.append((axis, c))
                        taken.append((axis, c, bolt['tab_r']))

        if not p.use_keys:
            return
        radius = self._key_radius(max(min(fw * 0.3, p.flange_thickness * 0.8), 1.2))
        lug = self._lug_radius(radius)
        # Mid-flange, measured as true distance to the model (not horizontally), so on sloped surfaces the
        # key doesn't end up near the flange edge; never closer to the silicone than the socket needs
        # (the shell wall counts as material).
        middle = max(s + w + fw / 2, s + radius + p.key_tolerance + self.KEY_MARGIN)
        left_out = False
        for axis in axes:
            per_side = {}
            for t in (0.3, 0.75, 0.5, 0.4, 0.6, 0.2, 0.85):
                for side, c in self._flange_points(bvh, axis, (t,), lug, middle, floor, flange_top):
                    if per_side.get(side, 0) < 2 and free(axis, c, lug):
                        self.keys.append((axis, c, radius))
                        taken.append((axis, c, lug))
                        per_side[side] = per_side.get(side, 0) + 1
            left_out |= len(per_side) < 2 or min(per_side.values()) < 2
        if not self.keys:
            self.warnings.append("No room for registration keys on the flanges")
        elif left_out and self.bolts:
            self.notes.append("fewer keys where the bolts took the room (they align the parts too)")

    def _flange_points(self, bvh, axis, heights, lug, middle, floor, flange_top):
        """(side, point) on the split plane at the given relative heights, on both sides of the object, at
        true distance `middle` from the model (so they sit mid-flange even on sloped surfaces)."""
        i = AXIS_INDEX[axis]
        j = 1 - i  # the other horizontal axis (shell splits are X or Y)
        far = self.size * 2 + 100
        span = self.props.flange_width * 4 + lug
        points = []
        for t in heights:
            z = floor + (self.bmax.z - floor) * t
            # Keep the whole lug inside the flange's height.
            z = max(floor + lug + 0.5, min(z, flange_top - lug - 0.5))
            for side in (-1, 1):
                origin = self.center.copy()
                origin.z = z
                origin[j] += side * far
                direction = Vector((0, 0, 0))
                direction[j] = -side
                hit, _n, _i, _d = bvh.ray_cast(origin, direction)
                if hit is None:
                    continue
                near, far_t = -(lug + span), span
                for _ in range(40):
                    mid = (near + far_t) / 2
                    if self.bvh.find_nearest(hit + direction * mid)[3] > middle:
                        near = mid
                    else:
                        far_t = mid
                points.append((side, hit + direction * far_t))
        return points

    # --- silicone anchors ------------------------------------------------

    def anchor_spheres(self, axes):
        """Spheres centered on the shell's inner surface: cut as dimples in the shell, filled by silicone."""
        p = self.props
        s, w = p.silicone_thickness, p.wall_thickness
        radius = max(0.8, min(w - 0.8, 3.0))
        spacing = max(radius * 8, self.size / 4)
        floor = self._floor()
        surface = self.offset_source(s).data
        avoid = [pt for pt, _r in self.pours + self.vents]
        clearance = max([r for _pt, r in self.pours + self.vents] + [0]) + radius + 2
        step = max(1, len(surface.vertices) // 20000)
        spheres = []
        for index in range(0, len(surface.vertices), step):
            v = surface.vertices[index].co
            if v.z < floor + radius * 2 + self.props.lip_height or v.z > self.bmax.z + s - radius * 2:
                continue
            if any(abs(v[AXIS_INDEX[a]] - self.center[AXIS_INDEX[a]]) < radius + 1.5 for a in axes):
                continue
            if any((v.xy - q.xy).length < clearance for q in avoid):
                continue
            if all((v - c).length > spacing for c, _r in spheres):
                spheres.append((v.copy(), radius))
        return spheres

    # --- clamping tabs (bolt the walls/shell down to the base) -----------

    def _bolt(self):
        """Dimensions derived from the bolt diameter (metric nut: across flats ~1.8 d, height ~0.8 d)."""
        d = self.props.bolt_diameter
        hole_r = d / 2 + 0.2
        nut_r = (1.8 * d + 0.3) / math.sqrt(3)  # hexagon circumradius
        nut_h = 0.8 * d + 0.3
        return {
            'hole_r': hole_r,
            'nut_r': nut_r,
            'nut_h': nut_h,
            'tab_r': nut_r + 1.6,              # material around the nut
            'offset': nut_r + 1.0,             # hole center distance from the wall surface
            'foot': nut_h + max(1.6, d * 0.6),  # tab height on the walls/shell
        }

    def _clamp_points(self, outline_obj, z, axes, offset, radius):
        """(surface point, outward direction) around the outside of `outline_obj` at height z, at least one
        per mold part. A tab is centered `offset` out from the surface and must stay `radius` away from
        every split plane, so it belongs to a single part.
        """
        bvh = self._bvh(outline_obj)
        far = self.size * 2 + 100

        def spot(angle):
            d = Vector((math.cos(math.radians(angle)), math.sin(math.radians(angle)), 0))
            hit = bvh.ray_cast(Vector((self.center.x, self.center.y, z)) + d * far, -d)[0]
            if hit is None:
                return None
            center = hit + d * offset
            if any(abs(center[AXIS_INDEX[ax]] - self.center[AXIS_INDEX[ax]]) < radius + 0.5 for ax in axes):
                return None
            return hit, d

        points = []
        for quarter in (0, 90, 180, 270):
            # Two per quarter with 4 parts (one per part per side), else the diagonal (box corners).
            tries = ([(quarter + 22.5, quarter + 67.5), (quarter + 45,)] if len(axes) == 2
                     else [(quarter + 45,)])
            for angles in tries:
                found = [s for s in (spot(a) for a in angles) if s is not None]
                if found:
                    points += found
                    break
        if not points:
            self.warnings.append("Could not place the clamping tabs")
        return points

    def plan_base_fasteners(self, outline_obj, axes, seat, base_bottom):
        """Where and how the walls/shell are fastened to the base (None: not fastened).

        Screws: round tabs with a bolt hole (nut trap on the upper tab). Clips: a flange all around the foot
        of the walls/shell, on a base of the same outline, together as thin as the clip opening. Bands are
        handled separately (box only).
        """
        p = self.props
        plan = {'seat': seat, 'bottom': base_bottom}
        if p.clamp_method == 'SCREW':
            bolt = self._bolt()
            plan['foot'] = bolt['foot']
            spots = self._clamp_points(outline_obj, seat + bolt['foot'] / 2, axes, bolt['offset'], bolt['tab_r'])
            plan['points'] = [hit + d * bolt['offset'] for hit, d in spots]
        elif p.clamp_method == 'CLIP':
            base_part = seat - base_bottom
            room = self._clip()['opening'] - base_part - 0.3
            if room < 1.6:
                self.warnings.append(f"Base + flange ({base_part + 1.6:.1f} mm) are thicker than a {p.clip_size:g} mm "
                                     "clip opens: use bigger clips or a thinner base")
            plan['foot'] = max(1.6, min(p.flange_thickness, room))
        else:
            return None
        plan['method'] = p.clamp_method
        return plan

    def add_upper_fasteners(self, upper, plan):
        """Tabs or flange at the foot of the walls/shell; before hollowing, so they can't reach into the
        silicone."""
        seat, top = plan['seat'], plan['seat'] + plan['foot']
        if plan['method'] == 'SCREW':
            self.add_clamp_tabs(upper, plan['points'], seat, top)
        else:
            flange = self.add_object("EasyMold_FootFlange", self._foot_flange_mesh(seat, top), temp=True)
            self.boolean(upper, flange, 'UNION')
            self.remove(flange)

    def add_base_side_fasteners(self, base, plan):
        """Screw tabs on the base (with clips, the base already has the flange's outline)."""
        seat, bottom = plan['seat'], plan['bottom']
        if plan['method'] == 'SCREW':
            self.add_clamp_tabs(base, plan['points'], bottom, seat)
            self.cut_clamp_holes(base, plan['points'], bottom, seat, nut_trap=False)

    def finish_upper_fasteners(self, upper, plan):
        """Holes and nut traps in the upper tabs (after hollowing)."""
        if plan['method'] == 'SCREW':
            self.cut_clamp_holes(upper, plan['points'], plan['seat'], plan['seat'] + plan['foot'], nut_trap=True)

    # --- clips, block bolts and band grooves ------------------------------

    def _clip(self):
        """Binder clip proportions from its width: how far the jaws open, how thick each flange half can be."""
        opening = self.props.clip_size * 0.3
        return {
            'opening': opening,
            'thickness': max(1.6, min(opening / 2 - 0.3, 3.0)),
        }

    def _clip_flange_width(self):
        return max(self.props.flange_width, 4.0)

    def _foot_flange_mesh(self, z0, z1):
        """Flange around the foot of the walls (box: a ring like the walls) or the shell (follows its shape,
        like the flanges on the splits), that a binder clip grips together with the base."""
        p = self.props
        grow = p.wall_thickness + self._clip_flange_width()
        if p.mold_type in ('SILICONE_BOX', 'SILICONE_TWO_PART'):
            return prism_mesh("Flange", self._box_ring(grow), z0, z1)
        flange = self.add_object("EasyMold_FootFlange", self.offset_source(p.silicone_thickness + grow).data.copy(),
                                 temp=True)
        self.boolean_temp(flange, self.slab_mesh('Z', (z0 + z1) / 2, (z1 - z0) / 2), 'INTERSECT')
        mesh = flange.data.copy()
        self.remove(flange)
        return mesh

    def add_block_flanges(self, block, axes, lo, hi):
        """Flanges around each split plane of a block, thin enough for a binder clip to grip both halves at
        once. Not over the top and bottom faces (pour hole; the mold must stand). Before cutting the cavity,
        so they can't fill it."""
        p = self.props
        half = self._clip()['thickness']
        fw = self._clip_flange_width()
        for axis in axes:
            i = AXIS_INDEX[axis]
            grow = Vector((fw, fw, 0.0))
            grow[i] = 0.0
            flange = self.add_object("EasyMold_Flange", box_mesh("Flange", lo - grow, hi + grow, bevel=min(fw * 0.4, 2.0)),
                                     temp=True)
            self.boolean_temp(flange, self.slab_mesh(axis, self.center[i], half), 'INTERSECT')
            self.boolean(block, flange, 'UNION')
            self.remove(flange)

    def _block_edge_points(self, axes, lo, hi):
        """Bolt spots on each split plane of a block: (axis, point in the middle of the margin).

        One per edge of the plane; along an edge crossed by another split plane, two (at 1/4 and 3/4) so
        none sits on that plane. Never on the top face (pour hole and vents).
        """
        m = self.props.margin
        spots = []
        for axis in axes:
            i = AXIS_INDEX[axis]
            for n in (n for n in range(3) if n != i):
                other = next(k for k in range(3) if k not in (i, n))
                crossed = any(AXIS_INDEX[a] == other for a in axes)
                along = (0.25, 0.75) if crossed else (0.5,)
                for side, face in ((-1, lo[n]), (1, hi[n])):
                    if n == 2 and side > 0:
                        continue
                    for t in along:
                        inner = self.center.copy()
                        inner[n] = face - side * m / 2
                        inner[other] = lo[other] + (hi[other] - lo[other]) * t
                        inner[i] = self._plane_value(axis, inner)
                        spots.append((axis, inner))
        return spots

    def cut_block_bolts(self, obj):
        """Bolts across the split plane of a block: a through hole with a head pocket on the + side and a
        nut pocket on the - side, leaving `grip` of material each side so a short bolt is enough."""
        p = self.props
        bolt = self._bolt()
        lo, hi = self._block_bounds()
        grip = max(bolt['nut_h'] + 2.0, 4.0)
        if p.margin < 2 * bolt['nut_r'] + 2:
            self.warnings.append("The block margin is thin for these bolts: increase the margin or use smaller bolts")
        holes, pockets = [], []
        for axis, c in self.bolts:
            i = AXIS_INDEX[axis]
            at = _axis_matrix(axis, c)
            near, far = lo[i] - c[i] - 1, hi[i] - c[i] + 1
            holes.append((lathe_mesh("EasyMold_BoltHole", [(at, [(near, bolt['hole_r']), (far, bolt['hole_r'])])]),
                          c, max(-near, far)))
            pockets.append((lathe_mesh("EasyMold_BoltHead", [(at, [(grip, bolt['nut_r']), (far, bolt['nut_r'])])]),
                            c, max(-near, far)))
            pockets.append((lathe_mesh("EasyMold_NutTrap", [(at, [(near, bolt['nut_r']), (-grip, bolt['nut_r'])])],
                                       segments=6), c, max(-near, far)))
        self.boolean_many(obj, holes, 'DIFFERENCE')
        self.boolean_many(obj, pockets, 'DIFFERENCE')
        if self.bolts:
            self.notes.append(f"use M{p.bolt_diameter:g} bolts of about {2 * grip + bolt['nut_h']:.0f} mm")

    def band_grooves(self, z_ranges, outline, inner, depth):
        """Cutters for rubber band grooves running up the sides of a box, over the top rim and under the base.

        outline: the box's outer outline; inner: the same outline `depth` smaller; z_ranges: (bottom, top) of
        the whole assembly. Two loops cross at the center (one along X, one along Y).
        """
        p = self.props
        width = p.band_width + 1.0
        z0, z1 = z_ranges
        cutters = []
        for axis in ('X', 'Y'):
            i = AXIS_INDEX[axis]
            ring = self.add_object("EasyMold_BandRing", prism_mesh("Ring", outline, z0 - 1, z1 + 1), temp=True)
            self.boolean_temp(ring, prism_mesh("RingHole", inner, z0 - 2, z1 + 2), 'DIFFERENCE')
            self.boolean_temp(ring, self.slab_mesh(axis, self.center[i], width / 2), 'INTERSECT')
            cutters.append(ring)
            # Over the top rim and under the base, across the whole box.
            far = self.size * 10 + 100
            for zlo, zhi in ((z1 - depth, z1 + 1), (z0 - 1, z0 + depth)):
                lo = self.center - Vector((far, far, 0))
                hi = self.center + Vector((far, far, 0))
                lo[i], hi[i] = self.center[i] - width / 2, self.center[i] + width / 2
                lo.z, hi.z = zlo, zhi
                cutters.append(self.add_object("EasyMold_BandSlot", box_mesh("Slot", lo, hi), temp=True))
        return cutters

    def block_band_grooves(self, block, axes, lo, hi, depth):
        """Rubber band grooves around a block: rings that cross every split plane."""
        width = self.props.band_width + 1.0
        rings = set()
        for axis in axes:
            normal = 'Z' if axis != 'Z' else 'X'
            n = AXIS_INDEX[normal]
            for t in (0.25, 0.75):
                rings.add((normal, round(lo[n] + (hi[n] - lo[n]) * t, 4)))
        for normal, position in sorted(rings):
            ring = self.add_object("EasyMold_BandRing", box_mesh("Ring", lo - Vector((1, 1, 1)), hi + Vector((1, 1, 1))),
                                   temp=True)
            self.boolean_temp(ring, box_mesh("RingHole", lo + Vector((depth,) * 3), hi - Vector((depth,) * 3)),
                              'DIFFERENCE')
            self.boolean_temp(ring, self.slab_mesh(normal, position, width / 2), 'INTERSECT')
            self.boolean(block, ring, 'DIFFERENCE')
            self.remove(ring)

    def add_clamp_tabs(self, obj, points, z0, z1):
        bolt = self._bolt()
        # Tabs are spread around the outline and never overlap: one boolean for all of them.
        items = [(lathe_mesh("EasyMold_Tab", [(Matrix.Translation((pt.x, pt.y, 0)),
                                               [(z0, bolt['tab_r']), (z1, bolt['tab_r'])])]),
                  pt, bolt['tab_r'] + (z1 - z0)) for pt in points]
        self.boolean_many(obj, items, 'UNION')

    def cut_clamp_holes(self, obj, points, z0, z1, nut_trap):
        """Bolt holes through the tabs; on the upper tab a hexagonal pocket holds the nut."""
        bolt = self._bolt()
        holes, nuts = [], []
        for pt in points:
            at = Matrix.Translation((pt.x, pt.y, 0))
            holes.append((lathe_mesh("EasyMold_BoltHole", [(at, [(z0 - 1, bolt['hole_r']), (z1 + 1, bolt['hole_r'])])]),
                          pt, bolt['hole_r'] + (z1 - z0) + 2))
            if nut_trap:
                nut = [(z1 - bolt['nut_h'], bolt['nut_r']), (z1 + 1, bolt['nut_r'])]
                nuts.append((lathe_mesh("EasyMold_NutTrap", [(at, nut)], segments=6), pt, bolt['nut_r'] + bolt['nut_h'] + 1))
        self.boolean_many(obj, holes, 'DIFFERENCE')
        self.boolean_many(obj, nuts, 'DIFFERENCE')

    def _block_keys(self, axes, lo, hi):
        """Keys at the corners of each split plane, in the middle of the margin (never in the cavity)."""
        margin = self.props.margin
        radius = self._key_radius(margin * 0.3)
        keys = []
        for axis in axes:
            i = AXIS_INDEX[axis]
            j, k = [n for n in range(3) if n != i]
            for sj in (lo[j] + margin / 2, hi[j] - margin / 2):
                for sk in (lo[k] + margin / 2, hi[k] - margin / 2):
                    c = self.center.copy()
                    c[j], c[k] = sj, sk
                    c[i] = self._plane_value(axis, c)
                    keys.append((axis, c, radius))
        return keys

    def _place_spec(self, axes):
        if self.layout is not None:
            self.spec_from_layout(axes)
        else:
            self.auto_spec(axes)

    # --- mold types ------------------------------------------------------

    def build_silicone_box(self, axes):
        p = self.props
        s = p.silicone_thickness
        floor = self._floor()
        lip_bottom = floor - p.lip_height
        top = self.bmax.z + s

        yield 0.2, "Building walls and base"
        inner, outer, lip = self._box_outlines()
        walls = self.add_object(f"{self.name}_Walls", prism_mesh("Walls", outer, lip_bottom, top), style='PART_A')
        # Base: plate with the same outline as the walls (with clips, as their foot flange) + lip that fits
        # inside them (+ master).
        base_bottom = lip_bottom - p.base_thickness
        plate = self._box_ring(p.wall_thickness + self._clip_flange_width()) if p.clamp_method == 'CLIP' else outer
        base = self.add_object(f"{self.name}_Base", prism_mesh("Base", plate, base_bottom, lip_bottom), style='BASE')
        self.boolean_temp(base, prism_mesh("EasyMold_Lip", lip, lip_bottom - p.base_thickness / 2, floor), 'UNION')

        # Fastening tabs at the corners, added before hollowing so they can't reach into the silicone.
        yield 0.4, "Adding clamping"
        plan = self.plan_base_fasteners(walls, axes, lip_bottom, base_bottom)
        if plan is not None:
            self.add_upper_fasteners(walls, plan)
            self.add_base_side_fasteners(base, plan)
        self.boolean_temp(walls, prism_mesh("EasyMold_Hole", inner, lip_bottom - 1, top + 1), 'DIFFERENCE')
        if plan is not None:
            self.finish_upper_fasteners(walls, plan)
        if p.clamp_method == 'BAND':
            depth = min(1.5, p.wall_thickness * 0.4)
            for cutter in self.band_grooves((base_bottom, top), outer, self._box_ring(p.wall_thickness - depth), depth):
                self.boolean(walls, cutter, 'DIFFERENCE')
                self.boolean(base, cutter, 'DIFFERENCE')
                self.remove(cutter)
        if p.include_master:
            self.boolean(base, self.target, 'UNION')

        yield 0.75, "Silicone preview"
        silicone = self.add_object(f"{self.name}_Silicone (preview)", prism_mesh("Silicone", inner, floor, top),
                                   style='SILICONE')
        self.boolean(silicone, self.master_solid(), 'DIFFERENCE')
        self.silicone_volume = mesh_volume(silicone.data)

        if p.explode:
            walls.location.z += (top - lip_bottom) + self.size * 0.1

    def _box_outlines(self):
        p = self.props
        return self._box_ring(0.0), self._box_ring(p.wall_thickness), self._box_ring(-p.key_tolerance)

    def _box_ring(self, distance):
        """Outline of the box at `distance` outside the silicone (0 = inner face of the walls)."""
        s = self.props.silicone_thickness
        in_lo = Vector((self.bmin.x - s, self.bmin.y - s))
        in_hi = Vector((self.bmax.x + s, self.bmax.y + s))
        radius = min(s, 0.25 * min(in_hi - in_lo))
        grow = Vector((distance, distance))
        return rounded_rect(in_lo - grow, in_hi + grow, radius + distance)

    # --- 2-part silicone mold --------------------------------------------

    def _parting_z(self):
        """Height of the parting line: the layout's Parting Line plane, the widest section, or a % of the height."""
        p = self.props
        height = self.bmax.z - self.bmin.z
        planes = [o for o in self.layout.objects if o.get(ROLE) == 'PARTING'] if self.layout else []
        if planes:
            z = planes[0].matrix_world.translation.z
            if not self.bmin.z + height * 0.02 < z < self.bmax.z - height * 0.02:
                raise MoldError("Move the Parting Line so it crosses the master")
        elif self.tray_parting is not None:
            z = self.tray_parting  # the objects of the tray were lined up on it
        elif p.parting_auto:
            z = self._widest_section()
        else:
            z = self.bmin.z + height * p.parting_height / 100
        # Never exactly on a vertex height (a flat face there would be coplanar with the board's top).
        co, _normals, _edges = analysis.mesh_arrays(self.target.data)
        while np.any(np.abs(co[:, 2] - z) < 1e-4):
            z += 1e-3
        return z

    def _widest_section(self):
        """Height (5..95 % of the master) where its horizontal section is largest (convex hull area). The
        silicone halves come off it upward and downward, so that is where they release best. On a plateau of
        equal sections (a cylinder), the level nearest the middle."""
        co, _normals, edges = analysis.mesh_arrays(self.target.data)
        za, zb = co[edges[:, 0], 2], co[edges[:, 1], 2]
        height = self.bmax.z - self.bmin.z
        # The odd offset keeps the levels off vertex rings (a ring right on a level gives no crossing edges).
        levels = np.linspace(self.bmin.z + height * 0.05, self.bmax.z - height * 0.05, 61) + height * 1.37e-4
        areas = []
        for z in levels:
            crossing = (za - z) * (zb - z) < 0
            a, b = co[edges[crossing, 0]], co[edges[crossing, 1]]
            t = ((z - a[:, 2]) / (b[:, 2] - a[:, 2]))[:, None]
            points = (a + (b - a) * t)[:, :2]
            if len(points) < 3:
                areas.append(0.0)
                continue
            hull = [tuple(points[i]) for i in convex_hull_2d([tuple(q) for q in points])]
            areas.append(_polygon_area(hull))
        areas = np.array(areas)
        near_best = np.nonzero(areas >= areas.max() * 0.999)[0]
        return float(levels[min(near_best, key=lambda i: abs(levels[i] - self.center.z))])

    def _frame_range(self):
        """(bottom, top) of the frame: the silicone block (master + silicone thickness) with a lip of room at
        each end, so neither pour spills over."""
        p = self.props
        s = p.silicone_thickness
        return self.bmin.z - s - p.lip_height, self.bmax.z + s + p.lip_height

    def _parting_keys(self):
        """Keys on the parting line, in the silicone between the master and the frame, as far apart as
        possible (the board gets dimples, the first half fills them as bumps, the second half wraps them)."""
        p = self.props
        s, tol = p.silicone_thickness, p.key_tolerance
        radius = self._key_radius(min(max(s * 0.3, 1.5), 5.0))
        z = self.parting_z
        lo = Vector((self.bmin.x - s, self.bmin.y - s))
        hi = Vector((self.bmax.x + s, self.bmax.y + s))
        corner = min(s, 0.25 * min(hi - lo))  # as in _box_ring
        half, mid = (hi - lo) / 2, (hi + lo) / 2
        edge = radius + 1.0 + tol      # from the frame
        model = radius + 1.5           # from the master
        step = max(min(hi - lo) / 40, 0.5)
        candidates = []
        for x in np.arange(lo.x + edge, hi.x - edge + 1e-6, step):
            for y in np.arange(lo.y + edge, hi.y - edge + 1e-6, step):
                q = Vector((abs(x - mid.x), abs(y - mid.y))) - (half - Vector((corner, corner)))
                outside = Vector((max(q.x, 0), max(q.y, 0))).length + min(max(q.x, q.y), 0) - corner
                if outside > -edge:
                    continue  # too close to the (rounded) frame
                c = Vector((x, y, z))
                c.z = self._plane_value('Z', c)  # on a curved parting surface
                loc, normal, _i, dist = self.bvh.find_nearest(c)
                if loc is None or dist < model or (c - loc).dot(normal) < 0:
                    continue  # too close to the master, or inside it
                candidates.append(c)
        if not candidates:
            self.warnings.append("No room for registration keys on the parting line: use a thicker silicone")
            return []
        center = Vector((mid.x, mid.y, z))
        chosen = [max(candidates, key=lambda c: (c - center).length)]
        while len(chosen) < 4:
            far = max(candidates, key=lambda c: min((c - q).length for q in chosen))
            if min((far - q).length for q in chosen) < radius * 4:
                break
            chosen.append(far)
        if len(chosen) < 3:
            self.notes.append(f"only {len(chosen)} registration key(s) fit on the parting line")
        return [('Z', c, radius) for c in chosen]

    def _sprue_profile(self, pt, r):
        """Profile (z, radius) of the vertical sprue rod from the top of the master up through the first half
        to the top of the frame; with the funnel style it flares at the top, so the channel left in the
        silicone is easy to pour into."""
        p = self.props
        s = p.silicone_thickness
        top = self._frame_range()[1]
        depth = min(p.sprue_height, s + p.lip_height - 1.0) if p.pour_style == 'FUNNEL' else 0.0
        bottom = self._tip_profile(pt.z, r, self._tip(r, p.pour_tip), max(top - pt.z - 1.0, 0.3) * 0.6)
        depth = min(depth, top - bottom[-1][0] - 0.5)  # the funnel makes room for the tip's cone
        if depth < 0.5:
            chamfer = min(self._hole_chamfer(r), (top - bottom[-1][0]) * 0.5)
            return bottom + [(top - chamfer, r), (top, r + chamfer)]
        # Never wider than the room to the frame.
        room = min(pt.x - (self.bmin.x - s), self.bmax.x + s - pt.x, pt.y - (self.bmin.y - s), self.bmax.y + s - pt.y)
        mouth = max(r, min(r + depth * 0.6, room - 1.0))
        return bottom + [(top - depth, r), (top, mouth)]

    def _board_lock(self, board):
        """Share of the master's surface below the parting line that faces up with the board right over it:
        the rigid printed board holds it down, so the master can't be lifted out."""
        centers, normals = analysis.face_arrays(self.target.data)
        areas = np.empty(len(centers))
        self.target.data.polygons.foreach_get("area", areas)
        below = centers[:, 2] < self._plane_values(2, centers) - max(self.size * 2e-3, 0.05)
        facing_up = np.nonzero(below & (normals[:, 2] > 0.05))[0]
        facing_up = facing_up[::max(1, len(facing_up) // 20000)]  # a sample is enough on big meshes
        bvh = self._bvh(board)
        step = self.props.key_tolerance + 0.5  # just past the clearance, into the board if it's there
        locked = 0.0
        for i in facing_up:
            q = Vector(centers[i] + normals[i] * step)
            loc, normal, _i, _d = bvh.find_nearest(q)
            if loc is not None and (q - loc).dot(normal) < 0:
                locked += areas[i]
        sampled = areas[facing_up].sum() / max(areas[below & (normals[:, 2] > 0.05)].sum(), 1e-9)
        return float(locked / max(sampled, 1e-9) / max(areas[below].sum(), 1e-9))

    def build_two_part(self, axes):
        p = self.props
        s, w, tol = p.silicone_thickness, p.wall_thickness, p.key_tolerance
        z = self.parting_z
        bottom, top = self._frame_range()
        block_bottom, block_top = self.bmin.z - s, self.bmax.z + s

        self._place_spec(axes)
        yield 0.15, "Building the sprue and vents"
        rods = [(Matrix.Translation((pt.x, pt.y, 0)), self._sprue_profile(pt, r)) for pt, r in self.pours]
        rods += [(Matrix.Translation((pt.x, pt.y, 0)), [(q.z, rr) for q, rr in zip(*self._vent_axis(pt, r, top))])
                 for pt, r in self.vents]
        sprue = self.add_object("EasyMold_Sprue", lathe_mesh("Sprue", rods), temp=True) if rods else None
        if p.include_master:
            # The master printed with its sprue and vents attached (from a closed surface if it is open).
            master = self.add_object(f"{self.name}_Master_Sprue", self.master_solid().data.copy(), style='MASTER')
            if sprue is not None:
                self.boolean(master, sprue, 'UNION')
        elif sprue is not None:
            # Only the rods, shaped to sit on the surface: glue them onto the original.
            rod_piece = self.add_object(f"{self.name}_Sprue", sprue.data.copy(), style='CORE')
            self.boolean(rod_piece, self.target, 'DIFFERENCE')

        yield 0.25, "Building the frame"
        inner, outer = self._box_ring(0.0), self._box_ring(w)
        frame = self.add_object(f"{self.name}_Frame", prism_mesh("Frame", outer, bottom, top), style='PART_A')
        # Parting board: a plate under the frame (with clips, as wide as its foot flange) and a block that
        # fits inside it up to the parting line, with the lower half of the master sunk into it.
        base_bottom = bottom - p.base_thickness
        plate = self._box_ring(w + self._clip_flange_width()) if p.clamp_method == 'CLIP' else outer
        board = self.add_object(f"{self.name}_Board", prism_mesh("Board", plate, base_bottom, bottom), style='BASE')
        if self.parting is None:
            self.boolean_temp(board, prism_mesh("EasyMold_BoardBlock", self._box_ring(-tol),
                                                bottom - p.base_thickness / 2, z), 'UNION')
        else:
            # Its top follows the curved parting surface.
            block = self.add_object("EasyMold_BoardBlock", prism_mesh(
                "BoardBlock", self._box_ring(-tol), bottom - p.base_thickness / 2, top), temp=True)
            self.boolean_temp(block, self.parting_solid(-1), 'INTERSECT')
            self.boolean(board, block, 'UNION')
            self.remove(block)

        yield 0.4, "Adding clamping"
        plan = self.plan_base_fasteners(frame, axes, bottom, base_bottom)
        if plan is not None:
            self.add_upper_fasteners(frame, plan)
            self.add_base_side_fasteners(board, plan)
        self.boolean_temp(frame, prism_mesh("EasyMold_Hole", inner, bottom - 1, top + 1), 'DIFFERENCE')
        if plan is not None:
            self.finish_upper_fasteners(frame, plan)
        if p.clamp_method == 'BAND':
            depth = min(1.5, w * 0.4)
            for cutter in self.band_grooves((base_bottom, top), outer, self._box_ring(w - depth), depth):
                self.boolean(frame, cutter, 'DIFFERENCE')
                self.boolean(board, cutter, 'DIFFERENCE')
                self.remove(cutter)

        yield 0.55, "Sinking the master into the board"
        # A little clearance, so the printed master drops in and lifts out (a closed offset surface).
        self.boolean(board, self.offset_source(tol) if tol > 0 else self.master_solid(), 'DIFFERENCE')
        if sprue is not None:
            self.boolean(board, sprue, 'DIFFERENCE')  # vents of traps below the parting line
        # Hollows of the master that open upward (inside a cup) are cut off from the rest of the board: they
        # can't be part of it (the master couldn't be lifted off), the first half of silicone fills them.
        pockets, _volumes = self.take_islands(board, keep=1.0)
        if pockets is not None:
            bpy.data.meshes.remove(pockets)
            self.notes.append("the top half fills the hollow of the master below the parting line")
        if self.keys:
            self.boolean_many(board, [(key_mesh("EasyMold_Dimple", 'Z', c, r, p.key_shape), c, r * 1.5)
                                      for _axis, c, r in self.keys], 'DIFFERENCE')
        lock = self._board_lock(board)
        if lock > 0.01:
            self.warnings.append(f"{lock:.0%} of the master below the parting line faces up: it will be locked in "
                                 "the printed board. Move the Parting Line (Edit Layout)")

        yield 0.7, "Silicone preview"
        upper = self.add_object(f"{self.name}_Silicone_Top (preview)",
                                prism_mesh("Silicone", inner, block_bottom, block_top), style='SILICONE')
        self.boolean(upper, self.master_solid(), 'DIFFERENCE')
        if sprue is not None:
            self.boolean(upper, sprue, 'DIFFERENCE')
        lower = self.add_object(f"{self.name}_Silicone_Bottom (preview)", upper.data.copy(), style='SILICONE')
        if self.parting is None:
            self.boolean_temp(upper, self.halfspace_mesh('Z', z, +1), 'INTERSECT')
            self.boolean_temp(lower, self.halfspace_mesh('Z', z, -1), 'INTERSECT')
        else:
            self.boolean_temp(upper, self.parting_solid(+1), 'INTERSECT')
            self.boolean_temp(lower, self.parting_solid(-1), 'INTERSECT')
        pockets, _volumes = self.take_islands(lower, keep=1.0, name="Pockets")
        if pockets is not None:
            self.boolean_temp(upper, pockets, 'UNION')  # as on the board: filled by the top half
        if self.keys:
            # The first half fills the board's dimples; the second one is poured around those bumps.
            for obj, operation in ((upper, 'UNION'), (lower, 'DIFFERENCE')):
                self.boolean_many(obj, [(key_mesh("EasyMold_Key", 'Z', c, r, p.key_shape), c, r * 1.5)
                                        for _axis, c, r in self.keys], operation)
        self.silicone_volume = mesh_volume(upper.data) + mesh_volume(lower.data)
        if sprue is not None:
            self.remove(sprue)
        self.notes.append("pour the top half, flip the frame, remove the board, brush release agent on the "
                          "silicone and pour the other half")

        if p.explode:
            frame.location.z += (top - bottom) + self.size * 0.1

    def build_silicone_shell(self, axes):
        p = self.props
        s, w, tol = p.silicone_thickness, p.wall_thickness, p.key_tolerance
        floor = self._floor()
        lip_bottom = floor - p.lip_height
        use_flange = bool(axes) and p.flange_width > 0

        yield 0.08, "Computing silicone surface"
        inner = self.offset_source(s)
        yield 0.18, "Computing shell surface"
        shell = self.offset(f"{self.name}_Shell", s + w)

        if use_flange:
            fw, ft = p.flange_width, p.flange_thickness
            yield 0.28, "Building flanges"
            flange_outer = self.offset_source(s + w + fw)
            for axis in axes:
                flange = self.add_object("EasyMold_FlangeBody", flange_outer.data.copy(), temp=True)
                i = AXIS_INDEX[axis]
                self.boolean_temp(flange, self.slab_mesh(axis, self.center[i], ft), 'INTERSECT')
                # Flange only on the sides, not over the top where the pour hole goes.
                self.boolean_temp(flange, self.halfspace_mesh('Z', floor, -1), 'DIFFERENCE')
                self.boolean_temp(flange, self.halfspace_mesh('Z', self.bmax.z + s, +1), 'DIFFERENCE')
                self.boolean(shell, flange, 'UNION')
                self.remove(flange)

        self._place_spec(axes)
        if self.keys or self.bolts:
            yield 0.35, "Adding key and bolt lugs"
            self.add_lugs(shell)
        if p.clamp_method == 'CLIP' and axes:
            # The clips grip the flanges of the splits.
            if not use_flange:
                self.warnings.append("Clips grip the flanges: set a Flange Width")
            elif 2 * p.flange_thickness > self._clip()['opening'] - 0.3:
                self.warnings.append(f"The flanges ({2 * p.flange_thickness:g} mm together) are thicker than a "
                                     f"{p.clip_size:g} mm clip opens: use bigger clips or thinner flanges")

        # Fastening tabs at the foot of the shell, before hollowing so they can't reach into the silicone.
        plan = self.plan_base_fasteners(self.offset_source(s + w), axes, floor, lip_bottom - p.base_thickness)
        if plan is not None:
            yield 0.37, "Adding clamping to the base"
            self.add_upper_fasteners(shell, plan)

        yield 0.4, "Hollowing the shell"
        # Hollow it once at the end: subtracting the same inner surface twice leaves coincident faces.
        self.boolean(shell, inner, 'DIFFERENCE')
        # Bottom of the shell goes down into the groove of the base.
        self.boolean_temp(shell, self.halfspace_mesh('Z', lip_bottom, -1), 'DIFFERENCE')
        footprint = [v.co.xy.copy() for v in shell.data.vertices]

        if self.pours or self.vents:
            yield 0.5, "Cutting pour hole and vents"
            self.cut_pours(shell)
        if plan is not None:
            self.finish_upper_fasteners(shell, plan)
        if self.bolts:
            yield 0.53, "Cutting bolt holes"
            self.cut_bolt_holes(shell)

        anchors = self.anchor_spheres(axes) if p.silicone_anchors else []
        if anchors:
            yield 0.55, "Adding silicone anchors"
            # Spheres are spaced apart, so one boolean cuts them all.
            self.boolean_temp(shell, spheres_mesh("EasyMold_Anchors", anchors), 'DIFFERENCE')

        yield 0.58, "Silicone preview"
        silicone = self.add_object(f"{self.name}_Silicone (preview)", inner.data.copy(), style='SILICONE')
        if anchors:
            self.boolean_temp(silicone, spheres_mesh("EasyMold_Anchors", anchors), 'UNION')
        self.boolean_temp(silicone, self.halfspace_mesh('Z', floor, -1), 'DIFFERENCE')
        self.boolean(silicone, self.master_solid(), 'DIFFERENCE')
        self.silicone_volume = mesh_volume(silicone.data)

        yield 0.66, "Building base"
        # Base plate: follows the shell's outline (rounded convex hull) with a small brim,
        # and has a groove that the shell bottom sits in.
        base = self.add_object(f"{self.name}_Base",
                               prism_mesh("Base", rounded_hull(footprint, w), lip_bottom - p.base_thickness, floor),
                               style='BASE')
        # Fastening tabs first: cutting the groove afterwards keeps it continuous under the tabs.
        if plan is not None:
            self.add_base_side_fasteners(base, plan)
        groove = self.offset("EasyMold_Groove", s + w + tol, temp=True)
        self.boolean(groove, self.offset_source(s - tol), 'DIFFERENCE')
        self.boolean_temp(groove, self.slab_mesh('Z', floor, floor - lip_bottom + 1e-3), 'INTERSECT')
        self.boolean(base, groove, 'DIFFERENCE')
        if p.include_master:
            self.boolean(base, self.target, 'UNION')

        pieces = [(shell, {})]
        for n, axis in enumerate(axes):
            yield 0.78 + 0.06 * n, f"Splitting along {axis}"
            pieces = self.split(pieces, axis)
        if self.keys:
            yield 0.9, "Adding registration keys"
            self.add_keys(pieces)
        self.finish_pieces(pieces, f"{self.name}_Shell")
        if p.explode:
            self.explode(pieces)

    # --- automatic cores of a direct mold ------------------------------------

    # A hollow gets a core if its opening is at most this share of its surface (a shallow dish, 0.36, does; the
    # space around a handle or between legs, ~0.5, doesn't)...
    CORE_MOUTH_SHARE = 0.45
    # ...and at least this share of it can't come out with the block parts (sock split across: 0.36, the lower part
    # would be a loose piece; dish 1.0; around a bear 0.18, a handle 0.01).
    CORE_TRAPPED = 0.3

    def _wants_auto_core(self):
        p = self.props
        return p.mold_type == 'DIRECT' and p.auto_core and p.core_object is None

    def _core_grow(self):
        """How far a core reaches into the part's walls before the part is subtracted from it again: no face of
        the core lies right on the part (coincident faces break booleans)."""
        return max(self.props.voxel_size, 0.3)

    def _tray_vertex_groups(self):
        """Vertices of each object of a tray (all of them for a single object), as arrays."""
        mesh = self.target.data
        co = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        if not self.tray:
            return [co]
        groups = []
        for part in self.tray:
            lo = np.array(part['bmin']) - 1e-3
            hi = np.array(part['bmax']) + 1e-3
            groups.append(co[np.all((co >= lo) & (co <= hi), axis=1)])
        return groups

    def _find_cores(self, axes):
        """Cores for the hollows of the part that the opened block can't let go of (the inside of a sock, a
        tube): sets core_solid, core_prints and core_bvh.

        The hollows are the pockets between the part and its convex hull. A pocket gets a core if it is fairly
        closed (its opening is a small share of its surface) and much of it is trapped: from inside it, a ray
        along the way its block part opens hits the part. Each opening facing (about) one side of the block
        gets a print: the outline of the opening, extruded straight out to the face of the block, where the
        core ends flush. A print is {'axis': index, 'sign': side of the block, 'outline': convex outline in the
        two other axes, 'start': where it begins inside the hollow, 'top': outermost point of the opening}.
        """
        p = self.props
        grow = self._core_grow()
        min_volume = max(self.target_volume * 0.002, (3 * p.voxel_size) ** 3)
        pockets, prints, closed = [], [], 0
        for co in self._tray_vertex_groups():
            if len(co) < 4:
                continue
            bm = bmesh.new()
            for c in co:
                bm.verts.new(c)
            bmesh.ops.convex_hull(bm, input=bm.verts[:])
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
            # Shrunk a hair, so the faces of the part that lie on the hull don't coincide with it.
            mid = sum((v.co for v in bm.verts), Vector()) / len(bm.verts)
            for v in bm.verts:
                v.co = mid + (v.co - mid) * (1 - 2e-4)
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
            hull_bvh = BVHTree.FromBMesh(bm)
            hull = self.add_object("EasyMold_Hull", _mesh_from_bmesh("Hull", bm), temp=True)
            self.boolean(hull, self.target, 'DIFFERENCE')

            tol = self.size * 5e-5
            bm = bmesh.new()
            bm.from_mesh(hull.data)
            bm.normal_update()
            for volume, faces in face_islands(bm):
                if volume < min_volume:
                    continue
                mouth, area = [], 0.0
                for f in faces:
                    a = f.calc_area()
                    area += a
                    loc, normal, _i, dist = hull_bvh.find_nearest(f.calc_center_median())
                    if loc is not None and dist < tol and f.normal.dot(normal) > 0.99:
                        mouth.append((f, a))
                if not mouth:
                    closed += 1
                    continue
                if sum(a for _f, a in mouth) > area * self.CORE_MOUTH_SHARE:
                    continue  # an open dent: the block fills it
                if self._trapped_share(faces, axes) < self.CORE_TRAPPED:
                    continue
                pockets.append(faces_mesh("Pocket", faces))
                prints += self._core_prints_of(mouth, grow)
            bm.free()
            self.remove(hull)

        if closed:
            self.warnings.append(f"The part has {closed} closed hollow(s) inside: a mold can't make them, they "
                                 "come out solid")
        if not pockets:
            self.notes.append("found no hollow that needs a core")
            return
        joined = self.add_object("EasyMold_Pockets", _join_meshes("Pockets", pockets), temp=True)
        extent = max(Vector([max(v.co[i] for v in joined.data.vertices) - min(v.co[i] for v in joined.data.vertices)
                             for i in range(3)]))
        solid = self.sdf_offset(joined, grow, max(grow / 2, extent / 800), name="EasyMold_Cores")
        self.remove(joined)
        for pr in prints:
            self.boolean_temp(solid, self._print_mesh(pr), 'UNION')
        self._set_cores(solid, prints)

    def _set_cores(self, solid, prints):
        self.core_solid, self.core_prints = solid, prints
        self.core_bvh = self._bvh(solid)

    def _trapped_share(self, faces, axes):
        """Share of a pocket (given by its faces) that can't come out with the block part it is in: from a point
        inside it, a ray along the way that part opens hits the part."""
        verts = list({v for f in faces for v in f.verts})
        index = {v: n for n, v in enumerate(verts)}
        bvh = BVHTree.FromPolygons([v.co.copy() for v in verts], [[index[v] for v in f.verts] for f in faces])
        lo = Vector([min(v.co[i] for v in verts) for i in range(3)])
        hi = Vector([max(v.co[i] for v in verts) for i in range(3)])
        step = max(hi - lo) / 14
        counts = [max(1, math.ceil((hi[i] - lo[i]) / step)) for i in range(3)]
        inside = trapped = 0
        for a in range(counts[0]):
            for b in range(counts[1]):
                for c in range(counts[2]):
                    pt = Vector([lo[i] + (hi[i] - lo[i]) * (n + 0.5) / counts[i] for i, n in enumerate((a, b, c))])
                    if not _inside(bvh, pt):
                        continue
                    inside += 1
                    free = False
                    for axis in axes:
                        i = AXIS_INDEX[axis]
                        way = AXIS_VECTOR[axis] * (1 if pt[i] >= self._plane_value(axis, pt) else -1)
                        if self.bvh.ray_cast(pt, way)[0] is None:
                            free = True
                            break
                    trapped += not free
        return trapped / inside if inside else 0.0

    @staticmethod
    def _core_prints_of(mouth, grow):
        """Prints for the opening (faces, areas) of a pocket: one per side of the block it faces."""
        groups = {}
        for f, area in mouth:
            n = f.normal
            a = max(range(3), key=lambda i: abs(n[i]))
            group = groups.setdefault((a, 1 if n[a] > 0 else -1), [0.0, []])
            group[0] += area
            group[1] += [v.co.copy() for v in f.verts]
        biggest = max(area for area, _pts in groups.values())
        prints = []
        for (a, sign), (area, pts) in groups.items():
            if area < biggest * 0.15:
                continue  # e.g. a nozzle hole at the bottom of a sock: the core just reaches the block there
            j, k = [n for n in range(3) if n != a]
            w = [c[a] for c in pts]
            deep, top = (min(w), max(w)) if sign > 0 else (max(w), min(w))
            prints.append({'axis': a, 'sign': sign, 'outline': rounded_hull([(c[j], c[k]) for c in pts], grow),
                           'start': deep - sign * grow, 'top': top})
        return prints

    def _print_face(self, pr):
        lo, hi = self._block_bounds()
        return hi[pr['axis']] if pr['sign'] > 0 else lo[pr['axis']]

    def _print_mesh(self, pr, hole=False):
        """The print of a core: from inside the hollow to the face of the block. As a hole in the block: with the
        key tolerance around it and through the face."""
        a, sign = pr['axis'], pr['sign']
        tol = self.props.key_tolerance
        outline = rounded_hull(pr['outline'], tol) if hole and tol > 0 else pr['outline']
        start = pr['start'] - sign * (0.05 if hole else 0.0)
        end = self._print_face(pr) + sign * (1.0 if hole else 0.0)
        return prism_mesh("Print", outline, min(start, end), max(start, end), axis="XYZ"[a])

    def _in_core_print(self, pt, r):
        """Whether a point (with radius r around it) touches a core print."""
        for pr in self.core_prints:
            a = pr['axis']
            face = self._print_face(pr)
            if not min(pr['start'], face) - r <= pt[a] <= max(pr['start'], face) + r:
                continue
            j, k = [n for n in range(3) if n != a]
            q = Vector((pt[j], pt[k]))
            ring = [Vector(c) for c in pr['outline']]
            # The outline is convex and counter-clockwise: inside (or within r) of every edge.
            if all((p1 - p0).cross(q - p0) / max((p1 - p0).length, 1e-9) >= -(r + self.props.key_tolerance)
                   for p0, p1 in zip(ring, ring[1:] + ring[:1])):
                return True
        return False

    def _clear_core_prints(self):
        """Leave out the block keys and bolts that would cut into a core print."""
        p = self.props
        tol = p.key_tolerance
        pegs = self._core_pegs(self.axes)
        # Nor next to a core's peg: in a small block both sit in the middle of the margin, and their holes merge.
        keys = [key for key in self.keys if not self._in_core_print(key[1], key[2] + tol + 1.0)
                and all((key[1] - c).length > key[2] + r + 2 * tol + 2 * self.KEY_MARGIN for _a, c, r in pegs)]
        lo, hi = self._block_bounds()
        nut_r = self._bolt()['nut_r'] + 1.0

        def crosses(axis, c):
            # The bolt goes right through the block along its axis.
            i = AXIS_INDEX[axis]
            for n in range(41):
                q = c.copy()
                q[i] = lo[i] + (hi[i] - lo[i]) * n / 40
                if self._in_core_print(q, nut_r):
                    return True
            return False

        bolts = [bolt for bolt in self.bolts if not crosses(*bolt)]
        dropped = len(self.keys) - len(keys) + len(self.bolts) - len(bolts)
        if dropped:
            self.notes.append(f"left out {dropped} key(s)/bolt(s) where a core comes out of the block")
        self.keys, self.bolts = keys, bolts

    def _core_cover(self, pt, r):
        """Share of a vertical hole of radius r going up from pt that runs into a core."""
        if self.core_bvh is None:
            return 0.0
        up = Vector((0, 0, 1))
        hits = 0
        for n in range(17):
            q = pt.copy()
            if n:
                angle = 2 * math.pi * n / 16
                q += Vector((math.cos(angle), math.sin(angle), 0)) * r
            q.z = pt.z + 0.01
            hits += self.core_bvh.ray_cast(q, up)[0] is not None
        return hits / 17

    def _core_blocked(self, pt, r):
        return self._core_cover(pt, r) > 0

    def _pour_beside_cores(self, pt, r):
        """The pour hole moved off the cores (one in the opening of a sock would be closed by it): the top
        point of the object it overlaps least, the closest to the split planes."""
        if not self._core_blocked(pt, r):
            return pt
        height = self.bmax.z - self.bmin.z
        vertical = [AXIS_INDEX[a] for a in self.axes if a != 'Z']
        up = Vector((0, 0, 1))
        candidates = [pt]
        for v in self.high_verts[:3000]:
            if len(candidates) > 1 and v.z < candidates[1].z - max(height * 0.05, r):
                break
            if self.bvh.ray_cast(v + Vector((0, 0, 0.05)), up)[0] is None:
                candidates.append(v)

        def score(v):
            # On a split plane or well away from it: a hole just beside one would cross it.
            off = [abs(v[i] - self.center[i]) for i in vertical]
            crosses = any(0.5 < d < r + 3.5 for d in off)
            return round(self._core_cover(v, r), 3), crosses, sum(off), v

        cover, _crosses, _d, best = min((score(v) for v in candidates), key=lambda s: s[:3])
        if cover > 0:
            # Usual with a thin rim around the opening (a sock): fine, the hole goes on through the core.
            self.notes.append("the pour hole is partly over a core: it goes through the core too, above the part")
        else:
            self.notes.append("moved the pour hole off the core")
        return best.copy()

    def _core_pegs(self, axes):
        """(axis, center, radius) of key pegs on the sides of the core prints, on the split planes that cross
        them: the block parts hold the print between them and the pegs keep it from sliding out."""
        pegs = []
        for pr in self.core_prints:
            a = pr['axis']
            face = self._print_face(pr)
            wall = abs(face - pr['top'])
            j, k = [n for n in range(3) if n != a]
            ring = pr['outline']
            for axis in axes:
                s = AXIS_INDEX[axis]
                if s == a:
                    continue  # the print comes out of the part that opens along it
                t = j if s == k else k
                si, ti = (0, 1) if s == j else (1, 0)
                # Where the split is at the middle of the print's wall (a curved surface: about there).
                mid = Vector()
                mid[a] = (pr['top'] + face) / 2
                mid[j] = sum(q[0] for q in ring) / len(ring)
                mid[k] = sum(q[1] for q in ring) / len(ring)
                c = self._plane_value(axis, mid)
                hits = [p0[ti] + (c - p0[si]) / (p1[si] - p0[si]) * (p1[ti] - p0[ti])
                        for p0, p1 in zip(ring, ring[1:] + ring[:1])
                        if (p0[si] - c) * (p1[si] - c) <= 0 and p0[si] != p1[si]]
                if len(hits) < 2:
                    continue  # the plane misses the print
                low, high = min(hits), max(hits)
                r = min(self._key_radius(self.props.margin * 0.3), wall * 0.35, (high - low) * 0.25)
                if r < 0.8:
                    continue
                for value in (low, high):
                    center = Vector()
                    center[a] = (pr['top'] + face) / 2
                    center[s] = c
                    center[t] = value
                    center[s] = self._plane_value(axis, center)  # right on a curved surface
                    pegs.append(("XYZ"[t], center, r))
        return pegs

    def _load_or_find_cores(self, axes):
        """Cores from the edited layout (if it was made with Auto Core), otherwise found now."""
        if self.layout is None or not self.layout.get(AUTO_CORE):
            self._find_cores(axes)
            return
        dg = bpy.context.evaluated_depsgraph_get()
        meshes, prints = [], []
        for obj in [o for o in self.layout.objects if o.get(ROLE) == 'CORE' and o.type == 'MESH']:
            mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
            mesh.transform(obj.matrix_world)
            meshes.append(mesh)
            prints += decode_prints(obj.get(PRINTS, []), obj.matrix_world.translation)
        if meshes:
            self._set_cores(self.add_object("EasyMold_Cores", _join_meshes("Cores", meshes), temp=True), prints)

    def _cut_pours_in_core(self, core, holes):
        """Pour holes that overlap a core go through it too, but only above the part (below, the core forms the
        inside of the part)."""
        level = max((pr['top'] for pr in self.core_prints if pr['axis'] == 2 and pr['sign'] > 0), default=None)
        if level is None:
            return
        items = []
        for at, profile in holes:
            kept = [(z, r) for z, r in profile if z > level + 0.01]
            if not kept:
                continue
            item = (at, [(level + 0.01, profile[0][1])] + kept)
            items.append((lathe_mesh("EasyMold_Hole", [item]), *self._lathe_reach(item)))
        self.boolean_many(core, items, 'DIFFERENCE')

    def _finish_cores(self, core):
        """One printed piece per core, lying on its end that is flush with the block."""
        self.drop_fragments(core)
        bm = bmesh.new()
        bm.from_mesh(core.data)
        islands = face_islands(bm)
        pieces = [core]
        if len(islands) > 1:
            pieces = [self.add_object(f"{self.name}_Core_{n}", faces_mesh("Core", faces))
                      for n, (_v, faces) in enumerate(islands, 1)]
        bm.free()
        if len(pieces) > 1:
            self.remove(core)
        for obj in pieces:
            self._styled.append((obj, 'CORE'))
            co = [v.co for v in obj.data.vertices]
            lo = Vector([min(c[i] for c in co) for i in range(3)]) - Vector((1, 1, 1))
            hi = Vector([max(c[i] for c in co) for i in range(3)]) + Vector((1, 1, 1))
            downs = []
            for pr in self.core_prints:
                a = pr['axis']
                j, k = [n for n in range(3) if n != a]
                end = Vector()
                end[a] = self._print_face(pr)
                end[j] = sum(c[0] for c in pr['outline']) / len(pr['outline'])
                end[k] = sum(c[1] for c in pr['outline']) / len(pr['outline'])
                if all(lo[i] <= end[i] <= hi[i] for i in range(3)):
                    downs.append(AXIS_VECTOR["XYZ"[a]] * pr['sign'])
            export.set_down(obj, downs + [Vector((0, 0, -1))])
        self.notes.append(f"made {len(pieces)} core(s) for the hollows: put them in place before closing the mold")

    def build_direct(self, axes):
        p = self.props
        m = p.margin
        lo, hi = self._block_bounds()

        yield 0.1, "Building block"
        # Rounded edges; the keys sit at m/2 from the outside, so keep the bevel well below that.
        block = self.add_object(f"{self.name}_Mold", box_mesh("Mold", lo, hi, bevel=m * 0.3))
        if axes and p.clamp_method == 'CLIP':
            self.add_block_flanges(block, axes, lo, hi)
        yield 0.25, "Cutting the cavity"
        self.boolean(block, self.target, 'DIFFERENCE')

        core = p.core_object
        auto_core = None
        if core is not None:
            if core == self.original:
                raise MoldError("The core must be a different object than the part")
            yield 0.4, "Building core"
            core_part = self.world_copy(core, f"{self.name}_Core", style='CORE')
            self.boolean(core_part, self.target, 'DIFFERENCE')
            self.boolean(block, core, 'DIFFERENCE')
            if self.kind == 'RESULT':
                core.hide_set(True)
        elif p.auto_core:
            yield 0.3, "Finding hollows for cores"
            self._load_or_find_cores(axes)
            if self.core_solid is not None:
                yield 0.4, "Building cores"
                hole = self.add_object("EasyMold_CoreHole", self.core_solid.data.copy(), temp=True)
                for pr in self.core_prints:
                    self.boolean_temp(hole, self._print_mesh(pr, hole=True), 'UNION')
                self.boolean(block, hole, 'DIFFERENCE')
                self.core_hole = hole  # until the pour holes are cut
                auto_core =self.add_object(f"{self.name}_Core", self.core_solid.data.copy())
                self.boolean(auto_core, self.target, 'DIFFERENCE')
                pegs = self._core_pegs(axes)
                shape = p.key_shape
                self.boolean_many(auto_core, [(key_mesh("EasyMold_Peg", axis, c, r, shape), c, r * 1.5)
                                              for axis, c, r in pegs], 'UNION')
                if len(pegs) < 2 * len(self.core_prints):
                    self.notes.append("a core print doesn't cross a split line: no key pegs, it is held by its fit")

        self._place_spec(axes)
        if self.pours or self.vents:
            yield 0.5, "Cutting pour hole and vents"
            holes = self.cut_pours(block)
            if auto_core is not None:
                self._cut_pours_in_core(auto_core, holes)
        if auto_core is not None:
            # The peg sockets last: the raised sprue cup is joined to the block and could fill one.
            tol, shape = p.key_tolerance, p.key_shape
            self.boolean_many(block, [(key_mesh("EasyMold_Socket", axis, c, r, shape, grow=tol), c, (r + tol) * 1.5)
                                      for axis, c, r in self._core_pegs(axes)], 'DIFFERENCE')
            self.remove(self.core_hole)
            self.core_hole = None
            self._finish_cores(auto_core)
        if axes:
            yield 0.55, "Adding clamping"
            if self.bolts:
                self.cut_block_bolts(block)
            if p.clamp_method == 'BAND':
                self.block_band_grooves(block, axes, lo, hi, depth=min(1.5, m * 0.2))

        # Preview of the cast silicone part inside the mold.
        self.world_copy(self.target, f"{self.name}_Silicone (preview)", style='SILICONE')

        pieces = [(block, {})]
        for n, axis in enumerate(axes):
            yield 0.6 + 0.1 * n, f"Splitting along {axis}"
            pieces = self.split(pieces, axis)
        if self.keys:
            yield 0.85, "Adding registration keys"
            self.add_keys(pieces)
        self.finish_pieces(pieces, f"{self.name}_Mold", back_down=True)
        if p.explode:
            self.explode(pieces)

    # --- print bed ---------------------------------------------------------

    PIN_R = 1.75 / 2 + 0.15  # 1.75 mm filament used as alignment pins, with clearance
    PIN_DEPTH = 8.0          # hole depth on each side of a bed cut

    def _mark_printable(self):
        for obj in self.collection.objects:
            if (export.DOWN not in obj and "(preview)" not in obj.name
                    and obj not in self._temp and obj != self.target):
                export.set_down(obj, [(0, 0, -1)])  # base, walls, core, master: as they stand

    def _misfit(self, obj):
        """(world axis index to cut, print size) if the piece, turned as it will be printed, doesn't fit the
        bed (it may be turned 90 degrees on the plate); (None, size) if it fits."""
        p = self.props
        co, tris = export._arrays(obj)
        matrix, _down = export.print_matrix(obj, co, tris)
        size = export._apply(matrix, co).max(axis=0)
        rot = matrix.to_3x3()  # pieces are never rotated in the scene: this is the print turn

        def world_axis(k):
            return max(range(3), key=lambda j: abs(rot[k][j]))

        small, large = sorted((p.bed_x, p.bed_y))
        short = 0 if size[0] <= size[1] else 1
        if size[2] > p.bed_z:
            return world_axis(2), size
        if size[1 - short] > large:
            return world_axis(1 - short), size
        if size[short] > small:
            return world_axis(short), size
        return None, size

    def fit_bed(self):
        """Warn about pieces that don't fit the print bed; with Split to Fit, cut them in halves."""
        p = self.props
        self._mark_printable()
        queue = [o for o in self.collection.objects if export.DOWN in o]
        too_big, cuts, pinless = [], 0, 0
        while queue:
            obj = queue.pop()
            j, size = self._misfit(obj)
            if j is None:
                continue
            if not p.split_to_fit or obj.get("easymold_cuts", 0) >= 3:
                too_big.append(f"{obj.name} ({size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f} mm)")
                continue
            halves, pinned = self._cut_in_two(obj, j)
            cuts += 1
            pinless += not pinned
            queue += halves
        if too_big:
            hint = "" if p.split_to_fit else ": enable Split to Fit or use more parts"
            self.warnings.append(f"Too big for the {p.bed_x:g} x {p.bed_y:g} x {p.bed_z:g} mm bed: "
                                 + ", ".join(too_big) + hint)
        if cuts:
            self.notes.append(f"cut {cuts} piece(s) to fit the bed: glue them with "
                              f"{2 * self.PIN_DEPTH - 1:.0f} mm pins of 1.75 mm filament")
        if pinless:
            self.warnings.append(f"{pinless} bed cut(s) had no room for alignment pins: line those up by hand")

    def _cut_in_two(self, obj, j):
        """Cut a piece in two across world axis j, at its middle, with filament pin holes across the cut."""
        axis = 'XYZ'[j]
        offset = obj.matrix_world.translation
        co, _tris = export._arrays(obj)
        value = (co[:, j].min() + co[:, j].max()) / 2 + offset[j]
        pins = self._pin_points(obj, j, value)

        kind = next((k for o, k in self._styled if o == obj), 'PART_A')
        other = self.add_object(obj.name, obj.data.copy(), style=kind)
        other.location = obj.location
        other[export.DOWN] = list(obj[export.DOWN])
        if self._is_dirty(obj):
            self._mark_dirty(other)
        cutter = self.add_object("EasyMold_Cutter", self.halfspace_mesh(axis, value, +1), temp=True)
        self.boolean(obj, cutter, 'INTERSECT')
        self.boolean(other, cutter, 'DIFFERENCE')
        self.remove(cutter)
        for piece in (obj, other):
            # A new list each time: boolean_many uses up the cutter meshes.
            holes = [(lathe_mesh("EasyMold_PinHole", [(_axis_matrix(axis, c), [(-self.PIN_DEPTH, self.PIN_R),
                                                                               (self.PIN_DEPTH, self.PIN_R)])]),
                      c, self.PIN_DEPTH + self.PIN_R) for c in pins]
            self.boolean_many(piece, holes, 'DIFFERENCE')

        name, n = obj.name, obj.get("easymold_cuts", 0) + 1
        obj.name, other.name = f"{name}_2", f"{name}_1"
        for piece, side in ((obj, 1), (other, -1)):
            piece["easymold_cuts"] = n
            if self.props.explode:
                piece.location[j] += side * 3.0
        return [obj, other], bool(pins)

    def _pin_points(self, obj, j, value):
        """Up to 3 spots on the cut plane (world value along axis j) with material all around a pin hole,
        as far apart as possible."""
        r, depth = self.PIN_R, self.PIN_DEPTH
        offset = obj.matrix_world.translation
        bvh = self._bvh(obj)
        co, _tris = export._arrays(obj)
        k1, k2 = [k for k in range(3) if k != j]
        lo, hi = co.min(axis=0), co.max(axis=0)
        step = max(1.0, max(hi[k1] - lo[k1], hi[k2] - lo[k2]) / 50)
        candidates = []
        for u in np.arange(lo[k1] + step / 2, hi[k1], step):
            for w in np.arange(lo[k2] + step / 2, hi[k2], step):
                p = Vector((0.0, 0.0, 0.0))
                p[j], p[k1], p[k2] = value - offset[j], u, w
                clearance = math.inf
                for t in (-depth, 0.0, depth):
                    q = p.copy()
                    q[j] += t
                    loc, normal, _i, dist = bvh.find_nearest(q)
                    if loc is None or (q - loc).dot(normal) >= 0 or dist < r + 1.0:
                        break
                    clearance = min(clearance, dist)
                else:
                    candidates.append((clearance, p))
        if not candidates:
            return []
        chosen = [max(candidates, key=lambda c: c[0])[1]]
        while len(chosen) < 3:
            far = max(candidates, key=lambda c: min((c[1] - q).length for q in chosen))[1]
            if min((far - q).length for q in chosen) < 10.0:
                break
            chosen.append(far)
        return [q + offset for q in chosen]

    # --- results ---------------------------------------------------------

    def _finish_result(self):
        # Unmodified copy of the (oriented) master placed next to the mold.
        master = self.world_copy(self.target, f"{self.name}_Master", style='MASTER')
        master[export.MASTER_COPY] = True
        self.cleanup()
        for obj in self.collection.objects:
            if obj.type == 'MESH':
                triangulate_ngons(obj.data)
        bpy.context.view_layer.update()  # exploded pieces moved: refresh matrix_world
        min_x = min((o.matrix_world @ Vector(c)).x for o in self.collection.objects if o != master
                    for c in o.bound_box)
        master.location.x = min_x - self.size * 0.2 - self.bmax.x

        self._apply_styles()
        self._replace_previous()
        # The original stays untouched; it is only hidden so it doesn't overlap the mold.
        for obj in [self.original] + self.extras:
            obj.hide_set(True)
        if self.layout is not None:
            set_collection_visible(self.layout, False)
        for obj in self.collection.objects:
            obj[ROLE] = 'PART'  # lets the panel find the source from any generated part
            if "easymold_cuts" in obj:
                del obj["easymold_cuts"]
        self._mark_printable()
        self.collection["easymold_volume"] = self.silicone_volume or 0.0
        self.collection["easymold_cast_volume"] = self.cast_volume or 0.0
        _make_active(master)

    def _finish_preview(self):
        """Auto Preview: the pieces as they are, not selectable; the selection, the original and the layout are
        left alone. The real result (if any) is hidden while the preview stands in for it."""
        self.cleanup()
        self._apply_styles()
        self._replace_previous()
        for obj in self.collection.objects:
            obj[ROLE] = 'PART'
            obj.hide_select = True
            if "easymold_cuts" in obj:
                del obj["easymold_cuts"]
        for result in _collections_of(self.original, 'RESULT'):
            set_collection_visible(result, False)

    # --- live preview overlay --------------------------------------------

    def plan_shapes(self, axes, markers_only=False):
        """What the overlay draws, in world space: {'lines': {kind: [p0, p1, ...] segments}, 'tris': {kind:
        [a, b, c, ...]}, 'markers': {name: {'kind', 'lines', 'origin', 'radius'}} the shapes of the layout markers
        (not in 'lines'; origin and radius: the marker when planned), 'misfit': ["W x D x H", ...] pieces too big
        for the bed, 'summary': text}. Kinds: OUTLINE, SPLIT, KEY, BOLT, POUR, VENT, BLOCKER, MISFIT, MASTER.
        markers_only: only the markers are wanted (a shell's outline, slow to make, is left out)."""
        p = self.props
        lines = {}
        tris = {}
        markers = {}

        def add(kind, segments, at=None):
            found = self.marker_of.get(id(at)) if at is not None else None
            if found is None or found[1] is not at:
                lines.setdefault(kind, []).extend(segments)
                return
            name = found[0]
            if name not in markers:
                obj = self.layout.objects.get(name)
                markers[name] = {'kind': kind, 'lines': [], 'origin': obj.matrix_world.translation.copy(),
                                 'radius': marker_radius(obj)}
            markers[name]['lines'].extend(segments)

        # The outside of the mold, and the boxes of the pieces it is cut into (for the bed check).
        s, w = p.silicone_thickness, p.wall_thickness
        if p.mold_type == 'DIRECT':
            lo, hi = self._block_bounds()
            add('OUTLINE', _box_lines(lo, hi))
            boxes = self._plan_pieces(lo, hi, axes)
        elif p.mold_type == 'SILICONE_BOX':
            inner, outer, _lip = self._box_outlines()
            lip_bottom = self._floor() - p.lip_height
            top = self.bmax.z + s
            base_bottom = lip_bottom - p.base_thickness
            add('OUTLINE', _prism_lines(outer, lip_bottom, top) + _prism_lines(inner, lip_bottom, top, sides=False)
                + _prism_lines(outer, base_bottom, lip_bottom))
            lo, hi = _polygon_box(outer, base_bottom, top)
            boxes = [_polygon_box(outer, lip_bottom, top), _polygon_box(outer, base_bottom, lip_bottom)]
        elif p.mold_type == 'SILICONE_TWO_PART':
            bottom, top = self._frame_range()
            outer = self._box_ring(w)
            base_bottom = bottom - p.base_thickness
            add('OUTLINE', _prism_lines(outer, bottom, top) + _prism_lines(self._box_ring(0.0), bottom, top, sides=False)
                + _prism_lines(outer, base_bottom, bottom))
            lo, hi = _polygon_box(outer, base_bottom, top)
            boxes = [_polygon_box(outer, bottom, top), _polygon_box(outer, base_bottom, self.parting_z)]
        elif markers_only:
            lo, hi = self.bmin - Vector((s + w,) * 3), self.bmax + Vector((s + w,) * 3)
            boxes = self._plan_pieces(lo, hi, axes)
        else:
            # The shell follows the object (a coarse offset in a plan, see offset_source).
            corners = _mesh_triangles(self.offset_source(s + w).data)
            tris['OUTLINE'] = corners
            lo, hi = Vector(corners.min(axis=0)), Vector(corners.max(axis=0))
            boxes = self._plan_pieces(lo, hi, axes)
        pad = Vector((2, 2, 2))
        lo, hi = lo - pad, hi + pad

        # Split planes, the curved parting surface, the 2-part parting line.
        curved = self.parting.axis if self.parting is not None else None
        for axis in axes:
            if AXIS_INDEX[axis] != curved:
                add('SPLIT', _plane_lines(axis, self.center[AXIS_INDEX[axis]], lo, hi))
        if self.parting is not None:
            add('SPLIT', _surface_lines(self.parting, lo, hi))
        elif self.parting_z is not None:
            add('SPLIT', _plane_lines('Z', self.parting_z, lo, hi))

        for _axis, c, r in self.keys:
            add('KEY', _sphere_lines(c, r), c)
        for _a, c, r in self._core_pegs(axes) if self.core_prints else []:
            add('KEY', _sphere_lines(c, r))
        bolt_r = p.bolt_diameter / 2
        for axis, c in self.bolts:
            i = AXIS_INDEX[axis]
            a, b = c.copy(), c.copy()
            if p.mold_type == 'DIRECT':
                a[i], b[i] = lo[i] + 2, hi[i] - 2
            else:
                a[i] -= p.flange_thickness * 1.5
                b[i] += p.flange_thickness * 1.5
            # A cylinder: its two ends and four lines along it.
            sides = []
            for n in range(4):
                d = Vector()
                d[(i + 1 + n % 2) % 3] = bolt_r if n < 2 else -bolt_r
                sides += [a + d, b + d]
            add('BOLT', sides + _circle_lines(a, i, bolt_r) + _circle_lines(b, i, bolt_r), c)

        # Pour holes (with their funnels) and vents, as the build shapes them.
        gate_tubes = {id(gate[0]): self._gate_tube(gate) for gate in self.gates}
        for pt, r in self.pours:
            if p.mold_type == 'SILICONE_TWO_PART':
                profile = self._sprue_profile(pt, r)
            else:
                if id(pt) in gate_tubes:
                    lift = hi.z - 2
                    profile = [(pt.z + gate_tubes[id(pt)][2], r)]
                else:
                    lift = self._pour_lift(pt)
                    profile = self._tip_profile(pt.z, r, self._tip(r, p.pour_tip), (lift - pt.z) * 0.4)
                    profile[0] = (pt.z, profile[0][1])  # drawn from the part's surface
                if (shape := self._sprue_shape(r, pt)) is not None:
                    profile += [(lift, r), (lift + p.sprue_height, shape[0])]
                elif p.pour_style == 'HOLE':
                    chamfer = min(self._hole_chamfer(r), (lift - profile[-1][0]) * 0.5)
                    profile += [(lift - chamfer, r), (lift, r + chamfer)]
                else:
                    profile.append((lift, r))
            add('POUR', _profile_lines(pt, profile), pt)
        for gate in self.gates:
            points, radii, _bend = gate_tubes[id(gate[0])]
            add('POUR', _tube_lines(points, radii), gate[0])
        for pt, r in self.vents:
            top = self._frame_range()[1] if p.mold_type == 'SILICONE_TWO_PART' else self._surface_z(pt) + 1
            points, radii = self._vent_axis(pt, r, top)
            points[0] = Vector((points[0].x, points[0].y, pt.z))
            add('VENT', _tube_lines(points, radii), pt)
        # Markers that end up unused (in the way of a pour channel, off in the settings): a faint red ball.
        if self.layout is not None:
            for obj in self.layout.objects:
                if obj.get(ROLE) in MARKERS and obj.type == 'EMPTY' and obj.name not in markers:
                    c = obj.matrix_world.translation.copy()
                    markers[obj.name] = {'kind': 'UNUSED', 'lines': _sphere_lines(c, marker_radius(obj)),
                                         'origin': c, 'radius': marker_radius(obj)}

        add('BLOCKER', self.blocker_lines)
        if self.reshaped and not markers_only:
            # The mold is made for the turned (or enlarged) copy, not the object as it stands.
            tris['MASTER'] = _mesh_triangles(self.target.data)

        misfit = []
        for box_lo, box_hi in boxes:
            size = box_hi - box_lo
            if not _fits_bed(size, p):
                add('MISFIT', _box_lines(box_lo, box_hi))
                misfit.append(f"{size.x:.0f} x {size.y:.0f} x {size.z:.0f}")
        parts = len(boxes)
        bits = [f"{parts} piece{'s' if parts != 1 else ''}"]
        for count, word in ((len(self.keys), "key"), (len(self.bolts), "bolt"), (len(self.pours), "pour hole"),
                            (len(self.vents), "vent")):
            if count:
                bits.append(f"{count} {word}{'s' if count != 1 else ''}")
        return {'lines': lines, 'tris': tris, 'markers': markers, 'misfit': misfit, 'summary': ", ".join(bits)}

    def _plan_pieces(self, lo, hi, axes):
        """Bounding boxes of the pieces a box from lo to hi is split into (at the flat split values)."""
        boxes = [(lo.copy(), hi.copy())]
        for axis in axes:
            i = AXIS_INDEX[axis]
            value = self.center[i]
            halves = []
            for a, b in boxes:
                below, above = b.copy(), a.copy()
                below[i] = above[i] = value
                halves += [(a, below), (above, b)]
            boxes = halves
        return boxes

    def _create_layout(self, axes):
        p = self.props
        master = self.world_copy(self.target, f"{self.name}_Layout_Master", style='MASTER')
        master[ROLE] = 'MASTER'
        master[export.MASTER_COPY] = True
        if self.tray:
            master[TRAY] = [c for part in self.tray for c in (*part['bmin'], *part['bmax'])]

        # Translucent outline of the outside of the mold, as a reference to place things.
        if p.mold_type == 'DIRECT':
            lo, hi = self._block_bounds()
            outline_mesh = box_mesh("Outline", lo, hi, bevel=p.margin * 0.3)
        elif p.mold_type == 'SILICONE_BOX':
            _inner, outer, _lip = self._box_outlines()
            outline_mesh = prism_mesh("Outline", outer, self._floor() - p.lip_height, self.bmax.z + p.silicone_thickness)
        elif p.mold_type == 'SILICONE_TWO_PART':
            bottom, top = self._frame_range()
            outline_mesh = prism_mesh("Outline", self._box_ring(p.wall_thickness), bottom, top)
        else:
            outline_mesh = self.offset_source(p.silicone_thickness + p.wall_thickness).data.copy()
        outline = self.add_object(f"{self.name}_Outline", outline_mesh, style='OUTLINE')

        curved = "XYZ"[self.parting.axis] if self.parting is not None else None
        planes = [self.add_object(f"Split {axis}", plane_mesh("Plane", self.center, axis, self.size * 2 + 40))
                  for axis in axes if axis != curved]
        for obj in [outline] + planes:
            obj[ROLE] = 'GUIDE'
            obj.hide_select = True
            obj.hide_render = True
        for plane in planes:
            plane.display_type = 'WIRE'
        if self.parting is not None:
            # Editable: move it, or its vertices along its axis (proportional editing) in Edit Mode.
            surface = self.add_object("Parting Surface", self.parting.surface_mesh("Parting Surface"),
                                      style='PARTING')
            surface.hide_render = True
            surface[ROLE] = 'PARTING_SURFACE'
            surface[GRID] = self.parting.grid_info()
        elif self.parting_z is not None:
            # Editable: move it up or down to choose where the two silicone halves meet.
            parting = self.add_object("Parting Line", plane_mesh("Plane", Vector(), 'Z', self.size * 2 + 40))
            parting.location = (self.center.x, self.center.y, self.parting_z)
            parting.display_type = 'WIRE'
            parting.hide_render = True
            parting.lock_location = (True, True, False)
            parting.lock_rotation = (True, True, True)
            parting[ROLE] = 'PARTING'

        for _axis, pt, r in self.keys:
            _new_marker(self.collection, 'KEY', pt, r)
        for axis, pt in self.bolts:
            restyle_marker(_new_marker(self.collection, 'BOLT', pt, p.bolt_diameter / 2), AXIS_INDEX[axis])
        sprues = [gate[0] for gate in self.gates]
        for pt, r in self.pours:
            if not any(pt is sprue for sprue in sprues):
                _new_marker(self.collection, 'POUR', pt, r)
        for _sprue, end, r, _path in self.gates:
            _new_marker(self.collection, 'POUR', end, r)  # an entry from below: where it enters the part
        for pt, r in self.vents:
            _new_marker(self.collection, 'VENT', pt, r)
        for n, mesh in enumerate(self.blockers):
            _new_blocker(self.collection, f"Blocker {n + 1}", mesh)
        self.blockers = []
        if self._wants_auto_core():
            # Editable (move it, or delete it to go without); Generate uses what is left here.
            self.collection[AUTO_CORE] = True
            if self.core_solid is not None:
                core = self.add_object("Core", self.core_solid.data.copy(), style='CORE')
                core[ROLE] = 'CORE'
                core[PRINTS] = encode_prints(self.core_prints)

        # Where the markers snap to while they are moved (snap_marker).
        self.collection[LAYOUT_AXES] = "".join(axes)
        self.collection[LAYOUT_CENTER] = list(self.center)
        if self.parting_z is not None:
            self.collection[LAYOUT_PARTING_Z] = self.parting_z
        if p.mold_type == 'DIRECT':
            lo, hi = self._block_bounds()
            self.collection[LAYOUT_BLOCK] = list(lo) + list(hi)

        self.cleanup()
        self._apply_styles()
        if self.face_classes is not None:
            self._paint_undercuts(master)
        self._replace_previous()
        for obj in [self.original] + self.extras:
            obj.hide_set(True)
        for result in _collections_of(self.original, 'RESULT'):
            set_collection_visible(result, False)
        _make_active(master)

    # --- adding to an edited layout ----------------------------------------

    def _place_marker(self, layout, role, axes):
        """A new marker where the automatic placement puts one, at the candidate farthest from the markers already
        there (see add_layout_item)."""
        p = self.props
        name = ROLE_NAMES[role]
        if role in ('POUR', 'VENT') and not p.use_sprues:
            raise MoldError("Pour Hole & Vents are off (Pouring panel)")
        if role == 'KEY':
            if not p.use_keys:
                raise MoldError("Registration Keys are off (Parts panel)")
            if not self.keys:
                raise MoldError("This mold has no registration keys (it isn't split, or there is no room)")
            cands = [(c, r) for _a, c, r in self.keys] + self._along_edges(self.keys)
            size = self.keys[0][2]
        elif role == 'BOLT':
            if p.clamp_method != 'SCREW':
                raise MoldError("Bolts are only used with Clamping: Screws")
            if not self.bolts:
                raise MoldError("This mold has no room for bolts")
            bolts = [(a, c, p.bolt_diameter) for a, c in self.bolts]
            cands = [(c, r) for _a, c, r in bolts] + self._along_edges(bolts)
            size = p.bolt_diameter
        elif role == 'POUR':
            size = p.sprue_radius
            sprues = [g[0] for g in self.gates]
            cands = [(end, r) for _s, end, r, _path in self.gates]
            cands += [(pt, r) for pt, r in self.pours if not any(pt is s for s in sprues)]
            cands += [(v, size) for v in self._auto_vents(8, size) + self._top_points(8)]
        else:
            size = p.vent_radius
            cands = [(v, size) for v in self._auto_vents(8, size) + self._top_points(8)]
        if not cands:
            raise MoldError(f"No place found for a {name}")
        existing = [(o.matrix_world.translation.copy(), marker_radius(o)) for o in layout.objects
                    if o.get(ROLE) in MARKERS and o.type == 'EMPTY']
        # The pour channels of the layout as it is (from its markers) are in the way too.
        self.spec_from_layout(axes)
        top = self._block_bounds()[1].z + 1
        channels = [(s0, s1, r) for sprue, _end, r, path in self.gates
                    for s0, s1 in [(sprue, Vector((sprue.x, sprue.y, top)))] + list(zip(path, path[1:]))]

        def room(c, r):
            near = [(c - q).length - r - rq for q, rq in existing]
            near += [_segment_distance(c, s0, s1) - r - rs for s0, s1, rs in channels]
            return min(near, default=math.inf)

        best = max(cands, key=lambda cand: room(*cand))
        obj = _new_marker_like(layout, role, best[0].copy(), size, p)
        snap_marker(obj, p)
        note = "" if room(*best) > 0.5 else f"no free spot left: the new {name} overlaps another one, move it"
        return obj, note

    def _along_edges(self, items):
        """More spots for keys/bolts of a direct mold: every so often between two automatic ones on the same split
        plane and the same edge of the block (one of their two other coordinates equal), where the margin runs
        around the object. Other molds keep to the automatic spots (between two of them can be the object)."""
        if self.props.mold_type != 'DIRECT':
            return []
        out = []
        for n, (axis, a, r) in enumerate(items):
            i = AXIS_INDEX[axis]
            for axis2, b, _r in items[n + 1:]:
                if axis2 != axis or not any(abs(a[j] - b[j]) < 1e-3 for j in range(3) if j != i):
                    continue
                steps = max(2, int((a - b).length / (2 * r + 2)))
                for t in range(1, steps):
                    q = a.lerp(b, t / steps)
                    q[i] = self._plane_value(axis, q)
                    out.append((q, r))
        return out

    def _top_points(self, count):
        """High points of the object open to the sky, spread out (more spots for pour holes and vents)."""
        up = Vector((0, 0, 1))
        found = []
        for v in self.high_verts:
            if len(found) >= count:
                break
            if all((v.xy - q.xy).length > self.size * 0.12 for q in found) and \
                    self.bvh.ray_cast(v + Vector((0, 0, 0.05)), up)[0] is None:
                found.append(v.copy())
        return found

    def _place_blocker(self, layout):
        """A new blocker over a through-hole that is still open, or (none) in the middle to move onto one."""
        found = self._find_blockers()
        if found:
            for mesh in found[1:]:
                bpy.data.meshes.remove(mesh)
            return _new_blocker(layout, "Blocker", found[0]), ""
        t = self.props.blocker_thickness
        disk = lathe_mesh("Blocker", [(Matrix.Translation(self.center), [(-t / 2, self.size * 0.15),
                                                                          (t / 2, self.size * 0.15)])], segments=48)
        return _new_blocker(layout, "Blocker", disk), ("no open through-hole found: placed in the middle, move, "
                                                       "rotate and scale it over the hole")

    def analyze_undercuts(self, axes):
        """Per-face OK / NO_DRAFT / UNDERCUT of the working copy, for how this mold type is opened."""
        p = self.props
        centers, normals = analysis.face_arrays(self.target.data)
        n = len(centers)
        eps = max(self.size * 2e-3, 0.05)
        ignore = np.zeros(n, dtype=bool)
        if p.mold_type == 'DIRECT' and axes:
            # Each part moves away from the split plane, on the side the face is on.
            options = []
            for axis in axes:
                i = AXIS_INDEX[axis]
                dirs = np.zeros((n, 3))
                dirs[:, i] = np.where(centers[:, i] >= self._plane_values(i, centers), 1.0, -1.0)
                options.append(dirs)
        elif p.mold_type == 'DIRECT':
            # Open-top mold: the part is lifted out, i.e. the mold moves down; the top is open.
            options = [np.tile((0.0, 0.0, -1.0), (n, 1))]
            ignore = centers[:, 2] > self.bmax.z - eps
        elif p.mold_type == 'SILICONE_TWO_PART':
            # Above the parting line the first half lifts off; below it the master lifts out of the board
            # (and later of the second half).
            dirs = np.zeros((n, 3))
            dirs[:, 2] = np.where(centers[:, 2] >= self._plane_values(2, centers), 1.0, -1.0)
            options = [dirs]
        else:
            # Silicone mold: the master comes out through the opening at the bottom (the silicone moves
            # up relative to it); the bottom face sits on the base.
            options = [np.tile((0.0, 0.0, 1.0), (n, 1))]
            ignore = centers[:, 2] < self.bmin.z + eps
        self.face_classes = analysis.classify_faces(self.bvh, centers, normals, options, ignore)
        areas = np.empty(n)
        self.target.data.polygons.foreach_get("area", areas)
        undercut = float(areas[self.face_classes == analysis.UNDERCUT].sum() / max(areas.sum(), 1e-9))
        if undercut > 0.001:
            if p.mold_type == 'DIRECT':
                self.notes.append(f"{undercut:.0%} of the surface is undercut (red): it locks the printed parts")
            elif p.mold_type == 'SILICONE_TWO_PART':
                red = self.face_classes == analysis.UNDERCUT
                below = float(areas[red & (centers[:, 2] < self._plane_values(2, centers))].sum()
                              / max(areas.sum(), 1e-9))
                self.notes.append(f"{undercut:.0%} of the surface is undercut (red)")
                if below > 0.001:
                    self.notes.append(f"{below:.0%} is below the parting line, where it locks the master in the "
                                      "printed board: try moving the Parting Line")
            else:
                self.notes.append(f"{undercut:.0%} of the surface needs the silicone to stretch to demold (red)")

    def _paint_undercuts(self, master):
        mesh = master.data
        mesh.materials.clear()
        for kind in ('RELEASES', 'NO_DRAFT', 'UNDERCUT'):
            mesh.materials.append(get_material(kind))
        mesh.polygons.foreach_set("material_index", self.face_classes.astype(np.int32))
        mesh.update()

    def _apply_styles(self):
        for obj in self.collection.objects:
            if "easymold_dirty" in obj:
                del obj["easymold_dirty"]
        for obj, kind in self._styled:
            mesh = obj.data
            mesh.materials.clear()
            mesh.materials.append(get_material(kind))
            # Custom normals of the user's object ride along through the booleans and leave the new faces (cut
            # faces, block sides) streaky: only the plain copies of the master keep them.
            if "custom_normal" in mesh.attributes and not obj.get(export.MASTER_COPY):
                mesh.attributes.remove(mesh.attributes["custom_normal"])
            mesh.shade_smooth()
            mesh.set_sharp_from_angle(angle=math.radians(35))
            if MATERIALS[kind][1] < 1.0:
                obj.show_transparent = True

    # --- small helpers ---------------------------------------------------

    @staticmethod
    def _bvh(obj):
        mesh = obj.data
        return BVHTree.FromPolygons([v.co.copy() for v in mesh.vertices], [tuple(p.vertices) for p in mesh.polygons])


def steps(builder, run):
    """Wrap a builder generator: on any error or cancel (generator closed) remove what was created."""
    try:
        yield from run()
    except BaseException:
        builder.abort()
        raise
    builder.cleanup()
