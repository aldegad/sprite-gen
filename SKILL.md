---
name: sprite-gen
version: 1.59.1
description: 'PNG generator first. `sprite-gen gen` defaults to Codex ChatGPT OAuth image_gen, 6-concurrent, optional --ref. Use whenever the user wants stills or any PNG. Do not refuse because the output is not a game sprite. Atlas work is a second pipeline (request SSoT, row strips, chroma, extraction, frame_layout). Curation compares any image-candidate set. Triggers (KR/EN): 이미지 뽑아, 6장 병렬, 지피티로 생성, 코덱스 이미지, 레퍼런스 넣고 생성, sprite-gen gen, 스프라이트, 아틀라스, 큐레이션, recolor, palette swap.'
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
    - scripts/recolor.py
    - scripts/compose_layers.py
modes:
  default: component-row
---

# Sprite Gen

**Route first. Do not mix these up.**

| User asked for | Do this | Do not do this |
|---|---|---|
| 이미지 / still / PNG / 레퍼런스 / 6장 병렬 / 지피티로 생성 | `sprite-gen gen` only. Codex ChatGPT OAuth. 6-concurrent. `--ref` if they gave a sheet. [`docs/gen.md`](docs/gen.md) | Do not say this skill is sprite-only. Do not start `prepare_sprite_run.py`. Do not refuse a still as an incomplete atlas. |
| 게임 스프라이트 / 아틀라스 / idle-walk row | The component-row pipeline below | Do not treat a one-shot master sheet as a finished atlas |

Default provider is Codex. `--provider grok` is the faster backend only.

## Sprite atlas pipeline

`sprite-gen` builds generic game sprite atlases with a `component-row` pipeline:

```text
sprite-request.json -> layout guides + prompts -> image-gen state rows
-> chroma alpha -> connected components -> transparent cells
-> sprite-sheet-alpha.png + manifest.json.frame_layout
```

For **sprite atlas results**, use only the `component-row` pipeline. Do not treat one-shot master sheets, fixed-grid atlas cutting, local drawing, or static fallback as a successful sprite result. A `sprite-gen gen` still is a PNG, not an atlas, and does not have to pass the atlas pipeline.

## 필수 게이트 — AI raw 는 최종 스프라이트 에셋이 아니다 (BLOCKING)

이 체크리스트는 **스프라이트 아틀라스/파이프라인 산출물**에 적용한다. `sprite-gen gen` 낱장 PNG 는 해당 변환 경로를 타지 않는다. 아틀라스 산출이 하나라도 어기면 그 결과물은 실패로 보고한다:

- [ ] **AI 개입은 raw 생성 한 곳뿐이다.** `raw/<state>.png` 는 중간 산출물이며, 최종 에셋은 반드시 결정론 변환 — `extract_sprite_row_frames.py`(크로마 제거 → 컴포넌트 분리 → 피치 검출/그리드 스냅 → kCentroid → 공유 팔레트 → 셀 배치) — 를 거친다. 같은 입력이면 항상 같은 출력이 나오는 코드 경로만 픽셀 언페이크다.
- [ ] **단순 다운스케일 쇼트컷 금지.** raw 를 PIL `resize()` 한 줄로 줄여 최종 경로에 놓는 것은 픽셀 언페이크 변환이 아니다 — AA 가장자리 열화와 그리드 미정렬이 그대로 남는다. "이번 한 번만 빠르게" 도 금지. 파이프라인 없이 낱장만 변환할 때도 run dir 를 만들어 같은 추출 경로를 태운다.
- [ ] **베이스/앵커가 스타일 SSoT 다 — 도트 런이면 베이스부터 진짜 도트여야 한다.** 프롬프트의 `Style contract:` 기본값이 "첨부 레퍼런스를 그대로 따라라" 이고 이미지 모델은 첨부를 텍스트보다 강하게 따른다 — `fit.pixel_unfake` 런에 AA/벡터풍 베이스를 붙이면 "TRUE 32x32 pixel art" 를 적어도 raw 가 도트로 안 나온다. 잠금 전에 **픽셀 격자가 실측으로 검출되는지**(균일 블록 피치, AA 반투명 가장자리 없음) 확인하고, 아니면 베이스부터 다시 만든다. 프롬프트 문구로 베이스의 스타일을 이기려 하지 마라.
- [ ] **크로마 키는 소재색을 먼저 보고 고른다.** 핑크/보라/자주 소재 → 그린 `#00FF00`, 녹색/청록 식물 → 마젠타 `#FF00FF`. 분기표 SSoT 는 image-gen SKILL.md 최상단 게이트 (상세는 [`docs/chroma-alpha.md`](docs/chroma-alpha.md)).
- [ ] **변환 후 소재색 보존을 검증한다.** 꽃이 희게 탈색됐거나 주요 색이 빠졌으면 키 선택이 소재와 충돌한 것이다 — 로컬 보정이 아니라 키를 바꿔 재생성한다.

