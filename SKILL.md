---
name: sprite-gen
description: "Generate clean 2D game sprites and animation atlases with a component-row pipeline: base identity, numeric sprite-request SSoT, per-state layout guides, image-gen row strips, chroma-key alpha cleanup, connected-component frame extraction, cell-based atlas composition, QA reports, and runtime manifest frame_layout."
license: Apache-2.0
depends_on:
  required_skills:
    - kuma:image-gen
  required_scripts:
    - scripts/prepare_sprite_run.py
    - scripts/extract_sprite_row_frames.py
    - scripts/compose_sprite_atlas.py
    - scripts/preview_animation.py
    - scripts/compose_selected_cycle.py
    - scripts/compose_sprite_gif.py
    - scripts/gif_utils.py
modes:
  default: component-row
---

# Sprite Gen

`sprite-gen` builds generic game sprite atlases with a `component-row` pipeline:

```text
sprite-request.json -> layout guides + prompts -> image-gen state rows
-> chroma alpha -> connected components -> transparent cells
-> sprite-sheet-alpha.png + manifest.json.frame_layout
```

Use only the `component-row` pipeline. Do not treat one-shot master sheets, fixed-grid atlas cutting, local drawing, or static fallback as a successful sprite result.

## Script Map

The skill uses scripts as explicit pipeline commands, not as hidden imports. Each script has one job:

- `prepare_sprite_run.py` — prepare a run from request truth: write `sprite-request.json`, per-state layout guides, prompts, and empty `raw/` and `frames/` folders.
- `extract_sprite_row_frames.py` — read generated `raw/<state>.png` strips, remove chroma background, extract connected sprite components, and write transparent frame PNGs plus `frames/frames-manifest.json`.
- `compose_sprite_atlas.py` — compose extracted frames into `sprite-sheet-alpha.png` and runtime `manifest.json.frame_layout`.
- `preview_animation.py` — build QA previews from extracted frames: contact sheets and state GIFs under `qa/`.
- `compose_selected_cycle.py` — record a human-selected frame subset as an explicit selected-cycle manifest plus QA GIF/contact sheet.
- `compose_sprite_gif.py` — export a clean transparent GIF from selected frame PNGs and optional frame order.
- `gif_utils.py` — shared transparent-GIF writer used by the GIF/QA scripts.
- `check_visible_magenta.py` — optional screenshot QA guard for visible chroma-key leakage.

## Simple MVP Scope

The default user promise is deliberately simple:

> A Codex user installs this skill, provides a character/base image and one or more simple actions, then receives a sprite sheet, GIF preview, and QA notes.

Do not frame the default path as game-ready humanoid locomotion. The current Codex/image-gen path is good at short readable pose changes, identity-preserving rows, chroma cleanup, atlas composition, and QA. It is not yet reliable enough to promise precise cyclic locomotion for humanoids.

Default/simple states:

- `idle` — stable default. Use 4 frames, loop true.
- `jump` — stable default as a short non-loop action. Use 4 frames, loop false.
- `attack` — stable default as a short non-loop action. Use 4 frames, loop false.
- `wave` — simple gesture, but only stable as non-loop unless the row includes a return-to-idle frame. Use 4 frames, loop false by default; use 5 frames only when the final frame intentionally returns near frame 1.
- `talk`, `blink`, `bounce`, `hurt`, `celebrate`, `magic_cast` — allowed simple candidates, but still require motion QA before pass.

Experimental states:

- `walk`, `run`, `frontwalk`, `45_frontwalk`, and other cyclic locomotion.
- Directional cycles that require exact foot-contact alternation or phase symmetry.
- Any state where the user needs game-ready locomotion rather than a readable preview animation.

For experimental states, report them as experimental in `qa-notes.md` unless motion QA passes. Never silently promote a weak walk/run row to the same status as simple MVP output.

## Quick Path For Simple Animations

When the user asks for "simple sprite animation", prefer this request shape unless they specify otherwise:

