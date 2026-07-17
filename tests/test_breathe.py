# SPDX-License-Identifier: Apache-2.0
"""결정론 호흡 생성(sprite_gen.breathe) 회귀 — 정수 행 시프트 불변량."""

from PIL import Image

from sprite_gen.breathe import breathe_frames, compose_take_strip, TAKE_SCALE


def _figure() -> Image.Image:
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for y in range(10, 58):          # 콘텐츠 h=48
        for x in range(20, 44):
            im.putpixel((x, y), (90, 60, 30, 255))
    return im


def _bbox(im):
    return im.split()[3].getbbox()


def test_single_squash_keeps_feet_and_compresses() -> None:
    src = _figure()
    (ex,) = breathe_frames(src, split=0.55)
    b0, b1 = _bbox(src), _bbox(ex)
    assert b1[3] == b0[3], "발(하단)은 고정"
    assert b1[1] == b0[1] + 1, "상단은 1px 하강 (압축)"


def test_two_band_lag_keeps_height_then_compresses() -> None:
    src = _figure()
    p1, p2 = breathe_frames(src, split=0.55, two_band=True, head_split=0.32)
    b0 = _bbox(src)
    assert _bbox(p1)[1] == b0[1] and _bbox(p1)[3] == b0[3], "P1: 머리 제자리(목 스트레치)"
    assert _bbox(p2)[1] == b0[1] + 1 and _bbox(p2)[3] == b0[3], "P2: 전체 1px 하강"


def test_take_strip_scale_roundtrip() -> None:
    src = _figure()
    strip = compose_take_strip([src, src], {"size": 64}, (255, 0, 255))
    assert strip.size == (64 * TAKE_SCALE * 2, 64 * TAKE_SCALE)
    # 업스케일은 NEAREST — 블록이 정확히 TAKE_SCALE 배 (무손실 왕복 전제)
    px = strip.getpixel((20 * TAKE_SCALE, 10 * TAKE_SCALE))
    assert px[:3] == (90, 60, 30)
