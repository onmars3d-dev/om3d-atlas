# SPDX-License-Identifier: GPL-3.0-or-later
"""OM3D Atlas: pack a character's texture sets into fewer atlases.

Six steps in the sidebar: 1 Scan, 2 Choose, 3 Name, 4 Plan, 5 Pack, 6 Results.
Scan finds each material's maps (node tree first, filename suffix fallback),
whether it is really transparent, and its worst-object texel density.
Auto Plan keeps opaque and masked materials apart and quadtree-packs them at
the density target. Pack writes the atlases, copies a material of each set onto
them, adds AtlasUV as UV channel 0 and keeps everything needed for Revert.
"""
import os

import bpy

from . import density, maps, packer, planner


def _read_version():
    try:
        with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"),
                  encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("version"):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "?"


VERSION = _read_version()


def _k(n):
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024}K"
    return str(n)


def _relayout(self, context):
    if packer.SUPPRESS:
        return
    packer.relayout(context)


def _exclude_changed(self, context):
    if packer.SUPPRESS:
        return
    packer.relayout(context)
    if context.scene.om3d_atlas.isolate:
        packer.isolate_apply(context)


def _rescan(self, context):
    if packer.SUPPRESS:
        return
    if len(context.scene.om3d_atlas.items):
        packer.scan(context)


def _isolate_changed(self, context):
    if self.isolate:
        packer.isolate_apply(context)
    else:
        packer.isolate_restore(context)


SIZES = [("2048", "2K", ""), ("4096", "4K", ""), ("8192", "8K", "")]
OP_MODES = [
    ("AUTO", "Auto", "Masked if the opacity map has real transparency, opaque if it is almost solid"),
    ("OPAQUE", "Opaque", "Ignore the opacity map"),
    ("MASKED", "Masked", "Always keep the opacity map"),
]
SOCKET_ITEMS = [
    ("BASE", "Base Color", ""), ("ORM", "Roughness/Metallic (packed)", ""),
    ("NORMAL", "Normal", ""), ("ALPHA", "Alpha", ""), ("EMISSION", "Emission", ""),
    ("NONE", "None (suffix only)", ""),
]
COLORSPACES = [("sRGB", "sRGB", ""), ("Non-Color", "Non-Color", "")]

# Dynamic enum items must stay referenced from Python or Blender shows garbage labels.
_enum_cache = {}


def _tile_items(self, context):
    """Tile dropdown: 'Auto 2K' shows what Auto picked; the rest force a size."""
    auto = f"Auto {_k(self.tile)}" if self.tile else "Auto"
    if self.filled:
        auto += " (filled)"
    items = [("0", auto, "Smallest tile that meets the density target (Auto Plan may grow it to fill free space)", 0)]
    items += [(str(z), _k(z), f"Force a {_k(z)} tile", i + 1)
              for i, z in enumerate((256, 512, 1024, 2048, 4096, 8192))]
    _enum_cache[("tile", self.name)] = items
    return items


def _move_items(self, context):
    state = context.scene.om3d_atlas
    it = state.items[self.index] if 0 <= self.index < len(state.items) else None
    names = [st.name for st in state.sets]
    items = [(n, n, f"Move to {n}", i) for i, n in enumerate(names)]
    items.append(("__NEW__", "New Set", "Move to a new set of its own", len(items)))
    _enum_cache["move"] = items
    return items


TIP_PREFIX = ("Text added to the name of every material and texture this tool creates, "
              "e.g. Knight gives M_Knight_Main1 and T_Knight_Main1_D. "
              "It does not select objects or collections")
TIP_SUFFIX = "Optional text added after the set name, e.g. _Atlas gives M_Knight_Main1_Atlas"
TIP_PADDING = ("Atlas padding: an empty border inside each tile, filled by stretching the tile's "
               "edge pixels, so mipmaps don't bleed into the neighbouring tile. "
               "8 px protects about 3 mip levels at 4K. 0 = tiles touch (pixel-exact copy)")


# --------------------------------------------------------------------------
# Preferences
# --------------------------------------------------------------------------

