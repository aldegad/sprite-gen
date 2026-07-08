<p align="center">
  <img src="docs/claudecy-idle.gif" width="110" alt="claudecy idle" />
  <img src="docs/claudecy-running.gif" width="110" alt="claudecy running" />
  <img src="docs/claudecy-success.gif" width="110" alt="claudecy success" />
  <img src="docs/claudecy-talking.gif" width="110" alt="claudecy talking" />
  <img src="docs/howl-idle.gif" width="110" alt="howl idle" />
  <img src="docs/howl-running.gif" width="110" alt="howl running" />
  <img src="docs/howl-success.gif" width="110" alt="howl success" />
</p>

<h1 align="center">sprite-gen</h1>

<p align="center"><b>그림 하나를 넣으면, 게임에 바로 쓸 수 있는 스프라이트 아틀라스가 나옵니다.</b></p>

<p align="center">

**English** · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-Hans.md) · [Español](README.es.md) · [Français](README.fr.md)

</p>

---

이미지 모델에게 "sprite sheet"를 요청해 본 적 있다면 결과가 어떤지 알 것입니다. 프레임마다 얼굴이 바뀌는 캐릭터, 키아웃되지 않는 배경, 서로 겹치고 그리드에서 밀려나는 포즈, 그리고 게임 엔진이 실제로 먹을 수 없는 PNG. 귀여운 데모지만 쓸모없는 에셋입니다.

`sprite-gen`은 그 간극을 메우는 Codex/Claude skill입니다. **기본 이미지 하나**와 액션 목록을 주면, 행 단위로 생성을 진행하고, 캐릭터 정체성을 고정하며, 크로마 배경을 실제 알파로 제거하고, 각 포즈를 깔끔한 투명 프레임으로 추출한 뒤, **기계가 읽을 수 있는 `manifest.json.frame_layout`**이 포함된 런타임 아틀라스로 굽습니다. 위의 모든 스프라이트가 이 방식으로 만들어졌습니다.

그리고 생성이 결코 완벽히 맞히지 못하는 마지막 10%를 위해 **큐레이션 웹뷰**가 있습니다. 프레임을 나란히 비교하고, 망가진 프레임을 거절하고, 회전/스케일/위치를 비파괴적으로 살짝 조정하고, 루프를 실시간으로 확인한 뒤 굽습니다. 파이프라인이 노동을 맡고, 당신은 취향을 지킵니다.

