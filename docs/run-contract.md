# Run Contract — pipeline stages · run-dir folder tree · curation-view display (SSoT)

> Status: **contract** (normative). This doc is the single source of truth for
> three things every sprite-gen run must satisfy so that **any agent who serves a
> view gets the same experience** — the base reference row, the per-state
> generation-material chips, the pixel grid, and the original-quality toggle.
>
> Precedence (no overlap, so no contradiction):
> - [`../SKILL.md`](../SKILL.md) owns the **behavior** contract — what the agent does, step by step.
> - **This doc** owns the **structural** contract — the stage I/O table, the run-dir
>   folder tree, the curation-view display payload, and the import-run source rule.
>   These are the parts the scripts enforce.
> - [`architecture.md`](architecture.md) explains **how** the code realizes both; it is
>   always description, never contract.
>
> If the three ever disagree: behavior → SKILL.md wins; structure/display → this
> doc wins; architecture.md is the bug.

## 1. Pipeline stages

One image-gen call **per state row** is the only AI step; every stage after it is
deterministic (same input → same output). Each script does one job and reads/writes
canonical files, not hidden imports.

| Stage | Script | Input | Output |
|---|---|---|---|
| Prepare | `prepare_sprite_run.py` | base image + request flags/JSON | `sprite-request.json`, per-state layout guide, per-state prompt, empty `raw/`+`frames/` |
| Generate | `sprite-gen gen` (`generate_sprite_image.py`) | `prompts/<state>.txt` + refs | verified `raw/<state>.png` strip + audit raw/report |
| Extract | `extract_sprite_row_frames.py` | `raw/<state>.png` | `frames/<state>/frame-N.png` (+ `.plain.png` twin on pixel-perfect runs), `frames/frames-manifest.json` |
| Curate (opt) | `serve_curation.py` + `curation.py` | `frames/` | `curation.json` sidecar |
| Compose | `compose_sprite_atlas.py` | `frames/` + `curation.json` | `sprite-sheet-alpha.png`, `manifest.json`, `*.report.json` |
| QA | `preview_animation.py` | `frames/` | `qa/<state>-contact.png`, `qa/<state>.gif` |
| Inspect | `inspect_sprite_run.py` | `sprite-request.json`, `raw/` or `frames/` | `sprite-inspect.report.json` |
| Score | `score_sprite_run.py` | `sprite-inspect.report.json` | `sprite-score.report.json`, correction hints |
| Correction loop | `run_correction_loop.py` | run dir + optional provider command | `correction-loop.report.json`, per-attempt inspect/score/hints |
| GIF export | `compose_sprite_gif.py`, `gif_utils.py` | selected frames | clean transparent GIF into `exports/` |
| Selected cycle | `compose_selected_cycle.py` | `curation.json` / `--frames` | selected-cycle manifest + QA |
| Inverse / import | `unpack_atlas_run.py` | finished atlas **or** `--pngs-dir` | curator-ready run dir (§4) |
| Export stills | `export_curated_pngs.py` | curated `frames/` | named PNGs under `curated/` |
| Chroma guard | `check_visible_magenta.py` | screenshot | leakage warning |

The happy path is `prepare → gen → extract → (curate) → compose`, with a curation
webview opened as the closing step. Stage internals (chroma removal, connected
components, pixel-perfect path, the `.sprite-gen.lock` single-writer rule, the
inspect/score/loop split) are described in
[`architecture.md`](architecture.md) §2, §6 — this table is the contract, that doc
is the explanation.

## 2. Run-dir folder contract

**One worker owns exactly one character folder.** This is the canonical tree — do
not restate it elsewhere; point here.

