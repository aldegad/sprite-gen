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
| Extract | `extract_sprite_row_frames.py` | `raw/<state>.png` | on success: `frames/<state>/frame-N.png` (+ `.plain.png` twin on pixel-perfect runs), `frames/frames-manifest.json`; on failure: nothing in `frames/`, `extract-failure.json` instead (§6) |
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
  # ── 파일 택소노미 (layout: taxonomy/v1 — 신규 런 기본, 수홍 확정 2026-07-14) ──
  # 방향 계약(directions) 런은 위 두 경로가 방향/자세 폴더로 나뉜다. 상태 ID 는 그대로
  # <direction>_<pose>, 파일 경로만 분리. 리졸버 SSoT = sprite_gen/layout.py.
  # 이미 추출된 프레임을 읽는 소비자는 frames-manifest 의 row.files 경로를 따른다
  # (row_frame_rel) — 패턴 조립 금지. layout 필드 없는 legacy 런은 flat 유지.
  # 프로젝트별 정비 방식이 지침으로 오면 지침이 우선한다.
  raw/<direction>/<pose>.png                 # 택소노미: 생성 스트립
  frames/<direction>/<pose>/frame-N.png      # 택소노미: 추출 프레임 (+ .plain / orig/)
  references/layout-guides/<direction>/<pose>.png
  prompts/<direction>/<pose>.txt
  frames/<state>/frame-N.plain.png   # pixel-perfect runs only: cell-sized pre-pixel-perfect twin, baked by compose on pixel_perfect:false (§3)
  frames/<state>/orig/frame-N.png    # pixel-perfect runs only: hi-res original twin (display-only), drives the pp-off toggle at original quality (§3)
  frames/frames-manifest.json        # per-row extract report (files, labels, ok) — only ever a COMPLETE ok generation (§6)
  extract-failure.json               # while any state's extract is unresolved: per-state ok:false diagnostics, OUTSIDE frames/ (§6); merged per state, removed once all resolved
  curation.json                      # optional, non-destructive sidecar (selected/order/transforms/pixel_perfect)
  unpack-source.json                 # import runs only (unpack_atlas_run.py): provenance — atlas/pngs source, base_source, imported_refs
  sprite-sheet-alpha.png             # composed runtime atlas
  sprite-sheet-alpha.report.json     # compose report
  manifest.json                      # runtime SSoT: frame_layout absolute rects
  sprite-inspect.report.json         # inspect_sprite_run.py output (per-state health rows)
  sprite-score.report.json           # score_sprite_run.py output (overall score + correction hints)
  correction-loop/                   # run_correction_loop.py: attempt-N/ (inspect/score/hints) + candidate-N/ regenerated run dirs
  qa/<state>-contact.png             # QA contact sheet
  qa/<state>.gif                     # QA state GIF
  qa/<name>.gif, qa/<name>-contact.png, qa/<name>.json   # compose_selected_cycle.py: named selected-frame cycle + its manifest
  qa-notes.md                        # per-state motion verdict + reference-plan notes
  curated/                           # only when export_curated_pngs.py runs
  exports/                           # only when compose_sprite_gif.py --run-dir runs (per-state GIF + gif-manifest.json)
  .sprite-gen.lock                   # single-writer lock (runio.py); a live holder blocks a second writer (§7 guarantee boundary)
  .frames.sg-staging/                # transient: extract builds the new generation here, swapped into frames/ under publish_guard
