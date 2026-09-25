"""`decontam` keeps gold and yellow as they are (synthetic fixture, known coverage and colour).

Gold sits between red and green: a gold a little yellower or darker than the palette's own
reads as a warmer palette gold with some green mixed in. Before, the pass took that reading,
so gold edges and small gold drops came out orange and partly see-through. The RGB matte
(`cutout`) makes it worse on its own: it scores key tint on the channel average, which calls
yellow green, so it unmixes gold as if it were a blend. The scene: textured gold strokes
with a crimson outline, drops of a darker shade of that gold, and drops of a yellower gold
the strokes never show, drawn at 4x and box-reduced so every edge is a blend, on the pure
and on a painted green key.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from sprite_gen.frames.cutout import extract_route
from sprite_gen.frames.extract import _grow_chebyshev, remove_chroma_background_ycbcr

GREEN = (0, 255, 0)
SIZE, SCALE = 160, 4
EDGE_BAND = 6  # the pass's band: truth pixels this close to the background are the ones it may touch


def _hsv(hue: np.ndarray, sat: float, val: np.ndarray) -> np.ndarray:
    """Vectorised HSV (hue in degrees) to RGB 0-255."""
    h = (hue % 360) / 60.0
    c = val * sat
    x = c * (1 - np.abs(h % 2 - 1))
    zero = np.zeros_like(h)
    sector = np.floor(h).astype(int) % 6
    rgb = np.select([sector[..., None] == k for k in range(6)],
                    [np.stack(v, -1) for v in ((c, x, zero), (x, c, zero), (zero, c, x), (zero, x, c), (x, zero, c), (c, zero, x))])
    return (rgb + (val - c)[..., None]) * 255


def _scene(key: tuple[int, int, int]) -> tuple[Image.Image, np.ndarray, np.ndarray, np.ndarray]:
    """(raw still, true coverage, true colour, gold mask) at SIZE x SIZE."""
    n = SIZE * SCALE
    yy, xx = np.mgrid[0:n, 0:n] / SCALE
    cover = np.zeros((n, n), dtype=bool)
    colour = np.zeros((n, n, 3))
    gold = np.zeros((n, n), dtype=bool)
    stroke = ((np.abs(yy - 45) < 16) & (xx > 12) & (xx < 148)) | ((np.abs(xx - 42) < 14) & (yy > 30) & (yy < 150))
    outline = stroke.copy()
    for _ in range(3 * SCALE):
        outline = _grow_chebyshev(outline)
    outline &= ~stroke
    cover |= outline
    colour[outline] = (140, 12, 14)
    # a dry-brush gold: hue 41-48 degrees, value 0.80-1.00
    hue = 44.5 + 3.5 * np.sin(xx / 4.7) * np.cos(yy / 6.3)
    val = 0.9 + 0.1 * np.sin(xx / 2.9 + yy / 3.7)
    cover |= stroke
    colour[stroke] = _hsv(hue, 0.76, val)[stroke]
    gold |= stroke
    # drops the strokes' interior never shows: a darker shade of their gold, and a yellower gold
    for cy, cx, radius, drop_hue, shade in ((100, 90, 4.5, 47.0, 0.80), (120, 118, 5.5, 46.0, 0.84), (140, 95, 4.0, 48.0, 0.82),
                                           (98, 130, 5.0, 54.0, 0.97), (130, 142, 6.0, 55.0, 0.95), (148, 120, 4.5, 53.0, 0.98)):
        drop = (yy - cy) ** 2 + (xx - cx) ** 2 <= radius ** 2
        cover |= drop
        colour[drop] = _hsv(np.full(int(drop.sum()), drop_hue), 0.74, np.full(int(drop.sum()), shade))
        gold |= drop
    big = np.where(cover[..., None], colour, np.asarray(key, dtype=np.float64))
    raw = big.reshape(SIZE, SCALE, SIZE, SCALE, 3).mean((1, 3))
    coverage = cover.reshape(SIZE, SCALE, SIZE, SCALE).mean((1, 3))
    weight = np.maximum(cover.reshape(SIZE, SCALE, SIZE, SCALE).sum((1, 3)), 1)[..., None]
    true_colour = (colour * cover[..., None]).reshape(SIZE, SCALE, SIZE, SCALE, 3).sum((1, 3)) / weight
    gold_share = gold.reshape(SIZE, SCALE, SIZE, SCALE).mean((1, 3))
    image = Image.fromarray(np.round(raw).astype(np.uint8)).convert("RGBA")
    return image, coverage, true_colour, gold_share >= 0.999


def _hue(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb, dtype=np.float64)
    top, low = rgb.max(-1), rgb.min(-1)
    span = np.maximum(top - low, 1e-9)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    hue = np.where(top == r, (g - b) / span % 6, np.where(top == g, (b - r) / span + 2, (r - g) / span + 4))
    return hue * 60.0


def _solid_gold_at_the_edge(coverage: np.ndarray, is_gold: np.ndarray) -> np.ndarray:
    """Fully covered gold pixels within the pass's band of the background."""
    near = coverage < 0.02
    for _ in range(EDGE_BAND):
        near = _grow_chebyshev(near)
    return is_gold & (coverage >= 0.999) & near


