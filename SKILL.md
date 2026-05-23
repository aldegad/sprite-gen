---
name: sprite-gen
description: "Generate clean 2D game sprites and animation atlases with the hatch-pet-proven component-row pipeline: base identity, numeric sprite-request SSoT, per-state layout guides, image-gen row strips, chroma-key alpha cleanup, connected-component frame extraction, cell-based atlas composition, QA reports, and runtime manifest frame_layout."
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
modes:
  default: component-row
---

# Sprite Gen

`sprite-gen` is the game-sprite version of the hatch-pet-style component-row pipeline. It does not create Codex pets, `pet.json`, or the fixed 8x9 pet atlas. It reuses the proven workflow shape for generic game sprites:

```text
sprite-request.json -> layout guides + prompts -> image-gen state rows
-> chroma alpha -> connected components -> transparent cells
-> sprite-sheet-alpha.png + manifest.json.frame_layout
```

There is only one canonical engine: `component-row`. Do not use one-shot master sheets, fixed-grid atlas cutting, local drawing, or static fallback as a successful sprite result.

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

Terminal architecture:

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

### Hatch-Pet Locomotion Pattern

For compact mascot or pet-like locomotion, use the hatch-pet-proven pattern instead of treating `run`/`walk` as a generic humanoid row:

- Make the base image small-runtime readable first. Compact silhouette and clear limb shapes matter more than high-detail character fidelity.
- Prefer rectangular 192x208-ish row cells for chibi/pet locomotion, then let the manifest expose the final atlas rectangles. Do not force the generation row to be square just because a game engine later uses square texture regions.
- Use 8 frames for directional run rows only when the base is simple enough and motion QA is part of the output.
- Generate `running-right` first and inspect it before generating `running-left`.
- When generating `running-left`, attach `raw/running-right.png` as a gait row input. Use it as a **gait rhythm reference only**; identity remains owned by `base-source.*`.
- Do not mirror `running-right` into `running-left` unless the user explicitly approves and the design is symmetric enough. Mirroring is observable derivation, not a silent fallback.

This pattern is not a skeleton. The layout guide still only provides slot count, spacing, centering, and safe padding. The gait row reference gives the image model a visual example of limb phase and body rhythm.

### Directional Chain Default

Directional states must default to a chained reference plan, not independent per-direction generation. This applies to locomotion (`running`, `walking`) and non-locomotion action rows (`working`, `talking`, `success`, `idle`, etc.) whenever the state name encodes a facing direction such as `*-front-right`, `*-front-left`, `*-back-right`, or `*-back-left`. Independent generation is allowed only as an explicit experiment and must be labeled that way in `qa-notes.md`.

### Checklist Direction-Anchor Workflow

For humanoid or direction-sensitive sprites, use this staged checklist before
generating any final sheet row. The goal is to reduce the model's choices at
each step instead of asking it to solve identity, direction, state, and motion
in one image.

0. **Input gate** — collect what the user already has. If information is
   missing, ask for only the next blocking choice:
   - base character image / character sheet / style reference
   - target direction set: front only, horizontal/vertical 4-way, or 45-degree
     4-way
   - requested states and frame counts
   - style contract and cell size

1. **Base idle gate** — create or accept one canonical base idle. It owns the
   first identity lock only. Do not proceed if the base is cropped, wrong style,
   or identity-weak. After direction idle anchors are accepted, this base must
   not be attached to final action rows.

2. **Direction gate** — create direction anchors before action rows:
   - front-only: one accepted front idle anchor
   - horizontal/vertical 4-way: accepted `idle-front`, `idle-left`,
     `idle-right`, `idle-back`
   - 45-degree 4-way: accepted `idle-front-right`, `idle-front-left`,
     `idle-back-right`, `idle-back-left`

