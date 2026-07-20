# SPDX-License-Identifier: Apache-2.0
"""Extract component-row sprite strips into clean RGBA frames."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import median
from typing import Any

from PIL import Image, ImageChops

from sprite_gen.layout import frames_dir_rel, raw_rel, take_raw_rel
from sprite_gen.runio import acquire_run_dir_lock, atomic_save_image, atomic_write_text, publish_guard, relative_posix, release_run_dir_lock
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


# --- YCbCr chrominance matting (opt-in) --------------------------------------
# Port of the chrominance-plane matting from perfectpixel-studio
# (https://github.com/gykim80/perfectpixel-studio, internal/sprite/chroma.go),
# Copyright Andrew Kim (gykim80), MIT License. See NOTICE.
#
# The default path above classifies pixels by RGB distance to the key, so
# background shading and JPEG 4:2:0 chroma subsampling can push background
# pixels past the erase radius and leave fringe. This path drops luma entirely
# and separates on the CbCr plane: shading moves Y, not CbCr, so the key stays
# one tight chroma cluster. Selected with request `chroma.mode: "ycbcr"` or
# `--chroma-mode ycbcr`; the default "rgb" path stays byte-identical.

_YCC_CHROMA_IN = 24.0  # CbCr distance at/below → fully transparent (key)
_YCC_CHROMA_OUT = 72.0  # CbCr distance at/above → fully opaque (subject)
_YCC_DESPILL_BAND = 100.0  # despill pixels whose CbCr distance is inside this
_YCC_DESPILL_SCALE = 0.92  # despill strength (key-direction chroma suppression)
_YCC_FLOOD_TOL = 88.0  # border flood fill background tolerance (CbCr, lenient)
_YCC_ALPHA_EMPTY = 10  # alpha at/below counts as an empty pixel
_YCC_KEY_RESIDUE_DIST = 55.0  # residue metric radius around the declared key
_YCC_KEY_BIAS_FRACTION = 0.12  # declared-key border share that overrides the mode
_YCC_REMATTE_OPAQUE_FRAC = 0.60  # opaque-ratio spike → key likely mis-detected
_YCC_REMATTE_RESIDUE_FRAC = 0.025  # declared-key residue spike → matting incomplete


def rgb_to_ycc(red: int, green: int, blue: int) -> tuple[float, float, float]:
    """BT.601 YCbCr, 8-bit range, chroma centered on 128."""
    luma = 0.299 * red + 0.587 * green + 0.114 * blue
    return luma, (blue - luma) * 0.564 + 128.0, (red - luma) * 0.713 + 128.0


def _u8(value: float) -> int:
    if value <= 0:
        return 0
    if value >= 255:
        return 255
    return int(value + 0.5)


def ycc_to_rgb(luma: float, cb: float, cr: float) -> tuple[int, int, int]:
    return (
        _u8(luma + 1.402 * (cr - 128.0)),
        _u8(luma - 0.344136 * (cb - 128.0) - 0.714136 * (cr - 128.0)),
        _u8(luma + 1.772 * (cb - 128.0)),
    )


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    """Hermite 0→1 transition (soft matte edge feathering)."""
    if edge1 <= edge0:
        return 0.0
    t = (x - edge0) / (edge1 - edge0)
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return t * t * (3.0 - 2.0 * t)


def detect_background_key_ycc(
    image: Image.Image, declared_key: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Estimate the background key from corner patches + thin borders.

    Mode of a quantized CbCr histogram — never a mean: a mean drifts on
    gradient/compression noise, the mode locks onto the dominant chroma
    cluster. Wide poses (walk cycles) touch the borders and can crowd the
    histogram with subject colors, so when a sufficient share of the samples
    sits in the declared key's chroma family, that cluster wins outright
    (the original biases toward its always-magenta key the same way).
    """
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    width, height = rgba.size
    if width == 0 or height == 0:
        return declared_key
    pixels = rgba.load()
    _, declared_cb, declared_cr = rgb_to_ycc(*declared_key)
    bins: dict[int, list[int]] = {}
    total = 0
    key_family = [0, 0, 0, 0]  # count, sum r, sum g, sum b

    def visit(x: int, y: int) -> None:
        nonlocal total
        red, green, blue = pixels[x, y][:3]
        total += 1
        _, cb, cr = rgb_to_ycc(red, green, blue)
        if math.hypot(cb - declared_cb, cr - declared_cr) < _YCC_KEY_RESIDUE_DIST:
            key_family[0] += 1
            key_family[1] += red
            key_family[2] += green
            key_family[3] += blue
        slot = (int(cb) >> 3 << 6) | (int(cr) >> 3)
        acc = bins.get(slot)
        if acc is None:
            acc = [0, 0, 0, 0]
            bins[slot] = acc
        acc[0] += 1
        acc[1] += red
        acc[2] += green
        acc[3] += blue

    corner_w, corner_h = width // 5, height // 5
    if corner_w < 2:
        corner_w = width
    if corner_h < 2:
        corner_h = height
    for x0, y0, x1, y1 in (
        (0, 0, corner_w, corner_h),
        (width - corner_w, 0, width, corner_h),
        (0, height - corner_h, corner_w, height),
        (width - corner_w, height - corner_h, width, height),
    ):
        for y in range(y0, y1):
            for x in range(x0, x1):
                visit(x, y)
    for x in range(width):
        visit(x, 0)
        visit(x, height - 1)
    for y in range(height):
        visit(0, y)
        visit(width - 1, y)

    if total > 0 and key_family[0] * 100 >= total * int(_YCC_KEY_BIAS_FRACTION * 100):
        count = key_family[0]
        return (key_family[1] // count, key_family[2] // count, key_family[3] // count)
    best = max(bins.values(), key=lambda acc: acc[0], default=None)
    if best is None or best[0] == 0:
        return declared_key
    return (best[1] // best[0], best[2] // best[0], best[3] // best[0])


def _flood_clear_background_ycc(out: Image.Image, source: Image.Image, key_cb: float, key_cr: float) -> None:
    """4-connected flood fill from the borders clearing key-chroma pixels.

    Connectivity is what preserves the subject: an interior pixel whose chroma
    happens to sit near the key (isolated highlight, gem) never connects to
    the border and survives; a gradient/noisy background the soft matte could
    not fully erase is border-connected and gets cleared.
    """
    width, height = source.size
    if width < 3 or height < 3:
        return
    src_pixels = source.load()
    visited = bytearray(width * height)
    stack: list[int] = []
    # floodable(rgb) 는 픽셀값의 순수 함수 (키·톨은 고정) — 색값으로 메모이즈하면
    # 지배적인 단색 배경의 float 판정(rgb→ycc, hypot)을 색당 한 번만 돈다. 판정
    # 결과가 동일하므로 방문 집합·클리어 결과가 **바이트 동일**하다.
    flood_cache: dict[tuple[int, int, int], bool] = {}

    def push(x: int, y: int) -> None:
        position = y * width + x
        if visited[position]:
            return
        rgb = src_pixels[x, y][:3]
        ok = flood_cache.get(rgb)
        if ok is None:
            _, cb, cr = rgb_to_ycc(rgb[0], rgb[1], rgb[2])
            ok = math.hypot(cb - key_cb, cr - key_cr) <= _YCC_FLOOD_TOL
            flood_cache[rgb] = ok
        if ok:
            visited[position] = 1
            stack.append(position)

    for x in range(width):
        push(x, 0)
        push(x, height - 1)
    for y in range(height):
        push(0, y)
        push(width - 1, y)
    # 클리어 대상 = 방문 집합과 정확히 동일 (모든 방문 픽셀이 정확히 한 번 pop 되어
    # 알파 0 이 된다). 그래서 루프에서 픽셀별로 지우지 않고 방문 집합만 확정한 뒤
    # 마지막에 PIL 마스크로 한 번에 지운다 — RGB 불변, ~90만 PixelAccess 왕복 제거.
    while stack:
        position = stack.pop()
        x, y = position % width, position // width
        if x > 0:
            push(x - 1, y)
        if x < width - 1:
            push(x + 1, y)
        if y > 0:
            push(x, y - 1)
        if y < height - 1:
            push(x, y + 1)
    # 방문 집합을 마스크로 알파만 0 으로 (RGB 불변). 원본의 픽셀별 클리어와 동일 결과.
    # visited 는 0/1 → 하드 마스크로 쓰려면 0/255 로 (paste 마스크는 알파 블렌드).
    clear_mask = Image.frombuffer("L", (width, height), bytes(visited), "raw", "L", 0, 1)
    clear_mask = clear_mask.point(lambda v: 255 if v else 0)
    alpha = out.getchannel("A")
    alpha.paste(0, mask=clear_mask)
    out.putalpha(alpha)


def _matte_ycc(source: Image.Image, key: tuple[int, int, int]) -> tuple[Image.Image, float]:
    """Soft-matte `source` against `key` on the CbCr plane.

    Returns the matted RGBA image and its opaque-pixel fraction (the
    self-diagnostic input: a mis-detected key erases the subject instead of
    the background and the fraction spikes).
    """
    width, height = source.size
    out = Image.new("RGBA", source.size, (0, 0, 0, 0))
    _, key_cb, key_cr = rgb_to_ycc(*key)
    key_vb, key_vr = key_cb - 128.0, key_cr - 128.0
    key_len = math.hypot(key_vb, key_vr)
    # 핫스팟 (상태당 추출의 ~1/3, 1280×720 전 픽셀). 매트 출력은 픽셀값 (r,g,b) 의
    # 순수 함수(키는 고정) — 크로마 배경 스프라이트는 고유색이 적어 색값으로
    # 메모이즈하면 같은 float 연산을 색당 한 번만 돈다. 산술은 **바이트 동일**.
    # 캐시값: (despill 후 r, g, b, coverage). coverage<=0 이면 원본과 같이 건너뛴다.
    cache: dict[tuple[int, int, int], tuple[int, int, int, float]] = {}
    out_data = [(0, 0, 0, 0)] * (width * height)
    src = list(source.getdata())
    for i, px in enumerate(src):
        alpha = px[3]
        if alpha == 0:
            continue
        rgb = (px[0], px[1], px[2])
        hit = cache.get(rgb)
        if hit is None:
            red, green, blue = rgb
            luma, cb, cr = rgb_to_ycc(red, green, blue)
            dist = math.hypot(cb - key_cb, cr - key_cr)
            coverage = smoothstep(_YCC_CHROMA_IN, _YCC_CHROMA_OUT, dist)
            if coverage <= 0:
                hit = (0, 0, 0, coverage)
            else:
                if key_len > 1 and dist < _YCC_DESPILL_BAND:
                    # Despill: subtract only the key-direction chroma component —
                    # colors orthogonal to the key keep their saturation.
                    pixel_vb, pixel_vr = cb - 128.0, cr - 128.0
                    proj = (pixel_vb * key_vb + pixel_vr * key_vr) / key_len
                    if proj > 0:
                        weight = smoothstep(0.0, 1.0, (_YCC_DESPILL_BAND - dist) / _YCC_DESPILL_BAND) * _YCC_DESPILL_SCALE
                        cb = 128.0 + (pixel_vb - key_vb / key_len * proj * weight)
                        cr = 128.0 + (pixel_vr - key_vr / key_len * proj * weight)
                        red, green, blue = ycc_to_rgb(luma, cb, cr)
                hit = (red, green, blue, coverage)
            cache[rgb] = hit
        cov = hit[3]
        if cov <= 0:
            continue
        out_data[i] = (hit[0], hit[1], hit[2], int(alpha * cov))
    out.putdata(out_data)
    _flood_clear_background_ycc(out, source, key_cb, key_cr)
    opaque = sum(out.getchannel("A").histogram()[_YCC_ALPHA_EMPTY + 1 :])
    frac = opaque / (width * height) if width and height else 0.0
    return out, frac


def key_residue_fraction_ycc(image: Image.Image, key: tuple[int, int, int]) -> float:
    """Fraction of pixels still opaque within the key's chroma family.

    Symptom metric for "the matte did not finish removing the background".
    """
    width, height = image.size
    if not width or not height:
        return 0.0
    _, key_cb, key_cr = rgb_to_ycc(*key)
    pixels = image.load()
    count = 0
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha <= _YCC_ALPHA_EMPTY:
                continue
            _, cb, cr = rgb_to_ycc(red, green, blue)
            if math.hypot(cb - key_cb, cr - key_cr) < _YCC_KEY_RESIDUE_DIST:
                count += 1
    return count / (width * height)


def _cleanup_alpha_ycc(image: Image.Image) -> None:
    """Remove fully isolated opaque dots and fill nearly-enclosed pinholes.

    Decisions are made on an alpha snapshot so the pass cannot cascade; soft
    matte edges are untouched (only 0-neighbor dots and ≥7-neighbor holes).
    """
    width, height = image.size
    if width < 3 or height < 3:
        return
    # PIL 네이티브 스텐실 — 8이웃 불투명 카운트를 시프트-합으로 벡터화한다 (핫스팟:
    # 상태당 추출의 ~17%, 1280×720 전체 순회). 정수 논리(임계 비교·이웃 카운트)라
    # 픽셀 루프와 **바이트 동일**하며, numpy 없이 pillow 만으로 된다.
    alpha = image.getchannel("A")
    # op: 불투명 마스크 (0/1) — before 스냅샷과 같은 기준 (> EMPTY).
    op = alpha.point(lambda a: 1 if a > _YCC_ALPHA_EMPTY else 0)
    # 1px zero-pad 후 8방향 크롭으로 이웃을 꺼내 합산 (경계 밖 = 0, 정수 덧셈은 순서 무관).
    padded = Image.new("L", (width + 2, height + 2), 0)
    padded.paste(op, (1, 1))
    neighbors = None
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                   (-1, -1), (1, -1), (-1, 1), (1, 1)):
        shifted = padded.crop((1 + dx, 1 + dy, 1 + dx + width, 1 + dy + height))
        neighbors = shifted if neighbors is None else ImageChops.add(neighbors, shifted)
    # combined = op*16 + neighbors (둘 다 작아 24 이하) → 단일 LUT 로 두 마스크 분리.
    combined = ImageChops.add(op.point(lambda v: v * 16), neighbors)
    clear_mask = combined.point(lambda v: 255 if (v >> 4) == 1 and (v & 15) == 0 else 0)
    fill_mask = combined.point(lambda v: 255 if (v >> 4) == 0 and (v & 15) >= 7 else 0)
    # 원본은 알파만 바꾼다 (RGB 불변) — 스냅샷에서 시작해 두 disjoint 마스크만 덮어쓴다.
    new_alpha = alpha.copy()
    new_alpha.paste(0, mask=clear_mask)
    new_alpha.paste(255, mask=fill_mask)
    image.putalpha(new_alpha)


def remove_chroma_background_ycbcr(
    image: Image.Image,
    chroma_key: tuple[int, int, int],
    warnings: list[str] | None = None,
) -> Image.Image:
    """Chrominance-plane matting with self-diagnostic pure-key rematte.

    Pipeline: detect the background key from the borders (CbCr histogram
    mode), soft-matte + despill + border flood fill, then check the two
    mis-detection symptoms — opaque-fraction spike (subject erased instead of
    background) and declared-key residue spike (background survived). Either
    symptom triggers a rematte with the declared pure key; the better result
    wins and the fallback is reported through `warnings` (observable, never
    silent).
    """

    def note(message: str) -> None:
        if warnings is not None:
            warnings.append(message)
        print(f"[chroma-ycbcr] {message}", file=sys.stderr)

    rgba = image.convert("RGBA")
    detected = detect_background_key_ycc(rgba, chroma_key)
    out, opaque_frac = _matte_ycc(rgba, detected)
    residue = key_residue_fraction_ycc(out, chroma_key)
    used_declared = detected == tuple(chroma_key)

    if not used_declared and (
        opaque_frac > _YCC_REMATTE_OPAQUE_FRAC or residue > _YCC_REMATTE_RESIDUE_FRAC
    ):
        retry, retry_frac = _matte_ycc(rgba, chroma_key)
        retry_residue = key_residue_fraction_ycc(retry, chroma_key)
        better_frac = retry_frac < opaque_frac - 0.03 and retry_frac > 0.02
        less_residue = retry_residue < residue
        if (better_frac or less_residue) and retry_frac > 0.02:
            note(
                f"detected key {detected} rejected (opaque {opaque_frac:.3f}, "
                f"residue {residue:.4f}) — rematted with declared key {tuple(chroma_key)}"
            )
            out, opaque_frac, residue = retry, retry_frac, retry_residue
            used_declared = True

    if not used_declared:
        # Detected key outside the declared key's chroma family (dark/neutral
        # border mis-read): also try the declared pure key, keep the cleaner.
        _, detected_cb, detected_cr = rgb_to_ycc(*detected)
        _, declared_cb, declared_cr = rgb_to_ycc(*chroma_key)
        if math.hypot(detected_cb - declared_cb, detected_cr - declared_cr) > _YCC_KEY_RESIDUE_DIST:
            retry, retry_frac = _matte_ycc(rgba, chroma_key)
            if retry_frac > 0.02 and key_residue_fraction_ycc(retry, chroma_key) < residue:
                note(
                    f"detected key {detected} outside declared key family — "
                    f"rematted with declared key {tuple(chroma_key)}"
                )
                out = retry

    _cleanup_alpha_ycc(out)
    return out


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


# --- run-length pitch estimator (perfectpixel-studio unfake 이식) ------------
# detect_pixel_grid(경계 히스토그램)의 세컨드 오피니언. 추정 전용 — 스냅 경로는
# 이 값을 절대 쓰지 않는다. 두 추정기가 벌어지면 경고로만 표면화한다.

RUNLEN_ALPHA_THRESHOLD = 10  # perfectpixel alphaThreshold — 소프트 매팅 프린지 제외
RUNLEN_RGB_TOLERANCE = 12  # perfectpixel nearRGB tol — AA 미세 노이즈는 같은 색
RUNLEN_MIN_RUNS = 32  # 축 전체 런이 이보다 적으면 추정 포기 (사진/노이즈)
RUNLEN_MODE_SHARE = 0.5  # 최빈값 ±1 창이 가중 질량의 절반 미만이면 확신 없음


def _runlen_axis_estimate(pixels, outer: int, inner: int, horizontal: bool, cap: int) -> float:
    """한 축의 동일색 런 길이 히스토그램 → 가중 최빈값의 소수 추정. 확신 없으면 1.0.

    짧은 런(AA 잔재·질감)이 개수로는 항상 많으므로 원본과 같이 run 길이로 가중한다
    (hist[s]·s). AI 도트의 블록 폭은 정수로 안 떨어지므로 최빈값 하나가 아니라
    ±1 창의 가중 무게중심을 쓴다 — 참 피치 30.56 이면 30/31 런이 44:56 으로 섞여
    나오고, 그 무게중심이 소수 피치를 복원한다.
    """
    hist = [0] * (cap + 2)
    for o in range(0, outer, 2):
        prev = pixels[0, o] if horizontal else pixels[o, 0]
        run = 1
        for i in range(1, inner):
            cur = pixels[i, o] if horizontal else pixels[o, i]
            if (cur[3] <= RUNLEN_ALPHA_THRESHOLD and prev[3] <= RUNLEN_ALPHA_THRESHOLD) or (
                cur[3] > RUNLEN_ALPHA_THRESHOLD
                and prev[3] > RUNLEN_ALPHA_THRESHOLD
                and abs(cur[0] - prev[0]) <= RUNLEN_RGB_TOLERANCE
                and abs(cur[1] - prev[1]) <= RUNLEN_RGB_TOLERANCE
                and abs(cur[2] - prev[2]) <= RUNLEN_RGB_TOLERANCE
            ):
                run += 1
            else:
                if 2 <= run <= cap:
                    hist[run] += 1
                run = 1
            prev = cur
        if 2 <= run <= cap:
            hist[run] += 1
    if sum(hist) < RUNLEN_MIN_RUNS:
        return 1.0
    weighted = [count * length for length, count in enumerate(hist)]
    mode = max(range(2, cap + 1), key=weighted.__getitem__)
    lo, hi = max(2, mode - 1), min(cap, mode + 1)
    window = sum(weighted[lo : hi + 1])
    if not weighted[mode]:
        return 1.0
    # 확신 게이트: 최빈값의 고조파 패밀리(k·mode ±1)가 가중 질량의 절반은 차지해야
    # 한다. 인접 논리 픽셀이 같은 색이면 런이 2·mode, 3·mode 로 늘어나므로 고조파는
    # 최빈값을 반박하는 게 아니라 지지하는 증거다 (실측: founder_v7 down_idle 은
    # 11 과 함께 22~23, 34~35 에 질량이 실린다). 패밀리 밖에 질량 절반이 흩어져
    # 있으면 블록 구조 확신이 없는 것 — 1.0 으로 관측 가능하게 포기한다.
    family = 0
    for k in range(1, cap // mode + 2):
        centre = k * mode
        family += sum(weighted[max(2, centre - k) : min(cap, centre + k) + 1])
    if family < sum(weighted) * RUNLEN_MODE_SHARE:
        return 1.0
    return sum(length * weighted[length] for length in range(lo, hi + 1)) / window


def estimate_pixel_grid_runlen(strip: Image.Image, max_pitch: int = 48) -> tuple[float, float]:
    """동일색 런 길이 최빈값으로 축별 피치를 추정한다 — 추정 전용 세컨드 오피니언.

    perfectpixel-studio internal/sprite/pixelize.go 의 unfake(DetectPixelScale)
    이식 (MIT — NOTICE 표기). 원본은 수평/수직 런을 한 히스토그램에 합치지만,
    우리 목적은 detect_pixel_grid 의 축 붕괴 방어라 축별로 분리해 센다 — 실사고:
    솔벨 주인공 컴포넌트에서 y 피치가 x 값(29.52)으로 붕괴, 실측 30.56. 붕괴
    메커니즘은 정수 씨앗 로터리 실패(참 소수 피치가 어떤 씨앗의 ±0.75 정밀화 창에도
    안 들어감)라 경계 히스토그램 안에서는 자기 진단이 안 된다. 런 길이는 완전히
    다른 신호(경계 위치가 아니라 경계 사이 거리)라 세컨드 오피니언이 된다.

    확신 없는 축은 1.0 — 런 수 부족(RUNLEN_MIN_RUNS), 최빈값 창 질량 미달
    (RUNLEN_MODE_SHARE), 32px 미만 이미지 전부 관측 가능하게 포기한다.
    """
    image = strip.convert("RGBA")
    width, height = image.size
    if width < 32 or height < 32:
        return (1.0, 1.0)
    cap = min(max_pitch, min(width, height) // 8)
    if cap < 2:
        return (1.0, 1.0)
    pixels = image.load()
    return (
        _runlen_axis_estimate(pixels, height, width, True, cap),
        _runlen_axis_estimate(pixels, width, height, False, cap),
    )


def crosscheck_pitch_runlen(
    grid_pitch: tuple[float, float],
    runlen_pitch: tuple[float, float],
    axis_tolerance: float = 0.12,
    ratio_tolerance: float = 0.02,
) -> list[str]:
    """detect_pixel_grid vs 런길이 추정의 교차검증. 이견은 경고 문자열 목록으로.

    추정 전용 — 어느 값도 바꾸지 않는다. 자동 교체 금지: 어느 쪽이 맞는지는
    사람이/상위 게이트가 판단한다 (No Silent Fallback).

    runlen 의 오차 모델이 규칙 모양을 정한다: AA 가 런 양끝을 갉아먹으므로 runlen 은
    참 피치를 **한쪽(하향)으로만** 빗나간다 — 픽셀 단위 절대 바이어스라 작은 피치일수록
    상대 오차가 커진다 (실측: 피치 30 에서 −0.8px≈3%, 피치 9 에서 −2px≈20%).

    - 약수 오검출 (runlen 이 grid 보다 훨씬 큼): grid 가 참 피치의 약수로 떨어진 것
      (참 29.5 를 14.73 으로). runlen 은 과대추정하지 않으므로 슬랙 2px 만 두면 된다.
    - 배수/고조파 오검출 (grid 가 runlen 보다 훨씬 큼): grid 가 참 피치의 배수·고조파에
      낚인 것. runlen 하향 바이어스(≤3px) 를 슬랙으로 흡수한 뒤에도 크게 남으면 경고.
    - 축비(y/x) 불일치: 축 붕괴 (y 가 x 값으로). 실사고의 축차는 3.5%(29.52 vs 30.56)라
      축별 오차로는 임계 밑에 숨는다. AA 하향 바이어스의 공통 성분은 비율에서
      상쇄되지만(실측: 붕괴 신호 2.8%, 건강한 검출의 비 오차 0.7%), 축별 편차는
      남는다 — 편차는 절대량(서브픽셀, 축당 ~±0.35px)이라 비율 노이즈가 ~0.7/피치 로
      작은 피치에서 커진다 (founder_v7 실측: 8~15px 대역에서 3~9% 드리프트가 흔함,
      진위 판별 불가). 그래서 0.7/피치 를 하한 슬랙으로 둔다: 히어로 스케일(30px)의
      실붕괴 신호(2.8%)는 임계(2.4%) 위, 소피치 대역의 판별 불가 드리프트는 밑.
    """
    notes: list[str] = []
    for name, grid, runlen in (
        ("x", grid_pitch[0], runlen_pitch[0]),
        ("y", grid_pitch[1], runlen_pitch[1]),
    ):
        if grid < 2.0 or runlen < 2.0:
            continue
        if runlen - grid > max(axis_tolerance * grid, 2.0):
            notes.append(
                f"pitch crosscheck: run-length mode estimates {name}={runlen:.2f} but grid "
                f"detection returned {name}={grid:.2f} — likely a divisor misdetection"
            )
        elif grid - runlen > axis_tolerance * grid + 3.0:
            notes.append(
                f"pitch crosscheck: run-length mode estimates {name}={runlen:.2f} but grid "
                f"detection returned {name}={grid:.2f} — likely a multiple/harmonic misdetection"
            )
    if min(*grid_pitch, *runlen_pitch) >= 2.0:
        grid_ratio = grid_pitch[1] / grid_pitch[0]
        runlen_ratio = runlen_pitch[1] / runlen_pitch[0]
        drift = abs(runlen_ratio / grid_ratio - 1.0)
        if drift > max(ratio_tolerance, 0.7 / min(grid_pitch)):
            notes.append(
                f"pitch crosscheck: axis ratio y/x disagrees — run-length {runlen_ratio:.3f} vs "
                f"grid {grid_ratio:.3f} ({drift:.1%}); one axis may have collapsed to the other"
            )
    return notes



def _grid_uniformity(component: Image.Image, pitch: tuple[float, float],
                     phase: tuple[float, float],
                     flat: list | None = None,
                     width: int | None = None, height: int | None = None) -> float:
    """격자 가설의 셀 내부 색 분산 (낮을수록 진짜 격자). 불투명 픽셀만, 셀 크기 가중.

    핫스팟 (실측: 상태당 추출의 ~절반이 여기 — `_best_phase` 가 8×8 위상을 훑는다).
    산술은 원본과 **바이트 동일**하게 유지한다 (같은 순서의 순차 float 덧셈·같은
    정수합·같은 나눗셈). 최적화는 순전히 호출 오버헤드 제거다: (1) 프레임 픽셀을
    행 우선 flat 리스트로 한 번만 실어 `_best_phase` 의 64회 호출이 재로딩하지 않게
    하고, (2) `sum()`/제너레이터 대신 명시 누적 루프로 같은 순서의 덧셈을 돌린다.
    """
    w = component.width if width is None else width
    h = component.height if height is None else height
    if flat is None:
        flat = list(component.getdata())
    xs = _grid_edges(w, pitch[0], phase[0])
    ys = _grid_edges(h, pitch[1], phase[1])
    return _grid_score_edges(flat, w, h, xs, ys)


def _grid_score_edges(flat: list, w: int, h: int,
                      xs: list[int], ys: list[int]) -> float:
    """`_grid_uniformity` 의 채점 코어 — 격자 경계가 이미 정해진 뒤의 순수 계산.

    `_best_phase` 가 이 코어를 (xs, ys) 로 메모이즈한다: 8단계 위상 스캔은 `_grid_edges`
    의 정수 스냅 때문에 서로 다른 위상이 **같은 정수 경계**로 자주 붕괴한다 (offset 이
    피치의 1/4 미만이면 lead=0 등). 같은 경계 → 같은 계산 → 같은 값이므로 캐시는
    바이트 동일하며, 64회 픽셀 순회가 고유 경계 수만큼으로 줄어든다.
    """
    total_dev = 0.0
    total_n = 0
    for yi in range(len(ys) - 1):
        y_lo, y_hi = ys[yi], min(ys[yi + 1], h)
        for xi in range(len(xs) - 1):
            x_lo, x_hi = xs[xi], min(xs[xi + 1], w)
            # 평균 패스 — 정수 채널합은 순서 무관·정확(CPython sum 정수 경로와 값 동일).
            acc = []
            append = acc.append
            s0 = s1 = s2 = 0
            for y in range(y_lo, y_hi):
                base = y * w
                for x in range(x_lo, x_hi):
                    q = flat[base + x]
                    if q[3] >= 128:
                        r, g, b = q[0], q[1], q[2]
                        append((r, g, b))
                        s0 += r
                        s1 += g
                        s2 += b
            n = len(acc)
            if n < 2:
                continue
            # `mean = [sum(c[k])/n for k]` 와 값 동일 (같은 정수합 / 같은 n).
            mean = [s0 / n, s1 / n, s2 / n]
            # 편차는 원본과 **바이트 동일**해야 한다: CPython 3.12+ 의 sum() 은 float 에
            # 대해 보정합(Neumaier)을 쓰므로 순진한 += 루프와 마지막 ULP 가 갈린다.
            # 따라서 float 축약은 원본 제너레이터 표현을 글자 그대로 유지한다 (속도
            # 이득은 flat 로드 hoist + 위상 메모이즈에서 이미 확보).
            dev = sum(sum(abs(c[k] - mean[k]) for k in range(3)) for c in acc) / n
            total_dev += dev * n
            total_n += n
    return (total_dev / total_n) if total_n else 1e9


def _best_phase(component: Image.Image, pitch: tuple[float, float]) -> tuple[float, float]:
    """주어진 피치에서 셀 균일도가 최선인 위상 (축별 8단계 탐색 — 결정론)."""
    best = (0.0, 0.0)
    best_score = None
    steps = 8
    # 프레임 픽셀을 한 번만 실어 모든 위상이 공유한다 (재로딩 제거).
    flat = list(component.getdata())
    w, h = component.width, component.height
    # (xs, ys) 정수 경계로 채점을 메모이즈 — 위상 스캔은 같은 경계로 자주 붕괴한다.
    cache: dict[tuple, float] = {}
    for i in range(steps):
        for j in range(steps):
            phase = (pitch[0] * i / steps, pitch[1] * j / steps)
            xs = tuple(_grid_edges(w, pitch[0], phase[0]))
            ys = tuple(_grid_edges(h, pitch[1], phase[1]))
            key = (xs, ys)
            score = cache.get(key)
            if score is None:
                score = _grid_score_edges(flat, w, h, xs, ys)
                cache[key] = score
            if best_score is None or score < best_score:
                best_score = score
                best = phase
    return best


def arbitrate_pitch(images: list, detect_pitch: tuple[float, float],
                    detect_phases: list, runlen_pitch: tuple[float, float],
                    tag: str, warnings_out: list) -> tuple[tuple[float, float], list, str]:
    """detect 합의와 runlen 세컨드 오피니언이 크게 다를 때 실측 채점으로 승자 채택.

    하모닉 오검출 (실사고 2026-07-18 side_idle: detect 가 참 피치 9.5 의 하모닉
    6.5 를 잡아 45×83 과분할 → 물리캡 강제 리사이즈 = 이중 리샘플 찌그러짐) 방어.
    자동 폴백이 아니라 두 가설을 셀 균일도로 결정론 채점해 채택하고, 결과를
    경고로 관측 가능하게 남긴다.

    반환: (채택 피치, 프레임별 위상, 채택 근거 라벨)."""
    dx, dy = detect_pitch
    rx, ry = runlen_pitch
    if rx < 2.0 or ry < 2.0:
        return detect_pitch, detect_phases, "detect"
    if abs(dx - rx) <= max(1.0, dx * 0.15) and abs(dy - ry) <= max(1.0, dy * 0.15):
        return detect_pitch, detect_phases, "detect"
    # 채택은 하지 않는다 (실사고 2026-07-18: 균일도 지표는 세밀 격자에 자명하게
    # 유리한 편향이 있어 up_idle 을 잘못 뒤집었다). 관측 로그만 남긴다 — 큰 불일치의
    # 실제 의미는 대개 '생성 해상도가 셀 계약보다 세밀' (해법 = 굵기 강제 리롤).
    sample = images[0]
    detect_score = _grid_uniformity(sample, detect_pitch, detect_phases[0])
    runlen_phase0 = _best_phase(sample, runlen_pitch)
    runlen_score = _grid_uniformity(sample, runlen_pitch, runlen_phase0)
    warnings_out.append(
        f"{tag}: pitch disagreement — detect {dx:.2f}x{dy:.2f} (uniformity {detect_score:.1f}) "
        f"vs runlen {rx:.2f}x{ry:.2f} ({runlen_score:.1f}); detect kept. "
        f"large gap usually means the raw was drawn finer than the cell contract — reroll with a "
        f"block-count hint")
    return detect_pitch, detect_phases, "detect"


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
    # 정수 산술만 (제곱거리·정수합//count) — 순서 무관이라 명시 누적으로 바꿔도
    # 원본과 값 동일하다. 제너레이터/`sum()` 호출 오버헤드만 제거.
    for _ in range(3):
        c00, c01, c02 = centroids[0]
        c10, c11, c12 = centroids[1]
        for i, p in enumerate(opaque):
            p0, p1, p2 = p[0], p[1], p[2]
            d0 = (p0 - c00) ** 2 + (p1 - c01) ** 2 + (p2 - c02) ** 2
            d1 = (p0 - c10) ** 2 + (p1 - c11) ** 2 + (p2 - c12) ** 2
            assign[i] = 0 if d0 <= d1 else 1
        for cluster in (0, 1):
            s0 = s1 = s2 = cnt = 0
            for i, p in enumerate(opaque):
                if assign[i] == cluster:
                    s0 += p[0]
                    s1 += p[1]
                    s2 += p[2]
                    cnt += 1
            if cnt:
                centroids[cluster] = (s0 // cnt, s1 // cnt, s2 // cnt)
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


def solid_alpha_bbox(image: Image.Image, threshold: int = 128) -> tuple[int, int, int, int] | None:
    """α>=threshold 픽셀만의 bbox — AA 프린지(반투명 가장자리)를 제외한 실 콘텐츠 범위.

    픽셀퍼펙트 경로의 불투명 판정(α>=128: `grid_snap_downscale`/`binarize_alpha`/
    `apply_palette`)과 같은 기준이어야 한다. any-alpha `getbbox()` 로 격자를 치면
    프린지가 bbox 를 부풀려 `_grid_edges` 의 셀 개수 반올림이 한 칸 늘고, 내용이
    프린지뿐인 가장자리 셀이 50% 규칙을 통과해 실루엣 밖 부스러기 픽셀로 굳는다
    (실사고 2026-07-17: founder_v7 150 프레임 중 141 프레임 폭 +1~4px, 수홍 발견 —
    회귀 테스트 test_pixel_snap.py::test_fringe_does_not_inflate_grid)."""
    return image.convert("RGBA").split()[3].point(lambda a: 255 if a >= threshold else 0).getbbox()


def tighten_components(images: list[Image.Image]) -> list[Image.Image]:
    """픽셀퍼펙트 스냅 전에 컴포넌트를 실 콘텐츠(solid alpha) bbox 로 타이트하게 조인다.

    `_grid_edges` 의 lead-스냅(위상 < 피치의 1/4 이면 0 으로)은 컴포넌트가 bbox 로
    잘려 블록 경계에서 시작한다는 전제다. `component_group_image` 는 사방 4px
    패딩을 두르므로, 패딩째 격자를 치면 위상 추정 노이즈가 lead-스냅 문턱 아래로
    떨어지는 순간 격자 전체가 패딩만큼 위로 밀린다 — 꼬리에 자투리 셀이 생기고,
    경계에서 쪼개진 바닥 블록 + 문턱 근처 알파(~134) 프린지가 유령 픽셀 한 줄로
    태어난다 (실사고 2026-07-14: down_idle blink 발밑 1px 돌출, 수홍 발견 —
    회귀 테스트 test_pixel_snap.py::test_padded_component_no_ghost_bottom_row).
    크롭 기준은 `solid_alpha_bbox` — any-alpha bbox 는 프린지째 격자를 쳐서 같은
    유령 픽셀을 사방 가장자리에 만든다 (해당 docstring 참조). 전부 프린지인
    퇴화 컴포넌트만 any-alpha bbox 로 폴백한다 (어차피 binarize 에서 빈 프레임)."""
    return [
        component.crop(box)
        if (box := (solid_alpha_bbox(component) or component.getbbox())) else component
        for component in images
    ]



def _boundary_mass(image: Image.Image) -> tuple[list[int], list[int]]:
    """축별 색/알파 경계 질량 프로파일 — 절단선 스냅의 증거."""
    px = image.load()
    w, h = image.size
    col = [0] * max(1, w)
    row = [0] * max(1, h)
    for y in range(h):
        for x in range(w - 1):
            a, b = px[x, y], px[x + 1, y]
            if (a[3] >= 128) != (b[3] >= 128) or (
                a[3] >= 128 and b[3] >= 128
                and abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2]) > 48
            ):
                col[x + 1] += 1
    for x in range(w):
        for y in range(h - 1):
            a, b = px[x, y], px[x, y + 1]
            if (a[3] >= 128) != (b[3] >= 128) or (
                a[3] >= 128 and b[3] >= 128
                and abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2]) > 48
            ):
                row[y + 1] += 1
    return col, row


