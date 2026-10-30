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
    importlib.reload(presets)
    importlib.reload(ui)
else:
    import bpy
    from . import analysis, export, utils, properties, preview, operators, presets, ui


def register():
    properties.register()
    preview.register()
    operators.register()
    presets.register()
    ui.register()


def unregister():
    ui.unregister()
    presets.unregister()
    operators.unregister()
    preview.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
