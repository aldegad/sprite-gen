# SPDX-License-Identifier: Apache-2.0
"""A synthetic keyed figure and the "no colour the source did not have" count.

The figure is area-sampled: a dark-outlined body (a skin half and a white half), a light rim
outside part of the outline, green-tinted ink along another part, a 1 px line across the body,
a hair cap with strands 0.5-1.4 px wide — the edge a keyer leaves. Shared by the tests of every
place the engine scales or maps a keyed picture (`sprite_gen.util.resample.resize_cell`,
`transform_cell`).
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

SS = 4  # supersamples per pixel side
OUTLINE = (28, 18, 16)
INK_GREEN = (21, 27, 2)
SKIN = (238, 206, 178)
SHIRT = (245, 245, 240)
HAIR = (170, 52, 36)
RIM = (235, 228, 196)
BODY = (80.0, 120.0, 46.0, 2.2)  # centre x, centre y, radius, outline width
CAP = (80.0, 58.0, 30.0, 1.6)
STRANDS = [(-150, 0.5, 18), (-125, 0.7, 20), (-100, 1.0, 18), (-60, 1.4, 16), (-35, 0.8, 18)]  # degrees, width, length


def strand(deg: float) -> tuple[float, float, float, float]:
    t = math.radians(deg)
    return CAP[0] + (CAP[2] - 1) * math.cos(t), CAP[1] + (CAP[2] - 1) * math.sin(t), math.cos(t), math.sin(t)


def figure(shift: float = 0.0, width: int = 160, height: int = 180) -> Image.Image:
    ys, xs = np.mgrid[0 : height * SS, 0 : width * SS]
    X = (xs + 0.5) / SS - shift
    Y = (ys + 0.5) / SS - shift
    rgb = np.zeros(X.shape + (3,))
    a = np.zeros(X.shape)

    def paint(mask: np.ndarray, colour: tuple[int, int, int]) -> None:
        rgb[mask] = colour
        a[mask] = 1.0

    cx, cy, r, t = BODY
    rb = np.hypot(X - cx, Y - cy)
    ang = np.degrees(np.arctan2(Y - cy, X - cx))
    paint((rb < r + t + 0.6) & (ang > -40) & (ang < 50), RIM)
    paint(rb < r + t, OUTLINE)
    paint((rb < r + t) & (ang > 100) & (ang < 170), INK_GREEN)
    paint((rb < r) & (Y < cy), SKIN)
    paint((rb < r) & (Y >= cy), SHIRT)
    paint((rb < r) & (np.abs(Y - (cy + 18.3)) < 0.5), OUTLINE)
    qx, qy, qr, qt = CAP
    rc = np.hypot(X - qx, Y - qy)
    paint((rc < qr + qt) & (Y < qy + 2), OUTLINE)
    paint((rc < qr) & (Y < qy), HAIR)
    for deg, thick, length in STRANDS:
        x0, y0, dx, dy = strand(deg)
        along = np.clip((X - x0) * dx + (Y - y0) * dy, 0, length)
        paint(np.hypot(X - (x0 + along * dx), Y - (y0 + along * dy)) < thick / 2, HAIR)
    A = a.reshape(height, SS, width, SS).mean(axis=(1, 3))
    P = (rgb * a[..., None]).reshape(height, SS, width, SS, 3).mean(axis=(1, 3))
    C = np.where(A[..., None] > 0, P / np.maximum(A[..., None], 1e-9), 0)
    return Image.fromarray(np.dstack([C.round(), (A * 255).round()]).astype(np.uint8), "RGBA")


def scaled_size(image: Image.Image, scale: float) -> tuple[int, int]:
    return round(image.width * scale), round(image.height * scale)


def _windows(n_src: int, n_out: int) -> list[range]:
    """Source pixels whose centre lies within the Hamming filter's reach of each output pixel's centre."""
    ratio = n_src / n_out
    reach = max(1.0, ratio)
    out = []
    for k in range(n_out):
        c = (k + 0.5) * ratio
        out.append(range(max(0, math.ceil(c - reach - 0.5)), min(n_src - 1, math.floor(c + reach - 0.5)) + 1))
    return out


def neighbour_ranges(src: Image.Image, size: tuple[int, int]):
    s = np.asarray(src).astype(int)
    rows, cols = _windows(src.height, size[1]), _windows(src.width, size[0])
    lo = np.full((size[1], size[0], 3), 256)
    hi = np.full((size[1], size[0], 3), -1)
    amin = np.full((size[1], size[0]), 255)
    amax = np.zeros((size[1], size[0]), int)
    for v, ry in enumerate(rows):
        for u, rx in enumerate(cols):
            block = s[ry.start : ry.stop, rx.start : rx.stop].reshape(-1, 4)
            amin[v, u], amax[v, u] = block[:, 3].min(), block[:, 3].max()
            seen = block[block[:, 3] > 0, :3]
            if len(seen):
                lo[v, u], hi[v, u] = seen.min(axis=0), seen.max(axis=0)
    return lo, hi, amin, amax


def colour_outside(src: Image.Image, out: Image.Image) -> int:
    """Visible output pixels (alpha >= 38) whose colour is more than one level outside the range of
    the visible source pixels under them."""
    lo, hi, _, _ = neighbour_ranges(src, out.size)
    o = np.asarray(out).astype(int)
    seen = o[..., 3] >= 38
    outside = ((o[..., :3] < lo - 1) | (o[..., :3] > hi + 1)).any(axis=-1)
    return int(np.count_nonzero(seen & outside))


def colour_outside_mapped(src: Image.Image, out: Image.Image, inverse: tuple[float, ...]) -> int:
    """`colour_outside` for an affine map (Pillow's AFFINE data, output -> input): the colour range
    of the visible source pixels among the 2 x 2 around each output pixel's mapped centre."""
    s = np.asarray(src).astype(int)
    pad = np.zeros((s.shape[0] + 2, s.shape[1] + 2, 4), int)
    pad[1:-1, 1:-1] = s
    a, b, c, d, e, f = inverse
    v, u = np.mgrid[0 : out.height, 0 : out.width] + 0.5
    x0 = np.floor(a * u + b * v + c - 0.5).astype(int)
    y0 = np.floor(d * u + e * v + f - 0.5).astype(int)
    lo = np.full((out.height, out.width, 3), 256)
    hi = np.full((out.height, out.width, 3), -1)
    for dy in (0, 1):
        for dx in (0, 1):
            px = pad[np.clip(y0 + dy, -1, s.shape[0]) + 1, np.clip(x0 + dx, -1, s.shape[1]) + 1]
            seen = (px[..., 3] > 0)[..., None]
            lo = np.where(seen, np.minimum(lo, px[..., :3]), lo)
            hi = np.where(seen, np.maximum(hi, px[..., :3]), hi)
    o = np.asarray(out).astype(int)
    outside = ((o[..., :3] < lo - 1) | (o[..., :3] > hi + 1)).any(axis=-1)
    return int(np.count_nonzero((o[..., 3] >= 38) & outside))
