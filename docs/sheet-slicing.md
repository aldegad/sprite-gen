# Sheet Slicing — multi-figure grid sheets to per-cell standing cuts (tachi-e)

`slice_sheet_cells.py` (= `sprite_gen.cli slice-sheet`) turns ONE generated
image containing a COLSxROWS grid of the same character (expressions, poses,
outfits) into per-cell RGBA cuts on a fixed canvas with a shared feet
baseline and a normalized subject height. Built for dialogue cut-in
portraits (立ち絵); works for any "same subject, N variants in one sheet"
generation.

This is NOT the component-row animation pipeline. Rows animate one state
over time; a sheet enumerates variants of one standing subject. Use
`prepare`/`extract`/`compose-atlas` for animation, `slice-sheet` for
variant sheets.

## Alpha ownership

Alpha truth is `sprite_gen.extract.remove_chroma_background` — the same
v1.13 4-pass chain the row pipeline uses (hard key cut → key-depth in-band
unmix → soft-alpha unmix → trapped-spill despill). `slice-sheet` owns only
cell geometry. Generate sheets on a chroma background chosen by the
subject-color gate in image-gen's SKILL.md (pink/purple material → green
key, green material → magenta key). Never generate variant sheets on a
cream/white background and flood-fill them — background pockets trapped
between props and legs survive as opaque bands, and light clothing is one
tolerance away from being punched full of holes (2026-07-09 field
accident, twice).

## Usage

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/slice_sheet_cells.py \
  --sheet raw/hero_expressions.png \
  --out-dir portraits/hero \
  --chroma-key green \
  --grid 3x2 \
  --names default,smile,sad,annoyed,angry,pleased \
  --prefix hero_ \
  --cell-width 512 --cell-height 768 \
  --baseline-y 725 --target-height 645
```

- `--names` count must equal the grid cell count (reading order,
  left-to-right then top-to-bottom). Omit for `cell-N` names.
- `--target-height` is the character's canonical body height on the output
  canvas. Keep one value per character across every sheet so a cast keeps
  its height hierarchy.
- Extraction tunables (`--key-threshold`, `--fringe-*`, `--spill-max-fraction`)
  pass through to `remove_chroma_background` with the same defaults as
  `extract`.

## Geometry rules (and the field failures they encode)

Each rule below exists because the naive version shipped a visible defect
during the Sol Valley cut-in overhaul (2026-07-09):

1. **Component-to-cell assignment by centroid, not grid cropping.** Grid
   cropping slices a figure's own cape/broom at the cell border and imports
   the neighbour's overflow. Whole-sheet connected components go to the
   cell containing their centroid, so a figure keeps everything attached
   to it.
2. **Merged-figure split + in-cell re-label.** Adjacent figures fused
   through a touching prop (kunai tip into the next figure's hair) form one
   sheet-wide component spanning cells. Pixels split at cell borders, then
   connectivity is re-run *inside* each cell — without the re-label, the
   neighbour's hair stays fused to the cell's own figure and ships as a
   floating clothes/hair fragment.
3. **Neighbour-debris rule.** After splitting, fragments that touch the
   cell border and are far smaller than the cell's main figure
   (`--debris-fraction`, default 0.30) are neighbour overhang — dropped.
   Detached effects (hearts, sparkles, ZZZ) sit inside the cell and
   survive.
4. **Per-cell height normalization.** Generators routinely draw grid rows
   at different sizes (one sheet shipped a top row ~40% taller than the
   bottom row). Sheet-wide max-height scaling preserves that jitter; each
   cell's main figure is scaled independently so every variant's body
   height equals `--target-height`. Trade-off: a bowed pose (sleep) gets a
   slightly larger body — runtime cut-in renderers assume one scale, so
   asset-side height consistency wins.
5. **Feet on the baseline.** The main figure's lowest pixel lands on
   `--baseline-y`; effects above the head never shift the feet.
6. **Fail loud on empty cells.** A cell with no subject after extraction
   raises instead of writing a blank — a cropped or mis-gridded sheet must
   be regenerated, not silently absorbed.

## Prompting the sheet

Ask for: exactly COLSxROWS figures, one full body per cell with head and
feet fully inside the sheet (generators love cropping the top row's feet at
the row boundary — say "shoe soles fully visible"), identical body size and
feet line across cells, the flat chroma background, and no grid lines,
labels, or numbers. Two-arm/anatomy constraints belong in the prompt too:
the slicer cannot repair a three-armed generation, only regeneration fixes
bad drawing (same doctrine as the row pipeline).
