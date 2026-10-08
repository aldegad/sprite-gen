# Direction sets — `video-set-export`

> Owns: a finished `video-set` as one eight-heading set for a top-down game · Index: [docs/README.md](README.md)

A top-down orthographic game asks for its character by compass heading — S, SE, E, NE, N, NW, W, SW,
screen-up north. A `video-set` is filmed by view and facing (`front`, `side` facing right, …) and usually
in five views, the left-hand ones left to a mirror. `sprite-gen video-set-export` turns the set's final
strips into one folder a loader reads: a strip and its json per heading, the missing left or right headings
mirrored cell by cell with their provenance, one foot pivot per strip, the standing heights compared, and a
`directions.json` that says all of it. It reads the set and never writes into it; nothing is generated.

Productions did this by hand before (crashbang `stocky-village-ortho-20261006/mirror_run.py`,
`kuma-village-ortho-20261007/assemble.py`, `prep_place.py`, `pack.mjs`, 2026-10): per-cell mirrors that
recorded `mirroredFrom` and the source's sha256, a per-frame x offset negated, one foot origin per clip and a
heading table. This is that step, once.

```bash
sprite-gen video-set --base front=front.png --base back=back.png --base side=side.png \
  --base front_diagonal=fd.png --base back_diagonal=bd.png --states walk,run,attack --body-height 400 --out-dir set/
sprite-gen video-set-export --set-dir set/ --out-dir set-8dir/                                # every state, eight headings
sprite-gen video-set-export --set-dir set/ --out-dir set-8dir/ --headings walk=all,run=all,attack=diagonals
```

| Flag | |
|---|---|
| `--set-dir` | a finished `video-set --out-dir` (it holds `set.report.json`) |
| `--out-dir` | where the set is written: an empty or new folder, or a previous export's (its own files are replaced; anything else in a non-empty folder is refused) |
| `--states` | comma list of the states to export (default: every state of the set) |
| `--headings` | the headings each state is exported in: `all` (default, the eight), `diagonals` (SE, SW, NE, NW), a list (`S,SE,E`), or per state, `walk=all,attack=diagonals,hit=SE,SW` (a state not named takes `all`) |
| `--allow-mirror-handed` | mirror a set filmed with `--handed` anyway, recorded per heading (see below) |
| `--keep-mirror-phase` | leave a mirrored aligned loop starting on its first cell (see "The start foot") |

**`--headings`.** A production often films its actions in fewer headings than its movement: the crashbang KUMA
pack (2026-10-07) took walk and run in all eight and hit, slam and combo in the four diagonals, two filmed
(front and back diagonal, facing right) and two mirrored. A heading a state does not ask for is not written
and is no error; a mirror's source is still read where it is not asked for (`--headings W` writes W from E
and no E). Each state records what it asked for (`requested`, and `requested_as` as it was said).

Exit 0 when every exported state has every heading it asked for. A state short of one is still written, and
the exit is 1: the record says which heading is missing and why.

## Headings

Screen-up is N, x runs right and y down (image pixels). The order is clockwise on screen from screen-down.

| Heading | View the set films | Screen vector (x, y down) | Made by a mirror of |
|---|---|---|---|
| S | `front` | (0, 1) | never |
| SE | `front_diagonal`, facing right | (0.7071, 0.7071) | SW |
| E | `side`, facing right | (1, 0) | W |
| NE | `back_diagonal`, facing right | (0.7071, -0.7071) | NW |
| N | `back` | (0, -1) | never |
| NW | `back_diagonal`, facing left | (-0.7071, -0.7071) | NE |
| W | `side`, facing left | (-1, 0) | E |
| SW | `front_diagonal`, facing left | (-0.7071, 0.7071) | SE |

A diagonal is the turn the set's prompts film: a front diagonal walks "down and to the right" as in an
isometric game, a back diagonal "up and to the right" ([video pipeline](video-pipeline.md) §2). The table is
written into `directions.json` under `convention`, so a loader never has to know it.

## Which strip

Each item's **final** strip: the one `<item>.strip.png` (and `.strip.json`) in `<set>/<item>/loop/`.
`video-cycle-align` and `video-follow` rewrite that same file, so it is the aligned one where the alignment
ran and the cut as filmed where it did not; the heading's `aligned` says which (the json carries
`cycle_align`). Where `set.report.json` says a state was aligned and an item's strip carries no alignment, the
loop was cut again afterwards, and a warning names it.

## Mirroring

A heading the set did not film whose horizontal opposite it did (E↔W, SE↔SW, NE↔NW) is made from that
strip. S and N have no opposite and are never mirrored.