## 리네임 게이트 — 어휘/키를 바꿀 때 (BLOCKING)

스키마 키·식별자·라벨을 걸쳐 어휘를 바꾸는 작업(`pixel_perfect` → `pixel_unfake` 류)은 **일괄 치환으로 시작하지 않는다** — 계약은 층위(식별자 → 키 문자열 → 사용자 라벨 → 문서 예제 → `--help` → 테스트 하니스)로 존재하고, 순서를 거꾸로 하면 검증자 리젝트가 반복된다(회귀 2026-07-25/26). 순서와 6개 체크박스(구조 단정 먼저 · mutant 검증 · 판독부는 게이트 뒤로 · 토큰 단위 치환 · 은퇴 이름은 hard error · 골든 회귀): [`docs/rename-gate.md`](docs/rename-gate.md).

## Base Lock Gate (Stage 0, BLOCKING)

Identity ownership: **identity truth = accepted idle anchor · motion truth = layout guide (+ paired/basis row) · base truth = only creates the idle anchors, then leaves row inputs.** Full reference-ownership flow and the base re-attach ban: [`docs/architecture.md`](docs/architecture.md) §5.

A weak idle anchor poisons every state — proportions, style, and identity drift compound across all rows. Before any row generation, answer `y`/`n`: **is there an image good enough to lock as the canonical base idle?**

It locks only when **all** hold: full body, nothing cropped · the final proportions and style the user asked for are already correct here (the base defines the target — nothing gets "fixed later" in the rows) · for a `fit.pixel_unfake` run the base itself is true pixel art (uniform pixel-block grid measurably present, hard edges) — the style contract delegates authority to this image, so a non-pixel base structurally produces a non-pixel row · identity matches the character sheet (face, hair, markings, palette, props) · one clear single idle pose, readable silhouette at small size · flat chroma-ready background.

If the answer is `n`, iterate base candidates and re-gate. **Do not run `prepare_sprite_run.py` until a base is locked** — "good enough for now" is not a pass, and drift only grows once the rows start. When it is `y`, that exact file becomes the accepted idle anchor for its direction; keep the original generation so the lock is auditable, but do not attach it again once the idle anchors have replaced it as row identity truth.

## 실행 인터프리터 — 전역 `python3` 는 이 스킬의 인터프리터가 아니다 (BLOCKING)

이 스킬의 모든 명령은 **레포 루트의 venv 인터프리터**로 실행한다:

```bash
export SPRITE_GEN_ROOT=/path/to/sprite-gen
$SPRITE_GEN_ROOT/.venv/bin/python <script.py> ...
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen <tool> ...   # 콘솔 스크립트 (shebang 이 이미 이 venv 를 가리킨다)
```

