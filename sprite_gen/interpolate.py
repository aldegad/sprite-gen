# SPDX-License-Identifier: Apache-2.0
"""AI 프레임 보간 — 한 상태의 두 프레임 사이 중간(in-between) 프레임을 만들어 테이크로 기록한다.

파이프라인 도크트린과의 관계: "AI 개입은 raw 생성 한 곳뿐" — 보간도 raw 단계의
AI 생성이다. 산출물은 최종 프레임이 아니라 **테이크 raw** (`raw/<state>.takes/<label>.png`
+ request `states.<state>.takes`) 로 기록되고, 논리 프레임은 언제나 기존 결정론
추출 경로(크로마 제거 → 컴포넌트 → 격자 스냅 → 팔레트)가 굽는다.

백엔드 = **생성형** (`sprite_gen.gen`: codex `image_gen` / grok Imagine, 기본 codex).
플로우 기반 VFI(RIFE)는 파기했다 (수홍 확정 2026-07-17): 외형이 변하는 픽셀아트
보간에서 VFI 는 구조적으로 크로스페이드(블러 잔상)를 내고, 3-way 실측 비교
(founder down_action 팔스윙: codex/grok/RIFE)에서 생성형이 중간 포즈를 깨끗한
픽셀로 그려내 압승했다 — 근거 시트 `tween-3way-compare.png` (솔벨 트레이 2026-07-17).

인증 전제 (GUI 버튼 포함): 생성은 항상 **서버 머신의 provider CLI** (`codex`/`grok`)
가 수행한다 — 브라우저·웹뷰에는 어떤 자격증명도 지나가지 않는다. 해당 CLI 가
이 머신에 설치·로그인(ChatGPT OAuth / xAI OAuth)되어 있어야 하며, 미인증이면
provider 가 요란하게 실패하고 그 메시지가 CLI/뷰 상태줄에 그대로 표면화된다.
설정 절차는 [`docs/gen.md`](../docs/gen.md), 보간 관점 가이드는
[`docs/frame-interpolation.md`](../docs/frame-interpolation.md).

사용:
    python3 scripts/interpolate_frames.py --run-dir <run> --state down_idle \
        --between 1 2 [--provider codex|grok] [--t 0.5] [--label blink_mid] [--extract]

- `--between A B`: 그 상태 primary 스트립의 프레임 인덱스 두 개 (추출된 컴포넌트 순서).
- `--provider`: 생성 백엔드 (기본 codex — 3-way 비교 승자).
- `--t`: 보간 시점 (기본 0.5 = 정중앙). 프롬프트의 포즈 배분 문구로 반영된다.
- `--label`: 테이크 라벨 (기본 `tween_<A>_<B>_t<t>`). 같은 라벨 재실행 = 같은 파일
  덮어쓰기 (멱등).
- `--extract`: 기록 후 **전체 배치** 재추출까지 수행. 부분 추출은 run-wide 팔레트가
  배치 구성에 따라 달라지므로(CHANGELOG v1.56.17 known limitation) 전체 배치만 지원.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .extract import (extract_component_images, register_row_frames,
                      remove_chroma_background_ycbcr, tighten_components)
from .layout import raw_rel, take_raw_rel

PROVIDERS = ("codex", "grok")

# interpolator 시그니처: (img0 RGB, img1 RGB, t, prompt) -> mid RGB. 테스트는 스텁을 주입한다.
Interpolator = Callable[[Image.Image, Image.Image, float, str], Image.Image]


def gen_interpolator(provider: str = "codex") -> Interpolator:
    """생성형 in-between interpolator — 두 정합 프레임을 ref 로 물려 중간 포즈를 그리게 한다."""
    if provider not in PROVIDERS:
        raise SystemExit(f"unknown interpolation provider: {provider} (choose from {PROVIDERS})")
    from .gen import generate_image

    def run(img0: Image.Image, img1: Image.Image, t: float, prompt: str) -> Image.Image:
        workdir = Path(tempfile.mkdtemp(prefix="sprite-gen-tween-"))
        try:
            ref_a = workdir / "ref-a.png"
            ref_b = workdir / "ref-b.png"
            out = workdir / "mid.png"
            img0.save(ref_a)
            img1.save(ref_b)
            generate_image(provider, prompt, out, refs=[ref_a, ref_b])
            with Image.open(out) as image:
                return image.convert("RGB").copy()
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return run


def tween_prompt(request: dict[str, Any], t: float) -> str:
    """보간 생성 프롬프트 — request 의 캐릭터/크로마 진실에서 결정론으로 조립한다."""
    character = str(request.get("character", {}).get("description") or "the same character")
    chroma = request["chroma_key"]
    if abs(t - 0.5) < 1e-9:
        blend = "the precise halfway in-between of A and B"
    else:
        nearer = "A" if t < 0.5 else "B"
        blend = (f"the in-between of A and B at t={t:g} on the A->B motion "
                 f"(closer to {nearer})")
    return (
        "Pixel art animation IN-BETWEEN frame task. Reference image 1 is frame A and "
        "reference image 2 is frame B of the same character's animation. "
        f"Draw exactly ONE full-body pose that is {blend}: limbs and body positioned "
        "between the two reference poses. "
        f"Character identity must match the references exactly ({character}). "
        "Same clean pixel-art style, same pixel block size, same scale, and same "
        "position in the canvas as the references. "
        f"Flat solid chroma {chroma['name']} {chroma['hex']} background filling the "
        "entire canvas. No shadows, no text, no labels, no frame borders, no arrows, "
        "no multiple panels — exactly one figure on the flat background."
    )


def aligned_pair_on_chroma(
    strip: Image.Image, frame_count: int, index_a: int, index_b: int,
    chroma_rgb: tuple[int, int, int],
) -> tuple[Image.Image, Image.Image]:
    """스트립에서 두 프레임 컴포넌트를 뽑아 상체 정합(register) 후 같은 캔버스에 앉힌다.

    정합이 핵심이다: 정적인 몸 픽셀이 두 ref 에서 같은 위치에 있어야 생성 모델이
    "움직인 부위만 다른 같은 그림 두 장" 으로 읽는다. 배경은 요청 크로마 단색 —
    생성 결과도 같은 배경을 유지하도록 프롬프트가 요구하고, 추출의 크로마 제거가
    그대로 처리한다."""
    cleaned = remove_chroma_background_ycbcr(strip, chroma_rgb)
    components = extract_component_images(cleaned, frame_count)
    if not components:
        raise SystemExit(f"could not extract {frame_count} components from the strip")
    for index in (index_a, index_b):
        if not 0 <= index < len(components):
            raise SystemExit(f"frame index {index} out of range 0..{len(components) - 1}")
    tight = tighten_components(components)
    pair = register_row_frames([tight[index_a], tight[index_b]])
    width, height = pair[0].size
    padded_w = (width + 31) // 32 * 32
    padded_h = (height + 31) // 32 * 32
    outputs = []
    for frame in pair:
        canvas = Image.new("RGBA", (padded_w, padded_h), chroma_rgb + (255,))
        canvas.alpha_composite(frame, ((padded_w - width) // 2, padded_h - height))
        outputs.append(canvas.convert("RGB"))
    return outputs[0], outputs[1]


def write_take(run_dir: Path, request: dict[str, Any], state: str, label: str,
               image: Image.Image) -> Path:
    """중간 프레임을 테이크 raw 로 기록하고 request 의 takes 선언을 갱신한다 (멱등)."""
    rel = take_raw_rel(request, state, label)
    target = run_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    takes = request["states"][state].setdefault("takes", [])
    entry = next((t for t in takes if t.get("label") == label), None)
    if entry is None:
        takes.append({"label": label, "frames": 1})
    else:
        entry["frames"] = 1
    (run_dir / "sprite-request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def interpolate_between(run_dir: Path | str, state: str, index_a: int, index_b: int,
                        t: float = 0.5, label: str | None = None,
                        provider: str = "codex",
                        interpolator: Interpolator | None = None) -> Path:
    """두 프레임 사이 중간 프레임을 만들어 테이크로 기록한다. 반환 = 테이크 raw 경로."""
    run_dir = Path(run_dir)
    request_path = run_dir / "sprite-request.json"
    if not request_path.is_file():
        raise SystemExit(f"not a sprite-gen run dir (no sprite-request.json): {run_dir}")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if state not in request.get("states", {}):
        raise SystemExit(f"unknown state: {state}")
    if not 0.0 < t < 1.0:
        raise SystemExit(f"t must be inside (0, 1): {t}")
    strip_path = run_dir / raw_rel(request, state)
    if not strip_path.is_file():
        raise SystemExit(f"raw strip missing: {strip_path}")
    chroma_rgb = tuple(request["chroma_key"]["rgb"])
    frame_count = int(request["states"][state]["frames"])
    strip = Image.open(strip_path).convert("RGBA")
    img0, img1 = aligned_pair_on_chroma(strip, frame_count, index_a, index_b, chroma_rgb)
    run = interpolator or gen_interpolator(provider)
    mid = run(img0, img1, t, tween_prompt(request, t))
    label = label or f"tween_{index_a}_{index_b}_t{t:g}".replace(".", "p")
    if "/" in label or label.startswith("."):
        raise SystemExit(f"take label must be filesystem-safe: {label!r}")
    target = write_take(run_dir, request, state, label, mid)
    print(f"[interpolate] take written: {target.relative_to(run_dir)} "
          f"(state={state}, frames {index_a}<->{index_b}, t={t})")
    return target


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--between", nargs=2, type=int, required=True,
                        metavar=("FROM", "TO"), help="primary 스트립의 프레임 인덱스 두 개")
    parser.add_argument("--provider", choices=PROVIDERS, default="codex",
                        help="생성 백엔드 (기본 codex — 3-way 실측 비교 승자)")
    parser.add_argument("--t", type=float, default=0.5, help="보간 시점 (0..1, 기본 0.5)")
    parser.add_argument("--label", default=None, help="테이크 라벨 (기본 tween_<A>_<B>_t<t>)")
    parser.add_argument("--extract", action="store_true",
                        help="기록 후 전체 배치 재추출까지 수행 (부분 추출은 팔레트 배치 결합 때문에 지원하지 않음)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    interpolate_between(args.run_dir, args.state, args.between[0], args.between[1],
                        t=args.t, label=args.label, provider=args.provider)
    if args.extract:
        from . import extract as extract_module
        code = extract_module.run(run_dir=Path(args.run_dir))
        if code != 0:
            return code
        print("[interpolate] full-batch re-extraction done")
    else:
        print("[interpolate] next: re-extract the FULL batch so the shared palette stays "
              f"consistent -> python3 -m sprite_gen.extract --run-dir {args.run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
