# SPDX-License-Identifier: Apache-2.0
"""결정론 호흡(idle breathing) 프레임 생성 — 정수 행 시프트 스쿼시.

생성형이 원리적으로 못 하는 문제(정체성 100% + 1px 통제 변화)의 수학적 해법
(수홍 확정 2026-07-17): 가슴선 위 행들을 날숨에 1px 내리고 발은 고정한다.
정수 픽셀 행 이동은 픽셀 격자에 닫혀 있어 팔레트·아웃라인·격자가 절대 안 깨진다.
스타듀류 손그림 아이들의 프레임 델타와 동형.

두 모드:
- 기본(single): exhale 1프레임 — 가슴선(`--split`, 콘텐츠 높이 비율) 위 전체가 1px 다운.
- `--two-band`: 2프레임 — P1 = 어깨~가슴 밴드만 먼저 다운(목 1행 스트레치로 메움,
  머리는 반 박자 지연), P2 = 머리까지 전체 다운. 사이클 [0,0,P1,P2,P2,P1] 이 유기적.

산출은 도크트린대로 **테이크**(`raw/<state>.takes/<label>.png`, 크로마 합성 스트립)로
기록되고 논리 프레임은 결정론 추출이 굽는다. AI 개입 0.

사용:
    python3 scripts/breathe_frames.py --run-dir <run> --state down_idle \
        [--frame 0] [--split 0.55] [--two-band [--head-split 0.32]] \
        [--amplitude 1] [--label breathe] [--extract]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image

from .interpolate import write_take
from .layout import row_frame_rel


def _content_top_bottom(frame: Image.Image) -> tuple[int, int]:
    box = frame.split()[3].getbbox()
    if not box:
        raise SystemExit("breathe: frame has no content")
    return box[1], box[3]


def shift_above(frame: Image.Image, split_y: int, amplitude: int = 1) -> Image.Image:
    """split_y 위의 모든 행을 amplitude px 아래로 — 경계 행이 눌리며 압축(스쿼시)."""
    top, _ = _content_top_bottom(frame)
    out = frame.copy()
    region = frame.crop((0, top, frame.width, split_y))
    out.paste(Image.new("RGBA", (frame.width, split_y - top), (0, 0, 0, 0)), (0, top))
    out.alpha_composite(region, (0, top + amplitude))
    return out


def shift_band(frame: Image.Image, y0: int, y1: int, amplitude: int = 1) -> Image.Image:
    """[y0, y1) 밴드만 amplitude px 아래로 — 위 경계는 y0 행 복제(스트레치)로 메운다.

    two-band 의 P1: 어깨·가슴이 먼저 내려가고 머리는 제자리 → 목이 1행 늘어난다
    (다음 프레임 P2 에서 머리가 따라 내려오며 해소 — 살아있는 지연감)."""
    out = frame.copy()
    band = frame.crop((0, y0, frame.width, y1))
    out.alpha_composite(band, (0, y0 + amplitude))
    stretch = frame.crop((0, y0, frame.width, y0 + 1)).resize(
        (frame.width, amplitude), Image.Resampling.NEAREST)
    out.alpha_composite(stretch, (0, y0))
    return out


def breathe_frames(frame: Image.Image, split: float = 0.55, two_band: bool = False,
                   head_split: float = 0.32, amplitude: int = 1) -> list[Image.Image]:
    """호흡 프레임(들) 생성. single = [exhale], two-band = [P1, P2]."""
    if not 0.0 < split < 1.0:
        raise SystemExit(f"breathe: split must be inside (0, 1): {split}")
    top, bottom = _content_top_bottom(frame)
    chest = top + int((bottom - top) * split)
    if not two_band:
        return [shift_above(frame, chest, amplitude)]
    if not 0.0 < head_split < split:
        raise SystemExit(f"breathe: head-split must be inside (0, split): {head_split}")
    head_end = top + int((bottom - top) * head_split)
    p1 = shift_band(frame, head_end, chest, amplitude)
    p2 = shift_above(frame, chest, amplitude)
    return [p1, p2]


TAKE_SCALE = 8  # 테이크 raw 업스케일 배율 — 추출 피치 검출이 8 을 정확히 잡고
                # 논리로 되돌아오는 무손실 왕복 (논리 1px 스트립은 검출이 헛피치를 잡음)


def compose_take_strip(frames: list[Image.Image], cell: dict[str, Any],
                       chroma_rgb: tuple[int, int, int]) -> Image.Image:
    """프레임들을 크로마 배경 raw 스트립으로 — 추출이 컴포넌트로 다시 분리한다.

    논리 해상도 프레임을 ×TAKE_SCALE NEAREST 업스케일해 넣는다: 추출의 피치 검출이
    정수 배율을 그대로 잡아 원본 논리 픽셀로 무손실 복귀한다 (실사고 2026-07-17:
    1px 스트립은 5.96 같은 헛피치로 잘려 28픽셀 조각이 됐다)."""
    cw = int(cell.get("width") or cell.get("size"))
    ch = int(cell.get("height") or cell.get("size"))
    s = TAKE_SCALE
    strip = Image.new("RGBA", (cw * s * len(frames), ch * s), chroma_rgb + (255,))
    for index, frame in enumerate(frames):
        cellf = frame if frame.size == (cw, ch) else frame.resize((cw, ch), Image.Resampling.NEAREST)
        strip.alpha_composite(
            cellf.resize((cw * s, ch * s), Image.Resampling.NEAREST), (index * cw * s, 0))
    return strip


def generate(run_dir: Path | str, state: str, frame_index: int = 0, split: float = 0.55,
             two_band: bool = False, head_split: float = 0.32, amplitude: int = 1,
             label: str | None = None) -> Path:
    """상태의 추출 프레임에서 호흡 테이크를 만들어 기록한다. 반환 = 테이크 raw 경로."""
    run_dir = Path(run_dir)
    request_path = run_dir / "sprite-request.json"
    if not request_path.is_file():
        raise SystemExit(f"not a sprite-gen run dir (no sprite-request.json): {run_dir}")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if state not in request.get("states", {}):
        raise SystemExit(f"unknown state: {state}")
    manifest_path = run_dir / "frames" / "frames-manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("breathe: extract the run first (no frames manifest)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    row = next((r for r in manifest.get("rows", []) if r.get("state") == state), None)
    if row is None:
        raise SystemExit(f"breathe: state '{state}' not in frames manifest")
    frame_rel = row_frame_rel(row, frame_index)
    with Image.open(run_dir / frame_rel) as opened:
        frame = opened.convert("RGBA")
    frames = breathe_frames(frame, split=split, two_band=two_band,
                            head_split=head_split, amplitude=amplitude)
    chroma_rgb = tuple(request["chroma_key"]["rgb"])
    strip = compose_take_strip(frames, request["cell"], chroma_rgb)
    label = label or ("breathe2" if two_band else "breathe")
    if "/" in label or label.startswith("."):
        raise SystemExit(f"take label must be filesystem-safe: {label!r}")
    target = write_take(run_dir, request, state, label, strip)
    # write_take 는 frames=1 로 기록 — 스트립 프레임 수로 갱신
    request = json.loads(request_path.read_text(encoding="utf-8"))
    for take in request["states"][state]["takes"]:
        if take.get("label") == label:
            take["frames"] = len(frames)
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print(f"[breathe] take written: {target.relative_to(run_dir)} "
          f"({len(frames)} frame(s), split={split}, two_band={two_band}, amp={amplitude})")
    return target


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--frame", type=int, default=0, help="소스 프레임 인덱스 (기본 0)")
    parser.add_argument("--split", type=float, default=0.55, help="가슴선 (콘텐츠 높이 비율)")
    parser.add_argument("--two-band", action="store_true", help="머리 지연 2프레임 모드")
    parser.add_argument("--head-split", type=float, default=0.32, help="머리/어깨 경계 (two-band)")
    parser.add_argument("--amplitude", type=int, default=1, help="시프트 픽셀 수 (기본 1)")
    parser.add_argument("--label", default=None, help="테이크 라벨 (기본 breathe / breathe2)")
    parser.add_argument("--extract", action="store_true", help="기록 후 전체 배치 재추출")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    generate(args.run_dir, args.state, frame_index=args.frame, split=args.split,
             two_band=args.two_band, head_split=args.head_split,
             amplitude=args.amplitude, label=args.label)
    if args.extract:
        from . import extract as extract_module
        code = extract_module.run(run_dir=Path(args.run_dir))
        if code != 0:
            return code
        print("[breathe] full-batch re-extraction done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
