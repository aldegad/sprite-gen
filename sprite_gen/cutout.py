# SPDX-License-Identifier: Apache-2.0
"""Background cutout for externally authored (imported) images — the unified
post-edit entry point.

The generation pipeline already renders onto a solid magenta/green key and keys
it out (`gen/chroma.py` / `extract.py`), so its own output never needs this.
`cutout` is the import/post-edit utility: an image that arrived *with* an opaque
uniform background (a hand-drawn icon, a downloaded sprite, a messenger
screenshot) is turned into a clean transparent RGBA here.

It routes on the corner background colour:

- **white / ivory / bright achromatic** → position-based matte (this module).
  A colour-only key would punch holes in the object's own bright highlights, so
  a corner flood-fill marks the *connected* background by position; the border
  gets a decontaminated soft alpha (`F = (P - (1-a)*B) / a`) + soft erode.
- **magenta / green key colour** → the project's verified chroma engine
  `extract.remove_chroma_background` is reused as-is (no reimplementation, no
  drift). Key colours are absent from real objects, so that engine's colour-only
  cut is safe there — the exact case a white matte's position gard is *not*
  needed. Empirically (2026-07-20) feeding an ivory key to the extract engine
  holes out bench stone / lamp glass; the two routes are deliberate.

Sibling note: `unmix_key_blend`/`_matte_ycc` in `extract.py` and the matte
despill here are the same "despill + soft edge" idea in two colour spaces
(extract = CbCr chroma projection for saturated keys; here = RGB unmix for
achromatic backgrounds). Keep them coherent when either edge path changes.

No Silent Fallback: a transparent pixel that still carries non-zero RGB raises
loudly (same contract as `gen/chroma.py`).
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from PIL import Image

# Matte defaults converged on a 4-icon reference set (tree/bench/flower/lamp, ivory bg).
STRENGTH_DEFAULT = 45  # LO: colour distance at/below which a border pixel is fully background
HI_OFFSET = 60  # HI = STRENGTH + HI_OFFSET: distance at/above which a pixel is fully opaque
BAND_DEFAULT = 6  # only pixels within this many px of the border get alpha/decontam; deeper interior is preserved verbatim
ERODE_DEFAULT = 1.0  # soft-erode radius (fractional part shaves the last ring to partial alpha)
CHROMA_TOLERANCE = 26  # flood-fill background membership: max per-channel diff from the estimated bg colour
CORNER_MIN_BRIGHT = 225  # a corner pixel must be at least this bright (min channel) to seed the bg colour estimate

# Key colours reused from the generation contract (chroma.py KEYS). The extract
# engine keys these out; cutout only detects them and routes.
KEY_TARGETS: dict[str, tuple[int, int, int]] = {
    "magenta": (255, 0, 255),
    "green": (0, 255, 0),
}
# extract.remove_chroma_background params — the extract CLI defaults (SSoT: extract.py argparse).
_EXTRACT_KEY_THRESHOLD = 96.0
_EXTRACT_FRINGE_THRESHOLD = 180.0
_EXTRACT_FRINGE_DELTA = 18.0

# 3-primary verification backgrounds — white fringe/spill shows loudest on these.
WHITE_CHECK_COLORS: dict[str, tuple[int, int, int]] = {
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "yellow": (255, 255, 0),
}


def estimate_background(image: Image.Image) -> tuple[int, int, int]:
    """Average of the bright, near-achromatic corner pixels (the uniform bg colour).

    Only corners that are bright (min channel >= CORNER_MIN_BRIGHT) and non-transparent
    are sampled, so a corner that is already transparent or covered by the object does
    not poison the estimate. Falls back to ivory when no corner qualifies.
    """
    width, height = image.size
    px = image.load()
    corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
    samples = []
    for x, y in corners:
        r, g, b, a = px[x, y]
        if a > 0 and min(r, g, b) >= CORNER_MIN_BRIGHT:
            samples.append((r, g, b))
    if not samples:
        return (248, 247, 242)
    n = len(samples)
    return (
        sum(s[0] for s in samples) // n,
        sum(s[1] for s in samples) // n,
        sum(s[2] for s in samples) // n,
    )


def _corner_average(image: Image.Image) -> tuple[int, int, int]:
    """Raw average of the non-transparent corner pixels — no brightness filter.

    Used for route detection so a saturated key colour (magenta/green, which the
    bright-only `estimate_background` skips) is still seen.
    """
    width, height = image.size
    px = image.load()
    samples = [
        px[x, y][:3]
        for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
        if px[x, y][3] > 0
    ]
    if not samples:
        return (248, 247, 242)
    n = len(samples)
    return tuple(sum(s[i] for s in samples) // n for i in range(3))  # type: ignore[return-value]


def _detect_key_kind(color: tuple[int, int, int]) -> str:
    """Classify a corner background colour into a route: magenta | green | white.

    Saturated magenta/green keys route to the extract engine; everything else
    (bright achromatic ivory/white, or any low-saturation background) routes to
    the position-based matte.
    """
    r, g, b = color
    if r >= 128 and b >= 128 and g <= 100 and (r - g) >= 60 and (b - g) >= 60:
        return "magenta"
    if g >= 128 and r <= 100 and b <= 100 and (g - r) >= 60 and (g - b) >= 60:
        return "green"
    return "white"


def _background_mask(px: Any, width: int, height: int, bg: tuple[int, int, int], tol: int) -> bytearray:
    """Corner flood-fill: 1 where a pixel is transparent or within `tol` of `bg` AND connected to a corner."""
    mask = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def is_bg(x: int, y: int) -> bool:
        r, g, b, a = px[x, y]
        if a == 0:
            return True
        return max(abs(r - bg[0]), abs(g - bg[1]), abs(b - bg[2])) <= tol

    for cx, cy in [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]:
        if is_bg(cx, cy) and not mask[cy * width + cx]:
            mask[cy * width + cx] = 1
            queue.append((cx, cy))
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and not mask[ny * width + nx] and is_bg(nx, ny):
                mask[ny * width + nx] = 1
                queue.append((nx, ny))
    return mask


def _border_distance(mask: bytearray, width: int, height: int, band: int) -> list[int]:
    """BFS distance (capped at `band`) from any background pixel into the object."""
    dist = [band + 1] * (width * height)
    queue: deque[tuple[int, int]] = deque()
    for i in range(width * height):
        if mask[i]:
            dist[i] = 0
            queue.append((i % width, i // width))
    while queue:
        x, y = queue.popleft()
        d0 = dist[y * width + x]
        if d0 >= band:
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and dist[ny * width + nx] > d0 + 1:
                dist[ny * width + nx] = d0 + 1
                queue.append((nx, ny))
    return dist


def _soft_erode(image: Image.Image, amount: float) -> None:
    """Shave `amount` px off the alpha edge in place. Fractional part = last ring to partial alpha."""
    if amount <= 0:
        return
    width, height = image.size
    px = image.load()
    full = int(amount)
    frac = amount - full

    def edge_pixels() -> list[tuple[int, int]]:
        out = []
        for y in range(height):
            for x in range(width):
                if not px[x, y][3]:
                    continue
                if (
                    (x > 0 and px[x - 1, y][3] == 0)
                    or (x < width - 1 and px[x + 1, y][3] == 0)
                    or (y > 0 and px[x, y - 1][3] == 0)
                    or (y < height - 1 and px[x, y + 1][3] == 0)
                ):
                    out.append((x, y))
        return out

    for _ in range(full):
        for x, y in edge_pixels():
            px[x, y] = (0, 0, 0, 0)
    if frac > 0:
        for x, y in edge_pixels():
            r, g, b, a = px[x, y]
            px[x, y] = (r, g, b, round(a * (1 - frac)))


def _clamp(value: int) -> int:
    return 0 if value < 0 else 255 if value > 255 else value


def _matte_route(
    image: Image.Image,
    input_path: Path,
    strength: int,
    band: int,
    erode: float,
    tolerance: int,
) -> tuple[Image.Image, dict[str, Any]]:
    """White/achromatic background → position flood-fill + decontaminated soft alpha."""
    width, height = image.size
    src = image.load()
    bg = estimate_background(image)
    mask = _background_mask(src, width, height, bg, tolerance)
    if not any(mask):
        raise SystemExit(
            f"cutout: no background located from corners in {input_path} "
            f"(estimated bg {bg}); is the border actually a uniform colour?"
        )

    dist = _border_distance(mask, width, height, band)
    hi = strength + HI_OFFSET
    span = hi - strength

    result = Image.new("RGBA", (width, height))
    dst = result.load()
    keyed = decontaminated = 0
    for y in range(height):
        for x in range(width):
            i = y * width + x
            r, g, b, a = src[x, y]
            if mask[i]:
                dst[x, y] = (0, 0, 0, 0)
                keyed += 1
                continue
            if dist[i] <= band:
                d = max(abs(r - bg[0]), abs(g - bg[1]), abs(b - bg[2]))
                alpha_f = 0.0 if d <= strength else (1.0 if d >= hi else (d - strength) / span)
                if alpha_f <= 0:
                    dst[x, y] = (0, 0, 0, 0)
                    keyed += 1
                    continue
                if alpha_f < 1.0:
                    r = _clamp(round((r - (1 - alpha_f) * bg[0]) / alpha_f))
                    g = _clamp(round((g - (1 - alpha_f) * bg[1]) / alpha_f))
                    b = _clamp(round((b - (1 - alpha_f) * bg[2]) / alpha_f))
                    decontaminated += 1
                dst[x, y] = (r, g, b, round(alpha_f * 255))
            else:
                dst[x, y] = (r, g, b, 255)

    _soft_erode(result, erode)
    return result, {
        "route": "matte",
        "background": list(bg),
        "strength": strength,
        "band": band,
        "erode": erode,
        "keyed_pixels": keyed,
        "decontaminated_pixels": decontaminated,
    }


def _extract_route(image: Image.Image, kind: str) -> tuple[Image.Image, dict[str, Any]]:
    """Magenta/green key background → reuse the verified `extract` chroma engine (no drift)."""
    from sprite_gen.extract import remove_chroma_background

    target = KEY_TARGETS[kind]
    result = remove_chroma_background(
        image, target, _EXTRACT_KEY_THRESHOLD, _EXTRACT_FRINGE_THRESHOLD, _EXTRACT_FRINGE_DELTA
    )
    return result.convert("RGBA"), {"route": f"extract:{kind}", "chroma_key": list(target)}


def cutout(
    input_path: Path,
    out_path: Path,
    *,
    key: str = "auto",
    strength: int = STRENGTH_DEFAULT,
    band: int = BAND_DEFAULT,
    erode: float = ERODE_DEFAULT,
    tolerance: int = CHROMA_TOLERANCE,
    white_check_dir: Path | None = None,
) -> dict[str, Any]:
    """Cut a uniform-background imported image to a clean transparent RGBA PNG.

    `key`: "auto" (detect from corners) | "white" (matte) | "magenta" | "green"
    (reuse the extract chroma engine). Returns a stats dict. Raises SystemExit if
    the key is unknown, the background cannot be located, or any transparent pixel
    keeps non-zero RGB (No Silent Fallback).
    """
    if key not in ("auto", "white", "magenta", "green"):
        raise SystemExit(f"cutout: unknown key {key!r}; expected one of auto|white|magenta|green")

    image = Image.open(input_path).convert("RGBA")
    width, height = image.size

    route = _detect_key_kind(_corner_average(image)) if key == "auto" else key
    if route in ("magenta", "green"):
        result, route_stats = _extract_route(image, route)
    else:
        result, route_stats = _matte_route(image, input_path, strength, band, erode, tolerance)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(out_path)

    # No Silent Fallback verification.
    check = result.load()
    total = width * height
    alpha_zero = stale_rgb = 0
    for y in range(height):
        for x in range(width):
            r, g, b, a = check[x, y]
            if a == 0:
                alpha_zero += 1
                if r or g or b:
                    stale_rgb += 1
    if stale_rgb:
        raise SystemExit(
            f"cutout: transparent pixels still contain non-zero RGB ({stale_rgb} px) in {out_path}"
        )

    stats: dict[str, Any] = {
        "out": str(out_path),
        "mode": "RGBA",
        "size": f"{width}x{height}",
        "key": key,
        **route_stats,
        "alpha_zero_pct": round(alpha_zero / total * 100, 2) if total else 0.0,
    }

    if white_check_dir is not None:
        white_check_dir.mkdir(parents=True, exist_ok=True)
        stem = out_path.stem
        checks = []
        for name, color in WHITE_CHECK_COLORS.items():
            plate = Image.new("RGBA", (width, height), color + (255,))
            plate.alpha_composite(result)
            check_path = white_check_dir / f"{stem}_{name}.png"
            plate.convert("RGB").save(check_path)
            checks.append(str(check_path))
        stats["white_check"] = checks

    return stats


def add_arguments(p: Any) -> None:
    p.add_argument("input", type=Path, help="imported image with a uniform (white/ivory or magenta/green key) background")
    p.add_argument("--out", type=Path, help="output PNG (default: <input>_cutout.png)")
    p.add_argument(
        "--key",
        choices=["auto", "white", "magenta", "green"],
        default="auto",
        help="background type; auto detects from corners (white→matte, magenta/green→extract engine)",
    )
    p.add_argument(
        "--strength",
        type=int,
        default=STRENGTH_DEFAULT,
        help=f"matte LO: bg colour-distance cutoff; higher removes brighter bevel (default {STRENGTH_DEFAULT})",
    )
    p.add_argument("--band", type=int, default=BAND_DEFAULT, help=f"matte edge processing depth in px (default {BAND_DEFAULT})")
    p.add_argument("--erode", type=float, default=ERODE_DEFAULT, help=f"matte soft erode radius in px (default {ERODE_DEFAULT})")
    p.add_argument(
        "--tolerance",
        type=int,
        default=CHROMA_TOLERANCE,
        help=f"matte flood-fill bg membership tolerance (default {CHROMA_TOLERANCE})",
    )
    p.add_argument(
        "--white-check",
        dest="white_check",
        action="store_true",
        help="also write cyan/magenta/yellow verification composites next to the output",
    )


def run(**kwargs: object) -> int:
    input_path = Path(kwargs["input"])  # type: ignore[arg-type]
    out = kwargs.get("out")
    out_path = Path(out) if out else input_path.with_name(f"{input_path.stem}_cutout.png")  # type: ignore[arg-type]
    white_check = bool(kwargs.get("white_check"))
    stats = cutout(
        input_path,
        out_path,
        key=str(kwargs.get("key", "auto")),  # type: ignore[arg-type]
        strength=int(kwargs.get("strength", STRENGTH_DEFAULT)),  # type: ignore[arg-type]
        band=int(kwargs.get("band", BAND_DEFAULT)),  # type: ignore[arg-type]
        erode=float(kwargs.get("erode", ERODE_DEFAULT)),  # type: ignore[arg-type]
        tolerance=int(kwargs.get("tolerance", CHROMA_TOLERANCE)),  # type: ignore[arg-type]
        white_check_dir=out_path.parent if white_check else None,
    )
    import json

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0
