# SPDX-License-Identifier: Apache-2.0
"""The overlapped wrap (docs/loop-repair.md section 2, "The overlapped wrap"): a walk cut two steps long closes for the
legs, and a tail on a beat of its own closes on no cut of two steps, so the seam gate refuses every one. The clip goes on
past the cut; the frames beside the wrap are made again between the clip's frame a cycle apart and the cut's own, and
the loop passes the gate, still two steps, its body standing where it stood.

The walker is seen from the front, 26 frames a cycle, its tail swinging round on a beat of 41 frames, in a 73-frame clip.
The interpolator is a stand-in that draws the walker between two frames the way an optical-flow in-between does: the body
at the time between them, the tail's tip on the line between its two places — and says its passes agree. A second walker,
a big tuft bobbing on a short tail, is carried over at the wrap with its torso where it stood, or with its torso sliding.
A third, painted without a line and seen from behind, swings a big tail across its hand between two frames a cycle apart,
and the real RIFE tears the hand there. With `--overlap-wrap off` the wrap is not made again: nothing is read for it, and
the gate refuses the cut as filmed. Synthetic frames only."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import loop, repair, rife
from sprite_gen.video.interpolation_quality import TORN_WARN, faults
from tests.video.test_loop_repair import _no_rife

FRAMES, CYCLE, TAIL = 73, 26, 41
REACH, LIFT, TUFT = 40, 14, 18  # the tail's tip swings round this far sideways and up and down, px; its tuft's radius
# The second walker: a tuft of BIG_TUFT px held at the hips, bobbing BOB px up and down every BOB_FRAMES frames — 2.6 bobs
# a cut, which starts it at the bottom of its bob and ends it at the top — and a cycle on carried ASIDE px a frame sideways;
# where its torso slides, the whole walker stands SLID px aside a cycle on (the clip walked on over the cycle).
BIG_TUFT, BOB, BOB_FRAMES, ASIDE, SLID = 30, 16, 10, 8, 3


def _tip(k: float) -> tuple[float, float]:
    a = 2 * math.pi * k / TAIL
    return REACH * math.sin(a), LIFT * math.cos(a)


def _bob(k: float) -> float:
    return BOB * math.cos(2 * math.pi * k / BOB_FRAMES)


def _walker(body: float, tip: tuple[float, float], *, tuft: float = TUFT, hang: int = 72, aside: int = 0) -> Image.Image:
    """The walker at `body` frames into its walk, its tail's tip at `tip`: legs side by side, the nearer foot drawn lower
    each step, arms swinging opposite, and a tail from the hips ending in a tuft of radius `tuft`, `hang` px below the head
    at rest; the whole walker `aside` px right of its place."""
    ph = 2 * math.pi * (body + 5) / CYCLE
    im = Image.new("RGBA", (300, 240))
    d = ImageDraw.Draw(im)
    cx, y = 150 + aside, 40 + round(2 * math.cos(2 * ph))
    drop = round(6 * math.sin(ph))
    tx, ty = tip
    d.line((cx, y + 60, cx + tx, y + hang + ty), fill=(180, 120, 40, 255), width=18)
    d.ellipse((cx + tx - tuft, y + hang + ty - tuft, cx + tx + tuft, y + hang + ty + tuft), fill=(200, 140, 60, 255))
    d.rectangle((cx - 12, y + 80, cx - 1, y + 120 + drop), fill=(60, 60, 70, 255))
    d.rectangle((cx, y + 80, cx + 11, y + 120 - drop), fill=(60, 60, 70, 255))
    d.rectangle((cx - 16, y + 30, cx + 16, y + 84), fill=(200, 80, 80, 255))
    d.rectangle((cx - 12, y, cx + 12, y + 28), fill=(230, 200, 170, 255))
    sw = round(8 * math.sin(ph))
    d.rectangle((cx - 22, y + 34 + sw, cx - 16, y + 70 + sw), fill=(225, 225, 225, 255))
    d.rectangle((cx + 16, y + 34 - sw, cx + 22, y + 70 - sw), fill=(225, 225, 225, 255))
    d.point((cx - 6, y + 40 + round(body) % 11), fill=(250, 250, 250, 255))  # no two frames the same picture
    return im


def _filmed(k: int) -> Image.Image:
    return _walker(k, _tip(k))


def _key(frame: Image.Image) -> str:
    """A frame's picture wherever the canvas puts it: its pixels inside its own box."""
    return hashlib.sha256(frame.crop(frame.getbbox()).tobytes()).hexdigest()


