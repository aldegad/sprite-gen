# SPDX-License-Identifier: Apache-2.0
"""align_x "alpha-centroid" (perfectpixel-studio port) unit tests.

The mode is opt-in: defaults must keep producing foot-centroid placement
(covered bit-for-bit by test_extraction_golden.py). Here we pin the mode's
own contract: fringe-insensitive alpha-weighted centroid on the cell axis,
and per-frame placement in the pixel-perfect row path that cancels residual
registration jitter.
"""

from PIL import Image

from sprite_gen.extract import (
    ALPHA_CENTROID_MIN_ALPHA,
    _alpha_centroid_row_left,
    _alpha_centroid_x,
    fit_to_cell,
    place_row_frame,
)


def _body_with_arm(arm_alpha: int = 255) -> Image.Image:
    # 16x16 dense body on the right + long 20x2 arm reaching left.
    sprite = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for y in range(2, 18):
        for x in range(22, 38):
            sprite.putpixel((x, y), (200, 60, 60, 255))
    for y in range(8, 10):
        for x in range(2, 22):
            sprite.putpixel((x, y), (60, 60, 200, arm_alpha))
    return sprite


def test_alpha_centroid_ignores_soft_matte_fringe() -> None:
    fringe = _body_with_arm(arm_alpha=ALPHA_CENTROID_MIN_ALPHA)  # α=10 → 프린지 취급
    with_fringe = _alpha_centroid_x(fringe, 1.0, ALPHA_CENTROID_MIN_ALPHA)
    plain = _alpha_centroid_x(fringe, 1.0, 0)
    body_only = _alpha_centroid_x(_body_with_arm(arm_alpha=0), 1.0, 0)
    assert with_fringe == body_only  # 프린지가 무게중심을 못 끈다
    assert plain < body_only  # 임계 없는 기존 centroid 는 왼쪽으로 끌린다


def test_fit_to_cell_alpha_centroid_centers_body_mass() -> None:
    sprite = _body_with_arm()
    cell = fit_to_cell(sprite, 64, 64, 0, 0, {"align_x": "alpha-centroid", "align_y": "bottom"})
    placed_cx = _alpha_centroid_x(cell, 1.0, ALPHA_CENTROID_MIN_ALPHA)
    assert abs(placed_cx - 32.0) <= 1.0  # 무게중심이 셀 중앙에
    bbox_center = fit_to_cell(sprite, 64, 64, 0, 0, {"align_x": "bbox-center", "align_y": "bottom"})
    bbox_cx = _alpha_centroid_x(bbox_center, 1.0, ALPHA_CENTROID_MIN_ALPHA)
    assert abs(bbox_cx - 32.0) > 2.0  # bbox 중심은 뻗은 팔이 몸통을 민다


def test_default_fit_is_unchanged_foot_centroid() -> None:
    sprite = _body_with_arm()
    default = fit_to_cell(sprite, 64, 64, 0, 0, {})
    explicit = fit_to_cell(sprite, 64, 64, 0, 0, {"align_x": "foot-centroid"})
    assert default.tobytes() == explicit.tobytes()
    opted_in = fit_to_cell(sprite, 64, 64, 0, 0, {"align_x": "alpha-centroid"})
    assert default.tobytes() != opted_in.tobytes()  # 옵트인이 실제로 다르다


def test_row_per_frame_left_cancels_registration_jitter() -> None:
    # 같은 몸통이 행 캔버스 안에서 3px 어긋나 등록된 두 프레임 —
    # union 공동 left 라면 그 3px 이 그대로 재생 지터로 남는다.
    frames = []
    for offset in (0, 3):
        canvas = Image.new("RGBA", (48, 24), (0, 0, 0, 0))
        for y in range(4, 20):
            for x in range(10 + offset, 26 + offset):
                canvas.putpixel((x, y), (90, 160, 90, 255))
        frames.append(canvas)
    scale = 2
    cell_w = cell_h = 128
    centroids = []
    for frame in frames:
        left = _alpha_centroid_row_left(frame, cell_w, scale)
        placed = place_row_frame(frame, cell_w, cell_h, scale, left, 0, 4, True)
        centroids.append(_alpha_centroid_x(placed, 1.0, ALPHA_CENTROID_MIN_ALPHA))
    assert abs(centroids[0] - centroids[1]) <= float(scale)  # 논리 격자 스냅 오차 이내
    assert abs(centroids[0] - cell_w / 2.0) <= float(scale)


def test_row_left_snaps_to_logical_grid_and_clamps() -> None:
    frame = Image.new("RGBA", (30, 10), (0, 0, 0, 0))
    for y in range(10):
        for x in range(30):
            frame.putpixel((x, y), (10, 10, 10, 255))
    scale = 3
    left = _alpha_centroid_row_left(frame, 96, scale)
    assert left % scale == 0
    assert 0 <= left <= 96 - frame.width * scale
