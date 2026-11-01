# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-stop` — a walk or run that stops, from the clip's own start played backwards.

A walk or run clip filmed from a standing still (image to video) opens on that still: the body stands,
sets off, and runs into the gait the loop is cut from. Played backwards from the frame where the clip
reaches the loop, that start is the body running into the standing still — filmed frames, no frame
made between two others, ending on the clip's first frame, the still as filmed. This cuts it as a
one-shot clip beside the loop: a strip, its sidecar (`loop: false`, `hold_last: true`), a GIF, a WebP
and a report.

- **The rest** is the clip's opening run of frames within SAME_POSE steps of its first frame (the engine
  pose distance, `gait_fallback.poses`; a step is the clip's median step between neighbouring frames in
  the loop's window, the loop as filmed, whether the loop given is that cut or aligned from it). The stop
  ends on the first frame itself.
- **The entry** is the first frame after the rest within SAME_POSE of one of the loop's cells, searched
  to the end of the loop's window (`source_cut`): where the clip reaches the loop. The stop leaves the
  loop from the cell nearest to it (`from_cell`) — a player shows the loop through that cell, then the
  stop — and its first frame is the clip frame before the entry. The seam, from that cell into that
  frame, is reported in steps; past the jump line (SEAM_MAX, `--seam-max`) the stop is refused.
- **Another motion**: a setting-off goes one way, from the still into the loop; the body does not come
  back to a pose it has left. A run-up frame within SAME_POSE of an earlier frame of the clip — the still,
  or a pose of the run-up — after the body went TURN_BACK_AWAY steps or more from it in between is a
  motion that turned back: a gesture that ends where it began, a motion done again, a run going round a
  cycle of its own. The stop would play it backwards, and it is refused, its frames named (`turn_back`,
  the nearest such comeback, is reported either way). This is the one judgment of another motion, and the
  run-up's length is no part of it. A motion carried one way into the run (an arm drawn in, a crouch, a
  turn before the first stride) is the setting-off's own and is not refused: the pose distance does not
  tell it from a gesture carried one way.
- **The run-up** is what lies between the rest and the entry, the frames the stop plays before the
  still. A body sets off into its gait within about a cycle; a run-up longer than RUN_UP_MAX_CYCLES of
  the clip's own cycles (`--run-up-max`) plays strides unlike the loop's — a run of its own before the
  loop's, or a start slower than a setting-off — and it is refused.
