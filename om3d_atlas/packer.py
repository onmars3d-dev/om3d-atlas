# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan, plan, pack and revert. Works on the scene state (Scene.om3d_atlas)."""
import json
import os

import bpy
import numpy as np

from . import density, imaging, maps, planner

ATLAS_UV = "AtlasUV"
ATLAS_UV_PREV = "AtlasUV_prev"
UV_NODE = "OM3D Atlas UV"
P_ORIG = "om3d_atlas_orig"        # object: JSON list of original slot materials
P_SRC_UV = "om3d_atlas_src_uv"    # mesh: name of the original UV layer
P_RENAMED = "om3d_atlas_renamed"  # mesh: a pre-existing "AtlasUV" layer was renamed to this
P_SET = "om3d_atlas_set"          # material: set name
P_SOURCES = "om3d_atlas_sources"  # material: comma list of source materials
P_IMAGES = "om3d_atlas_images"    # material: comma list of atlas images
P_ISOLATED = "om3d_atlas_isolated"  # scene: JSON list of objects hidden by Show Only Packed
P_FAKE = "om3d_atlas_fake"        # material: fake user set by Pack (Revert clears it)
P_COLOR = "om3d_atlas_color"      # object: original viewport colour before Preview Colors
P_WRITTEN = "om3d_atlas_written"  # scene: JSON list of files the last Pack wrote (Revert deletes them)

EXT = {"TARGA": ".tga", "PNG": ".png"}
OP_KEY = "OP"

# While > 0, property update callbacks do nothing (scan writes many props at once).
SUPPRESS = 0


