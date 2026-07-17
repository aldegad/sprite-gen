---
name: sprite-gen
version: 1.56.24
description: "Generate clean 2D game sprites and animation atlases with a component-row pipeline: base identity, numeric sprite-request SSoT, per-state layout guides, image-gen row strips, chroma-key alpha cleanup, connected-component frame extraction, cell-based atlas composition, QA reports, and runtime manifest frame_layout. Its curation webview also serves ANY image-candidate set (icons, logos, generated drafts) — agent chat can't render images, this can: unpack_atlas_run --pngs-dir import, then serve_curation side-by-side compare/pick. Curation triggers (KR/EN): 큐레이션, 큐레이션뷰, 큐레이션 해줘, 이미지 후보 보여줘/안 보임, 나란히 비교, 골라볼게 띄워줘, curation view, show image candidates side by side, let me pick."
license: Apache-2.0
depends_on:
  required_bins:
    - name: codex
      why: "gen --provider codex (image_gen via ChatGPT OAuth)"
    - name: grok
      why: "gen --provider grok (Imagine via xAI OAuth)"
  required_scripts:
    - scripts/prepare_sprite_run.py
    - scripts/generate_sprite_image.py
    - scripts/extract_sprite_row_frames.py
    - scripts/interpolate_frames.py
    - scripts/compose_sprite_atlas.py
    - scripts/preview_animation.py
    - scripts/compose_selected_cycle.py
    - scripts/compose_sprite_gif.py
    - scripts/inspect_sprite_run.py
    - scripts/score_sprite_run.py
    - scripts/run_correction_loop.py
    - scripts/gif_utils.py
    - scripts/curation.py
    - scripts/runio.py
    - scripts/serve_curation.py
    - scripts/slice_sheet_cells.py
    - scripts/unpack_atlas_run.py
    - scripts/export_curated_pngs.py
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

## 필수 게이트 — AI raw 는 최종 에셋이 아니다 (BLOCKING)

이 스킬의 모든 산출물은 아래 체크리스트를 통과해야 한다. 하나라도 어기면 그 결과물은 실패로 보고한다:

- [ ] **AI 개입은 raw 생성 한 곳뿐이다.** `raw/<state>.png` 는 중간 산출물이며, 최종 에셋은 반드시 결정론 변환 — `extract_sprite_row_frames.py`(크로마 제거 → 컴포넌트 분리 → 피치 검출/그리드 스냅 → kCentroid → 공유 팔레트 → 셀 배치) — 를 거친다. 같은 입력이면 항상 같은 출력이 나오는 코드 경로만 픽셀퍼펙트다.
- [ ] **단순 다운스케일 쇼트컷 금지.** raw 를 PIL `resize()` 한 줄로 줄여 최종 경로에 놓는 것은 픽셀퍼펙트 변환이 아니다 — AA 가장자리 열화와 그리드 미정렬이 그대로 남는다. "이번 한 번만 빠르게" 도 금지. 파이프라인 없이 낱장만 변환할 때도 run dir 를 만들어 같은 추출 경로를 태운다.
- [ ] **크로마 키는 소재색을 먼저 보고 고른다.** 핑크/보라/자주 소재 → 그린 `#00FF00`, 녹색/청록 식물 → 마젠타 `#FF00FF`. 분기표 SSoT 는 image-gen SKILL.md 최상단 게이트 (상세는 [`docs/chroma-alpha.md`](docs/chroma-alpha.md)).
- [ ] **변환 후 소재색 보존을 검증한다.** 꽃이 희게 탈색됐거나 주요 색이 빠졌으면 키 선택이 소재와 충돌한 것이다 — 로컬 보정이 아니라 키를 바꿔 재생성한다.

## Base Lock Gate (Stage 0, BLOCKING)

Identity ownership in the row pipeline:

```text
identity truth = accepted idle anchor
motion truth   = layout guide + paired/basis row when needed
base truth     = used only to create idle anchors, then removed from row inputs
```

The full reference-ownership flow (base → idle anchors → base 폐기 → basis/paired rows) and the base re-attach ban live in [`docs/architecture.md`](docs/architecture.md) §5.

A weak idle anchor poisons every state — proportions, style, and identity drift compound across all rows. Before any row generation, answer the gate question `y`/`n`:

> Is there an image good enough to **lock** as the canonical base idle?

The base idle locks only when **all** of these hold:

