# SPDX-License-Identifier: Apache-2.0
"""바이트 동일 게이트 — 크로마 제거 스칼라 픽셀 루프의 벡터화 대비 동결 참조.

플랜 `sprite-gen/extract-numpy-vectorization` 체크리스트 2번. 최적화 **전에** 세운다.

## 왜 새 파일인가 (부모 파일을 확장하지 않은 근거)

부모 플랜의 `tests/test_extract_perf_equivalence.py` 는 좋은 설계지만 그 참조들이
프로덕션 프리미티브를 **그대로 호출한다** (`extract.rgb_to_ycc`, `extract.ycc_to_rgb`,
`extract.smoothstep`, `extract._grid_edges`, `extract._YCC_*`). 부모가 깎은 함수들은
그 프리미티브를 건드리지 않았으니 그 파일 안에서는 유효한 계약이다. 그러나 이 플랜은
정확히 그 층(픽셀 단위 산수)을 배열 연산으로 바꾸므로, 같은 파일에 새 단정을 얹으면
**참조가 프로덕션을 따라 움직여** 새 코드를 새 코드와 비교하고 무조건 통과한다.

그래서 분리했다. 격리는 이 파일의 **파일 단위 불변식**이고, 아래 동결 구역은
`test_frozen_reference_is_isolated_from_production` 이 소스를 직접 읽어 강제한다 —
동결 구역이 프로덕션 모듈을 참조하는 순간 그 테스트가 빨개진다. 부모 파일에 섞으면
이 불변식을 단정별로 사람이 눈으로 지켜야 하는데, 그건 게이트가 아니다.

## 무엇을 고정하는가 (이중 잠금)

1. **동결 스칼라 사본 대조** — 최적화 전 커밋(`5dd3118`, `engine_revision 3adf0169561a`)의
   스칼라 구현을 상수 리터럴까지 이 파일 안에 복사해 두고, 프로덕션 출력과
   `.tobytes()` 로 완전 일치를 요구한다. 참조는 프로덕션을 import 하지 않으므로
   프로덕션이 바뀌어도 따라 움직이지 않는다.
2. **절대 해시 고정** — 케이스별 출력 SHA-256 을 리터럴로 박는다. 누군가 프로덕션과
   참조를 **같이** 고쳐도(1번을 우회) 여기서 걸린다. 픽스처 PNG 바이트 해시도 함께
   박아 픽스처가 조용히 바뀌는 경로를 막는다.

## 실사이즈 실런은 여기서 다루지 않는다 (역할 분리)

1.5Mpx 원본을 레포에 커밋하지 않는다(용량) — 플랜 Decisions 대로 실사이즈 바이트
동일은 체크리스트 8·10 의 실측 증적이 담당한다. 최적화 전 실런 골든은 이미 떠 뒀다:
`~/.kuma/vault/plans/sprite-gen/_assets/extract-numpy-vectorization/golden-baseline-founder_v8.json`
(founder_v8 14행 204프레임, 프레임별 PNG·픽셀 SHA-256, 2회 재현 204/204 일치).
그 파일은 **테스트가 읽지 않는다** — pytest 는 레포 안에서 자립해야 하고, 남의 레포
런 자산에 매달리면 부모 플랜의 골든이 썩은 경로를 그대로 반복한다.

## 픽스처를 실런 크롭으로 커밋하지 않은 근거

플랜은 (a) 실런 크롭 커밋 / (b) 고정 합성 입력 두 갈래를 워커 판단에 맡겼다. (b) 를
골랐다. 이 레포는 **공개 원격**(`github.com/aldegad/sprite-gen`)이 붙어 있고
`founder_v8` 은 solvell 게임 아트다 — 픽스처로 커밋하면 남의 프로젝트 아트를 공개
저장소에 게시하는 외부 행위가 되는데, 그건 이 플랜이 승인받은 범위가 아니다.
대신 이미 이 레포에 커밋되어 있는 **실촬 raw 축소본** `tests/fixtures/moe/` 두 장을
1차 픽스처로 쓴다: 실제 AA 프린지·갇힌 스필이 들어 있고, 마젠타 키와 **그린 키**
(실런 145/145 가 쓰는 그 키) 양쪽을 덮는다. 새 바이너리를 하나도 추가하지 않는다.
합성 픽스처는 실촬본이 못 밟는 분기(입력 alpha==0, 과대 스필 클러스터, coverage<=0,
축퇴 키)와 **임계 경계값**만 결정론적으로 채운다 (난수 없음 — 순수 산수).

## 경계값이 게이트의 본체다

첫 판은 39개 전부 통과했지만 소스 mutant 23종 중 **6종이 살아남았다** — 임계에서 정확히
같은 값을 갖는 픽셀이 없으면 `<=` 를 `<` 로 바꿔도 출력이 그대로다. 살아남은 6종에 맞춰
경계 픽스처를 추가한 뒤 **23/23 killed** 이 됐다. 각 픽스처 docstring 에 "어떤 변형을
잡기 위한 자리인지" 를 적어 뒀으니 값을 바꿀 때 그 이유를 먼저 읽을 것 —
숫자 하나(예: 클러스터 41px == spill_limit 41)가 mutant 두 종을 잡고 있다.

## 노드 3 (체크리스트 3번) 에서 다시 만든 부분

모듈 소스 전체를 변형해 갈아끼우는 하네스로 mutant 64종을 돌렸다
(`_assets/extract-numpy-vectorization/mutants_node3.py`). 함수 단위 치환과 달리
프리미티브 mutant 가 `remove_chroma_background` 안쪽까지 전파되고 모듈 상수도 대상이
된다. 1라운드 50/63 killed → 살아남은 13종 중 **8종이 진짜 게이트 구멍**이라
아래를 추가했다 (나머지 5종은 관측 불가능한 등가 mutant — 노드 3 결과 문서에 논증):

- `_KEYS` 에 `(192, 255, 0)`·`(0, 255, 64)` — `key_tint_score` 의 채널 선택 임계
  `>= 192`·`< 64` 를 정확히 밟는 키가 없어 두 연산자가 안 잠겨 있었다.
- `_synthetic_spill_semantics_field` — 스필 한도 산식의 입력(`subject_count`),
  클러스터 축약(`max` vs `mean`), 알파 보존, 반올림 규약(half-to-even).
- `_synthetic_reach_geometry_field` — 거리변환의 연결성 패리티와 경계 padding
  (`np.roll` wrap).

**여기 숫자는 하나도 임의값이 아니다.** 각 값이 어떤 변형을 잡는지 픽스처 docstring 에
적어 뒀다.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.frames import extract

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "moe"

# extract CLI 기본값 (SSoT: sprite_gen/frames/extract.py argparse) — cutout.py 도 이 값을 쓴다
CLI_KEY_THRESHOLD = 96.0
CLI_FRINGE_THRESHOLD = 180.0
CLI_FRINGE_DELTA = 18.0
CLI_UNMIX_REACH = 4
CLI_SPILL_MAX_FRACTION = 0.005

MAGENTA = (255, 0, 255)
GREEN = (0, 255, 0)


# ===========================================================================
# --- FROZEN SCALAR REFERENCE (BEGIN) ---------------------------------------
# 최적화 전 커밋 5dd3118 / engine_revision 3adf0169561a 의 스칼라 구현 사본.
# **이 구역은 프로덕션 모듈을 참조하지 않는다.** 상수도 리터럴로 복사한다.
# 성능은 목적이 아니다 — 느려도 된다. 강제: test_frozen_reference_is_isolated_from_production
# ===========================================================================

_REF_KEYED = 0
_REF_SUBJECT = 1
_REF_BLEND_IN_BAND = 2
_REF_BLEND_OUT_OF_BAND = 3
_REF_IN_BAND_UNMIX_KEY_DEPTH = 2
_REF_SPILL_MIN_TINT = 40.0
_REF_COMPONENT_ALPHA_CUTOFF = 16
_REF_EDGE_HISTOGRAM_DELTA = 96
_REF_BOUNDARY_ALPHA_CUTOFF = 128
_REF_BOUNDARY_COLOR_DELTA = 48


def _ref_color_distance(left, right):
    return math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def _ref_key_tint_score(color, chroma_key):
    keyed_channels = [index for index, value in enumerate(chroma_key) if value >= 192]
    unkeyed_channels = [index for index, value in enumerate(chroma_key) if value < 64]
    if not keyed_channels or not unkeyed_channels:
        return 0.0
    keyed_average = sum(color[index] for index in keyed_channels) / len(keyed_channels)
    unkeyed_average = sum(color[index] for index in unkeyed_channels) / len(unkeyed_channels)
    return keyed_average - unkeyed_average


def _ref_despill_color(color, chroma_key, key_tint, tint):
    k = min(tint / key_tint, 1.0)
    coverage = 1.0 - k
    if coverage <= 0:
        return 0.0, (0, 0, 0)
    red, green, blue = (
        min(255, max(0, round((color[index] - k * chroma_key[index]) / coverage)))
        for index in range(3)
    )
    return coverage, (red, green, blue)


def _ref_unmix_key_blend(color, alpha, chroma_key, key_tint, tint):
    coverage, despilled = _ref_despill_color(color, chroma_key, key_tint, tint)
    out_alpha = round(alpha * coverage)
    if out_alpha <= 0:
        return (0, 0, 0, 0)
    return (*despilled, out_alpha)


def _ref_remove_chroma_background(
    image,
    chroma_key,
    threshold,
    fringe_threshold,
    fringe_delta,
    *,
    unmix_reach=4,
    spill_max_fraction=0.005,
    counters=None,
):
    """동결 사본. `counters` 는 참조 전용 계측(분기 도달 확인)이며 출력에 영향이 없다."""
    def bump(name, amount=1):
        if counters is not None:
            counters[name] = counters.get(name, 0) + amount

    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    classes = bytearray(width * height)
    unseen = 255
    depths = bytearray(b"\xff" * (width * height))
    keyed = []
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            index = y * width + x
            color = (red, green, blue)
            if alpha == 0 or _ref_color_distance(color, chroma_key) <= threshold:
                pixels[x, y] = (0, 0, 0, 0)
                classes[index] = _REF_KEYED
                depths[index] = 0
                keyed.append(index)
                bump("keyed_alpha_zero" if alpha == 0 else "keyed_by_distance")
            elif _ref_key_tint_score(color, chroma_key) < fringe_delta:
                classes[index] = _REF_SUBJECT
                bump("subject")
            elif _ref_color_distance(color, chroma_key) <= fringe_threshold:
                classes[index] = _REF_BLEND_IN_BAND
                bump("blend_in_band")
            else:
                classes[index] = _REF_BLEND_OUT_OF_BAND
                bump("blend_out_of_band")

    key_tint = _ref_key_tint_score(chroma_key, chroma_key)
    max_reach = min(unseen - 1, unmix_reach if key_tint > 0 else 0)
    bump("degenerate_key_tint", 1 if key_tint <= 0 else 0)

    frontier = keyed
    depth = 0
    while frontier and depth < max_reach:
        depth += 1
        next_frontier = []
        for index in frontier:
            x = index % width
            y = index // width
            for dy in (-1, 0, 1):
                ny = y + dy
                if ny < 0 or ny >= height:
                    continue
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if nx < 0 or nx >= width:
                        continue
                    neighbor = ny * width + nx
                    if depths[neighbor] == unseen:
                        depths[neighbor] = depth
                        next_frontier.append(neighbor)
        frontier = next_frontier
    bump("depth_reached_max", 1 if depth == max_reach and max_reach else 0)
    bump("depth_positive", sum(1 for value in depths if 0 < value < unseen))

    if key_tint > 0 and unmix_reach > 0:
        for y in range(height):
            for x in range(width):
                index = y * width + x
                if not 0 < depths[index] <= unmix_reach:
                    continue
                pixel_class = classes[index]
                if pixel_class == _REF_BLEND_IN_BAND:
                    if depths[index] > _REF_IN_BAND_UNMIX_KEY_DEPTH:
                        bump("unmix_in_band_skipped_deep")
                        continue
                    bump("unmix_in_band")
                elif pixel_class != _REF_BLEND_OUT_OF_BAND:
                    continue
                else:
                    bump("unmix_out_of_band")
                red, green, blue, alpha = pixels[x, y]
                color = (red, green, blue)
                out = _ref_unmix_key_blend(
                    color, alpha, chroma_key, key_tint, _ref_key_tint_score(color, chroma_key)
                )
                bump("unmix_collapsed_to_transparent", 1 if out[3] == 0 else 0)
                # 조건부 bump — 안 걸리면 키 자체가 안 생겨 기존 13 케이스의 기대표가
                # 그대로다. 이 자리가 옛 경로에서 PIL 클램프에 기대던 유일한 지점이다:
                # `pixels[x, y] = out` 이 258 을 255 로 조여서 넣었다.
                if out[3] > 255:
                    bump("unmix_alpha_over_255")
                pixels[x, y] = out

    if key_tint > 0 and keyed and spill_max_fraction > 0:
        subject_count = sum(1 for pixel_class in classes if pixel_class != _REF_KEYED)
        spill_limit = max(32, round(subject_count * spill_max_fraction))
        bump("spill_limit_from_fraction", 1 if round(subject_count * spill_max_fraction) > 32 else 0)
        tints_left = {}
        for y in range(height):
            for x in range(width):
                red, green, blue, alpha = pixels[x, y]
                if not alpha:
                    continue
                tint = _ref_key_tint_score((red, green, blue), chroma_key)
                if tint >= fringe_delta:
                    tints_left[y * width + x] = tint
        bump("spill_candidates", len(tints_left))
        visited = set()
        for start in tints_left:
            if start in visited:
                continue
            stack = [start]
            visited.add(start)
            cluster = []
            while stack:
                index = stack.pop()
                cluster.append(index)
                x = index % width
                y = index // width
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if 0 <= x + dx < width and 0 <= y + dy < height:
                            neighbor = (y + dy) * width + (x + dx)
                            if neighbor in tints_left and neighbor not in visited:
                                visited.add(neighbor)
                                stack.append(neighbor)
            if len(cluster) > spill_limit:
                bump("spill_cluster_too_big")
                continue
            if max(tints_left[index] for index in cluster) <= _REF_SPILL_MIN_TINT:
                bump("spill_cluster_low_tint")
                continue
            bump("spill_cluster_treated")
            bump("spill_treated_px", len(cluster))
            for index in cluster:
                x = index % width
                y = index // width
                red, green, blue, alpha = pixels[x, y]
                color = (red, green, blue)
                coverage, despilled = _ref_despill_color(
                    color, chroma_key, key_tint, _ref_key_tint_score(color, chroma_key)
                )
                if coverage > 0:
                    pixels[x, y] = (*despilled, alpha)
                else:
                    bump("spill_zero_coverage")
    return rgba


def _ref_connected_components(image):
    alpha = image.getchannel("A")
    width, height = image.size
    data = alpha.tobytes()
    visited = bytearray(width * height)
    components = []

    for start, alpha_value in enumerate(data):
        if alpha_value <= _REF_COMPONENT_ALPHA_CUTOFF or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        pixels = []
        min_x = width
        min_y = height
        max_x = 0
        max_y = 0

        while stack:
            current = stack.pop()
            pixels.append(current)
            x = current % width
            y = current // width
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

            for neighbor in (current - 1, current + 1, current - width, current + width):
                if neighbor < 0 or neighbor >= len(data) or visited[neighbor]:
                    continue
                nx = neighbor % width
                if abs(nx - x) > 1:
                    continue
                if data[neighbor] > _REF_COMPONENT_ALPHA_CUTOFF:
                    visited[neighbor] = 1
                    stack.append(neighbor)

        components.append(
            {
                "pixels": pixels,
                "area": len(pixels),
                "bbox": (min_x, min_y, max_x + 1, max_y + 1),
                "center_x": (min_x + max_x + 1) / 2,
            }
        )
    return components


def _ref_edge_histograms(image):
    pixels = image.convert("RGBA").load()
    width, height = image.size
    col_edges = [0] * width
    row_edges = [0] * height
    for y in range(0, height, 2):
        for x in range(1, width):
            a = pixels[x, y]
            b = pixels[x - 1, y]
            if (abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3])
                    > _REF_EDGE_HISTOGRAM_DELTA):
                col_edges[x] += 1
    for x in range(0, width, 2):
        for y in range(1, height):
            a = pixels[x, y]
            b = pixels[x, y - 1]
            if (abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3])
                    > _REF_EDGE_HISTOGRAM_DELTA):
                row_edges[y] += 1
    return col_edges, row_edges, width, height


def _ref_boundary_mass(image):
    px = image.load()
    w, h = image.size
    col = [0] * max(1, w)
    row = [0] * max(1, h)
    cut = _REF_BOUNDARY_ALPHA_CUTOFF
    delta = _REF_BOUNDARY_COLOR_DELTA
    for y in range(h):
        for x in range(w - 1):
            a, b = px[x, y], px[x + 1, y]
            if (a[3] >= cut) != (b[3] >= cut) or (
                a[3] >= cut and b[3] >= cut
                and abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) > delta
            ):
                col[x + 1] += 1
    for x in range(w):
        for y in range(h - 1):
            a, b = px[x, y], px[x, y + 1]
            if (a[3] >= cut) != (b[3] >= cut) or (
                a[3] >= cut and b[3] >= cut
                and abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) > delta
            ):
                row[y + 1] += 1
    return col, row


# ===========================================================================
# --- FROZEN SCALAR REFERENCE (END) -----------------------------------------
# ===========================================================================


# --- 픽스처 ----------------------------------------------------------------
# 실촬본은 이미 레포에 있는 moe raw 축소본이고, 합성본은 실촬본이 못 밟는
# 분기만 결정론적 산수로 채운다 (난수 없음).

# 픽스처 PNG 바이트 고정 — 픽스처가 조용히 바뀌면 골든이 의미를 잃는다
MOE_FIXTURE_SHA256 = {
    "moe_green.png": "91fe50bfd640fa2e029723e1859a1578e780221d5870f0614fbc73f0b788c825",
    "moe_red.png": "28279535eaca2ce603b7671b26d30a1475763f7d9ae7674f19b6d28a8cfb8eaa",
}


def _open_moe(name: str) -> Image.Image:
    with Image.open(FIXTURES / name) as opened:
        return opened.convert("RGBA")


def _synthetic_branch_field(width: int = 96, height: int = 72) -> Image.Image:
    """그린 키 합성 입력 — 실촬 moe 픽스처가 못 밟는 분기를 채운다.

    - 상단 2행은 입력 alpha==0 (실촬 raw 는 전부 불투명이라 이 분기가 안 밟힌다)
    - 피험체 안쪽 8x8 틴트 패치 = 스필 한도 초과 클러스터 (untouched 분기)
    - 안쪽 3x3 강한 틴트 패치 = 작은 스필 클러스터 (despill 분기)
    - 안쪽 4x2 약한 틴트 패치 = tint <= 40 (untouched 분기)
    - 실루엣 프린지 링 = in-band/out-of-band 블렌드 + depth 1~4
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = img.load()
    for y in range(height):
        for x in range(width):
            # 흔들리는 키 배경 — 평탄색이 아니어야 거리 임계가 실제로 판정을 한다
            red = 11 + ((x * 3) % 5)
            green = 238 - ((x + y) % 7)
            blue = 27 + ((y * 2) % 5)
            alpha = 255
            if y < 2:  # 입력 alpha==0 밴드
                px[x, y] = (red, green, blue, 0)
                continue
            if 20 <= x < 76 and 16 <= y < 64:  # 피험체 (마젠타 계열 → tint 음수)
                red, green, blue = 180 + (x % 20), 40 + (y % 15), 150 - (x % 12)
                if x < 24 or x >= 72 or y < 20 or y >= 60:
                    # 프린지 링: in-band (거리 <= 180, tint >= 18)
                    red, green, blue = 95, 150, 80
                if x == 24 or x == 71:
                    # 프린지 안쪽 한 겹: out-of-band (거리 > 180, tint >= 18)
                    red, green, blue = 30, 200, 220
                if 30 <= x < 38 and 30 <= y < 38:
                    red, green, blue = 120, 200, 90  # 과대 스필 클러스터 (64px)
                if 52 <= x < 55 and 30 <= y < 33:
                    red, green, blue = 120, 200, 90  # 작은 스필 클러스터 (9px)
                if 52 <= x < 56 and 44 <= y < 46:
                    red, green, blue = 150, 180, 140  # 약한 틴트 (tint 35)
                if 60 <= x < 63 and 44 <= y < 47:
                    red, green, blue = 160, 200, 160  # tint 정확히 40.0 (경계)
                # tint 정확히 18.0 픽셀이 강한 틴트 클러스터에 **붙어** 있어야
                # `tint >= fringe_delta` 를 `>` 로 뒤집는 변경이 출력을 바꾼다
                # (혼자 있으면 어느 쪽이든 max tint <= 40 으로 통과돼 무해해진다)
                if 40 <= x < 42 and 50 <= y < 52:
                    red, green, blue = 120, 200, 90   # tint 95
                if x == 42 and y == 50:
                    red, green, blue = 100, 118, 100  # tint 정확히 18.0
                # 대각선으로만 이어진 강/약 틴트 쌍 — 스필 flood 의 8-이웃 계약.
                # 4-이웃으로 바뀌면 약한 쪽이 별도 클러스터가 되어 despill 을 면한다
                if 46 <= x < 48 and 54 <= y < 56:
                    red, green, blue = 120, 200, 90   # tint 95
                if 48 <= x < 50 and 56 <= y < 58:
                    red, green, blue = 150, 180, 140  # tint 35 (대각선 접촉만)
            px[x, y] = (red, green, blue, alpha)
    return img


