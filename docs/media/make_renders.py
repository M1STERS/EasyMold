# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Renders for the README (docs/images/), made headless with Workbench. Never saves anything.

    blender -b --factory-startup --python docs/media/make_renders.py -- synthetic [only=name]
    blender -b <your file>.blend --python docs/media/make_renders.py -- scene object=<name>

    blender -b --factory-startup --python docs/media/make_renders.py -- logo

`synthetic` builds its own objects (box, shell, two-part, tray, curved parting, pour channel and vents, bed split);
`scene` makes hero.jpg, featured.jpg and direct_core.jpg from an object of an open .blend file (a direct mold in
2 parts along X with a core, as set up in that file): the file is not part of the repository.
"""
import math
import os
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ROOT / "docs" / "images"
IMAGES.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "tests"))
from _env import EasyMold  # noqa: E402  (imports the add-on from this checkout)
from shapes import make_pumpkin  # noqa: E402

try:
    EasyMold.register()
except ValueError:
    pass  # already registered (the extension is installed in this Blender)
from EasyMold import utils  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
MODE = ARGS[0] if ARGS else "synthetic"
ONLY = next((a.split("=", 1)[1] for a in ARGS if a.startswith("only=")), None)
OBJECT = next((a.split("=", 1)[1] for a in ARGS if a.startswith("object=")), None)


# --- objects ----------------------------------------------------------------------

def lathe(name, profile, segments=64):
    """A solid of revolution around Z from [(radius, z), ...] (bottom to top; radius 0 closes an end)."""
    bm = bmesh.new()
    rings = []
    for r, z in profile:
        if r <= 1e-6:
            rings.append([bm.verts.new((0, 0, z))])
            continue
        rings.append([bm.verts.new((r * math.cos(a), r * math.sin(a), z))
                      for a in (2 * math.pi * k / segments for k in range(segments))])
    for lo, hi in zip(rings, rings[1:]):
        if len(lo) == 1:
            for k in range(segments):
                bm.faces.new((lo[0], hi[k], hi[(k + 1) % segments]))
        elif len(hi) == 1:
            for k in range(segments):
                bm.faces.new((lo[k], lo[(k + 1) % segments], hi[0]))
        else:
            for k in range(segments):
                bm.faces.new((lo[k], lo[(k + 1) % segments], hi[(k + 1) % segments], hi[k]))
    for ring in (rings[0], rings[-1]):
        if len(ring) > 1:
            bm.faces.new(ring)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    mesh.shade_smooth()
    return obj


def pawn(name="Pawn", scale=1.0):
    profile = [(0, 0), (14, 0), (14, 3), (12, 5), (9, 7), (6, 10), (5, 18), (5.5, 26), (8, 28), (8, 30), (4, 31),
               (6.5, 34), (7.5, 38), (6.5, 42), (4, 44.5), (0, 45.2)]
    return lathe(name, [(r * scale, z * scale) for r, z in profile])


def fluted_candle(name="Candle", r=16, h=46, flutes=10):
    obj = lathe(name, [(0, 0), (r, 0), (r, h * 0.33), (r, h * 0.66), (r, h), (r * 0.75, h + 2), (0, h + 2)], 96)
    for v in obj.data.vertices:
        ring = math.hypot(v.co.x, v.co.y)
        if ring > r * 0.9:
            a = math.atan2(v.co.y, v.co.x) + v.co.z * 0.04  # a slow twist
            k = 1 + 0.06 * math.cos(flutes * a)
            v.co.x *= k
            v.co.y *= k
    return obj


def egg(name="Egg"):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=12, segments=48, ring_count=24, location=(0, 0, 20))
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (1.6, 1, 1)
    obj.rotation_euler = (0, math.radians(20), 0)
    bpy.ops.object.transform_apply(scale=True, rotation=True)
    obj.data.shade_smooth()
    return obj


def bent(name="Bent"):
    """A long capsule bent sideways (along Y): its widest outline, where the parting surface runs, is curved."""
    bpy.ops.mesh.primitive_uv_sphere_add(radius=10, segments=64, ring_count=32, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    for v in obj.data.vertices:
        x, y, z = v.co.x * 3.2, v.co.y * 0.9, v.co.z * 0.9
        v.co = (x, y + 0.012 * x * x - 6, z + 20)
    obj.data.shade_smooth()
    return obj


# --- scene and render ---------------------------------------------------------------

def fresh(**settings):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    p = bpy.context.scene.easymold_props
    for k, v in settings.items():
        setattr(p, k, v)
    return p


def generate(obj, extras=()):
    for o in bpy.context.view_layer.objects:
        if o is not None:  # the list can hold None right after objects are removed
            o.select_set(False)
    for o in [obj, *extras]:
        o.hide_set(False)
        o.select_set(True)
    bpy.context.view_layer.objects.active = obj
    print("GENERATE", obj.name, bpy.ops.object.easymold_generate())
    bpy.context.view_layer.update()
    return utils.find_result(obj)


def shown(objects):
    """Render only these objects (and the camera)."""
    keep = set(objects)
    for o in bpy.context.scene.objects:
        o.hide_render = o.type != 'CAMERA' and o not in keep


def frame(objects, direction=(1.0, -1.25, 0.75), lens=55, margin=1.08):
    """A perspective camera looking along -direction at the objects, just far enough to fit their boxes."""
    sc = bpy.context.scene
    cam = sc.camera
    if cam is None:
        cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
        sc.collection.objects.link(cam)
        sc.camera = cam
    cam.data.lens = lens
    pts = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    lo = Vector([min(q[i] for q in pts) for i in range(3)])
    hi = Vector([max(q[i] for q in pts) for i in range(3)])
    center = (lo + hi) / 2
    d = Vector(direction).normalized()
    rot = (-d).to_track_quat('-Z', 'Y')
    right, up = rot @ Vector((1, 0, 0)), rot @ Vector((0, 1, 0))
    aspect = sc.render.resolution_x / sc.render.resolution_y  # landscape: the sensor width is horizontal
    tan_h = cam.data.sensor_width / (2 * lens)
    tan_v = tan_h / aspect
    # Each corner fits when its offset across the view over its depth is within the field of view.
    dist = max(max(c.dot(d) + abs(c.dot(right)) * margin / tan_h, c.dot(d) + abs(c.dot(up)) * margin / tan_v)
               for c in (q - center for q in pts))
    cam.location = center + d * dist
    cam.rotation_euler = rot.to_euler()
    cam.data.clip_end = 10000


def no_metadata(sc):
    """Blender stamps renders with metadata (the .blend path among it): none goes into published images."""
    for prop in sc.render.bl_rna.properties:
        if prop.identifier.startswith("use_stamp"):
            setattr(sc.render, prop.identifier, False)


def render(name, size=(1600, 1000)):
    if ONLY and ONLY != name:
        return
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'
    sc.render.resolution_x, sc.render.resolution_y = size
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = 'JPEG'  # soft gradients: a PNG would be ~1.5 MB each
    sc.render.image_settings.color_mode = 'RGB'
    sc.render.image_settings.quality = 90
    no_metadata(sc)
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.color = (0.80, 0.81, 0.83)  # a light neutral grey: reads on GitHub's light and dark themes
    shading = sc.display.shading
    shading.background_type = 'WORLD'
    shading.light = 'STUDIO'
    shading.color_type = 'MATERIAL'
    shading.show_cavity = True
    shading.cavity_type = 'BOTH'
    shading.show_shadows = True
    shading.shadow_intensity = 0.35
    shading.show_object_outline = True
    shading.object_outline_color = (0.08, 0.08, 0.1)
    sc.display.render_aa = '16'
    sc.render.filepath = str(IMAGES / f"{name}.jpg")
    bpy.ops.render.render(write_still=True)
    print("RENDERED", name)


def printed(result, master=False):
    """The pieces of a result worth showing: everything but the silicone preview."""
    return [o for o in result.objects if o.type == 'MESH' and "preview" not in o.name
            and (master or not o.name.endswith("_Master"))]


def wanted(*names):
    return ONLY is None or ONLY in names


# --- shots --------------------------------------------------------------------------

def place(obj, angle, target):
    """Turn an object about Z and move it so its box center lands on `target` (render only, nothing is saved)."""
    obj.rotation_euler.z = math.radians(angle)
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    center = sum(pts, Vector()) / len(pts)
    obj.location += Vector(target) - center
    bpy.context.view_layer.update()


def scene():
    """A direct mold with a core for an object of the open file, laid open like a book: both halves turned so their
    cut faces (the cavity, the pour channel and the vents) face the camera, the core between them, the cast part in
    front."""
    src = bpy.data.objects.get(OBJECT or "")
    if src is None:
        sys.exit(f"make_renders: no object {OBJECT!r} in this file (pass object=<name>)")
    bpy.context.scene.easymold_props.explode = True
    result = generate(src)
    by_end = {o.name.rsplit("_", 1)[-1]: o for o in result.objects if o.type == 'MESH'}
    left, right, core = by_end["X-"], by_end["X+"], by_end["Core"]
    master = next(o for o in result.objects if o.name.endswith("_Master"))
    width = max(left.dimensions.y, right.dimensions.y)
    place(left, -62, (-width * 0.62, 0, 0))
    place(right, 62, (width * 0.62, 0, 0))
    place(core, 0, (0, -width * 0.15, core.dimensions.z * 0.5))
    place(master, 0, (0, -width * 0.62, 0))
    pieces = [left, right, core, master]
    shown(pieces)
    for name, size in (("hero", (1920, 1080)), ("featured", (1920, 1080)), ("direct_core", (1600, 1000))):
        frame(pieces, direction=(0.0, -1.0, 0.55), lens=60, margin=1.04)
        render(name, size)


def synthetic():
    if wanted("silicone_box"):
        fresh(mold_type='SILICONE_BOX', explode=True)
        obj = pawn()
        result = generate(obj)
        pieces = printed(result)
        shown(pieces)
        frame(pieces)
        render("silicone_box")

    if wanted("silicone_shell"):
        fresh(mold_type='SILICONE_SHELL', mold_parts='4', explode=True, silicone_anchors=True)
        obj = make_pumpkin()
        result = generate(obj)
        pieces = printed(result)
        shown(pieces)
        frame(pieces, direction=(1.0, -1.1, 0.9))
        render("silicone_shell")

    if wanted("two_part"):
        fresh(mold_type='SILICONE_TWO_PART', explode=True)
        obj = egg()
        result = generate(obj)
        pieces = printed(result)
        shown(pieces)
        frame(pieces)
        render("two_part")

    if wanted("tray"):
        fresh(mold_type='DIRECT', mold_parts='2', split_axis='X', explode=True)
        a = fluted_candle("Candle", r=11, h=30)
        b = pawn("Pawn", 0.75)
        b.location.x = 34
        c = egg("Egg")
        c.scale = (0.6, 0.6, 0.6)
        c.location = (-30, 0, -8)
        bpy.context.view_layer.update()
        result = generate(a, extras=(b, c))
        half = next(o for o in result.objects if o.name.endswith("_X-"))  # its cut face shows the three cavities
        shown([half])
        frame([half], direction=(1.0, -0.45, 0.55), margin=1.02)
        render("tray")

    if wanted("curved_parting"):
        fresh(mold_type='DIRECT', mold_parts='2', split_axis='Y', curved_parting=True, explode=True)
        obj = bent()
        result = generate(obj)
        half = next(o for o in result.objects if o.name.endswith("_Y-"))  # its curved parting face
        shown([half])
        frame([half], direction=(0.35, 1.0, 0.75), margin=1.02)
        render("curved_parting")

    if wanted("pour_vents"):
        # One half of a direct mold, seen on its cut face: the pour channel from below with its round elbow and
        # narrow tip, and vents leaning away from the part.
        fresh(mold_type='DIRECT', mold_parts='2', split_axis='X', explode=True, pour_entry='BOTTOM', pour_tip=1.0,
              vent_tip=0.6, vent_route='AWAY', vent_count=3)
        obj = fluted_candle()
        result = generate(obj)
        half = next(o for o in result.objects if o.name.endswith("_X+"))
        shown([half])
        frame([half], direction=(-1.0, -0.35, 0.35), margin=1.0)
        render("pour_vents")

    if wanted("bed_split"):
        fresh(mold_type='DIRECT', mold_parts='2', split_axis='X', explode=True, split_to_fit=True,
              bed_x=70.0, bed_y=70.0, bed_z=120.0)
        obj = fluted_candle("Candle", r=26, h=70)
        result = generate(obj)
        pieces = printed(result, master=False)
        shown(pieces)
        frame(pieces, direction=(1.0, -1.3, 0.9))
        render("bed_split")


def logo():
    """logo.png (512, README header) and icon.png (256, extensions platform) from docs/EasyMoldLogo.png."""
    for name, size in (("logo", 512), ("icon", 256)):
        img = bpy.data.images.load(str(ROOT / "docs" / "EasyMoldLogo.png"))
        img.scale(size, size)
        img.filepath_raw = str(IMAGES / f"{name}.png")
        img.file_format = 'PNG'
        img.save()
        bpy.data.images.remove(img)
        print("RENDERED", name, size)


if MODE == "logo":
    logo()
elif MODE == "scene":
    scene()
else:
    synthetic()
print("DONE")