def prefs(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def map_types(context):
    return maps.from_prefs(prefs(context))


def settings(state):
    return planner.Settings(int(state.max_size), state.target_td, state.inset, state.max_sets)


def is_atlas_material(mat):
    return mat is not None and P_SET in mat


def packed_objects(context):
    return [o for o in context.view_layer.objects if P_ORIG in o]


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------

def _type_prefixes(context):
    p = prefs(context)
    if p is None:
        return "M_", "T_"
    return p.material_prefix, p.texture_prefix


def _stem(state, set_name):
    prefix = state.prefix.strip()
    core = f"{prefix}_{set_name}" if prefix else set_name
    return core + state.suffix.strip()


def material_name(context, state, set_name):
    return _type_prefixes(context)[0] + _stem(state, set_name)


def texture_name(context, state, set_name, key):
    return f"{_type_prefixes(context)[1]}{_stem(state, set_name)}_{key}"


# --------------------------------------------------------------------------
# Opacity detection
# --------------------------------------------------------------------------

_op_cache = {}


def transparent_fraction(image, tile=(0, 0)):
    """Share of pixels below 0.5 in an opacity map (R channel). Cached.
    For a UDIM image, reads the tile the material uses."""
    key = (image.name, image.filepath, tuple(image.size), tuple(tile) if image.source == "TILED" else None)
    if key not in _op_cache:
        a, _ = imaging.load_source(image, tile)
        _op_cache[key] = float((a[..., 0] < 0.5).mean())
        del a
    return _op_cache[key]


def effective_found(it, found):
    """Drop the OP map when the item is treated as opaque."""
    if OP_KEY in found and not it.masked:
        found = {k: v for k, v in found.items() if k != OP_KEY}
    return found


# --------------------------------------------------------------------------
# Scan
# --------------------------------------------------------------------------

def used_materials(context):
    seen = {}
    for obj in density.mesh_objects(context):
        for s in obj.material_slots:
            m = s.material
            if m and not is_atlas_material(m) and m.name not in seen:
                seen[m.name] = m
    return [seen[k] for k in sorted(seen)]


def _names(objs, n=3):
    return ", ".join(objs[:n]) + ("..." if len(objs) > n else "")


def scan(context):
    """Refresh the material list. Keeps set names, exclude, overrides and opacity mode."""
    global SUPPRESS
    SUPPRESS += 1
    try:
        n = _scan(context)
    finally:
        SUPPRESS -= 1
    relayout(context)
    if context.scene.om3d_atlas.isolate:
        isolate_apply(context)
    return n


def _scan(context):
    state = context.scene.om3d_atlas
    keep = {it.name: (it.set_name, it.exclude, it.override, it.op_mode, it.plan_tile, it.plan_kind) for it in state.items}
    types = map_types(context)
    p = prefs(context)
    solid_limit = (p.op_solid_threshold if p else 0.1) / 100.0
    mats = used_materials(context)
    stats = density.gather(context, mats)
    state.items.clear()
    for m in mats:
        found, rest = maps.discover(m, types)
        res = maps.source_resolution(found)
        it = state.items.add()
        it.name = m.name
        it.material = m
        if m.name in keep:
            it.set_name, it.exclude, it.override, it.op_mode, it.plan_tile, it.plan_kind = keep[m.name]
        it.roles_raw = ",".join(t.key for t in types if t.key in found)
        st = stats.get(m.name, [])
        all_tiles = sorted({s.tile for s in st if not s.uv_out})
        tile = all_tiles[0] if all_tiles else (0, 0)
        it.src_tile_u, it.src_tile_v = tile
        udim = [n.image for n in found.values() if n.image.source == "TILED"]
        udim_err = ""
        if udim and len(all_tiles) > 1:
            udim_err = (f"UDIM texture but its objects use {len(all_tiles)} tiles: "
                        + ", ".join(f"{imaging.udim_number(t)}" for t in all_tiles[:4]))
        elif udim:
            missing = [i.name for i in udim if imaging.tile_path(i, tile) is None]
            if missing:
                udim_err = f"UDIM tile {imaging.udim_number(tile)} missing on disk: " + _names(missing)
        # opacity: real transparency, or an OP map that is basically solid
        it.op_transparent = -1.0
        it.op_note = ""
        if OP_KEY in found:
            try:
                frac = transparent_fraction(found[OP_KEY].image, tile)
            except RuntimeError:
                frac = 1.0
            it.op_transparent = frac * 100.0
            if it.op_mode == "OPAQUE":
                it.masked = False
                it.op_note = "Forced opaque: OP map ignored"
            elif it.op_mode == "MASKED":
                it.masked = True
            else:
                it.masked = frac >= solid_limit
                if not it.masked:
                    it.op_note = f"OP ignored: {100 - frac * 100:.2f}% solid"
        else:
            it.masked = False
        eff = effective_found(it, found)
        it.roles = ",".join(t.key for t in types if t.key in eff)
        it.src_res = res
        td, obj = density.worst(st, res, state.include_hidden) if res else (None, "")
        it.td_src = td if td is not None else -1.0
        it.worst_obj = obj
        it.ignored = ",".join(sorted(s.obj.name for s in st if density.is_ignored(s.obj)))
        it.object_count = len(st)
        out = [s.obj.name for s in st if s.uv_out]
        tiles = sorted({s.tile for s in st if not s.uv_out and s.tile != (0, 0)})
        it.uv_tile = ", ".join(f"u{u} v{v}" for u, v in tiles)
        no_uv = [s.obj.name for s in st if s.no_uv]
        notes = []
        if no_uv:
            notes.append("No UV map: " + _names(no_uv))
        if out:
            notes.append("UVs span more than one 0-1 tile: " + _names(out))
        if udim_err:
            notes.append(udim_err)
        if rest:
            notes.append("Unknown images: " + _names([n.image.name for n in rest]))
        if not found:
            notes.append("No textures found")
        it.note = " | ".join(notes)
        it.uv_error = bool(out or no_uv or udim_err)
        it.udim = bool(udim)
        if not found:
            it.exclude = True
    return len(mats)


def refresh_density(context, names):
    """Re-measure density for a few materials only (after an ignore toggle)."""
    state = context.scene.om3d_atlas
    items = [it for it in state.items if it.name in names and it.material]
    stats = density.gather(context, [it.material for it in items])
    for it in items:
        st = stats.get(it.name, [])
        td, obj = density.worst(st, it.src_res, state.include_hidden) if it.src_res else (None, "")
        it.td_src = td if td is not None else -1.0
        it.worst_obj = obj
        it.ignored = ",".join(sorted(x.obj.name for x in st if density.is_ignored(x.obj)))
    relayout(context)


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------

def _entries(state):
    out = []
    for it in state.items:
        roles = [r for r in it.roles.split(",") if r]
        e = planner.Entry(it.name, roles, it.src_res, it.td_src if it.td_src >= 0 else None,
                          it.set_name.strip(), it.exclude, int(it.override), masked=it.masked,
                          plan_tile=it.plan_tile, plan_kind=it.plan_kind)
        out.append(e)
    return out


def _write_back(state, entries, sets):
    by_name = {e.name: e for e in entries}
    for it in state.items:
        e = by_name.get(it.name)
        if e is None or it.exclude or not it.set_name.strip():
            it.tile = 0
            it.td_tile = -1.0
            it.flag = ""
            continue
        it.tile = e.tile
        it.plan_note = e.plan_kind if (e.plan_tile and not e.override and e.tile == e.plan_tile) else ""
        it.td_tile = e.td_tile if e.td_tile is not None else -1.0
        it.flag = e.flag
        it.px = e.x
        it.py = e.y
    state.sets.clear()
    for name in sorted(sets):
        info = sets[name]
        row = state.sets.add()
        row.name = name
        row.size = info["size"] or 0
        row.fill = info["fill"]
        row.count = len(info["members"])
        row.mixed = info["mixed"]
        row.masked = any(e.masked for e in info["members"])


_busy = False


def relayout(context):
    global _busy
    if _busy:
        return
    _busy = True
    try:
        state = context.scene.om3d_atlas
        entries = _entries(state)
        sets = planner.layout(entries, settings(state))
        _write_back(state, entries, sets)
    finally:
        _busy = False


def auto_plan(context):
    state = context.scene.om3d_atlas
    entries = _entries(state)
    for e in entries:
        e.set_name = ""
    state.over_budget = bool(planner.auto_plan(entries, settings(state), fill=state.fill_gaps))
    by_name = {e.name: e for e in entries}
    global SUPPRESS
    SUPPRESS += 1
    try:
        for it in state.items:
            if not it.exclude:
                it.set_name = by_name[it.name].set_name
            it.plan_tile = by_name[it.name].plan_tile
            it.plan_kind = by_name[it.name].plan_kind
    finally:
        SUPPRESS -= 1
    relayout(context)


def problems(context):
    """Blocking problems for Pack, as a list of strings."""
    state = context.scene.om3d_atlas
    out = []
    if not bpy.data.filepath and state.output_dir.startswith("//"):
        out.append("Save the .blend first (output path is relative)")
    if packed_objects(context):
        out.append("Scene is already packed: Revert first")
    active = [it for it in state.items if not it.exclude]
    if not active:
        out.append("Nothing to pack")
    for it in active:
        if not it.set_name.strip():
            out.append(f"{it.name}: no set")
        if it.flag == planner.FLAG_NOFIT:
            out.append(f"{it.name}: set {it.set_name} does not fit {state.max_size}")
        if it.uv_error:
            out.append(f"{it.name}: {it.note}")
    out += _shared_mesh_conflicts(context, {it.name for it in active})
    return out


def warnings(context):
    """Non-blocking warnings (shown in yellow, Pack still runs)."""
    state = context.scene.om3d_atlas
    out = []
    by_set = {}
    for it in state.items:
        if not it.exclude and it.set_name.strip():
            by_set.setdefault(it.set_name.strip(), []).append(it)
    for s in state.sets:
        if s.mixed:
            masked = [it.name for it in by_set.get(s.name, []) if it.masked]
            out.append(f"Set {s.name} mixes opaque and masked: move {_names(masked, 2)}")
    if state.over_budget and state.max_sets:
        out.append(f"Max Sets {state.max_sets} can't be met: every tile is already at its smallest")
    low = [it.name for it in state.items if not it.exclude and it.flag == planner.FLAG_LOW]
    if low:
        out.append(f"Below {state.target_td:g} px/cm: {_names(low, 3)}")
    if not state.prefix.strip():
        out.append("No prefix: names will be M_<Set>")
    return out


def _mesh_users(context):
    users = {}
    for obj in density.mesh_objects(context):
        users.setdefault(obj.data.name, []).append(obj)
    return users


def _shared_mesh_conflicts(context, packed_names):
    out = []
    for mesh_name, objs in _mesh_users(context).items():
        sig = None
        for o in objs:
            s = tuple((sl.material.name if sl.material and sl.material.name in packed_names else None)
                      for sl in o.material_slots)
            if sig is None:
                sig = s
            elif s != sig:
                out.append(f"Mesh {mesh_name} is shared by objects with different materials")
                break
    return out


# --------------------------------------------------------------------------
# Show only packed objects
# --------------------------------------------------------------------------

def _packed_material_names(state):
    return {it.name for it in state.items if not it.exclude}


def will_pack(obj, names):
    return obj.type == "MESH" and any(s.material and s.material.name in names for s in obj.material_slots)


def isolate_apply(context):
    """Hide every visible object that won't be packed; remember them for restore."""
    scene = context.scene
    state = scene.om3d_atlas
    isolate_restore(context)
    names = _packed_material_names(state)
    hidden = []
    for o in context.view_layer.objects:
        if not o.hide_get() and not will_pack(o, names):
            o.hide_set(True)
            hidden.append(o.name)
    scene[P_ISOLATED] = json.dumps(hidden)


def isolate_restore(context):
    scene = context.scene
    raw = scene.get(P_ISOLATED)
    if not raw:
        return
    for n in json.loads(raw):
        o = context.view_layer.objects.get(n)
        if o is not None:
            o.hide_set(False)
    del scene[P_ISOLATED]


# --------------------------------------------------------------------------
# Pack
# --------------------------------------------------------------------------

def _output_dir(state):
    return bpy.path.abspath(state.output_dir)


def _wire_missing(nt, node, mtype, uv_node):
    """Connect a new atlas image node for a role the template lacks."""
    bsdf = maps._principled(nt)
    nt.links.new(uv_node.outputs["UV"], node.inputs["Vector"])
    if bsdf is None:
        return
    if mtype.socket == "BASE":
        nt.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
    elif mtype.socket == "ALPHA":
        nt.links.new(node.outputs["Color"], bsdf.inputs["Alpha"])
    elif mtype.socket == "EMISSION":
        sock = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
        nt.links.new(node.outputs["Color"], sock)
        st = bsdf.inputs.get("Emission Strength")
        if st is not None and st.default_value == 0:
            st.default_value = 1.0
    elif mtype.socket == "ORM":
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        sep.location = node.location.x + 300, node.location.y
        nt.links.new(node.outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
        nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
    elif mtype.socket == "NORMAL":
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.uv_map = ATLAS_UV
        nm.location = node.location.x + 300, node.location.y
        nt.links.new(node.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])


def _make_material(name, template, found_template, images, types_by_key, masked=True):
    """Copy *template*, swap its images for the atlas ones, drop maps the set
    doesn't have (an ignored OP), add maps the template lacks."""
    old = bpy.data.materials.get(name)
    if old is not None:
        if old.users:
            raise RuntimeError(f"Material {name} already exists and is in use")
        bpy.data.materials.remove(old)
    mat = template.copy()
    mat.name = name
    nt = mat.node_tree
    uv_node = nt.nodes.new("ShaderNodeUVMap")
    uv_node.name = uv_node.label = UV_NODE
    uv_node.uv_map = ATLAS_UV
    xs = [n.location.x for n in nt.nodes if n.type == "TEX_IMAGE"] or [0]
    uv_node.location = min(xs) - 300, 0
    done = set()
    for key, src_node in found_template.items():
        node = nt.nodes.get(src_node.name)
        if node is None:
            continue
        if key not in images:
            nt.nodes.remove(node)   # e.g. a near-solid OP the set treats as opaque
            continue
        node.image = images[key]
        for l in list(node.inputs["Vector"].links):
            nt.links.remove(l)
        nt.links.new(uv_node.outputs["UV"], node.inputs["Vector"])
        done.add(key)
    y = -900
    for key, img in images.items():
        if key in done:
            continue
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = key
        node.location = uv_node.location.x + 300, y
        y -= 300
        _wire_missing(nt, node, types_by_key[key], uv_node)
    for n in nt.nodes:
        if n.type == "NORMAL_MAP":
            n.uv_map = ATLAS_UV
    if not masked:
        # opaque set: nothing may drive Alpha (e.g. a D alpha that mirrored a near-solid OP)
        bsdf = maps._principled(nt)
        if bsdf is not None and "Alpha" in bsdf.inputs:
            for l in list(bsdf.inputs["Alpha"].links):
                nt.links.remove(l)
            bsdf.inputs["Alpha"].default_value = 1.0
    return mat


def _loop_material_index(mesh):
    """Material index for every loop (vectorised)."""
    n = len(mesh.polygons)
    mi = np.empty(n, np.int32)
    ls = np.empty(n, np.int32)
    lt = np.empty(n, np.int32)
    mesh.polygons.foreach_get("material_index", mi)
    mesh.polygons.foreach_get("loop_start", ls)
    mesh.polygons.foreach_get("loop_total", lt)
    order = np.argsort(ls, kind="stable")
    return np.repeat(mi[order], lt[order]).astype(np.int32)


def _write_atlas_uv(mesh, slot_xform):
    """slot_xform: {slot index: (scale, offset_u, offset_v)} in 0-1 atlas space.

    Each slot's UVs are first moved back from their unit tile (UDIM-style layouts
    keep every material in its own tile), then scaled and offset into the atlas.
    A pre-existing layer called AtlasUV is renamed AtlasUV_prev (Revert names it back)."""
    if ATLAS_UV in mesh.uv_layers and P_SRC_UV not in mesh:
        mesh.uv_layers[ATLAS_UV].name = ATLAS_UV_PREV
        mesh[P_RENAMED] = mesh.uv_layers[ATLAS_UV_PREV].name
    src = density.source_uv(mesh)
    layers = []
    for l in mesh.uv_layers:
        a = np.empty(len(l.data) * 2, np.float32)
        l.data.foreach_get("uv", a)
        layers.append((l.name, a.reshape(-1, 2)))
    src_uv = next(a for n, a in layers if n == src.name)
    new = src_uv.copy()
    loop_mi = _loop_material_index(mesh)
    for i, (sc, ou, ov) in slot_xform.items():
        mask = loop_mi == i
        if not mask.any():
            continue
        tu, tv = density.unit_tile([float(src_uv[mask, 0].min()), float(src_uv[mask, 1].min()),
                                    float(src_uv[mask, 0].max()), float(src_uv[mask, 1].max())]) or (0, 0)
        new[mask, 0] = (src_uv[mask, 0] - tu) * sc + ou
        new[mask, 1] = (src_uv[mask, 1] - tv) * sc + ov
    src_name = src.name
    while mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[0])
    first = mesh.uv_layers.new(name=ATLAS_UV)
    first.data.foreach_set("uv", new.ravel())
    for n, a in layers:
        l = mesh.uv_layers.new(name=n)
        l.data.foreach_set("uv", a.ravel())
    mesh.uv_layers.active = mesh.uv_layers[ATLAS_UV]
    mesh.uv_layers[ATLAS_UV].active_render = True
    mesh[P_SRC_UV] = src_name


