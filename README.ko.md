<p align="center">
  <img src="docs/assets/claudecy-idle.gif" width="110" alt="claudecy idle" />
  <img src="docs/assets/claudecy-running.gif" width="110" alt="claudecy running" />
  <img src="docs/assets/claudecy-success.gif" width="110" alt="claudecy success" />
  <img src="docs/assets/claudecy-talking.gif" width="110" alt="claudecy talking" />
  <img src="docs/assets/howl-idle.gif" width="110" alt="howl idle" />
  <img src="docs/assets/howl-running.gif" width="110" alt="howl running" />
  <img src="docs/assets/howl-success.gif" width="110" alt="howl success" />
</p>

<h1 align="center">sprite-gen</h1>

<p align="center"><b>그림 하나를 넣으면. 게임에서 바로 쓸 수 있는 스프라이트 아틀라스가 나옵니다.</b></p>

<p align="center">

**English** · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-Hans.md) · [Español](README.es.md) · [Français](README.fr.md)

</p>

---

이미지 모델에 "sprite sheet"를 요청하면 어떤 결과가 나오는지 압니다. 프레임마다 얼굴이 바뀌는 캐릭터, 키아웃되지 않는 배경, 서로 겹치고 그리드 밖으로 밀려나는 포즈, 그리고 실제 게임 엔진이 제대로 먹지 못하는 PNG. 귀여운 데모지만, 에셋으로는 쓸모없습니다.

`sprite-gen`은 그 간극을 메우는 Codex/Claude skill입니다. **베이스 이미지 하나**와 액션 목록을 주면, 행 단위로 생성을 진행하고, 캐릭터 정체성을 고정하고, 크로마 배경을 진짜 알파로 제거하고, 각 포즈를 깨끗한 투명 프레임으로 추출한 뒤, **기계가 읽을 수 있는 `manifest.json.frame_layout`**이 포함된 런타임 아틀라스를 굽습니다. 위의 모든 스프라이트는 이 방식으로 만들었습니다.

그리고 생성이 끝내 맞추지 못하는 마지막 10%를 위해 **큐레이션 웹뷰**가 있습니다. 프레임을 나란히 비교하고, 망가진 것을 버리고, 회전/스케일/위치를 비파괴적으로 미세 조정하고, 루프를 실시간으로 확인한 뒤 굽습니다. 파이프라인이 노동을 맡고, 당신은 취향을 지킵니다.

```text
sprite-request.json → layout guides + prompts → sprite-gen gen state rows
→ chroma alpha → connected components → transparent frames
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

```mermaid
flowchart LR
    REQ["sprite-request.json<br/>(numeric SSoT)"] --> GUIDES["layout guides<br/>+ prompts"]
    GUIDES --> GEN["sprite-gen gen<br/>state row strips"]
    GEN --> EXTRACT["chroma alpha →<br/>connected components"]
    EXTRACT --> FRAMES["transparent frames"]
    FRAMES --> ATLAS["sprite-sheet-alpha.png<br/>+ manifest.json.frame_layout"]
    FRAMES -. "curation webview (optional)" .-> ATLAS
