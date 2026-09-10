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