- Full body, nothing cropped (head to feet inside frame).
- The final proportions and style the user asked for are already correct in this image (for example SD / chibi head-to-body ratio, pixel look, outline weight). The base defines the target — do not plan to "fix it later" in the rows.
- Identity matches the character sheet / reference (face, hair, markings, palette, props).
- One clear single idle pose, facing the intended camera, readable silhouette at small size.
- Background is a flat clean chroma-ready fill (or trivially keyable).

If the answer is `n`: generate/iterate base candidates, review each against the criteria above, and re-gate. **Do not run `prepare_sprite_run.py` until a base is locked.** "Good enough for now" is not a pass — drift only grows once the rows start. When the answer is `y`, that exact file becomes the accepted idle anchor for its direction; keep the original generation so the lock decision is auditable, but do not attach it again after the idle anchors have replaced it as row identity truth.

## Script Map

Scripts are explicit pipeline commands, not hidden imports. One job each (stage detail: [`docs/architecture.md`](docs/architecture.md) §2):

- `prepare_sprite_run.py` — write `sprite-request.json`, per-state layout guides, prompts, and empty `raw/` + `frames/` from request truth.
- `extract_sprite_row_frames.py` — read `raw/<state>.png` strips: chroma removal → connected components → transparent frame cells + `frames/frames-manifest.json`.
- `interpolate_frames.py` — AI in-between: 두 프레임을 ref 로 물려 **생성형**(codex 기본/grok)으로 중간 프레임을 그려 **테이크**로 기록 (raw 단계 AI — 최종 프레임은 여전히 결정론 추출이 굽는다). 서버 머신의 provider CLI OAuth 를 쓰므로 GUI 버튼도 동작 — 인증 전제와 실측 근거(RIFE 파기): [`docs/frame-interpolation.md`](docs/frame-interpolation.md).
- `compose_sprite_atlas.py` — compose `sprite-sheet-alpha.png` + runtime `manifest.json.frame_layout`.
- `preview_animation.py` — QA previews from extracted frames: contact sheets + state GIFs under `qa/`.
- `compose_selected_cycle.py` — record a human-selected frame subset as a selected-cycle manifest + QA GIF/contact sheet (reads `curation.json` by default; `--frames` overrides).
- `compose_sprite_gif.py` — clean transparent GIF export: single frame set, or `--run-dir` batch (one GIF per state from request fps + `curation.json`) into `<run-dir>/exports/`; called by the webview's Export-GIFs button and the v2 desktop app.
- `inspect_sprite_run.py` — deterministic row inspection for the automatic correction loop: expected vs found frame count, 64-bin RGB histogram similarity, dHash silhouette similarity, motion presence, centroid jitter, and extraction warnings.
- `score_sprite_run.py` — score an inspect report (0-100), preserve the best-candidate rank signal, and turn measured defects into provider-ready correction hints.
- `run_correction_loop.py` — bounded inspect → score → correction-hint loop (max 3 passes by default). It can run as a dry-run verifier without a provider, or call an explicit provider command; missing provider without `--dry-run` fails loudly.
- `gif_utils.py` — shared transparent-GIF writer.
- `curation.py` — curation sidecar SSoT (schema + transform math) shared by the compose scripts and the webview server so they never drift.
- `runio.py` — safe run-dir IO: single-writer lock (`.sprite-gen.lock`) + atomic writes for the extract/compose/export/unpack writers, so parallel agents cannot interleave writes into one character folder.
- `serve_curation.py` — standalone curation webview for one run dir (works from Claude Code Desktop, the Codex app, or any host with the skill).
- `unpack_atlas_run.py` — inverse of compose: rebuild a curator-ready run dir from a finished sheet (`--grid` > `--manifest` > auto-detect) or import a PNG folder (`--pngs-dir`, with sibling `meta.json` labels/iso grid).
- `export_curated_pngs.py` — export curated frames back to named PNGs with the transform baked in, into `<run-dir>/curated/`; the deliverable for imported still sets.
- `slice_sheet_cells.py` — slice a multi-figure grid sheet (same character, N expressions/variants in one image) into per-cell standing cuts: v1.13 chroma alpha + centroid cell assignment + merged-figure split/in-cell re-label + neighbour-debris drop + per-cell height normalization + shared feet baseline. For dialogue cut-in portraits (立ち絵), not animation rows. Detail: [`docs/sheet-slicing.md`](docs/sheet-slicing.md).
- `check_visible_magenta.py` — optional screenshot QA guard for visible chroma-key leakage.

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

For hatch-pet-style locomotion, add the cell gate explicitly: `--cell-width 192 --cell-height 208`.

