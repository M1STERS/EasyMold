# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Presets: built-in ones shipped with the extension (presets/ folder) and the user's own (Blender's preset
system, saved in the user's scripts/presets/easymold folder)."""
import os

import bpy
from bl_operators.presets import AddPresetBase

from .properties import EasyMoldProperties

SUBDIR = "easymold"
BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "presets")
# Everything except what belongs to one scene/file.
SKIP = {"core_object", "export_folder"}


def _is_py(ext):
    return ext.lower() == ".py"


class EASYMOLD_MT_presets(bpy.types.Menu):
    bl_label = "Presets"
    preset_subdir = SUBDIR
    preset_operator = "script.execute_preset"

    def draw(self, context):
        layout = self.layout
        props = {"menu_idname": self.bl_idname}
        layout.label(text="Built-in")
        self.path_menu([BUILTIN_DIR], self.preset_operator, props_default=props, filter_ext=_is_py)
        user = bpy.utils.preset_paths(SUBDIR)
        if any(f.endswith(".py") for path in user for f in os.listdir(path)):
            layout.separator()
            layout.label(text="Yours")
            self.path_menu(user, self.preset_operator, props_default=props, filter_ext=_is_py,
                           add_operator="easymold.add_preset")


class EASYMOLD_OT_add_preset(AddPresetBase, bpy.types.Operator):
    """Save the current settings as a preset (or remove the selected one of yours)"""
    bl_idname = "easymold.add_preset"
    bl_label = "Add EasyMold Preset"
    preset_menu = "EASYMOLD_MT_presets"
    preset_subdir = SUBDIR
    preset_defines = ["props = bpy.context.scene.easymold_props"]
    preset_values = [f"props.{name}" for name in EasyMoldProperties.__annotations__ if name not in SKIP]


classes = (EASYMOLD_MT_presets, EASYMOLD_OT_add_preset)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
