---
name: sprite-gen
version: 3.14.0
description: "Generates images and game sprites through GPT or Grok with guided provider choices, separate saved defaults, automatic cleanup and optional curation. Handles sprite requests, ordinary image generation/editing, typography logos (로고, 타이포그래피, 타이틀 글자, 타이틀 로고, wordmark, logo, title lettering: a title screen drawn with the title in it, its lettering lifted out transparent), standalone image-to-video clips (i2v, animate this still, 그록 영상, 이매진 비디오, 스틸 움직여줘, first/last frame, reference-to-video, 영상 이어붙이기, 영상 편집, extend/edit a clip), clips from a video MCP on the agent (ZCRE, 지크 MCP), chroma removal, animation atlases, video loops, 큐레이션뷰, image candidates, 팔레트 스왑, palette swap, recolor, engine exports, repeating backgrounds, projected shadows, talking face layers over body motions (말하는 얼굴, 입 맞추기, 얼굴 층, lip sync, face pack), motion/contact inspection and optional scene composition from existing assets."
license: Apache-2.0
depends_on:
  required_bins:
    - name: codex
      why: "gen --provider codex (image_gen via ChatGPT OAuth)"
    - name: ffmpeg
      why: "video-frames (clip -> frames) and video-set"
    - name: img2webp
      why: "video-loop WebP with exact alpha (libwebp); Pillow's animated writer drops -exact"
    - name: node
      why: "face-bake: the face pack's player (sprite-gen-face-v1.mjs) picks the frames"
    - name: rife-ncnn-vulkan
      why: "video-loop jump repair and video-cycle-align (RIFE v4.6 in-betweens with the -x -z test-time augmentation, release 20221029). Install once with `sprite-gen rife install` (sha256-checked, into the user data directory); SPRITE_GEN_RIFE or PATH override it; Linux without a GPU also needs libvulkan1 mesa-vulkan-drivers. Without it, walk and run loops are cut as filmed with a warning. SPRITE_GEN_RIFE_BACKEND=torch runs the same model in PyTorch instead, for a GPU with no Vulkan (`pip install \"sprite-gen[torch]\"`, then `sprite-gen rife install --backend torch`; SPRITE_GEN_RIFE_DEVICE=cuda or cpu; SPRITE_GEN_RIFE_EXECUTOR=module:attr sends its frames to a GPU elsewhere, rife_torch.serve there)"
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

## When codex cannot run — exit 75

