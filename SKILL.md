---
name: sprite-gen
version: 1.57.2
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

- [ ] **AI 개입은 raw 생성 한 곳뿐이다.** `raw/<state>.png` 는 중간 산출물이며, 최종 에셋은 반드시 결정론 변환 — `extract_sprite_row_frames.py`(크로마 제거 → 컴포넌트 분리 → 피치 검출/그리드 스냅 → kCentroid → 공유 팔레트 → 셀 배치) — 를 거친다. 같은 입력이면 항상 같은 출력이 나오는 코드 경로만 픽셀 언페이크다.
- [ ] **단순 다운스케일 쇼트컷 금지.** raw 를 PIL `resize()` 한 줄로 줄여 최종 경로에 놓는 것은 픽셀 언페이크 변환이 아니다 — AA 가장자리 열화와 그리드 미정렬이 그대로 남는다. "이번 한 번만 빠르게" 도 금지. 파이프라인 없이 낱장만 변환할 때도 run dir 를 만들어 같은 추출 경로를 태운다.
- [ ] **베이스/앵커가 스타일 SSoT 다 — 도트 런이면 베이스부터 진짜 도트여야 한다.** 조립되는
      프롬프트의 `Style contract:` 기본값은 "첨부한 베이스/앵커 레퍼런스를 그대로 따라라"이고,
      이미지 모델은 첨부 레퍼런스를 프롬프트 텍스트보다 강하게 따른다. 그래서 `fit.pixel_unfake`
      런에 AA/벡터풍 베이스를 붙이면 프롬프트에 "TRUE 32x32 pixel art" 를 적어도 raw 가 도트로
      나오지 않는다 (실사고 2026-07-29 gptaku 아이콘: AA 블롭 베이스 → 비도트 raw → 추출 피치
      x/y 불균일(10.5/9.7) → 실루엣 눌림 반려 3회). 잠금 전에 베이스에서 **픽셀 격자가 실측으로
      검출되는지**(균일 블록 피치, AA 반투명 가장자리 없음) 확인하고, 아니면 베이스부터 다시
      만든다. 프롬프트 문구로 베이스의 스타일을 이기려 하지 마라.
- [ ] **크로마 키는 소재색을 먼저 보고 고른다.** 핑크/보라/자주 소재 → 그린 `#00FF00`, 녹색/청록 식물 → 마젠타 `#FF00FF`. 분기표 SSoT 는 image-gen SKILL.md 최상단 게이트 (상세는 [`docs/chroma-alpha.md`](docs/chroma-alpha.md)).
- [ ] **변환 후 소재색 보존을 검증한다.** 꽃이 희게 탈색됐거나 주요 색이 빠졌으면 키 선택이 소재와 충돌한 것이다 — 로컬 보정이 아니라 키를 바꿔 재생성한다.

## 리네임 게이트 — 어휘/키를 바꿀 때 (BLOCKING)

스키마 키·식별자·라벨을 걸쳐 어휘를 바꾸는 작업(`pixel_perfect` → `pixel_unfake` 류)은
**일괄 치환으로 시작하지 않는다**. 치환은 이름을 바꾸지만 계약은 **층위**로 존재한다:

```text
식별자 → 키 문자열 → 사용자 라벨(en+ko) → 문서 예제 → --help 문구 → 테스트 하니스
```

순서가 정해져 있다 (실사고 2026-07-25/26, plan `sprite-gen/pixel-unfake-rename` — 이 순서를
거꾸로 해서 검증자 리젝트 3라운드가 났다):

- [ ] **구조 단정을 스윕보다 먼저 쓴다.** 판독 SSoT(게이트) 밖에서 그 파일/키를 읽는 프로덕션
      경로가 있으면 실패하는 테스트. **정규식이 아니라 AST** 로 — 실제 회귀 형태는 보통 두
      줄(경로를 변수에 담고 다음 줄에서 읽기)이라 한 줄 정규식은 못 잡는다
      (`tests/test_pixel_unfake_migration.py` 마지막 케이스가 그 형태).
