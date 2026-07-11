# SPDX-License-Identifier: Apache-2.0
"""Tests for the opt-in YCbCr chrominance matting path (chroma.mode: "ycbcr").

Port of perfectpixel-studio internal/sprite/chroma.go (MIT — see NOTICE).
What the RGB-distance path cannot do and this path must:

1. **Shaded/gradient key background** — a green key darkened by shading keeps
   its chroma direction but moves >96 RGB distance from the declared key, so
   the RGB threshold leaves it opaque. On the CbCr plane it stays in the key's
   chroma family and the border flood fill removes it.
2. **Key detection by histogram mode, not mean** — a border containing two
   chroma clusters must yield the dominant cluster's average, never the
   global mean (which lands between clusters and mattes neither).
3. **Key-direction despill** — a green-spilled subject pixel loses only its
   key-direction chroma; colors orthogonal to the key keep their saturation.
4. **Connectivity preserves the interior** — a key-family pixel enclosed by
   subject never connects to the border and survives the flood fill.
5. **Self-diagnostic rematte is observable** — when border sampling
   mis-detects the key (subject crowds the corners), the declared-key rematte
   engages and reports itself through the warnings channel (no silent
   fallback).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
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

GREEN = (0, 255, 0)
# Shaded green: same chroma direction as the key, dimmed luma. RGB distance to
# pure green is 115 (past the 96 erase radius) yet its CbCr offset stays inside
# the lenient flood tolerance.
SHADED_GREEN = (0, 140, 0)
RED = (200, 40, 40)


def _opaque_count(image: Image.Image, threshold: int = 10) -> int:
    return sum(image.getchannel("A").histogram()[threshold + 1 :])


def _green_family_opaque(image: Image.Image) -> int:
    """Opaque pixels still in the key's chroma family (residual background)."""
    pixels = image.load()
    width, height = image.size
    _, key_cb, key_cr = extract.rgb_to_ycc(*GREEN)
    count = 0
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha <= 10:
                continue
            _, cb, cr = extract.rgb_to_ycc(red, green, blue)
            if ((cb - key_cb) ** 2 + (cr - key_cr) ** 2) ** 0.5 < 55.0:
                count += 1
    return count


def _shaded_key_strip() -> Image.Image:
    """Green-key strip with a wide shaded-green band and a red subject.

    The shaded band is kept >6px away from any pure-key pixel by making it a
    thick border-adjacent region, so the RGB path's depth-limited unmix cannot
    reach it either.
    """
    strip = Image.new("RGB", (96, 64), GREEN)
    # Shaded background band across the bottom third — border-connected.
    for y in range(44, 64):
        for x in range(96):
            strip.putpixel((x, y), SHADED_GREEN)
    # Red subject block, clear of the band.
    for y in range(8, 36):
        for x in range(30, 62):
            strip.putpixel((x, y), RED)
    return strip


def test_rgb_path_leaves_shaded_key_but_ycbcr_clears_it():
    strip = _shaded_key_strip()
    rgb_result = extract.remove_chroma_background(strip, GREEN, 96.0, 180.0, 18.0)
    ycc_result = extract.remove_chroma_background_ycbcr(strip, GREEN)

    # The shaded band's lower rows sit beyond the RGB path's depth-limited
    # unmix reach; count survivors there directly.
    band = (0, 50, 96, 64)
    rgb_residue = _opaque_count(rgb_result.crop(band))
    ycc_residue = _opaque_count(ycc_result.crop(band))
    assert rgb_residue > 500, "expected the RGB path to leave the shaded band opaque"
    assert ycc_residue == 0, f"ycbcr path left {ycc_residue} background pixels"
    assert _green_family_opaque(ycc_result) == 0

    # The subject must survive intact in both.
    subject = ycc_result.crop((30, 8, 62, 36))
    assert _opaque_count(subject) == subject.width * subject.height


