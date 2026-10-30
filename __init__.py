# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

# Metadata lives in blender_manifest.toml (Blender extension format).

if "bpy" in locals():
    import importlib
    importlib.reload(analysis)
    importlib.reload(export)
    importlib.reload(utils)
    importlib.reload(properties)
    importlib.reload(preview)
    importlib.reload(operators)
    importlib.reload(ui)
else:
    import os
    import bpy
    from . import analysis, export, utils, properties, preview, operators, ui

# The built-in presets ship in presets/easymold/ (Blender's preset system finds them with the user's own).
ADDON_DIR = os.path.dirname(os.path.abspath(__file__))


def register():
    bpy.utils.register_preset_path(ADDON_DIR)
    properties.register()
    preview.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    preview.unregister()
    properties.unregister()
    bpy.utils.unregister_preset_path(ADDON_DIR)


if __name__ == "__main__":
    register()