class OM3D_AT_MapType(bpy.types.PropertyGroup):
    key: bpy.props.StringProperty(name="Key", description="Short name used in texture names")
    suffixes: bpy.props.StringProperty(name="Suffixes", description="Comma separated filename suffixes")
    colorspace: bpy.props.EnumProperty(name="Colorspace", items=COLORSPACES)
    fill: bpy.props.FloatVectorProperty(name="Fill", size=4, subtype="COLOR", min=0, max=1,
                                        default=(0, 0, 0, 1),
                                        description="Colour for empty tiles and materials without this map")
    is_normal: bpy.props.BoolProperty(name="Normal", description="Re-normalise after resampling")
    socket: bpy.props.EnumProperty(name="Socket", items=SOCKET_ITEMS,
                                   description="Principled input used to find this map in the node tree")


def load_default_types(prefs):
    prefs.map_types.clear()
    for key, suf, cs, fill, nrm, sock in maps.DEFAULT_TYPES:
        r = prefs.map_types.add()
        r.key, r.suffixes, r.colorspace, r.fill, r.is_normal, r.socket = key, suf, cs, fill, nrm, sock


class OM3D_UL_at_map_types(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "key", text="", emboss=False)
        row.label(text=item.suffixes)


class OM3D_AT_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    map_types: bpy.props.CollectionProperty(type=OM3D_AT_MapType)
    map_index: bpy.props.IntProperty()
    material_prefix: bpy.props.StringProperty(name="Material Prefix", default="M_",
                                              description="Type prefix for atlas materials (some studios use MI_)")
    texture_prefix: bpy.props.StringProperty(name="Texture Prefix", default="T_",
                                             description="Type prefix for atlas textures")
    op_solid_threshold: bpy.props.FloatProperty(
        name="Solid Opacity Threshold %", default=0.1, min=0.0, max=10.0, precision=3,
        description="An opacity map with less than this share of transparent pixels counts as opaque")

    def draw(self, context):
        col = self.layout.column()
        row = col.row()
        row.prop(self, "material_prefix")
        row.prop(self, "texture_prefix")
        col.prop(self, "op_solid_threshold")
        col.separator()
        col.label(text="Map types")
        row = col.row()
        row.template_list("OM3D_UL_at_map_types", "", self, "map_types", self, "map_index", rows=5)
        side = row.column(align=True)
        side.operator("om3d.atlas_map_type_add", text="", icon="ADD")
        side.operator("om3d.atlas_map_type_remove", text="", icon="REMOVE")
        side.separator()
        side.operator("om3d.atlas_map_type_defaults", text="", icon="FILE_REFRESH")
        if 0 <= self.map_index < len(self.map_types):
            r = self.map_types[self.map_index]
            box = col.box()
            box.prop(r, "key")
            box.prop(r, "suffixes")
            box.prop(r, "socket")
            box.prop(r, "colorspace")
            box.prop(r, "fill")
            box.prop(r, "is_normal")
        if not len(self.map_types):
            col.label(text="Empty table: the built-in defaults are used", icon="INFO")


class OM3D_OT_atlas_map_type_add(bpy.types.Operator):
    bl_idname = "om3d.atlas_map_type_add"
    bl_label = "Add Map Type"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        p = packer.prefs(context)
        if not len(p.map_types):
            load_default_types(p)
        r = p.map_types.add()
        r.key, r.suffixes, r.socket = "NEW", "_NEW", "NONE"
        p.map_index = len(p.map_types) - 1
        return {"FINISHED"}


class OM3D_OT_atlas_map_type_remove(bpy.types.Operator):
    bl_idname = "om3d.atlas_map_type_remove"
    bl_label = "Remove Map Type"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        p = packer.prefs(context)
        if 0 <= p.map_index < len(p.map_types):
            p.map_types.remove(p.map_index)
            p.map_index = max(0, p.map_index - 1)
        return {"FINISHED"}


class OM3D_OT_atlas_map_type_defaults(bpy.types.Operator):
    bl_idname = "om3d.atlas_map_type_defaults"
    bl_label = "Reset Map Types"
    bl_description = "Replace the table with the built-in defaults (D, ORM, N, OP, E)"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        load_default_types(packer.prefs(context))
        return {"FINISHED"}


