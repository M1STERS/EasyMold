# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Findings of the 1.0 code review, one check each:
- Split to Fit with exploded pieces cuts every piece at its middle (matrix_world was stale after the explode);
- Edit Layout run again without Reset (script, redo panel) keeps the edited layout;
- the master copy keeps the object's custom normals on every Generate, not only the first;
- the master copy is never exported with Include Master off, even when it couldn't get its plain name;
- a marker scaled negative gives Generate the same (positive) radius the overlay shows;
- a generated piece whose original was deleted is not taken as a new source."""
import sys, os
import bpy
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from EasyMold import utils, export
failures = []


def check(label, ok, msg):
    print(f"   {'ok  ' if ok else 'FAIL'} {label}: {msg}")
    if not ok:
        failures.append(f"{label}: {msg}")


def scene(**settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=15, depth=30, location=(0, 0, 15))
    obj = bpy.context.active_object
    obj.name = "Cup"
    p = bpy.context.scene.easymold_props
    for k, v in settings.items():
        setattr(p, k, v)
    return obj, p


def active(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def span(obj, j):
    vals = [(obj.matrix_world @ v.co)[j] for v in obj.data.vertices]
    return max(vals) - min(vals)


# 1. Split to Fit, exploded: the two halves of a cut piece are equally long across the cut.
obj, p = scene(mold_type='DIRECT', mold_parts='2', split_axis='X', explode=True, split_to_fit=True,
               bed_x=40.0, bed_y=40.0, bed_z=200.0)
bpy.ops.object.easymold_generate()
bpy.context.view_layer.update()
result = utils.find_result(obj)
pairs = {}
for o in export.printable(result):
    base, _sep, side = o.name.rpartition("_")
    if side in ("1", "2"):  # the halves of a bed cut
        pairs.setdefault(base, []).append(o)
check("fit bed", pairs, f"cut pieces {sorted(pairs)}")
for name, halves in pairs.items():
    if len(halves) != 2:
        continue
    # The cut axis: the one where the two halves' boxes don't overlap.
    lo = [[min((h.matrix_world @ v.co)[k] for v in h.data.vertices) for k in range(3)] for h in halves]
    hi = [[max((h.matrix_world @ v.co)[k] for v in h.data.vertices) for k in range(3)] for h in halves]
    j = max(range(3), key=lambda k: max(lo[0][k] - hi[1][k], lo[1][k] - hi[0][k]))
    a, b = span(halves[0], j), span(halves[1], j)
    check("fit bed", abs(a - b) < 0.5, f"{name}: halves {a:.2f} / {b:.2f} mm along {'XYZ'[j]}")

# 2. Edit Layout again without Reset keeps the edits.
obj, p = scene(mold_type='DIRECT', mold_parts='2', split_axis='X')
active(obj)
bpy.ops.object.easymold_edit_layout(reset=True)
layout = utils.find_layout(obj)
key = next(o for o in layout.objects if o.get(utils.ROLE) == 'KEY')
key_name = key.name
key.location.z += 3.0
bpy.context.view_layer.update()
moved = key.location.copy()
active(obj)
bpy.ops.object.easymold_edit_layout(reset=False)
layout = utils.find_layout(obj)
kept = layout.objects.get(key_name)
check("edit layout", kept is not None and (kept.location - moved).length < 1e-5,
      f"edited key kept ({'gone' if kept is None else tuple(round(c, 2) for c in kept.location)})")

# 3 and 4. The master copy: custom normals on every Generate; never exported unless asked, whatever its name.
obj, p = scene(mold_type='DIRECT', mold_parts='2', split_axis='X', explode=False)
obj.data.shade_smooth()
obj.data.normals_split_custom_set_from_vertices([v.normal for v in obj.data.vertices])
for run in (1, 2):
    active(obj)
    bpy.ops.object.easymold_generate()
    result = utils.find_result(obj)
    master = next(o for o in result.objects if o.get(export.MASTER_COPY)) if hasattr(export, "MASTER_COPY") else \
        next(o for o in result.objects if o.name.startswith("Cup_Master"))
    check("custom normals", "custom_normal" in master.data.attributes, f"run {run}: {master.name} keeps them")
taken = bpy.data.objects.new("Cup_Master", bpy.data.meshes.new("taken"))  # the plain name is taken
bpy.context.scene.collection.objects.link(taken)
active(obj)
bpy.ops.object.easymold_generate()
result = utils.find_result(obj)
names = [o.name for o in export.printable(result, include_master=False)]
check("master export", not any(n.startswith("Cup_Master") for n in names), f"printable {names}")
names = [o.name for o in export.printable(result, include_master=True)]
check("master export", any(n.startswith("Cup_Master") for n in names), "included when asked")
master = next(o for o in result.objects if o.name.startswith("Cup_Master"))
master.name = "My Candle"  # the user renames the master copy
names = [o.name for o in export.printable(result, include_master=False)]
check("master export", "My Candle" not in names, f"renamed master left out: printable {names}")

# 5. A marker scaled negative: Generate uses the radius the overlay shows.
obj, p = scene(mold_type='DIRECT', mold_parts='2', split_axis='X')
active(obj)
bpy.ops.object.easymold_edit_layout(reset=True)
layout = utils.find_layout(obj)
key = next(o for o in layout.objects if o.get(utils.ROLE) == 'KEY')
key.scale = (-1.0, -1.0, -1.0)
bpy.context.view_layer.update()
b = utils.MoldBuilder(obj, p, 'PLAN')
b.prepare(use_layout=True)
b.spec_from_layout(b.split_axes())
radii = [r for _a, c, r in b.keys if (c - key.matrix_world.translation).length < 1.0]
b.discard()
check("negative scale", radii and abs(radii[0] - utils.marker_radius(key)) < 1e-6,
      f"radius {radii} vs marker {utils.marker_radius(key):.2f}")

# 6. A piece whose original was deleted is not a mold source.
obj, p = scene(mold_type='DIRECT', mold_parts='2', split_axis='X')
active(obj)
bpy.ops.object.easymold_generate()
result = utils.find_result(obj)
piece = next(o for o in result.objects if o.get(utils.ROLE) == 'PART')
bpy.data.objects.remove(obj)
check("orphan piece", utils.resolve_source(piece) is None, f"source {utils.resolve_source(piece)}")

print("FAILURES:", failures if failures else "none")
