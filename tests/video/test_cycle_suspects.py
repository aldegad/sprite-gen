# SPDX-License-Identifier: Apache-2.0
"""Two cycles in one loop are suspected, never cut on the pixels' word (docs/video-pipeline.md,
"The fundamental period"; docs/loop-repair.md section 4).

`video-loop` cuts the period it always cut and records a half or a third of it that no pose
refused (`cycle.fundamental`). `video-cycle-align` screens each loop the same way, stops a set on a
suspect before anything is rewritten, and changes the number of cycles in a loop only when told it
(`--cycles <loop>=k`). The figures are drawn here: a walker whose far leg is shaded `far` against
the near leg's 200, a runner whose rear foot kicks up by `lift` px at the back of its stride, a
legless bounce, and a far arm shaded `arm`. The screen's rule (`period.verdict`): a half or a
third that repeats, no shorter than the gait floor, is a suspect; the pose and the legs are
evidence for whoever looks, never a reason to refuse."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageEnhance

from sprite_gen.video import align, batch, legs, local_cycle
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import period as period_mod

W = H = 192
FPS = 24.0


def _figure(phase: float, *, fam: str = "shade", far: int = 110, lift: float = 0.0, arm: int = 0) -> Image.Image:
    """One frame at `phase` (radians, one stride = 2 pi), seen from the side. Half a stride on the
    legs are back in the same places with their shades swapped; the body bobs and the head sways
    twice a stride, a quarter apart, so no two frames in a row are the same picture."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if fam == "bounce":
        squash, lean = 10 * math.cos(phase), 8 * math.sin(phase)
        d.ellipse((60 - squash + lean, 70 + squash, 132 + squash + lean, 170), fill=(90, 160, 220, 255))
        return im
    amp, half_width = (16, 12) if fam == "shade" else (20, 8)
    bob, sway = 4 * math.cos(2 * phase), 5 * math.sin(2 * phase)
    for sign, shade in ((-1, far), (1, 200)):
        s = sign * math.sin(phase)
        x = 96 + amp * s
        kick = max(0.0, -s - 0.6) / 0.4 * lift if lift else 0.0  # the rear foot up at the back of the stride
        d.rectangle((x - half_width, 110 + bob, x + half_width, 170 - kick), fill=(shade, shade, shade, 255))
    d.rectangle((66, 60 + bob, 126, 120 + bob), fill=(200, 80, 80, 255))
    d.rectangle((76 + sway, 24 + bob, 116 + sway, 60 + bob), fill=(230, 200, 170, 255))
    if arm:  # arms swing against the legs, the far one shaded `arm`
        for sign, shade in ((1, arm), (-1, 230)):
            ax = 96 + 14 * sign * math.sin(phase)
            d.rectangle((ax - 5, 66 + bob, ax + 5, 104 + bob), fill=(shade, shade, shade, 255))
    return im


def _clip(root: Path, n: int, stride: int, *, twos: bool = False, **kw) -> list[Path]:
    """`n` frames of a motion whose cycle is `stride` frames. On twos a new drawing comes every other
    frame and the frame between moves a fifth of the way on: an odd `stride` then shows each cycle
    half a drawing off the one before, and the profile dips deeper two cycles on than one."""
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for j in range(n):
        t = (j - j % 2) + 0.4 * (j % 2) if twos else j
        files.append(root / f"frame-{j:04d}.png")
        _figure(2 * math.pi * t / stride, **kw).save(files[-1])
    return files


def _frames(files: list[Path]) -> list[Image.Image]:
    return [Image.open(f).convert("RGBA") for f in files]


def _detect(files: list[Path], state: str = "walk") -> dict:
    profile = loop_mod.profile_for(state)
    lo, hi = profile.window(len(files), FPS)
    return loop_mod.detect_cycle(loop_mod.distance_matrix(files), min_len=lo, max_len=hi, gait_floor=round(profile.min_seconds * FPS),
                                 signals=legs.strike_signals(_frames(files)))