class Between:
    """A stand-in RIFE for the walker: between two frames of the clip — wherever the loop placed them — at t, the body at
    the time t of the way from the first to the second (the shorter way round its cycle), the tail's tip t of the way along
    the line between its two places, placed where the second frame is. Records each call as (a, b, t) by clip frame, and
    refuses a frame the clip did not film."""

    def __init__(self, frames: int = FRAMES):
        self.of = {_key(_filmed(k)): k for k in range(frames)}
        self.calls: list[tuple[int, int, float]] = []

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        ka, kb = self.of[_key(a)], self.of[_key(b)]  # KeyError: made from a frame the clip did not film
        self.calls.append((ka, kb, round(t, 4)))
        apart = (ka - kb + CYCLE / 2) % CYCLE - CYCLE / 2
        (xa, ya), (xb, yb) = _tip(ka), _tip(kb)
        drawn = _walker(kb + (1 - t) * apart, (xa + t * (xb - xa), ya + t * (yb - ya)))
        # where the loop put b: its box against the box of the frame the clip filmed
        dx, dy = (p - q for p, q in zip(b.getbbox()[:2], _filmed(kb).getbbox()[:2]))
        out = Image.new("RGBA", b.size)
        out.paste(drawn, (dx, dy))
        return out

    def read(self, a: Image.Image, b: Image.Image, t: float) -> tuple[Image.Image, np.ndarray]:
        """The frame between, and its passes agreeing everywhere: a drawing has one way through."""
        return self(a, b, t), np.zeros((b.height, b.width), dtype=np.float32)


def _bobbing(body: float, tip: tuple[float, float], aside: int = 0) -> Image.Image:
    """The second walker: the walker with a big tuft on a short tail, whose bob moves the picture and not the body's mass."""
    return _walker(body, tip, tuft=BIG_TUFT, hang=56, aside=aside)


def _pixels(frame: Image.Image) -> str:
    return hashlib.sha256(frame.tobytes()).hexdigest()


class Drawn:
    """A stand-in RIFE for frames drawn here (`draw`), each known by its pixels where it stands: between two of them at t,
    the walker at the second's time, its tail's tip and its place t of the way along the line between the two's. Records
    each call as (time of a, time of b, t)."""

    def __init__(self, walker):
        self.walker = walker
        self.of: dict[str, tuple[float, tuple[float, float], int]] = {}
        self.calls: list[tuple[float, float, float]] = []

    def draw(self, body: float, tip: tuple[float, float], aside: int = 0) -> Image.Image:
        frame = self.walker(body, tip, aside)
        self.of[_pixels(frame)] = (body, tip, aside)
        return frame

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        (ba, (xa, ya), sa), (bb, (xb, yb), sb) = self.of[_pixels(a)], self.of[_pixels(b)]
        self.calls.append((ba, bb, round(t, 4)))
        return self.draw(bb, (xa + t * (xb - xa), ya + t * (yb - ya)), round(sa + t * (sb - sa)))

    def read(self, a: Image.Image, b: Image.Image, t: float) -> tuple[Image.Image, np.ndarray]:
        return self(a, b, t), np.zeros((b.height, b.width), dtype=np.float32)


def _second(slid: int = 0) -> tuple[Drawn, list[Image.Image], dict[str, dict[int, Image.Image]]]:
    """The second walker's loop as cut, its tuft bobbing and the walker in its place; and the clip's frames a cycle after
    its first ones, the tuft carried on its bob and ASIDE px a frame sideways, the whole walker `slid` px aside."""
    drawn = Drawn(_bobbing)
    frames = [drawn.draw(q, (0.0, _bob(q))) for q in range(CYCLE)]
    after = {q: drawn.draw(q, (ASIDE * (q + 1), _bob(CYCLE + q)), slid) for q in range(max(repair.OVERLAP_WIDTHS) + 1)}
    return drawn, frames, {"after": after, "before": {}}


