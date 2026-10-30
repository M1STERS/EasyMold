# extensions.blender.org listing

Text and images to paste into the upload form at https://extensions.blender.org/submit/ (Blender ID needed).
The package is the release zip, `easymold-1.0.0.zip`; the form reads the name, tagline, version, license, tags and
permissions from its manifest.

## Images to upload

- Icon: `docs/images/icon.png` (256 × 256)
- Featured image: `docs/images/featured.jpg` (1920 × 1080)
- Screenshots, in this order, with their captions:
  1. `docs/images/direct_core.jpg`: "Direct mold with a core for a hot end silicone sock, laid open"
  2. `docs/images/pour_vents.jpg`: "Pour channel from below with a round elbow and a narrow tip"
  3. `docs/images/silicone_shell.jpg`: "Shell mother mold in four parts with silicone anchors"
  4. `docs/images/two_part.jpg`: "Two-part silicone mold: frame and parting board"
  5. `docs/images/ui_preview.png`: "Live preview overlay"
  6. `docs/images/ui_layout.png`: "Editing the layout: every key, vent and pour hole is a marker"
  7. `docs/images/bed_split.jpg`: "Pieces split to fit the print bed, with pin holes"

Check the exact sizes the form asks for when uploading.

## Description

EasyMold turns a mesh into a mold you can 3D print. Select the object, choose a mold type, press **Generate Mold**,
and export the pieces for your slicer. Your object is never modified, and the mold is placed in its own collection.

**Mold types**

- **Direct Mold (Cast Part)**: a block mold, in 1, 2 or 4 parts, for casting the object itself in silicone. For
  hollow parts it adds a printable core.
- **Silicone Mold: Box**: a box and base for pouring a block silicone mold around a master.
- **Silicone Mold: Shell**: a shell that follows the master's shape, for a thin silicone mold. It has flanges, keys
  and silicone anchors.
- **Silicone Mold: 2-Part**: a frame and parting board, for masters with no flat side.

**What it takes care of**

- Split planes, or a curved parting surface.
- Registration keys with printing tolerance.
- Clamping with screws (with nut traps), clips or elastic-band grooves.
- A pour hole with a funnel or a chamfer, or an entry from below with a round elbow.
- Narrow tips where the pour hole and vents meet the part.
- Air vents that can lean away from thin walls.
- An automatic core for hollow parts.
- Several objects in one mold.
- Mesh repair, blocker walls for through-holes, and undercut analysis.
- Shrinkage compensation.
- Pieces that are too big for your print bed: it flags them and can split them, with holes for alignment pins.
- A material calculator: silicone volume, weight, the A + B mix and cost.
- Material presets.
- Export to STL or 3MF, with each piece placed on its best flat face.

**Live preview and layout editing**

**Preview** draws the mold in the viewport as you change the settings, without adding anything to the scene.
**Edit Layout** turns every key, bolt, pour hole and vent into a marker you can add, move, resize or delete.

**Where to find it**: 3D Viewport → Sidebar (N) → **EasyMold** tab. Work in millimeters (1 unit = 1 mm).

**Permissions**: EasyMold writes mold files only when you press **Export for Printing**, to the folder you choose
(your own presets are saved by Blender's preset system).

**Source, documentation and issues**: https://github.com/M1STERS/EasyMold

**Support the project**: EasyMold is free and open source (GPL-3.0-or-later). If it helps you, you can support its
development on GitHub Sponsors: https://github.com/sponsors/M1STERS
