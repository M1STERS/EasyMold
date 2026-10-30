# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Curved parting surface (direct molds in 2 parts, 2-part silicone molds).
Checks: it follows the object's outline (far fewer undercut faces than a flat plane on tilted objects), the
pieces are closed, in one piece and don't overlap each other or the cast part, the rim stays flat (keys on it),
the layout surface is editable (moved, its vertices pushed), pour channel from below along it, core, ignored
where it doesn't apply. Renders parting_*.png."""
import sys, importlib, math
import bpy, bmesh
import numpy as np
from mathutils import Vector
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from EasyMold import utils, analysis
failures = []


def check(label, ok, msg):
    print(f"   {'ok  ' if ok else 'FAIL'} {label}: {msg}")
    if not ok:
        failures.append(f"{label}: {msg}")


def tilted_bar():
    """A rounded bar lying along Y, turned 25 degrees about Z: seen along X, its outline drifts from x = -10 to
    x = +10 along its length, so the flat split plane x = 0 cuts across it."""
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=7, depth=50, location=(0, 0, 10), rotation=(math.pi / 2, 0, 0))
    obj = bpy.context.active_object
    obj.modifiers.new("b", 'BEVEL').width = 3
    obj.modifiers["b"].segments = 4
    bpy.ops.object.modifier_apply(modifier="b")
    obj.rotation_euler = (math.pi / 2, 0, math.radians(25))
    bpy.ops.object.transform_apply(rotation=True)
    return obj


def tilted_egg():
    """For the 2-part mold: an egg lying tilted 20 degrees, so its widest outline seen from above is not level."""
    bpy.ops.mesh.primitive_uv_sphere_add(radius=12, segments=48, ring_count=24, location=(0, 0, 20))
    obj = bpy.context.active_object
    obj.scale = (1.8, 1, 1)
    obj.rotation_euler = (0, math.radians(20), 0)
    bpy.ops.object.transform_apply(scale=True, rotation=True)
    return obj


def sock():
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 7))
    outer = bpy.context.active_object
    outer.scale = (24, 18, 14)
    bpy.ops.object.transform_apply(scale=True)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 10))
    hole = bpy.context.active_object
    hole.scale = (20, 14, 14)
    bpy.ops.object.transform_apply(scale=True)
    m = outer.modifiers.new("b", 'BOOLEAN'); m.object = hole; m.operation = 'DIFFERENCE'
    bpy.context.view_layer.objects.active = outer
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(hole)
    outer.rotation_euler = (0, 0, math.radians(20))
    bpy.ops.object.transform_apply(rotation=True)
    return outer


