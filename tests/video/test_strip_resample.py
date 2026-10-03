"""`video-loop` cells are scaled without a ring at the edge.

A keyed frame's edge holds what the keyer left there: a light rim outside a dark outline,
ink carrying a little of the key's green, half-covered strands. LANCZOS over premultiplied
RGBA weighs the colours across that edge against each other and divides by the edge's low
coverage, so a scaled cell carried colour that no source pixel near it had: a lighter rim,
greener ink. `resize_cell` scales coverage and colour apart: coverage keeps LANCZOS's edge,
held to the range of the coverage under it; colour is a Hamming mix of premultiplied colour.

The figure is synthetic and area-sampled: a dark-outlined body (a skin half and a white
half), a light rim outside part of the outline, green-tinted ink along another part, a
1 px line across the body, a hair cap with strands 0.5-1.4 px wide.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import loop

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
SCALES = (0.5, 0.9662, 1.0256, 1.5)


def _strand(deg: float) -> tuple[float, float, float, float]:
    t = math.radians(deg)
    return CAP[0] + (CAP[2] - 1) * math.cos(t), CAP[1] + (CAP[2] - 1) * math.sin(t), math.cos(t), math.sin(t)


def _figure(shift: float = 0.0, width: int = 160, height: int = 180) -> Image.Image:
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
        x0, y0, dx, dy = _strand(deg)
        along = np.clip((X - x0) * dx + (Y - y0) * dy, 0, length)
        paint(np.hypot(X - (x0 + along * dx), Y - (y0 + along * dy)) < thick / 2, HAIR)
    A = a.reshape(height, SS, width, SS).mean(axis=(1, 3))
    P = (rgb * a[..., None]).reshape(height, SS, width, SS, 3).mean(axis=(1, 3))
    C = np.where(A[..., None] > 0, P / np.maximum(A[..., None], 1e-9), 0)
    return Image.fromarray(np.dstack([C.round(), (A * 255).round()]).astype(np.uint8), "RGBA")


def _size(image: Image.Image, scale: float) -> tuple[int, int]:
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


def _neighbour_ranges(src: Image.Image, size: tuple[int, int]):
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


def _colour_outside(src: Image.Image, out: Image.Image) -> int:
    """Visible output pixels (alpha >= 38) whose colour is more than one level outside the range of
    the visible source pixels under them."""
    lo, hi, _, _ = _neighbour_ranges(src, out.size)
    o = np.asarray(out).astype(int)
    seen = o[..., 3] >= 38
    outside = ((o[..., :3] < lo - 1) | (o[..., :3] > hi + 1)).any(axis=-1)
    return int(np.count_nonzero(seen & outside))


@pytest.mark.parametrize("scale", SCALES)
def test_cell_colour_is_always_a_mix_of_the_colours_under_it(scale: float) -> None:
    src = _figure()
    size = _size(src, scale)
    assert _colour_outside(src, loop.resize_cell(src, size)) == 0
    # the filter it replaces put colour there that nothing under it had
    assert _colour_outside(src, src.resize(size, Image.Resampling.LANCZOS)) > 100


@pytest.mark.parametrize("scale", SCALES)
def test_cell_coverage_has_no_halo_and_no_hole(scale: float) -> None:
    src = _figure(shift=0.37)
    size = _size(src, scale)
    _, _, amin, amax = _neighbour_ranges(src, size)
    a = np.asarray(loop.resize_cell(src, size))[..., 3].astype(int)
    assert ((a >= amin) & (a <= amax)).all()
    # LANCZOS dips inside the silhouette and spills past it
    lz = np.asarray(src.resize(size, Image.Resampling.LANCZOS))[..., 3].astype(int)
    assert np.count_nonzero((lz < amin) | (lz > amax)) > 20


def _strand_peaks(image: Image.Image, scale_xy: tuple[float, float], shift: float) -> list[float]:
    a = np.asarray(image)[..., 3] / 255.0
    h, w = a.shape
    peaks = []
    for deg, _, length in STRANDS:
        x0, y0, dx, dy = _strand(deg)
        found = []
        for t in np.arange(0.3 * length, 0.9 * length, 0.5):
            px, py = (x0 + t * dx + shift) * scale_xy[0], (y0 + t * dy + shift) * scale_xy[1]
            near = [a[v, u] for v in range(int(py) - 2, int(py) + 3) for u in range(int(px) - 2, int(px) + 3)
                    if 0 <= u < w and 0 <= v < h and math.hypot(u + 0.5 - px, v + 0.5 - py) <= 1.0]
            found.append(max(near))
        peaks.append(float(np.mean(found)))
    return peaks


@pytest.mark.parametrize("scale", SCALES)
def test_strands_keep_the_coverage_lanczos_gave_them(scale: float) -> None:
    for shift in (0.0, 0.25, 0.5, 0.75):
        src = _figure(shift=shift)
        size = _size(src, scale)
        sxy = (size[0] / src.width, size[1] / src.height)
        new = _strand_peaks(loop.resize_cell(src, size), sxy, shift)
        old = _strand_peaks(src.resize(size, Image.Resampling.LANCZOS), sxy, shift)
        assert min(n - o for n, o in zip(new, old)) >= -0.03, (shift, new, old)


def test_a_cell_at_its_own_size_is_untouched() -> None:
    src = _figure()
    out = loop.resize_cell(src, src.size)
    assert out is not src and out.tobytes() == src.tobytes()


@pytest.mark.parametrize("scale", (0.37, 0.5, 0.9662, 1.0256, 1.5, 2.3))
def test_coverage_clamp_reads_every_pixel_the_colour_filter_mixes(scale: float) -> None:
    n_src = 41
    n_out = round(n_src * scale)
    lo, hi = loop._support_bounds(n_src, n_out, 1.0)
    for x in range(n_src):
        impulse = np.zeros((1, n_src), np.float32)
        impulse[0, x] = 1.0
        response = np.asarray(Image.fromarray(impulse).resize((n_out, 1), Image.Resampling.HAMMING))[0]
        for u in np.nonzero(response > 1e-6)[0]:
            assert lo[u] <= x <= hi[u], (scale, x, u, lo[u], hi[u])


@pytest.mark.parametrize("anchor", ("none", "feet"))
def test_build_strip_scales_every_cell_this_way(monkeypatch, anchor: str) -> None:
    frames = [_figure(shift=k * 0.5) for k in range(4)]
    calls: list[tuple[int, int]] = []
    resize_cell = loop.resize_cell

    def spy(image: Image.Image, size: tuple[int, int]) -> Image.Image:
        calls.append(tuple(size))
        return resize_cell(image, size)

    monkeypatch.setattr(loop, "resize_cell", spy)
    _, meta = loop.build_strip(frames, cycle_seconds=0.5, body_height=150, anchor=anchor)
    assert meta["scale"] != 1.0
    assert calls == [(meta["w"], meta["h"])] * meta["frames"]
