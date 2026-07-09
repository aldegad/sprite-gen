# Changelog

All notable changes to `sprite-gen` are recorded here. Versions track the `version:` field in `SKILL.md`.

## v1.13.0 — Chroma peel removal, key-depth unmix, safer auto key sampling

This release removes the legacy fringe erase peel that became harmful after v1.12.0's soft-alpha unmix. The peel ran before unmix, deleted the original 1.1-1.3 px antialias band wholesale, turned silhouettes into binary stair-steps, and could erode thin outlines or hair strands by 1-2 px. Soft-alpha unmix now owns chroma boundary cleanup.

- **Fringe erase peel removed completely.** `--fringe-reach` and the `remove_chroma_background(..., fringe_reach=...)` parameter are gone; old CLI use fails loudly with `unrecognized arguments` instead of silently re-enabling a destructive path.
- **In-band unmix guard changed from subject-neighbor to key-depth.** In-band key blends are unmixed only when their distance from the keyed region is `<= 2`; out-of-band blends still use `--fringe-unmix-reach` (default 4). This fixes blend pockets whose inner pixels had no untinted `_SUBJECT` 8-neighbor and previously survived as `alpha=255` warm grey/brown crust.
- **`--fringe-unmix-reach 0` now disables all chroma boundary cleanup beyond the hard key cut.** Before the peel removal, `unmix_reach=0` still allowed the independent fringe erase peel to remove boundary fringe; in v1.13.0, the peel is gone, so `0` means no soft-alpha unmix and no peel cleanup. On the downscaled fixture `tests/fixtures/moe/moe_heart.png`, measured mid-alpha is `204` at reach 4, `196` at reach 2, and `0` at reach 0.
- **Optional unmix tunables are keyword-only.** `unmix_reach` and `spill_max_fraction` can no longer be passed as ambiguous positional arguments, preventing old six-argument calls from silently changing meaning after `fringe_reach` was removed.
- **Package metadata version synchronized.** `pyproject.toml` now mirrors `SKILL.md` at `1.13.0`; future releases must update both fields in the same release commit because editable installs and CI consume `pyproject.toml`, while the changelog treats `SKILL.md` as the release SSoT.
- **`--chroma-key auto` no longer counts opaque chroma background as subject** (commit `83b269b`). Candidate scoring excludes the detected flat background, records candidate `score` / `min_subject_distance` / `clears_erase_radius` / `background` metadata, and keeps the nearest-subject erase-radius guard.
- **Measured cleanup impact** (full-resolution raw inputs under `assets/chroma-repro/`):
  - moe-heart raw, magenta key: mid-alpha `5,391 -> 11,621`; `6,274` former peel-band pixels, `alpha=255` residue `0`.
  - moe-mirror raw, green key: mid-alpha `1,971 -> 6,835`; `4,877` former peel-band pixels, `alpha=255` residue `0`.
  - accident fixtures: opaque subject pixels improve from herb `4,254 -> 4,415`, seed `4,501 -> 4,513`.
  - pixel-art after `binarize_alpha`: pixel-heart silhouette `+115 px`, pixel-mirror `+1,781 px`, discarded pixels `0`; recovered pixels are key-distant black outline colors on average `(35,26,19)` / `(7,9,11)`.
- **Tests and fixtures**: `tests/fixtures/moe/moe_heart.png` and `tests/fixtures/moe/moe_mirror.png` pin the green/magenta mirror cases; `tests/test_chroma_extraction.py` pins former peel-band soft alpha, heart material byte-identity, accident opaque baselines, and keyword-only fail-loud behavior; `tests/test_chroma_key_auto.py` covers the auto-key background exclusion.

## v1.12.0 — Soft-alpha chroma edges, trapped-spill despill, fit CLI parity

Non-pixel-target extraction produced binary alpha (0 or 255) — antialiasing died at the hard key cut, so every silhouette read as a staircase, and key-colored blend pixels trapped between hair strands survived as opaque magenta/green residue (351/339 px on the two repro runs). Extraction alpha cleanup in `extract_sprite_row_frames.py` is now a four-pass chain; the v1.10.1 guarantee (key-tinted subject interiors like hot-pink packets and purple crystals survive byte-identical) is regression-tested and unchanged.