def _ring(stride: int, cycles: int, *, twos: bool = False, drift: tuple[str, float] | None = None, **kw) -> list[Image.Image]:
    """A loop cut at exactly `cycles` strides, from frame 5 of a filmed clip. `drift` draws every
    cycle after the first again, a little differently, as a generated clip does: ("shift", px) or
    ("shift_y", px) moved, ("bright", %) lighter, ("lift", px) the rear foot kicked higher, ("phase",
    frames) late."""
    frames = []
    for j in range(5, 5 + cycles * stride):
        t = (j - j % 2) + 0.4 * (j % 2) if twos else j
        again = drift is not None and j >= 5 + stride
        kind, size = drift if again else ("", 0)
        if kind == "phase":
            t -= size
        im = _figure(2 * math.pi * t / stride, **({**kw, "lift": kw.get("lift", 0) + size} if kind == "lift" else kw))
        if kind == "shift":
            im = ImageChops.offset(im, int(size), 0)
        elif kind == "shift_y":
            im = ImageChops.offset(im, 0, int(size))
        elif kind == "bright":
            im = ImageEnhance.Brightness(im).enhance(1 + size / 100)
        frames.append(im)
    return frames


# --- video-loop: the period it always cut, the shorter one recorded ---------------------------

def test_a_walk_filmed_through_two_cycles_is_cut_as_before_and_its_half_recorded_as_a_suspect(tmp_path):
    """Stride 15 on twos: the frame 15 on is half a drawing off, the frame 30 on the same drawing, so
    the deepest repeat is 30. It is cut there, as 2.25 cut it; the 15 is recorded as a suspect — two
    cycles, the pose returning on the motion's path — with the legs' reading beside it."""
    cycle = _detect(_clip(tmp_path / "keyed", 73, 15, twos=True))
    assert cycle["period_global"] == 30 and cycle["review_recommended"] is False
    found = cycle["fundamental"]
    assert found["period"] == 30 and found["legs"]["by"] == "stride"
    assert [(row["period"], row["cycles"], row["verdict"]) for row in found["suspects"]] == [(15, 2, "suspect")]
    row = found["suspects"][0]
    assert row["pose"] < 0.5 and "steps" in row and "follow" in row


# The counter-examples of the two rejected rounds: one stride each, the half one step with the
# legs swapped. 2.25 cut each at its stride; so does this one. The half repeats, so it is recorded
# as a suspect — its pose, another pose, beside it for whoever looks — and nothing is cut.
COUNTER = [
    ("run", 18, dict(far=170)),  # the far leg shaded nearly like the near one
    ("run", 16, dict(fam="kick", lift=20)),  # a heel kick: the stride swings twice a step
    ("run", 20, dict(fam="kick", lift=20)),
    ("run", 16, dict(fam="kick", lift=20, twos=True)),
]