```text
sprite-request.json → layout guides + prompts → image-gen state rows
→ chroma alpha → connected components → transparent frames
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

<p align="center">
  <img src="docs/architecture-diagram.png" width="640" alt="sprite-gen architecture — component-row pipeline" />
</p>

> 전체 아키텍처: [`docs/architecture.md`](docs/architecture.md) · 다이어그램 소스: [`docs/architecture-diagram.html`](docs/architecture-diagram.html)

## 실제로 얻는 것

- **투명 스프라이트 아틀라스** (`sprite-sheet-alpha.png`) — 실제 알파, 남은 크로마 가장자리 없음, 흰 배경에서 검증됨.
- **런타임 매니페스트** (`manifest.json.frame_layout`) — 절대 프레임 사각형, 상태별 fps와 루프 플래그. 엔진은 사각형을 샘플링하며, 그리드를 추측하지 않습니다.
- **눈으로 확인하는 QA** — 상태별 GIF와 컨택트 시트로, 출하 전에 모션을 모션으로 판단합니다.
- **정직한 라벨** — 짧고 읽기 쉬운 액션(idle, jump, attack, wave)이 안정적인 경로입니다. 순환 이동(walk/run)은 모션 QA가 실제로 통과하지 않는 한 실험적이라고 표시합니다. 조용한 과장 약속은 없습니다.

## 큐레이션 웹뷰

생성은 90%까지 데려다줍니다. 웹뷰는 사람이 그것을 *출하 가능한* 상태로 만드는 곳입니다. 독립 실행형이며 Studio나 프레임워크 의존성이 없고, skill이 설치된 곳이면 어디서나 실행됩니다(Claude Code Desktop, Codex 앱, 일반 터미널).

![curation webview — characters](docs/demo-character.gif)

- **상태마다 두 줄:** 위에는 **재생 시퀀스**, 아래에는 **후보 풀**(예: 두 번째나 세 번째 생성 테이크)이 있습니다. 프레임의 ⠿ 그립을 드래그해 시퀀스를 재정렬하거나, 풀에서 컷을 위로 끌어올릴 수 있습니다. 여러 테이크의 가장 좋은 프레임으로 하나의 깨끗한 달리기 루프를 다시 만드세요. 배치는 저장되므로 다시 열어도 복원됩니다.
- 프레임별 **비파괴 변형**: 드래그 = 이동, 휠 = 스케일, 위쪽 핸들 = 회전, 왼쪽 아래 = 전단, 그리고 좌우가 반전된 출력을 위한 수평 뒤집기 토글. 편집 내용은 `curation.json` 사이드카에 저장됩니다. 원본 PNG는 절대 다시 쓰지 않으며, 합성 단계가 결과를 결정적으로 굽습니다. 미리보기와 굽기는 동일한 아핀 행렬을 공유하므로, 정렬한 그대로 결과가 나옵니다.
- **실시간 미리보기**는 상태의 fps로 시퀀스를 애니메이션하며, 재생/일시정지, 프레임 단위 이동, 0.25×–4× 속도 제어를 제공합니다.
- 스프라이트에만 쓰는 도구가 아닙니다. `unpack_atlas_run.py --pngs-dir`로 이미지 후보 폴더(아이콘, 로고, 생성 초안)를 가리키면 일반적인 승자 고르기 뷰로 사용할 수 있습니다.

### 아이소메트릭 바닥 그리드

아이소메트릭 세트의 경우, 웹뷰가 바닥 그리드(`meta.json`의 tile/anchor 기반)를 오버레이하므로 전단 핸들로 가구를 다이아몬드 축에 맞출 수 있습니다.

![curation webview — isometric furniture](docs/demo-furniture.gif)

<img src="docs/curator-iso.png" width="520" alt="isometric ground grid overlay" />

### 언어

웹뷰는 영어와 한국어를 제공합니다. 실행할 때 `--lang en|ko`를 넘기거나, 앱 안의 토글을 사용하세요.

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # or ko
```

## Python 지원

`sprite-gen`은 CPython 3.10+를 지원합니다. CI는 GitHub-hosted runner에서 최소 지원 버전(3.10)과 최신 커버 버전(3.14)을 실행합니다.

퀵스타트에는 작동하는 `venv`/`ensurepip`가 포함된 Python 설치가 필요합니다. 로컬 배포판에서 패키지 설치 전에 `python3 -m venv`가 실패한다면, 지원되는 버전의 표준 CPython 빌드를 사용하고 같은 명령을 다시 실행하세요.

## Quickstart

```bash
# 0. install dependencies (Pillow) into a fresh virtualenv
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. prepare a run from a base image
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. generate one row image per state with image-gen, save as raw/<state>.png
# 3. extract frames
python3 scripts/extract_sprite_row_frames.py --run-dir <run-dir>

# 4. (optional) curate frames in the webview
python3 scripts/serve_curation.py --run-dir <run-dir>

# 5. bake the runtime atlas
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### 완성된 시트 편집하기

결합된 시트만 남아 있는 경우, 큐레이터에서 바로 쓸 수 있는 run dir을 다시 만든 뒤 큐레이션하고 내보내세요.

```bash
# rebuild frames: explicit --grid, --manifest rectangles, or alpha auto-detect (default)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # auto-detect
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # exact rectangles
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # import a loose PNG set

# after curating, bake corrections back to named PNGs
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

출력은 기본적으로 입력 옆의 찾기 쉬운 `<source>-curator` 폴더에 생성됩니다.

전체 에이전트용 워크플로와 계약은 [`SKILL.md`](SKILL.md)에 있습니다.

## 설치

Codex skill installer 워크플로에서 이 저장소를 루트 skill로 설치하세요.

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

### 필수 skill 의존성

원시 행 이미지(퀵스타트 2단계)는 별도의 [`image-gen`](https://github.com/aldegad/image-gen) skill이 생성합니다(`SKILL.md`의 `depends_on`에서 `kuma:image-gen`으로 선언됨). 같은 방식으로 설치하세요.

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/image-gen --path .
```

## Attribution

component-row 워크플로는 Apache-2.0 라이선스의 `hatch-pet` skill에서 영감을 받았지만, 범용 게임 스프라이트 아틀라스를 대상으로 하며 pet 패키지나 pet 시각 에셋은 포함하지 않습니다.

## License

Apache-2.0