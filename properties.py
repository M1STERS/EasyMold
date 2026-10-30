# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

# Distances are plain scene units. STL files exported in millimeters import
# at 1 unit = 1 mm, so the defaults below are chosen in millimeters.


def _poll_mesh(self, obj):
    return obj.type == 'MESH'


class EasyMoldProperties(bpy.types.PropertyGroup):
    mold_type: bpy.props.EnumProperty(
        name="Mold Type",
        description="Select the type of mold to generate",
        items=[
            ('SILICONE_BOX', "Silicone Mold: Box",
             "Printable box + base to pour a block silicone mold around the master (e.g. a candle)"),
            ('SILICONE_SHELL', "Silicone Mold: Shell",
             "Printable shell that follows the master's shape to pour a thin silicone mold and save silicone"),
            ('SILICONE_TWO_PART', "Silicone Mold: 2-Part",
             "Printable frame + parting board to pour a silicone mold in two halves, for masters with no flat "
             "side (figures, balls): pour one half, flip, remove the board, pour the other"),
            ('DIRECT', "Direct Mold (Cast Part)",
             "Printable block mold to cast the object itself in silicone (e.g. a hot end sock)"),
        ],
        default='SILICONE_BOX',
    )

    # --- Silicone mold (mold for the mold) ---
    silicone_thickness: bpy.props.FloatProperty(
        name="Silicone Thickness",
        description="Thickness of the silicone layer around the master",
        default=5.0, min=0.5, soft_max=50.0,
    )
    wall_thickness: bpy.props.FloatProperty(
        name="Wall Thickness",
        description="Thickness of the printed walls that contain the silicone",
        default=3.0, min=0.4, soft_max=20.0,
    )
    base_thickness: bpy.props.FloatProperty(
        name="Base Thickness",
        description="Thickness of the printed base plate",
        default=3.0, min=0.4, soft_max=20.0,
    )
    lip_height: bpy.props.FloatProperty(
        name="Lip Height",
        description="Height of the lip/groove that aligns the walls with the base",
        default=2.0, min=0.0, soft_max=10.0,
    )
    include_master: bpy.props.BoolProperty(
        name="Print Master on Base",
        description="Join the object to the base so master and base print as one piece. "
                    "Disable it to glue a physical original instead. The face on -Z becomes the pour opening. "
                    "2-part mold: print the master with its sprue and vents attached (disabled: only the sprue "
                    "and vents, to glue onto the original)",
        default=True,
    )
    repair_mode: bpy.props.EnumProperty(
        name="Repair",
        description="Fix the copy of the object used for the mold (the original is never modified)",
        items=[
            ('CLEAN', "Clean", "Merge duplicate vertices and fill holes in the surface"),
            ('REMESH', "Rebuild", "Clean, then rebuild a watertight surface with voxels (for broken or "
                                  "self-intersecting meshes; uses the Resolution setting)"),
            ('NONE', "None", "Use the object as it is"),
        ],
        default='CLEAN',
    )
    fill_holes: bpy.props.BoolProperty(
        name="Block Through-Holes",
        description="Close holes that go right through the object (handles, rings) with a thin wall, so "
                    "the silicone doesn't lock around them. Trim the thin flash off the casts afterwards",
        default=True,
    )
    blocker_thickness: bpy.props.FloatProperty(
        name="Blocker Thickness",
        description="Thickness of the walls that close through-holes",
        default=1.0, min=0.3, soft_max=5.0,
    )
    silicone_anchors: bpy.props.BoolProperty(
        name="Silicone Anchors",
        description="Dimples on the inside of the shell: the silicone fills them and always sits back "
                    "in the exact same place",
        default=True,
    )
    analyze_undercuts: bpy.props.BoolProperty(
        name="Analyze Undercuts",
        description="Color the layout master: green releases, yellow has no draft, red is an undercut",
        default=True,
    )
    clamp_method: bpy.props.EnumProperty(
        name="Clamping",
        description="How the mold parts are held together (and to the base) while the material cures",
        items=[
            ('SCREW', "Screws", "Tabs with bolt holes and nut traps"),
            ('CLIP', "Clips", "Flanges along the splits and around the base to grip with binder clips"),
            ('BAND', "Elastic Bands", "Grooves that keep rubber bands in place "
                                      "(box and direct molds; can't hold the base of a shell mold)"),
            ('NONE', "None", "Nothing: use your own clamps or tape"),
        ],
        default='SCREW',
    )
    bolt_diameter: bpy.props.FloatProperty(
        name="Bolt Diameter",
        description="Bolt size for the clamping tabs (3 = M3); zip ties also fit through the holes",
        default=3.0, min=1.5, soft_max=8.0,
    )
    clip_size: bpy.props.FloatProperty(
        name="Clip Size",
        description="Width of the binder clips (15, 19, 25, 32 mm...): sets how thick the flanges can be "
                    "so the clip still closes on them",
        default=25.0, min=10.0, soft_max=51.0,
    )
    band_width: bpy.props.FloatProperty(
        name="Band Width",
        description="Width of the rubber bands (the grooves are a bit wider)",
        default=6.0, min=1.0, soft_max=20.0,
    )
    shrinkage: bpy.props.FloatProperty(
        name="Shrinkage",
        description="How much the cast material shrinks as it sets: the master is enlarged so the copies come "
                    "out at the original size (resin ~0.5-2%, wax ~1-3%, silicone ~0.1%). Recreate the layout "
                    "after changing it",
        default=0.0, min=0.0, max=20.0, soft_max=5.0, subtype='PERCENTAGE', precision=2,
    )
    auto_orient: bpy.props.BoolProperty(
        name="Auto Orient",
        description="Rotate the object so its largest flat face rests on the base "
                    "(for a 1-part direct mold, faces up as the open side). The original is not modified",
        default=True,
    )
    flat_base: bpy.props.BoolProperty(
        name="Flat Pour Opening",
        description="If the bottom of the object is not flat, add a pour plug under it so it prints flat on the base "
                    "and leaves a flat pour opening in the silicone",
        default=True,
    )
    plug_height: bpy.props.FloatProperty(
        name="Plug Height",
        description="Height of the pour plug between the base and the object",
        default=5.0, min=0.5, soft_max=30.0,
    )
    plug_min_width: bpy.props.FloatProperty(
        name="Min Opening Width",
        description="Minimum width of the flat pour opening",
        default=10.0, min=1.0, soft_max=50.0,
    )
    voxel_size: bpy.props.FloatProperty(
        name="Resolution",
        description="Voxel size used to compute the offset surfaces (smaller = more detail, slower)",
        default=0.5, min=0.05, soft_max=5.0,
    )
    parting_auto: bpy.props.BoolProperty(
        name="Auto Parting Line",
        description="Put the parting line (where the two silicone halves meet) at the widest section of the master",
        default=True,
    )
    parting_height: bpy.props.FloatProperty(
        name="Parting Height",
        description="Height of the parting line, from the bottom (0%) to the top (100%) of the master",
        default=50.0, min=5.0, max=95.0, subtype='PERCENTAGE',
    )
    fast_surfaces: bpy.props.BoolProperty(
        name="Fast Surfaces",
        description="Build the shell and silicone surfaces with big faces where they are nearly flat: about 20 times "
                    "fewer faces and much faster, with ~0.1 mm of error. Disable for the exact voxel surface",
        default=True,
    )
    flange_width: bpy.props.FloatProperty(
        name="Flange Width",
        description="Width of the flanges along the splits of a shell (hold the keys, bolts and clips) and, "
                    "with clips, of the flange around the foot of the mold",
        default=8.0, min=0.0, soft_max=30.0,
    )
    flange_thickness: bpy.props.FloatProperty(
        name="Flange Thickness",
        description="Thickness of the flange on each half",
        default=3.0, min=0.4, soft_max=15.0,
    )

    # --- Several objects in one mold (tray) ---
    tray_arrange: bpy.props.EnumProperty(
        name="Arrange",
        description="How several selected objects are placed in one mold (the active object stays where it is)",
        items=[
            ('ROW', "Row", "Side by side in a row, lined up so the mold works for all of them"),
            ('GRID', "Grid", "In a compact grid (molds that are not split; otherwise a row)"),
            ('KEEP', "As Placed", "Where they are in the scene (they must not touch)"),
        ],
        default='ROW',
    )
    tray_gap: bpy.props.FloatProperty(
        name="Spacing",
        description="Closest distance between the objects' surfaces (mold material between their cavities, at "
                    "least 1 mm). In a shell mold, between their silicone layers: 0 = the silicone of one touches "
                    "the silicone of the next; negative = they share silicone, down to minus the silicone "
                    "thickness",
        default=10.0, min=-50.0, soft_min=-10.0, soft_max=50.0,
    )

    # --- Direct mold ---
    margin: bpy.props.FloatProperty(
        name="Block Margin",
        description="Thickness of mold material between the object's bounding box and the outside of the block",
        default=10.0, min=1.0, soft_max=50.0,
    )
    core_object: bpy.props.PointerProperty(
        name="Core",
        description="Optional mesh that fills the hollow inside the part (e.g. a heater block shape). "
                    "It becomes a separate printed core; let it stick out of the block so it can be held",
        type=bpy.types.Object,
        poll=_poll_mesh,
    )
    curved_parting: bpy.props.BoolProperty(
        name="Curved Parting",
        description="Split along the object's outline instead of a flat plane: the parting surface follows its "
                    "widest edge as seen from the way the mold opens and flattens out toward the outside (keys, "
                    "bolts and clips stay on a flat rim). Editable in the layout. Direct molds in 2 parts and "
                    "2-part silicone molds",
        default=False,
    )
    auto_core: bpy.props.BoolProperty(
        name="Auto Core",
        description="Find the hollows of the part that the opened block can't let go of (like the inside of a "
                    "sock) and make a printed core for each, flush with the outside of the block and held by "
                    "key pegs. Needs a mold in 2 or 4 parts; not used when a Core object is set",
        default=False,
    )

    # --- Splitting ---
    split_axis: bpy.props.EnumProperty(
        name="Split Axis",
        description="Axis perpendicular to the main split plane",
        items=[
            ('X', "X (Left/Right)", ""),
            ('Y', "Y (Front/Back)", ""),
            ('Z', "Z (Top/Bottom)", ""),
        ],
        default='X',
    )
    mold_parts: bpy.props.EnumProperty(
        name="Parts",
        description="Number of parts for the mold",
        items=[
            ('1', "1 Part", "No split (direct mold: open top)"),
            ('2', "2 Parts", "Single split"),
            ('4', "4 Parts", "Split along two perpendicular planes (a shell mold always uses X and Y)"),
        ],
        default='2',
    )

    use_keys: bpy.props.BoolProperty(
        name="Registration Keys",
        description="Add pegs and holes to align the mold parts",
        default=True,
    )
    key_shape: bpy.props.EnumProperty(
        name="Key Shape",
        items=[
            ('SPHERE', "Sphere", "Half-sphere bumps"),
            ('TAPERED', "Tapered Peg", "Cone-shaped pegs, easier to fit and print"),
        ],
        default='SPHERE',
    )
    key_radius: bpy.props.FloatProperty(
        name="Key Radius",
        description="Radius of the registration keys (0 = automatic from the wall/flange size)",
        default=0.0, min=0.0, soft_max=15.0,
    )
    key_tolerance: bpy.props.FloatProperty(
        name="Key Tolerance",
        description="Clearance added to the key holes so printed parts fit",
        default=0.2, min=0.0, soft_max=1.0,
    )

    use_sprues: bpy.props.BoolProperty(
        name="Pour Hole & Vents",
        description="Cut a pouring hole on the split line at the highest point, and air vents at other high points",
        default=True,
    )
    pour_style: bpy.props.EnumProperty(
        name="Pour Hole Style",
        items=[
            ('FUNNEL', "Sprue Funnel", "Raised funnel (sprue cup) on top of the mold: easier to pour and keeps a reserve"),
            ('HOLE', "Straight Hole", "Plain cylindrical hole"),
        ],
        default='FUNNEL',
    )
    pour_entry: bpy.props.EnumProperty(
        name="Pour Entry",
        description="Where the material enters the cast part (direct mold)",
        items=[
            ('BOTTOM', "Parting Line, Bottom",
             "A channel runs down a split plane beside the part and enters it low on its side: it fills from below "
             "(fewer bubbles, the air leaves through the vents on top) and the mark is on the seam the mold leaves "
             "anyway"),
            ('TOP', "Top", "Straight down into the top of the part (marks its top)"),
        ],
        default='BOTTOM',
    )
    sprue_height: bpy.props.FloatProperty(
        name="Sprue Height",
        description="How far the funnel rises above the mold (0 = funnel cut into the mold surface)",
        default=10.0, min=0.0, soft_max=40.0,
    )
    sprue_radius: bpy.props.FloatProperty(
        name="Pour Hole Radius",
        description="Radius of the pouring hole",
        default=3.0, min=0.3, soft_max=15.0,
    )
    vent_radius: bpy.props.FloatProperty(
        name="Vent Radius",
        description="Radius of the air vents (small: they only let the air out)",
        default=1.5, min=0.3, soft_max=5.0,
    )
    vent_count: bpy.props.IntProperty(
        name="Vents",
        description="Number of air vents",
        default=2, min=0, max=8,
    )
    pour_tip: bpy.props.FloatProperty(
        name="Entry Tip",
        description="Radius of the pour hole or channel where it touches the cast part, narrowing in a short cone: a "
                    "smaller mark on the part (0 = as wide as the hole)",
        default=0.0, min=0.0, soft_max=3.0,
    )
    vent_tip: bpy.props.FloatProperty(
        name="Vent Tip",
        description="Radius of the vents where they touch the cast part, narrowing in a short cone: a smaller mark "
                    "(0 = as wide as the vent)",
        default=0.0, min=0.0, soft_max=2.0,
    )
    vent_route: bpy.props.EnumProperty(
        name="Vent Path",
        description="How the vents go up from the part (direct mold)",
        items=[
            ('STRAIGHT', "Straight", "Straight up from the part"),
            ('AWAY', "Away from Part", "Leans away where a straight vent would run close beside the part (e.g. "
                                       "next to a thin wall), so it doesn't leave a groove along its side"),
        ],
        default='STRAIGHT',
    )

    # --- Output ---
    boolean_solver: bpy.props.EnumProperty(
        name="Boolean Solver",
        items=[
            ('AUTO', "Auto", "Manifold if the object is closed, Exact otherwise"),
            ('EXACT', "Exact", "Slow but tolerant"),
            ('MANIFOLD', "Manifold", "Much faster and cleaner, requires a closed (manifold) mesh"),
        ],
        default='AUTO',
    )
    explode: bpy.props.BoolProperty(
        name="Separate Parts",
        description="Move the parts apart so they are easy to see",
        default=True,
    )

    # --- Material calculator ---
    silicone_density: bpy.props.FloatProperty(
        name="Density", description="Density of the mixed silicone (g/ml); most are 1.05 - 1.2",
        default=1.1, min=0.1, soft_max=3.0, precision=2,
    )
    mix_a: bpy.props.FloatProperty(
        name="A", description="Parts of component A in the mix", default=1.0, min=0.0, soft_max=100.0,
    )
    mix_b: bpy.props.FloatProperty(
        name="B", description="Parts of component B (catalyst) in the mix", default=1.0, min=0.0, soft_max=100.0,
    )
    mix_by: bpy.props.EnumProperty(
        name="Mix By",
        items=[
            ('WEIGHT', "Weight", "The A:B ratio is by weight (grams)"),
            ('VOLUME', "Volume", "The A:B ratio is by volume (ml)"),
        ],
        default='WEIGHT',
    )
    silicone_price: bpy.props.FloatProperty(
        name="Price / kg", description="Price of 1 kg of silicone (A + B), to estimate the cost (0 = don't show)",
        default=0.0, min=0.0, soft_max=200.0,
    )
    waste: bpy.props.FloatProperty(
        name="Extra", description="Extra to mix for what stays in the cup and on the stick",
        default=10.0, min=0.0, soft_max=50.0, subtype='PERCENTAGE',
    )
    cast_density: bpy.props.FloatProperty(
        name="Cast Density",
        description="Density of what will be cast in the silicone mold (wax ~0.9, soap ~1.05, resin ~1.1, "
                    "plaster ~1.6 g/ml)",
        default=0.9, min=0.1, soft_max=10.0, precision=2,
    )
    cast_price: bpy.props.FloatProperty(
        name="Cast Price / kg", description="Price of 1 kg of the cast material (0 = don't show)",
        default=0.0, min=0.0, soft_max=200.0,
    )

    # --- Print bed ---
    bed_x: bpy.props.FloatProperty(
        name="Bed X", description="Width of the printer's build plate",
        default=220.0, min=20.0, soft_max=500.0,
    )
    bed_y: bpy.props.FloatProperty(
        name="Bed Y", description="Depth of the printer's build plate",
        default=220.0, min=20.0, soft_max=500.0,
    )
    bed_z: bpy.props.FloatProperty(
        name="Height", description="Maximum print height",
        default=250.0, min=20.0, soft_max=500.0,
    )
    split_to_fit: bpy.props.BoolProperty(
        name="Split to Fit",
        description="Cut pieces that don't fit the bed (as they will be printed) in halves, with holes for "
                    "1.75 mm filament pins to line them up when gluing",
        default=False,
    )

    # --- Export ---
    export_format: bpy.props.EnumProperty(
        name="Format",
        items=[
            ('3MF', "3MF", "One file with every piece laid out on the plate (millimeters)"),
            ('STL', "STL", "One file per piece"),
        ],
        default='3MF',
    )
    export_folder: bpy.props.StringProperty(
        name="Folder",
        description="Where the files go (// = next to the .blend file)",
        default="//EasyMold_export",
        subtype='DIR_PATH',
        options={'PATH_SUPPORTS_BLEND_RELATIVE'},
    )
    export_master: bpy.props.BoolProperty(
        name="Include Master",
        description="Also export the (oriented, repaired) master on its own, e.g. to print it separately",
        default=False,
    )


