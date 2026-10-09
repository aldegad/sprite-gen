# SPDX-License-Identifier: Apache-2.0
"""A filmed ghost in a loop delivered as cut is given way in `video-loop`'s jump repair stage
(docs/loop-repair.md sections 2 and 4): a walk filmed in one direction never meets the cycle
alignment, so its cut is what is delivered. The ghost screen reads the cut as filmed, before the
repair; each ghost it names is a round of that repair (`why` ghost) — RIFE's frame between its two
neighbours where both are clean and the frame has no fault, else the clean frame beside it (the one
after, else the one before), else, both being filmed ghosts too, kept and named. A cell given way is
in `jump_repair.replaced`, so the source projection and the restoration read it as a made cell, and
the GIF and WebP are checked for one frame per run of identical cells (a cell given way to the frame
beside it is that frame shown twice in a row). The seam gate, on in every cut here, reads a cell given
way to the frame beside it as filmed (`seam_as_filmed`): at the wrap that frame twice in a row is a
step of two or a step of nothing, which would refuse a cut that closes or pass one that does not.
`--anchor body`, the walk's default, reads the wrap of its strip (`seam_ratio_after_anchor`) the same way.

The fixtures are synthetic: the walk of tests/video/test_cycle_align.py (a body and a foot going round,
20 frames a cycle) with a band of 0.3 coverage beside the foot (tests/video/test_ghost_screen.py `_band`)
— a small one that reads well over the ghost limit and is no jump (the step into it stays under 1.4
times the median), or the wide one, which also reads as a jump — and the side walker of
tests/video/test_one_step_cut.py cut with the application's flags. The stand-in RIFE draws the walk's
true frame half way between two of its frames; the real-RIFE case is marked `real_rife`."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.video import align, rife, restoration, source
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import repair
from sprite_gen.video.compare import Loop
from sprite_gen.video.interpolation_quality import GHOST_WARN
from tests.video import test_ghost_screen as tg
from tests.video.test_cycle_align import _walker as _block_walker
from tests.video.test_loop_repair import _no_rife

L = 20
SMALL = (0.36, 0.80, 0.44, 0.87)  # 8 x 8 px beside the foot: reads 0.011, and the step into it is no jump
WIDE = (0.25, 0.80, 0.75, 0.95)  # around the feet: reads 0.36, and the steps into and out of it read as jumps
# The walk two frames into its cycle: cut from there, its wrap is an ordinary step (the seam ratio of the cut reads
# 1.05), as a cut chosen to close has it. From the cycle's start the wrap is a short step (0.44), and two steps there
# still read under the gate.
EVEN = 2


def _keyed(root: Path, name: str, ghosts: tuple[int, ...], box=SMALL, phase: int = 0) -> Path:
    """Keyed frames of the walk from `phase` frames into its cycle, a little more than a cycle, its frames in `ghosts`
    (frames of the clip, which the cut starts at) filmed with a band."""
    keyed = root / f"{name}-keyed"
    keyed.mkdir(parents=True)
    for k in range(L + 6):
        frame = _block_walker(2 * math.pi * (k + phase) / L)
        (tg._band(frame, box=box) if k % L in ghosts else frame).save(keyed / f"{k:04d}.png")
    return keyed


def _cut(keyed: Path, out: Path, length: int = L, anchor: str = "none", **kw) -> dict:
    """`video-loop --cycle fixed --start 0 --anchor none` (or the anchor given) with the jump repair (auto unless given)
    and the seam gate, the default `--seam-max` the application cuts with."""
    return loop_mod.run_loop(keyed, out, fps=24.0, state="walk", min_len=None, max_len=None, n_out=None,
                             seam_max=loop_mod.SEAM_RATIO_MAX, name="walk", report_path=None, cycle_mode="fixed", start=0,
                             length=length, anchor=anchor, **kw)


class _Middle:
    """A stand-in RIFE: the walk's true frame half way between two of its frames, found by their bytes;
    a frame it does not know gets the later one. Between the two phases of the walk in `ghostly` its frame
    carries a band (tests/video/test_ghost_screen.py `_band`): RIFE's frame for a ghost, a ghost too."""

    def __init__(self, length: int = L, ghostly: tuple[tuple[int, int], ...] = ()):
        self.length = length
        self.ghostly = ghostly
        self.phase = {_block_walker(2 * math.pi * k / length).tobytes(): k for k in range(length)}
        self.calls: list[tuple[int | None, int | None, float]] = []

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        ka, kb = self.phase.get(a.tobytes()), self.phase.get(b.tobytes())
        self.calls.append((ka, kb, t))
        if ka is None or kb is None:
            return b.copy()
        made = _block_walker(2 * math.pi * (ka + t * ((kb - ka) % self.length)) / self.length)
        return tg._band(made) if (ka, kb) in self.ghostly else made


