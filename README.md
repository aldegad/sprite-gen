<p align="center">
  <img src="docs/assets/claudecy-idle.gif" width="110" alt="claudecy idle" />
  <img src="docs/assets/claudecy-running.gif" width="110" alt="claudecy running" />
  <img src="docs/assets/claudecy-success.gif" width="110" alt="claudecy success" />
  <img src="docs/assets/claudecy-talking.gif" width="110" alt="claudecy talking" />
  <img src="docs/assets/howl-idle.gif" width="110" alt="howl idle" />
  <img src="docs/assets/howl-running.gif" width="110" alt="howl running" />
  <img src="docs/assets/howl-success.gif" width="110" alt="howl success" />
</p>

<h1 align="center">sprite-gen</h1>

<p align="center"><b>One drawing in. A game-ready sprite atlas out.</b></p>

<p align="center">

**English** · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-Hans.md) · [Español](README.es.md) · [Français](README.fr.md)

</p>

---

Ask an image model for a "sprite sheet" and you know what you get: a character whose face changes every frame, a background that won't key out, poses that overlap and drift off-grid, and a PNG your game engine can't actually consume. Cute demo, useless asset.

`sprite-gen` is a Codex/Claude skill that closes that gap. Give it **one base image** and a list of actions — it drives the generation row by row, locks the character's identity, strips the chroma background to real alpha, extracts each pose as a clean transparent frame, and bakes a runtime atlas **with a machine-readable `manifest.json.frame_layout`**. Every sprite above was made this way.

And for the last 10% that generation never gets right, there's a **curation webview**: compare frames side by side, reject the broken ones, nudge rotation/scale/position non-destructively, watch the loop live — then bake. The pipeline does the labor; you keep the taste.

```text
sprite-request.json → layout guides + prompts → sprite-gen gen state rows
→ chroma alpha → connected components → transparent frames
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

```mermaid
flowchart LR
    REQ["sprite-request.json<br/>(numeric SSoT)"] --> GUIDES["layout guides<br/>+ prompts"]
    GUIDES --> GEN["sprite-gen gen<br/>state row strips"]
    GEN --> EXTRACT["chroma alpha →<br/>connected components"]
    EXTRACT --> FRAMES["transparent frames"]
    FRAMES --> ATLAS["sprite-sheet-alpha.png<br/>+ manifest.json.frame_layout"]
    FRAMES -. "curation webview (optional)" .-> ATLAS
```

> Full architecture: [`docs/architecture.md`](docs/architecture.md)

## What you actually get

- **A transparent sprite atlas** (`sprite-sheet-alpha.png`) — real alpha, no leftover chroma fringe, verified against white backgrounds.
- **A runtime manifest** (`manifest.json.frame_layout`) — absolute frame rectangles, per-state fps and loop flags. Your engine samples rectangles; it never guesses a grid.
- **QA you can watch** — per-state GIFs and contact sheets, so motion is judged as motion before anything ships.
- **Honest labels** — short readable actions (idle, jump, attack, wave) are the stable path; cyclic locomotion (walk/run) is marked experimental unless motion QA actually passes. No silent overpromising.

## Chroma alpha quality

The extractor keeps chroma cleanup deterministic: soft-alpha unmix preserves antialiased hair strands and thin outlines instead of peeling them away before coverage can be solved.

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-magenta.png" width="640" alt="full-body chroma comparison: illustration on magenta key" /><br />
  <em>Illustration, magenta key: source, v1.12.0 peel, v1.13.0 soft-alpha unmix.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-green.png" width="640" alt="full-body chroma comparison: illustration on green key" /><br />
  <em>Illustration, green key: source, v1.12.0 peel, v1.13.0 soft-alpha unmix.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-magenta.png" width="640" alt="full-body chroma comparison: pixel art on magenta key" /><br />
  <em>Pixel art, magenta key: source, v1.12.0 peel, v1.13.0 binarized output.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-green.png" width="640" alt="full-body chroma comparison: pixel art on green key" /><br />
  <em>Pixel art, green key: source, v1.12.0 peel, v1.13.0 binarized output.</em>
</p>

The close-up crops below show the edge detail behind the full-body comparisons.

![chroma peel before and after — illustrated hair strand](docs/assets/chroma-peel-illustration-before-after.png)

![chroma peel before and after — pixel-art outline](docs/assets/chroma-peel-pixelart-before-after.png)

## Curation webview

Generation gets you 90%. The webview is where a human takes it to *shipped* — standalone, no Studio or framework dependency, runs anywhere the skill is installed (Claude Code Desktop, the Codex app, a plain terminal).

![curation webview — characters](docs/assets/demo-character.gif)

- **Two rows per state:** the **play sequence** on top and a **candidate pool** below (e.g. a second or third generated take). Drag a frame's ⠿ grip to reorder the sequence, or pull a cut up from the pool — rebuild one clean run loop from the best frames across takes. The arrangement is saved, so reopening restores it.
- **Non-destructive transform** per frame: drag = move, wheel = scale, top handle = rotate, bottom-left = shear, plus a horizontal-flip toggle for left-right-reversed output. Edits live in a `curation.json` sidecar — source PNGs are never rewritten, and the compose step bakes the result deterministically. Preview and bake share one affine matrix, so what you align is what you get.
- **Live preview** animates the sequence at the state's fps, with play/pause, frame-by-frame stepping, and a 0.25×–4× speed control.
- Not just for sprites: point it at any folder of image candidates (icons, logos, generated drafts) with `unpack_atlas_run.py --pngs-dir` and use it as a general pick-the-winner view.

### Isometric ground grid

For isometric sets, the webview overlays the floor grid (from `meta.json` tile/anchor) so you can snap furniture to the diamond axes with the shear handle.

![curation webview — isometric furniture](docs/assets/demo-furniture.gif)

<img src="docs/assets/curator-iso.png" width="520" alt="isometric ground grid overlay" />

### Languages

The webview ships with English and Korean. Pass `--lang en|ko` when launching, or use the in-app toggle:

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # or ko
```

## Python support

`sprite-gen` supports CPython 3.10+. CI runs the minimum supported version (3.10) and the latest covered version (3.14) on GitHub-hosted runners.

The quickstart requires a Python install with working `venv`/`ensurepip`. If `python3 -m venv` fails before package installation in a local distribution, use a standard CPython build for any supported version and rerun the same commands.

## Quickstart

```bash
# 0. install dependencies (Pillow) into a fresh virtualenv
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. prepare a run from a base image
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. generate one row image per state with the engine-owned provider CLI
python3 scripts/generate_sprite_image.py --provider codex \
  --prompt-file <run-dir>/prompts/<state>.txt \
  --out <run-dir>/raw/<state>.png \
  --ref <run-dir>/base-source.png \
  --ref <run-dir>/references/layout-guides/<state>.png
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

### Image generation ownership

Provider-backed generation is part of this engine (`sprite_gen.gen`), with
`codex` and `grok` as the supported providers. The general `image-gen` skill is
only a thin shuttle to the same command, so it does not need a second provider
implementation. See [`docs/gen.md`](docs/gen.md) for the CLI and verification
contract.

## Attribution

The component-row workflow is inspired by the Apache-2.0 licensed `hatch-pet` skill, but targets generic game sprite atlases and includes no pet packages or pet visual assets.

## License

Apache-2.0
