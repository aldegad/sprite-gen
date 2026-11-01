# SPDX-License-Identifier: Apache-2.0
"""Clear the faint alpha haze of a transparent sheet; keep every object and its anti-aliased edge.

A pixel at or above ``floor`` is solid. A pixel above zero and below the floor is faint. A faint
pixel within ``edge`` pixels of a solid pixel (8-connected: diagonals count) belongs to an
object's anti-aliased edge and is kept as it is. Every other faint pixel is haze and becomes
``(0, 0, 0, 0)``. Nothing is blended or resampled: a pixel is kept byte for byte or cleared.

Used by `slice-sheet --alpha-floor` (docs/sheet-slicing.md "Alpha cleanup"). The report measures
what changed instead of asserting it.
"""

from __future__ import annotations

from numbers import Integral
from typing import Any

from PIL import Image

from sprite_gen._deps import np

MAX_EDGE = 64


def _setting(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{name} must be an integer")
    if not low <= int(value) <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return int(value)


def near(mask: Any, reach: int) -> Any:
    """True where a True pixel of ``mask`` lies within ``reach`` pixels, diagonals included."""
    if reach == 0:
        return mask.copy()
    height, width = mask.shape
    padded = np.pad(mask, reach)
    across = np.zeros((height + 2 * reach, width), dtype=bool)
    for dx in range(2 * reach + 1):
        across |= padded[:, dx:dx + width]
    result = np.zeros((height, width), dtype=bool)
    for dy in range(2 * reach + 1):
        result |= across[dy:dy + height]
    return result


def clear_haze(image: Image.Image, *, floor: int = 64, edge: int = 2) -> tuple[Image.Image, dict[str, Any]]:
    """Return a cleaned RGBA copy of ``image`` and the cleanup report; ``image`` is not changed."""
    floor = _setting(floor, "alpha floor", 1, 255)
    edge = _setting(edge, "alpha edge", 0, MAX_EDGE)
    before = np.array(image.convert("RGBA"))
    pixels = before.copy()
    alpha = before[..., 3]
    solid = alpha >= floor
    faint = (alpha > 0) & ~solid
    by_solid = near(solid, edge)
    haze = faint & ~by_solid
    pixels[haze] = 0
    changed = (pixels != before).any(axis=-1)
    after = pixels[..., 3]
    after_faint = (after > 0) & (after < floor)
    after_haze = after_faint & ~near(after >= floor, edge)
    total = alpha.size

    def share(mask: Any) -> float:
        return float(np.count_nonzero(mask) / total) if total else 0.0

    report = {
        "floor": floor,
        "edge_px": edge,
        "pixels": int(total),
        "faint_fraction_before": share(faint),
        "faint_fraction_after": share(after_faint),
        "haze_fraction_before": share(haze),
        "haze_fraction_after": share(after_haze),
        "cleared_pixels": int(np.count_nonzero(changed & haze)),
        "changed_solid_pixels": int(np.count_nonzero(changed & solid)),
        "changed_edge_pixels": int(np.count_nonzero(changed & faint & by_solid)),
    }
    return Image.fromarray(pixels), report