def _frames(path: Path) -> list[Image.Image]:
    with Image.open(path) as im:
        out = []
        for k in range(getattr(im, "n_frames", 1)):
            im.seek(k)
            out.append(im.convert("RGBA"))
    return out


def _cells(out: Path) -> list[Image.Image]:
    meta = json.loads((out / "walk.strip.json").read_text())
    strip = Image.open(out / "walk.strip.png").convert("RGBA")
    return [strip.crop((k * meta["w"], 0, (k + 1) * meta["w"], meta["h"])) for k in range(meta["frames"])]


def _cycle(out: Path, k: int) -> Image.Image:
    return Image.open(out / "cycle" / f"frame-{k:03d}.png").convert("RGBA")


def _delivered_clean(out: Path) -> None:
    """No delivered frame of the loop — `cycle/`, the strip, the WebP, the GIF — carries a ghost."""
    for name, frames in (("cycle", [Image.open(p).convert("RGBA") for p in sorted((out / "cycle").glob("frame-*.png"))]),
                         ("strip", _cells(out)), ("webp", _frames(out / "walk.webp")), ("gif", _frames(out / "walk.gif"))):
        worst = max(rife.ghost(f) for f in frames)
        assert worst <= GHOST_WARN, f"{name} delivers a ghost ({worst})"


def _ghost_rounds(report: dict) -> list[dict]:
    return [r for r in report["jump_repair"]["rounds"] if r.get("why") == "ghost"]


# --- the rounds (repair.repair_jumps) ------------------------------------------------------------


