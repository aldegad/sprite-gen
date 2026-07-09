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

<p align="center"><b>그림 하나를 넣으면 게임용 스프라이트 아틀라스가 나옵니다.</b></p>

<p align="center">

**English** · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-Hans.md) · [Español](README.es.md) · [Français](README.fr.md)

</p>

---

이미지 모델에 "sprite sheet"를 요청해 본 적이 있다면 결과가 어떤지 알 겁니다. 프레임마다 얼굴이 바뀌는 캐릭터, 키잉되지 않는 배경, 서로 겹치고 그리드에서 밀려나는 포즈, 실제 게임 엔진이 소비할 수 없는 PNG. 귀여운 데모지만 쓸모없는 에셋입니다.

`sprite-gen`은 그 간극을 메우는 Codex/Claude 스킬입니다. **기본 이미지 하나**와 액션 목록을 주면, 행 단위로 생성을 진행하고, 캐릭터 정체성을 고정하고, 크로마 배경을 실제 알파로 제거하고, 각 포즈를 깨끗한 투명 프레임으로 추출한 뒤, **기계가 읽을 수 있는 `manifest.json.frame_layout`**이 포함된 런타임 아틀라스로 굽습니다. 위의 모든 스프라이트가 이 방식으로 만들어졌습니다.

그리고 생성이 끝내 맞추지 못하는 마지막 10%를 위해 **큐레이션 웹뷰**가 있습니다. 프레임을 나란히 비교하고, 망가진 것을 제외하고, 회전/스케일/위치를 비파괴적으로 조정하고, 루프를 실시간으로 확인한 뒤 굽습니다. 파이프라인이 노동을 맡고, 당신은 취향을 유지합니다.

```text
sprite-request.json → layout guides + prompts → image-gen state rows
→ chroma alpha → connected components → transparent frames
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

```mermaid
flowchart LR
    REQ["sprite-request.json<br/>(numeric SSoT)"] --> GUIDES["layout guides<br/>+ prompts"]
    GUIDES --> GEN["image-gen<br/>state row strips"]
    GEN --> EXTRACT["chroma alpha →<br/>connected components"]
    EXTRACT --> FRAMES["transparent frames"]
    FRAMES --> ATLAS["sprite-sheet-alpha.png<br/>+ manifest.json.frame_layout"]
    FRAMES -. "curation webview (optional)" .-> ATLAS