방향 있는 캐릭터(휴머노이드 4/8방향)는 방향 계약을 함께 선언한다: `--directions down,side,up --mirror left=side`.
방향 계약 런의 파일은 **택소노미**(`raw/<dir>/<pose>.png`, `frames/<dir>/<pose>/`, 가이드/프롬프트 동일)로
나뉜다 — 자세가 늘어도 flat 폴더가 비대해지지 않는다. 경로 리졸버 SSoT 는 `sprite_gen/layout.py`,
추출된 프레임의 경로는 frames-manifest `row.files` 가 SSoT 다 (run-contract §2).
base = down 정면 기본자세 하나이고, prepare 가 방향 앵커(`<dir>_idle`) 슬롯을 합성하고 생성 체인 SSoT
(`references/generation-plan.json` — 1단계 앵커는 base 기반, 2단계 행은 자기 방향 앵커 기반, 미러 방향은
생성 생략 계약)를 기록한다. 상세와 좌우 재생성 규칙: [`docs/directional-anchor-workflow.md`](docs/directional-anchor-workflow.md) "Prepare 스캐폴딩".

This writes:

```text
sprite-request.json
base-source.<ext>
references/layout-guides/<state>.png
prompts/<state>.txt
raw/
frames/
```

2. Generate one image per state with the engine's own `gen` command (generation is engine-owned; the `image-gen` skill is now a thin shuttle over this — [`docs/gen.md`](docs/gen.md)):

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/generate_sprite_image.py \
  --provider codex \
  --prompt-file <run>/prompts/<state>.txt \
  --out <run>/raw/<state>.png \
  --ref <run>/base-source.<ext> --ref <run>/references/layout-guides/<state>.png
```

Use `prompts/<state>.txt` as the prompt; save the selected image as `raw/<state>.png`. `--provider grok` is the faster backend; codex adheres tighter to negative constraints. Keep the request chroma key on the background (extraction removes it). Reference attachment rules:

Generation providers are engine backends, not Studio workers. Selecting `grok`
launches a headless `grok -p` agent process owned by `GrokProvider`; it does not
require or route through a separate user-facing skill/task. A visible worker is
created independently with `kuma spawn`. The canonical topology and command chain live in
[`docs/gen.md`](docs/gen.md#provider-and-visible-worker-topology).

- Simple/default states (before direction-anchor mode exists): attach exactly two references — `base-source.<ext>` (canonical identity) + `references/layout-guides/<state>.png` (layout only).
- Direction-anchor mode: do **not** attach `base-source.<ext>` to action rows. Attach the accepted target-direction idle anchor (**a single-pose single image — never a multi-frame idle row**) + the state layout guide; for a paired row also attach the basis row as timing/scale/motion reference only. Chain details: [`docs/directional-anchor-workflow.md`](docs/directional-anchor-workflow.md).
- Hatch-pet-style locomotion may attach additional references only when they are part of the row plan, recorded in `qa-notes.md`: original sheet / canonical base (identity support only), a previous gait row such as `raw/running-right.png` (motion rhythm only), or an accepted motion-QA artifact (gait readability support only).

3. Extract frames:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/extract_sprite_row_frames.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

This removes the request chroma key, finds connected sprite components, fits each pose into a fresh transparent request-sized cell, and writes `frames/<state>/frame-N.png` plus `frames/frames-manifest.json`.

3.5. (Optional) Curate frames in the webview:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/serve_curation.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

Standalone local webview: side-by-side frame compare, select/reject, drag-to-reorder play sequence, non-destructive per-frame transform saved to `curation.json` (originals never rewritten; no sidecar = all frames in order, an explicit default). Usage detail, finished-sheet editing via `unpack_atlas_run.py`, and the standalone image-candidate curation path: [`docs/curation.md`](docs/curation.md).

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

5. Launch the curation webview automatically (default closing step):

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/serve_curation.py \
  --run-dir <target>/assets/generated/sprites/<character-id> &
```

After the atlas composes (and QA previews exist), launch the webview in the background and report the printed URL — finishing a run means handing the human the open webview, not just file paths. Multi-agent launch rules (per-launch free port, one webview per run dir, `.sprite-gen.lock`, `--no-open` for headless): [`docs/curation.md`](docs/curation.md). Skip the auto-launch only for an explicitly unattended batch run.

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

