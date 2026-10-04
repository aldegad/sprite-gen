# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-follow` — a soft part of a looping body follows the body's motion.

A clip model draws a walk's body moving, but a soft part that hangs off it (a chest, a belly,
a pouch on a strap) mostly moves with the body as one piece, even when the prompt asks it to
bounce: at sprite size it reads as rigid. This puts that follow-through back after the loop is
cut.

Each frame of the strip is a cell of one cycle. The body's motion is read off the cells: the
row of the crown (up and down) and the middle of the head (side to side). A part hung on the
body is a damped mass: its offset x from where the body carries it answers the body's own
acceleration, x'' + 2·ζ·ω·x' + ω²·x = −body''. The loop repeats, so the answer is the periodic
steady state, solved per harmonic of the cycle: no start-up, no kick at a foot strike, and the
last frame leads into the first. `--gain` scales that physical answer and nothing else; it is
not normalised to a target size.

The part is an ellipse in the first cell (`--region cx,cy,rx,ry`, cell pixels), carried with
the body's bob. Inside it every pixel is moved by the offset times a weight that is 1 at the
centre and falls to 0 at the rim (cos²); outside it no pixel changes. The move is sampled as
premultiplied bilinear colour, so an edge never picks up the colour under a transparent pixel.
A move so large that the weight's slope folds the picture over (offset × π / (2 · radius) ≥ 1)
is refused.

The strip as it was before is kept as `follow.source.png` beside it; running the command again
reads from there, so a second follow-through never moves a moved strip. `video-loop` (a new
cut) and `video-cycle-align` (a new cycle) remove it, and the alignment records that the
follow-through was cleared. docs/video-pipeline.md section 6.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
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
HARMONICS = 6  # of the cycle, for the body's motion: a step's shape, not its noise
ALPHA_SOLID = 128
HEAD_SHARE = 0.07  # of the body's height under the crown: where the head's middle is read
SOURCE = loop_mod.FOLLOW_SOURCE


