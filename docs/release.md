# 릴리즈 — 태그를 push 하고, 레인이 빌드한 것으로 릴리즈 페이지를 낸다

> Owns: How a vX.Y.Z tag becomes a release page, the lanes a commit passes (Linux on GitHub-hosted runners, and an NVIDIA CUDA host for the torch backend), what the lane builds and the publisher attaches, and what stays manual · Index: [docs/README.md](README.md)

릴리즈 페이지는 손으로 만들지 않는다. `vX.Y.Z` 태그를 push 한 뒤 `scripts/release_publish.py` 가
그 태그의 `CHANGELOG.md` 절을 본문으로 페이지를 만든다. 사람이 `gh release create` 의 인자를
기억할 필요가 없게 하는 것이 목적이다 — v2.5.3 이 태그·CHANGELOG·README 까지 올라간 채 릴리즈
페이지만 빠져서, 릴리즈 목록에는 v2.5.2 가 Latest 로 남아 있었던 적이 있다.

push 나 PR 마다 도는 CI 는 없고, 이 레포에는 워크플로가 없다. 테스트 게이트와 wheel·sdist 빌드는
`scripts/linux_lane.sh` 가 하고, 그것을 메인테이너가 잴 커밋의 SHA 를 넣어 손으로 시작하는 워크플로가 GitHub
호스트 표준 러너(ubuntu x86-64)에서 돌린다 — [aldegad/sprite-gen-lane](https://github.com/aldegad/sprite-gen-lane)
의 `linux-lane.yml` 이다. 그 레포는 공개 클론으로 커밋을 받을 뿐 비밀값도, 잡 토큰의 권한도 없다. RIFE torch 판의
CUDA 게이트는 메인테이너의 NVIDIA GPU 호스트에서 `scripts/cuda_lane.py` 가 돌린다(아래 "CUDA 레인"). 페이지를
내는 기계는 아무것도 빌드하지 않고 레인이 만든 것을 붙인다.

리눅스 레인이 통과했다는 것은 한 번의 실행이 exit 0 으로 끝났거나, 같은 커밋에서 `--rife-unmeasured "<이유>"`
실행(exit 3, RIFE 를 재지 못했다고 끝에 적는다)과 `--rife-only` 실행(exit 0)이 둘 다 끝났다는
뜻이다. RIFE 는 rife-ncnn-vulkan 을 돌릴 수 있는 Vulkan 드라이버가 있어야 잴 수 있다 — WSL1 의 Mesa
lavapipe 에서는 첫 업로드에서 죽는다 — 그래서 그런 호스트에서는 RIFE 를 떼어 내되 조용히 건너뛰지는
않는다.

GitHub 호스트 러너의 잡 하나는 6시간에 끊기므로 워크플로는 같은 SHA 를 두 잡으로 나눠 나란히 돌린다: `suite`
잡이 `--rife-unmeasured "<이유>"` 로, `rife` 잡이 `--rife-only` 로 돈다. 같은 실행에서 `suite` 가 exit 3 으로,
`rife` 가 exit 0 으로 끝나 둘 다 초록이면(뒤따르는 `verdict` 잡도 초록이다) 그 커밋은 리눅스 레인을 통과한
것이다. 러너에는 GPU 가 없어 RIFE 는 컨테이너의 Mesa llvmpipe(Vulkan 의 CPU 드라이버)에서 돈다.

`scripts/linux_lane.sh` 레인은 `pytest` 호출마다 `--full` 을 전달해 모든 시험을 실행한다(CUDA 레인도 같다).
일반 `pytest` 실행은 `pyproject.toml` 에 등록된 `slow` 표시 시험을 `tests/conftest.py` 가 실행 대상에서
제외하는 빠른 묶음이다. `tests/release/test_fast_bundle.py` 는 레인의 각 `pytest` 호출에 `--full` 이 있는지
확인한다.

같은 픽셀이라도 zlib 차이로 리눅스와 macOS에서 PNG 파일 바이트가 달라 macOS에서 파일 바이트를 재서 박은 시험이 리눅스 레인에서 아무것도 안 바뀐 채 실패한 적이 있으므로, 기계 사이에 고정하는 sha256 값은 출력 파일의 바이트가 아니라 `tests/picture_digest.py`의 `picture_digest`를 써서 디코딩한 그림(모든 프레임을 RGBA로 디코딩한 픽셀과 각 프레임의 크기, 프레임 개수)으로 박는다. 손실 압축 파일(JPEG, 손실 WebP)은 디코더 빌드에 따라 픽셀도 달라질 수 있으니 값을 박지 말고 같은 실행 안에서 만든 짝과 비교한다.

실제 rife-ncnn-vulkan 을 돌리는 시험은 `real_rife` 표시를 단다(`pyproject.toml` 이 등록하고,
`tests/conftest.py` 가 RIFE 를 못 찾으면 그 표시가 붙은 시험을 건너뛴다). `--rife-unmeasured` 는
RIFE 가 닿는 곳이 하나도 없는 상태로 스위트 전체를 돌려 그 시험들이 이 이유로 건너뛰어지고,
`--rife-only` 는 `sprite-gen rife install`(그 확인 프레임)과 스위트 전체에서 `real_rife` 가 붙은
시험(`pytest --full -m "real_rife and not torch_rife"`, 아래 torch 단계가 나머지)을 돌린다 — 하나라도 건너뛰거나, 표시된 시험이 없으면 실패다. 그래서 새
실제-RIFE 시험은 표시만 붙이면 `--rife-only` 가 저절로 걷는다.

컷의 기본 채움(`--fill-held auto`)으로 바이너리에 닿고 CPU 의 Vulkan 드라이버에서 기본 120 s 제한을 넘길 만큼 자주 실행하는 시험은 `real_rife` 표시 여부와 관계없이 긴 바이너리 시험처럼 `@pytest.mark.timeout(900)` 과 바이너리 호출 수 주석을 달고 `tests/conftest.py` 의 `as_pinned` 로 요청하는 RIFE 실행 수를 모델별로 고정한다(바이너리를 못 찾으면 호출 수를 0 으로, 즉 호출이 없는 것으로 고정하며, 이는 이전에 `rife_located()` 가 거짓일 때와 같다). `as_pinned` 는 고정값을 int 로 받으면 정확한 수로, `(floor, top)` 쌍으로 받으면 양 끝을 포함하여 floor 부터 top 까지로 고정하고, 고정값은 모델 이름을 키로 한 dict 로 모델별로 주거나 모든 모델에 같게 줄 수 있으며, 이 읽기 방식은 `tests/release/test_rife_pins.py` 가 고정한다. rife-v4.6 은 전처럼 정확한 수로 고정하고, rife-v4.25 만 두 끝 사이로 고정하며, 다른 모델에 쌍을 주면 ValueError 로 거부한다. 전에는 rife-v4.25 를 고정값 이하로만 고정했는데, 보간을 건너뛰는 변경(호출이 없거나 너무 적은 경우)도 그대로 통과한다는 검토 지적이 있어 지금은 하한과 상한 사이로 고정한다. 하한은 어느 프레임도 다른 분수에서 다시 만들지 않을 때 컷이 하는 호출 수로, 컷이 요청하는 프레임 하나마다 제때에 한 번씩 요청하는 두 번의 호출이다. 상한은 Apple GPU 와 llvmpipe(Linux 레인의 CPU Vulkan 드라이버)에서 잰 수 가운데 큰 쪽이다. rife-v4.25 를 범위로 두는 까닭은 증강 없이 float16 으로 실행하여 프레임이 GPU 의 연산에 따라 달라지고, 그에 따라 거부되어 다른 분수에서 다시 만드는 프레임도 달라지기 때문이다. 하한은 컷이 요청하는 프레임을 세는 것이지, 채우려고 나선 유지 구간의 모든 셀을 세는 것이 아니다. 다시 시도할 분수가 남지 않은 채 거부된 프레임은 그 구간의 채움을 끝내며, 네 건에서는 그래서 컷이 셀마다 한 프레임씩 만들 때보다 바이너리를 적게 호출한다. 고정된 35 건을 두 기계에서 실행하여 그 중 24 건이 바이너리에 닿았고, 모든 건에서 두 기계가 같은 수의 프레임을 제때에 요청하여 하한은 건마다 하나의 수이며, 합성 걷기 하나에서 바이너리가 Apple GPU 에서는 8 번, llvmpipe 에서는 10 번 호출된 것 외에는 두 기계의 수가 같았고, 24 건 중 9 건은 어느 프레임도 다시 만들지 않아 하한과 상한이 같으므로 정확한 수로 고정되며, rife-v4.6 은 잰 모든 기계에서 수가 같았다. 바이너리를 몇 번만 실행하고 기본 제한 안에서 여유 있게 끝나는 시험은 둘 다 두지 않는다. 고정의 목적은 그대로다. CPU 의 Vulkan 드라이버에서는 실행 하나에 몇 초씩 걸리므로, 실행을 더 요구하는 변경이 시간 초과가 아니라 호출 수에서 걸린다. 지금은 프레임을 더 적게 요청하는 변경도 호출 수에서 걸린다.

RIFE 의 torch 백엔드(`SPRITE_GEN_RIFE_BACKEND=torch`, docs/loop-repair.md 1절)를 돌리는 시험은
`torch_rife` 표시를 단다(`pyproject.toml` 이 등록한다). `tests/conftest.py` 는 PyTorch 가 import 되지
않거나, `rife.locate_model()` 이 모델을 못 찾거나, `SPRITE_GEN_RIFE_DEVICE` 가 이름 댄 장치가
없으면(기본 cuda 인데 CUDA 가 없으면) 그 표시가 붙은 시험을 건너뛴다. 레인은 스위트를 다 돈 뒤 같은
환경에 PyTorch 2.13.0 의 CPU 판을 PyTorch CPU 인덱스에서 깔고, `sprite-gen rife install --backend torch`
의 확인 프레임(PyTorch 로 만든다)과 `torch_rife` 가 붙은 시험 전부(`pytest --full -m torch_rife`)를
`SPRITE_GEN_RIFE_DEVICE=cpu` 로 돌린다. 하나라도 건너뛰거나 표시된 시험이 없으면 실패다. `--rife-only`
도 같은 단계를 돈다. `real_rife` 와 `torch_rife` 가 함께 붙은 시험(같은 기계의 바이너리와 torch 판의
판정 비교)은 `real_rife` 단계에서 빼고 이 단계에서 돈다. `--rife-unmeasured` 는 이 단계를 돌지 않고, 그
실행에서는 `torch_rife` 가 붙은 시험도 RIFE 때문에 건너뛸 수 있는 것으로 친다.
`tests/release/test_lane_rife_collection.py` 의 표시 검사도 두 표시를 같이 받는다. CUDA 위의 torch 판은
이 레인이 재지 않고 CUDA 레인이 잰다.

표시 없이 RIFE 가 없을 때 건너뛰거나 모이지 않는 시험은 두 실행 어느 쪽도 재지 않는다. 그래서
`--rife-unmeasured` 는 시험을 `pytest --full --rife-unmeasured` 로 돌리고, 그 실행에서 건너뛴 것마다 — skip
표시로든, fixture 나 시험 본문에서든, 파일째 import 에서든 — `tests/conftest.py` 가 `real_rife` 표시를
본다. 표시가 없고, 같은 파일의 `NOT_RIFE_SKIPS` 가 RIFE 가 아닌 이유(레인이 주지 않는 입력)로
건너뛴다고 이름 대지도 않았으면, 그 결과를 실패로 바꿔 시험의 이름을 댄다. RIFE 가 없으면 아예 모이지
않는 시험은 그 실행에 보이지 않으므로, `tests/release/test_lane_rife_collection.py` 가 스위트를 RIFE 가
있는 척하는 대역을 둔 채 한 번, 없는 채 한 번 모아, 모이는지나 skip 표시의 판정이 갈리는 시험마다
표시를 요구하고 없으면 그 이름을 대고 실패한다.

## CUDA 레인 — torch 판을 CUDA 에서

`scripts/cuda_lane.py` 는 메인테이너의 NVIDIA GPU 호스트에서 torch 판을 CUDA 로 잰다. 표준 라이브러리만 쓰고,
두 기계에서 두 단계로 돈다:

```bash
# safedeps 가 있는 기계에서, 잴 커밋을 체크아웃한 트리(커밋되지 않은 변경이 없는)로
.venv/bin/python scripts/cuda_lane.py pack --requirements <목록> --out <빈 디렉터리> [--host-spec <생태계>:<이름>@<버전> ...]
# 그 디렉터리를 호스트로 옮긴 뒤, 호스트에서 Python 3.14 로
python -I <디렉터리>/cuda_lane.py run --pack <디렉터리> [--wheels <PyPI 에 없는 휠의 디렉터리>] [--rife-zip <zip>]
```

목록은 호스트가 깔 패키지 전부를 한 줄에 하나씩 `이름==버전 --hash=sha256:<값>` 으로 적은 것이다(백슬래시로 줄을
이어도 된다). 트리의 `[dev]` 와 PyTorch 2.13.0 CUDA 판의 의존성 닫힘을 호스트의 플랫폼과 Python 3.14 에 맞춰
적는다. 리눅스 호스트라면 PyPI 의 `torch==2.13.0`(리눅스 휠이 CUDA 13 판이다)을, 윈도우 호스트라면 PyTorch cu130
인덱스의 `torch==2.13.0+cu130` 을 적는다(PyPI 의 윈도우 휠은 CPU 판이다). PyPI 에 없는 휠은 `--wheels`
디렉터리에 둔다.

`pack` 은 목록의 패키지마다(PyTorch 는 로컬 꼬리표를 뗀 버전으로), 그리고 `--host-spec` 마다(호스트가 레인을 위해
목록 밖에서 까는 것, 예를 들어 윈도우에서 python.org 의 NuGet 패키지를 풀어 쓰는 Python 이면
`nuget:python@3.14.10`) `safedeps check` 를 돌린다. 그 버전 그대로 `clean` 이나 `already_approved` 가 아닌 답,
끝나지 못한 검사(`error`, `check_did_not_finish`), 돌지 못한 safedeps 는 그 자리에서 `pack` 을 멈추고, `pack` 은
아무것도 쓰지 않는다 — 그러면 호스트가 깔 것이 없다. 모든 검사가 통과해야 HEAD 의 트리(`git archive`),
`SOURCE_SHA`, 검사한 목록(`requirements.txt`), 검사마다의 답(`checks.json`), 그 커밋의 `cuda_lane.py` 를 쓴다.
`run` 은 `checks.json` 에 목록의 줄마다 통과한 검사가 없거나 목록이 검사한 것과 다르면 아무것도 만들지 않고
멈춘다. 두 멈춤은 `tests/release/test_cuda_lane.py` 가 본다.

`run` 은 리눅스 레인이 깨끗한 클론에 까는 것처럼 Python 3.14 의 새 가상 환경을 짓는다. 목록의 휠을 해시를
확인하며 받고, 받은 파일만으로 깔고, 트리를 `pip install -e "src[dev]"` 로(더 받는 것 없이) 깔고, `pip check` 를
돌리고, 모델을 `sprite-gen rife install --backend torch --no-check` 로 엔진이 바이너리를 찾지 않는 디렉터리에
둔다. 윈도우는 긴 경로를 켜지 않은 곳에서 260자 이상인 경로를 쓰지 못하고, PyTorch 2.13.0+cu130 휠에는 173자인
이름이 있다. 그래서 `run` 은 휠이 쓰는 가장 긴 경로가 가상 환경 아래에 들어가지 않으면 깔기 전에 멈추고 몇 자를
줄여야 하는지 말한다. 호스트의 설정은 바꾸지 않고 디렉터리의 경로를 짧게 잡는다(예를 들어
`<드라이브>:\<짧은 이름>\<SHA 앞 7자>`). 그리고 호스트의 GPU 에서:

- `nvidia-smi` 로 GPU 와 드라이버를, PyTorch 로 CUDA 를 확인한다(PyTorch 가 GPU 를 찾지 못하면 실패다).
- 설치의 확인 프레임을 CUDA 로 만든다(`sprite-gen rife install --backend torch`, `SPRITE_GEN_RIFE_DEVICE=cuda`).
- `torch_rife` 가 붙고 `real_rife` 는 붙지 않은 시험 전부를 CUDA 로 돈다. 하나라도 건너뛰거나 표시된 시험이
  없으면 실패다. 둘이 함께 붙은 시험(같은 기계의 바이너리와 torch 판의 판정 비교)은 리눅스 레인 몫이다.

엔진이 바이너리를 찾는 곳은 모두 비워 둔다: 밖에서 온 `SPRITE_GEN_*` 가 없고, 사용자 데이터 디렉터리(윈도우는
`LOCALAPPDATA`, 그 밖은 `XDG_DATA_HOME`)가 비어 있고, PATH 에 rife-ncnn-vulkan 이 없다. NVIDIA 호스트에서는
Vulkan 바이너리도 돌 수 있으므로 일부러 닿지 않게 두는 것이다. 임시 디렉터리, pip 캐시, CUDA 캐시도 그 디렉터리
안을 가리키므로 실행은 디렉터리 밖에 아무것도 쓰지 않고, 디렉터리를 통째로 지우면 실행이 쓴 것이 모두 지워진다.

실행은 커밋, GPU 와 드라이버, `pip freeze`, 단계마다의 출력을 찍고 `run.json` 과 `logs/` 에 남긴다. 모든 단계가
통과해야 exit 0 이다. CUDA 레인이 통과했다는 것은 그 커밋에서 돈 실행 하나가 exit 0 으로 끝났다는 뜻이다. 두
레인이 같은 PyTorch 와 Python 을 까는지도 `tests/release/test_cuda_lane.py` 가 본다.

## 통과는 커밋 하나의 것이다

커밋이 레인을 통과했다는 것은 그 커밋에서 리눅스 레인이 통과하고(위 정의) CUDA 레인이 통과했다는 뜻이다.
통과는 커밋 하나의 것이다. 어떤 커밋에서 잰 레인 — 한 번의 exit 0 이든, `--rife-only` 의 exit 0 이든 —
은 다른 커밋의 통과가 아니다. 이전 릴리즈나 부모 커밋의 통과를 다음 커밋으로 옮겨 적지 않고, 머지할
커밋과 태그 커밋을 각각 그 SHA 의 깨끗한 클론에서 잰다. `--rife-unmeasured` 로 잰 커밋은 같은 SHA 의
`--rife-only` 가 exit 0 으로 끝나기 전까지 레인을 통과하지 않은 것이다. 패키지도 그 SHA 의 것이다:
`SOURCE_SHA` 가 태그 커밋이 아닌 디렉터리는 그 릴리즈에 쓰지 못한다(아래 5).

## 한 묶음

1. **릴리즈 커밋** — `CHANGELOG.md` 의 `## Unreleased (vX.Y.Z)` 절을 `## vX.Y.Z - <제목>` 으로
   바꾸고, `pyproject.toml` 의 `version` 과 `SKILL.md` 의 `version:` 을 그 버전으로 맞춘다
   (둘의 일치는 `tests/packaging/test_version_ssot.py` 가 강제한다).
2. **PR 로 `main` 머지** — `main` 은 보호 브랜치라 직접 push 가 거부된다. 머지할 커밋에서 두 레인이
   통과해야 한다(위 정의) — 그 커밋을 PR head 로 올린 뒤 리눅스 레인 워크플로의 한 실행(두 잡)과, CUDA
   호스트에서 그 SHA 로 `pack` 한 `scripts/cuda_lane.py` 의 실행.

   ```bash
   gh workflow run linux-lane.yml --repo aldegad/sprite-gen-lane -f sha=<SHA> [-f ref=<브랜치나 PR>]
   ```
3. **태그 push** — 코드네임 접두사 없는 `vX.Y.Z` 로.

   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

4. **레인이 태그 커밋을 패키징한다** — 태그가 가리키는 SHA 로 워크플로를 `-f artifact=true` 와 함께 돌리고,
   산출물을 빈 디렉터리로 받는다.

   ```bash
   gh workflow run linux-lane.yml --repo aldegad/sprite-gen-lane -f sha=<태그 SHA> -f artifact=true
   gh run download <실행 id> --repo aldegad/sprite-gen-lane -n package-<태그 SHA> -D <빈 디렉터리>
   ```

   `suite` 잡의 `scripts/linux_lane.sh --artifact` 가 게이트를 통과한 뒤에만 wheel·sdist·`SHA256SUMS`·
   `SOURCE_SHA`(그 커밋) 네 파일을 쓰고, 워크플로는 그 넷만 `package-<SHA>` 산출물로 14일 동안 남긴다. 같은
   실행의 `rife` 잡도 초록이어야 레인이 통과한 것이다. 받은 디렉터리를 페이지를 낼 기계에서 쓴다.
5. **페이지를 낸다** — 태그가 있는 체크아웃에서.

   ```bash
   .venv/bin/python scripts/release_publish.py --tag vX.Y.Z --prebuilt <가져온 디렉터리>
   ```

   `SOURCE_SHA` 가 태그 커밋이고 `SHA256SUMS` 가 두 아카이브와 맞을 때만 붙인다. 그 버전의
   CHANGELOG 절을 본문으로, 절이 이름 댄 GIF 를 자산으로 함께 붙여 릴리즈를 생성한다.
6. **확인은 `gh release view`** — 태그를 push 한 것으로 끝이 아니다. 이게 성공해야 릴리즈가 끝난 것이다.

   ```bash
   gh release view vX.Y.Z --repo aldegad/sprite-gen
   ```

## 이미 있는 릴리즈는 건드리지 않는다

이미 페이지가 있는 태그로 스크립트를 다시 돌리면 **아무것도 만들지 않고 아무것도 고치지 않고**
통과한다(`… already has a release page …; body and assets left untouched.`). 실패한 실행을 다시
돌려도 공개된 페이지를 덮어쓰지 않는다는 뜻이고, 반대로 **이미 공개된 페이지의 본문·자산을 이
스크립트로 고칠 수는 없다** — 고칠 일이 생기면 `gh release edit` / `gh release upload` 로 직접 한다.

## 쇼케이스 GIF 는 레포에 커밋된 것만 붙는다

스크립트가 자산으로 첨부하는 GIF 는 **두 조건을 모두 만족한 것뿐**이다:

- 그 버전의 CHANGELOG 절이 `docs/assets/<이름>.gif` 로 이름을 댄다 (본문의 맨 `<이름>.gif` 도
  `docs/assets/` 안에 그 파일이 있으면 같은 것으로 본다), **그리고**
- 그 파일이 레포에 커밋돼 있다.

즉 **레포 밖에서 만든 GIF 는 게이트가 붙이지 못한다.** v2.5.4 처럼 쇼케이스 클립을 레포에 커밋하지
않고 릴리즈 자산으로만 올리는 경우, 그 GIF 는 릴리즈가 생성된 뒤 손으로 올린다:

```bash
gh release upload vX.Y.Z <clip>.gif --repo aldegad/sprite-gen
```

본문에서 그 클립을 보여주려면 릴리즈 다운로드 URL
(`https://github.com/aldegad/sprite-gen/releases/download/vX.Y.Z/<clip>.gif`)로 임베드하고,
공개 후 그 URL 이 200 인지 확인한다. 반대로 CHANGELOG 절이 `docs/assets/…` 경로를 이름 댔는데 그
파일이 체크아웃에 없으면, 죽은 이미지가 달린 페이지를 내보내는 대신 **스크립트가 멈춘다.**

## 태그 없이 미리 돌려보기

`--dry-run` 으로 같은 판정을 돌린다. 태그도, 릴리즈도, 업로드도 없이 제목·본문·첨부 목록만 찍는다.
`--checkout` 의 `CHANGELOG.md` 를 읽으므로, 릴리즈 커밋을 준비하는 브랜치에서 본문을 미리 읽어 볼 수
있다. `--prebuilt` 없이 돌리면 그 자리에서 빌드하므로, 빌드하지 않는 기계에서는 `--skip-build`(GIF
만 붙는 목록)로 판정만 본다.

## 스크립트가 멈추는 경우

전부 "조용히 이상한 페이지" 대신 실패를 고른 지점이다.

- 태그에 해당하는 `## vX.Y.Z` 절이 `CHANGELOG.md` 에 없다 (있는 절 목록을 같이 찍는다).
- 그 절이 비어 있다.
- 절이 이름 댄 `docs/assets/…gif` 가 체크아웃에 없다.
- `--prebuilt` 디렉터리가 wheel 하나·sdist 하나·`SHA256SUMS`·`SOURCE_SHA` 말고 다른 것을 담고
  있거나, 아카이브 버전이 태그와 다르거나, `SOURCE_SHA` 가 태그 커밋이 아니거나, `SHA256SUMS` 가
  아카이브와 맞지 않는다 — 다른 커밋의 빌드나 낡은 아카이브가 이번 릴리즈 자산으로 섞이는 것을 막는다.
- 그 자리에서 빌드할 때 빌드 디렉터리가 비어 있지 않다 — 같은 이유다.
- `gh` 가 "릴리즈가 있다/없다" 를 답하지 못했다 (토큰 없음, 네트워크 끊김, 5xx). **모르는 답은
  '없음' 으로 읽지 않는다** — 그대로 멈춘다.

## `release not found` 는 "레포 없음" 이기도 하다

`gh release view` 는 **없는 레포·권한 없는 레포**에 대고 물어도 똑같이 `release not found` 로 답한다.
"릴리즈가 아직 없다" 와 "레포 이름을 잘못 썼다" 가 같은 문장인 셈이다. 스크립트의 기본 `--repo` 는
`aldegad/sprite-gen` 이라 보통은 이 모호함에 도달하지 않고, 도달하더라도 뒤따르는 생성이 크게
실패한다. `--repo` 를 손으로 줄 때만 주의하면 된다 — 오타가 "아직 릴리즈가 없네" 로 읽힌다. `gh` 의
입자도 한계이지 스크립트가 고칠 수 있는 것이 아니다.

## Related

- [docs/README.md](README.md) — documentation index
- `scripts/linux_lane.sh` — the test gates and the release build, run per commit by
  [aldegad/sprite-gen-lane](https://github.com/aldegad/sprite-gen-lane)'s workflow on GitHub-hosted runners
- `scripts/cuda_lane.py` — the torch backend's CUDA gates, on the maintainer's NVIDIA host
