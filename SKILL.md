---
name: sprite-gen
version: 2.38.0
description: "Generates images and game sprites through GPT or Grok with guided provider choices, separate saved defaults, automatic cleanup and optional curation. Handles sprite requests, ordinary image generation/editing, standalone image-to-video clips (i2v, animate this still, 그록 영상, 이매진 비디오, 스틸 움직여줘, first/last frame, reference-to-video, 영상 이어붙이기, 영상 편집, extend/edit a clip), clips from a video MCP on the agent (ZCRE, 지크 MCP), chroma removal, animation atlases, video loops, 큐레이션뷰, image candidates, 팔레트 스왑, palette swap, recolor, rig layers, engine exports, repeating backgrounds, projected shadows, motion/contact inspection and optional scene composition from existing assets."
license: Apache-2.0
depends_on:
  required_bins:
    - name: codex
      why: "gen --provider codex (image_gen via ChatGPT OAuth)"
    - name: ffmpeg
      why: "video-frames (clip -> frames) and video-set"
    - name: img2webp
      why: "video-loop WebP with exact alpha (libwebp); Pillow's animated writer drops -exact"
    - name: rife-ncnn-vulkan
      why: "video-loop jump repair and video-cycle-align (RIFE v4.6 in-betweens, release 20221029). Install once with `sprite-gen rife install` (sha256-checked, into the user data directory); SPRITE_GEN_RIFE or PATH override it; Linux without a GPU also needs libvulkan1 mesa-vulkan-drivers. Without it, walk and run loops are cut as filmed with a warning"
  required_scripts:
    - scripts/prepare_sprite_run.py
    - scripts/generate_sprite_image.py
    - scripts/gen_set.py
    - scripts/generate_sprite_video.py
    - scripts/video_canvas.py
    - scripts/video_frames.py
    - scripts/video_loop.py
    - scripts/video_set.py
    - scripts/extract_sprite_row_frames.py
    - scripts/interpolate_frames.py
    - scripts/compose_sprite_atlas.py
    - scripts/preview_animation.py
    - scripts/compose_selected_cycle.py
    - scripts/compose_sprite_gif.py
    - scripts/inspect_sprite_run.py
    - scripts/score_sprite_run.py
    - scripts/run_correction_loop.py
    - scripts/curation.py
    - scripts/serve_curation.py
    - scripts/slice_sheet_cells.py
    - scripts/unpack_atlas_run.py
    - scripts/export_curated_pngs.py
    - scripts/recolor.py
    - scripts/compose_layers.py
modes:
  default: component-row
---

# Sprite Gen

Generation entry points: **make sprites**, **make an image**, or **animate a still into a video**. Existing generation, extraction and export tools do the work; the user chooses the result and provider. For **repeat a background**, **project a shadow**, **inspect motion** or **compose a scene**, use the independent routes below directly with existing assets.

## Start every generation request here

For a standalone video clip, read [video](docs/video.md) and use `sprite-gen video`.
This route works from any agent engine and delivers a verified MP4 plus report.
For sprites or ordinary images, use the guides below.

