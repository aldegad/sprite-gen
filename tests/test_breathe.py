# SPDX-License-Identifier: Apache-2.0
"""결정론 호흡 레이어(sprite_gen.breathe) 회귀 — 행 시프트 불변량 + 패턴/굽기 계약."""

from PIL import Image

from sprite_gen.breathe import (BAKE_CAP, _seam_rows, bake_breathe_sequence,
                                breathe_frames, breathe_pattern, phase_frame)


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


def test_pattern_single_line_cycle() -> None:
    cfg = {"splits": [0.55], "amplitude": 1, "hold": 3, "subpixel": False}
    assert breathe_pattern(cfg) == [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]


def test_pattern_two_lines_cascade() -> None:
    cfg = {"splits": [0.32, 0.55], "amplitude": 1, "hold": 3, "subpixel": False}
    # [기준×3, P1, P2, P2×2(유지), P1(복귀)] — 아래 밴드 먼저, 위가 반 박자 지연
    assert breathe_pattern(cfg) == [0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 2.0, 1.0]


def test_pattern_subpixel_inserts_half_phases() -> None:
    cfg = {"splits": [0.55], "amplitude": 1, "hold": 2, "subpixel": True}
    # 모든 전이(1→0 루프 랩 포함)에 중간 위상 정확히 1개 — 랩 전이는 시퀀스
    # 맨 앞(prev=마지막)에서 담당한다 (중복 삽입 회귀 방지, 클라 미러와 동형)
    assert breathe_pattern(cfg) == [0.5, 0.0, 0.0, 0.5, 1.0, 1.0]


def test_phase_frame_matches_breathe_frames() -> None:
    src = _figure()
    cfg = {"splits": [0.55], "amplitude": 1, "hold": 3, "subpixel": False}
    legacy = breathe_frames(src, split=0.55)[0]
    layered = phase_frame(src, cfg, 1.0)
    assert list(legacy.getdata()) == list(layered.getdata()), "레이어 위상 = 기존 수학"


def test_phase_zero_is_identity() -> None:
    src = _figure()
    cfg = {"splits": [0.55], "amplitude": 1, "hold": 3, "subpixel": False}
    assert phase_frame(src, cfg, 0.0) is src


def test_bake_sequence_lcm_realignment() -> None:
    src = _figure()
    cfg = {"splits": [0.55], "amplitude": 1, "hold": 3, "subpixel": False}
    images = [src] * 4                      # 시퀀스 4, 패턴 6 → LCM 12
    baked, phases = bake_breathe_sequence(images, cfg)
    assert len(baked) == 12 and len(phases) == 12
    assert phases == [0.0, 0.0, 0.0, 1.0, 1.0, 1.0] * 2
    assert len(baked) <= BAKE_CAP


def test_bake_sequence_blink_breathes_too() -> None:
    """깜빡임 프레임(다른 그림)도 같은 위상 변조를 받는다 — 직교 레이어 계약."""
    a = _figure()
    blink = _figure()
    blink.putpixel((30, 20), (0, 0, 0, 255))  # 다른 그림 표식
    cfg = {"splits": [0.55], "amplitude": 1, "hold": 1, "subpixel": False}
    baked, phases = bake_breathe_sequence([a, blink], cfg)
    # 패턴 [0,1] × 시퀀스 [a,blink] → LCM 2: [a@0, blink@1]
    assert phases == [0.0, 1.0]
    assert _bbox(baked[1])[1] == _bbox(blink)[1] + 1, "깜빡임 프레임이 날숨 위상으로 하강"


def test_subpixel_blend_limited_to_seam_bands() -> None:
    """서브픽셀 중간색은 움직이는 경계(정수리+호흡선)에만 — 몸통 안 가로 경계
    (눈 등)에 잔상 금지 (수홍 정정 2026-07-18)."""
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for y in range(10, 58):
        for x in range(20, 44):
            im.putpixel((x, y), (200, 50, 50, 255) if 20 <= y <= 24 else (90, 60, 30, 255))
    cfg = {"splits": [0.55], "amplitude": 1, "hold": 2, "subpixel": True}
    base = phase_frame(im, cfg, 0.0)
    half = phase_frame(im, cfg, 0.5)
    band_rows = set()
    for r0, r1 in _seam_rows(10, [10 + int(48 * 0.55)], 1, 64):
        band_rows.update(range(r0, r1))
    for y in range(64):
        if y in band_rows:
            continue
        for x in range(64):
            assert base.getpixel((x, y)) == half.getpixel((x, y)), f"잔상 at ({x},{y})"