- **Drift**: the clip's body may move across its frame as it sets off, and played back as filmed the stop
  would slide that far into the still. Two frames are laid where the loop stands: the entry on its cell
  (the least coverage difference), and the first frame with its lowest body row on the loop's ground and
  across where it covers the loop best (the cells' mean coverage). The frames between are moved by a
  straight share of the difference per step of the stop, none at the entry and all of it on the first
  frame: the drift is taken out, and no frame is moved by its own pose. A standing pose is laid on a
  running one by its coverage, so a run that leans or reaches away from it moves the still a few pixels —
  a straight glide over the stop, the same with the drift or without it — and a drift that is not
  straight over the frames shown leaves a little of itself.
- **Speed and length**: `--speed 2` plays every other frame (the still kept last); `--max-frames N`
  takes N frames evenly spaced from the first to the still. Every frame is shown one clip tick.
- **Ghosts**: the frames shown pass the ghost screen every loop path delivers (`interpolation_quality`),
  read on them as filmed, as `video-loop` reads its cut; a ghost is named in the report and a warning, by
  its cell and clip frame, and kept as filmed.
- **Held cells**: the frames shown are read for a picture shown again (`held_fill.same_steps`, on them as filmed)
  and, by default (`--fill-held auto`), each held run's cells are made again between the run's first cell and the
  next pose, as `video-loop` makes a cut played once (docs/loop-repair.md section 7): its opening and closing pose
  (the still), a run longer than `held_fill.HOLD_MAX` and a clip drawn on twos or threes kept as filmed. A frame
  between that is refused, and every held cell where no RIFE is installed, is kept as filmed with a warning line;
  `--fill-held off` plays the frames as filmed.

The cells are the loop's cells' scale and placement: the loop's cell sits inside the stop's cell
(`loop_cell_origin`), centred on it with the same bottom unless a frame reaches lower, and the sidecar's
`anchor` is the loop's own pivot there, so a stop drawn at the loop's pivot stands where the loop stood.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.spec.runio import atomic_write_text, refuse_overlap
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.util.resample import resize_cell
from sprite_gen.video import evidence as evidence_mod
from sprite_gen.video import gait_fallback
from sprite_gen.video import held_fill as held_fill_mod
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import nonloop
from sprite_gen.video import rife as rife_mod
from sprite_gen.video.interpolation_quality import ghost_screen
from sprite_gen.video.repair import ALPHA_SOLID
from sprite_gen.util.console import utf8_streams

PROG = "video-stop"
KIND = "stop"
REPORT_KIND = "sprite-gen-video-stop-report"
# Two poses within a third of the loop's step are one pose: a frame of the rest, or one of the loop's frames.
SAME_POSE = 0.35
# A pose the body has left by a whole step (a frame of its own gait) and comes back to: a motion that turned back.
TURN_BACK_AWAY = 1.0
# A body sets off into its gait within about a cycle; past one and a half of the clip's own cycles the run-up
# plays strides unlike the loop's.
RUN_UP_MAX_CYCLES = 1.5
# A seam past this many of the clip's steps is a jump, and the stop is refused: the line a loop's step read by its
# size was judged a jump at, 2.44.0's `repair.JUMP_RATIO` (the jump repair now reads a loop's flow and keeps no line).
SEAM_MAX = 1.4
SPEEDS = (1, 2)
MARGIN = 8  # px of transparent room around the bodies, the strip cells' own side and top margin
REGISTER_SEARCH = 0.12  # of the cell height: how far the entry is searched for on its cell


class StopRefused(ValueError):
    """A stop the clip cannot give; `record` holds what was measured before the refusal."""

    def __init__(self, message: str, record: dict[str, Any]):
        super().__init__(message)
        self.record = record


def _strip_beside(meta_path: Path) -> Path:
    """The strip a loop's sidecar `<name>.strip.json` names: `<name>.strip.png` beside it."""
    return meta_path.with_name(meta_path.name[: -len(".json")] + ".png")


def _refuse_overlap(frames_dir: Path, files: list[Path], loop_meta: Path, out_dir: Path, name: str, target: Path,
                    report_given: bool) -> None:
    """A stop never writes over, moves or deletes what it reads: `--frames-dir` and every frame found in it, `--loop`
    and the strip beside it, each with every link or folder its path looks up, against the files it writes in
    `--out-dir` and its report (`runio.refuse_overlap`). Refused before anything is made."""
    meta = Path(loop_meta).expanduser()
    inputs = {"--frames-dir": Path(frames_dir), **{f"{f.name} in --frames-dir": f for f in files},
              "--loop": meta, f"{_strip_beside(meta).name} beside --loop": _strip_beside(meta)}
    outputs = {f"{n} in --out-dir": out_dir / n for n in (f"{name}.strip.png", f"{name}.strip.json", f"{name}.gif", f"{name}.webp")}
    outputs["--report" if report_given else f"{target.name} in --out-dir"] = target
    refuse_overlap(inputs, outputs, verb=PROG)


def read_loop(meta_path: Path) -> tuple[dict[str, Any], list[Image.Image], str]:
    """The loop's sidecar, its cells and its strip's sha256. A loop the stop cannot leave from is refused."""
    meta_path = Path(meta_path).expanduser()
    if not meta_path.name.endswith(".strip.json"):
        raise SystemExit(f"{PROG}: --loop {meta_path}: a loop strip's sidecar, <name>.strip.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    strip_path = _strip_beside(meta_path)
    data = strip_path.read_bytes()
    strip = Image.open(strip_path).convert("RGBA")
    n, w, h = int(meta["frames"]), int(meta["w"]), int(meta["h"])
    if strip.size != (n * w, h):
        raise SystemExit(f"{PROG}: {strip_path.name} is {strip.size[0]}x{strip.size[1]}, its sidecar says {n * w}x{h}")
    if meta.get("loop") is False:
        raise SystemExit(f"{PROG}: {meta_path.name} plays once (loop false); a stop leaves a loop")
    if "out_fps" in meta:
        raise SystemExit(f"{PROG}: {meta_path.name} was cut at --out-fps {meta['out_fps']:g}, its cells each a time of their own; "
                         "a stop is cut from a loop at its clip's rate (video-loop without --out-fps)")
    state = str(meta.get("state") or "")
    if not loop_mod.profile_for(state).gait:
        raise SystemExit(f"{PROG}: {meta_path.name} is a {state or 'state-less'} loop; a stop is cut from a walk or run loop")
    cut = meta.get("source_cut")
    if not isinstance(cut, dict) or not all(k in cut for k in ("source", "start", "length")):
        raise SystemExit(f"{PROG}: {meta_path.name} names no source cut (source_cut: source, start, length); "
                         "cut the loop again with video-loop")
    cells = [strip.crop((k * w, 0, (k + 1) * w, h)) for k in range(n)]
    return meta, cells, hashlib.sha256(data).hexdigest()


def _scaled(path: Path, scale: float) -> Image.Image:
    return _scale(Image.open(path).convert("RGBA"), scale)


def _scale(im: Image.Image, scale: float) -> Image.Image:
    return resize_cell(im, (max(1, round(im.width * scale)), max(1, round(im.height * scale))))


def _box(im: Image.Image) -> tuple[int, int, int, int] | None:
    return im.getchannel("A").point(lambda v: 255 if v >= ALPHA_SOLID else 0).getbbox()


class Space:
    """One canvas for the loop's cells and the clip's frames, in the loop's cell coordinates (the cell's top left
    at 0, 0): every body laid where it lies there, with room around them, so the cells and the frames are read in
    one place and a pose is measured the same on both (`gait_fallback.poses` cuts each body out about its box, on
    whole pixels)."""

    def __init__(self, laid: list[tuple[Image.Image, tuple[int, int]]], room: int):
        boxes = [(b[0] + x, b[1] + y, b[2] + x, b[3] + y) for im, (x, y) in laid if (b := _box(im)) is not None]
        self.origin = (min(b[0] for b in boxes) - room, min(b[1] for b in boxes) - room)
        self.size = (max(b[2] for b in boxes) + room - self.origin[0], max(b[3] for b in boxes) + room - self.origin[1])

    def lay(self, im: Image.Image, at: tuple[int, int]) -> Image.Image:
        canvas = Image.new("RGBA", self.size, (0, 0, 0, 0))
        canvas.paste(im, (at[0] - self.origin[0], at[1] - self.origin[1]))
        return canvas


def _alpha(im: Image.Image) -> np.ndarray:
    return np.asarray(im.getchannel("A"), np.float32) / 255


def _shifted(alpha: np.ndarray, by: tuple[int, int]) -> np.ndarray:
    """`alpha` moved by `by` (x, y) on its own canvas; what falls outside is cut, what comes in is empty."""
    h, w = alpha.shape
    out = np.zeros_like(alpha)
    dx, dy = by
    if abs(dx) >= w or abs(dy) >= h:
        return out
    out[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)] = alpha[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)]
    return out