**테이크(takes)** — 같은 상태의 후보/보강 스트립은 수동 병합이 아니라 request 로 선언한다:
`"states": { "down_idle": { "frames": 4, ..., "takes": [{ "label": "blink", "frames": 4 }] } }`
+ `raw/<...>.takes/<label>.png`. 추출이 primary 뒤에 이어붙여 한 행의 프레임 풀을 만들고
manifest `labels`("blink#0"…)로 큐레이션 뷰에 표시된다. 계약 상세: `docs/run-contract.md` §2.

**실시간 계약** — `frames/` 는 (raw + request + 엔진)의 파생 캐시다. 큐레이션 뷰·compose·
다운로드가 진입 시 `heal_run` 으로 stale 행을 자동 재유도하므로 "재추출" 을 별도 스텝으로
지시할 필요가 없다 (raw 없는 행은 보존 + 관측 노트). 캐시 키 = 행별 `engine_revision`.

Optional `fit` object (opt-in; absent means legacy behavior), exposed by `prepare_sprite_run.py` as `--fit-*` flags:

- `"fit": { "resample": "kcentroid", "align_x": "foot-centroid", "align_y": "bottom" }` — pixel-art-aware downscale and jitter-free frame alignment. `align_x: "alpha-centroid"` (opt-in, perfectpixel-studio port) aligns the fringe-insensitive alpha-weighted centroid per frame — the strongest anti-jitter anchor for walk/run rows.
- `"fit": { "pixel_perfect": true, "logical_height": 64, ... }` — true pixel-perfect extraction with no non-integer resampling (per-frame pitch detection → grid snap → kCentroid → run-wide shared palette → integer NEAREST). Fully deterministic code, applied at the row-extraction stage only; the style SSoT is the attached base/anchor reference, never prompt text.
- Parameter reference, stage ownership, the pixel-density reference rule, and the before/after plain-twin + curator toggle: [`docs/pixel-perfect.md`](docs/pixel-perfect.md).

Rectangular generation cells are allowed when the target motion benefits from hatch-pet-style row proportions:

```json
"cell": { "shape": "rect", "width": 192, "height": 208, "safe_margin_x": 18, "safe_margin_y": 16 }
```

The generated row uses the request cell shape. The final atlas is still consumed through `manifest.json.frame_layout`; runtime code must not assume square cells.

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

## Output Contract

One worker owns exactly one character folder. The canonical run-dir folder tree — every input/output file and which ones drive the curation view — is owned by [`docs/run-contract.md`](docs/run-contract.md) §2. Do not let multiple workers write the same character folder. The `curation.json` sidecar schema (selected/order/transforms/pixel_perfect) and its folder-collision rule: [`docs/curation.md`](docs/curation.md).

## Runtime Contract

`manifest.json` must contain:

- `game_input: "sprite-sheet-alpha.png"`
- `degraded_static_fallback: false`
- `animation.rows.<state>` with `frames`, `fps`, `durations_ms`, and `loop`
- `frame_layout.rows.<state>[i]` absolute atlas rectangles

Runtime must sample only the active rectangle. Rendering the whole atlas on one plane, guessing a grid, or showing a raw chroma row is a failed integration.

Frame timing and cell reuse (2026-07-16, Aseprite-JSON 과 동형 패턴):

- `frame_layout.rows.<state>` 는 재생(인스턴스) 순서 그대로이며, 같은 그림으로
  구워지는 복제 인스턴스는 **같은 rect 가 반복**된다 — 텍스처 칸은 고유 굽기당
  하나만 쓴다. 소비자는 지금처럼 프레임 인덱스 → rect 샘플링만 하면 된다.
- `animation.rows.<state>.durations_ms[i]` 가 프레임별 표시 시간의 SSoT 다
  (현재는 fps 등간격으로 채워짐). 배열이 있으면 fps 대신 이것을 따른다 —
  루프딜레이/홀드 프레임은 마지막 프레임 복제(rect 재사용, 텍스처 비용 0)나
  duration 연장으로 표현한다.

Static fallback is allowed only as explicit survival output when generation is blocked. It is not a sprite-gen pass and must not create `sprite-sheet-alpha.png`.

## QA

Automated checks (must all pass before reporting done):

- `frames/frames-manifest.json.ok` is true
- `sprite-sheet-alpha.report.json.ok` is true
- every state has the declared frame count
- no frame is empty or near-opaque background
- no frame has excessive edge pixels or chroma-adjacent pixels
- browser screenshots pass `scripts/check_visible_magenta.py` when used in a game

Automatic correction-loop dry run:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/run_correction_loop.py \
  --run-dir <target>/assets/generated/sprites/<character-id> \
  --states <state> \
  --dry-run
