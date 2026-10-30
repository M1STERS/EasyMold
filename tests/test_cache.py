# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Session cache. For each mold type: a regenerate that reuses the cache gives exactly the same pieces as one
without it (also after changing a setting the preparation doesn't depend on); an edit / move of the object or a
setting it depends on is not served from the cache; the cache is never saved in the file; Clear Cache frees it;
the panel numbers. Prints the cold and warm times."""
import sys, importlib, math, time, os, tempfile
import bpy, bmesh
import numpy as np
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
    bpy.ops.mesh.primitive_uv_sphere_add(radius=12, segments=48, ring_count=24, location=(0, 0, 20))
    obj = bpy.context.active_object
    obj.scale = (1.6, 1, 1)
    obj.rotation_euler = (0, math.radians(20), 0)
    bpy.ops.object.transform_apply(scale=True, rotation=True)
    return obj


def ring():
    bpy.ops.mesh.primitive_torus_add(major_radius=14, minor_radius=5, location=(0, 0, 5))
    return bpy.context.active_object


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


def generate(obj):
    obj.hide_set(False)
    for o in bpy.context.view_layer.objects.selected:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    t = time.perf_counter()
    bpy.ops.object.easymold_generate()
    return time.perf_counter() - t


def pieces(obj):
    """Name: (vertices, faces, volume, bounds) of every piece of the result."""
    out = {}
    for o in utils.find_result(obj).objects:
        if o.type != 'MESH':
            continue
        co = np.empty(len(o.data.vertices) * 3)
        o.data.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        bm = bmesh.new(); bm.from_mesh(o.data)
        out[o.name] = (len(o.data.vertices), len(o.data.polygons), round(bm.calc_volume(signed=False), 3),
                       tuple(np.round(co.min(axis=0), 4)), tuple(np.round(co.max(axis=0), 4)),
                       sum(1 for e in bm.edges if not e.is_manifold))
        bm.free()
    return out


def same(label, a, b):
    diff = [n for n in set(a) | set(b) if a.get(n) != b.get(n)]
    check(label, not diff, "identical pieces" if not diff else f"differ: {[(n, a.get(n), b.get(n)) for n in diff[:2]]}")


def reused():
    return [o for o in bpy.data.meshes if o.get(utils.CACHE_TAG)]


CASES = [
    ("shell", egg, dict(mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X'), ("key_radius", 4.0)),
    ("shell4 screws", egg, dict(mold_type='SILICONE_SHELL', mold_parts='4', clamp_method='SCREW'), ("bolt_diameter", 4.0)),
    ("box", ring, dict(mold_type='SILICONE_BOX', fill_holes=True, flat_base=False), ("lip_height", 3.0)),
    ("two", egg, dict(mold_type='SILICONE_TWO_PART'), ("sprue_radius", 2.0)),
    ("direct core", cup, dict(mold_type='DIRECT', mold_parts='2', split_axis='X', auto_core=True), ("margin", 12.0)),
    ("direct curved", egg, dict(mold_type='DIRECT', mold_parts='2', split_axis='Y', curved_parting=True),
     ("vent_count", 3)),
]
for label, make, settings, (prop, value) in CASES:
    print(f"## {label}")
    obj = setup(make, **settings)
    p = bpy.context.scene.easymold_props
    utils.clear_cache()
    cold_t = generate(obj)
    cold = pieces(obj)
    warm_t = generate(obj)
    warm = pieces(obj)
    print(f"     cold {cold_t:.2f} s, warm {warm_t:.2f} s, cache {utils.cache_info()}")
    same(label, cold, warm)
    # A setting the preparation doesn't depend on: reused, and the same as without the cache.
    setattr(p, prop, value)
    warm_t = generate(obj)
    warm = pieces(obj)
    utils.clear_cache()
    check(label, not reused(), "Clear Cache frees every cached mesh")
    cold_t = generate(obj)
    cold = pieces(obj)
    print(f"     {prop}={value}: warm {warm_t:.2f} s, cold {cold_t:.2f} s")
    same(f"{label} {prop}", cold, warm)
    for o in utils.find_result(obj).objects:
        if o.type == 'MESH' and "preview" not in o.name and not o.name.endswith("_Master"):
            check(label, pieces(obj)[o.name][5] == 0, f"{o.name} closed")

# Not from the cache when the object or what the preparation depends on changes.
obj = setup(egg, mold_type='SILICONE_SHELL', mold_parts='2', split_axis='X')
generate(obj)
key = next(iter(utils._PREPARED))
obj.data.vertices[0].co.z += 3.0
obj.data.update()
generate(obj)
check("edit", len(utils._PREPARED) == 2 and key in utils._PREPARED, "an edited object is prepared again")
obj.location.x += 5.0
bpy.context.view_layer.update()
generate(obj)
check("move", len(utils._PREPARED) == 3, "a moved object is prepared again")
p = bpy.context.scene.easymold_props
p.shrinkage = 1.0
generate(obj)
check("setting", len(utils._PREPARED) == 3 and key not in utils._PREPARED,
      f"a setting of the preparation: prepared again (and the oldest of {utils.MAX_PREPARED} dropped)")
b = utils.MoldBuilder(obj, p)
b.prepare(use_layout=True)
check("notes", "the prepared object" in b.reused, f"reused {b.reused}")
b.abort()

# A layout: its master is the start; editing the layout's blockers changes it.
obj = setup(ring, mold_type='SILICONE_BOX', fill_holes=True, flat_base=False)
bpy.ops.object.easymold_edit_layout(reset=True)
generate(obj)
n = len(utils._PREPARED)
generate(obj)
check("layout", len(utils._PREPARED) == n, "same layout: reused")
blocker = next(o for o in utils.find_layout(obj).objects if o.get(utils.ROLE) == 'BLOCKER')
blocker.location.z += 1.0
bpy.context.view_layer.update()
generate(obj)
check("layout", len(utils._PREPARED) == n + 1, "moved blocker: prepared again")

# Never saved in the file, and still there after saving.
objects, surfaces, size = utils.cache_info()
check("info", objects > 0 and size > 0, f"{objects} objects, {surfaces} surfaces, {size / 2**20:.1f} MB")
path = os.path.join(tempfile.gettempdir(), "easymold_cache_test.blend")
bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
check("save", reused() and all(m.use_fake_user for m in reused()), "kept in the session after saving")
with bpy.data.libraries.load(path) as (data_from, _data_to):
    saved = list(data_from.meshes)
cached_names = {m.name for m in reused()}
check("save", not (cached_names & set(saved)), f"not in the saved file ({len(saved)} meshes saved)")
os.remove(path)
bpy.ops.object.easymold_clear_cache()
check("clear", utils.cache_info() == (0, 0, 0) and not reused(), "operator clears it")

print("FAILURES:", "none" if not failures else "")
for f in failures:
    print("  ", f)
