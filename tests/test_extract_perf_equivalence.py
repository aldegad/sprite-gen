# SPDX-License-Identifier: Apache-2.0
"""Byte-identity guard for the extract-performance optimizations (plan
sprite-gen/extract-performance).

The hot paths `_grid_score_edges`/`_best_phase`, `_matte_ycc`,
`_flood_clear_background_ycc`, `_cleanup_alpha_ycc` and `_dominant_block_color`
were rewritten for speed under an absolute determinism contract: same input ->
byte-identical output. These tests pin that contract structurally by running a
**naive reference** implementation (the pre-optimization form) beside the
optimized one and asserting exact equality — so any future edit that changes a
value, not just the golden run, trips here.
"""

from __future__ import annotations

import math

from PIL import Image

from sprite_gen import extract


def _synthetic_keyed(width: int = 60, height: int = 48) -> Image.Image:
    """Green-screen-ish RGBA: flat key bg + shaded key + subject blob + AA
    fringe + an enclosed key-family pixel (connectivity) + spill band."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = img.load()
    for y in range(height):
        for x in range(width):
            # base key background, slightly shaded by position (gradient noise)
            g = 238 - ((x + y) % 7)
            r = 11 + ((x * 3) % 5)
            b = 27 + ((y * 2) % 5)
            a = 255
            # subject blob (a magenta-ish body) in the center
            if 18 <= x <= 40 and 12 <= y <= 38:
                r, g, b = 180 + (x % 20), 40 + (y % 15), 150 - (x % 12)
                # spill band on the blob's left edge (key-direction chroma)
                if x <= 22:
                    r, g, b = 120, 180, 90
            # an enclosed key-family pixel deep inside the subject (must survive)
            if x == 30 and y == 25:
                r, g, b = 12, 236, 26
            px[x, y] = (r, g, b, a)
    return img


# --- reference (naive, pre-optimization) implementations --------------------


def _ref_grid_uniformity(component, pitch, phase):
    xs = extract._grid_edges(component.width, pitch[0], phase[0])
    ys = extract._grid_edges(component.height, pitch[1], phase[1])
    p = component.load()
    total_dev = 0.0
    total_n = 0
    for yi in range(len(ys) - 1):
        for xi in range(len(xs) - 1):
            acc = []
            for y in range(ys[yi], min(ys[yi + 1], component.height)):
                for x in range(xs[xi], min(xs[xi + 1], component.width)):
                    q = p[x, y]
                    if q[3] >= 128:
                        acc.append(q[:3])
            n = len(acc)
            if n < 2:
                continue
            mean = [sum(c[k] for c in acc) / n for k in range(3)]
            dev = sum(sum(abs(c[k] - mean[k]) for k in range(3)) for c in acc) / n
            total_dev += dev * n
            total_n += n
    return (total_dev / total_n) if total_n else 1e9


def _ref_matte(source, key):
    width, height = source.size
    out = Image.new("RGBA", source.size, (0, 0, 0, 0))
    src_pixels = source.load()
    out_pixels = out.load()
    _, key_cb, key_cr = extract.rgb_to_ycc(*key)
    key_vb, key_vr = key_cb - 128.0, key_cr - 128.0
    key_len = math.hypot(key_vb, key_vr)
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = src_pixels[x, y]
            if alpha == 0:
                continue
            luma, cb, cr = extract.rgb_to_ycc(red, green, blue)
            dist = math.hypot(cb - key_cb, cr - key_cr)
            coverage = extract.smoothstep(extract._YCC_CHROMA_IN, extract._YCC_CHROMA_OUT, dist)
            if coverage <= 0:
                continue
            if key_len > 1 and dist < extract._YCC_DESPILL_BAND:
                pixel_vb, pixel_vr = cb - 128.0, cr - 128.0
                proj = (pixel_vb * key_vb + pixel_vr * key_vr) / key_len
                if proj > 0:
                    weight = extract.smoothstep(
                        0.0, 1.0, (extract._YCC_DESPILL_BAND - dist) / extract._YCC_DESPILL_BAND
                    ) * extract._YCC_DESPILL_SCALE
                    cb = 128.0 + (pixel_vb - key_vb / key_len * proj * weight)
                    cr = 128.0 + (pixel_vr - key_vr / key_len * proj * weight)
                    red, green, blue = extract.ycc_to_rgb(luma, cb, cr)
            out_pixels[x, y] = (red, green, blue, int(alpha * coverage))
    # naive border flood, matching pre-optimization semantics
    _ref_flood(out, source, key_cb, key_cr)
    return out


def _ref_flood(out, source, key_cb, key_cr):
    width, height = source.size
    if width < 3 or height < 3:
        return
    src_pixels = source.load()
    out_pixels = out.load()
    visited = bytearray(width * height)
    stack = []

    def push(x, y):
        position = y * width + x
        if visited[position]:
            return
        red, green, blue = src_pixels[x, y][:3]
        _, cb, cr = extract.rgb_to_ycc(red, green, blue)
        if math.hypot(cb - key_cb, cr - key_cr) <= extract._YCC_FLOOD_TOL:
            visited[position] = 1
            stack.append(position)

    for x in range(width):
        push(x, 0)
        push(x, height - 1)
    for y in range(height):
        push(0, y)
        push(width - 1, y)
    while stack:
        position = stack.pop()
        x, y = position % width, position // width
        red, green, blue, _ = out_pixels[x, y]
        out_pixels[x, y] = (red, green, blue, 0)
        if x > 0:
            push(x - 1, y)
        if x < width - 1:
            push(x + 1, y)
        if y > 0:
            push(x, y - 1)
        if y < height - 1:
            push(x, y + 1)


def _ref_cleanup(image):
    width, height = image.size
    if width < 3 or height < 3:
        return
    before = image.getchannel("A").tobytes()
    pixels = image.load()

    def opaque(x, y):
        if x < 0 or y < 0 or x >= width or y >= height:
            return 0
        return 1 if before[y * width + x] > extract._YCC_ALPHA_EMPTY else 0

    for y in range(height):
        for x in range(width):
            neighbors = (
                opaque(x - 1, y) + opaque(x + 1, y) + opaque(x, y - 1) + opaque(x, y + 1)
                + opaque(x - 1, y - 1) + opaque(x + 1, y - 1)
                + opaque(x - 1, y + 1) + opaque(x + 1, y + 1)
            )
            if before[y * width + x] > extract._YCC_ALPHA_EMPTY:
                if neighbors == 0:
                    red, green, blue, _ = pixels[x, y]
                    pixels[x, y] = (red, green, blue, 0)
            elif neighbors >= 7:
                red, green, blue, _ = pixels[x, y]
                pixels[x, y] = (red, green, blue, 255)


def _ref_dominant(opaque, detail_bias=False):
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


# --- equivalence tests ------------------------------------------------------


def test_grid_uniformity_byte_identical_to_reference():
    img = _synthetic_keyed()
    for pitch in [(7.0, 7.0), (7.82, 8.28), (6.17, 6.32), (5.0, 9.0)]:
        for phase in [(0.0, 0.0), (1.3, 2.9), (3.0, 3.0), (pitch[0] * 5 / 8, pitch[1] * 3 / 8)]:
            ref = _ref_grid_uniformity(img, pitch, phase)
            got = extract._grid_uniformity(img, pitch, phase)
            # exact float equality — determinism contract, not approximate
            assert got == ref, f"pitch={pitch} phase={phase}: {got!r} != {ref!r}"


def test_best_phase_byte_identical_to_reference():
    img = _synthetic_keyed()
    for pitch in [(7.0, 7.0), (7.82, 8.28), (6.17, 6.32)]:
        # reference argmin over the same 8x8 grid, same tie-break (strict <)
        best = (0.0, 0.0)
        best_score = None
        for i in range(8):
            for j in range(8):
                phase = (pitch[0] * i / 8, pitch[1] * j / 8)
                score = _ref_grid_uniformity(img, pitch, phase)
                if best_score is None or score < best_score:
                    best_score = score
                    best = phase
        assert extract._best_phase(img, pitch) == best, f"pitch={pitch}"


def test_matte_and_flood_byte_identical_to_reference():
    img = _synthetic_keyed()
    key = (0, 255, 0)
    ref = _ref_matte(img, key)
    got, frac = extract._matte_ycc(img, key)
    assert got.tobytes() == ref.tobytes()
    # frac is derived from the matted alpha histogram — must match too
    opaque = sum(ref.getchannel("A").histogram()[extract._YCC_ALPHA_EMPTY + 1:])
    assert frac == opaque / (img.width * img.height)


def test_cleanup_alpha_byte_identical_to_reference():
    img = _synthetic_keyed()
    got, _ = extract._matte_ycc(img, (0, 255, 0))
    ref = got.copy()
    extract._cleanup_alpha_ycc(got)
    _ref_cleanup(ref)
    assert got.tobytes() == ref.tobytes()


def test_dominant_block_color_byte_identical_to_reference():
    # deterministic pixel sets covering single, two-cluster, and detail-bias cases
    cases = [
        [(200, 30, 40)],
        [(10, 10, 10), (240, 240, 240), (12, 8, 9), (250, 249, 248), (11, 11, 12)],
        [(5, 5, 5)] * 3 + [(250, 250, 250)] * 4,
        [(i * 7 % 256, i * 13 % 256, i * 29 % 256) for i in range(50)],
    ]
    for opaque in cases:
        for bias in (False, True):
            assert extract._dominant_block_color(list(opaque), bias) == _ref_dominant(list(opaque), bias)
