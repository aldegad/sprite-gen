# SPDX-License-Identifier: Apache-2.0
"""``--chroma-key auto`` candidate scoring over a base that carries a background.

Every base in this pipeline is a subject drawn on a flat chroma background. The
scorer used to count those background pixels as subject, which pinned whichever
candidate matched the current background to ``min_subject_distance ~= 0`` and made
it permanently unselectable -- ``auto`` could never re-choose the key the base was
drawn against.

Excluding the background naively would trade that bug for a worse one: a key-hued
*gem* enclosed by the subject would vanish from the scoring too, and ``auto`` would
bless the key that erases it. The two are told apart by flatness -- a hole showing
the background through the silhouette is a flat fill, drawn material is shaded.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("prepare_sprite_run", ROOT / "scripts" / "prepare_sprite_run.py")
prepare = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(prepare)

MAGENTA = (255, 0, 255)
BODY = (196, 54, 38)


def _base_on_background(path: Path, background: tuple[int, int, int] = MAGENTA, size: int = 160) -> Image.Image:
    """A round body on a flat chroma background -- the shape every base has."""
    image = Image.new("RGBA", (size, size), (*background, 255))
    pixels = image.load()
    radius = size * 3 // 8
    center = size // 2
    for y in range(size):
        for x in range(size):
            if (x - center) ** 2 + (y - center) ** 2 < radius ** 2:
                pixels[x, y] = (*BODY, 255)
    image.save(path)
    return image


def _paint_block(path: Path, image: Image.Image, box: tuple[int, int, int, int], color) -> None:
    pixels = image.load()
    left, top, right, bottom = box
    for y in range(top, bottom):
        for x in range(left, right):
            pixels[x, y] = color(x, y) if callable(color) else (*color, 255)
    image.save(path)


def _candidate(result: dict, name: str) -> dict:
    return next(entry for entry in result["candidates"] if entry["name"] == name)


def test_flat_background_is_excluded_from_subject_pixels(tmp_path: Path) -> None:
    ref = tmp_path / "base.png"
    _base_on_background(ref)

    result = prepare.choose_chroma_key(ref, "auto")
    assert result["background"]["mode"] == "flat"
    assert result["background"]["hex"] == "#FF00FF"

    # No sampled subject pixel is the background, so the key the base was drawn
    # against is scored against the body -- not against itself.
    for pixel in prepare.sampled_reference_pixels(ref):
        assert prepare.color_distance(pixel, MAGENTA) > prepare.BACKGROUND_TOLERANCE
    assert _candidate(result, "magenta")["min_subject_distance"] > prepare.MIN_SUBJECT_KEY_DISTANCE
    assert "warning" not in result


def test_auto_can_reselect_the_key_the_base_was_drawn_against(tmp_path: Path) -> None:
    # Subject material sits inside the erase radius of green, cyan and blue, so
    # magenta is the only safe key -- and magenta is also the base's background.
    ref = tmp_path / "base.png"
    image = _base_on_background(ref)
    for index, color in enumerate(((30, 235, 30), (30, 235, 235), (20, 80, 235))):
        left = 55 + index * 20
        _paint_block(ref, image, (left, 70, left + 18, 90), color)

    result = prepare.choose_chroma_key(ref, "auto")
    assert result["name"] == "magenta", result["candidates"]
    assert result["min_subject_distance"] > prepare.MIN_SUBJECT_KEY_DISTANCE
    for name in ("green", "cyan", "blue"):
        assert _candidate(result, name)["clears_erase_radius"] is False
    assert "warning" not in result


def test_enclosed_flat_hole_is_background_not_material(tmp_path: Path) -> None:
    # A flat key-colored square inside the body: the background showing through.
    ref = tmp_path / "base.png"
    image = _base_on_background(ref)
    _paint_block(ref, image, (70, 70, 92, 92), MAGENTA)

    result = prepare.choose_chroma_key(ref, "auto")
    background = result["background"]
    assert background["enclosed_background_pixels"] > 0
    assert background["enclosed_material_pixels"] == 0
    assert _candidate(result, "magenta")["clears_erase_radius"] is True


def test_enclosed_shaded_key_hued_material_blocks_that_key(tmp_path: Path) -> None:
    # A *shaded* magenta gem inside the body. It is not a hole, so it must stay in
    # the subject and push magenta inside the erase radius (v1.10.1 key-tint
    # protection: magenta material must not be silently deleted by a magenta key).
    ref = tmp_path / "base.png"
    image = _base_on_background(ref)

    def gem(x: int, _y: int) -> tuple[int, int, int, int]:
        ramp = (x - 70) / 22
        return (int(200 + 50 * ramp), int(10 + 70 * (1 - ramp)), int(190 + 60 * ramp), 255)

    _paint_block(ref, image, (70, 70, 92, 92), gem)

    result = prepare.choose_chroma_key(ref, "auto")
    assert result["background"]["enclosed_material_pixels"] > 0
    assert _candidate(result, "magenta")["clears_erase_radius"] is False
    assert result["name"] != "magenta"


def test_transparent_base_keeps_its_pre_background_behaviour(tmp_path: Path) -> None:
    # Regression: a base with no background at all. Nothing beyond the alpha rule
    # is excluded, and a sub-1% feature still drives the erase-radius gate.
    ref = tmp_path / "transparent.png"
    image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(128):
        for x in range(128):
            if (x - 64) ** 2 + (y - 64) ** 2 < 58 ** 2:
                pixels[x, y] = (*BODY, 255)
    for center_x in (50, 78):
        for y in range(50, 55):
            for x in range(center_x - 2, center_x + 3):
                pixels[x, y] = (0, 250, 200, 255)  # teal eye: ~55 from cyan
    image.save(ref)

    result = prepare.choose_chroma_key(ref, "auto")
    assert result["background"]["mode"] == "transparent"
    assert "enclosed_background_pixels" not in result["background"]
    assert result["name"] != "cyan"
    assert _candidate(result, "cyan")["clears_erase_radius"] is False
    assert result["min_subject_distance"] > prepare.MIN_SUBJECT_KEY_DISTANCE


def test_heterogeneous_border_is_reported_instead_of_silently_assumed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A full-bleed image has no background ring to detect. Excluding nothing is the
    # only honest answer, and it is reported rather than passed off as a clean read.
    ref = tmp_path / "rainbow.png"
    image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    pixels = image.load()
    for index, color in enumerate(((235, 30, 235), (30, 235, 30), (30, 235, 235), (20, 80, 235))):
        for x in range(index * 8, index * 8 + 8):
            for y in range(32):
                pixels[x, y] = (*color, 255)
    image.save(ref)

    result = prepare.choose_chroma_key(ref, "auto")
    assert result["background"]["mode"] == "heterogeneous"
    assert "note" in result["background"]
    assert "border ring is not a flat fill" in capsys.readouterr().err
    # No candidate can clear a subject that hugs all four keys.
    assert result["min_subject_distance"] <= prepare.MIN_SUBJECT_KEY_DISTANCE
    assert "warning" in result


def test_background_only_base_warns_instead_of_defaulting_quietly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ref = tmp_path / "empty.png"
    Image.new("RGBA", (64, 64), (*MAGENTA, 255)).save(ref)

    result = prepare.choose_chroma_key(ref, "auto")
    assert result["selection"] == "fallback"
    assert "no subject pixels" in result["selection_reason"]
    assert "no subject pixels" in capsys.readouterr().err


def test_absent_reference_defaults_without_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    result = prepare.choose_chroma_key(None, "auto")
    assert result["selection"] == "fallback"
    assert result["background"]["mode"] == "absent"
    assert result["selection_reason"] == "no base reference to sample"
    assert capsys.readouterr().err == ""


def test_manual_key_skips_reference_analysis(tmp_path: Path) -> None:
    ref = tmp_path / "base.png"
    _base_on_background(ref)
    result = prepare.choose_chroma_key(ref, "#00FF00")
    assert result == {"name": "green", "hex": "#00FF00", "rgb": [0, 255, 0], "selection": "manual"}


def test_request_json_records_the_selection_basis(tmp_path: Path) -> None:
    ref = tmp_path / "base.png"
    _base_on_background(ref)
    out_dir = tmp_path / "run"

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "prepare_sprite_run.py"),
            "--out-dir", str(out_dir),
            "--character-id", "probe",
            "--base-image", str(ref),
            "--chroma-key", "auto",
        ],
        check=True,
        capture_output=True,
    )

    chroma = json.loads((out_dir / "sprite-request.json").read_text())["chroma_key"]
    assert chroma["selection"] == "auto"
    assert chroma["background"]["mode"] == "flat"
    assert chroma["background"]["hex"] == "#FF00FF"
    assert {entry["name"] for entry in chroma["candidates"]} == {"magenta", "green", "cyan", "blue"}
    assert all("min_subject_distance" in entry for entry in chroma["candidates"])
    assert "erase radius" in chroma["selection_reason"]
