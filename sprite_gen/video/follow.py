# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-follow` — a soft part of a looping body follows the body's motion.

A clip model draws a walk's body moving, but a soft part that hangs off it (a chest, a belly,
a pouch on a strap) mostly moves with the body as one piece, even when the prompt asks it to
bounce: at sprite size it reads as rigid. This puts that follow-through back after the loop is
cut.

Each frame of the strip is a cell of one cycle. The body's motion is read off the cells: how far
each cell's body lies from the first cell's. The silhouette is worn down from a quarter to half
of its depth in the first cell, so what swings on the body — a tail, a ponytail, an ear, a sword
or a rod held up, the legs and arms — is worn away and the head and torso are left; each cell is
laid where it overlaps the first cell most, worn alike, a pixel counting once for every level it
is still in. (Read off the top of the body instead, the motion was whatever came to the top: a
tail tip or a raised sword made it jump by the part's whole swing from one cell to the next.) A part
hung on the body is a damped mass: its offset x from where the body carries it answers the body's own
acceleration, x'' + 2·ζ·ω·x' + ω²·x = −body''. The loop repeats, so the answer is the periodic
steady state, solved per harmonic of the cycle: no start-up, no kick at a foot strike, and the
last frame leads into the first. `--gain` scales that physical answer and nothing else; it is
not normalised to a target size.

The part is an ellipse in the first cell (`--region cx,cy,rx,ry`, cell pixels), carried with
the body's bob. Inside it every pixel is moved by the offset times a weight that is 1 at the
centre and falls to 0 at the rim (cos²); outside it no pixel changes. The move is sampled as
premultiplied bilinear colour, so an edge never picks up the colour under a transparent pixel.
A move so large that the weight's slope folds the picture over (offset × π / (2 · radius) ≥ 1)
is refused. `--on-fold lower` gives each region the largest gain that does not fold it instead
(the offset grows in a straight line with the gain, so that gain is known without a search), and
never one under 1, the mass as measured: a region too small for that is held — it does not move,
and the record names it — and the strip is refused only when every region is.

The strip as it was before is kept as `follow.source.png` beside it; running the command again
reads from there, so a second follow-through never moves a moved strip. `video-loop` (a new
cut) and `video-cycle-align` (a new cycle) remove it, and the alignment records that the
follow-through was cleared. docs/video-pipeline.md section 6.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.video import loop as loop_mod

FREQ_DEFAULT = 2.4  # Hz: a slow, soft part
ZETA_DEFAULT = 0.6  # damping ratio: it lags and settles, with no ringing
GAIN_DEFAULT = 2.5  # times the physical answer
GAIN_MEASURED = 1.0  # the mass as measured: `--on-fold lower` does not go under it
GAIN_STEP = 0.01  # a lowered gain is a whole number of these, so the report's `gain` is the `--gain` to pass
ON_FOLD_MODES = ("refuse", "lower")
HARMONICS = 6  # of the cycle, for the body's motion: a step's shape, not its noise
ALPHA_SOLID = 128
WORN_FROM, WORN_TO = 0.25, 0.5  # of the body's depth in the first cell: the levels its silhouette is worn down to
SOURCE = loop_mod.FOLLOW_SOURCE


def parse_region(text: str) -> tuple[float, float, float, float]:
    try:
        cx, cy, rx, ry = (float(v) for v in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--region wants cx,cy,rx,ry (four numbers), got {text!r}") from exc
    if rx <= 0 or ry <= 0:
        raise argparse.ArgumentTypeError(f"--region radii must be positive, got {text!r}")
    return cx, cy, rx, ry


def _run_depth(solid: np.ndarray, axis: int) -> np.ndarray:
    """Per solid pixel, how far along `axis` the nearest clear pixel is (1 beside one; past the cell's edge is clear)."""
    m = np.moveaxis(solid, axis, -1)
    n = m.shape[-1]
    at = np.broadcast_to(np.arange(n), m.shape)
    before = np.maximum.accumulate(np.where(m, -1, at), axis=-1)
    after = np.minimum.accumulate(np.where(m, n, at)[..., ::-1], axis=-1)[..., ::-1]
    return np.moveaxis(np.where(m, np.minimum(at - before, after - at), 0), -1, axis)


def wear(solid: np.ndarray, r: int) -> np.ndarray:
    """`solid` worn down by r: the pixels whose square of side 2·r + 1 around them is solid."""
    return _run_depth(_run_depth(solid, 1) > r, 0) > r


def depth(solid: np.ndarray) -> int:
    """How deep the body is at its deepest: the most it is worn down by and still has a pixel, plus one."""
    lo, hi = 0, (min(solid.shape) + 1) // 2  # worn by lo something is left; worn by hi nothing is
    if not solid.any():
        return 0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if wear(solid, mid).any() else (lo, mid)
    return lo + 1


def _levels(solid: np.ndarray, worn: range) -> tuple[list[np.ndarray], int, int] | None:
    """`solid` worn down by each of `worn`, cut to the box of the first (each lies inside the one before),
    and that box's top and left; None when nothing is left of it worn down by the first."""
    first = wear(solid, worn[0])
    if not first.any():
        return None
    ys, xs = np.nonzero(first.any(axis=1))[0], np.nonzero(first.any(axis=0))[0]
    level = first[ys[0]: ys[-1] + 1, xs[0]: xs[-1] + 1]
    levels = [level]
    for _ in worn[1:]:
        # Worn down by one more pixel: a square of side 2·r + 3 is one of side 2·r + 1 grown by one each way.
        across = np.pad(level, 1)
        level = across[1:-1, :-2] & across[1:-1, 1:-1] & across[1:-1, 2:]
        down = np.pad(level, 1)
        level = down[:-2, 1:-1] & down[1:-1, 1:-1] & down[2:, 1:-1]
        levels.append(level)
    return levels, int(ys[0]), int(xs[0])


def _fft_length(n: int) -> int:
    """The first length from n on with no prime factor over 5: an FFT of it is fast."""
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


def _overlay(levels0: tuple[list[np.ndarray], int, int], levels: tuple[list[np.ndarray], int, int]) -> tuple[int, int]:
    """The shift (dx, dy) of a cell from cell 0: where cell 0's levels, moved by it, land on the cell's own most,
    the pixels that land counted at every level and summed."""
    (a, ya, xa), (b, yb, xb) = levels0, levels
    # Long enough that no move wraps round (a.h + b.h - 1 each way, and so on), and fast.
    shape = (_fft_length(a[0].shape[0] + b[0].shape[0] - 1), _fft_length(a[0].shape[1] + b[0].shape[1] - 1))
    # overlap[i, j]: how many pixels of each of cell 0's levels land on the cell's same level moved by
    # (j - (a.w - 1), i - (a.h - 1)), summed over the levels, for every move that meets.
    spectrum = sum(np.fft.rfft2(lb.astype(np.float64), shape) * np.fft.rfft2(la[::-1, ::-1].astype(np.float64), shape)
                   for la, lb in zip(a, b))
    # Counts of pixels are whole numbers: rounded, the FFT's own rounding (not the same on every machine) is
    # gone, so a tie is a tie on every machine, and it goes to the move nearest none.
    overlap = np.fft.irfft2(spectrum, shape)[: a[0].shape[0] + b[0].shape[0] - 1, : a[0].shape[1] + b[0].shape[1] - 1]
    overlap = np.rint(overlap).astype(np.int64)
    shifts = [(int(j) - (a[0].shape[1] - 1) + xb - xa, int(i) - (a[0].shape[0] - 1) + yb - ya)
              for i, j in np.argwhere(overlap == overlap.max())]
    return min(shifts, key=lambda s: (s[0] ** 2 + s[1] ** 2, s[1], s[0]))


def body_motion(cells: list[Image.Image]) -> tuple[np.ndarray, np.ndarray, int, list[int]]:
    """Per cell: how far its body lies from cell 0's, down and across (px); the body's height in cell 0,
    and the first and last level its silhouette is worn down to.

    The top of the body is whatever comes to the top — a tail tip, a ponytail, an ear, a sword held
    up — and read there the motion jumps from cell to cell. What swings on the body is thinner than
    the body it swings on: worn down by a quarter of the body's depth it is gone, and what is left
    is the head and torso. Each cell is laid where its silhouette, worn down by every whole number
    of pixels from a quarter to half of that depth, overlaps cell 0's, worn alike, most, the
    overlaps of every level summed: a pixel counts once for each level it is still in, so the
    deepest of the body counts most and nothing that comes and goes outweighs it, and a torso that
    is shallower in one cell (an arm swung away from it) still counts at its shallower levels."""
    solids = []
    for k, cell in enumerate(cells):
        solid = np.asarray(cell.getchannel("A")) >= ALPHA_SOLID
        if not solid.any():
            raise ValueError(f"cell {k} has no solid body")
        solids.append(solid)
    ys = np.nonzero(solids[0].any(axis=1))[0]
    height0 = int(ys[-1] - ys[0])
    deepest = depth(solids[0])
    first = int(WORN_FROM * deepest)
    worn = range(first, max(first + 1, int(WORN_TO * deepest)))
    levels0 = _levels(solids[0], worn)
    rows, cols = [], []
    for k, solid in enumerate(solids):
        levels = _levels(solid, worn)
        if levels is None:
            raise ValueError(f"cell {k} has no body as deep as a quarter of cell 0's ({worn[0]} px)")
        dx, dy = _overlay(levels0, levels)
        rows.append(dy)
        cols.append(dx)
    return np.asarray(rows, float), np.asarray(cols, float), height0, [worn[0], worn[-1]]


def follow_offsets(motion: np.ndarray, fps: float, *, freq: float, zeta: float, gain: float) -> np.ndarray:
    """The part's offset from where the body carries it, per cell, in the cycle's steady state."""
    n = len(motion)
    spectrum = np.fft.rfft(motion - motion.mean())
    k = np.arange(len(spectrum))
    spectrum[k > HARMONICS] = 0
    omega = 2 * math.pi * fps / n * k  # each harmonic's angular frequency
    w = 2 * math.pi * freq
    # x = H·y with y the body's position: x'' + 2ζw x' + w² x = −y''  ⇒  H = Ω² / (w² − Ω² + 2iζwΩ)
    response = omega ** 2 / (w ** 2 - omega ** 2 + 2j * zeta * w * omega)
    return gain * np.fft.irfft(spectrum * response, n)


def fold_ratio(reach: float, radius: float) -> float:
    """How near a move of `reach` px is to folding a region whose smaller radius is `radius`: at 1
    the steepest part of the weight (cos², slope π / (2 · radius)) stops the picture, past it the
    picture runs backwards."""
    return reach * math.pi / (2 * radius)


def gain_limit(reach_per_gain: float, radius: float) -> float | None:
    """The gain at which a region of that radius folds; no gain folds it when the body is still."""
    return 2 * radius / (math.pi * reach_per_gain) if reach_per_gain > 0 else None


def lowered_gain(gain: float, reach_per_gain: float, radius: float) -> float:
    """The largest gain, in steps of GAIN_STEP and no more than `gain`, that `fold_ratio` passes."""
    limit = gain_limit(reach_per_gain, radius)
    if limit is None:
        return gain
    steps = math.ceil(limit / GAIN_STEP) - 1  # under the limit, never on it
    # The step count is rounded from floats: the one test that decides is `fold_ratio` itself.
    while steps > 0 and fold_ratio(steps * GAIN_STEP * reach_per_gain, radius) >= 1:
        steps -= 1
    return min(gain, round(steps * GAIN_STEP, 2))


def _sample(premultiplied: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    h, w = premultiplied.shape[:2]
    x0 = np.clip(np.floor(sx).astype(int), 0, w - 2)
    y0 = np.clip(np.floor(sy).astype(int), 0, h - 2)
    fx = np.clip(sx - x0, 0, 1)[..., None]
    fy = np.clip(sy - y0, 0, 1)[..., None]
    a, b = premultiplied[y0, x0], premultiplied[y0, x0 + 1]
    c, d = premultiplied[y0 + 1, x0], premultiplied[y0 + 1, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def move_regions(cell: Image.Image, moves: list[tuple[list[tuple[float, float, float, float]], tuple[float, float]]],
                 shift: tuple[float, float]) -> Image.Image:
    """`cell` with each group of regions moved by its own offset (dx, dy), their centres carried by `shift`.

    The groups' offsets are one motion times their gains, so where regions of different groups
    overlap the larger move wins: the move is the largest of the groups' weighted moves, which is
    nowhere steeper than the steepest group's own."""
    src = np.asarray(cell.convert("RGBA"), dtype=np.float32)
    h, w = src.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    weights = []
    for regions, _ in moves:
        weight = np.zeros((h, w), np.float32)
        for cx, cy, rx, ry in regions:
            r = np.sqrt(((xx - cx - shift[0]) / rx) ** 2 + ((yy - cy - shift[1]) / ry) ** 2)
            weight = np.maximum(weight, np.where(r < 1, np.cos(r * math.pi / 2) ** 2, 0))
        weights.append(weight)
    inside = np.logical_or.reduce([weight > 0 for weight in weights])
    if not inside.any():
        return cell.copy()
    largest = np.argmax(np.stack([math.hypot(*offset) * weight for (_, offset), weight in zip(moves, weights)]), axis=0)[None]
    move_x = np.take_along_axis(np.stack([offset[0] * weight for (_, offset), weight in zip(moves, weights)]), largest, 0)[0]
    move_y = np.take_along_axis(np.stack([offset[1] * weight for (_, offset), weight in zip(moves, weights)]), largest, 0)[0]
    pm = src.copy()
    pm[..., :3] *= pm[..., 3:4] / 255
    moved = _sample(pm, xx - move_x, yy - move_y)
    alpha = np.clip(np.round(moved[..., 3:4]), 0, 255)
    # Colour is read back from the premultiplied mix, and none is kept where the coverage
    # rounds to nothing (the WebP check refuses colour under alpha 0).
    moved[..., :3] = np.where(alpha > 0, moved[..., :3] * 255 / np.maximum(moved[..., 3:4], 1e-3), 0)
    moved[..., 3:4] = alpha
    out = np.where(inside[..., None], np.clip(np.round(moved), 0, 255), src).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def follow_loop(loop_dir: Path, regions: list[tuple[float, float, float, float]], *, gain: float = GAIN_DEFAULT,
                freq: float = FREQ_DEFAULT, zeta: float = ZETA_DEFAULT, board: Path | None = None,
                on_fold: str = "refuse") -> dict[str, Any]:
    loop_dir = loop_dir.expanduser().resolve()
    # The numbers go into the record, which is JSON, and into the cells' arithmetic: a numpy number from a
    # caller (an int64 has no JSON form) is read as the float the command line gives, so every caller of the
    # same numbers writes the same strip and record.
    regions = [tuple(float(v) for v in region) for region in regions]
    gain, freq, zeta = float(gain), float(freq), float(zeta)
    metas = sorted(loop_dir.glob("*.strip.json"))
    if len(metas) != 1:
        raise SystemExit(f"video-follow: {loop_dir}: expected one <name>.strip.json from video-loop, found {len(metas)}")
    meta_path = metas[0]
    name = meta_path.name[: -len(".strip.json")]
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("kind") == "one-shot" or meta.get("loop") is False:
        raise SystemExit("video-follow: a one-shot plays once; the follow-through is a loop's steady state")
    if not regions:
        raise SystemExit("video-follow: at least one --region cx,cy,rx,ry")
    if gain < 0 or freq <= 0 or not 0 < zeta:
        raise SystemExit("video-follow: --gain must be 0 or more, --freq and --zeta above 0")
    if on_fold not in ON_FOLD_MODES:
        raise SystemExit(f"video-follow: unknown --on-fold {on_fold!r}; expected one of {', '.join(ON_FOLD_MODES)}")
    strip_path = loop_dir / f"{name}.strip.png"
    source = loop_dir / SOURCE
    if not source.exists():
        shutil.copyfile(strip_path, source)
    strip = Image.open(source).convert("RGBA")
    n, w, h = int(meta["frames"]), int(meta["w"]), int(meta["h"])
    if strip.size != (w * n, h):
        raise SystemExit(f"video-follow: {source.name} is {strip.size[0]}x{strip.size[1]}, the strip meta says {w * n}x{h}; "
                         "cut the loop again (video-loop)")
    for cx, cy, rx, ry in regions:
        if not (0 <= cx < w and 0 <= cy < h):
            raise SystemExit(f"video-follow: --region centre {cx:g},{cy:g} is outside the {w}x{h} cell")
    cells = [strip.crop((k * w, 0, (k + 1) * w, h)) for k in range(n)]
    fps = 1000.0 / float(meta["delay_ms"])
    try:
        rows, cols, height0, worn = body_motion(cells)
    except ValueError as exc:
        raise SystemExit(f"video-follow: {exc}") from exc
    dy = follow_offsets(rows, fps, freq=freq, zeta=zeta, gain=gain)
    dx = follow_offsets(cols, fps, freq=freq, zeta=zeta, gain=gain)
    reach = float(np.max(np.hypot(dx, dy)))
    radii = [min(rx, ry) for _, _, rx, ry in regions]
    smallest = min(radii)
    requested, reach_requested = gain, reach
    # The offset is the gain times the answer at gain 1, so one move decides every gain.
    reach_per_gain = float(np.max(np.hypot(follow_offsets(cols, fps, freq=freq, zeta=zeta, gain=1.0),
                                           follow_offsets(rows, fps, freq=freq, zeta=zeta, gain=1.0))))
    gains, held = [requested] * len(regions), [False] * len(regions)
    if fold_ratio(reach, smallest) >= 1:
        if on_fold == "refuse":
            raise SystemExit(f"video-follow: a move of {reach:.1f} px folds a region of radius {smallest:g} px over itself; "
                             "lower --gain or give the region larger radii")
        # A gain for each region: every part hangs on the same body and answers the same motion, but
        # how far it moves before it folds is its own size, so a small part (an ear) does not hold a
        # large one (a chest) back. A region that does not fold keeps the gain asked for.
        folds = [fold_ratio(reach, radius) >= 1 for radius in radii]
        gains = [lowered_gain(requested, reach_per_gain, radius) if fold else requested for radius, fold in zip(radii, folds)]
        # One that would have to go under the mass as measured does not move at all; the strip is
        # refused only when that is every region.
        held = [fold and g < GAIN_MEASURED for fold, g in zip(folds, gains)]
        if all(held):
            k = radii.index(max(radii))  # the largest folds at the largest gain: if it is held, every region is
            every = f" (the largest of {len(regions)} regions: every one folds)" if len(regions) > 1 else ""
            raise SystemExit(f"video-follow: a move of {reach:.1f} px folds a region of radius {radii[k]:g} px over itself{every}, and "
                             f"--on-fold lower would have to go under --gain {GAIN_MEASURED:g} (the mass as measured moves "
                             f"{reach_per_gain:.1f} px; the largest gain that does not fold is {gains[k]:g}); give the region larger radii")
        gains = [0.0 if hold else g for g, hold in zip(gains, held)]
    # One move per gain: a region's offset is its gain times the answer at gain 1. The strip's `gain`
    # is the largest any region moves by, and `dx_px`/`dy_px` and `reach_px` are that gain's.
    moves = {g: (follow_offsets(cols, fps, freq=freq, zeta=zeta, gain=g), follow_offsets(rows, fps, freq=freq, zeta=zeta, gain=g))
             for g, hold in zip(gains, held) if not hold}
    reaches = {g: float(np.max(np.hypot(*move))) for g, move in moves.items()}
    gain = max(moves)
    dx, dy = moves[gain]
    reach = reaches[gain]
    ratios = [0.0 if hold else fold_ratio(reaches[g], radius) for radius, g, hold in zip(radii, gains, held)]
    groups = [([r for r, rg, hold in zip(regions, gains, held) if rg == g and not hold], move) for g, move in moves.items()]
    out = [move_regions(c, [(group, (mx[k], my[k])) for group, (mx, my) in groups], (cols[k] - cols[0], rows[k] - rows[0]))
           for k, c in enumerate(cells)]
    joined = Image.new("RGBA", (w * n, h), (0, 0, 0, 0))
    for k, c in enumerate(out):
        joined.alpha_composite(c, (k * w, 0))
    delay_ms = max(20, round(float(meta["delay_ms"])))
    gif_path, webp_path = loop_dir / f"{name}.gif", loop_dir / f"{name}.webp"
    # Written and checked beside the loop first, each under its own name, and moved over the loop's files
    # only once the record is written too: a run that fails on the way (an animation that fails its check, a
    # record that does not serialise) leaves the strip, its animations and its record as they were. Staged as
    # files, not as payloads in memory (runio.atomic_write_set): img2webp writes a file, and the check reads one.
    stage = Path(tempfile.mkdtemp(prefix=".follow.", dir=loop_dir))
    try:
        staged = {path: stage / path.name for path in (strip_path, gif_path, webp_path, meta_path)}
        joined.save(staged[strip_path])
        save_clean_gif(out, staged[gif_path], duration_ms=delay_ms, loop=0, alpha_threshold=128)
        loop_mod.write_webp(out, staged[webp_path], delay_ms=delay_ms, workdir=stage / ".webp-frames")
        record = {
            "regions": [list(r) for r in regions], "gain": gain, "freq_hz": freq, "zeta": zeta, "harmonics": HARMONICS,
            "source": SOURCE, "body_px": height0, "body_worn_px": worn,
            "body_bob_px": [round(float(np.ptp(cols)), 2), round(float(np.ptp(rows)), 2)],
            "dx_px": np.round(dx, 2).tolist(), "dy_px": np.round(dy, 2).tolist(),
            "reach_px": round(reach, 2),
            "gain_requested": requested, "on_fold": on_fold,
            "fold": {
                "lowered": any(g != requested for g in gains), "ratio": round(max(ratios), 3),
                "reach_requested_px": round(reach_requested, 2), "reach_per_gain_px": round(reach_per_gain, 3),
                # Where the regions took gains of their own, each says the gain it moved by and whether it
                # was held still (gain 0); where they all took one, the strip's `gain` is every region's.
                "regions": [{"radius_px": radius, "ratio": round(ratio, 3),
                             "gain_limit": None if (limit := gain_limit(reach_per_gain, radius)) is None else round(limit, 3)}
                            | ({} if len(set(gains)) == 1 else {"gain": g, "held": hold})
                            for radius, ratio, g, hold in zip(radii, ratios, gains, held)],
            },
            "gif": loop_mod.verify_animation(staged[gif_path], expect_frames=n, check_stale=False),
            "webp": loop_mod.verify_animation(staged[webp_path], expect_frames=n, check_stale=True),
        }
        atomic_write_text(staged[meta_path], json.dumps({**meta, "follow": record}, indent=2) + "\n")
        for path, written in staged.items():  # the record last, as it was written last
            os.replace(written, path)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if board is not None:
        _board(cells, out, dy, board)
        record["board"] = str(board)
    return {"strip": str(strip_path), **record}


def _board(before: list[Image.Image], after: list[Image.Image], dy: np.ndarray, path: Path) -> None:
    """Before and after, side by side on white, at the frames where the part sits lowest and highest."""
    picks = [int(np.argmax(dy)), int(np.argmin(dy))]
    w, h = before[0].size
    board = Image.new("RGB", (w * 4 + 30, h), (128, 128, 128))
    x = 0
    for k in picks:
        for cell in (before[k], after[k]):
            ground = Image.new("RGBA", (w, h), (255, 255, 255, 255))
            ground.alpha_composite(cell)
            board.paste(ground.convert("RGB"), (x, 0))
            x += w + 10
    path.parent.mkdir(parents=True, exist_ok=True)
    board.save(path)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--loop-dir", required=True, type=Path, help="a video-loop output directory (after video-cycle-align, if the set is aligned)")
    parser.add_argument("--region", action="append", type=parse_region, required=True,
                        help="cx,cy,rx,ry: an ellipse over the soft part in the strip's first cell, in cell pixels (repeatable)")
    parser.add_argument("--gain", type=float, default=GAIN_DEFAULT, help=f"times the physical answer (default {GAIN_DEFAULT:g}; 1 is the mass as measured, 0 leaves the strip as it was)")
    parser.add_argument("--on-fold", choices=ON_FOLD_MODES, default="refuse",
                        help=f"a move that folds a region over itself: refuse (default), or lower — each region takes the largest gain that does "
                             f"not fold it, never under {GAIN_MEASURED:g} (the mass as measured): a region that would have to is held still and "
                             "named in the record, and the strip is refused only when every region is")
    parser.add_argument("--freq", type=float, default=FREQ_DEFAULT, help=f"the part's own frequency in Hz (default {FREQ_DEFAULT:g})")
    parser.add_argument("--zeta", type=float, default=ZETA_DEFAULT, help=f"damping ratio (default {ZETA_DEFAULT:g}: lags and settles, no ringing)")
    parser.add_argument("--board", type=Path, help="write a before/after picture at the frames where the part sits lowest and highest")


def run(**kwargs: object) -> int:
    result = follow_loop(Path(str(kwargs["loop_dir"])), list(kwargs["region"]),  # type: ignore[arg-type]
                         gain=float(kwargs.get("gain", GAIN_DEFAULT)), freq=float(kwargs.get("freq", FREQ_DEFAULT)),  # type: ignore[arg-type]
                         zeta=float(kwargs.get("zeta", ZETA_DEFAULT)), board=kwargs.get("board"),  # type: ignore[arg-type]
                         on_fold=str(kwargs.get("on_fold") or "refuse"))
    fold = result["fold"]
    own = "held" in fold["regions"][0]  # the regions took gains of their own
    if own:
        for (cx, cy, rx, ry), entry in zip(result["regions"], fold["regions"]):
            name = f"--region {cx:g},{cy:g},{rx:g},{ry:g}"
            if entry["held"]:
                print(f"video-follow: {name} folds at --gain {entry['gain_limit']:g}, under {GAIN_MEASURED:g} (the mass as measured): "
                      "it is held, and does not move", file=sys.stderr)
            elif entry["gain"] != result["gain_requested"]:
                print(f"video-follow: --gain {result['gain_requested']:g} folds {name} (a move of {fold['reach_requested_px']:g} px); "
                      f"lowered to --gain {entry['gain']:g} for it", file=sys.stderr)
    elif fold["lowered"]:
        print(f"video-follow: --gain {result['gain_requested']:g} folds a region (a move of {fold['reach_requested_px']:g} px); "
              f"lowered to --gain {result['gain']:g}", file=sys.stderr)
    print(json.dumps({k: result[k] for k in ("strip", "regions", "gain", "gain_requested", "on_fold", "body_bob_px", "reach_px")}
                     | ({"region_gains": [entry["gain"] for entry in fold["regions"]]} if own else {})
                     | ({"board": result["board"]} if "board" in result else {}), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-follow", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
