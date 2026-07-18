# SPDX-License-Identifier: Apache-2.0
"""결정론 호흡 레이어(sprite_gen.breathe) 회귀 — 행 시프트·루프-맞춤·서브픽셀 규칙."""

from PIL import Image

from sprite_gen.breathe import (_seam_rows, bake_breathe_sequence, breathe_frames,
                                fit_breathe_pattern, phase_frame)


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


def test_fit_pattern_loop_length_invariant() -> None:
    """루프-맞춤: 패턴 길이 = 시퀀스 길이 (루프 불변)."""
    cfg = {"splits": [0.55], "amplitude": 1, "breaths": 1}
    assert fit_breathe_pattern(6, cfg) == [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
    cfg2 = {"splits": [0.55], "amplitude": 1, "breaths": 2}
    assert fit_breathe_pattern(6, cfg2) == [0.0, 1.0, 1.0, 0.0, 1.0, 1.0]


def test_fit_pattern_two_lines_cascade() -> None:
    cfg = {"splits": [0.32, 0.55], "amplitude": 1, "breaths": 1}
    assert fit_breathe_pattern(6, cfg) == [0.0, 0.0, 1.0, 2.0, 2.0, 1.0]


def test_fit_pattern_exact_requested_count_v2() -> None:
    """v2 (수홍 정정): 요청 횟수 그대로 — 등분 안 되면 나머지를 앞 사이클 쉼에 배분."""
    cfg = {"splits": [0.55], "amplitude": 1, "breaths": 2}
    pattern = fit_breathe_pattern(5, cfg)   # 사이클 [3,2]
    assert len(pattern) == 5
    assert pattern == [0.0, 1.0, 1.0, 0.0, 1.0]
    cfg3 = {"splits": [0.62], "amplitude": 1, "breaths": 3}
    p11 = fit_breathe_pattern(11, cfg3)     # 사이클 [4,4,3] — 정확히 3회 하강
    assert len(p11) == 11
    downs = sum(1 for i, v in enumerate(p11) if v > 0 and p11[i - 1] == 0)
    assert downs == 3


def test_fit_pattern_too_short_is_all_zero() -> None:
    cfg = {"splits": [0.3, 0.55], "amplitude": 1, "breaths": 1}
    assert fit_breathe_pattern(2, cfg) == [0.0, 0.0]  # 사이클 최소 2K 미만 — 호흡 없음 (관측)


def test_fit_pattern_subpixel_preserves_length() -> None:
    cfg = {"splits": [0.55], "amplitude": 1, "breaths": 2, "subpixel": True}
    pattern = fit_breathe_pattern(6, cfg)
    assert len(pattern) == 6, "서브픽셀은 길이 보존 (루프 불변)"
    assert pattern == [0.0, 0.5, 1.0, 0.0, 0.5, 1.0]


def test_bake_sequence_length_unchanged() -> None:
    src = _figure()
    cfg = {"splits": [0.55], "amplitude": 1, "breaths": 2}
    baked, phases = bake_breathe_sequence([src] * 6, cfg)
    assert len(baked) == 6 and len(phases) == 6, "굽기 후에도 루프 길이 불변"


def test_bake_sequence_blink_breathes_too() -> None:
    """깜빡임 프레임(다른 그림)도 같은 위상 변조를 받는다 — 직교 레이어 계약."""
    a = _figure()
    blink = _figure()
    blink.putpixel((30, 20), (0, 0, 0, 255))
    cfg = {"splits": [0.55], "amplitude": 1, "breaths": 1}
    baked, phases = bake_breathe_sequence([a, blink], cfg)
    assert phases == [0.0, 1.0]
    assert _bbox(baked[1])[1] == _bbox(blink)[1] + 1, "깜빡임 프레임이 날숨 위상으로 하강"


def test_subpixel_craft_rules() -> None:
    """서브픽셀 도트 규칙: (1) 이음새 밴드 밖 잔상 0, (2) 반투명 생성 0,
    (3) 중간색은 프레임에 이미 있는 색만 (수홍 확정 2026-07-18)."""
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for y in range(10, 58):
        for x in range(20, 44):
            im.putpixel((x, y), (200, 50, 50, 255) if 20 <= y <= 24 else (90, 60, 30, 255))
    cfg = {"splits": [0.55], "amplitude": 1, "breaths": 1, "subpixel": True}
    base = phase_frame(im, cfg, 0.0)
    half = phase_frame(im, cfg, 0.5)
    band_rows = set()
    for r0, r1 in _seam_rows(10, [10 + int(48 * 0.55)], 1, 64):
        band_rows.update(range(r0, r1))
    base_colors = {p[:3] for p in {base.getpixel((x, y)) for x in range(64) for y in range(64)} if p[3] >= 128}
    for y in range(64):
        for x in range(64):
            ph = half.getpixel((x, y))
            if y not in band_rows:
                assert base.getpixel((x, y)) == ph, f"밴드 밖 잔상 at ({x},{y})"
            assert ph[3] in (0, 255), f"반투명 생성 at ({x},{y})"
            if ph[3] == 255:
                assert ph[:3] in base_colors, f"팔레트 밖 색 at ({x},{y}): {ph}"
