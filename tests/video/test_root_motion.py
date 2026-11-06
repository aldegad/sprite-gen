# SPDX-License-Identifier: Apache-2.0
"""Root motion (sprite_gen/video/root_motion.py, docs/video-pipeline.md "Root motion"): a cut played once carries
where its body is in each cell (the mass centre), what each cell touches and its air, in the strip sidecar; with
`--air-retime` the air is given a length, a gravity and a speed of its own, its cells made between the clip's frames
(held_fill.at_times), the take-off and the landing as filmed and the contacts where they were. Without the handle the
cut is the same pictures as before (tests/picture_digest.py) and its sidecar only grows a key (and the record of its
cells, `cell_record`, as every cut's).

The jump is synthetic (tests/video/jump_inputs.py), its mass centre on a parabola under a known gravity; the
interpolator is the stand-in that draws it again at the time asked for (`Between`)."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.video import evidence
from sprite_gen.video import held_fill
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import nonloop
from sprite_gen.video import root_motion as rm
from sprite_gen.video import sway
from tests.video import jump_inputs as J
from tests.video.held_fill_inputs import Between
from tests.video.test_held_fill import UNHELD, _no_rife, _pictures


def _run(tmp_path: Path, frames: list[Image.Image], name: str = "j", **kw) -> dict:
    keyed = J.save(frames, tmp_path / f"{name}-keyed")
    return loop_mod.run_loop(keyed, tmp_path / f"{name}-out", fps=J.FPS, min_len=None, max_len=None, n_out=None,
                             seam_max=loop_mod.SEAM_RATIO_MAX, name=name, report_path=None, **kw)


def _meta(rep: dict) -> dict:
    return json.loads(Path(rep["strip"]["path"]).with_suffix("").with_suffix(".strip.json").read_text())


def _cells(rep: dict) -> list[Image.Image]:
    meta = _meta(rep)
    strip = Image.open(rep["strip"]["path"]).convert("RGBA")
    return [strip.crop((k * meta["w"], 0, (k + 1) * meta["w"], meta["h"])) for k in range(meta["frames"])]


def _read(frames: list[Image.Image], declared=()) -> dict:
    floor = max(nonloop.body_box(f)[3] for f in frames)
    roots = [rm.root(f) for f in frames]
    touch = rm.touching(frames, floor=floor, times=range(len(frames)), declared=declared)
    return {"roots": roots, "touch": touch, **rm.read(roots, touch, [k / J.FPS for k in range(len(frames))])}


def test_the_root_of_a_body_flying_under_gravity_is_read_on_its_parabola():
    frames = J.jump()
    got = _read(frames)
    height = max(b[3] - b[1] for b in map(nonloop.body_box, frames))
    worst = max(max(abs(got["roots"][k][0] - J.path(k - J.TAKEOFF)[0]), abs(got["roots"][k][1] - J.path(k - J.TAKEOFF)[1]))
                for k in range(J.TAKEOFF, J.LANDING + 1))
    assert worst <= 0.5 and worst / height < 0.05  # a whole pixel's placing, under half a pixel
    (air,) = got["air"]
    assert air["cells"] == [J.TAKEOFF + 1, J.LANDING - 1]
    assert (air["takeoff"], air["landing"]) == (J.TAKEOFF, J.LANDING)
    assert air["seconds"] == pytest.approx(J.AIR, abs=1e-4)
    assert air["apex_px"] == pytest.approx(J.APEX, abs=1.0)
    assert got["contacts"] == [{"by": "ground", "cells": [0, J.TAKEOFF]}, {"by": "ground", "cells": [J.LANDING, J.FRAMES - 1]}]


def test_a_contact_the_floor_does_not_show_is_named_over_the_clip_s_frames():
    frames = J.jump()
    declared = rm.parse_contacts(["hand=6:8"], len(frames))
    got = _read(frames, declared)
    assert {"by": "hand", "cells": [6, 8]} in got["contacts"]
    (air,) = got["air"]
    assert (air["takeoff"], air["cells"], air["landing"]) == (8, [9, 14], J.LANDING)
    with pytest.raises(ValueError, match="ground"):
        rm.parse_contacts(["ground=1:2"], 10)
    with pytest.raises(ValueError, match="numbered from 0"):
        rm.parse_contacts(["hand=4:40"], 10)


def test_the_retime_keys_are_read_and_a_wrong_one_is_refused():
    assert rm.parse_retime("gravity=9.81,air=0.6,speed=4") == {"gravity": 9.81, "air": 0.6, "speed": 4.0}
    assert rm.parse_retime("air=1.2") == {"air": 1.2}
    for bad in ("air", "air=0", "lift=2", "air=1,air=2", "gravity=-1", "speed=x"):
        with pytest.raises(ValueError):
            rm.parse_retime(bad)


def _retimed(**asked):
    frames = J.jump()
    between = Between(J.draw, range(J.FRAMES))
    floor = max(nonloop.body_box(f)[3] for f in frames)
    out, record, plan = rm.retime(frames, between, fps=J.FPS, rate=J.FPS, floor=floor, times=range(J.FRAMES), declared=(),
                                  asked=asked, meters_per_px=J.METERS_PER_PX)
    return frames, out, record, plan, between


def test_the_air_twice_as_long_rises_g_t2_over_8_its_cells_made_between_and_the_contacts_kept():
    frames, out, record, plan, between = _retimed(gravity=9.81, air=2 * J.AIR)
    assert record["applied"]
    (air,) = record["air"]
    cells = round(2 * J.AIR * J.FPS)
    assert len(out) == J.FRAMES + cells - (J.LANDING - J.TAKEOFF)  # the air holds twice the cells
    assert air["seconds"] == pytest.approx(2 * J.AIR, abs=1e-4) and air["gravity"] == pytest.approx(9.81, abs=1e-3)
    T = 2 * J.AIR
    assert air["apex_px"] == pytest.approx(J.GRAVITY * T * T / 8, abs=0.01)
    # read on the cells delivered: the air spans the new cells, rises g·t²/8 and lands where the parabola does
    ox, oy = plan["offset"]
    roots = [rm.root(f) for f in out]
    measured = rm.read(roots, plan["touch"], [k / J.FPS for k in range(len(out))])
    (got,) = measured["air"]
    assert got["cells"] == [J.TAKEOFF + 1, J.TAKEOFF + cells - 1] and got["seconds"] == pytest.approx(T, abs=1e-4)
    assert got["apex_px"] == pytest.approx(J.GRAVITY * T * T / 8, abs=1.0)
    # every air cell on the parabola, within a whole pixel's placing
    xa, ya = roots[J.TAKEOFF]
    for j in range(1, cells):
        t = j / J.FPS
        x = xa + 2 * (J.X_LANDING - J.X_TAKEOFF) * j / cells  # the clip's own speed, for twice as long
        y = ya - J.GRAVITY * t * (T - t) / 2
        assert abs(roots[J.TAKEOFF + j][0] - x) <= 0.5 and abs(roots[J.TAKEOFF + j][1] - y) <= 0.5
    # the poses: the clip's at the same share of its air, every other one made between two frames
    assert record["made"] == cells // 2 and record["shown_again"] == []
    assert sorted({round(c[2], 4) for c in between.calls}) == [0.5]
    # no picture shown twice in the air
    same = held_fill.same_steps(out)
    assert not any(same[J.TAKEOFF:J.TAKEOFF + cells])
    # the take-off, the cells before it and the landing block are the frames as filmed, moved whole
    moved = air["moved_along_px"]
    assert moved == 2 * (J.X_LANDING - J.X_TAKEOFF) - (J.X_LANDING - J.X_TAKEOFF)
    for k in range(J.TAKEOFF + 1):
        assert out[k].crop((ox, oy, ox + J.W, oy + J.H)).tobytes() == frames[k].tobytes()
    for k in range(J.LANDING, J.FRAMES):
        o = J.TAKEOFF + cells + (k - J.LANDING)
        assert out[o].crop((ox + moved, oy, ox + moved + J.W, oy + J.H)).tobytes() == frames[k].tobytes()
    # the ground cells stand on the floor, moved, and the air cells touch nothing
    floor = J.FLOOR + 1 + oy
    for k, f in enumerate(out):
        on = nonloop.on_floor(nonloop.body_box(f), floor)
        assert on == (not J.TAKEOFF < k < J.TAKEOFF + cells), k


def test_the_air_given_only_a_length_keeps_the_clip_s_own_gravity_and_speed():
    frames, out, record, plan, _ = _retimed(air=J.AIR)
    (air,) = record["air"]
    assert air["gravity_before_px"] == pytest.approx(J.GRAVITY, rel=0.02)
    assert air["seconds"] == pytest.approx(J.AIR, abs=1e-4) and air["moved_along_px"] == 0
    assert record["made"] == 0  # every cell a frame: nothing to make, no RIFE asked for


def test_a_cut_with_no_air_is_not_retimed_and_says_why():
    frames = [J.draw(0)] * 2 + J.jump()[:J.TAKEOFF + 1]
    floor = max(nonloop.body_box(f)[3] for f in frames)
    out, record, plan = rm.retime(frames, None, fps=J.FPS, rate=J.FPS, floor=floor, times=range(len(frames)),
                                  declared=(), asked={"air": 1.0}, meters_per_px=None)
    assert out is frames and plan is None and record["applied"] is False and record["why"] == "no air in the cut"


def test_video_loop_carries_the_root_motion_of_a_jump_and_retimes_its_air(tmp_path):
    rep = _run(tmp_path, J.jump(), "plain", state="jump", cycle_mode="once")
    meta = _meta(rep)
    root = meta["root_motion"]
    assert len(root["root"]) == meta["frames"]
    start = meta["source_cut"]["start"]
    (air,) = root["air"]
    assert (air["takeoff"], air["landing"]) == (J.TAKEOFF - start, J.LANDING - start)
    assert air["seconds"] == pytest.approx(J.AIR, abs=1e-3)
    assert air["apex_px"] == pytest.approx(J.APEX * meta["scale"], abs=1.0)
    assert "air_retime" not in meta and rep["root_motion"]["air"] == root["air"]
    between = Between(J.draw, range(J.FRAMES))
    rep = _run(tmp_path, J.jump(), "long", state="jump", cycle_mode="once", air_retime=f"gravity=9.81,air={2 * J.AIR}",
               meters_per_px=J.METERS_PER_PX, interpolate=between)
    meta = _meta(rep)
    record = meta["air_retime"]
    assert record["applied"] and record["made"] == 10 and meta["frames"] == rep["cycle"]["length"] + 10
    (air,) = meta["root_motion"]["air"]
    T = 2 * J.AIR
    assert air["seconds"] == pytest.approx(T, abs=1e-3)
    assert air["apex_m"] == pytest.approx(9.81 * T * T / 8, rel=0.03)
    assert "times" in meta["source_cut"] and "out_fps" not in meta
    # no picture shown twice in the air: the held runs left are the end pose the cut holds
    first, last = air["cells"]
    assert all(r["start"] > last or r["start"] + r["length"] <= first for r in meta["held_fill"]["held_runs"])
    assert record["poses"] == meta["held_fill"]["poses"] and record["cells"] == meta["frames"]


def test_an_air_whose_frames_between_show_a_frame_beside_them_again_names_those_cells(tmp_path, capsys):
    # An interpolator that gives back the frame before makes no air cell between two frames: each is refused (`held`),
    # tried again only later within half a cell, and is the nearer frame, named in the record and in a warning line.
    calls: list[float] = []

    def stuck(a, b, t):
        calls.append(t)
        return a

    rep = _run(tmp_path, J.jump(), "stuck", state="jump", cycle_mode="once", air_retime=f"air={2 * J.AIR}",
               meters_per_px=J.METERS_PER_PX, interpolate=stuck)
    record = _meta(rep)["air_retime"]
    assert record["applied"] and record["made"] == 0 and len(record["shown_again"]) == 10
    assert all(t["faults"] == ["held"] and t["t"] >= 0.5 for m in rep["air_retime"]["frames"] for t in m["tries"])
    assert len(calls) == 10 * 5  # 0.5, then 0.55 to 0.7: half a cell is a quarter of a frame
    cells = ", ".join(map(str, record["shown_again"]))
    assert f"10 cells of the retimed air ({cells}) show the frame beside them again" in capsys.readouterr().err

@pytest.mark.real_rife
@pytest.mark.timeout(900)  # 10 calls of the binary at about 210 x 250, each judged: seconds on a GPU, minutes on a CPU's Vulkan driver
def test_real_rife_makes_the_air_twice_as_long_with_no_frame_shown_again(tmp_path):
    """The same jump through rife-ncnn-vulkan located as a cut locates it: every between made and judged, none shown
    again, the air on its parabola."""
    rep = _run(tmp_path, J.jump(), "real", state="jump", cycle_mode="once", air_retime=f"air={2 * J.AIR}",
               meters_per_px=J.METERS_PER_PX)
    meta = _meta(rep)
    record = meta["air_retime"]
    assert record["applied"] and record["made"] == 10 and record["shown_again"] == []
    (air,) = meta["root_motion"]["air"]
    T = 2 * J.AIR
    assert air["seconds"] == pytest.approx(T, abs=1e-3)
    assert air["apex_m"] == pytest.approx(9.81 * T * T / 8, rel=0.03)
    first, last = air["cells"]
    assert all(r["start"] > last or r["start"] + r["length"] <= first for r in meta["held_fill"]["held_runs"])


def test_no_rife_leaves_the_air_as_filmed_and_says_which_motion(tmp_path, monkeypatch, capsys):
    _no_rife(monkeypatch, tmp_path)
    rep = _run(tmp_path, J.jump(), "leap", state="jump", cycle_mode="once", air_retime=f"air={2 * J.AIR}")
    err = capsys.readouterr().err
    meta = _meta(rep)
    assert meta["air_retime"]["applied"] is False and meta["air_retime"]["why"] == "RIFE is not installed"
    assert "leap" in err and "was not retimed" in err and "rife install" in err
    assert meta["frames"] == rep["cycle"]["length"]  # as filmed
    assert meta["root_motion"]["air"][0]["seconds"] == pytest.approx(J.AIR, abs=1e-3)


def test_a_retimed_cut_records_each_cell_as_the_frame_it_is_and_a_cell_made_or_moved_as_not_filmed(tmp_path):
    """The strip's record of its cells (`cell_record`, video-sway --read-on-cut) of the jump with its air half as long
    again, on the clip's own canvas: a cell held_fill.at_times takes as a frame (`source_frames`) is that frame, one made between
    two frames reads as the frame before its time; the cells up to the take-off are their frames as filmed, and no air
    cell (made, or moved onto the parabola) nor any from the landing on (moved along) is, so --read-on-cut does not
    pair it."""
    between = Between(J.draw, range(J.FRAMES))
    rep = _run(tmp_path, J.jump(), "long", state="jump", cycle_mode="once", air_retime=f"air={1.5 * J.AIR}",
               interpolate=between)
    meta = _meta(rep)
    record = meta["air_retime"]
    (air,) = record["air"]
    assert record["applied"] and record["canvas_offset"] == [0, 0] and air["moved_along_px"] != 0
    assert {im.size for im in (Image.open(p) for p in (Path(rep["out_dir"]) / "cycle").glob("frame-*.png"))} == {(J.W, J.H)}
    sha = hashlib.sha256(Path(rep["strip"]["path"]).read_bytes()).hexdigest()
    cells, times = sway.cell_record(meta, sha), meta["source_cut"]["times"]
    assert len(cells) == meta["frames"] == len(times)
    made = [c for c, t in enumerate(times) if abs(t - round(t)) > held_fill.SNAP]
    assert len(made) == record["made"] == 10 and all(air["takeoff"] < c < air["landing"] for c in made)
    for c, (cell, t) in enumerate(zip(cells, times)):
        assert cell["source_frame"] == (math.floor(t) if c in made else round(t)), c
        assert cell["as_filmed"] is (c <= air["takeoff"]), c
    # Read on a cut of the same clip from frame 6: the retimed cut's first cell of frame 6 is made between 6 and 7.
    read = _run(tmp_path, J.jump(), "from6", state="jump", cycle_mode="once", start=6, length=10)
    first = [c["source_frame"] for c in cells].index(6)
    assert first in made
    with pytest.raises(SystemExit, match=rf"cell {first} is not that frame as filmed: video-loop did not cut it as filmed "
                                         rf"\(cell_record\.cells\[{first}\]\.as_filmed\)"):
        sway.recut_pair(meta, _meta(read), hashlib.sha256(Path(read["strip"]["path"]).read_bytes()).hexdigest(),
                        strip_sha256=sha)


def test_a_retimed_cut_whose_frames_between_show_a_frame_again_records_those_cells_as_the_nearer_frame(tmp_path):
    """The same air half as long again with an interpolator that gives back the frame before: no cell between two
    frames is made (each refused, `held`), each is the nearer frame (`air_retime.shown_again`), and the strip's record of
    its cells names that frame (`source_frames`, held_fill.at_times'), not the frame before its time."""
    rep = _run(tmp_path, J.jump(), "stuck", state="jump", cycle_mode="once", air_retime=f"air={1.5 * J.AIR}",
               interpolate=lambda a, b, t: a)
    meta = _meta(rep)
    record = meta["air_retime"]
    between = [c for c, t in enumerate(meta["source_cut"]["times"]) if abs(t - round(t)) > held_fill.SNAP]
    assert record["applied"] and record["made"] == 0 and record["shown_again"] == between and len(between) == 10
    sha = hashlib.sha256(Path(rep["strip"]["path"]).read_bytes()).hexdigest()
    cells, times = sway.cell_record(meta, sha), meta["source_cut"]["times"]
    assert len(cells) == meta["frames"] == len(times)
    assert [cell["source_frame"] for cell in cells] == [round(t) for t in times]
    assert any(round(times[c]) != math.floor(times[c]) for c in between)


def test_gravity_or_speed_without_metres_per_pixel_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="--meters-per-px"):
        _run(tmp_path, J.jump(), "g", state="jump", cycle_mode="once", air_retime="gravity=9.81")
    with pytest.raises(SystemExit, match="--contact"):
        _run(tmp_path, J.jump(), "c", state="jump", cycle_mode="once", contacts=["hand=3:99"])