```
Transient publish sidecars exist only during an extract/import commit: `.frames.sg-backup`
and `.extract-failure.sg-backup` (in-place rollback copies) and `.<name>.sg-rwlock` (in the
parent dir — the publish reader/writer lock). Their durability/concurrency boundary is §7.

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
- **`frames/<state>/frame-N.plain.png`** (pixel-perfect runs only) is the *cell-sized*
  pre-fit twin that `compose` bakes when the sidecar turns pixel-perfect off for that
  state (`states.<state>.pixel_perfect:false`, or the run-wide `pixel_perfect:false`
  default — resolver: `curation.frame_variant(curation, state)`) — the
  atlas slot is cell-sized, so this twin must be too. **`frames/<state>/orig/frame-N.png`**
  is the *hi-res* (S×cell, capped) pre-fit twin the view displays when the user turns
  pixel-perfect off, so "off = original" is crisp instead of an upscaled cell blur. The
  view prefers `orig/`, falling back to `.plain.png` when no hi-res twin exists. Both
  twins are fitted into the pixel-perfect frame's content bbox (same footprint), so the
  toggle compares pixel treatment at identical size and a plain bake keeps the same
  character size as pixel rows.
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
| **Pixel grid** | grid is known or measurable | `states[].pixelScale` + `pixelPerfect{label,scale}` + `states[].frames[].contentBox` | **Per-state** checkbox on each grid-capable row's refs strip (right side, above the frames); the top checkbox is a **toggle-all** (indeterminate when mixed). Display only, never persisted. On the pixel-perfect view: the output raster (request scale on `fit.pixel_perfect` runs, per-row measured pitch labelled `auto` on import runs). On the original (plain) view: the **final correspondence grid** — the final pixel content box divided into exactly the final logical pixel count, drawn green and bounded to the sprite, so one green cell = one result pixel. (The stage-1 cut lattice recorded in `frames-manifest input_grids` is NOT this grid — the post-cut conform shrink can merge cells — and stays diagnostic-only.) A row where nothing is known draws **no grid** — no fake grid. |
| **Direction groups** | request has a `directions` block | `directionGroups[]` — `{direction, anchor, states}` + mirror entries `{direction, mirrorOf}` | States render grouped per direction with the direction anchor first (badge `방향 앵커`); mirrored directions render as an informational strip (`<src> 런타임 미러 — 생성 없음`), never as silently missing rows. Runs without the block keep the flat request order. |
| **Original-quality toggle** | any frame has a pre-fit twin | `states[].frames[].plainUrl` + `fitPixelPerfect` | **Per-state** checkbox on each twin-bearing row's refs strip (right side, above the frames; on = canonical pixel-perfect `frame-N.png`; off = `plainUrl` — the hi-res `orig/frame-N.png` when present, else the cell-sized `.plain.png`; off also hides that row's pixel grid). The top-right checkbox is a **toggle-all** over the same per-state truth (indeterminate when rows are mixed). Absent twins hide both the row checkbox and the top one. |

`GET /api/run` payload — the display-relevant subset below (the full snapshot,
including non-display fields like `states[].action`, is assembled by
`build_run_state`):

```jsonc
{
  "characterId": "howl",
  "runDir": "<abs path>",
  "baseUrl": "/run/base-source.png",        // base reference row; null when no base-source.*
  "cell": { "width": 256, "height": 256 },
  "pixelPerfect": { "logicalHeight": 48, "scale": 5, "source": "request", "label": "48px" },
                                            // or { "source": "auto", "label": "auto", "scale": null }; null when no grid anywhere
  "fitPixelPerfect": true,                   // request opted into the deterministic pixel-perfect path
  "runRevision": "9f3c1a0b7e2d4c58",         // frame-content fingerprint of this generation; POST /api/curation echoes it (stale ⇒ 409)
  "hasAtlas": true,
  "iso": null,                               // sibling meta.json iso tile/anchor → ground-grid overlay
  "lang": "ko",
  "schemaVersion": 1,
  "states": [
    {
      "name": "down_walk",
      "pixelScale": 5,                       // request scale, or auto-measured pitch, or null (no grid)
      "refs": [                              // generation-material chips
        { "role": "anchor", "name": "down_idle.png", "url": "/run/raw/down_idle.png" },
        { "role": "guide",  "name": "down_walk.png", "url": "/run/references/layout-guides/down_walk.png" }
      ],
      "fps": 8, "loop": true, "requestFrames": 6, "extractOk": true,
      "frames": [
        { "index": 0, "url": "/frames/down_walk/frame-0.png",
          "plainUrl": "/frames/down_walk/orig/frame-0.png",    // hi-res orig/ twin (else cell-sized .plain.png); present ⇒ toggle available
          "present": true, "label": "step-1",
          "size": [256, 256], "contentSize": [120, 210] }      // contentSize = alpha bbox, for size-parity review
      ]
    }
  ],
  "contract": { "base": true, "refs": true, "refsStates": 1, "grid": true, "sourceless": false },
                                            // self-report (§3): sourceless=true when base+refs+grid all absent → server warns at startup
  "curation": { /* current sidecar snapshot, or empty */ }
}
```

`pixelPerfect.scale` is `cell.height // fit.logical_height` (integer floor), so the
example's `256 // 48 = 5` (not 5.33 — floor); `label` is `"<logical_height>px"` and
`logicalHeight` echoes `fit.logical_height`. `states[].pixelScale` mirrors that scale
on a `fit.pixel_perfect` run, or carries the per-row auto-measured block pitch on a
run with no pixel-perfect contract (import/plain), or `null` when a row's pitch cannot
be measured. Real runs are usually smaller than this synthetic 256 example — the
founder v7 anchor is `cell 56 / logical 48 → 56 // 48 = 1`.

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
  → the row's generation-material chips. The role is the filename prefix and **must** be
  `anchor` / `basis` / `guide` (same vocabulary as §3, owned by `curation.IMPORTED_REF_ROLES`).
  A `_refs` file with any other prefix is malformed input: the importer **fails loud**
  (lists the offending files) — it never silently relabels an unknown role as `guide`.

