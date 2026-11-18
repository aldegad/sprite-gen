# SPDX-License-Identifier: Apache-2.0
"""The beat an aligned walk keeps, and the feet it steps with, read off a loop's own frames (docs/loop-repair.md
section 4).

- The leg path (`leg_path`): how far the legs move from each frame of a cycle to the next, read where the feet are, the
  lowest LEG_PATH_BAND of the box every frame's figure fits in. Each BLOCK x BLOCK block holding a part of the figure
  there is looked for in the next frame within SEARCH px; the step is the blocks' mean shift, in % of the box's height.
  A cape or a tail held still over the band adds no shift, where a brightness difference would add its whole change; a
  block that matches nowhere changed shape (a sole turning) and counts its residual instead. Read at READ_HEIGHT: a cut's
  frames are drawn about twice a strip's size, and a block and the search reach the same part of the figure at the
  strip's. Summed from the first frame, the steps give each frame of the cycle a place on the path, M(i), and the cycle
  its length P = M(n); a frame made at fraction t between frames i and i + 1 stands at M(i) + t (M(i + 1) - M(i)).
- The beat jolt (`Beat`): a cycle of n frames resampled to L asks frame k for the time k n / L, and standing there it is
  at e_k = M(k n / L). A frame standing elsewhere — the nearer source frame taken for one that could not be made, a
  filmed ghost given way to the frame beside it — moves the legs more into it and less out of it, or the other way round:
  jolt_k = ((p_(k+1) - p_k) mod P - (e_(k+1) - e_k) mod P) / (P / L), in beats, p_k where frame k stands. Every frame at
  its own time leaves every jolt 0, whatever the cut's own rhythm (drawings held for two or three frames, one step
  quicker than the next). A jolt of BEAT_JOLT or more is a broken beat: a skip into a frame and a halt out of it.
- The screen step (`screen_steps`, `screen_record`): how much of the picture changes from each frame of the cut to the
  next, the mean |luminance difference| over white of the box (0..255) and of its lowest SCREEN_ROWS, where the leg
  path reads only where the feet go. Two drawings filmed a pose apart (the legs wide in one, crossed in the next) move
  the feet little and change the picture much; the beat cannot see it, the screen can. A step SCREEN_JUMP times the
  cycle's typical step (its upper quartile, a drawing change on ones, twos or threes) and SCREEN_ALONE times every
  other step is a jump: between two frames inside the cut, filmed so; the last step, the cut's seam.
- Same foot twice (`same_foot`, over a loop; `clip_feet`, over every cut of a clip): read where the feet stand side by
  side and the stepping foot is drawn higher (STEP_VIEWS). The feet split at the middle of the solid columns of the
  box's lowest FOOT_ROWS over the loop; for each frame and half, its lift is how far the half's lowest solid row ends
  above the frame's own lowest row, of the box's height. A step is a run of LIFT_CELLS or more frames (round the ring)
  lifted LIFT_MIN or more; two runs of one half are two steps only where its lift comes down to DOWN_MAX or under between
  them. Read only where both halves step: the steps in ring order by their highest frame, the same foot twice where two
  neighbours on the ring (the last and the first among them) are one half.
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.gen.handedness import VIEW_BACK, VIEW_BACK_DIAGONAL

LEG_PATH_BAND = 0.10  # the leg path is read on the lowest tenth of the loop's box: the feet and the shins
READ_HEIGHT = 410  # px the first frame's figure ends at, at most, where the path is read
BLOCK, SEARCH = 8, 12  # px: the blocks matched, and how far each is looked for in the next frame
MATCH_BAD = 40.0  # mean |RGBA| difference (0..255) over which a block matched nowhere: its residual is its motion
# A broken beat: a jolt of a quarter of a beat or more. An aligned frame may move the legs into it or out of it by up to a
# quarter of a beat more than its own time would, so two even gaps beside it stay within 5 to 3 of each other.
BEAT_JOLT = 0.25
SCREEN_ROWS = 0.30  # the screen's leg rows: the lowest 30 % of the box (a strip cell's lowest 125 of its 406 px)
# A jump: a step of the cut's frames that, over the box or on its leg rows, changes SCREEN_JUMP times as much of the
# picture as the cycle's typical step or more (its upper quartile: a drawing change, whether the cut is drawn on ones,
# twos or threes) and SCREEN_ALONE times as much as any other step of the cycle or more (a cycle whose drawing changes
# are all large has no one step that jumps).
SCREEN_JUMP = 2.0
SCREEN_ALONE = 1.3
STEP_VIEWS = (VIEW_BACK, VIEW_BACK_DIAGONAL)  # the views whose feet stand side by side, the stepping foot drawn higher
FOOT_ROWS = 0.03  # the feet split at the middle of the solid columns of the box's lowest 3 % over the loop
LIFT_MIN = 0.010  # a half lifted by 1 % of the box's height or more is in a step ...
LIFT_CELLS = 2  # ... held for two frames or more
DOWN_MAX = 0.006  # two lifts of one half are two steps where it comes down to 0.6 % or under between them


def _premultiplied(frame: Image.Image) -> np.ndarray:
    a = np.asarray(frame.convert("RGBA"), dtype=np.float32)
    out = a.copy()
    out[..., :3] *= a[..., 3:4] / 255.0
    return out


def _box(solid: list[np.ndarray]) -> tuple[int, int, int, int]:
    """(top, bottom, left, right) of the union of the frames' solid pixels, the bottom and right exclusive."""
    union = np.any(np.stack(solid), axis=0)
    rows, cols = np.nonzero(union.any(axis=1))[0], np.nonzero(union.any(axis=0))[0]
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def _block_flow(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per block of `a` (premultiplied RGBA), the shift to its best match in `b` within SEARCH px, that match's mean
    |difference| and whether the block holds a part of the figure (a quarter of its pixels solid, in `a` or `b`)."""
    H, W = a.shape[:2]
    pad = np.pad(b, ((SEARCH, SEARCH), (SEARCH, SEARCH), (0, 0)))
    nby, nbx = H // BLOCK, W // BLOCK
    A = a[: nby * BLOCK, : nbx * BLOCK]
    best = np.full((nby, nbx), np.inf, dtype=np.float32)
    shift = np.zeros((nby, nbx, 2), dtype=np.float32)
    for dy in range(-SEARCH, SEARCH + 1):
        for dx in range(-SEARCH, SEARCH + 1):
            B = pad[SEARCH + dy : SEARCH + dy + nby * BLOCK, SEARCH + dx : SEARCH + dx + nbx * BLOCK]
            diff = np.abs(A - B).mean(axis=2).reshape(nby, BLOCK, nbx, BLOCK).mean(axis=(1, 3))
            diff = diff + 0.02 * (abs(dy) + abs(dx))  # a flat block does not wander
            better = diff < best
            best = np.where(better, diff, best)
            shift[better] = (dy, dx)
    solid_a = (a[: nby * BLOCK, : nbx * BLOCK, 3] >= 128).reshape(nby, BLOCK, nbx, BLOCK).mean(axis=(1, 3))
    solid_b = (b[: nby * BLOCK, : nbx * BLOCK, 3] >= 128).reshape(nby, BLOCK, nbx, BLOCK).mean(axis=(1, 3))
    return shift, best, (solid_a >= 0.25) | (solid_b >= 0.25)


def _step(a: np.ndarray, b: np.ndarray, height: int) -> float:
    shift, best, keep = _block_flow(a, b)
    if not keep.any():
        return 0.0
    length = np.hypot(shift[..., 0], shift[..., 1])
    length = np.where(best > MATCH_BAD, np.maximum(length, best / MATCH_BAD * SEARCH), length)
    return float(length[keep].mean() / height * 100.0)


def leg_path(frames: list[Image.Image]) -> list[float]:
    """How far the legs move from each frame of a cycle to the next, the last to the first: the steps of the leg path,
    in % of the box's height (the module's docstring)."""
    bottom = (frames[0].getchannel("A").point(lambda v: 255 if v >= 128 else 0).getbbox() or (0, 0, 0, 0))[3]
    scale = min(1.0, READ_HEIGHT / max(1, bottom))
    if scale < 1.0:
        size = (round(frames[0].width * scale), round(frames[0].height * scale))
        frames = [f.convert("RGBA").resize(size, Image.LANCZOS) for f in frames]
    pm = [_premultiplied(f) for f in frames]
    y0, y1, x0, x1 = _box([p[..., 3] >= 128 for p in pm])
    height = y1 - y0
    top = y1 - round(height * LEG_PATH_BAND)
    legs = [p[top:y1, x0:x1] for p in pm]
    n = len(legs)
    return [round(_step(legs[k], legs[(k + 1) % n], height), 4) for k in range(n)]


def screen_steps(frames: list[Image.Image]) -> dict[str, list[float]]:
    """How much of the picture changes from each frame of a cycle to the next, the last to the first: the mean
    |luminance difference| over white (0..255) of the box every frame's figure fits in (`whole`) and of its lowest
    SCREEN_ROWS (`legs`), read at READ_HEIGHT (the module's docstring)."""
    bottom = (frames[0].getchannel("A").point(lambda v: 255 if v >= 128 else 0).getbbox() or (0, 0, 0, 0))[3]
    scale = min(1.0, READ_HEIGHT / max(1, bottom))
    if scale < 1.0:
        size = (round(frames[0].width * scale), round(frames[0].height * scale))
        frames = [f.convert("RGBA").resize(size, Image.LANCZOS) for f in frames]
    rgba = [np.asarray(f.convert("RGBA"), dtype=np.float32) for f in frames]
    y0, y1, x0, x1 = _box([a[..., 3] >= 128 for a in rgba])
    top = y1 - round((y1 - y0) * SCREEN_ROWS)
    luma = np.array([0.299, 0.587, 0.114], dtype=np.float32)
    lum = [(a[y0:y1, x0:x1, :3] * (a[y0:y1, x0:x1, 3:4] / 255.0) + 255.0 * (1 - a[y0:y1, x0:x1, 3:4] / 255.0)) @ luma for a in rgba]
    n = len(lum)
    diffs = [np.abs(lum[(k + 1) % n] - lum[k]) for k in range(n)]
    return {"whole": [round(float(d.mean()), 3) for d in diffs], "legs": [round(float(d[top - y0 :].mean()), 3) for d in diffs]}


def screen_record(steps: dict[str, list[float]]) -> dict[str, Any]:
    """The jumps among a cycle's screen steps (`screen_steps` of the frames as cut; step i from frame i to i + 1, the last
    step the cut's seam): each step that, over the box or on its leg rows, is SCREEN_JUMP times the cycle's typical step
    (`typical`, the upper quartile) or more and SCREEN_ALONE times every other step or more. A jump names its step,
    whether it is the seam, and per region its change and the two ratios ([change, of typical, of the next largest])."""
    n = len(steps["whole"])
    typical = {r: float(np.percentile(steps[r], 75)) for r in ("whole", "legs")}
    jumps = []
    for i in range(n):
        ratios = {}
        for r in ("whole", "legs"):
            g = steps[r]
            rest = max(g[:i] + g[i + 1 :], default=0.0)
            ratios[r] = [round(g[i], 2), round(g[i] / typical[r], 3) if typical[r] > 0 else 0.0, round(g[i] / rest, 3) if rest > 0 else 0.0]
        if any(of >= SCREEN_JUMP and alone >= SCREEN_ALONE for _, of, alone in ratios.values()):
            jumps.append({"step": [i, (i + 1) % n], "seam": i == n - 1, **ratios})
    return {**steps, "typical": {r: round(v, 3) for r, v in typical.items()}, "jumps": jumps,
            "limits": {"jump": SCREEN_JUMP, "alone": SCREEN_ALONE, "rows": SCREEN_ROWS}}


class Beat:
    """A cycle's leg path (`leg_path` steps) and the beat of a loop of `length` frames resampled from it."""

    def __init__(self, steps: list[float], length: int):
        self.n, self.length = len(steps), length
        self.M = np.concatenate([[0.0], np.cumsum(steps)])
        self.P = float(self.M[-1])
        self.beat = self.P / length

    @property
    def reads(self) -> bool:
        """Whether the legs move at all on the band: a path of no length has no beat."""
        return self.P > 0

    def at(self, tau: float) -> float:
        """The place on the path of the source's time `tau` (cyclic)."""
        tau %= self.n
        i = int(np.floor(tau))
        return float(self.M[i] + (tau - i) * (self.M[i + 1] - self.M[i]))

    def place(self, cell: dict[str, Any]) -> float:
        """Where a frame of the loop stands, as `cells_from` records it: a source frame, or one made between two."""
        return self.at(cell["source"]) if "source" in cell else self.at(cell["between"][0] + cell["t"])

    def own(self, k: int) -> float:
        """Where frame k of the loop would stand at its own time, k n / L."""
        return self.at(k * self.n / self.length)

    def off(self, k: int, place: float) -> float:
        """How far `place` is from frame k's own, in beats, the shorter way round the path."""
        return (((place - self.own(k)) + self.P / 2) % self.P - self.P / 2) / self.beat

    def jolt(self, k: int, p: float, q: float) -> float:
        """The jolt from frame k at place p to frame k + 1 at place q, in beats."""
        L = self.length
        return (((q - p) % self.P) - ((self.own((k + 1) % L) - self.own(k)) % self.P)) / self.beat

    def jolts(self, places: list[float]) -> list[float]:
        L = len(places)
        return [self.jolt(k, places[k], places[(k + 1) % L]) for k in range(L)]


def beat_record(beat: Beat | None, places: list[float] | None, *, why: str | None = None) -> dict[str, Any]:
    """The beat of a loop whose frames stand at `places` (frames of the cycle before it is turned): its largest jolt and
    the gap it is in (`gap`: frames k and k + 1), every gap at BEAT_JOLT or more (`over`), the limit and the band.
    `places` None: every frame at its own time, every jolt 0 (`path` on-time, nothing read). `beat` None or a path of
    no length: not read (`path` unread, `why`)."""
    record: dict[str, Any] = {"limit": BEAT_JOLT, "band": LEG_PATH_BAND}
    if places is None:
        return {"jolt": 0.0, "gap": None, "over": [], **record, "path": "on-time"}
    if beat is None or not beat.reads:
        return {"jolt": None, "gap": None, "over": [], **record, "path": "unread",
                "why": why or "the legs do not move on the band: the leg path has no length"}
    jolts = beat.jolts(places)
    L = len(jolts)
    worst = max(range(L), key=lambda k: abs(jolts[k]))
    return {"jolt": round(abs(jolts[worst]), 3), "gap": [worst, (worst + 1) % L],
            "over": [[k, (k + 1) % L] for k in range(L) if abs(jolts[k]) >= BEAT_JOLT], **record, "path": "read",
            "jolts": [round(j, 3) for j in jolts]}


def turned(record: dict[str, Any], start: int) -> dict[str, Any]:
    """A `beat_record` read on the cycle before it was turned to start at frame `start`, as frames of the turned loop."""
    L = len(record["jolts"]) if record.get("jolts") else None
    if L is None:
        return record
    at = lambda k: (k - start) % L  # noqa: E731
    return {**record, "gap": [at(k) for k in record["gap"]], "over": sorted([at(a), at(b)] for a, b in record["over"]),
            "jolts": [record["jolts"][(k + start) % L] for k in range(L)]}


def _profiles(frames: list[Image.Image]) -> list[dict[str, Any]]:
    """Per frame, its solid pixels' extent and, per column, the lowest solid row in it (-1 where none)."""
    out = []
    for f in frames:
        solid = np.asarray(f.convert("RGBA").getchannel("A")) >= 128
        rows = np.nonzero(solid.any(axis=1))[0]
        cols = solid.any(axis=0)
        low = np.where(cols, solid.shape[0] - 1 - np.argmax(solid[::-1], axis=0), -1)
        out.append({"top": int(rows[0]) if rows.size else None, "bottom": int(rows[-1]) if rows.size else None, "low": low})
    return out


def _read_feet(profiles: list[dict[str, Any]], *, seam: bool = False) -> dict[str, Any]:
    """Same foot twice over a loop of these frames (`_profiles`), read as a ring (the module's docstring); with `seam`,
    also the halves lifted LIFT_MIN or more at both the loop's last frame and its first (`seam_in_step`)."""
    drawn = [p for p in profiles if p["top"] is not None]
    if len(drawn) < len(profiles) or not drawn:
        return {"read": False, "why": "a frame has no solid pixel"}
    y0, y1 = min(p["top"] for p in drawn), max(p["bottom"] for p in drawn) + 1
    h = y1 - y0
    rows = max(1, round(h * FOOT_ROWS))
    low = np.any(np.stack([p["low"] >= y1 - rows for p in profiles]), axis=0)
    xs = np.nonzero(low)[0]
    split = (xs[0] + xs[-1]) / 2
    cols = np.arange(low.size)
    lifts = []
    for p in profiles:
        row = []
        for half in (cols < split, cols >= split):
            lowest = p["low"][half]
            lowest = lowest[lowest >= 0]
            row.append((p["bottom"] - int(lowest.max())) / h if lowest.size else 0.0)
        lifts.append(row)
    n = len(profiles)
    joined = {"seam_in_step": [half for hi, half in enumerate("LR") if min(lifts[0][hi], lifts[-1][hi]) >= LIFT_MIN]} if seam else {}
    steps: list[dict[str, Any]] = []
    for hi, half in enumerate("LR"):
        v = [lifts[k][hi] for k in range(n)]
        up = [x >= LIFT_MIN for x in v]
        if not any(up) or all(up):
            continue
        first = next(k for k in range(n) if not up[k])
        runs: list[list[int]] = []
        cur: list[int] = []
        for j in range(1, n + 1):
            k = (first + j) % n
            if up[k]:
                cur.append(k)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)

        def down_between(a: list[int], b: list[int]) -> bool:
            k = (a[-1] + 1) % n
            while k != b[0]:
                if v[k] <= DOWN_MAX:
                    return True
                k = (k + 1) % n
            return False

        merged: list[list[int]] = []
        for r in runs:
            if merged and not down_between(merged[-1], r):
                merged[-1] = merged[-1] + r
            else:
                merged.append(r)
        if len(merged) > 1 and not down_between(merged[-1], merged[0]):
            merged[0] = merged[-1] + merged[0]
            merged.pop()
        steps += [{"half": half, "frames": r, "peak": max(r, key=lambda k: v[k]), "lift": round(max(v[k] for k in r), 4)}
                  for r in merged if len(r) >= LIFT_CELLS]
    halves = {s["half"] for s in steps}
    if halves != {"L", "R"}:
        return {"read": False, "why": (f"only the picture's {'left' if halves == {'L'} else 'right'} half steps" if halves
                                       else "neither half steps") + f" (a run of {LIFT_CELLS} or more frames lifted "
                                       f"{LIFT_MIN:g} of the height or more)", "steps": steps, **joined}
    steps.sort(key=lambda s: s["peak"])
    twice = [[steps[i]["peak"], steps[(i + 1) % len(steps)]["peak"]] for i in range(len(steps))
             if steps[i]["half"] == steps[(i + 1) % len(steps)]["half"]]
    return {"read": True, "order": "".join(s["half"] for s in steps), "steps": steps, "twice": twice, **joined}