def _scores(out: Image.Image, target: np.ndarray, true_colour: np.ndarray) -> tuple[float, np.ndarray]:
    """(share of target pixels below alpha 250, signed hue change of every target pixel)."""
    rgba = np.asarray(out.convert("RGBA")).astype(np.float64)
    loss = float((rgba[target][:, 3] < 250).mean())
    shift = (_hue(rgba[target][:, :3]) - _hue(true_colour[target]) + 180) % 360 - 180
    return loss, shift


MATTES = {
    "ycbcr (gen)": lambda image, **kw: remove_chroma_background_ycbcr(image, GREEN, [], **kw),
    "rgb (cutout)": lambda image, **kw: extract_route(image, "green", **kw)[0],
}


@pytest.mark.parametrize("key", [GREEN, (26, 217, 30)], ids=["pure key", "painted key"])
@pytest.mark.parametrize("matte", list(MATTES))
def test_decontam_leaves_gold_solid_and_gold(key: tuple[int, int, int], matte: str) -> None:
    image, coverage, true_colour, is_gold = _scene(key)
    target = _solid_gold_at_the_edge(coverage, is_gold)
    assert target.sum() > 400
    loss, shift = _scores(MATTES[matte](image, decontam="palette"), target, true_colour)
    assert loss <= 0.01, f"{loss:.1%} of solid gold went see-through"
    assert abs(float(shift.mean())) <= 1.0, f"gold hue moved {shift.mean():.2f} degrees on average"
    assert float(np.percentile(shift, 5)) >= -2.0, f"gold turned orange: 5th percentile {np.percentile(shift, 5):.2f} degrees"


def test_the_rgb_matte_alone_turns_gold_orange_and_the_pass_undoes_it() -> None:
    image, coverage, true_colour, is_gold = _scene(GREEN)
    target = _solid_gold_at_the_edge(coverage, is_gold)
    matte_loss, matte_shift = _scores(MATTES["rgb (cutout)"](image), target, true_colour)
    fixed_loss, fixed_shift = _scores(MATTES["rgb (cutout)"](image, decontam="palette"), target, true_colour)
    # the channel-average tint reads yellow as green: the matte itself unmixes gold
    assert matte_loss >= 0.2 and float(np.percentile(matte_shift, 5)) <= -5.0
    assert fixed_loss <= 0.01 and float(np.percentile(fixed_shift, 5)) >= -2.0


@pytest.mark.parametrize("matte", list(MATTES))
def test_the_green_blend_around_gold_is_still_cleaned(matte: str) -> None:
    """Keeping gold must not stop the pass: the green fringe the edge blends leave is still removed."""
    image, coverage, true_colour, is_gold = _scene(GREEN)
    fringe = (coverage > 0.1) & (coverage < 0.9)

    def visible_green(out: Image.Image) -> int:
        rgba = np.asarray(out.convert("RGBA")).astype(np.int32)
        excess = rgba[..., 1] - np.maximum(rgba[..., 0], rgba[..., 2])
        return int((fringe & (rgba[..., 3] > 25) & (excess > 8)).sum())

    before = visible_green(MATTES[matte](image))
    after = visible_green(MATTES[matte](image, decontam="palette"))
    assert after <= before // 4, (before, after)


def test_only_the_still_fit_takes_the_edge_colour_as_material() -> None:
    """The video fit keeps its decisions: a decoded frame's chroma is blurred, so an edge colour proves nothing."""
    image, *_ = _scene(GREEN)
    _, still = extract_route(image, "green", decontam="palette")
    _, video = extract_route(image, "green", decontam="palette", decontam_fit="video")
    assert still["decontam"]["restored_px"] > 0
    assert video["decontam"]["restored_px"] == 0
