# Changelog

All notable public changes to `sprite-gen` are recorded here. Versions track the `version:` field in `SKILL.md` and `pyproject.toml`.

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