A call that names no provider (`gen`, `logo`, `gen-set`, `video-set`'s mid-step redraw) runs on codex. When codex cannot run here — not signed in, or the codex usage of the account used up — and the user's choice for that is to ask (the default), the command generates nothing, prints one stderr line starting `sprite-gen ask codex-unavailable:` followed by JSON, and ends with **exit 75**. That is a question for the user, not a failure: never answer it yourself, never retry without the answer, never switch provider on your own.

Ask it in these words, with the reason from the line's `cause` (`login`: "로그인이 없어요", `usage-limit`: "사용량을 다 썼어요"):

> 지피티(codex)를 지금 쓸 수 없어요 — {이유}. 어떻게 할까요?
> 1. OpenAI API 로 만들기 (호출당 과금)
> 2. 그록으로 만들기
> 3. 멈추기

## Side-view facing

Keep the side still, canvas placement and motion prompt facing the same direction.
`video-set` observes each side input once before canvas placement and uses the requested
`--facing` for both the canvas and clip prompt. Observation is record-only by default;
it never changes the requested direction. Image correction requires explicit opt-in.

Direction is requested through the generation and motion prompts, which cannot guarantee model compliance.
The detector can be wrong even at high confidence: review the still before choosing `mirror` or `regen`.
Generation reports record direction, model, requested direction, model-reported confidence and correction
under `extra.facing`; video reports and batch items use `facing`. `final_direction` is an observation
or a value derived from it, not independent verification; `final_direction_source` identifies which.
An uncertain or failed inspection records `unknown` and its reason and continues without correction;
front-facing observations also remain unchanged. Mirroring does not preserve left/right accessory handedness.

## Execution routes

| Task | Entry | Contract |
|---|---|---|
| GPT image sprites | `prepare`, `gen-set --provider codex`, `extract`, compose and QA | [atlas-workflow](docs/atlas-workflow.md) |
| Standalone video / animate a still, pin a last frame, reference images | `video` (`--image`, `--last-frame`, `--reference`) | [video](docs/video.md) |
| Continue or edit an existing clip | `video-extend`, `video-edit` | [video](docs/video.md) |
| A clip run left unfinished (its line or report names a `request_id`): ask for it again, never submit it again | `video-resume --request-id <id>` | [video](docs/video.md#sprite-gen-video-resume--ask-again-for-an-unfinished-generation) |
| Grok video sprites | `video-set` | [video-pipeline](docs/video-pipeline.md) |
| A soft part of a walk loop or cycle frames sways with the body (the Sway layer): an ellipse after `video-cycle-align`, `--read-on <sha256>` for a cut, `--stretch-floor D` for least area, `--frames` for full frames, `--breathe` for the breath drive, a flat share to move a region whole, `--gain-across` for across gain, and `--reference` for a way in or out of a loop | `video-sway` (old name `video-follow`) | [video-pipeline](docs/video-pipeline.md#6-the-sway-layer--video-sway) |
| Where an ellipse is carried in every other cell, and what the move changes there (an arm crossing a chest moves with it; the stretch, the bend and the seam): a record and boards of every cell, nothing moved; a seam under a third moving region is not counted | `video-sway-inspect` (old name `video-follow-inspect`) | [video-pipeline](docs/video-pipeline.md#inspecting-a-sway--video-sway-inspect) |
| A set's directions on one sheet, standing on one ground line (a 3×3 board, an engine import) | `video-set-sheet` | [video-pipeline](docs/video-pipeline.md#7-one-sheet-for-a-set--video-set-sheet) |
| A walk or run that stops, from the clip's own start played backwards | `video-stop` | [video-pipeline](docs/video-pipeline.md#8-a-walk-or-run-that-stops--video-stop) |
| Build a talking lower-face pack anchored to every frame of body motions from a reference and keyed clips. | `video-face` | [face-parts](docs/face-parts.md) |
| Bake a voice and the pack's face layer onto body frames, optionally after a clip's own mouth and sound. | `face-bake` | [face-parts](docs/face-parts.md#face-bake) |
| Compare the currently displayed loop with a final candidate, including after follow-through | `video-loop-compare` | [loop-comparison](docs/loop-comparison.md) |
| Restore post-processing damage in the current cut from its exact source times, preserving normal cells and playback | `video-source-manifest` → `video-loop-repair` → `video-loop-compare` | [source restoration](docs/loop-comparison.md#restoring-the-active-cut-from-source) |
| Video sprites from a clip your agent makes with a connected video MCP (ZCRE, 지크) | `video-canvas`, `video-prompt --no-last-frame`, the agent's own MCP tools (quote, user approval, generate, save the mp4), then `video-frames` and `video-loop`, and for a walk or run cut in two or more directions `video-cycle-align --view …` over their loop directories. sprite-gen never calls the MCP. | [video-pipeline](docs/video-pipeline.md#a-clip-from-a-video-mcp-on-your-agent--zcre) |
| Typography logo, wordmark or title lettering (로고, 타이포그래피, 타이틀 글자): the title lettered into a title screen or poster first, then only its lettering redrawn on a key and cut out with its glow; the text is read back and a misspelt logo refused | `logo --text … --style …` (`--mood` to start from a picture you have, `--lettering` to cut again for nothing) | [logo](docs/logo.md) |
| Ordinary image or edit | `gen --provider codex` or `gen --provider grok` (the subscription login first; without one grok uses `XAI_API_KEY`, and a codex signed in with an API key bills it — both per call, said on stderr) | [gen](docs/gen.md) |
| Pixel art the model draws directly as a grid, 8–48 px, on your Claude subscription (`claude` signed in), or without one on `ANTHROPIC_API_KEY`, **billed per call** | `pixel-draw` | [pixel-draw](docs/pixel-draw.md) |
| Image generation with no login available (server, container, SaaS) | `gen --provider openai` — server/SaaS route on `OPENAI_API_KEY`, **billed per call**, never a default; it runs only when named, or when the user chose it for a codex that cannot run | [gen](docs/gen.md#subscription-first--openai-runs-only-when-you-chose-it) |
| Base and direction anchors | `anchor` | [directional-anchor-workflow](docs/directional-anchor-workflow.md) |
| Curation view or existing image candidates | `curation`, `unpack-atlas --pngs-dir` | [curation](docs/curation.md) |
| Uniform background removal or imported sheets | `cutout`, `slice-sheet` | [sheet-slicing](docs/sheet-slicing.md) |
| Palette swap | `sprite-gen recolor-palette`, `sprite-gen recolor` | [recolor](docs/recolor.md) |
| Idle breathing as a post-processing layer (the Breathe layer): pixel (default, pixel art) or smooth (large illustrations, video frames: head fixed, chest breathes, shoulders follow; facing values for a turned body); smooth can choose a beat that misses the hands with `--avoid-region`, and a way in or out of a loop carries on its breath with `--loop-cells`, `--start-cell` and `--reference`; pixel can breathe round from the chest with the arms moving sideways (`--chest-round`, `--arms-x`), cells held still (`--hold-mask`), what the arms hold moving whole with them (`--props-mask`), what the arms hold behind the body (a shield hung at the back) moves the same way with the body staying in front (`--props-behind-mask`), and pieces painted one colour move together (`--props-groups colour`, so a sword's reflection follows the sword) | curation sidecar, baked by compose; or `sprite-gen breathe <frames> <out>` on a folder of loop frames | [breathing](docs/breathing.md) |
| Engine exports | `export-aseprite`, `export-pngs` | [engine-export](docs/engine-export.md) |
| Background recipe or repeating tile | existing `gen` / `cutout`, then optional `background-tile` | [asset-tools](docs/asset-tools.md#background-recipe) |
| Ground tile that repeats both ways | `gen` (opaque), then `background-tile --axis xy` | [asset-tools](docs/asset-tools.md#both-axes-a-ground-tile) |
| Game UI kit: panel, buttons, card, HP/XP bar frames and fills, timer badge, item slot, counter plate, on/off dots, joystick base and knob as sliced elements | `gen --transparent --aspect-ratio 2:3 --layout-guide ui-kit` (`1:1`: the seven frames alone), then `ui-kit`; the seven frames are required, and a band element whose box is empty is left out and named under `missing` | [ui-kit](docs/ui-kit.md#missing-elements) |
| Props or icons on a transparent sheet | `slice-sheet --chroma-key none --alpha-floor 64`, props with `--scale sheet --fit contain`, icons with `--fit contain` | [sheet-slicing](docs/sheet-slicing.md#props-and-icons-sheet-scale-and-contain-fit) |
| Painted props, a ground tile and a UI kit in a game pack | `game-pack` request version 2 (`images`, `tiles`, `ui`) | [game-pack](docs/game-pack.md#version-2-images-tiles-and-ui) |
| Standalone projected shadow | `shadow` | [asset-tools](docs/asset-tools.md#projected-shadows) |
| Duplicate poses, foot contact and stride measurement | `inspect-motion` | [asset-tools](docs/asset-tools.md#motion-and-contact-evidence) |
| Optional scene placement, lighting, camera and render | `scene-render`, `scene-inspect` | [scene](docs/scene.md) |
| What the engine can do, for an agent or a tool to read once: every verb and argument, the providers each may call and be billed by (one JSON line, a quarter of every `--help`); a sandbox that reaches the providers through a relay: `SPRITE_GEN_<PROVIDER>_BASE_URL` (`OPENAI`, `XAI`, `ANTHROPIC`, `ELEVENLABS`) moves a provider's calls and the files it hands back, `SPRITE_GEN_LOGINS=off` runs every call on its API key | `capabilities` | [capabilities](docs/capabilities.md), [provider addresses](docs/gen.md#provider-addresses) |
| Defaults | `defaults show`, `defaults save` (`--codex-unavailable ask\|grok\|openai`: what to do when codex cannot run), `defaults clear` | [user-workflow](docs/user-workflow.md#one-settings-owner) |

Say a thing once: what an option says, leave out of the prompt. `--direction`, `--facing`, `--handed`, the key background and `video --direction side` each add their own sentence after your text, in one place. Your prompt carries the subject, its design and its pose. Text that says the opposite of an option ("facing left" with `--facing right`, the item on the other wrist) is sent as written with a warning (`extra.prompt_notes`, `video-prompt`'s `warnings`): fix the prompt, do not send it — [prompt-assembly](docs/prompt-assembly.md).

Use existing automatic pipeline stages for background removal, extraction, alignment and export. Do not ask users to select each script. For a direct utility request, run that utility; no unrelated generation questions are needed. Preserve the row pipeline and component extraction for image sprites. One-shot grid generation and fixed cell cutting are not an alternative sprite-generation route.

For attack repeat coverage, observed one-shot returns and structured loop failure reports, follow [video-pipeline](docs/video-pipeline.md#one-shot-actions----cycle-autoperiodicone-shot).

Every cut is read for held cells (neighbour cells showing the same picture), and by default they are filled with RIFE's frames between and the cut read again (`video-loop --fill-held auto`; `off` cuts as filmed). Tell the user each motion's poses over cells in one line from `held_fill` (`poses/cells`, before and after, e.g. `24/27` to `27/27`). A run whose frames between were all refused stays as filmed and is named in a `warning:` line: say so with its poses over cells. A cut wanted above its clip's rate is given with `--out-fps`, frames between, never repeats. Where the game shows the cells smaller than they were cut (cells drawn at 60 %), pass `--judge-scale` at that share (`0.6`) to `video-loop`, `video-set` or `video-cycle-align`: a frame between refused only for its outline as cut is taken back where its line keeps its ink at the size the game shows it, and every other reading and the cells delivered stay as cut. Then say the scale in the poses line (`27/27 at judge scale 0.6`). Details in [loop repair](docs/loop-repair.md#7-held-cells-a-picture-shown-again-and-a-cut-at-a-higher-rate).

A move played once (a jump, a vault: `once`, `once-return`, `one-shot`) carries its root motion in the strip sidecar by default, the cells unchanged: `root_motion` gives each cell's root (the body's mass centre, cell px), what it stands on, and each air's take-off, landing, seconds and apex; a game reads `root` to place each cell and moves the body on its own physics. A move cut out of a longer motion is named with `video-loop --cycle once --start S --length L`, a hand on something the clip does not draw with `--contact hand=A:B`, metres with `--meters-per-px`; a jump whose body crosses the canvas is named the same way, because it never comes back to a pose it left: the one-shot cut refuses it and its line names the closest return (`the closest return, frames A-B, is R of its departure away`), whose frames are where to start the range. To give the air its own length, gravity and speed, `--air-retime gravity=G,air=T,speed=V` redraws it on that parabola with RIFE's frames between; contacts and the take-off and landing stay as filmed. Tell the user each air in one line, its seconds and its apex (the summary's `air`, e.g. `air 0.42 s, apex 0.21 m`). Details in [video-pipeline](docs/video-pipeline.md#root-motion--a-cut-played-once-where-the-body-is-what-it-stands-on-its-air).

For a move over an obstacle that clears a low wall (such as a vault or a jump), paint a flat magenta plate on the green key with `video-canvas --prop-plate` (which requires `--report`), then pass that canvas report to `video-frames --prop-plate <canvas report json>` to erase the plate from keyed frames. The plate box lives in the canvas report, which `video-frames` reads directly to scale and position the erase box; there is no second copy. If the video model drew the plate off its place by even a single pixel, grey blend lines remain down its sides: tell the user if any keyed frame kept lines of the plate. Details in [video-pipeline](docs/video-pipeline.md#prop-plate---prop-plate).

A pinned idle or attack whose last frame the video model drew again has its wrap blended by default (`video-loop --wrap-blend auto`, a redraw only; `off` cuts as filmed), with details in [loop repair](docs/loop-repair.md#6-the-redrawn-wrap-a-pinned-clip-whose-last-frame-the-model-drew-again).

Scene creation consumes finished assets and remains optional. Asset metadata owns frames, native durations and anchors; scene specs own placement, scale, playback rate, planes, camera and light. Measure stride only with declared same-foot contact and an isolated foot ROI; unknown contact stays unverified. Apply only a verified report for the exact selected asset with an explicit scene direction. Never infer walking direction from the bottommost silhouette, reverse frames or change source assets to make a scene work.

## 실행 인터프리터

`SPRITE_GEN_ROOT` is the absolute installed repository path. Use `$SPRITE_GEN_ROOT/.venv/bin/sprite-gen` or `$SPRITE_GEN_ROOT/.venv/bin/python`; do not assume an activated shell. **폴백 금지**: create a missing venv or report the failure, never use an arbitrary global Python. **NumPy 가 없는 인터프리터** fails at package import. Setup and diagnosis: [interpreter](docs/interpreter.md).

## RIFE for walk and run loops

Without RIFE, tell the user which loops went unrepaired, which motions' held cells were not filled (`held_fill.unmade`, or `--out-fps` not met) and which airs were not retimed (`air_retime.why`), and offer the install; after it, cut those loops again and run `video-cycle-align`. [loop-repair](docs/loop-repair.md).

## Contracts and advanced tools

[run-contract](docs/run-contract.md) owns numeric requests, run layout, atomic publication and curated exports. [architecture](docs/architecture.md) explains domain ownership. [docs index](docs/README.md) lists every specialized feature and QA procedure. `sprite-gen --help` derives the command map from the package catalog. Never replace an engine stage — extraction, the loop cut, set alignment, loop repair — with a script of your own while presenting the result as a pipeline output. Where a stage's result is wrong, use the engine's own options first, keep the engine's result as the comparison, report the defect against the engine, and name in the delivery each stage done by hand and why. A new clip is for what no stage can reach: the drawing, the motion itself, a clip that holds no whole cycle ([loop-review](docs/loop-review.md)) — and a clip the engine names for one: when the alignment report's `retake` lists a loop (reason `held-drawings`: the cycle cut from its clip shows each drawing for two or three frames, the set's length leaves it at under 13 drawings a second, and frames between its drawings could not be made, so the loop halts there; or `filmed-ghost`: a filmed ghost frame has filmed ghosts on both sides, so no clean frame stands there and the loop shows it — `reasons` lists both where both apply), run the engine verbs first as above, then film that direction again and align the set anew rather than hand-painting the gap; tell the user what the reason was and that a new take usually fixes it ([loop-repair](docs/loop-repair.md) section 4).