def _synthetic_zero_coverage(width: int = 40, height: int = 32) -> Image.Image:
    """coverage <= 0 분기용 — 포화되지 않은 키가 필요하다.

    포화 키(green/magenta)에서는 `tint >= key_tint` 가 곧 "키 자기 자신"이라
    P1 이 이미 지워버려 이 분기에 도달할 수 없다. 실측으로도 확인했다:
    founder_v8 raw 14장 + moe 2장 전수에서 도달 픽셀 0개
    (`max non-keyed tint` 149.5~183.5 vs `key_tint` 255).
    그래서 키를 (0,200,0)/threshold 20 으로 두고 tint 228 픽셀을 심는다.

    두 자리에 심는다 — coverage<=0 은 두 패스에 각각 있고 결과가 다르다:
    - 실루엣 경계(depth 1~4): unmix 가 `(0,0,0,0)` 으로 지운다
    - 피험체 깊은 안쪽(depth > reach): 갇힌 스필 패스가 도달하지만
      `if coverage > 0` 이 거짓이라 **아무것도 쓰지 않는다** (원본 유지)
    """
    img = Image.new("RGBA", (width, height), (5, 198, 3, 255))
    px = img.load()
    for y in range(6, 26):
        for x in range(8, 32):
            px[x, y] = (200, 40, 180, 255)  # 피험체 24x20
    for y in range(5, 27):
        px[7, y] = (27, 255, 27, 255)  # 경계 tint 228 → unmix coverage <= 0
        px[32, y] = (27, 255, 27, 255)
    for y in range(14, 17):
        for x in range(18, 21):
            px[x, y] = (27, 255, 27, 255)  # 깊은 안쪽 3x3 → 스필 패스 coverage <= 0
    return img