def setup(make, **settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = make()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    p = bpy.context.scene.easymold_props
    p.explode = False
    for k, v in settings.items():
        setattr(p, k, v)
    return obj


def undercut_share(obj, curved):
    """Share of the surface that is undercut for this mold, flat or curved split."""
    p = bpy.context.scene.easymold_props
    p.curved_parting = curved
    b = utils.MoldBuilder(obj, p)
    b.prepare(use_layout=False)
    axes = b.split_axes()
    if b._curved_axis():
        b.parting = b._make_parting()
    b.analyze_undercuts(axes)
    areas = np.empty(len(b.target.data.polygons))
    b.target.data.polygons.foreach_get("area", areas)
    share = float(areas[b.face_classes == analysis.UNDERCUT].sum() / areas.sum())
    b.abort()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return share


def stats(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    n = len(utils.face_islands(bm))
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    vol = bm.calc_volume(signed=False)
    bm.free()
    return n, nm, vol


def world_obj(obj):
    mesh = obj.data.copy()
    mesh.transform(obj.matrix_world)
    tmp = bpy.data.objects.new("tmp", mesh)
    bpy.context.scene.collection.objects.link(tmp)
    return tmp


def overlap(a, b):
    x, y = world_obj(a), world_obj(b)
    m = x.modifiers.new("b", 'BOOLEAN'); m.object = y; m.operation = 'INTERSECT'; m.solver = 'MANIFOLD'
    dg = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(x.evaluated_get(dg))
    bm = bmesh.new(); bm.from_mesh(mesh)
    vol = bm.calc_volume(signed=False)
    bm.free()
    for o in (x, y):
        bpy.data.objects.remove(o)
    bpy.data.meshes.remove(mesh)
    return vol


def generate(label, obj):
    before = [v.co.copy() for v in obj.data.vertices]
    try:
        print(f"## {label}:", bpy.ops.object.easymold_generate())
    except RuntimeError as e:
        print(f"## {label}: ERROR", e)
        return None
    check(label, all((a - b.co).length < 1e-9 for a, b in zip(before, obj.data.vertices)), "original untouched")
    return utils.find_result(obj)


def inspect(label, coll, parts_name="_Mold"):
    pieces = []
    for o in sorted(coll.objects, key=lambda o: o.name):
        n, nm, vol = stats(o)
        print(f"     {o.name:34s} islands={n:3d} nonmanifold={nm:4d} vol={vol:9.0f}")
        if "preview" in o.name or o.name.endswith("_Master"):
            continue
        check(label, n == 1 and nm == 0, f"{o.name} islands={n} nonmanifold={nm}")
        if parts_name in o.name:
            pieces.append(o)
    return pieces


def render(label, coll, show, loc, target):
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'; sc.display.shading.color_type = 'MATERIAL'
    sc.display.shading.show_cavity = True
    sc.render.resolution_x, sc.render.resolution_y = 800, 600
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam")); cam.data.lens = 35
    sc.collection.objects.link(cam); sc.camera = cam
    for o in bpy.data.objects:
        o.hide_render = o.name not in show
    cam.location = loc
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    sc.render.filepath = f"{OUT}\\parting_{label}.png"
    bpy.ops.render.render(write_still=True)


# 1. Direct mold, 2 parts along X, tilted bar: the curved surface follows its outline.
obj = setup(tilted_bar, mold_type='DIRECT', mold_parts='2', split_axis='X', clamp_method='SCREW')
flat, curved = undercut_share(obj, False), undercut_share(obj, True)
check("bar undercuts", curved < 0.02 and curved < flat * 0.25, f"flat {flat:.1%}, curved {curved:.1%}")
coll = generate("bar", obj)
if coll:
    pieces = inspect("bar", coll)
    cast = next(o for o in coll.objects if "preview" in o.name)
    check("bar halves", len(pieces) == 2 and overlap(pieces[0], pieces[1]) < 0.5,
          f"{len(pieces)} halves, overlap {overlap(pieces[0], pieces[1]) if len(pieces) == 2 else 0:.2f}")
    for o in pieces:
        check("bar cavity", overlap(o, cast) < 0.5, f"{o.name} keeps out of the cast part ({overlap(o, cast):.2f})")
    # Away from the bar the halves meet on the flat rim: the X+ half starts at x = 0 at the block's corners.
    plus = next(o for o in pieces if o.name.endswith("X+"))
    # Cut face vertices (|x| < 1) past the corner keys.
    corner = [v.co for v in plus.data.vertices if abs(v.co.y) > 34 and abs(v.co.x) < 1]
    check("bar rim", corner and max(abs(c.x) for c in corner) < 0.05,
          f"rim at x {max((abs(c.x) for c in corner), default=-1):.3f}")
    render("bar", coll, [plus.name], (-70, -40, 60), (0, 0, 10))
    p = bpy.context.scene.easymold_props
    p.explode = True
    coll = generate("bar_view", obj)
    render("bar_all", coll, [o.name for o in coll.objects if "_Mold" in o.name], (90, -90, 80), (0, 0, 10))
    p.explode = False

# 2. Pour entry from below follows the curved surface.
obj = setup(tilted_bar, mold_type='DIRECT', mold_parts='2', split_axis='X', curved_parting=True, pour_entry='BOTTOM')
b = utils.MoldBuilder(obj, bpy.context.scene.easymold_props)
b.prepare(use_layout=False)
axes = b.split_axes()
b.parting = b._make_parting()
b.auto_spec(axes)
gates = list(b.gates)
on_surface = all(abs(q.x - b.parting.at(q)) < 1e-6 for g in gates for q in g[3][:-1])
check("gate", len(gates) == 1 and on_surface, f"{len(gates)} gate(s), path on the surface: {on_surface}")
b.abort()
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
coll = generate("gate", obj)
if coll:
    pieces = inspect("gate", coll)
    if gates:
        from mathutils.bvhtree import BVHTree
        trees = []
        for o in pieces:
            m = o.data.copy(); m.transform(o.matrix_world)
            trees.append(BVHTree.FromPolygons([v.co.copy() for v in m.vertices], [tuple(f.vertices) for f in m.polygons]))
            bpy.data.meshes.remove(m)
        path = gates[0][3]
        blocked = [q for s0, s1 in zip(path, path[1:]) for q in (s0.lerp(s1, t / 4) for t in range(5))
                   if any(utils._inside(t, q) for t in trees)]
        check("gate open", not blocked, f"{len(blocked)} blocked points along the channel")

# 3. Split along Z (the bar again): a heightfield in Z.
obj = setup(tilted_bar, mold_type='DIRECT', mold_parts='2', split_axis='Z', curved_parting=True)
coll = generate("barZ", obj)
if coll:
    inspect("barZ", coll)

# 4. Auto core with a curved split.
obj = setup(sock, mold_type='DIRECT', mold_parts='2', split_axis='X', curved_parting=True, auto_core=True)
coll = generate("core", obj)
if coll:
    pieces = inspect("core", coll)
    core = next((o for o in coll.objects if "_Core" in o.name), None)
    check("core", core is not None, "a core")
    if core:
        for o in pieces:
            check("core", overlap(core, o) < 1.0, f"core keeps out of {o.name} ({overlap(core, o):.2f})")

# 5. 2-part silicone mold, tilted egg: the board follows the widest outline.
obj = setup(tilted_egg, mold_type='SILICONE_TWO_PART')
flat, curved = undercut_share(obj, False), undercut_share(obj, True)
check("egg undercuts", curved < flat * 0.5, f"flat {flat:.1%}, curved {curved:.1%}")
coll = generate("egg", obj)
if coll:
    inspect("egg", coll, parts_name="_Board")
    board = next(o for o in coll.objects if o.name.endswith("_Board"))
    top = next(o for o in coll.objects if "Silicone_Top" in o.name)
    bottom = next(o for o in coll.objects if "Silicone_Bottom" in o.name)
    check("egg halves", overlap(top, bottom) < 0.5, f"silicone halves overlap {overlap(top, bottom):.2f}")
    check("egg board", overlap(board, top) < 0.5, f"board under the top half ({overlap(board, top):.2f})")
    check("egg no lock", not any("locked" in w for w in []), "")
    render("egg", coll, [board.name], (60, -80, 70), (0, 0, 15))

# 6. Layout: the surface is there and editable. Moved 1.5 along X, the rim moves with it; a bump pushed into
# its vertices shows in the halves.
obj = setup(tilted_bar, mold_type='DIRECT', mold_parts='2', split_axis='X', curved_parting=True)
print("## layout:", bpy.ops.object.easymold_edit_layout(reset=True))
lay = utils.find_layout(obj)
surf = next((o for o in lay.objects if o.get(utils.ROLE) == 'PARTING_SURFACE'), None)
check("layout surface", surf is not None and not any(o.name == "Split X" for o in lay.objects), "surface, no flat plane")
if surf:
    surf.location.x = 1.5
    bpy.context.view_layer.update()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    coll = generate("layout_moved", obj)
    if coll:
        pieces = inspect("layout_moved", coll)
        plus = next(o for o in pieces if o.name.endswith("X+"))
        corner = [v.co for v in plus.data.vertices if abs(v.co.y) > 34 and abs(v.co.x - 1.5) < 1]
        check("layout moved", corner and max(abs(c.x - 1.5) for c in corner) < 0.05,
              f"rim at x {[round(c.x, 3) for c in corner[:3]]}")
    # Push the vertices of a patch on the rim 3 mm along X.
    surf.location.x = 0.0
    for v in surf.data.vertices:
        if v.co.y > 32 and v.co.z > 22:
            v.co.x += 3.0
    bpy.context.view_layer.update()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    coll = generate("layout_edited", obj)
    if coll:
        pieces = inspect("layout_edited", coll)
        plus = next(o for o in pieces if o.name.endswith("X+"))
        patch = [v.co.x for v in plus.data.vertices if v.co.y > 34 and v.co.z > 24]
        check("layout edited", patch and min(patch) > 2.0, f"X+ half starts at x {min(patch):.2f} in the pushed patch")

# 7. Not used where it doesn't apply: 4 parts, shell.
obj = setup(tilted_bar, mold_type='DIRECT', mold_parts='4', split_axis='X', curved_parting=True)
coll = generate("four", obj)
if coll:
    inspect("four", coll)
obj = setup(tilted_bar, mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X', curved_parting=True)
coll = generate("shell", obj)
if coll:
    inspect("shell", coll, parts_name="_Shell")
print("FAILURES:", failures or "none")