def _overlapped(drawn: Drawn, frames: list[Image.Image], others: dict) -> tuple[list[Image.Image], dict]:
    """The overlapped wrap of the second walker's loop, its steps taken as two (the walker shows no crossings)."""
    return repair.overlap_wrap(frames, others, drawn.read, seam=loop.wrap_on_cells, steps=lambda f: {"verdict": "two"},
                               seam_max=loop.SEAM_RATIO_MAX, reads=True)


def _places(tried: dict, slid: int) -> list[int]:
    """Where the walker stands, px aside, on each frame of a try's stretch as it was shown: its place where the loop shows
    its own frame, and t of the way from SLID to it on a frame made at t."""
    made = {r["frame"]: r["t"] for r in tried["made"]}
    return [round((1 - made[q]) * slid) if q in made else 0 for q in tried["stretch"]]


def _mass_step(frames: list[Image.Image]) -> float:
    return float(np.abs(np.diff([loop.body_centre(f, f.getbbox()) for f in frames])).max())


def _keyed(root: Path) -> Path:
    keyed = root / "keyed"
    keyed.mkdir(parents=True)
    for k in range(FRAMES):
        _filmed(k).save(keyed / f"{k:03}.png")
    return keyed


def _cut(root: Path, keyed: Path, *, repair_mode: str = "auto", interpolate=None) -> dict:
    """The walker cut as `video-loop --state walk --anchor motion-auto` cuts it; the report, whether it passed or not."""
    try:
        loop.run_loop(keyed, root / "out", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None,
                      seam_max=loop.SEAM_RATIO_MAX, name="loop", report_path=root / "loop.json", anchor="motion-auto",
                      repair=repair_mode, interpolate=interpolate)
    except SystemExit:
        pass
    return json.loads((root / "loop.json").read_text())


@pytest.mark.slow
def test_a_tail_on_its_own_beat_is_closed_at_the_wrap_with_the_clips_frames_a_cycle_apart(tmp_path):
    keyed = _keyed(tmp_path)
    between = Between()
    report = _cut(tmp_path, keyed, interpolate=between)
    c = report["cycle"]
    s, L = c["start"], c["length"]
    assert c["steps"] == {"count": 2, "by": "crossings"} and abs(L - CYCLE) <= 1
    overlap = report["wrap_overlap"]
    assert overlap["seam"] > loop.SEAM_RATIO_MAX  # the cut as filmed: the gate refuses it
    assert report["status"] == "passed" and report["resampled_seam_ratio"] <= loop.SEAM_RATIO_MAX
    taken = overlap["tries"][-1]
    assert taken["outcome"] == "accepted" and (overlap["width"], overlap["seam_after"]) == (taken["width"], taken["seam"])
    assert taken["seam"] <= loop.SEAM_RATIO_MAX and taken["steps"]["verdict"] == "two" and not taken["drift"]["over"]
    assert taken["rest"]["faster"] == [] and taken["faults"] == [] and taken["rushes"] == []
    assert taken["part"]["step_max_after"] < taken["part"]["step_max_before"]
    # each frame of a run shown between the clip's frame a cycle apart and the cut's own, at the weight the search chose,
    # the weights never going back toward the frame a cycle apart; RIFE asked only for a frame between the two, the weight
    # nearest a half first, and once for a frame at one weight, whatever the run
    calls, asked = list(between.calls), set()
    for tried in overlap["tries"]:
        sign = 1 if tried["side"] == "after" else -1
        assert tried["frames"] == repair.overlap_run(tried["side"], tried["width"], L)
        weights = [r["t"] for r in tried.get("made", [])]
        assert weights == sorted(weights)
        rows = {r["frame"]: r for r in tried.get("made", [])}
        order = tried.get("made_order", [])
        assert sorted(order) == sorted(q for q, r in rows.items() if 0 < r["t"] < 1)
        assert [abs(rows[q]["t"] - 0.5) for q in order] == sorted(abs(rows[q]["t"] - 0.5) for q in order)
        for q in order:
            r = rows[q]
            late, early = (s + q + L, s + q) if sign > 0 else (s + q, s + q - L)
            if (tried["side"], q, r["t"]) not in asked:
                asked.add((tried["side"], q, r["t"]))
                ka, kb, t = calls.pop(0)
                assert (ka, kb) == (late, early) and abs(t - r["t"]) < 1e-3
    assert calls == [] and overlap["calls"] == len(between.calls) == len(asked)
    run = overlap["frames"]
    # every other frame is the clip's own, and nothing else was made
    assert report["jump_repair"]["overlapped"] == run and report["jump_repair"]["replaced"] == []
    cells = sorted((tmp_path / "out" / "cycle").glob("frame-*.png"))
    assert len(cells) == L
    filmed = {_key(_filmed(k)): k for k in range(FRAMES)}
    assert [filmed.get(_key(Image.open(p).convert("RGBA"))) for p in cells] == [None if q in run else s + q for q in range(L)]


