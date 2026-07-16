# SPDX-License-Identifier: Apache-2.0
"""AI 프레임 보간 — 한 상태의 두 프레임 사이 중간(in-between) 프레임을 만들어 테이크로 기록한다.

파이프라인 도크트린과의 관계: "AI 개입은 raw 생성 한 곳뿐" — 보간도 raw 단계의
AI 생성이다. 산출물은 최종 프레임이 아니라 **테이크 raw** (`raw/<state>.takes/<label>.png`
+ request `states.<state>.takes`) 로 기록되고, 논리 프레임은 언제나 기존 결정론
추출 경로(크로마 제거 → 컴포넌트 → 격자 스냅 → 팔레트)가 굽는다.

모델 = RIFE 4.9 ensemble ONNX (artkit 프레임 보간과 동일 모델, 수홍 확정 2026-07-17
"현재 RIFE 가 최선" — 후속 VFI(RIFE 4.2x/FILM/EMA-VFI)가 있으나 2프레임 픽셀아트
미세모션은 픽셀퍼펙트 재양자화가 품질을 결정해 이 조합이 실용 최적).
모델 파일은 캐시( `~/.cache/sprite-gen/` )에 없으면 다운로드한다 — 실패는 요란하게.

사용:
    python3 scripts/interpolate_frames.py --run-dir <run> --state down_idle \
        --between 1 2 [--t 0.5] [--label blink_mid] [--extract]

- `--between A B`: 그 상태 primary 스트립의 프레임 인덱스 두 개 (추출된 컴포넌트 순서).
- `--t`: 보간 시점 (기본 0.5 = 정중앙).
- `--label`: 테이크 라벨 (기본 `tween_<A>_<B>_t<t>`). 같은 라벨 재실행 = 같은 파일
  덮어쓰기 (멱등).
- `--extract`: 기록 후 **전체 배치** 재추출까지 수행. 부분 추출은 run-wide 팔레트가
  배치 구성에 따라 달라지므로(CHANGELOG v1.56.17 known limitation) 전체 배치만 지원.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .extract import (extract_component_images, register_row_frames,
                      remove_chroma_background_ycbcr, tighten_components)
from .layout import raw_rel, take_raw_rel

RIFE_MODEL_URL = (
    "https://huggingface.co/yuvraj108c/rife-onnx/resolve/main/"
    "rife49_ensemble_True_scale_1_sim.onnx"
)
RIFE_MODEL_MIN_BYTES = 10_000_000  # 잘린 다운로드(HTML 오류 페이지 등) 요란한 거부용 하한

# interpolator 시그니처: (img0 RGB, img1 RGB, t) -> mid RGB. 테스트는 스텁을 주입한다.
Interpolator = Callable[[Image.Image, Image.Image, float], Image.Image]


def model_cache_path() -> Path:
    return Path.home() / ".cache" / "sprite-gen" / Path(RIFE_MODEL_URL).name


def ensure_model(path: Path | None = None) -> Path:
    """RIFE ONNX 모델 확보. 없으면 다운로드, 실패/절단은 SystemExit (No Silent Fallback)."""
    target = path or model_cache_path()
    if target.is_file() and target.stat().st_size >= RIFE_MODEL_MIN_BYTES:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    print(f"[interpolate] downloading RIFE model -> {target}", file=sys.stderr)
    try:
        urllib.request.urlretrieve(RIFE_MODEL_URL, tmp)
    except OSError as exc:
        raise SystemExit(f"RIFE model download failed: {exc} ({RIFE_MODEL_URL})")
    if tmp.stat().st_size < RIFE_MODEL_MIN_BYTES:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"RIFE model download truncated (<{RIFE_MODEL_MIN_BYTES} bytes): {RIFE_MODEL_URL}")
    tmp.replace(target)
    return target


def rife_interpolator(model_path: Path | None = None) -> Interpolator:
    """실 모델 interpolator. onnxruntime 미설치는 설치 힌트와 함께 요란하게 실패한다."""
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        raise SystemExit(
            f"frame interpolation needs onnxruntime ({exc}). "
            "install: pip install 'sprite-gen[interpolate]' or pip install onnxruntime")
    session = ort.InferenceSession(str(ensure_model(model_path)),
                                   providers=["CPUExecutionProvider"])

    def run(img0: Image.Image, img1: Image.Image, t: float) -> Image.Image:
        if img0.size != img1.size:
            raise SystemExit(f"interpolation inputs differ in size: {img0.size} vs {img1.size}")

        def tensor(im: Image.Image):
            arr = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
            return arr.transpose(2, 0, 1)[None]

        out = session.run(None, {
            "img0": tensor(img0), "img1": tensor(img1),
            "timestep": np.array([t], dtype=np.float32),
        })[0]
        mid = (np.clip(out[0].transpose(1, 2, 0), 0.0, 1.0) * 255.0).round().astype(np.uint8)
        return Image.fromarray(mid, "RGB")

    return run


def aligned_pair_on_chroma(
    strip: Image.Image, frame_count: int, index_a: int, index_b: int,
    chroma_rgb: tuple[int, int, int],
) -> tuple[Image.Image, Image.Image]:
    """스트립에서 두 프레임 컴포넌트를 뽑아 상체 정합(register) 후 같은 캔버스에 앉힌다.

    정합이 핵심이다: 정적인 몸 픽셀이 두 입력에서 같은 위치에 있어야 보간이 움직인
    부위(눈꺼풀 등)만 morph 하고 몸을 이중상(ghost)으로 만들지 않는다. 캔버스는
    RIFE 제약대로 32 배수로 패딩하고 배경은 요청 크로마 단색 — 배경이 두 입력에서
    상수라 보간 결과에서도 상수로 남고, 추출의 크로마 제거가 그대로 처리한다.
    """
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
    run = interpolator or rife_interpolator()
    mid = run(img0, img1, t)
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
    parser.add_argument("--t", type=float, default=0.5, help="보간 시점 (0..1, 기본 0.5)")
    parser.add_argument("--label", default=None, help="테이크 라벨 (기본 tween_<A>_<B>_t<t>)")
    parser.add_argument("--model", default=None, help="RIFE ONNX 경로 (기본: 캐시, 없으면 다운로드)")
    parser.add_argument("--extract", action="store_true",
                        help="기록 후 전체 배치 재추출까지 수행 (부분 추출은 팔레트 배치 결합 때문에 지원하지 않음)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    interpolator = rife_interpolator(Path(args.model) if args.model else None)
    interpolate_between(args.run_dir, args.state, args.between[0], args.between[1],
                        t=args.t, label=args.label, interpolator=interpolator)
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
