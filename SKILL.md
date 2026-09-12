---
name: sprite-gen
version: 2.2.0
description: "Generates images and game sprites through GPT or Grok with guided provider choices, separate saved defaults, automatic cleanup and optional curation. Handles sprite requests, ordinary image generation/editing, standalone image-to-video clips (i2v, animate this still, 그록 영상, 이매진 비디오, 스틸 움직여줘), chroma removal, animation atlases, video loops, 큐레이션뷰, image candidates, 팔레트 스왑, palette swap, recolor, rig layers and engine exports."
license: Apache-2.0
depends_on:
  required_bins:
    - name: codex
      why: "gen --provider codex (image_gen via ChatGPT OAuth)"
    - name: ffmpeg
      why: "video-frames (clip -> frames) and video-set"
    - name: img2webp
      why: "video-loop WebP with exact alpha (libwebp); Pillow's animated writer drops -exact"
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

Three user entry points: **make sprites**, **make an image**, or **animate a still into a video**. Existing generation, extraction and export tools do the work; the user chooses the result and provider.

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

## Execution routes

| Task | Entry | Contract |
|---|---|---|
| GPT image sprites | `prepare`, `gen-set --provider codex`, `extract`, compose and QA | [atlas-workflow](docs/atlas-workflow.md) |
| Standalone video / animate a still | `video` | [video](docs/video.md) |
| Grok video sprites | `video-set` | [video-pipeline](docs/video-pipeline.md) |
| Ordinary image or edit | `gen --provider codex` or `gen --provider grok` | [gen](docs/gen.md) |
| Base and direction anchors | `anchor` | [directional-anchor-workflow](docs/directional-anchor-workflow.md) |
| Curation view or existing image candidates | `curation`, `unpack-atlas --pngs-dir` | [curation](docs/curation.md) |
| Uniform background removal or imported sheets | `cutout`, `slice-sheet` | [sheet-slicing](docs/sheet-slicing.md) |
| Palette swap | `sprite-gen recolor-palette`, `sprite-gen recolor` | [recolor](docs/recolor.md) |
| Rig layer composition | `sprite-gen compose-layers` | [layer-tracks](docs/layer-tracks.md) |
| Idle breathing | curation choice, baked by compose | [breathing](docs/breathing.md) |
| Engine exports | `export-aseprite`, `export-pngs` | [engine-export](docs/engine-export.md) |
| Defaults | `defaults show`, `defaults save`, `defaults clear` | [user-workflow](docs/user-workflow.md#one-settings-owner) |

Use existing automatic pipeline stages for background removal, extraction, alignment and export. Do not ask users to select each script. For a direct utility request, run that utility; no unrelated generation questions are needed. Preserve the row pipeline and component extraction for image sprites. One-shot grid generation and fixed cell cutting are not an alternative sprite-generation route.

## 실행 인터프리터

`SPRITE_GEN_ROOT` is the absolute installed repository path. Use `$SPRITE_GEN_ROOT/.venv/bin/sprite-gen` or `$SPRITE_GEN_ROOT/.venv/bin/python`; do not assume an activated shell. **폴백 금지**: create a missing venv or report the failure, never use an arbitrary global Python. **NumPy 가 없는 인터프리터** fails at package import. Setup and diagnosis: [interpreter](docs/interpreter.md).

## Contracts and advanced tools

[run-contract](docs/run-contract.md) owns numeric requests, run layout, atomic publication and curated exports. [architecture](docs/architecture.md) explains domain ownership. [docs index](docs/README.md) lists every specialized feature and QA procedure. `sprite-gen --help` derives the command map from the package catalog. Never replace canonical extraction with temporary crop scripts while presenting the result as a pipeline output.
