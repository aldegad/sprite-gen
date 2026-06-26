# SPDX-License-Identifier: Apache-2.0
"""Regression tests for chroma-key alpha cleanup and auto key selection.

These guard two ways the extractor used to silently destroy real subject
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


def _key(pixel: tuple[int, int, int], chroma_key=MAGENTA):
    image = Image.new("RGBA", (1, 1), (*pixel, 255))
    out = extract.remove_chroma_background(image, chroma_key, KEY_THRESHOLD, FRINGE_THRESHOLD, FRINGE_DELTA)
    return out.getpixel((0, 0))


def test_despill_preserves_far_subject_colors() -> None:
    # All of these sit >200 color-distance away from magenta: they are the
    # subject, not key fringe, and must survive completely untouched.
    for pixel in [(196, 54, 38), (224, 96, 40), (208, 44, 40), (40, 70, 200)]:
        assert extract.color_distance(pixel, MAGENTA) > FRINGE_THRESHOLD
        assert _key(pixel) == (*pixel, 255), f"{pixel} was mangled by despill"


def test_despill_still_removes_key_and_fringe() -> None:
    # Exact key -> fully transparent.
    assert _key(MAGENTA)[3] == 0
    # A magenta antialias fringe (the key blended with a green subject edge)
    # sits inside FRINGE_THRESHOLD with a strong key tint and must still go.
    fringe = (147, 90, 157)
    assert extract.color_distance(fringe, MAGENTA) <= FRINGE_THRESHOLD
    assert extract.key_tint_score(fringe, MAGENTA) >= FRINGE_DELTA
    assert _key(fringe)[3] == 0


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