def test_the_drift_bound_is_the_stretch_as_the_loop_plays_it_never_the_clips_frames_a_cycle_apart():
    """(a) The validator's counterexample, in synthetic form: the clip's own step into its frame a cycle on moves the body's
    mass further than any step of the stretch — the second walker's tuft carried 8 px aside a frame — and a run made near
    that frame was taken against a bound read off those frames. Every run's bound is the stretch's own, as the loop plays
    it: the foot line's and the ground's largest steps there, and the torso's, which stands where it stood (the walker is
    drawn in its place on every frame of the loop); the body's mass is reported beside it, its own largest step there."""
    drawn, frames, others = _second()
    new, record = _overlapped(drawn, frames, others)
    assert record["seam"] > loop.SEAM_RATIO_MAX
    made = [t for t in record["tries"] if "made" in t]
    assert made
    for tried in made:
        stretch = [frames[q] for q in tried["stretch"]]
        played = np.abs(np.diff([repair.drift_points(f) for f in stretch], axis=0)).max(axis=0)  # the feet and the ground
        foot, torso, ground = tried["drift"]["bound"]
        assert (foot, ground) == pytest.approx((played[0], played[2]), abs=1e-3) and torso < 0.1
        assert tried["drift"]["body_mass"]["bound"] == pytest.approx(_mass_step(stretch), abs=1e-3)


@pytest.mark.slow
def test_a_torso_that_slides_is_refused_however_the_tail_swings():
    """(b) The second walker whose frames a cycle on stand SLID px aside as well as carrying the tuft over: the clip walked
    on over the cycle, and a frame made near those frames slides the torso. Read on the torso, no run moves the walker
    further in a step than the stretch's own largest step (it stands still there) and a pixel — its place on the frames
    shown, drawn again — so the frame next to the wrap is held toward the cut's own, off the ease; the wrap stays open and
    the cut is kept as filmed for the gate to refuse."""
    drawn, frames, others = _second(slid=SLID)
    new, record = _overlapped(drawn, frames, others)
    made = [t for t in record["tries"] if "made" in t]
    assert made
    for tried in made:
        assert np.abs(np.diff(_places(tried, SLID))).max() <= repair.DRIFT_PIXEL
        assert tried["made"][0]["t"] > tried["eased"][0] + 0.25
    assert record["frames"] == [] and all(a is b for a, b in zip(new, frames))


def test_a_tail_carried_over_alone_is_taken_the_torso_within_its_pixel():
    """(c) The second walker with only its tuft carried over, the walker in its place: the run is taken near the ease and
    the loop passes the gate. Its torso moves within the stretch's own largest step and a pixel; the body's mass, which the
    tuft pulls, moves 2 px and more past its own, reported, not held: moving that part is what the run is for."""
    drawn, frames, others = _second()
    new, record = _overlapped(drawn, frames, others)
    assert record["frames"], [(t["width"], t.get("rejected_for")) for t in record["tries"]]
    taken = record["tries"][-1]
    assert taken["outcome"] == "accepted" and taken["seam"] <= loop.SEAM_RATIO_MAX
    assert taken["made"][0]["t"] <= taken["eased"][0] + 0.1
    (_, torso, _), (_, moved, _) = taken["drift"]["bound"], taken["drift"]["moved"]
    assert torso < 0.1 and moved <= torso + repair.DRIFT_PIXEL
    assert record["torso"]["regions"]
    stretch = taken["stretch"]
    assert _mass_step([new[q] for q in stretch]) > _mass_step([frames[q] for q in stretch]) + 2 * repair.DRIFT_PIXEL
    assert taken["drift"]["body_mass"] == pytest.approx({"bound": _mass_step([frames[q] for q in stretch]),
                                                         "moved": _mass_step([new[q] for q in stretch])}, abs=1e-3)


