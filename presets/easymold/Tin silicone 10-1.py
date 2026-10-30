# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
props = bpy.context.scene.easymold_props

# Only the mix: tin-cure silicone, 10 parts A to 1 part B by weight.
props.silicone_density = 1.12
props.mix_a = 10.0
props.mix_b = 1.0
props.mix_by = 'WEIGHT'
