# sprite-gen

<p align="center">
  <img src="docs/claudecy-running.gif" width="160" alt="claudecy running" />
</p>

**English** · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-Hans.md) · [Español](README.es.md) · [Français](README.fr.md)

A Codex/Claude skill for generating clean 2D game sprites and animation atlases with a component-row pipeline — and a standalone webview for reviewing, curating, and fixing the frames before they bake.

```text
sprite-request.json → layout guides + prompts → image-gen state rows
→ chroma alpha → connected components → transparent frames
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

## Example output

Sprites generated and curated with this skill (`claudecy`, `howl`):

<p>
  <img src="docs/claudecy-idle.gif" width="110" alt="claudecy idle" />
  <img src="docs/claudecy-running.gif" width="110" alt="claudecy running" />
  <img src="docs/claudecy-success.gif" width="110" alt="claudecy success" />
  <img src="docs/claudecy-talking.gif" width="110" alt="claudecy talking" />
  <img src="docs/howl-idle.gif" width="110" alt="howl idle" />
  <img src="docs/howl-running.gif" width="110" alt="howl running" />
  <img src="docs/howl-success.gif" width="110" alt="howl success" />
</p>

## Curation webview

After frames are extracted, launch a standalone local webview to review them — no Studio or framework dependency, so it runs anywhere the skill is installed (Claude Code Desktop, the Codex app, a plain terminal).

![curation webview — characters](docs/demo-character.gif)

- **Compare frames side by side** per state, and **select / reject** individual frames.
- **Non-destructive transform** per frame: drag = move, wheel = scale, top handle = rotate, bottom-left handle = shear. Edits are saved to a `curation.json` sidecar — the source PNGs are never rewritten, and the compose step bakes the result deterministically. Preview (CSS + canvas) and bake share one affine matrix, so what you align is what you get.
- **Live preview** animates the selected frames at the state's fps.

### Isometric ground grid

For isometric sets, the webview overlays the floor grid (from `meta.json` tile/anchor) so you can snap furniture to the diamond axes with the shear handle.

![curation webview — isometric furniture](docs/demo-furniture.gif)

<img src="docs/curator-iso.png" width="520" alt="isometric ground grid overlay" />

### Languages

The webview ships with English and Korean. Pass `--lang en|ko` when launching, or use the in-app toggle:

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # or ko
```

## Quickstart

```bash
# 0. install dependencies (Pillow) into a fresh virtualenv
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. prepare a run from a base image
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. generate one row image per state with image-gen, save as raw/<state>.png
# 3. extract frames
python3 scripts/extract_sprite_row_frames.py --run-dir <run-dir>

# 4. (optional) curate frames in the webview
python3 scripts/serve_curation.py --run-dir <run-dir>

# 5. bake the runtime atlas
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### Editing a finished sheet

When only the combined sheet survives, rebuild a curator-ready run dir, then curate and export:

```bash
# rebuild frames: explicit --grid, --manifest rectangles, or alpha auto-detect (default)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # auto-detect
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # exact rectangles
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # import a loose PNG set

# after curating, bake corrections back to named PNGs
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

Output defaults to a findable `<source>-curator` folder next to the input.

The full agent-facing workflow and contracts live in [`SKILL.md`](SKILL.md).

## Install

From Codex skill installer workflows, install this repository as a root skill:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

## Attribution

The component-row workflow is inspired by the Apache-2.0 licensed `hatch-pet` skill, but targets generic game sprite atlases and includes no pet packages or pet visual assets.

## License

Apache-2.0