# --------------------------------------------------------------------------
# Scene state
# --------------------------------------------------------------------------

class OM3D_AT_Item(bpy.types.PropertyGroup):
    material: bpy.props.PointerProperty(type=bpy.types.Material)
    set_name: bpy.props.StringProperty(name="Set", update=_relayout,
                                       description="Atlas set this material goes into")
    exclude: bpy.props.BoolProperty(name="Exclude", update=_exclude_changed,
                                    description="Leave this material out (hair cards, for example)")
    override: bpy.props.EnumProperty(name="Tile", items=_tile_items, update=_relayout,
                                     description="Tile size. Auto picks the smallest that meets the target")
    fill_tile: bpy.props.IntProperty()
    filled: bpy.props.BoolProperty()
    op_mode: bpy.props.EnumProperty(name="Opacity", items=OP_MODES, update=_rescan,
                                    description="How the opacity map is treated")
    masked: bpy.props.BoolProperty()
    op_transparent: bpy.props.FloatProperty(default=-1)
    op_note: bpy.props.StringProperty()
    roles: bpy.props.StringProperty()
    roles_raw: bpy.props.StringProperty()
    src_res: bpy.props.IntProperty()
    td_src: bpy.props.FloatProperty(default=-1)
    worst_obj: bpy.props.StringProperty()
    ignored: bpy.props.StringProperty()
    object_count: bpy.props.IntProperty()
    note: bpy.props.StringProperty()
    uv_error: bpy.props.BoolProperty()
    tile: bpy.props.IntProperty()
    td_tile: bpy.props.FloatProperty(default=-1)
    flag: bpy.props.StringProperty()
    px: bpy.props.IntProperty()
    py: bpy.props.IntProperty()


class OM3D_AT_Set(bpy.types.PropertyGroup):
    size: bpy.props.IntProperty()
    fill: bpy.props.FloatProperty()
    count: bpy.props.IntProperty()
    mixed: bpy.props.BoolProperty()
    masked: bpy.props.BoolProperty()


class OM3D_AT_Result(bpy.types.PropertyGroup):
    kind: bpy.props.StringProperty()   # SET, TILE, TEX
    text: bpy.props.StringProperty()
    expanded: bpy.props.BoolProperty()


class OM3D_AT_State(bpy.types.PropertyGroup):
    prefix: bpy.props.StringProperty(name="Prefix", description=TIP_PREFIX)
    suffix: bpy.props.StringProperty(name="Suffix", description=TIP_SUFFIX)
    max_size: bpy.props.EnumProperty(name="Max Atlas", items=SIZES, default="4096", update=_relayout,
                                     description="Largest atlas sheet. A set uses a smaller sheet when its tiles fit")
    target_td: bpy.props.FloatProperty(name="Target px/cm", default=10.0, min=0.1, update=_relayout,
                                       description="Minimum texel density every visible object must keep")
    fill_gaps: bpy.props.BoolProperty(
        name="Fill Empty Space", default=True,
        description=("Auto Plan grows tiles (lowest density first) into space left empty in a sheet. "
                     "It never adds a sheet, makes a sheet bigger, or changes a tile you set by hand"))
    inset: bpy.props.IntProperty(name="Atlas Padding px", default=8, min=0, max=64, update=_relayout,
                                 description=TIP_PADDING)
    include_hidden: bpy.props.BoolProperty(name="Count Hidden Objects", update=_rescan,
                                           description="Count hidden objects when finding each material's worst density")
    isolate: bpy.props.BoolProperty(name="Show Only Packed Objects", update=_isolate_changed,
                                    description="Hide every object that won't be packed. Turn off to restore your visibility")
    file_format: bpy.props.EnumProperty(name="Format", items=[("TARGA", "TGA", ""), ("PNG", "PNG", "")])
    output_dir: bpy.props.StringProperty(name="Output", default="//Textures/Atlas", subtype="DIR_PATH",
                                         description="Folder for the atlas textures")
    items: bpy.props.CollectionProperty(type=OM3D_AT_Item)
    index: bpy.props.IntProperty()
    sets: bpy.props.CollectionProperty(type=OM3D_AT_Set)
    results: bpy.props.CollectionProperty(type=OM3D_AT_Result)
    results_dir: bpy.props.StringProperty()
    last_report: bpy.props.StringProperty()


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------

