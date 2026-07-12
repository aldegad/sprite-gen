# Directional / 45° Anchor Workflow — sprite-gen reference

> `SKILL.md` 에서 분리한 시나리오 상세. 방향성·45도·locomotion 행 생성 시 이 문서를 따른다. 기본 simple sprite (`idle`/`jump`/`attack`/`wave`) 에는 필요 없다. 내용은 손실 없이 `SKILL.md` 본문에서 그대로 옮겨졌다.

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

   **A direction anchor is exactly ONE single-pose image (앵커 = 1장).**
   A multi-frame idle row is NOT a valid direction anchor: attaching a row
   makes the model read frame-to-frame micro-motion as identity variance and
   dilutes the facing lock. If only an idle row exists, crop one pose
   deterministically (frame 0 by default) and lock that single image as the
   anchor; the row itself stays a motion/timing artifact, never an anchor.
   (수홍 확정 2026-07-12 — solvell founder 5-anchor 사고에서 도출.)

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
     side-specific identity details for that direction — always a
     **single-pose single image**, never a multi-frame row
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

