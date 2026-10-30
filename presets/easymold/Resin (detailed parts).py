# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
props = bpy.context.scene.easymold_props

# Thin shell silicone mold in 2 parts for epoxy/PU resin copies; saves silicone.
props.mold_type = 'SILICONE_SHELL'
props.mold_parts = '2'
props.silicone_thickness = 5.0
props.wall_thickness = 2.4
props.silicone_anchors = True
props.clamp_method = 'CLIP'
props.use_sprues = True
props.pour_style = 'FUNNEL'
props.silicone_density = 1.1
props.mix_a = 1.0
props.mix_b = 1.0
props.mix_by = 'WEIGHT'
props.cast_density = 1.1
props.shrinkage = 0.5
