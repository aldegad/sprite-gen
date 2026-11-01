# Sheet Slicing — multi-figure grid sheets to per-cell standing cuts (tachi-e)

> Owns: Multi-figure grid sheets to per-cell standing cuts; the `cutout` routes · Index: [docs/README.md](README.md)

`slice_sheet_cells.py` (= `sprite_gen.cli slice-sheet`) turns ONE generated
image containing a COLSxROWS grid of the same character (expressions, poses,
outfits) into per-cell RGBA cuts on a fixed canvas with a shared feet
baseline and a normalized subject height. Built for dialogue cut-in
portraits (立ち絵); works for any "same subject, N variants in one sheet"
generation. For a sheet of different props or icons, `--scale sheet` keeps
the sizes they were drawn at and `--fit contain` keeps every cut whole
([Props and icons](#props-and-icons-sheet-scale-and-contain-fit)).

This is NOT the component-row animation pipeline. Rows animate one state
over time; a sheet enumerates variants of one standing subject. Use
`prepare`/`extract`/`compose-atlas` for animation, `slice-sheet` for
variant sheets.

## Alpha ownership

Alpha truth is `sprite_gen.frames.extract.remove_chroma_background` — the same
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
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/slice_sheet_cells.py \
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
  `extract`. The key distance is measured from the pure key *and* from the
  background colour detected on the sheet's borders, so a sheet the generator
  painted a darker green/magenta than asked still keys cleanly
  ([chroma-alpha.md](chroma-alpha.md)).

## Transparent sheets

A sheet a provider returned with real alpha (`gen --transparent` on a native-alpha route)
has no key to remove: pass `--chroma-key none` and the sheet keeps its own alpha.

Transparent sheets often carry a faint alpha haze around and between the drawn objects. It
joins neighbouring parts and enlarges a figure's measured height. `--alpha-floor N` (1..255;
`64` suits most generated sheets) clears it before parts are measured, with the shared
[alpha cleanup](#alpha-cleanup): pixels at or above the floor and the faint pixels
within `--alpha-edge` pixels (default 2) of them are kept as they are, every other faint pixel
becomes fully transparent. The run writes the cleanup's report to
`<out-dir>/<prefix>alpha-cleanup.json` (haze share before and after, pixels cleared, and the
count of solid and edge pixels that changed, which is `0`). Without the flag nothing changes:
the default `0` leaves the alpha exactly as extraction made it.

```bash
sprite-gen slice-sheet --sheet props.png --out-dir props/ --chroma-key none --alpha-floor 64 \
  --grid 3x2 --names pillar,statue,shrub,lantern,gravestone,fountain --prefix prop_
```

### Alpha cleanup

Transparent sheets from image models often carry a faint alpha haze: barely visible pixels around and between the drawn objects. `--alpha-floor` clears it with `sprite_gen.frames.alpha_floor.clear_haze`.

* A pixel at or above the floor (`--alpha-floor`) is solid. A pixel above zero and below the floor is faint.
* A faint pixel within `--alpha-edge` pixels (8-connected, so diagonals count) of a solid pixel is part of an object's anti-aliased edge and is kept unchanged.
* Every other faint pixel is haze and becomes fully transparent (`0, 0, 0, 0`). A pixel that was already fully transparent is left as it was.

Solid pixels and edge pixels are never changed, so a stroke stays whole as long as some of its pixels reach the floor. A stroke drawn entirely below the floor is indistinguishable from haze and is removed: lower the floor for very faint art. The report measures instead of asserting: it counts what actually changed.

| Field | Meaning |
|---|---|
| `floor`, `edge_px` | the settings |
| `pixels` | pixels in the image |
| `faint_fraction_before`, `faint_fraction_after` | share of all pixels with alpha from 1 to `floor - 1`, before and after |
| `haze_fraction_before`, `haze_fraction_after` | share of all pixels that are faint and farther than `edge_px` from a solid pixel |
| `cleared_pixels` | pixels made transparent |
| `changed_solid_pixels`, `changed_edge_pixels` | solid and kept-edge pixels whose value changed; `0` unless the cleanup is wrong |

## Props and icons: sheet scale and contain fit

The default suits a character's variants: each cell's main subject is scaled to `--target-height` on its own,
and the canvas cuts whatever that scale puts past its edge. A sheet of different things needs two other rules,
each a flag:

- **`--scale sheet`** takes one factor for the whole sheet: the tallest main subject reaches `--target-height`
  and every other cell is scaled by the same factor. A prop drawn twice as tall as another stays twice as tall,
  within a pixel. The default `--scale cell` gives every cell its own factor.
- **`--fit contain`** lowers the factor until the whole cut, every part the cell kept, fits the canvas: the main
  subject stays centred with its base on `--baseline-y`, and nothing past an edge is cut. A wide icon on a square
  canvas is scaled down to the canvas width instead of losing its sides. With `--scale sheet` the sheet takes the
  smallest factor any of its cells fits at, so the relative sizes stay. The default `--fit none` keeps the factor
  and lets the canvas cut. `contain` needs `--baseline-y` inside the canvas, and a part drawn below the main
  subject's base needs room under the baseline; a cell that cannot fit is refused by name.

```bash
# props: drawn sizes kept, nothing cut, feet on the canvas floor
sprite-gen slice-sheet --sheet props.png --out-dir props/ --chroma-key none --alpha-floor 64 \
  --grid 3x2 --names pillar,statue,shrub,lantern,gravestone,fountain --prefix prop_ \
  --cell-width 512 --cell-height 256 --baseline-y 256 --target-height 240 --scale sheet --fit contain
# icons: each icon as tall as the target unless it is wide, then as wide as the canvas
sprite-gen slice-sheet --sheet icons.png --out-dir icons/ --chroma-key none --alpha-floor 64 \
  --grid 4x3 --cell-width 256 --cell-height 256 --baseline-y 236 --target-height 216 --fit contain
```

Every cell of a sheet is cut on the same canvas, so a consumer that draws each cell at the same height scales them
all by the same amount and the sizes stay relative there too. The alpha
cleanup (`--alpha-floor`) runs before anything is measured, so the factor is taken on the cleaned sheet and applies to
every cell alike.

## Report

Every run writes `<out-dir>/<prefix>slice-sheet.json`:

```json
{
  "kind": "sprite-gen-slice-sheet",
  "version": 1,
  "sheet": "props.png",
  "grid": [3, 2],
  "canvas": {"w": 512, "h": 256},
  "baseline_y": 256.0,
  "target_height": 240.0,
  "scale": "sheet",
  "fit": "contain",
  "factor": 0.8,
  "factor_from": {"rule": "target_height", "cell": "fountain"},
  "alpha_cleanup": "prop_alpha-cleanup.json",
  "cells": [
    {"name": "pillar", "file": "prop_pillar.png", "factor": 0.8, "factor_from": "target_height",
     "subject": {"sheet": {"w": 120, "h": 260}, "canvas": {"w": 96, "h": 208}},
     "box": {"x": 208, "y": 48, "w": 96, "h": 208}, "anchor": [256.0, 256.0],
     "clipped_px": 0, "parts": 1}
  ]
}
```

- `scale` and `fit` name the rules the run used. `factor` is the one factor of a `sheet` run and `factor_from`
  says where it came from: `target_height` of the tallest cell, or `fit` of the cell that needed the smallest
  factor. Both are `null` for a `cell` run, where each cell has its own.
- Per cell, in reading order: `file` is the PNG beside the report; `factor` and `factor_from` (`target_height` or
  `fit`) are that cell's; `subject` is the main subject's size on the sheet and scaled onto the canvas; `box` is
  where the whole cut (every part kept) lies on the canvas; `anchor` is `[x, y]` on the canvas, the centre of the
  main subject's base, the point it stands on; `clipped_px` counts the cut's pixels with any alpha that fell past
  the canvas edge (always `0` with `contain`; the run also prints a line for each cell the canvas cut); `parts` is
  how many parts the cell kept.
- `anchor` is in the PNG's own pixels, so an engine can take it as the image's origin as it is.

The PNGs of a default run (`--scale cell --fit none`) are byte for byte those of the version before these flags.

## Geometry rules (and the field failures they encode)

Each rule below exists because the naive version shipped a visible defect
during the a cut-in overhaul (2026-07-09):

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
   asset-side height consistency wins. The figure is scaled with its
   coverage and its colour taken apart (`resize_cell`,
   [video-pipeline.md](video-pipeline.md) "Cells"), so the scaled figure has no rim or key tint at its edge that
   the sliced figure did not have.
5. **Feet on the baseline.** The main figure's lowest pixel lands on
   `--baseline-y`; effects above the head never shift the feet. Under the
   default `--fit none` a cut that crosses the canvas's top or left edge is
   moved back inside and the canvas cuts it on the other side; the report's
   `anchor`, `box` and `clipped_px` say where it landed and what was cut.
   `--fit contain` scales it until it fits instead.
6. **Fail loud on empty cells.** A cell with no subject after extraction
   raises instead of writing a blank — a cropped or mis-gridded sheet must
   be regenerated, not silently absorbed. Every cell is measured before any
   is written, so a failing sheet writes no cells.

## Prompting the sheet

Ask for: exactly COLSxROWS figures, one full body per cell with head and
feet fully inside the sheet (generators love cropping the top row's feet at
the row boundary — say "shoe soles fully visible"), identical body size and
feet line across cells, the flat chroma background, and no grid lines,
labels, or numbers. Two-arm/anatomy constraints belong in the prompt too:
the slicer cannot repair a three-armed generation, only regeneration fixes
bad drawing (same doctrine as the row pipeline).

## Related

- [docs/README.md](README.md) — documentation index
