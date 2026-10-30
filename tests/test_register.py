# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Release checks: enabling, disabling and enabling the add-on again in one session leaves exactly one of each
handler and timer (none after disabling); the built-in presets come through Blender's preset path API, list for a
new user with no presets of their own and can't be removed; the manifest keeps only the Modeling tag;
exporting without a saved file or into a folder that can't be written gives a clear error, not a traceback."""
import sys, os, tempfile
import bpy
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import EasyMold, OUT, ROOT
from EasyMold import preview, operators
failures = []


def check(label, ok, msg):
    print(f"   {'ok  ' if ok else 'FAIL'} {label}: {msg}")
    if not ok:
        failures.append(f"{label}: {msg}")


HANDLERS = {
    "depsgraph_update_post": [preview._on_depsgraph, operators.snap_moved_markers],
    "load_post": [preview._after_load], "load_pre": [preview._before_load],
    "undo_pre": [preview._before_undo], "undo_post": [preview._after_undo],
    "save_pre": [preview._before_save], "save_post": [preview._after_save],
}


def counts():
    return {f"{name}:{func.__name__}": list(getattr(bpy.app.handlers, name)).count(func)
            for name, funcs in HANDLERS.items() for func in funcs}


# 1. Enable, disable, enable.
EasyMold.register()
EasyMold.unregister()
gone = counts()
check("unregister clean", not any(gone.values()), f"handlers left {[k for k, v in gone.items() if v]}")
check("unregister clean", not bpy.app.timers.is_registered(preview._tick), "no preview timer")
check("unregister clean", not hasattr(bpy.types.Scene, "easymold_props"), "no scene settings")
check("unregister clean", preview._State.draw_handle is None, "no viewport draw handler")
EasyMold.register()
again = counts()
check("cycle", all(v == 1 for v in again.values()), f"each handler once {again}")
check("cycle", bpy.app.timers.is_registered(preview._tick), "preview timer registered")
check("cycle", hasattr(bpy.types.Scene, "easymold_props"), "scene settings back")

# 2. Presets (extensions.blender.org review): no custom presets module; the built-in presets ship in
# presets/easymold/ and are found through bpy.utils.register_preset_path; the menu lists them for a user with no
# presets of their own (BLENDER_USER_SCRIPTS is an empty folder here); a built-in preset can't be removed.
import tomllib  # noqa: E402
from EasyMold import ui  # noqa: E402
SHIPPED = os.path.join(str(ROOT), "presets", "easymold")
check("presets module", not os.path.exists(os.path.join(str(ROOT), "presets.py")), "no custom presets.py")
check("presets path", os.path.normcase(SHIPPED) in map(os.path.normcase, bpy.utils.preset_paths("easymold")),
      f"shipped presets found: {bpy.utils.preset_paths('easymold')}")
shipped = sorted(f for f in os.listdir(SHIPPED) if f.endswith(".py")) if os.path.isdir(SHIPPED) else []
check("presets path", len(shipped) == 6, f"{len(shipped)} built-in presets")
EasyMold.unregister()
check("presets path", os.path.normcase(SHIPPED) not in map(os.path.normcase, bpy.utils.preset_paths("easymold")),
      "path removed on unregister")
EasyMold.register()


class FakeLayout:
    def __getattr__(self, name):
        return lambda *a, **k: FakeLayout()


class FakeMenu:
    """Stands in for the menu instance: the real class's settings, a layout that accepts anything."""
    layout = FakeLayout()
    searched = []

    def __getattr__(self, name):
        return getattr(ui.EASYMOLD_MT_presets, name)

    def path_menu(self, searchpaths, *a, **k):
        FakeMenu.searched += list(searchpaths)


try:
    ui.EASYMOLD_MT_presets.draw(FakeMenu(), bpy.context)
    check("presets empty", os.path.normcase(SHIPPED) in map(os.path.normcase, FakeMenu.searched),
          f"menu drawn from {FakeMenu.searched}")
except Exception as e:
    check("presets empty", False, f"{type(e).__name__}: {e}")
soap = os.path.join(SHIPPED, "Soap.py")
ui.EASYMOLD_MT_presets.bl_label = "Soap"  # as after choosing it in the menu
try:
    result = bpy.ops.easymold.add_preset(remove_active=True)
except RuntimeError as e:
    result = str(e).strip()
check("built-in kept", os.path.exists(soap), f"Soap.py still shipped after removing it ({result})")

# The listing keeps only the Modeling tag (extensions.blender.org review).
with open(os.path.join(str(ROOT), "blender_manifest.toml"), "rb") as f:
    tags = tomllib.load(f).get("tags")
check("manifest tags", tags == ["Modeling"], f"tags {tags}")

# 3. Export errors.
bpy.ops.wm.read_factory_settings(use_empty=True)
EasyMold.unregister()  # read_factory_settings keeps the add-on registered: start from a clean registration
EasyMold.register()
bpy.ops.mesh.primitive_cylinder_add(radius=10, depth=20, location=(0, 0, 10))
obj = bpy.context.active_object
p = bpy.context.scene.easymold_props
p.mold_type, p.mold_parts, p.explode = 'DIRECT', '2', False
bpy.ops.object.easymold_generate()
bpy.context.view_layer.objects.active = obj
p.export_folder = "//"
try:
    bpy.ops.object.easymold_export()
    check("export unsaved", False, "no error")
except RuntimeError as e:
    check("export unsaved", "Save the .blend file" in str(e), str(e).strip())
blocker = os.path.join(tempfile.mkdtemp(), "a_file")
open(blocker, "w").close()
p.export_folder = os.path.join(blocker, "sub")  # a folder inside a file: can't be made
try:
    bpy.ops.object.easymold_export()
    check("export unwritable", False, "no error")
except RuntimeError as e:
    check("export unwritable", "Could not write the files" in str(e), str(e).strip())
check("export unwritable", not os.path.isdir(os.path.join(blocker, "sub")), "nothing written")

print("FAILURES:", failures if failures else "none")