def _quarter(a: np.ndarray) -> np.ndarray:
    a = np.pad(a, ((0, -a.shape[0] % 4), (0, -a.shape[1] % 4)))
    return a.reshape(a.shape[0] // 4, 4, a.shape[1] // 4, 4).mean(axis=(1, 3))


def register(cell: Image.Image, frame: Image.Image, reach: int) -> tuple[tuple[int, int], float]:
    """The move that lays `frame` on `cell` (both laid in one `Space`) with the least coverage difference, within
    `reach` px either way: searched on a quarter-size matte, then to the pixel. Returns the move and its difference
    (the mean over the canvas)."""
    target, alpha = _alpha(cell), _alpha(frame)
    qt, qa = _quarter(target), _quarter(alpha)
    q = reach // 4 + 1
    coarse = min(((dx, dy) for dy in range(-q, q + 1) for dx in range(-q, q + 1)),
                 key=lambda by: (float(np.abs(_shifted(qa, by) - qt).mean()), abs(by[0]) + abs(by[1])))

    def cost(by: tuple[int, int]) -> float:
        return float(np.abs(_shifted(alpha, by) - target).mean())

    best = min(((coarse[0] * 4 + dx, coarse[1] * 4 + dy) for dy in range(-4, 5) for dx in range(-4, 5)),
               key=lambda by: (cost(by), abs(by[0]) + abs(by[1])))
    return best, round(cost(best), 6)


def stand_on(cells: list[Image.Image], frame: Image.Image, ground: int, reach: int) -> tuple[tuple[int, int], float]:
    """The move that stands `frame` on `ground` (its lowest body row there) and across where it covers the loop best —
    the cells' mean coverage, where the loop's body is over a cycle — within `reach` px either way: searched on a
    quarter-size matte, then to the pixel. Returns the move and its coverage difference (the mean over the canvas)."""
    box = _box(frame)
    dy = ground - (box[3] - 1)
    target, alpha = np.mean([_alpha(c) for c in cells], axis=0), _shifted(_alpha(frame), (0, dy))
    qt, qa = _quarter(target), _quarter(alpha)
    q = reach // 4 + 1
    coarse = min(range(-q, q + 1), key=lambda v: (float(np.abs(_shifted(qa, (v, 0)) - qt).mean()), abs(v)))

    def cost(dx: int) -> float:
        return float(np.abs(_shifted(alpha, (dx, 0)) - target).mean())

    dx = min(range(coarse * 4 - 4, coarse * 4 + 5), key=lambda v: (cost(v), abs(v)))
    return (dx, dy), round(cost(dx), 6)


def turn_back(poses: np.ndarray, rest_end: int, entry: int, step: float) -> dict[str, Any] | None:
    """The nearest the run-up (frames rest_end + 1 to entry - 1) comes back to a pose it has left: over every run-up
    frame and every earlier frame of the clip that the body went TURN_BACK_AWAY steps or more from in between, the pair
    nearest in steps (the earliest such frame, then the latest pose it is back at), with the farthest frame between.
    None when the body leaves no pose by that much. Within SAME_POSE, the run-up turns back: another motion."""
    d = np.stack([np.nanmean(np.abs(poses[:entry] - poses[i]), axis=1) for i in range(entry)]) / step
    best = None
    for j in range(rest_end + 1, entry):
        for i in range(j - 2, -1, -1):
            between = d[i, i + 1:j]
            k = i + 1 + int(np.argmax(np.where(np.isnan(between), -np.inf, between)))
            if d[i, k] >= TURN_BACK_AWAY and np.isfinite(d[i, j]) and (best is None or d[i, j] < best[0]):
                best = (float(d[i, j]), j, i, k)
    if best is None:
        return None
    steps, j, i, k = best
    return {"frame": j, "back_to": i, "steps": round(steps, 4), "away_frame": k, "away_steps": round(float(d[i, k]), 4)}


def find_stop(cells: list[Image.Image], frames: list[Image.Image], *, start: int, length: int,
              seam_max: float = SEAM_MAX, run_up_max: float = RUN_UP_MAX_CYCLES) -> dict[str, Any]:
    """Rest, entry, the cell left from, the seam, the turn-back and the run-up of a clip whose frames (`frames`,
    from the first, at the loop's scale, through the frame after the window) reach the loop's `cells` in the window
    [start, start + length). Refused (`StopRefused`) where no frame reaches the loop, the seam is past `seam_max`
    steps, the run-up turns back (another motion), or the run-up is longer than `run_up_max` cycles."""
    n = len(cells)
    thumbs = gait_fallback.poses(cells + frames, gait_fallback.subject_boxes(cells + frames))
    pc, pf = thumbs[:n], thumbs[n:]

    def dist(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.nanmean(np.abs(a - b)))

    # The clip's own step, over the loop's window as filmed: the frames the stop plays are a step apart.
    step = float(np.median([dist(pf[j], pf[j + 1]) for j in range(start, min(start + length, len(pf) - 1))]))
    cell_step = float(np.median([dist(pc[k], pc[(k + 1) % n]) for k in range(n)]))
    if not step > 0:
        raise StopRefused(f"{PROG}: the clip does not move in the loop's window (median step 0); there is no gait to stop", {})
    to_rest = [dist(p, pf[0]) / step for p in pf]
    near = [min((dist(p, q) / step, k) for k, q in enumerate(pc)) for p in pf]
    rest_end = 0
    while rest_end + 1 < len(pf) and to_rest[rest_end + 1] <= SAME_POSE:
        rest_end += 1
    record: dict[str, Any] = {
        "step": round(step, 6), "loop_cell_step": round(cell_step / step, 4), "same_pose": SAME_POSE,
        "turn_back_away": TURN_BACK_AWAY, "rest": {"end": rest_end},
        "to_rest": [round(v, 4) for v in to_rest],
        "to_loop": [[round(v, 4), k] for v, k in near],
    }
    end = min(len(pf), start + length)
    entry = next((j for j in range(rest_end + 1, end) if near[j][0] <= SAME_POSE), None)
    if entry is None:
        raise StopRefused(f"{PROG}: no clip frame after the rest (frame {rest_end}) comes within {SAME_POSE} steps of a loop "
                          f"cell before the loop's window ends (frame {end - 1}); these frames are not the loop's clip", record)
    cell = near[entry][1]
    played = list(range(entry - 1, rest_end, -1)) + [0]
    seam = dist(pc[cell], pf[played[0]]) / step
    turn = turn_back(pf, rest_end, entry, step)
    run_up = entry - 1 - rest_end
    record.update(entry={"frame": entry, "match": round(near[entry][0], 4), "from_cell": cell},
                  seam={"steps": round(seam, 4), "from_cell": cell, "into_frame": played[0], "max": seam_max},
                  turn_back=turn,
                  run_up={"frames": run_up, "cycles": round(run_up / length, 3), "max_cycles": run_up_max},
                  played=played)
    refused = []
    if seam > seam_max:
        refused.append(f"the seam from loop cell {cell} into clip frame {played[0]} is {seam:.2f} steps, past the jump line "
                       f"{seam_max} (--seam-max): the clip reaches the loop (frame {entry}) by a jump")
    if turn is not None and turn["steps"] <= SAME_POSE:
        refused.append(f"clip frame {turn['frame']} is back at the pose of frame {turn['back_to']} ({turn['steps']:.2f} steps, "
                       f"within {SAME_POSE}) after the body went {turn['away_steps']:.2f} steps from it (frame "
                       f"{turn['away_frame']}): the run-up turns back — another motion, that the stop would play backwards")
    if run_up > run_up_max * length:
        refused.append(f"the run-up is {run_up} frames ({run_up / length:.2f} cycles) between the rest (frame {rest_end}) and "
                       f"the loop's frames (frame {entry}), longer than a setting-off ({run_up_max} cycles, --run-up-max): "
                       "strides unlike the loop's, that the stop would play backwards")
    if refused:
        raise StopRefused(f"{PROG}: " + "; ".join(refused), record)
    return record


def place(cells: list[Image.Image], frames: list[Image.Image], found: dict[str, Any], *, reach: int
          ) -> tuple[dict[int, tuple[int, int]], dict[str, Any]]:
    """How far each played frame is moved from where it is laid (cells and frames laid in one `Space`): the module's
    "Drift". Returns {frame: (dx, dy)} and the record, in the Space's pixels."""
    entry, cell = found["entry"]["frame"], found["entry"]["from_cell"]
    leave, mismatch = register(cells[cell], frames[entry], reach)
    boxes = [_box(c) for c in cells]
    if any(b is None for b in boxes) or _box(frames[0]) is None:
        raise StopRefused(f"{PROG}: a loop cell or the clip's first frame is empty", found)
    ground = max(b[3] - 1 for b in boxes)  # the loop's lowest body row: its feet on the ground
    # The clip may have carried its body far across its frame: the still is looked for over a cell's width.
    end, end_mismatch = stand_on(cells, frames[0], ground, cells[0].width)
    # A straight share per frame shown, the stop's own steps: the still's rest frames, never shown, take none.
    played = found["played"]
    moves = {j: tuple(int(round(a + (b - a) * (i + 1) / len(played))) for a, b in zip(leave, end)) for i, j in enumerate(played)}
    return moves, {"leave": {"by": list(leave), "cell": cell, "mismatch": mismatch},
                   "stand": {"by": list(end), "mismatch": end_mismatch},
                   "clip_px": [b - a for a, b in zip(leave, end)], "ground": ground}


def _cells(frames: list[Image.Image], moves: list[tuple[int, int]], loop_rect: tuple[int, int, int, int]
           ) -> tuple[list[Image.Image], tuple[int, int, int, int]]:
    """The stop's cells: every shown frame moved, in one cell that holds them all and the loop's own cell
    (`loop_rect`), centred on it across, with no room below the lowest body (as a strip's cells). Returns the cells
    and the stop cell's rectangle, both rectangles in the frames' canvas."""
    boxes = [(b[0] + x, b[1] + y, b[2] + x, b[3] + y) for im, (x, y) in zip(frames, moves) if (b := _box(im)) is not None]
    l0, t0, r0, b0 = loop_rect
    side = max(0, l0 - (min(b[0] for b in boxes) - MARGIN), max(b[2] for b in boxes) + MARGIN - r0)
    rect = (l0 - side, min(t0, min(b[1] for b in boxes) - MARGIN), r0 + side, max(b0, max(b[3] for b in boxes)))
    size = (rect[2] - rect[0], rect[3] - rect[1])
    out = []
    for im, (x, y) in zip(frames, moves):
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        canvas.paste(im, (x - rect[0], y - rect[1]))
        a = np.asarray(canvas).copy()
        a[a[..., 3] == 0] = 0
        out.append(Image.fromarray(a, "RGBA"))
    return out, rect


def pick(played: list[int], *, speed: int, max_frames: int | None) -> list[int]:
    """The frames shown: every `speed`-th from the first (the still kept last), then at most `max_frames` of
    them evenly spaced from the first to the still."""
    shown = played[::speed]
    if shown[-1] != played[-1]:
        shown.append(played[-1])
    if max_frames is not None and len(shown) > max_frames:
        shown = [shown[k] for k in nonloop.samples(len(shown), max_frames)]
    return shown


def make_stop(frames_dir: Path, loop_meta: Path, out_dir: Path, *, name: str = "stop", speed: int = 1,
              max_frames: int | None = None, fps: float | None = None, report_path: Path | None = None,
              seam_max: float = SEAM_MAX, run_up_max: float = RUN_UP_MAX_CYCLES, fill_held: str = "auto",
              interpolate: rife_mod.Interpolate | None = None) -> dict[str, Any]:
    """Cut the stop and write `<name>.strip.png`, `<name>.strip.json`, `<name>.gif`, `<name>.webp` and the report
    (`<name>.stop.report.json`, or `report_path`) into `out_dir`. A refusal writes the report and exits. `fill_held`
    auto makes the held cells again between the poses beside them (the module's "Held cells"), with `interpolate`, or
    RIFE located once a frame is to be made; off plays them as filmed."""
    if fill_held not in held_fill_mod.MODES:
        raise SystemExit(f"{PROG}: --fill-held {fill_held!r}: one of {', '.join(held_fill_mod.MODES)}")
    if speed not in SPEEDS:
        raise SystemExit(f"{PROG}: --speed {speed}: 1 or 2")
    if not (math.isfinite(seam_max) and seam_max > 0 and math.isfinite(run_up_max) and run_up_max >= 0):
        raise SystemExit(f"{PROG}: --seam-max is a positive number and --run-up-max a number of cycles, 0 or more")
    if max_frames is not None and max_frames < 2:
        raise SystemExit(f"{PROG}: --max-frames {max_frames}: at least 2 (a stop shows the frame it leaves on and the still)")
    out_dir = Path(out_dir).expanduser().resolve()
    target = Path(report_path or out_dir / f"{name}.stop.report.json").expanduser().resolve()
    meta, cells, strip_sha = read_loop(loop_meta)
    cut = meta["source_cut"]
    fps = float(fps or cut["source"].get("fps") or 24.0)
    files = sorted(Path(frames_dir).expanduser().resolve().glob("*.png"))
    start, length = int(cut["start"]), int(cut["length"])
    if len(files) < start + length:
        raise SystemExit(f"{PROG}: {frames_dir} holds {len(files)} frames; the loop was cut from frames {start} to "
                         f"{start + length - 1} of its clip — give the clip's keyed frames (video-frames …/keyed)")
    _refuse_overlap(frames_dir, files, loop_meta, out_dir, name, target, report_path is not None)
    identity = evidence_mod.source_identity(files, fps)
    same_source = identity["sha256"] == cut["source"].get("sha256")
    base: dict[str, Any] = {
        "kind": REPORT_KIND, "frames_dir": str(Path(frames_dir).expanduser().resolve()), "loop": str(Path(loop_meta).expanduser().resolve()),
        "loop_strip_sha256": strip_sha, "producer": evidence_mod.engine_identity(), "fps": fps,
        "source": {**identity, "loop_source_sha256": cut["source"].get("sha256"), "same_as_loop": same_source},
        "loop_cut": {"start": start, "length": length, "cells": len(cells), "state": meta.get("state")},
        "speed": speed, "max_frames": max_frames, "same_pose": SAME_POSE, "seam_max": seam_max, "run_up_max_cycles": run_up_max,
    }
    scale = float(meta["scale"])
    w, h = int(meta["w"]), int(meta["h"])
    scaled = [_scaled(f, scale) for f in files[: min(len(files), start + length + length // 2 + 1)]]
    # Where each frame lies on the loop's cells before it is registered: the strip's own crop and scale, or, for a
    # strip that records no crop (cut about its feet), its body's bottom centre on the cells'.
    rect = meta.get("source_rect")
    if rect:
        guess = (round(-rect[0] * scale), round(-rect[1] * scale))
    else:
        b = _box(scaled[0]) or (0, 0, 0, 0)
        guess = (round(w / 2 - (b[0] + b[2]) / 2), h - b[3])
    reach = max(8, round(REGISTER_SEARCH * h))
    space = Space([(c, (0, 0)) for c in cells] + [(f, guess) for f in scaled], reach + MARGIN)
    laid_cells = [space.lay(c, (0, 0)) for c in cells]
    laid = [space.lay(f, guess) for f in scaled]
    try:
        found = find_stop(laid_cells, laid, start=start, length=length, seam_max=seam_max, run_up_max=run_up_max)
        moves, drift = place(laid_cells, laid, found, reach=reach)
    except StopRefused as exc:
        loop_mod.write_loop_report(target, {**base, **exc.record, "status": "failed", "error": str(exc)})
        raise SystemExit(str(exc)) from exc
    shown = pick(found["played"], speed=speed, max_frames=max_frames)
    # Read as filmed, at the keyed frames' own size, as video-loop reads its cut: a part-covered band is a
    # number of pixels thick, and scaled down a ghost's band falls under it.
    filmed = [Image.open(files[j]).convert("RGBA") for j in shown]
    screen = ghost_screen(filmed)
    # The stop's frames shown again (docs/loop-repair.md section 7), made again between the poses beside them as a cut
    # played once is, on the frames as filmed at their own size; a cell made stands where the held cell stood (its
    # frame's move), scaled and laid as the clip's frames are.
    try:
        filled, held = loop_mod.fill_cut(filmed, interpolate, ring=False, same=held_fill_mod.same_steps(filmed),
                                           mode=fill_held, hold=(meta.get("drawings") or {}).get("hold"))
    except (ValueError, rife_mod.RifeUnavailable) as exc:
        error = (f"{PROG}: {exc}; the stop shows a picture again and the frames between are to be made — fix RIFE, or "
                 "pass --fill-held off to cut it as filmed (docs/loop-repair.md section 7)")
        loop_mod.write_loop_report(target, {**base, **found, "drift": drift, "status": "failed", "error": error})
        raise SystemExit(error) from exc
    shown_frames = [laid[j] for j in shown]
    for k in held["made"]:
        shown_frames[k] = space.lay(_scale(filled[k], scale), guess)
    loop_rect = (-space.origin[0], -space.origin[1], w - space.origin[0], h - space.origin[1])
    stop_cells, rect = _cells(shown_frames, [moves[j] for j in shown], loop_rect)
    cw, ch = stop_cells[0].size
    if cw * len(stop_cells) > loop_mod.STRIP_MAX_WIDTH:
        error = (f"{PROG}: {len(stop_cells)} cells of {cw} px make a strip {cw * len(stop_cells)} px wide, over "
                 f"{loop_mod.STRIP_MAX_WIDTH}; pass --max-frames or --speed 2")
        loop_mod.write_loop_report(target, {**base, **found, "drift": drift, "status": "failed", "error": error})
        raise SystemExit(error)
    origin = [loop_rect[0] - rect[0], loop_rect[1] - rect[1]]
    pivot = meta.get("anchor") or [w / 2, h]
    last = _box(stop_cells[-1])
    for g in screen.get("ghosts", []):
        g["clip_frame"] = shown[g["frame"]]
    delay = round(1000 / fps, 2)
    strip_meta = {
        "frames": len(stop_cells), "w": cw, "h": ch, "delay_ms": delay, "kind": KIND, "state": KIND,
        "loop": False, "hold_last": True, "end_frame": len(stop_cells) - 1, "events": [],
        "anchor": [round(pivot[0] + origin[0], 2), round(pivot[1] + origin[1], 2)], "loop_cell_origin": origin,
        "body_h": meta.get("body_h"), "body_src_h": meta.get("body_src_h"), "scale": meta.get("scale"),
        "cycle_frames": len(stop_cells), "cycle_seconds": round(len(stop_cells) / fps, 4),
        "from_loop": {"strip_sha256": strip_sha, "state": meta.get("state"), "frames": len(cells), "w": w, "h": h,
                      "from_cell": found["entry"]["from_cell"]},
        "source_cut": {"source": identity, "frames": shown, "entry": found["entry"]["frame"], "rest_end": found["rest"]["end"]},
        "speed": speed, "seam_steps": found["seam"]["steps"], "held_fill": held_fill_mod.public(held),
        # The holds the motion means, as cells of the strip (one cell a frame shown): what a reader of it —
        # `inspect-holds`, a scene — does not take for a picture shown again by mistake.
        "holds": held_fill_mod.intended(held),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    strip = Image.new("RGBA", (cw * len(stop_cells), ch), (0, 0, 0, 0))
    for k, im in enumerate(stop_cells):
        strip.paste(im, (k * cw, 0))
    strip_path = out_dir / f"{name}.strip.png"
    strip.save(strip_path)
    atomic_write_text(out_dir / f"{name}.strip.json", json.dumps(strip_meta, indent=2) + "\n")
    gif_path, webp_path = out_dir / f"{name}.gif", out_dir / f"{name}.webp"
    ms = max(20, round(1000 / fps))
    save_clean_gif(stop_cells, gif_path, duration_ms=ms, loop=None, alpha_threshold=128)
    loop_mod.write_webp(stop_cells, webp_path, delay_ms=ms, loop=1)
    shown_runs = loop_mod.shown_runs(stop_cells)
    gif = loop_mod.verify_animation(gif_path, expect_frames=shown_runs, check_stale=False, expected_loop=None)
    webp = loop_mod.verify_animation(webp_path, expect_frames=shown_runs, check_stale=True, expected_loop=1)
    payload = {
        **base, **found, "status": "passed",
        "drift": {**drift, "ground_in_cell": drift["ground"] - rect[1],
                  "ends_on_ground": last is not None and last[3] - 1 == drift["ground"] - rect[1]},
        "shown": shown, "frames_shown": len(shown), "seconds": round(len(shown) / fps, 4),
        "ghost_screen": screen, "held_fill": held,
        "strip": {"path": str(strip_path), **strip_meta}, "gif": gif, "webp": webp,
    }
    loop_mod.write_loop_report(target, payload)
    if not same_source:
        print(f"{PROG}: warning: the frames in {frames_dir} are not byte for byte the keyed frames the loop was cut from "
              f"(source {identity['sha256'][:12]}, the loop's {str(cut['source'].get('sha256'))[:12]}): keyed again; the entry "
              "is found by pose, so look at the stop", file=sys.stderr)
    for g in screen.get("ghosts", []):
        print(f"{PROG}: warning: cell {g['frame']} (clip frame {g['clip_frame']}) carries a part-covered band over "
              f"{100 * g['ghost']:.2f} % of its body — a filmed ghost, kept as filmed", file=sys.stderr)
    if not screen["reads"]:
        print(f"{PROG}: warning: the ghost screen does not read this stop — {screen['why']}", file=sys.stderr)
    # What became of the held cells: those left for want of RIFE, the runs a frame between was refused for, and, with
    # --fill-held off, every run (each played as filmed); a run kept as the motion's own says nothing.
    for line in loop_mod.fill_lines(held):
        print(f"{PROG}: warning: {line}", file=sys.stderr)
    for run in held["before"]["held_runs"]:
        if run.get("why") == "--fill-held off":
            print(f"{PROG}: warning: cells {run['start']}-{run['start'] + run['length'] - 1} show one picture "
                  f"({held_fill_mod.said(held)} poses/cells): the clip stalls there, played as filmed (--fill-held off)",
                  file=sys.stderr)
    payload["report"] = str(target)
    return payload


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--frames-dir", type=Path, required=True,
                        help="the clip's keyed frames, every one from the first (video-frames …/keyed): the clip the loop was cut from")
    parser.add_argument("--loop", type=Path, required=True,
                        help="the walk or run loop the stop leaves, its <name>.strip.json (as cut or aligned; the strip PNG beside it)")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--name", default="stop", help="basename of the strip, sidecar, GIF, WebP and report (default stop)")
    parser.add_argument("--speed", type=int, choices=SPEEDS, default=1, help="1: every frame; 2: every other frame, the still kept last")
    parser.add_argument("--max-frames", type=int, help="at most this many frames, evenly spaced from the first to the still")
    parser.add_argument("--seam-max", type=float, default=SEAM_MAX,
                        help=f"refuse a seam (the last loop cell into the stop's first frame) over this many of the clip's steps (default {SEAM_MAX}, the jump line)")
    parser.add_argument("--run-up-max", type=float, default=RUN_UP_MAX_CYCLES,
                        help=f"refuse a stop whose frames between the rest and the loop's frames are longer than this many of the clip's cycles (default {RUN_UP_MAX_CYCLES})")
    parser.add_argument("--fill-held", choices=held_fill_mod.MODES, default="auto",
                        help="auto (default): cells of the stop that show the picture before them again (the clip stalls) are "
                             "made again with RIFE between the poses beside them, as video-loop makes a cut played once; the "
                             f"opening pose, the closing still, a run longer than {held_fill_mod.HOLD_MAX} cells and a clip drawn on "
                             "twos or threes are kept, and a frame between that is refused, or no RIFE installed, keeps the cells "
                             "as filmed with a warning line (`held_fill`, docs/loop-repair.md section 7); off: as filmed")
    parser.add_argument("--fps", type=float, help="the frames' rate (default: the loop's source_cut, else 24)")
    parser.add_argument("--report", type=Path, help="report path (default <out-dir>/<name>.stop.report.json)")


def run(**kwargs: object) -> int:
    payload = make_stop(Path(str(kwargs["frames_dir"])), Path(str(kwargs["loop"])), Path(str(kwargs["out_dir"])),
                        name=str(kwargs.get("name") or "stop"), speed=int(kwargs.get("speed") or 1),  # type: ignore[arg-type]
                        max_frames=kwargs.get("max_frames"), fps=kwargs.get("fps"), report_path=kwargs.get("report"),  # type: ignore[arg-type]
                        seam_max=float(SEAM_MAX if kwargs.get("seam_max") is None else kwargs["seam_max"]),  # type: ignore[arg-type]
                        run_up_max=float(RUN_UP_MAX_CYCLES if kwargs.get("run_up_max") is None else kwargs["run_up_max"]),  # type: ignore[arg-type]
                        fill_held=str(kwargs.get("fill_held") or "auto"))
    print(json.dumps({"strip": payload["strip"]["path"], "frames": payload["strip"]["frames"], "seconds": payload["seconds"],
                      "from_cell": payload["entry"]["from_cell"], "entry": payload["entry"]["frame"], "rest_end": payload["rest"]["end"],
                      "seam_steps": payload["seam"]["steps"], "run_up": payload["run_up"], "drift": payload["drift"],
                      "ghosts": [g["clip_frame"] for g in payload["ghost_screen"].get("ghosts", [])],
                      "held_fill": {"poses_cells": held_fill_mod.said(payload["held_fill"]),
                                    "before": held_fill_mod.said(payload["held_fill"]["before"]),
                                    "made": payload["held_fill"]["made"]},
                      "report": payload["report"]},
                     ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog=f"sprite-gen {PROG}", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
