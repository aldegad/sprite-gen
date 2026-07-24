# SPDX-License-Identifier: Apache-2.0
"""경계 스냅 슬리버 가드 계약 (plan sprite-gen/backbone-sliver-guard).

실사고 (수홍 2026-07-24, v8 down_jump): 경계 스냅의 최소 간격이 절대 2px 라
피치 ~13px 소재에서 0.15셀짜리 슬리버 절단 쌍(0.4/1.6)을 허용했고, 인접 절단
둘이 같은 색경계로 끌려가면 출력 두 행이 소스의 거의 같은 밴드를 이중
샘플링했다 — f2 턱 늘어남(턱 라인 세로 복제)과 f0 눈물점(50/50 걸침 셀 지배색
플립)이 한 기전이다. 실측: 가드 후 v8 down 11행 내부 진성 슬리버 45 → 0,
엔진 출력만으로 f0 눈 대칭 복원(사이드카 보정 불필요), f1 변경 0px (외과적).

계약: 스냅이 만든 절단 간격은 내부에서 피치의 0.6배 미만이 될 수 없다.
"""
from __future__ import annotations

from PIL import Image

from sprite_gen.extract import refine_edges_to_boundaries


def _double_boundary_image(width: int = 130, height: int = 40) -> Image.Image:
    """인접 격자선 둘을 서로 향해 당기는 색경계 쌍 픽스처 — 실제 슬리버 기하.

    v8 실사고의 기전: 슬리버는 두 절단선이 "한 점"에 모여서가 아니라, ~5px
    간격의 경계 쌍이 각자의 창(±pitch/3=±4) 안에서 **서로를 향해** 절단선을
    당겨 태어난다. 격자선 52·65 사이에 경계를 56(55→56)과 61(60→61)에 두면:
    가드 전 = 절단선이 56·61 로 끌려가 내부 간격 5px(0.38셀) 슬리버,
    가드 후 = 두 번째 절단선이 하한(min_gap=8) 밖으로 못 들어와 슬리버 불가.
    """
    im = Image.new("RGBA", (width, height), (200, 60, 60, 255))
    px = im.load()
    for y in range(height):
        for x in range(56, 61):
            px[x, y] = (30, 30, 30, 255)      # 경계 1: 55→56 전이
        for x in range(61, width):
            px[x, y] = (60, 120, 200, 255)    # 경계 2: 60→61 전이 (5px 옆)
    return im


def test_interior_gaps_never_fall_below_guard_floor():
    im = _double_boundary_image()
    pitch = 13.0
    lattice = list(range(0, im.width + 1, int(pitch)))
    xs, ys = refine_edges_to_boundaries(im, lattice, [0, 13, 26, im.height], (pitch, pitch))
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    interior = gaps[1:-1]
    floor = round(pitch * 0.6)
    assert interior and min(interior) >= floor, (
        f"내부 절단 간격 {min(interior)}px < 가드 하한 {floor}px — 슬리버 쌍 부활 "
        f"(gaps={gaps}). 턱 늘어남/눈물점 클래스가 돌아온다"
    )


def test_snap_still_follows_boundaries_within_the_guard():
    """가드는 스냅을 죽이지 않는다 — 창 안 오프-격자 경계 추종은 유지된다."""
    im = Image.new("RGBA", (130, 40), (200, 60, 60, 255))
    px = im.load()
    for y in range(40):
        for x in range(50, 130):  # 경계가 격자선(52)에서 2px 벗어난 x=50 에 하나만
            px[x, y] = (60, 120, 200, 255)
    pitch = 13.0
    lattice = list(range(0, im.width + 1, int(pitch)))
    xs, _ = refine_edges_to_boundaries(im, lattice, [0, 13, 26, im.height], (pitch, pitch))
    assert 50 in xs, f"오프-격자 경계(50)로 스냅하지 않았다: {xs} — 경계 조각이 옆 칸으로 샌다"


def test_low_pitch_material_keeps_legacy_floor():
    """피치 < 3.3 소재는 기존 절대 2px 하한 그대로다 (저해상 런 무회귀)."""
    im = Image.new("RGBA", (30, 12), (200, 60, 60, 255))
    px = im.load()
    for y in range(12):
        for x in range(15, 30):
            px[x, y] = (60, 120, 200, 255)
    pitch = 3.0
    lattice = list(range(0, 31, 3))
    xs, _ = refine_edges_to_boundaries(im, lattice, [0, 3, 6, 9, 12], (pitch, pitch))
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    assert min(gaps[1:-1] or gaps) >= 2, f"저피치 하한(2px) 붕괴: {gaps}"