```text
<target>/assets/generated/sprites/<character-id>/
  sprite-request.json                # numeric SSoT (cell, chroma, states, fit) — every stage reads this
  base-source.<ext>                  # identity truth; drives the view's base reference row (§3)
  references/layout-guides/<state>.png   # per-state layout guide (motion only)
  references/imported/<group>/           # imported-run generation material → chips (§4); real runs use raw/ anchors instead
  prompts/<state>.txt                # generated row prompt (frame count, safe margin, anchor lock)
  raw/<state>.png                    # one horizontal image-gen strip per state (the only AI output)
  frames/<state>/frame-N.png         # extracted transparent cells (canonical)
  frames/<state>/frame-N.plain.png   # pixel-perfect runs only: pre-pixel-perfect twin, drives the pp toggle (§3)
  frames/frames-manifest.json        # per-row extract report (files, labels, ok)
  curation.json                      # optional, non-destructive sidecar (selected/order/transforms/pixel_perfect)
  sprite-sheet-alpha.png             # composed runtime atlas
  sprite-sheet-alpha.report.json     # compose report
  manifest.json                      # runtime SSoT: frame_layout absolute rects
  qa/<state>-contact.png             # QA contact sheet
  qa/<state>.gif                     # QA state GIF
  qa-notes.md                        # per-state motion verdict + reference-plan notes
  curated/                           # only when export_curated_pngs.py runs
  exports/                           # only when compose_sprite_gif.py --run-dir runs
  .sprite-gen.lock                   # single-writer lock (runio.py); a live holder blocks a second writer
```

Rules the display depends on:

- **`base-source.*` is the identity truth** and must survive independent of baking —
  the view shows it as a top reference row (§3) whether or not it was attached to any
  row. Real runs write it in Prepare; imported runs write it from `pngs/_base/` (§4).
- **`raw/<direction>_idle.png` and `raw/down_<state>.png`** are what the view resolves
  into per-state generation-material chips for real (directional-anchor) runs — the
  chip is "which anchor / basis row / guide generated this state". See
  [`directional-anchor-workflow.md`](directional-anchor-workflow.md) for the naming.
- **`references/imported/<group>/`** is the imported-run equivalent of those raw
  anchors: an imported row carries its generation material here so the view produces
  the same chips (§4).
- **`frames/<state>/frame-N.plain.png`** (pixel-perfect runs only) is the pre-fit
  twin the view toggles to when the user turns pixel-perfect off — "off = original".
  Sidecar-baking semantics are owned by [`pixel-perfect.md`](pixel-perfect.md); the
  display contract for the toggle is §3.

The runtime `manifest.json.frame_layout` contract (absolute rects, no runtime
alpha-recovery, `degraded_static_fallback: false`) is owned by
[`../SKILL.md`](../SKILL.md) "Runtime Contract" and is out of scope here.

## 3. Curation-view display contract

`serve_curation.py` serves one run dir and returns the run snapshot at `GET /api/run`.
The webview (`scripts/curator/*`) renders exactly four contract elements from that
payload. **A view that omits any element it has the data for is a broken view** — the
whole point is that the experience does not vary by who launched it.

| Element | Shown when | Payload source | Rule |
|---|---|---|---|
| **Base reference row** | `base-source.*` exists | `baseUrl` (null if absent) | Top row, pure image — no preview/select UI. Identity truth, always visible. |
| **Generation-material chips** | the state has resolvable material | `states[].refs[]` — each `{role, name, url}` | Per-state header shows *what generated this row*. `role ∈ {anchor, basis, guide}`, labelled `방향 앵커` / `basis row` / `레이아웃 가이드` (i18n key `ref_<role>`). Only run-dir files that actually exist appear. |
| **Pixel grid** | grid is known or measurable | `states[].pixelScale` + `pixelPerfect{label,scale}` | Snap grid at the logical pixel size. `fit.pixel_perfect` runs → request scale, label `48px`-style. Import/plain runs → per-row auto-measured block pitch, label `auto`. A row where the pitch cannot be measured draws **no grid** — no fake grid. |
| **Original-quality toggle** | any frame has a `.plain.png` twin | `states[].frames[].plainUrl` + `fitPixelPerfect` | Top-right checkbox. Checked = canonical pixel-perfect `frame-N.png`; unchecked = the pre-fit `plainUrl` twin ("off = original"). Absent twins hide the toggle. |

`GET /api/run` payload — the display-relevant fields (full snapshot in
`build_run_state`):