@pytest.mark.slow
def test_with_the_overlapped_wrap_off_nothing_is_read_for_the_wrap_and_the_gate_refuses_the_cut_as_filmed(tmp_path, monkeypatch, capsys):
    """`sprite-gen video-loop --overlap-wrap off`, as a caller passes it on a host whose RIFE runs on a CPU, where one frame
    of the wrap takes minutes: the wrap the gate refuses is not made again. Nothing is read for it — no RIFE is installed
    here, and the record says the wrap was turned off, not that RIFE is missing — and the gate refuses the cut as filmed:
    the loop fails on its seam, as it does where every run is refused. Without the flag the wrap is on; another word is
    refused."""
    parser = argparse.ArgumentParser()
    loop.add_arguments(parser)
    assert parser.parse_args(["--frames-dir", "k", "--out-dir", "o"]).overlap_wrap == "on"
    with pytest.raises(SystemExit):
        parser.parse_args(["--frames-dir", "k", "--out-dir", "o", "--overlap-wrap", "auto"])
    _no_rife(monkeypatch, tmp_path)
    keyed = _keyed(tmp_path)
    with pytest.raises(SystemExit, match="^video-loop: loop seam ratio"):
        loop.main(["--frames-dir", str(keyed), "--out-dir", str(tmp_path / "out"), "--fps", "24.0", "--state", "walk",
                   "--anchor", "motion-auto", "--report", str(tmp_path / "loop.json"), "--overlap-wrap", "off"])
    report = json.loads((tmp_path / "loop.json").read_text())
    assert report["status"] == "failed" and report["error"].startswith("video-loop: loop seam ratio")
    assert report["cycle"]["steps"] == {"count": 2, "by": "crossings"}
    overlap = report["wrap_overlap"]
    assert overlap["seam"] > loop.SEAM_RATIO_MAX and overlap["seam_max"] == loop.SEAM_RATIO_MAX
    assert (overlap["skipped"], overlap["tries"], overlap["frames"], overlap["calls"]) == ("turned off (--overlap-wrap off)", [], [], 0)
    # a frame read for the wrap looks for RIFE first, and where none is installed the record names it missing
    assert "rife" not in overlap and "interpolator" not in overlap and "torso" not in overlap
    assert "overlapped" not in report["jump_repair"]
    err = capsys.readouterr().err
    assert ("video-loop: warning: the cut's wrap does not close (seam " in err
            and "the overlapped wrap was not made: turned off (--overlap-wrap off) (docs/loop-repair.md section 2)" in err)
    with pytest.raises(SystemExit, match="unknown --overlap-wrap 'auto'"):
        loop.run_loop(keyed, tmp_path / "again", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None,
                      seam_max=loop.SEAM_RATIO_MAX, name="loop", report_path=None, anchor="motion-auto", overlap_wrap="auto")


@pytest.mark.slow
def test_the_cut_as_filmed_is_refused_at_the_seam_with_the_repair_off(tmp_path):
    report = _cut(tmp_path, _keyed(tmp_path), repair_mode="off")
    assert report["status"] == "failed" and report["error"].startswith("video-loop: loop seam ratio")
    assert report["cycle"]["steps"] == {"count": 2, "by": "crossings"} and "wrap_overlap" not in report


@pytest.mark.slow
def test_without_rife_the_wrap_is_cut_as_filmed_and_says_so(tmp_path, monkeypatch, capsys):
    _no_rife(monkeypatch, tmp_path)
    report = _cut(tmp_path, _keyed(tmp_path))
    assert report["status"] == "failed" and report["error"].startswith("video-loop: loop seam ratio")
    overlap = report["wrap_overlap"]
    assert overlap["frames"] == [] and overlap["install"] == "sprite-gen rife install" and overlap["calls"] == 0
    assert "the overlapped wrap was not made" in capsys.readouterr().err