class OM3D_OT_atlas_scan(bpy.types.Operator):
    bl_idname = "om3d.atlas_scan"
    bl_label = "Scan Materials"
    bl_description = "Find every material on visible-layer meshes, its maps, its opacity and its worst texel density"
    bl_options = {"REGISTER"}

    def execute(self, context):
        n = packer.scan(context)
        self.report({"INFO"}, f"{n} materials")
        return {"FINISHED"}


class OM3D_OT_atlas_auto_plan(bpy.types.Operator):
    bl_idname = "om3d.atlas_auto_plan"
    bl_label = "Auto Plan"
    bl_description = ("Pack opaque and masked materials into separate sets at the density target. "
                      "Edit the set names afterwards")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not len(context.scene.om3d_atlas.items):
            packer.scan(context)
        packer.auto_plan(context)
        return {"FINISHED"}


class OM3D_OT_atlas_pack(bpy.types.Operator):
    bl_idname = "om3d.atlas_pack"
    bl_label = "Pack Atlases"
    bl_description = "Write the atlas textures, create the atlas materials and AtlasUV (UV channel 0)"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            r = packer.pack(context)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        msg = f"{r['sets']} sets, {r['images']} textures, {r['meshes']} meshes"
        context.scene.om3d_atlas.last_report = msg
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class OM3D_OT_atlas_revert(bpy.types.Operator):
    bl_idname = "om3d.atlas_revert"
    bl_label = "Revert"
    bl_description = "Put the original materials back and remove AtlasUV. Texture files stay on disk"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        r = packer.revert(context)
        context.scene.om3d_atlas.last_report = ""
        self.report({"INFO"}, f"Reverted {r['objects']} objects, {r['meshes']} meshes")
        return {"FINISHED"}


class OM3D_OT_atlas_select_object(bpy.types.Operator):
    bl_idname = "om3d.atlas_select_object"
    bl_label = "Select Object"
    bl_description = "Select this object"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.view_layer.objects.get(self.name)
        if obj is None:
            return {"CANCELLED"}
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class OM3D_OT_atlas_pick(bpy.types.Operator):
    bl_idname = "om3d.atlas_pick"
    bl_label = "Select Material"
    bl_description = "Show this material's details below"
    bl_options = {"INTERNAL"}

    index: bpy.props.IntProperty()

    def execute(self, context):
        context.scene.om3d_atlas.index = self.index
        return {"FINISHED"}


class OM3D_OT_atlas_move(bpy.types.Operator):
    bl_idname = "om3d.atlas_move"
    bl_label = "Move to Set"
    bl_description = "Move this material to another set, or to a new set of its own"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty()
    target: bpy.props.EnumProperty(name="Set", items=_move_items)

    def execute(self, context):
        state = context.scene.om3d_atlas
        if not 0 <= self.index < len(state.items):
            return {"CANCELLED"}
        it = state.items[self.index]
        choice = self.target
        if choice == "__NEW__":
            used = {st.name for st in state.sets}
            base = "Masked" if it.masked else "Main"
            n = 1
            while f"{base}{n}" in used:
                n += 1
            choice = f"{base}{n}"
        it.set_name = choice
        return {"FINISHED"}


class OM3D_OT_atlas_rename_set(bpy.types.Operator):
    bl_idname = "om3d.atlas_rename_set"
    bl_label = "Rename Set"
    bl_description = "Rename this set (used in the material and texture names)"
    bl_options = {"REGISTER", "UNDO"}

    old: bpy.props.StringProperty()
    new: bpy.props.StringProperty(name="Name")

    def invoke(self, context, event):
        self.new = self.old
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        new = self.new.strip()
        if not new:
            return {"CANCELLED"}
        for it in context.scene.om3d_atlas.items:
            if it.set_name.strip() == self.old:
                it.set_name = new
        return {"FINISHED"}


