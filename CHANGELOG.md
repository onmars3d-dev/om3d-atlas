# Changelog

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