@pytest.mark.slow
def test_without_rife_and_the_repair_on_the_loop_fails_asking_for_it(tmp_path, monkeypatch):
    _no_rife(monkeypatch, tmp_path)
    report = _cut(tmp_path, _keyed(tmp_path), repair_mode="on")
    assert report["status"] == "failed" and "at a wrap a part on its own beat does not close" in report["error"]
    assert "sprite-gen rife install" in report["error"]


def _stopped(tried: dict) -> dict:
    """The one frame a run made before it ended: the frame nearest a half, made first, and its fault ended the run."""
    (stopped,) = tried["made"]
    assert tried["made_order"] == [stopped["frame"]] and stopped["faults"]
    assert abs(stopped["t"] - 0.5) == min(abs(w - 0.5) for w in tried["weights"])
    assert sorted([stopped["frame"], *tried["unmade"]]) == sorted(tried["frames"])
    return stopped


@pytest.mark.slow
def test_a_made_frame_with_a_fault_ends_its_run_and_the_wrap_is_cut_as_filmed(tmp_path, capsys):
    """A stand-in that returns nothing: each run ends at the first frame it makes (`fault`), the rest of it not made, each
    width tried once, and the gate refuses the cut as filmed."""
    calls = []

    class Empty:
        def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
            calls.append(t)
            return Image.new("RGBA", b.size)

        def read(self, a: Image.Image, b: Image.Image, t: float) -> tuple[Image.Image, np.ndarray]:
            return self(a, b, t), np.zeros((b.height, b.width), dtype=np.float32)

    report = _cut(tmp_path, _keyed(tmp_path), interpolate=Empty())
    assert report["status"] == "failed" and report["error"].startswith("video-loop: loop seam ratio")
    overlap = report["wrap_overlap"]
    assert overlap["frames"] == [] and [t["width"] for t in overlap["tries"]] == list(repair.OVERLAP_WIDTHS)
    assert all(t["outcome"] == "rejected" and t["rejected_for"] == ["fault"] and _stopped(t)["faults"] == ["empty"]
               for t in overlap["tries"])
    # the overlapped wrap's calls first, once a frame and weight; the jump repair then reads the cut as filmed, its wrap off
    # the flow
    made = list(dict.fromkeys((t["side"], r["frame"], r["t"]) for t in overlap["tries"] for r in t["made"] if 0 < r["t"] < 1))
    assert overlap["calls"] == len(made) and calls[:len(made)] == pytest.approx([t for _, _, t in made], abs=1e-3)
    assert "the overlapped wrap was refused" in capsys.readouterr().err


class Torn(Between):
    """The stand-in, saying its passes disagree over a patch of the body on a frame made far from both of its two (a
    weight between 0.2 and 0.8): a part torn there."""

    def read(self, a: Image.Image, b: Image.Image, t: float) -> tuple[Image.Image, np.ndarray]:
        frame = self(a, b, t)
        apart = np.zeros((b.height, b.width), dtype=np.float32)
        if 0.2 < t < 0.8:
            x0, y0, x1, y1 = frame.getbbox()
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            apart[cy - 15:cy + 15, cx - 15:cx + 15] = 0.5
        return frame, apart


@pytest.mark.slow
def test_a_frame_its_passes_disagree_on_ends_its_run_and_the_wrap_is_cut_as_filmed(tmp_path, capsys):
    """The first walker with a stand-in whose passes disagree over a patch of its body on a frame made far from both of its
    two: each run makes the frame nearest a half first, reads it torn, and ends there, the rest of it not made; every
    width is refused (`fault`, torn), and the gate refuses the cut as filmed, the warning naming the torn frame."""
    torn = Torn()
    report = _cut(tmp_path, _keyed(tmp_path), interpolate=torn)
    assert report["status"] == "failed" and report["error"].startswith("video-loop: loop seam ratio")
    overlap = report["wrap_overlap"]
    assert overlap["frames"] == [] and [t["width"] for t in overlap["tries"]] == list(repair.OVERLAP_WIDTHS)
    for t in overlap["tries"]:
        assert t["outcome"] == "rejected" and t["rejected_for"] == ["fault"] and t["faults"] == ["torn"]
        stopped = _stopped(t)
        assert stopped["faults"] == ["torn"] and stopped["proposal"]["torn"] > TORN_WARN
    # one call a run, the overlapped wrap's first; the jump repair then reads the cut as filmed
    made = list(dict.fromkeys((t["side"], t["made"][0]["frame"], t["made"][0]["t"]) for t in overlap["tries"]))
    assert overlap["calls"] == len(made)
    assert [t for _, _, t in torn.calls[:len(made)]] == pytest.approx([t for _, _, t in made], abs=1e-3)
    err = capsys.readouterr().err
    assert "the overlapped wrap was refused" in err and "fault (torn at frame" in err


