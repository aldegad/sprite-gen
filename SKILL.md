---
name: sprite-gen
version: 2.0.0
description: "Generate clean 2D game sprites and animation atlases with a component-row pipeline: base identity, numeric sprite-request SSoT, per-state layout guides, image-gen row strips, chroma-key alpha cleanup, connected-component frame extraction, cell-based atlas composition, QA reports, and runtime manifest frame_layout. Its curation webview also serves ANY image-candidate set (icons, logos, generated drafts) — agent chat can't render images, this can: unpack_atlas_run --pngs-dir import, then serve_curation side-by-side compare/pick. Palette-swap bake (`sprite-gen recolor`) turns a base sheet + palette map into N colourway sheets; the curation view blink-compares and adopts a pick into curation.json.recolor.picked. Curation triggers (KR/EN): 큐레이션, 큐레이션뷰, 큐레이션 해줘, 이미지 후보 보여줘/안 보임, 나란히 비교, 골라볼게 띄워줘, curation view, show image candidates side by side, let me pick. Recolor triggers (KR/EN): 팔레트 스왑, 팔레트 베이크, 리컬러, 색깔 바꾸기, 컬러웨이, 색 변형, 팔레트 맵, 색갈이, palette swap, recolor, colourway, colorway, bake variants, palette map."
license: Apache-2.0
depends_on:
  required_bins:
    - name: codex
      why: "gen --provider codex (image_gen via ChatGPT OAuth)"
    - name: grok
      why: "gen --provider grok (Imagine via xAI OAuth)"
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

One still → clean 2D game sprites. Three routes, one engine (`sprite_gen/`), one interpreter.

## 라우트 — 무엇을 원하나 → 어디로

| 원하는 것 | 명령 | 계약 |
|---|---|---|
| PNG 스틸 1장 (투명 포함) — 아이콘·초상·컨셉·베이스 | `sprite-gen gen` (codex 기본 / grok) | [`docs/gen.md`](docs/gen.md) — 투명은 프로바이더 전략(codex native · grok chroma) |
| **애니메이션 아틀라스** — 행 파이프라인 (`component-row`) | `prepare` → `gen` → `extract` → `curation` → `compose-atlas` (아래 Workflow) | [`docs/run-contract.md`](docs/run-contract.md), [`docs/architecture.md`](docs/architecture.md) |
| **영상 → 투명 루프 세트** — 상태별 GIF/WebP/스트립 | `video-canvas` → `video` → `video-frames` → `video-loop`, 배치는 `video-set` | [`docs/video-pipeline.md`](docs/video-pipeline.md), [`docs/video.md`](docs/video.md) |
| 이미지 후보 비교·선택 (스프라이트 아님) | `unpack-atlas --pngs-dir` → `curation` | [`docs/curation.md`](docs/curation.md) |
| 가져온 이미지 배경 제거 / 그리드 시트 슬라이스 | `cutout` / `slice-sheet` | Script Map · [`docs/sheet-slicing.md`](docs/sheet-slicing.md) |
| 완성 시트 팔레트 스왑 (컬러웨이) | `recolor-palette` → `recolor` (Workflow 4.5) | [`docs/recolor.md`](docs/recolor.md) |
| 리그 런 레이어 합성 | `compose-layers` (Workflow 4.6) | [`docs/layer-tracks.md`](docs/layer-tracks.md) |

행 파이프라인의 모양:

```text
sprite-request.json -> layout guides + prompts -> image-gen state rows
-> chroma alpha -> connected components -> transparent cells
-> sprite-sheet-alpha.png + manifest.json.frame_layout
```

Use only the `component-row` pipeline for atlases. One-shot master sheets, fixed-grid atlas cutting, local drawing, or static fallback are not a sprite result.

## 실행 인터프리터 (BLOCKING)

모든 명령은 **레포 루트 venv 의 인터프리터**로 실행한다 — 전역 `python3` 는 이 스킬의 인터프리터가 아니다:

```bash
export SPRITE_GEN_ROOT=/path/to/sprite-gen
$SPRITE_GEN_ROOT/.venv/bin/python <script.py> ...      # 래퍼 스크립트
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen <tool> ...         # 콘솔 스크립트 (동치)
```

