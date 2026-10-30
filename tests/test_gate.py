# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pour entry from below on the parting line (direct mold): the sprue goes down a split plane beside the part and
a gate enters it low on its side. Checks the channel is open (sprue and gate empty in every block piece), it
lands low on the part, keys stay clear, radius reduced to fit, fallbacks (Z split, Top), layout marker moved.
Renders gate_*.png."""
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


def check(label, ok, msg):
    print(f"   {'ok  ' if ok else 'FAIL'} {label}: {msg}")
    if not ok:
        failures.append(f"{label}: {msg}")


def candle():
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=12, depth=30, location=(0, 0, 15))
    return bpy.context.active_object


def ball():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=12, segments=48, ring_count=24, location=(0, 0, 12))
    return bpy.context.active_object


def setup(make, **settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = make()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    p = bpy.context.scene.easymold_props
    p.mold_type, p.explode = 'DIRECT', False
    for k, v in settings.items():
        setattr(p, k, v)
    return obj


def plan(obj):
    """The pour holes and gates the builder would make (without building)."""
    props = bpy.context.scene.easymold_props
    b = utils.MoldBuilder(obj, props, extras=utils.tray_members(obj, bpy.context.selected_objects, props))
    b.prepare(use_layout=True)
    b.split_axes()
    b._place_spec(b.axes)
    out = (list(b.pours), list(b.gates), list(b.keys), b.bmin.copy(), b.bmax.copy(), b._block_bounds(),
           list(b.notes), list(b.warnings))
    b.abort()
    for o in [obj] + b.extras:
        o.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return out


def bvh(o):
    m = o.data.copy()
    m.transform(o.matrix_world)
    t = BVHTree.FromPolygons([v.co.copy() for v in m.vertices], [tuple(f.vertices) for f in m.polygons])
    bpy.data.meshes.remove(m)
    return t


def solid_at(pieces, pt):
    return any(utils._inside(t, pt) for t in pieces)


def stats(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    n = len(utils.face_islands(bm))
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    return n, nm


def generate(label):
    try:
        r = bpy.ops.object.easymold_generate()
    except RuntimeError as e:
        print(f"## {label}: ERROR", e)
        return None
    print(f"## {label}:", r)
    obj = bpy.context.view_layer.objects.active
    return utils.find_result(utils.resolve_source(obj))


def check_channel(label, coll, gates, keys):
    pieces = [o for o in coll.objects if "_Mold" in o.name]
    for o in sorted(coll.objects, key=lambda o: o.name):
        if "preview" in o.name or o.name.endswith("_Master"):
            continue
        n, nm = stats(o)
        check(label, n == 1 and nm == 0, f"{o.name} islands={n} nonmanifold={nm}")
    trees = [bvh(o) for o in pieces]
    for sprue, end, r, _path in gates:
        # Points along the sprue axis (up to just under the block top) and the gate axis must be empty.
        top = max((o.matrix_world @ v.co).z for o in pieces for v in o.data.vertices)
        along = [sprue.lerp(Vector((sprue.x, sprue.y, top - 0.5)), k / 10) for k in range(11)]
        along += [sprue.lerp(end, k / 10) for k in range(11)]
        full = [q for q in along if solid_at(trees, q)]
        check(label, not full, f"pour channel open ({len(full)} blocked points)")
        for _axis, c, kr in keys:
            d = utils._segment_distance(c, sprue, Vector((sprue.x, sprue.y, top))) - r - kr
            d = min(d, utils._segment_distance(c, sprue, end) - r - kr)
            check(label, d > 0.5, f"key at {tuple(round(x, 1) for x in c)} clear of the channel ({d:.1f} mm)")


def render(label, coll, loc, target):
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'; sc.display.shading.color_type = 'MATERIAL'
    sc.display.shading.show_cavity = True
    sc.render.resolution_x, sc.render.resolution_y = 800, 600
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam")); cam.data.lens = 35
    sc.collection.objects.link(cam); sc.camera = cam
    piece = next(o for o in coll.objects if o.name.endswith("_X+") or o.name.endswith("_X+_Y-"))
    for o in bpy.data.objects:
        o.hide_render = o != piece
    cam.location = loc
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    sc.render.filepath = f"{OUT}\\gate_{label}.png"
    bpy.ops.render.render(write_still=True)


# 1. Candle, 2 parts X: the sprue on the X plane beside the candle, the gate low on its side.
obj = setup(candle, mold_parts='2', split_axis='X', clamp_method='SCREW')
pours, gates, keys, bmin, bmax, (lo, hi), notes, warnings = plan(obj)
check("candle", len(gates) == 1, f"{len(gates)} gate(s)")
if gates:
    sprue, end, r = gates[0][:3]
    check("candle on the plane", abs(sprue.x) < 1e-6 and abs(end.x) < 1e-6, f"sprue {tuple(sprue)}, end {tuple(end)}")
    check("candle low", end.z < bmin.z + 0.2 * (bmax.z - bmin.z), f"gate at z {end.z:.2f}")
    check("candle beside", lo.y < sprue.y < bmin.y or bmax.y < sprue.y < hi.y, f"sprue y {sprue.y:.2f}")
    check("candle radius", abs(r - bpy.context.scene.easymold_props.sprue_radius) < 1e-6, f"r {r}")
coll = generate("candle")
if coll:
    check_channel("candle", coll, gates, keys)
    render("candle", coll, (-55, -45, 45), (0, -12, 12))

# 2. 4 parts, clips: still one gate, on a split plane.
obj = setup(candle, mold_parts='4', split_axis='X', clamp_method='CLIP')
pours, gates, keys, *_ = plan(obj)
check("candle4", len(gates) == 1, f"{len(gates)} gate(s)")
coll = generate("candle4")
if coll:
    check_channel("candle4", coll, gates, keys)

# 3. A thin margin: the radius shrinks to fit, with a warning.
obj = setup(ball, mold_parts='2', split_axis='Y', margin=5.0, sprue_radius=3.0)
pours, gates, keys, bmin, bmax, _b, notes, warnings = plan(obj)
check("thin", gates and gates[0][2] < 3.0 and any("radius" in w for w in warnings), f"gates {gates}, warnings {warnings}")
coll = generate("thin")
if coll:
    check_channel("thin", coll, gates, keys)

# 4. Split along Z: no vertical plane, poured from the top.
obj = setup(candle, mold_parts='2', split_axis='Z')
pours, gates, *_ = plan(obj)
check("splitZ", not gates and pours and pours[0][0].z > 29, f"pours {pours}")
coll = generate("splitZ")

# 5. Top entry as before.
obj = setup(candle, mold_parts='2', split_axis='X', pour_entry='TOP')
pours, gates, *_ = plan(obj)
check("top", not gates and pours and pours[0][0].z > 29, f"pours {[tuple(round(c, 1) for c in p) for p, _r in pours]}")

# 6. Layout: the Pour marker sits where the gate enters; moved up, the gate follows.
obj = setup(candle, mold_parts='2', split_axis='X')
print("## layout:", bpy.ops.object.easymold_edit_layout(reset=True))
lay = utils.find_layout(obj)
marker = next(o for o in lay.objects if o.get(utils.ROLE) == 'POUR')
check("layout marker", marker.location.z < 8 and abs(marker.location.x) < 1e-6,
      f"marker at {tuple(round(c, 2) for c in marker.location)}")
marker.location.z = 12.0
bpy.context.view_layer.update()
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
pours, gates, keys, *_ = plan(obj)
check("layout moved", gates and abs(gates[0][0].z - 12.0) < 1e-3, f"gates {gates}")
coll = generate("layout_moved")
if coll:
    check_channel("layout_moved", coll, gates, keys)

# 7. Two candles in one mold: a gate each.
bpy.ops.wm.read_factory_settings(use_empty=True)
a = candle(); b = candle(); b.location.x = 50
for o in (a, b):
    o.select_set(True)
bpy.context.view_layer.objects.active = a
p = bpy.context.scene.easymold_props
p.mold_type, p.mold_parts, p.split_axis, p.explode = 'DIRECT', '2', 'X', False
pours, gates, keys, *_ = plan(a)
b.select_set(True)
check("tray", len(gates) == 2, f"{len(gates)} gate(s)")
coll = generate("tray")
if coll:
    check_channel("tray", coll, gates, keys)
print("FAILURES:", failures or "none")