- **부트스트랩**은 README quickstart·CI 와 같은 한 줄이다 — `python3 -m venv .venv && .venv/bin/pip install -e .`. 다른 경로에 만들었으면 그 인터프리터의 절대경로로 바꿔 쓴다. 바뀌면 안 되는 것은 경로가 아니라 **"전역 `python3` 를 쓰지 않는다"** 는 규칙이다.
- **폴백 금지**: "`.venv` 있으면 그거, 없으면 `python3`" 같은 해석은 두지 않는다(원칙 6). 없으면 만들거나 요란하게 실패한다.
- **NumPy 가 없는 인터프리터에서는 아무것도 시작하지 않는다** — 진입점이 import 시점에 멈추고 실행한 인터프리터 경로와 부트스트랩 명령을 찍는다. 추출 경로는 바이트 동일 계약을 지고 있어 **순수 파이썬 폴백은 없다**. (게이트 `sprite_gen/_deps.py`, 잠금 `tests/test_numpy_dependency_gate.py`)
- **자식 프로세스는 상속한다** — `heal_run` 과 큐레이션 서버는 자식을 `sys.executable` 로 띄운다. 부모를 옳은 인터프리터로 띄우면 그 아래가 전부 옳고, 반대면 전부 같이 틀린다. 고칠 곳은 띄우는 순간 한 곳이다.
- **`SKILL.md`·`docs/*.md` 에서는 상대형 `python3 scripts/...` 를 쓰지 않는다** — 이 문서들은 활성화 없는 셸에서 읽힌다 (`tests/test_entrypoint_interpreter.py` 가 잠근다).

근거(전역 python3 가 왜 선언과 갈리는지, 콘솔 스크립트 부재 케이스, 레지스터별 세칙): [`docs/interpreter.md`](docs/interpreter.md).

## Script Map

Scripts are explicit pipeline commands, not hidden imports — one job each. Stage detail:
[`docs/architecture.md`](docs/architecture.md) §2.

**Pipeline** — `prepare_sprite_run.py` (request truth → guides/prompts/empty `raw/`+`frames/`) ·
`generate_sprite_image.py` (prompt + refs → `raw/<state>.png`) ·
`extract_sprite_row_frames.py` (chroma removal → connected components → frame cells + `frames-manifest.json`) ·
`compose_sprite_atlas.py` (`sprite-sheet-alpha.png` + `manifest.json.frame_layout`).

**Curation & export** — `sprite_gen/serve/serve_curation.py` (`sprite-gen curation`, standalone webview; also blink-compares baked colourways and records `curation.json.recolor.picked`) · `curation.py` (sidecar SSoT: schema + transform math + stamping atomic writer, shared by compose/anchor/webview so they never drift) · `sprite_gen/curate/anchor.py` (`sprite-gen anchor`, direction-anchor SSoT: human pin > anchor-row head, bakes `references/anchors/<dir>-anchor-x8.png`) · `compose_selected_cycle.py` · `compose_sprite_gif.py` · `gif_utils.py` · `export_curated_pngs.py` (→ `<run-dir>/curated/`) · `export_aseprite.py` (`sprite-gen export-aseprite` → [`docs/engine-export.md`](docs/engine-export.md)).

**Optional bakes** — `sprite_gen/effects/recolor.py` (`sprite-gen recolor` / `sprite-gen recolor-palette`, deterministic palette-swap into `<run-dir>/variants/`; exact RGB by default, opt-in tolerance; No Silent Fallback — unused map sources and unmapped passthrough colours are named in `recolor.report.json` → [`docs/recolor.md`](docs/recolor.md)) · `sprite_gen/compose/compose_layers.py` (`sprite-gen compose-layers`, integer-pivot composite stack for a run that declares a **rig**; all-or-nothing, a run without `rig`/`track`/`layers` is refused by name → [`docs/layer-tracks.md`](docs/layer-tracks.md)) · `interpolate_frames.py` (generative in-between recorded as a **take**; final frames are still deterministic extraction → [`docs/frame-interpolation.md`](docs/frame-interpolation.md)).