PAINTED = (480, 640)  # the third walker's canvas: the tail's tip swings over 200 px between its two frames


def _shape(draw) -> np.ndarray:
    im = Image.new("L", PAINTED, 0)
    draw(ImageDraw.Draw(im))
    return np.asarray(im) > 127


def _paint(canvas: np.ndarray, mask: np.ndarray, dark: tuple, light: tuple, across: bool = True) -> None:
    """`mask` painted over `canvas` in a soft gradient from `dark` to `light`, left to right (or top to bottom): a fill with
    no outline."""
    ys, xs = np.nonzero(mask)
    v = xs if across else ys
    v = (v - v.min()) / max(1, v.max() - v.min())
    canvas[ys, xs, :3] = (np.array(dark)[None] * (1 - v[:, None]) + np.array(light)[None] * v[:, None]) / 255
    canvas[ys, xs, 3] = 1


def _painted(tail: float) -> Image.Image:
    """The third walker, seen from behind and painted without a line: legs, boots, shorts, a shirt, a head, the left arm
    hanging with a four-fingered hand, and a big furred tail from the hips in front of it all, swung `tail` degrees toward
    the hand (0 hangs straight down). Two of its frames a cycle apart differ by the tail alone."""
    f = np.zeros((PAINTED[1], PAINTED[0], 4))
    cx = 250
    _paint(f, _shape(lambda d: (d.rounded_rectangle((cx - 52, 380, cx - 6, 600), 18, fill=255),
                                d.rounded_rectangle((cx + 6, 380, cx + 52, 600), 18, fill=255))), (232, 180, 150), (250, 214, 190))
    _paint(f, _shape(lambda d: (d.rectangle((cx - 56, 560, cx - 2, 620), fill=255), d.rectangle((cx + 2, 560, cx + 56, 620), fill=255))),
           (110, 60, 30), (150, 90, 50), across=False)
    _paint(f, _shape(lambda d: d.rounded_rectangle((cx - 62, 300, cx + 62, 400), 20, fill=255)), (50, 90, 160), (90, 130, 200), across=False)
    _paint(f, _shape(lambda d: d.rounded_rectangle((cx - 66, 130, cx + 66, 310), 30, fill=255)), (225, 225, 235), (250, 250, 255))
    _paint(f, _shape(lambda d: d.ellipse((cx - 55, 20, cx + 55, 140), fill=255)), (200, 190, 220), (240, 235, 250), across=False)
    _paint(f, _shape(lambda d: d.rounded_rectangle((cx - 96, 150, cx - 68, 330), 14, fill=255)), (235, 190, 160), (250, 215, 190))
    _paint(f, _shape(lambda d: (d.ellipse((cx - 104, 324, cx - 62, 364), fill=255),
                                *[d.rounded_rectangle((cx - 104 + 10 * i, 354, cx - 96 + 10 * i, 392 - 4 * abs(i - 1.5)), 4, fill=255)
                                  for i in range(4)])), (230, 170, 140), (250, 205, 180))
    a = math.radians(tail)
    root, length, half = (cx - 20, 330), 230, 85
    tip = (root[0] - length * math.sin(a), root[1] + length * math.cos(a))
    nx, ny = math.cos(a), math.sin(a)
    tuft = _shape(lambda d: (d.polygon([(root[0] - 30 * nx, root[1] - 30 * ny), (root[0] + 30 * nx, root[1] + 30 * ny),
                                        (tip[0] + half * nx, tip[1] + half * ny), (tip[0] - half * nx, tip[1] - half * ny)], fill=255),
                             d.ellipse((tip[0] - half, tip[1] - half, tip[0] + half, tip[1] + half), fill=255)))
    ys, xs = np.nonzero(tuft)
    along = (xs - root[0]) * -math.sin(a) + (ys - root[1]) * math.cos(a)
    across = (xs - root[0]) * nx + (ys - root[1]) * ny
    fur = 0.85 + 0.15 * (0.5 + 0.5 * np.sin(across / 7.0 + along / 40.0))  # bands along the tail
    f[ys, xs, :3] = np.array([236, 232, 244])[None] / 255 * fur[:, None]
    f[ys, xs, 3] = 1
    return Image.fromarray(np.uint8(np.clip(f, 0, 1) * 255 + 0.5), "RGBA")