def refine_edges_to_boundaries(component: Image.Image, x_edges: list[int], y_edges: list[int],
                               pitch: tuple[float, float]) -> tuple[list[int], list[int]]:
    """등간격 절단선을 실제 블록 경계로 스냅 (수홍 확정 2026-07-18).

    AI 블록은 국소적으로 흔들려서 등간격 자름은 경계 조각을 옆 칸으로 흘린다
    (실사고: 눈 아래 유령 픽셀, 발밑 1픽셀 — 같은 뿌리). 각 내부 절단선을
    ±pitch/3 창 안의 색경계 질량 최대 위치로 옮긴다. 질량이 무의미하면(평탄 영역)
    등간격 위치 유지. 단조 증가·최소 칸폭 2px 불변식 강제 — 결정론."""
    col, row = _boundary_mass(component)

    def snap(edges: list[int], mass: list[int], pitch_axis: float, limit: int) -> list[int]:
        if len(edges) < 3:
            return edges
        out = list(edges)
        window = max(1, int(pitch_axis / 3))
        for i in range(1, len(out) - 1):
            e = edges[i]
            lo = max(out[i - 1] + 2, e - window)
            hi = min(edges[i + 1] - 2, e + window, limit - 1)
            if hi < lo:
                continue
            best = max(range(lo, hi + 1), key=lambda pos: (mass[pos] if pos < len(mass) else 0))
            if (mass[best] if best < len(mass) else 0) > 0:
                out[i] = best
        # 단조/최소폭 재확인 (스냅 순서상 위반 불가지만 방어적 단정)
        for i in range(1, len(out)):
            if out[i] <= out[i - 1]:
                return list(edges)  # 위반 시 통째로 등간격 유지 (관측 불필요 — 발생 불가 경로)
        return out

    return (snap(x_edges, col, pitch[0], component.width),
            snap(y_edges, row, pitch[1], component.height))