**호흡(idle breathing)은 스크립트가 아니라 후처리 레이어다** — curation.json 사이드카 `states.<state>.breathe = {depth, depth_x?, breaths, lag, rigid_row?, anatomy}` 로 선언하고 compose/GIF 가 결정론(`sprite_gen/breathe.py`)으로 굽는다. 뷰 없이 에이전트가 `breathe` 만 써도 동작하고(경계는 `sprite_gen/anatomy.py` 가 검출한다 — 선언하지 않는다), **굽기는 얼린 값을 믿지 않고 매번 다시 잰다**. 구 `splits`/`amplitude`/`subpixel` 은 요란하게 거부된다 (`sprite-gen migrate-breathe <run-dir> --apply`). 전부: [`docs/breathe.md`](docs/breathe.md), [`docs/static-pose-recipe.md`](docs/static-pose-recipe.md).

**QA & correction loop** — `preview_animation.py` (contact sheets + state GIFs under `qa/`) ·
`inspect_sprite_run.py` (deterministic row inspection: frame count, RGB histogram, dHash silhouette, motion presence, centroid jitter, extraction warnings) · `score_sprite_run.py` (0-100 + provider-ready correction hints) · `run_correction_loop.py` (bounded inspect → score → hint, max 3 passes; a missing provider without `--dry-run` fails loudly) · `check_visible_magenta.py`.

**Imported (non-pipeline) inputs** — `unpack_atlas_run.py` (finished sheet or `--pngs-dir` folder → curator-ready run dir) · `cutout.py` (`sprite-gen cutout`, background remover routed on the corner colour; No Silent Fallback) · `slice_sheet_cells.py` (multi-figure grid sheet → per-cell standing cuts, 立ち絵 not rows → [`docs/sheet-slicing.md`](docs/sheet-slicing.md)).

**Concurrency** — `sprite_gen/spec/runio.py` gives every writer a single-writer lock (`.sprite-gen.lock`) + atomic writes, so parallel agents cannot interleave writes into one character folder.

## Workflow

Every command runs on the venv interpreter (see the interpreter gate above). Each step's exact
invocation, options, and reference-attachment rules: [`docs/workflow.md`](docs/workflow.md).

0. **Base Lock Gate** (above) — do not start step 1 until a base idle locks (`y`).
1. `prepare_sprite_run.py` — write `sprite-request.json`, per-state layout guides, prompts, empty `raw/`+`frames/`. Directional runs declare `--directions`/`--mirror` here and get the taxonomy layout + `references/generation-plan.json` ([`docs/directional-anchor-workflow.md`](docs/directional-anchor-workflow.md)).
2. `generate_sprite_image.py` — one image per state from `prompts/<state>.txt` into `raw/<state>.png`. Default provider **codex** (`--provider grok` for the faster backend). Batches run **6-concurrent**, not serial (maintainer 2026-07-19, raised 4→6 on 2026-08-22). Attach exactly the identity reference + the state layout guide — in direction-anchor mode never attach `base-source`, and never pick the anchor crop by hand: ask `sprite-gen anchor --run-dir <run> --for-state <state>` right before each generation. Provider topology and default policy: [`docs/gen.md`](docs/gen.md).
3. `extract_sprite_row_frames.py` — chroma removal → connected components → `frames/<state>/frame-N.png` + `frames/frames-manifest.json`.
   3.5. (Optional) `sprite-gen curation --run-dir <run>` — webview compare/select/reorder/transform into `curation.json`; originals are never rewritten ([`docs/curation.md`](docs/curation.md)).
4. `compose_sprite_atlas.py` — `sprite-sheet-alpha.png` + `sprite-sheet-alpha.report.json` + `manifest.json`. `manifest.json.frame_layout` is the runtime SSoT.
   4.5. (Optional) `sprite-gen recolor-palette` → edit spec → `sprite-gen recolor --run-dir <run> --spec <spec>` bakes N colourways into `<run>/variants/` ([`docs/recolor.md`](docs/recolor.md)).
   4.6. (Optional, rig runs only) `sprite-gen compose-layers --run-dir <run>` stacks curated rows into `<run>/layers/` ([`docs/layer-tracks.md`](docs/layer-tracks.md)). A run without `rig`/`track`/`layers` is refused by name, not composed.