- 부트스트랩은 README quickstart·CI 와 같은 한 줄: `python3 -m venv .venv && .venv/bin/pip install -e .`. `.venv` 가 없으면 만든다.
- **폴백 금지**: "`.venv` 있으면 그거, 없으면 `python3`" 같은 해석은 없다 — 없으면 만들거나 요란하게 실패한다(원칙 6).
- **NumPy 가 없는 인터프리터에서는 아무것도 시작하지 않는다**: 진입점이 패키지 import 시점에 멈추고 실행한 인터프리터 경로와 부트스트랩 명령을 찍는다. 순수 파이썬 폴백은 없다(추출 경로는 바이트 동일 계약).
- `SKILL.md`·`docs/*.md` 안에서는 절대경로 형식만 쓴다(활성화 없는 셸에서 읽힌다). 자식 프로세스는 `sys.executable` 을 상속하므로 띄우는 순간 한 곳만 옳으면 된다.
- 근거·함정·레지스터 상세: [`docs/interpreter.md`](docs/interpreter.md).

## 필수 게이트 — AI raw 는 최종 에셋이 아니다 (BLOCKING)

- [ ] **AI 개입은 raw 생성 한 곳뿐이다.** `raw/<state>.png` 는 중간 산출물이고 최종 에셋은 결정론 변환(`extract_sprite_row_frames.py`: 크로마 제거 → 컴포넌트 분리 → 피치 검출/그리드 스냅 → kCentroid → 공유 팔레트 → 셀 배치)을 거친다. 같은 입력 = 같은 출력인 코드 경로만 픽셀 언페이크다.
- [ ] **단순 다운스케일 쇼트컷 금지.** raw 를 `resize()` 한 줄로 줄여 최종 경로에 놓지 않는다 — 낱장이라도 run dir 를 만들어 같은 추출 경로를 태운다.
- [ ] **베이스/앵커가 스타일 SSoT 다.** 이미지 모델은 첨부 레퍼런스를 프롬프트보다 강하게 따른다. `fit.pixel_unfake` 런이면 베이스부터 진짜 도트(균일 블록 피치 실측, AA 가장자리 없음)여야 한다 — 프롬프트 문구로 베이스 스타일을 이기려 하지 마라.
- [ ] **크로마 키는 소재색을 먼저 보고 고른다.** 핑크/보라/자주 소재 → 그린 `#00FF00`, 녹색/청록 → 마젠타 `#FF00FF`. 분기표: [`docs/chroma-alpha.md`](docs/chroma-alpha.md). 변환 후 소재색이 빠졌으면 로컬 보정이 아니라 키를 바꿔 재생성한다.
- [ ] **어휘/키 리네임은 일괄 치환으로 시작하지 않는다** — 층위 순서와 mutant 검증: [`docs/rename-gate.md`](docs/rename-gate.md).

## Base Lock Gate (Stage 0, BLOCKING)

```text
identity truth = accepted idle anchor
motion truth   = layout guide + paired/basis row when needed
base truth     = used only to create idle anchors, then removed from row inputs
```

Before any row generation answer `y`/`n`: **is there an image good enough to lock as the canonical base idle?** It locks only when all hold — full body uncropped · final proportions/style already correct (SD 비율, 도트 look, 외곽선 두께 — rows never "fix it later") · pixel-art run ⇒ true pixel art with a measurable grid · identity matches the reference · one clear idle pose facing the intended camera, readable small · flat chroma-ready background. `n` ⇒ iterate base candidates and re-gate; **do not run `prepare_sprite_run.py` until locked.** The locked file becomes the direction's idle anchor and is not re-attached after the anchors replace it. Reference-ownership flow: [`docs/architecture.md`](docs/architecture.md) §5.

## Script Map

One job each, all under `scripts/` (wrappers) ↔ `sprite_gen/<domain>/` (impl). Stage detail: [`docs/architecture.md`](docs/architecture.md) §2.