def view_reads_feet(view: str | None) -> bool:
    return bool(view) and view.partition("@")[0] in STEP_VIEWS


def same_foot(frames: list[Image.Image], view: str | None) -> dict[str, Any]:
    """Whether a loop steps the same foot twice in a row (the module's docstring): `read`, the steps in ring order as
    the picture's halves (`order`, L and R; seen from the back or a back diagonal the picture's left foot is the
    character's own left), each step's frames, highest frame and lift, and the pairs of neighbouring steps of one foot
    (`twice`, by their highest frames). Not read in a view whose feet are not side by side (`why`)."""
    if not view_reads_feet(view):
        return {"read": False, "why": f"the {(view or 'unnamed').partition('@')[0]} view: its feet are not side by side with "
                                      f"the stepping one drawn higher (read in {', '.join(STEP_VIEWS)})"}
    return _read_feet(_profiles(frames))


def clip_feet(frames: list[Image.Image], length: int, *, first: int = 0) -> dict[str, Any]:
    """Same foot twice over every cut of `length` frames a clip holds, each read as the loop it would make (the
    module's docstring, whatever the view: the alignment reads it where the view does): how many cuts there are
    (`cuts`), how many step the same foot twice, alternate, or do not read, and the first frame of each that steps the
    same foot twice (`same_foot_at`, a clip frame where `frames` start at its frame `first`). A cut whose seam falls
    inside a step — a half lifted at both its last frame and its first — is counted apart (`seam_in_step`), neither
    alternating nor the same foot twice: looped, it joins the end of one lift to the start of another across the seam,
    so where the take lifts one foot twice the loop hides the second lift in its seam and reads as alternating. Every
    other cut that reads stepping the same foot twice is a take that steps so every cycle: no cut of it alternates."""
    n = len(frames)
    if length < 2 or n < length:
        return {"length": length, "cuts": 0, "why": f"the clip holds {n} frames, no cut of {length}"}
    profiles = _profiles(frames)
    readings = [_read_feet(profiles[s : s + length], seam=True) for s in range(n - length + 1)]
    joined = [s for s, r in enumerate(readings) if r.get("seam_in_step")]
    kept = [(s, r) for s, r in enumerate(readings) if not r.get("seam_in_step")]
    same = [first + s for s, r in kept if r["read"] and r["twice"]]
    return {"length": length, "cuts": len(readings), "same_foot": len(same), "alternate": sum(1 for _, r in kept if r["read"] and not r["twice"]),
            "unread": sum(1 for _, r in kept if not r["read"]), "seam_in_step": len(joined), "same_foot_at": same}
