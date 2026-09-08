# Changelog

All notable public changes to `sprite-gen` are recorded here. Versions track the `version:` field in `SKILL.md` and `pyproject.toml`.

## Unreleased (v1.62.0) - Video to Sprite

- Added the `sprite_gen/video` domain: `sprite-gen video-canvas` (state canvas — tall for jumps, wide for attacks, square otherwise; the still's corner key fills the padding), `sprite-gen video-frames` (ffmpeg extraction + cutout keying with an edge-contact gate), `sprite-gen video-loop` (global-period cycle detection, strip with `body_h` metadata, 1-bit-alpha GIF, `img2webp -exact` WebP, seam and periodicity gates, output re-verification) and `sprite-gen video-set` (directions × states with staggered starts and a bounded HTTP 429 retry, per-item reports and a table).
- Declared `ffmpeg` and `img2webp` as required binaries for the video pipeline; `docs/video-pipeline.md` records the contract and the 2026-09-08 measurements it comes from.

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
