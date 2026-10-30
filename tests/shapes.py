# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test objects shared by several tests."""
import math

import bpy
from mathutils import Vector


def make_pumpkin():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=30, segments=96, ring_count=48, location=(0, 0, 0))
    body = bpy.context.active_object
    for v in body.data.vertices:
        co = v.co
        ang = math.atan2(co.y, co.x)
        lobes = 1 + 0.08 * math.cos(8 * ang) * (1 - abs(co.z) / 30)
        v.co = Vector((co.x * lobes, co.y * lobes, co.z * 0.65))
    bpy.ops.mesh.primitive_cylinder_add(radius=4, depth=14, location=(0, 0, 24))
    stem = bpy.context.active_object
    m = body.modifiers.new("u", 'BOOLEAN'); m.object = stem; m.operation = 'UNION'
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(stem)
    body.location.z = 20
    body.name = "Pumpkin"
    return body