def snap_by_edges(image: Image.Image, x_edges: list[int], y_edges: list[int],
                  detail_bias: bool = False) -> Image.Image:
    """명시 절단선으로 블록 샘플링 (grid_snap_downscale 의 코어 — 비등간격 지원)."""
    source = image.convert("RGBA")
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
    # 격자 크롭은 solid bbox 기준 — any-alpha 는 프린지가 셀 개수를 부풀린다
    # (`solid_alpha_bbox` docstring 의 부스러기 메커니즘과 동일).
    bbox = solid_alpha_bbox(sprite) or sprite.getbbox()
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



PALETTE_LOCK = "palette.lock.json"


def load_pinned_palette(run_dir: Path) -> list | None:
    """런에 고정된 공유 팔레트 (없으면 None). 팔레트 피닝 (수홍 확정 2026-07-18):
    공유 팔레트가 추출 배치 구성에 따라 재계산되며 확정 행 픽셀을 흔들던 드리프트
    (실사고 2026-07-17 down_idle, 3회)를 원천 차단한다 — 배치가 어떻게 바뀌어도
    팔레트는 이 파일 하나가 진실이다. 재산출은 `--repalette` 명시 시에만."""
    path = run_dir / PALETTE_LOCK
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        colors = [tuple(int(v) for v in c) for c in data["colors"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"corrupt {PALETTE_LOCK}: {exc} — fix or delete it explicitly")
    if not colors:
        raise SystemExit(f"{PALETTE_LOCK} has no colors — fix or delete it explicitly")
    return colors


def write_pinned_palette(run_dir: Path, palette: list, source: str) -> None:
    atomic_write_text(run_dir / PALETTE_LOCK, json.dumps({
        "kind": "sprite-gen-palette-lock",
        "source": source,
        "colors": [list(c) for c in palette],
    }, ensure_ascii=False, indent=2) + "\n")


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


def fit_component_to_bbox(component: Image.Image, cell_width: int, cell_height: int,
                          bbox: tuple[int, int, int, int], scale: int = 1,
                          ) -> tuple[Image.Image, dict[str, float] | None]:
    """원본 컴포넌트를 픽셀퍼펙트 프레임의 콘텐츠 bbox(×scale) 풋프린트에 앉힌다.

    plain/orig 쌍둥이용: 픽셀퍼펙트 결과와 같은 크기·같은 자리에 원본 화질 스프라이트를
    두어, 큐레이터의 픽셀퍼펙트 토글이 크기 변화 없이 픽셀 처리 품질만 비교하게 한다
    (contain 맞춤 + 하단 정렬 + 가로 중앙 — bbox 종횡비와의 오차는 격자 반올림 수준).

    두 번째 반환값은 컴포넌트 좌표 → 쌍둥이 좌표 매핑 {crop_x, crop_y, ratio, left, top}
    (twin = (raw - crop) * ratio + offset) — 검출 격자선을 쌍둥이 위에 겹칠 때 쓴다."""
    target = Image.new("RGBA", (cell_width * scale, cell_height * scale), (0, 0, 0, 0))
    src_bbox = component.getbbox()
    if src_bbox is None:
        return target, None
    src = component.crop(src_bbox)
    x0, y0, x1, y1 = (v * scale for v in bbox)
    box_w, box_h = max(1, x1 - x0), max(1, y1 - y0)
    ratio = min(box_w / src.width, box_h / src.height)
    tw, th = max(1, round(src.width * ratio)), max(1, round(src.height * ratio))
    resized = src.resize((tw, th), Image.Resampling.LANCZOS)
    left = x0 + (box_w - tw) // 2
    top = y1 - th
    target.alpha_composite(resized, (left, top))
    mapping = {"crop_x": float(src_bbox[0]), "crop_y": float(src_bbox[1]),
               "ratio": ratio, "left": float(left), "top": float(top)}
    return target, mapping


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
        "--chroma-mode",
        choices=("rgb", "ycbcr"),
        default=None,
        help="background matting path; overrides request chroma.mode "
        "(ycbcr = chrominance-plane matting with border-mode key detection, "
        "despill and flood fill — perfectpixel-studio port; default rgb)",
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
    parser.add_argument("--repalette", action="store_true",
                        help="고정 팔레트(palette.lock.json)를 무시하고 재산출·재고정")
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
def _load_validated(path: Path, name: str, require) -> dict:
    """Read a canonical run JSON record, FAILING LOUD on any corruption — unreadable,
    unparseable, or a broken schema — instead of silently treating it as absent/empty. EVERY
    reader (extract's subset seeding + commit, inspect / the correction loop) goes through here:
    silently reading a `{}`, a missing-field, or a wrong-`ok` record as empty would drop a
    still-unresolved failure (extract-failure.json) or a real generation (frames-manifest.json)
    from view, so a corrupt record would read as "all clear" (No Silent Fallback / Consistency).
    Returns {} ONLY when the file is genuinely absent — an existing-but-broken record never reads
    as empty. `require` enforces the kind-specific contract on top of the shared shape."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"corrupt {name} {path}: {exc}\n"
            f"  refusing to treat a canonical run record as empty; inspect/repair or delete it deliberately."
        )
    if not isinstance(data, dict):
        raise SystemExit(f"corrupt {name} {path}: expected a JSON object, got {type(data).__name__}")
    for key in ("errors", "warnings", "rows"):
        if not isinstance(data.get(key), list):  # required list, not merely list-if-present
            raise SystemExit(f"corrupt {name} {path}: '{key}' must be a list")
    for key in ("errors", "warnings"):
        for item in data[key]:
            if not isinstance(item, str) or not item.strip():  # e.g. null / "" slipping through
                raise SystemExit(f"corrupt {name} {path}: every '{key}' entry must be a non-empty string")
    for row in data["rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("state"), str) or not row.get("state"):
            raise SystemExit(f"corrupt {name} {path}: every 'rows' entry needs a non-empty string 'state'")
    require(data, path, name)
    return data


def _require_complete_manifest(data: dict, path: Path, name: str) -> None:
    if data.get("ok") is not True:
        raise SystemExit(f"corrupt {name} {path}: a published frames manifest must have 'ok': true")


def _require_failure_evidence(data: dict, path: Path, name: str) -> None:
    if data.get("ok") is not False:
        raise SystemExit(f"corrupt {name} {path}: failure evidence must have 'ok': false")
    if not data["errors"]:
        raise SystemExit(f"corrupt {name} {path}: failure evidence must have a non-empty 'errors' list")
    # every diagnostic is a per-state message (`<state>: ...`) — the correction loop keys on the
    # state prefix, so a prefix-less or empty entry is a silently-lost failure (e.g. a `null`
    # entry would be dropped by the per-state merge and delete the whole record on next success).
    for key in ("errors", "warnings"):
        for item in data[key]:
            if ":" not in item or not item.split(":", 1)[0].strip():
                raise SystemExit(f"corrupt {name} {path}: every '{key}' entry must be state-scoped ('<state>: ...'), got {item!r}")


def load_frames_manifest(path: Path) -> dict:
    """Read a published frames-manifest.json (a COMPLETE generation), failing loud on corruption
    or a broken schema (`ok`:true, required `rows`/`errors`/`warnings` lists, rows carry a
    `state`). Returns {} only when genuinely absent (No Silent Fallback — see _load_validated)."""
    return _load_validated(path, "frames manifest", _require_complete_manifest)


def load_failure_evidence(path: Path) -> dict:
    """Read extract-failure.json (the run's unresolved per-state failures), failing loud on
    corruption or a broken schema (`ok`:false, non-empty state-scoped `errors`, `warnings`/`rows`
    lists). Returns {} only when genuinely absent (No Silent Fallback — see _load_validated)."""
    return _load_validated(path, "failure evidence", _require_failure_evidence)


def _require_generation_consistency(run_dir: Path, manifest: dict, name: str,
                                    allow_pending_states: bool = False) -> None:
    """Beyond JSON schema, a published generation must AGREE with the physical frame tree and the
    request. Frames and the manifest are published together as one transaction, so a disagreement
    means corruption/staleness: fail loud rather than let a consumer build output from stale,
    partial, or orphan frames (Consistency / No Silent Fallback). Checks:
      - every request state has exactly one row (no missing state, no duplicate row);
      - no physical frame state dir is an orphan the manifest omits;
      - every row's canonical frames exist on disk.

    `allow_pending_states` (관찰자 전용 — 큐레이션 뷰): 아직 생성되지 않은 요청 상태
    (manifest 행도 없고 물리 프레임도 없음)는 '진행 중'으로 허용한다. 부분 추출이
    정식 흐름(단계별 생성·증분 추출)이 된 뒤 뷰가 미생성 상태 때문에 죽으면 안 된다.
    소비자(compose/export 등)는 기본값(False)으로 완결 세대를 계속 강제한다."""
    frames_root = run_dir / "frames"
    try:
        request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read sprite-request.json for {run_dir}: {exc}")
    request_states = set(request.get("states", {}))
    row_states = [row["state"] for row in manifest["rows"]]
    dupes = sorted({s for s in row_states if row_states.count(s) > 1})
    if dupes:
        raise SystemExit(f"corrupt {name} {run_dir}: duplicate manifest rows for state(s) {dupes}")
    row_state_set = set(row_states)
    # request states and manifest row states must match EXACTLY, both directions: a missing row is
    # an incomplete generation, an extra row is a stale/unknown state some consumers would render
    # while serve (request-driven) hides it — a per-consumer divergence.
    missing = sorted(request_states - row_state_set)
    if missing:
        pending_only = [
            m for m in missing
            if not (run_dir / frames_dir_rel(request, m)).is_dir()
        ]
        if not (allow_pending_states and pending_only == missing):
            raise SystemExit(f"corrupt {name} {run_dir}: manifest missing row(s) for request state(s) {missing} — incomplete generation")
    unknown = sorted(row_state_set - request_states)
    if unknown:
        raise SystemExit(f"corrupt {name} {run_dir}: manifest has row(s) for state(s) {unknown} not in the request (stale/unknown state)")
    # 물리 고아 탐지 (경로 기반): 캐노니컬 프레임을 직접 담은 모든 디렉토리가
    # 어떤 row 의 파일 디렉토리이거나 요청 상태의 예약 위치여야 한다.
    row_dirs = set()
    for row in manifest["rows"]:
        for rel in row.get("files") or []:
            if isinstance(rel, str):
                row_dirs.add(str(Path(rel).parent))
    physical_dirs = set()
    if frames_root.is_dir():
        for frame in frames_root.rglob("frame-*.png"):
            if frame.name.endswith(".plain.png") or frame.parent.name == "orig":
                continue
            physical_dirs.add(str(Path("frames") / frame.parent.relative_to(frames_root)))
    orphan = sorted(physical_dirs - row_dirs)
    if orphan:
        raise SystemExit(f"corrupt {name} {run_dir}: physical frames under {orphan} have no manifest row (orphan/stale generation)")
    request_states_spec = request.get("states", {})
    for row in manifest["rows"]:
        state = row["state"]
        files = row.get("files")
        if not isinstance(files, list) or not files:
            raise SystemExit(f"corrupt {name} {run_dir}: manifest row '{state}' has no frame files (empty generation)")
        prefix = frames_dir_rel(request, state) + "/"
        for rel in files:
            if not isinstance(rel, str) or not rel.startswith(prefix):
                raise SystemExit(f"corrupt {name} {run_dir}: row '{state}' file {rel!r} is not under {prefix} (state boundary)")
            if not (run_dir / rel).is_file():
                raise SystemExit(f"corrupt {name} {run_dir}: manifest row '{state}' references missing frame {rel}")
        # the canonical physical frames for this state must EXACTLY equal the row's files — no
        # missing (the `files:[]` / deleted-frames case) and no extra (a stale frame on disk).
        state_dir = run_dir / frames_dir_rel(request, state)
        physical = sorted(
            f"{prefix}{f.name}" for f in state_dir.glob("frame-*.png") if not f.name.endswith(".plain.png")
        ) if state_dir.is_dir() else []
        if physical != sorted(files):
            raise SystemExit(f"corrupt {name} {run_dir}: row '{state}' files {sorted(files)} != physical canonical frames {physical}")
        # a complete generation has exactly the request's frame count for the state —
        # 행 프레임 풀 = primary + 선언된 테이크들 (takes 1급 계약)
        state_spec = request_states_spec.get(state, {})
        expected = int(state_spec.get("frames", len(files)))
        expected += sum(int(take.get("frames", 0)) for take in (state_spec.get("takes") or []))
        if len(files) != expected:
            raise SystemExit(f"corrupt {name} {run_dir}: row '{state}' has {len(files)} frame(s), request expects {expected}")
        if "frames" in row and row["frames"] != len(files):
            raise SystemExit(f"corrupt {name} {run_dir}: row '{state}' row.frames={row['frames']} != {len(files)} files")


def load_consistent_frames_manifest(run_dir: Path, name: str = "frames manifest",
                                     allow_pending_states: bool = False) -> dict:
    """For readers that TOLERATE an ungenerated run (serve scaffold, inspect from raw): return the
    validated + consistent manifest if present; {} only when the run genuinely has NO generation
    (no manifest AND no physical frame dirs); fail loud if the manifest is corrupt/inconsistent, OR
    absent while physical frames exist (an orphan/stale generation — not a fresh scaffold).
    `allow_pending_states=True` (뷰 전용) 는 아직 생성 전인 요청 상태의 행 부재를 허용한다."""
    frames_root = run_dir / "frames"
    manifest = load_frames_manifest(frames_root / "frames-manifest.json")
    if not manifest:
        physical = sorted(d.name for d in frames_root.iterdir() if d.is_dir()) if frames_root.is_dir() else []
        if physical:
            raise SystemExit(
                f"orphan frames in {run_dir}: physical frame dir(s) {physical} exist but "
                f"frames/frames-manifest.json is absent — not a fresh scaffold. Re-extract or remove them."
            )
        return {}
    _require_generation_consistency(run_dir, manifest, name, allow_pending_states=allow_pending_states)
    return manifest


def require_frames_manifest(run_dir: Path) -> dict:
    """Gate for a finished-generation consumer (compose / export / preview / gif / cycle): the
    published frames manifest must be present, schema-valid, AND consistent with the physical frame
    tree + request (No Silent Fallback / Consistency). A consumer of a completed generation must
    call this before reading physical frames, so it never silently produces output from a missing,
    broken, stale, partial, or orphan generation."""
    manifest = load_consistent_frames_manifest(run_dir)
    if not manifest:
        raise SystemExit(
            f"frames/frames-manifest.json not found in {run_dir}; run a successful extract before "
            f"consuming this generation."
        )
    return manifest


def _merged_failure_lists(prior: dict, result: dict, target_states: set, all_states: set):
    """Compute the union of the run's CURRENTLY-UNRESOLVED per-state failures. A subset
    `--states` extract only determines the outcome of the states it targets, so carry forward
    prior failures for untouched states and replace the target states with this attempt's
    outcome (a target state that succeeded drops out). Warnings are scoped to states that still
    have an error, so a resolved state never leaves a stray warning in the failure evidence.
    Returns (errors, warnings); empty errors means every recorded failure is resolved."""
    def _state_of(message: object):
        head = str(message).split(":", 1)[0]
        return head if head in all_states else None

    kept_errors = [e for e in prior.get("errors", []) if _state_of(e) is not None and _state_of(e) not in target_states]
    new_errors = [e for e in result.get("errors", []) if _state_of(e) is not None]
    merged_errors = kept_errors + new_errors
    unresolved = {str(e).split(":", 1)[0] for e in merged_errors}
    kept_warnings = [w for w in prior.get("warnings", []) if _state_of(w) not in target_states and _state_of(w) in unresolved]
    new_warnings = [w for w in result.get("warnings", []) if _state_of(w) in unresolved]
    return merged_errors, kept_warnings + new_warnings


def _commit_generation(run_dir: Path, staging: Path | None, result: dict, target_states: set, all_states: set) -> None:
    """Advance the run's two canonical surfaces — the frames/ generation and the per-state
    failure evidence (extract-failure.json) — as ONE transaction under a single publish_guard.

    `staging` is the new complete frames dir to swap into frames/ on success, or None to leave
    frames/ untouched on failure. The evidence is (re)written from the merged unresolved
    failures, or removed when none remain. Either BOTH surfaces advance or, on any I/O failure,
    both roll back to their pre-commit state (Atomicity/Consistency). Because the whole commit
    holds publish_guard, a reader under read_guard never observes the new frames alongside a
    stale failure record (Isolation) — the old defect where a swapped-in generation and a not-
    yet-merged prior failure were briefly visible together."""
    frames_final = run_dir / "frames"
    evidence = run_dir / "extract-failure.json"
    frames_backup = run_dir / ".frames.sg-backup"
    evidence_backup = run_dir / ".extract-failure.sg-backup"

    def _discard(p: Path) -> None:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()

    with publish_guard(run_dir):
        prior = load_failure_evidence(evidence)  # fail-loud on a corrupt/broken-schema record
        merged_errors, merged_warnings = _merged_failure_lists(prior, result, target_states, all_states)

        _discard(frames_backup)
        _discard(evidence_backup)
        frames_backed_up = False
        frames_swapped = False
        evidence_backed_up = False
        evidence_written = False
        try:
            if staging is not None:
                if frames_final.exists():
                    frames_final.rename(frames_backup)
                    frames_backed_up = True
                staging.rename(frames_final)
                frames_swapped = True
            if evidence.exists():
                evidence.rename(evidence_backup)
                evidence_backed_up = True
            if merged_errors:
                payload = dict(result)
                payload["ok"] = False
                payload["errors"] = merged_errors
                payload["warnings"] = merged_warnings
                atomic_write_text(evidence, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                evidence_written = True
            # else: leave evidence absent — every recorded per-state failure is resolved
        except BaseException:
            # roll BOTH surfaces back to their pre-commit state, in reverse order
            if evidence_written:
                _discard(evidence)
            if evidence_backed_up and not evidence.exists():
                evidence_backup.rename(evidence)
            if frames_swapped:
                _discard(frames_final)
                if frames_backed_up:
                    frames_backup.rename(frames_final)
            elif frames_backed_up and not frames_final.exists():
                frames_backup.rename(frames_final)
            raise
        _discard(frames_backup)
        _discard(evidence_backup)


def _run(args: argparse.Namespace):
    if args.fringe_key_threshold < args.key_threshold:
        raise SystemExit("--fringe-key-threshold must be greater than or equal to --key-threshold")
    if args.fringe_unmix_reach is not None and args.fringe_unmix_reach < 0:
        raise SystemExit("--fringe-unmix-reach must be zero or positive")
    if args.spill_max_fraction is not None and args.spill_max_fraction < 0:
        raise SystemExit("--spill-max-fraction must be zero or positive")

    run_dir = args.run_dir.expanduser().resolve()
    acquire_run_dir_lock(run_dir, "extract_sprite_row_frames")
    try:
        return _run_locked(args, run_dir)
    finally:
        # 작업 단위 상호배제 — atexit 까지 쥐고 있으면 장수 프로세스(테스트 러너 등)가
        # in-process 추출 뒤 heal_run 서브프로세스를 자기 락으로 막는다.
        release_run_dir_lock(run_dir)


def _run_locked(args: argparse.Namespace, run_dir: Path):
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
    chroma_mode = (
        args.chroma_mode
        if args.chroma_mode is not None
        else str(chroma_config.get("mode", "rgb"))
    )
    if chroma_mode not in ("rgb", "ycbcr"):
        raise SystemExit("chroma.mode must be 'rgb' or 'ycbcr'")
    effective_chroma = {
        **chroma_config,
        "mode": chroma_mode,
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
    # Build the new frames in a staging dir, then swap it into place as one generation under
    # publish_guard (below). Extract rewrites many frame files; without this, a live curation
    # reader (serve_curation read_guard) would observe a mix of old/new frame generations
    # mid-extract. The manifest records the FINAL `frames/<state>/...` paths regardless of the
    # staging write location. Writer-writer exclusion stays the separate `.sprite-gen.lock`.
    frames_final = run_dir / "frames"
    frames_root = run_dir / ".frames.sg-staging"
    target = set(states)
    # A subset `--states` re-extract (auto-correction / single-row regeneration) must preserve
    # the states it is NOT rebuilding — the staging generation, which is swapped in whole, is
    # seeded with those states' current frames and carries their prior manifest rows, so the
    # swap replaces only the rebuilt states and never deletes the others (SSoT/Idempotency).
    # Read+validate the prior manifest FIRST, before staging is even created: a corrupt prior
    # generation fails loud here (No Silent Fallback), so the prior generation stays byte-intact
    # and we never publish an incomplete manifest that disagrees with the carried frame tree.
    rows = []
    if frames_final.is_dir():
        prior_manifest = load_frames_manifest(frames_final / "frames-manifest.json")
        rows = [row for row in prior_manifest.get("rows", []) if row.get("state") not in target]
    if frames_root.exists():
        shutil.rmtree(frames_root)
    frames_root.mkdir(parents=True)
    if frames_final.is_dir():
        for state_name in request["states"]:
            if state_name not in target:
                rel = frames_dir_rel(request, state_name).removeprefix("frames/")
                src = frames_final / rel
                if src.is_dir():
                    (frames_root / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(src, frames_root / rel)
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
    # 기본 48 (24 에서 상향, 2026-07-17): run-wide 팔레트는 배치의 모든 행이 나눠 쓴다.
    # 24 는 다상태 배치(솔벨 founder 36상태×150프레임)에서 희소 포인트 컬러를 굶겼다 —
    # population median-cut 이 금색(머리끈·목걸이) 클러스터를 갈색 박스에 흡수해
    # 디테일이 통째로 사라졌다 (실측: 24색 배치 팔레트에서 금색 최근접 ΔRGB 59,
    # 48색이면 5.5). 레트로 감축이 필요한 런은 request `fit.palette_size` 로 명시한다.
    palette_size = int(fit_config.get("palette_size", 48))
    # 원본 화질 표시 쌍둥이(orig/)의 배율: 같은 legacy fit 을 S×셀에 앉혀 확대 흐림을
    # 없앤다. 4배(상한 1024px)로 캡. 셀이 이미 커서 S<=1 이면 굽지 않는다(.plain 로 충분).
    plain_display_scale = max(1, min(4, 1024 // max(1, cell_width, cell_height)))
    pending: list = []

    def finalize_state(state: str, frames: list, frame_count: int, method: str,
                       plain_frames: list | None = None, orig_frames: list | None = None,
                       input_grids: list | None = None, labels: list | None = None,
                       takes: list | None = None) -> None:
        rel_dir = frames_dir_rel(request, state)  # e.g. frames/down/idle (taxonomy) | frames/down_idle (legacy)
        state_dir = frames_root / rel_dir.removeprefix("frames/")
        state_dir.mkdir(parents=True, exist_ok=True)
        output_paths = []
        for index, frame in enumerate(frames):
            output = state_dir / f"frame-{index}.png"
            atomic_save_image(frame, output)
            output_paths.append(f"{rel_dir}/frame-{index}.png")  # final location (staging is swapped in)
        # 픽셀퍼펙트 전 원본 변형(.plain.png) — curation.json `pixel_perfect: false`
        # 굽기(compose)가 이 셀 크기 쌍둥이를 읽는다. 아틀라스 슬롯 = 셀 크기라 여긴
        # 셀 해상도로 유지한다 (compose_atlas 가 정확히 셀 크기를 요구, 기하 불변).
        plain_paths = []
        if plain_frames is not None:
            for index, frame in enumerate(plain_frames):
                output = state_dir / f"frame-{index}.plain.png"
                atomic_save_image(frame, output)
                plain_paths.append(f"{rel_dir}/frame-{index}.plain.png")
        # 원본 화질 표시용 고해상 쌍둥이 — 큐레이션뷰 pp 해제 토글이 읽는다. 셀 크기
        # .plain.png 는 확대 시 흐리므로 별도 서브폴더(orig/)에 S×셀로 굽는다. 서브폴더라
        # frame-*.png glob 소비자(inspect/measure/compose)와 충돌하지 않고 순수 표시용이다.
        orig_paths = []
        if orig_frames is not None:
            orig_dir = state_dir / "orig"
            orig_dir.mkdir(parents=True, exist_ok=True)
            for index, frame in enumerate(orig_frames):
                output = orig_dir / f"frame-{index}.png"
                atomic_save_image(frame, output)
                orig_paths.append(f"{rel_dir}/orig/frame-{index}.png")

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
            # 파생 캐시 키 — 이 행을 구운 엔진 리비전. heal_run 이 현재 엔진과
            # 비교해 stale 행을 raw 에서 자동 재유도한다 (self-heal).
            "engine_revision": engine_revision(),
        }
        if labels and any(labels):
            row["labels"] = labels
        if takes:
            row["takes"] = takes
        if plain_paths:
            row["plain_files"] = plain_paths
        if orig_paths:
            row["orig_files"] = orig_paths
        if input_grids is not None and any(g is not None for g in input_grids):
            # 프레임별 검출 입력 격자(셀 좌표 절단선) — 큐레이터 원본 뷰 오버레이용
            row["input_grids"] = input_grids
        rows.append(row)

    def _load_strip(state: str, tag: str, rel: str, frame_count: int) -> Image.Image | None:
        raw_path = run_dir / rel
        if not raw_path.is_file():
            all_errors.append(f"{tag}: missing raw strip {raw_path}")
            return None
        with Image.open(raw_path) as opened:
            if chroma_mode == "ycbcr":
                ycc_notes: list[str] = []
                strip = remove_chroma_background_ycbcr(opened, chroma_key, ycc_notes)
                all_warnings.extend(f"{tag}: {note}" for note in ycc_notes)
            else:
                strip = remove_chroma_background(
                    opened,
                    chroma_key,
                    args.key_threshold,
                    args.fringe_key_threshold,
                    args.fringe_delta,
                    unmix_reach=unmix_reach,
                    spill_max_fraction=spill_max_fraction,
                )
        return separate_fused_poses(strip, frame_count, fit_config, args.segmentation, state)

    def _snap_strip(tag: str, strip: Image.Image, frame_count: int) -> dict[str, Any] | None:
        """한 생성 스트립(primary 또는 take)을 컴포넌트 분리→합의 스냅→캡까지.

        프레임별 픽셀퍼펙트 (2026-07-05 재설계, 2026-07-20 per-frame 확정): 포즈
        컴포넌트를 먼저 분리한 뒤 각 프레임마다 피치·위상을 독립 검출해 **자기
        검출값으로** 스냅한다. 스트립 전역 단일 격자는 프레임 간 위상 드리프트로
        미끄러졌고(알렉스 관찰: "격자가 픽셀에 안 맞음"), 합의 피치 강제는 측정차를
        폭 전체에 누적시켰다(수홍 관찰: "눈이 반쪽", plan per-frame-pixel-grid).
        스트립(=한 번의 생성) 단위 합의는 검출 실패 프레임의 fallback 전용이다
        — 테이크마다 생성이 달라 블록 크기가 다를 수 있어 합의도 스트립 단위다.
        """
        images = extract_component_images(strip, frame_count)
        method = "components"
        if images is None:
            if not args.allow_slot_fallback:
                all_errors.append(f"{tag}: could not extract {frame_count} sprite components")
                return None
            slot_width = strip.width / frame_count
            images = [
                strip.crop((round(i * slot_width), 0, round((i + 1) * slot_width), strip.height))
                for i in range(frame_count)
            ]
            method = "slots-explicit"
        images = tighten_components(images)
        # 프레임 자체 검출 (피치·위상) 이 1순위 진실이다. 프레임별 검출값의
        # 중앙값(합의)은 검출 실패 프레임의 fallback 전용으로만 계산한다 —
        # 합의를 프레임에 강제하면 측정차가 폭 전체에 누적된다 (아래 스냅 루프 주석).
        # 피치는 소수이고 축마다 다를 수 있다 — AI 가 그린 블록은 정수 픽셀로 떨어지지
        # 않고(예: 17.24), 비균등 리스케일된 생성물은 가로/세로 블록 크기가 어긋난다.
        # 정수로 반올림하면 그 오차가 폭 전체에 누적돼 셀 경계가 블록 한가운데를 지난다.
        # 측정은 소수·축별로 하고, 격자선은 `_grid_edges` 가 정수로 확정한다.
        hint = int(fit_config.get("pitch_hint", 0))
        grids = [detect_pixel_grid(component) for component in images]

        def _consensus(axis: int) -> float:
            confident = sorted(g[0][axis] for g in grids if g[0][axis] >= 2.0)
            if confident:
                # 붕괴한 프레임(참 피치의 약수로 떨어진 값)이 중앙값을 오염시킨다 —
                # 솔벨 down_carry_run 은 6 프레임 중 절반이 3.00 으로 무너져 합의가 5.00 이 됐다.
                # 스트립 안에서 참 피치는 거의 같으므로, 최대값의 60% 미만은 붕괴로 보고 버린다.
                ceiling = confident[-1]
                trusted = [p for p in confident if p >= ceiling * 0.6]
                dropped = len(confident) - len(trusted)
                if dropped:
                    all_warnings.append(
                        f"{tag}: dropped {dropped} collapsed per-frame pitch(es) below {ceiling * 0.6:.2f}"
                    )
                return trusted[len(trusted) // 2]
            if hint >= 2:
                all_warnings.append(
                    f"{tag}: pitch from fit.pitch_hint={hint} (all per-frame detections inconclusive)"
                )
                return float(hint)
            strip_pitch, _ = detect_pixel_grid(strip)
            if strip_pitch[axis] >= 2.0:
                all_warnings.append(
                    f"{tag}: pitch from whole-strip detection={strip_pitch[axis]:.2f}"
                )
            return strip_pitch[axis]

        consensus_x, consensus_y = _consensus(0), _consensus(1)
        # 세컨드 오피니언 (perfectpixel unfake 이식): 동일색 런 최빈값으로 축별
        # 피치를 따로 추정해 합의 피치와 교차검증한다. 경고 전용 — 스냅은 아래에서
        # 계속 detect_pixel_grid 합의만 쓴다 (자동 교체 금지, No Silent Fallback).
        runlen_axes = [estimate_pixel_grid_runlen(component) for component in images]

        def _runlen_consensus(axis: int) -> float:
            confident = sorted(e[axis] for e in runlen_axes if e[axis] >= 2.0)
            return confident[len(confident) // 2] if confident else 1.0

        for note in crosscheck_pitch_runlen(
            (consensus_x, consensus_y), (_runlen_consensus(0), _runlen_consensus(1))
        ):
            all_warnings.append(f"{tag}: {note}")
            print(f"[pitch-crosscheck] {tag}: {note}", file=sys.stderr)
        # 피치 중재 (하모닉 오검출 방어): 세컨드 오피니언과 크게 어긋나면 셀 균일도
        # 실측으로 승자 채택 — 채택 결과는 경고로 관측된다 (arbitrate_pitch docstring).
        frame_phases = [frame_phase for (_, frame_phase) in grids]
        (consensus_x, consensus_y), frame_phases, _pitch_src = arbitrate_pitch(
            images, (consensus_x, consensus_y), frame_phases,
            (_runlen_consensus(0), _runlen_consensus(1)), tag, all_warnings)
        # 프레임 자체 검출 격자가 1순위 진실이다 (수홍 2026-07-20, plan
        # sprite-gen/per-frame-pixel-grid): 합의 피치를 프레임에 강제하면 측정차
        # (0.5px/셀 수준)가 폭 전체에 누적돼 경계가 블록 중앙을 지난다 (실사고
        # founder_v7 down_jump frame-0: 합의 13.00 vs 자체 12.50 → 눈이 반쪽).
        # 합의는 자체 검출이 실패한 프레임의 fallback 전용이고, 적용 사실은
        # 프레임별 경고로 관측된다. 자체 검출이 합의와 크게 어긋나도 덮어쓰지
        # 않는다 — 경고만 남긴다 (No Silent Fallback). 기록(cut_edges)은 실제
        # 샘플링한 그 선이라 표시 격자 = 샘플링 진실 (수홍 확정 2026-07-18) 유지.
        snapped = []
        cut_edges: list[tuple[list[int], list[int]] | None] = []
        used_pitches: list[tuple[float, float]] = []
        for index, (component, ((own_x, own_y), _own_phase)) in enumerate(zip(images, grids)):
            if min(own_x, own_y) >= 2.0:
                use_x, use_y = own_x, own_y
                if min(consensus_x, consensus_y) >= 2.0 and (
                    abs(own_x - consensus_x) > max(2.0, consensus_x * 0.25)
                    or abs(own_y - consensus_y) > max(2.0, consensus_y * 0.25)
                ):
                    all_warnings.append(
                        f"{tag}: frame {index} own pitch {own_x:.2f}x{own_y:.2f} deviates from "
                        f"strip consensus {consensus_x:.2f}x{consensus_y:.2f} — kept own (no override)")
            elif min(consensus_x, consensus_y) >= 2.0:
                use_x, use_y = consensus_x, consensus_y
                all_warnings.append(
                    f"{tag}: frame {index} pitch detection inconclusive — consensus fallback "
                    f"{consensus_x:.2f}x{consensus_y:.2f}")
            else:
                snapped.append(component)
                cut_edges.append(None)
                used_pitches.append((1.0, 1.0))
                continue
            frame_phase = frame_phases[index]
            xs = _grid_edges(component.width, use_x, frame_phase[0])
            ys = _grid_edges(component.height, use_y, frame_phase[1])
            xs, ys = refine_edges_to_boundaries(component, xs, ys, (use_x, use_y))
            snapped.append(snap_by_edges(component, xs, ys, pp_detail_bias))
            cut_edges.append((xs, ys))
            used_pitches.append((use_x, use_y))
        rounded = [(round(px_, 2), round(py_, 2)) for (px_, py_) in used_pitches]
        def _pitch_repr(pair: tuple[float, float]):
            return pair[0] if abs(pair[0] - pair[1]) < 0.05 else pair
        pitch = (_pitch_repr(rounded[0]) if len(set(rounded)) == 1
                 else [_pitch_repr(pair) for pair in rounded])
        # 눌림 없음 — 스냅된 네이티브 논리 크기를 유지한다 (수홍 확정 2026-07-14,
        # 옵트인 잔재까지 완전 제거 2026-07-17): 계약(logical_height)으로의 conform
        # 축소는 칸을 병합해 디테일을 갈라먹는다. 과거의 `fit.conform: true` 옵트인은
        # 제거된 결정이 플래그 하나로 되살아나는 회귀 경로였다 (실사고 2026-07-17:
        # 높이 통일 시도가 이 플래그로 재발) — 선언되어 있으면 요란하게 거부한다.
        # 물리 한계(셀에서 바닥 마진만 지킴)만 캡으로 강제하고, 캡에 걸리면 관측
        # 가능하게 경고한다.
        if fit_config.get("conform") is not None:
            raise SystemExit(
                "fit.conform was removed (Soohong 2026-07-14/17): native logical size is "
                "the only behavior — squashing to logical_height merges cells and eats "
                "detail. Remove the key from sprite-request.json.")
        cap_w = max(1, cell_width // pp_scale)
        cap_h = max(1, (cell_height - safe_margin_y) // pp_scale)
        over = [f"{i}:{s.width}x{s.height}" for i, s in enumerate(snapped)
                if s.width > cap_w or s.height > cap_h]
        if over:
            all_warnings.append(
                f"{tag}: native logical exceeds the physical cap "
                f"{cap_w}x{cap_h} — capped frames (reroll candidates): {', '.join(over)}")
        # 안전영역(사방 여백 준수 상한)은 넘었지만 물리캡 이내 = 여백 침범.
        # 리롤 대상 아님 — 정보성 알림만 (수홍 확정 2026-07-14).
        safe_w = max(1, (cell_width - safe_margin_x * 2) // pp_scale)
        safe_h = max(1, (cell_height - safe_margin_y * 2) // pp_scale)
        soft = [f"{i}:{s.width}x{s.height}" for i, s in enumerate(snapped)
                if (s.width > safe_w or s.height > safe_h)
                and s.width <= cap_w and s.height <= cap_h]
        if soft:
            all_warnings.append(
                f"{tag}: frames exceed the safe area {safe_w}x{safe_h} but fit "
                f"within the margin zone (informational, no action): {', '.join(soft)}")
        logical_frames = conform_row_logical(snapped, cap_w, cap_h, pp_detail_bias)
        return {"method": method, "pitch": pitch, "logical": logical_frames,
                "components": images, "cut_edges": cut_edges}

    total_steps = len(states) * 2  # 1패스(컴포넌트/스냅) + 2패스(팔레트/배치)
    progress_step = 0
    for state in states:
        _write_progress(run_dir, "extract", progress_step, total_steps, state, "components")
        progress_step += 1
        if state not in request["states"]:
            raise SystemExit(f"unknown state in request: {state}")
        state_cfg = request["states"][state]
        frame_count = int(state_cfg["frames"])
        takes_cfg = state_cfg.get("takes") or []
        if takes_cfg and not pixel_perfect:
            all_errors.append(f"{state}: takes require fit.pixel_perfect")
            continue
        if not pixel_perfect:
            strip = _load_strip(state, state, raw_rel(request, state), frame_count)
            if strip is None:
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
            continue
        # 테이크 1급 계약: 한 상태의 프레임 풀 = primary 스트립 + 선언된 테이크들.
        # 각 스트립은 독립 생성이므로 따로 스냅하고(스트립별 합의 피치), 행 정합·
        # 팔레트·배치는 행 단위로 함께 한다. 어느 스트립 하나라도 실패하면 행 전체를
        # 이전 세대로 남긴다 (Atomicity — 부분 풀 게시 금지).
        specs: list[tuple[str, str, int, str]] | None = [
            (state, raw_rel(request, state), frame_count, "")]
        for take in takes_cfg:
            label = str(take.get("label") or "")
            if not label or "/" in label or label.startswith("."):
                all_errors.append(f"{state}: take needs a filesystem-safe label: {take!r}")
                specs = None
                break
            specs.append((f"{state}[{label}]", take_raw_rel(request, state, label),
                          int(take["frames"]), label))
        if specs is None:
            continue
        parts: list[dict[str, Any]] | None = []
        for tag, rel, count, label in specs:
            strip = _load_strip(state, tag, rel, count)
            part = _snap_strip(tag, strip, count) if strip is not None else None
            if part is None:
                parts = None
                break
            part.update({"label": label, "count": count, "raw": rel})
            parts.append(part)
        if parts is None:
            continue
        registered = register_row_frames([f for p in parts for f in p["logical"]])
        labels = None
        takes_summary = None
        if len(parts) > 1:
            labels = [
                f"{p['label']}#{i}" if p["label"] else ""
                for p in parts for i in range(p["count"])
            ]
            starts = [0]
            for p in parts[:-1]:
                starts.append(starts[-1] + p["count"])
            takes_summary = [
                {"label": p["label"] or None, "start": start, "frames": p["count"], "raw": p["raw"]}
                for p, start in zip(parts, starts)
            ]
        # 전/후 비교 쌍둥이(plain/orig)는 픽셀퍼펙트 프레임의 최종 콘텐츠 bbox 가
        # 확정된 뒤(아래 pending 루프) 같은 풋프린트에 앉힌다 — 여기서는 원본
        # 컴포넌트만 보관한다. (이전: legacy fit 이 가용영역을 채워 pp 결과보다
        # 크게 앉음 → 토글 순간 크기가 튀어 품질 비교가 안 됐다.)
        pending.append({
            "state": state, "frame_count": len(registered), "method": parts[0]["method"],
            "pitch": parts[0]["pitch"] if len(parts) == 1 else [p["pitch"] for p in parts],
            "frames": registered,
            "components": [c for p in parts for c in p["components"]],
            "cut_edges": [e for p in parts for e in p["cut_edges"]],
            "labels": labels, "takes": takes_summary,
        })

    if pixel_perfect and pending:
        # 팔레트는 런 전체(모든 state 의 논리 프레임)에서 한 번 뽑아 공유한다 —
        # 프레임/행 간 색 흔들림(플리커) 제거 + 아이덴티티 색 고정.
        pinned = None if getattr(args, "repalette", False) else load_pinned_palette(run_dir)
        if pinned is not None:
            palette = pinned
            all_warnings.append(f"palette: pinned ({len(palette)} colors, {PALETTE_LOCK})")
        else:
            palette = build_shared_palette([f for entry in pending for f in entry["frames"]], palette_size)
            write_pinned_palette(run_dir, palette,
                                 f"batch:{','.join(entry['state'] for entry in pending)}")
            all_warnings.append(f"palette: rebuilt+pinned ({len(palette)} colors → {PALETTE_LOCK})")
        total_steps = progress_step + len(pending)  # 스킵된 행 반영 재산정
        outline_cfg = fit_config.get("outline", True)
        for entry in pending:
            _write_progress(run_dir, "extract", progress_step, total_steps, entry["state"], "pixel-perfect")
            progress_step += 1
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
            # 전/후 비교 쌍둥이: 픽셀퍼펙트 프레임의 최종 콘텐츠 bbox 와 같은 풋프린트에
            # 원본 컴포넌트를 앉힌다 (plain=셀 크기 굽기용, orig=S×셀 표시용). 빈 프레임은
            # 관측 가능하게 스킵 — 조용한 폴백 없음.
            plain_frames = []
            orig_frames = [] if plain_display_scale > 1 else None
            input_grids: list[dict | None] = []
            for index, (component, frame) in enumerate(zip(entry["components"], frames)):
                frame_bbox = frame.getbbox()
                if frame_bbox is None:
                    all_warnings.append(
                        f"{entry['state']}: frame {index} is empty — plain/orig twin skipped")
                    plain_frames.append(Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0)))
                    if orig_frames is not None:
                        orig_frames.append(Image.new(
                            "RGBA", (cell_width * plain_display_scale, cell_height * plain_display_scale), (0, 0, 0, 0)))
                    input_grids.append(None)
                    continue
                plain, mapping = fit_component_to_bbox(component, cell_width, cell_height, frame_bbox)
                plain_frames.append(plain)
                if orig_frames is not None:
                    orig_frames.append(fit_component_to_bbox(
                        component, cell_width, cell_height, frame_bbox, plain_display_scale)[0])
                # 검출된 입력 격자(실제 절단선)를 쌍둥이(셀) 좌표로 매핑해 manifest 에 남긴다
                edges = entry["cut_edges"][index] if index < len(entry["cut_edges"]) else None
                if edges is not None and mapping is not None:
                    input_grids.append({
                        "x": [round(mapping["left"] + (e - mapping["crop_x"]) * mapping["ratio"], 1)
                              for e in edges[0]],
                        "y": [round(mapping["top"] + (e - mapping["crop_y"]) * mapping["ratio"], 1)
                              for e in edges[1]],
                    })
                else:
                    input_grids.append(None)
            finalize_state(entry["state"], frames, entry["frame_count"], entry["method"],
                           plain_frames=plain_frames, orig_frames=orig_frames,
                           input_grids=input_grids, labels=entry.get("labels"),
                           takes=entry.get("takes"))
        all_warnings.append(
            "pixel-perfect: pitch=%s scale=%dx logical<=%dx%d palette=%d"
            % (",".join(str(entry["pitch"]) for entry in pending), pp_scale, logical_width, logical_height, len(palette))
        )

    result = {
        "ok": not all_errors,
        "engine": "component-row",
        "engine_revision": engine_revision(),
        # 이 생성에 실제로 쓰인 비기본 추출 플래그 — heal_run 재유도가 같은
        # 조건으로 재현하도록 기록한다 (조건 드리프트 = 조용한 결과 변화 금지).
        "extract_args": _nondefault_args(args),
        "run_dir": str(run_dir),
        "cell": request["cell"],
        "chroma_key": request["chroma_key"],
        "rows": rows,
        "errors": all_errors,
        "warnings": all_warnings,
    }
    all_state_names = set(request["states"])
    _clear_progress(run_dir)
    if result["ok"]:
        # Whole-generation atomicity: canonical frames/ only ever holds a COMPLETE generation.
        # Swap in the staging generation AND resolve this attempt's target states in the failure
        # evidence as ONE publish transaction — a subset --states extract does not touch the other
        # states, so their unresolved failures are preserved (file removed only once nothing
        # remains, Consistency), and a reader never sees new frames beside a stale failure record.
        atomic_write_text(frames_root / "frames-manifest.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        _commit_generation(run_dir, frames_root, result, target, all_state_names)
    else:
        # A failed extract — FIRST or re-extract — never publishes a partial/ok:false generation
        # to canonical frames/ (strict Atomicity: the operation fully succeeds or leaves canonical
        # state untouched). The per-state failure signal is recorded OUTSIDE frames/ as
        # extract-failure.json so it stays observable (No Silent Fallback) and still drives the
        # automatic correction loop (inspect._manifest_state_notes reads it → score → hint →
        # regenerate). The evidence merges per state, so one state's failure never overwrites
        # another's still-unresolved failure. A failed re-extract leaves the prior complete
        # generation byte-intact. Exit code 1 + printed errors signal the failure.
        _commit_generation(run_dir, None, result, target, all_state_names)
        shutil.rmtree(frames_root, ignore_errors=True)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1



def _nondefault_args(args: argparse.Namespace) -> dict[str, Any]:
    """파서 기본값과 다른 플래그만 추린다 (run_dir/states 제외) — manifest 기록용."""
    diff: dict[str, Any] = {}
    for action in _build_parser()._actions:
        dest = action.dest
        if dest in ("help", "run_dir", "states"):
            continue
        value = getattr(args, dest, None)
        if value != action.default:
            diff[dest] = value
    return diff


_ENGINE_REVISION: str | None = None
_ENGINE_REVISION_KEY: tuple | None = None


def engine_revision() -> str:
    """추출 엔진 리비전 = 엔진 소스 해시. 프레임 파일은 (raw + request + 엔진)의
    파생 캐시고 이 값이 캐시 키다 — 행 스탬프와 불일치하면 stale.

    캐시 키 = 소스 파일 mtime. 프로세스-수명 캐시는 장수 프로세스(큐레이션 서버)가
    엔진 갱신 후에도 옛 리비전을 들고 있게 만들어, 새 리비전으로 스탬프하는
    서브프로세스 힐과 서로를 stale 로 보는 **힐 무한 루프**를 만들었다
    (실사고 2026-07-17: 3초 폴링마다 전체 36행 재추출 재점화 — 뷰 빈 화면, 수홍 발견)."""
    global _ENGINE_REVISION, _ENGINE_REVISION_KEY
    sources = (Path(__file__), Path(__file__).with_name("layout.py"))
    key = tuple(source.stat().st_mtime_ns for source in sources)
    if _ENGINE_REVISION is None or _ENGINE_REVISION_KEY != key:
        digest = hashlib.sha256()
        for source in sources:
            digest.update(source.read_bytes())
        _ENGINE_REVISION = digest.hexdigest()[:12]
        _ENGINE_REVISION_KEY = key
    return _ENGINE_REVISION


def heal_run(run_dir: Path | str) -> dict[str, Any]:
    """파생 프레임 캐시의 자가치유 — 소비자(큐레이션 뷰/합성/내보내기) 진입점이 부른다.

    큐레이션 뷰에는 '재추출' 이라는 개념이 없다 (수홍 확정 2026-07-14): 뷰가
    보여주는 것은 항상 (raw + request + 현재 엔진 + 큐레이션)의 실시간 결과다.
    frames/ 는 그 파생 캐시일 뿐이므로, 행의 engine_revision 이 현재 엔진과
    다르면 raw 에서 조용히 다시 굽는다. raw 가 없는 행은 재료가 없어 재유도할
    수 없다 — 지우지도 속이지도 않고 그대로 두되 노트로 관측 가능하게 남긴다.
    재추출은 기존 스테이징+통짜 스왑 경로를 그대로 타므로 실패해도 이전 세대가
    바이트 그대로 남는다 (Atomicity).
    """
    run_dir = Path(run_dir)
    report: dict[str, Any] = {"healed": [], "kept_stale": [], "notes": []}
    manifest_path = run_dir / "frames" / "frames-manifest.json"
    request_path = run_dir / "sprite-request.json"
    if not manifest_path.is_file() or not request_path.is_file():
        return report
    manifest = load_frames_manifest(manifest_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    current = engine_revision()
    # 확정 행 동결 (수홍 확정 2026-07-18): curation states.<state>.frozen == true 인
    # 행은 사용자가 승인·편집을 끝낸 확정본 — 엔진이 바뀌어도 자가치유가 절대
    # 재유도하지 않는다 (실사고: 서버 가동 중 엔진 편집 → heal 이 확정 down_idle 을
    # 재양자화해 드리프트). 해제는 사용자가 frozen 을 끄는 것뿐 — 관측 노트로 남긴다.
    frozen: set[str] = set()
    curation_path = run_dir / "curation.json"
    if curation_path.is_file():
        try:
            cur_states = json.loads(curation_path.read_text(encoding="utf-8")).get("states", {})
            frozen = {s for s, e in cur_states.items() if isinstance(e, dict) and e.get("frozen")}
        except ValueError:
            pass  # 손상 사이드카는 로드 경로가 별도로 보고한다
    frozen_kept = []
    for row in manifest.get("rows", []):
        state = row.get("state")
        if state not in request.get("states", {}) or row.get("engine_revision") == current:
            continue
        if state in frozen:
            frozen_kept.append(state)
            continue
        raws = [raw_rel(request, state)] + [
            take_raw_rel(request, state, str(take.get("label") or ""))
            for take in (request["states"][state].get("takes") or [])
        ]
        if all((run_dir / rel).is_file() for rel in raws):
            report["healed"].append(state)
        else:
            report["kept_stale"].append(state)
    if frozen_kept:
        report["frozen"] = frozen_kept
        report["notes"].append("frozen rows kept (user-approved, heal skipped): " + ", ".join(frozen_kept))
    if report["kept_stale"]:
        report["notes"].append(
            "stale rows kept as-is (raw missing, cannot re-derive): "
            + ", ".join(report["kept_stale"]))
    if report["healed"]:
        # 서브프로세스로 재추출한다: run-dir 락은 atexit 해제라, 장수 프로세스
        # (큐레이션 서버)가 인프로세스로 추출하면 락을 영구 보유해 이후의
        # compose/export 서브프로세스가 전부 죽는다. 자식 프로세스면 락 수명이
        # 그 실행으로 끝나고, 추출의 SystemExit 도 격리된다.
        cmd = [sys.executable, "-m", "sprite_gen.extract",
               "--run-dir", str(run_dir), "--states", ",".join(report["healed"])]
        for dest, value in (manifest.get("extract_args") or {}).items():
            flag = "--" + dest.replace("_", "-")
            if isinstance(value, bool):
                if value:
                    cmd.append(flag)
            else:
                cmd.extend([flag, str(value)])
        # 자식은 부모의 sys.path 조작(스크립트 shim 등)을 상속하지 않는다 —
        # 패키지 루트를 PYTHONPATH 로 명시해 어디서 불러도 -m 이 뜬다.
        env = dict(os.environ)
        env["PYTHONPATH"] = (str(Path(__file__).resolve().parents[1])
                             + os.pathsep + env.get("PYTHONPATH", ""))
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            report["failed"] = report.pop("healed")
            report["healed"] = []
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            report["notes"].append(
                "heal re-extract failed — prior generation kept: " + " | ".join(tail))
    return report



PROGRESS_REL = ".sprite-gen.progress.json"


def _write_progress(run_dir: Path, op: str, done: int, total: int, label: str, phase: str) -> None:
    """오래 걸리는 파이프라인의 잘게 쪼갠 진행도 (수홍 요청 2026-07-18 "잘게잘게").

    한 파일 SSoT — 큐레이터가 폴링해 퍼센트로 표시한다. 완료 시 삭제 (아래
    _clear_progress). 기록 실패는 파이프라인을 막지 않는다 (진행도는 관측용)."""
    import time as _time
    try:
        atomic_write_text(run_dir / PROGRESS_REL, json.dumps({
            "op": op, "done": done, "total": total, "label": label,
            "phase": phase, "ts": _time.time()}, ensure_ascii=False))
    except OSError:
        pass


def _clear_progress(run_dir: Path) -> None:
    try:
        (run_dir / PROGRESS_REL).unlink(missing_ok=True)
    except OSError:
        pass


def run(**kwargs: object):
    return _run(_namespace_from_kwargs(**kwargs))

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