`serve_curation.py`'s `_state_refs` resolves chips from `raw/` anchors for real runs
and from `references/imported/<group>/` for imported runs — one chip vocabulary, two
sources, identical rendering. A `_base`/`_refs`-free import still works (frames only,
no base row, no chips), but then the view honestly shows "no source material" rather
than a divergent experience.

**`--force` re-import is a writer-isolated, rollback-safe, reader-atomic rebuild.** It
validates all inputs, builds the new run into a staging dir, then publishes it over the
run dir. A **successful** re-import reflects **only** the current input — removing a
`_base`/`_refs` source and re-importing leaves no stale `base-source.png` /
`references/imported/*` behind, so provenance (`unpack-source.json`) and the served view
never disagree (Idempotency/SSoT: the result never depends on the prior out-dir state).
A **failed or invalid** re-import (e.g. a bad `_refs` role) leaves the prior run
**byte-intact** — it is never cleared-then-failed (write-side Atomicity: a rebuild fully
succeeds or rolls back). The publish holds the run-dir single-writer lock in place
throughout, so a concurrent **writer** cannot preempt (writer Isolation).

> **Reader isolation (named strategy: reader/writer locking).** Every operation that
> republishes frames — a `--force` re-import **and** a re-extract (`extract` builds into a
> staging dir, then swaps `frames/` into place) — holds the run dir's *exclusive* publish
> lock (a sidecar `.<name>.sg-rwlock` `flock`, `runio.publish_guard`) around the swap, while
> `serve_curation` reads (`/api/run` **and** run-dir file requests) under the matching
> *shared* read lock (`runio.read_guard`). So a webview serving that same run dir
> concurrently always sees a **complete old-or-new snapshot** —
> never a half-published (old/new-mixed or missing-file) state, and never a transient
> `HTTP 500`. The reader blocks only for the brief swap. The rwlock is a sidecar (beside
> the run dir), so it survives content swaps and is never itself published. Where `fcntl`
> is unavailable or the sidecar can't be created, the guard **fails loud** (raises
> `runio.RWLockUnavailable`) rather than degrading to a no-op: a no-op would be a failover
> that lets canonical truth change inside a read transaction (a reader could observe a
> half-published run), and the isolation contract permits an availability failover only
> when it stays observable **and does not change canonical truth**. The pipeline runs on
> platforms with `fcntl` advisory locks (macOS/Linux); a platform without them refuses the
> publish/read rather than silently serving a partial run.
>
> The curation **write** (`POST /api/curation`) takes the same exclusive publish lock, so
> a `select`/`reorder`/`transform` autosave is serialized with a concurrent re-import; and
> the POST must echo the `runRevision` (a frame-content fingerprint) it was loaded with —
> a POST from a different run generation is **rejected** (`HTTP 409`). So a stale autosave
> from a webview still on a pre-re-import (or pre-re-extract) run can never apply old
> selections/transforms to new frames, **even when the re-import keeps the same state
> names** but swaps the candidate images (Consistency — run identity, not just state
> membership).

## 5. Conformance status

All four contracts are enforced by the shipped scripts today:

- Stage table (§1), folder tree (§2), and the base-row / chips / grid elements of §3.
- The "off = original quality" toggle (§3): extract writes a hi-res `orig/` twin the
  view prefers, so pp-off is crisp; the cell-sized `.plain.png` stays for the `compose`
  bake. Canonical `frame-N.png` bytes are unchanged (deterministic — verified
  byte-identical on re-extraction of founder v6/v7).
- The `--pngs-dir` `_base`/`_refs` embedding (§4): imported runs reach the same base
  row + chips as real runs.
- The view-contract self-report (§3): serve_curation logs base/refs/grid coverage at
  startup and warns on a sourceless view.

Verified end-to-end on three views — founder v6 (`base=yes refs=12/12 grid=yes`),
founder v7 (`refs=36/36`, anchor/basis/guide chips across down/side/up), and a
comprehensive `_base`+`_refs` import — plus a sourceless run that emits the warning.

