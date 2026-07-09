# SPDX-License-Identifier: Apache-2.0
"""Regression tests for soft-alpha edges and trapped interior chroma blends.

Reproduction (moe raws, 2026-07-08): a mint-green-hair character keyed on
magenta and a crimson-hair character keyed on green both extracted with

1. **Binary alpha** — ``remove_chroma_background`` either erases a pixel to
   ``(0, 0, 0, 0)`` or leaves it fully opaque; no partial alpha is ever
   produced, so every antialiased silhouette edge collapses into a hard
   staircase (mid-alpha pixel count was exactly 0 on both raws).
2. **Trapped key-tint residue** — key/subject blend pixels caught between
   hair strands survive cleanup. Measured on these fixtures: the survivors
   sit 1-4 layers from keyed-out pixels, but (a) dark blends drift 181-265
   color-distance from the key — outside the fringe band — so the band test
   rejects them, and (b) blend pockets run up to 4 layers deep while the old
   in-band peel only removed the nearest 2 layers.

The fixtures are 1/8-size NEAREST copies of the repro raws (1024x1536 ->
128x192), same recipe as ``tests/fixtures/accident/``:
``Image.open(raw).convert("RGB").resize((w // 8, h // 8), Image.NEAREST)``.
Source raws: moe-test ``raw-green.png`` (magenta key) / ``raw-red.png``
(green key), generated at cell 1024x1536, idle single frame.

The third test pins the v1.10.1 guardrail from the other side: on these
fixtures, pixels deeper than any plausible fringe peel must come out
byte-identical. The one sanctioned interior treatment is the trapped-spill
despill (fourth test): a *small* cluster with a strongly key-tinted pixel,
buried inside the subject, is generator spill and gets its color corrected
in place (alpha kept — no pinholes); marginally warm subject colors (skin)
and large key-tinted material regions are never touched.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import deque
from pathlib import Path

import pytest
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

# extract_sprite_row_frames.py main() defaults
KEY_THRESHOLD = 96.0
FRINGE_THRESHOLD = 180.0
FRINGE_DELTA = 18.0

# Blend pockets in the repro raws run up to 4 layers deep, so a fix may
# legitimately unmix beyond the old 2-layer in-band band for out-of-band
# pixels. Pixels beyond this margin from every keyed pixel are unambiguously
# subject interior and must never be altered, whatever the fix does at the
# boundary.
INTERIOR_MARGIN = 8

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "moe"

MAGENTA = (255, 0, 255)
GREEN = (0, 255, 0)

CASES = [
    pytest.param("moe_green.png", MAGENTA, id="magenta-key"),
    pytest.param("moe_red.png", GREEN, id="green-key"),
]


def _clean(image: Image.Image, chroma_key: tuple[int, int, int]) -> Image.Image:
    return extract.remove_chroma_background(
        image,
        chroma_key,
        threshold=KEY_THRESHOLD,
        fringe_threshold=FRINGE_THRESHOLD,
        fringe_delta=FRINGE_DELTA,
    )


def _open(name: str) -> Image.Image:
    with Image.open(FIXTURES / name) as opened:
        return opened.convert("RGBA")


def _is_key_tinted(color: tuple[int, int, int], chroma_key: tuple[int, int, int]) -> bool:
    """The plan's residue detector: every keyed channel clears every unkeyed
    channel by more than 40 (for magenta: r-g>40 and b-g>40)."""
    keyed = [index for index, value in enumerate(chroma_key) if value >= 192]
    unkeyed = [index for index, value in enumerate(chroma_key) if value < 64]
    return min(color[index] for index in keyed) - max(color[index] for index in unkeyed) > 40


def _keyed_distance_map(image: Image.Image, chroma_key: tuple[int, int, int]) -> list[int | None]:
    """8-connected BFS distance from every pixel to the nearest keyed pixel."""
    width, height = image.size
    pixels = image.load()
    distance: list[int | None] = [None] * (width * height)
    frontier: deque[int] = deque()
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha == 0 or extract.color_distance((red, green, blue), chroma_key) <= KEY_THRESHOLD:
                distance[y * width + x] = 0
                frontier.append(y * width + x)
    while frontier:
        index = frontier.popleft()
        x = index % width
        y = index // width
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    neighbor = ny * width + nx
                    if distance[neighbor] is None:
                        distance[neighbor] = distance[index] + 1
                        frontier.append(neighbor)
    return distance


@pytest.mark.parametrize("name, chroma_key", CASES)
def test_boundary_blend_pixels_get_partial_alpha(name: str, chroma_key) -> None:
    # ① Antialiased edges must survive as partial alpha. A hard binary cut
    # leaves zero mid-alpha pixels and the silhouette staircases.
    out = _clean(_open(name), chroma_key)
    histogram = out.getchannel("A").histogram()
    mid_alpha = sum(histogram[1:255])
    assert mid_alpha > 0, f"{name}: no partial alpha anywhere — edges are a binary cut"


@pytest.mark.parametrize("name, chroma_key", CASES)
def test_no_key_tint_residue_survives_cleanup(name: str, chroma_key) -> None:
    # ② Key/subject blends trapped between hair strands must not survive as
    # visible key-colored specks, whether they touch the outer background or
    # sit inside an enclosed hole.
    source = _open(name)
    source_pixels = source.load()
    distance = _keyed_distance_map(source, chroma_key)
    width, height = source.size
    trapped = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if 0 < (distance[y * width + x] or 0) <= 4
        and _is_key_tinted(source_pixels[x, y][:3], chroma_key)
    ]
    assert trapped, f"{name}: fixture lost its key-blend pixels; fixture is wrong"

    out = _clean(source, chroma_key).load()
    residue = [
        ((x, y), out[x, y][:3])
        for y in range(height)
        for x in range(width)
        if out[x, y][3] > 0 and _is_key_tinted(out[x, y][:3], chroma_key)
    ]
    assert not residue, f"{name}: {len(residue)} key-tint pixels survived, e.g. {residue[:5]}"


@pytest.mark.parametrize("name, chroma_key", CASES)
def test_deep_interior_subject_pixels_survive_untouched(name: str, chroma_key) -> None:
    # ③ v1.10.1 guardrail: on these fixtures every pixel beyond a plausible
    # peel of the keyed regions (outer background *and* interior holes) must
    # come out byte-identical — the fixtures carry no strong-tint spill
    # cluster deeper than INTERIOR_MARGIN, so nothing deeper may change. In
    # particular this pins that marginally key-leaning subject colors (skin
    # under a magenta key scores tint 19-21) are never "despilled".
    source = _open(name)
    source_pixels = source.load()
    distance = _keyed_distance_map(source, chroma_key)
    width, height = source.size
    interior = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if (distance[y * width + x] or 0) > INTERIOR_MARGIN
    ]
    assert interior, f"{name}: fixture has no deep-interior pixels; fixture is wrong"

    out = _clean(source, chroma_key).load()
    changed = [
        (x, y)
        for x, y in interior
        if out[x, y] != source_pixels[x, y]
    ]
    assert not changed, f"{name}: {len(changed)} deep-interior pixels altered, e.g. {changed[:5]}"


def test_trapped_spill_strand_is_despilled_in_place() -> None:
    # ④ Generator spill: a small, strongly key-tinted streak buried deep in
    # the subject (a green strand inside crimson hair at full resolution sits
    # 20+ px from any keyed pixel) is unreachable by any boundary peel. It
    # must be color-corrected in place — tint removed, alpha kept, so the
    # sprite gets no pinhole — while the untinted subject around it stays
    # byte-identical.
    subject = (60, 170, 70)
    spill = (190, 60, 170)  # dist ~123 from magenta (past the hard cut), tint 120
    assert extract.color_distance(spill, MAGENTA) > KEY_THRESHOLD
    assert extract.key_tint_score(spill, MAGENTA) > 40

    image = Image.new("RGBA", (64, 64), (*MAGENTA, 255))
    pixels = image.load()
    for y in range(4, 60):
        for x in range(4, 60):
            pixels[x, y] = (*subject, 255)
    streak = [(x, y) for y in range(28, 36) for x in range(30, 33)]  # 3x8, depth > 8
    for x, y in streak:
        pixels[x, y] = (*spill, 255)

    out = _clean(image, MAGENTA).load()
    for x, y in streak:
        red, green, blue, alpha = out[x, y]
        assert alpha == 255, f"spill despill must keep alpha, got {alpha} at {(x, y)}"
        assert not _is_key_tinted((red, green, blue), MAGENTA), f"tint survived at {(x, y)}"
    assert out[20, 32] == (*subject, 255)  # deep untinted subject untouched
