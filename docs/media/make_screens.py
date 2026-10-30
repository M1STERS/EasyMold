# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Screenshots of the UI for the README (docs/images/ui_*.png), in a real window. Never saves anything.

    blender --factory-startup --enable-event-simulate --python docs/media/make_screens.py

ui_layout.png: an edited layout with Preview off (the markers drawn with their real shape, the pour hole selected);
ui_preview.png: the live Preview overlay; ui_panels.png: the EasyMold sidebar, cropped.
"""
import math
import sys
import traceback
from pathlib import Path

import bpy
import numpy as np
from mathutils import Euler

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ROOT / "docs" / "images"
IMAGES.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "tests"))
from _env import EasyMold  # noqa: E402

EasyMold.register()
from EasyMold import utils  # noqa: E402

# Only the EasyMold tab in the sidebar (its tab can't be picked from a script; the only one is shown).
# Sub-panels go first: a panel can't be unregistered while its children are.
others = [cls for cls in bpy.types.Panel.__subclasses__()
          if getattr(cls, "bl_space_type", "") == 'VIEW_3D' and getattr(cls, "bl_region_type", "") == 'UI'
          and getattr(cls, "bl_category", "") != 'EasyMold']
for cls in sorted(others, key=lambda c: not getattr(c, "bl_parent_id", "")):
    try:
        bpy.utils.unregister_class(cls)
    except RuntimeError as e:
        print("KEPT", cls.__name__, e)


def area3d():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return win, area, next(r for r in area.regions if r.type == 'WINDOW'), next(r for r in area.regions if r.type == 'UI')


def shot(name):
    win, area, _region, _ui = area3d()
    with bpy.context.temp_override(window=win, screen=win.screen, area=area):
        bpy.ops.screen.screenshot_area(filepath=str(IMAGES / f"{name}.png"))
    print("SHOT", name)


def crop_sidebar(source, name):
    """The sidebar part of an area screenshot."""
    win, area, _region, ui = area3d()
    img = bpy.data.images.load(str(IMAGES / f"{source}.png"))
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    x0 = max(0, ui.x - area.x)
    header = area.height - ui.height - 6  # the 3D view header above the sidebar (rows run bottom to top)
    part = px[:h - header, x0:x0 + ui.width].copy()
    out = bpy.data.images.new(name, part.shape[1], part.shape[0], alpha=True)
    out.pixels.foreach_set(part.ravel())
    out.filepath_raw = str(IMAGES / f"{name}.png")
    out.file_format = 'PNG'
    out.save()
    print("SHOT", name, part.shape[1], "x", part.shape[0])


def close_splash():
    win = bpy.context.window_manager.windows[0]
    win.event_simulate(type='ESC', value='PRESS')
    win.event_simulate(type='ESC', value='RELEASE')


def setup():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=15, depth=30, location=(0, 0, 15))
    obj = bpy.context.active_object
    obj.name = "Candle"
    p = bpy.context.scene.easymold_props
    p.mold_type, p.mold_parts, p.split_axis, p.clamp_method = 'DIRECT', '2', 'X', 'SCREW'
    p.pour_entry, p.pour_tip, p.vent_tip, p.vent_count = 'BOTTOM', 1.0, 0.5, 2
    win, area, region, _ui = area3d()
    space = area.spaces[0]
    space.show_region_ui = True
    space.overlay.show_floor = True
    r3d = space.region_3d
    r3d.view_perspective = 'PERSP'
    r3d.view_distance, r3d.view_location = 120, (4, 0, 12)
    r3d.view_rotation = Euler((math.radians(66), 0, math.radians(32))).to_quaternion()
    with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
        print("LAYOUT", bpy.ops.object.easymold_edit_layout('INVOKE_DEFAULT', reset=True))
    bpy.context.window_manager.easymold_preview = False


def open_tab():
    """Click the EasyMold tab: the second one on the sidebar's right edge (after Item, whose Transform panel is
    built into Blender and can't be removed)."""
    win, area, _region, ui = area3d()
    tabs = [ui.x + ui.width - 12, area.y + area.height - 165]
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=tabs[0], y=tabs[1])
    win.event_simulate(type='LEFTMOUSE', value='PRESS', x=tabs[0], y=tabs[1])
    win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=tabs[0], y=tabs[1])


def check_tab():
    _win, _area, _region, ui = area3d()
    print("TAB", ui.active_panel_category)
    if ui.active_panel_category != 'EasyMold':
        raise RuntimeError("the EasyMold tab didn't open: adjust open_tab()")


def select(role):
    layout = utils.find_layout(bpy.data.objects["Candle"])
    obj = sorted((o for o in layout.objects if o.get(utils.ROLE) == role), key=lambda o: o.name)[0]
    for o in bpy.context.view_layer.objects:
        if o is not None:
            o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def deselect():
    for o in bpy.context.view_layer.objects:
        if o is not None:
            o.select_set(False)
    layout = utils.find_layout(bpy.data.objects["Candle"])
    master = next(o for o in layout.objects if o.get(utils.ROLE) == 'MASTER')  # the original is hidden meanwhile
    master.select_set(True)
    bpy.context.view_layer.objects.active = master


PLAN = [
    (1.0, close_splash),
    (0.5, setup),
    (2.5, open_tab), (0.8, check_tab),
    (0.2, lambda: select('POUR')), (1.5, lambda: shot("ui_layout")),
    (0.3, lambda: crop_sidebar("ui_layout", "ui_panels")),
    (0.3, deselect), (0.2, lambda: setattr(bpy.context.window_manager, "easymold_preview", True)),
    (2.5, lambda: shot("ui_preview")),
    (0.5, lambda: bpy.ops.wm.quit_blender()),
]


def run():
    _delay, func = PLAN.pop(0)
    try:
        func()
    except Exception:
        traceback.print_exc()
        bpy.ops.wm.quit_blender()
        return None
    return PLAN[0][0] if PLAN else None


bpy.app.timers.register(run, first_interval=PLAN[0][0])