3. **State anchor gate** — for each requested non-locomotion state and
   direction, create one representative state anchor before generating the
   multi-frame row. For example, `working-front-right-anchor` can show the
   approved desk/computer pose while `idle-front-right` still owns facing.
   For cyclic locomotion (`running`, `walking`, `run`, `walk`, and directional
   variants), do **not** feed a single peak-pose state anchor into the final
   row. A single contact pose causes the model to repeat that same leg phase
   across every frame. Locomotion needs a motion-phase reference that contains
   both opposite contacts, such as a contact sheet, selected cycle, or layout
   phase guide.

4. **Asymmetric identity gate** — lock side-specific character features before
   paired direction generation. Hairpins, earrings, scars, logos, handed props,
   one-sided markings, asymmetric clothing, and lighting cues are identity
   invariants, not direction controls. A paired left/right row may rotate the
   body, feet, shoulders, face angle, and gaze, but it must not silently mirror
   these features onto the wrong physical side of the character. If a feature
   would become wrong under a horizontal flip, write that explicitly into the
   row prompt and fail QA if it flips.

5. **Row generation gate** — generate the final row only after the matching
   direction idle anchor and state anchor exist. Reference ownership is:
   - base image: pre-idle source only, not a row-generation input
   - direction idle anchor: identity, facing/orientation, and visible
     side-specific identity details for that direction
   - state anchor: pose/state vocabulary plus the approved state-specific
     identity rendering, for non-locomotion states only
   - locomotion motion sheet/contact sheet: foot-contact phase and gait rhythm
   - paired basis row: timing, scale, and animation intensity
   - layout guide: frame count, slots, margins, optional motion phase
   The row prompt must keep character detail as an already-approved idle-anchor input and
   spend its degrees of freedom on animation only: limb contacts, arm
   counter-swing, body height, torso lean, head bob, hair bounce, and loop seam.
   Do not ask the row to decide hairpin side, outfit details, colors, face
   design, or other identity features from scratch.
   For locomotion rows, a single running/walking pose anchor is not valid row
   grounding unless it is part of a multi-pose contact sheet with both
   left-forward and right-forward contacts visible.

6. **Hatch-pet left/right gate** — preserve the hatch-pet-proven left/right
   pattern. Generate the right/basis row first, inspect it, then generate the
   left/paired row with the basis row attached. The paired row must obey its own
   target-direction idle anchor for facing; the basis row is only gait rhythm,
   scale, and animation intensity.

7. **QA gate** — do not advance silently. Record pass/fail per stage:
   - base idle QA
   - direction anchor QA
   - asymmetric identity QA
   - state anchor QA
   - extraction and atlas QA
   - motion continuity / loop seam / direction readability QA

If any gate fails, stop at that stage or mark the output `experimental`.

For 45-degree state packs, make direction a separate visual SSoT:

- Preferred default: create and accept four canonical idle direction anchors first: `idle-front-right`, `idle-front-left`, `idle-back-right`, and `idle-back-left`.
- Generate every later action row from its matching idle direction anchor. For example, `working-front-left`, `running-front-left`, and `walking-front-left` all attach the accepted `idle-front-left` anchor.
- The base image owns only the pre-idle source stage. The direction idle anchor owns row identity and facing/orientation. The action row owns motion/state. Do not collapse these truths into one prompt burden.
- If four direction idle anchors do not exist yet, create or reuse one accepted 4-direction direction sheet/contact sheet before generating action rows.
- Attach that direction sheet to **every** 45-degree row. Use it only for facing/orientation, not pose, state, identity, or timing.
- Also attach one **single target-direction anchor** for the requested row direction (`front-right`, `front-left`, `back-right`, or `back-left`). Prefer the accepted idle anchor for that direction. This anchor is the highest-priority facing reference for that row.
- Each row prompt must name the exact facing: `front-right`, `front-left`, `back-right`, or `back-left`.
- If the generated row averages into straight front/back or pure side view, mark it `direction-failed`; do not silently rename it to a different direction.

For a left/right side pair:

1. Generate the basis row first: `running-right`.
2. Inspect the basis row before continuing.
3. Generate `running-left` with the basis row attached as a gait rhythm reference.

Reference order for the basis side row:

