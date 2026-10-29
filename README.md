# OM3D Atlas

Blender extension that packs a character's texture sets into fewer atlases, with texel density checks. It lives in the **OM3D** tab of the 3D Viewport sidebar.

Made by Aunmar at [OnMars3D](https://onmars3d.com). Built with Claude.

Blender 4.2 or newer (developed on 5.2).

## Install

1. Download `om3d_atlas_v0.3.0.zip` from the Releases page.
2. In Blender: Edit > Preferences > Get Extensions > Install from Disk (or drag the zip into Blender).
3. Open the sidebar (N) in the 3D Viewport and go to **OM3D > Atlas**.

## Workflow

The panel walks through six steps. Later steps stay greyed out until Scan has run, and steps 2 to 4 lock while a pack is applied.

1. **Scan.** Collects the materials in the scene and finds their maps.
2. **Choose.** Eye toggle per material (excluded materials are left untouched), Show Only Packed Objects, opacity info, worst object and Ignore in density.
3. **Name.** Prefix and Suffix with a live preview: `M_<Prefix>_<Set><Suffix>` and `T_<Prefix>_<Set><Suffix>_<Map>`.
4. **Plan.** One box per set (size, % used, opaque or masked, rename). Each row has the material, a tile dropdown and px/cm. Move a material to another set or a new one.
5. **Pack.** Problems in red (they block the pack), warnings in yellow. Pack or Revert.
6. **Results.** Snapshot of the sets, tiles with px/cm, and textures written.

## How it plans

- **Map discovery:** follows the shader nodes first (Base Color = D, Separate Color into Roughness/Metallic = ORM, image behind a Normal Map = N, Alpha = OP). Filename suffixes (`_D`, `_ORM`, `_N`, `_OP`, `_E`) are the fallback and can be edited in preferences.
- **Texel density:** per object, area-weighted px/cm at the tile size it would get after padding. A material is judged by its worst visible object. Hidden objects are skipped by default, and any object can be set to Ignore in density (e.g. a base mesh under fur cards). The target defaults to 10 px/cm.
- **Opacity:** a material is masked only if its OP map has real transparency (more than 0.1% transparent pixels). There's a per-material override (Auto / Opaque / Masked). Auto Plan never puts opaque and masked in the same set.
- **Auto Plan:** opaque and masked groups are packed separately with first-fit decreasing into max-size sheets on a quadtree grid (power-of-two square tiles down to 1/8 of the sheet width). Each sheet then shrinks to the smallest size that holds it. Fill Empty Space grows tiles into leftover room, lowest density first.
- Tiles are never larger than their source texture. A material that's below target even at full resolution gets its own full-size tile and a red flag.

## Output

- Atlas materials are copies of the set member with the most maps, with images swapped to the atlas maps and every image and Normal Map node pointed at `AtlasUV`. Custom nodes and blend settings are kept.
- Textures go to `//Textures/Atlas` as TGA by default (PNG optional).
- Downsampling is 2x2 box averaging per halving, then bilinear. Normals are renormalized and keep their convention (DirectX stays DirectX). Alpha is kept when a source tile has it.
- Atlas Padding: 8 px per tile edge by default, filled by edge-extend.
- `AtlasUV` becomes UV channel 0, and the original UV stays on channel 1. Shared mesh data is processed once. UVs outside 0-1 or a missing UV map block the pack and name the object.
- Objects store their original materials, and Revert restores materials and removes AtlasUV. The tool never saves the .blend. The .blend must be saved before packing, because the output folder is relative to it.

## Known limits

- No 2:1 sheets (e.g. 4096x2048) for half-empty sets yet.
- Tint mask RGB packing and group hints for Auto Plan are planned, not built.
- Map types are copied as they are, with no channel repacking.

## About this tool

I built this for my own workflow and I'm sharing it as a starting point. Use it, change it, make it yours. It's provided as is, so test it on a copy of your scene first.

## License

GPL-3.0-or-later. See `LICENSE`.