def parse_region(text: str) -> tuple[float, float, float, float]:
    try:
        cx, cy, rx, ry = (float(v) for v in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--region wants cx,cy,rx,ry (four numbers), got {text!r}") from exc
    if rx <= 0 or ry <= 0:
        raise argparse.ArgumentTypeError(f"--region radii must be positive, got {text!r}")
    return cx, cy, rx, ry


def body_motion(cells: list[Image.Image]) -> tuple[np.ndarray, np.ndarray, int]:
    """Per cell: the crown's row and the head's middle column; and the body's height in cell 0."""
    rows, cols = [], []
    height0 = 0
    for k, cell in enumerate(cells):
        solid = np.asarray(cell.getchannel("A")) >= ALPHA_SOLID
        ys = np.nonzero(solid.any(axis=1))[0]
        if not len(ys):
            raise ValueError(f"cell {k} has no solid body")
        top, bottom = int(ys[0]), int(ys[-1])
        if k == 0:
            height0 = bottom - top
        band = solid[top: top + max(8, round(HEAD_SHARE * (bottom - top)))]
        xs = np.nonzero(band.any(axis=0))[0]
        rows.append(top)
        cols.append((xs[0] + xs[-1]) / 2)
    return np.asarray(rows, float), np.asarray(cols, float), height0


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


def _sample(premultiplied: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    h, w = premultiplied.shape[:2]
    x0 = np.clip(np.floor(sx).astype(int), 0, w - 2)
    y0 = np.clip(np.floor(sy).astype(int), 0, h - 2)
    fx = np.clip(sx - x0, 0, 1)[..., None]
    fy = np.clip(sy - y0, 0, 1)[..., None]
    a, b = premultiplied[y0, x0], premultiplied[y0, x0 + 1]
    c, d = premultiplied[y0 + 1, x0], premultiplied[y0 + 1, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def move_regions(cell: Image.Image, regions: list[tuple[float, float, float, float]], shift: tuple[float, float],
                 offset: tuple[float, float]) -> Image.Image:
    """`cell` with each region moved by `offset` (dx, dy), its centre carried by `shift`."""
    src = np.asarray(cell.convert("RGBA"), dtype=np.float32)
    h, w = src.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    weight = np.zeros((h, w), np.float32)
    for cx, cy, rx, ry in regions:
        r = np.sqrt(((xx - cx - shift[0]) / rx) ** 2 + ((yy - cy - shift[1]) / ry) ** 2)
        weight = np.maximum(weight, np.where(r < 1, np.cos(r * math.pi / 2) ** 2, 0))
    inside = weight > 0
    if not inside.any():
        return cell.copy()
    pm = src.copy()
    pm[..., :3] *= pm[..., 3:4] / 255
    moved = _sample(pm, xx - offset[0] * weight, yy - offset[1] * weight)
    alpha = np.clip(np.round(moved[..., 3:4]), 0, 255)
    # Colour is read back from the premultiplied mix, and none is kept where the coverage
    # rounds to nothing (the WebP check refuses colour under alpha 0).
    moved[..., :3] = np.where(alpha > 0, moved[..., :3] * 255 / np.maximum(moved[..., 3:4], 1e-3), 0)
    moved[..., 3:4] = alpha
    out = np.where(inside[..., None], np.clip(np.round(moved), 0, 255), src).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def follow_loop(loop_dir: Path, regions: list[tuple[float, float, float, float]], *, gain: float = GAIN_DEFAULT,
                freq: float = FREQ_DEFAULT, zeta: float = ZETA_DEFAULT, board: Path | None = None) -> dict[str, Any]:
    loop_dir = loop_dir.expanduser().resolve()
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
        rows, cols, height0 = body_motion(cells)
    except ValueError as exc:
        raise SystemExit(f"video-follow: {exc}") from exc
    dy = follow_offsets(rows, fps, freq=freq, zeta=zeta, gain=gain)
    dx = follow_offsets(cols, fps, freq=freq, zeta=zeta, gain=gain)
    reach = float(np.max(np.hypot(dx, dy)))
    smallest = min(min(rx, ry) for _, _, rx, ry in regions)
    if reach * math.pi / (2 * smallest) >= 1:
        raise SystemExit(f"video-follow: a move of {reach:.1f} px folds a region of radius {smallest:g} px over itself; "
                         "lower --gain or give the region larger radii")
    out = [move_regions(c, regions, (cols[k] - cols[0], rows[k] - rows[0]), (dx[k], dy[k])) for k, c in enumerate(cells)]
    joined = Image.new("RGBA", (w * n, h), (0, 0, 0, 0))
    for k, c in enumerate(out):
        joined.alpha_composite(c, (k * w, 0))
    joined.save(strip_path)
    delay_ms = max(20, round(float(meta["delay_ms"])))
    gif_path, webp_path = loop_dir / f"{name}.gif", loop_dir / f"{name}.webp"
    save_clean_gif(out, gif_path, duration_ms=delay_ms, loop=0, alpha_threshold=128)
    loop_mod.write_webp(out, webp_path, delay_ms=delay_ms, workdir=loop_dir / ".webp-frames")
    shutil.rmtree(loop_dir / ".webp-frames", ignore_errors=True)
    record = {
        "regions": [list(r) for r in regions], "gain": gain, "freq_hz": freq, "zeta": zeta, "harmonics": HARMONICS,
        "source": SOURCE, "body_px": height0,
        "body_bob_px": [round(float(np.ptp(cols)), 2), round(float(np.ptp(rows)), 2)],
        "dx_px": np.round(dx, 2).tolist(), "dy_px": np.round(dy, 2).tolist(),
        "reach_px": round(reach, 2),
        "gif": loop_mod.verify_animation(gif_path, expect_frames=n, check_stale=False),
        "webp": loop_mod.verify_animation(webp_path, expect_frames=n, check_stale=True),
    }
    atomic_write_text(meta_path, json.dumps({**meta, "follow": record}, indent=2) + "\n")
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
    parser.add_argument("--freq", type=float, default=FREQ_DEFAULT, help=f"the part's own frequency in Hz (default {FREQ_DEFAULT:g})")
    parser.add_argument("--zeta", type=float, default=ZETA_DEFAULT, help=f"damping ratio (default {ZETA_DEFAULT:g}: lags and settles, no ringing)")
    parser.add_argument("--board", type=Path, help="write a before/after picture at the frames where the part sits lowest and highest")


def run(**kwargs: object) -> int:
    result = follow_loop(Path(str(kwargs["loop_dir"])), list(kwargs["region"]),  # type: ignore[arg-type]
                         gain=float(kwargs.get("gain", GAIN_DEFAULT)), freq=float(kwargs.get("freq", FREQ_DEFAULT)),  # type: ignore[arg-type]
                         zeta=float(kwargs.get("zeta", ZETA_DEFAULT)), board=kwargs.get("board"))  # type: ignore[arg-type]
    print(json.dumps({k: result[k] for k in ("strip", "regions", "gain", "body_bob_px", "reach_px")}
                     | ({"board": result["board"]} if "board" in result else {}), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-follow", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
