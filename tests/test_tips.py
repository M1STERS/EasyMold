# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where the holes touch the cast part: the entry from below turns down a round elbow
instead of a sharp L, the pour hole/channel and the vents can narrow to a tip (a smaller mark), and the vents can
lean away from the part instead of running alongside a wall. Each case is built with and without the option and
checked by sampling points of the pieces: what must be open is open, what must now be mold is mold."""
import sys, importlib, math
import bpy, bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from EasyMold import utils
failures = []
UP = Vector((0, 0, 1))


def check(label, ok, msg):
    print(f"   {'ok  ' if ok else 'FAIL'} {label}: {msg}")
    if not ok:
        failures.append(f"{label}: {msg}")


def prism(name, outline, y0, y1):
    """A solid from an XZ outline (counter-clockwise seen from -Y) extruded from y0 to y1."""
    bm = bmesh.new()
    front = [bm.verts.new((x, y0, z)) for x, z in outline]
    back = [bm.verts.new((x, y1, z)) for x, z in outline]
    bm.faces.new(front)
    bm.faces.new(list(reversed(back)))
    n = len(outline)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((front[i], back[i], back[j], front[j]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def ring():  # a thin wall like a hot end silicone sock: 1.4 mm thick, 5 mm high
    bpy.ops.mesh.primitive_cylinder_add(radius=6, depth=5, vertices=96, location=(0, 0, 2.5))
    obj = bpy.context.active_object
    bpy.ops.mesh.primitive_cylinder_add(radius=4.6, depth=8, vertices=96, location=(0, 0, 2.5))
    hole = bpy.context.active_object
    mod = obj.modifiers.new("hole", 'BOOLEAN')
    mod.operation, mod.object = 'DIFFERENCE', hole
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="hole")
    bpy.data.objects.remove(hole)
    return obj


def step():  # a low step beside a tall wall (the wall's face at x = 4)
    return prism("Step", [(-10, 0), (10, 0), (10, 20), (4, 20), (4, 5), (-10, 5)], -10, 10)


def candle():
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=12, depth=30, location=(0, 0, 15))
    return bpy.context.active_object


def ball():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=12, segments=48, ring_count=24, location=(0, 0, 12))
    return bpy.context.active_object


def setup(make, **settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = make()
    for o in bpy.context.view_layer.objects.selected:
        if o is not None:
            o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    p = bpy.context.scene.easymold_props
    p.mold_type, p.explode, p.voxel_size = 'DIRECT', False, 0.3
    for k, v in settings.items():
        setattr(p, k, v)
    return obj


def vent_at(obj, where):
    """Edit the layout and leave a single vent marker at `where` (it lands on the part below it)."""
    bpy.ops.object.easymold_edit_layout(reset=True)
    layout = next(c for c in bpy.data.collections if c.get(utils.ROLE) == 'LAYOUT')
    vents = [o for o in layout.objects if o.get(utils.ROLE) == 'VENT']
    for o in vents[1:]:
        bpy.data.objects.remove(o)
    vents[0].location = where
    for o in bpy.context.view_layer.objects.selected:
        if o is not None:
            o.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def generate(obj, label):
    before = [tuple(v.co) for v in obj.data.vertices]
    result = bpy.ops.object.easymold_generate()
    coll = utils.find_result(obj)
    pieces = [o for o in coll.objects if "_Mold" in o.name or o.name.endswith(("_Master_Sprue", "_Board", "_Frame"))]
    for o in pieces:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        islands = len(utils.face_islands(bm))
        bm.free()
        print(f"     {o.name:34s} nonmanifold={utils.open_edges(o.data):4d} islands={islands}")
        check(label, utils.open_edges(o.data) == 0 and islands == 1, f"{o.name} closed, one piece")
    check(label, result == {'FINISHED'} and [tuple(v.co) for v in obj.data.vertices] == before,
          "generated, original untouched")
    trees = {}
    for o in pieces:
        m = o.data.copy()
        m.transform(o.matrix_world)
        trees[o.name] = BVHTree.FromPolygons([v.co.copy() for v in m.vertices], [tuple(f.vertices) for f in m.polygons])
        bpy.data.meshes.remove(m)
    return trees


def solid(trees, pt):
    pt = pt + Vector((0.031, 0.029, 0.0))  # off the split planes (on a cut face inside/outside is ambiguous)
    return any(utils._inside(t, pt) for t in trees.values())


def builder(obj):
    props = bpy.context.scene.easymold_props
    b = utils.MoldBuilder(obj, props)
    b.prepare(use_layout=True)
    b.split_axes()
    b._place_spec(b.axes)
    return b


# 1. Entry from below: round elbow, and the tip where the gate enters the part.
for tip in (0.0, 1.0):
    label = f"candle, entry from below, tip {tip}"
    print("##", label)
    obj = setup(candle, mold_parts='2', split_axis='X', pour_entry='BOTTOM', pour_tip=tip, sprue_radius=3.0)
    b = builder(obj)
    sprue, end, r, path = b.gates[0]
    points, radii, bend = b._gate_tube(b.gates[0])
    b.abort()
    obj.hide_set(False)
    trees = generate(obj, label)
    way = (path[1] - path[0])
    way.z = 0
    way.normalize()
    check(label, not any(solid(trees, q) for q in points[1:-1]), "the sprue, elbow and gate are open all along")
    corner = sprue + (-UP - way).normalized() * r * 0.9  # inside the old L's corner
    check(label, solid(trees, corner), "the elbow is round (the L's outer corner is mold now)")
    entry = path[-2] if len(path) > 2 else end - (end - sprue).normalized() * min(0.3, r * 0.5)
    beside = entry - way * 0.15 + UP * (r * 0.75)  # next to the entry, just outside the part
    check(label, solid(trees, beside) == (tip > 0), f"beside the entry: {'mold (narrow tip)' if tip else 'open'}")
    if tip:
        check(label, abs(radii[-1] - tip) < 1e-6 and max(radii) == r, f"the gate narrows from {r} to {tip}")

# 2. Pour from the top with a tip.
for tip in (0.0, 1.0):
    label = f"ball, pour from the top, tip {tip}"
    print("##", label)
    obj = setup(ball, mold_parts='2', split_axis='X', pour_entry='TOP', pour_style='HOLE', pour_tip=tip,
                sprue_radius=2.5, vent_count=0)
    b = builder(obj)
    pt, r = b.pours[0]
    lift, chamfer = b._surface_z(pt), b._hole_chamfer(r)
    b.abort()
    obj.hide_set(False)
    trees = generate(obj, label)
    check(label, not solid(trees, pt + UP * 0.2) and not solid(trees, pt + UP * 6), "the pour hole is open")
    side = Vector((0, 1, 0))
    mouth = Vector((pt.x, pt.y, lift)) + side * (r + chamfer * 0.5) - UP * (chamfer * 0.25)
    below = Vector((pt.x, pt.y, lift)) + side * (r + 0.3) - UP * (chamfer + 0.5)
    check(label, not solid(trees, mouth) and solid(trees, below),
          f"the mouth of the straight hole has a {chamfer:.2f} mm chamfer")
    beside = pt + Vector((0, 1, 0)) * (r * 0.8) + UP * 0.3
    check(label, solid(trees, beside) == (tip > 0), f"beside the hole at the part: {'mold' if tip else 'open'}")

# 3. A vent on a thin wall: with a tip it only touches the top of the wall (no groove down its sides).
for tip in (0.0, 0.5):
    label = f"thin ring, vent tip {tip}"
    print("##", label)
    obj = setup(ring, mold_parts='2', split_axis='X', pour_entry='BOTTOM', vent_count=1, vent_radius=1.5,
                vent_tip=tip, margin=8.0)
    vent_at(obj, (0, 5.3, 8))
    trees = generate(obj, label)
    top = Vector((0, 5.3, 5.0))
    check(label, not solid(trees, top + UP * 0.2) and not solid(trees, top + UP * 4), "the vent is open")
    sides = [top + Vector((0, d, -0.6)) for d in (-1.1, 1.1)]
    check(label, all(solid(trees, q) for q in sides) == (tip > 0),
          f"beside the wall below its top: {'mold' if tip else 'open (the old groove)'}")

# 4. A vent next to a tall wall: leaning away keeps the vent off the wall.
for route in ('STRAIGHT', 'AWAY'):
    label = f"step, vent {route.lower()}"
    print("##", label)
    obj = setup(step, mold_parts='2', split_axis='Y', pour_entry='TOP', vent_count=1, vent_radius=1.5,
                vent_tip=0.5, vent_route=route, margin=10.0)
    vent_at(obj, (3.0, 0, 25))
    trees = generate(obj, label)
    check(label, not solid(trees, Vector((3.0, 0, 5.2))), "the vent touches the step")
    wall = Vector((3.6, 0, 13))  # 0.4 mm off the wall's face, half way up
    check(label, solid(trees, wall) == (route == 'AWAY'),
          f"along the wall: {'mold (leans away)' if route == 'AWAY' else 'open (runs beside it)'}")
    # Picture of the cut face of the Y+ part (tips_step_<route>.png): the vent runs in it.
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'
    sc.render.resolution_x, sc.render.resolution_y = 600, 600
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.location = (30, -75, 40)
    cam.rotation_euler = (Vector((0, 0, 12)) - cam.location).to_track_quat('-Z', 'Y').to_euler()
    for o in bpy.data.objects:
        o.hide_render = o is not cam and not o.name.endswith("_Mold_Y+")
    sc.render.filepath = __import__("os").path.join(OUT, f"tips_step_{route.lower()}.png")
    bpy.ops.render.render(write_still=True)
    if route == 'AWAY':
        b = builder(obj)
        axis, _radii = b._vent_axis(b.vents[0][0], b.vents[0][1], b._surface_z(b.vents[0][0]) + 1)
        b.abort()
        print("     vent axis", [tuple(round(c, 2) for c in q) for q in axis])
        check(label, axis[-1].x < 4 - 1.5 - 0.5 and abs(axis[-1].y) < 1e-6,
              "it leans along the split plane, away from the wall")
        check(label, not any(solid(trees, q) for q in axis[1:]), "the leaning vent is open all along")

# 5. 2-part silicone mold: the rods on the printed master narrow to the tip.
for tip in (0.0, 0.8):
    label = f"ball, 2-part silicone, tips {tip}"
    print("##", label)
    obj = setup(ball, mold_type='SILICONE_TWO_PART', pour_tip=tip, vent_tip=tip, sprue_radius=2.5,
                include_master=True)
    b = builder(obj)
    pt, r = b.pours[0]
    print("     sprue rod profile", [(round(z, 2), round(rr, 2)) for z, rr in b._sprue_profile(pt, r)], "at", pt)
    b.abort()
    obj.hide_set(False)
    trees = generate(obj, label)
    rod ={k: v for k, v in trees.items() if k.endswith("_Master_Sprue")}
    beside = pt + Vector((0, 1, 0)) * (r * 0.8) + UP * 0.3
    check(label, solid(rod, pt + UP * 6), "the sprue rod is on the master")
    check(label, solid(rod, beside) == (tip == 0), f"beside the rod at the master: {'rod' if not tip else 'free (tip)'}")

print("FAILURES:", "none" if not failures else "")
for f in failures:
    print("  ", f)
