# SPDX-License-Identifier: Apache-2.0
"""Regression: a flat background that is the *declared key's hue* but darker than
the pure key must key out completely.

Image models asked for #00FF00 / #FF00FF return a slightly different flat color
every time. Measured 2026-09-11 (Grok, samurai-b-walk rev1): the background came
back as (8, 162, 24). Its absolute RGB distance to (0, 255, 0) is 96.38 — a hair
over the 96.0 hard-key threshold — so the *entire* background survived as opaque
pixels, while a one-step-brighter (7, 163, 24) at 95.34 keyed out. Nothing about
the frame changed; only the model's brightness did. `video-frames` then reported
the surviving background as "framed too tight" (edge contact).

The fixtures below are synthetic (no binary assets): a flat background of the
dark key plus a subject square that shares no hue with the key. The contract is
pixel-exact — zero residual background pixels and the subject byte-preserved —
because a partial result is exactly the failure mode (a half-keyed background
is not "mostly fine", it is what breaks every downstream gate).

Before the relative-threshold fix these cases fail on both the `cutout` route
and the underlying `remove_chroma_background` engine. (5, 200, 10) and
(250, 8, 240) are within 96 of their keys and pass on the old code; they are kept
so the fix cannot regress the easy case.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.frames.cutout import (
    _EXTRACT_FRINGE_DELTA,
    _EXTRACT_FRINGE_THRESHOLD,
    _EXTRACT_KEY_THRESHOLD,
    cutout,
)
from sprite_gen.frames.extract import remove_chroma_background

SUBJECT = (110, 70, 50)  # warm brown — no green/magenta tint
HIGHLIGHT = (230, 210, 190)
SIZE = 128
SUBJECT_BOX = (32, 32, 96, 96)  # x0, y0, x1, y1 (exclusive)
HIGHLIGHT_BOX = (56, 56, 72, 72)

# (declared key name, key rgb, model-returned flat background, rgb distance to key)
DARK_KEY_CASES = [
    pytest.param("green", (0, 255, 0), (8, 162, 24), id="green-measured-8-162-24-dist96.38"),
    pytest.param("green", (0, 255, 0), (10, 150, 30), id="green-darker-10-150-30-dist109.66"),
    pytest.param("green", (0, 255, 0), (5, 200, 10), id="green-easy-5-200-10-dist56.12"),
    pytest.param("magenta", (255, 0, 255), (170, 8, 180), id="magenta-dark-170-8-180-dist113.64"),
    pytest.param("magenta", (255, 0, 255), (180, 10, 175), id="magenta-dark-180-10-175-dist110.11"),
    pytest.param("magenta", (255, 0, 255), (250, 8, 240), id="magenta-easy-250-8-240-dist17.72"),
]


def make_flat_key_still(background: tuple[int, int, int]) -> Image.Image:
    """SIZE x SIZE flat `background`, centered subject square with a highlight inside."""
    img = Image.new("RGBA", (SIZE, SIZE), background + (255,))
    px = img.load()
    x0, y0, x1, y1 = SUBJECT_BOX
    for y in range(y0, y1):
        for x in range(x0, x1):
            px[x, y] = SUBJECT + (255,)
    hx0, hy0, hx1, hy1 = HIGHLIGHT_BOX
    for y in range(hy0, hy1):
        for x in range(hx0, hx1):
            px[x, y] = HIGHLIGHT + (255,)
    return img


def _in_subject(x: int, y: int) -> bool:
    x0, y0, x1, y1 = SUBJECT_BOX
    return x0 <= x < x1 and y0 <= y < y1


def audit(result: Image.Image, source: Image.Image) -> tuple[int, int]:
    """(residual background px with alpha>0, subject px changed in RGBA)."""
    out = result.convert("RGBA").load()
    src = source.load()
    residual = subject_changed = 0
    for y in range(SIZE):
        for x in range(SIZE):
            if _in_subject(x, y):
                if out[x, y] != src[x, y]:
                    subject_changed += 1
            elif out[x, y][3] != 0:
                residual += 1
    return residual, subject_changed


@pytest.mark.parametrize(("key", "key_rgb", "background"), DARK_KEY_CASES)
def test_remove_chroma_background_keys_out_dark_flat_key(
    key: str, key_rgb: tuple[int, int, int], background: tuple[int, int, int]
) -> None:
    """Engine level: the extract CLI defaults (as cutout passes them) must clear the flat bg."""
    source = make_flat_key_still(background)
    result = remove_chroma_background(
        source, key_rgb, _EXTRACT_KEY_THRESHOLD, _EXTRACT_FRINGE_THRESHOLD, _EXTRACT_FRINGE_DELTA
    )
    residual, subject_changed = audit(result, source)
    assert residual == 0, f"{background} on key {key}: {residual} background px survived"
    assert subject_changed == 0, f"{background} on key {key}: {subject_changed} subject px altered"


@pytest.mark.parametrize(("key", "key_rgb", "background"), DARK_KEY_CASES)
def test_cutout_explicit_key_keys_out_dark_flat_key(
    tmp_path: Path, key: str, key_rgb: tuple[int, int, int], background: tuple[int, int, int]
) -> None:
    """`cutout --key green|magenta` (the video-canvas / gen chroma entry): bg 100% transparent."""
    source = make_flat_key_still(background)
    src_path = tmp_path / f"{key}-{'-'.join(map(str, background))}.png"
    source.save(src_path)
    out_path = tmp_path / "cutout.png"

    stats = cutout(src_path, out_path, key=key)

    assert stats["route"] == f"extract:{key}"
    residual, subject_changed = audit(Image.open(out_path), source)
    assert residual == 0, f"{background} on key {key}: {residual} background px survived"
    assert subject_changed == 0, f"{background} on key {key}: {subject_changed} subject px altered"
    subject_area = (SUBJECT_BOX[2] - SUBJECT_BOX[0]) * (SUBJECT_BOX[3] - SUBJECT_BOX[1])
    expected_alpha_zero_pct = round((SIZE * SIZE - subject_area) / (SIZE * SIZE) * 100, 2)
    assert stats["alpha_zero_pct"] == expected_alpha_zero_pct


def test_boundary_pair_must_not_split(tmp_path: Path) -> None:
    """The measured pair: (7,163,24) keyed out, (8,162,24) survived — one brightness step apart.

    Whatever the fixed rule is, two flat backgrounds this close must be treated
    the same. This is the pixel-level "half keyed" symptom stated as a test.
    """
    key_rgb = (0, 255, 0)
    outcomes = {}
    for background in ((7, 163, 24), (8, 162, 24)):
        source = make_flat_key_still(background)
        result = remove_chroma_background(
            source, key_rgb, _EXTRACT_KEY_THRESHOLD, _EXTRACT_FRINGE_THRESHOLD, _EXTRACT_FRINGE_DELTA
        )
        outcomes[background] = audit(result, source)
    assert outcomes[(7, 163, 24)] == outcomes[(8, 162, 24)] == (0, 0), outcomes


# --- the detection layer itself ----------------------------------------------
# `remove_chroma_background` keys from the smaller of the distance to the
# declared key and to the background colour it detects on the borders. These
# pin the detector's contract: what counts as "the key's family", that the mode
# wins over the mean, and that the opt-out reproduces the single-key result.

from sprite_gen.frames.extract import detect_background_key_rgb, is_key_family  # noqa: E402

GREEN = (0, 255, 0)
MAGENTA = (255, 0, 255)
HOT_PINK = (250, 77, 150)  # ~129 from magenta — subject material the trap-band tests protect
PURPLE = (213, 112, 246)  # ~153 from magenta — same


@pytest.mark.parametrize(
    ("color", "key", "expected"),
    [
        ((8, 162, 24), GREEN, True),
        ((10, 150, 30), GREEN, True),
        ((20, 120, 25), GREEN, True),  # brightness is free
        ((3, 248, 5), GREEN, True),
        ((170, 8, 180), MAGENTA, True),
        ((251, 2, 248), MAGENTA, True),
        ((0, 255, 255), GREEN, False),  # cyan: blue is not dark
        ((120, 255, 120), GREEN, False),  # washed out: unkeyed channels too high
        ((30, 60, 30), GREEN, False),  # too dark to read as a lit key channel
        (HOT_PINK, MAGENTA, False),
        (PURPLE, MAGENTA, False),
        ((255, 0, 128), MAGENTA, False),  # keyed channels out of balance
        ((248, 247, 242), GREEN, False),
        ((8, 162, 24), (128, 128, 128), False),  # degenerate key has no family
    ],
)
def test_is_key_family(color: tuple[int, int, int], key: tuple[int, int, int], expected: bool) -> None:
    assert is_key_family(color, key) is expected


def test_detect_returns_declared_key_when_background_is_exact_despite_border_fringe() -> None:
    """A few antialiased fringe pixels on the border must not drag the key off — mode, not mean."""
    img = Image.new("RGBA", (64, 48), GREEN + (255,))
    px = img.load()
    for x in range(0, 64, 3):  # sparse family-coloured fringe along the top border
        px[x, 0] = (10, 200, 20, 255)
    assert detect_background_key_rgb(img, GREEN) == GREEN


def test_detect_picks_dominant_cluster_not_midpoint() -> None:
    """Two family clusters on the border: the dominant one wins outright."""
    img = Image.new("RGBA", (60, 60), (8, 162, 24, 255))
    px = img.load()
    for y in range(60):
        for x in range(0, 10):  # minority stripe of a different green
            px[x, y] = (5, 230, 10, 255)
    assert detect_background_key_rgb(img, GREEN) == (8, 162, 24)


def test_detect_ignores_family_coloured_subject_when_background_dominates() -> None:
    """A green-family subject touching the borders does not become the key."""
    img = Image.new("RGBA", (120, 120), GREEN + (255,))
    px = img.load()
    for y in range(0, 120):  # subject column touching top and bottom borders
        for x in range(50, 70):
            px[x, y] = (20, 140, 30, 255)
    assert detect_background_key_rgb(img, GREEN) == GREEN


def test_detect_returns_declared_key_without_family_background() -> None:
    img = Image.new("RGBA", (40, 40), (248, 247, 242, 255))
    assert detect_background_key_rgb(img, GREEN) == GREEN
    assert detect_background_key_rgb(img, (128, 128, 128)) == (128, 128, 128)


def test_detect_skips_transparent_border_pixels() -> None:
    img = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    px = img.load()
    for y in range(8, 32):
        for x in range(8, 32):
            px[x, y] = (8, 162, 24, 255)
    # Only transparent pixels are sampled → nothing to detect from.
    assert detect_background_key_rgb(img, GREEN) == GREEN


def test_background_key_opt_out_reproduces_single_key_behaviour() -> None:
    """`background_key=<declared>` pins the pre-detection result (the byte-identity gate relies on it)."""
    source = make_flat_key_still((8, 162, 24))
    pinned = remove_chroma_background(
        source, GREEN, _EXTRACT_KEY_THRESHOLD, _EXTRACT_FRINGE_THRESHOLD, _EXTRACT_FRINGE_DELTA,
        background_key=GREEN,
    )
    assert audit(pinned, source)[0] == SIZE * SIZE - 64 * 64  # every background pixel survives


def test_cutout_auto_routes_dark_green_corners_to_extract(tmp_path: Path) -> None:
    source = make_flat_key_still((8, 162, 24))
    src_path = tmp_path / "dark-green.png"
    source.save(src_path)
    stats = cutout(src_path, tmp_path / "out.png", key="auto")
    assert stats["route"] == "extract:green"
    assert stats["chroma_key"] == [0, 255, 0]
    assert stats["chroma_key_painted"] == [8, 162, 24]
    assert audit(Image.open(tmp_path / "out.png"), source) == (0, 0)


def test_cutout_auto_route_uses_the_engine_family_rule() -> None:
    """Route detection and the engine share one family rule: key variants → extract, subject colours → matte."""
    from sprite_gen.frames.cutout import _detect_key_kind

    assert _detect_key_kind((8, 162, 24)) == "green"
    assert _detect_key_kind((170, 8, 180)) == "magenta"
    assert _detect_key_kind(HOT_PINK) == "white"
    assert _detect_key_kind((248, 247, 242)) == "white"