# The base engine's sidecars of the six inputs the held-cell tests pin (559d498, v3.8.0), every key: without the handle
# the cut keeps every one of them, value for value, a cut played once grows `root_motion`, and every cut the record of
# its cells (`cell_record`, bound to its strip by the PNG file's sha, so read against this run's strip). The keyed clip's
# identity (`source_cut.source.sha256`) is the sha of the input PNG files, other bytes on Linux than on macOS (its
# zlib): it is read against this run's own files, and pinned without.
SIDECARS = {
    "hop-one-shot": "b546c1762af5aee76faa7ff83752a50b2accbc2de4b5daf3fd610a8d3eef914e",
    "swing-once-return": "991343cce794a6f90c30b606f461ed6eeaf8d804e0ecadac3f51ab631b0fb813",
    "swing-pinned": "88ecedeca1ff148799ac226b946c5c7d60d4a73dee347e2c6b4c33ca98eeeec3",
    "topple-once": "78f0c731c7f6c72ea27fa15890652899d45ad8efd1e55ba8a66907b2d447f84e",
    "walk-auto": "edf29b6fde312a55e0e4813ca1571511d855b603a78c12b75a9d5121f9e9448d",
    "walk-fixed": "b9d991735bd05268e04ffae9c2660a21271075f5c0a0c281b2d0e7fbbe4c84de",
}