| Script / verb | Job | Doc |
|---|---|---|
| `prepare_sprite_run.py` | request → `sprite-request.json`, layout guides, prompts, empty `raw/`+`frames/` | run-contract §2 |
| `generate_sprite_image.py` (`gen`) | one still via codex `image_gen` / grok Imagine → verified PNG (+ transparent strategy) | [`docs/gen.md`](docs/gen.md) |
| `gen_set.py` (`gen-set`) | every state row of a prepared run, N at a time — identity ref from the run (base or accepted anchor), one report per row, `table.md`, non-zero exit on any failure | [`docs/gen.md`](docs/gen.md) |
| `generate_sprite_video.py` (`video`) | one still → verified mp4 via Grok Imagine, user's own login / `XAI_API_KEY` | [`docs/video.md`](docs/video.md) |
| `video_canvas.py` · `video_frames.py` · `video_loop.py` · `video_set.py` | state canvas (jump tall / attack wide / else square) · ffmpeg + keying · true-period cycle → strip/GIF/WebP · directions × states batch | [`docs/video-pipeline.md`](docs/video-pipeline.md) |
| `extract_sprite_row_frames.py` (`extract`) | `raw/<state>.png` → chroma removal → components → transparent cells + `frames/frames-manifest.json` | run-contract · [`docs/pixel-unfake.md`](docs/pixel-unfake.md) |
| `interpolate_frames.py` | generative in-between (codex/grok) recorded as a take | [`docs/frame-interpolation.md`](docs/frame-interpolation.md) |
| `compose_sprite_atlas.py` (`compose-atlas`) | `sprite-sheet-alpha.png` + runtime `manifest.json.frame_layout` (breathing baked as a post-process layer) | run-contract · [`docs/breathing.md`](docs/breathing.md) |
| `compose_sprite_gif.py` · `compose_selected_cycle.py` · `preview_animation.py` | clean transparent GIFs · selected-cycle manifest · QA contact sheets/GIFs | [`docs/locomotion-curation.md`](docs/locomotion-curation.md), [`docs/qa-motion.md`](docs/qa-motion.md) |
| `export_aseprite.py` (`export-aseprite`) | Aseprite-compatible JSON for Phaser / Flame | [`docs/engine-export.md`](docs/engine-export.md) |
| `inspect_sprite_run.py` · `score_sprite_run.py` · `run_correction_loop.py` | deterministic row inspection → 0-100 score → bounded correction loop (explicit provider command; no silent generator) | QA below |
| `curation.py` · `serve_curation.py` (`curation`) · `sprite_gen/curate/anchor.py` (`anchor`) | sidecar SSoT + stamping writer · webview · direction-anchor SSoT and `references/anchors/<dir>-anchor-x8.png` | [`docs/curation.md`](docs/curation.md), [`docs/directional-anchor-workflow.md`](docs/directional-anchor-workflow.md) |
| `runio.py` | single-writer lock (`.sprite-gen.lock`) + atomic writes; parallel agents never interleave one character folder | run-contract |
| `recolor.py` (`recolor` / `recolor-palette`) | deterministic palette-swap bake → `variants/` + report (exact match; opt-in tolerance) | [`docs/recolor.md`](docs/recolor.md) |
| `compose_layers.py` (`sprite-gen compose-layers`) | rig runs only: curated rows stacked by integer pivots + masks → `layers/` (all-or-nothing) | [`docs/layer-tracks.md`](docs/layer-tracks.md) |
| `unpack_atlas_run.py` (`unpack-atlas`) · `export_curated_pngs.py` (`export-pngs`) | finished sheet / PNG folder → curator-ready run · curated frames → named PNGs (`curated/`) | [`docs/curation.md`](docs/curation.md) |
| `cutout` · `slice_sheet_cells.py` (`slice-sheet`) · `dev/check_visible_magenta.py` | imported-image background removal (white matte / chroma engine) · multi-figure sheet → per-cell cuts · screenshot chroma-leak guard | [`docs/sheet-slicing.md`](docs/sheet-slicing.md) |

Breathing (idle) and static-pose rows are a **post-process layer** declared in `curation.json` (`states.<state>.breathe`) and baked by compose — never a script step; the contract (anatomy detection, editor, rigid boundary, `migrate-breathe`) is [`docs/breathing.md`](docs/breathing.md) and [`docs/breathing.md`](docs/breathing.md) "정지 자세 행 레시피".

## Workflow (atlas route)

0. Pass the **Base Lock Gate**.

1. Prepare the run:

```bash
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/prepare_sprite_run.py \
  --out-dir <target>/assets/generated/sprites/<character-id> \
  --character-id <character-id> --base-image /absolute/path/to/base.png \
  --description "<short identity note>" --force
```

Hatch-pet-style locomotion adds the cell gate (`--cell-width 192 --cell-height 208`). Directional characters declare the direction contract (`--directions down,side,up --mirror left=side`); files then follow the taxonomy `raw/<dir>/<pose>.png`, `frames/<dir>/<pose>/` (path resolver SSoT `sprite_gen/layout.py`, frame paths SSoT = frames-manifest `row.files`) and `prepare` records the generation chain in `references/generation-plan.json` — [`docs/directional-anchor-workflow.md`](docs/directional-anchor-workflow.md). Writes `sprite-request.json`, `base-source.<ext>`, `references/layout-guides/<state>.png`, `prompts/<state>.txt`, `raw/`, `frames/`.