def _get_radius(obj):
    return obj.empty_display_size * max(abs(s) for s in obj.scale) if obj.type == 'EMPTY' else 0.0


def _set_radius(obj, value):
    obj.empty_display_size = value
    obj.scale = (1.0, 1.0, 1.0)


def register():
    bpy.utils.register_class(EasyMoldProperties)
    bpy.types.Scene.easymold_props = bpy.props.PointerProperty(type=EasyMoldProperties)
    # Size of a layout marker (key, pour hole, vent): its display size times its scale, set in mm in the panel.
    bpy.types.Object.easymold_radius = bpy.props.FloatProperty(
        name="Radius", description="Radius of this element (scaling it with S changes it too)",
        get=_get_radius, set=_set_radius, min=0.1, soft_max=20.0)
    # Progress of the running operation, shown in the panel.
    wm = bpy.types.WindowManager
    wm.easymold_running = bpy.props.BoolProperty(default=False)
    wm.easymold_progress = bpy.props.FloatProperty(default=0.0, min=0.0, max=1.0)
    wm.easymold_status = bpy.props.StringProperty(default="")


def unregister():
    wm = bpy.types.WindowManager
    del wm.easymold_status
    del wm.easymold_progress
    del wm.easymold_running
    del bpy.types.Object.easymold_radius
    del bpy.types.Scene.easymold_props
    bpy.utils.unregister_class(EasyMoldProperties)
