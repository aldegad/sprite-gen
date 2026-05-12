---
name: sprite-gen
description: "Generate clean 2D game sprites and animation atlases with the hatch-pet-proven component-row pipeline: base identity, numeric sprite-request SSoT, per-state layout guides, image-gen row strips, chroma-key alpha cleanup, connected-component frame extraction, square-cell atlas composition, QA reports, and runtime manifest frame_layout."
depends_on:
  required_skills:
    - kuma:image-gen
  required_scripts:
    - scripts/prepare_sprite_run.py
    - scripts/extract_sprite_row_frames.py
    - scripts/compose_sprite_atlas.py
modes:
  default: component-row
---

# Sprite Gen

`sprite-gen` is the game-sprite version of the hatch-pet pipeline. It does not create Codex pets, `pet.json`, or the fixed 8x9 pet atlas. It reuses the proven shape of hatch-pet:

```text
sprite-request.json -> layout guides + prompts -> image-gen state rows
-> chroma alpha -> connected components -> square frames
-> sprite-sheet-alpha.png + manifest.json.frame_layout
```

There is only one canonical engine: `component-row`. Do not use one-shot master sheets, fixed-grid atlas cutting, local drawing, or static fallback as a successful sprite result.

## SSoT

Every run starts with `sprite-request.json`. It owns the numeric recipe used by prompts and scripts:

```json
{
  "version": 1,
  "kind": "sprite-gen-request",
  "engine": "component-row",
  "character": { "id": "howl", "description": "same character as the base image" },
  "cell": { "shape": "square", "size": 256, "safe_margin": 24 },
  "chroma_key": { "name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255] },
  "states": {
    "idle": { "frames": 4, "fps": 4, "loop": true, "action": "subtle breathing and blinking" },
    "run": { "frames": 6, "fps": 10, "loop": true, "action": "running cycle with limb and body motion only" },
    "jump": { "frames": 4, "fps": 8, "loop": false, "action": "jump arc through body position only" },
    "talk": { "frames": 4, "fps": 6, "loop": true, "action": "mouth and small hand or face gesture" }
  }
}
```

`256` is a default variable, not a hidden constant. Change it through the request, then regenerate guides, prompts, extraction, and atlas from the same request.

## Workflow

1. Prepare the run:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/prepare_sprite_run.py \
  --out-dir <target>/assets/generated/sprites/<character-id> \
  --character-id <character-id> \
  --base-image /absolute/path/to/base.png \
  --description "<short identity note>" \
  --force
```

This writes:

```text
sprite-request.json
base-source.<ext>
references/layout-guides/<state>.png
prompts/<state>.txt
raw/
frames/
```

2. Generate one image per state with `kuma:image-gen`.

For each state, attach exactly two references:

- `base-source.<ext>` — canonical character identity
- `references/layout-guides/<state>.png` — layout-only guide

Use `prompts/<state>.txt` as the prompt. Save the selected generated image as `raw/<state>.png`.

3. Extract frames:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/extract_sprite_row_frames.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

This removes the request chroma key, finds connected sprite components, fits each pose into a fresh transparent square cell, and writes `frames/<state>/frame-N.png` plus `frames/frames-manifest.json`.

4. Compose the runtime atlas:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/compose_sprite_atlas.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

This writes:

```text
sprite-sheet-alpha.png
sprite-sheet-alpha.report.json
manifest.json
```

`manifest.json.frame_layout` is the runtime SSoT. Game code must consume rectangles from the manifest and must not recover frame rectangles from alpha content at runtime.

## Prompt Contract

The generated row prompt must come from `prompts/<state>.txt`. Do not hand-write frame counts into a separate prompt. The prompt requires:

- exact state frame count from `sprite-request.json`
- one complete full-body pose per invisible square slot
- safe margin from `sprite-request.json`
- same identity across every frame
- flat chroma-key background from `sprite-request.json`
- no shadows, glows, smears, speed lines, dust, scenery, text, UI, frame numbers, guide boxes, or detached effects

If image generation produces guide boxes, visible labels, overlapping poses, backgrounds, cropped bodies, or identity drift, regenerate the row. Do not repair bad visual generation by drawing or tiling sprites locally.

## Chroma And Alpha

`prepare_sprite_run.py` chooses a chroma key by sampling the base image unless the request forces one. The generated character must not use the chroma color or chroma-adjacent colors.

`extract_sprite_row_frames.py` owns alpha cleanup for sprite rows. It removes pixels near the chroma key, removes chroma-tinted antialias fringe, neutralizes remaining key-color tint, clears fully transparent RGB, extracts connected components, and writes fresh transparent cells. This is intentionally closer to hatch-pet than to simple `magick -transparent`.

If component extraction cannot find the declared frame count, the row is blocked. `--allow-slot-fallback` exists for explicit debugging only; it must be reported as `slots-explicit` and is not the default path.

## Output Contract

One worker owns exactly one character folder:

```text
<target>/assets/generated/sprites/<character-id>/
  sprite-request.json
  base-source.<ext>
  references/layout-guides/<state>.png
  prompts/<state>.txt
  raw/<state>.png
  frames/<state>/frame-N.png
  frames/frames-manifest.json
  sprite-sheet-alpha.png
  sprite-sheet-alpha.report.json
  manifest.json
  qa-notes.md
```

Do not let multiple workers write the same character folder. If a folder exists from a previous run, create a timestamped sibling unless the user explicitly says to replace it.

## Runtime Contract

`manifest.json` must contain:

- `game_input: "sprite-sheet-alpha.png"`
- `degraded_static_fallback: false`
- `animation.rows.<state>` with `frames`, `fps`, and `loop`
- `frame_layout.rows.<state>[i]` absolute atlas rectangles

Runtime must sample only the active rectangle. Rendering the whole atlas on one plane, guessing a grid, or showing a raw chroma row is a failed integration.

Static fallback is allowed only as explicit survival output when generation is blocked. It is not a sprite-gen pass and must not create `sprite-sheet-alpha.png`.

## QA

Before reporting done:

- `frames/frames-manifest.json.ok` is true
- `sprite-sheet-alpha.report.json.ok` is true
- every state has the declared frame count
- no frame is empty or near-opaque background
- no frame has excessive edge pixels or chroma-adjacent pixels
- contact/visual review confirms identity consistency and readable motion
- browser screenshots pass `scripts/check_visible_magenta.py` when used in a game

Report:

```text
sprite_gen_done=<character-id>
folder=<absolute folder path>
engine=component-row
files=sprite-request,raw,frames,atlas,manifest
qa_note=<one sentence>
```
