# SPDX-License-Identifier: Apache-2.0
"""Subject profile — what kind of thing this run draws, and the validation
defaults that follow from it.

The sparse-frame floor exists to catch empty frames and extraction debris.
"How many opaque pixels count as debris" is a property of the *subject*, not
of arithmetic: a character is expected to fill its cell (the historical 400px
default, tuned on 256px character cells), while a small effect — a bone
shard, an orb, a talisman — is legitimately sparse. Measured on a real
project: seven 64px-cell effect runs whose frames carried 70–208 opaque
pixels each were all extracted correctly and then rejected wholesale by the
character floor (400px on a 64px cell demands 9.8% coverage — a 20x20 solid
block). The runs sat abandoned for months; the assets were fine.

So the floor is declared, not computed: `sprite-request.json` may carry
`"subject": "character" | "effect"`. No field means `character` — legacy runs
and their outputs are byte-identical. An explicit `--min-used-pixels` always
wins over the profile.

`effect` floor = 48: below the smallest legitimate effect frame observed in
production (70px), above typical chroma debris (< 20px).
"""

from __future__ import annotations

from typing import Any

SUBJECT_DEFAULT = "character"
SUBJECTS = ("character", "effect")

# Sparse-frame floor per subject kind (opaque pixels per extracted frame).
MIN_USED_PIXELS = {"character": 400, "effect": 48}


def subject_kind(request: dict[str, Any]) -> str:
    """The run's declared subject; absent field = character (legacy)."""
    kind = request.get("subject", SUBJECT_DEFAULT)
    if kind not in SUBJECTS:
        raise SystemExit(
            f"unknown subject kind: {kind!r} (expected one of {', '.join(SUBJECTS)})"
        )
    return kind


def default_min_used_pixels(request: dict[str, Any]) -> int:
    return MIN_USED_PIXELS[subject_kind(request)]


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
