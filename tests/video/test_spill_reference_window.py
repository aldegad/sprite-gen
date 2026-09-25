"""`video-frames --spill auto` keys only the subject window of its reference still.

A canvas padded for motion room is mostly key, and keying all of it made the reference
judgment's memory grow with the padding. The judgment keys the box of pixels the hard
cut cannot erase, grown by a margin that covers the matte's reach, with the painted key
read on the whole still. It has to give the counts keying the whole still gives, which
`_whole_still_judgment` (the judgment as it was) checks on synthetic stills.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.frames import cutout as cutout_mod
from sprite_gen.frames.cutout import KEY_TARGETS, extract_route
from sprite_gen.frames.extract import DEFAULT_UNMIX_REACH, key_material_pixels, subject_window
from sprite_gen.video import frames as frames_mod

GREEN_PAINTED = (8, 162, 24)  # a model's green, off the pure key
GROK_MAGENTA = (216, 46, 147)
# green material of the subject's own, far from both keys: a brighter leaf green would sit inside the
# painted colour's ball with the key's signature, and the matte erases that as background
MOSS = (80, 115, 90)


def _whole_still_judgment(path: Path, kind: str) -> tuple[int, int]:
    """(key material, subject) with the whole still through the matte: the judgment before the window."""
    keyed, _ = extract_route(Image.open(path).convert("RGBA"), kind)
    return key_material_pixels(keyed, KEY_TARGETS[kind], frames_mod.SPILL_FULL_MIN_TINT,
                               require_hue=True, ignore_fringe=DEFAULT_UNMIX_REACH)


def _figure(width: int, height: int, background: tuple[int, int, int], *, patch: tuple[int, int, int] | None,
            spill: tuple[int, int, int] | None = None) -> Image.Image:
    """A figure drawn at 4x and box-reduced, so its outline, strands and patches meet the key as blends."""
    big = Image.new("RGB", (width * 4, height * 4), background)
    draw = ImageDraw.Draw(big)
    draw.ellipse((40, 60, width * 4 - 40, height * 4 - 20), fill=(200, 205, 212), outline=(20, 30, 22), width=10)
    for index in range(9):  # red strands, 1-3 px wide after the reduction
        x = 60 + index * (width * 4 - 120) // 9
        draw.line((x, 8, x + 30, height * 2), fill=(170, 30, 40), width=4 + (index % 3) * 4)
    if patch is not None:
        draw.rectangle((width, height, width * 3, height * 2), fill=patch)
    if spill is not None:  # a small key-tinted cluster inside the subject
        draw.rectangle((width * 2, height * 3, width * 2 + 16, height * 3 + 16), fill=spill)
    return big.reduce(4)


def _canvas(path: Path, size: tuple[int, int], background: tuple[int, int, int], figures: list[tuple[int, int, Image.Image]],
            *, seed: int = 0) -> Path:
    """Figures pasted on a flat painted key, then +-5 noise on every channel of every pixel."""
    canvas = Image.new("RGB", size, background)
    for left, top, figure in figures:
        canvas.paste(figure, (left, top))
    noise = np.random.default_rng(seed).integers(-5, 6, size=(size[1], size[0], 3))
    Image.fromarray(np.clip(np.asarray(canvas).astype(np.int16) + noise, 0, 255).astype(np.uint8)).save(path)
    return path


def _scenes(tmp_path: Path) -> dict[str, tuple[Path, str]]:
    clean = _figure(120, 150, GREEN_PAINTED, patch=None, spill=(60, 235, 70))
    leafy = _figure(120, 150, GREEN_PAINTED, patch=MOSS)
    teal = _figure(120, 150, GROK_MAGENTA, patch=(40, 150, 150), spill=(230, 60, 215))
    # a subject that fills its own box, large enough that the ring of background around it is
    # under the detector's 12 % of the window's samples: only the painted colour read on the
    # whole still keys that ring
    block = Image.new("RGB", (240, 260), (190, 195, 205))
    ImageDraw.Draw(block).rectangle((50, 60, 170, 200), fill=MOSS)
    scenes = {
        "padded": (_canvas(tmp_path / "padded.png", (620, 460), GREEN_PAINTED, [(300, 170, clean)]), "green"),
        "touches the bottom edge": (_canvas(tmp_path / "edge.png", (400, 300), GREEN_PAINTED, [(40, 150, clean)], seed=1), "green"),
        "two subjects": (_canvas(tmp_path / "two.png", (700, 400), GREEN_PAINTED, [(10, 10, clean), (560, 240, leafy)], seed=2), "green"),
        "own green material": (_canvas(tmp_path / "leafy.png", (500, 420), GREEN_PAINTED, [(200, 130, leafy)], seed=3), "green"),
        "grok magenta": (_canvas(tmp_path / "magenta.png", (520, 400), GROK_MAGENTA, [(150, 90, teal)], seed=4), "magenta"),
        "fills its box": (_canvas(tmp_path / "block.png", (600, 500), GREEN_PAINTED, [(180, 120, block)], seed=5), "green"),
    }
    # transparent pixels far from the key: keyed by their alpha, whatever their colour
    rgba = Image.open(scenes["padded"][0]).convert("RGBA")
    ImageDraw.Draw(rgba).rectangle((20, 20, 90, 60), fill=(200, 40, 40, 0))
    rgba.save(tmp_path / "transparent.png")
    scenes["transparent corner"] = (tmp_path / "transparent.png", "green")
    return scenes


@pytest.mark.parametrize("scene", ["padded", "touches the bottom edge", "two subjects", "own green material",
                                   "grok magenta", "fills its box", "transparent corner"])
def test_the_window_gives_the_whole_stills_counts(tmp_path: Path, scene: str) -> None:
    path, kind = _scenes(tmp_path)[scene]
    material, subject = _whole_still_judgment(path, kind)
    decision = frames_mod.decide_spill(path, kind)
    assert (decision["key_material_px"], decision["subject_px"]) == (material, subject)
    assert decision["reference_stride"] == 1
    assert subject > 0
    share = material / subject
    assert decision["mode"] == ("full" if share <= frames_mod.SPILL_REFERENCE_MAX else "small")
    left, top, right, bottom = decision["reference_window"]
    width, height = decision["reference_size"]
    assert (right - left) * (bottom - top) < width * height  # the padding was not keyed


def test_the_scenes_cover_both_modes(tmp_path: Path) -> None:
    scenes = _scenes(tmp_path)
    assert frames_mod.decide_spill(*scenes["padded"])["mode"] == "full"
    assert frames_mod.decide_spill(*scenes["own green material"])["mode"] == "small"


def test_the_matte_sees_the_window_not_the_canvas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, kind = _scenes(tmp_path)["padded"]
    keyed_sizes: list[tuple[int, int]] = []
    real = cutout_mod.extract_route

    def spy(image: Image.Image, route: str, **kwargs: object) -> tuple[Image.Image, dict]:
        keyed_sizes.append(image.size)
        return real(image, route, **kwargs)

    monkeypatch.setattr(cutout_mod, "extract_route", spy)
    decision = frames_mod.decide_spill(path, kind)
    left, top, right, bottom = decision["reference_window"]
    assert keyed_sizes == [(right - left, bottom - top)]
    assert keyed_sizes[0][0] * keyed_sizes[0][1] < 620 * 460 // 4


def test_a_window_over_the_budget_is_keyed_on_a_stride(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenes = _scenes(tmp_path)
    budget = 6000
    monkeypatch.setattr(frames_mod, "SPILL_REFERENCE_MAX_PIXELS", budget)
    keyed_sizes: list[tuple[int, int]] = []
    real = cutout_mod.extract_route

    def spy(image: Image.Image, route: str, **kwargs: object) -> tuple[Image.Image, dict]:
        keyed_sizes.append(image.size)
        return real(image, route, **kwargs)

    monkeypatch.setattr(cutout_mod, "extract_route", spy)
    clean = frames_mod.decide_spill(*scenes["padded"])
    leafy = frames_mod.decide_spill(*scenes["own green material"])
    for decision, (width, height) in zip((clean, leafy), keyed_sizes):
        left, top, right, bottom = decision["reference_window"]
        stride = decision["reference_stride"]
        assert stride > 1
        assert (width, height) == (-(-(right - left) // stride), -(-(bottom - top) // stride))
        assert width * height <= budget * 1.5
    # a clear call stays the same call on the sparser grid
    assert clean["mode"] == "full" and leafy["mode"] == "small"


def test_a_reference_that_is_all_key_keys_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _canvas(tmp_path / "empty.png", (300, 200), GREEN_PAINTED, [])
    monkeypatch.setattr(cutout_mod, "extract_route", lambda *a, **k: pytest.fail("nothing to key"))
    decision = frames_mod.decide_spill(path, "green")
    assert decision["reference_window"] is None
    assert (decision["key_material_px"], decision["subject_px"], decision["mode"]) == (0, 0, "full")


def test_subject_window_bounds_what_the_hard_cut_cannot_erase() -> None:
    image = Image.new("RGBA", (50, 40), (0, 255, 0, 255))
    image.putpixel((10, 12), (0, 250, 5, 255))  # near the key: erased, outside the box
    image.putpixel((20, 15), (200, 30, 30, 255))
    image.putpixel((31, 22), (200, 30, 30, 0))  # far, transparent: the box ignores alpha
    assert subject_window(image, (0, 255, 0), 96.0, 3) == (17, 12, 35, 26)
    assert subject_window(image, (0, 255, 0), 96.0, 40) == (0, 0, 50, 40)
    assert subject_window(Image.new("RGB", (8, 8), (0, 255, 0)), (0, 255, 0), 96.0, 3) is None


def test_subject_window_erases_the_painted_key_like_the_matte() -> None:
    # 96.38 from the pure key: only the painted colour's ball erases this background
    image = Image.new("RGB", (60, 30), GREEN_PAINTED)
    image.putpixel((40, 10), (200, 30, 30))
    assert subject_window(image, (0, 255, 0), 96.0, 2) == (0, 0, 60, 30)
    assert subject_window(image, (0, 255, 0), 96.0, 2, GREEN_PAINTED) == (38, 8, 43, 13)
