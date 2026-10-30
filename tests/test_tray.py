# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Item 12: several objects in one mold (tray). Every mold type, arrangement, per-object pour holes,
originals untouched and hidden, regenerate from a result part. Renders tray_*.png."""
import sys, importlib, math
import bpy, bmesh
from mathutils import Vector
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


def cyl(r, h, loc):
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=r, depth=h, location=(loc[0], loc[1], loc[2] + h / 2))
    return bpy.context.active_object


def ball(r, loc):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, segments=48, ring_count=24, location=loc)
    return bpy.context.active_object


def cube(size, loc):
    bpy.ops.mesh.primitive_cube_add(size=size, location=(loc[0], loc[1], loc[2] + size / 2))
    return bpy.context.active_object


def islands(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    seen, out = set(), []
    for f in bm.faces:
        if f in seen:
            continue
        stack, comp = [f], []
        seen.add(f)
        while stack:
            cur = stack.pop(); comp.append(cur)
            for e in cur.edges:
                for g in e.link_faces:
                    if g not in seen:
                        seen.add(g); stack.append(g)
        out.append([v.co.copy() for face in comp for v in face.verts])
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    vol = bm.calc_volume(signed=False)
    bm.free()
    return out, nm, vol


def closest(a, b):
    """Closest distance between two vertex clouds (dense meshes: about the distance between the surfaces)."""
    from mathutils import kdtree
    tree = kdtree.KDTree(len(a))
    for n, v in enumerate(a):
        tree.insert(v, n)
    tree.balance()
    return min(tree.find(v)[2] for v in b)


def scene(make):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    objs = make()
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    return objs


def run(label, make, layout=False, shot=None, **settings):
    objs = scene(make)
    before = [[v.co.copy() for v in o.data.vertices] for o in objs]
    p = bpy.context.scene.easymold_props
    for k, v in settings.items():
        setattr(p, k, v)
    if layout:
        print(f"## {label} layout: {bpy.ops.object.easymold_edit_layout(reset=True)}")
        lay = utils.find_layout(objs[0])
        pours = [o for o in lay.objects if o.get("easymold_role") == 'POUR']
        check(label, len(pours) == len(objs), f"{len(pours)} pour markers for {len(objs)} objects")
    r = bpy.ops.object.easymold_generate()
    print(f"## {label}: {r}")
    coll = utils.find_result(objs[0])
    if coll is None:
        return objs, None, {}
    check(label, len(utils.tray_extras(coll)) == len(objs) - 1, "extras stored")
    for o, verts in zip(objs, before):
        check(label, all((a - b.co).length < 1e-9 for a, b in zip(verts, o.data.vertices)) and o.hide_get(),
              f"{o.name} untouched and hidden")
    by = {}
    for o in sorted(coll.objects, key=lambda o: o.name):
        isl, nm, vol = islands(o)
        print(f"     {o.name:34s} islands={len(isl):3d} nonmanifold={nm:4d} vol={vol:9.0f}")
        if "Master" not in o.name and "preview" not in o.name:
            check(label, nm == 0 and len(isl) == 1, f"{o.name} islands={len(isl)} nonmanifold={nm}")
        by[o.name.split("_", 1)[1]] = (o, isl, vol)
    master = by["Master"]
    check(label, len(master[1]) == len(objs), f"master has {len(master[1])} objects")
    if shot:
        sc = bpy.context.scene
        sc.render.engine = 'BLENDER_WORKBENCH'; sc.display.shading.color_type = 'MATERIAL'
        sc.display.shading.show_cavity = True
        sc.render.resolution_x, sc.render.resolution_y = 900, 600
        for o in bpy.data.objects:
            o.hide_render = o.name not in coll.objects or "preview" in o.name or o.name.endswith("_Master")
        cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam")); cam.data.lens = 30
        sc.collection.objects.link(cam); sc.camera = cam
        cam.location, target = shot
        cam.rotation_euler = (Vector(target) - Vector(cam.location)).to_track_quat('-Z', 'Y').to_euler()
        sc.render.filepath = f"{OUT}\\tray_{label}.png"
        bpy.ops.render.render(write_still=True)
    return objs, coll, by


def mixed():
    return [cyl(10, 30, (0, 0, 0)), cyl(8, 20, (5, 40, 3)), cube(15, (-30, -20, 7))]


def heights(isl, f):
    return sorted(round(f(v.z for v in i), 3) for i in isl)


# Box: a row of three on the same base, volumes add up.
objs, coll, by = run("box_row", mixed, shot=((60, -170, 120), (35, 0, 15)), mold_type='SILICONE_BOX')
if coll:
    lows = heights(by["Master"][1], min)
    check("box bottoms", max(lows) - min(lows) < 1e-3, f"bottoms {lows}")
    total = math.pi * 100 * 30 * 0.99 + math.pi * 64 * 20 * 0.99 + 15 ** 3
    check("box cast volume", abs(coll["easymold_cast_volume"] - total) / total < 0.02,
          f"{coll['easymold_cast_volume']:.0f} vs {total:.0f}")
    row = sorted(by["Master"][1], key=lambda i: min(v.x for v in i))
    gaps = [closest(a, b) for a, b in zip(row, row[1:])]
    # Vertex to vertex: a little more than the true distance between the surfaces.
    check("box spacing", all(10 - 1e-3 < g < 10.7 for g in gaps), f"closest distances {gaps}")

# Grid of four.
run("box_grid", lambda: mixed() + [ball(9, (60, 60, 9))], shot=((40, -150, 160), (15, -15, 10)),
    mold_type='SILICONE_BOX', tray_arrange='GRID')

# Shell split along X: the row runs along Y, every object on the split plane, one pour hole each.
objs, coll, by = run("shell2", mixed, layout=True, shot=((170, -90, 120), (0, 30, 15)),
                     mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X')

def rounded_pair():
    # Like the bears: a body with a nose sticking out higher up, so the bounding boxes meet at the nose, far
    # from where the surfaces would come closest.
    out = []
    for y in (0, 60):
        b = ball(12, (0, y, 12))
        bpy.ops.mesh.primitive_uv_sphere_add(radius=4, segments=24, ring_count=12, location=(0, y + 11, 20))
        nose = bpy.context.active_object
        m = b.modifiers.new("u", 'BOOLEAN'); m.object = nose; m.operation = 'UNION'
        bpy.context.view_layer.objects.active = b
        bpy.ops.object.modifier_apply(modifier=m.name)
        bpy.data.objects.remove(nose)
        out.append(b)
    return out


# Shell with Spacing 0: the silicone layers touch (surfaces 2 x silicone thickness apart), measured on the
# surfaces even where the bounding boxes are far apart (the nose sticks out at the top, the body below).
for gap in (0.0, 3.0, -5.0, -8.0):  # silicone 5: -8 is limited to -5
    objs, coll, by = run(f"shell_gap{gap:g}", rounded_pair, mold_type='SILICONE_SHELL', mold_parts='2',
                         split_axis='X', tray_gap=gap, shot=((150, 25, 40), (0, 25, 15)))
    if coll:
        s = bpy.context.scene.easymold_props.silicone_thickness
        a, b = sorted(by["Master"][1], key=lambda i: min(v.y for v in i))
        d = closest(a, b)
        want = 2 * s + max(gap, -s)
        check(f"shell gap {gap:g}", abs(d - want) < 0.5, f"closest {d:.2f} (2 x silicone + spacing = {want:g})")

# Direct molds: split along Y (row along X) and open-top (same top height).
run("direct2", mixed, layout=True, shot=((40, -170, 110), (35, 0, 15)), mold_type='DIRECT', mold_parts='2',
    split_axis='Y')
objs, coll, by = run("direct1", mixed, mold_type='DIRECT', mold_parts='1')
if coll:
    tops = heights(by["Master"][1], max)
    check("direct1 tops", max(tops) - min(tops) < 1e-3, f"tops {tops}")
run("direct2Z", mixed, mold_type='DIRECT', mold_parts='2', split_axis='Z')

# 2-part: lined up on each object's own widest section.
objs, coll, by = run("two_part", lambda: [ball(12, (0, 0, 20)), ball(8, (0, 40, 30)), cyl(9, 25, (30, 0, 0))],
                     layout=True, shot=((60, -150, 100), (30, 0, 15)), mold_type='SILICONE_TWO_PART')
if coll:
    board_top = max(v.co.z for v in by["Board"][0].data.vertices)
    # The balls' equators (their widest sections) are on the board's top.
    balls = [i for i in by["Master"][1] if len(i) > 4000]
    centers = sorted(round((min(v.z for v in i) + max(v.z for v in i)) / 2, 2) for i in balls)
    print("     board top", round(board_top, 2), "ball centers", centers)
    check("two_part parting", len(centers) == 2 and all(abs(c - board_top) < 0.7 for c in centers),
          f"board {board_top:.2f}, ball centers {centers}")

# Errors and warnings.
objs = scene(mixed)
p = bpy.context.scene.easymold_props
p.mold_type, p.mold_parts = 'DIRECT', '4'
try:
    print("## direct4:", bpy.ops.object.easymold_generate())
except RuntimeError as e:
    print("## direct4: EXPECTED ERROR", e)
check("direct4", utils.find_result(objs[0]) is None, "no result")
check("direct4", all(not o.hide_get() for o in objs), "objects visible again")
objs, coll, by = run("keep_overlap", lambda: [cyl(10, 20, (0, 0, 0)), cyl(10, 20, (12, 0, 0))],
                     mold_type='SILICONE_BOX', tray_arrange='KEEP')

# Regenerate from a result part, nothing else selected: the same objects again.
objs, coll, by = run("regen", mixed, mold_type='SILICONE_BOX')
part = next(o for o in coll.objects if o.name.endswith("_Base"))
for o in bpy.context.view_layer.objects.selected:
    if o is not None:
        o.select_set(False)
part.select_set(True)
bpy.context.view_layer.objects.active = part
print("## regen again:", bpy.ops.object.easymold_generate())
coll = utils.find_result(objs[0])
master = next(o for o in coll.objects if o.name.endswith("_Master"))
check("regen", len(islands(master)[0]) == 3 and len(utils.tray_extras(coll)) == 2, "3 objects again")
check("resolve extra", utils.resolve_source(objs[1]) == objs[0], "an extra object resolves to the mold's source")
print("FAILURES:", failures or "none")
