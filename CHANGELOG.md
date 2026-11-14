# Changelog

## v0.4.5 (2026-11-13)

- Revert lets you choose what happens to the packed textures: Move (to the `_reverted` folder, the default), Delete (for good, handy while testing so old atlases don't pile up) or Keep. The choice is remembered until Blender closes. Delete follows the same safety rules as Move: only files the last Pack wrote, and only if they are unchanged since Pack.

## v0.4.4 (2026-11-13)

- Revert no longer deletes anything. It moves the files the last Pack wrote into a `_reverted` folder next to them, and only files that are unchanged since Pack (same size and modified time). A texture you repainted or re-exported, a file another image in the .blend still uses, and anything that isn't a texture all stay where they are and are listed.
- The Revert dialog now shows the Remove Packed Textures checkbox and the file names before anything happens. In 0.4.3 the option only appeared after the files were gone.
- Reverting with the option off forgets the file list, so a later Revert can't take files you chose to keep. A list saved by 0.4.3 is never acted on.
- Pack records each file as it is written, so a Pack that stops halfway still knows its files.
- Scan, Auto Plan, Pack and Revert only run in Object Mode.
- New problems that block Pack with a plain message: a texture file missing on disk, a mesh that already has 8 UV maps, an empty output folder, a prefix, suffix or set name with a character a file name can't use, and UDIM UVs outside the UDIM range.
- A material slot with no faces no longer makes a UDIM material look like it uses two tiles.
- If Pack hits an unexpected error it reports it in the status bar instead of a Python traceback.
- Layout Image no longer removes an image of the same name that it didn't create.
- The manifest declares the files permission, and the tagline fits Blender's 64 character limit.

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