```json
{
  "states": {
    "idle": { "frames": 4, "fps": 4, "loop": true, "action": "subtle breathing and one blink" },
    "attack": { "frames": 4, "fps": 8, "loop": false, "action": "simple windup, strike, recovery attack pose sequence with no detached effects" },
    "jump": { "frames": 4, "fps": 8, "loop": false, "action": "simple jump arc: crouch, takeoff, airborne, landing" }
  }
}
```

Add `wave` only as a non-loop gesture by default:

```json
"wave": { "frames": 4, "fps": 6, "loop": false, "action": "friendly hand wave gesture; arm changes clearly while feet stay planted" }
```

Simple MVP pass requires:

- automated extraction and atlas reports pass
- `qa/<state>.gif` reads as the requested simple action
- loop seam passes for looped states
- non-loop states have clear start/middle/end pose progression
- `qa-notes.md` records `pass`, `best-effort`, or `experimental` per state

### Frame Count Guidance

Keep default simple actions short. More frames do not automatically create smoother animation in the current component-row image generation path:

- `4` frames is the default stable range for simple actions.
- `5` frames is acceptable when a non-loop gesture needs a return-to-idle pose.
- `6` frames is the conservative upper edge for simple humanoid one-shot defaults.
- `8` frames is hatch-pet-style advanced territory, not forbidden. Use it for compact mascots, locomotion rows, or explicit experiments only when extraction/motion QA passes.
- `9` and `12` frames are **not** default simple settings. In validation runs, they increased duplicate bodies, empty/sparse frames, slot collapse, and extraction failure before adding useful in-betweens.

If a user asks for 9 or 12 frames, run it as an explicit experiment and report `duplicate-heavy`, `blur/merge`, or `extract-fail` honestly instead of treating it as a normal pass.

## Idle Anchor Architecture (Stage 0, BLOCKING)

The row-generation pipeline has one hard ownership rule:

```text
identity truth = accepted idle anchor
motion truth   = layout guide + paired/basis row when needed
base truth     = used only to create idle anchors, then removed from row inputs
```

Base character images, original character sheets, and broad style references are allowed only before idle anchors are accepted. Once an idle anchor exists for the requested direction, later state rows must not attach the base character image as insurance. Re-attaching base makes the row model solve identity again and weakens the purpose of the idle-anchor workflow.

Reference ownership flow:

```text
[USER REFS / CHARACTER SHEET]
          |
          v
+-----------------------------+
| 0. BASE IDLE 생성            |
| - 원본/캐릭터시트는 여기서만 사용 |
| - 비율/스타일/색/소품 고정       |
+-----------------------------+
          |
          v
+-----------------------------+
| 1. 방향별 IDLE ANCHOR 생성    |
| - idle-front-right           |
| - idle-front-left            |
| - idle-back-right            |
| - idle-back-left             |
+-----------------------------+
          |
          v
        BASE CHARACTER 폐기
        original refs 폐기
        character sheet 폐기
        이후 row 입력 금지
          |
          v
+-----------------------------+
| 2. BASIS ROW 생성            |
| input:                       |
| - target-direction idle      |
| - target-state layout only   |
| output: basis row            |
+-----------------------------+
          |
          v
+-----------------------------+
| 3. PAIRED ROW 생성           |
| input:                       |
| - paired-direction idle      |
| - paired-state layout only   |
| - basis row                  |
| output: paired row           |
+-----------------------------+
          |
          v
+-----------------------------+
| 4. GIF / SHEET 조립          |
| - row crop                   |
| - gif preview                |
| - frame-index QA             |
+-----------------------------+
```

A weak idle anchor poisons every state — proportions, style, and identity drift compound across all rows. So before any row generation you must pass an explicit gate.

Gate question, answered `y`/`n`:

> Is there an image good enough to **lock** as the canonical base idle?

The base idle locks only when **all** of these hold:

- Full body, nothing cropped (head to feet inside frame).
- The final proportions and style the user asked for are already correct in this image (for example SD / chibi head-to-body ratio, pixel look, outline weight). The base defines the target — do not plan to "fix it later" in the rows.
- Identity matches the character sheet / reference (face, hair, markings, palette, props).
- One clear single idle pose, facing the intended camera, readable silhouette at small size.
- Background is a flat clean chroma-ready fill (or trivially keyable).

