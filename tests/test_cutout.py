# SPDX-License-Identifier: Apache-2.0
"""Tests for the matte-based `cutout` background remover (imported-image utility).

Synthetic fixtures are built in-process (no binary assets): an ivory canvas with
a centered green square, optionally carrying a pure-white highlight *inside* the
square. The interior highlight is the crux — a colour-only key would punch a hole
in it, but the corner flood-fill keys by position so it must survive.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.cutout import cutout, estimate_background

IVORY = (248, 247, 242)
GREEN = (86, 133, 39)


def _make_icon(with_interior_white: bool = False) -> Image.Image:
    """128x128 ivory bg, centered 64x64 green square, optional white dot inside it."""
    img = Image.new("RGBA", (128, 128), IVORY + (255,))
    px = img.load()
    for y in range(32, 96):
        for x in range(32, 96):
            px[x, y] = GREEN + (255,)
    if with_interior_white:
        for y in range(56, 72):
            for x in range(56, 72):
                px[x, y] = (255, 255, 255, 255)  # pure white highlight buried in the object
    return img


def test_uniform_background_removed(tmp_path: Path) -> None:
    src = tmp_path / "icon.png"
    _make_icon().save(src)
    out = tmp_path / "icon_cutout.png"
    stats = cutout(src, out)

    result = Image.open(out).convert("RGBA")
    px = result.load()
    # corners (background) fully transparent
    for cx, cy in [(0, 0), (127, 0), (0, 127), (127, 127)]:
        assert px[cx, cy][3] == 0
    # object center fully opaque and still green
    r, g, b, a = px[64, 64]
    assert a == 255
    assert (r, g, b) == GREEN
    assert stats["keyed_pixels"] > 0


def test_interior_white_highlight_preserved(tmp_path: Path) -> None:
    """A pure-white region *inside* the object must not be keyed out (no pinhole)."""
    src = tmp_path / "icon_hl.png"
    _make_icon(with_interior_white=True).save(src)
    out = tmp_path / "icon_hl_cutout.png"
    cutout(src, out)

    px = Image.open(out).convert("RGBA").load()
    # the interior white dot stays opaque despite matching the ivory background colour
    r, g, b, a = px[64, 64]
    assert a == 255
    assert (r, g, b) == (255, 255, 255)


def test_no_silent_fallback_holds(tmp_path: Path) -> None:
    """cutout() completes without raising — every transparent pixel has zeroed RGB."""
    src = tmp_path / "icon.png"
    _make_icon().save(src)
    out = tmp_path / "icon_cutout.png"
    cutout(src, out)  # would SystemExit if a transparent pixel kept non-zero RGB
    px = Image.open(out).convert("RGBA").load()
    r, g, b, a = px[0, 0]
    assert (r, g, b, a) == (0, 0, 0, 0)


def test_white_check_composites_written(tmp_path: Path) -> None:
    src = tmp_path / "icon.png"
    _make_icon().save(src)
    out = tmp_path / "icon_cutout.png"
    check_dir = tmp_path / "checks"
    stats = cutout(src, out, white_check_dir=check_dir)
    assert "white_check" in stats
    for name in ("cyan", "magenta", "yellow"):
        assert (check_dir / f"icon_cutout_{name}.png").exists()


def test_missing_background_raises(tmp_path: Path) -> None:
    """A fully object-filled image (no locatable background corners) fails loudly."""
    src = tmp_path / "full.png"
    Image.new("RGBA", (64, 64), GREEN + (255,)).save(src)
    out = tmp_path / "full_cutout.png"
    with pytest.raises(SystemExit):
        cutout(src, out)


def test_estimate_background_from_corners(tmp_path: Path) -> None:
    img = _make_icon()
    assert estimate_background(img) == IVORY
