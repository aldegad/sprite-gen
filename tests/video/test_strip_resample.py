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

from keyed_figure import STRANDS, colour_outside, figure, neighbour_ranges, scaled_size, strand
from sprite_gen.util import resample
from sprite_gen.video import loop

SCALES = (0.5, 0.9662, 1.0256, 1.5)


@pytest.mark.parametrize("scale", SCALES)
def test_cell_colour_is_always_a_mix_of_the_colours_under_it(scale: float) -> None:
    src = figure()
    size = scaled_size(src, scale)
    assert colour_outside(src, resample.resize_cell(src, size)) == 0
    # the filter it replaces put colour there that nothing under it had
    assert colour_outside(src, src.resize(size, Image.Resampling.LANCZOS)) > 100


@pytest.mark.parametrize("scale", SCALES)
def test_cell_coverage_has_no_halo_and_no_hole(scale: float) -> None:
    src = figure(shift=0.37)
    size = scaled_size(src, scale)
    _, _, amin, amax = neighbour_ranges(src, size)
    a = np.asarray(resample.resize_cell(src, size))[..., 3].astype(int)
    assert ((a >= amin) & (a <= amax)).all()
    # LANCZOS dips inside the silhouette and spills past it
    lz = np.asarray(src.resize(size, Image.Resampling.LANCZOS))[..., 3].astype(int)
    assert np.count_nonzero((lz < amin) | (lz > amax)) > 20


def _strand_peaks(image: Image.Image, scale_xy: tuple[float, float], shift: float) -> list[float]:
    a = np.asarray(image)[..., 3] / 255.0
    h, w = a.shape
    peaks = []
    for deg, _, length in STRANDS:
        x0, y0, dx, dy = strand(deg)
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
        src = figure(shift=shift)
        size = scaled_size(src, scale)
        sxy = (size[0] / src.width, size[1] / src.height)
        new = _strand_peaks(resample.resize_cell(src, size), sxy, shift)
        old = _strand_peaks(src.resize(size, Image.Resampling.LANCZOS), sxy, shift)
        assert min(n - o for n, o in zip(new, old)) >= -0.03, (shift, new, old)


def test_a_cell_at_its_own_size_is_untouched() -> None:
    src = figure()
    out = resample.resize_cell(src, src.size)
    assert out is not src and out.tobytes() == src.tobytes()


def test_mix_windows_hold_every_pixel_the_colour_filter_mixes() -> None:
    # Every pair of lengths up to 48, shrinking and enlarging, and a few long ones: one impulse per
    # source column, as the rows of one picture, scaled along the columns only.
    pairs = [(n_src, n_out) for n_src in range(2, 49) for n_out in range(1, 2 * n_src + 1)]
    for n_src, n_out in [*pairs, (720, 256), (256, 720), (997, 431), (431, 997)]:
        (_, _), (lo, hi) = resample.mix_windows((n_src, 1), (n_out, 1))
        impulses = Image.fromarray(np.eye(n_src, dtype=np.float32))
        response = np.asarray(impulses.resize((n_out, n_src), Image.Resampling.HAMMING))
        x, u = np.nonzero(response > 1e-6)
        assert ((lo[u] <= x) & (x <= hi[u])).all(), (n_src, n_out)


def test_a_mix_can_carry_more_key_hue_than_the_colours_under_it() -> None:
    # Each channel of a cell stays inside the colours under it. The key hue's excess (green:
    # G - max(R, B)) does not: a colour led by red mixed with one led by blue is led by neither
    # as far. `resize_cell` leaves the mix as it is; it knows no key and caps nothing.
    def over_the_bar(image: Image.Image) -> int:
        a = np.asarray(image).astype(int)
        return int(np.count_nonzero((a[..., 3] > 0) & (a[..., 1] - np.maximum(a[..., 0], a[..., 2]) > 8)))

    def stripes(one: tuple[int, ...], other: tuple[int, ...]) -> Image.Image:
        image = Image.new("RGBA", (60, 40), one)
        for x in range(5, 60, 10):
            image.paste(other, (x, 0, x + 5, 40))
        return image

    crossed = stripes((120, 114, 90, 255), (90, 114, 120, 255))  # led by red, led by blue
    same = stripes((120, 114, 90, 255), (150, 130, 60, 255))  # both led by red
    assert over_the_bar(crossed) == over_the_bar(same) == 0
    for size in ((30, 20), (58, 39), (61, 41), (90, 60)):
        cell = resample.resize_cell(crossed, size)
        assert over_the_bar(cell) > 0, size
        assert colour_outside(crossed, cell) == 0, size
        assert over_the_bar(resample.resize_cell(same, size)) == 0, size
    assert over_the_bar(resample.resize_cell(crossed, crossed.size)) == 0


@pytest.mark.parametrize("anchor", ("none", "feet"))
def test_build_strip_scales_every_cell_this_way(monkeypatch, anchor: str) -> None:
    frames = [figure(shift=k * 0.5) for k in range(4)]
    calls: list[tuple[int, int]] = []
    resize_cell = loop.resize_cell

    def spy(image: Image.Image, size: tuple[int, int]) -> Image.Image:
        calls.append(tuple(size))
        return resize_cell(image, size)

    monkeypatch.setattr(loop, "resize_cell", spy)
    _, meta = loop.build_strip(frames, cycle_seconds=0.5, body_height=150, anchor=anchor)
    assert meta["scale"] != 1.0
    assert calls == [(meta["w"], meta["h"])] * meta["frames"]