- [ ] **그 단정을 mutant 로 검증한다.** 옛 형태를 일부러 되돌려 실제로 실패하는지 본다. 통과만
      하는 단정은 장식이고, 그걸 근거로 "구조로 닫았다" 고 말하면 거짓 보고가 된다.
- [ ] **판독부는 키 이름만 바꾸지 않고 게이트 뒤로 옮긴다.** 이관 전 데이터에서 그 판독부만
      조용히 틀린 답을 본다 (실측: 리롤이 "언페이크가 꺼져 있다" 며 거짓 거부).
- [ ] **구분자가 바뀌는 치환은 토큰 단위로.** `pixel-perfect` → `pixel unfake` 처럼 하이픈이
      공백이 되는 치환은 argparse 옵션·경로·식별자를 깨뜨린다 (실측: `--fit-pixel unfake` 유령
      옵션 등록 + 은퇴 안내문이 현행 플래그를 은퇴했다고 말하는 자기모순).
- [ ] **은퇴 이름은 조용한 별칭으로 남기지 않는다.** 새 이름을 안내하며 hard error. 그 안내
      문구 자체는 치환 대상에서 제외한다.
- [ ] **순수 리네임 주장은 골든 회귀로 증명한다.** 리네임 전/후 산출물이 바이트 동일한지.


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
- For a pixel-art run (`fit.pixel_unfake`): the base itself is true pixel art — a uniform pixel-block grid is measurably present and edges are hard (no anti-aliased fringe). The style contract delegates style authority to this image, so a non-pixel base structurally produces a non-pixel row.
- Identity matches the character sheet / reference (face, hair, markings, palette, props).
- One clear single idle pose, facing the intended camera, readable silhouette at small size.
- Background is a flat clean chroma-ready fill (or trivially keyable).

If the answer is `n`: generate/iterate base candidates, review each against the criteria above, and re-gate. **Do not run `prepare_sprite_run.py` until a base is locked.** "Good enough for now" is not a pass — drift only grows once the rows start. When the answer is `y`, that exact file becomes the accepted idle anchor for its direction; keep the original generation so the lock decision is auditable, but do not attach it again after the idle anchors have replaced it as row identity truth.

## 실행 인터프리터 — 전역 `python3` 는 이 스킬의 인터프리터가 아니다 (BLOCKING)

이 스킬의 모든 명령은 **레포 루트의 venv 인터프리터**로 실행한다:

```bash
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python <script.py> ...
```

- **부트스트랩은 README quickstart·CI 와 같은 한 줄이다** — `python3 -m venv .venv && .venv/bin/pip install -e .`.
  `.venv` 가 없으면 만든 뒤 실행한다. 다른 경로에 만들었다면 그 인터프리터의 절대경로로 바꿔 쓴다 —
  바뀌면 안 되는 것은 경로가 아니라 **"전역 `python3` 를 쓰지 않는다"** 는 규칙이다.
- **이유**: 의존(Pillow, NumPy)의 SSoT 는 `pyproject.toml` 이고, 그것을 실물로 만드는 곳은 이 venv 하나다.
  전역 `python3` 는 `$PATH` 가 그날 가리키는 아무 인터프리터이고(macOS 에서는 보통 homebrew CPython,
  PEP 668 `EXTERNALLY-MANAGED`), 거기 든 패키지는 손으로 넣은 것이라 선언과 실물이 갈린다. 실제로
  그렇게 갈렸다: homebrew python3 에는 Pillow 만 있고 NumPy 가 없어서, **한 개가 깔려 있다는 이유로
  다 깔린 것처럼 보이는** 상태였다.
- **폴백 금지**: "`.venv` 있으면 그거, 없으면 `python3`" 같은 해석은 두지 않는다 (원칙 6).
  없으면 만들거나 요란하게 실패한다 — 조용히 다른 인터프리터로 도는 경로는 없다.
- **NumPy 가 없는 인터프리터에서는 아무것도 시작하지 않는다**: 진입점은 패키지 import 시점에
  멈추고, 실행한 인터프리터 경로와 위 부트스트랩 명령을 그대로 찍는다. 추출 경로는 바이트 동일
  계약을 지고 있어서 **순수 파이썬 폴백은 없다** — 느리게라도 도는 두 번째 구현을 두면 같은 질문에
  답이 둘이 된다. (게이트 `sprite_gen/_deps.py`, 잠금 `tests/test_numpy_dependency_gate.py`)