Read [user-workflow](docs/user-workflow.md), then run the appropriate read-only guide:

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen workflow --kind sprite
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen workflow --kind image
```

Pass choices already stated in the request. The guide checks access, combines explicit choices with saved defaults, and returns only missing questions. Follow its start and finish stages. Always pass the resolved provider explicitly to generation tools. Deliver checked files before offering the curation view; save defaults only when the user agrees. The complete conversation and settings contract is owned by the linked document, not duplicated in individual pipeline docs.

## Side-view facing

Keep the side still, canvas placement and motion prompt facing the same direction.
`video-set` observes each side input once before canvas placement and uses the requested
`--facing` for both the canvas and clip prompt. Observation is record-only by default;
it never changes the requested direction. Image correction requires explicit opt-in.

| Command option | Values and default | Behavior |
|---|---|---|
| `gen --facing` | `preserve` (default), `right`, `left` | With `--ref`, an explicit direction adds a prompt requirement and checks the generated still. |
| `gen --facing-fix` | `none` (default), `mirror`, `regen` | Record without correction; opt into mirroring an observed opposite or regenerating once and rechecking. A still-opposite regeneration is mirrored. |
| `video --direction` | `side`, `front`, `back`, `front_diagonal`, `back_diagonal`; unset by default | `side` opts into facing inspection and a matching prompt requirement; the others skip it. The two diagonals are three-quarter views turned right (`VIEW_TEXT`); a walk or run in one is filmed pinned (`pins_last_frame`). |
| `video --facing`, `video-set --facing` | `right` (default), `left`; `video-set` also `right,left` | Required side direction. `right,left` films every side and diagonal view both ways, each from its own still (`--base side@left=…`); none is mirrored. |
| `gen --direction`, `--handed`; `video-prompt` / `video-set --handed` | a view; `"<item>=<left\|right> [part]"` | An item on one of the character's own sides: the still and clip prompts say where it is in each view, and mirroring is refused. |
| `video-prompt` / `video-set` / `gen --direction` / `prepare --body-plan` | `biped` (default), `quadruped`, `legless`; or `"<figure>=<plan>"` per figure of a scene | What the subject stands on: no clip, attack, redraw, still view or sheet row prompt names a part it lacks (a horse is not walked on a person's legs, stood up, given arms in a walk sheet or told to strike with its hands), and a scene says it per figure. |
| `video --facing-fix`, `video-set --facing-fix` | `none` (default), `mirror` | Record the observation; opt into mirroring an observed opposite in a copy. Batch side inspection is enabled by default. |

Direction is requested through the generation and motion prompts, which cannot guarantee model compliance.
The detector can be wrong even at high confidence: review the still before choosing `mirror` or `regen`.
Generation reports record direction, model, requested direction, model-reported confidence and correction
under `extra.facing`; video reports and batch items use `facing`. `final_direction` is an observation
or a value derived from it, not independent verification; `final_direction_source` identifies which.
An uncertain or failed inspection records `unknown` and its reason and continues without correction;
front-facing observations also remain unchanged. Mirroring does not preserve left/right accessory handedness.
It stays the default, since drawing the left-facing views costs as much again; for a character with a watch on
one wrist or a pin on one side, offer to draw them instead with `--handed` and check them with `handed-check`
(a colour-marked item, frame by frame; `--board` for the eye) —
[handedness](docs/video-pipeline.md#handedness--an-item-on-one-side). In a side view an item on the far wrist
or hand shows each time that arm swings forward, never hidden for the whole walk: draw that side still mid-stride with
the far arm out in front of the body and the item in view (`video-prompt` says so under `still_needs`), do not
hold the arms still, and check the loop with `handed-check --state walk`.

## Execution routes

| Task | Entry | Contract |
|---|---|---|
| GPT image sprites | `prepare`, `gen-set --provider codex`, `extract`, compose and QA | [atlas-workflow](docs/atlas-workflow.md) |
| Standalone video / animate a still, pin a last frame, reference images | `video` (`--image`, `--last-frame`, `--reference`) | [video](docs/video.md) |
| Continue or edit an existing clip | `video-extend`, `video-edit` | [video](docs/video.md) |
| Grok video sprites | `video-set` | [video-pipeline](docs/video-pipeline.md) |
| A soft part of a walk loop follows the body (a chest, a belly): an ellipse in the first cell, after `video-cycle-align` | `video-follow` | [video-pipeline](docs/video-pipeline.md#6-follow-through--video-follow) |
| Video sprites from a clip your agent makes with a connected video MCP (ZCRE, 지크) | `video-canvas`, `video-prompt --no-last-frame`, the agent's own MCP tools (quote, user approval, generate, save the mp4), then `video-frames` and `video-loop` (a walk or run with `--anchor motion-auto`, which holds its size), and for a walk or run cut in two or more directions `video-cycle-align --view …` over their loop directories (a set it stops on a loop that may hold two cycles: look at that loop, count its strides, and pass `--cycles <loop>=<k>`; a loop the report lists under `unnamed_feet` — its view could not tell the feet apart, or only by too small a margin (`low-margin`): look at its first candidate frame, say which own foot lands there, and align again with `--foot <loop>=left|right`). `video-set` is not run on this route, so the set stage it runs by default (`--align-cycles auto`) is yours to run. sprite-gen never calls the MCP. Walk, run and jump only: ZCRE's `grok-imagine-video-1.5` has no end frame, so idle, attack and diagonal walks are refused unless `--unpinned` | [video-pipeline](docs/video-pipeline.md#a-clip-from-a-video-mcp-on-your-agent--zcre) |
| Ordinary image or edit | `gen --provider codex` or `gen --provider grok` (subscription routes) | [gen](docs/gen.md) |
| Image generation with no login available (server, container, SaaS) | `gen --provider openai` — server/SaaS route on `OPENAI_API_KEY`, **billed per call**, never a default or a fallback | [gen](docs/gen.md#subscription-first--openai-is-named-or-it-does-not-run) |
| Base and direction anchors | `anchor` | [directional-anchor-workflow](docs/directional-anchor-workflow.md) |
| Curation view or existing image candidates | `curation`, `unpack-atlas --pngs-dir` | [curation](docs/curation.md) |
| Uniform background removal or imported sheets | `cutout`, `slice-sheet` | [sheet-slicing](docs/sheet-slicing.md) |
| Palette swap | `sprite-gen recolor-palette`, `sprite-gen recolor` | [recolor](docs/recolor.md) |
| Rig layer composition | `sprite-gen compose-layers` | [layer-tracks](docs/layer-tracks.md) |
| Idle breathing | curation choice, baked by compose | [breathing](docs/breathing.md) |
| Engine exports | `export-aseprite`, `export-pngs` | [engine-export](docs/engine-export.md) |
| Background recipe or repeating tile | existing `gen` / `cutout`, then optional `background-tile` | [asset-tools](docs/asset-tools.md#background-recipe) |
| Standalone projected shadow | `shadow` | [asset-tools](docs/asset-tools.md#projected-shadows) |
| Duplicate poses, foot contact and stride measurement | `inspect-motion` | [asset-tools](docs/asset-tools.md#motion-and-contact-evidence) |
| Optional scene placement, lighting, camera and render | `scene-render`, `scene-inspect` | [scene](docs/scene.md) |
| Defaults | `defaults show`, `defaults save`, `defaults clear` | [user-workflow](docs/user-workflow.md#one-settings-owner) |

Say a thing once: what an option says, leave out of the prompt. `--direction`, `--facing`, `--handed`, the key background and `video --direction side` each add their own sentence after your text, in one place; without `--handed` every prompt is 2.22.0's, byte for byte, and only the handed sentences are left out where your text already has them. Your prompt carries the subject, its design and its pose. Text that says the opposite of an option ("facing left" with `--facing right`, the item on the other wrist) is sent as written with a warning (`extra.prompt_notes`, `video-prompt`'s `warnings`): fix the prompt, do not send it — [prompt-assembly](docs/prompt-assembly.md).

`gen --transparent` with `--ref` plans a chroma key (adding the key's background line to a prompt that names none), and publishes a result that already came back transparent on its own alpha instead of keying its outline away; `alpha.strategy_source` in the report says which (`refs-attached` keyed, `refs-attached-raw-alpha` not) — [gen](docs/gen.md#transparent-output--strategy-per-provider).

Use existing automatic pipeline stages for background removal, extraction, alignment and export. Do not ask users to select each script. For a direct utility request, run that utility; no unrelated generation questions are needed. Preserve the row pipeline and component extraction for image sprites. One-shot grid generation and fixed cell cutting are not an alternative sprite-generation route.

For attack repeat coverage, observed one-shot returns and structured loop failure reports, follow [video-pipeline](docs/video-pipeline.md#one-shot-actions--cycle-autoperiodicone-shot).

Scene creation consumes finished assets and remains optional. Asset metadata owns frames, native durations and anchors; scene specs own placement, scale, playback rate, planes, camera and light. Measure stride only with declared same-foot contact and an isolated foot ROI; unknown contact stays unverified. Apply only a verified report for the exact selected asset with an explicit scene direction. Never infer walking direction from the bottommost silhouette, reverse frames or change source assets to make a scene work.

## 실행 인터프리터

`SPRITE_GEN_ROOT` is the absolute installed repository path. Use `$SPRITE_GEN_ROOT/.venv/bin/sprite-gen` or `$SPRITE_GEN_ROOT/.venv/bin/python`; do not assume an activated shell. **폴백 금지**: create a missing venv or report the failure, never use an arbitrary global Python. **NumPy 가 없는 인터프리터** fails at package import. Setup and diagnosis: [interpreter](docs/interpreter.md).

## RIFE for walk and run loops

`video-loop` repairs a walk's or run's jump frames and `video-set` gives a direction set one cycle length, both with RIFE in-betweens. Install it once per machine: `$SPRITE_GEN_ROOT/.venv/bin/sprite-gen rife install` (pinned rife-ncnn-vulkan 20221029 and model rife-v4.6, SHA-256 checked, into the user data directory, ending with a check frame; Linux without a GPU first needs `libvulkan1 mesa-vulkan-drivers`). Without it every loop is still cut, as filmed, and says so: a `warning:` line on stderr, `jump_repair.applied: false` in the loop report, `cycle_align.<state>.applied: false` and `warnings` in `set.report.json`. When that happens, tell the user which loops went unrepaired and offer the install; after it, cut those loops again and run `video-cycle-align`. `video-loop --repair on` and `video-cycle-align` fail without RIFE instead. [loop-repair](docs/loop-repair.md).

## Contracts and advanced tools

[run-contract](docs/run-contract.md) owns numeric requests, run layout, atomic publication and curated exports. [architecture](docs/architecture.md) explains domain ownership. [docs index](docs/README.md) lists every specialized feature and QA procedure. `sprite-gen --help` derives the command map from the package catalog. Never replace an engine stage — extraction, the loop cut, set alignment, loop repair — with a script of your own while presenting the result as a pipeline output. Where a stage's result is wrong, use the engine's own options first (another cut with `video-loop --max-len` or `--cycle fixed`, `video-cycle-align --length`, `--between auto` (the default: a made frame that melted takes the nearer source frame) or `--between nearest`, or `--cycles <loop>=<k>` once you have counted a suspect loop's strides, or `--foot <loop>=left|right` (`video-set --align-foot <item>=…`) once you have looked at the first strike frame of a loop the report lists under `unnamed_feet`), keep the engine's result as the comparison, report the defect against the engine, and name in the delivery each stage done by hand and why. A new clip is for what no stage can reach: the drawing, the motion itself, a clip that holds no whole cycle ([loop-review](docs/loop-review.md)) — and a clip the engine names for one: when the alignment report's `retake` lists a loop (reason `held-drawings`: the cycle cut from its clip shows each drawing for two or three frames, the set's length leaves it at under 13 drawings a second, and frames between its drawings could not be made, so the loop halts there), run the engine verbs first as above, then film that direction again and align the set anew rather than hand-painting the gap; tell the user what the reason was and that a new take usually fixes it ([loop-repair](docs/loop-repair.md) section 4). `video-loop --anchor motion-auto` already cuts a walk two steps long — one cycle — where a staff held above the head closes only there (`cycle.steps`, `strip.json` `steps: 2`; `video-cycle-align` aligns it as the one cycle it is). When its report's `cycle.step_screen` says `suspect` (a slow walk cut one step long), or `video-cycle-align` stops on a loop under `one_step`, count the steps in the loop (how often each foot lands) and cut it again with `video-loop --steps 1` if it is one step (`--steps 2` if it is two); never stretch or double it by hand. When `video-loop` warns that the top of the silhouette jumps into the loop's first frame (a staff or flag held above the head, swinging on its own beat), or that the held part does not close at the wrap below its top, that part does not close at the cut: look at the loop, and try `--cycle fixed` with another cut before filming again; a walk refused for no cycle names in its report's `cycle.candidates` the windows it measured, best first, for a cut you must deliver anyway ([loop-repair](docs/loop-repair.md) section 3, "The seam pop" and "The held side").
