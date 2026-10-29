# SPDX-License-Identifier: GPL-3.0-or-later
"""Texel density and UV sanity per (object, material).

px/cm at resolution R = sqrt(uv_area * R^2 / world_area_cm2), area weighted
over the faces that use the material. A material is judged by its worst
object.
"""
import math

import bmesh
import bpy

UV_EPS = 1e-3


class ObjStat:
    __slots__ = ("obj", "uv_area", "world_cm2", "visible", "uv_out", "no_uv")

    def __init__(self, obj, visible):
        self.obj = obj
        self.uv_area = 0.0
        self.world_cm2 = 0.0
        self.visible = visible
        self.uv_out = False
        self.no_uv = False

    def td(self, res):
        if self.world_cm2 <= 0 or self.uv_area <= 0:
            return None
        return math.sqrt(self.uv_area * res * res / self.world_cm2)


def source_uv(mesh):
    """The UV layer the original materials read (active render layer)."""
    for l in mesh.uv_layers:
        if l.active_render:
            return l
    return mesh.uv_layers.active


def mesh_objects(context):
    return [o for o in context.view_layer.objects if o.type == "MESH" and o.data.polygons]


def gather(context, materials):
    """{material name: [ObjStat]} for every mesh object using one of *materials*."""
    wanted = {m.name for m in materials}
    scale = context.scene.unit_settings.scale_length or 1.0
    to_cm2 = (scale * 100.0) ** 2
    out = {n: [] for n in wanted}
    for obj in mesh_objects(context):
        slots = {i: s.material.name for i, s in enumerate(obj.material_slots)
                 if s.material and s.material.name in wanted}
        if not slots:
            continue
        uv = source_uv(obj.data)
        visible = obj.visible_get()
        stats = {i: ObjStat(obj, visible) for i in slots}
        if uv is None:
            for st in stats.values():
                st.no_uv = True
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.transform(obj.matrix_world)
        uvl = bm.loops.layers.uv.get(uv.name) if uv else None
        for f in bm.faces:
            st = stats.get(f.material_index)
            if st is None:
                continue
            st.world_cm2 += f.calc_area() * to_cm2
            if uvl is None:
                continue
            pts = [l[uvl].uv for l in f.loops]
            a = 0.0
            for k in range(len(pts)):
                p, q = pts[k], pts[(k + 1) % len(pts)]
                a += p.x * q.y - q.x * p.y
                if not st.uv_out and not (-UV_EPS <= p.x <= 1 + UV_EPS and -UV_EPS <= p.y <= 1 + UV_EPS):
                    st.uv_out = True
            st.uv_area += abs(a) * 0.5
        bm.free()
        # several slots can hold the same material: merge them
        merged = {}
        for i, st in stats.items():
            name = slots[i]
            m = merged.get(name)
            if m is None:
                merged[name] = st
            else:
                m.uv_area += st.uv_area
                m.world_cm2 += st.world_cm2
                m.uv_out = m.uv_out or st.uv_out
                m.no_uv = m.no_uv or st.no_uv
        for name, st in merged.items():
            out[name].append(st)
    return out


P_IGNORE = "om3d_atlas_td_ignore"   # object: leave out of the density check


def is_ignored(obj):
    return bool(obj.get(P_IGNORE, False))


def worst(stats, res, include_hidden):
    """(td, object name) of the lowest density object, or (None, '').
    Objects flagged 'Ignore in density' are skipped."""
    best = (None, "")
    for st in stats:
        if not st.visible and not include_hidden:
            continue
        if is_ignored(st.obj):
            continue
        td = st.td(res)
        if td is None:
            continue
        if best[0] is None or td < best[0]:
            best = (td, st.obj.name)
    return best


def td_at_tile(td_src, src_res, tile, inset):
    """Density after the source is resampled into a tile with an inset."""
    if not td_src or not src_res:
        return None
    return td_src * max(tile - 2 * inset, 1) / src_res
