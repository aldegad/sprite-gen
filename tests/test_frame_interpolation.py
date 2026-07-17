# SPDX-License-Identifier: Apache-2.0
"""AI 프레임 보간(sprite_gen.interpolate) 배관 회귀.

실 생성(provider CLI)은 CI 에서 돌리지 않는다 — interpolator 주입 시그니처로
배관만 검증: 정합 캔버스 계약(/32 패딩·동일 크기·크로마 배경), 테이크 raw 기록,
request 갱신 멱등성, 프롬프트 조립, 인덱스/라벨/프로바이더 검증의 요란한 실패.
"""

import json
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.interpolate import aligned_pair_on_chroma, interpolate_between

MAGENTA = (255, 0, 255)


def _figure(canvas: Image.Image, left: int, top: int, eye_open: bool) -> None:
    for y in range(top, top + 40):
        for x in range(left, left + 24):
            canvas.putpixel((x, y), (90, 60, 30, 255))
    eye_rows = range(top + 10, top + 14) if eye_open else range(top + 12, top + 14)
    for y in eye_rows:
        for x in (left + 6, left + 16):
            canvas.putpixel((x, y), (10, 10, 10, 255))


def _build_run(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    (run / "raw").mkdir(parents=True)
    request = {
        "version": 1, "kind": "sprite-gen-request", "engine": "component-row",
        "character": {"id": "tween-test", "description": "plumbing fixture"},
        "cell": {"shape": "square", "size": 64, "safe_margin": 6},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": list(MAGENTA)},
        "states": {"wave": {"frames": 2, "fps": 4, "loop": True, "action": "test"}},
    }
    (run / "sprite-request.json").write_text(json.dumps(request), encoding="utf-8")
    strip = Image.new("RGBA", (160, 64), MAGENTA + (255,))
    _figure(strip, 12, 12, eye_open=True)
    _figure(strip, 92, 12, eye_open=False)
    strip.save(run / "raw" / "wave.png")
    return run


def _blend_stub(img0: Image.Image, img1: Image.Image, t: float, prompt: str) -> Image.Image:
    assert img0.size == img1.size
    assert "IN-BETWEEN" in prompt and "#FF00FF" in prompt  # 프롬프트 조립 검증
    return Image.blend(img0.convert("RGB"), img1.convert("RGB"), t)


def test_aligned_pair_contract(tmp_path: Path) -> None:
    run = _build_run(tmp_path)
    strip = Image.open(run / "raw" / "wave.png").convert("RGBA")
    img0, img1 = aligned_pair_on_chroma(strip, 2, 0, 1, MAGENTA)
    assert img0.size == img1.size
    assert img0.width % 32 == 0 and img0.height % 32 == 0
    assert img0.getpixel((0, 0)) == MAGENTA  # 크로마 배경 상수
    assert img0.mode == "RGB" and img1.mode == "RGB"


def test_interpolate_writes_take_and_updates_request(tmp_path: Path) -> None:
    run = _build_run(tmp_path)
    target = interpolate_between(run, "wave", 0, 1, t=0.5, label="mid",
                                 interpolator=_blend_stub)
    assert target == run / "raw" / "wave.takes" / "mid.png"
    assert target.is_file()
    request = json.loads((run / "sprite-request.json").read_text(encoding="utf-8"))
    assert request["states"]["wave"]["takes"] == [{"label": "mid", "frames": 1}]
    # 같은 라벨 재실행 = 덮어쓰기 멱등 (takes 항목이 불어나지 않는다)
    interpolate_between(run, "wave", 0, 1, t=0.5, label="mid", interpolator=_blend_stub)
    request = json.loads((run / "sprite-request.json").read_text(encoding="utf-8"))
    assert request["states"]["wave"]["takes"] == [{"label": "mid", "frames": 1}]


def test_interpolate_rejects_bad_inputs(tmp_path: Path) -> None:
    run = _build_run(tmp_path)
    with pytest.raises(SystemExit, match="unknown state"):
        interpolate_between(run, "nope", 0, 1, interpolator=_blend_stub)
    with pytest.raises(SystemExit, match="out of range"):
        interpolate_between(run, "wave", 0, 5, interpolator=_blend_stub)
    with pytest.raises(SystemExit, match="inside"):
        interpolate_between(run, "wave", 0, 1, t=1.5, interpolator=_blend_stub)
    with pytest.raises(SystemExit, match="filesystem-safe"):
        interpolate_between(run, "wave", 0, 1, label="a/b", interpolator=_blend_stub)


def test_unknown_provider_rejected(tmp_path: Path) -> None:
    from sprite_gen.interpolate import gen_interpolator
    with pytest.raises(SystemExit, match="unknown interpolation provider"):
        gen_interpolator("rife")


def test_tween_scale_normalized_to_reference_pair(tmp_path: Path) -> None:
    """실사고 2026-07-17 회귀 (tween 32×58 — 형제 28×48 보다 14% 커짐, 수홍 발견).

    생성형 백엔드는 피사체 크기를 보장하지 않는다 — 중간 프레임의 콘텐츠 높이를
    참조 쌍 평균에 맞춰 리스케일해 테이크로 기록해야 한다."""
    from sprite_gen.interpolate import normalize_tween_scale, _chroma_content_bbox
    chroma = MAGENTA

    def frame_with_figure(height: int) -> Image.Image:
        im = Image.new("RGB", (160, 200), chroma)
        for y in range(200 - height, 200 - 4):
            for x in (70, 90):
                for dx in range(12):
                    im.putpixel((x + dx, y), (90, 60, 30))
        return im

    img0 = frame_with_figure(120)
    img1 = frame_with_figure(124)
    oversized = frame_with_figure(170)  # 모델이 피사체를 크게 그린 경우
    fixed = normalize_tween_scale(oversized, img0, img1, chroma)
    assert fixed.size == img0.size
    box = _chroma_content_bbox(fixed, chroma)
    got_h = box[3] - box[1]
    assert abs(got_h - 118) <= 2, f"normalized content height {got_h}, expected ~118 (mean of 116/120)"


def test_interpolate_normalizes_stub_output(tmp_path: Path) -> None:
    """interpolate_between 경로에서도 정규화가 걸린다 — 스텁이 큰 출력을 내도 테이크는 참조 스케일."""
    from sprite_gen.interpolate import _chroma_content_bbox
    run = _build_run(tmp_path)

    def oversized_stub(img0: Image.Image, img1: Image.Image, t: float, prompt: str) -> Image.Image:
        big = img0.convert("RGB").resize((img0.width * 2, img0.height * 2), Image.Resampling.NEAREST)
        return big

    target = interpolate_between(run, "wave", 0, 1, t=0.5, label="mid", interpolator=oversized_stub)
    took = Image.open(target)
    strip = Image.open(run / "raw" / "wave.png").convert("RGBA")
    img0, _ = aligned_pair_on_chroma(strip, 2, 0, 1, MAGENTA)
    assert took.size == img0.size
    ref_h = (lambda b: b[3] - b[1])(_chroma_content_bbox(img0, MAGENTA))
    mid_h = (lambda b: b[3] - b[1])(_chroma_content_bbox(took, MAGENTA))
    assert abs(mid_h - ref_h) <= 2, f"take content height {mid_h} vs reference {ref_h}"
