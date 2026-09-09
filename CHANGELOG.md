# Changelog

All notable public changes to `sprite-gen` are recorded here. Versions track the `version:` field in `SKILL.md` and `pyproject.toml`.

## Unreleased (v2.0.0) - Four pipelines, one taxonomy

### Highlights

- **Video → sprite loops.** One still becomes a whole motion set: `video-canvas` pads it into the canvas the state needs, `video` animates it in place through Grok Imagine, `video-frames` keys every frame, `video-loop` finds the true period (or the one performed action) and emits a strip, a transparent GIF and a WebP, and `video-set` runs directions × states with per-item reports. Every stage is measured and fails by name.
- **Parallel row generation is a command.** `gen-set` generates every state row of a prepared run N at a time with the run's own identity ref, one report per row, a table and a non-zero exit on any failure — what the skill used to describe in prose.
- **One taxonomy.** `sprite-gen --help` opens with the four named pipelines (A atlas rows · B video → loop · C utilities · D post-processing) and groups every verb by domain; the grouping, the `scripts/` map and the pipeline list derive from `sprite_gen/_modules.py` (`MODULE_DOMAIN`, `PIPELINES`), and the docs classification is catalogued in `docs/README.md` itself, checked by tests against the file set and the pipeline catalog.
- **Documentation you can navigate.** `docs/README.md` indexes every doc once under its branch with a one-line owner; `docs/architecture.md` opens with a domain diagram and a four-pipeline diagram; the largest docs carry a table of contents; the repo README shrinks to an entry page and the sections it carried live in the docs that own them.

### Breaking

- New required binaries for pipeline B: `ffmpeg` (frame extraction) and `img2webp` from libwebp (WebP with exact alpha). Pipelines A, C and D do not need them.
- Removed the `scripts/` aliases that pointed at library modules or verb-less modules. Use the verb or module instead:

  | Removed | Use |
  |---|---|
  | `scripts/extract.py` | `sprite-gen extract` (`scripts/extract_sprite_row_frames.py`) |
  | `scripts/gif_utils.py` | `from sprite_gen.util import gif_utils` |
  | `scripts/runio.py` | `from sprite_gen.spec import runio` |
  | `scripts/reroll_state_row.py` | `sprite_gen.effects.reroll` (module; `interpolate_frames.py` covers the take workflow) |

- Maintainer experiments moved to `scripts/dev/` (`breathe_mutation_battery.py`, `measure_align_sigma.py`, `validate_pr6_subject_profile.py`, `check_visible_magenta.py`); they are not verbs and the skill no longer requires them.
- `docs/static-pose-recipe.md` merged into `docs/breathing.md` (one contract, one owner); links to the old file are gone.

### Added

- `sprite_gen/video/` domain and the verbs `video-canvas`, `video-frames`, `video-loop`, `video-set` (contract: `docs/video-pipeline.md`). `video-loop --cycle auto|periodic|one-shot`: action states (`jump`, `attack`, unknown) whose clip performs the action once get a recorded one-shot cut (rest → excursion → rest) instead of a hard failure; the report keeps the rejected periodic attempt and `table.md` gains a `kind` column. `walk`, `run`, `idle` still fail loud without a period.
- `sprite-gen gen-set` (`sprite_gen/gen/gen_set.py`, `scripts/gen_set.py`): 6 rows at a time by default (the lead-verified batch width), anchors before rows on direction runs, reuse unless `--force`, `reports/gen-set/table.md` + `set.report.json`, `--provider` honoured verbatim with `gen`'s own default resolution and its recorded codex → grok availability failover per row.
- `sprite_gen._modules.DOMAINS` / `domain_of()`: display order and one-line meaning per domain; `cli.command_domains()` derives the help groups. A verb whose module is not in the table fails loudly.
- `docs/README.md` (documentation index) with a test that pins it to the file set and resolves every relative markdown link; tables of contents in `run-contract.md`, `layer-tracks.md`, `curation.md`, `directional-anchor-workflow.md`, `architecture.md`; `scripts/dev/README.md`.

### Changed

- `SKILL.md` is a route-first hub (still / atlas / video-to-loop / utilities) under the 24 KB skill budget; the interpreter rationale, the rename gate and the breathing contract moved verbatim to `docs/interpreter.md`, `docs/rename-gate.md`, `docs/breathing.md`.
- `video-loop` strips are capped by pixel width (32 000 px) as well as by cell count: a 650 px-wide cell now yields 49 cells instead of a 41 664 px image Chrome refuses; the strip meta records `cell_cap` and `subsampled`.
- Walk detection window floor 10 % → 6 % of the clip so a legless body's fast bounce resolves; the 15 % depth rule keeps rejecting the one-step half period (verified on a biped and a quadruped). `video-set` motion templates no longer assume a biped.
- `README.md` is an entry page: what it is, the four pipelines, one quickstart each, install. Breathe, chroma-alpha quality, Backbone Lattice and the curation webview tour moved verbatim to `docs/breathing.md`, `docs/chroma-alpha.md`, `docs/pixel-unfake.md`, `docs/curation.md`.

