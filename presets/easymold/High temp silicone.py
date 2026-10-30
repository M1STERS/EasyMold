# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
props = bpy.context.scene.easymold_props

# Direct mold to cast a 3D printer hot end sock in high-temperature silicone.
props.mold_type = 'DIRECT'
props.mold_parts = '2'
props.split_axis = 'X'
props.margin = 6.0
props.use_keys = True
props.clamp_method = 'SCREW'
props.use_sprues = True
props.pour_style = 'FUNNEL'
props.sprue_radius = 2.0
props.vent_count = 2
props.vent_radius = 1.0
props.silicone_density = 1.2
props.mix_a = 1.0
props.mix_b = 1.0
props.mix_by = 'WEIGHT'
props.shrinkage = 0.0
