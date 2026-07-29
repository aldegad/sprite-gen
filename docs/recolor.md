# Recolor — deterministic palette-swap bake + colourway pick — sprite-gen reference

> Split out of the `SKILL.md` hub. Follow this doc when you need to bake N colour
> variants of one base sheet from a palette map, or when the curation webview is
> comparing and adopting those baked colourways. Implementation:
> `sprite_gen/recolor.py`. View surface: `sprite_gen/curator/src/recolor.js` +
> `serve_curation._recolor_info`.

## Why bake, not tint at runtime

Dot art lives and dies by palette control. A baked sheet is QA-able pixel by
pixel, costs the renderer nothing, and keeps every colourway under the same
geometry contract as the base atlas. Runtime shaders/filters are out of scope —
they reintroduce anti-aliased edge bleed and cannot be diffed as assets.

Same input bytes → same output bytes. The bake does no floating-point
resampling; it rewrites RGB only and preserves alpha, so a base
`manifest.json.frame_layout` describes every variant sheet.

## Triggers (agent routing)

Use this path when the user says any of:

- **KR:** 팔레트 스왑, 팔레트 베이크, 리컬러, 색깔 바꾸기, 컬러웨이, 색 변형, 팔레트 맵,
  색갈이, recolor 해줘
- **EN:** palette swap, recolor, colourway / colorway, bake variants, palette map,
  recolour the sheet

Not this path: live tint filters, shader recolor, one-off hand-painted
recolours that skip the bake report.

## Commands

Two subcommands share the module. The console script, the module form, and the
`scripts/recolor.py` wrapper all reach the same `cli.COMMANDS` entry (identity,
not a copied flag list — see `tests/test_recolor_cli_entrypoint.py`).

### 1. Draft a palette map from a base sheet

```bash
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen recolor-palette \
  --base <sheet.png> \
  --out <palette.json>
```

- Scans opaque pixels (`alpha > 8` by default — same floor as compose-gif).
- Emits distinct colours ordered by frequency (desc), then hex (asc) — a total
  order, so two runs of the same sheet produce the same draft.
- `--max-colors N` keeps the top-N only and **reports** how many pixels were
  dropped (`dropped_colors_pixels`) — never a silent cap.
- Without `--out`, the JSON goes to stdout.

Draft kind: `"kind": "sprite-gen-palette"`. This is authoring scaffolding, not a
swap. Edit the hex list into a recolor **spec** (next section).

### 2. Bake N variant sheets from a recolor spec

```bash
# Against a finished run dir (default atlas = sprite-sheet-alpha.png,
# default out = <run-dir>/variants/, manifest auto-resolved if present):
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen recolor \
  --run-dir <run-dir> \
  --spec <recolor-spec.json>

# Against a free-standing sheet (out-dir required):
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen recolor \
  --base <sheet.png> \
  --spec <recolor-spec.json> \
  --out-dir <out-dir> \
  [--manifest <manifest.json>]
```

Exactly one of `--base` or `--run-dir`. Two truths for one input are refused.

Flags:

| Flag | Default | Role |
|---|---|---|
| `--atlas` | `sprite-sheet-alpha.png` | filename inside `--run-dir` |
| `--out-dir` | `<run-dir>/variants` (with `--run-dir`) | where sheets + report land |
| `--manifest` | auto `<run-dir>/manifest.json` if present | propagate per-variant, repointing sheet-filename fields |
| `--alpha-threshold` | `8` | opaque floor; sub-threshold pixels keep RGB, are not targets |
| `--report` | `recolor.report.json` | report filename inside out-dir |

Also available as:

```bash
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python -m sprite_gen.cli recolor ...
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/recolor.py ...
```

## Recolor spec

```json
{
  "version": 1,
  "kind": "sprite-gen-recolor",
  "match": "exact",
  "tolerance": 0,
  "variants": [
    {
      "name": "moss",
      "map": {
        "#b42828": "#3a7a3a",
        "#f0c000": "#c8a040"
      }
    },
    {
      "name": "azure",
      "map": {
        "#b42828": "#2850b4",
        "#f0c000": "#80c0e0"
      }
    }
  ]
}
```

- `kind` must be `sprite-gen-recolor` — anything else fails loud.
- `match`:
  - **`exact`** (default) — a pixel's RGB must equal a source colour to be
    replaced. Dot art's safe default: anti-aliased edge pixels stay untouched.
  - **`tolerance`** — opt-in for soft-edged art. An opaque pixel within Chebyshev
    distance `tolerance` of a source is recoloured to that source's target;
    nearest source (L2) wins, ties broken by map order. Pure integer arithmetic,
    so still deterministic.
- `match: "exact"` with a non-zero `tolerance` is contradictory and refused —
  two truths for one behaviour.
- Variant `name`s must be unique; every variant needs a non-empty `map`.
- Map keys/values are `#RRGGBB` (or `RRGGBB`). A source mapped twice fails loud.

## Bake outputs

Under `--out-dir` (default `<run-dir>/variants/`):