class OM3D_OT_atlas_td_ignore(bpy.types.Operator):
    bl_idname = "om3d.atlas_td_ignore"
    bl_label = "Ignore in Density"
    bl_description = ("Leave this object out of the texel density check (e.g. a base mesh hidden "
                      "under fur cards). It is still packed")
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty()
    material: bpy.props.StringProperty()
    ignore: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None:
            return {"CANCELLED"}
        if self.ignore:
            obj[density.P_IGNORE] = True
        elif density.P_IGNORE in obj:
            del obj[density.P_IGNORE]
        packer.refresh_density(context, {self.material})
        return {"FINISHED"}


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

FLAG_ICON = {planner.FLAG_LOW: "ERROR", planner.FLAG_NOFIT: "CANCEL", planner.FLAG_CLAMP: "INFO"}


class OM3D_UL_atlas_choose(bpy.types.UIList):
    """Step 2: what gets packed."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "exclude", text="", icon="HIDE_ON" if item.exclude else "HIDE_OFF", emboss=False)
        sub = row.row()
        sub.active = not item.exclude
        ic = "CANCEL" if item.uv_error else ("MOD_MASK" if item.masked else "MATERIAL")
        sub.label(text=item.name, icon=ic)
        sub.label(text="Masked" if item.masked else "Opaque")


def _active(state):
    if 0 <= state.index < len(state.items):
        return state.items[state.index]
    return None


class _AtlasStep:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "OM3D"
    bl_parent_id = "OM3D_PT_atlas"

    def gate(self, context, need_scan=True, block_when_packed=True):
        """Grey out a step that can't be used yet. Returns (layout, state, ok)."""
        layout = self.layout
        state = context.scene.om3d_atlas
        if block_when_packed and packer.packed_objects(context):
            layout.label(text="Packed. Revert in step 5 to change this", icon="LOCKED")
            return layout, state, False
        if need_scan and not len(state.items):
            layout.label(text="Run step 1 Scan first", icon="INFO")
            return layout, state, False
        return layout, state, True