2. Generate the rows (the one AI step; the `image-gen` skill is a thin shuttle over this). The batch form is the default — `$SPRITE_GEN_ROOT/.venv/bin/sprite-gen gen-set --run-dir <run>` generates every non-mirrored state 6 at a time with the run's own identity ref and writes `reports/gen-set/table.md`; one row by hand:

```bash
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/generate_sprite_image.py \
  --provider codex --prompt-file <run>/prompts/<state>.txt --out <run>/raw/<state>.png \
  --ref <run>/base-source.<ext> --ref <run>/references/layout-guides/<state>.png
```

- `--provider` is optional: default codex (`SPRITE_GEN_DEFAULT_PROVIDER` overrides; observable grok fallback only if codex is unavailable). Rows keep the request chroma key and are generated **without** `--transparent`; standalone stills use `--transparent` (codex native alpha first, grok chroma) — [`docs/gen.md`](docs/gen.md).
- References: default states attach exactly two — `base-source.<ext>` + the state layout guide. Direction-anchor mode attaches the accepted anchor instead of the base: **never pick the anchor crop by hand**, ask `$SPRITE_GEN_ROOT/.venv/bin/python -m sprite_gen.cli anchor --run-dir <run> --for-state <state>` right before each generation (derived cache, re-run every time; the human pins which frame). Extra motion references only when recorded in `qa-notes.md`.
- **Concurrency (lead-verified 2026-08, no throttling at 6)**: multi-row batches run **6 at a time** — that is `gen-set`'s default `--concurrency`; serial one-by-one is an anti-pattern. `runio.py` locks make parallel `raw/<state>.png` writes safe. Providers are engine backends, not agents — no worker surface is spawned ([`docs/gen.md`](docs/gen.md#provider-topology)).

3. Extract frames — chroma removal, connected components, one transparent request-sized cell per pose, `frames/<state>/frame-N.png` + `frames/frames-manifest.json`:

```bash
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/extract_sprite_row_frames.py --run-dir <run>
```

3.5. (Optional) Curate in the webview — compare, select/reject, reorder, non-destructive transforms in `curation.json` (originals never rewritten): `$SPRITE_GEN_ROOT/.venv/bin/sprite-gen curation --run-dir <run>` — [`docs/curation.md`](docs/curation.md).

4. Compose the runtime atlas — `sprite-sheet-alpha.png`, `sprite-sheet-alpha.report.json`, `manifest.json` (`frame_layout` is the runtime SSoT; game code consumes rectangles, never recovers them from alpha):

```bash
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/compose_sprite_atlas.py --run-dir <run>
```

4.5. (Optional) Colourways: `$SPRITE_GEN_ROOT/.venv/bin/sprite-gen recolor-palette --base <run>/sprite-sheet-alpha.png --out <run>/palette.draft.json` then `$SPRITE_GEN_ROOT/.venv/bin/sprite-gen recolor --run-dir <run> --spec <run>/recolor.spec.json` — exact RGB by default, opt-in tolerance, every unused/unmapped colour named in the report — [`docs/recolor.md`](docs/recolor.md).

4.6. (Optional, rig runs only) `$SPRITE_GEN_ROOT/.venv/bin/sprite-gen compose-layers --run-dir <run>` bakes the declared stacks into `layers/`; `--names a,b` bakes a subset — [`docs/layer-tracks.md`](docs/layer-tracks.md).

5. Close by launching the webview in the background and reporting its URL (`… sprite-gen curation --run-dir <run> &`) — finishing a run means handing the human the open view, not file paths. Multi-agent launch rules (free port, one view per run, `--no-open` headless): [`docs/curation.md`](docs/curation.md). Skip only for an explicitly unattended batch.

## Video → loop route

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen video-set \
  --base side=<still.png> --states idle,walk,run,jump,attack --out-dir <set-dir> \
  --character "<subject phrase>"