```text
variants/
  moss.png
  azure.png
  moss.manifest.json          # only with --manifest
  azure.manifest.json
  recolor.report.json         # SSoT for "what the bake did"
```

Geometry is never touched — only RGB on matching opaque pixels. Alpha is
byte-identical to the base sheet. Manifest sheet-filename fields that are
rewritten per variant (when `--manifest` is set): `atlas`, `game_input`,
`sprite_sheet_alpha`, `sprite_sheet`. Unknown schema keys are copied verbatim.

### Report contract (No Silent Fallback)

`recolor.report.json` (`kind: "sprite-gen-recolor-report"`) is the one truth for
what the bake did. Per variant it records:

| Field | Meaning |
|---|---|
| `substituted_pixels` | total opaque pixels rewritten |
| `substitutions[]` | per source→target hit count (`pixels: 0` = source never matched) |
| `unused_sources[]` | the subset of substitutions with `pixels == 0` (typos in the map) |
| `passthrough_pixels` / `passthrough_color_count` | opaque colours the map did not cover — left unchanged, but **named and counted** |
| `passthrough_colors[]` | top colours by frequency (capped at 64; over-cap is announced as `passthrough_colors_truncated`, never silent) |

A colour outside the map is never quietly left behind without a name. The
curation view prints these numbers from the report — it does not re-count
pixels. Reader: `sprite_gen.recolor.load_report(run_dir)`.

- No report file → `None` ("this run has no variants").
- A file at the report path with a foreign `kind` → fail loud. Reading it as
  "no variants" would hide a bake that wrote somewhere unexpected.

## Curation webview — colourway compare and pick

After a bake into `<run-dir>/variants/`, open the ordinary curation view:

```bash
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen curation --run-dir <run-dir>
```

The server attaches a `recolor` block to `/api/run` (`serve_curation._recolor_info`).
The view (`curator/src/recolor.js`) shows:

1. **One stacked stage** — all variant sheets occupy the same place; hovering a
   list row swaps the visible sheet in place (blink comparison). Side-by-side
   eye travel loses small palette shifts; blink does not.
2. **Frame-cell thumbnails** — one cell cropped from the sheet, not the whole
   atlas. The crop box comes from `manifest.frame_layout` (first frame of the
   first state). Origin assumptions are forbidden; no layout → no crop style.
3. **Adopt button** — writes the chosen variant **name** into the curation
   sidecar.

### Adopted pick SSoT

```json
{
  "version": 1,
  "kind": "sprite-gen-curation",
  "recolor": { "picked": "moss" }
}
```

- Keyed by **variant name**, not a frame index. A colourway is a colour
  decision, not a frame generation — re-baking does not stamp it stale the way
  anchors do. Reader: `curation.recolor_pick(curation)`.
- A name that the current bake no longer produces is **kept** and reported as
  `pickedKnown: false` (banner in the view). It is never silently cleared.
- A save from a run that has no recolor section does not author the field; the
  server carries the existing pick over (same contract as `anchors`). An
  unrelated edit cannot erase an adopted colourway.
- File tree shows a `variants/` folder with an adopt badge on the picked name.

Schema detail and the rest of the sidecar live in [`curation.md`](curation.md).

## Typical workflow

```bash
# 1. Compose the base atlas first (or start from any finished sheet).
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python \
  $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/compose_sprite_atlas.py --run-dir <run>

# 2. Draft the palette from the base sheet.
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen recolor-palette \
  --base <run>/sprite-sheet-alpha.png \
  --out <run>/palette.draft.json

# 3. Author the recolor spec (edit the draft hex list into source→target maps).
#    Save as e.g. <run>/recolor.spec.json with kind "sprite-gen-recolor".

# 4. Bake every colourway in one command.
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen recolor \
  --run-dir <run> \
  --spec <run>/recolor.spec.json

# 5. Compare and adopt in the curation view.
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen curation --run-dir <run>
```

Ship the adopted sheet from `<run>/variants/<picked>.png` (and its sibling
manifest if one was propagated). The base atlas stays the geometry SSoT.

## What this is not

- Not a runtime filter. The game loads a baked PNG.
- Not a second atlas composer. Geometry, cell size, and `frame_layout` come
  from the base; recolor only rewrites colours.
- Not a silent repair tool. Unused sources and passthrough colours are always
  in the report — fix the map, do not paper over a miss.

## Related

- [`../SKILL.md`](../SKILL.md) — hub, trigger routing, script map
- [`curation.md`](curation.md) — webview + `curation.json` schema (`recolor.picked`)
- [`run-contract.md`](run-contract.md) — run-dir folder tree (`variants/`)
- `tests/test_recolor_bake.py` — determinism, exact/tolerance, report contract
- `tests/test_recolor_curation_view.py` — view payload, pick round-trip, fail-loud foreign report
- `tests/test_recolor_cli_entrypoint.py` — CLI identity across entry forms
- `tests/test_recolor_docs.py` — doc surface lock (hub + leaf teach the same commands)