def _synthetic_fraction_limit_field(width: int = 140, height: int = 110) -> Image.Image:
    """`spill_limit = max(32, round(subject_count * spill_max_fraction))` 의 산식
    자체를 고정한다. 작은 캔버스에서는 항상 32 로 클램프돼 이 분기가 죽는다.

    비-키 픽셀을 **정확히 8,120** 개로 맞춘다 (피험체 박스 100x80 = 8,000 +
    별도 블록 12x10 = 120). 8120 x 0.005 = **40.6** 이라
    `round` → 41, `int` → 40 으로 갈린다. 그 안에 심은 강한 틴트 클러스터는
    정확히 **41px** (8x5 + 1) 이므로:

    - 현행 `round` + `len(cluster) > spill_limit`: 41 > 41 거짓 → **despill 됨**
    - `round` 를 `int` 로 바꾸면: limit 40, 41 > 40 → too_big → **안 건드림**
    - `>` 를 `>=` 로 바꾸면: 41 >= 41 → too_big → **안 건드림**

    즉 세 변형이 모두 출력을 바꾼다 — 클러스터 크기를 한도와 정확히 같게 두는 게
    핵심이다(36px 이던 첫 판은 두 변형을 다 놓쳤다).
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = img.load()
    for y in range(height):
        for x in range(width):
            red = 11 + ((x * 3) % 5)
            green = 238 - ((x + y) % 7)
            blue = 27 + ((y * 2) % 5)
            if 20 <= x < 120 and 15 <= y < 95:  # 피험체 박스 100x80 = 8,000px
                red, green, blue = 180 + (x % 20), 40 + (y % 15), 150 - (x % 12)
                if x < 22 or x >= 118 or y < 17 or y >= 93:
                    red, green, blue = 95, 150, 80  # in-band 프린지 링
                if (60 <= x < 68 and 50 <= y < 55) or (x == 68 and y == 50):
                    red, green, blue = 120, 200, 90  # 41px 강한 틴트 클러스터
            elif 125 <= x < 137 and 15 <= y < 25:  # 별도 피험체 블록 12x10 = 120px
                red, green, blue = 200, 40, 180
            px[x, y] = (red, green, blue, 255)
    return img


def _synthetic_threshold_boundary_field(width: int = 48, height: int = 40) -> Image.Image:
    """분류 임계의 **경계값** 전용 — `<=` 를 `<` 로 바꾸면 출력이 실제로 달라지게.

    임계에서 정확히 같은 값을 갖는 픽셀이 없으면 비교 연산자 뒤집기가 게이트를
    빠져나간다(실측으로 확인: 첫 판의 게이트는 `_SPILL_MIN_TINT 40->39` 를 놓쳤다).
    키 배경 위에 서로 3px 이상 떨어진 고립 픽셀로 심어 각자 독립 클러스터가 되게 한다.

    | 색             | 값                        | 걸리는 경계                    |
    |----------------|---------------------------|--------------------------------|
    | (0, 159, 0)    | 키 거리 정확히 96.0       | `<= threshold` (지움)          |
    | (1, 159, 0)    | 키 거리 96.005            | 바로 위 — 지우지 않음          |
    | (0, 75, 0)     | 키 거리 정확히 180.0      | `<= fringe_threshold` (in-band)|
    | (1, 75, 0)     | 키 거리 180.003           | 바로 위 — out-of-band          |
    | (100,118,100)  | tint 정확히 18.0          | `< fringe_delta` (블렌드)      |
    | (101,118,100)  | tint 17.5                 | 바로 아래 — subject            |
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = img.load()
    for y in range(height):
        for x in range(width):
            px[x, y] = (11, 238, 27, 255)
    probes = [
        (0, 159, 0), (1, 159, 0), (0, 75, 0), (1, 75, 0),
        (100, 118, 100), (101, 118, 100), (200, 40, 180), (12, 236, 26),
    ]
    for slot, color in enumerate(probes):
        for repeat in range(3):  # 같은 경계값을 3자리에 — 깊이 1 고립 픽셀
            x = 4 + (slot % 4) * 11 + repeat * 3
            y = 6 + (slot // 4) * 12 + repeat * 4
            px[x, y] = (*color, 255)
    px[45, 2] = (200, 40, 180, 0)  # 입력 alpha==0 (색은 피험체) → 지워져야 한다
    # 깊이 3·4 자리의 프린지 경계 프로브. 깊이 1 에서는 in-band 와 out-of-band 가
    # **똑같이** unmix 되므로 `<= fringe_threshold` 를 뒤집어도 출력이 안 바뀐다.
    # 깊이 3 이상에서만 in-band 는 건너뛰고 out-of-band 는 unmix 되어 갈라진다.
    for y in range(30, 37):
        for x in range(6, 13):
            px[x, y] = (200, 40, 180, 255)  # 7x7 피험체 블록 → 중심이 깊이 4
    px[8, 33] = (0, 75, 0, 255)   # 키 거리 정확히 180.0, 깊이 3
    px[9, 33] = (0, 75, 0, 255)   # 키 거리 정확히 180.0, 깊이 4
    return img


def _alpha_boundary_field(width: int = 40, height: int = 24) -> Image.Image:
    """티어 2 함수(`connected_components`·`_edge_histograms`·`_boundary_mass`)의
    임계 경계값 전용 RGBA. 크로마 패스를 통과시키지 않고 **직접** 만든다 —
    크로마 출력에는 alpha 가 정확히 16/17 이나 델타가 정확히 96/97 인 픽셀이
    우연히 없어서 컷오프 변경이 게이트를 빠져나갔다(실측 확인).

    - alpha 15/16/17: `connected_components` 의 `> 16` 컷오프
    - alpha 127/128: `_boundary_mass` 의 `>= 128` 컷오프
    - 이웃 채널 절대차 합 96/97: `_edge_histograms` 의 `> 96`
    - 이웃 RGB 절대차 합 48/49: `_boundary_mass` 의 `> 48`
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = img.load()
    for slot, alpha in enumerate((15, 16, 17, 127, 128, 255)):
        x = 2 + slot * 6
        for y in (2, 3, 4):  # 세로 3px 덩어리 → 성분 경계가 생긴다
            px[x, y] = (120, 60, 200, alpha)
            px[x + 1, y] = (120, 60, 200, alpha)
    base = (100, 100, 100)
    for slot, delta in enumerate((95, 96, 97, 47, 48, 49)):
        y = 10 + slot * 2
        for x in range(0, width - 1, 4):
            px[x, y] = (*base, 255)
            px[x + 1, y] = (base[0] + delta, base[1], base[2], 255)
    for x in range(width):  # 알파 127/128 인 가로 띠 — 세로 방향 경계 판정
        px[x, 22] = (10, 220, 30, 127)
        px[x, 23] = (10, 220, 30, 128)
    # 성분 픽셀에 **붙어 있는** alpha 16 — `data[neighbor] > 16` 을 `>= 16` 으로
    # 바꾸면 이 픽셀이 성분에 흡수돼 area 가 달라진다. 떨어져 있으면 어느 쪽이든
    # 시작 조건(`<= 16`)에서 걸러져 무해해진다.
    px[20, 7] = (90, 140, 220, 255)
    px[21, 7] = (90, 140, 220, 16)
    # `_boundary_mass` AND 항의 **위쪽** 경계 (노드 3 attempt 2, R2).
    # 위 alpha 127/128 가로 띠는 XOR 항만 밟는다 — 색이 같아서 AND 항의 색차
    # 조건이 거짓이기 때문이다. `a[3] >= 128 and b[3] >= 128` 을 `> 128` 로 옮기는
    # 변경을 잡으려면 **양쪽 알파가 >= 128 이고 한쪽이 정확히 128 이면서 색차 L1
    # 이 48 을 넘는** 인접쌍이 필요하다 (그 쌍은 XOR 항이 거짓이라 AND 항만 판정한다).
    # attempt 1 의 `>=128 -> >=127` 은 killed 였으므로 임계가 한쪽에서만 잠겨 있었다.
    # 색차는 100 (200 vs 100 채널 0) 이라 `> 48` 쪽 임계와는 독립이다.
    px[2, 6] = (200, 100, 100, 128)   # 가로쌍 왼쪽 — 알파 정확히 128
    px[3, 6] = (100, 100, 100, 255)
    px[6, 5] = (200, 100, 100, 128)   # 세로쌍 위 — 알파 정확히 128
    px[6, 6] = (100, 100, 100, 255)
    return img


def _synthetic_spill_semantics_field(width: int = 160, height: int = 120) -> Image.Image:
    """갇힌 스필 패스의 **의미**를 고정한다 (노드 3 추가 — mutant 4종을 잡는 자리).

    - 비-키 픽셀이 **정확히 8,000** 개다 (피험체 100x80, 안쪽 재색칠은 개수를 안
      바꾼다). 캔버스는 19,200px 이라 `subject_count` 를 `width * height` 로 갈음하면
      한도가 `round(8000*0.005)=40` 에서 `round(19200*0.005)=96` 으로 뛴다. 그
      사이에 **60px 클러스터**를 놓았다 — 원본은 `60 > 40` 이라 too_big 로 안
      건드리고, 갈음한 구현은 despill 한다.
    - **11px 클러스터** = 강한 틴트 1px(tint 95) + 약한 틴트 10px(tint 19).
      `max` 95 > 40 이라 원본은 treated, 평균은 25.909 <= 40 이라 클러스터 축약을
      `max` 대신 `mean`(np.mean) 으로 짜면 통째로 빠진다.
    - 그 11px 는 **alpha 200** 이다. 스필은 색보정이라 알파를 보존한다 — despill
      결과에 255 를 박는 구현은 여기서 갈린다. (다른 픽스처의 스필 클러스터는 전부
      alpha 255 라 이 계약이 안 잠겨 있었다.)
    - **(2, 52, 0) 단독 픽셀**: tint 51 이라 `k = 51/255 = 0.2`, `coverage = 0.8`,
      채널 0 이 정확히 `2 / 0.8 == 2.5`. 파이썬 `round` 는 half-to-even 이라 **2**,
      손으로 짠 `np.floor(x + 0.5)` 는 **3** 이다. 반올림 규약이 갈리는 유일한 자리다.

    피험체 테두리는 subject 색이라 unmix 패스는 이 케이스에서 아무것도 하지 않는다 —
    스필 패스만 관측된다.
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = img.load()
    for y in range(height):
        for x in range(width):
            red = 11 + ((x * 3) % 5)
            green = 238 - ((x + y) % 7)
            blue = 27 + ((y * 2) % 5)
            alpha = 255
            if 20 <= x < 120 and 15 <= y < 95:  # 피험체 100x80 = 8,000px
                red, green, blue = 180 + (x % 20), 40 + (y % 15), 150 - (x % 12)
                if 30 <= x < 40 and 30 <= y < 36:
                    red, green, blue = 120, 200, 90  # 60px — 한도 40 초과, 96 미만
                if x == 60 and y == 50:
                    red, green, blue, alpha = 120, 200, 90, 200  # 11px 클러스터의 씨앗
                if 61 <= x < 66 and 50 <= y < 52:
                    red, green, blue, alpha = 150, 169, 150, 200  # tint 19 x 10px
                if x == 90 and y == 70:
                    red, green, blue = 2, 52, 0  # coverage 0.8 → 채널0 이 정확히 2.5
            px[x, y] = (red, green, blue, alpha)
    return img


def _synthetic_reach_geometry_field(width: int = 40, height: int = 28) -> Image.Image:
    """거리변환(P2)의 **연결성 패리티와 경계 padding** 을 고정한다 (노드 3 추가).

    깊이는 최근접 keyed 픽셀까지의 체비셰프 거리다. 그 정의가 깨지는 두 방향이
    기존 9 케이스에서 안 잠겨 있었다.

    - **키 구멍 1px** `(10, 15)` 과 거기서 오프셋 `(4, 1)` 인 out-of-band 블렌드
      픽셀 `(14, 16)`. 체비셰프 깊이는 `max(4, 1) = 4` 라 원본은 unmix 하지만, 같은
      행 이웃을 잃은 구현(`for dy in (-1, 1)`)은 y 를 매 스텝 ±1 옮겨야 해서 홀수
      스텝이 필요하고 깊이 5 -> 미도달이다. 기존 픽스처는 최대깊이 픽셀이 전부 짝수
      패리티(오프셋 `(4, 0)`) 자리라 이 변형이 통과했다.
    - **상단 1행만 키 배경**, 나머지는 피험체. 바닥 행 `(25, 27)` 의 블렌드 픽셀은
      원본에서 깊이 27 이라 도달 불가지만, 시프트를 `np.roll` 로 짜면 바닥이 상단
      키 행과 이웃이 되어 깊이 1 -> unmix 된다. (가장자리를 clip 하는 구현은 등가다 —
      clip 이 만드는 좌표는 `dx = 0` / `dy = 0` 으로 어차피 도달하는 칸이다.)

    두 블렌드 픽셀 모두 tint 35 다 — 40 이하라 스필 패스가 손대지 않으므로
    **unmix 되었는가만** 관측된다.
    """
    img = Image.new("RGBA", (width, height), (200, 40, 180, 255))  # 피험체 바탕
    px = img.load()
    for x in range(width):  # 키 배경은 상단 1행뿐
        px[x, 0] = (11 + ((x * 3) % 5), 238 - (x % 7), 27, 255)
    px[10, 15] = (13, 235, 29, 255)   # 피험체 안쪽 키 구멍 (머리카락 틈)
    px[14, 16] = (150, 120, 20, 255)  # 오프셋 (4, 1) — 체비셰프 깊이 4
    px[25, 27] = (150, 120, 20, 255)  # 바닥 행 — wrap 구현에서만 깊이 1
    return img


def _synthetic_transparent_spill_field(width: int = 40, height: int = 30) -> Image.Image:
    """스필 후보 루프의 `if not alpha: continue` 를 잠근다 (노드 3 추가).

    이 스킵은 **`fringe_delta > 0` 일 때만** 무의미하다: P1 이 지운 픽셀은 전부
    `(0, 0, 0, 0)` 이고 그 tint 는 0 이라 `tint >= fringe_delta` 에서 걸러지기
    때문이다. 그런데 `--fringe-delta` 는 하한 없는 `type=float` CLI 플래그이고
    `slice_sheet`·`inspect`·`cutout` 까지 그대로 흘러간다. `0` 을 주면 지워진
    픽셀이 전부 후보가 되고, **강한 틴트 클러스터에 붙어 있으면 클러스터가 배경
    전체와 이어져 한도를 넘겨** 진짜 스필이 치료를 면한다.

    그래서 투명 24px 를 강한 틴트 24px **바로 옆에** 붙여 두고 이 케이스만
    `fringe_delta = 0.0` 으로 돌린다. 스킵을 지운 구현은 여기서 갈린다.
    (노드 3 1라운드에서는 이 mutant 가 살아남았고, 등가라고 논증할 뻔했다 —
    적대적 코퍼스 차분이 `fringe_delta = 0` 에서 29건의 차이를 찾아냈다.)
    """
    img = Image.new("RGBA", (width, height), (11, 238, 27, 255))
    px = img.load()
    for y in range(8, 24):
        for x in range(8, 32):
            px[x, y] = (200, 40, 180, 255)  # 피험체 (tint -150 → fringe_delta 0 에서도 subject)
    for y in range(14, 18):
        for x in range(14, 20):
            px[x, y] = (120, 200, 90, 255)  # 강한 틴트 24px (tint 95)
    for y in range(14, 18):
        for x in range(20, 26):
            px[x, y] = (0, 0, 0, 0)         # 거기 붙은 투명 24px
    return img


def _synthetic_classification_priority_field(width: int = 44, height: int = 32) -> Image.Image:
    """P1 분류 if/elif 의 **우선순위**를 잠근다 (노드 3 attempt 2, R1).

    `key_tint_score < fringe_delta -> _SUBJECT` 와
    `color_distance <= fringe_threshold -> _BLEND_IN_BAND` 의 순서를 맞바꾼 mutant 는
    **두 조건이 동시에 참인 픽셀**이 있어야만 관측된다. 기본 CLI 값에서는 그런 픽셀이
    존재할 수 없다 — 포화 2채널 키에서 교집합이 생기려면 코시-슈바르츠 상한
    `sqrt(1.5) * T > 255 - delta` 를 넘어야 하는데 `T=180, delta=18` 이면
    `220.4 vs 237` 로 **공집합**이다. 임계는 `T > 193.5`.
    `--fringe-key-threshold` 는 상한 없는 `type=float` CLI 플래그이고
    (`sprite_gen/cli.py:88,173,189`, `inspect.py:38`, 기본 180.0)
    `slice_sheet`·`inspect`·`cutout` 까지 그대로 흘러간다 — 도달 가능한 설정이다.
    그래서 이 케이스만 `fringe_threshold` 를 **196.0** 으로 둔다.

    if/elif 사슬을 벡터화하는 표준 수단이 `np.select`/중첩 `np.where` 이고, 그 변환에서
    유일하게 사람이 손으로 정하는 값이 **조건 우선순위**다. 잠글 자리가 바로 여기다.

    충돌 픽셀 `(80, 95, 80)`: 키 거리 195.959 <= 196, tint 15.0 < 18.
    - 원본 순서: `_SUBJECT` — unmix 패스가 건드리지 않아 `(80, 95, 80, 255)` 유지
    - 순서 교환: `_BLEND_IN_BAND` — depth <= 2 라 unmix 되어 `(85, 85, 85, 240)`

    `_REF_IN_BAND_UNMIX_KEY_DEPTH` 가 2 라 깊이 3 이상이면 in-band 도 건너뛰어져
    차이가 사라진다. 그래서 피험체 블록 왼쪽 모서리에 붙여 깊이 1·2 만 쓴다
    (`(12,11)`·`(12,12)` = 깊이 1, `(13,11)` = 깊이 2).

    unmix 뒤 색은 `(85, 85, 85)` 라 tint 0 이고 원본도 tint 15 < 18 이라, 두 갈래 모두
    스필 후보가 아니다 — 차이가 뒤 패스에서 씻기지 않고 출력까지 남는다.
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = img.load()
    for y in range(height):
        for x in range(width):
            # 흔들리는 키 배경 (거리 33.7~41.4 <= 96 → 전부 keyed)
            px[x, y] = (11 + ((x * 3) % 5), 238 - ((x + y) % 7), 27 + ((y * 2) % 5), 255)
    for y in range(10, 24):
        for x in range(12, 32):
            px[x, y] = (200, 40, 180, 255)  # 피험체 (거리 344.4, tint -150 → subject)
    for x, y in ((12, 11), (12, 12), (13, 11)):
        px[x, y] = (80, 95, 80, 255)  # 충돌 픽셀 — 거리 195.959, tint 15.0
    return img


def _synthetic_negative_fringe_delta_field(width: int = 32, height: int = 24) -> Image.Image:
    """되쓰기의 **0..255 클램프**를 잠근다 (노드 7 attempt 2, R1).

    옛 스칼라 경로는 `pixels[x, y] = unmix_key_blend(...)` 로 PIL 에 넣었고 PIL 이
    범위 밖 채널을 클램프했다. 벡터화 경로는 uint8 배열에 직접 대입이라 numpy 가
    **던진다** — `OverflowError: Python integer 257 out of bounds for uint8`.

    `out_alpha > 255` 가 되는 조건은 정확히 하나다: `fringe_delta < 0` 이면 tint 가
    음수인 픽셀이 `_SUBJECT` 로 안 빠지고 블렌드로 분류되고, `k = tint/key_tint < 0`
    → `coverage = 1 - k > 1` → `round(alpha * coverage) > alpha` 가 된다.
    `fringe_delta >= 0` 이면 블렌드는 tint >= 0 뿐이라 `coverage <= 1` 이다 —
    그래서 기존 13 케이스(delta 18.0·0.0)는 이 자리를 **한 번도 안 밟는다.**

    `--fringe-delta` 는 하한 없는 `type=float` CLI 플래그이고 진입점이 5자리다
    (`sprite_gen/cli.py:89,174,190`, `inspect.py:39`, `slice_sheet.py:315`) —
    `slice_sheet --fringe-delta=-5` 로 CLI 에서 그대로 도달한다. 노드 3 §C 가
    `fringe_delta = 0` 을 등가 논증 대신 픽스처로 처리하고 노드 3 R1 이
    `--fringe-key-threshold > 193.5` 를 블로킹으로 판정한 것과 같은 형태다.

    그래서 이 케이스만 `fringe_delta = -5.0` 으로 돌린다. **넘침의 크기는 delta 가
    가둔다** — 블렌드로 분류되려면 `tint >= fringe_delta` 여야 하므로 tint 는
    `[-5, 0)` 뿐이고 `coverage <= 1 + 5/255`, 즉 알파 255 픽셀의 상한이 260 이다.
    심은 픽셀 6종은 그 창 안에서 클램프 경계와 대조군을 같이 덮는다
    (키 `(0, 255, 0)`, `key_tint = 255`):

    | 좌표     | 입력                | tint  | 분류     | out_alpha | 되쓰기 | 잡는 변형 |
    |----------|---------------------|------:|----------|----------:|-------:|-----------|
    | (10, 9)  | `(60,58,60,255)`    |  -2.0 | out-band |       257 |    255 | 클램프 삭제 → OverflowError |
    | (10, 11) | `(60,59,60,255)`    |  -1.0 | out-band |       256 |    255 | `min(256, …)` 오프바이원 |
    | (10, 13) | `(60,56,60,255)`    |  -4.0 | out-band |       259 |    255 | `% 256` wrap (→ 3) |
    | (10, 15) | `(60,58,60,200)`    |  -2.0 | out-band |       202 |    202 | `out_alpha = 255` 무조건 대입 / `min(alpha, …)` |
    | (10, 17) | `(120,200,90,255)`  |  95.0 | in-band  |       160 |    160 | 양수 tint 경로 동반 회귀 |
    | (10, 19) | `(60,55,60,255)`    |  -5.0 | out-band |       260 |    255 | 음수 경계의 `< fringe_delta` → `<=` (피험체로 새면 무변경) |

    여섯 자리 모두 피험체 블록의 왼쪽 열이라 keyed 배경과 붙어 **깊이 1** 이고,
    (10, 17) 을 뺀 다섯은 키 거리 213.6~217.3 > 180 이라 `_BLEND_OUT_OF_BAND` 다 —
    in-band 깊이 제한(`_IN_BAND_UNMIX_KEY_DEPTH`)과 무관하게 unmix 된다. (10, 17) 은
    거리 159.8 로 in-band 이고 깊이 1 <= 2 라 역시 unmix 된다.

    블록 본체 `(200, 40, 180)` 은 tint -150 < -5 라 음수 delta 에서도 `_SUBJECT` 로
    남는다 — 이 케이스의 피험체 대조군이 그것이다.

    unmix 뒤 색은 전부 tint 0 (회색) 이라 스필 후보로는 잡히되 `_SPILL_MIN_TINT`
    40 이하라 치료되지 않는다 — 차이가 뒤 패스에서 씻기지 않고 출력까지 남는다.
    행 간격을 2로 벌려 여섯 픽셀이 서로 8-이웃이 아니게 두었다: 각자 1px 클러스터라
    스필 패스의 판정이 픽셀마다 독립이고 기대표가 흔들리지 않는다.
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = img.load()
    for y in range(height):
        for x in range(width):
            # 흔들리는 키 배경 (거리 33.7~41.4 <= 96 → 전부 keyed)
            px[x, y] = (11 + ((x * 3) % 5), 238 - ((x + y) % 7), 27 + ((y * 2) % 5), 255)
    for y in range(8, 21):
        for x in range(10, 24):
            px[x, y] = (200, 40, 180, 255)  # 피험체 (거리 344.4, tint -150 → delta -5 에서도 subject)
    px[10, 9] = (60, 58, 60, 255)     # coverage 1.0078 → 257
    px[10, 11] = (60, 59, 60, 255)    # coverage 1.0039 → 256 (클램프 경계 정확히 +1)
    px[10, 13] = (60, 56, 60, 255)    # coverage 1.0157 → 259
    px[10, 15] = (60, 58, 60, 200)    # 같은 coverage, 알파가 낮아 202 — 클램프 미적용 대조군
    px[10, 17] = (120, 200, 90, 255)  # 양수 tint 95 → coverage 0.6275 → 160
    px[10, 19] = (60, 55, 60, 255)    # tint 가 정확히 fringe_delta — coverage 1.0196 → 260
    return img


# (case id, image builder, key, threshold, fringe_threshold, fringe_delta, reach, spill_fraction)
CASES = [
    ("moe-magenta", lambda: _open_moe("moe_green.png"), MAGENTA,
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, CLI_FRINGE_DELTA, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
    ("moe-green", lambda: _open_moe("moe_red.png"), GREEN,
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, CLI_FRINGE_DELTA, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
    ("synthetic-cli-defaults", _synthetic_branch_field, GREEN,
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, CLI_FRINGE_DELTA, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
    ("synthetic-no-unmix", _synthetic_branch_field, GREEN,
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, CLI_FRINGE_DELTA, 0, CLI_SPILL_MAX_FRACTION),
    ("synthetic-no-spill", _synthetic_branch_field, GREEN,
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, CLI_FRINGE_DELTA, CLI_UNMIX_REACH, 0.0),
    ("synthetic-degenerate-tint", _synthetic_branch_field, (128, 128, 128),
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, CLI_FRINGE_DELTA, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
    ("synthetic-zero-coverage", _synthetic_zero_coverage, (0, 200, 0),
     20.0, CLI_FRINGE_THRESHOLD, CLI_FRINGE_DELTA, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
    ("synthetic-fraction-spill-limit", _synthetic_fraction_limit_field, GREEN,
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, CLI_FRINGE_DELTA, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
    ("synthetic-threshold-boundaries", _synthetic_threshold_boundary_field, GREEN,
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, CLI_FRINGE_DELTA, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
    ("synthetic-spill-semantics", _synthetic_spill_semantics_field, GREEN,
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, CLI_FRINGE_DELTA, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
    ("synthetic-reach-geometry", _synthetic_reach_geometry_field, GREEN,
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, CLI_FRINGE_DELTA, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
    # 유일하게 fringe_delta 를 0 으로 두는 케이스 — 픽스처 docstring 에 이유가 있다
    ("synthetic-transparent-spill", _synthetic_transparent_spill_field, GREEN,
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, 0.0, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
    # 유일하게 fringe_threshold 를 기본값 위로 올리는 케이스 (196.0 > 193.5 임계).
    # 기본 180.0 에서는 subject/in-band 조건의 교집합이 공집합이라 분류 우선순위가
    # 관측 불가다 — 픽스처 docstring 에 유도가 있다.
    ("synthetic-classification-priority", _synthetic_classification_priority_field, GREEN,
     CLI_KEY_THRESHOLD, 196.0, CLI_FRINGE_DELTA, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
    # 유일하게 fringe_delta 를 **음수**로 두는 케이스. 되쓰기의 0..255 클램프는
    # 여기서만 관측된다 — 나머지 13 케이스는 delta 18.0·0.0 이라 coverage <= 1 이다.
    ("synthetic-negative-fringe-delta", _synthetic_negative_fringe_delta_field, GREEN,
     CLI_KEY_THRESHOLD, CLI_FRINGE_THRESHOLD, -5.0, CLI_UNMIX_REACH, CLI_SPILL_MAX_FRACTION),
]

# 절대 잠금 — 프로덕션과 참조를 **같이** 고쳐 1번 잠금을 우회하는 경로를 막는다.
# 값은 최적화 전 커밋(engine_revision 3adf0169561a)에서 뽑았다.
EXPECTED_OUTPUT_SHA256 = {
    "moe-magenta": "e987f14173a4ca102ad4e4b8ffbe13379ada43149f84c678fcd2d57b227fabbc",
    "moe-green": "674709d6f773f0f0e84219a518d4a8c121a9e047d57f2c286da4bc3b4e6f73e0",
    "synthetic-cli-defaults": "734f377991d3987ebbbd711b42b9ec99f7de86ca3cf9cf5be6d7f22b6bbf6821",
    "synthetic-no-unmix": "1c667d0d0f01498e69afea8ce2ebb47dae890433b1cbeb0ecf8f2fa264bbc68d",
    "synthetic-no-spill": "7426d33bfc6956db63e5818b5f0af38adedc1ac9b736091696957cbbb49c9520",
    "synthetic-degenerate-tint": "249ceacd332f509d978e96b38080d7f9663ffa08eb2088494f385d490309f572",
    "synthetic-zero-coverage": "32b2bdfc5d97d8ad7b97cc5f0da65bd5cd1be19ac6c4a4016c1f5bc69a48f762",
    "synthetic-fraction-spill-limit": "262fd62b164365c23a6505da8ce025b8d38841c572dbf13c2781a94757da898c",
    "synthetic-threshold-boundaries": "e1c84624a9a497595fe0f7db45a242e94d97e168935b24a0ffb347aee681d82a",
    # 노드 3 추가분. 같은 최적화 전 엔진(`extract.py` 무변경, engine_revision
    # 3adf0169561a)에서 뽑았다 — 위 7개와 같은 기준선이다.
    "synthetic-spill-semantics": "ef45e6c84f334c8925c9e0df6be3a1beecfa6b59cec7611bbeb1b65af8fae2d1",
    "synthetic-reach-geometry": "0d5030be40603283ea3e9743b2c1e069481b9ae38695bc99d50ec4c75415cf5f",
    "synthetic-transparent-spill": "62309e999d53448eaab201e023cf24e734a1c780fc5d5287f0176eb755eafebf",
    # 노드 3 attempt 2(R1) 추가분. 같은 기준선이다 — `extract.py` 는 노드 3 내내
    # 무변경이고(engine_revision 3adf0169561a) 이 커밋에서도 손대지 않았다.
    "synthetic-classification-priority": "65f0961c7e4a9a6cc98b2ec2f1bfd08d3341fff1df8b8576ce8fa1f71f41be03",
    # 노드 7 attempt 2(R1) 추가분. 위 13개는 최적화 전 커밋에서 뽑았고 이 값도 같은
    # 기준선이다 — **동결 사본이 아니라 `git show 05c549a:sprite_gen/frames/extract.py` 로
    # 뽑은 그때 실제로 돌던 모듈**에 이 픽스처를 먹여 얻었다(하네스
    # `_assets/extract-numpy-vectorization/probe_negative_delta_case.py`).
    # 클램프를 잃은 4ae7d70 은 이 케이스에서 해시를 내지 못한다 — OverflowError 로
    # 죽는다. 그래서 이 한 줄은 "값이 다르다" 가 아니라 "돌긴 하는가" 까지 잠근다.
    "synthetic-negative-fringe-delta": "8711c53554807c59714d1d6485ac107e234e8530e895475afbc474a4bc363b9b",
}

# 분기 도달 계측 고정 — 픽스처가 조용히 분기를 잃으면 여기서 걸린다.
EXPECTED_COVERAGE = {
    "moe-magenta": {
        "blend_in_band": 101,
        "blend_out_of_band": 48,
        "degenerate_key_tint": 0,
        "depth_positive": 3158,
        "depth_reached_max": 1,
        "keyed_by_distance": 18312,
        "spill_candidates": 11,
        "spill_cluster_low_tint": 10,
        "spill_cluster_treated": 1,
        "spill_limit_from_fraction": 0,
        "spill_treated_px": 1,
        "subject": 6115,
        "unmix_collapsed_to_transparent": 0,
        "unmix_in_band": 100,
        "unmix_in_band_skipped_deep": 1,
        "unmix_out_of_band": 38,
    },
    "moe-green": {
        "blend_in_band": 60,
        "blend_out_of_band": 40,
        "degenerate_key_tint": 0,
        "depth_positive": 3051,
        "depth_reached_max": 1,
        "keyed_by_distance": 18614,
        "spill_candidates": 1,
        "spill_cluster_treated": 1,
        "spill_limit_from_fraction": 0,
        "spill_treated_px": 1,
        "subject": 5862,
        "unmix_collapsed_to_transparent": 0,
        "unmix_in_band": 59,
        "unmix_in_band_skipped_deep": 1,
        "unmix_out_of_band": 40,
    },
    "synthetic-cli-defaults": {
        "blend_in_band": 833,
        "blend_out_of_band": 118,
        "degenerate_key_tint": 0,
        "depth_positive": 768,
        "depth_reached_max": 1,
        "keyed_alpha_zero": 192,
        "keyed_by_distance": 4032,
        "spill_candidates": 559,
        "spill_cluster_low_tint": 2,
        "spill_cluster_too_big": 2,
        "spill_cluster_treated": 3,
        "spill_limit_from_fraction": 0,
        "spill_treated_px": 22,
        "subject": 1737,
        "unmix_collapsed_to_transparent": 0,
        "unmix_in_band": 392,
        "unmix_in_band_skipped_deep": 360,
        "unmix_out_of_band": 16,
    },
    "synthetic-no-unmix": {
        "blend_in_band": 833,
        "blend_out_of_band": 118,
        "degenerate_key_tint": 0,
        "depth_positive": 0,
        "depth_reached_max": 0,
        "keyed_alpha_zero": 192,
        "keyed_by_distance": 4032,
        "spill_candidates": 951,
        "spill_cluster_low_tint": 2,
        "spill_cluster_too_big": 2,
        "spill_cluster_treated": 3,
        "spill_limit_from_fraction": 0,
        "spill_treated_px": 22,
        "subject": 1737,
    },
    "synthetic-no-spill": {
        "blend_in_band": 833,
        "blend_out_of_band": 118,
        "degenerate_key_tint": 0,
        "depth_positive": 768,
        "depth_reached_max": 1,
        "keyed_alpha_zero": 192,
        "keyed_by_distance": 4032,
        "subject": 1737,
        "unmix_collapsed_to_transparent": 0,
        "unmix_in_band": 392,
        "unmix_in_band_skipped_deep": 360,
        "unmix_out_of_band": 16,
    },
    "synthetic-degenerate-tint": {
        "degenerate_key_tint": 1,
        "depth_positive": 0,
        "depth_reached_max": 0,
        "keyed_alpha_zero": 192,
        "keyed_by_distance": 996,
        "subject": 5724,
    },
    "synthetic-zero-coverage": {
        "blend_in_band": 53,
        "degenerate_key_tint": 0,
        "depth_positive": 308,
        "depth_reached_max": 1,
        "keyed_by_distance": 756,
        "spill_candidates": 9,
        "spill_cluster_treated": 1,
        "spill_limit_from_fraction": 0,
        "spill_treated_px": 9,
        "spill_zero_coverage": 9,
        "subject": 471,
        "unmix_collapsed_to_transparent": 44,
        "unmix_in_band": 44,
    },
    "synthetic-fraction-spill-limit": {
        "blend_in_band": 745,
        "degenerate_key_tint": 0,
        "depth_positive": 1488,
        "depth_reached_max": 1,
        "keyed_by_distance": 7280,
        "spill_candidates": 41,
        "spill_cluster_treated": 1,
        "spill_limit_from_fraction": 1,
        "spill_treated_px": 41,
        "subject": 7375,
        "unmix_collapsed_to_transparent": 0,
        "unmix_in_band": 704,
    },
    "synthetic-threshold-boundaries": {
        "blend_in_band": 8,
        "blend_out_of_band": 6,
        "degenerate_key_tint": 0,
        "depth_positive": 67,
        "depth_reached_max": 1,
        "keyed_alpha_zero": 1,
        "keyed_by_distance": 1852,
        "spill_candidates": 2,
        "spill_cluster_treated": 1,
        "spill_limit_from_fraction": 0,
        "spill_treated_px": 2,
        "subject": 53,
        "unmix_collapsed_to_transparent": 0,
        "unmix_in_band": 6,
        "unmix_in_band_skipped_deep": 2,
        "unmix_out_of_band": 6,
    },
    # 노드 3 추가분. `subject 7928 + 61 + 11 == 8000` 이 스필 한도 산식의 입력이고,
    # `treated 2 / too_big 1 / treated_px 12` 가 max-vs-mean·한도 갈음 mutant 를
    # 잡는 구성이다 — 이 숫자가 흔들리면 그 mutant 들이 다시 살아난다.
    "synthetic-spill-semantics": {
        "blend_in_band": 61,
        "blend_out_of_band": 11,
        "degenerate_key_tint": 0,
        "depth_positive": 1376,
        "depth_reached_max": 1,
        "keyed_by_distance": 11200,
        "spill_candidates": 72,
        "spill_cluster_too_big": 1,
        "spill_cluster_treated": 2,
        "spill_limit_from_fraction": 1,
        "spill_treated_px": 12,
        "subject": 7928,
    },
    # `unmix_out_of_band == 1` 이 이 케이스의 전부다 — 깊이 4 짜리 블렌드 픽셀
    # 하나가 unmix 되고 바닥 행 픽셀은 도달하지 않는다는 사실 자체가 단정이다.
    "synthetic-reach-geometry": {
        "blend_out_of_band": 2,
        "degenerate_key_tint": 0,
        "depth_positive": 240,
        "depth_reached_max": 1,
        "keyed_by_distance": 41,
        "spill_candidates": 1,
        "spill_cluster_low_tint": 1,
        "spill_limit_from_fraction": 0,
        "subject": 1077,
        "unmix_collapsed_to_transparent": 0,
        "unmix_out_of_band": 1,
    },
    # `keyed_alpha_zero 24` 가 `spill_candidates 24` 에 **안 섞여 있다**는 것이
    # 이 케이스의 단정이다 — 스킵을 지우면 후보가 배경 전체로 번져 클러스터가
    # 한도를 넘고 `spill_cluster_treated` 가 0 이 된다.
    "synthetic-transparent-spill": {
        "blend_in_band": 24,
        "degenerate_key_tint": 0,
        "depth_positive": 328,
        "depth_reached_max": 1,
        "keyed_alpha_zero": 24,
        "keyed_by_distance": 816,
        "spill_candidates": 24,
        "spill_cluster_treated": 1,
        "spill_limit_from_fraction": 0,
        "spill_treated_px": 24,
        "subject": 336,
        "unmix_collapsed_to_transparent": 0,
        "unmix_in_band": 8,
        "unmix_in_band_skipped_deep": 8,
    },
    # 이 케이스가 고정하는 것은 **분기 도달이 아니라 미도달**이다: `blend_in_band` 와
    # `unmix_*` 가 아예 없다. 충돌 픽셀 3개가 전부 `subject 280` 안에 들어 있고 unmix
    # 패스가 아무것도 안 한다 — 그래서 우선순위를 뒤집은 프로덕션이 그 3개를 unmix 하면
    # 출력 바이트가 갈린다. (이 표는 참조 구현으로 재는 픽스처측 가드라 프로덕션
    # mutant 로는 안 빨개진다. 잠그는 것은 픽스처가 조용히 이 구도를 잃는 경우다 —
    # 충돌 픽셀이 subject 를 벗어나면 `subject 280` 이 어긋나 여기서 걸린다.)
    "synthetic-classification-priority": {
        "degenerate_key_tint": 0,
        "depth_positive": 208,
        "depth_reached_max": 1,
        "keyed_by_distance": 1128,
        "spill_candidates": 0,
        "spill_limit_from_fraction": 0,
        "subject": 280,
    },
    # `unmix_alpha_over_255: 4` 가 이 케이스의 본체다 — 이 계수는 **여기에만** 있고
    # (조건부 bump 라 나머지 12 케이스에는 키 자체가 없다) 되쓰기 클램프가 실제로
    # 일하는 픽셀 수를 센다. 픽스처가 조용히 그 4자리를 잃으면
    # `test_every_branch_is_reached_by_some_case` 가 먼저 빨개진다.
    # `blend_out_of_band 5 / unmix_out_of_band 5` 는 심은 6종 중 (10,17) 을 뺀 다섯이고,
    # `subject 176` 은 블록 본체(음수 delta 에서도 피험체로 남는 대조군)다.
    "synthetic-negative-fringe-delta": {
        "blend_in_band": 1,
        "blend_out_of_band": 5,
        "degenerate_key_tint": 0,
        "depth_positive": 152,
        "depth_reached_max": 1,
        "keyed_by_distance": 586,
        "spill_candidates": 6,
        "spill_cluster_low_tint": 6,
        "spill_limit_from_fraction": 0,
        "subject": 176,
        "unmix_alpha_over_255": 4,
        "unmix_collapsed_to_transparent": 0,
        "unmix_in_band": 1,
        "unmix_out_of_band": 5,
    },
}


def _case_params(case):
    return dict(zip(
        ("threshold", "fringe_threshold", "fringe_delta", "unmix_reach", "spill_max_fraction"),
        case[3:],
    ))


def _run_production(case, image=None):
    """`image` 를 주면 **그 이미지 객체 그대로** 넘긴다 — 입력 불변 단정은 자기가
    들고 있는 객체가 들어가야 성립한다 (노드 3: 매번 새로 빌드하면 공허해진다)."""
    _, builder, key, threshold, fringe_threshold, fringe_delta, reach, spill = case
    return extract.remove_chroma_background(
        builder() if image is None else image,
        key, threshold, fringe_threshold, fringe_delta,
        unmix_reach=reach, spill_max_fraction=spill,
    )


def _run_reference(case, counters=None):
    _, builder, key, threshold, fringe_threshold, fringe_delta, reach, spill = case
    return _ref_remove_chroma_background(
        builder(), key, threshold, fringe_threshold, fringe_delta,
        unmix_reach=reach, spill_max_fraction=spill, counters=counters,
    )


def _sha256(image: Image.Image) -> str:
    return hashlib.sha256(image.tobytes()).hexdigest()


# --- 프리미티브: 프로덕션 vs 동결 사본 --------------------------------------

# 결정론 스윕 — 임계 경계(63/64, 191/192)와 채널 극단을 모두 포함한다
_SWEEP = (0, 1, 17, 63, 64, 127, 128, 191, 192, 254, 255)
# 마지막 두 키는 `key_tint_score` 의 **채널 선택 임계 자체**를 밟는 자리다 (노드 3).
# 색 쪽 스윕만으로는 `value >= 192` -> `> 192`, `value < 64` -> `<= 64` 가 안 잠긴다 —
# 채널값이 정확히 192·64 인 키가 없으면 두 연산자가 같은 채널 집합을 고른다.
#   (192, 255, 0): keyed 가 [0, 1] vs [1] 로 갈린다
#   (0, 255, 64):  unkeyed 가 [0] vs [0, 2] 로 갈린다
_KEYS = [
    GREEN, MAGENTA, (0, 255, 255), (255, 255, 0), (11, 238, 27), (0, 200, 0),
    (255, 255, 255), (0, 0, 0), (128, 128, 128), (200, 255, 30),
    (192, 255, 0), (0, 255, 64),
]
# 축퇴 키 — 키 채널이 아예 없거나(전부 어두움) 언키 채널이 아예 없다(전부 밝음).
# 두 방향이 다 있어야 `_key_channel_split` 의 가드가 양쪽에서 잠긴다: 한쪽 항만
# 남기면 나머지 방향에서 0 나눗셈이 샌다.
_DEGENERATE_KEYS = [(128, 128, 128), (255, 255, 255), (0, 0, 0), (200, 200, 200), (63, 63, 63)]


def test_color_distance_matches_frozen_scalar_reference():
    """1,331 색 x 10 키 전수 — 근사 아니라 정확한 float 동일."""
    checked = 0
    for key in _KEYS:
        for red in _SWEEP:
            for green in _SWEEP:
                for blue in _SWEEP:
                    color = (red, green, blue)
                    got = extract.color_distance(color, key)
                    ref = _ref_color_distance(color, key)
                    assert got == ref, f"key={key} color={color}: {got!r} != {ref!r}"
                    checked += 1
    assert checked == len(_KEYS) * len(_SWEEP) ** 3


def test_key_tint_score_matches_frozen_scalar_reference():
    checked = 0
    for key in _KEYS:
        for red in _SWEEP:
            for green in _SWEEP:
                for blue in _SWEEP:
                    color = (red, green, blue)
                    got = extract.key_tint_score(color, key)
                    ref = _ref_key_tint_score(color, key)
                    assert got == ref, f"key={key} color={color}: {got!r} != {ref!r}"
                    checked += 1
    assert checked == len(_KEYS) * len(_SWEEP) ** 3


def test_key_tint_score_returns_zero_for_degenerate_keys():
    """축퇴 키(키 채널 없음 / 언키 채널 없음)는 0.0 — 이 값이
    `remove_chroma_background` 의 unmix·spill 패스 전체를 끄는 스위치다."""
    for key in _DEGENERATE_KEYS:
        assert extract.key_tint_score((10, 200, 30), key) == 0.0
        assert _ref_key_tint_score((10, 200, 30), key) == 0.0


def test_despill_and_unmix_match_frozen_scalar_reference():
    """despill/unmix 는 벡터화 대상 안쪽의 산수라 별도로 고정한다."""
    key = GREEN
    key_tint = _ref_key_tint_score(key, key)
    for red in _SWEEP:
        for green in _SWEEP:
            for blue in _SWEEP:
                color = (red, green, blue)
                tint = _ref_key_tint_score(color, key)
                if tint <= 0:
                    continue
                for alpha in (1, 17, 128, 254, 255):
                    assert extract.despill_color(color, key, key_tint, tint) == \
                        _ref_despill_color(color, key, key_tint, tint)
                    assert extract.unmix_key_blend(color, alpha, key, key_tint, tint) == \
                        _ref_unmix_key_blend(color, alpha, key, key_tint, tint)


# --- 음수 tint 영역: 되쓰기 클램프가 기대는 두 전제 --------------------------
# 위 스윕은 `tint <= 0` 을 건너뛴다. 그 영역이 `--fringe-delta < 0` 에서 블렌드로
# 분류되는 자리이고, `coverage = 1 - tint/key_tint > 1` 이라 `round(alpha*coverage)`
# 가 알파를 **키운다** — 옛 경로는 그걸 PIL 이 조여서 넣었고 배열 되쓰기는 던진다.
# 프로덕션 되쓰기가 알파 **하나만**, **위로만** 조이는 근거가 아래 두 단정이다.
# 둘 다 프로덕션 프리미티브를 재는 것이지 동결 사본을 재는 게 아니다.


def _sweep_tints(key):
    """스칼라 스윕 1,331 색을 tint 부호로 갈라 준다."""
    key_tint = _ref_key_tint_score(key, key)
    negative, positive = [], []
    for red in _SWEEP:
        for green in _SWEEP:
            for blue in _SWEEP:
                color = (red, green, blue)
                tint = _ref_key_tint_score(color, key)
                (negative if tint <= 0 else positive).append((color, tint))
    return key_tint, negative, positive


def test_despill_color_channels_stay_in_byte_range():
    """되쓰기가 RGB 를 다시 클램프하지 않는 근거 — `despill_color` 가 이미 조인다.

    두 영역 다 돈다. `k >= 0` 에서는 외삽이라 범위를 벗어날 수 있고
    (`min(255, max(0, …))` 가 거기서 일한다), `k < 0` 에서는 색과 키의 볼록결합이라
    구조적으로 못 벗어난다. 이 단정이 빨개지면 되쓰기의 "알파 하나만" 전제가 깨진
    것이므로 클램프 자리를 다시 정해야 한다.
    """
    for key in (GREEN, MAGENTA):
        key_tint, negative, positive = _sweep_tints(key)
        assert negative and positive, f"{key}: 한쪽 영역이 비었다 — 스윕이 공허하다"
        for color, tint in negative + positive:
            _, despilled = extract.despill_color(color, key, key_tint, tint)
            assert all(0 <= channel <= 255 for channel in despilled), \
                f"key={key} color={color} tint={tint}: {despilled}"


def test_unmix_key_blend_alpha_overflows_upward_only_for_negative_tint():
    """되쓰기가 위쪽 한 방향만 조이는 근거 + 그 자리가 공허하지 않다는 근거.

    - 알파는 절대 음수가 안 된다: `out_alpha <= 0` 이면 완전 투명으로 접힌다.
      그래서 되쓰기에 `max(0, …)` 가 없다.
    - 알파는 음수 tint 에서 **실제로 255 를 넘는다**. 넘는 사례가 0이면 되쓰기
      클램프도, 게이트의 음수 delta 케이스도 아무것도 잠그지 않는 장식이 된다.
    - 그 영역에서도 프로덕션 프리미티브는 동결 사본과 정확히 같은 값을 낸다 —
      클램프는 프리미티브가 아니라 되쓰기의 일이다 (프리미티브를 고치면 이 단정이
      빨개진다).
    """
    overflowed = 0
    for key in (GREEN, MAGENTA):
        key_tint, negative, _ = _sweep_tints(key)
        for color, tint in negative:
            for alpha in (0, 1, 17, 128, 200, 254, 255):
                got = extract.unmix_key_blend(color, alpha, key, key_tint, tint)
                assert got == _ref_unmix_key_blend(color, alpha, key, key_tint, tint)
                assert got[3] >= 0, f"key={key} color={color} alpha={alpha}: {got}"
                overflowed += got[3] > 255
    assert overflowed > 0, "음수 tint 스윕이 알파 255 초과를 한 번도 안 냈다 — 공허하다"


# --- 배열 커널: 프로덕션 vs 같은 동결 사본 ------------------------------------
# `remove_chroma_background` 는 이제 픽셀마다 `color_distance`·`key_tint_score` 를
# 부르지 않고 `_key_distance_field`·`_key_tint_field` 에 이미지를 통째로 넘긴다.
# 위의 스칼라 스윕이 지키던 그 산수가 **이 함수들로 옮겨간 것**이라, 스윕도 같이
# 옮긴다 — 안 옮기면 rcb 가 타는 경로는 픽스처 몇 장으로만 덮이고, float32 강등처럼
# 임계에 정확히 걸리는 픽셀이 없으면 관측되지 않는 슬립이 조용히 통과한다
# (노드 7 하네스에서 실제로 SURVIVED 로 재현한 뒤 이 두 단정을 넣었다).
# 참조는 위와 **같은 동결 스칼라 사본**이므로 프로덕션을 따라 움직이지 않는다.


def _sweep_color_field():
    """스칼라 스윕과 같은 1,331 색을 (N, 1, 3) 배열 한 장으로."""
    colors = [(red, green, blue) for red in _SWEEP for green in _SWEEP for blue in _SWEEP]
    return colors, np.array(colors, dtype=np.int32).reshape(len(colors), 1, 3)


def test_key_distance_field_matches_frozen_scalar_reference():
    """1,331 색 x 12 키 전수 — 배열 쪽도 근사가 아니라 정확한 float 동일."""
    colors, field = _sweep_color_field()
    checked = 0
    for key in _KEYS:
        got = extract._key_distance_field(field, key)
        assert got.dtype == np.float64, f"key={key}: {got.dtype} — float64 계약 위반"
        for index, color in enumerate(colors):
            ref = _ref_color_distance(color, key)
            assert got[index, 0] == ref, f"key={key} color={color}: {got[index, 0]!r} != {ref!r}"
            checked += 1
    assert checked == len(_KEYS) * len(_SWEEP) ** 3


def test_key_tint_field_matches_frozen_scalar_reference():
    """축퇴 키까지 포함한다 — 배열 경로의 축퇴 스위치도 같은 자리에서 갈려야 한다."""
    colors, field = _sweep_color_field()
    checked = 0
    for key in _KEYS + _DEGENERATE_KEYS:
        keyed_channels, unkeyed_channels = extract._key_channel_split(key)
        got = extract._key_tint_field(field, keyed_channels, unkeyed_channels)
        assert got.dtype == np.float64, f"key={key}: {got.dtype} — float64 계약 위반"
        for index, color in enumerate(colors):
            ref = _ref_key_tint_score(color, key)
            assert got[index, 0] == ref, f"key={key} color={color}: {got[index, 0]!r} != {ref!r}"
            checked += 1
    assert checked == (len(_KEYS) + len(_DEGENERATE_KEYS)) * len(_SWEEP) ** 3


# --- remove_chroma_background: 바이트 동일 ---------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_remove_chroma_background_byte_identical_to_frozen_reference(case):
    got = _run_production(case)
    ref = _run_reference(case)
    assert got.size == ref.size
    assert got.mode == ref.mode == "RGBA"
    assert got.tobytes() == ref.tobytes(), f"{case[0]}: {_sha256(got)} != {_sha256(ref)}"


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_remove_chroma_background_matches_pinned_output_hash(case):
    """절대 잠금 — 프로덕션과 동결 참조를 같이 고쳐도 여기서 걸린다."""
    expected = EXPECTED_OUTPUT_SHA256[case[0]]
    assert expected != "PENDING", "고정 해시가 비어 있다 — 게이트가 아니다"
    assert _sha256(_run_production(case)) == expected
    assert _sha256(_run_reference(case)) == expected


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_case_branch_coverage_is_pinned(case):
    """픽스처가 어떤 분기를 실제로 밟는지 고정 — 통과만 하는 단정을 막는다."""
    counters = {}
    _run_reference(case, counters=counters)
    assert counters == EXPECTED_COVERAGE[case[0]], f"{case[0]}: {counters}"


def test_every_branch_is_reached_by_some_case():
    """벡터화 대상 분기가 하나도 미도달로 남지 않는다 — 미도달 분기는 mutant 로
    검증할 수 없으므로(체크리스트 3번) 여기서 잡는다."""
    required = {
        "keyed_by_distance", "keyed_alpha_zero", "subject", "blend_in_band", "blend_out_of_band",
        "depth_positive", "depth_reached_max", "unmix_in_band", "unmix_in_band_skipped_deep",
        "unmix_out_of_band", "unmix_collapsed_to_transparent", "spill_candidates",
        "spill_cluster_treated", "spill_cluster_too_big", "spill_cluster_low_tint",
        "spill_limit_from_fraction", "degenerate_key_tint", "spill_zero_coverage",
        # 되쓰기 클램프가 일하는 유일한 분기. 어느 픽스처도 알파 255 를 넘기지
        # 않으면 클램프 단정 전체가 공허해지므로 여기서 미도달을 잡는다.
        "unmix_alpha_over_255",
    }
    reached = {name for coverage in EXPECTED_COVERAGE.values()
               for name, count in coverage.items() if count}
    assert required <= reached, f"미도달 분기: {sorted(required - reached)}"


def test_remove_chroma_background_does_not_mutate_its_input():
    """`convert("RGBA")` 가 사본을 만든다는 계약. numpy 로 옮길 때 `np.asarray`
    뷰에 제자리 기록하면 조용히 깨지고, `slice_sheet`·`cutout` 은 입력 이미지를
    호출 뒤에도 들고 있다.

    노드 3 수리: 첫 판은 `source` 를 만들어 놓고 프로덕션에는 `builder()` 로 **새로
    빌드한 다른 객체**를 넘겨서, `rgba = image` (사본 미생성) mutant 가 그대로
    통과했다. 통과만 하는 단정이었다. 이제 들고 있는 객체를 그대로 넘긴다."""
    for case in CASES:
        source = case[1]()
        before = source.tobytes()
        result = _run_production(case, image=source)
        assert result is not source, f"{case[0]}: 입력 객체를 그대로 반환했다"
        assert source.tobytes() == before, f"{case[0]}: 입력이 변형됐다"


# --- 티어 2 대상 (플랜 Scope 행에 포함) ------------------------------------


def _cleaned_synthetic():
    return _run_production(CASES[2])


def _tier2_images():
    """크로마 출력(실전 입력) + 경계값 픽스처(연산자 판정) 양쪽."""
    return (_cleaned_synthetic(), _open_moe("moe_red.png"), _alpha_boundary_field())


def test_connected_components_matches_frozen_scalar_reference():
    for image in _tier2_images():
        got = extract.connected_components(image)
        ref = _ref_connected_components(image)
        assert len(got) == len(ref) and got, "성분이 0개면 픽스처가 무의미하다"
        for left, right in zip(got, ref):
            assert left["area"] == right["area"]
            assert left["bbox"] == right["bbox"]
            assert left["center_x"] == right["center_x"]
            # 픽셀 목록은 순서까지 계약이다 — 하류가 이 순서로 이미지를 재구성한다
            assert left["pixels"] == right["pixels"]


def test_connected_components_alpha_cutoff_boundary_is_pinned():
    """`> 16` 컷오프 자체를 고정 — alpha 16 은 배경, 17 은 성분이다.
    경계값 픽셀이 없으면 컷오프를 한 칸 옮기는 변경이 게이트를 통과한다."""
    image = _alpha_boundary_field()
    alpha = image.getchannel("A").tobytes()
    assert alpha.count(16) and alpha.count(17), "픽스처에 경계 alpha 가 없다"
    components = extract.connected_components(image)
    assert components, "성분이 0개면 픽스처가 무의미하다"
    assert sum(component["area"] for component in components) == sum(1 for value in alpha if value > 16)


def test_edge_histograms_matches_frozen_scalar_reference():
    for image in _tier2_images():
        assert extract._edge_histograms(image) == _ref_edge_histograms(image)


def test_boundary_mass_matches_frozen_scalar_reference():
    for image in _tier2_images():
        assert extract._boundary_mass(image) == _ref_boundary_mass(image)


# --- 격리 불변식 -----------------------------------------------------------

_FROZEN_BEGIN = "--- FROZEN SCALAR REFERENCE (BEGIN) ---"
_FROZEN_END = "--- FROZEN SCALAR REFERENCE (END) ---"


def _frozen_region() -> str:
    source = Path(__file__).read_text(encoding="utf-8")
    start = source.index(_FROZEN_BEGIN)
    end = source.index(_FROZEN_END)
    assert start < end
    return source[start:end]


def test_frozen_reference_is_isolated_from_production():
    """이 게이트의 존재 이유. 동결 구역이 프로덕션 모듈을 참조하면 참조가
    프로덕션을 따라 움직여 새 코드를 새 코드와 비교하고 무조건 통과한다 —
    그 상태의 green 은 근거가 아니라 위증이다. 그래서 소스 단위로 강제한다."""
    frozen = _frozen_region()
    for token in (r"\bextract\b", r"\bsprite_gen\b", r"\bImage\b"):
        assert not re.search(token, frozen), f"동결 구역이 {token} 를 참조한다"
    # 참조가 실제로 존재하는지 (구역 마커만 남고 내용이 사라지는 사고 방지)
    for name in ("_ref_color_distance", "_ref_key_tint_score", "_ref_remove_chroma_background",
                 "_ref_connected_components", "_ref_edge_histograms", "_ref_boundary_mass"):
        assert f"def {name}(" in frozen


def test_fixture_bytes_are_pinned():
    """실촬 픽스처가 조용히 교체되면 고정 해시 전부가 의미를 잃는다."""
    for name, expected in MOE_FIXTURE_SHA256.items():
        assert expected != "PENDING", "픽스처 해시가 비어 있다"
        digest = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
        assert digest == expected, f"{name}: {digest}"