```jsonc
{
  "characterId": "howl",
  "runDir": "<abs path>",
  "baseUrl": "/run/base-source.png",        // base reference row; null when no base-source.*
  "cell": { "width": 256, "height": 256 },
  "pixelPerfect": { "label": "48px", "scale": 4, "source": "request" },
                                            // or { "source": "auto", "label": "auto", "scale": null }; null when no grid anywhere
  "fitPixelPerfect": true,                   // request opted into the deterministic pixel-perfect path
  "hasAtlas": true,
  "iso": null,                               // sibling meta.json iso tile/anchor → ground-grid overlay
  "lang": "ko",
  "schemaVersion": 3,
  "states": [
    {
      "name": "down_walk",
      "pixelScale": 4,                       // request scale, or auto-measured pitch, or null (no grid)
      "refs": [                              // generation-material chips
        { "role": "anchor", "name": "down_idle.png", "url": "/run/raw/down_idle.png" },
        { "role": "guide",  "name": "down_walk.png", "url": "/run/references/layout-guides/down_walk.png" }
      ],
      "fps": 8, "loop": true, "requestFrames": 6, "extractOk": true,
      "frames": [
        { "index": 0, "url": "/frames/down_walk/frame-0.png",
          "plainUrl": "/frames/down_walk/frame-0.plain.png",   // present ⇒ toggle available
          "present": true, "label": "step-1",
          "size": [256, 256], "contentSize": [120, 210] }      // contentSize = alpha bbox, for size-parity review
      ]
    }
  ],
  "curation": { /* current sidecar snapshot, or empty */ }
}
```

Display invariants (enforced by the server, not by the launching agent):

- The base row, chips, grid, and toggle are all **derived from run-dir files** — an
  agent cannot "forget" to set them up. If a source file is missing the element is
  simply absent; there is no per-agent styling step to get wrong.
- `contentSize` (alpha bbox) is exposed so a reviewer can spot size-parity drift
  across a row without opening each frame.
- Standalone image-candidate curation (icons / logos / drafts — not sprites) and the
  webview interaction model (select/reorder/transform, `curation.json` schema,
  multi-agent launch rules) live in [`curation.md`](curation.md); this section owns
  only the four display-contract elements above.

## 4. Import-run source rule (`--pngs-dir`)

An imported run (a folder of separate PNGs, no generation pipeline) must reach the
**same** display contract as a real run: a base reference row and per-state
generation-material chips. The importer treats source material as first-class, not
just frames.

Import folder layout accepted by `unpack_atlas_run.py --pngs-dir <dir>`:

```text
pngs/
  _base/<any>.png            # optional — identity/base image for the whole set
  <group-a>/                 # one subfolder = one curator row (state)
    1-name.png               # frames; numeric prefix sets play order
    2-name.png
    _refs/                   # optional — this row's generation material
      anchor-<name>.png      #   role = direction anchor
      basis-<name>.png       #   role = basis row
      guide-<name>.png       #   role = layout guide
  <group-b>/ ...
  meta.json                  # optional — human labels + iso tile/anchor (§3 grid overlay)
```

Mapping into the run dir (so both view code paths resolve identically):

- `pngs/_base/<img>` → `base-source.png` → drives the base reference row (`baseUrl`).
- `pngs/<group>/<frames>.png` → `frames/<group>/frame-N.png` → the row's frames.
- `pngs/<group>/_refs/<role>-<name>.png` → `references/imported/<group>/<role>-<name>.png`
  → the row's generation-material chips, role parsed from the filename prefix
  (`anchor` / `basis` / `guide`, same vocabulary as §3).

`serve_curation.py`'s `_state_refs` resolves chips from `raw/` anchors for real runs
and from `references/imported/<group>/` for imported runs — one chip vocabulary, two
sources, identical rendering. A `_base`/`_refs`-free import still works (frames only,
no base row, no chips), but then the view honestly shows "no source material" rather
than a divergent experience.

## 5. Conformance status

The stage table (§1), folder tree (§2), and the base-row / chips / grid / toggle
elements of §3 are enforced by the shipped scripts today. The `--pngs-dir` `_base`/
`_refs` embedding (§4) and the "off = original quality" fidelity of the plain twin
(§3, the twin is currently written at cell resolution) are being wired by the active
**pipeline-run-contract-standardization** plan; this doc is the target they conform to.
Do not treat an unwired part as done — check the plan, not this sentence, for status.

## Related

- [`../SKILL.md`](../SKILL.md) — behavior contract (Workflow, Base Lock Gate, Runtime Contract)
- [`architecture.md`](architecture.md) — how the code realizes these contracts (stage internals, lock, extraction, pixel-perfect path)
- [`curation.md`](curation.md) — webview interaction model, `curation.json` schema, standalone image-candidate path, multi-agent launch rules
- [`pixel-perfect.md`](pixel-perfect.md) — `fit`/`pixel_perfect` behavior + plain-twin bake decision
- [`directional-anchor-workflow.md`](directional-anchor-workflow.md) — directional/45° anchor chains that name the `raw/` anchors §3 resolves into chips
