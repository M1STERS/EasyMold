# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""2-part silicone mold (frame + parting board + master with sprue).
Checks: pieces closed and in one piece, parting line height, board top, keys, sprue reaching the frame top,
silicone volume, layout Parting Line, every clamping method. Renders bottom/top views."""
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


def sphere():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=20, segments=64, ring_count=32, location=(0, 0, 30))
    return bpy.context.active_object


def egg():
    # Widest low down: a sphere squashed at the bottom half... simpler: cone + sphere
    bpy.ops.mesh.primitive_uv_sphere_add(radius=15, segments=64, ring_count=32, location=(0, 0, 20))
    obj = bpy.context.active_object
    for v in obj.data.vertices:
        if v.co.z > 0:
            v.co.z *= 1.8
    return obj


def monkey():
    bpy.ops.mesh.primitive_monkey_add(size=40, location=(0, 0, 20))
    bpy.context.active_object.modifiers.new("s", 'SUBSURF').levels = 2
    return bpy.context.active_object


def candle():
    bpy.ops.mesh.primitive_cylinder_add(radius=15, depth=40, location=(5, 3, 20))
    return bpy.context.active_object


def hollow_cup():
    # Open-top cup: 30 mm across, 40 high, 3 mm walls and floor.
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=15, depth=40, location=(0, 0, 20))
    cup = bpy.context.active_object
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=12, depth=40, location=(0, 0, 23))
    hole = bpy.context.active_object
    m = cup.modifiers.new("b", 'BOOLEAN'); m.object = hole; m.operation = 'DIFFERENCE'
    bpy.context.view_layer.objects.active = cup
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(hole)
    return cup


def stats(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    seen, count = set(), 0
    for f in bm.faces:
        if f in seen:
            continue
        count += 1
        stack = [f]; seen.add(f)
        while stack:
            for e in stack.pop().edges:
                for g in e.link_faces:
                    if g not in seen:
                        seen.add(g); stack.append(g)
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    vol = bm.calc_volume(signed=False)
    bm.free()
    return count, nm, vol


def zrange(obj):
    zs = [(obj.matrix_world @ v.co).z for v in obj.data.vertices]
    return min(zs), max(zs)


def render(label, coll, views):
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'; sc.display.shading.color_type = 'MATERIAL'
    sc.display.shading.show_cavity = True
    sc.render.resolution_x, sc.render.resolution_y = 800, 600
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam")); cam.data.lens = 35
    sc.collection.objects.link(cam)
    sc.camera = cam
    for o in bpy.data.objects:
        o.hide_render = o.name not in coll.objects or "preview" in o.name or o.name.endswith("_Master")
    for name, (loc, target) in views.items():
        cam.location = loc
        cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
        sc.render.filepath = f"{OUT}\\two_{label}_{name}.png"
        bpy.ops.render.render(write_still=True)


def run(label, make, layout=None, views=None, **settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = make()
    p = bpy.context.scene.easymold_props
    p.mold_type = 'SILICONE_TWO_PART'
    for k, v in settings.items():
        setattr(p, k, v)
    if layout is not None:
        bpy.ops.object.easymold_edit_layout(reset=True)
        layout(utils.find_layout(obj))
    r = bpy.ops.object.easymold_generate()
    print(f"## {label}: {r}")
    coll = utils.find_result(obj)
    if coll is None:
        check(label, False, "no result")
        return None, None
    by = {}
    for o in sorted(coll.objects, key=lambda o: o.name):
        n, nm, vol = stats(o)
        lo, hi = zrange(o)
        print(f"     {o.name:34s} islands={n:3d} nonmanifold={nm:4d} vol={vol:9.0f} z={lo:7.2f}..{hi:7.2f}")
        if "Master" not in o.name or "Sprue" in o.name:
            # Suzanne's eyes are separate islands; the sprue alone is one rod per hole.
            one = n == 1 or "Suzanne" in o.name or "Sprue" in o.name and "Master" not in o.name
            check(label, nm == 0 and one, f"{o.name} islands={n} nonmanifold={nm}")
        by[o.name.split("_", 1)[1] if "_" in o.name else o.name] = o
    print(f"     silicone {coll['easymold_volume'] / 1000:.1f} ml, cast {coll['easymold_cast_volume'] / 1000:.1f} ml")
    if views:
        render(label, coll, views)
    return coll, by


# Sphere: parting line at the equator, 4 keys, master + sprue reaches the top of the frame.
coll, by = run("sphere", sphere, views={"side": ((110, -130, 90), (0, 0, 30)), "top": ((0, -1, 160), (0, 0, 30))},
               explode=True)
if coll:
    board_top = zrange(by["Board"])[1]
    check("sphere parting", abs(board_top - 30) < 1.0, f"board top {board_top:.2f} (equator 30)")
    frame = by["Frame"]
    p = bpy.context.scene.easymold_props
    s, lip = p.silicone_thickness, p.lip_height
    fz = zrange(frame)
    height = fz[1] - fz[0]
    check("sphere frame", abs(height - (40 + 2 * s + 2 * lip)) < 0.01, f"frame height {height:.2f}")
    sprue_top = zrange(by["Master_Sprue"])[1]
    check("sphere sprue", abs(sprue_top - (50 + s + lip)) < 0.01, f"sprue top {sprue_top:.2f}")
    top = by["Silicone_Top (preview)"]
    bottom = by["Silicone_Bottom (preview)"]
    block = (40 + 2 * s) ** 2 * (40 + 2 * s) - (4 - math.pi) * s ** 2 * (40 + 2 * s)  # rounded corners r = s
    master = stats(by["Master"])[2]
    vol = coll["easymold_volume"]
    check("sphere volume", block - master - 3000 < vol < block - master, f"{vol:.0f} vs block-master {block - master:.0f}")
    upper_vol, lower_vol = stats(top)[2], stats(bottom)[2]
    check("sphere halves", abs(upper_vol - lower_vol) / lower_vol < 0.08, f"top {upper_vol:.0f} bottom {lower_vol:.0f}")

# Egg (wide at the bottom): the widest section is low.
coll, by = run("egg", egg)
if coll:
    board_top = zrange(by["Board"])[1]
    check("egg parting", abs(board_top - 20) < 1.5, f"board top {board_top:.2f} (widest at 20)")

# Fixed height, no sprue funnel, tapered keys, only the sprue printed (glued onto the original).
coll, by = run("candle_manual", candle, parting_auto=False, parting_height=25.0, pour_style='HOLE',
               key_shape='TAPERED', include_master=False, clamp_method='CLIP')
if coll:
    check("manual parting", abs(zrange(by["Board"])[1] - 10) < 0.05, f"board top {zrange(by['Board'])[1]:.2f}")
    check("sprue only", "Sprue" in by and "Master_Sprue" not in by, str(list(by)))

# Hollow cup: the inside below the parting line belongs to the top half, not to the board.
coll, by = run("hollow_cup", hollow_cup, views={"board": ((40, -70, 110), (0, 0, 10))}, parting_auto=False,
               parting_height=50.0)
if coll:
    board_vol = stats(by["Board"])[2]
    top_vol = stats(by["Silicone_Top (preview)"])[2]
    inside = math.pi * 12 ** 2 * 17  # cup inside, floor (z 3) up to the parting line (z 20)
    check("cup board", stats(by["Board"])[0] == 1, "board in one piece")
    check("cup top half fills the inside", top_vol > inside, f"top half {top_vol:.0f} > inside below the line {inside:.0f}")
    print("     board volume", round(board_vol))

# Parting line moved in the layout.
def move_parting(layout):
    plane = next(o for o in layout.objects if o.get("easymold_role") == 'PARTING')
    plane.location.z = 12.0
coll, by = run("monkey_layout", monkey, layout=move_parting,
               views={"side": ((120, -140, 90), (0, 0, 20))})
if coll:
    check("layout parting", abs(zrange(by["Board"])[1] - 12) < 0.05, f"board top {zrange(by['Board'])[1]:.2f}")

for method in ('SCREW', 'BAND', 'NONE'):
    run(f"sphere_{method}", sphere, clamp_method=method)

print("FAILURES:", failures or "none")