def _k(n):
    return f"{n // 1024}K" if n >= 1024 and n % 1024 == 0 else str(n)


def pack(context, progress=None):
    state = context.scene.om3d_atlas
    scan(context)
    bad = problems(context)
    if bad:
        raise RuntimeError("; ".join(bad[:5]))
    types = map_types(context)
    types_by_key = {t.key: t for t in types}
    order = [t.key for t in types]
    inset = state.inset
    ext = EXT[state.file_format]
    out_dir = _output_dir(state)

    items = [it for it in state.items if not it.exclude]
    sets = {}
    for it in items:
        sets.setdefault(it.set_name.strip(), []).append(it)
    set_size = {r.name: r.size for r in state.sets}

    new_mat = {}       # source material name -> atlas material
    xform = {}         # source material name -> (scale, ou, ov)
    written = []
    state.results.clear()
    for sname, members in sorted(sets.items()):
        size = set_size[sname]
        found = {it.name: effective_found(it, maps.discover(it.material, types)[0]) for it in members}
        roles = [k for k in order if any(k in f for f in found.values())]
        images = {}
        for key in roles:
            mt = types_by_key[key]
            atlas = imaging.blank(size, mt.fill)
            has_alpha = False
            set_masked = any(it.masked for it in members)
            for it in members:
                node = found[it.name].get(key)
                if node is None:
                    continue
                src, channels = imaging.load_source(node.image, (it.src_tile_u, it.src_tile_v))
                if set_masked and channels == 4 and float(src[..., 3].min()) < 1.0:
                    has_alpha = True
                t = imaging.tile(src, it.tile, inset, mt.is_normal)
                atlas[it.py:it.py + it.tile, it.px:it.px + it.tile] = t
                del src
            name = texture_name(context, state, sname, key)
            path = os.path.join(out_dir, name + ext)
            images[key] = imaging.save(name, atlas, path, mt.colorspace, state.file_format, has_alpha)
            written.append(path)
            if progress:
                progress(name)
        template = max(members, key=lambda it: len(found[it.name]))
        mname = material_name(context, state, sname)
        mat = _make_material(mname, template.material, found[template.name], images, types_by_key,
                             masked=any(it.masked for it in members))
        mat[P_SET] = sname
        mat[P_SOURCES] = ",".join(it.name for it in members)
        mat[P_IMAGES] = ",".join(i.name for i in images.values())
        for it in members:
            new_mat[it.name] = mat
            content = it.tile - 2 * inset
            xform[it.name] = (content / size, (it.px + inset) / size, (it.py + inset) / size)
        # results (a snapshot, not live)
        r = state.results.add()
        r.kind = "SET"
        r.name = sname
        kind = "masked" if any(it.masked for it in members) else "opaque"
        r.text = f"{mname}  {_k(size)}, {len(members)} tiles, {kind}"
        for it in sorted(members, key=lambda it: (-it.tile, it.name)):
            r = state.results.add()
            r.kind = "TILE"
            r.name = sname
            r.text = f"{it.name}  {_k(it.tile)}  {it.td_tile:.1f} px/cm"
        for img in images.values():
            r = state.results.add()
            r.kind = "TEX"
            r.name = sname
            r.text = os.path.basename(img.filepath)

    users = _mesh_users(context)
    meshes = 0
    for mesh_name, objs in users.items():
        first = objs[0]
        slot_x = {i: xform[s.material.name] for i, s in enumerate(first.material_slots)
                  if s.material and s.material.name in xform}
        if not slot_x:
            continue
        # record every user first: data-linked slots change for all users at once
        for o in objs:
            o[P_ORIG] = json.dumps([s.material.name if s.material else "" for s in o.material_slots])
        _write_atlas_uv(first.data, slot_x)
        meshes += 1
        for o in objs:
            for s in o.material_slots:
                if s.material and s.material.name in new_mat:
                    s.material = new_mat[s.material.name]
    relayout(context)
    state.results_dir = out_dir
    # originals now have no users; protect them so Revert still works after save + reopen
    for it in items:
        m = it.material
        if m is not None and not m.use_fake_user:
            m.use_fake_user = True
            m[P_FAKE] = True
    context.scene[P_WRITTEN] = json.dumps([os.path.normpath(p) for p in written])
    return {"sets": len(sets), "images": len(written), "meshes": meshes, "dir": out_dir}