```

This writes `correction-loop.report.json`, per-attempt `inspect.json`, `score.json`,
and `correction-hints.txt`. A real regeneration loop must pass an explicit
provider command; there is no silent fallback generator.
Use `--min-attempts 2` for a live E2E that must exercise at least one provider
regeneration even when the seed candidate already clears the score gate.

### Motion Continuity (BLOCKING)

Static identity QA is not enough — a row can have the right frame count, clean alpha, and consistent identity and still animate as garbage. Build the previews and review motion **as motion**:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/preview_animation.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

The full verdict criteria (cyclic locomotion, loop seam, non-loop gestures, humanoid per-frame anatomy review, independent second opinion) live in [`docs/qa-motion.md`](docs/qa-motion.md). If a row fails motion continuity, **regenerate that row** — do not repair motion by drawing or re-timing frames locally. Record the per-state motion verdict in `qa-notes.md`.

Report:

```text
sprite_gen_done=<character-id>
folder=<absolute folder path>
engine=component-row
files=sprite-request,raw,frames,atlas,manifest
qa_note=<one sentence>
```

## Docs Topology

Leaf docs are one link deep from this hub. The tree groups them by the concern
you are in — walk down the branch that matches your task, don't scan the flat
list. Each doc owns its tables; SKILL.md and the others point rather than restate.

```text
sprite-gen (this SKILL.md = behavior contract + hub)
│
├─ CONTRACT & STRUCTURE ── "what files exist and what each stage promises"
│   ├─ docs/run-contract.md      # pipeline stage I/O table · canonical run-dir folder tree ·
│   │                            #   curation-view display contract · run_revision/HTTP-409 ·
│   │                            #   per-state salvage + stale backup · --pngs-dir import rule
│   └─ docs/architecture.md      # how scripts realize the contract: stages · cell geometry ·
│                                #   idle-anchor ownership flow · extraction internals (SKILL wins on conflict)
│
├─ REQUEST AUTHORING ── "fill sprite-request.json before generating"
│   ├─ docs/states-and-frames.md # which states · frame counts (4/5/6/8/9/12) · Quick Path JSON
│   ├─ docs/pixel-perfect.md     # fit / pixel_perfect params · plain-twin curator toggle · density refs
│   └─ docs/chroma-alpha.md      # chroma key branch table · --chroma-key auto · alpha cleanup
│
├─ GENERATION ── "raw/<state>.png from prompts (the one AI step)"
│   ├─ docs/gen.md               # sprite-gen gen provider CLI · verified PNG/report · image-gen shuttle
│   └─ docs/frame-interpolation.md  # generative in-between (codex/grok) → take raw · auth prereqs · RIFE retire rationale
│
├─ CURATION ── "human/agent picks, edits, and downloads via the webview"
│   └─ docs/curation.md          # webview · curation.json schema (selected/order/transforms/
│                                #   deleted/clones/revision) · per-state salvage · frame CLONES ·
│                                #   standalone image-candidate path · finished-sheet re-edit (unpack)
│
├─ SPECIALIZED INPUTS ── "not the plain animation-row path"
│   ├─ docs/directional-anchor-workflow.md  # directional / 45° anchor chains · hatch-pet locomotion
│   └─ docs/sheet-slicing.md     # multi-figure variant sheet → per-cell standing cuts (立ち絵, not rows)
│
└─ QA ── "verify motion as motion before reporting done"
    ├─ docs/qa-motion.md         # Motion Continuity verdict criteria (BLOCKING)
    └─ docs/locomotion-curation.md  # motion-phase guides · manual selected cycles · clean GIF export
```

Concept taxonomy (which doc owns each term, so agents don't guess):

- `sprite-request.json`, cell, states, takes → run-contract.md §2 · states-and-frames.md
- `run_revision`, `state_revision`, per-state salvage, `curation.stale-*.json` → curation.py + curation.md
- `curation.json` fields (`selected`/`order`/`deleted`/`transforms`/`pixels`/`clones`/`pixel_perfect`/`revision`) → curation.md
- frame **clones** (duplicate instances, `source_frame_index`) → curation.md + compose consumers
- `frame_layout`, `manifest.json` runtime contract → run-contract.md + this SKILL.md "Runtime Contract"
- pixel-perfect `fit`, `.plain.png`/`orig/` twins → pixel-perfect.md
- webview interactions (title-drag reorder, 넣기/빼기 toggle, 2-tier card, custom `data-tip` tooltip) → curator/ (curator.js/css), described in curation.md