# The weights at which the real RIFE drags the third walker's hand apart, by model (`rife.model_name()`): rife-v4.6 at 0.5
# and 0.7; rife-v4.25 at 0.5, and at 0.7 it keeps the hand whole, its fingers their own colour.
TORN_AT = {"rife-v4.6": (0.5, 0.7), "rife-v4.25": (0.5,)}


@pytest.mark.slow
@pytest.mark.real_rife
@pytest.mark.timeout(900)  # 102 calls of the binary at 480 x 640: over a minute on a GPU, minutes on a CPU's Vulkan driver
def test_real_rife_a_frame_made_across_a_hand_is_torn_and_refused_and_a_short_swing_is_kept():
    """The third walker: between its frame with the tail swung over the hand (48 degrees) and its frame a cycle on with
    the tail hanging (-8 degrees), the real RIFE drags the hand apart where the model does (TORN_AT), and only the passes
    say so — the frame is refused as torn, and without that reading nothing refuses it (painted without a line, the
    outline readings pass it, and its coverage keeps no ghost band); where it keeps the hand whole the passes read it
    under the bar. Swung 10 degrees, past no hand, its frame half way reads nothing."""
    interpolate = rife.Rife()
    early = _painted(-8)
    for u in (0.5, 0.7):
        _, said = repair.overlap_frame(interpolate.read, _painted(48), early, u, reads=True)
        if u in TORN_AT[interpolate.model_name]:
            assert said["faults"] == ["torn"] and said["proposal"]["torn"] > TORN_WARN
        else:
            assert said["faults"] == [] and said["proposal"]["torn"] < TORN_WARN
        assert faults({**said["proposal"], "torn": None}) == []
    _, said = repair.overlap_frame(interpolate.read, _painted(2), early, 0.5, reads=True)
    assert said["faults"] == [] and said["proposal"]["torn"] < TORN_WARN / 2
    assert interpolate.calls == 3 * (2 + rife.PASS_CALLS)


def test_the_weights_ease_from_the_late_frame_to_the_early_off_the_ease_only_where_the_body_needs_it():
    """`overlap_weights` on lines drawn by hand: three frames between a late frame and an early one. Unbounded, they are
    the ease in and out (`overlap_weight`) on the grid; a window of the rest of the body that moves with the share taken
    in a step, bounded in the second step to a tenth, moves the weights off the ease there and no further; a body measure
    that may not move leaves no weighting."""
    P, G = 4, repair.OVERLAP_GRID + 1
    grid = np.linspace(0.0, 1.0, G)
    step = np.zeros((P, G, G, 2))
    step[..., 0] = 1.0 + 4.0 * np.abs(grid[None, :] - grid[:, None])  # the part, unbounded
    body = np.zeros((P, G, G, 3))
    eased = [round(repair.overlap_weight(p, P - 1) * (G - 1)) / (G - 1) for p in range(1, P)]
    free = repair.overlap_weights(step, body, [], np.zeros(0), np.ones(3))
    assert free is not None and free["weights"] == pytest.approx(eased) and free["off_ease"] < 0.5 / (G - 1) + 1e-9
    step[1, ..., 1] = np.abs(grid[None, :] - grid[:, None])  # the rest of the body, in the second step only
    held = repair.overlap_weights(step, body, [1], np.array([0.1]), np.ones(3))
    assert held is not None and held["weights"][1] - held["weights"][0] <= 0.1 + 1e-9
    assert held["weights"] == sorted(held["weights"]) and held["off_ease"] > free["off_ease"]
    body[...] = 1.0
    assert repair.overlap_weights(step, body, [], np.zeros(0), np.full(3, 0.5)) is None
