# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Automatic cores for hollow parts in a direct mold.
Checks: which hollows get a core, the prints flush with the block, key pegs, nothing overlapping (core, block
parts, cast part), pour hole moved off the core, pieces closed and in one piece, layout core (kept, moved,
deleted), several objects, errors. Renders core_*.png."""
import sys, importlib, math
import bpy, bmesh
from mathutils import Vector
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from EasyMold import utils, export
failures = []


def check(label, ok, msg):
    print(f"   {'ok  ' if ok else 'FAIL'} {label}: {msg}")
    if not ok:
        failures.append(f"{label}: {msg}")


def apply_boolean(obj, cutter, op='DIFFERENCE'):
    m = obj.modifiers.new("b", 'BOOLEAN'); m.object = cutter; m.operation = op
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(cutter)


def box(size, loc):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    obj = bpy.context.active_object
    obj.scale = size
    bpy.ops.object.transform_apply(scale=True)
    return obj


def sock(x=0.0):
    """Like a hot end silicone sock: a box 24 x 18 x 14 open at the top (pocket 20 x 14, 11 deep) with a nozzle
    hole in its 3 mm floor."""
    outer = box((24, 18, 14), (x, 0, 7))
    apply_boolean(outer, box((20, 14, 14), (x, 0, 10)))
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=3, depth=10, location=(x, 0, 0))
    apply_boolean(outer, bpy.context.active_object)
    outer.name = "Sock"
    return outer


def tube():
    """Open at both ends, lying along Y."""
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=10, depth=40, location=(0, 0, 10), rotation=(math.pi / 2, 0, 0))
    obj = bpy.context.active_object
    bpy.ops.object.transform_apply(rotation=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=6, depth=50, location=(0, 0, 10), rotation=(math.pi / 2, 0, 0))
    apply_boolean(obj, bpy.context.active_object)
    obj.name = "Tube"
    return obj


def ball():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=15, segments=48, ring_count=24, location=(0, 0, 15))
    return bpy.context.active_object


def stats(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    count = len(utils.face_islands(bm))
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    vol = bm.calc_volume(signed=False)
    bm.free()
    return count, nm, vol


def world_obj(obj):
    mesh = obj.data.copy()
    mesh.transform(obj.matrix_world)
    tmp = bpy.data.objects.new("tmp", mesh)
    bpy.context.scene.collection.objects.link(tmp)
    return tmp


def overlap(a, b):
    """Volume shared by two objects."""
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


def render(label, coll, loc, target):
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'; sc.display.shading.color_type = 'MATERIAL'
    sc.display.shading.show_cavity = True
    sc.render.resolution_x, sc.render.resolution_y = 800, 600
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam")); cam.data.lens = 35
    sc.collection.objects.link(cam); sc.camera = cam
    for o in bpy.data.objects:
        o.hide_render = o.name not in coll.objects or "preview" in o.name or o.name.endswith("_Master")
    cam.location = loc
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    sc.render.filepath = f"{OUT}\\core_{label}.png"
    bpy.ops.render.render(write_still=True)


def setup(make, **settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    made = make()
    objs = made if isinstance(made, list) else [made]
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    p = bpy.context.scene.easymold_props
    p.mold_type, p.auto_core, p.explode = 'DIRECT', True, False
    for k, v in settings.items():
        setattr(p, k, v)
    return objs


def generate(label, objs):
    before = [[v.co.copy() for v in o.data.vertices] for o in objs]
    try:
        print(f"## {label}:", bpy.ops.object.easymold_generate())
    except RuntimeError as e:
        print(f"## {label}: ERROR", e)
        return None
    for o, verts in zip(objs, before):
        check(label, all((a - b.co).length < 1e-9 for a, b in zip(verts, o.data.vertices)), f"{o.name} untouched")
    return utils.find_result(objs[0])


def inspect(label, coll, cores):
    """Every piece closed and in one piece; cores as expected; nothing overlaps."""
    parts, core_objs = [], []
    for o in sorted(coll.objects, key=lambda o: o.name):
        n, nm, vol = stats(o)
        print(f"     {o.name:28s} islands={n:3d} nonmanifold={nm:4d} vol={vol:9.0f}")
        if "preview" in o.name or o.name.endswith("_Master"):
            continue
        check(label, n == 1 and nm == 0, f"{o.name} islands={n} nonmanifold={nm}")
        (core_objs if "_Core" in o.name else parts).append(o)
    check(label, len(core_objs) == cores, f"{len(core_objs)} core(s), want {cores}")
    cast = next(o for o in coll.objects if "preview" in o.name)
    for c in core_objs:
        check(label, overlap(c, cast) < 1.0, f"{c.name} doesn't fill the cavity ({overlap(c, cast):.2f} mm3)")
        for part in parts:
            check(label, overlap(c, part) < 1.0, f"{c.name} doesn't overlap {part.name} ({overlap(c, part):.2f} mm3)")
        check(label, export.DOWN in c, f"{c.name} has a print orientation")
    return core_objs, parts


def block_top(coll):
    # The top face of the block (the sprue funnel rises above it): the sock's top + the margin.
    return 14 + bpy.context.scene.easymold_props.margin


# 1. Sock, 2 parts split along X: one core, print flush with the block top, two pegs (one in each part's side).
objs = setup(sock, mold_parts='2', split_axis='X')
coll = generate("sock", objs)
if coll:
    cores, parts = inspect("sock", coll, 1)
    core = cores[0]
    top = max(v.co.z for v in core.data.vertices)
    check("sock print flush", abs(top - block_top(coll)) < 1e-3, f"core top {top:.3f}, block top {block_top(coll):.3f}")
    # Volume: the pocket (20 x 14 x 11 + nozzle hole 3 mm) + print up to the block top (20.4 x 14.4 x 10), + pegs.
    vol = stats(core)[2]
    pocket = 20 * 14 * 11 + math.pi * 9 * 3
    check("sock core volume", pocket + 20 * 14 * 10 < vol < pocket + 22 * 16 * 11, f"{vol:.0f} mm3")
    _m, down = export.print_matrix(core)
    check("sock core prints on its flush end", Vector(down) == Vector((0, 0, 1)), f"down {tuple(down)}")
    # Pegs stick out of the print's sides (Y), on the split plane x = 0.
    ys = [v.co.y for v in core.data.vertices if 8 < v.co.z < 17]
    check("sock pegs", max(ys) > 9 and min(ys) < -9, f"core y range at the print {min(ys):.1f}..{max(ys):.1f}")

# Same, exploded, for a picture.
objs = setup(sock, mold_parts='2', split_axis='X', explode=True)
coll = generate("sock_view", objs)
if coll:
    render("sock", coll, (70, -90, 70), (0, 0, 8))

# 2. Tube open at both ends along Y, split along X: one core with two prints (front and back), four pegs.
objs = setup(tube, mold_parts='2', split_axis='X')
coll = generate("tube", objs)
if coll:
    cores, parts = inspect("tube", coll, 1)
    ys = [v.co.y for v in cores[0].data.vertices]
    lo_y = min((o.matrix_world @ v.co).y for o in parts for v in o.data.vertices)
    hi_y = max((o.matrix_world @ v.co).y for o in parts for v in o.data.vertices)
    check("tube prints", abs(max(ys) - hi_y) < 1e-3 and abs(min(ys) - lo_y) < 1e-3,
          f"core y {min(ys):.2f}..{max(ys):.2f}, block {lo_y:.2f}..{hi_y:.2f}")

objs = setup(tube, mold_parts='2', split_axis='X', explode=True)
coll = generate("tube_view", objs)
if coll:
    render("tube", coll, (80, -80, 60), (0, 0, 10))

# 3. Sock in 4 parts.
objs = setup(sock, mold_parts='4', split_axis='X')
coll = generate("sock4", objs)
if coll:
    inspect("sock4", coll, 1)

# 4. Sock split along Z: the print comes out of the top part, no pegs.
objs = setup(sock, mold_parts='2', split_axis='Z')
coll = generate("sockZ", objs)
if coll:
    inspect("sockZ", coll, 1)

# 5. A solid ball needs no core.
objs = setup(ball, mold_parts='2', split_axis='X')
coll = generate("ball", objs)
if coll:
    inspect("ball", coll, 0)

# 6. Two socks in one mold: a core each.
objs = setup(lambda: [sock(0), sock(40)], mold_parts='2', split_axis='X')
coll = generate("tray", objs)
if coll:
    inspect("tray", coll, 2)

# 7. Layout: the core is shown; moved a little it is used where it is; deleted, no core.
objs = setup(sock, mold_parts='2', split_axis='X')
print("## layout:", bpy.ops.object.easymold_edit_layout(reset=True))
lay = utils.find_layout(objs[0])
lcores = [o for o in lay.objects if o.get(utils.ROLE) == 'CORE']
check("layout", len(lcores) == 1 and lay.get(utils.AUTO_CORE), f"{len(lcores)} core(s) in the layout")
if lcores:
    lcores[0].location.y = 1.0
    bpy.context.view_layer.update()
    coll = generate("layout_moved", objs)
    if coll:
        cores, _parts = inspect("layout_moved", coll, 1)
        ys = [v.co.y for v in cores[0].data.vertices if v.co.z > 12]
        check("layout moved", abs((max(ys) + min(ys)) / 2 - 1.0) < 0.3, f"print center y {(max(ys) + min(ys)) / 2:.2f}")
    bpy.data.objects.remove(lcores[0])
    coll = generate("layout_deleted", objs)
    if coll:
        check("layout deleted", not any("_Core" in o.name for o in coll.objects), "no core")

# 8. Errors: 1 part.
objs = setup(sock, mold_parts='1')
coll = generate("one_part", objs)
check("one_part", coll is None and not objs[0].hide_get(), "error, object visible again")

# 9. Off by default: the old behavior.
objs = setup(sock, mold_parts='2', split_axis='X', auto_core=False)
coll = generate("off", objs)
check("off", coll is not None and not any("_Core" in o.name for o in coll.objects), "no core")
print("FAILURES:", failures or "none")