def test_detect_background_key_uses_histogram_mode_not_mean():
    # Two chroma clusters on the border: dominant real background vs a
    # minority stripe. The mean of the samples is a muddy midpoint that
    # mattes neither cluster; both resolution branches (declared-key family
    # bias, plain histogram mode) must land on the dominant cluster.
    image = Image.new("RGB", (60, 60), (11, 238, 27))
    for y in range(60):
        for x in range(0, 12):  # left edge minority stripe
            image.putpixel((x, y), (200, 40, 200))
    rgba = image.convert("RGBA")
    # Declared key green → the green family on the border wins.
    detected = extract.detect_background_key_ycc(rgba, GREEN)
    assert extract.color_distance(detected, (11, 238, 27)) < 30.0
    # A declared key with no family on the border (blue) forces the pure
    # histogram-mode branch; the dominant green cluster must still win.
    detected_mode = extract.detect_background_key_ycc(rgba, (0, 77, 255))
    assert extract.color_distance(detected_mode, (11, 238, 27)) < 30.0


def test_despill_subtracts_key_direction_only():
    # Green-spilled interior pixels ((60, 220, 60): CbCr distance ~51 from the
    # key — inside the despill band) shielded from the border flood fill by a
    # solid red ring, so the despilled soft-alpha result survives.
    spilled = (60, 220, 60)
    strip = Image.new("RGB", (48, 48), GREEN)
    for y in range(10, 38):
        for x in range(10, 38):
            strip.putpixel((x, y), RED)
    for y in range(16, 32):
        for x in range(16, 32):
            strip.putpixel((x, y), spilled)
    result = extract.remove_chroma_background_ycbcr(strip, GREEN)
    red, green, blue, alpha = result.getpixel((24, 24))
    assert 0 < alpha < 255, "spill blend must resolve to partial coverage"
    # Green excess over the other channels must shrink after despill.
    before_excess = spilled[1] - (spilled[0] + spilled[2]) / 2
    after_excess = green - (red + blue) / 2
    assert after_excess < before_excess * 0.7
    # A color orthogonal to the key direction is preserved byte-exact.
    assert result.getpixel((12, 12)) == (*RED, 255)


def test_flood_fill_preserves_enclosed_key_family_pixel():
    strip = Image.new("RGB", (48, 48), GREEN)
    # Solid red subject with one shaded-green pixel sealed inside.
    for y in range(10, 38):
        for x in range(10, 38):
            strip.putpixel((x, y), RED)
    strip.putpixel((24, 24), SHADED_GREEN)
    result = extract.remove_chroma_background_ycbcr(strip, GREEN)
    assert result.getpixel((24, 24))[3] > 0, "enclosed key-family pixel must survive"
    # Border-connected shaded green (same color) is removed.
    strip.putpixel((2, 2), SHADED_GREEN)
    result2 = extract.remove_chroma_background_ycbcr(strip, GREEN)
    assert result2.getpixel((2, 2))[3] == 0


def test_self_diagnostic_rematte_is_reported():
    # Subject crowds every corner: border sampling detects the red frame as
    # the key, mattes away the subject, and the green background survives —
    # the residue symptom must trigger the declared-key rematte.
    strip = Image.new("RGB", (64, 64), RED)
    for y in range(16, 48):
        for x in range(16, 48):
            strip.putpixel((x, y), GREEN)
    warnings: list[str] = []
    result = extract.remove_chroma_background_ycbcr(strip, GREEN, warnings)
    assert warnings, "fallback rematte must be observable"
    assert result.getpixel((32, 32))[3] == 0, "green region must be keyed out"
    assert result.getpixel((4, 4))[3] == 255, "red subject must survive"


def test_extract_cli_ycbcr_mode_end_to_end(fixture_run_dir: Path):
    from conftest import run_script

    result = run_script(
        "extract_sprite_row_frames.py",
        "--run-dir",
        str(fixture_run_dir),
        "--chroma-mode",
        "ycbcr",
    )
    assert result.returncode == 0, result.stderr
    request = json.loads((fixture_run_dir / "sprite-request.json").read_text())
    assert request["chroma"]["mode"] == "ycbcr"
    for state, frames in (("idle", 4), ("walk", 3)):
        for index in range(frames):
            frame = fixture_run_dir / "frames" / state / f"frame-{index}.png"
            assert frame.is_file()
            with Image.open(frame) as opened:
                assert _opaque_count(opened.convert("RGBA")) > 0


def test_default_mode_stays_rgb(fixture_run_dir: Path):
    from conftest import run_script

    result = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir))
    assert result.returncode == 0, result.stderr
    request = json.loads((fixture_run_dir / "sprite-request.json").read_text())
    assert request["chroma"]["mode"] == "rgb"