def record_written(context, path):
    """Add a file (e.g. the layout PNG) to the list Revert deletes."""
    files = json.loads(context.scene.get(P_WRITTEN, "[]"))
    path = os.path.normpath(path)
    if path not in files:
        files.append(path)
    context.scene[P_WRITTEN] = json.dumps(files)


def written_files(context):
    """Files the last Pack wrote that are still on disk."""
    return [p for p in json.loads(context.scene.get(P_WRITTEN, "[]")) if os.path.isfile(p)]


# --------------------------------------------------------------------------
# Revert
# --------------------------------------------------------------------------

def _delete_written(context):
    """Delete the files the last Pack wrote, unless an image still in the file uses one."""
    files = json.loads(context.scene.get(P_WRITTEN, "[]"))
    in_use = {os.path.normpath(bpy.path.abspath(i.filepath)) for i in bpy.data.images
              if i.filepath and i.users}
    deleted, kept, failed = 0, [], []
    for p in files:
        if not os.path.isfile(p):
            continue
        if p in in_use:
            kept.append(os.path.basename(p))
            continue
        try:
            os.remove(p)
            deleted += 1
        except OSError:
            failed.append(os.path.basename(p))
    if P_WRITTEN in context.scene:
        del context.scene[P_WRITTEN]
    return deleted, kept, failed


