# Changelog

## v0.4.3 (2026-11-04)

- Revert now deletes the atlas textures and layout image the last Pack wrote (only those files, and not any that an image in the file still uses). The confirm dialog says how many files it will delete; a "Delete Packed Textures" option turns it off.

## v0.4.2 (2026-11-04)

- UDIM texture sets: when a source map is a UDIM (tiled) image shared by several materials, each material now reads the tile its UVs sit in (e.g. u1 v0 reads 1002). Before, every material got tile 1001, so packed textures came out wrong. Opacity detection reads the right tile too. A material whose objects use different tiles of one UDIM image, or whose tile file is missing, is flagged in step 2 and blocks the pack.

## v0.4.1 (2026-11-04)

- UDIM-style layouts: when a material's UVs sit inside one unit tile other than 0-1 (each material in its own tile), Pack moves them back to 0-1 before placing them in the atlas. Step 2 shows "UVs in tile u3 v1: moved to 0-1". Only UVs spanning more than one tile still block the pack.
- A UV map already named `AtlasUV` is renamed `AtlasUV_prev` on Pack and named back on Revert.

## v0.4.0 (2026-10-31)

- Max Sets (step 4, 0 = off): cap the number of atlas sets (opaque + masked). Auto Plan shrinks the tiles that lose the least density until the plan fits, then undoes shrinks it didn't need. Shrunk tiles show "Auto 1K (shrunk)", and any below target are flagged LOW. Runs before Fill Empty Space, which never adds a sheet.
- Preview Colors / Material Preview: colour every object by its set (grey = not packed) in Solid view, then restore the objects' own colours and Material Preview. Works before and after Pack.
- Layout Image: draws the plan (sheets to scale, tiles, size, px/cm, low tiles in red) to `<Prefix>_Layout.png` in the output folder and shows it in an Image Editor. Written automatically on Pack (skipped in background runs).
- Pack asks before overwriting existing atlas textures.
- Fix: opaque sets no longer keep a source diffuse alpha (e.g. a near-solid one); their material's Alpha is unlinked and set to 1.
- Fix: original materials get a fake user on Pack so Revert still works after save and reopen. Revert clears only the fake users it set.
- Blender 4.5+: no warning for the relative output folder default.
- Faster per-face material lookup.

## v0.3.0 (2026-10-15)

- Fill Empty Space (step 4, on by default, part of Auto Plan): after packing, each sheet shrinks to its smallest size, then tiles grow x2, lowest density first, into leftover space. It never adds a sheet, never grows a sheet, never goes above source resolution, and never touches hand-set tiles. Grown tiles show "Auto 2K (filled)".
- Step 4 Plan is grouped by set: one box per set (name, size, % used, opaque/masked, rename button), rows with full material name, tile dropdown and px/cm.
- Selected material: "Move from <set>" menu (existing set or New Set), worst object, filled/clamp/low notes.

## v0.2.0 (2026-10-14)

- Panel split into six steps: Scan, Choose, Name, Plan, Pack, Results.
- Prefix/Suffix naming with live preview: `M_<Prefix>_<Set><Suffix>`, `T_<Prefix>_<Set><Suffix>_<Map>`. M_/T_ editable in preferences.
- Opaque and masked never share a set in Auto Plan; a hand-made mix shows a warning.
- OP maps with under 0.1% transparent pixels count as opaque; per-material opacity override.
- Per-object Ignore in density. Show Only Packed Objects (hide and exact restore).
- Excluding a material (eye toggle) leaves it untouched.

## v0.1.0 (2026-10-14)

- First version: scan, map discovery (node trace, suffix fallback), worst-object texel density, quadtree Auto Plan, Atlas Padding, pack to TGA/PNG, AtlasUV on channel 0 with the original UV on channel 1, Revert.
