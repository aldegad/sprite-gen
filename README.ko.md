# sprite-gen

[English](README.md) · **한국어**

깔끔한 2D 게임 스프라이트와 애니메이션 아틀라스를 component-row 파이프라인으로 생성하는 Codex/Claude 스킬. 그리고 굽기 전에 프레임을 검토·큐레이션·보정하는 독립 웹뷰를 함께 제공한다.

```text
sprite-request.json → 레이아웃 가이드 + 프롬프트 → image-gen state 행
→ 크로마 알파 → 연결요소 → 투명 프레임
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

## 결과 예시

이 스킬로 생성·큐레이션한 스프라이트 (`claudecy`, `howl`):

<p>
  <img src="docs/claudecy-idle.gif" width="110" alt="claudecy idle" />
  <img src="docs/claudecy-running.gif" width="110" alt="claudecy running" />
  <img src="docs/claudecy-success.gif" width="110" alt="claudecy success" />
  <img src="docs/claudecy-talking.gif" width="110" alt="claudecy talking" />
  <img src="docs/howl-idle.gif" width="110" alt="howl idle" />
  <img src="docs/howl-running.gif" width="110" alt="howl running" />
  <img src="docs/howl-success.gif" width="110" alt="howl success" />
</p>

## 큐레이션 웹뷰

프레임 추출 후, 독립 로컬 웹뷰를 띄워 검토한다 — Studio·프레임워크 의존이 없어 스킬이 설치된 어디서든 동작한다 (Claude Code Desktop, Codex 앱, 일반 터미널).

![큐레이션 웹뷰 — 캐릭터](docs/demo-character.gif)

- state별 **프레임을 나란히 비교**하고, 개별 프레임을 **선택 / 제외**.
- 프레임별 **비파괴 보정**: 드래그=이동, 휠=확대/축소, 상단 핸들=회전, 좌하단 핸들=기울이기(shear). 편집은 `curation.json` 사이드카에 저장되며 — 원본 PNG 는 절대 다시 쓰지 않고, compose 단계가 결과를 deterministic 하게 굽는다. 프리뷰(CSS+canvas)와 굽기가 동일 affine 행렬을 써서, 맞춘 그대로 나온다.
- **라이브 프리뷰**가 선택된 프레임을 state fps 로 애니메이션한다.

### 아이소메트릭 바닥 그리드

아이소 세트의 경우, 웹뷰가 `meta.json` 의 tile/anchor 로 바닥 격자를 깔아준다. shear 핸들로 가구를 다이아몬드 축에 맞출 수 있다.

![큐레이션 웹뷰 — 아이소 가구](docs/demo-furniture.gif)

<img src="docs/curator-iso.png" width="520" alt="아이소 바닥 그리드 오버레이" />

### 언어

웹뷰는 영어·한국어를 지원한다. 띄울 때 `--lang en|ko` 로 지정하거나, 화면 내 토글로 전환한다:

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang ko   # 또는 en
```

## 빠른 시작

```bash
# 1. base 이미지로 run 준비
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. image-gen 으로 state 마다 행 이미지 생성, raw/<state>.png 로 저장
# 3. 프레임 추출
python3 scripts/extract_sprite_row_frames.py --run-dir <run-dir>

# 4. (선택) 웹뷰에서 프레임 큐레이션
python3 scripts/serve_curation.py --run-dir <run-dir>

# 5. 런타임 아틀라스 굽기
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### 완성된 시트 편집

합쳐진 시트만 남아있을 때, 큐레이터용 run 폴더를 되돌려 만든 뒤 큐레이션·내보내기한다:

```bash
# 프레임 복원: 명시 --grid, --manifest 좌표, 또는 알파 자동검출(기본)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # 자동검출
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # 정확한 좌표
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # 개별 PNG 세트 import

# 큐레이션 후, 보정본을 이름별 PNG 로 굽기
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

출력은 입력 옆 `<source>-curator` 폴더로 (찾기 쉽게) 기본 생성된다.

에이전트용 전체 워크플로우와 계약은 [`SKILL.md`](SKILL.md) 에 있다.

## 설치

Codex 스킬 인스톨러 워크플로우에서, 이 저장소를 root 스킬로 설치한다:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

## 출처

component-row 워크플로우는 Apache-2.0 라이선스의 `hatch-pet` 스킬에서 영감을 받았으나, 범용 게임 스프라이트 아틀라스를 대상으로 하며 pet 패키지나 pet 비주얼 에셋을 포함하지 않는다.

## 라이선스

Apache-2.0