## 6. Failed extract is atomic — no partial generation in `frames/`

Canonical `frames/` only ever holds a **complete** generation. A failed extract — the
**first** extract on a fresh run *or* a re-extract — publishes **nothing** to `frames/`
(strict whole-generation Atomicity: the operation fully succeeds or leaves canonical state
untouched). A failed re-extract additionally leaves the prior complete generation
byte-intact, and the frames publish stays reader-atomic under `publish_guard` (§4).

The per-state failure signal is **not** discarded — it is written **outside** `frames/` as
`extract-failure.json` in the run-dir root, so it stays observable (No Silent Fallback) and
still drives the automatic correction loop:

- `extract-failure.json` holds the **union of the run's currently-unresolved per-state
  failures** — `ok:false`, per-state `errors`/`warnings` — and the CLI exits `1`.
- The **automatic correction loop** consumes it: `inspect._manifest_state_notes` reads the
  per-state `errors` from `extract-failure.json` (alongside the published
  `frames/frames-manifest.json` row) to drive inspect → score → correction-hint →
  regeneration. So error-driven regeneration keeps its *which state failed, and why* signal
  even though `frames/` holds only complete generations.
- The evidence is maintained **per state**, because a subset `--states` extract (the formal
  single-row / auto-correction path) only determines the outcome of the states it targets: a
  target state that succeeds is removed, a target state that fails is (re)recorded, and states
  the attempt did not touch keep their prior failure. So a `walk` success never erases an
  unresolved `idle` failure, and an `idle` failure never overwrites a `walk` one. The file is
  deleted only once **no** per-state failure remains (Consistency — never re-flag a now-good
  state, never silently drop a still-broken one).
- Advancing the two canonical surfaces is **one transaction**: `extract` swaps `frames/`
  and (re)writes/removes `extract-failure.json` under a single `publish_guard`, rolling
  **both** back together on any **in-process** I/O failure (a raised exception), so a reader
  never sees a new generation beside a stale failure record. Readers that combine them
  (`inspect` / the correction loop, via `inspect_run`) hold the matching `read_guard` for the
  whole read. (This rollback covers raised exceptions; a hard **process kill** mid-swap is a
  known durability gap — see the guarantee boundary in §7.)
- A **complete generation** is enforced beyond JSON schema: a finished-generation consumer
  (`compose_*` / `export_pngs` / `preview`) calls `extract.require_frames_manifest`, and
  `serve_curation` / `inspect` call `extract.load_consistent_frames_manifest`, which verify the
  manifest AGREES with the physical frame tree and the request — every request state has exactly
  one row, no physical frame dir is an orphan the manifest omits, and every row's canonical
  frames exist on disk. `{"ok":true,"rows":[]}` over real frames, a deleted frame, or frames with
  **no** manifest (an orphan, distinct from a fresh scaffold) all fail loud; `compose_gif` /
  `export_pngs` likewise fail on a missing selected frame instead of silently skipping it. A run
  with no generation at all (no manifest, no frames) still serves the request/state scaffold.
- A **corrupt** canonical record fails loud on **every** path — the writer *and* every reader.
  Extract's subset-seeding + commit, `inspect._manifest_state_notes`, and every
  finished-generation consumer (`compose_atlas` / `compose_cycle` / `compose_gif` /
  `export_pngs` / `preview` via `extract.require_frames_manifest`, and `serve_curation` via
  `extract.load_frames_manifest`) go through the shared typed loaders
  (`extract.load_frames_manifest` / `load_failure_evidence`) that refuse an unreadable /
  unparseable / broken-schema record (must be a `dict`; `errors`/`warnings`/`rows` lists whose
  entries are non-empty strings / `state`-bearing objects; a frames manifest must be `ok:true`,
  failure evidence `ok:false` with non-empty `state`-scoped `errors`) rather than treating it as
  empty. Absence still returns empty (a run with no generation yet); only an existing-but-broken
  record fails loud. Silently reading a corrupt `extract-failure.json` as "no failures", or a corrupt
  `frames/frames-manifest.json` as "no prior rows" (then publishing an incomplete manifest that
  disagrees with the carried frame tree), is exactly the No-Silent-Fallback violation this
  forbids. A corrupt prior manifest fails a subset re-extract loud **before** it stages
  anything, so the prior generation stays byte-intact.