- **Pass 1 — hard key cut** (unchanged): pixels within `--key-threshold` of the key erased, alpha=0 RGB cleared to `(0,0,0)`.
- **Pass 2 — fringe erase peel** (unchanged v1.10.1 semantics): in-band key-tinted pixels chain-adjacent to the keyed region erased, at most `--fringe-reach` layers. Erase set is byte-identical to v1.10.1, so the accident-fixture protections hold.
- **Pass 3 — soft-alpha unmix** (new): key-tinted blends the erase pass cannot represent — out-of-band boundary blends, and in-band specks touching untinted subject — within `--fringe-unmix-reach` (default 4) of the keyed region are solved against the blend model `observed = (1-k)·subject + k·key` and rewritten as despilled RGB + **partial alpha**. Silhouettes keep their antialiased coverage ramp; blend pockets inside interior holes (between hair strands) stop surviving as opaque residue. Measured on the repro runs: mid-alpha 0 → 2,092/2,263 px.
- **Pass 4 — trapped-spill despill** (new): small connected key-tinted clusters buried deep inside the subject (generator spill drawn into the hair, unreachable by any bounded peel) are detected by cluster size (≤ `--spill-max-fraction` of the subject, default 0.005, floor 32 px) plus one strongly tinted pixel (tint > 40), and color-corrected in place with alpha kept — no pinholes. Large key-tinted regions (real material, 7–20× above the threshold on the accident fixtures) and marginally warm skin tones never qualify. Key-tint residue on the repro runs: 351/339 → **0**.
- **Request JSON SSoT**: the extractor reads `chroma.unmix_reach` / `chroma.spill_max_fraction` from `sprite-request.json`, CLI flags override, and effective values are written back. Pixel-perfect path unaffected (downstream α≥128 binarize).
- **`prepare_sprite_run.py` fit CLI parity** (docs promised these since v1.10.0): `--fit-resample` gains `kcentroid`, `--fit-align-x` gains `foot-centroid`, and the `pixel_perfect` family is exposed — `--fit-pixel-perfect`, `--fit-logical-height`, `--fit-palette-size`, `--fit-detail-bias`, `--fit-outline {on,off,STRENGTH}`, `--fit-pitch-hint`. CLI overrides merge over `--request` JSON and the merged `fit` object is recorded in the run's `sprite-request.json`.
- **Tests**: new `tests/test_chroma_soft_alpha.py` on 1/8-NEAREST repro fixtures (`tests/fixtures/moe/`) pins boundary partial alpha, zero key-tint residue, deep-interior byte-identity, and the trapped-spill pass; `tests/test_pipeline_smoke.py` pins fit-CLI-to-request recording. The extraction golden manifest needed no update: the synthetic fixture strips contain no key-blend pixels, so passes 3–4 are no-ops there and the manifest matches bit-for-bit.

## v1.11.0 — SKILL.md becomes a thin hub; scenario detail moves to a `docs/` leaf network

Docs-only topology split (no script changes). A 589-line SKILL.md front-loaded every scenario's detail into every session; it is now a 299-line hub — BLOCKING gates, workflow commands, contract summaries, and one-click links into leaf docs read only when their scenario comes up. The split is lossless: every rule, number, and prohibition lives in exactly one place, hub or leaf.

- **SKILL.md hub (589 → 299 lines, `version: 1.11.0`).** Keeps verbatim the mandatory raw→deterministic gate, the Base Lock Gate criteria, the Motion Continuity BLOCKING declaration, the prompt/output/runtime contracts, and the step 0–5 command blocks. Adds a `## Docs Topology` section listing each leaf with a one-line "read when" trigger. The only deletion is the License And Attribution section (duplicated in README).
- **Five new leaf docs** carved out of the hub: `docs/pixel-perfect.md` (the `fit` object, `pixel_perfect` mode, stage ownership, role contract), `docs/states-and-frames.md` (MVP state scope, quick-path request, frame-count guidance), `docs/curation.md` (standalone curation-view recipe, webview usage, finished-sheet editing, multi-agent launch rules, `curation.json` schema), `docs/chroma-alpha.md` (key-selection branching, `auto` scoring, extraction internals, slot fallback), `docs/qa-motion.md` (full motion-continuity judgment criteria).
- **`reference/` folder retired**: `directional-anchor-workflow.md` and `locomotion-curation.md` moved under `docs/` (content unchanged, internal links updated).
- **`docs/architecture.md` refreshed to v1.10.x reality**: absorbs the base-frame ownership ASCII flow from the hub, documents the pixel-perfect fit path against the actual `extract_sprite_row_frames.py` call order, and replaces the retired HTML/PNG diagrams with embedded mermaid.
- **Retired files deleted**: `docs/architecture-diagram{,.ko}.{html,png}` (4 files, ~1.6 MB of hand-maintained diagrams superseded by mermaid) and `docs/skill-improvement-plan.md` (stale 2026-06-02 draft, absorbed by v1.10.x).
- **README** swaps the PNG diagram embed for a GitHub-native mermaid pipeline block plus a `docs/architecture.md` link; all five translated READMEs (ko/ja/es/fr/zh-Hans) regenerated from the English source.

