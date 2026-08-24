# Changelog

All notable public changes to `sprite-gen` are recorded here. Versions track the `version:` field in `SKILL.md` and `pyproject.toml`.

## Unreleased

- `SKILL.md` 48.2KB → 24.0KB. The hub had absorbed per-feature procedure and rationale; both now live one link deep, and the hub keeps the blocking gates, the script map, the step index, and the contracts. New leaf docs: `docs/workflow.md` (each step's exact command and reference-attachment rules), `docs/breathe.md` (sidecar contract, rigid-boundary detection, curation editor), `docs/interpreter.md` (why the venv interpreter is the only register), `docs/rename-gate.md` (the vocabulary-rename order and its six checks), `docs/README.md` (docs topology tree + concept→owner taxonomy). No rule was deleted and no moved line survives in two places.

## v1.59.1 - Gen is the default image path

- Frontmatter `description` and hub intro now lead with `sprite-gen gen`: Codex ChatGPT OAuth by default, 6-concurrent batches, optional `--ref`, not sprite-only. Atlas pipeline stays the second job. The blocking "AI raw is not the final asset" gate is scoped to atlas/pipeline outputs so a still PNG is not rejected as an incomplete sprite. A route-first table at the top of the hub stops agents from treating still generation as an atlas job.

## v1.59.0 - Contributor Collection

This release incorporates accepted work from eight community pull requests. Thanks to [@devswha](https://github.com/devswha) for chroma color preservation, [@bokjk](https://github.com/bokjk) for portable manifest paths, [@Dongkyu-ES](https://github.com/Dongkyu-ES) for deterministic CLI tests, engine export, and subject-aware sparse-frame handling, [@napkn34](https://github.com/napkn34) for the Windows provider and publish-lock fixes, and [@monibu1548](https://github.com/monibu1548) for pixel-unfake vertical centering and grounding controls.

- Added `sprite-gen export-aseprite` for Phaser-compatible Aseprite JSON and Flame-compatible hash files split by state. Curated frame geometry and timing remain canonical, and exports are confined to the run's `exports/` directory.
- Added a Windows `LockFileEx` backend that preserves shared readers and exclusive publishers across processes without weakening the fail-loud isolation contract.
- Fixed provider CLI resolution and UTF-8 subprocess I/O on Windows, including npm `.cmd` shims and non-UTF-8 console code pages.
- Made Python 3.14 CLI option tests deterministic under colored shell output.
- Added `character` and `effect` subject profiles. Their sparse-frame floors scale with cell resolution: `ceil(sqrt(width * height))` for characters and half that value for effects. Explicit `--min-used-pixels` still wins.

## v1.58.0 - Compose canvas and domain package layout

- Added the human-facing `sprite-gen compose` assembly canvas and handoff to the curation view.
- Reorganized the Python package and tests into domain subpackages while preserving CLI and script entrypoints.
- Split request loading from schema migration so reads no longer mutate run state.

## v1.57.0 - First Pixel Breath

- Added deterministic breathing, pixel-grid measurement, curation editing, and run repair contracts.
- Added deterministic palette-swap recolor baking (`sprite-gen recolor` / `recolor-palette`) and curation-side colourway selection.
- Added package entrypoints, declared runtime dependencies, and install smoke coverage.

Earlier public milestones are summarized above. Historical tags remain published only where their contents pass the current public-data policy.
