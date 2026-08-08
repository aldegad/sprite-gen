# SPDX-License-Identifier: Apache-2.0
"""수동 피치(fit.pitch_manual)가 추출 프레임을 실제로 바꾼다.

`_manual_pitch_from_fit` 는 사람이 확정한 축별 피치를 읽어 자동 검출을 이긴다
(extract.py `_snap_strip`: 중재까지 끝난 뒤 consensus 를 이 값으로 덮는다). 추출의
샘플링 절단선은 이 피치에서 나오므로(`_grid_edges` → `snap_by_edges`), 피치가 바뀌면
스냅된 프레임이 바뀐다. 여기서는 실제 샘플링 함수 `grid_snap_downscale` 로 그 인과를
잠근다 — 같은 컴포넌트를 검출 피치로 스냅한 결과와 수동 피치로 스냅한 결과가 다르다.
"""
from __future__ import annotations

from PIL import Image

from sprite_gen.frames.extract import (_manual_pitch_from_fit, detect_pixel_grid,
                                       grid_snap_downscale)


_PALETTE = [(200, 40, 40), (40, 200, 40), (40, 40, 200),
            (220, 200, 40), (200, 40, 200), (40, 200, 200)]


def _blocks(pitch=8, cells=6):
    """pitch×pitch 단색 블록 cells×cells 합성 픽셀아트 컴포넌트. 인접 블록 색이 항상
    달라 검출이 확신 피치를 잡는다."""
    side = cells * pitch
    img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    for by in range(cells):
        for bx in range(cells):
            color = _PALETTE[(bx + by * 2) % len(_PALETTE)] + (255,)
            for y in range(pitch):
                for x in range(pitch):
                    img.putpixel((bx * pitch + x, by * pitch + y), color)
    return img


def test_manual_pitch_from_fit_gating():
    assert _manual_pitch_from_fit({"pitch_manual": [36, 36]}) == (36.0, 36.0)
    assert _manual_pitch_from_fit({"pitch_manual": [10.5, 12.25]}) == (10.5, 12.25)
    assert _manual_pitch_from_fit({"pitch_manual": [1, 36]}) is None       # <2px
    assert _manual_pitch_from_fit({"pitch_manual": [36]}) is None          # 형식 오류
    assert _manual_pitch_from_fit({"pitch_manual": "36x36"}) is None
    assert _manual_pitch_from_fit({}) is None                              # 없음 = 자동


def test_manual_pitch_changes_snapped_frame():
    """검출 피치 스냅 ≠ 수동 피치 스냅 — override 가 프레임을 실제로 바꾼다."""
    component = _blocks(pitch=8, cells=6)  # 48px 컴포넌트
    (detected_x, detected_y), _phase = detect_pixel_grid(component)
    assert detected_x >= 2.0 and detected_y >= 2.0  # 확신 검출

    snap_detected = grid_snap_downscale(component, (detected_x, detected_y))
    # 사람이 자동 검출과 명백히 다른 피치로 격자를 바로잡았다고 가정 (48/16 = 3 논리).
    manual = _manual_pitch_from_fit({"pitch_manual": [16, 16]})
    snap_manual = grid_snap_downscale(component, manual)

    assert snap_manual.size == (3, 3)
    # 검출은 ~8px(6논리) 근처라 수동 16px(3논리) 스냅과 논리 크기가 다르다.
    assert snap_detected.size != snap_manual.size
