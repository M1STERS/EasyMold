# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Item 16: live preview. Overlay plan (every mold type: shapes, nothing left in the scene, original untouched and
not hidden, timing), the update loop (follows settings and layout markers), bed misfit, Auto Preview (coarse build
into its own collection, restarted by a change, never touches the selection, removed by Generate / turning it off /
saving), the face limit."""
import sys, importlib, math, time, os, tempfile
import bpy, bmesh
from mathutils import Vector
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from EasyMold import utils, preview
failures = []
preview.PLAN_DELAY = 0.0
preview.BUILD_DELAY = 0.0


def check(label, ok, msg):
    print(f"   {'ok  ' if ok else 'FAIL'} {label}: {msg}")
    if not ok:
        failures.append(f"{label}: {msg}")


def cup():
    bpy.ops.mesh.primitive_cylinder_add(vertices=40, radius=15, depth=30, location=(0, 0, 15))
    outer = bpy.context.active_object
    bpy.ops.mesh.primitive_cylinder_add(vertices=40, radius=12, depth=30, location=(0, 0, 20))
    hole = bpy.context.active_object
    m = outer.modifiers.new("b", 'BOOLEAN'); m.object = hole; m.operation = 'DIFFERENCE'
    bpy.context.view_layer.objects.active = outer
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(hole)
    return outer


def egg():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=12, segments=32, ring_count=16, location=(0, 0, 20))
    obj = bpy.context.active_object
    obj.scale = (1.6, 1, 1)
    obj.rotation_euler = (0, math.radians(20), 0)
    bpy.ops.object.transform_apply(scale=True, rotation=True)
    return obj


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


def counts():
    return len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.collections), len(bpy.data.node_groups)


def plan(obj):
    p = bpy.context.scene.easymold_props
    b = utils.MoldBuilder(obj, p, 'PLAN')
    try:
        return b.run_plan()
    finally:
        b.discard()


def stats(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    n = len(utils.face_islands(bm))
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    return n, nm


# 1. The plan of every mold type.
CASES = [
    ("direct2", cup, dict(mold_type='DIRECT', mold_parts='2', split_axis='X'), {'OUTLINE', 'SPLIT', 'KEY', 'POUR', 'VENT', 'BOLT'}),
    ("direct4", cup, dict(mold_type='DIRECT', mold_parts='4', split_axis='X'), {'OUTLINE', 'SPLIT', 'KEY', 'POUR'}),
    ("direct1", cup, dict(mold_type="DIRECT", mold_parts="1"), {"OUTLINE"}),
    ("directZ", cup, dict(mold_type='DIRECT', mold_parts='2', split_axis='Z', pour_entry='TOP'), {'OUTLINE', 'SPLIT', 'KEY', 'POUR'}),
    ("curved", egg, dict(mold_type='DIRECT', mold_parts='2', split_axis='Y', curved_parting=True), {'OUTLINE', 'SPLIT', 'KEY', 'POUR'}),
    ("box", cup, dict(mold_type='SILICONE_BOX'), {'OUTLINE'}),
    ("shell", egg, dict(mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X'), {'SPLIT', 'KEY'}),
    ("two", egg, dict(mold_type="SILICONE_TWO_PART"), {"OUTLINE", "SPLIT", "KEY", "POUR"}),
    ("two_curved", egg, dict(mold_type='SILICONE_TWO_PART', curved_parting=True), {'OUTLINE', 'SPLIT', 'KEY', 'POUR'}),
]
for label, make, settings, kinds in CASES:
    obj = setup(make, **settings)
    wm = bpy.context.window_manager
    obj.hide_set(False)
    before_co = [v.co.copy() for v in obj.data.vertices]
    before = counts()
    t = time.perf_counter()
    try:
        shapes = plan(obj)
    except utils.MoldError as e:
        check(label, False, f"MoldError {e}")
        continue
    dt = time.perf_counter() - t
    have = {k for k, v in shapes['lines'].items() if v} | {k for k, v in shapes['tris'].items() if len(v)}
    print(f"## {label}: {dt * 1000:.0f} ms, {shapes['summary']}, kinds {sorted(have)}")
    check(label, kinds <= have, f"draws {sorted(kinds - have)} missing")
    check(label, all(len(v) % 2 == 0 for v in shapes['lines'].values()), "line lists are segment pairs")
    check(label, counts() == before, f"nothing left behind {before} -> {counts()}")
    check(label, all((a - v.co).length < 1e-9 for a, v in zip(before_co, obj.data.vertices)), "original untouched")
    check(label, not obj.hide_get() and obj.select_get() and bpy.context.view_layer.objects.active == obj,
          "original still shown, selected and active")
    check(label, dt < 1.0, f"fast enough ({dt:.2f} s)")
    check(label, not shapes['misfit'], f"fits the default bed {shapes['misfit']}")
    if label == "shell":
        check(label, 'OUTLINE' in shapes['tris'], "shell outline drawn as a surface")

# 2. Plan positions match what the build uses: keys of a plan = keys of a layout.
obj = setup(cup, mold_type='DIRECT', mold_parts='2', split_axis='X')
wm = bpy.context.window_manager
b = utils.MoldBuilder(obj, bpy.context.scene.easymold_props, 'PLAN')
b.run_plan()
plan_keys = sorted(tuple(round(c, 4) for c in k[1]) for k in b.keys)
b.discard()
bpy.ops.object.easymold_edit_layout(reset=True)
layout = utils.find_layout(obj)
layout_keys = sorted(tuple(round(c, 4) for c in o.matrix_world.translation) for o in layout.objects
                     if o.get(utils.ROLE) == 'KEY')
check("same keys", plan_keys == layout_keys, f"plan {len(plan_keys)} keys, layout {len(layout_keys)}")

# 3. The update loop: follows settings and layout markers; the plan uses the edited layout.
wm = bpy.context.window_manager
bpy.context.view_layer.objects.active = obj
wm.easymold_preview = True
preview.update(bpy.context)
summary = preview.status()[0]
check("loop", summary.startswith("2 pieces"), f"summary '{summary}'")
key = next(o for o in layout.objects if o.get(utils.ROLE) == 'KEY')
key.location.y += 3.0
bpy.context.view_layer.update()
preview.update(bpy.context)
moved = list(preview._State.plan['markers'][key.name]['lines'])  # the layout markers are drawn on their own
want = key.matrix_world.translation.copy()
want.x = 0.0  # snapped onto the split plane x = center
center_x = (min(v.co.x for v in obj.data.vertices) + max(v.co.x for v in obj.data.vertices)) / 2
want.x = center_x
near = min((q - want).length for q in moved)
check("loop marker", abs(near - key.empty_display_size * max(key.scale)) < 0.05,
      f"key circle around the moved marker (distance {near:.3f})")
utils.delete_collection(layout)
bpy.context.view_layer.objects.active = obj
bpy.context.scene.easymold_props.mold_parts = '4'
preview.update(bpy.context)
check("loop settings", preview.status()[0].startswith("4 pieces"), f"summary '{preview.status()[0]}'")
bpy.context.scene.easymold_props.bed_x = bpy.context.scene.easymold_props.bed_y = 20.0
preview.update(bpy.context)
check("bed", len(preview.status()[1]) == 4 and 'MISFIT' in preview._State.plan['lines'],
      f"misfit {preview.status()[1]}")
bpy.context.scene.easymold_props.bed_x = bpy.context.scene.easymold_props.bed_y = 220.0
bpy.context.scene.easymold_props.mold_parts = '1'
bpy.context.scene.easymold_props.auto_core = True
preview.update(bpy.context)
check("error", "Auto Core" in preview.status()[2] and preview._State.plan is None, f"error '{preview.status()[2]}'")
bpy.context.scene.easymold_props.auto_core = False
bpy.context.scene.easymold_props.mold_parts = '2'
preview.update(bpy.context)
check("error cleared", not preview.status()[2] and preview._State.plan is not None, "plan back")
# Edit Mode: nothing recomputed until leaving it.
bpy.ops.object.mode_set(mode='EDIT')
bpy.context.scene.easymold_props.margin = 15.0
preview.update(bpy.context)
check("edit mode", preview._State.plan_due, "waits for Object Mode")
bpy.ops.object.mode_set(mode='OBJECT')
preview.update(bpy.context)
check("edit mode", not preview._State.plan_due, "recomputed in Object Mode")
# The prepared copy is cached: a setting that doesn't change it reuses it; an edit of the mesh doesn't.
key = preview._State.prepared_key
cached = utils._PLAN_CACHE.get(key)
check("cache", cached is not None and cached['mesh'].use_fake_user, "prepared copy kept")
bpy.context.scene.easymold_props.key_radius = 4.0
preview.update(bpy.context)
check("cache", preview._State.prepared_key == key and utils._PLAN_CACHE.get(key) is cached
      and all(abs(k_r - 4.0) < 1e-6 for k_r in [4.0]), "a key setting reuses it")
lo_before = preview._State.plan['lines']['OUTLINE'][0].copy()
obj.data.vertices[0].co.z -= 20.0  # the user edits the object
obj.data.update()
bpy.context.view_layer.update()
preview.update(bpy.context)
lo_after = min(q.z for q in preview._State.plan['lines']['OUTLINE'])
check("cache", preview._State.prepared_key != key and lo_after < lo_before.z - 15,
      f"a mesh edit is seen (outline bottom {lo_before.z:.1f} -> {lo_after:.1f})")
obj.data.vertices[0].co.z += 20.0
obj.data.update()
bpy.context.view_layer.update()
wm.easymold_preview = False
check("off", preview._State.plan is None and not utils._PLAN_CACHE, "overlay and cache cleared")
check("off", not any(m.use_fake_user for m in bpy.data.meshes), "no cached mesh left")


# 4. Auto Preview.
def run_auto(limit=400):
    for _ in range(limit):
        preview.update(bpy.context)
        if preview._State.job is None and not preview._State.build_due:
            return True
    return False


obj = setup(cup, mold_type='DIRECT', mold_parts='2', split_axis='X')
wm = bpy.context.window_manager
before_co = [v.co.copy() for v in obj.data.vertices]
bpy.ops.object.easymold_generate()
result = utils.find_result(obj)
obj.hide_set(False)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
t = time.perf_counter()
wm.easymold_auto_preview = True
done = run_auto()
dt = time.perf_counter() - t
coll = preview.previews(obj)
check("auto", done and len(coll) == 1, f"built in {dt:.1f} s, {len(coll)} preview collection(s), "
      f"error '{preview.status()[2]}'")
if coll:
    pieces = [o for o in coll[0].objects if "_Mold" in o.name]
    check("auto", len(pieces) == 2, f"{len(pieces)} mold pieces")
    for o in pieces:
        n, nm = stats(o)
        print(f"     {o.name:34s} nonmanifold={nm:4d} islands={n}")
        check("auto", nm == 0 and o.hide_select, f"{o.name} closed, not selectable")
    check("auto", bpy.context.view_layer.objects.active == obj and not obj.hide_get(),
          "selection and original left alone")
    check("auto", utils._layer_collection(bpy.context.view_layer.layer_collection, result).hide_viewport,
          "the real result is hidden while previewed")
    check("auto", utils.find_result(obj) == result, "export still finds the real result")
# A change restarts it, and it replaces the old preview.
p = bpy.context.scene.easymold_props
p.margin = 14.0
preview.update(bpy.context)
preview.update(bpy.context)
check("restart", preview._State.job is not None, "a change starts a new build")
p.margin = 12.0
preview.update(bpy.context)
check("restart", preview._State.job is None or preview._State.job[2] == preview._State.signature,
      "a change during a build cancels it")
run_auto()
coll = preview.previews(obj)
block = [o for o in coll[0].objects if "_Mold" in o.name] if coll else []
width = max((max(v.co.x for v in o.data.vertices) - min(v.co.x for v in o.data.vertices) for o in block), default=0)
check("restart", len(coll) == 1 and len(block) == 2, f"{len(coll)} preview(s) with the new margin")
# Undo/save: removed before saving.
path = os.path.join(tempfile.gettempdir(), "easymold_preview_test.blend")
bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
check("save", not preview.previews(), "not saved with the file")
run_auto()
check("save", len(preview.previews(obj)) == 1, "rebuilt after saving")
os.remove(path)
# Generate removes it; turning Auto Preview off too and shows the result again.
bpy.ops.object.easymold_generate()
check("generate", not preview.previews(), "Generate replaces the preview")
result = utils.find_result(obj)
obj.hide_set(False)
bpy.context.view_layer.objects.active = obj
p.margin = 10.0
run_auto()
check("off", len(preview.previews(obj)) == 1, "preview again after a change")
wm.easymold_auto_preview = False
check("off", not preview.previews() and
      not utils._layer_collection(bpy.context.view_layer.layer_collection, result).hide_viewport,
      "turned off: preview removed, result shown")
check("original", all((a - v.co).length < 1e-9 for a, v in zip(before_co, obj.data.vertices)), "original untouched")

# 5. Other mold types build as an Auto Preview too.
def ring():
    bpy.ops.mesh.primitive_torus_add(major_radius=14, minor_radius=5, location=(0, 0, 5))
    return bpy.context.active_object


def silicone_volume(coll):
    return sum(utils.mesh_volume(o.data) for o in coll.objects if "Silicone" in o.name)


for label, make, settings in (("box", cup, dict(mold_type='SILICONE_BOX')),
                              ("shell", egg, dict(mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X')),
                              ("two", egg, dict(mold_type='SILICONE_TWO_PART')),
                              ("ring", ring, dict(mold_type='SILICONE_BOX', fill_holes=True))):
    obj = setup(make, **settings)
    wm = bpy.context.window_manager
    orphans = sum(1 for m in bpy.data.meshes if m.users == 0)
    t = time.perf_counter()
    wm.easymold_auto_preview = True
    run_auto()
    coll = preview.previews(obj)
    bad = [o.name for o in (coll[0].objects if coll else []) if "preview" not in o.name and stats(o)[1]]
    check(f"auto {label}", len(coll) == 1 and not bad and not preview.status()[2],
          f"{time.perf_counter() - t:.1f} s, non-manifold {bad}, error '{preview.status()[2]}'")
    check(f"auto {label}", sum(1 for m in bpy.data.meshes if m.users == 0) == orphans, "no orphan meshes")
    if label == "ring" and coll:
        coarse = silicone_volume(coll[0])
        wm.easymold_auto_preview = False
        bpy.ops.object.easymold_generate()
        real = silicone_volume(utils.find_result(obj))
        check("auto ring", abs(coarse - real) < real * 0.03, f"blocker joined like the real mold: silicone "
              f"{coarse:.0f} vs {real:.0f}")
    wm.easymold_auto_preview = False

# 6. Too detailed objects aren't auto-previewed.
obj = setup(lambda: (bpy.ops.mesh.primitive_uv_sphere_add(radius=10, segments=400, ring_count=300),
                     bpy.context.active_object)[1], mold_type='DIRECT')
wm = bpy.context.window_manager
wm.easymold_auto_preview = True
run_auto()
check("limit", "too detailed" in preview.status()[2] and not preview.previews(), f"'{preview.status()[2]}'")
wm.easymold_auto_preview = False

print("FAILURES:", "none" if not failures else "")
for f in failures:
    print("  ", f)
