"""detect_pixel_pitch 를 합성 정답 데이터로 고정한다.

논리 픽셀아트를 정수 배율 k 로 NEAREST 업스케일하면 참 피치는 정확히 k 다.
예전 구현은 `w = 1 if p >= 8 else 0` 때문에 k=8,10,12,14 에서 약수 k/2 를
반환했다 (창이 열린 참 피치의 우연 기대치가 3/p 로 부풀어, 창이 닫힌 약수에
졌다). 이 테스트가 그 회귀를 막는다.
"""
import random

import pytest
from PIL import Image

from sprite_gen.extract import (
    detect_pixel_grid,
    detect_pixel_pitch,
    grid_snap_downscale,
    _grid_edges,
    _grid_phase,
)

PALETTE = [
    (240, 210, 175),
    (60, 40, 30),
    (40, 90, 180),
    (230, 225, 200),
    (150, 90, 50),
    (20, 20, 20),
]


def _logical_art(width: int = 24, height: int = 40, seed: int = 11) -> Image.Image:
    """비주기 무작위 도트. 주기적 패턴이면 약수도 '진짜' 격자가 되어 테스트가 무의미해진다."""
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        for x in range(width):
            px[x, y] = rng.choice(PALETTE)
    return img


def test_integer_pitch_is_detected_exactly():
    art = _logical_art()
    for k in (4, 6, 8, 10, 12, 14, 16, 17, 20, 24, 32):
        upscaled = art.resize((art.width * k, art.height * k), Image.NEAREST)
        assert detect_pixel_pitch(upscaled) == k, f"pitch {k} misdetected"


def test_divisor_is_not_preferred_over_true_pitch():
    """k=12 에서 6 을 반환하던 회귀의 최소 재현."""
    art = _logical_art()
    upscaled = art.resize((art.width * 12, art.height * 12), Image.NEAREST)
    assert detect_pixel_pitch(upscaled) == 12


def test_phase_follows_crop_offset():
    art = _logical_art()
    k = 16
    upscaled = art.resize((art.width * k, art.height * k), Image.NEAREST)
    for offset in (0, 3, 7, 11):
        cropped = upscaled.crop((offset, offset, upscaled.width, upscaled.height))
        assert detect_pixel_pitch(cropped) == k
        assert _grid_phase(cropped.convert("RGBA"), k)[0] == (-offset) % k


def test_no_grid_falls_back_to_one():
    """격자가 없는 사진 같은 입력은 1(스냅 안 함)로 관측 가능하게 떨어진다."""
    rng = random.Random(3)
    noise = Image.new("RGB", (200, 200))
    px = noise.load()
    for y in range(200):
        for x in range(200):
            px[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    assert detect_pixel_pitch(noise) == 1


def _upscaled(art: Image.Image, scale: float) -> Image.Image:
    """AI 도트처럼 블록 폭이 정수로 안 떨어지는 판을 만든다 (NEAREST 라 색은 원본 그대로)."""
    big = art.resize((art.width * 64, art.height * 64), Image.NEAREST)
    return big.resize((round(art.width * scale), round(art.height * scale)), Image.NEAREST)


def _mismatch(a: Image.Image, b: Image.Image) -> int:
    pa, pb = a.convert("RGB").load(), b.convert("RGB").load()
    return sum(1 for y in range(a.height) for x in range(a.width) if pa[x, y] != pb[x, y])


@pytest.mark.parametrize("scale", [12.0, 14.35, 16.0, 16.2, 17.24, 20.0, 23.7])
def test_fractional_pitch_roundtrips_to_the_original_logical_art(scale):
    """소수 배율로 늘린 도트를 스냅하면 원본 논리 픽셀이 그대로 돌아와야 한다.

    정수 피치만 보던 예전에는 배율 16.2 / 17.24 에서 셀이 밀려 크기부터 틀렸다
    (25x41 등). 측정은 소수, 격자선은 길이 등분 -> 결과는 항상 정수 격자다.
    """
    art = _logical_art()
    upscaled = _upscaled(art, scale)
    pitch, phase = detect_pixel_grid(upscaled)
    snapped = grid_snap_downscale(upscaled, pitch, phase=phase)

    assert snapped.size == art.size, f"scale {scale}: {snapped.size} != {art.size}"
    assert abs(pitch[0] - scale) < 0.1, f"scale {scale}: detected {pitch[0]:.3f}"
    assert abs(pitch[1] - scale) < 0.1, f"scale {scale}: detected {pitch[1]:.3f}"
    # 소수 배율은 블록 경계가 화면 픽셀 중간에 걸리므로 1% 이내의 색 불일치는 허용한다.
    assert _mismatch(snapped, art) <= art.width * art.height // 100


def test_integer_pitch_still_snaps_exactly():
    art = _logical_art()
    for scale in (12, 16, 20):
        upscaled = _upscaled(art, float(scale))
        pitch, phase = detect_pixel_grid(upscaled)
        snapped = grid_snap_downscale(upscaled, pitch, phase=phase)
        assert snapped.size == art.size
        assert _mismatch(snapped, art) == 0


@pytest.mark.parametrize("fringe", [1, 7, 14, 20])
def test_non_integer_bbox_does_not_stretch_the_grid(fringe):
    """bbox 가 블록의 정수배가 아니어도 셀 폭은 참 피치를 지켜야 한다.

    v1.56.2 회귀: `_grid_edges` 가 length 를 셀 개수로 등분했다. AA 프린지 때문에 bbox 가
    27.46 블록이면 셀이 31.44px 로 늘어나(참 블록 30.92px) 칸마다 0.52px 씩 어긋났고,
    오른쪽 끝에서 반 블록이 밀려 스냅 결과의 얼굴이 부서졌다 (솔벨 주인공 chibi-8).
    """
    art = _logical_art(width=24, height=30)
    k = 31
    upscaled = art.resize((art.width * k, art.height * k), Image.NEAREST)
    # 오른쪽에 블록의 정수배가 아닌 자투리를 붙인다 (AA 프린지 흉내)
    padded = Image.new("RGB", (upscaled.width + fringe, upscaled.height), (20, 20, 20))
    padded.paste(upscaled, (0, 0))

    pitch, phase = detect_pixel_grid(padded)
    edges = _grid_edges(padded.width, pitch[0], phase[0])
    widths = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]

    # 마지막 셀만 자투리를 흡수한다. 나머지는 전부 참 피치 ±1px.
    for w in widths[:-1]:
        assert abs(w - k) <= 1, f"cell width {w} drifted from pitch {k} (widths={widths})"