- **자식 프로세스는 상속한다**: `heal_run` 과 큐레이션 서버는 자식을 `sys.executable` 로 띄운다.
  즉 부모를 옳은 인터프리터로 띄우면 그 아래는 자동으로 옳고, 반대로 큐레이션 서버를 전역 `python3` 로
  띄우면 그 서버가 부르는 재추출·compose 가 전부 같이 틀린다. 고칠 곳은 **띄우는 순간 한 곳**이다.
- **`sprite-gen <tool>` 은 실재하는 콘솔 스크립트다** (`anchor`, `cutout`, `curation`,
  `migrate-breathe` …). `pip install` 이 venv 의 `bin/` 에 써 넣고 그 shebang 이 **바로 그 venv 의
  인터프리터**를 가리키므로, 이 형식은 인터프리터를 고르는 문제 자체가 없다:

  ```bash
  $ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen <tool> ...
  ```

  - **여기서도 절대경로다** — 맨 `sprite-gen` 이 PATH 에 있는 건 venv 를 활성화했거나 그 환경에
    설치한 셸 안에서뿐이다. `SKILL.md`·`docs/*.md` 는 활성화 없는 셸에서 읽히므로 맨 `python3` 와
    같은 이유로 맨 `sprite-gen` 도 쓰지 않는다 (README quickstart 는 활성화가 앞에 있어 예외).
  - **이 변경 이전에 만든 `.venv` 에는 없다** — `[project.scripts]` 가 없던 시절 설치본이라
    `bin/sprite-gen` 이 안 만들어졌다. `pip install -e .` 를 한 번 다시 돌리면 생긴다.
  - `$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python -m sprite_gen.cli <tool> ...` 는 같은
    `cli:main` 을 부르는 동치 형식이다 — 콘솔 스크립트가 아직 없는 venv 에서 쓴다.
- **레지스터는 파일로 갈린다**: 상대경로 `python3 scripts/...` 형식이 같은 인터프리터를 가리키는 건
  `source .venv/bin/activate` 가 **바로 앞에 적혀 있는** README quickstart 안에서만이다. `SKILL.md` 와
  `docs/*.md` 는 활성화 단계가 없는 셸에서 읽히므로, 절대경로든 상대경로든 **여기서는 상대형을 쓰지
  않는다** — 위 venv 절대경로 형식 하나만 쓴다. (`tests/test_entrypoint_interpreter.py` 가 이 두 파일군에
  대해 잠근다.)

## Script Map

Scripts are explicit pipeline commands, not hidden imports. One job each (stage detail: [`docs/architecture.md`](docs/architecture.md) §2):

