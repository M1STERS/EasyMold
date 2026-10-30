# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
props = bpy.context.scene.easymold_props

# Sturdy silicone mold for plaster or concrete pieces (heavy, rough handling).
props.mold_type = 'SILICONE_BOX'
props.silicone_thickness = 8.0
props.wall_thickness = 3.2
props.clamp_method = 'SCREW'
props.silicone_density = 1.12
props.mix_a = 1.0
props.mix_b = 1.0
props.mix_by = 'WEIGHT'
props.cast_density = 1.6
props.shrinkage = 0.0