def test_pitch_is_detected_per_axis():
    """가로/세로 블록 크기가 다르면 축별로 따로 잡아야 한다.

    비균등 리스케일된 생성물은 가로 블록과 세로 블록이 어긋난다 (솔벨 chibi 베이스:
    가로 30.38 / 세로 30.92). 한 피치를 두 축에 강제하면 한 축이 통째로 미끄러졌다
    — 실측 가로 정렬률 11.7%.
    """
    art = _logical_art(width=20, height=24)
    upscaled = art.resize((art.width * 24, art.height * 30), Image.NEAREST)

    (pitch_x, pitch_y), _ = detect_pixel_grid(upscaled)

    assert abs(pitch_x - 24) < 0.6, f"x pitch {pitch_x:.2f} != 24"
    assert abs(pitch_y - 30) < 0.6, f"y pitch {pitch_y:.2f} != 30"


def test_non_square_pitch_roundtrips():
    art = _logical_art(width=20, height=24)
    upscaled = art.resize((art.width * 24, art.height * 30), Image.NEAREST)
    pitch, phase = detect_pixel_grid(upscaled)
    snapped = grid_snap_downscale(upscaled, pitch, phase=phase)
    assert snapped.size == art.size
    assert _mismatch(snapped, art) == 0


def test_wildly_disagreeing_axes_fall_back_to_the_trusted_axis():
    """한 축의 검출이 무너지면(참 피치의 약수) 엣지가 많은 축의 피치를 쓴다.

    솔벨 down_carry_walk 실사고: 팔을 위로 든 포즈는 세로로 균일한 막대가 많아 세로 엣지가
    적고, 그 축에서 참 피치 9 대신 약수 3 이 이겼다 (가로 9 / 세로 3). 스냅 결과가 짓눌렸다.
    축별 피치가 1.5배 넘게 벌어지는 것은 물리적으로 불가능하다 — 비균등 리스케일도 2% 수준이다.
    """
    art = _logical_art(width=20, height=30)
    upscaled = art.resize((art.width * 12, art.height * 12), Image.NEAREST)
    # 아래쪽 절반을 단색으로 덮어 세로 엣지를 고갈시킨다 (긴 균일 막대 흉내)
    flat = Image.new("RGB", (upscaled.width, upscaled.height // 2), (40, 90, 180))
    upscaled.paste(flat, (0, upscaled.height // 2))

    (pitch_x, pitch_y), _ = detect_pixel_grid(upscaled)

    assert max(pitch_x, pitch_y) / min(pitch_x, pitch_y) <= 1.5, (
        f"axes disagree wildly: {pitch_x:.2f} vs {pitch_y:.2f}"
    )


def test_synthetic_axis_collapse_is_repaired():
    """한 축 피치를 인위로 약수까지 끌어내려도 최종 반환은 두 축이 붙어 있어야 한다."""
    art = _logical_art(width=24, height=24)
    upscaled = art.resize((art.width * 9, art.height * 9), Image.NEAREST)
    (px, py), _ = detect_pixel_grid(upscaled)
    assert abs(px - py) < 1.0, f"{px:.2f} vs {py:.2f}"
    assert px > 5.0 and py > 5.0, "collapsed to a divisor"