def revert(context, delete_files=True):
    objs = [o for o in bpy.data.objects if P_ORIG in o]
    for o in objs:
        names = json.loads(o[P_ORIG])
        for s, n in zip(o.material_slots, names):
            if n and n in bpy.data.materials:
                s.material = bpy.data.materials[n]
        del o[P_ORIG]
    meshes = 0
    for me in bpy.data.meshes:
        if P_SRC_UV not in me:
            continue
        src = me[P_SRC_UV]
        if ATLAS_UV in me.uv_layers:
            me.uv_layers.remove(me.uv_layers[ATLAS_UV])
        prev = me.get(P_RENAMED)
        if prev and prev in me.uv_layers:
            me.uv_layers[prev].name = ATLAS_UV
            del me[P_RENAMED]
        if src in me.uv_layers:
            me.uv_layers.active = me.uv_layers[src]
            me.uv_layers[src].active_render = True
        del me[P_SRC_UV]
        meshes += 1
    mats = 0
    for m in [m for m in bpy.data.materials if P_SET in m]:
        if m.users:
            continue
        imgs = [i for i in m.get(P_IMAGES, "").split(",") if i]
        bpy.data.materials.remove(m)
        mats += 1
        for n in imgs:
            img = bpy.data.images.get(n)
            if img is not None and img.users == 0:
                bpy.data.images.remove(img)
    for m in bpy.data.materials:
        if P_FAKE in m:
            m.use_fake_user = False
            del m[P_FAKE]
    for img in [i for i in bpy.data.images if i.get("om3d_atlas_layout") and i.users == 0]:
        bpy.data.images.remove(img)
    deleted, kept, failed = _delete_written(context) if delete_files else (0, [], [])
    state = context.scene.om3d_atlas
    if delete_files:
        state.layout_path = ""
    preview_restore(context, shading=False)
    state.results.clear()
    scan(context)
    return {"objects": len(objs), "meshes": meshes, "materials": mats,
            "deleted": deleted, "kept": kept, "failed": failed}