- `compose_atlas` requires a complete generation: it fails loud if
  `frames/frames-manifest.json` is absent (a failed extract published none) and refuses a
  non-`ok` manifest, so a partial never becomes an atlas.

The curation view tolerates a run with **no generation** (no `frames/`, no manifest):
`serve_curation` falls back to an empty row set and `curation.run_revision` treats the missing
manifest as an empty fingerprint, so the view shows the request/state scaffold rather than
erroring. Physical frames *without* a manifest are an orphan, not a scaffold — that fails loud
(see the complete-generation bullet above).

## 7. Guarantee boundary — atomicity & concurrency (honest scope)

To avoid claiming guarantees the code does not provide (No Silent Fallback), this is the exact
boundary of the run-dir's atomicity and concurrency guarantees. What is **in force today**:

- **In-process transaction rollback.** The `frames/` + `extract-failure.json` commit (§6) and
  the `--force` re-import publish (§4) roll back on any raised exception, leaving the prior
  generation byte-intact. `atomic_write_text` / `os.replace` make each file write torn-free.
- **Reader isolation.** A publish holds the exclusive `publish_guard` for its swap; a reader
  sees a complete old-or-new snapshot, never a mix (§4). Where advisory locks are unavailable the
  guard **fails loud** (`RWLockUnavailable`), never a silent no-op. Every finished-generation
  consumer has exactly one named isolation strategy against a concurrent extract/import publish:
  - **shared `read_guard`** wrapping the whole manifest + curation + frame read —
    `serve_curation` (`/api/run` + static), `inspect`, `preview`, run-dir `compose_gif`,
    `compose_cycle`. They block only for the brief swap, then read a complete generation.
  - **writer exclusion** via the run-dir single-writer lock (`acquire_run_dir_lock`) —
    `compose_atlas`, `export_pngs`. They serialize with the extract writer, so they never overlap
    a publish at all.
  A consumer that only calls the manifest gate once but then reads the frame tree unguarded would
  leave a TOCTOU window; the full read must be inside the strategy.
- **Cross-process single-writer.** `.sprite-gen.lock` blocks a second **process** from writing
  the same run dir; the pipeline's **one worker owns one character folder** rule (§2, SKILL.md)
  is what excludes concurrent writers on the same run dir in the first place. Same-process
  re-entry is intentionally allowed so one interpreter can run prepare → extract → compose in
  sequence.

What is **intentionally out of scope** for this local, single-user, re-runnable tool — and so
not claimed here (a decision, not a deferral: 수홍 2026-07-12):

- **Process-kill durability.** A hard `SIGKILL` *between* the commit's rename steps can leave a
  new generation beside a stale `extract-failure.json` plus an orphan `.sg-backup`. This is **not
  silent corruption**: the consistency gate (§6) catches the mixed state and fails loud, and a
  re-run fixes it (the next extract's cleanup reconciles the leftover `.sg-backup` / `.sg-staging`).
  A journal / self-heal replay protocol would auto-heal it instead, but that is database-grade
  durability that a local sprite-curation tool does not need — observable-fail-loud + re-run is
  the deliberate design. In-process rollback (above) still covers every raised-exception case.
- **Thread-level write isolation.** `.sprite-gen.lock` re-entry is keyed per process, not per
  thread. This is a **non-scenario** under the pipeline's one-worker-owns-one-folder rule (§2,
  SKILL.md): concurrent writers on the same run dir are already excluded, and same-process
  re-entry is the *intended* path for one interpreter to run prepare → extract → compose in
  sequence. A thread-aware per-run mutex would only matter for a multi-threaded server host, which
  is not this tool.

Both are deliberate boundaries, not TODOs; §4/§6's atomicity claims are scoped to the in-process
cases above and do not contradict this. If sprite-gen is ever re-homed as a multi-user or server
service, revisit both here.

## Related

- [`../SKILL.md`](../SKILL.md) — behavior contract (Workflow, Base Lock Gate, Runtime Contract)
- [`architecture.md`](architecture.md) — how the code realizes these contracts (stage internals, lock, extraction, pixel-perfect path)
- [`curation.md`](curation.md) — webview interaction model, `curation.json` schema, standalone image-candidate path, multi-agent launch rules
- [`pixel-perfect.md`](pixel-perfect.md) — `fit`/`pixel_perfect` behavior + plain-twin bake decision
- [`directional-anchor-workflow.md`](directional-anchor-workflow.md) — directional/45° anchor chains that name the `raw/` anchors §3 resolves into chips
