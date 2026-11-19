# SPDX-License-Identifier: Apache-2.0
"""Subject profile — resolution-aware sparse-frame validation defaults.

The sparse-frame floor exists to catch empty frames and extraction debris.
"How many opaque pixels count as debris" is a property of the *subject*, not
of arithmetic: a character is expected to occupy more pixels than an effect,
but neither expectation is an absolute number independent of resolution.

`sprite-request.json` declares `"subject": "character" | "effect"`; no field
means character. The floor is derived from the cell geometry:

    equivalent_side = ceil(sqrt(width * height))
    character floor = equivalent_side
    effect floor    = ceil(equivalent_side / 2)

The geometric mean extends the same rule to rectangular cells without picking
one axis as truth. It deliberately uses the declared cell rather than safe
margins: integer margin rounding can grow by two pixels when a cell grows by
one, which would make a supposedly resolution-scaled floor decrease. Scaling a
cell by k scales the floor by k, not k². An explicit `--min-used-pixels` always
wins.
"""

from __future__ import annotations

from math import isqrt
from typing import Any

SUBJECT_CHARACTER, SUBJECT_EFFECT = SUBJECTS = ("character", "effect")
SUBJECT_DEFAULT = SUBJECT_CHARACTER

# Multipliers over the cell's equivalent side. Effect is half the character
# floor at the formula level; integer ceilings can differ by 1.
SUBJECT_FLOOR_RATIOS = {SUBJECT_CHARACTER: (1, 1), SUBJECT_EFFECT: (1, 2)}


def subject_kind(request: dict[str, Any]) -> str:
    """The run's declared subject; absent field = character (legacy)."""
    kind = request.get("subject", SUBJECT_DEFAULT)
    if kind not in SUBJECTS:
        raise SystemExit(
            f"unknown subject kind: {kind!r} (expected one of {', '.join(SUBJECTS)})"
        )
    return kind


def cell_area(request: dict[str, Any]) -> int:
    """Declared cell area, validated without a silent clamp."""
    cell = request.get("cell")
    if not isinstance(cell, dict):
        raise SystemExit("sprite-request.json cell must be an object")
    width = int(cell.get("width", cell.get("size", 0)))
    height = int(cell.get("height", cell.get("size", 0)))
    if width <= 0 or height <= 0:
        raise SystemExit("sprite-request.json cell width/height must be positive")
    return width * height


def _ceil_ratio_sqrt(area: int, numerator: int, denominator: int) -> int:
    """Return ceil(numerator/denominator * sqrt(area)) with integer math."""
    scaled = area * numerator * numerator
    root = isqrt(scaled)
    if root * root < scaled:
        root += 1
    return (root + denominator - 1) // denominator


def default_min_used_pixels(request: dict[str, Any]) -> int:
    kind = subject_kind(request)
    numerator, denominator = SUBJECT_FLOOR_RATIOS[kind]
    return _ceil_ratio_sqrt(cell_area(request), numerator, denominator)


def sparse_frame_error(index: int, nontransparent: int, floor: int, kind: str) -> str:
    """Failure text that names the remedy — an unexplained rejection reads as a
    generation failure and gets the run abandoned (measured: 7 effect runs)."""
    remedy = (
        'small-subject run? declare "subject": "effect" in sprite-request.json, '
        "or pass --min-used-pixels"
        if kind == SUBJECT_DEFAULT
        else "pass --min-used-pixels if this frame is legitimate"
    )
    return (
        f"frame {index:02d} is empty or too sparse "
        f"({nontransparent} pixels < {floor}, {kind} profile floor — {remedy})"
    )