@pytest.mark.parametrize("case", sorted(UNHELD))
def test_without_the_handle_the_cut_is_the_same_pictures_as_before_and_its_sidecar_only_grows(case, tmp_path, monkeypatch):
    _no_rife(monkeypatch, tmp_path)
    frames, kw, pinned = UNHELD[case]
    keyed = J.save(frames(), tmp_path / f"{case}-keyed")
    rep = loop_mod.run_loop(keyed, tmp_path / f"{case}-out", fps=24.0, min_len=None, max_len=None, n_out=None,
                            seam_max=loop_mod.SEAM_RATIO_MAX, name=case, report_path=None, **kw)
    assert _pictures(Path(rep["out_dir"]), case) == pinned
    meta = _meta(rep)
    grown = meta.pop("root_motion", None)
    cells = meta.pop(loop_mod.CELL_RECORD)
    assert cells["strip_sha256"] == hashlib.sha256(Path(rep["strip"]["path"]).read_bytes()).hexdigest()
    assert len(cells["cells"]) == meta["frames"]
    source = meta["source_cut"]["source"]
    assert source.pop("sha256") == evidence.source_identity(sorted(keyed.glob("*.png")), 24.0)["sha256"]
    # 4.0.0 adds the strip's exact rate and the cells it shows again on purpose; the rest is as the base wrote it.
    assert meta.pop("fps") == pytest.approx(meta["frames"] / meta["cycle_seconds"], rel=1e-4)
    holds = meta.pop("holds")
    rows = ((meta.get("held_fill") or {}).get("before") or {}).get("held_runs", [])
    kept = [row.pop("keep") for row in rows if "keep" in row]
    assert [h["kind"] for h in holds] == kept and set(kept) <= set(held_fill.KEEPS)
    assert hashlib.sha256(json.dumps(meta, sort_keys=True).encode()).hexdigest() == SIDECARS[case]
    assert (grown is not None) == (meta["kind"] in rm.KINDS)
    if grown is not None:
        assert len(grown["root"]) == meta["frames"] and grown["point"] == "mass-centre"


