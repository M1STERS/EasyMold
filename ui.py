# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from . import operators, preview, utils


class EASYMOLD_MT_presets(bpy.types.Menu):
    bl_label = "Presets"
    preset_subdir = operators.PRESET_SUBDIR
    preset_operator = "script.execute_preset"
    draw = bpy.types.Menu.draw_preset


def _flags(props):
    is_box = props.mold_type == 'SILICONE_BOX'
    two = props.mold_type == 'SILICONE_TWO_PART'
    return {
        'direct': props.mold_type == 'DIRECT',
        'shell': props.mold_type == 'SILICONE_SHELL',
        'box': is_box,
        'two': two,
        'silicone': props.mold_type != 'DIRECT',
        'split': not (is_box or two) and props.mold_parts != '1',
    }


class VIEW3D_PT_easymold(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'EasyMold'
    bl_label = 'Mold Generator'

    def draw(self, context):
        layout = self.layout
        props = context.scene.easymold_props
        wm = context.window_manager

        if wm.easymold_running:
            layout.progress(factor=wm.easymold_progress, type='BAR', text=wm.easymold_status)
            layout.label(text="Press Esc to cancel", icon='CANCEL')
            return

        source = utils.resolve_source(context.active_object)
        if source is None:
            layout.label(text="Select a mesh object", icon='INFO')
        else:
            extras = utils.tray_members(source, context.selected_objects, props)
            if extras:
                box = layout.box()
                box.label(text=f"{source.name} + {len(extras)} more in one mold", icon='MESH_DATA')
                box.row(align=True).prop(props, "tray_arrange", expand=True)
                box.prop(props, "tray_gap")
                limit = -props.silicone_thickness if props.mold_type == 'SILICONE_SHELL' else 1.0
                if props.tray_gap < limit:
                    box.label(text=f"Spacing is limited to {limit:g}", icon='ERROR')
            else:
                layout.label(text=source.name, icon='MESH_DATA')
        row = layout.row(align=True)
        row.menu("EASYMOLD_MT_presets", text=bpy.types.EASYMOLD_MT_presets.bl_label, icon='PRESET')
        row.operator("easymold.add_preset", text="", icon='ADD')
        row.operator("easymold.add_preset", text="", icon='REMOVE').remove_active = True
        layout.prop(props, "mold_type", text="")

        has_layout = source is not None and utils.find_layout(source) is not None
        box = layout.box()
        if has_layout:
            box.label(text="Layout: click an element to edit it", icon='EDITMODE_HLT')
            self.draw_elements(box, context, props, utils.find_layout(source))
            self.draw_selected(box, context, props)
            row = box.row(align=True)
            row.operator("object.easymold_edit_layout", text="Show", icon='HIDE_OFF').reset = False
            row.operator("object.easymold_edit_layout", text="Reset", icon='FILE_REFRESH').reset = True
            row.operator("object.easymold_remove_layout", text="", icon='TRASH')
        else:
            box.operator("object.easymold_edit_layout", text="Edit Layout (optional)", icon='EDITMODE_HLT').reset = True

        self.draw_preview(layout, wm)
        row = layout.row()
        row.scale_y = 1.5
        row.operator("object.easymold_generate", text="Generate Mold", icon='MOD_BOOLEAN')

    @staticmethod
    def draw_elements(box, context, props, lay):
        """One row per kind of element the mold uses: how many, a + to add one, and a button per element."""
        f = _flags(props)
        kinds = []
        if not f['box'] and props.use_keys:
            kinds.append(('KEY', "Keys"))
        if props.clamp_method == 'SCREW' and (f['direct'] or f['shell']) and f['split']:
            kinds.append(('BOLT', "Bolts"))
        if not f['box'] and props.use_sprues:
            kinds += [('POUR', "Pour Holes"), ('VENT', "Vents")]
        if f['silicone']:
            kinds.append(('BLOCKER', "Blockers"))
        active = context.active_object
        col = box.column(align=True)
        for role, title in kinds:
            items = sorted((o for o in lay.objects if o.get(utils.ROLE) == role), key=lambda o: o.name)
            row = col.row(align=True)
            row.label(text=f"{title} ({len(items)})")
            row.operator("object.easymold_add_marker", text="Add", icon='ADD').role = role
            if items:
                grid = col.grid_flow(columns=6, align=True)
                for n, obj in enumerate(items, 1):
                    op = grid.operator("object.easymold_select_element", text=str(n), depress=obj == active)
                    op.name = obj.name
            col.separator()
        others = [o for o in lay.objects if o.get(utils.ROLE) in ('CORE', 'PARTING_SURFACE', 'PARTING')]
        if others:
            row = col.row(align=True)
            for obj in others:
                op = row.operator("object.easymold_select_element", text=utils.ROLE_NAMES[obj.get(utils.ROLE)],
                                  depress=obj == active)
                op.name = obj.name

    @staticmethod
    def draw_selected(box, context, props):
        """The selected element: what it is, its size in mm, and buttons to move / resize / delete it."""
        obj = context.active_object
        if obj is None or utils.layout_of(obj) is None or obj.get(utils.ROLE) not in utils.ROLE_NAMES:
            box.label(text="Nothing selected", icon='RESTRICT_SELECT_ON')
            return
        role = obj.get(utils.ROLE)
        sub = box.box()
        sub.label(text=f"Selected: {utils.ROLE_NAMES[role]}", icon='RESTRICT_SELECT_OFF')
        if role in ('KEY', 'POUR', 'VENT'):
            sub.prop(obj, "easymold_radius")
        elif role == 'BOLT':
            sub.label(text=f"Size: Bolt Diameter ({props.bolt_diameter:g})")
        if role == 'PARTING_SURFACE':
            sub.label(text="Tab, then move its vertices", icon='INFO')
            sub.label(text="(O = proportional editing)")
        row = sub.row(align=True)
        row.operator("object.easymold_transform_element", text="Move", icon='VIEW_PAN').mode = 'MOVE'
        if role in ('KEY', 'POUR', 'VENT', 'BLOCKER'):
            row.operator("object.easymold_transform_element", text="Size", icon='FULLSCREEN_ENTER').mode = 'SIZE'
        if role == 'BLOCKER':
            row.operator("object.easymold_transform_element", text="Rotate", icon='DRIVER_ROTATIONAL_DIFFERENCE'
                         ).mode = 'ROTATE'
        if role in utils.MARKERS + ('BLOCKER', 'CORE'):
            row.operator("object.easymold_delete_element", text="", icon='TRASH')
        hint = {'KEY': "Stays on the split plane", 'BOLT': "Stays on the split plane",
                'VENT': "Stays on the top of the object", 'BLOCKER': "Cover the hole with it",
                'CORE': "Move it only (it keeps its prints)", 'PARTING': "Move it up or down"}.get(role)
        if role == 'POUR':
            below = props.mold_type == 'DIRECT' and props.pour_entry == 'BOTTOM'
            hint = "Where it enters the part, on the split plane" if below else "Stays on the top of the object"
        if hint:
            sub.label(text=hint, icon='INFO')

    @staticmethod
    def draw_preview(layout, wm):
        row = layout.row(align=True)
        row.prop(wm, "easymold_preview", toggle=True, icon='HIDE_OFF' if wm.easymold_preview else 'HIDE_ON')
        row.prop(wm, "easymold_auto_preview", toggle=True, icon='FILE_REFRESH')
        summary, misfit, error, building, progress, step = preview.status()
        col = layout.column(align=True)
        if error:
            col.label(text=error, icon='ERROR')
        if wm.easymold_preview and summary:
            col.label(text=summary, icon='INFO')
        if wm.easymold_preview and misfit:
            col.alert = True
            col.label(text=f"Too big for the bed: {', '.join(misfit[:2])}" + (" ..." if len(misfit) > 2 else ""),
                      icon='ERROR')
        if building:
            row = layout.row(align=True)
            row.progress(factor=progress, type='BAR', text=f"Preview: {step}...")
            row.operator("object.easymold_stop_preview", text="", icon='CANCEL')


class _SubPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'EasyMold'
    bl_parent_id = 'VIEW3D_PT_easymold'

    @classmethod
    def poll(cls, context):
        return not context.window_manager.easymold_running


class VIEW3D_PT_easymold_master(_SubPanel, bpy.types.Panel):
    bl_label = "Master"

    def draw(self, context):
        layout = self.layout
        props = context.scene.easymold_props
        f = _flags(props)
        layout.prop(props, "repair_mode")
        layout.prop(props, "shrinkage")
        if not f['two']:  # a 2-part mold keeps the master as it is
            layout.prop(props, "auto_orient")
        if f['silicone']:
            if not f['two']:
                layout.prop(props, "flat_base")
            if props.flat_base and not f['two']:
                col = layout.column(align=True)
                col.prop(props, "plug_height")
                col.prop(props, "plug_min_width")
            row = layout.row(align=True)
            row.prop(props, "fill_holes")
            if props.fill_holes:
                row.prop(props, "blocker_thickness", text="")
        layout.prop(props, "analyze_undercuts")


class VIEW3D_PT_easymold_mold(_SubPanel, bpy.types.Panel):
    bl_label = "Mold"

    def draw(self, context):
        layout = self.layout
        props = context.scene.easymold_props
        f = _flags(props)
        col = layout.column(align=True)
        if f['direct']:
            col.prop(props, "margin")
            layout.prop(props, "core_object")
            if props.core_object is None:
                layout.prop(props, "auto_core")
                if props.auto_core and props.mold_parts == '1':
                    layout.label(text="Auto Core needs 2 or 4 parts", icon='ERROR')
        else:
            col.prop(props, "silicone_thickness")
            col.prop(props, "wall_thickness")
            col.prop(props, "base_thickness")
            col.prop(props, "lip_height")
            if f['two']:
                layout.prop(props, "include_master", text="Print Master with Sprue")
                box = layout.box()
                box.prop(props, "parting_auto")
                if not props.parting_auto:
                    box.prop(props, "parting_height")
                box.prop(props, "curved_parting")
                box.prop(props, "use_keys")
                if props.use_keys:
                    box.row().prop(props, "key_shape", expand=True)
                    box.prop(props, "key_radius")
            else:
                layout.prop(props, "include_master")
            if f['shell']:
                layout.prop(props, "silicone_anchors")
                row = layout.row(align=True)
                row.prop(props, "voxel_size")
                row.prop(props, "fast_surfaces", text="Fast")
        self.draw_clamping(layout, props, f)

    @staticmethod
    def draw_clamping(layout, props, f):
        box = layout.box()
        box.prop(props, "clamp_method")
        method = props.clamp_method
        if method == 'SCREW':
            box.prop(props, "bolt_diameter")
        elif method == 'CLIP':
            box.prop(props, "clip_size")
            if not (f['shell'] and f['split']):  # otherwise it is in the Parts panel
                box.prop(props, "flange_width")
        elif method == 'BAND':
            if f['shell']:
                box.label(text="Bands can't hold a shell's base", icon='ERROR')
            else:
                box.prop(props, "band_width")
        if f['direct'] and not f['split'] and method != 'NONE':
            box.label(text="A 1-part mold has nothing to clamp", icon='INFO')


class VIEW3D_PT_easymold_parts(_SubPanel, bpy.types.Panel):
    bl_label = "Parts"

    @classmethod
    def poll(cls, context):
        return super().poll(context) and context.scene.easymold_props.mold_type not in ('SILICONE_BOX',
                                                                                         'SILICONE_TWO_PART')

    def draw(self, context):
        layout = self.layout
        props = context.scene.easymold_props
        f = _flags(props)
        layout.row().prop(props, "mold_parts", expand=True)
        if not f['split']:
            return
        if not (f['shell'] and props.mold_parts == '4'):
            layout.prop(props, "split_axis")
        if f['direct'] and props.mold_parts == '2':
            layout.prop(props, "curved_parting")
        if f['shell']:
            if props.mold_parts == '4':
                layout.label(text="Splits along X and Y")
            elif props.split_axis == 'Z':
                layout.label(text="Use X or Y for a shell mold", icon='ERROR')
            col = layout.column(align=True)
            col.prop(props, "flange_width")
            col.prop(props, "flange_thickness")

        box = layout.box()
        box.prop(props, "use_keys")
        if props.use_keys:
            box.row().prop(props, "key_shape", expand=True)
            col = box.column(align=True)
            col.prop(props, "key_radius")
            col.prop(props, "key_tolerance")


class VIEW3D_PT_easymold_pouring(_SubPanel, bpy.types.Panel):
    bl_label = "Pouring"

    @classmethod
    def poll(cls, context):
        f = _flags(context.scene.easymold_props)
        return super().poll(context) and (f['shell'] or f['split'] or f['two'])

    def draw_header(self, context):
        self.layout.prop(context.scene.easymold_props, "use_sprues", text="")

    def draw(self, context):
        layout = self.layout
        props = context.scene.easymold_props
        layout.active = props.use_sprues
        if _flags(props)['direct']:
            layout.prop(props, "pour_entry")
        layout.row().prop(props, "pour_style", expand=True)
        col = layout.column(align=True)
        col.prop(props, "sprue_radius")
        if props.pour_style == 'FUNNEL':
            if _flags(props)['two']:
                col.prop(props, "sprue_height", text="Funnel Depth")  # the funnel is in the silicone
            else:
                col.prop(props, "sprue_height")
        cast = _flags(props)['direct'] or _flags(props)['two']  # the holes touch the cast part
        if cast:
            col.prop(props, "pour_tip")
        col.prop(props, "vent_count")
        if props.vent_count:
            col.prop(props, "vent_radius")
            if cast:
                col.prop(props, "vent_tip")
            if _flags(props)['direct']:
                layout.prop(props, "vent_route")


class VIEW3D_PT_easymold_output(_SubPanel, bpy.types.Panel):
    bl_label = "Output"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        props = context.scene.easymold_props
        col = self.layout.column(align=True)
        col.prop(props, "boolean_solver")
        col.prop(props, "explode")
        box = self.layout.box()
        box.label(text="Print Bed", icon='MESH_GRID')
        col = box.column(align=True)
        col.prop(props, "bed_x")
        col.prop(props, "bed_y")
        col.prop(props, "bed_z")
        box.prop(props, "split_to_fit")
        # Session cache: what Generate keeps to regenerate faster.
        objects, surfaces, size = utils.cache_info()
        box = self.layout.box()
        row = box.row()
        row.label(text="Cache", icon='FILE_CACHE')
        row.operator("object.easymold_clear_cache", text="Clear Cache", icon='TRASH')
        if objects or surfaces or size:
            box.label(text=f"{objects} object(s), {surfaces} surface(s), about {size / 2 ** 20:.0f} MB")
        else:
            box.label(text="Empty")


class VIEW3D_PT_easymold_material(_SubPanel, bpy.types.Panel):
    bl_label = "Material"

    def draw(self, context):
        props = context.scene.easymold_props
        layout = self.layout
        direct = props.mold_type == 'DIRECT'

        col = layout.column(align=True)
        col.prop(props, "silicone_density")
        row = col.row(align=True)
        row.prop(props, "mix_a")
        row.prop(props, "mix_b")
        col.row(align=True).prop(props, "mix_by", expand=True)
        col.prop(props, "waste")
        col.prop(props, "silicone_price")
        if not direct:
            col = layout.column(align=True)
            col.prop(props, "cast_density")
            col.prop(props, "cast_price")

        source = utils.resolve_source(context.active_object)
        result = utils.find_result(source) if source is not None else None
        if result is None:
            layout.label(text="Generate the mold to see the amounts", icon='INFO')
            return
        box = layout.box()
        volume = result.get("easymold_volume", 0.0)
        if volume:
            need = utils.silicone_needs(props, volume)
            box.label(text=f"Silicone: {need['ml']:.0f} ml = {need['g']:.0f} g", icon='MOD_FLUID')
            box.label(text=f"A: {need['a']:.0f} {need['unit']}    B: {need['b']:.0f} {need['unit']}")
            if need['cost']:
                box.label(text=f"Cost: {need['cost']:.2f}")
        cast = result.get("easymold_cast_volume", 0.0)
        if cast and not direct:
            need = utils.cast_needs(props, cast)
            text = f"Each cast: {need['ml']:.0f} ml = {need['g']:.0f} g"
            if need['cost']:
                text += f" ({need['cost']:.2f})"
            box.label(text=text, icon='DUPLICATE')


class VIEW3D_PT_easymold_export(_SubPanel, bpy.types.Panel):
    bl_label = "Export"

    @classmethod
    def poll(cls, context):
        source = utils.resolve_source(context.active_object)
        return super().poll(context) and source is not None and utils.find_result(source) is not None

    def draw(self, context):
        props = context.scene.easymold_props
        layout = self.layout
        layout.row().prop(props, "export_format", expand=True)
        layout.prop(props, "export_folder", text="")
        layout.prop(props, "export_master")
        row = layout.row()
        row.scale_y = 1.3
        row.operator("object.easymold_export", icon='EXPORT')


classes = (
    EASYMOLD_MT_presets,
    VIEW3D_PT_easymold,
    VIEW3D_PT_easymold_master,
    VIEW3D_PT_easymold_mold,
    VIEW3D_PT_easymold_parts,
    VIEW3D_PT_easymold_pouring,
    VIEW3D_PT_easymold_output,
    VIEW3D_PT_easymold_material,
    VIEW3D_PT_easymold_export,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