def _true_middle(frames: list[Image.Image]):
    """For tg._stride: the stride's own frame half way between two of `frames` (by identity)."""
    index = {id(f): k for k, f in enumerate(frames)}

    def middle(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        k = index[id(a)] + 1
        return tg._walker(18 - 3 * k, -18 + 3 * k)
    return middle


def test_each_filmed_ghost_is_a_round_of_its_own_and_gives_way_to_a_clean_frame():
    """Ghost 2 between clean frames: RIFE's frame, no fault, taken. Ghosts 6, 7, 8 in a row: 6 gives way to
    5 (7 after it is a ghost), 8 to 9, and 7 has no clean frame beside it — kept. Outside the jump budget."""
    frames = tg._stride(10)
    for k in (2, 6, 7, 8):
        frames[k] = tg._band(frames[k])
    out, record = repair.repair_jumps(frames, _true_middle(frames), max_frames=0)
    rounds = [r for r in record["rounds"] if r["why"] == "ghost"]
    assert [(r["target"], r["taken"], r["outcome"]) for r in rounds] == [
        (2, "rife", "accepted"), (6, {"source": 5}, "given-way"), (7, None, "kept"), (8, {"source": 9}, "given-way")]
    assert record["replaced"] == [2, 6, 8] and record["attempts"] == 0
    assert rife.ghost(out[2]) <= GHOST_WARN and out[6] is frames[5] and out[8] is frames[9] and out[7] is frames[7]
    assert rounds[0]["proposal"]["ghost"] <= GHOST_WARN and rounds[0]["faults"] == [] and rounds[0]["original"]["ghost"] > GHOST_WARN
    assert [r.get("no_proposal") for r in rounds[1:]] == ["a frame beside it is a filmed ghost too"] * 3
    assert all(r["proposal"] is None for r in rounds[1:])


def test_a_proposal_that_carries_the_ghost_gives_way_to_the_frame_after_it():
    """RIFE's frame between two clean frames that is a ghost itself is no frame to take: the clean frame after
    the ghost is. With no interpolator at all, the same, and nothing is proposed."""
    frames = tg._stride(8)
    frames[3] = tg._band(frames[3])
    out, record = repair.repair_jumps(frames, lambda a, b, t: tg._band(tg._walker(9, -9)), max_frames=0)
    (round_,) = [r for r in record["rounds"] if r["why"] == "ghost"]
    assert "ghost" in round_["faults"] and round_["taken"] == {"source": 4} and out[3] is frames[4]
    out, record = repair.repair_jumps(frames, None, max_frames=0)
    (round_,) = record["rounds"]
    assert round_["no_proposal"] == "no interpolator" and round_["taken"] == {"source": 4} and record["replaced"] == [3]


# --- video-loop: the cut as delivered -------------------------------------------------------------


def test_a_walk_filmed_in_one_direction_gives_its_small_filmed_ghost_way_at_the_cut(tmp_path, capsys):
    """B1: frame 10 of a walk delivered as cut carries a small band — no jump, so the jump repair did not see it,
    and `cycle/`, the strip and the WebP delivered it. It is given way to RIFE's frame between 9 and 11."""
    middle = _Middle()
    report = _cut(_keyed(tmp_path, "E", (10,)), tmp_path / "E", interpolate=middle)
    out = tmp_path / "E"
    assert rife.ghost(_cycle(out, 10)) <= GHOST_WARN  # before: 0.011, delivered as cut
    _delivered_clean(out)
    assert [g["frame"] for g in report["ghost_screen"]["ghosts"]] == [10]  # the cut as filmed
    (round_,) = _ghost_rounds(report)
    assert (round_["target"], round_["taken"], round_["outcome"], round_["neighbours"]) == (10, "rife", "accepted", [9, 11])
    assert round_["original"]["ghost"] > GHOST_WARN and round_["proposal"]["ghost"] <= GHOST_WARN
    assert middle.calls == [(9, 11, 0.5)]
    assert report["jump_repair"]["replaced"] == [10] and report["jump_repair"]["applied"] is True
    assert report["seam_measurement"] == "rendered-cells"
    given = [{"frame": 10, "ghost": round_["original"]["ghost"], "taken": "rife"}]
    assert report["ghost_given_way"] == given and report["ghost_kept"] == []
    meta = json.loads((out / "walk.strip.json").read_text())
    assert meta["ghost_given_way"] == given and "ghost_kept" not in meta
    assert _cycle(out, 10).tobytes() == _block_walker(2 * math.pi * 10 / L).tobytes()
    err = capsys.readouterr().err
    assert "frame 10 of the cut carries a part-covered band" in err and "given way to RIFE's frame between frames 9 and 11" in err


def test_without_rife_a_filmed_ghost_gives_way_to_the_clean_frame_after_it(tmp_path, monkeypatch, capsys):
    """No RIFE installed (`--repair auto`): the ghost gives way to frame 11, shown twice in a row. The GIF and the
    WebP hold it as one frame of twice the delay and are checked so. No jump was left, so nothing says one was."""
    _no_rife(monkeypatch, tmp_path)
    report = _cut(_keyed(tmp_path, "E", (10,)), tmp_path / "E")
    out = tmp_path / "E"
    assert _cycle(out, 10).tobytes() == _cycle(out, 11).tobytes()  # before: the ghost, delivered
    _delivered_clean(out)
    (round_,) = _ghost_rounds(report)
    assert (round_["taken"], round_["no_proposal"], round_["outcome"]) == ({"source": 11}, "no interpolator", "given-way")
    assert report["ghost_given_way"] == [{"frame": 10, "ghost": round_["original"]["ghost"], "taken": {"source": 11}}]
    jr = report["jump_repair"]
    assert jr["replaced"] == [10] and jr["applied"] is True and "rife" not in jr and "interpolator" not in jr
    assert report["n_out"] == L and report["gif"]["n_frames"] == report["webp"]["n_frames"] == L - 1
    err = capsys.readouterr().err
    assert "given way to frame 11 beside it, shown twice in a row" in err and "RIFE is not installed" in err
    assert "jump frame was not repaired" not in err


def test_a_cell_shown_twice_in_a_row_passes_the_gif_and_webp_check(tmp_path):
    """B3: two identical frames in a row — what giving a ghost way to the frame beside it leaves — are one frame
    of the GIF and of the WebP. The check counted a frame per cell and refused the loop ("19 frames, expected 20")."""
    keyed = _keyed(tmp_path, "E", ())
    (keyed / "0010.png").write_bytes((keyed / "0011.png").read_bytes())
    report = _cut(keyed, tmp_path / "E", repair="off")
    assert report["n_out"] == L and report["gif"]["n_frames"] == report["webp"]["n_frames"] == L - 1
    assert loop_mod.shown_runs(_cells(tmp_path / "E")) == L - 1


def test_a_ghost_with_no_clean_frame_beside_it_is_kept_and_named(tmp_path, capsys):
    """Frames 9, 10, 11 are filmed ghosts: 9 gives way to 8, 11 to 12, and 10, with no clean frame beside it, is
    kept — `ghost_kept`, and a line to film the direction again."""
    report = _cut(_keyed(tmp_path, "E", (9, 10, 11)), tmp_path / "E", interpolate=_Middle())
    out = tmp_path / "E"
    assert _cycle(out, 9).tobytes() == _cycle(out, 8).tobytes() and _cycle(out, 11).tobytes() == _cycle(out, 12).tobytes()
    assert rife.ghost(_cycle(out, 10)) > GHOST_WARN  # nothing clean to show there
    assert [(r["target"], r["taken"]) for r in _ghost_rounds(report)] == [(9, {"source": 8}), (10, None), (11, {"source": 12})]
    assert [g["frame"] for g in report["ghost_given_way"]] == [9, 11]
    (kept,) = report["ghost_kept"]
    assert kept["frame"] == 10 and kept["ghost"] > GHOST_WARN
    assert 10 not in report["jump_repair"]["replaced"] and {9, 11} <= set(report["jump_repair"]["replaced"])
    meta = json.loads((out / "walk.strip.json").read_text())
    assert meta["ghost_kept"] == report["ghost_kept"] and meta["ghost_given_way"] == report["ghost_given_way"]
    assert report["gif"]["n_frames"] == report["webp"]["n_frames"] == L - 2
    err = capsys.readouterr().err
    assert "frame 10 of the cut" in err and "film this direction again" in err


def test_the_screen_reads_the_cut_as_filmed_before_the_repair(tmp_path):
    """B4: a wide band reads as a jump too, so the jump repair replaced the frame and the screen, read after it,
    named nothing — the record did not say the cut held a ghost. Read before the repair, it names it, and the
    round that replaced it says why."""
    report = _cut(_keyed(tmp_path, "E", (10,), box=WIDE), tmp_path / "E", interpolate=_Middle())
    assert [g["frame"] for g in report["ghost_screen"]["ghosts"]] == [10]  # before: [] (read after the repair)
    assert [(r["why"], r["target"], r["taken"]) for r in report["jump_repair"]["rounds"]] == [("ghost", 10, "rife")]
    assert report["ghost_given_way"][0]["frame"] == 10 and report["jump_repair"]["replaced"] == [10]
    _delivered_clean(tmp_path / "E")


def test_repair_off_cuts_the_ghost_as_filmed_and_says_so(tmp_path, capsys):
    report = _cut(_keyed(tmp_path, "E", (10,)), tmp_path / "E", repair="off")
    assert rife.ghost(_cycle(tmp_path / "E", 10)) > GHOST_WARN
    assert [g["frame"] for g in report["ghost_screen"]["ghosts"]] == [10] and "ghost_given_way" not in report
    assert "delivered as cut it shows (--repair off)" in capsys.readouterr().err


def test_repair_on_without_rife_refuses_a_ghost_it_would_make_a_frame_for(tmp_path, monkeypatch):
    """`--repair on` hands a caller no less than it asked for: a ghost between two clean frames is a frame to
    make, and without RIFE the loop fails with the install line, as for a jump."""
    _no_rife(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match=r"sprite-gen rife install.*--repair auto"):
        _cut(_keyed(tmp_path, "E", (10,)), tmp_path / "E", repair="on")


def test_the_alignment_reads_the_cut_given_way_and_carries_its_record(tmp_path):
    """A set of two: E's cut gave its ghost way, so the alignment's screen of `cycle.source/` names nothing there
    (it is the cut as delivered) and the strip metadata carries what the cut gave way, frames of the cut."""
    dirs = []
    for name, ghosts in (("S", ()), ("E", (10,))):
        _cut(_keyed(tmp_path, name, ghosts), tmp_path / name, interpolate=_Middle())
        dirs.append(tmp_path / name)
    cut = json.loads((tmp_path / "E" / "walk.strip.json").read_text())["ghost_given_way"]
    report = align.align_set(dirs, interpolate=_Middle(), report_path=tmp_path / "align.json")
    e = next(r for r in report["loops"] if Path(r["dir"]).name == "E")
    assert e["ghost_screen"]["ghosts"] == [] and e["ghost_at"] == [] and e["retake"] is None
    meta = json.loads((tmp_path / "E" / "walk.strip.json").read_text())
    assert meta["ghost_given_way"] == cut and "cycle_align" in meta


# --- the seam gate: a cell given way is read as filmed ----------------------------------------------


def _phase(k: int) -> int:
    """The phase of the walk at frame k of a clip keyed from EVEN (`_Middle` finds its frames by it)."""
    return (k + EVEN) % L


@pytest.mark.parametrize("rife_frame", ["not installed", "a ghost too"])
def test_a_ghost_at_the_cuts_first_frame_given_way_leaves_the_seam_the_cut_closes_with(tmp_path, monkeypatch, rife_frame):
    """The default seam gate on a walk whose wrap is an ordinary step, its filmed ghost at the cut's first frame, given
    way to frame 1 — no RIFE installed, or RIFE's frame between frames 19 and 1 a ghost too. Frame 1 then shows twice in
    a row and the step from the last frame into the loop is two: read on the cells as they play, the wrap measured 2.17
    times the mean step and a walk that closes was refused, "the cycle does not close". The gate reads the cell given
    way as filmed — on the source frames, the seam `--repair off` reads."""
    filmed = _cut(_keyed(tmp_path, "filmed", (0,), phase=EVEN), tmp_path / "filmed", repair="off")
    middle = None
    if rife_frame == "not installed":
        _no_rife(monkeypatch, tmp_path)
    else:
        middle = _Middle(ghostly=((_phase(L - 1), _phase(1)),))
    report = _cut(_keyed(tmp_path, "E", (0,), phase=EVEN), tmp_path / "E", interpolate=middle)  # before: refused
    out = tmp_path / "E"
    assert [(g["frame"], g["taken"]) for g in report["ghost_given_way"]] == [(0, {"source": 1})]
    assert _cycle(out, 0).tobytes() == _cycle(out, 1).tobytes()
    _delivered_clean(out)
    assert report["seam_as_filmed"] == [0] and report["seam_measurement"] == "source-frames"
    assert report["resampled_seam_ratio"] == filmed["resampled_seam_ratio"] < loop_mod.SEAM_RATIO_MAX
    assert report["gif"]["n_frames"] == report["webp"]["n_frames"] == L - 1


def test_a_ghost_at_the_cuts_last_frame_given_way_to_its_first_leaves_the_seam_the_cut_closes_with(tmp_path, monkeypatch):
    """The ghost at the cut's last frame given way to frame 0, the cut's first shown again at the wrap: read on the cells,
    the wrap was no step at all (0.0), whatever the cut. Read as filmed, it is the seam the cut closes with."""
    filmed = _cut(_keyed(tmp_path, "filmed", (L - 1,), phase=EVEN), tmp_path / "filmed", repair="off")
    _no_rife(monkeypatch, tmp_path)
    report = _cut(_keyed(tmp_path, "E", (L - 1,), phase=EVEN), tmp_path / "E")
    out = tmp_path / "E"
    assert report["resampled_seam_ratio"] == filmed["resampled_seam_ratio"] > 1  # before: 0.0
    assert [(g["frame"], g["taken"]) for g in report["ghost_given_way"]] == [(L - 1, {"source": 0})]
    assert _cycle(out, L - 1).tobytes() == _cycle(out, 0).tobytes()
    assert report["seam_as_filmed"] == [L - 1] and report["seam_measurement"] == "source-frames"


def test_ghosts_at_both_ends_of_the_cut_given_way_inward_leave_the_seam_the_cut_closes_with(tmp_path, monkeypatch):
    """Filmed ghosts at the cut's first and last frames at once: across the wrap each has the other beside it, so no
    frame is made between its neighbours — frame 0 gives way to the frame after it, the last frame to the one before
    it — and the GIF and WebP hold two runs of two. On the cells as they play the wrap was three steps (2.40, refused).
    Read as filmed, it is the seam `--repair off` reads."""
    filmed = _cut(_keyed(tmp_path, "filmed", (0, L - 1), phase=EVEN), tmp_path / "filmed", repair="off")
    _no_rife(monkeypatch, tmp_path)
    report = _cut(_keyed(tmp_path, "E", (0, L - 1), phase=EVEN), tmp_path / "E")  # before: refused
    out = tmp_path / "E"
    assert [(g["frame"], g["taken"]) for g in report["ghost_given_way"]] == [(0, {"source": 1}), (L - 1, {"source": L - 2})]
    assert _cycle(out, 0).tobytes() == _cycle(out, 1).tobytes() and _cycle(out, L - 1).tobytes() == _cycle(out, L - 2).tobytes()
    _delivered_clean(out)
    assert report["seam_as_filmed"] == [0, L - 1] and report["seam_measurement"] == "source-frames"
    assert report["resampled_seam_ratio"] == filmed["resampled_seam_ratio"] < loop_mod.SEAM_RATIO_MAX
    assert report["gif"]["n_frames"] == report["webp"]["n_frames"] == L - 2


SHORT = 14  # a cut six frames short of the walk's cycle: its wrap is six steps and does not close


@pytest.mark.parametrize("anchor", ["none", "body"])
def test_a_cut_that_does_not_close_is_refused_though_its_last_frame_gives_way_to_its_first(tmp_path, monkeypatch, anchor):
    """A cut 14 frames long of a walk 20 frames a cycle does not close (`--repair off` refuses it); its last frame a
    filmed ghost given way to its first, the wrap read on the cells was a step of nothing and the cut was delivered,
    under `--anchor body` (the walk's default) as under `none`. Read as filmed, it is refused as it is filmed."""
    with pytest.raises(SystemExit, match="the cycle does not close"):
        _cut(_keyed(tmp_path, "filmed", (SHORT - 1,), phase=EVEN), tmp_path / "filmed", length=SHORT, anchor=anchor,
             repair="off")
    filmed = json.loads((tmp_path / "filmed" / "walk.loop.report.json").read_text())
    _no_rife(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="the cycle does not close"):  # before: delivered, the seam read 0.0
        _cut(_keyed(tmp_path, "E", (SHORT - 1,), phase=EVEN), tmp_path / "E", length=SHORT, anchor=anchor)
    report = json.loads((tmp_path / "E" / "walk.loop.report.json").read_text())
    assert [(g["frame"], g["taken"]) for g in report["ghost_given_way"]] == [(SHORT - 1, {"source": 0})]
    assert report["seam_as_filmed"] == [SHORT - 1]
    assert report["resampled_seam_ratio"] == filmed["resampled_seam_ratio"] > loop_mod.SEAM_RATIO_MAX


@pytest.mark.parametrize("ghost", [0, L - 1])
def test_a_cut_with_a_frame_made_reads_its_cell_given_way_as_filmed_on_the_cells(tmp_path, ghost):
    """Frame 9's filmed ghost is given RIFE's frame, so the gate reads the rendered cells; the ghost at the cut's first
    (or last) frame, RIFE's frame for it a ghost too, is given way to the frame after it. On the cells as they play the
    wrap read two steps at the first frame (2.17, refused) and none at the last (0.0). The gate reads the strip the cut
    makes with that frame as filmed: near the cut with frame 9 alone given way, and under the gate."""
    reference = _cut(_keyed(tmp_path, "ref", (9,), phase=EVEN), tmp_path / "ref", interpolate=_Middle())
    middle = _Middle(ghostly=((_phase(ghost - 1), _phase(ghost + 1)),))
    report = _cut(_keyed(tmp_path, "E", (ghost, 9), phase=EVEN), tmp_path / "E", interpolate=middle)  # before: refused, or 0.0
    assert 1 < report["resampled_seam_ratio"] < loop_mod.SEAM_RATIO_MAX
    assert abs(report["resampled_seam_ratio"] - reference["resampled_seam_ratio"]) < 0.5
    assert {r["target"]: r["taken"] for r in _ghost_rounds(report)} == {9: "rife", ghost: {"source": (ghost + 1) % L}}
    assert report["seam_measurement"] == reference["seam_measurement"] == "rendered-cells"
    assert report["seam_as_filmed"] == [ghost] and "seam_as_filmed" not in reference
    _delivered_clean(tmp_path / "E")


@pytest.mark.parametrize("ghosts", [(0,), (L - 1,), (0, L - 1)], ids=["first", "last", "both"])
def test_anchor_body_reads_a_cell_given_way_as_filmed_in_the_wrap_after_its_ramp(tmp_path, monkeypatch, ghosts):
    """`--anchor body`, the walk's default, ramps the cut so its last frame returns to its first and records the wrap
    as it then plays in the strip metadata (`seam_ratio_after_anchor`: the last frame against the first, over the mean
    step). Read on `cycle/`, a ghost at the cut's last frame given way to its first was no step at all (0.0, as the
    gate read it too), and one at the first frame given way to the second two steps (the gate refused it, 2.17; 2.40
    with both ends). The gate and the wrap after the ramp read a cell given way as filmed: what `--repair off` reads."""
    filmed = _cut(_keyed(tmp_path, "filmed", ghosts, phase=EVEN), tmp_path / "filmed", anchor="body", repair="off")
    _no_rife(monkeypatch, tmp_path)
    report = _cut(_keyed(tmp_path, "E", ghosts, phase=EVEN), tmp_path / "E", anchor="body")  # before: refused, or 0.0

    def seams(r: dict) -> tuple[float, float]:
        return r["resampled_seam_ratio"], r["strip"]["seam_ratio_after_anchor"]

    assert seams(report) == seams(filmed)
    assert filmed["resampled_seam_ratio"] < loop_mod.SEAM_RATIO_MAX and filmed["strip"]["seam_ratio_after_anchor"] > 0.5
    assert [g["frame"] for g in report["ghost_given_way"]] == sorted(ghosts)
    assert report["seam_as_filmed"] == sorted(ghosts) and report["seam_measurement"] == "source-frames"
    _delivered_clean(tmp_path / "E")


# --- the application's walk: motion anchoring, size hold, body height ------------------------------


def _strider_keyed(root: Path) -> Path:
    """The side walker of tests/video/test_one_step_cut.py (cycle 42 over 73 frames), a small band at phase 20 of every cycle."""
    from tests.video import test_one_step_cut as ts
    keyed = root / "keyed"
    keyed.mkdir(parents=True)
    for k in range(ts.FRAMES):
        im = ts._strider(k)
        (tg._band(im, box=(0.30, 0.68, 0.42, 0.74)) if k % ts.CYCLE == 20 else im).save(keyed / f"{k:03}.png")
    return keyed


def _app_cut(root: Path, interpolate) -> dict:
    """`video-loop` with the application's walk flags: the cycle searched, `--anchor motion-auto --size-hold auto
    --body-height 160 --repair auto`."""
    return loop_mod.run_loop(_strider_keyed(root), root / "E", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None,
                             seam_max=loop_mod.SEAM_RATIO_MAX, name="walk", report_path=None, anchor="motion-auto",
                             repair="auto", interpolate=interpolate, body_height=160, size_hold="auto")


def _app_ghost_given_way(report: dict, out: Path) -> None:
    ghosts = [g["frame"] for g in report["ghost_screen"]["ghosts"]]
    assert ghosts, "the cut holds the filmed ghost"
    rounds = _ghost_rounds(report)
    assert [r["target"] for r in rounds] == ghosts and all(r["taken"] is not None for r in rounds)
    assert set(ghosts) <= set(report["jump_repair"]["replaced"])
    _delivered_clean(out)


def test_the_applications_walk_gives_a_small_filmed_ghost_way(tmp_path):
    """B1b with a stand-in RIFE that takes the frame after: the band, 13 times the limit, is no jump, so before it was
    delivered in the strip and the WebP of a walk cut with the application's flags."""
    report = _app_cut(tmp_path, tg._follow)
    _app_ghost_given_way(report, tmp_path / "E")


@pytest.mark.real_rife
def test_real_rife_the_applications_walk_gives_a_small_filmed_ghost_way(tmp_path):
    """B1b through the real RIFE, located as `video-loop` locates it."""
    report = _app_cut(tmp_path, None)
    assert report["jump_repair"]["interpolator"]["kind"] == "rife-ncnn-vulkan"
    _app_ghost_given_way(report, tmp_path / "E")


# --- the source projection and the restoration ------------------------------------------------------


def test_a_cell_given_way_is_a_repair_hint_and_the_restoration_does_not_bring_the_ghost_back(tmp_path, monkeypatch):
    """The cut's source projection proves every other cell exactly and lists the cell given way among the jump
    repair's (`repair_hints`); a restoration request on the loop reads its playback, the GIF and WebP holding the
    frame shown twice as one, proposes nothing there (its source frame is the filmed ghost), and the comparison
    names the ghost the cut read."""
    if not shutil.which("ffmpeg") or not loop_mod.img2webp_supports_exact():
        pytest.skip("ffmpeg and img2webp >=1.5 required")
    _no_rife(monkeypatch, tmp_path)
    keyed = _keyed(tmp_path, "E", (10,))
    report = _cut(keyed, tmp_path / "E")
    assert report["jump_repair"]["replaced"] == [10]  # before: [] — the ghost delivered, no hint
    files = sorted(keyed.glob("*.png"))
    w, h = Image.open(files[0]).size
    frames_report = tmp_path / "frames.json"
    frames_report.write_text(json.dumps({"kind": "sprite-gen-video-frames-report", "fps": 24.0, "frames": len(files),
                                         "width": w, "height": h, "stream_index": 0, "key": "green"}))
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-framerate", "24", "-i", str(keyed / "%04d.png"), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    manifest = tmp_path / "source.json"
    manifest.write_text(json.dumps(source.manifest(clip=clip, canvas=files[0], frames_report=frames_report, files=files,
                                                   timestamps=source.timestamps(clip, 0))))
    inputs = dict(source_manifest=manifest, source_frames_dir=keyed, source_clip=clip, source_canvas=files[0],
                  source_frames_report=frames_report)
    out = tmp_path / "E"
    paths = {"strip": out / "walk.strip.png", "meta": out / "walk.strip.json", "report": out / "walk.loop.report.json",
             "gif": out / "walk.gif", "webp": out / "walk.webp"}
    projection, issue = source.project(Loop.read(paths["strip"], paths["meta"], paths["report"]), source.Source.read(**inputs))
    assert issue is None and projection.record["repair_hints"] == [10]
    assert projection.record["unmodified_cells"] == [k for k in range(L) if k != 10]
    result = restoration.restore(paths, paths, source.Source.read(**inputs), out_dir=tmp_path / "candidate", name="walk")
    assert (result["status"], result["reasons"], result["proposals_available"]) == ("no_change", [], 0)
    comparison = result["comparison"]
    assert comparison["verdict"] == "non_regressing", comparison["reasons"]
    assert [g["frame"] for g in comparison["axes"]["ghost"]["filmed"]["ghosts"]] == [10]
    gif = comparison["playback"]["baseline"]["gif"]
    assert gif["frames"] == L - 1 and gif["durations_ms"][10] == 2 * 42 // 10 * 10 and gif["strip_indices"] == list(range(L))
