# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
props = bpy.context.scene.easymold_props

# Block silicone mold for melt-and-pour soap.
props.mold_type = 'SILICONE_BOX'
props.silicone_thickness = 5.0
props.wall_thickness = 2.4
props.flat_base = True
props.clamp_method = 'SCREW'
props.silicone_density = 1.1
props.mix_a = 1.0
props.mix_b = 1.0
props.mix_by = 'WEIGHT'
props.cast_density = 1.05
props.shrinkage = 0.0