- `prepare_sprite_run.py` — write `sprite-request.json`, per-state layout guides, prompts, and empty `raw/` + `frames/` from request truth.
- `extract_sprite_row_frames.py` — read `raw/<state>.png` strips: chroma removal → connected components → transparent frame cells + `frames/frames-manifest.json`.
- **에이전트 주도 호흡** (사용자가 "숨쉬기 적용해서 뽑아줘" 라고만 해도 됨): 호흡은 사이드카 필드라 뷰 없이도 켤 수 있다 — (1) `states.<state>.breathe = {"depth": 0.06, "breaths": 1, "lag": 0.1}` 만 쓰면 된다. **경계는 선언하지 않는다** — `sprite_gen/anatomy.py` 가 검출한다. 큐레이터를 거치면 그 결과가 사이드카 `anatomy` 에 얼려지고(`GET /api/breathe-anatomy`), 뷰 없이 에이전트가 `breathe` 만 쓴 런은 `anatomy` 가 비어 있어 **굽기가 매번 다시 잰다** — 굽기는 사이드카에 쓰지 않는다. 어느 쪽이든 동작한다. **굽기는 얼린 값을 신뢰하지 않는다 — 언제나 자기 기준 프레임에서 다시 잰다** (얼린 값은 큐레이터 프리뷰용 캐시다). 사이드카와 어긋나면 manifest 의 `sidecar_drift` 로 값을 실어 보고한다. 그 캐시가 아직 유효한지는 기준 프레임의 **입력** 지문(원본 파일 스탬프·픽셀편집·변형·변종)으로 판정하고, 어긋나면 큐레이터가 프리뷰·영상 내보내기를 **거부하며 갱신하라고 알린다** — 조용히 낡은 숫자로 그리지 않는다. 사람이 특정 행에 고정하고 싶을 때만 `rigid_row` 를 준다. (에이전트 직접 쓰기는 `load_curation`→`stamp_curation` 도장 경로 필수, 열린 탭은 새로고침 안내 — 함정 상세: [`docs/troubleshooting.md`](docs/troubleshooting.md)), (2) `compose_sprite_gif.py`/`compose_sprite_atlas.py` 가 자동으로 굽는다. 검증: gif-manifest 의 `breathe.phases`. **구 `splits`/`amplitude`/`subpixel` 은 요란하게 거부된다** — 옮기려면 `sprite-gen migrate-breathe <run-dir> --apply`.
- **정지 자세(sit/lie/carry_idle 등) 행 레시피** — 정지 1컷 + 링크 복제 @ 4fps + 허리선 호흡(breaths 3) + 눈 보이는 방향만 깜빡임. **복제 수 = `recommended_breathe_frames(breathe) − 1`** (호흡당 `SMOOTH_CYCLE_FRAMES`=6 프레임 확보; breaths 3 → 총 18컷) — 짧은 루프(옛 11컷)에 다수 호흡을 우겨넣으면 1px 위상이 매 프레임 토글해 진동으로 읽히던 걸 막는다 (수홍 2026-07-24). 깜빡임은 **시퀀스 끝 근처**(맨 끝 아님, 뒤에 눈뜬 rest ≥2)에 배치해 루프 이음새 전에 다시 떠 스냅을 없앤다. 실측 도출 근거·자동 적용 절차·프레임 게이트: [`docs/static-pose-recipe.md`](docs/static-pose-recipe.md) (수홍 확정 2026-07-19, 이징 게이트 2026-07-24).
- 호흡(idle breathing)은 **후처리 레이어**다 (수홍 확정 2026-07-18) — 스크립트가 아니라 curation.json 사이드카 `states.<state>.breathe = {depth, breaths, lag, rigid_row?, anatomy}` 로 선언하고, compose/GIF 가 재생 시퀀스 위에 결정론(봉투 워프, `sprite_gen/breathe.py`)으로 굽는다. 깜빡임 프레임도 그대로 숨쉰다 (프레임 선택과 직교).
  - **변형은 자르지 않고 강도를 떨군다** (2026-07-25 교체): 스프라이트 전체에 연속 변형장을 걸고 그 강도를 강체 경계에서 0 으로 테이퍼한다. `env=0` 인 행은 가로 사상이 항등이고 세로 누적이 정확히 1씩 늘어 **그 구간이 프레임 간 비트 동일**하다 — 눈·입이 몇 도트뿐이라 근사로는 표정이 뭉갠다. 가로는 행 안에서 밀도를 적분하므로 사상이 단조라 접힘이 없고, 날개 같은 부속은 밀리기만 하고 안 늘어난다.
  - **강체 경계는 가슴이 아니라 목이다.** 가슴은 해부학 개념이라 몬스터마다 다르지만 목은 기하학적 병목이라 안정적으로 잡힌다. 얼굴이 몸통에 있으면(버섯·슬라임) 대칭 눈쌍을 찾아 얼굴 아래로 내린다. 병목도 얼굴도 없으면 어깨-기울기로 떨어지고 그 사실이 `anatomy.warnings` 에 남는다.
  - 큐레이션 뷰: 줄 헤더 호흡 체크박스(즉시 on/off) + 라벨 클릭 편집기(실재생 위 **강체 경계 1개 드래그** · 진폭(몸통 높이 %) · 루프당 호흡 횟수 · `auto` 되돌리기 — 즉시 반영, Esc 복원, 최종 굽기 필름스트립). 루프 길이는 시퀀스 그대로 불변이고 위상이 연속값이라 breaths 는 요청 그대로 적용된다 (범위 1~8이고 **정수여야 한다**; 밖이거나 비정수면 조용히 깎지 않고 요란하게 거부한다 — `depth` 0.005~0.20, `lag` 0~0.45 도 같다). 재추출/굽기 대기 없음.
