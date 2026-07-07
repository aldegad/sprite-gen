# SPDX-License-Identifier: Apache-2.0
"""Regression tests for chroma-key alpha cleanup and auto key selection.

These guard three ways the extractor used to silently destroy real subject
colors:

1. ``remove_chroma_background`` ran a "neutralize key tint" pass on every pixel
   whose channels leaned toward the key's channels, *regardless of color
   distance* — so a saturated red/orange/blue subject was clamped toward
   olive/grey under a magenta key even though it sat >200 away from magenta.
   The destructive pass is gone; near-key antialias fringe is still removed.
2. ``choose_chroma_key`` ranked candidates by the 1st-percentile distance to
   subject pixels, which discards sub-1% features (eyes, gems, ear lamps): the
   auto key could look safe while its nearest subject pixel was still inside the
   extraction erase radius and would be deleted.
3. The fringe cut erased every pixel inside the fringe color band *anywhere in
   the image* — hot pink (~129 from magenta) and purple (~153) subjects fell in
   the band and were bleached wholesale (solvell seed_flower_pink /
   herb_plant_star_bloom, 2026-07-07). Fringe is boundary antialiasing, so the
   cut is now limited to pixels spatially adjacent to the keyed-out background
   (peeled at most ``fringe_reach`` layers); interior subject pixels survive.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


extract = _load("extract_sprite_row_frames")
prepare = _load("prepare_sprite_run")

MAGENTA = (255, 0, 255)
# extract_sprite_row_frames.py main() defaults
KEY_THRESHOLD = 96.0
FRINGE_THRESHOLD = 180.0
FRINGE_DELTA = 18.0
FRINGE_REACH = 2

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Real accident colors: dominant subject pixels the old position-blind fringe
# cut erased (solvell raws, magenta key). Both sit inside the fringe band.
HOT_PINK = (250, 77, 150)  # seed_flower_pink petals, ~129 from magenta
PURPLE = (213, 112, 246)  # herb_plant_star_bloom petals, ~153 from magenta


def _key(pixel: tuple[int, int, int], chroma_key=MAGENTA):
    image = Image.new("RGBA", (1, 1), (*pixel, 255))
    out = extract.remove_chroma_background(image, chroma_key, KEY_THRESHOLD, FRINGE_THRESHOLD, FRINGE_DELTA, FRINGE_REACH)
    return out.getpixel((0, 0))


def _clean(image: Image.Image, chroma_key=MAGENTA) -> Image.Image:
    return extract.remove_chroma_background(image, chroma_key, KEY_THRESHOLD, FRINGE_THRESHOLD, FRINGE_DELTA, FRINGE_REACH)


def test_despill_preserves_far_subject_colors() -> None:
    # All of these sit >200 color-distance away from magenta: they are the
    # subject, not key fringe, and must survive completely untouched.
    for pixel in [(196, 54, 38), (224, 96, 40), (208, 44, 40), (40, 70, 200)]:
        assert extract.color_distance(pixel, MAGENTA) > FRINGE_THRESHOLD
        assert _key(pixel) == (*pixel, 255), f"{pixel} was mangled by despill"


def test_exact_key_is_removed_everywhere() -> None:
    assert _key(MAGENTA)[3] == 0


FRINGE = (147, 90, 157)  # magenta blended with a green subject edge


def test_boundary_fringe_is_still_removed() -> None:
    # Green subject on magenta background with a one-pixel antialias fringe
    # ring between them: the background and the fringe must go, the subject
    # must stay.
    assert extract.color_distance(FRINGE, MAGENTA) <= FRINGE_THRESHOLD
    assert extract.key_tint_score(FRINGE, MAGENTA) >= FRINGE_DELTA
    green = (60, 170, 70)
    image = Image.new("RGBA", (16, 16), (*MAGENTA, 255))
    pixels = image.load()
    for y in range(4, 12):
        for x in range(4, 12):
            pixels[x, y] = (*green, 255)
    for x in range(3, 13):  # fringe ring just outside the subject block
        for y in (3, 12):
            pixels[x, y] = (*FRINGE, 255)
            pixels[y, x] = (*FRINGE, 255)
    out = _clean(image).load()
    assert out[0, 0][3] == 0  # background gone
    assert out[3, 3][3] == 0 and out[12, 8][3] == 0  # fringe ring gone
    assert out[8, 8] == (*green, 255)  # subject intact


def test_isolated_fringe_band_pixel_is_subject_not_fringe() -> None:
    # The same fringe-band color *inside* a subject, nowhere near background,
    # is a real material color and must survive (this is what the old
    # position-blind cut destroyed).
    green = (60, 170, 70)
    image = Image.new("RGBA", (16, 16), (*green, 255))
    image.load()[8, 8] = (*FRINGE, 255)
    out = _clean(image).load()
    assert out[8, 8] == (*FRINGE, 255)


def test_key_tinted_subject_interior_survives() -> None:
    # Hot pink / purple blocks on a magenta background: only the edge layers
    # within FRINGE_REACH of the keyed-out background may be trimmed; the
    # interior must keep its exact color.
    for subject in (HOT_PINK, PURPLE):
        distance = extract.color_distance(subject, MAGENTA)
        assert KEY_THRESHOLD < distance <= FRINGE_THRESHOLD  # inside the trap band
        assert extract.key_tint_score(subject, MAGENTA) >= FRINGE_DELTA
        image = Image.new("RGBA", (32, 32), (*MAGENTA, 255))
        pixels = image.load()
        for y in range(8, 24):
            for x in range(8, 24):
                pixels[x, y] = (*subject, 255)
        out = _clean(image).load()
        assert out[0, 0][3] == 0  # background gone
        # Edge peel is bounded by FRINGE_REACH: everything deeper survives.
        for offset in range(8 + FRINGE_REACH, 24 - FRINGE_REACH):
            assert out[offset, offset] == (*subject, 255), f"{subject} interior erased at {offset}"


def _subject_band_survival(path: Path) -> float:
    """Survival ratio of fringe-band subject pixels after cleanup."""
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    source = image.load()
    band = []
    for y in range(image.height):
        for x in range(image.width):
            color = source[x, y][:3]
            distance = extract.color_distance(color, MAGENTA)
            if KEY_THRESHOLD < distance <= FRINGE_THRESHOLD and extract.key_tint_score(color, MAGENTA) >= FRINGE_DELTA:
                band.append((x, y))
    assert band, f"{path.name} has no fringe-band pixels; fixture is wrong"
    out = _clean(image).load()
    survived = sum(1 for x, y in band if out[x, y][3] != 0)
    return survived / len(band)


def test_accident_raws_keep_pink_and_purple_material() -> None:
    # 1/8-size NEAREST copies of the real solvell accident raws (magenta key):
    # a hot-pink seed packet and a purple star bloom. The old cut erased 100%
    # of their fringe-band subject pixels; boundary-limited despill must keep
    # nearly all of them (only genuine key-blend edge pixels may go).
    for name in ("seed_flower_pink.png", "herb_plant_star_bloom.png"):
        ratio = _subject_band_survival(FIXTURES / "accident" / name)
        assert ratio >= 0.90, f"{name}: only {ratio:.1%} of key-tinted subject pixels survived"


# A small, cyan-leaning teal feature: ~55 from the cyan key, so cyan would erase
# it, yet it is far enough from magenta/green/blue for those to keep it.
EYE = (0, 250, 200)


def _reference_image(path: Path) -> None:
    # 128px so PIL never downscales the sample (deterministic across platforms).
    image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(128):
        for x in range(128):
            if (x - 64) ** 2 + (y - 64) ** 2 < 58 ** 2:
                pixels[x, y] = (196, 54, 38, 255)  # red-orange body (the bulk hue)
    # Two tiny eyes: well under 1% of the subject, so the 1st-percentile ranking
    # ignores them and a naive selector happily picks the cyan key that erases them.
    for cx, cy in ((50, 52), (78, 52)):
        for dy in range(-1, 2):
            for dx in range(-2, 3):
                pixels[cx + dx, cy + dy] = (*EYE, 255)
    image.save(path)


def test_auto_key_avoids_erasing_small_feature(tmp_path: Path) -> None:
    ref = tmp_path / "base.png"
    _reference_image(ref)

    opaque = eyes = 0
    with Image.open(ref) as opened:
        data = opened.convert("RGBA").load()
        for y in range(128):
            for x in range(128):
                r, g, b, a = data[x, y]
                if a <= 16:
                    continue
                opaque += 1
                if (r, g, b) == EYE:
                    eyes += 1
    assert opaque and eyes / opaque < 0.01  # the eyes are a sub-1% feature

    pixels = prepare.sampled_reference_pixels(ref)
    cyan = prepare.parse_hex_color("#00FFFF")
    cyan_min = min(prepare.color_distance(cyan, pixel) for pixel in pixels)
    # cyan would win the raw 1st-percentile ranking yet erase the eyes: that is
    # exactly the trap the guard exists for.
    assert cyan_min <= prepare.MIN_SUBJECT_KEY_DISTANCE

    result = prepare.choose_chroma_key(ref, "auto")
    assert result["selection"] == "auto"
    assert result["name"] != "cyan"
    # The chosen key clears every subject pixel, including the tiny eyes.
    assert result["min_subject_distance"] > prepare.MIN_SUBJECT_KEY_DISTANCE
    assert "warning" not in result


def test_auto_key_warns_when_no_candidate_is_safe(tmp_path: Path) -> None:
    # A subject that parks saturated pixels next to every candidate key: no safe
    # choice exists, so the selector falls back to the ranking *and* warns.
    ref = tmp_path / "rainbow.png"
    image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    pixels = image.load()
    near_keys = [(235, 30, 235), (30, 235, 30), (30, 235, 235), (20, 80, 235)]
    for index, color in enumerate(near_keys):
        for x in range(index * 8, index * 8 + 8):
            for y in range(32):
                pixels[x, y] = (*color, 255)
    image.save(ref)

    result = prepare.choose_chroma_key(ref, "auto")
    assert result["selection"] == "auto"
    assert result["min_subject_distance"] <= prepare.MIN_SUBJECT_KEY_DISTANCE
    assert "warning" in result