## v1.10.2 — Dependency source declared for `image-gen`

Docs-only. A fresh installer could not resolve the `kuma:image-gen` dependency from its name alone.

- **`SKILL.md` `depends_on.required_skills`** now declares the public source alongside the name: `name: kuma:image-gen`, `source: github:aldegad/image-gen`.
- **README `## Install`** gains a "Required skill dependency" subsection with the `install-skill-from-github.py --repo aldegad/image-gen` command; all five translated READMEs (ko/ja/es/fr/zh-Hans) regenerated from the English source.

## v1.10.1 — Boundary-limited fringe cut (key-tinted subjects survive) + mandatory raw→deterministic gate

Fixes the third way extraction destroyed real subject colors, and hardens the skill contract that a prior worker shortcut violated.

- **The fringe tint-gate no longer erases key-tinted subject material.** `remove_chroma_background` cut every pixel with `distance <= 180` and `key_tint_score >= 18` *anywhere in the image*, so hot pink (~129 from magenta) and purple (~153) subjects fell inside the band and were bleached wholesale — real accidents: solvell `seed_flower_pink` (hot-pink seed packet rendered three times as a white flower) and `herb_plant_star_bloom` (purple star bloom turned white), both on a magenta key, 2026-07-07. Fringe is boundary antialiasing by definition, so the cut is now limited to pixels spatially adjacent to the keyed-out background, peeled at most `--fringe-reach` layers (default 2). Measured on the accident raws: 98.7% / 98.9% of the key-tinted subject pixels survive (previously 0%), while a green-subject control (`herb_plant_wind_leaf`) removes the exact same 2,582 fringe pixels as before — no quality regression.
- **Regression tests** in `tests/test_chroma_extraction.py`: boundary fringe still removed, isolated fringe-band colors treated as subject, hot-pink/purple interiors survive, and 1/8-size NEAREST copies of both accident raws (`tests/fixtures/accident/`) must keep >= 90% of their fringe-band subject pixels.
- **SKILL.md leads with a BLOCKING gate**: AI touches raw generation only; the final asset must go through the deterministic extraction transform — a plain `PIL.resize()` downscale shortcut is a failed result (the shortcut a worker actually took on 2026-07-07, degrading edges). Chroma key selection by subject color (pink/purple → green, green plants → magenta) is part of the gate; the branch-table SSoT lives in image-gen's SKILL.md top gate.
- **History moved out of the SKILL.md body** (per skill-hook-authoring): dated redesign narratives now live here. For the record — the pixel-pitch detector replaced a run-length-mode approach that antialiased 2px runs dominated; per-frame phase snapping replaced a strip-global grid that always let some frames slide (inter-frame phase drift); the `logical_height` default changed from half the usable height (which mushed a protagonist to ~logical 30) to cell-height 1:1 (2026-07-05); the style-contract prose ("compact chibi / chunky / thick outline") that kept polluting a slim base was removed in favor of reference-image style SSoT; a 64px-locked anchor re-input erased eyes while the raw anchor preserved them (double-degradation proof, 2026-07-05).

## v1.10.0 — Pixel-perfect row pipeline (`fit`), auto-outline, light-theme curator

Game-ready pixel-art output for animation rows, built while shipping the Sol Valley protagonist (Godot 4, 64px cells). The headline: extraction no longer treats each frame as an independent image — pixel-perfect is a *row* methodology.