# --------------------------------------------------------------------------
# Files Pack will write (for the overwrite check)
# --------------------------------------------------------------------------

def planned_files(context):
    state = context.scene.om3d_atlas
    out_dir = _output_dir(state)
    ext = EXT[state.file_format]
    order = [t.key for t in map_types(context)]
    sets = {}
    for it in state.items:
        if not it.exclude and it.set_name.strip():
            sets.setdefault(it.set_name.strip(), set()).update(r for r in it.roles.split(",") if r)
    out = []
    for sname, roles in sorted(sets.items()):
        for key in order:
            if key in roles:
                out.append(os.path.join(out_dir, texture_name(context, state, sname, key) + ext))
    return out


# --------------------------------------------------------------------------
# Preview Colors
# --------------------------------------------------------------------------

PALETTE = (
    (0.90, 0.36, 0.30), (0.30, 0.62, 0.90), (0.40, 0.80, 0.42), (0.95, 0.75, 0.25),
    (0.68, 0.45, 0.90), (0.25, 0.80, 0.78), (0.95, 0.55, 0.75), (0.65, 0.65, 0.35),
)
OTHER = (0.18, 0.18, 0.18)


def _set_of_object(obj, set_by_material):
    """Set name of the object's first slot that is in the plan (works before and after Pack)."""
    names = []
    if P_ORIG in obj:
        names = json.loads(obj[P_ORIG])
    else:
        names = [s.material.name if s.material else "" for s in obj.material_slots]
    for n in names:
        if n in set_by_material:
            return set_by_material[n]
    return None