def test_video_loop_and_video_set_take_the_switches():
    from tests.conftest import help_options

    loop = help_options("-m", "sprite_gen.cli", "video-loop")
    assert {"--air-retime", "--meters-per-px", "--contact"} <= loop
    assert {"--air-retime", "--meters-per-px"} <= help_options("-m", "sprite_gen.cli", "video-set")


def test_video_set_hands_the_switches_to_each_cut_and_says_each_air(monkeypatch, tmp_path):
    from sprite_gen.video import batch

    seen: list[dict] = []
    monkeypatch.setattr(batch.frames_mod, "run_frames", lambda clip, out, **kw: {
        "fps": 24, "frames": 10, "alpha_zero_pct_min": 50, "alpha_zero_pct_max": 60, "keyed_dir": str(out)})

    def run_loop(*a, **kw):
        seen.append(kw)
        return {"cycle": {"length": 10, "period_global": None, "ratio": 0.0, "kind": "once"}, "resampled_seam_ratio": None,
                "n_out": 10, "gif": {"file": "loop.gif"}, "webp": {"file": "loop.webp"}, "strip": {"path": "strip.png"},
                "held_fill": {"cells": 10, "poses": 10, "held_runs": [], "made": [],
                              "before": {"cells": 10, "poses": 10, "held_runs": []}},
                "root_motion": {"air": [{"cells": [3, 6], "takeoff": 2, "landing": 7, "seconds": 0.2083, "apex_px": 31.5,
                                         "apex_m": 0.42}]},
                "air_retime": {"applied": False, "why": "RIFE is not installed", "before": []}}

    def video(image, prompt, out, report, **kw):
        out.write_bytes(b"test-video")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0

    monkeypatch.setattr(batch.loop_mod, "run_loop", run_loop)
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)
    still = tmp_path / "side.png"
    Image.new("RGBA", (64, 64), (200, 80, 30, 255)).save(still)
    result = batch.run_set(bases={"side": still}, states=["jump"], root=tmp_path / "set", character=None, duration=3,
                           resolution="720p", key="green", concurrency=1, force=False, gap=0, video_runner=video,
                           align_cycles="off", air_retime="gravity=9.81,air=0.6,speed=4", meters_per_px=0.005)
    assert [(kw["air_retime"], kw["meters_per_px"]) for kw in seen] == [("gravity=9.81,air=0.6,speed=4", 0.005)]
    assert result["items"][0]["loop"]["air"] == ["air 0.21 s, apex 0.42 m (cells 3-6)"]
    assert any("air not retimed" in w for w in result["warnings"])
    with pytest.raises(SystemExit, match="--meters-per-px"):
        batch.run_set(bases={"side": still}, states=["jump"], root=tmp_path / "set2", character=None, duration=3,
                      resolution="720p", key="green", concurrency=1, force=False, gap=0, video_runner=video,
                      align_cycles="off", air_retime="speed=4")