class OM3D_PT_atlas(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "OM3D"
    bl_label = f"Atlas v{VERSION}"

    def draw(self, context):
        state = context.scene.om3d_atlas
        packed = packer.packed_objects(context)
        if packed:
            self.layout.label(text=f"Packed: {len(packed)} objects", icon="CHECKMARK")
        elif len(state.items):
            n = sum(1 for it in state.items if not it.exclude)
            self.layout.label(text=f"{n} of {len(state.items)} materials, {len(state.sets)} sets planned")
        else:
            self.layout.label(text="Start with step 1")


class OM3D_PT_atlas_1_scan(_AtlasStep, bpy.types.Panel):
    bl_label = "1  Scan"

    def draw(self, context):
        layout, state, ok = self.gate(context, need_scan=False)
        if not ok:
            return
        col = layout.column()
        col.operator("om3d.atlas_scan", icon="VIEWZOOM")
        col.prop(state, "include_hidden")
        if len(state.items):
            col.label(text=f"{len(state.items)} materials found", icon="CHECKMARK")


class OM3D_PT_atlas_2_choose(_AtlasStep, bpy.types.Panel):
    bl_label = "2  Choose"

    def draw(self, context):
        layout, state, ok = self.gate(context)
        if not ok:
            return
        layout.label(text="Click the eye to leave a material out")
        layout.template_list("OM3D_UL_atlas_choose", "choose", state, "items", state, "index", rows=6)
        layout.prop(state, "isolate", icon="HIDE_ON" if state.isolate else "HIDE_OFF", toggle=True)
        it = _active(state)
        if it is None:
            return
        b = layout.box()
        col = b.column(align=True)
        col.label(text=f"{it.name}: {it.object_count} objects", icon="MATERIAL")
        col.label(text=f"Maps: {it.roles_raw or 'none'}  ({_k(it.src_res)} source)")
        if it.op_transparent >= 0:
            col.label(text=f"Opacity map: {it.op_transparent:.3f}% transparent")
            col.prop(it, "op_mode")
            if it.op_note:
                col.label(text=it.op_note, icon="INFO")
        if it.worst_obj:
            r = col.row(align=True)
            r.label(text=f"Worst: {it.worst_obj}  {it.td_src:.1f} px/cm")
            op = r.operator("om3d.atlas_select_object", text="", icon="RESTRICT_SELECT_OFF")
            op.name = it.worst_obj
            op = r.operator("om3d.atlas_td_ignore", text="", icon="X")
            op.name, op.material, op.ignore = it.worst_obj, it.name, True
        for n in [n for n in it.ignored.split(",") if n]:
            r = col.row(align=True)
            r.label(text=f"Ignored in density: {n}", icon="GHOST_DISABLED")
            op = r.operator("om3d.atlas_td_ignore", text="", icon="LOOP_BACK")
            op.name, op.material, op.ignore = n, it.name, False
        if it.note:
            for part in it.note.split(" | "):
                col.label(text=part, icon="ERROR")


class OM3D_PT_atlas_3_name(_AtlasStep, bpy.types.Panel):
    bl_label = "3  Name"

    def draw(self, context):
        layout, state, ok = self.gate(context)
        if not ok:
            return
        col = layout.column(align=True)
        col.label(text="Added to every material and texture created")
        col.prop(state, "prefix")
        col.prop(state, "suffix")
        sets = [s.name for s in state.sets] or ["Main1"]
        b = layout.box()
        c = b.column(align=True)
        c.label(text="Preview", icon="SORTALPHA")
        c.label(text=packer.material_name(context, state, sets[0]))
        c.label(text=packer.texture_name(context, state, sets[0], "D") + packer.EXT[state.file_format])
        c.label(text=packer.texture_name(context, state, sets[0], "N") + packer.EXT[state.file_format])


class OM3D_PT_atlas_4_plan(_AtlasStep, bpy.types.Panel):
    bl_label = "4  Plan"

    def draw(self, context):
        layout, state, ok = self.gate(context)
        if not ok:
            return
        col = layout.column(align=True)
        col.row(align=True).prop(state, "max_size", expand=True)
        col.prop(state, "target_td")
        col.prop(state, "inset")
        col.prop(state, "fill_gaps")
        layout.operator("om3d.atlas_auto_plan", icon="AUTO")

        index = {it.name: i for i, it in enumerate(state.items)}
        groups = {}
        for it in state.items:
            if not it.exclude:
                groups.setdefault(it.set_name.strip(), []).append(it)
        order = [st.name for st in state.sets if st.name in groups]
        if "" in groups:
            order.append("")
        info = {st.name: st for st in state.sets}
        sel = _active(state)
        for name in order:
            box = layout.box()
            st = info.get(name)
            head = box.row(align=True)
            if not name:
                head.label(text="No set", icon="ERROR")
            elif st and st.size:
                kind = "masked" if st.masked else "opaque"
                head.label(text=f"{name}   {_k(st.size)}   {st.fill * 100:.0f}% used   {kind}",
                           icon="ERROR" if st.mixed else "TEXTURE")
            else:
                head.label(text=f"{name}   does not fit {_k(int(state.max_size))}", icon="CANCEL")
            if name:
                op = head.operator("om3d.atlas_rename_set", text="", icon="GREASEPENCIL", emboss=False)
                op.old = name
            col = box.column(align=True)
            for it in sorted(groups[name], key=lambda x: (-x.tile, x.name)):
                row = col.row(align=True)
                split = row.split(factor=0.52, align=True)
                ic = FLAG_ICON.get(it.flag, "MOD_MASK" if it.masked else "MATERIAL")
                if it.uv_error:
                    ic = "CANCEL"
                op = split.operator("om3d.atlas_pick", text=it.name, icon=ic,
                                    depress=(sel is not None and sel.name == it.name))
                op.index = index[it.name]
                right = split.row(align=True)
                right.prop(it, "override", text="")
                low = it.td_tile >= 0 and it.td_tile < state.target_td
                right.label(text=f"{it.td_tile:.1f}" if it.td_tile >= 0 else "-",
                            icon="ERROR" if low else "BLANK1")
        if order:
            layout.label(text="Tile dropdown sets the size. Number = px/cm at that size.")

        if sel is not None and not sel.exclude:
            b = layout.box()
            c = b.column(align=True)
            c.label(text=sel.name, icon="MATERIAL")
            r = c.row(align=True)
            op = r.operator_menu_enum("om3d.atlas_move", "target", text=f"Move from {sel.set_name or 'no set'}",
                                      icon="FORWARD")
            op.index = state.index

            if sel.worst_obj:
                c.label(text=f"Worst object: {sel.worst_obj}")
            if sel.filled:
                c.label(text="Grown by Fill Empty Space", icon="INFO")
            if sel.flag == planner.FLAG_CLAMP:
                c.label(text="Tile clamped to source size", icon="INFO")
            if sel.flag == planner.FLAG_LOW:
                c.label(text="Below target even at full size", icon="ERROR")


class OM3D_PT_atlas_5_pack(_AtlasStep, bpy.types.Panel):
    bl_label = "5  Pack"

    def draw(self, context):
        layout, state, ok = self.gate(context, block_when_packed=False)
        if not ok:
            return
        if packer.packed_objects(context):
            if state.last_report:
                layout.label(text=state.last_report, icon="CHECKMARK")
            layout.operator("om3d.atlas_revert", icon="LOOP_BACK")
            return
        row = layout.row(align=True)
        row.prop(state, "file_format", text="")
        row.prop(state, "output_dir", text="")
        bad = packer.problems(context)
        warn = packer.warnings(context)
        if bad or warn:
            b = layout.box()
            for p in bad[:6]:
                b.label(text=p, icon="CANCEL")
            if len(bad) > 6:
                b.label(text=f"... {len(bad) - 6} more")
            for w in warn:
                b.label(text=w, icon="ERROR")
        row = layout.row()
        row.enabled = not bad
        row.scale_y = 1.4
        row.operator("om3d.atlas_pack", icon="IMAGE_DATA")


class OM3D_PT_atlas_6_results(_AtlasStep, bpy.types.Panel):
    bl_label = "6  Results"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        state = context.scene.om3d_atlas
        if not len(state.results):
            layout.label(text="Nothing packed yet")
            return
        sets = sum(1 for r in state.results if r.kind == "SET")
        texs = sum(1 for r in state.results if r.kind == "TEX")
        layout.label(text=f"{sets} sets, {texs} textures")
        if state.results_dir:
            layout.label(text=state.results_dir, icon="FILE_FOLDER")
        b = None
        for r in state.results:
            if r.kind == "SET":
                b = layout.box()
                b.label(text=r.text, icon="MATERIAL")
            elif b is not None:
                b.label(text=r.text, icon="IMAGE_DATA" if r.kind == "TEX" else "DOT")


classes = (
    OM3D_AT_MapType,
    OM3D_UL_at_map_types,
    OM3D_AT_Preferences,
    OM3D_OT_atlas_map_type_add,
    OM3D_OT_atlas_map_type_remove,
    OM3D_OT_atlas_map_type_defaults,
    OM3D_AT_Item,
    OM3D_AT_Set,
    OM3D_AT_Result,
    OM3D_AT_State,
    OM3D_OT_atlas_scan,
    OM3D_OT_atlas_auto_plan,
    OM3D_OT_atlas_pack,
    OM3D_OT_atlas_revert,
    OM3D_OT_atlas_select_object,
    OM3D_OT_atlas_pick,
    OM3D_OT_atlas_move,
    OM3D_OT_atlas_rename_set,
    OM3D_OT_atlas_td_ignore,
    OM3D_UL_atlas_choose,
    OM3D_PT_atlas,
    OM3D_PT_atlas_1_scan,
    OM3D_PT_atlas_2_choose,
    OM3D_PT_atlas_3_name,
    OM3D_PT_atlas_4_plan,
    OM3D_PT_atlas_5_pack,
    OM3D_PT_atlas_6_results,
)


def _fill_defaults():
    p = bpy.context.preferences.addons.get(__package__)
    if p and not len(p.preferences.map_types):
        load_default_types(p.preferences)
    return None


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.om3d_atlas = bpy.props.PointerProperty(type=OM3D_AT_State)
    bpy.app.timers.register(_fill_defaults, first_interval=0.5)


def unregister():
    del bpy.types.Scene.om3d_atlas
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