5. `sprite-gen curation --run-dir <run> &` — **default closing step.** Finishing a run means handing the human the open webview and reporting its URL, not just file paths. Skip only for an explicitly unattended batch.

## SSoT

Every run starts with `sprite-request.json`. It owns the numeric recipe used by prompts and scripts:

```json
{
  "version": 1,
  "kind": "sprite-gen-request",
  "engine": "component-row",
  "character": { "id": "demo-hero", "description": "same character as the base image" },
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

When `safe_margin` is omitted, the default is **proportional**: 9.4% of the cell dimension per axis, floored (256 → 24px, 128 → 12px, rect 192×208 → 18/19px). An explicit request/CLI value is absolute and wins as-is.

**테이크(takes)** — 같은 상태의 후보/보강 스트립은 수동 병합이 아니라 request 로 선언한다(`states.<state>.takes` + `raw/<...>.takes/<label>.png`); 추출이 primary 뒤에 이어붙이고 manifest `labels` 로 큐레이션 뷰에 뜬다. **실시간 계약** — `frames/` 는 (raw + request + 엔진)의 파생 캐시라 뷰·compose·다운로드 진입 시 `heal_run` 이 stale 행을 자동 재유도한다("재추출" 은 별도 스텝이 아니다). 둘 다 계약 상세: [`docs/run-contract.md`](docs/run-contract.md) §2.

Optional `fit` object (opt-in; absent means legacy behavior), exposed by `prepare_sprite_run.py` as `--fit-*` flags:

- `"fit": { "resample": "kcentroid", "align_x": "foot-centroid", "align_y": "bottom" }` — pixel-art-aware downscale and jitter-free frame alignment. `align_x: "alpha-centroid"` (opt-in, perfectpixel-studio port) aligns the fringe-insensitive alpha-weighted centroid per frame — the strongest anti-jitter anchor for walk/run rows.
- `"fit": { "pixel_unfake": true, "logical_height": 64, ... }` — true pixel-unfake extraction with no non-integer resampling (per-frame pitch detection → grid snap → kCentroid → run-wide shared palette → integer NEAREST). Fully deterministic code, applied at the row-extraction stage only; the style SSoT is the attached base/anchor reference, never prompt text.
- Parameter reference, stage ownership, the pixel-density reference rule, and the before/after plain-twin + curator toggle: [`docs/pixel-unfake.md`](docs/pixel-unfake.md).

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

**Install from `curated/`, never from `frames/`.** `frames/` is pre-curation — the human's
picks, pixel edits and transforms live in `curation.json` and are applied downstream. Copying
`frames/` into an app silently ships the un-edited image and nothing fails. Stills →
`export_curated_pngs.py` then `curated/`. Animation → the composed atlas + manifest.
Contract: [`docs/run-contract.md`](docs/run-contract.md) §2-c.

One worker owns exactly one character folder. The canonical run-dir folder tree — every input/output file and which ones drive the curation view — is owned by [`docs/run-contract.md`](docs/run-contract.md) §2. Do not let multiple workers write the same character folder. The `curation.json` sidecar schema (selected/order/transforms/pixel_unfake) and its folder-collision rule: [`docs/curation.md`](docs/curation.md).

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
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/run_correction_loop.py \
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
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/preview_animation.py \
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

Leaf docs are one link deep from this hub; each owns its tables and this file points rather than restates.
The branch tree (CONTRACT & STRUCTURE · REQUEST AUTHORING · GENERATION · CURATION · COLOURWAYS ·
LAYER TRACKS · ENGINE EXPORT · SPECIALIZED INPUTS · QA · TROUBLESHOOTING) and the concept→owner
taxonomy — which doc owns `curation.json` fields, `frame_layout`, `fit`/`pixel_unfake`, recolor specs,
rig/track/layers, webview interactions — live in [`docs/README.md`](docs/README.md). Walk down the branch
that matches your task instead of scanning the flat `docs/` listing.
