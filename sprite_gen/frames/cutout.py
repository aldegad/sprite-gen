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
  cut is safe there — the exact case a white matte's position guard is *not*
  needed. Empirically (2026-07-20) feeding an ivory key to the extract engine
  holes out bench stone / lamp glass; the two routes are deliberate.
- **any other flat colour** (a muted yellow-green, an off-cyan, a grey, a key
  behind dark edge strips) → the measured matte (`measured_matte.py`), which reads
  the colour, its spread and its shade off the image itself. A magenta or green
  request whose background the chroma engine does not recognise as that key goes
  there too (`extract_route`), instead of keying nothing.

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
from typing import TYPE_CHECKING, Any

from PIL import Image

from sprite_gen.frames import extract as extract_mod

if TYPE_CHECKING:
    from sprite_gen._deps import np

# Matte defaults converged on a 4-icon reference set (tree/bench/flower/lamp, ivory bg).
STRENGTH_DEFAULT = 45  # LO: colour distance at/below which a border pixel is fully background
HI_OFFSET = 60  # HI = STRENGTH + HI_OFFSET: distance at/above which a pixel is fully opaque
BAND_DEFAULT = 6  # only pixels within this many px of the border get alpha/decontam; deeper interior is preserved verbatim
ERODE_DEFAULT = 1.0  # soft-erode radius (fractional part shaves the last ring to partial alpha)
CHROMA_TOLERANCE = 26  # flood-fill background membership: max per-channel diff from the estimated bg colour
CORNER_MIN_BRIGHT = 225  # a corner pixel must be at least this bright (min channel) to seed the bg colour estimate