**Per cell.** A strip is one row of cells, `frames` of `w`×`h`, played left to right. Turning the whole image
over would turn every cell over and also put the last cell first: the walk would play backwards. Each cell is
turned over where it stands instead, and the cell order is kept.

**The json.** The mirrored heading's json is the source's, with every x it is known to hold mirrored:
positions inside a cell (`anchor`, `foot_x`) become `w − x`, signed horizontal offsets (`wrap_dx_px`, a
production's keypose `sequence[].shake[0]`) are negated. The records of how the source was cut — its
source-frame rectangle (`source_rect`), `motion_anchor`, `cycle_align` (its view and start foot), `follow` —
describe the source's pixels and sides, so they are not carried; they stay readable in the opposite heading's
json, which is the source's copied byte for byte. The json ends with `mirroredFrom`: the heading, item and
strip it came from, that strip's sha256, and which fields were mirrored, negated and not carried.

**The start foot.** A mirror swaps the character's own sides, so a loop aligned to start as the right foot
lands (`video-cycle-align`, the set's start foot) starts, mirrored, as the left foot lands — and a game that
keeps the cell index when the character turns would change step there. So a mirrored aligned loop is started
later, the same cycle played forward from another cell (`phase_rotated_by` cells, `phase_why` says from where):

1. the source's other strike, where its other own foot landed — the one of `cycle_align.strikes` (frames of
   the aligned cycle) that is not 0, at the cell showing that frame. Mirrored, that is where the set's foot
   lands;
2. without strikes, half the cells, but only for a loop aligned as one cycle (no `--cycles` count above 1
   left in it, `steps` 2 or unknown);
3. otherwise it is not turned, and the warning below stays.

Not turned either: a loop whose alignment did not name its start foot (the set's foot is not known there), a
one-shot, and every mirror under `--keep-mirror-phase`. The per-cell lists of the mirrored json
(`sample_indices`, `source_cut.samples`) start where the cells start. Each heading records its `start_foot`;
a state whose headings still start on different feet gets a warning.

**Previews.** A filmed heading's GIF and WebP are copied beside its strip. A mirror's are the source's turned
over frame by frame, each frame's duration and the loop count kept (`previews_from: source-mirrored`); a
mirror turned to the set's start foot has frames that no longer line up with the source's, so its previews
are written from its own cells at the strip's cell delay, as `video-cycle-align` writes an aligned loop's
(`previews_from: cells`). A WebP is written as video-loop writes one (img2webp, lossless, `-exact`); without
an img2webp that has `-exact`, a mirror has no WebP and `preview_notes` says so.

**`--handed`: refused.** A set filmed with `--handed` has an item on one of the character's own sides, and a
mirror moves it to the other ([handedness](video-pipeline.md#handedness--an-item-on-one-side)). The export
stops before writing anything, naming every heading it would mirror, and says to film that facing:
`video-set --facing right,left` with a still drawn each way. `--allow-mirror-handed` mirrors anyway; each such
heading is marked `handed_override`, the index records `allow_mirror_handed` and the set's `handed` items,
and a warning says where the items now show on the wrong side.

**A lost heading is missing, not mirrored.** A heading whose item the set filmed and failed is reported
missing with the item's error, even when its opposite is there: the set asked for that facing drawn, perhaps
for a reason. Re-run `video-set` (it reuses the clips that worked).

## Pivot

One foot pivot per heading strip, `[x, y]` in the cell's pixels from its top-left corner, on pixel edges
(`y = h` is the cell's bottom edge), so a mirror maps it to `[w − x, y]` exactly.

- `strip-anchor`: the strip's own `anchor` — `video-loop --anchor feet` (or `video-set --anchor feet`) writes
  the foot line at the cell's bottom.
- `alpha`: otherwise, measured on the strip's solid pixels (alpha ≥ 128): y is the floor, the lowest solid
  row's bottom edge over every cell (where `--anchor feet` stands its pivot too); x is the median, over the
  cells standing on that floor (within 15 % of the cell height of it, so a hop's airborne cells do not count),
  of the centre of each cell's foot band — the lowest 16 % of its solid height.

  Both numbers are for a camera from above, which draws the near foot lower on screen than the far one.
  Measured on the crashbang KUMA walks (640 px body, 35°, 2026-10-08, the pivots checked by eye on their
  cells): the lowest 8 % (a side view's sole line, `legs.FOOT_BAND`) held one foot only and put the
  back-diagonal walk's pivot on its near foot (232 px of a 548 px cell, 261 with both feet); a 3 % ground
  tolerance kept only the cells one foot was lowest in, since the lowest row moves 9 % of the body with every
  step, and put the front walk's pivot 62 px off its body (152 px of 443, 214 over the whole cycle).
- A mirror takes its source's pivot mirrored: `mirrored:strip-anchor` or `mirrored:alpha`.

The y is the floor, not the mean of the two soles (`prep_place.py` took that mean): under a camera from above
the near foot's sole reaches below the point the body stands over, by up to half the step's depth, so a
loader that wants the body's ground point raises the pivot by that much itself. The floor is what video-loop's
own `anchor` declares, so a strip with one and a strip without stand the same way.

The pivot is in `directions.json`; a filmed heading's json is the source's own, unchanged.

## Size check

Per state, each filmed heading's standing height (`body_h`, as video-loop recorded it; measured on the alpha
only for a strip without one) and the spread, the tallest over the shortest minus one. Past **3 %** a warning
names the headings; nothing is rescaled. A set cut with one `--body-height` lands every heading on its target
(unless a cell met the strip's height cap first) and rounding moves `body_h` by one pixel, so past 3 % is a
real size difference — a set cut without the target, a capped cell, a still drawn taller: 12 px on a 400 px body,
a visible pop when the character turns. Chosen, not measured over many sets; the record keeps the numbers.

Two more warnings, also never fixed by the export: looping headings of one state with different cell counts
(a game that keeps the cell index on a turn needs one length: `video-cycle-align`), and different start feet
(above).

## Output

```text
<out-dir>/
  directions.json
  table.md
  <state>/<HEADING>.png       the strip: one row of cells, played left to right
  <state>/<HEADING>.json      a filmed heading: the strip's json as filmed; a mirrored one: see Mirroring
  <state>/<HEADING>.gif|webp  where the filmed heading (or a mirror's source) has one; see Previews
```

Only the headings a state asks for (`--headings`) are written. The same set gives the same bytes: no time
stamps, the headings in their fixed order, a mirror's PNG and GIF encoded the same way every time (its WebP
too, with the same img2webp).

`directions.json`: `kind: "sprite-gen-direction-set"`, `schema_version: 1`.

| Field | Meaning |
|---|---|
| `convention` | `projection`, `up` (N), `axes`, `headings` and their `order`, `vector` and `view` per heading, `mirror_of`, what `pivot` measures |
| `source` | `set_dir`, `set_report_sha256`, and from the set: `facing`, `handed`, `camera_elevation`, `body_height` |
| `headings_arg`, `allow_mirror_handed`, `keep_mirror_phase`, `size_spread_max` | as run |
| `complete` | every exported state has every heading it asked for |
| `states.<state>.requested`, `.requested_as` | the headings it asked for, and how (`all`, `diagonals`, `S,SE,E`) |
| `states.<state>.complete`, `.missing` | `{heading: reason}` for each heading asked for with nothing to stand on |
| `states.<state>.size` | `body_h` per filmed heading, `spread`, `max`, `ok` |
| `states.<state>.headings.<H>.status` | `filmed`, `mirrored` or `missing` (then `reason`) |
| `…view` | the view the heading stands for (`side@left`), filmed or not |
| `…source_item`, `…source_strip`, `…source_sha256` | the set item, its final strip (relative to the set) and that strip's sha256 |
| `…mirroredFrom`, `…mirror` | mirrored only: the heading it was made from; `per_cell`, `x_mirrored`, `x_negated`, `not_carried` |
| `…phase_rotated_by`, `…phase_why` | mirrored only: cells the loop was started later by (0: not turned), and why |
| `…file`, `…sha256`, `…meta`, `…previews` | the strip written (and its sha256), its json, its GIF/WebP |
| `…previews_from`, `…preview_notes` | mirrored only: `source-mirrored` or `cells`; a preview not made, and why |
| `…aligned`, `…loop` | the strip was cycle-aligned; it loops (false for a one-shot) |
| `…cells`, `…fps`, `…delay_ms`, `…cell` | cell count, cells per second (cells over the cycle's seconds), per-cell delay, `[w, h]` |
| `…pivot`, `…pivot_source` | see Pivot |
| `…body_h`, `…body_h_source` | standing height in px; `strip-meta` or `alpha` |
| `…start_foot` | the own foot landing on the first cell, where the alignment named it |
| `…handed_override` | mirrored with `--allow-mirror-handed` |
| `warnings` | each line also printed to stderr |

`table.md` is one row per state and heading (status, source, cells, fps, cell, pivot, body_h), a line per
state (complete or not, what it asked for, the height spread) and the warnings.

## Related

- [video-pipeline.md](video-pipeline.md) §5 — the set this reads; handedness
- [loop-repair.md](loop-repair.md) §4 — one cycle length and one start foot across a set
