# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Release checks: enabling, disabling and enabling the add-on again in one session leaves exactly one of each
handler and timer (none after disabling); the Presets menu draws for a new user with no presets of their own;
exporting without a saved file or into a folder that can't be written gives a clear error, not a traceback."""
import sys, os, tempfile
import bpy
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import EasyMold, OUT
from EasyMold import preview, operators, presets
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

# 2. The Presets menu for a user with no presets of their own (BLENDER_USER_SCRIPTS is an empty folder here).
class FakeLayout:
    def __getattr__(self, name):
        return lambda *a, **k: FakeLayout()


class FakeMenu:
    bl_idname = presets.EASYMOLD_MT_presets.__name__
    preset_operator = presets.EASYMOLD_MT_presets.preset_operator
    layout = FakeLayout()

    def path_menu(self, *a, **k):
        pass


try:
    presets.EASYMOLD_MT_presets.draw(FakeMenu(), bpy.context)
    check("presets empty", True, f"menu drawn (user preset paths: {bpy.utils.preset_paths(presets.SUBDIR)})")
except Exception as e:
    check("presets empty", False, f"{type(e).__name__}: {e}")

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
