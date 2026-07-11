# SPDX-License-Identifier: Apache-2.0
"""Extract component-row sprite strips into clean RGBA frames."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from statistics import median
from typing import Any

from PIL import Image

from sprite_gen.runio import acquire_run_dir_lock, atomic_save_image, atomic_write_text, relative_posix
from sprite_gen.segment import separate_fused_poses


def color_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def alpha_nonzero_count(image: Image.Image) -> int:
    return sum(image.getchannel("A").histogram()[1:])


def edge_alpha_count(image: Image.Image, margin: int) -> int:
    alpha = image.getchannel("A")
    width, height = image.size
    total = 0
    for box in (
        (0, 0, width, margin),
        (0, height - margin, width, height),
        (0, 0, margin, height),
        (width - margin, 0, width, height),
    ):
        total += sum(alpha.crop(box).histogram()[1:])
    return total


def key_tint_score(color: tuple[int, int, int], chroma_key: tuple[int, int, int]) -> float:
    keyed_channels = [index for index, value in enumerate(chroma_key) if value >= 192]
    unkeyed_channels = [index for index, value in enumerate(chroma_key) if value < 64]
    if not keyed_channels or not unkeyed_channels:
        return 0.0
    keyed_average = sum(color[index] for index in keyed_channels) / len(keyed_channels)
    unkeyed_average = sum(color[index] for index in unkeyed_channels) / len(unkeyed_channels)
    return keyed_average - unkeyed_average



def despill_color(
    color: tuple[int, int, int],
    chroma_key: tuple[int, int, int],
    key_tint: float,
    tint: float,
) -> tuple[float, tuple[int, int, int]]:
    """Estimate the key fraction of a blend pixel and remove it from the RGB.

    Blend model: observed = (1-k)*subject + k*key. key_tint_score is linear in
    the channels and scores the key itself at `key_tint`, so k = tint/key_tint
    recovers a subject estimate whose own tint score is ~0. Returns the
    subject coverage (1-k) and the despilled color.
    """
    k = min(tint / key_tint, 1.0)
    coverage = 1.0 - k
    if coverage <= 0:
        return 0.0, (0, 0, 0)
    red, green, blue = (
        min(255, max(0, round((color[index] - k * chroma_key[index]) / coverage)))
        for index in range(3)
    )
    return coverage, (red, green, blue)


def unmix_key_blend(
    color: tuple[int, int, int],
    alpha: int,
    chroma_key: tuple[int, int, int],
    key_tint: float,
    tint: float,
) -> tuple[int, int, int, int]:
    """Separate a key/subject blend pixel into despilled RGB + partial alpha."""
    coverage, despilled = despill_color(color, chroma_key, key_tint, tint)
    out_alpha = round(alpha * coverage)
    if out_alpha <= 0:
        return (0, 0, 0, 0)
    return (*despilled, out_alpha)


# remove_chroma_background pixel classes, decided once on the source colors.
_KEYED = 0  # erased: transparent input or hard key cut
_SUBJECT = 1  # not key-tinted — never touched
_BLEND_IN_BAND = 2  # key-tinted, within fringe_threshold of the key
_BLEND_OUT_OF_BAND = 3  # key-tinted, farther than fringe_threshold
_IN_BAND_UNMIX_KEY_DEPTH = 2

# A trapped-spill cluster must contain at least one strongly key-tinted pixel
# to be treated. This is the plan's visible-residue detector (every keyed
# channel clears every unkeyed channel by >40): warm subject colors (skin)
# score a marginal tint just above fringe_delta and must not be "corrected".
_SPILL_MIN_TINT = 40.0


def remove_chroma_background(
    image: Image.Image,
    chroma_key: tuple[int, int, int],
    threshold: float,
    fringe_threshold: float,
    fringe_delta: float,
    *,
    unmix_reach: int = 4,
    spill_max_fraction: float = 0.005,
) -> Image.Image:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    classes = bytearray(width * height)
    unseen = 255
    depths = bytearray(b"\xff" * (width * height))  # chebyshev distance to keyed region
    keyed: list[int] = []
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            index = y * width + x
            color = (red, green, blue)
            if alpha == 0 or color_distance(color, chroma_key) <= threshold:
                pixels[x, y] = (0, 0, 0, 0)
                classes[index] = _KEYED
                depths[index] = 0
                keyed.append(index)
            elif key_tint_score(color, chroma_key) < fringe_delta:
                classes[index] = _SUBJECT
            elif color_distance(color, chroma_key) <= fringe_threshold:
                classes[index] = _BLEND_IN_BAND
            else:
                classes[index] = _BLEND_OUT_OF_BAND

    key_tint = key_tint_score(chroma_key, chroma_key)
    max_reach = min(unseen - 1, unmix_reach if key_tint > 0 else 0)

    # Geometric distance to the nearest keyed-out pixel — outer background
    # *and* interior holes (hair gaps) alike. This walk is not blocked by
    # subject pixels, so an isolated key blend locked inside subject material
    # still gets a depth.
    frontier = keyed
    depth = 0
    while frontier and depth < max_reach:
        depth += 1
        next_frontier: list[int] = []
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

    # Soft-alpha unmix — binary erase cannot represent antialiased coverage.
    # Any key-tinted pixel within unmix_reach of the keyed region is separated
    # into despilled RGB + partial alpha instead:
    #   - out-of-band blends always (they are too subject-heavy to erase);
    #   - in-band blends only within the AA band nearest the key. Deeper
    #     key-tinted material stays byte-identical (v1.10.1 guardrail).
    if key_tint > 0 and unmix_reach > 0:
        for y in range(height):
            for x in range(width):
                index = y * width + x
                if not 0 < depths[index] <= unmix_reach:
                    continue
                pixel_class = classes[index]
                if pixel_class == _BLEND_IN_BAND:
                    if depths[index] > _IN_BAND_UNMIX_KEY_DEPTH:
                        continue
                elif pixel_class != _BLEND_OUT_OF_BAND:
                    continue
                red, green, blue, alpha = pixels[x, y]
                color = (red, green, blue)
                pixels[x, y] = unmix_key_blend(
                    color, alpha, chroma_key, key_tint, key_tint_score(color, chroma_key)
                )

    # Trapped-spill despill — generators paint key-colored spill *inside* the
    # subject (a green streak buried in crimson hair, key reflections between
    # strands) too far from any keyed pixel for depth-based treatment to
    # reach. Among the still-tinted pixels left after the passes above, a
    # small connected cluster is spill; a large one is intentional key-tinted
    # material (the hot-pink seed packet) and stays untouched. Spill keeps its
    # alpha — it sits inside opaque subject, so this is color correction, not
    # coverage: partial alpha here would punch pinholes through the sprite.
    if key_tint > 0 and keyed and spill_max_fraction > 0:
        subject_count = sum(1 for pixel_class in classes if pixel_class != _KEYED)
        spill_limit = max(32, round(subject_count * spill_max_fraction))
        tints_left: dict[int, float] = {}
        for y in range(height):
            for x in range(width):
                red, green, blue, alpha = pixels[x, y]
                if not alpha:
                    continue
                tint = key_tint_score((red, green, blue), chroma_key)
                if tint >= fringe_delta:
                    tints_left[y * width + x] = tint
        visited: set[int] = set()
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
                continue
            if max(tints_left[index] for index in cluster) <= _SPILL_MIN_TINT:
                continue
            for index in cluster:
                x = index % width
                y = index // width
                red, green, blue, alpha = pixels[x, y]
                color = (red, green, blue)
                coverage, despilled = despill_color(
                    color, chroma_key, key_tint, key_tint_score(color, chroma_key)
                )
                if coverage > 0:
                    pixels[x, y] = (*despilled, alpha)
    return rgba


def connected_components(image: Image.Image) -> list[dict[str, Any]]:
    alpha = image.getchannel("A")
    width, height = image.size
    data = alpha.tobytes()
    visited = bytearray(width * height)
    components: list[dict[str, Any]] = []

    for start, alpha_value in enumerate(data):
        if alpha_value <= 16 or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        pixels: list[int] = []
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
                if data[neighbor] > 16:
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


def component_group_image(source: Image.Image, components: list[dict[str, Any]], padding: int = 4) -> Image.Image:
    width, height = source.size
    min_x = max(0, min(component["bbox"][0] for component in components) - padding)
    min_y = max(0, min(component["bbox"][1] for component in components) - padding)
    max_x = min(width, max(component["bbox"][2] for component in components) + padding)
    max_y = min(height, max(component["bbox"][3] for component in components) + padding)
    output = Image.new("RGBA", (max_x - min_x, max_y - min_y), (0, 0, 0, 0))
    source_pixels = source.load()
    output_pixels = output.load()
    for component in components:
        for pixel_index in component["pixels"]:
            x = pixel_index % width
            y = pixel_index // width
            output_pixels[x - min_x, y - min_y] = source_pixels[x, y]
    return output


def cell_geometry(cell: dict[str, Any]) -> tuple[int, int, int, int]:
    width = int(cell.get("width", cell.get("size", 0)))
    height = int(cell.get("height", cell.get("size", 0)))
    safe_margin_x = int(cell.get("safe_margin_x", cell.get("safe_margin", 0)))
    safe_margin_y = int(cell.get("safe_margin_y", cell.get("safe_margin", 0)))
    if width <= 0 or height <= 0:
        raise SystemExit("cell width/height must be positive in sprite-request.json")
    return width, height, safe_margin_x, safe_margin_y


# align_x "alpha-centroid" 는 perfectpixel-studio internal/sprite/extract.go 이식
# (github.com/gykim80/perfectpixel-studio, MIT). bbox 중심 정렬은 팔/무기가 뻗은
# 프레임에서 면적이 큰 몸통을 반대로 밀어 재생 시 좌우 지터를 만들고, 알파 가중
# 무게중심 cx=Σ(x·α)/Σα 는 몸통이 지배해 축이 안정된다. α ≤ 10 은 소프트 매팅
# 프린지로 보고 무게에서 제외한다 (원본 alphaThreshold 와 동일).
ALPHA_CENTROID_MIN_ALPHA = 10


def _alpha_centroid_x(sprite: Image.Image, bottom_fraction: float = 1.0, min_alpha: int = 0) -> float:
    alpha = sprite.getchannel("A")
    width, height = sprite.size
    pixels = alpha.load()
    y_start = max(0, height - max(2, round(height * bottom_fraction)))
    total = 0
    weighted = 0.0
    for y in range(y_start, height):
        for x in range(width):
            value = pixels[x, y]
            if value > min_alpha:
                total += value
                weighted += value * (x + 0.5)
    if total == 0 and bottom_fraction < 1.0:
        return _alpha_centroid_x(sprite, 1.0, min_alpha)
    return (weighted / total) if total else width / 2.0


def _alpha_centroid_row_left(frame: Image.Image, cell_width: int, scale: int) -> int:
    # 픽셀퍼펙트 행 경로의 프레임별 가로 배치 (align_x: alpha-centroid 전용).
    # 행 union 공동 left 는 register_row_frames 의 정합 잔차가 그대로 지터로
    # 남는다 — perfectpixel 방식대로 프레임마다 무게중심을 셀 중앙에 앉힌다.
    # NEAREST xN 업스케일은 논리 픽셀 중심 (x+0.5) 을 scale·(x+0.5) 로 보내므로
    # 논리 해상도에서 잰 무게중심에 scale 을 곱하면 리샘플 없이 정확하다.
    left = round(cell_width / 2.0 - scale * _alpha_centroid_x(frame, 1.0, ALPHA_CENTROID_MIN_ALPHA))
    left = max(0, min(cell_width - frame.width * scale, left))
    return left - left % scale  # 논리 픽셀 격자 스냅 (flip 대칭 보존)


def _kcentroid_downscale(sprite: Image.Image, target_width: int, target_height: int, detail_bias: bool = False) -> Image.Image:
    # Astropulse kCentroid-style pixel-art downscale: each output pixel takes the
    # centroid of the dominant 2-means color cluster of its source block, so dark
    # outlines survive instead of being averaged away (LANCZOS) or arbitrarily
    # sampled (NEAREST when the target grid does not match the art's pixel grid).
    source = sprite.convert("RGBA")
    source_width, source_height = source.size
    src = source.load()
    output = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    out = output.load()
    for oy in range(target_height):
        y0 = oy * source_height // target_height
        y1 = max(y0 + 1, (oy + 1) * source_height // target_height)
        for ox in range(target_width):
            x0 = ox * source_width // target_width
            x1 = max(x0 + 1, (ox + 1) * source_width // target_width)
            block = [src[x, y] for y in range(y0, y1) for x in range(x0, x1)]
            opaque = [p for p in block if p[3] >= 128]
            if len(opaque) * 2 < len(block):
                continue
            if len(opaque) == 1:
                out[ox, oy] = opaque[0]
                continue
            color = _dominant_block_color(opaque, detail_bias)
            alpha_value = sum(p[3] for p in opaque) // len(opaque)
            out[ox, oy] = (color[0], color[1], color[2], alpha_value)
    return output


def fit_to_cell(
    image: Image.Image,
    cell_width: int,
    cell_height: int,
    safe_margin_x: int,
    safe_margin_y: int,
    fit: dict[str, Any] | None = None,
) -> Image.Image:
    # `fit` comes from sprite-request.json ("fit" object):
    #   resample: "lanczos" (default) | "nearest" | "kcentroid" — kcentroid is the
    #             pixel-art downscale that keeps 1px dark outlines readable
    #   align_x:  "foot-centroid" (default) | "centroid" | "alpha-centroid" |
    #             "bbox-center" —
    #             foot-centroid anchors on the bottom 20% alpha (the legs), so
    #             trailing hair/capes do not pull the body off the cell axis
    #             (critical for runtime horizontal flip); alpha-centroid is the
    #             perfectpixel-studio port — full alpha-weighted centroid that
    #             ignores soft-matte fringe (α ≤ 10), and in the pixel-perfect
    #             row path it is applied per frame instead of the row union
    #   align_y:  "bottom" (default) | "center" — bottom pins feet to a shared baseline
    # 2026-07-04 (알렉스): 기본값을 foot-centroid/bottom 으로 승격 — 프레임 간
    # "무게감"(발밑 기준선)이 기본으로 잡혀야 한다. pixel_perfect 경로와 동일 기본.
    fit = fit or {}
    resample_name = str(fit.get("resample", "lanczos")).lower()
    align_x = str(fit.get("align_x", "foot-centroid")).lower()
    align_y = str(fit.get("align_y", "bottom")).lower()
    bbox = image.getbbox()
    target = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
    if bbox is None:
        return target
    sprite = image.crop(bbox)
    max_width = max(1, cell_width - safe_margin_x * 2)
    max_height = max(1, cell_height - safe_margin_y * 2)
    scale = min(max_width / sprite.width, max_height / sprite.height, 1.0)
    if scale != 1.0:
        new_size = (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale)))
        if resample_name == "kcentroid":
            sprite = _kcentroid_downscale(sprite, new_size[0], new_size[1])
        else:
            sprite = sprite.resize(
                new_size,
                Image.Resampling.NEAREST if resample_name == "nearest" else Image.Resampling.LANCZOS,
            )
        cropped = sprite.getbbox()
        if cropped is not None:
            sprite = sprite.crop(cropped)
    if align_x == "foot-centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite, 0.2))
        left = max(0, min(cell_width - sprite.width, left))
    elif align_x == "centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite))
        left = max(0, min(cell_width - sprite.width, left))
    elif align_x == "alpha-centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite, 1.0, ALPHA_CENTROID_MIN_ALPHA))
        left = max(0, min(cell_width - sprite.width, left))
    else:
        left = (cell_width - sprite.width) // 2
    if align_y == "bottom":
        top = max(0, cell_height - safe_margin_y - sprite.height)
    else:
        top = (cell_height - sprite.height) // 2
    target.alpha_composite(sprite, (left, top))
    return target


# --- pixel-perfect pipeline (fit.pixel_perfect) -----------------------------
# unfake.js/pixeldetector 계열 접근: ① runs 기반으로 생성물의 논리 픽셀 pitch 검출
# ② 에지 히스토그램으로 격자 위상(offset) 정렬 ③ 격자 단위 dominant-color 스냅
# 다운스케일(진짜 해상도 복원) ④ 런 전체 공유 팔레트 양자화 + 알파 이진화
# ⑤ 정수배 NEAREST 업스케일로 셀 배치. 비정수 리샘플이 전혀 없어 픽셀이 깨지지 않는다.


def _edge_histograms(image: Image.Image) -> tuple[list[int], list[int], int, int]:
    """Color-transition edge counts indexed by x (vertical edges) and y
    (horizontal edges). AA ramps register near the true block boundary, so the
    boundary position signal survives anti-aliasing."""
    pixels = image.convert("RGBA").load()
    width, height = image.size
    col_edges = [0] * width
    row_edges = [0] * height
    for y in range(0, height, 2):
        for x in range(1, width):
            a = pixels[x, y]
            b = pixels[x - 1, y]
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3]) > 96:
                col_edges[x] += 1
    for x in range(0, width, 2):
        for y in range(1, height):
            a = pixels[x, y]
            b = pixels[x, y - 1]
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3]) > 96:
                row_edges[y] += 1
    return col_edges, row_edges, width, height


def detect_pixel_pitch(strip: Image.Image, max_pitch: int = 48) -> int:
    """True pixel-block pitch via edge-to-gridline alignment scoring.

    이전 구현(같은 색 런 길이 최빈값)은 AA 가장자리·블록 내부 질감이 만드는
    2px 런에 지배당해 큰 블록(~16px)을 놓치고 항상 2 를 내놨다 — 그 결과
    그리드 비정렬 축소로 이웃 픽셀이 섞여 뭉개졌다 (2026-07-05 사고).
    새 방식: 후보 피치 p 와 위상마다 "색 경계가 그리드선 ±w 안에 모이는
    비율"에서 우연 기대치 (그 창이 덮는 잉여류 수)/p 를 뺀 점수의 argmax.
    작은 p 가 공짜로 이기는 문제를 우연 보정이 막는다. 확신 없으면 1(스냅 안 함)로
    관측 가능하게 떨어진다.

    창 폭 w 는 모든 p 에 동일하다. 예전에는 `w = 1 if p >= 8 else 0` 이라
    p>=8 에서만 창이 열렸고, 그 결과 참 피치(>=8)의 우연 기대치가 3/p 로
    부풀어 창 없는 약수(p<8)에게 졌다 — k=8,10,12,14 에서 정확히 k/2 를
    반환하던 원인 (합성 정답 테스트 test_pitch_ground_truth 로 고정).
    창이 p 를 넘어 잉여류를 중복 합산하지 않도록 잉여류는 집합으로 센다."""
    image = strip.convert("RGBA")
    col_edges, row_edges, width, height = _edge_histograms(image)

    # 단순 argmax — 진짜 피치의 약수(p=7 vs 14)는 우연 기대치 |잉여류|/p 가
    # 커서 자동으로 밀린다. 최고점이 문턱(0.2) 미만이면 그리드 확신 없음 →
    # 1(스냅 안 함)로 관측 가능하게 폴백.
    best_pitch, best_score = 1, 0.2
    for p in range(2, max_pitch + 1):
        score = _axis_int_score(col_edges, p) + _axis_int_score(row_edges, p)
        if score > best_score:
            best_pitch, best_score = p, score
    return best_pitch


def _axis_int_score(edges: list[int], p: int, w: int = 1) -> float:
    """정수 피치 p 의 축별 점수 = (그리드선 ±w 에 모인 엣지 비율) − 우연 기대치.

    창 폭 w 는 모든 p 에 동일하고, 창이 덮는 잉여류는 집합으로 세어 중복 합산하지 않는다.
    """
    total = sum(edges) or 1
    best = 0.0
    for phase in range(p):
        residues = {(phase + offset) % p for offset in range(-w, w + 1)}
        hit = sum(sum(edges[r::p]) for r in residues)
        score = hit / total - len(residues) / p
        if score > best:
            best = score
    return best


def _axis_int_seed(edges: list[int], max_pitch: int = 48) -> int:
    """한 축만 보고 고른 정수 피치 씨앗. 확신 없으면 1."""
    best_pitch, best_score = 1, 0.1
    for p in range(2, max_pitch + 1):
        score = _axis_int_score(edges, p)
        if score > best_score:
            best_pitch, best_score = p, score
    return best_pitch


def _axis_refine(edges: list[int], pitch: float, w: float = 1.0, bin_step: float = 0.25) -> tuple[float, float]:
    """소수 피치 p 에서 최적 위상과 그 점수. 잉여류를 히스토그램으로 접어 O(nnz + p/step).

    정수 격자만 볼 수 있던 예전에는 참 피치 17.24 를 17 로 반올림했고, 그 0.24 가
    스프라이트 폭을 가로지르며 누적돼(23칸이면 5.5px) 셀 경계가 블록 한가운데를
    지났다. 작은 디테일은 두 셀에 반씩 걸려 평균에 먹혔다.
    """
    total = sum(edges) or 1
    bins = max(4, int(round(pitch / bin_step)))
    hist = [0] * bins
    for x, count in enumerate(edges):
        if count:
            hist[int((x % pitch) / pitch * bins) % bins] += count
    span = min(bins, max(1, int(round((2 * w) / pitch * bins)) + 1))
    chance = min(1.0, span / bins)
    doubled = hist + hist
    window = sum(doubled[:span])
    best_score, best_bin = window / total - chance, 0
    for start in range(1, bins):
        window += doubled[start + span - 1] - doubled[start - 1]
        score = window / total - chance
        if score > best_score:
            best_score, best_bin = score, start
    # 창의 기하학적 중심이 아니라 창 안 엣지의 가중 무게중심을 쓴다. 중심을 쓰면
    # 엣지가 한 bin 에 몰린 완전 정렬 격자에서도 위상이 반창(=w) 만큼 밀렸다.
    weight = sum(doubled[best_bin : best_bin + span])
    if weight:
        centre = sum((best_bin + k) * doubled[best_bin + k] for k in range(span)) / weight
    else:
        centre = best_bin + (span - 1) / 2.0
    return best_score, (centre % bins) / bins * pitch


def detect_pixel_grid(
    strip: Image.Image, max_pitch: int = 48
) -> tuple[tuple[float, float], tuple[float, float]]:
    """참 픽셀 격자 = ((가로 피치, 세로 피치), (가로 위상, 세로 위상)). 전부 소수.

    AI 가 그린 도트는 블록 폭이 정수로 떨어지지 않는다 (솔벨 주인공 base = 17.24px).
    측정은 소수로 하고, 스냅 결과(논리 픽셀 수)는 정수로 떨어진다.

    **피치는 축마다 다를 수 있다.** 생성물이 비균등 비율로 리스케일되면 가로 블록과 세로 블록의
    크기가 어긋난다 (솔벨 주인공 chibi 베이스: 가로 30.38 / 세로 30.92). 한 피치를 두 축에
    강제하면 한 축이 통째로 미끄러진다 — 실측 가로 정렬률 11.7% (축별로 재면 75.7%).

    격자 확신이 없으면 ((1.0, 1.0), (0,0)) — 스냅하지 않는다.
    """
    image = strip.convert("RGBA")
    combined = detect_pixel_pitch(image, max_pitch)
    if combined <= 1:
        return (1.0, 1.0), (0.0, 0.0)
    col_edges, row_edges, _, _ = _edge_histograms(image)

    half_span, step = 0.75, 0.02
    span = int(round(half_span / step))

    def refine(edges: list[int]) -> tuple[float, float]:
        # 씨앗 후보 = 축별 씨앗 + 두 축 합산 씨앗.
        # - 축별만 쓰면: 한 축의 정수 검출이 노이즈에 흔들려 약수(참 17.24 -> 씨앗 9)로 빠진다.
        # - 합산만 쓰면: 가로 24 / 세로 30 처럼 축마다 블록이 다른 그림에서 한 축의 참값이
        #   ±0.75 정밀화 창 밖에 놓인다.
        # 둘 다 후보로 두고 점수로 고르면 두 실패가 모두 막힌다.
        axis_seed = _axis_int_seed(edges, max_pitch)
        candidates = {float(s) for s in (axis_seed, combined) if s >= 2}
        if not candidates:
            return 1.0, 0.0
        # 정수 씨앗은 참 피치의 정수배를 집을 수 있다 (참 16.5 -> 씨앗 33: 33 간격선은
        # 엣지의 절반만 물지만, 정수만 보면 16 도 17 도 어긋나서 33 이 이긴다).
        # 그래서 씨앗의 약수들도 후보로 함께 정밀화하고 점수로 고른다.
        seeds = sorted(candidates | {s / d for s in candidates for d in (2, 3) if s / d >= 2.0})
        best = (-1.0, float(max(candidates)), 0.0)
        for centre in seeds:
            # centre 자체가 반드시 샘플에 들어가도록 대칭으로 훑는다 (예전엔 15.99/16.01 만 봐서
            # 정확히 정수인 격자에서도 소수로 빗나갔다).
            for i in range(-span, span + 1):
                pitch = centre + i * step
                if pitch < 2.0 or pitch > max_pitch:
                    continue
                score, phase = _axis_refine(edges, pitch)
                if score > best[0] + 1e-9:
                    best = (score, pitch, phase)
        return best[1], best[2]

    pitch_x, phase_x = refine(col_edges)
    pitch_y, phase_y = refine(row_edges)

    # 축별 피치는 서로 크게 다를 수 없다. 비균등 리스케일이어도 실측 차이는 2% 수준이다
    # (솔벨 chibi 베이스: 30.38 / 30.92). 한 축이 다른 축의 1.5배를 넘게 벗어나면 그 축의
    # 검출이 무너진 것이다 — 엣지가 적은 축(균일한 세로 막대가 화면을 채우는 carry 포즈 등)에서
    # 참 피치의 약수가 이겨 3.00 같은 값이 나왔다 (솔벨 down_carry_walk, 참값 9).
    # 엣지 총량이 많은 축을 신뢰해 양쪽에 쓴다. 조용히 고치지 않고 축 하나를 버렸음을 남긴다.
    lo, hi = sorted((pitch_x, pitch_y))
    if lo >= 2.0 and hi / lo > 1.5:
        if sum(col_edges) >= sum(row_edges):
            pitch_y = pitch_x
        else:
            pitch_x = pitch_y

    return (pitch_x, pitch_y), (phase_x, phase_y)


def _grid_edges(length: int, pitch: float, offset: float) -> list[int]:
    """소수 피치를 정수 픽셀 경계로 확정한다.

    두 경우를 가른다. 판정 기준은 body 가 피치의 정수배에 얼마나 가까운가다.

    1) 정수배에 가까우면(잔차 <= 블록의 1/4) body 를 셀 개수로 **등분**한다. 피치 측정의 미세오차
       (16.00 을 15.96 으로 재는 것 같은)를 흡수해 격자가 딱 떨어진다.
    2) 정수배가 아니면 `lead + i*pitch` 를 직접 곱해 놓고, 남는 자투리는 마지막 셀이 흡수한다.
       스프라이트 bbox 는 AA 프린지 때문에 블록의 정수배가 아닐 수 있다 (실사고: 849px =
       27.46 블록). 이때 등분하면 셀 폭이 31.44px 로 늘어나 참 블록 30.92px 와 칸마다 0.52px
       어긋나고, 오른쪽 끝에서 반 블록이 밀려 스냅 결과의 얼굴이 부서졌다 (v1.56.2 회귀).

    어느 쪽이든 피치를 누적 덧셈하지 않으므로 부동소수 오차가 쌓이지 않는다.
    """
    if pitch <= 1.0:
        return [0, length]
    # 선행 부분셀은 스프라이트가 블록 중간에서 시작할 때만 의미가 있다. 컴포넌트는 bbox 로
    # 잘려 블록 경계에서 시작하므로, 서브픽셀 오프셋(위상 추정 노이즈)은 0 으로 스냅한다.
    raw_lead = offset % pitch
    lead = 0 if (raw_lead < pitch * 0.25 or raw_lead > pitch * 0.75) else int(round(raw_lead))
    body = length - lead
    if body <= 0:
        return [0, length]
    ratio = body / pitch
    cells = max(1, int(round(ratio)))
    integral = abs(ratio - cells) <= 0.25
    edges = [0] if lead == 0 else [0, lead]
    for i in range(1, cells):
        e = lead + int(round(body * i / cells if integral else i * pitch))
        if edges[-1] < e < length:
            edges.append(e)
    if edges[-1] != length:
        edges.append(length)
    return edges


def _grid_phase(image: Image.Image, pitch: int) -> tuple[int, int]:
    pixels = image.convert("RGBA").load()
    width, height = image.size
    col_hits = [0] * pitch
    row_hits = [0] * pitch
    for y in range(0, height, 2):
        for x in range(1, width):
            a = pixels[x, y]
            b = pixels[x - 1, y]
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3]) > 96:
                col_hits[x % pitch] += 1
    for x in range(0, width, 2):
        for y in range(1, height):
            a = pixels[x, y]
            b = pixels[x, y - 1]
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3]) > 96:
                row_hits[y % pitch] += 1
    offset_x = max(range(pitch), key=lambda i: col_hits[i])
    offset_y = max(range(pitch), key=lambda i: row_hits[i])
    return offset_x, offset_y


def _dominant_block_color(opaque: list, detail_bias: bool = False) -> tuple[int, int, int]:
    if len(opaque) == 1:
        return opaque[0][:3]

    def luma(p):
        return p[0] * 299 + p[1] * 587 + p[2] * 114

    lo = min(opaque, key=luma)
    hi = max(opaque, key=luma)
    centroids = [lo[:3], hi[:3]]
    assign = [0] * len(opaque)
    for _ in range(3):
        for i, p in enumerate(opaque):
            d0 = sum((p[c] - centroids[0][c]) ** 2 for c in range(3))
            d1 = sum((p[c] - centroids[1][c]) ** 2 for c in range(3))
            assign[i] = 0 if d0 <= d1 else 1
        for cluster in (0, 1):
            members = [p for i, p in enumerate(opaque) if assign[i] == cluster]
            if members:
                centroids[cluster] = tuple(sum(p[c] for p in members) // len(members) for c in range(3))
    dominant = 0 if assign.count(0) >= assign.count(1) else 1
    if detail_bias:
        # 눈/아웃라인 같은 어두운 소수 디테일 보존: 두 클러스터의 명도차가 크고
        # 어두운 쪽 점유율이 1/3 이상이면 다수결 대신 어두운 클러스터를 택한다.
        darker = 0 if luma(centroids[0]) <= luma(centroids[1]) else 1
        share = assign.count(darker) / len(assign)
        if (
            darker != dominant
            and share >= 0.40
            and luma(centroids[darker]) < 70000
            and luma(centroids[1 - darker]) - luma(centroids[darker]) > 50000
        ):
            dominant = darker
    members = [p for i, p in enumerate(opaque) if assign[i] == dominant]
    return tuple(sum(p[c] for p in members) // len(members) for c in range(3))


def _pitch_pair(pitch: float | tuple[float, float]) -> tuple[float, float]:
    """스칼라 피치는 두 축에 같은 값, 쌍이면 (가로, 세로)."""
    if isinstance(pitch, (tuple, list)):
        return float(pitch[0]), float(pitch[1])
    return float(pitch), float(pitch)


def grid_snap_downscale(
    image: Image.Image,
    pitch: float | tuple[float, float],
    detail_bias: bool = False,
    phase: tuple[float, float] | None = None,
) -> Image.Image:
    """pitch/phase 는 소수를 받는다 — 격자선만 정수 픽셀로 확정한다 (`_grid_edges`).

    pitch 는 스칼라 또는 (가로, 세로) 쌍. 축마다 블록 크기가 다른 생성물이 있어서
    `detect_pixel_grid` 가 축별 피치를 낸다. 정수 pitch + 정수 phase 를 주면 예전과 같은
    경계가 나온다 (골든 추출 회귀 없음).
    """
    source = image.convert("RGBA")
    width, height = source.size
    pitch_x, pitch_y = _pitch_pair(pitch)
    if phase is None:
        offset_x, offset_y = _grid_phase(source, max(2, int(round(pitch_x))))
    else:
        offset_x, offset_y = phase
    x_edges = _grid_edges(width, pitch_x, offset_x)
    y_edges = _grid_edges(height, pitch_y, offset_y)
    pixels = source.load()
    output = Image.new("RGBA", (len(x_edges) - 1, len(y_edges) - 1), (0, 0, 0, 0))
    out = output.load()
    for oy in range(len(y_edges) - 1):
        for ox in range(len(x_edges) - 1):
            block = [
                pixels[x, y]
                for y in range(y_edges[oy], y_edges[oy + 1])
                for x in range(x_edges[ox], x_edges[ox + 1])
            ]
            opaque = [p for p in block if p[3] >= 128]
            if len(opaque) * 2 < len(block):
                continue
            color = _dominant_block_color(opaque, detail_bias)
            out[ox, oy] = (color[0], color[1], color[2], 255)
    return output


def binarize_alpha(image: Image.Image) -> Image.Image:
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            p = pixels[x, y]
            if p[3] < 128:
                pixels[x, y] = (0, 0, 0, 0)
            elif p[3] != 255:
                pixels[x, y] = (p[0], p[1], p[2], 255)
    return image


def pixel_snap_logical(image: Image.Image, pitch: int, logical_width: int, logical_height: int, detail_bias: bool = True) -> Image.Image:
    sprite = image
    bbox = sprite.getbbox()
    if bbox is not None:
        sprite = sprite.crop(bbox)
    if pitch >= 2:
        sprite = grid_snap_downscale(sprite, pitch, detail_bias)
        bbox = sprite.getbbox()
        if bbox is not None:
            sprite = sprite.crop(bbox)
    if sprite.width > logical_width or sprite.height > logical_height:
        scale = min(logical_width / sprite.width, logical_height / sprite.height)
        sprite = _kcentroid_downscale(
            sprite,
            max(1, round(sprite.width * scale)),
            max(1, round(sprite.height * scale)),
            detail_bias,
        )
        bbox = sprite.getbbox()
        if bbox is not None:
            sprite = sprite.crop(bbox)
    return binarize_alpha(sprite)


def conform_row_logical(images: list, logical_width: int, logical_height: int, detail_bias: bool = True) -> list:
    # 행(row) 단위 크기 통일: 축소 배율을 행에서 가장 큰 프레임 기준 하나로 계산해
    # 전 프레임에 동일 적용한다(프레임 간 크기 호흡 제거). 입력은 이미 격자 스냅된
    # 논리 해상도 프레임들이다.
    snapped = []
    for image in images:
        bbox = image.getbbox()
        snapped.append(image.crop(bbox) if bbox else image)
    max_width = max(s.width for s in snapped)
    max_height = max(s.height for s in snapped)
    if max_width > logical_width or max_height > logical_height:
        scale = min(logical_width / max_width, logical_height / max_height)
        conformed = []
        for sprite in snapped:
            resized = _kcentroid_downscale(
                sprite,
                max(1, round(sprite.width * scale)),
                max(1, round(sprite.height * scale)),
                detail_bias,
            )
            bbox = resized.getbbox()
            conformed.append(resized.crop(bbox) if bbox else resized)
        snapped = conformed
    return [binarize_alpha(s) for s in snapped]


def register_row_frames(frames: list, slack_x: int = 8, slack_y: int = 3) -> list:
    # 프레임 간 정합: 로코모션에서 다리는 원래 움직이므로, 안정 부위(상체 65%)의
    # 알파 겹침을 최대화하는 정수 시프트를 프레임마다 찾아 공통 캔버스에 앉힌다.
    # 이후 배치는 행 공통(union) 기준 1회 계산 → 프레임 간 몸통 흔들림 제거.
    cropped = []
    for frame in frames:
        bbox = frame.getbbox()
        cropped.append(frame.crop(bbox) if bbox else frame)
    canvas_width = max(f.width for f in cropped) + slack_x * 2
    canvas_height = max(f.height for f in cropped) + slack_y * 2

    def base_pos(f):
        return ((canvas_width - f.width) // 2, canvas_height - slack_y - f.height)

    reference = cropped[0]
    ref_x, ref_y = base_pos(reference)
    upper_limit = ref_y + int(reference.height * 0.65)
    ref_pixels = reference.load()
    ref_mask = set()
    for y in range(reference.height):
        if ref_y + y >= upper_limit:
            break
        for x in range(reference.width):
            if ref_pixels[x, y][3] >= 128:
                ref_mask.add((ref_x + x, ref_y + y))

    registered = []
    for index, frame in enumerate(cropped):
        base_x, base_y = base_pos(frame)
        best_dx, best_dy = 0, 0
        if index > 0 and ref_mask:
            pixels = frame.load()
            points = [
                (x, y)
                for y in range(frame.height)
                for x in range(frame.width)
                if pixels[x, y][3] >= 128 and base_y + y < upper_limit
            ]
            best_score = -1
            for dy in range(-slack_y, slack_y + 1):
                for dx in range(-slack_x, slack_x + 1):
                    score = sum(1 for (x, y) in points if (base_x + x + dx, base_y + y + dy) in ref_mask)
                    if score > best_score:
                        best_score = score
                        best_dx, best_dy = dx, dy
        canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
        canvas.alpha_composite(frame, (min(max(0, base_x + best_dx), canvas_width - frame.width), min(max(0, base_y + best_dy), canvas_height - frame.height)))
        registered.append(canvas)
    # 공통 union bbox 로 크롭 — 슬랙 여백이 셀보다 커져 배치 시 하단(발)이
    # 잘리는 것을 방지. 동일 박스 크롭이라 프레임 간 정합은 유지된다.
    union = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    for canvas in registered:
        union.alpha_composite(canvas)
    bbox = union.getbbox()
    if bbox is not None:
        registered = [canvas.crop(bbox) for canvas in registered]
    return registered


def row_placement(frames: list, cell_width: int, cell_height: int, safe_margin_y: int, scale: int, fit: dict[str, Any]) -> tuple[int, int]:
    # 가로 배치 오프셋은 행 union 기준으로 1회 계산해 전 프레임에 동일 적용한다
    # (플립 대칭·수평 안정). 세로는 place_row_frame 이 프레임별로 접지한다.
    union = Image.new("RGBA", frames[0].size, (0, 0, 0, 0))
    for frame in frames:
        union.alpha_composite(frame)
    sprite = union.resize((union.width * scale, union.height * scale), Image.Resampling.NEAREST)
    align_x = str(fit.get("align_x", "foot-centroid")).lower()
    if align_x == "foot-centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite, 0.2))
    elif align_x == "centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite))
    elif align_x == "alpha-centroid":
        # union 기준 값 — 실제 배치는 _run 이 프레임별로 _alpha_centroid_row_left
        # 를 쓴다 (per-frame 이 이 모드의 핵심).
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite, 1.0, ALPHA_CENTROID_MIN_ALPHA))
    else:
        left = (cell_width - sprite.width) // 2
    left = max(0, min(cell_width - sprite.width, left))
    left -= left % scale
    bbox = sprite.getbbox()
    content_bottom = bbox[3] if bbox else sprite.height
    top = max(0, cell_height - safe_margin_y - content_bottom)
    return left, top


def place_row_frame(frame: Image.Image, cell_width: int, cell_height: int, scale: int, left: int, top: int, safe_margin_y: int | None = None, ground: bool = True) -> Image.Image:
    # 2026-07-04 (알렉스): 세로는 프레임마다 콘텐츠 바닥을 공유 기준선에 접지한다 —
    # 행 union 공동 top 만 쓰면 소스 스트립의 상하 요동이 이동으로 남아 프레임 간
    # "무게감"(발밑 높이)이 들쭉해진다. perfectpixel-studio 의 프레임별 알파 가중
    # 정렬과 같은 원리의 세로축 버전. 점프 같은 의도적 오프셋은 fit.ground_frames
    # = false 로 끌 수 있다 (row_placement 의 공동 top 사용).
    target = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
    if frame.getbbox() is None:
        return target
    sprite = frame.resize((frame.width * scale, frame.height * scale), Image.Resampling.NEAREST)
    frame_top = top
    if ground and safe_margin_y is not None:
        bbox = sprite.getbbox()
        content_bottom = bbox[3] if bbox else sprite.height
        frame_top = max(0, cell_height - safe_margin_y - content_bottom)
    target.alpha_composite(sprite, (left, frame_top))
    return target


def build_shared_palette(frames: list, size: int) -> list:
    colors: list = []
    for frame in frames:
        pixels = frame.load()
        for y in range(frame.height):
            for x in range(frame.width):
                p = pixels[x, y]
                if p[3] >= 128:
                    colors.append((p[0], p[1], p[2]))
    if not colors:
        return []

    def box_widest(box):
        best_range = -1
        best_channel = 0
        for channel in range(3):
            lo = min(c[channel] for c in box)
            hi = max(c[channel] for c in box)
            if hi - lo > best_range:
                best_range = hi - lo
                best_channel = channel
        return best_range, best_channel

    boxes = [colors]
    while len(boxes) < size:
        best = None
        for index, box in enumerate(boxes):
            if len(box) < 2:
                continue
            spread, channel = box_widest(box)
            if spread > 0 and (best is None or spread > best[0]):
                best = (spread, channel, index)
        if best is None:
            break
        _, channel, index = best
        box = boxes.pop(index)
        box.sort(key=lambda c: c[channel])
        mid = len(box) // 2
        boxes.append(box[:mid])
        boxes.append(box[mid:])
    return [
        tuple(sum(c[channel] for c in box) // len(box) for channel in range(3))
        for box in boxes
        if box
    ]


def apply_palette(image: Image.Image, palette: list) -> Image.Image:
    if not palette:
        return image
    pixels = image.load()
    cache: dict = {}
    for y in range(image.height):
        for x in range(image.width):
            p = pixels[x, y]
            if p[3] < 128:
                pixels[x, y] = (0, 0, 0, 0)
                continue
            key = (p[0], p[1], p[2])
            if key not in cache:
                cache[key] = min(
                    palette,
                    key=lambda c: (c[0] - key[0]) ** 2 + (c[1] - key[1]) ** 2 + (c[2] - key[2]) ** 2,
                )
            color = cache[key]
            pixels[x, y] = (color[0], color[1], color[2], 255)
    return image


def enforce_outline(image: Image.Image, strength: float = 0.62) -> Image.Image:
    # 균일 오토 아웃라인: 실루엣 경계(투명 인접) 픽셀을 자기 색 기준으로 어둡게.
    # 다운스케일에서 얇은 원본 외곽선이 패치워크로 살아남는 문제를 결정론으로 보정 —
    # 모든 프레임/행에서 1 논리픽셀 외곽선이 보장돼 프레임 간 플리커도 줄인다.
    pixels = image.load()
    width, height = image.size
    boundary = []
    for y in range(height):
        for x in range(width):
            if pixels[x, y][3] < 128:
                continue
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if nx < 0 or ny < 0 or nx >= width or ny >= height or pixels[nx, ny][3] < 128:
                    boundary.append((x, y))
                    break
    keep = 1.0 - strength
    for x, y in boundary:
        r, g, b, _a = pixels[x, y]
        pixels[x, y] = (int(r * keep), int(g * keep), int(b * keep), 255)
    return image


def fit_pixel_perfect(logical: Image.Image, cell_width: int, cell_height: int, safe_margin_x: int, safe_margin_y: int, scale: int, fit: dict[str, Any]) -> Image.Image:
    target = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
    bbox = logical.getbbox()
    if bbox is None:
        return target
    # 콘텐츠 bbox 로 먼저 크롭 — 로지컬 셀의 투명 패딩째 바닥에 붙이면 패딩만큼
    # 프레임마다 발이 떠서 바닥선("무게감")이 흔들린다 (알렉스 2026-07-04).
    logical = logical.crop(bbox)
    sprite = logical.resize((logical.width * scale, logical.height * scale), Image.Resampling.NEAREST)
    align_x = str(fit.get("align_x", "foot-centroid")).lower()
    align_y = str(fit.get("align_y", "bottom")).lower()
    if align_x == "foot-centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite, 0.2))
    elif align_x == "centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite))
    elif align_x == "alpha-centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite, 1.0, ALPHA_CENTROID_MIN_ALPHA))
    else:
        left = (cell_width - sprite.width) // 2
    left = max(0, min(cell_width - sprite.width, left))
    left -= left % scale  # 논리 픽셀 격자에 스냅(짝수 배치로 flip 대칭 보존)
    if align_y == "bottom":
        top = max(0, cell_height - safe_margin_y - sprite.height)
    else:
        top = (cell_height - sprite.height) // 2
    target.alpha_composite(sprite, (left, top))
    return target


def extract_component_images(strip: Image.Image, frame_count: int) -> list[Image.Image] | None:
    components = connected_components(strip)
    if not components:
        return None
    largest_area = max(component["area"] for component in components)
    seed_threshold = max(120, largest_area * 0.20)
    seeds = [component for component in components if component["area"] >= seed_threshold]
    if len(seeds) < frame_count:
        seeds = sorted(components, key=lambda component: component["area"], reverse=True)[:frame_count]
    if len(seeds) < frame_count:
        return None

    seeds = sorted(
        sorted(seeds, key=lambda component: component["area"], reverse=True)[:frame_count],
        key=lambda component: component["center_x"],
    )
    seed_ids = {id(seed) for seed in seeds}
    groups: list[list[dict[str, Any]]] = [[seed] for seed in seeds]
    noise_threshold = max(12, largest_area * 0.002)

    dropped = 0
    for component in components:
        if id(component) in seed_ids or component["area"] < noise_threshold:
            continue
        nearest_index = min(
            range(len(seeds)),
            key=lambda index: abs(seeds[index]["center_x"] - component["center_x"]),
        )
        # x 거리로만 붙이면 멀리 떨어진 파편(크로마 잔여물·분리된 이펙트)까지
        # 병합돼 bbox 가 늘어나고 프레임 바닥선/크롭이 흔들린다 (알렉스 2026-07-04
        # "이상한 거 딸려나오게 하지 마"). 시드 bbox 를 살짝 넓힌 근접 영역과
        # 겹치는 위성만 병합하고, 나머지는 관측 가능하게 버린다.
        sx0, sy0, sx1, sy1 = seeds[nearest_index]["bbox"]
        pad_x = max(6, round((sx1 - sx0) * 0.15))
        pad_y = max(6, round((sy1 - sy0) * 0.15))
        cx0, cy0, cx1, cy1 = component["bbox"]
        if cx0 < sx1 + pad_x and cx1 > sx0 - pad_x and cy0 < sy1 + pad_y and cy1 > sy0 - pad_y:
            groups[nearest_index].append(component)
        else:
            dropped += 1
    if dropped:
        print(f"[extract] dropped {dropped} stray satellite component(s) outside seed proximity", file=sys.stderr)

    return [component_group_image(strip, group) for group in groups]


def extract_component_frames(strip: Image.Image, frame_count: int, cell_width: int, cell_height: int, safe_margin_x: int, safe_margin_y: int, fit: dict[str, Any] | None = None) -> list[Image.Image] | None:
    images = extract_component_images(strip, frame_count)
    if images is None:
        return None
    return [fit_to_cell(image, cell_width, cell_height, safe_margin_x, safe_margin_y, fit) for image in images]


def extract_slot_frames(strip: Image.Image, frame_count: int, cell_width: int, cell_height: int, safe_margin_x: int, safe_margin_y: int, fit: dict[str, Any] | None = None) -> list[Image.Image]:
    slot_width = strip.width / frame_count
    frames = []
    for index in range(frame_count):
        left = round(index * slot_width)
        right = round((index + 1) * slot_width)
        frames.append(fit_to_cell(strip.crop((left, 0, right, strip.height)), cell_width, cell_height, safe_margin_x, safe_margin_y, fit))
    return frames


def chroma_adjacent_count(image: Image.Image, chroma_key: tuple[int, int, int], threshold: float) -> int:
    count = 0
    data = image.convert("RGBA").tobytes()
    for index in range(0, len(data), 4):
        red, green, blue, alpha = data[index : index + 4]
        if alpha > 16 and color_distance((red, green, blue), chroma_key) <= threshold:
            count += 1
    return count


def inspect_frames(frames: list[Image.Image], chroma_key: tuple[int, int, int], args: argparse.Namespace) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    warnings: list[str] = []
    records: list[dict[str, Any]] = []
    areas = [alpha_nonzero_count(frame) for frame in frames]
    frame_median = median(areas) if areas else 0
    for index, frame in enumerate(frames):
        nontransparent = areas[index]
        edge = edge_alpha_count(frame, args.edge_margin)
        adjacent = chroma_adjacent_count(frame, chroma_key, args.chroma_adjacent_threshold)
        bbox = frame.getbbox()
        records.append(
            {
                "index": index,
                "nontransparent_pixels": nontransparent,
                "bbox": list(bbox) if bbox else None,
                "edge_pixels": edge,
                "chroma_adjacent_pixels": adjacent,
            }
        )
        if nontransparent < args.min_used_pixels:
            errors.append(f"frame {index:02d} is empty or too sparse ({nontransparent} pixels)")
        if edge > args.edge_pixel_threshold:
            warnings.append(f"frame {index:02d} has {edge} non-transparent edge pixels")
        if adjacent > args.chroma_adjacent_pixel_threshold:
            errors.append(f"frame {index:02d} has {adjacent} chroma-adjacent pixels")
        if frame_median and nontransparent < frame_median * args.small_outlier_ratio:
            warnings.append(f"frame {index:02d} is much smaller than median ({nontransparent} vs {frame_median:.0f})")
        if frame_median and nontransparent > frame_median * args.large_outlier_ratio:
            warnings.append(f"frame {index:02d} is much larger than median ({nontransparent} vs {frame_median:.0f})")
    return errors, warnings, records


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--states", default="all")
    parser.add_argument("--key-threshold", type=float, default=96.0)
    parser.add_argument("--fringe-key-threshold", type=float, default=180.0)
    parser.add_argument("--fringe-delta", type=float, default=18.0)
    parser.add_argument(
        "--fringe-unmix-reach",
        type=int,
        default=None,
        help="peel depth for soft-alpha unmix of out-of-band key blends; "
        "default from request chroma.unmix_reach, else 4; 0 disables",
    )
    parser.add_argument(
        "--spill-max-fraction",
        type=float,
        default=None,
        help="max size of a trapped key-spill cluster to despill, as a fraction "
        "of subject pixels; default from request chroma.spill_max_fraction, "
        "else 0.005; 0 disables",
    )
    parser.add_argument(
        "--segmentation",
        choices=("components", "projection"),
        default=None,
        help="frame separation mode; overrides request fit.segmentation "
        "(projection = projection-profile + DP optimal cut for fused poses, "
        "default components)",
    )
    parser.add_argument("--allow-slot-fallback", action="store_true")
    parser.add_argument("--min-used-pixels", type=int, default=400)
    parser.add_argument("--edge-margin", type=int, default=2)
    parser.add_argument("--edge-pixel-threshold", type=int, default=24)
    parser.add_argument("--chroma-adjacent-threshold", type=float, default=150.0)
    parser.add_argument("--chroma-adjacent-pixel-threshold", type=int, default=120)
    parser.add_argument("--small-outlier-ratio", type=float, default=0.35)
    parser.add_argument("--large-outlier-ratio", type=float, default=2.75)
    return parser


def _namespace_from_kwargs(**kwargs: object) -> argparse.Namespace:
    parser = _build_parser()
    values: dict[str, object] = {}
    remaining = dict(kwargs)
    for action in parser._actions:
        if action.dest == "help":
            continue
        default = [] if action.nargs == "*" else action.default
        if default is argparse.SUPPRESS:
            default = None
        value = remaining.pop(action.dest, default)
        if getattr(action, "required", False) and value is None:
            raise TypeError(f"missing required argument: {action.dest}")
        values[action.dest] = value
    if remaining:
        names = ", ".join(sorted(remaining))
        raise TypeError(f"unexpected keyword argument(s): {names}")
    return argparse.Namespace(**values)
def _run(args: argparse.Namespace):
    if args.fringe_key_threshold < args.key_threshold:
        raise SystemExit("--fringe-key-threshold must be greater than or equal to --key-threshold")
    if args.fringe_unmix_reach is not None and args.fringe_unmix_reach < 0:
        raise SystemExit("--fringe-unmix-reach must be zero or positive")
    if args.spill_max_fraction is not None and args.spill_max_fraction < 0:
        raise SystemExit("--spill-max-fraction must be zero or positive")

    run_dir = args.run_dir.expanduser().resolve()
    acquire_run_dir_lock(run_dir, "extract_sprite_row_frames")
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    # 크로마 튜너블의 SSoT 는 request JSON `chroma` — CLI 는 명시 override 만.
    # 실제 적용값은 request 에 되써서 런이 스스로 재현 가능하게 남긴다.
    chroma_config = dict(request.get("chroma") or {})
    unmix_reach = (
        args.fringe_unmix_reach
        if args.fringe_unmix_reach is not None
        else int(chroma_config.get("unmix_reach", 4))
    )
    if unmix_reach < 0:
        raise SystemExit("chroma.unmix_reach must be zero or positive")
    spill_max_fraction = (
        args.spill_max_fraction
        if args.spill_max_fraction is not None
        else float(chroma_config.get("spill_max_fraction", 0.005))
    )
    if spill_max_fraction < 0:
        raise SystemExit("chroma.spill_max_fraction must be zero or positive")
    effective_chroma = {
        **chroma_config,
        "unmix_reach": unmix_reach,
        "spill_max_fraction": spill_max_fraction,
    }
    if effective_chroma != chroma_config:
        request["chroma"] = effective_chroma
        atomic_write_text(
            run_dir / "sprite-request.json",
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        )
    states = list(request["states"]) if args.states == "all" else [state.strip() for state in args.states.split(",") if state.strip()]
    cell_width, cell_height, safe_margin_x, safe_margin_y = cell_geometry(request["cell"])
    fit_config = request.get("fit") or {}
    chroma_key = tuple(int(value) for value in request["chroma_key"]["rgb"])
    frames_root = run_dir / "frames"
    rows = []
    all_errors: list[str] = []
    all_warnings: list[str] = []

    pixel_perfect = bool(fit_config.get("pixel_perfect"))
    usable_width = max(1, cell_width - safe_margin_x * 2)
    usable_height = max(1, cell_height - safe_margin_y * 2)
    # 기본 = 셀과 동일(1:1) — 생성 프롬프트가 "TRUE 셀xN pixel grid" 를 명시하는
    # 현행 레시피에서 원본 그리드를 그대로 따라간다. 청키 2x 룩을 원할 때만
    # 절반 값(예: 셀 64 + 로지컬 32)을 명시한다. (2026-07-05, 이전 기본은 usable//2)
    logical_height = int(fit_config.get("logical_height", cell_height))
    pp_scale = max(1, cell_height // max(1, logical_height))
    if logical_height * pp_scale > cell_height:
        pp_scale = max(1, usable_height // max(1, logical_height))
    logical_width = max(1, cell_width // pp_scale)
    pp_detail_bias = bool(fit_config.get("detail_bias", True))
    palette_size = int(fit_config.get("palette_size", 24))
    pending: list = []

    def finalize_state(state: str, frames: list, frame_count: int, method: str,
                       plain_frames: list | None = None) -> None:
        state_dir = frames_root / state
        state_dir.mkdir(parents=True, exist_ok=True)
        output_paths = []
        for index, frame in enumerate(frames):
            output = state_dir / f"frame-{index}.png"
            atomic_save_image(frame, output)
            output_paths.append(relative_posix(output, run_dir))
        # 픽셀퍼펙트 전 원본 변형(.plain.png) — 큐레이션뷰의 전/후 토글과
        # curation.json `pixel_perfect: false` 굽기가 이 쌍둥이를 읽는다.
        plain_paths = []
        if plain_frames is not None:
            for index, frame in enumerate(plain_frames):
                output = state_dir / f"frame-{index}.plain.png"
                atomic_save_image(frame, output)
                plain_paths.append(relative_posix(output, run_dir))

        errors, warnings, frame_records = inspect_frames(frames, chroma_key, args)
        all_errors.extend(f"{state}: {error}" for error in errors)
        all_warnings.extend(f"{state}: {warning}" for warning in warnings)
        row = {
            "state": state,
            "frames": frame_count,
            "method": method,
            "files": output_paths,
            "frame_records": frame_records,
            "ok": not errors,
        }
        if plain_paths:
            row["plain_files"] = plain_paths
        rows.append(row)

    for state in states:
        if state not in request["states"]:
            raise SystemExit(f"unknown state in request: {state}")
        raw_path = run_dir / "raw" / f"{state}.png"
        if not raw_path.is_file():
            all_errors.append(f"{state}: missing raw strip {raw_path}")
            continue
        frame_count = int(request["states"][state]["frames"])
        with Image.open(raw_path) as opened:
            strip = remove_chroma_background(
                opened,
                chroma_key,
                args.key_threshold,
                args.fringe_key_threshold,
                args.fringe_delta,
                unmix_reach=unmix_reach,
                spill_max_fraction=spill_max_fraction,
            )
        strip = separate_fused_poses(strip, frame_count, fit_config, args.segmentation, state)
        if pixel_perfect:
            # 프레임별 픽셀퍼펙트 (2026-07-05 재설계): 포즈 컴포넌트를 먼저
            # 분리한 뒤 각 프레임마다 피치·위상을 독립 검출해 스냅한다.
            # 스트립 전역 단일 격자는 프레임 간 위상 드리프트 때문에 일부
            # 프레임이 항상 미끄러졌다 (알렉스 관찰: "격자가 픽셀에 안 맞음").
            images = extract_component_images(strip, frame_count)
            method = "components"
            if images is None:
                if not args.allow_slot_fallback:
                    all_errors.append(f"{state}: could not extract {frame_count} sprite components")
                    continue
                slot_width = strip.width / frame_count
                images = [
                    strip.crop((round(i * slot_width), 0, round((i + 1) * slot_width), strip.height))
                    for i in range(frame_count)
                ]
                method = "slots-explicit"
            # 피치는 행 안에서 사실상 상수(모델의 블록 크기)고 드리프트하는 건
            # 위상이다. 프레임별 검출값의 중앙값을 합의 피치로 쓰고(배수/노이즈
            # 낚임 방지), 위상만 프레임별로 다시 잡는다.
            # 피치는 소수다 — AI 가 그린 블록은 정수 픽셀로 떨어지지 않는다(예: 17.24).
            # 정수로 반올림하면 그 오차가 폭 전체에 누적돼 셀 경계가 블록 한가운데를 지난다.
            # 측정은 소수로 하고, 격자선은 `_grid_edges` 가 길이를 등분해 정수로 확정한다.
            # 피치는 소수이고 축마다 다를 수 있다 — AI 가 그린 블록은 정수 픽셀로 떨어지지 않고,
            # 비균등 리스케일된 생성물은 가로/세로 블록 크기가 어긋난다.
            # 측정은 소수·축별로 하고, 격자선은 `_grid_edges` 가 정수로 확정한다.
            hint = int(fit_config.get("pitch_hint", 0))
            grids = [detect_pixel_grid(component) for component in images]

            def _consensus(axis: int) -> float:
                confident = sorted(g[0][axis] for g in grids if g[0][axis] >= 2.0)
                if confident:
                    # 붕괴한 프레임(참 피치의 약수로 떨어진 값)이 중앙값을 오염시킨다 —
                    # 솔벨 down_carry_run 은 6 프레임 중 절반이 3.00 으로 무너져 합의가 5.00 이 됐다.
                    # 행 안에서 참 피치는 거의 같으므로, 최대값의 60% 미만은 붕괴로 보고 버린다.
                    ceiling = confident[-1]
                    trusted = [p for p in confident if p >= ceiling * 0.6]
                    dropped = len(confident) - len(trusted)
                    if dropped:
                        all_warnings.append(
                            f"{state}: dropped {dropped} collapsed per-frame pitch(es) below {ceiling * 0.6:.2f}"
                        )
                    return trusted[len(trusted) // 2]
                if hint >= 2:
                    all_warnings.append(
                        f"{state}: pitch from fit.pitch_hint={hint} (all per-frame detections inconclusive)"
                    )
                    return float(hint)
                strip_pitch, _ = detect_pixel_grid(strip)
                if strip_pitch[axis] >= 2.0:
                    all_warnings.append(
                        f"{state}: pitch from whole-strip detection={strip_pitch[axis]:.2f}"
                    )
                return strip_pitch[axis]

            consensus_x, consensus_y = _consensus(0), _consensus(1)
            outliers = [
                f"{i}:{g[0][0]:.2f}"
                for i, g in enumerate(grids)
                if g[0][0] >= 2.0 and abs(g[0][0] - consensus_x) > max(2.0, consensus_x * 0.25)
            ]
            if outliers:
                all_warnings.append(
                    f"{state}: per-frame pitch outliers ({', '.join(outliers)}) snapped at consensus {consensus_x:.2f}"
                )
            snapped = []
            if min(consensus_x, consensus_y) >= 2.0:
                # 위상만 프레임별로 (행 안에서 드리프트하는 건 위상이다).
                for component, (_, frame_phase) in zip(images, grids):
                    snapped.append(
                        grid_snap_downscale(component, (consensus_x, consensus_y), pp_detail_bias, frame_phase)
                    )
            else:
                snapped = list(images)
            pitch = round(consensus_x, 2) if abs(consensus_x - consensus_y) < 0.05 else (
                round(consensus_x, 2),
                round(consensus_y, 2),
            )
            logical_frames = conform_row_logical(snapped, logical_width, logical_height, pp_detail_bias)
            registered = register_row_frames(logical_frames)
            # 전/후 비교용 plain 쌍둥이: 같은 원본 스트립을 픽셀퍼펙트 없이
            # 기존 fit 경로로 셀에 앉힌 결과. 추출 실패 시 관측 가능하게 스킵.
            plain_frames = extract_component_frames(
                strip, frame_count, cell_width, cell_height, safe_margin_x, safe_margin_y, fit_config)
            if plain_frames is None:
                all_warnings.append(f"{state}: plain (pre-pixel-perfect) variant unavailable — component extraction differs")
            pending.append({"state": state, "frame_count": frame_count, "method": method,
                            "pitch": pitch, "frames": registered, "plain_frames": plain_frames})
            continue
        frames = extract_component_frames(strip, frame_count, cell_width, cell_height, safe_margin_x, safe_margin_y, fit_config)
        method = "components"
        if frames is None:
            if not args.allow_slot_fallback:
                all_errors.append(f"{state}: could not extract {frame_count} sprite components")
                continue
            frames = extract_slot_frames(strip, frame_count, cell_width, cell_height, safe_margin_x, safe_margin_y, fit_config)
            method = "slots-explicit"
        finalize_state(state, frames, frame_count, method)

    if pixel_perfect and pending:
        # 팔레트는 런 전체(모든 state 의 논리 프레임)에서 한 번 뽑아 공유한다 —
        # 프레임/행 간 색 흔들림(플리커) 제거 + 아이덴티티 색 고정.
        palette = build_shared_palette([f for entry in pending for f in entry["frames"]], palette_size)
        outline_cfg = fit_config.get("outline", True)
        for entry in pending:
            quantized = [apply_palette(frame, palette) for frame in entry["frames"]]
            if outline_cfg:
                strength = 0.62 if outline_cfg is True else float(outline_cfg)
                quantized = [enforce_outline(frame, strength) for frame in quantized]
            left, top = row_placement(quantized, cell_width, cell_height, safe_margin_y, pp_scale, fit_config)
            ground_frames = bool(fit_config.get("ground_frames", True))
            # alpha-centroid 는 프레임별 가로 배치 — 행 union 공동 left 로는
            # register_row_frames 의 정합 잔차가 지터로 남는다.
            per_frame_centroid = str(fit_config.get("align_x", "foot-centroid")).lower() == "alpha-centroid"
            frames = [
                place_row_frame(
                    frame, cell_width, cell_height, pp_scale,
                    _alpha_centroid_row_left(frame, cell_width, pp_scale) if per_frame_centroid else left,
                    top, safe_margin_y, ground_frames)
                for frame in quantized
            ]
            finalize_state(entry["state"], frames, entry["frame_count"], entry["method"],
                           plain_frames=entry.get("plain_frames"))
        all_warnings.append(
            "pixel-perfect: pitch=%s scale=%dx logical<=%dx%d palette=%d"
            % (",".join(str(entry["pitch"]) for entry in pending), pp_scale, logical_width, logical_height, len(palette))
        )

    result = {
        "ok": not all_errors,
        "engine": "component-row",
        "run_dir": str(run_dir),
        "cell": request["cell"],
        "chroma_key": request["chroma_key"],
        "rows": rows,
        "errors": all_errors,
        "warnings": all_warnings,
    }
    atomic_write_text(frames_root / "frames-manifest.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1



def run(**kwargs: object):
    return _run(_namespace_from_kwargs(**kwargs))

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