- **New `fit` object in `sprite-request.json`** (opt-in; absent = legacy behavior), exposed via `prepare_sprite_run.py --fit-*`:
  - `resample`: `lanczos` (default) | `nearest` | `kcentroid` — kCentroid (dominant-cluster) downscale keeps 1px outlines readable.
  - `align_x`: `bbox-center` (default) | `centroid` | `foot-centroid` — bbox-centering shifts the body whenever a pose's content width changes (per-frame horizontal jitter); `foot-centroid` anchors the leg axis so trailing hair/capes don't pull the body off the runtime flip pivot.
  - `align_y`: `center` (default) | `bottom` — shared foot baseline.
  - `pixel_perfect` mode (with `logical_height`, `palette_size`, `detail_bias`, `outline`): runs-based pixel-pitch detection → **strip-shared grid phase** snap downscale → row-uniform conform scale → **inter-frame registration** (upper-body alpha overlap; measured jitter <=1 logical px) → union-bbox crop (no more cell-bottom clipping) → run-wide shared median-cut palette (kills frame-to-frame color flicker) → alpha binarization → `enforce_outline` (uniform 1px silhouette outline; `detail_bias` keeps eyes/outlines that dominant voting erases) → **integer NEAREST upscale** with row-constant placement. No non-integer resampling anywhere.
- **Curation webview restyled to a clean light theme** (white base, neutral greys, single blue accent); dark mode removed.
- Lessons baked in from the field: don't rely on engine negative-rect flipping (Godot 4 renders negative dest rects displaced and negative src rects empty past the first cell — pre-mirror rows into the atlas instead), and never anchor locomotion frames on per-frame foot centroids (feet are the moving part; register on the stable upper body).

## v1.9.2 — Chroma extraction no longer eats subject colors

Extraction fixes for subjects whose colors share a channel with the chroma key (e.g. a red/orange body under magenta, or any subject with small green/teal features).

- **Despill no longer destroys colors far from the key.** `remove_chroma_background` ran its "neutralize key tint" pass on every pixel whose channels leaned toward the key's, with *no distance gate* — so a saturated red/orange/blue subject was clamped toward olive/grey under a magenta key even at color-distance 200+. The destructive pass is removed (it only ever fired on pixels the fringe stage had already decided to keep); near-key antialias fringe is still removed as before. `neutralize_key_tint` is dropped.
- **`--chroma-key auto` stops silently deleting small features.** Candidate scoring ranked by the 1st-percentile distance to subject pixels, which discards sub-1% features (eyes, gems, ear lamps): a key could look "safe" while its nearest subject pixel was still inside the erase radius. `auto` now prefers candidates that clear *every* subject pixel, records `min_subject_distance`, and warns on stderr when none do.
- Regression coverage in `tests/test_chroma_extraction.py`; the golden extraction manifest is unchanged.

## v1.9.1 — Docs sync & polish

Catch the docs and repo up to the v1.8–v1.9 curator (from an evaluator-grade consistency audit).

- Documented the `order` field and the `flipX` / shear transforms in the `curation.json` schema (`SKILL.md` + `curation.py`), and the two-row sequence/candidate-pool, grip reorder, flip, and preview transport in the README.
- Removed a stray `console.log` and a hardcoded `/tmp` path in the curation-view snippet.
- Backfilled changelog entries for v1.5–v1.7.0.

## v1.9.0 — Pool arrangement persistence + sweep hardening

Adds full arrangement persistence and the hardening from a second adversarial sweep (which also caught a regression from v1.8.1).

- **Candidate pool arrangement persists.** `curation.json` now records the full display `order` (sequence then pool) alongside `selected`, so reopening the curator restores exactly how you arranged *both* rows — not just the sequence. The bake is unchanged: compose/export key off `selected`; `order` is webview-only and documented in `curation.py` (the schema SSoT).
- **Robust against corrupt / hand-edited sidecars.** Frame indices in `selected` and `order` are coerced to integers and de-duplicated on load; `curation.py` now skips non-integer / out-of-range `selected` entries instead of crashing the bake.
- **Fixed a duplicate-render regression** (introduced in v1.8.1): missing/unextracted frames now render once (they already live in `order`) — removed the redundant second render loop that doubled them and could leak duplicate indices into the atlas.
- **Label escaping.** State names, actions, and imported frame labels are HTML-escaped before display, so an imported set's `meta.json` can't inject markup; over-long labels truncate instead of breaking the card.

## v1.8.2 — Preview UX polish

Remaining low-severity items from the v1.8.0 adversarial review.

- **Preview re-anchors on edits.** Reordering or moving frames no longer jumps the preview to a different frame — it keeps the on-screen frame in view (tracked by frame index), so a paused inspection stays put while you rearrange the sequence.
- **Transport disabled when the sequence is empty.** Play/step buttons grey out (and the position reads `0/0`) when no frames are in the sequence, instead of looking active but doing nothing.