### Removed

- See **Breaking** for the removed `scripts/` aliases and the merged doc.

## v1.61.0 - Image to Video

- Added `sprite-gen video`: one still + prompt → a verified mp4 through Grok Imagine (`POST /v1/videos/generations`), with duration 1–15 s, 480p/720p/1080p, optional aspect ratio and audio flag, and a `sprite-gen-video-report` JSON.
- Credentials are the user's own and never part of the repo: `XAI_API_KEY` when set, otherwise the `grok` CLI login file (`~/.grok/auth.json`, `GROK_HOME` honoured). The report records `auth_source`; tokens and download URLs are never printed or written.
- An expired grok login fails before any upload with the exact refresh command; a set-but-empty `XAI_API_KEY`, a missing credential, a refused request, a failed/expired generation, a poll timeout, or a non-mp4 download each fail by name and write nothing.
- New wrapper `scripts/generate_sprite_video.py` and docs at `docs/video.md`.

## v1.60.0 - Native Alpha

- `sprite-gen gen --transparent` now follows a per-provider transparency strategy declared once on each adapter (`Provider.transparency`). `codex` asks `image_gen` for a genuinely transparent background and publishes the measured alpha (`native`, first choice); `grok` keeps deterministic chroma keying because Grok Imagine returns JPEG only.
- Added `--alpha-mode auto|native|chroma`. `auto` reads the provider's strategy, `chroma` forces keying on codex for prompts that already carry a key background, and `native` on a chroma-only provider fails before any model call. With `--ref` attached, `auto` keys instead of asking codex for native alpha (measured 1/6 real alpha with references vs 6/6 chroma); the report records why under `alpha.strategy_source`.
- Native output is verified before publishing: no alpha channel or 0% transparent pixels refuses the run (a drawn checkerboard is never keyed silently), RGB under alpha 0 is scrubbed, and partial alpha is reported untouched.
- Reports carry an `alpha` block (`strategy` plus stats) next to the existing `chroma` stats, and codex's own `transparentBackground` claim under `extra.transparent_background_reported`.
- Sprite-row generation is unchanged: rows still carry the request chroma key and are keyed at extraction.

## v1.59.0 - Contributor Collection

This release incorporates accepted work from eight community pull requests. Thanks to [@devswha](https://github.com/devswha) for chroma color preservation, [@bokjk](https://github.com/bokjk) for portable manifest paths, [@Dongkyu-ES](https://github.com/Dongkyu-ES) for deterministic CLI tests, engine export, and subject-aware sparse-frame handling, [@napkn34](https://github.com/napkn34) for the Windows provider and publish-lock fixes, and [@monibu1548](https://github.com/monibu1548) for pixel-unfake vertical centering and grounding controls.

- Added `sprite-gen export-aseprite` for Phaser-compatible Aseprite JSON and Flame-compatible hash files split by state. Curated frame geometry and timing remain canonical, and exports are confined to the run's `exports/` directory.
- Added a Windows `LockFileEx` backend that preserves shared readers and exclusive publishers across processes without weakening the fail-loud isolation contract.
- Fixed provider CLI resolution and UTF-8 subprocess I/O on Windows, including npm `.cmd` shims and non-UTF-8 console code pages.
- Made Python 3.14 CLI option tests deterministic under colored shell output.
- Added `character` and `effect` subject profiles. Their sparse-frame floors scale with cell resolution: `ceil(sqrt(width * height))` for characters and half that value for effects. Explicit `--min-used-pixels` still wins.

## v1.58.0 - Compose canvas and domain package layout

- Added the human-facing `sprite-gen compose` assembly canvas and handoff to the curation view.
- Reorganized the Python package and tests into domain subpackages. CLI and script entrypoints remain stable; Python imports intentionally use `sprite_gen.<domain>.<module>` paths derived from `sprite_gen._modules`.
- Split request loading from schema migration so reads no longer mutate run state.

## v1.57.0 - First Pixel Breath

- Added deterministic breathing, pixel-grid measurement, curation editing, and run repair contracts.
- Added deterministic palette-swap recolor baking (`sprite-gen recolor` / `recolor-palette`) and curation-side colourway selection.
- Added package entrypoints, declared runtime dependencies, and install smoke coverage.

Earlier public milestones are summarized above. Historical tags remain published only where their contents pass the current public-data policy.