# The key colours, the one table: gen's `chroma.KEYS` is built from it, and the logo matte and
# slice-sheet key by it. The extract engine keys these out; cutout only detects them and routes.
MAGENTA, GREEN = "magenta", "green"
KEY_TARGETS: dict[str, tuple[int, int, int]] = {
    MAGENTA: (255, 0, 255),
    GREEN: (0, 255, 0),
}
# `--key`: auto reads the corners; white is the position matte; a key colour is the chroma engine;
# measured is the measured matte.
KEYS = ("auto", "white", *KEY_TARGETS, "measured")
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
    bright-only `estimate_background` skips) is still seen. Any mode: only the four
    corner pixels are converted to RGBA, so a large still is not converted whole.
    """
    width, height = image.size
    corners = [
        image.crop((x, y, x + 1, y + 1)).convert("RGBA").getpixel((0, 0))
        for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
    ]
    samples = [pixel[:3] for pixel in corners if pixel[3] > 0]
    if not samples:
        return (248, 247, 242)
    n = len(samples)
    return tuple(sum(s[i] for s in samples) // n for i in range(3))  # type: ignore[return-value]


def _bright_corners(image: Image.Image) -> bool:
    """Whether a corner is bright and achromatic enough for the white matte's estimate
    (`estimate_background`): otherwise the white matte would key against its ivory guess."""
    width, height = image.size
    for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        r, g, b, a = image.crop((x, y, x + 1, y + 1)).convert("RGBA").getpixel((0, 0))
        if a > 0 and min(r, g, b) >= CORNER_MIN_BRIGHT:
            return True
    return False


def _detect_key_kind(color: tuple[int, int, int]) -> str:
    """Classify a corner background colour into a route: magenta | green | white.

    Saturated magenta/green keys — at whatever brightness or balance the model
    painted them (`extract.is_border_key_candidate`, the border rule the engine's
    detector shares: a corner colour is background evidence) — route to the
    extract engine; everything else (bright achromatic ivory/white, or any
    low-saturation background) routes to the position-based matte.
    """
    from sprite_gen.frames.extract import is_border_key_candidate

    for kind, target in KEY_TARGETS.items():
        if is_border_key_candidate(color, target):
            return kind
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


def key_recognised(image: Image.Image, kind: str) -> tuple[bool, list[int] | None]:
    """Whether the chroma engine would key ``image``'s background as the ``kind`` key: (recognised,
    the measured background colour or None).

    The background is measured on the ring inside any edge strip (`measured_matte.measure_background`).
    It is the key when that colour is a border key candidate for it (`extract.is_border_key_candidate`,
    the detector's own rule) or within the hard cut of the pure key; a background nothing on the ring
    holds is left to the engine, whose detector falls back to the declared key as before."""
    from sprite_gen._deps import np
    from sprite_gen.frames.extract import is_border_key_candidate
    from sprite_gen.frames.measured_matte import measure_background

    measured = measure_background(np.asarray(image.convert("RGB"), dtype=np.float32))
    if measured is None:
        return True, None
    colour = tuple(int(round(v)) for v in measured["colour"])
    target = KEY_TARGETS[kind]
    distance = sum((a - b) ** 2 for a, b in zip(colour, target)) ** 0.5
    return is_border_key_candidate(colour, target) or distance <= _EXTRACT_KEY_THRESHOLD, list(colour)


def _measured_route(image: Image.Image, decontam: str, requested: str) -> tuple[Image.Image, dict[str, Any]]:
    """The measured matte, its stats carrying what was asked for and what the edge pass did."""
    from sprite_gen.frames.measured_matte import measured_matte

    if decontam == extract_mod.PALETTE:
        raise SystemExit(f"cutout: --decontam palette needs a chroma key background (magenta/green); "
                         f"the background was keyed as measured (asked: {requested})")
    result, stats = measured_matte(image)
    stats["requested_key"] = requested
    if requested in KEY_TARGETS:
        stats["chroma_key"] = list(KEY_TARGETS[requested])
        stats["chroma_key_painted"] = list(stats["background"])
    if decontam == extract_mod.AUTO:
        stats["decontam"] = {"mode": extract_mod.AUTO, "applied": False,
                             "reason": "the measured matte unmixes its own edge against the measured background"}
    return result, stats


def extract_route(image: Image.Image, kind: str, *, spill_max_fraction: float | None = None,
                  spill_min_tint: float | None = None,
                  spill_require_hue: bool = False, decontam: str = extract_mod.OFF, decontam_fit: str = "still",
                  decontam_palette: dict[str, Any] | None = None, decontam_edge_band: int = 0,
                  background_key: tuple[int, int, int] | None = None,
                  decontam_history: dict[str, Any] | None = None,
                  decontam_not_subject: np.ndarray | None = None,
                  measure_unrecognised: bool = True) -> tuple[Image.Image, dict[str, Any]]:
    """Magenta/green key background → reuse the verified `extract` chroma engine (no drift).

    The engine keys from the background colour it detects on the borders
    (`detect_background_key_rgb`) as well as the pure key, so the brightness
    the model happened to paint does not decide whether the cut lands. The
    detected colour is reported as `chroma_key_painted` for the audit trail.
    `background_key` hands in a colour detected elsewhere instead: a crop keyed as part
    of a larger image (`extract.subject_window`) takes the whole image's, whose borders
    it no longer has.
    Public because `video-canvas` normalizes a still's background through this
    exact matte, so the canvas and `video-frames` agree by construction.
    `decontam` is the engine's edge decontamination pass (`extract.remove_chroma_background`);
    what it did comes back under `decontam` in the stats. `decontam_history` is a clip's record of
    its last frame for the video fit, handed on to the pass, and `decontam_not_subject` the pixels the
    pass learns the subject's palette without (`decontaminate`'s `not_subject`).

    A background the engine does not recognise as the ``kind`` key (`key_recognised`: a muted or
    shifted colour, or a key behind dark edge strips) is keyed by the measured matte instead, its
    stats `route` "measured" and `chroma_key_painted` the measured colour; before it, such a
    background was keyed against the pure key and nothing came off (2026-10-26).
    `measure_unrecognised=False` keeps the engine on it anyway: `video-frames` refuses such a clip
    and sends its still to `video-canvas`, so every frame of a clip is keyed from one exact key.
    """
    from sprite_gen.frames.extract import detect_background_key_rgb, remove_chroma_background

    target = KEY_TARGETS[kind]
    if background_key is None and measure_unrecognised and not key_recognised(image, kind)[0]:
        return _measured_route(image, decontam, kind)
    if background_key is None:
        painted = detect_background_key_rgb(image, target, _EXTRACT_KEY_THRESHOLD)
    else:
        painted = (int(background_key[0]), int(background_key[1]), int(background_key[2]))
    extra: dict[str, Any] = {}
    if spill_max_fraction is not None:
        extra["spill_max_fraction"] = spill_max_fraction
    if spill_min_tint is not None:
        extra["spill_min_tint"] = spill_min_tint
    if spill_require_hue:
        extra["spill_require_hue"] = True
    decontam_stats: dict[str, Any] = {}
    if decontam != extract_mod.OFF:
        extra.update(decontam=decontam, decontam_fit=decontam_fit, decontam_palette=decontam_palette,
                     decontam_stats=decontam_stats, decontam_edge_band=decontam_edge_band,
                     decontam_history=decontam_history, decontam_not_subject=decontam_not_subject)
    # the colour detected above, not a second detection of the same borders
    result = remove_chroma_background(
        image, target, _EXTRACT_KEY_THRESHOLD, _EXTRACT_FRINGE_THRESHOLD, _EXTRACT_FRINGE_DELTA,
        background_key=painted, **extra
    )
    stats: dict[str, Any] = {
        "route": f"extract:{kind}",
        "chroma_key": list(target),
        "chroma_key_painted": list(painted),
    }
    if decontam != extract_mod.OFF:
        stats["decontam"] = decontam_stats
    return result.convert("RGBA"), stats


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
    spill_max_fraction: float | None = None,
    spill_min_tint: float | None = None,
    spill_require_hue: bool = False,
    decontam: str = extract_mod.OFF,
    decontam_fit: str = "still",
    decontam_palette: dict[str, Any] | None = None,
    decontam_edge_band: int = 0,
    decontam_history: dict[str, Any] | None = None,
    decontam_not_subject: np.ndarray | None = None,
    measure_unrecognised: bool = True,
    refuse_empty: bool = True,
) -> dict[str, Any]:
    """Cut a uniform-background imported image to a clean transparent RGBA PNG.

    `key`: "auto" (detect from corners) | "white" (matte) | "magenta" | "green"
    (reuse the extract chroma engine) | "measured" (the measured matte, any flat colour).
    Auto sends a corner that is neither a key nor bright and achromatic to the measured
    matte, and a magenta/green background the engine does not recognise goes there too
    (`extract_route`). `spill_max_fraction` overrides the chroma
    engine's trapped-spill cluster cap and `spill_min_tint` its tint bar (None = the
    engine default for either). `decontam` is the engine's edge decontamination
    pass: "off" (the default) keeps the matte as it is; "auto" runs it on chroma routes
    where it applies and records why not elsewhere; "palette" demands it and fails where
    it cannot run (the white matte has no key colour to remove). `decontam_edge_band`
    keeps what the matte left transparent along the top, left and right edges
    transparent (`video-frames` passes its edge band), and `decontam_history` carries a
    clip's last frame to the next (`video-frames` keeps one per clip). `decontam_not_subject` is the
    pixels the pass learns the subject's palette without (`video-frames --prop-plate` passes the
    plate's place on the frame it learns it on). `measure_unrecognised`
    is handed to `extract_route`, and `refuse_empty=False` leaves a cut with nothing transparent
    to the caller (`video-frames` passes both False: it refuses such frames itself, with its own
    report and guidance). Returns a stats dict. Raises SystemExit if the key is unknown, the background cannot be
    located, nothing was made transparent, or any transparent pixel keeps non-zero RGB
    (No Silent Fallback).
    """
    if key not in KEYS:
        raise SystemExit(f"cutout: unknown key {key!r}; expected one of auto|white|magenta|green|measured")
    if decontam not in extract_mod.DECONTAM_MODES:
        raise SystemExit(f"cutout: unknown decontam {decontam!r}; expected off|auto|palette")

    image = Image.open(input_path).convert("RGBA")
    width, height = image.size

    route = _detect_key_kind(_corner_average(image)) if key == "auto" else key
    if key == "auto" and route == "white" and not _bright_corners(image):
        route = "measured"
    if route == "measured":
        result, route_stats = _measured_route(image, decontam, key)
    elif route in KEY_TARGETS:
        result, route_stats = extract_route(image, route, spill_max_fraction=spill_max_fraction,
                                            spill_min_tint=spill_min_tint, spill_require_hue=spill_require_hue,
                                            decontam=decontam, decontam_fit=decontam_fit,
                                            decontam_palette=decontam_palette, decontam_edge_band=decontam_edge_band,
                                            decontam_history=decontam_history, decontam_not_subject=decontam_not_subject,
                                            measure_unrecognised=measure_unrecognised)
    else:
        if decontam == extract_mod.PALETTE:
            raise SystemExit(f"cutout: --decontam {decontam} needs a chroma key background (magenta/green); "
                             f"{input_path} routed to the white matte, which has no key colour to remove")
        result, route_stats = _matte_route(image, input_path, strength, band, erode, tolerance)
        if decontam == extract_mod.AUTO:
            route_stats["decontam"] = {"mode": extract_mod.AUTO, "applied": False,
                                       "reason": "the white matte has no key colour to remove"}

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
    if refuse_empty and not alpha_zero:
        raise SystemExit(
            f"cutout: nothing of {input_path} was made transparent (route {route_stats.get('route')}); "
            "refusing a successful cutout that keyed no background"
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
    p.add_argument("input", type=Path, help="imported image with a flat background of any colour (white, a key, a muted green...)")
    p.add_argument("--out", type=Path, help="output PNG (default: <input>_cutout.png)")
    p.add_argument(
        "--key",
        choices=KEYS,
        default="auto",
        help="background type; auto detects from corners (white→matte, magenta/green→extract engine, any other "
        "colour→measured matte); a magenta/green background the engine does not recognise is measured too",
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
    p.add_argument(
        "--decontam",
        choices=extract_mod.DECONTAM_MODES,
        default=extract_mod.OFF,
        help="edge decontamination on chroma routes: re-explain key-tinted edges (hair strands, outlines) "
        "with the subject's own colours. off (default) keeps the chroma engine's output; auto runs it "
        "where it applies and records why not elsewhere; palette demands it",
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
        decontam=str(kwargs.get("decontam") or extract_mod.OFF),
    )
    import json

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0