```text
idle-right.png -> references/layout-guides/running-right.png
```

Reference order for the paired side row:

```text
idle-left.png -> raw/running-right.png -> references/layout-guides/running-left.png
```

For any four-direction 45-degree state, use two basis rows and two paired rows:

1. Generate `<state>-front-right` first.
2. Generate `<state>-front-left` with `raw/<state>-front-right.png` attached as the paired-row reference.
3. Generate `<state>-back-right` first.
4. Generate `<state>-back-left` with `raw/<state>-back-right.png` attached as the paired-row reference.

For locomotion rows, the paired-row reference is a gait rhythm reference. Do
not attach a single running/walking state pose as the main row reference; it
will bias every frame toward that one leg phase. Use a motion contact sheet,
selected cycle, or the previously approved basis row for timing. For
non-locomotion rows, the paired-row reference is a pose-family and scale
reference: keep the same prop scale, body size, frame occupancy, and animation
intensity while changing the facing direction.

The direction sheet is still required for both basis and paired rows. The paired row is not allowed to copy the basis facing.

Reference order for a 45-degree basis row:

```text
idle-<basis-direction>.png -> references/layout-guides/<basis-state>.png
```

Reference order for a 45-degree paired row:

```text
idle-<paired-direction>.png -> raw/<basis-state>.png -> references/layout-guides/<paired-state>.png
```

If the target-direction anchor and paired basis row conflict, obey the target-direction anchor for facing and the paired basis row only for timing, scale, and animation intensity.

Use this state set for a 45-degree run request:

Use this state set for the 45-degree request:

```json
{
  "states": {
    "running-front-right": { "frames": 8, "fps": 8, "loop": true, "action": "45-degree diagonal run toward camera-right and slightly toward the viewer; alternating foot contacts, arms counter-swing, ponytail bounces, continuous loop" },
    "running-front-left": { "frames": 8, "fps": 8, "loop": true, "action": "45-degree diagonal run toward camera-left and slightly toward the viewer; use the attached front-right row only as gait timing and mirrored phase reference, not identity; alternating foot contacts, arms counter-swing, ponytail bounces, continuous loop" },
    "running-back-right": { "frames": 8, "fps": 8, "loop": true, "action": "45-degree diagonal run away from viewer toward camera-right; three-quarter-back view, alternating foot contacts, arms counter-swing, ponytail bounces, continuous loop" },
    "running-back-left": { "frames": 8, "fps": 8, "loop": true, "action": "45-degree diagonal run away from viewer toward camera-left; use the attached back-right row only as gait timing and mirrored phase reference, not identity; three-quarter-back view, alternating foot contacts, arms counter-swing, ponytail bounces, continuous loop" }
  }
}
```

Use this state set for a 45-degree working request:

```json
{
  "states": {
    "working-front-right": { "frames": 6, "fps": 6, "loop": true, "action": "45-degree three-quarter-front view facing camera-right and slightly toward the viewer; working at a compact laptop or tablet held close and touching both hands; subtle typing, eye, and hair motion" },
    "working-front-left": { "frames": 6, "fps": 6, "loop": true, "action": "45-degree three-quarter-front view facing camera-left and slightly toward the viewer; use the attached front-right row only for scale, prop size, and motion intensity; working at a compact laptop or tablet held close and touching both hands; subtle typing, eye, and hair motion" },
    "working-back-right": { "frames": 6, "fps": 6, "loop": true, "action": "45-degree three-quarter-back view facing away toward camera-right; working at a compact laptop or tablet held close and touching both hands; subtle typing, shoulder, and hair motion" },
    "working-back-left": { "frames": 6, "fps": 6, "loop": true, "action": "45-degree three-quarter-back view facing away toward camera-left; use the attached back-right row only for scale, prop size, and motion intensity; working at a compact laptop or tablet held close and touching both hands; subtle typing, shoulder, and hair motion" }
  }
}
```

Use this state set for a 45-degree walk experiment:

```json
{
  "states": {
    "walking-front-right": { "frames": 8, "fps": 6, "loop": true, "action": "45-degree diagonal walk toward camera-right and slightly toward the viewer; smaller stride than running; alternating foot contacts, gentle arm counter-swing, ponytail sway, continuous loop" },
    "walking-front-left": { "frames": 8, "fps": 6, "loop": true, "action": "45-degree diagonal walk toward camera-left and slightly toward the viewer; use the attached front-right row only as timing and mirrored phase reference, not identity; smaller stride than running; alternating foot contacts, gentle arm counter-swing, ponytail sway, continuous loop" },
    "walking-back-right": { "frames": 8, "fps": 6, "loop": true, "action": "45-degree diagonal walk away from viewer toward camera-right; three-quarter-back view; smaller stride than running; alternating foot contacts, gentle arm counter-swing, ponytail sway, continuous loop" },
    "walking-back-left": { "frames": 8, "fps": 6, "loop": true, "action": "45-degree diagonal walk away from viewer toward camera-left; use the attached back-right row only as timing and mirrored phase reference, not identity; three-quarter-back view; smaller stride than running; alternating foot contacts, gentle arm counter-swing, ponytail sway, continuous loop" }
  }
}
```

Record the exact reference stack for every row in `qa-notes.md`. The generated basis row is not a second identity truth; it is only a paired-row input. If the paired row copies the wrong facing direction, report the row as `direction-failed` instead of silently mirroring or renaming it.

### Advanced Gates

Expose only these gates to the caller for advanced hatch-style runs:

- **pose/state gate** — requested state row, frame count, fps, and loop flag
- **cell gate** — cell width, cell height, and safe margins
- **style gate** — one explicit style contract such as `pixel-art-adjacent`, `2.5D chibi`, or `3D-to-sprite`
- **reference gate** — base/canonical/original/gait references used for that row

All prompt text, guides, extraction, atlas composition, and QA must be regenerated from `sprite-request.json`. Do not keep a separate prompt fork as a second truth surface. If an advanced gate fails QA, report the state as failed or experimental rather than silently falling back to a static or mirrored result.

### Motion Phase Guide Experiment

For 8-frame run rows, `prepare_sprite_run.py --motion-phase-guides` adds simple stick-pose hints to the layout guide:

```text
contact -> down -> passing -> up -> opposite contact -> down -> passing -> up
```

Use this only for explicit locomotion experiments. The guide is not final art and must not appear in the generated row. Its purpose is to nudge foot contact, body height, and leg phase. It can improve leg alternation, but it is not a guarantee of a natural run loop; visual motion QA remains blocking.

### Manual Selected Cycle

When a generated locomotion row contains usable frames but the full row fails motion QA, do not pretend the full row passed and do not ask image generation to redraw locked peak frames. Preserve the generated frame truth and make a separate selected-cycle artifact:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/compose_selected_cycle.py \
  --run-dir <target>/assets/generated/sprites/<character-id> \
  --state running-right \
  --frames 2,3,4,5 \
  --name running-right-selected-2-3-4-5 \
  --duration-ms 190 \
  --note "human QA selected current best right-run loop"
```

This writes:

```text
qa/<name>.gif
qa/<name>-contact.png
qa/<name>.json
```

`qa/<name>.json` is the selected-cycle SSoT: it records the source state, exact 1-based selected frame numbers, runtime zero-based frame indices, duration, and SHA-256 for every source frame. Runtime integrations may consume the original atlas with the selected zero-based frame order, or export a derived atlas explicitly from that manifest.

Use this path for user-approved manual locomotion curation. It is not a silent fallback: report that full-row locomotion failed and that the selected subset is the accepted usable loop.

For precise humanoid running today, the most reliable path is candidate generation plus human frame picking. Generate a few candidate rows, keep the best extracted frames, and let the user choose or reorder the 1-based frame sequence for `compose_selected_cycle.py`. Do not promise automatic frame-order selection yet; if the row only works after manual picking, record that as the current limitation in `qa-notes.md`.

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