def _all_view3d():
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                yield area.spaces.active


def preview_colors(context):
    """Colour every object by its atlas set and show Solid / Object colour."""
    state = context.scene.om3d_atlas
    set_by_material = {it.name: it.set_name.strip() for it in state.items
                       if not it.exclude and it.set_name.strip()}
    order = [st.name for st in state.sets]
    colour = {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(order)}
    count = 0
    for obj in context.view_layer.objects:
        if obj.type != "MESH":
            continue
        if P_COLOR not in obj:
            obj[P_COLOR] = list(obj.color)
        sname = _set_of_object(obj, set_by_material)
        rgb = colour.get(sname, OTHER) if sname else OTHER
        obj.color = (rgb[0], rgb[1], rgb[2], 1.0)
        count += 1 if sname else 0
    for space in _all_view3d():
        space.shading.type = "SOLID"
        space.shading.color_type = "OBJECT"
    for st in state.sets:
        rgb = colour.get(st.name, OTHER)
        st.color = (rgb[0], rgb[1], rgb[2])
    state.preview_on = True
    return count


def preview_restore(context, shading=True):
    """Put object colours back; optionally switch viewports to Material Preview."""
    state = context.scene.om3d_atlas
    for obj in bpy.data.objects:
        if P_COLOR in obj:
            obj.color = tuple(obj[P_COLOR])
            del obj[P_COLOR]
    if shading:
        for space in _all_view3d():
            space.shading.type = "MATERIAL"
    state.preview_on = False