```

> 전체 아키텍처: [`docs/architecture.md`](docs/architecture.md)

## 실제로 얻게 되는 것

- **투명 스프라이트 아틀라스** (`sprite-sheet-alpha.png`) — 진짜 알파, 남은 크로마 프린지 없음, 흰 배경에서 검증됨.
- **런타임 매니페스트** (`manifest.json.frame_layout`) — 절대 프레임 사각형, 상태별 fps와 루프 플래그. 엔진은 사각형을 샘플링하며, 그리드를 추측하지 않습니다.
- **눈으로 확인 가능한 QA** — 상태별 GIF와 콘택트 시트로, 배포 전에 움직임을 움직임으로 판단할 수 있습니다.
- **정직한 라벨** — idle, jump, attack, wave 같은 짧고 읽기 쉬운 액션이 안정적인 경로입니다. walk/run 같은 순환 이동은 모션 QA를 실제로 통과하지 않는 한 experimental로 표시됩니다. 조용히 과장하지 않습니다.

## 크로마 알파 품질

추출기는 크로마 클린업을 결정적으로 유지합니다. soft-alpha unmix는 커버리지를 풀기도 전에 가장자리를 벗겨내지 않고, 앤티앨리어싱된 머리카락 가닥과 얇은 외곽선을 보존합니다.

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-magenta.png" width="640" alt="full-body chroma comparison: illustration on magenta key" /><br />
  <em>일러스트레이션, 마젠타 키: source, v1.12.0 peel, v1.13.0 soft-alpha unmix.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-green.png" width="640" alt="full-body chroma comparison: illustration on green key" /><br />
  <em>일러스트레이션, 그린 키: source, v1.12.0 peel, v1.13.0 soft-alpha unmix.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-magenta.png" width="640" alt="full-body chroma comparison: pixel art on magenta key" /><br />
  <em>픽셀 아트, 마젠타 키: source, v1.12.0 peel, v1.13.0 binarized output.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-green.png" width="640" alt="full-body chroma comparison: pixel art on green key" /><br />
  <em>픽셀 아트, 그린 키: source, v1.12.0 peel, v1.13.0 binarized output.</em>
</p>

아래 클로즈업 크롭은 전신 비교 이미지 뒤의 가장자리 디테일을 보여줍니다.

![chroma peel before and after — illustrated hair strand](docs/assets/chroma-peel-illustration-before-after.png)

![chroma peel before and after — pixel-art outline](docs/assets/chroma-peel-pixelart-before-after.png)

## 큐레이션 웹뷰

생성은 90%까지 데려다줍니다. 웹뷰는 사람이 그것을 *출시 가능한* 수준으로 가져가는 곳입니다. 독립 실행형이며 Studio나 프레임워크 의존성이 없고, skill이 설치된 어디서나 실행됩니다(Claude Code Desktop, Codex 앱, 일반 터미널).

![curation webview — characters](docs/assets/demo-character.gif)

- **상태마다 두 행:** 위에는 **재생 시퀀스**, 아래에는 **후보 풀**이 있습니다(예: 두 번째 또는 세 번째 생성 테이크). 프레임의 ⠿ 그립을 드래그해 시퀀스 순서를 바꾸거나, 풀에서 컷을 위로 끌어올릴 수 있습니다. 여러 테이크의 최고 프레임을 모아 하나의 깨끗한 run 루프를 다시 만드세요. 배치는 저장되므로 다시 열어도 복원됩니다.
- 프레임별 **비파괴 transform**: 드래그 = 이동, 휠 = 스케일, 상단 핸들 = 회전, 왼쪽 아래 = shear, 여기에 좌우 반전된 출력을 위한 horizontal-flip 토글이 있습니다. 편집 내용은 `curation.json` 사이드카에 저장됩니다. 원본 PNG는 절대 다시 쓰지 않으며, compose 단계가 결과를 결정적으로 굽습니다. 프리뷰와 bake는 동일한 affine matrix를 공유하므로, 정렬한 그대로 결과가 나옵니다.
- **실시간 프리뷰**는 상태의 fps로 시퀀스를 애니메이션합니다. 재생/일시정지, 프레임 단위 스텝, 0.25×–4× 속도 조절을 제공합니다.
- 스프라이트에만 쓰는 도구가 아닙니다. `unpack_atlas_run.py --pngs-dir`로 이미지 후보 폴더(아이콘, 로고, 생성 초안 등)를 지정하면, 일반적인 승자 선택 뷰로 사용할 수 있습니다.

### 아이소메트릭 바닥 그리드

아이소메트릭 세트에서는 웹뷰가 (`meta.json`의 tile/anchor에서 가져온) 바닥 그리드를 오버레이하므로, shear 핸들로 가구를 다이아몬드 축에 맞춰 스냅할 수 있습니다.

![curation webview — isometric furniture](docs/assets/demo-furniture.gif)

<img src="docs/assets/curator-iso.png" width="520" alt="isometric ground grid overlay" />

### 언어

웹뷰는 영어와 한국어를 함께 제공합니다. 실행할 때 `--lang en|ko`를 넘기거나, 앱 안의 토글을 사용하세요.

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # or ko
```

## Python 지원

`sprite-gen`은 CPython 3.10+를 지원합니다. CI는 GitHub-hosted runners에서 최소 지원 버전(3.10)과 최신 커버 버전(3.14)을 실행합니다.