- `interpolate_frames.py` — AI in-between: 두 프레임을 ref 로 물려 **생성형**(codex 기본/grok)으로 중간 프레임을 그려 **테이크**로 기록 (raw 단계 AI — 최종 프레임은 여전히 결정론 추출이 굽는다). 서버 머신의 provider CLI OAuth 를 쓰므로 GUI 버튼도 동작 — 인증 전제와 실측 근거(RIFE 파기): [`docs/frame-interpolation.md`](docs/frame-interpolation.md).
- `compose_sprite_atlas.py` — compose `sprite-sheet-alpha.png` + runtime `manifest.json.frame_layout`.
- `preview_animation.py` — QA previews from extracted frames: contact sheets + state GIFs under `qa/`.
- `compose_selected_cycle.py` — record a human-selected frame subset as a selected-cycle manifest + QA GIF/contact sheet (reads `curation.json` by default; `--frames` overrides).
- `compose_sprite_gif.py` — clean transparent GIF export: single frame set, or `--run-dir` batch (one GIF per state from request fps + `curation.json`) into `<run-dir>/exports/`; called by the webview's Export-GIFs button and the v2 desktop app.
- `inspect_sprite_run.py` — deterministic row inspection for the automatic correction loop: expected vs found frame count, 64-bin RGB histogram similarity, dHash silhouette similarity, motion presence, centroid jitter, and extraction warnings.
- `score_sprite_run.py` — score an inspect report (0-100), preserve the best-candidate rank signal, and turn measured defects into provider-ready correction hints.
- `run_correction_loop.py` — bounded inspect → score → correction-hint loop (max 3 passes by default). It can run as a dry-run verifier without a provider, or call an explicit provider command; missing provider without `--dry-run` fails loudly.
- `gif_utils.py` — shared transparent-GIF writer.
- `curation.py` — curation sidecar SSoT (schema + transform math + the stamping atomic writer) shared by the compose scripts, the anchor CLI, and the webview server so they never drift.
- `sprite_gen/anchor.py` (`sprite-gen anchor`) — direction-anchor SSoT: which curated instance is a direction's identity (human pin > the anchor row's sequence head), the post-processing bake of that one frame, and the `references/anchors/<dir>-anchor-x8.png` derived cache that row generation attaches. Reroll, the generation plan, and the curation view's anchor chip all resolve through it.
- `runio.py` — safe run-dir IO: single-writer lock (`.sprite-gen.lock`) + atomic writes for the extract/compose/export/unpack writers, so parallel agents cannot interleave writes into one character folder.
- `sprite_gen/serve_curation.py` (`sprite-gen curation`) — standalone curation webview for one run dir (works from Claude Code Desktop, the Codex app, or any host with the skill). The `-m sprite_gen.serve_curation` module form and the `scripts/serve_curation.py` wrapper reach the same declaration and the same implementation — three live entry forms, one program (launch forms: [`docs/curation.md`](docs/curation.md)).
- `unpack_atlas_run.py` — inverse of compose: rebuild a curator-ready run dir from a finished sheet (`--grid` > `--manifest` > auto-detect) or import a PNG folder (`--pngs-dir`, with sibling `meta.json` labels/iso grid).
- `export_curated_pngs.py` — export curated frames back to named PNGs with the transform baked in, into `<run-dir>/curated/`; the deliverable for imported still sets.
- `cutout.py` (`sprite-gen cutout`) — background remover for **imported** images (not pipeline output, which is already keyed). Routes on the corner background colour (`--key auto|white|magenta|green`): **white/ivory** → position matte (corner flood-fill keeps interior highlights unholed → decontaminated soft-alpha border + soft erode); **magenta/green key** → reuse the verified `extract.remove_chroma_background` engine as-is (no drift — key colours are absent from objects so its colour-only cut is safe there). `--white-check` writes cyan/magenta/yellow verification composites. No Silent Fallback (leftover non-zero RGB under transparency raises).
- `slice_sheet_cells.py` — slice a multi-figure grid sheet (same character, N expressions/variants in one image) into per-cell standing cuts: v1.13 chroma alpha + centroid cell assignment + merged-figure split/in-cell re-label + neighbour-debris drop + per-cell height normalization + shared feet baseline. For dialogue cut-in portraits (立ち絵), not animation rows. Detail: [`docs/sheet-slicing.md`](docs/sheet-slicing.md).
- `check_visible_magenta.py` — optional screenshot QA guard for visible chroma-key leakage.