@pytest.mark.parametrize("state, stride, kw", COUNTER)
def test_a_half_stride_with_the_legs_swapped_is_never_cut(tmp_path, state, stride, kw):
    keyed = tmp_path / "keyed"
    _clip(keyed, 73, stride, **kw)
    rep = loop_mod.run_loop(keyed, tmp_path / "out", fps=FPS, state=state, min_len=None, max_len=None, n_out=None, seam_max=2.0,
                            name=state, report_path=None, anchor="none", repair="off")
    cycle = rep["cycle"]
    assert cycle["period_global"] == stride and cycle["length"] == stride
    half = [row for row in cycle["fundamental"]["checked"] if row["divisor"] == 2]
    assert half and half[0]["verdict"] == "suspect" and half[0]["pose"] >= 0.8 and half[0]["period"] in (stride // 2, stride // 2 + 1)


# A representative slice of the design grid (A shade, C heel kick, X arms), each one stride long.
# The whole grid — 442 clips — is the release check in docs/loop-repair.md section 4.
SLICE = [("run", 16, dict(far=197)), ("run", 20, dict(far=200, twos=True)), ("walk", 31, dict(far=190)),
         ("run", 18, dict(fam="kick", far=190, lift=30, twos=True)), ("walk", 32, dict(fam="kick", far=140, lift=10)),
         ("run", 18, dict(far=200, arm=150)), ("walk", 30, dict(fam="kick", far=197, lift=20, arm=150, twos=True))]


@pytest.mark.parametrize("state, stride, kw", SLICE)
def test_video_loop_cuts_the_period_the_deepest_repeat_gives(tmp_path, state, stride, kw):
    """Nothing is cut shorter than the period the profile gives (2.25's rule): whatever the screen
    finds at a half or a third goes in the record only."""
    files = _clip(tmp_path / "keyed", 73, stride, **kw)
    profile = loop_mod.profile_for(state)
    lo, hi = profile.window(len(files), FPS)
    D = loop_mod.distance_matrix(files)
    floor = round(profile.min_seconds * FPS)
    with_legs = loop_mod.detect_cycle(D, min_len=lo, max_len=hi, gait_floor=floor, signals=legs.strike_signals(_frames(files)))
    without = loop_mod.detect_cycle(D, min_len=lo, max_len=hi, gait_floor=floor)
    assert {k: with_legs[k] for k in ("period_global", "length", "start", "review_recommended")} == \
        {k: without[k] for k in ("period_global", "length", "start", "review_recommended")}
    assert all(row["cycles"] * row["period"] in range(with_legs["period_global"] - 3, with_legs["period_global"] + 3)
               for row in with_legs["fundamental"]["suspects"])


def test_the_local_repeat_search_records_the_screen_too(tmp_path):
    """`--anchor motion-auto` reads a local profile (sprite_gen/video/local_cycle.py): the same
    record, nothing cut shorter."""
    files = _clip(tmp_path / "keyed", 73, 15, twos=True)
    D = loop_mod.distance_matrix(files)
    lo, hi = loop_mod.profile_for("walk").window(len(files), FPS)
    kw = dict(min_len=lo, max_len=hi, gait_floor=14, periodicity_min=loop_mod.PERIODICITY_MIN,
              double_tolerance=loop_mod.GAIT_DOUBLE_TOL, double_search=loop_mod.GAIT_DOUBLE_SEARCH)
    cycle = local_cycle.detect(D, np.zeros(len(files)), signals=legs.strike_signals(_frames(files)), **kw)
    plain = local_cycle.detect(D, np.zeros(len(files)), **kw)
    assert {k: cycle[k] for k in ("start", "length", "period_local")} == {k: plain[k] for k in ("start", "length", "period_local")}
    assert cycle["fundamental"]["period"] == cycle["period_local"] and "suspects" in cycle["fundamental"]


def test_video_loop_writes_the_state_and_the_record(tmp_path):
    keyed = tmp_path / "keyed"
    _clip(keyed, 73, 15, twos=True)
    rep = loop_mod.run_loop(keyed, tmp_path / "out", fps=FPS, state="walk", min_len=None, max_len=None, n_out=None, seam_max=2.0,
                            name="walk", report_path=None, anchor="none", repair="off")
    saved = json.loads(Path(rep["report"]).read_text())["cycle"]["fundamental"]
    assert saved["period"] == rep["cycle"]["period_global"] == 30 and saved["suspects"][0]["period"] == 15
    meta = json.loads((tmp_path / "out" / "walk.strip.json").read_text())
    assert meta["state"] == "walk" and meta["cycle_frames"] == 30


# --- the screen ----------------------------------------------------------------------------

TWO = [("walk", 15, dict(twos=True)), ("walk", 17, dict(twos=True, far=200)), ("run", 13, {}), ("run", 15, dict(fam="kick", lift=20, far=170)),
       ("run", 16, dict(far=110)), ("run", 20, dict(fam="kick", lift=30, far=110, twos=True)), ("walk", 30, dict(far=200, arm=150)),
       ("walk", 15, dict(fam="bounce", twos=True))]


@pytest.mark.parametrize("state, stride, kw", TWO)
def test_a_loop_of_two_cycles_is_a_suspect(state, stride, kw):
    """However the legs are drawn: the true cycle at half the loop is a pose on the motion's path,
    which the screen never refuses."""
    screen = align.cycle_screen(_ring(stride, 2, **kw), fps=FPS, state=state)
    assert [row["cycles"] for row in screen["suspects"]] == [2] and screen["suspects"][0]["period"] == stride
    assert screen["suspects"][0]["seconds"] == round(stride / FPS, 3)


# Two cycles whose second is drawn again a little differently (a generated clip redraws it): moved,
# lighter, kicking higher, late. A rule that refused "another pose" missed most of these — in pixels
# a redrawn second cycle is off the motion's path just as a swapped step is — so the screen refuses
# nothing it finds repeating.
DRIFT = [pytest.param(state, stride, kw, drift, id=f"{state}{stride}-{drift[0]}{drift[1]:g}")
         for state, stride, kw, drift in (
             ("run", 18, dict(far=110), ("shift", 1)),
             ("run", 18, dict(far=110), ("shift", 3)),
             ("run", 18, dict(far=110), ("bright", 5)),
             ("run", 18, dict(far=110), ("shift_y", 2)),
             ("run", 18, dict(far=110), ("lift", 5)),
             ("run", 18, dict(far=110), ("phase", 0.2)),
             ("run", 16, dict(fam="kick", far=110, lift=20), ("shift", 1)),
             ("run", 16, dict(fam="kick", far=110, lift=20), ("bright", 10)),
             ("run", 16, dict(fam="kick", far=110, lift=20), ("shift_y", 4)),
             ("walk", 30, dict(far=140), ("lift", 5)),
             ("walk", 30, dict(far=140), ("shift_y", 4)),
             ("walk", 30, dict(far=140), ("bright", 20)),
             ("walk", 30, dict(far=140), ("phase", 0.5)))]


@pytest.mark.parametrize("state, stride, kw, drift", DRIFT)
def test_a_loop_of_two_cycles_drawn_again_is_a_suspect(state, stride, kw, drift):
    screen = align.cycle_screen(_ring(stride, 2, drift=drift, **kw), fps=FPS, state=state)
    assert [row["cycles"] for row in screen["suspects"]] == [2], screen["checked"]


@pytest.mark.parametrize("state, stride, kw", COUNTER)
def test_a_stride_whose_half_repeats_is_a_suspect_with_its_pose_as_evidence(state, stride, kw):
    """One stride whose half is a step with the legs swapped: it repeats, so it is suspected (a
    false alarm costs one look); its pose, another pose, is in the record for that look."""
    screen = align.cycle_screen(_ring(stride, 1, **kw), fps=FPS, state=state)
    [row] = screen["suspects"]
    assert row["cycles"] == 2 and row["pose"] >= 0.8 and row["periodicity"] >= loop_mod.PERIODICITY_MIN


def test_two_steps_under_the_gait_floor_are_not_a_suspect():
    """A loop the half-period guard doubled (two look-alike bounces of 12 frames, under the walk's
    floor) holds one stride, as `video-loop` cut it."""
    screen = align.cycle_screen(_ring(12, 2, fam="bounce"), fps=FPS, state="walk")
    assert screen["suspects"] == [] and "gait floor" in screen["checked"][0]["why"]


def test_a_loop_too_short_to_halve_is_not_screened():
    screen = align.cycle_screen(_ring(6, 1), fps=FPS, state="walk")
    assert screen["checked"] == [] and screen["suspects"] == []


def test_the_screen_decides_by_the_repeat_alone():
    """No verdict says "a cycle" or "another pose": a candidate is a suspect or not a repeat, and
    the pose and the legs do not move it."""
    frames = _ring(15, 2, twos=True) + _ring(16, 1, fam="kick", lift=20)
    D = align._ring(frames)
    for lag in range(4, len(frames) - 4):
        row = {"periodicity": 1.0, "cycles": 2, **period_mod.measure(D, lag, legs={"by": None}, cyclic=True)}
        assert period_mod.verdict(row, periodicity_min=loop_mod.PERIODICITY_MIN)[0] == "suspect"
        assert period_mod.verdict({**row, "pose": 9.0}, periodicity_min=loop_mod.PERIODICITY_MIN)[0] == "suspect"
        assert period_mod.verdict({**row, "periodicity": 0.1}, periodicity_min=loop_mod.PERIODICITY_MIN)[0] is None


def test_off_path_is_zero_between_two_frames_and_large_for_another_pose(tmp_path):
    D = loop_mod.distance_matrix(_clip(tmp_path / "twos", 73, 15, twos=True))
    assert period_mod.off_path(D, 15) < 0.2 and period_mod.off_path(D, 30) < 0.05
    D = loop_mod.distance_matrix(_clip(tmp_path / "ones", 73, 30))
    assert period_mod.off_path(D, 15) > 1.0 and period_mod.off_path(D, 30) < 0.05
    assert period_mod.off_path(D, 71) is None  # no frame has room for the lag and the spans around it


def test_steps_read_two_steps_and_one():
    """For the record: a walk's stride opens and closes once a step (twice a stride of 20 frames)."""
    stride = [abs(math.sin(2 * math.pi * t / 20)) for t in range(80)]
    assert period_mod.steps(stride, 20)[0] < 0.1 and period_mod.steps(stride, 10)[0] > 1.0
    assert period_mod.steps(stride, 20, cyclic=True, js=np.arange(20))[0] < 0.1
    assert period_mod.steps([1.0] * 40, 20) is None


# --- video-cycle-align --------------------------------------------------------------------

class _Blend:
    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        return Image.blend(a, b, t)


def _cut(root: Path, name: str, stride: int, length: int, state: str = "walk", **kw) -> Path:
    """A loop of `length` frames cut from a walk (or `state`) whose stride is `stride` frames."""
    keyed = root / f"{name}-keyed"
    _clip(keyed, length + 6, stride, **kw)
    out = root / name
    loop_mod.run_loop(keyed, out, fps=FPS, state=state, min_len=None, max_len=None, n_out=None, seam_max=1000.0,
                      name=state, report_path=None, cycle_mode="fixed", start=0, length=length, anchor="none", repair="off")
    return out


def _set(tmp_path: Path) -> list[Path]:
    """S and E one stride each; NE two strides of 15 (30 frames), as a two-cycle cut leaves it."""
    return [_cut(tmp_path, "S", 24, 24), _cut(tmp_path, "E", 24, 24), _cut(tmp_path, "NE", 15, 30, twos=True)]


def test_a_set_with_a_suspect_is_stopped_before_anything_is_rewritten(tmp_path):
    dirs = _set(tmp_path)
    before = [(d / "walk.strip.png").read_bytes() for d in dirs]
    with pytest.raises(align.CycleSuspects, match=r"NE: may hold 2 cycles of 15 frames \(0\.625 s\)") as stopped:
        align.align_set(dirs, interpolate=_Blend(), report_path=tmp_path / "align.json")
    message = str(stopped.value)
    command = " ".join(["sprite-gen video-cycle-align", *(f"--loop-dir {d}" for d in dirs), f"--report {tmp_path / 'align.json'}",
                        f"--cycles {dirs[2]}=<1|2>"])
    assert stopped.value.command == command and command in message and "--multi-cycle warn" in message
    assert "video-loop" not in message
    assert [(d / "walk.strip.png").read_bytes() for d in dirs] == before
    assert all(not (d / "cycle.json").exists() and "cycle_align" not in json.loads((d / "walk.strip.json").read_text()) for d in dirs)
    report = json.loads((tmp_path / "align.json").read_text())
    assert report["applied"] is False and report["refused"] == "cycle-suspects" and report["command"] == command
    [entry] = report["suspects"]
    assert entry["dir"] == str(dirs[2]) and entry["status"] == "stopped" and entry["length"] == 30
    assert [(c["period"], c["cycles"]) for c in entry["candidates"]] == [(15, 2)]
    assert entry["settle"] == [f"--cycles {dirs[2]}=1", f"--cycles {dirs[2]}=2"]
    assert stopped.value.suspects == report["suspects"]


def test_cycles_two_takes_one_cycle_out_of_the_loop_as_filmed(tmp_path):
    """`--cycles NE=2`: 15 frames out of NE's 30 — from where the frame 15 on is most like the first
    — and the set aligned to its median, 24. Run again, it reads the same 30 filmed frames."""
    dirs = _set(tmp_path)
    report = align.align_set(dirs, interpolate=_Blend(), cycles={"NE": 2})
    by = {Path(r["dir"]).name: r for r in report["loops"]}
    assert report["applied"] is True and report["lengths"] == [24, 24, 15] and report["length"] == 24
    assert by["NE"]["cycles_given"] == 2 and by["NE"]["from"] == 15 and by["NE"]["to"] == 24
    taken = by["NE"]["cycle_taken"]
    assert taken["from"] == 30 and taken["length"] == 15 and taken["exact"] is True and taken["repeat_over_step"] < 0.5
    assert report["suspects"][0]["status"] == "counted" and report["cycles_given"] == {str(dirs[2]): 2}
    assert len(list((dirs[2] / "cycle.source").glob("frame-*.png"))) == 30
    again = align.align_set(dirs, interpolate=_Blend(), cycles={str(dirs[2]): 2})
    assert {Path(r["dir"]).name: r["cycle_taken"]["start"] for r in again["loops"] if "cycle_taken" in r} == {"NE": taken["start"]}


def test_cycles_one_aligns_the_loop_as_it_is(tmp_path):
    dirs = _set(tmp_path)
    report = align.align_set(dirs, interpolate=_Blend(), cycles={"NE": 1})
    by = {Path(r["dir"]).name: r for r in report["loops"]}
    assert by["NE"]["from"] == 30 and "cycle_taken" not in by["NE"] and report["suspects"][0]["status"] == "counted"
    assert report["length"] == 24


def test_multi_cycle_warn_aligns_the_set_as_it_is_and_names_the_loop(tmp_path):
    dirs = _set(tmp_path)
    report = align.align_set(dirs, interpolate=_Blend(), multi_cycle="warn")
    assert [r["from"] for r in report["loops"]] == [24, 24, 30]
    assert any(w.startswith(f"{dirs[2]}: may hold 2 cycles") and "--multi-cycle warn" in w for w in report["warnings"])
    assert report["suspects"][0]["status"] == "warned"


def test_the_number_of_cycles_in_a_loop_changes_only_with_cycles(tmp_path, monkeypatch):
    """Every way of aligning a set but `--cycles k>1` resamples each loop from all of its filmed
    frames: take_cycle is never reached."""
    dirs = _set(tmp_path)
    plain = [_cut(tmp_path, "P1", 20, 20), _cut(tmp_path, "P2", 24, 24)]

    def never(*_a, **_k):
        raise AssertionError("a loop's cycles changed without --cycles")

    monkeypatch.setattr(align, "take_cycle", never)
    for kwargs in (dict(dirs=plain), dict(dirs=dirs, multi_cycle="warn"), dict(dirs=dirs, cycles={"NE": 1})):
        report = align.align_set(kwargs.pop("dirs"), interpolate=_Blend(), **kwargs)
        assert all(r["from"] == len(list((Path(r["dir"]) / "cycle.source").glob("frame-*.png"))) for r in report["loops"])
    with pytest.raises(align.CycleSuspects):
        align.align_set(dirs, interpolate=_Blend())
    with pytest.raises(AssertionError, match="without --cycles"):
        align.align_set(dirs, interpolate=_Blend(), cycles={"NE": 2})


@pytest.mark.parametrize("strides, odd, state, kw", [
    ((18, 18, 20), 0, "run", dict(far=170)),  # rejected round 1's set: one loop's far leg shaded like the near one
    ((16, 16, 18), 0, "run", dict(fam="kick", lift=20)),  # rejected round 2's set: one loop a heel kick
])
def test_the_counter_example_sets_stop_and_align_as_before_once_counted(tmp_path, strides, odd, state, kw):
    """A run's half repeats in every one of these loops: the set stops, naming each loop, its
    evidence and the command that settles it. Counted one cycle each, it is aligned as 2.25 aligned
    it, each loop resampled from its own cut."""
    dirs = [_cut(tmp_path, f"L{i}", s, s, state, **(kw if i == odd else {})) for i, s in enumerate(strides)]
    with pytest.raises(align.CycleSuspects) as stopped:
        align.align_set(dirs, interpolate=_Blend())
    assert [e["dir"] for e in stopped.value.suspects] == [str(d) for d in dirs]
    assert all(f"--cycles {d}=<1|2>" in str(stopped.value) and f"--loop-dir {d}" in stopped.value.command for d in dirs)
    assert all("pose" in e["candidates"][0] for e in stopped.value.suspects)
    report = align.align_set(dirs, interpolate=_Blend(), cycles={str(d): 1 for d in dirs})
    assert report["length"] == strides[1] and [r["from"] for r in report["loops"]] == list(strides)
    assert all(e["status"] == "counted" for e in report["suspects"]) and not any("may hold" in w for w in report["warnings"])


def test_cycles_names_one_loop_or_is_refused(tmp_path):
    dirs = [_cut(tmp_path / "a", "loop", 24, 24), _cut(tmp_path / "b", "loop", 20, 20)]
    with pytest.raises(SystemExit, match="names 2 of the --loop-dir"):
        align.align_set(dirs, interpolate=_Blend(), cycles={"loop": 2})
    with pytest.raises(SystemExit, match="names 0 of the --loop-dir"):
        align.align_set(dirs, interpolate=_Blend(), cycles={"NE": 2})
    with pytest.raises(SystemExit, match="holds 1, 2, 3 cycles"):
        align.align_set(dirs, interpolate=_Blend(), cycles={"a": 4})
    report = align.align_set(dirs, interpolate=_Blend(), cycles={"a": 1})  # a video-set item: its directory's parent
    assert report["cycles_given"] == {str(dirs[0]): 1}
    assert align.parse_cycles(["x/NE=2", "a=b=1"]) == {"x/NE": 2, "a=b": 1}
    with pytest.raises(SystemExit, match="LOOP=K"):
        align.parse_cycles(["NE"])


def test_the_command_takes_cycles(tmp_path, monkeypatch, capsys):
    dirs = _set(tmp_path)
    monkeypatch.setattr(align.rife_mod, "Rife", _Blend)
    _Blend.describe = lambda self: {"binary": "blend"}  # type: ignore[attr-defined]
    with pytest.raises(SystemExit, match="may hold 2 cycles"):
        align.main([*sum((["--loop-dir", str(d)] for d in dirs), []), "--between", "nearest"])
    assert align.main([*sum((["--loop-dir", str(d)] for d in dirs), []), "--between", "nearest", "--cycles", "NE=2"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["lengths"] == [24, 24, 15] and out["cycles_given"] == {str(dirs[2]): 2}


# --- video-set ----------------------------------------------------------------------------

def test_video_set_skips_a_suspect_state_with_a_warning(tmp_path, capsys):
    rows = []
    for direction, stride, length, twos in (("front", 24, 24, False), ("side", 24, 24, False), ("back", 15, 30, True)):
        item = tmp_path / f"{direction}-walk"
        item.mkdir()
        _cut(item, "loop", stride, length, twos=twos)
        rows.append({"item": f"{direction}-walk", "direction": direction, "state": "walk", "dir": str(item), "ok": True,
                     "loop": {"n_out": length}})
    out = batch.align_gaits(rows, tmp_path, "auto", interpolate=_Blend())
    walk = out["walk"]
    assert walk["ok"] is True and walk["applied"] is False and walk["reason"] == "cycle-suspects"
    assert "back-walk may hold more than one cycle" in walk["why"] and walk["suspects"][0]["dir"] == str(tmp_path / "back-walk" / "loop")
    assert [r["loop"]["n_out"] for r in rows] == [24, 24, 30]
    assert json.loads((tmp_path / "walk.cycle-align.json").read_text())["refused"] == "cycle-suspects"
    assert "cycles not aligned" in capsys.readouterr().err
