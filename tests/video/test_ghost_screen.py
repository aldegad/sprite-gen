# SPDX-License-Identifier: Apache-2.0
"""The ghost screen (docs/loop-repair.md section 4): a frame that carries a part-covered band at
least five pixels thick — the half-drawn frame a video model leaves between two held drawings, or a
frame RIFE made beside one — is read on its own coverage, not against the frames beside it, and no
loop path delivers one unmeasured.

The fixtures are synthetic: two outlined legs (tests/video/test_rife.py `_walker`) and, for a
filmed ghost, a band of 0.3 coverage between the feet, where a video model's half-drawn frame
between two held drawings carries one. The interpolators are stand-ins that follow the motion
(a whole, outlined frame); the one real-RIFE case runs where RIFE is installed."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

from conftest import as_pinned
from sprite_gen.video import align, rife
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import repair
from sprite_gen.video.interpolation_quality import GHOST_WARN, faults, ghost_screen
from tests.video.test_cycle_align import _walker as _block_walker
from tests.video.test_loop_repair import forced_flow_group
from tests.video.test_rife import _walker


def _band(im: Image.Image, *, alpha: float = 0.3, box=(0.36, 0.78, 0.64, 0.99)) -> Image.Image:
    """`im` with a filmed ghost: the empty pixels of `box` (fractions of the frame) part-covered at
    `alpha`, in the near leg's fill."""
    x = np.asarray(im.convert("RGBA")).copy()
    h, w = x.shape[:2]
    sub = x[int(h * box[1]):int(h * box[3]), int(w * box[0]):int(w * box[2])]
    sub[sub[..., 3] == 0] = (240, 225, 220, round(alpha * 255))
    return Image.fromarray(x, "RGBA")