## Workflow

0. Pass the **Base Lock Gate** above. Do not start step 1 until a base idle is locked (`y`).

1. Prepare the run:

```bash
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/prepare_sprite_run.py \
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
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/generate_sprite_image.py \
  --provider codex \
  --prompt-file <run>/prompts/<state>.txt \
  --out <run>/raw/<state>.png \
  --ref <run>/base-source.<ext> --ref <run>/references/layout-guides/<state>.png
```

Use `prompts/<state>.txt` as the prompt; save the selected image as `raw/<state>.png`. `--provider` is optional — the default is **codex** (`SPRITE_GEN_DEFAULT_PROVIDER` env overrides it; an observable grok fallback kicks in only if codex is unavailable). Pass `--provider grok` explicitly for the faster backend; codex adheres tighter to negative constraints. Default policy: [`docs/gen.md`](docs/gen.md#default-provider-selection). Keep the request chroma key on the background (extraction removes it). Reference attachment rules:

**생성 동시성 (수홍 확정 2026-07-19)**: 여러 행을 뽑는 배치는 **4동시**로 돌린다 —
`sprite-gen gen` 호출을 최대 4개 병렬 (codex 실측 4병렬까지 스로틀 없음; grok 도 4,
사용자 관측상 6까지 가능하나 기본은 4). 1개씩 직렬은 멀티-행 배치에서 안티패턴.
run-dir 쓰기는 `runio.py` 락이 지키므로 생성(각자 다른 `raw/<state>.png` 출력)은
안전하게 병렬화된다. 이 규칙은 지침이다 — 오케스트레이션 스크립트를 짤 때
`ThreadPoolExecutor(max_workers=4)` 급으로 반영하라.

Generation providers are **engine backends**, not user-facing agents. Selecting
`grok` launches a headless `grok -p` process owned by `GrokProvider`; it does not
require or route through a separate user-facing skill/task. Spawning a visible
worker/agent is the caller's orchestrator concern — out of this engine's scope.
Command chain: [`docs/gen.md`](docs/gen.md#provider-topology).

- Simple/default states (before direction-anchor mode exists): attach exactly two references — `base-source.<ext>` (canonical identity) + `references/layout-guides/<state>.png` (layout only).
- Direction-anchor mode: do **not** attach `base-source.<ext>` to action rows. Attach the accepted target-direction anchor (**a single-pose single image — never a multi-frame idle row**) + the state layout guide; for a paired row also attach the basis row as timing/scale/motion reference only. **Never choose the anchor crop by hand** — ask the pipeline, right before each generation:

```bash
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python -m sprite_gen.cli anchor \
  --run-dir <run> --for-state <state>   # prints the identity ref path (bakes it)
```

  It returns `references/anchors/<dir>-anchor-x8.png` for an action row (the curated anchor frame — pixel edits, transforms, deletions and reordering all baked, upscaled ×8 NEAREST) and `base-source.<ext>` for an anchor row or a non-direction run. The file is a derived cache, so re-run it every time; which frame is the anchor is the human's call (`--pick <state>#<index>`, or the pin button in the curation view) and defaults to the anchor row's curated sequence head. Chain details: [`docs/directional-anchor-workflow.md`](docs/directional-anchor-workflow.md).
- Hatch-pet-style locomotion may attach additional references only when they are part of the row plan, recorded in `qa-notes.md`: original sheet / canonical base (identity support only), a previous gait row such as `raw/running-right.png` (motion rhythm only), or an accepted motion-QA artifact (gait readability support only).

3. Extract frames:

```bash
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/extract_sprite_row_frames.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

This removes the request chroma key, finds connected sprite components, fits each pose into a fresh transparent request-sized cell, and writes `frames/<state>/frame-N.png` plus `frames/frames-manifest.json`.

3.5. (Optional) Curate frames in the webview:

```bash
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen curation \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

Standalone local webview: side-by-side frame compare, select/reject, drag-to-reorder play sequence, non-destructive per-frame transform saved to `curation.json` (originals never rewritten; no sidecar = all frames in order, an explicit default). Usage detail, finished-sheet editing via `unpack_atlas_run.py`, and the standalone image-candidate curation path: [`docs/curation.md`](docs/curation.md).

4. Compose the runtime atlas:

```bash
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/compose_sprite_atlas.py \
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
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen curation \
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

When `safe_margin` is omitted, the default is **proportional**: 9.4% of the cell dimension per axis, floored (256 → 24px, 128 → 12px, rect 192×208 → 18/19px). An explicit request/CLI value is absolute and wins as-is.

**테이크(takes)** — 같은 상태의 후보/보강 스트립은 수동 병합이 아니라 request 로 선언한다:
`"states": { "down_idle": { "frames": 4, ..., "takes": [{ "label": "blink", "frames": 4 }] } }`
+ `raw/<...>.takes/<label>.png`. 추출이 primary 뒤에 이어붙여 한 행의 프레임 풀을 만들고
manifest `labels`("blink#0"…)로 큐레이션 뷰에 표시된다. 계약 상세: `docs/run-contract.md` §2.

**실시간 계약** — `frames/` 는 (raw + request + 엔진)의 파생 캐시다. 큐레이션 뷰·compose·
다운로드가 진입 시 `heal_run` 으로 stale 행을 자동 재유도하므로 "재추출" 을 별도 스텝으로
지시할 필요가 없다 (raw 없는 행은 보존 + 관측 노트). 캐시 키 = 행별 `engine_revision`.

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
`frames/` into an app silently ships the un-edited image and nothing fails (real incident
2026-07-26: 191 hand-edited pixels dropped, reported as "applied"). Stills →
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
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/run_correction_loop.py \
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
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/preview_animation.py \
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
│   ├─ docs/pixel-unfake.md     # fit / pixel_unfake params · plain-twin curator toggle · density refs
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
├─ QA ── "verify motion as motion before reporting done"
│   ├─ docs/qa-motion.md         # Motion Continuity verdict criteria (BLOCKING)
│   └─ docs/locomotion-curation.md  # motion-phase guides · manual selected cycles · clean GIF export
│
└─ TROUBLESHOOTING ── "조용히 이상할 때 먼저 볼 표"
    └─ docs/troubleshooting.md   # 사이드카 스테일 가드/도장 경로 · 두-작성자 충돌 ·
                                 #   provider 무출력 행(env 위생) · 세로 스트립 전멸 · ffmpeg 500
```

Concept taxonomy (which doc owns each term, so agents don't guess):

- `sprite-request.json`, cell, states, takes → run-contract.md §2 · states-and-frames.md
- `run_revision`, `state_revision`, per-state salvage, `curation.stale-*.json` → curation.py + curation.md
- `curation.json` fields (`selected`/`order`/`deleted`/`transforms`/`pixels`/`clones`/`pixel_unfake`/`revision`) → curation.md
- frame **clones** (duplicate instances, `source_frame_index`) → curation.md + compose consumers
- `frame_layout`, `manifest.json` runtime contract → run-contract.md + this SKILL.md "Runtime Contract"
- pixel-unfake `fit`, `.plain.png`/`orig/` twins → pixel-unfake.md
- webview interactions (title-drag reorder, 넣기/빼기 toggle, 2-tier card, custom `data-tip` tooltip) → `sprite_gen/curator/` (도메인 분할 `src/*.js` — 로드 순서 SSoT 는 index.html — + curator.css), described in curation.md
