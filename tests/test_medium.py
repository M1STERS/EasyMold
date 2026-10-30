# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Items 8-10: material calculator, presets (built-in + user), shrinkage compensation.
Run with BLENDER_USER_SCRIPTS pointing to a temporary folder (user presets are written there)."""
import sys, importlib, os, glob, math
import bpy, bmesh
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from _env import EasyMold, OUT
EasyMold.register()
from EasyMold import utils, presets
failures = []


def check(label, ok, msg):
    print(f"   {'ok  ' if ok else 'FAIL'} {label}: {msg}")
    if not ok:
        failures.append(f"{label}: {msg}")


def near(a, b, rel=0.01):
    return abs(a - b) <= rel * abs(b)


def candle():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=128, radius=15, depth=40, location=(0, 0, 20))
    return bpy.context.active_object


# --- 8: material calculator ---------------------------------------------------------------
obj = candle()
p = bpy.context.scene.easymold_props
p.mold_type = 'SILICONE_BOX'
print("##", bpy.ops.object.easymold_generate())
coll = utils.find_result(obj)
cylinder = math.pi * 15 ** 2 * 40 * math.sin(2 * math.pi / 128) * 128 / (2 * math.pi)  # 128-gon prism
check("cast volume", near(coll["easymold_cast_volume"], cylinder), f"{coll['easymold_cast_volume']:.0f} vs {cylinder:.0f}")
box = (30 + 10) * (30 + 10) * (40 + 5) - (10 * 10 - math.pi * 25) * 45 - cylinder  # rounded corners r = 5
check("silicone volume", near(coll["easymold_volume"], box, 0.02), f"{coll['easymold_volume']:.0f} vs {box:.0f}")
p.waste, p.silicone_density, p.mix_a, p.mix_b, p.mix_by, p.silicone_price = 10, 1.1, 1, 1, 'WEIGHT', 30
need = utils.silicone_needs(p, 100000)  # 100 ml
check("1:1 weight", near(need['ml'], 110) and near(need['g'], 121) and near(need['a'], 60.5) and near(need['b'], 60.5)
      and need['unit'] == 'g' and near(need['cost'], 3.63), str(need))
p.mix_a, p.mix_b = 10, 1
need = utils.silicone_needs(p, 100000)
check("10:1 weight", near(need['a'], 110) and near(need['b'], 11), str(need))
p.mix_by = 'VOLUME'
need = utils.silicone_needs(p, 100000)
check("10:1 volume", near(need['a'], 100) and near(need['b'], 10) and need['unit'] == 'ml', str(need))
p.cast_density, p.cast_price = 0.9, 5
cast = utils.cast_needs(p, 28274)
check("cast", near(cast['g'], 25.45) and near(cast['cost'], 0.127), str(cast))

# --- 9: presets ----------------------------------------------------------------------------
builtin = sorted(glob.glob(os.path.join(presets.BUILTIN_DIR, "*.py")))
check("built-in presets", len(builtin) == 6, ", ".join(os.path.basename(f) for f in builtin))
for path in builtin:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    r = bpy.ops.script.execute_preset(filepath=path, menu_idname="EASYMOLD_MT_presets")
    p = bpy.context.scene.easymold_props
    check(os.path.basename(path), r == {'FINISHED'}, f"type={p.mold_type} mix={p.mix_a:g}:{p.mix_b:g} "
          f"cast={p.cast_density:g} shrink={p.shrinkage:g}")
bpy.ops.wm.read_factory_settings(use_empty=True)
p = bpy.context.scene.easymold_props
p.mold_type, p.silicone_thickness, p.mix_a, p.shrinkage = 'SILICONE_SHELL', 7.5, 4.0, 1.25
check("save user preset", bpy.ops.easymold.add_preset(name="My Test Preset") == {'FINISHED'}, "")
files = [f for d in bpy.utils.preset_paths(presets.SUBDIR) for f in glob.glob(os.path.join(d, "*.py"))]
check("user preset file", any("my_test_preset" in f.lower() for f in files), str(files))
p.mold_type, p.silicone_thickness, p.mix_a, p.shrinkage = 'DIRECT', 3.0, 1.0, 0.0
user = next(f for f in files if "my_test_preset" in f.lower())
bpy.ops.script.execute_preset(filepath=user, menu_idname="EASYMOLD_MT_presets")
check("user preset restores", (p.mold_type, p.silicone_thickness, p.mix_a, p.shrinkage) == ('SILICONE_SHELL', 7.5, 4.0, 1.25),
      f"{p.mold_type} {p.silicone_thickness} {p.mix_a} {p.shrinkage}")
text = open(user, encoding="utf-8").read()
check("preset skips per-file props", "core_object" not in text and "export_folder" not in text, "")
os.remove(user)

# --- 10: shrinkage -------------------------------------------------------------------------
for mold_type in ('SILICONE_BOX', 'SILICONE_SHELL', 'DIRECT'):
    obj = candle()
    p = bpy.context.scene.easymold_props
    p.mold_type, p.shrinkage = mold_type, 2.0
    r = bpy.ops.object.easymold_generate()
    coll = utils.find_result(obj)
    master = next(o for o in coll.objects if o.name.endswith("_Master"))
    zs = [v.co.z for v in master.data.vertices]
    xs = [v.co.x for v in master.data.vertices]
    height, width = max(zs) - min(zs), max(xs) - min(xs)
    check(f"shrink {mold_type}", r == {'FINISHED'} and near(height, 40 / 0.98, 0.002) and near(width, 30 / 0.98, 0.002)
          and abs(min(zs)) < 1e-4, f"master {width:.3f} x {height:.3f}, bottom z={min(zs):.4f}")
    volume = coll["easymold_cast_volume"] or coll["easymold_volume"]
    check(f"shrink volume {mold_type}", near(volume, cylinder / 0.98 ** 3, 0.01), f"{volume:.0f}")
# The layout carries the enlarged master.
obj = candle()
p = bpy.context.scene.easymold_props
p.mold_type, p.shrinkage = 'SILICONE_SHELL', 2.0
bpy.ops.object.easymold_edit_layout(reset=True)
layout = utils.find_layout(obj)
lm = next(o for o in layout.objects if o.get("easymold_role") == 'MASTER')
zs = [v.co.z for v in lm.data.vertices]
check("shrink layout", near(max(zs) - min(zs), 40 / 0.98, 0.002), f"{max(zs) - min(zs):.3f}")
print("##", bpy.ops.object.easymold_generate())
print("FAILURES:", failures or "none")