```

Per item: `video-canvas` (the still padded into the state's canvas — the API ignores `aspect_ratio`, the input frame decides) → `video` (in-place, evenly paced, returns-to-start prompt) → `video-frames` (ffmpeg + cutout keying; edge contact fails loud) → `video-loop` (global period first, then best seam; for `jump`/`attack` a clip that performs the action once gets a recorded one-shot cut, `cycle.kind` in the report; strip + `body_h` meta, 1-bit GIF, `img2webp -exact` WebP, seam gate). Starts are staggered for the 2 req/s team quota, a 429 retries once or twice, every item reports and `table.md` lists failures by name. Render strips at 24 fps (one cell per frame). Contract and the measurements behind each rule: [`docs/video-pipeline.md`](docs/video-pipeline.md).

## SSoT

Every atlas run starts with `sprite-request.json` — it owns the numeric recipe prompts and scripts read (`cell` size/shape/safe margin, `chroma_key`, `states.<state>.{frames,fps,loop,action}`, optional `takes`, optional `fit`). `256` is a default variable; change it in the request and regenerate guides, prompts, extraction and atlas from the same request. Default `safe_margin` is proportional (9.4 % of the cell, floored). Rectangular cells (`"shape": "rect"`) are allowed; runtime never assumes square. `frames/` is a derived cache of (raw + request + engine) healed on entry (`heal_run`), so "re-extract" is never a separate instruction. Schema, takes, revisions: [`docs/run-contract.md`](docs/run-contract.md) §2; states and frame counts: [`docs/states-and-frames.md`](docs/states-and-frames.md); `fit` / `pixel_unfake`: [`docs/pixel-unfake.md`](docs/pixel-unfake.md).

## Prompt Contract

The row prompt comes from `prompts/<state>.txt` — never hand-write frame counts elsewhere. It carries the exact frame count, one full-body pose per invisible request-sized slot, the safe margin, the locked anchor identity, motion-only responsibility, the flat chroma-key background, and the ban on shadows/glows/smears/speed lines/scenery/text/UI/guide boxes/detached effects. Guide boxes, labels, overlaps, backgrounds, cropped bodies or identity drift ⇒ **regenerate the row**; never repair generation by drawing or tiling locally.

## Output Contract

**Install from `curated/`, never from `frames/`** — picks, pixel edits and transforms live in `curation.json` and are applied downstream (stills → `export_curated_pngs.py`; animation → atlas + manifest). One worker owns one character folder; the canonical run-dir tree is [`docs/run-contract.md`](docs/run-contract.md) §2, the sidecar schema [`docs/curation.md`](docs/curation.md).

## Runtime Contract

`manifest.json` carries `game_input: "sprite-sheet-alpha.png"`, `degraded_static_fallback: false`, `animation.rows.<state>` (`frames`, `fps`, `durations_ms`, `loop`) and `frame_layout.rows.<state>[i]` absolute rectangles. Runtime samples only the active rectangle. `frame_layout` is in playback order and duplicate instances **repeat the same rect**; `durations_ms[i]` is the per-frame timing SSoT (holds = rect reuse or a longer duration). Static fallback is explicit survival output only — it never creates `sprite-sheet-alpha.png`.

## QA

All must pass before reporting done: `frames/frames-manifest.json.ok` · `sprite-sheet-alpha.report.json.ok` · declared frame count per state · no empty or near-opaque-background frame · no excessive edge/chroma-adjacent pixels · `scripts/check_visible_magenta.py` on game screenshots when used.

Correction loop dry run: `$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/run_correction_loop.py --run-dir <run> --states <state> --dry-run` (writes `correction-loop.report.json`, per-attempt `inspect.json` / `score.json` / `correction-hints.txt`; a live loop needs an explicit provider command, `--min-attempts 2` forces one regeneration).

### Motion Continuity (BLOCKING)

Static identity QA is not enough. Build previews (`$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/preview_animation.py --run-dir <run>`) and judge motion **as motion** by [`docs/qa-motion.md`](docs/qa-motion.md); a failing row is regenerated, never re-timed locally. Record the per-state verdict in `qa-notes.md`.

Report:

```text
sprite_gen_done=<character-id>
folder=<absolute folder path>
engine=component-row
files=sprite-request,raw,frames,atlas,manifest
qa_note=<one sentence>
```

## Docs Topology

The documentation index is [`docs/README.md`](docs/README.md): every leaf doc once, under the four pipelines (A atlas rows · B video → loop · C utilities · D post-processing) and the taxonomy branches, each with a one-line owner. Leaf docs are one link deep; each owns its tables and this hub only points. Architecture with the domain and pipeline diagrams: [`docs/architecture.md`](docs/architecture.md).

Concept ownership: `sprite-request.json`/cell/states/takes → run-contract §2 · states-and-frames; `run_revision`/salvage/`curation.stale-*.json` and every `curation.json` field (`selected`/`order`/`deleted`/`transforms`/`pixels`/`clones`/`pixel_unfake`/`revision`/`recolor.picked`) → curation.md; frame **clones** → curation.md + compose; `frame_layout` runtime contract → run-contract + "Runtime Contract" above; `fit`/pixel-unfake twins → pixel-unfake.md; recolor spec/report/`variants/` → recolor.md; `rig`/`track`/`layers` → layer-tracks.md; video canvas/period/seam/strip meta → video-pipeline.md; webview interactions → `sprite_gen/curator/` described in curation.md + recolor.md.