If the answer is `n`: generate/iterate base candidates, review each against the criteria above, and re-gate. **Do not run `prepare_sprite_run.py` until a base is locked.** "Good enough for now" is not a pass — drift only grows once the rows start.

When the answer is `y`, that exact file becomes the accepted idle anchor for its direction. Keep the original generation around so the lock decision is auditable, but do not attach it again after the idle anchors have replaced it as row identity truth.

## License And Attribution

`sprite-gen` is released under Apache-2.0. The component-row workflow is inspired by the Apache-2.0 licensed `hatch-pet` skill, but this project does not include Codex pet assets, pet packages, or hatch-pet visual assets.

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
    "attack": { "frames": 4, "fps": 8, "loop": false, "action": "simple windup, strike, recovery attack pose sequence with no detached effects" },
    "jump": { "frames": 4, "fps": 8, "loop": false, "action": "jump arc through body position only" },
    "wave": { "frames": 4, "fps": 6, "loop": false, "action": "friendly hand wave gesture; arm changes clearly while feet stay planted" }
  }
}
```

`256` is a default variable, not a hidden constant. Change it through the request, then regenerate guides, prompts, extraction, and atlas from the same request.

Rectangular generation cells are allowed when the target motion benefits from hatch-pet-style row proportions:

```json
"cell": { "shape": "rect", "width": 192, "height": 208, "safe_margin_x": 18, "safe_margin_y": 16 }
```

The generated row uses the request cell shape. The final atlas is still consumed through `manifest.json.frame_layout`; runtime code must not assume square cells.

## Workflow

0. Pass the **Base Lock Gate** above. Do not start step 1 until a base idle is locked (`y`).

1. Prepare the run:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/prepare_sprite_run.py \
  --out-dir <target>/assets/generated/sprites/<character-id> \
  --character-id <character-id> \
  --base-image /absolute/path/to/base.png \
  --description "<short identity note>" \
  --force
```

For hatch-pet-style locomotion, add the cell gate explicitly:

```bash
  --cell-width 192 \
  --cell-height 208
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

For simple/default states before direction-anchor mode exists, attach exactly two references:

- `base-source.<ext>` — canonical character identity
- `references/layout-guides/<state>.png` — layout-only guide

For direction-anchor mode, do not attach `base-source.<ext>` to action rows. Attach the accepted target-direction idle anchor plus the state layout guide. For a paired row, also attach the already generated basis row as timing/scale/motion reference only.

For hatch-pet-style locomotion, attach additional references only when they are part of the row plan and record them in `qa-notes.md`. Useful advanced references are:

- original character reference / sheet — identity support only
- canonical base image — identity support only
- previous generated gait row, such as `raw/running-right.png` for `running-left` — motion rhythm only
- accepted previous motion QA artifact, such as `qa/<state>-contact.png` or an approved selected-cycle contact sheet — gait readability support only

Use `prompts/<state>.txt` as the prompt. Save the selected generated image as `raw/<state>.png`.

3. Extract frames:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/extract_sprite_row_frames.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

This removes the request chroma key, finds connected sprite components, fits each pose into a fresh transparent request-sized cell, and writes `frames/<state>/frame-N.png` plus `frames/frames-manifest.json`.

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
- one complete full-body pose per invisible request-sized slot
- safe margin from `sprite-request.json`
- same locked anchor identity across every frame
- motion-only row responsibility: the row should solve limb/body timing, not rediscover character details
- flat chroma-key background from `sprite-request.json`
- no shadows, glows, smears, speed lines, dust, scenery, text, UI, frame numbers, guide boxes, or detached effects

If image generation produces guide boxes, visible labels, overlapping poses, backgrounds, cropped bodies, or identity drift, regenerate the row. Do not repair bad visual generation by drawing or tiling sprites locally.

## Advanced Workflows

기본 simple sprite (`idle`/`jump`/`attack`/`wave`) 는 위 Workflow + 아래 QA 만으로 끝난다. 방향성·45도·locomotion 처럼 다단계 reference 가 필요한 작업은 아래 문서를 따른다 (본문에서 손실 없이 분리됨):

- 방향성/45도 앵커 체인, Hatch-Pet locomotion 패턴, Advanced Gates → [`reference/directional-anchor-workflow.md`](reference/directional-anchor-workflow.md)
- Motion phase guide 실험, 수동 selected-cycle, 클린 GIF export → [`reference/locomotion-curation.md`](reference/locomotion-curation.md)

## Chroma And Alpha

`prepare_sprite_run.py` chooses a chroma key by sampling the base image unless the request forces one. The generated character must not use the chroma color or chroma-adjacent colors.

**Choose the chroma away from the subject's dominant hue — do not blindly default to magenta.** `extract_sprite_row_frames.py` neutralizes chroma-adjacent tint, so any character color that sits near the key gets eaten. The failure is hue-adjacency, not exact match:

- Deep-red / crimson / wine hair or clothing is **magenta-adjacent** (both high R). Magenta keying turns red hair near-black after extraction. Use **green** for red/crimson/warm subjects.
- Green/teal/olive subjects → use **magenta**.
- Blue subjects → avoid cyan/blue keys; magenta or green.

When unsure, let `--chroma-key auto` sample the base (it scores candidates by distance from subject pixels). Only force a key when you know the subject hue is safely far from it. Verify after extraction that the dominant subject color survived — a black-where-it-should-be-colored frame means the key was adjacent to the subject.

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

Automated checks (must all pass before reporting done):

- `frames/frames-manifest.json.ok` is true
- `sprite-sheet-alpha.report.json.ok` is true
- every state has the declared frame count
- no frame is empty or near-opaque background
- no frame has excessive edge pixels or chroma-adjacent pixels
- browser screenshots pass `scripts/check_visible_magenta.py` when used in a game

### Motion Continuity (BLOCKING)

Static identity QA is not enough. A row can have the right frame count, clean alpha, and consistent identity and still animate as garbage. Review motion **as motion**:

- Build a per-state contact sheet and an animated preview, then watch the loop:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/preview_animation.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

This writes `qa/<state>-contact.png` (frames left-to-right) and `qa/<state>.gif` (played at the state `fps`).
The GIF is exported through the clean transparent GIF path (dedicated transparent index + disposal method 2), while the contact sheet uses a checker background for inspection.

- **Cyclic locomotion (walk / run):** the motion must read as continuous locomotion, not static bobbing. Review body rhythm, limb motion, foot contact stability, and whether the loop communicates the requested direction and speed.
- **Experimental locomotion boundary:** walk/run/frontwalk/45-frontwalk are not simple default pass states. They may be generated, but the report must call them experimental unless motion continuity passes cleanly.
- **Loop seam:** for `loop: true` states, the last frame must flow back into the first. A visible jump at the wrap is a fail.
- **Non-loop gestures:** for `loop: false` states such as attack, jump, hurt, or wave, judge start/middle/end readability instead of loop seam. Do not force a non-loop gesture into a loop just because it has multiple frames.
- **Humanoid caution:** humanoid joints (knees, elbows, hips, hands) are where diffusion drifts most. Review **every** frame for broken anatomy, extra/missing limbs, and limb-length changes. Humanoids need stricter per-frame review than blob/creature sprites — do not skim.
- **Independent second opinion (recommended for humanoids):** hand `qa/<state>.gif` (or the contact sheet) to a fresh `kuma:image-gen`-style codex vision pass and ask specifically: "does this read as continuous `<state>` motion; is the loop seamless; is the identity stable across frames; are there anatomy or jitter problems?" Trust a second judge over a single reviewer for motion calls.

If a row fails motion continuity (static bobbing, jitter, anatomy break, identity drift, or a hard loop seam), **regenerate that row**. Do not repair motion by drawing or re-timing frames locally.

Record the per-state motion verdict in `qa-notes.md`.

Report:

```text
sprite_gen_done=<character-id>
folder=<absolute folder path>
engine=component-row
files=sprite-request,raw,frames,atlas,manifest
qa_note=<one sentence>
```
