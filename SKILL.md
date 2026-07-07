---
name: sprite-gen
version: 1.10.1
description: "Generate clean 2D game sprites and animation atlases with a component-row pipeline: base identity, numeric sprite-request SSoT, per-state layout guides, image-gen row strips, chroma-key alpha cleanup, connected-component frame extraction, cell-based atlas composition, QA reports, and runtime manifest frame_layout. Its curation webview also serves ANY image-candidate set (icons, logos, generated drafts) — agent chat can't render images, this can: unpack_atlas_run --pngs-dir import, then serve_curation side-by-side compare/pick. Curation triggers (KR/EN): 큐레이션, 큐레이션뷰, 큐레이션 해줘, 이미지 후보 보여줘/안 보임, 나란히 비교, 골라볼게 띄워줘, curation view, show image candidates side by side, let me pick."
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
    - scripts/curation.py
    - scripts/runio.py
    - scripts/serve_curation.py
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
- [ ] **크로마 키는 소재색을 먼저 보고 고른다.** 핑크/보라/자주 소재 → 그린 `#00FF00`, 녹색/청록 식물 → 마젠타 `#FF00FF`. 분기표 SSoT 는 image-gen SKILL.md 최상단 게이트 (상세는 아래 ## Chroma And Alpha).
- [ ] **변환 후 소재색 보존을 검증한다.** 꽃이 희게 탈색됐거나 주요 색이 빠졌으면 키 선택이 소재와 충돌한 것이다 — 로컬 보정이 아니라 키를 바꿔 재생성한다.

## Script Map

The skill uses scripts as explicit pipeline commands, not as hidden imports. Each script has one job:

- `prepare_sprite_run.py` — prepare a run from request truth: write `sprite-request.json`, per-state layout guides, prompts, and empty `raw/` and `frames/` folders.
- `extract_sprite_row_frames.py` — read generated `raw/<state>.png` strips, remove chroma background, extract connected sprite components, and write transparent frame PNGs plus `frames/frames-manifest.json`.
- `compose_sprite_atlas.py` — compose extracted frames into `sprite-sheet-alpha.png` and runtime `manifest.json.frame_layout`.
- `preview_animation.py` — build QA previews from extracted frames: contact sheets and state GIFs under `qa/`.
- `compose_selected_cycle.py` — record a human-selected frame subset as an explicit selected-cycle manifest plus QA GIF/contact sheet. Reads `curation.json` selection/transform by default; explicit `--frames` overrides it.
- `compose_sprite_gif.py` — export a clean transparent GIF. Single mode: from selected frame PNGs + optional frame order. Batch mode (`--run-dir`): one GIF per state from sprite-request fps + `curation.json` selection/order/transform (curation.py SSoT) into `<run-dir>/exports/`. The curation webview's "GIF 내보내기 / Export GIFs" button calls this via `POST /api/export-gif` (`serve_curation.run_export_gif`), and the v2 desktop app calls it via the `compose_sprite_gif` MCP tool's `run_dir` arg + the `export_curation_gif` Tauri command.
- `gif_utils.py` — shared transparent-GIF writer used by the GIF/QA scripts.
- `curation.py` — shared curation sidecar logic (schema + transform application) used by the compose scripts and the curation webview server. Single source of truth so they never drift.
- `runio.py` — shared safe run-dir IO: the single-writer run-dir lock (`.sprite-gen.lock`) and atomic temp+replace writes used by the extract/compose/export/unpack writers, so two agents (for example Claude Code and Codex in parallel) cannot silently interleave writes into one character folder.
- `serve_curation.py` — launch the standalone curation webview for one run dir (frame compare, select/reject, drag-to-reorder play sequence, non-destructive rotate/scale/move). Standalone so it works from Claude Code Desktop, the Codex app, or any environment where the skill is installed.
- `unpack_atlas_run.py` — inverse of compose: rebuild a curator-ready run dir (per-frame PNGs + synthesized `sprite-request.json`) from a finished sprite sheet, or import a folder of separate PNGs (`--pngs-dir`, e.g. a furniture pack). Layout source priority: explicit `--grid COLSxROWS` > `--manifest` rectangles > auto-detect (default). Auto-detect reads the atlas alpha and clusters content blobs into a grid, so it survives a character's internal transparency on packed sheets. With `--pngs-dir`, a sibling `meta.json` (item names + iso tile/anchor) is carried into the run so the curator can label items and draw the iso ground grid.
- `export_curated_pngs.py` — export curated frames back to named PNGs (the curation transform baked in), keeping each item's original filename. Output goes inside the run dir (`<run-dir>/curated/`, provably writable, cross-platform); the skill never writes elsewhere in your tree. The right deliverable for an imported still set (furniture); the single-atlas `compose_sprite_atlas.py` is the deliverable for animation frames / runtime perf.
- `check_visible_magenta.py` — optional screenshot QA guard for visible chroma-key leakage.

## Standalone Curation View (이미지 후보 큐레이션 — 스프라이트 아님)

"큐레이션(뷰) 해줘 / 이미지 후보 보여줘 / 나란히 비교 / 골라볼게" 로 진입했고 대상이 **애니메이션 프레임이
아니라 임의 이미지 후보군**(아이콘 시안, 로고, 생성 초안)이면, 파이프라인 없이 이 단독 경로만 쓴다.
에이전트 채팅 surface 는 이미지를 못 보여주는 경우가 많다 — 이 웹뷰가 그 표시 수단이다.

```bash
SG=${ALEX_EXTENSIONS_DIR:-$HOME/Documents/workspace/personal/agent-extensions}/sprite-gen
STAGE=$(mktemp -d); mkdir -p "$STAGE/pngs"
cp <후보들> "$STAGE/pngs/"   # 의미 있는 이름으로: 1-hub-cube.png, 2-hook-plug.png ... (timestamp/uuid 파일명 금지)
# 성격이 다른 이미지를 한 통에 붓지 마라 — 하위폴더 = 큐레이터 줄(state) 하나.
# 예: pngs/reference/ (베이스/원본 레퍼런스 칸) + pngs/portraits/ (표정 세트 줄).
# 셀 크기는 전 그룹 공유 최대치이므로, 거대한 레퍼런스는 미리 다른 후보 크기대로 축소해 넣는다.
python3 "$SG/scripts/unpack_atlas_run.py" --pngs-dir "$STAGE/pngs" --out-dir "$STAGE/run" --force
nohup python3 "$SG/scripts/serve_curation.py" --run-dir "$STAGE/run" --lang ko > "$STAGE/server.log" 2>&1 &
sleep 2
PORT=$(lsof -nP -a -p $! -iTCP -sTCP:LISTEN | awk 'END{sub(".*:","",$9); print $9}')   # stdout 버퍼링 때문에 log 대신 lsof 로 포트 확보
curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/"   # 200 = positive proof, 그 후 URL 보고
```

- 사용자 로컬이면 브라우저 자동 오픈이 기본, headless/원격이면 `--no-open` + URL 전달.
- 선택 회수는 `"$STAGE/run/curation.json"` 의 `selected` 인덱스를 파일명으로 역매핑. 비어 있으면 다시 묻는다 — 추측 진행 금지.
- 결정 후 서버 kill + `$STAGE` 정리. 후보가 1장이면 큐레이션이 아니다 — 경로만 보고하고 끝.

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

Optional `fit` object (opt-in; absent means legacy behavior). For pixel-art targets and jitter-free locomotion use:

```json
"fit": { "resample": "kcentroid", "align_x": "foot-centroid", "align_y": "bottom" }
```

- `resample` — `lanczos` (default) | `nearest` | `kcentroid`. `kcentroid` (Astropulse-style dominant-cluster downscale) keeps 1px dark outlines readable when the generated art's implied pixel grid does not match the target cell; `nearest` is crisp but drops off-grid outline pixels; `lanczos` blurs pixel art.
- `align_x` — `bbox-center` (default) | `centroid` | `foot-centroid`. Bbox-centering shifts the body left/right whenever a pose's content bbox width changes (extended arm/leg), which reads as per-frame horizontal jitter. `centroid` aligns the whole-alpha centroid; `foot-centroid` aligns the bottom-20% alpha (the legs), so trailing hair/capes do not pull the body off the cell axis — use it when the runtime mirrors the cell for left/right facing (flip pivots on the leg axis instead of teleporting the body).
- `align_y` — `center` (default) | `bottom`. Bottom pins feet to a shared baseline (`cell_height - safe_margin_y`).

`prepare_sprite_run.py` exposes these as `--fit-resample`, `--fit-align-x`, `--fit-align-y`.

For true pixel-perfect output (game-ready chunky pixel art with intact 1px outlines), use the `pixel_perfect` mode instead of `resample` — it removes ALL non-integer resampling:

```json
"fit": { "pixel_perfect": true, "logical_height": 64, "palette_size": 24, "align_x": "foot-centroid", "align_y": "bottom" }
```

`logical_height` 를 **생략하면 셀 높이와 동일(1:1)** 이 기본이다 — 생성 프롬프트가 "TRUE `<셀>`x`<셀>` pixel grid" 를 명시하는 현행 레시피에서 원본 그리드 해상도를 그대로 따라간다(권장). 더 청키한 저해상 룩을 원할 때만 작은 값을 명시한다: 셀 64 + 로지컬 32 → 2× 청키 픽셀.

Pipeline (unfake.js/pixeldetector-style): 포즈 컴포넌트를 먼저 분리한 뒤 **프레임별** 처리 — 엣지-정렬 스코어링 피치 검출(그리드선 ±w 에 색 경계가 모이는 비율 − 우연 기대치 (2w+1)/p 의 argmax) → 프레임별 검출의 **중앙값을 합의 피치**로(배수 낚임 방지, 아웃라이어는 warning), 검출 확신 미달이면 `fit.pitch_hint`(보통 베이스 검출값) → **위상은 프레임마다** 다시 잡아 grid-snap → conform to `logical_height` (kCentroid) → run-wide shared median-cut palette (`palette_size`, kills frame-to-frame color flicker) → alpha binarization → integer NEAREST upscale into the cell. `detail_bias` (default true) prefers a near-black minority cluster (share ≥ 0.40, luma < 70/255) so eyes and outlines survive dominant voting. The final display scale is `cell_height // logical_height` — e.g. cell 64 + logical 32 → crisp 2× chunky pixels. (폐기된 대안과 그 이유는 `CHANGELOG.md` v1.10.0.)

**Stage ownership (불변).** 픽셀퍼펙트는 **row 추출 단계에서만** 적용한다. 베이스/앵커 생성 단계는 타깃 스타일(픽셀 룩 vs 2D 일러스트 vs 3D/실사풍)을 프롬프트·레퍼런스로 잠글 뿐, 픽셀퍼펙트 후처리를 하지 않는다 — 베이스는 row 의 identity truth 라 가공 없이 원본으로 쓴다. 생성 프롬프트가 "TRUE NxN pixel grid" 를 명시해도 모델이 완벽한 균일 그리드로 그리지는 않으므로 정렬 강제는 여전히 추출 단계 몫이다.

**스타일의 SSoT 는 첨부된 베이스/앵커 이미지다.** 프롬프트 텍스트로 체형·등신·볼살·아웃라인 굵기·디테일 밀도를 재기술하지 마라 — 텍스트가 레퍼런스와 경쟁해 identity 를 되돌린다. 행 프롬프트에는 "첨부 레퍼런스를 정확히 따라라(밀도·비율·아웃라인·팔레트)" + 모션 서술 + 레이아웃/크로마 규칙만 남긴다. `STYLE_DEFAULT` 도 이 원칙으로 고정돼 있다 — 강한 스타일 지시가 필요하면 베이스를 다시 뽑아 확정하는 게 정도다.

**픽셀 밀도는 프롬프트가 아니라 레퍼런스가 지배한다.** image_gen 은 출력 크기가 ~1024px 급 고정이라 "작게 생성"은 불가능하고, "TRUE NxN grid" 문구만으로는 밀도를 못 잠근다. 모델이 실제로 따라가는 것은 **첨부된 스타일 레퍼런스의 픽셀 블록 굵기**다: 진짜 저해상 도트(예: 24~64px 스프라이트를 NEAREST 확대한 것, 실게임 스크린샷)를 붙이면 그 굵기로 그리고, 고해상 가짜-도트(1024px+ 생성물)를 붙이면 그 고밀도를 따라가 로지컬 축소에서 뭉개진다. **규칙: 픽셀 타깃 런의 스타일 레퍼런스는 반드시 타깃 로지컬 해상도급의 진짜 저해상 도트로 준비한다.** 가짜-도트 밖에 없으면 한 번 픽셀퍼펙트로 잠근 결과물을 레퍼런스로 재사용한다. 단, **베이스 raw 가 이미 그리드-인식 생성물이면 그 raw 가 최상의 앵커다** — 픽셀퍼펙트로 잠근 판을 앵커로 재투입하면 이중 열화로 얼굴/디테일이 뭉개진다.

**역할 계약.** AI 개입은 **raw 생성 한 곳뿐**이다 (위 필수 게이트). 픽셀퍼펙트(피치 검출→그리드 스냅→kCentroid→팔레트→아웃라인)는 모델 호출이 없는 **완전 결정론 코드**라 같은 입력이면 항상 같은 출력이다. 에셋 제작의 기본 프로세스 = 변환 후 **큐레이션뷰 자동 런치**, **픽셀퍼펙트 적용 여부는 인간이 체크박스로 결정**. 사용자가 "뷰 생략하고 알아서 픽셀 이미지로" 라고 명시했을 때만 무인 처리한다.

**큐레이터 표시 규칙.** 확대 표시는 항상 nearest(`image-rendering: pixelated`, 패시브) — 안티앨리어싱 확대가 실픽셀 품질을 뭉개 보이게 하는 착시를 막는다. 헤더의 **"픽셀 그리드" 체크박스**는 셀 픽셀 래스터를 카드 위에 오버레이한다(표시 전용, 굽기 무관) — 픽셀이 격자에 딱 떨어지는지 눈으로 검증하는 스텝.

**전/후 쌍둥이 + 큐레이터 선택.** `fit.pixel_perfect` 런에서 추출은 픽셀퍼펙트 결과(`frame-N.png`, canonical)와 함께 **적용 전 쌍둥이 `frame-N.plain.png`** 도 저장한다(같은 스트립을 기존 fit 경로로 셀에 앉힌 결과; 실패 시 warning 으로 관측, plain 만 빠진다). 큐레이션 웹뷰 우측 상단에 **"보기: 적용 후/전" 토글**(표시만 전환)과 **"픽셀퍼펙트 적용" 체크박스**(굽기 결정 → `curation.json.pixel_perfect`)가 뜬다. 체크 해제 시 compose·GIF·PNG export 전부 `.plain.png` 변형을 굽고(`frame_variant`/`frame_filename` 리졸버 — curation.py SSoT), plain 파일이 없으면 조용한 폴백 없이 에러다. report/manifest 에 `frame_variant` 가 기록된다.

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

3.5. (Optional) Curate frames in the webview:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/serve_curation.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

This launches a standalone local webview (no Studio dependency — usable from Claude Code Desktop, the Codex app, or any host with the skill installed). It shows every state's frames side by side so you can compare them in parallel, toggle which frames are selected, drag the ⠿ grip on a card to reorder the play sequence or move it between the two rows — a **sequence** row (the selected play order, saved to `curation.json.selected` and baked left-to-right by compose) and a **candidate pool** row below it (unselected frames, e.g. an extra generated take of the same row); drag a cut from the pool up into the sequence to add it (or a sequence cut down to drop it), and apply a per-frame transform (drag = move, wheel = scale, top handle = rotate, bottom-left handle = shear) when a frame's angle or position is slightly off. A live preview animates the selected frames at the state fps, with play/pause, frame-by-frame stepping, and a 0.25×–4× speed control.

The webview UI is bilingual (English / Korean). Pass `--lang en|ko` to match the user's language (it is also toggleable in the app); default is `en`. For isometric sets imported with `--pngs-dir`, a sibling `meta.json` tile/anchor adds a ground-grid overlay for aligning furniture with the shear handle.

All edits are **non-destructive**: they are saved to `curation.json` in the run dir, and the original `frames/<state>/frame-N.png` files are never rewritten. The compose step bakes `curation.json` deterministically, so any curation decision is reversible by editing or deleting that file. The "Compose 굽기" button re-runs `compose_sprite_atlas.py`.

This step is optional. When there is no `curation.json`, every state uses all extracted frames in order with identity transform — an explicit default, not a silent fallback.

### Editing a finished sprite sheet (no `frames/` source)

When only the combined sheet survives (a deployed asset whose run dir is gone), rebuild a curator-ready run dir with the inverse step before curating:

```bash
# default: auto-detect the grid by reading the atlas alpha
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/unpack_atlas_run.py \
  --atlas <sheet>.png --out-dir <run-dir> --force

# when a manifest carries exact rectangles (position-faithful)
python3 .../unpack_atlas_run.py --manifest <manifest>.json [--direction <dir>] --out-dir <run-dir>

# when a human states the grid, e.g. "8x9"
python3 .../unpack_atlas_run.py --atlas <sheet>.png --grid 8x9 --out-dir <run-dir>
```

The chosen layout source is always reported (`manifest` / `grid-explicit` / `auto-detect`) and stored in `unpack-source.json` for a later writeback. Then point `serve_curation.py` at the new run dir. Auto-detect is the no-instruction default; `--grid` and `--manifest` are position-faithful (they crop full cells), while auto-detect crops each blob's content bbox and centers it in the cell.

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

After the atlas composes (and QA previews exist), launch the webview in the background and report the printed URL to the user — do not wait for them to ask. Curation is where a human accepts or fixes the result, so finishing a run means handing them the open webview, not just file paths.

Multi-agent rules for the auto-launch:

- The server picks a free port per launch (`--port 0` default) and serves exactly one run dir, so several agents curating different characters can each keep a webview open with no port or state conflicts.
- One curator webview per run dir. Two webviews on the same run dir are last-write-wins on `curation.json`; if one is already serving that run dir, reuse its URL instead of launching another.
- Pipeline writes are guarded by a run-dir lock (`.sprite-gen.lock`): extract/compose/export/unpack fail loudly when another sprite-gen process is writing the same run dir. Treat that error as "wait or pick another run dir", not as a retry-until-success loop.
- In a headless/remote session add `--no-open` and give the user the URL; on the user's own machine the default auto-opens their browser.
- Skip the auto-launch only when the user explicitly asked for an unattended batch run.

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

**Choose the chroma away from the subject's dominant hue — do not blindly default to magenta.** The hard key erase is a color-distance ball around the key (`--key-threshold`, default 96): a subject color inside that radius is deleted no matter where it sits. The antialias-fringe cut is **boundary-limited** — only pixels spatially adjacent to the keyed-out background (within `--fringe-reach` peel layers, default 2) are fringe candidates, so key-tinted *interior* subject colors (hot pink / purple under a magenta key) survive; their outermost 1–2 edge pixels, which genuinely blend with the key, are trimmed. Key choice still decides quality:

- Pink / purple / magenta-family subjects (꽃, 씨앗봉투) → use **green** `#00FF00`; their edge blend under a magenta key wastes real silhouette pixels.
- Deep-red / crimson / wine hair or clothing is **magenta-adjacent** (both high R). Use **green** for red/crimson/warm subjects.
- Green/teal/olive subjects → use **magenta**.
- Blue subjects → avoid cyan/blue keys; magenta or green.
- Both pink *and* green in one subject → pick the key farther from the larger/more important material, verify both survive after extraction, and prefer `--chroma-key auto` (distance-scored).

When unsure, let `--chroma-key auto` sample the base (it scores candidates by distance from subject pixels). `auto` now also refuses a key whose nearest subject pixel falls inside the erase radius when a safer candidate exists, records `min_subject_distance` in the request, and warns (stderr) when no candidate clears the subject — so a small but critical feature (eyes, a gem, an ear lamp) under 1% of the pixels is not silently deleted. Only force a key when you know the subject hue is safely far from it. Verify after extraction that the dominant subject color survived — a black-where-it-should-be-colored frame means the key was adjacent to the subject.

`extract_sprite_row_frames.py` owns alpha cleanup for sprite rows. It removes pixels near the chroma key, removes chroma-tinted antialias fringe within the fringe band, clears fully transparent RGB, extracts connected components, and writes fresh transparent cells. This is intentionally closer to hatch-pet than to simple `magick -transparent`.

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
  curation.json            # optional, non-destructive curation sidecar
  sprite-sheet-alpha.png
  sprite-sheet-alpha.report.json
  manifest.json
  qa-notes.md
```

Do not let multiple workers write the same character folder.

### Curation Sidecar (`curation.json`)

`curation.json` is an optional, non-destructive sidecar written by the curation webview (`serve_curation.py`) and consumed by `compose_sprite_atlas.py` and `compose_selected_cycle.py`. It records a human selection plus a per-frame affine transform; the original frame PNGs are never modified.

```json
{
  "version": 1,
  "kind": "sprite-gen-curation",
  "pixel_perfect": true,
  "states": {
    "idle": {
      "selected": [0, 2, 3],
      "order": [0, 2, 3, 1],
      "transforms": {
        "0": { "rotate": 15, "scale": 1.2, "dx": 10, "dy": -8, "flipX": 0 }
      }
    }
  }
}
```

- `pixel_perfect` — top-level, 웹뷰 우측 상단 체크박스. `false` → compose/export 가 적용 전 쌍둥이(`frame-N.plain.png`)를 굽는다. 없거나 `true` → canonical `frame-N.png`. plain 쌍둥이가 없는 런에서는 무의미(비 pixel_perfect fit).
- `selected` — 0-based frame indices in play order. Absent/empty → all extracted frames in order.
- `order` — optional, webview-owned: the full display order (sequence row then candidate-pool row) so reopening the curator restores the exact arrangement of both rows. `compose` / `state_plan` ignore it and key off `selected`.
- `transforms` — keyed by 0-based frame index. `rotate` degrees (counter-clockwise positive, PIL convention), `scale` multiplier about center, `dx`/`dy` pixel offsets in the cell (+x right, +y down), `shx`/`shy` shear, `flipX` (0|1) horizontal mirror. Absent → identity.
- A state missing from the sidecar uses the all-frames identity default.
- The transform is applied at compose time inside the request-sized cell, so atlas geometry never changes. `manifest.json.animation.rows.<state>.frames` reflects the curated frame count, and `manifest.json.curation_applied` records whether a sidecar was used.

`curation.py` owns this schema and the transform math so the server and the compose scripts cannot drift. If a folder exists from a previous run, create a timestamped sibling unless the user explicitly says to replace it.

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

## Related Docs

- [`docs/architecture.md`](docs/architecture.md) — how the scripts realize this contract: pipeline stages, the single-cell geometry model, extraction internals, curation sidecar, runtime manifest, and the hatch-pet comparison. Describes the code as-is; if it disagrees with this SKILL.md, this file wins.
- [`docs/skill-improvement-plan.md`](docs/skill-improvement-plan.md) — SKILL.md / scripts 개선 후보 (quick wins · 중간 · 큰 변경) 우선순위 초안. DRAFT — 본 SKILL.md 의 SSoT 와 충돌하면 본 파일이 우선한다.