def _follow(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
    """A stand-in RIFE whose flow followed the motion: the nearer frame, whole and outlined."""
    return (a if t < 0.5 else b).copy()


def _stride(n: int) -> list[Image.Image]:
    """n outlined frames of a stride, the legs closing from 18/-18 degrees."""
    return [_walker(18 - 3 * k, -18 + 3 * k) for k in range(n)]


def test_ghost_reads_a_thick_part_covered_band_not_an_antialiased_rim():
    """An antialiased edge or a thin soft strand erodes away; a band five pixels thick and more does not."""
    clean = _walker(12, -12)
    assert rife.ghost(clean) == 0
    alpha = clean.getchannel("A").filter(ImageFilter.GaussianBlur(1.2))
    soft = clean.copy()
    soft.putalpha(alpha)  # every edge part-covered over two or three pixels
    assert np.count_nonzero((np.asarray(alpha) > 25) & (np.asarray(alpha) < 230)) > 500
    assert rife.ghost(soft) <= GHOST_WARN / 5  # a little thicker only where two edges meet
    thin = _band(clean, box=(0.36, 0.95, 0.64, 0.97))  # a strand three rows high
    assert rife.ghost(thin) <= GHOST_WARN / 10
    assert rife.ghost(_band(clean)) > 5 * GHOST_WARN


def test_a_frame_made_beside_a_filmed_ghost_is_judged_on_its_own_reading():
    """Measured against its neighbours a frame that carries what a filmed ghost beside it carries
    has no fault (its part coverage beyond theirs is reported, not judged); on its own coverage it is
    a ghost."""
    a, b = _walker(18, -18), _band(_walker(15, -15))
    made = _band(_walker(16, -16))
    measure = rife.smear(made, a, b)
    assert faults(measure) == []
    assert faults({**measure, "ghost": rife.ghost(made)}) == ["ghost"]
    assert faults({**measure, "ghost": None}) == []  # a loop the screen does not read


def test_the_screen_names_each_filmed_ghost_and_does_not_judge_a_loop_drawn_part_covered():
    frames = _stride(6)
    frames[2] = _band(frames[2])
    screen = ghost_screen(frames)
    assert screen["reads"] and screen["limit"] == GHOST_WARN and screen["thick_px"] == rife.GHOST_THICK
    assert [g["frame"] for g in screen["ghosts"]] == [2] and screen["ghosts"][0]["ghost"] > GHOST_WARN
    cape = [_band(f) for f in _stride(6)]  # every frame drawn with it: the art, not a ghost
    worn = ghost_screen(cape)
    assert not worn["reads"] and worn["ghosts"] == [] and worn["level"] > GHOST_WARN and "part-covered" in worn["why"]


def test_auto_takes_the_clean_frame_beside_a_made_ghost_not_the_nearer_ghost():
    """A frame made next to a filmed ghost carries it (t past half way toward it): it is a ghost,
    and the nearer source frame is that ghost, so the other frame beside it is taken."""
    frames = _stride(4)
    frames[1] = _band(frames[1])
    cells: list[dict] = []
    out, facts = align.resample(frames, 6, _follow, cells_from=cells)  # times 0, .67, 1.33, 2, 2.67, 3.33
    assert [m["at"] for m in facts["smear"]] == [1, 2, 4, 5]
    by = {m["at"]: m for m in facts["smear"]}
    assert by[1]["faults"] == ["ghost"] and by[1]["method"] == "nearest" and by[1]["ghost"] > GHOST_WARN
    assert by[2]["faults"] == ["ghost"] and by[2]["method"] == "nearest"
    assert by[4]["faults"] == by[5]["faults"] == [] and by[4]["method"] == "rife"
    assert cells[:4] == [{"source": 0}, {"source": 0}, {"source": 2}, {"source": 2}]
    assert out[1] is frames[0] and out[2] is frames[2]
    assert [(g["at"], g["source"], g["taken"]) for g in facts["ghost_at"]] == [(1, 1, 0), (2, 1, 2)]
    assert [g["frame"] for g in facts["ghost_screen"]["ghosts"]] == [1]
    assert max(rife.ghost(f) for f in out) <= GHOST_WARN


def test_a_time_on_a_filmed_ghost_takes_the_clean_frame_beside_it_and_rife_keeps_it_named():
    frames = _stride(4)
    frames[1] = _band(frames[1])
    out, facts = align.resample(frames, 8, None, between="nearest")  # times 0, .5, 1, 1.5, …
    assert [frames.index(f) for f in out] == [0, 0, 2, 2, 2, 3, 3, 0]
    assert [(g["at"], g["source"], g["taken"]) for g in facts["ghost_at"]] == [(1, 1, 0), (2, 1, 2)]
    out, facts = align.resample(frames, 8, _follow)
    assert out[2] is frames[2] and max(rife.ghost(f) for f in out) <= GHOST_WARN
    assert (2, 1, 2) in [(g["at"], g["source"], g["taken"]) for g in facts["ghost_at"]]
    kept, facts = align.resample(frames, 8, _follow, between="rife")
    assert kept[2] is frames[1] and (2, 1, 1) in [(g["at"], g["source"], g["taken"]) for g in facts["ghost_at"]]
    assert next(m for m in facts["smear"] if m["at"] == 1)["faults"] == ["ghost"]  # kept under rife, named


def test_a_loop_drawn_part_covered_is_resampled_as_before():
    cape = [_band(f) for f in _stride(4)]
    out, facts = align.resample(cape, 8, _follow)
    assert not facts["ghost_screen"]["reads"] and facts["ghost_at"] == []
    assert all(m["ghost"] is None and m["faults"] == [] and m["method"] == "rife" for m in facts["smear"])
    assert all(out[2 * k] is cape[k] for k in range(4))


def _keyed(tmp_path: Path, name: str, length: int, ghosts: tuple[int, ...]) -> Path:
    """Keyed frames of a walk, its frames in `ghosts` (of the cycle starting at 0) filmed with a band."""
    keyed = tmp_path / f"{name}-keyed"
    keyed.mkdir()
    for k in range(length + 6):
        frame = _block_walker(2 * math.pi * k / length)
        (_band(frame, box=(0.25, 0.80, 0.75, 0.95)) if k % length in ghosts else frame).save(keyed / f"{k:04d}.png")
    return keyed


def _cut(keyed: Path, out: Path, length: int) -> dict:
    """`video-loop --cycle fixed --start 0`: the loop cut as filmed, its report."""
    return loop_mod.run_loop(keyed, out, fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=1000.0,
                             name="walk", report_path=None, cycle_mode="fixed", start=0, length=length, anchor="none", repair="off")


@pytest.mark.slow
def test_alignment_delivers_no_filmed_ghost_and_names_each_cell_it_passed_over(tmp_path):
    """Set length 22: S (20) lands on its ghost at a whole time and takes the frame after it. E (24)
    makes a frame next to its ghost, which carries it, and the nearer frame is that ghost: the frame
    after it would stand half a beat late (docs/loop-repair.md section 4, the beat), so the pair is
    made again at a fraction that keeps the beat and carries no ghost (this stand-in draws the nearer
    frame whole: from half way on, frame 6). E passes over no ghost."""
    dirs = []
    for name, length, ghost in (("S", 20, 10), ("E", 24, 5)):
        _cut(_keyed(tmp_path, name, length, (ghost,)), tmp_path / name, length)
        dirs.append(tmp_path / name)
    report = align.align_set(dirs, interpolate=_follow, report_path=tmp_path / "align.json")
    by = {Path(r["dir"]).name: r for r in report["loops"]}
    for name, ghost in (("S", 10), ("E", 5)):
        row = by[name]
        assert [g["frame"] for g in row["ghost_screen"]["ghosts"]] == [ghost]
        cells = [Image.open(p) for p in sorted((Path(row["dir"]) / "cycle").glob("frame-*.png"))]
        assert max(rife.ghost(c) for c in cells) <= GHOST_WARN
    s, e = by["S"], by["E"]
    assert [(g["source"], g["taken"]) for g in s["ghost_at"]] == [(10, 11)]
    assert s["cells_from"][s["ghost_at"][0]["at"]] == {"source": 11}
    (m,) = [m for m in e["smear"] if m["method"] == "retimed"]
    assert m["faults"] == ["ghost"] and m["retimed"]["faults"] == [] and m["retime"]["between"] == [5, 6]
    assert e["ghost_at"] == [] and e["cells_from"][m["at"]] == {"between": [5, 6], "t": m["retimed"]["t"]} and m["retimed"]["t"] >= 0.5
    named = [w for w in report["warnings"] if "is a filmed ghost" in w]
    assert len(named) == 1 and named[0].startswith(f"{s['dir']}: frame {s['ghost_at'][0]['at']}: source frame 10 ")


def test_jump_repair_rejects_a_proposal_that_carries_a_ghost(monkeypatch):
    """The proposal for the frame after a jump carries a band of part coverage: on its own coverage it
    is a ghost, and the original stays. (A filmed ghost beside it is no longer what the proposal is made
    from: the jump repair gives it way first, tests/video/test_ghost_given_way.py.)"""
    frames = [_walker(20, -20), _walker(8, -8), _walker(-5, 5), _walker(10, -10)]
    before = [f.tobytes() for f in frames]
    forced_flow_group(monkeypatch, [1], (0, 2), [0.5])
    out, record = repair.repair_jumps(frames, lambda a, b, t: _band(_walker(8, -8)))
    (round_,) = record["rounds"]
    assert record["replaced"] == [] and round_["why"] == "flow" and "ghost" in round_["faults"]
    assert round_["made"][0]["proposal"]["ghost"] > GHOST_WARN and round_["made"][0]["original"]["ghost"] == 0
    assert [f.tobytes() for f in out] == before


# The binary's calls of this resample, by model (`rife.model_name()`): rife-v4.25's frame made beside the filmed ghost
# carries a ghost of its own, so it is made again at other times and the nearer source frame taken.
SCREEN_CALLS = {"rife-v4.25": (8, 28), "rife-v4.6": 20}


@pytest.mark.slow
@pytest.mark.real_rife
@pytest.mark.timeout(900)  # 20 to 28 calls of the binary at 128 x 192: seconds on a GPU, a minute or two on a CPU's Vulkan driver
def test_real_rife_delivers_no_cell_carrying_a_filmed_ghost():
    """A filmed ghost resampled 4 to 8 through the real RIFE: before the screen the ghost itself was
    taken at its own time, unmeasured, and the frames made beside it carried it, judged only beyond it."""
    frames = [_walker(18, -18), _band(_walker(15, -15)), _walker(12, -12), _walker(9, -9)]
    interpolate = rife.Rife()
    out, facts = align.resample(frames, 8, interpolate)
    assert all(f is not frames[1] for f in out)
    assert max(rife.ghost(f) for f in out) <= GHOST_WARN
    assert [g["frame"] for g in facts["ghost_screen"]["ghosts"]] == [1]
    assert interpolate.model_name == rife.model_name() and as_pinned(interpolate.calls, SCREEN_CALLS), interpolate.calls