```

> 전체 아키텍처: [`docs/architecture.md`](docs/architecture.md)

## 실제로 얻는 것

- **투명 스프라이트 아틀라스** (`sprite-sheet-alpha.png`) — 진짜 알파, 남은 크로마 테두리 없음, 흰 배경 기준 검증 완료.
- **런타임 매니페스트** (`manifest.json.frame_layout`) — 절대 프레임 사각형, 상태별 fps와 루프 플래그. 엔진은 사각형을 샘플링하며, 그리드를 추측하지 않습니다.
- **눈으로 확인 가능한 QA** — 상태별 GIF와 컨택트 시트로, 배포 전에 모션을 모션으로 판단합니다.
- **정직한 라벨** — 짧고 읽기 쉬운 액션(idle, jump, attack, wave)이 안정적인 경로입니다. 순환 이동(walk/run)은 모션 QA가 실제로 통과하지 않는 한 experimental로 표시됩니다. 조용히 과장하지 않습니다.

## 크로마 알파 품질

추출기는 크로마 정리를 결정적으로 수행합니다. 소프트 알파 unmix가 머리카락 가닥과 얇은 아웃라인의 안티앨리어싱을 보존하므로, 커버리지를 계산하기 전에 경계가 깎여 나가지 않습니다.

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-magenta.png" width="640" alt="마젠타 키 일러스트 전신 크로마 비교" /><br />
  <em>일러스트, 마젠타 키: 원본, v1.12.0 peel, v1.13.0 소프트 알파 unmix.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-green.png" width="640" alt="그린 키 일러스트 전신 크로마 비교" /><br />
  <em>일러스트, 그린 키: 원본, v1.12.0 peel, v1.13.0 소프트 알파 unmix.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-magenta.png" width="640" alt="마젠타 키 픽셀아트 전신 크로마 비교" /><br />
  <em>픽셀아트, 마젠타 키: 원본, v1.12.0 peel, v1.13.0 이진화 출력.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-green.png" width="640" alt="그린 키 픽셀아트 전신 크로마 비교" /><br />
  <em>픽셀아트, 그린 키: 원본, v1.12.0 peel, v1.13.0 이진화 출력.</em>
</p>

아래 확대 크롭은 전신 비교의 경계 디테일을 보여줍니다.

![크로마 peel 전후 비교 — 일러스트 머리카락 가닥](docs/assets/chroma-peel-illustration-before-after.png)

![크로마 peel 전후 비교 — 픽셀아트 아웃라인](docs/assets/chroma-peel-pixelart-before-after.png)

## 큐레이션 웹뷰

생성은 90%까지 데려다줍니다. 웹뷰는 사람이 그것을 *출시 가능한 상태*로 가져가는 곳입니다. 독립 실행형이며 Studio나 프레임워크 의존성이 없고, 스킬이 설치된 어디서든 실행됩니다(Claude Code Desktop, Codex 앱, 일반 터미널).

![curation webview — characters](docs/assets/demo-character.gif)

- **상태마다 두 행:** 위에는 **재생 시퀀스**, 아래에는 **후보 풀**이 있습니다(예: 두 번째나 세 번째 생성 결과). 프레임의 ⠿ 그립을 드래그해 시퀀스를 재정렬하거나, 풀에서 컷을 위로 끌어올릴 수 있습니다. 여러 테이크의 가장 좋은 프레임으로 하나의 깨끗한 달리기 루프를 다시 구성하세요. 배열은 저장되므로 다시 열어도 복원됩니다.
- 프레임별 **비파괴 변환**: 드래그 = 이동, 휠 = 스케일, 위쪽 핸들 = 회전, 왼쪽 아래 = 기울이기, 여기에 좌우 반전 출력용 horizontal-flip 토글이 더해집니다. 편집은 `curation.json` 사이드카에 저장됩니다. 원본 PNG는 절대 다시 쓰지 않으며, compose 단계가 결과를 결정적으로 굽습니다. 미리보기와 굽기는 하나의 affine matrix를 공유하므로, 정렬한 그대로 결과가 나옵니다.
- **실시간 미리보기**는 상태의 fps로 시퀀스를 애니메이션합니다. 재생/일시정지, 프레임 단위 이동, 0.25×–4× 속도 조절을 제공합니다.
- 스프라이트 전용이 아닙니다. `unpack_atlas_run.py --pngs-dir`로 이미지 후보 폴더(아이콘, 로고, 생성 초안)를 지정하면, 일반적인 우승작 선택 뷰로 사용할 수 있습니다.

### 아이소메트릭 지면 그리드

아이소메트릭 세트의 경우, 웹뷰는 바닥 그리드(`meta.json`의 tile/anchor 기준)를 오버레이하므로 shear 핸들로 가구를 다이아몬드 축에 맞춰 스냅할 수 있습니다.

![curation webview — isometric furniture](docs/assets/demo-furniture.gif)

<img src="docs/assets/curator-iso.png" width="520" alt="isometric ground grid overlay" />

### 언어

웹뷰는 영어와 한국어를 함께 제공합니다. 실행할 때 `--lang en|ko`를 전달하거나, 앱 내부 토글을 사용하세요.

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # or ko
```

## Python 지원

`sprite-gen`은 CPython 3.10+를 지원합니다. CI는 GitHub-hosted runner에서 최소 지원 버전(3.10)과 최신 커버 버전(3.14)을 실행합니다.

퀵스타트에는 동작하는 `venv`/`ensurepip`가 포함된 Python 설치가 필요합니다. 로컬 배포판에서 패키지 설치 전에 `python3 -m venv`가 실패한다면, 지원 버전 중 표준 CPython 빌드를 사용한 뒤 같은 명령을 다시 실행하세요.

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

### 완성된 시트 편집

결합된 시트만 남아 있을 때는 큐레이터에서 바로 쓸 수 있는 run dir을 다시 만든 뒤, 큐레이션하고 내보내세요.

```bash
# rebuild frames: explicit --grid, --manifest rectangles, or alpha auto-detect (default)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # auto-detect
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # exact rectangles
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # import a loose PNG set

# after curating, bake corrections back to named PNGs
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

출력 기본값은 입력 옆에서 찾기 쉬운 `<source>-curator` 폴더입니다.

전체 에이전트용 워크플로와 계약은 [`SKILL.md`](SKILL.md)에 있습니다.

## Install

Codex skill installer 워크플로에서 이 저장소를 루트 스킬로 설치하세요.

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

### 필수 스킬 의존성

원시 행 이미지(퀵스타트 2단계)는 별도의 [`image-gen`](https://github.com/aldegad/image-gen) 스킬로 생성됩니다(`SKILL.md`의 `depends_on`에 `kuma:image-gen`으로 선언). 같은 방식으로 설치하세요.

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/image-gen --path .
```

## Attribution

component-row 워크플로는 Apache-2.0 라이선스의 `hatch-pet` 스킬에서 영감을 받았지만, 범용 게임 스프라이트 아틀라스를 대상으로 하며 pet 패키지나 pet 시각 에셋은 포함하지 않습니다.

## License

Apache-2.0