퀵스타트에는 작동하는 `venv`/`ensurepip`가 포함된 Python 설치가 필요합니다. 로컬 배포판에서 패키지 설치 전에 `python3 -m venv`가 실패한다면, 지원되는 아무 버전의 표준 CPython 빌드를 사용한 뒤 같은 명령을 다시 실행하세요.

## Quickstart

```bash
# 0. install dependencies (Pillow) into a fresh virtualenv
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. prepare a run from a base image
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. generate one row image per state with the engine-owned provider CLI
python3 scripts/generate_sprite_image.py --provider codex \
  --prompt-file <run-dir>/prompts/<state>.txt \
  --out <run-dir>/raw/<state>.png \
  --ref <run-dir>/base-source.png \
  --ref <run-dir>/references/layout-guides/<state>.png
# 3. extract frames
python3 scripts/extract_sprite_row_frames.py --run-dir <run-dir>

# 4. (optional) curate frames in the webview
python3 scripts/serve_curation.py --run-dir <run-dir>

# 5. bake the runtime atlas
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### 완성된 시트 편집하기

결합된 시트만 남아 있을 때는 curator-ready run dir을 다시 만든 뒤, 큐레이션하고 내보내세요.

```bash
# rebuild frames: explicit --grid, --manifest rectangles, or alpha auto-detect (default)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # auto-detect
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # exact rectangles
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # import a loose PNG set

# after curating, bake corrections back to named PNGs
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

출력은 기본적으로 입력 옆의 찾기 쉬운 `<source>-curator` 폴더에 생성됩니다.

### 가져온 이미지에서 배경 잘라내기

생성된 스프라이트는 파이프라인 안에서 자체 마젠타/그린 배경을 키로 처리하므로
이 기능이 필요하지 않습니다. `cutout`은 import/post-edit 유틸리티입니다. 불투명한 단색 배경을 *가지고 온* 이미지(손으로 그린 아이콘,
다운로드한 스프라이트, 스크린샷)를 깨끗한 투명 PNG로 바꿉니다.

<p align="center">
  <img src="docs/assets/cutout-demo.png" width="720" alt="cutout: a white-background game icon turned into a clean transparent PNG, glass highlights preserved" />
</p>

```bash
# routes on the corner colour: white/ivory -> matte, magenta/green -> extract engine
python3 -m sprite_gen.cli cutout icon.png --white-check
```

모서리 배경색을 읽고 라우팅합니다(`--key auto|white|magenta|green`).

- **white / ivory / solid** → position matte. 모서리 flood-fill은 연결된 배경만 유지합니다(객체 *안쪽*의 밝은 하이라이트는 구멍이 나지 않고 살아남습니다). 그런 다음 decontaminated soft alpha가 테두리를 부드럽게 만듭니다. `--strength`(bevel removal), `--band`(edge depth), `--erode`로 조정하세요.
- **magenta / green key** → 프로젝트의 검증된 `extract` chroma engine을 그대로 재사용합니다. 키 색상은 객체에 나타나지 않으므로, 색상만으로 자르는 방식이 그곳에서는 안전합니다. 바로 white matte의 flood-fill guard가 *필요하지 않은* 경우입니다.

`--white-check`는 cyan/magenta/yellow 합성 이미지를 써서 남은 프린지가 있으면 크게 드러나게 합니다. 단색 배경용이며, 복잡하거나 균일하지 않은 배경용이 아닙니다.

전체 agent-facing 워크플로와 계약은 [`SKILL.md`](SKILL.md)에 있습니다.

## Install

Codex skill installer 워크플로에서는 이 저장소를 root skill로 설치하세요.

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

### 이미지 생성 소유권

Provider-backed generation은 이 엔진(`sprite_gen.gen`)의 일부이며,
`codex`와 `grok`이 지원되는 provider입니다. 일반 `image-gen` skill은 같은 명령으로 가는 얇은 셔틀일 뿐이므로 두 번째 provider
구현이 필요하지 않습니다. CLI와 검증
계약은 [`docs/gen.md`](docs/gen.md)를 참고하세요.

## Attribution

component-row 워크플로는 Apache-2.0 라이선스의 `hatch-pet` skill에서 영감을 받았지만, 범용 게임 스프라이트 아틀라스를 대상으로 하며 pet 패키지나 pet 시각 에셋은 포함하지 않습니다.

## License

Apache-2.0