## v1.8.1 — Cross-platform hardening

Fixes from an adversarial cross-platform / blast-radius review of the v1.8.0 curator.

- **FLIP animation reliable on Safari/Firefox.** The reorder and settle animations now force a layout reflow between applying the inverted transform and enabling the transition (instead of a bare `requestAnimationFrame`), so cards slide instead of teleporting on non-Chromium engines. `.missing` cards are excluded from the FLIP.
- **Missing frames preserved.** `commitZones` and `seedEntries` keep not-yet-extracted frame slots in `order`, so a reorder during incremental extraction can't silently drop them.
- **Multi-touch guard.** The reorder grip ignores secondary pointers (`ev.isPrimary`), so a second finger can't start a parallel drag on touch devices.

## v1.8.0 — Curator: drag reorder + candidate pool

The standalone curation webview (`serve_curation.py`) gets a full frame-curation pass: reorder the play sequence by hand, scrub the preview, and reconstruct a run from several generated takes by dragging the cuts you like.

![Curator drag reorder + candidate pool](docs/assets/curator-drag-update.gif)

- **Drag-to-reorder frames.** Grab the `⠿` grip on a frame card to change the play order. The grabbed card lifts and follows the cursor while the others slide aside (FLIP animation), and it eases into its slot on drop. The new order saves to `curation.json.selected` and is baked left-to-right by `compose_sprite_atlas.py` — no backend change, fully non-destructive.
- **Two-row curation: sequence + candidate pool.** Each state now renders a **sequence** row (the selected play order, on top) and a **candidate pool** row below it (unselected frames — e.g. a second or third generated take of the same row). Drag a cut from the pool up into the sequence to add it, or a sequence cut down to drop it; a card click sends it to the other row. This makes it easy to reconstruct one clean run loop from the best cuts across multiple takes.
- **Preview transport.** The live preview gains play/pause, frame-by-frame stepping (`⏮`/`⏭`, auto-pauses), and a 0.25×–4× speed control, plus a `cursor/total · #frame` readout. Display-only — these never touch `curation.json`, so paused inspection and stepping don't disturb the selection.
- Selection is now a flag with a separate display order, so toggling a frame no longer re-sorts the sequence. i18n (en/ko) throughout.

Curator UI: `scripts/curator/curator.js`, `scripts/curator/curator.css`.

## v1.7.0 — README rewrite + standalone curation view

- README rewritten for humans (problem hook, what-you-get, honest labels) with an EN/KO architecture diagram.
- `SKILL.md`: standalone curation-view triggers (큐레이션 / curation keywords) and a generic image-candidate path, so the webview serves any PNG set (icons, logos, drafts), not just sprites.
- Version SSoT unified across `SKILL.md` and `pyproject.toml`.

## v1.6 — Concurrency-safe pipeline

Hardening so the pipeline is safe to run from multiple agents at once (e.g. Claude Code and Codex side by side).

- **Run-dir single-writer lock** (`runio.py`): extract/compose/export/unpack take a `.sprite-gen.lock` per run dir — a second writer on the same character folder fails loudly with the holder's pid instead of interleaving output; a dead holder's lock is reclaimed automatically.
- **Atomic outputs**: frames, atlas, manifests, and reports write via temp file + `os.replace`, so a concurrent reader never sees a half-written file.
- Curator flip (↔) `ReferenceError` fix; path-traversal guard on all curator-server routes; auto-launch the curation webview as the closing workflow step.
- Japanese, Simplified Chinese, Spanish & French READMEs.

## v1.5 — Curation webview

- Standalone local **curation webview** (`serve_curation.py`): compare a state's frames side by side, select/reject, non-destructive per-frame move/scale/rotate/shear, saved to a `curation.json` sidecar; bilingual UI (`--lang en|ko`).
- `unpack_atlas_run.py` — rebuild frames from a finished sheet (`--grid` › `--manifest` › alpha auto-detect), or import a loose PNG set (`--pngs-dir`).
- `export_curated_pngs.py` — bake corrections back to named PNGs.
- Isometric ground-grid overlay (from `meta.json` tile/anchor) + shear handle for aligning furniture to the floor.

Releases before v1.5 (v0.1.0–v1.4) predate this changelog; see the [GitHub releases](https://github.com/aldegad/sprite-gen/releases).
