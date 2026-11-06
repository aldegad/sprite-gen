# SPDX-License-Identifier: Apache-2.0
"""`video-chain`: pieces of keyed clips on one canvas played as one move (docs/video-pipeline.md section 9). Where they join
and when each frame shows is the caller's; the frames between, no picture shown again and the record are the engine's.

Each way of giving the time holds its frames against the same chain with nothing to make frames with: the nearer frame
shows a picture again, and `inspect-holds --gate`, reading what was delivered, fails it; with frames between it passes.
The interpolator is `Between`, a stand-in that draws the synthetic clip again at the time asked for; one test runs RIFE."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen.qa import holds as holds_mod
from sprite_gen.video import chain, held_fill
from tests.video.held_fill_inputs import Between, H, N, W, save


def walk_at(time: float) -> Image.Image:
    """One figure, read as one by `inspect-holds` (a torso and head, two legs going round a hip joined to it), `time`
    frames into a cycle of N: every part of a frame, a third of one too, moves it."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((50, 30, 70, 90), fill=(90, 90, 200, 255))
    d.rectangle((48, 10, 72, 30), fill=(230, 190, 160, 255))
    phase = 2 * math.pi * time / N
    x, y = 40 * math.cos(phase), 40 * math.sin(phase)
    d.line((60, 85, 60 + x, 85 + y), fill=(200, 160, 40, 255), width=14)
    d.line((60, 85, 60 - x, 85 - y), fill=(180, 60, 60, 255), width=14)
    return im


def walk(frames: int = N + 6) -> list[Image.Image]:
    return [walk_at(k) for k in range(frames)]


def clip(tmp_path: Path, name: str, frames: list[Image.Image] | None = None, *, key: str | None = None, fps: float | None = None) -> Path:
    """A clip's keyed frames as `video-frames` leaves them, with its report where a key or a rate is given."""
    out = tmp_path / name
    save(frames if frames is not None else walk(N + 6), out / "keyed")
    if key is not None or fps is not None:
        (out / "frames.report.json").write_text(json.dumps({"key": "auto", "key_read": key, "fps": fps}))
    return out


def between() -> Between:
    return Between(walk_at, [k / 12 for k in range(12 * N)], period=N)


def no_rife(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    path = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not (Path(d) / "rife-ncnn-vulkan").exists()]
    monkeypatch.setenv("PATH", os.pathsep.join(path))
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))


def make(tmp_path: Path, pieces: list[str], **kw) -> dict:
    kw.setdefault("key", "green")
    kw.setdefault("interpolate", between())
    return chain.make_chain(pieces, tmp_path / "out", **kw)


def delivered(out: dict) -> list[Image.Image]:
    return [Image.open(f).convert("RGBA") for f in sorted(Path(out["keyed_dir"]).glob("*.png"))]


def gate(folder: str | Path, *lists: Path) -> int:
    """`inspect-holds --gate` on what was delivered: the reading a user of the engine makes before handing it on."""
    args = [sys.executable, "-m", "sprite_gen.cli", "inspect-holds", "--source", str(folder), "--gate"]
    for path in lists:
        args += ["--holds", str(path)]
    return subprocess.run(args, capture_output=True, encoding="utf-8").returncode


def places_file(tmp_path: Path, places: list[float]) -> Path:
    path = tmp_path / "places.json"
    path.write_text(json.dumps({"places": places}))
    return path


def holds_file(tmp_path: Path, *holds: tuple[int, int]) -> Path:
    path = tmp_path / "meant.holds.json"
    path.write_text(json.dumps({"holds": [{"from": a, "to": b, "reason": "the figure stops on the ledge"} for a, b in holds]}))
    return path


# --- the three times, each no picture shown again ------------------------------------------------------------------

def test_a_join_that_shows_one_picture_twice_is_made_between_where_the_nearer_frame_shows_it_again(tmp_path, monkeypatch):
    """The second clip starts on the frame the first ends on (a clip made from the other's last frame)."""
    d = clip(tmp_path, "a")
    pieces = [f"one={d}:0:9", f"two={d}:9:20"]
    out = make(tmp_path / "with", pieces)
    assert out["status"] == "passed" and out["exit"] == 0 and out["read"]["unlisted"] == 0
    assert gate(out["keyed_dir"]) == 0
    assert out["frames"][10]["source"] == {"between": [{"piece": "one", "frame": 9}, {"piece": "two", "frame": 10}], "t": 0.5}
    assert not any(held_fill.same_steps(delivered(out)))

    no_rife(monkeypatch, tmp_path)
    bare = make(tmp_path / "without", pieces, interpolate=None)
    assert gate(bare["keyed_dir"]) == 1


def test_an_even_resample_makes_its_places_between_where_the_nearer_frame_shows_one_again(tmp_path, monkeypatch):
    d = clip(tmp_path, "a", fps=24.0)
    pieces = [f"one={d}:0:9", f"two={d}:10:20"]
    out = make(tmp_path / "with", pieces, out_fps=60.0)
    assert out["time"] == {"kind": "out-fps", "fps": 24.0, "out_fps": 60.0, "frames": 51}
    assert out["read"]["unlisted"] == 0 and gate(out["keyed_dir"]) == 0
    assert not any(held_fill.same_steps(delivered(out)))
    assert out["frames"][1]["place"] == pytest.approx(0.4)

    no_rife(monkeypatch, tmp_path)
    bare = make(tmp_path / "without", pieces, interpolate=None, out_fps=60.0)
    assert bare["exit"] == 0 and bare["read"]["unlisted"] > 0
    assert gate(bare["keyed_dir"]) == 1


def test_a_list_of_places_makes_each_between_where_the_nearer_frame_shows_one_again(tmp_path, monkeypatch):
    d = clip(tmp_path, "a")
    pieces = [f"one={d}:0:9", f"two={d}:10:20"]
    places = places_file(tmp_path, [2, 2.5, 3, 3.25, 3.5, 3.75, 4, 9, 9.5, 10, 12])
    out = make(tmp_path / "with", pieces, places=places)
    assert out["read"]["unlisted"] == 0 and gate(out["keyed_dir"]) == 0
    assert [c["place"] for c in out["frames"]] == [2, 2.5, 3, 3.25, 3.5, 3.75, 4, 9, 9.5, 10, 12]
    assert out["frames"][1]["source"] == {"between": [{"piece": "one", "frame": 2}, {"piece": "one", "frame": 3}], "t": 0.5}
    assert out["frames"][8]["source"] == {"between": [{"piece": "one", "frame": 9}, {"piece": "two", "frame": 10}], "t": 0.5}
    assert out["frames"][0]["source"] == {"piece": "one", "frame": 2}

    no_rife(monkeypatch, tmp_path)
    assert gate(make(tmp_path / "without", pieces, interpolate=None, places=places)["keyed_dir"]) == 1


def test_a_place_shown_twice_is_made_between_it_and_the_next_and_off_it_fails_by_its_number(tmp_path):
    """The caller's places show a clip frame on two frames in a row, as a time picked on whole frames does."""
    d = clip(tmp_path, "a")
    places = places_file(tmp_path, [2, 3, 3, 4, 5, 6])
    out = make(tmp_path / "on", [f"one={d}:0:9"], places=places)
    assert out["exit"] == 0 and out["read"]["unlisted"] == 0
    assert out["frames"][2]["source"] == {"between": [{"piece": "one", "frame": 3}, {"piece": "one", "frame": 4}], "t": 0.5}

    off = make(tmp_path / "off", [f"one={d}:0:9"], places=places, fill_held="off",
               interpolate=lambda a, b, t: pytest.fail("--fill-held off makes none"))
    assert off["status"] == "failed" and off["exit"] == chain.EXIT_SHOWN_AGAIN
    assert off["read"]["table"]["figure"]["unlistedRuns"][0]["from"] == 2
    assert "figure frame 2" in off["error"]
    assert "keyed_dir" not in off and Path(off["refused_dir"]).is_dir() and not (tmp_path / "off" / "out" / "keyed").exists()


def test_a_frame_filmed_twice_inside_a_piece_is_made_between_the_poses_beside_it(tmp_path):
    frames = walk(N + 6)
    frames[6] = frames[5]
    d = clip(tmp_path, "a", frames)
    out = make(tmp_path, [f"one={d}:0:12"])
    assert out["read"]["unlisted"] == 0 and out["held_fill"]["before"]["held_runs"][0]["filled"] is True
    cell = out["frames"][6]
    assert cell["source"] == {"between": [{"piece": "one", "frame": 5}, {"piece": "one", "frame": 7}], "t": 0.5}
    assert cell["judged"]["contact"]["reads"] in (True, False) and cell["refused"] == []


def test_a_join_s_bridge_makes_its_frames_between_and_they_take_a_place_on_the_chain(tmp_path):
    d = clip(tmp_path, "a")
    out = make(tmp_path, [f"one={d}:0:3", f"two={d}:5:7"], bridge=[1])
    assert out["chain"]["frames"] == 8 and out["chain"]["pieces"] == {"one": [0, 3], "two": [5, 7]}
    bridge = out["frames"][4]
    assert bridge["source"] == {"between": [{"piece": "one", "frame": 3}, {"piece": "two", "frame": 5}], "t": 0.5, "bridge": True}
    assert bridge["place"] == 4 and bridge["judged"]["contact"]["reads"] in (True, False)
    assert out["chain"]["bridges"][0]["place"] == 4
    assert out["joins"][0]["bridge"] == 1
    assert gate(out["keyed_dir"]) == 0


# --- what is kept ----------------------------------------------------------------------------------------------------

def test_a_hold_on_the_list_stays_and_the_same_hold_on_no_list_is_made_between(tmp_path):
    frames = walk(N + 6)
    frames[6] = frames[5]
    d = clip(tmp_path, "a", frames)
    meant = holds_file(tmp_path, (6, 6))
    out = make(tmp_path / "listed", [f"one={d}:0:12"], holds=[meant], interpolate=lambda a, b, t: pytest.fail("the hold is meant: no frame between"))
    assert out["exit"] == 0 and out["read"]["unlisted"] == 0
    assert out["read"]["table"]["figure"]["listedRuns"][0]["reason"] == "the figure stops on the ledge"
    got = delivered(out)
    assert got[6].tobytes() == got[5].tobytes()
    assert gate(out["keyed_dir"], meant) == 0
    assert gate(out["keyed_dir"]) == 1

    unlisted = make(tmp_path / "unlisted", [f"one={d}:0:12"])
    assert unlisted["read"]["unlisted"] == 0 and unlisted["held_fill"]["made"] == [6]
    assert gate(unlisted["keyed_dir"]) == 0

    narrowed = make(tmp_path / "narrowed", [f"one={d}:0:12"], holds=[meant], refuse="ghost", interpolate=lambda a, b, t: pytest.fail("a fault left out fills no hold meant"))
    assert narrowed["exit"] == 0 and narrowed["held_fill"]["made"] == []


def test_a_hold_longer_than_a_drawing_on_threes_is_not_made_away_it_fails_until_it_is_listed(tmp_path):
    frames = walk(N + 6)
    for k in (6, 7, 8, 9):
        frames[k] = frames[5]
    d = clip(tmp_path, "a", frames)
    out = make(tmp_path / "unlisted", [f"one={d}:0:14"], interpolate=lambda a, b, t: pytest.fail("a hold the motion means is kept"))
    assert out["exit"] == chain.EXIT_SHOWN_AGAIN and "figure frames 6-9" in out["error"] and "longer than a drawing on threes" in out["error"]
    assert make(tmp_path / "listed", [f"one={d}:0:14"], holds=[holds_file(tmp_path, (6, 9))])["exit"] == 0


def test_a_chain_s_last_frame_shown_again_has_no_next_pose_and_fails_by_its_number(tmp_path):
    frames = walk(N + 6)
    frames[12] = frames[11]
    d = clip(tmp_path, "a", frames)
    out = make(tmp_path, [f"one={d}:0:12"], interpolate=lambda a, b, t: pytest.fail("no pose after it to make a frame towards"))
    assert out["exit"] == chain.EXIT_SHOWN_AGAIN and "figure frame 12" in out["error"] and "closing pose" in out["error"]


def test_a_piece_drawn_on_twos_keeps_its_drawings_and_says_so(tmp_path):
    twos = [walk_at(k // 2 * 2) for k in range(26)]
    d = clip(tmp_path, "a", twos)
    out = make(tmp_path, [f"one={d}:0:25"], interpolate=lambda a, b, t: pytest.fail("a drawing is not filled"))
    assert out["drawn_pieces"] == {"one": 2} and out["exit"] == chain.EXIT_SHOWN_AGAIN and "one is drawn on twos" in out["error"]


def test_a_frame_with_every_try_refused_stays_named_and_fails_the_chain(tmp_path):
    frames = walk(N + 6)
    frames[6] = frames[5]
    d = clip(tmp_path, "a", frames)
    # a frame between that shows the picture before it: `held`, every fraction
    out = make(tmp_path, [f"one={d}:0:12"], interpolate=lambda a, b, t: a)
    assert out["exit"] == chain.EXIT_SHOWN_AGAIN and "figure frame 6" in out["error"]
    assert out["frames"][6]["refused"] and "held" in out["frames"][6]["refused"][0]["faults"]
    assert any("kept as filmed" in w for w in out["warnings"])


# --- the faults that refuse a frame ----------------------------------------------------------------------------------

def finding(monkeypatch, *faults: str, when=lambda t: True, **measure) -> None:
    """The judge as it is, finding `faults` too in a frame made `when` its fraction says: a material whose frames read so."""
    real = held_fill.judge

    def judge(frame, a, b, t, *, reads, before=None):
        found, wrong = real(frame, a, b, t, reads=reads, before=before)
        return {**found, **measure}, wrong + [f for f in faults if when(t)]

    monkeypatch.setattr(held_fill, "judge", judge)


def test_a_material_every_frame_of_which_reads_a_fault_fails_by_default_and_passes_with_the_fault_left_out(tmp_path, monkeypatch):
    finding(monkeypatch, "outline")
    d = clip(tmp_path, "a")
    places = places_file(tmp_path, [1, 1.5, 2, 3])
    every = make(tmp_path / "every", [f"one={d}:0:4"], places=places)
    assert every["status"] == "failed" and every["exit"] == chain.EXIT_SHOWN_AGAIN and every["refuse"] == list(held_fill.FAULTS)
    assert every["frames"][1]["refused"][0]["faults"] == ["outline"] and "kept_faults" not in every["frames"][1]
    assert "figure frame 2" in every["error"] and "--refuse" in every["error"]

    left_out = make(tmp_path / "left_out", [f"one={d}:0:4"], places=places, refuse="ghost,hole,doubled,held")
    assert left_out["exit"] == chain.EXIT_OK and left_out["read"]["unlisted"] == 0 and gate(left_out["keyed_dir"]) == 0
    cell = left_out["frames"][1]
    assert cell["source"] == {"between": [{"piece": "one", "frame": 1}, {"piece": "one", "frame": 2}], "t": 0.5}
    assert cell["kept_faults"] == ["outline"] and cell["judged"] and cell["refused"] == []
    assert "kept_faults" not in left_out["frames"][0]
    assert left_out["warnings"] == ["frame 1 kept with outline: found, and not named by --refuse"]


def test_the_record_opens_with_the_faults_this_run_refused_for(tmp_path):
    d = clip(tmp_path, "a")
    make(tmp_path / "every", [f"one={d}:0:4"])
    make(tmp_path / "given", [f"one={d}:0:4"], refuse="held,hole,ghost")
    head = lambda name: json.loads((tmp_path / name / "out" / "chain.report.json").read_text())
    assert head("every")["refuse"] == list(held_fill.FAULTS)
    assert head("given")["refuse"] == ["ghost", "hole", "held"]
    assert list(head("given"))[:3] == ["kind", "producer", "refuse"]


def test_the_faults_refuse_names_are_all_the_judge_can_find():
    from sprite_gen.video.interpolation_quality import faults

    over = dict.fromkeys(("dark_excess", "outline_loss", "ghost", "ghost_window", "torn", "hole", "doubled"), 1.0)
    assert set(faults(over)) | {"contact", "held"} == set(held_fill.FAULTS)


@pytest.mark.parametrize("given, words", [("ghost,halo", "'halo' is no fault"), ("", "'' is no fault"), ("ghost,,held", "'' is no fault"), ("GHOST", "'GHOST' is no fault")])
def test_a_name_that_is_no_fault_is_an_error_before_anything_is_touched(tmp_path, given, words):
    d = clip(tmp_path, "a")
    earlier = tmp_path / "out" / "keyed"
    earlier.mkdir(parents=True)
    with pytest.raises(chain.ChainRefused, match=words):
        make(tmp_path, [f"one={d}:0:4"], refuse=given)
    assert earlier.is_dir()
    proc = _cli("--piece", f"one={d}:0:4", "--key", "green", "--refuse", given, "--out-dir", str(tmp_path / "cli"))
    assert proc.returncode == chain.EXIT_ERROR and words in proc.stderr and proc.stdout == ""
    assert not (tmp_path / "cli").exists()


def test_held_refuses_a_frame_whatever_refuse_says(tmp_path):
    d = clip(tmp_path, "a")
    out = make(tmp_path, [f"one={d}:0:4"], places=places_file(tmp_path, [1, 1.5, 2, 3]), refuse="ghost", interpolate=lambda a, b, t: a)
    assert out["refuse"] == ["ghost", "held"]
    assert "held" in out["frames"][1]["refused"][0]["faults"] and "between" not in out["frames"][1]["source"]
    assert out["exit"] == chain.EXIT_SHOWN_AGAIN


def test_the_bar_of_a_frame_made_off_its_time_refuses_whatever_refuse_says(tmp_path, monkeypatch):
    """Refused at its own time, and no fault at any other: a ghost band over the bar still refuses those."""
    finding(monkeypatch, "ghost", when=lambda t: abs(t - 0.5) < 1e-6, ghost_window=0.01)
    d = clip(tmp_path, "a")
    out = make(tmp_path, [f"one={d}:0:4"], places=places_file(tmp_path, [1, 1.5, 2, 3]), refuse="ghost,held")
    cell = out["frames"][1]
    assert "between" not in cell["source"] and cell["nearer_frame"] is True
    assert cell["refused"][0]["faults"] == ["ghost"] and all(t["window_over"] and not t["faults"] for t in cell["refused"][1:]) and cell["refused"][1:]
    assert out["exit"] == chain.EXIT_SHOWN_AGAIN


def test_the_final_reading_fails_a_frame_shown_again_whatever_refuse_says(tmp_path):
    d = clip(tmp_path, "a")
    out = make(tmp_path, [f"one={d}:0:4"], places=places_file(tmp_path, [1, 2, 2, 3]), refuse="held", fill_held="off")
    assert out["exit"] == chain.EXIT_SHOWN_AGAIN and "figure frame 2" in out["error"] and "--refuse" not in out["error"]


def test_a_cell_filled_and_a_bridge_keep_the_faults_they_were_made_with(tmp_path, monkeypatch):
    finding(monkeypatch, "outline")
    frames = walk(N + 6)
    frames[2] = frames[1]
    d = clip(tmp_path, "a", frames)
    out = make(tmp_path, [f"one={d}:0:4", f"two={d}:5:7"], bridge=[1], refuse="ghost,held,contact")
    assert out["exit"] == chain.EXIT_OK, out.get("error")
    filled = out["frames"][2]
    assert filled["source"] == {"between": [{"piece": "one", "frame": 1}, {"piece": "one", "frame": 3}], "t": 0.5}
    assert filled["kept_faults"] == ["outline"]
    assert out["chain"]["bridges"][0]["kept_faults"] == ["outline"] and out["frames"][5]["kept_faults"] == ["outline"]
    assert out["warnings"] == ["frames 2, 5 kept with outline: found, and not named by --refuse"]


def test_a_cell_filled_keeps_its_own_faults_and_none_of_the_picture_it_showed(tmp_path, monkeypatch):
    """A bridge's frame shown twice: the second is filled, and its record is the frame made for it."""
    finding(monkeypatch, "outline", when=lambda t: abs(t - 0.5) > 0.01)
    d = clip(tmp_path, "a")
    places = places_file(tmp_path, [3, 4, 5, 5, 6, 7, 8])
    out = make(tmp_path, [f"one={d}:0:4", f"two={d}:5:7"], bridge=[2], places=places, refuse="ghost,held,contact")
    assert out["exit"] == chain.EXIT_OK, out.get("error")
    shown, filled = out["frames"][2], out["frames"][3]
    assert shown["source"]["bridge"] is True and shown["kept_faults"] == ["outline"]
    assert filled["source"]["t"] == 0.5 and "kept_faults" not in filled and filled["judged"]


def test_a_cut_asked_with_no_refuse_refuses_for_every_fault_and_keeps_none(monkeypatch):
    finding(monkeypatch, "outline")
    frames = walk(12)
    frames[6] = frames[5]
    out, record = held_fill.fill(frames, between(), ring=False)
    assert record["made"] == [] and out[6] is frames[6]
    assert record["before"]["held_runs"][0]["faults"] == ["outline"]
    assert not any("kept_faults" in t for row in record["before"]["held_runs"][0]["tries"] for t in row["tries"])


# --- no RIFE ---------------------------------------------------------------------------------------------------------

def _cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "sprite_gen.cli", "video-chain", *args], capture_output=True, encoding="utf-8", env=env)


def test_no_rife_takes_the_nearer_frame_warns_and_records_it_and_gate_fails_it(tmp_path, monkeypatch):
    no_rife(monkeypatch, tmp_path)
    d = clip(tmp_path, "a")
    places = places_file(tmp_path, [2, 2.5, 3, 3.5, 4, 4.5, 5])
    args = ["--piece", f"one={d}:0:9", "--key", "green", "--places", str(places)]
    plain = _cli(*args, "--out-dir", str(tmp_path / "plain"))
    assert plain.returncode == 0, plain.stderr
    assert "video-chain: warning: 3 frames (1, 3, 5) took the nearer frame" in plain.stderr and "rife install" in plain.stderr
    report = json.loads((tmp_path / "plain" / "chain.report.json").read_text())
    assert report["status"] == "passed" and report["rife"]["why"] == "RIFE is not installed"
    assert all(c["source"].get("piece") == "one" and c["nearer_frame"] for c in report["frames"] if c["frame"] in (1, 3, 5))
    assert (tmp_path / "plain" / "keyed").is_dir()

    gated = _cli(*args, "--gate", "--out-dir", str(tmp_path / "gated"))
    assert gated.returncode == chain.EXIT_SHOWN_AGAIN
    assert "--gate: RIFE is not installed" in gated.stderr
    assert not (tmp_path / "gated" / "keyed").exists() and (tmp_path / "gated" / "keyed.refused").is_dir()
    assert json.loads((tmp_path / "gated" / "chain.report.json").read_text())["status"] == "failed"


def test_no_rife_a_held_cell_and_a_bridge_warn_and_a_chain_with_nothing_to_make_says_nothing(tmp_path, monkeypatch):
    no_rife(monkeypatch, tmp_path)
    frames = walk(N + 6)
    frames[6] = frames[5]
    d = clip(tmp_path, "a", frames)
    held = make(tmp_path / "held", [f"one={d}:0:12", f"two={d}:14:16"], bridge=[1], interpolate=None)
    assert held["exit"] == 0 and held["held_fill"]["unmade"]["why"] == "RIFE is not installed"
    joined = " ".join(held["warnings"])
    assert "shown as filmed" in joined and "a join's frames between are the frames beside it" in joined and "rife install" in joined

    plain = clip(tmp_path, "plain")
    nothing = make(tmp_path / "nothing", [f"one={plain}:0:5", f"two={plain}:6:9"], interpolate=None)
    assert nothing["exit"] == 0 and nothing["warnings"] == [] and "rife" not in nothing and nothing["interpolator"] is None


def test_a_rife_that_fails_is_an_error_never_a_chain_as_filmed(tmp_path):
    from sprite_gen.video import rife

    def broken(a, b, t):
        raise rife.RifeUnavailable("rife-ncnn-vulkan failed")

    d = clip(tmp_path, "a")
    with pytest.raises(ValueError, match="rife-ncnn-vulkan failed"):
        make(tmp_path, [f"one={d}:0:3", f"two={d}:5:7"], bridge=[1], interpolate=broken)
    assert json.loads((tmp_path / "out" / "chain.report.json").read_text())["status"] == "failed"
    assert not (tmp_path / "out" / "keyed").exists()


# --- the pieces --------------------------------------------------------------------------------------------------------

def test_pieces_on_two_canvases_are_refused_and_nothing_is_touched(tmp_path):
    d = clip(tmp_path, "a")
    wider = clip(tmp_path, "b", [Image.new("RGBA", (130, 140), (0, 0, 0, 0))] * 3)
    earlier = tmp_path / "out" / "keyed"
    earlier.mkdir(parents=True)
    with pytest.raises(chain.ChainRefused, match="a chain is one canvas"):
        make(tmp_path, [f"one={d}:0:3", f"two={wider}:0:2"])
    assert earlier.is_dir()


def test_pieces_on_two_keys_are_refused_as_is_a_key_no_report_or_flag_says(tmp_path):
    green, magenta = clip(tmp_path, "g", key="green"), clip(tmp_path, "m", key="magenta")
    with pytest.raises(chain.ChainRefused, match="keyed differently .*one green, two magenta"):
        make(tmp_path, [f"one={green}:0:3", f"two={magenta}:4:6"], key=None)
    with pytest.raises(chain.ChainRefused, match="keyed differently .*one green, --key magenta"):
        make(tmp_path, [f"one={green}:0:3"], key="magenta")
    bare = clip(tmp_path, "bare")
    with pytest.raises(chain.ChainRefused, match="no report says what two is keyed on"):
        make(tmp_path, [f"one={green}:0:3", f"two={bare}:4:6"], key=None)
    out = make(tmp_path, [f"one={green}:0:3", f"two={bare}:4:6"], key="green")
    assert out["key"] == "green" and [p["key_from"] for p in out["pieces"]] == ["frames.report.json", "--key"]


def test_a_report_that_says_only_auto_says_no_key_and_a_brightness_cut_is_its_own(tmp_path):
    d = clip(tmp_path, "a")
    (d / "frames.report.json").write_text(json.dumps({"key": "auto"}))
    with pytest.raises(chain.ChainRefused, match="no report says"):
        make(tmp_path, [f"one={d}:0:3"], key=None)
    (d / "frames.report.json").write_text(json.dumps({"key": "auto", "alpha_mode": "brightness"}))
    assert make(tmp_path, [f"one={d}:0:3"], key=None)["key"] == "brightness"


def test_opaque_frames_are_no_keyed_pieces_and_a_rate_is_one(tmp_path):
    opaque = clip(tmp_path, "o", [Image.new("RGBA", (120, 140), (9, 200, 9, 255))] * 4)
    with pytest.raises(chain.ChainRefused, match="opaque"):
        make(tmp_path, [f"one={opaque}:0:3"])
    slow, fast = clip(tmp_path, "s", fps=24.0), clip(tmp_path, "f", fps=30.0)
    with pytest.raises(chain.ChainRefused, match="different rates"):
        make(tmp_path, [f"one={slow}:0:3", f"two={fast}:4:6"])
    bare = clip(tmp_path, "bare")
    with pytest.raises(chain.ChainRefused, match="--out-fps needs the pieces' rate"):
        make(tmp_path, [f"one={bare}:0:3"], out_fps=60.0)


@pytest.mark.parametrize("pieces, kw, words", [
    (["no-equals"], {}, "NAME=DIR:FROM:TO"),
    (["one={d}:x:3"], {}, "whole frame numbers"),
    (["one={d}:0:999"], {}, "holds frames 0.."),
    (["one={d}:5:2"], {}, "holds frames 0.."),
    (["one={d}:0:3", "one={d}:4:6"], {}, "two pieces are named one"),
    (["one={d}:0:3"], {"bridge": [1]}, "one piece has no join"),
    (["one={d}:0:3", "two={d}:4:6"], {"bridge": [1, 2]}, "2 times"),
    (["one={d}:0:3", "two={d}:4:6"], {"bridge": [-1]}, "0 or more"),
    (["one={d}:0:3"], {"out_fps": 0.0, "fps": 24.0}, "a rate above 0"),
    ([], {}, "no --piece"),
])
def test_what_is_no_chain_is_refused_in_words(tmp_path, pieces, kw, words):
    d = clip(tmp_path, "a")
    with pytest.raises(chain.ChainRefused, match=words.replace(".", r"\.")):
        make(tmp_path, [p.format(d=d) for p in pieces], **kw)


def test_places_off_the_chain_or_no_list_are_refused(tmp_path):
    d = clip(tmp_path, "a")
    with pytest.raises(chain.ChainRefused, match=r"off the chain, whose frames are numbered 0\.\.3"):
        make(tmp_path, [f"one={d}:0:3"], places=places_file(tmp_path, [1, 3.5]))
    bad = tmp_path / "bad.json"
    for body in ('{"places": []}', '{"places": [1, "x"]}', "{", '{"places": [true]}'):
        bad.write_text(body)
        with pytest.raises(chain.ChainRefused, match="--places"):
            make(tmp_path, [f"one={d}:0:3"], places=bad)
    with pytest.raises(chain.ChainRefused, match="give one time"):
        make(tmp_path, [f"one={d}:0:3"], places=bad, out_fps=30.0, fps=24.0)


def test_a_chain_never_writes_over_what_it_reads(tmp_path):
    d = clip(tmp_path, "a")
    with pytest.raises(SystemExit, match="video-chain"):
        chain.make_chain([f"one={d}:0:3"], d, key="green", interpolate=between())


# --- a reading of nothing --------------------------------------------------------------------------------------------

def test_a_chain_that_draws_nothing_is_an_error_at_the_command_line_and_in_the_report(tmp_path):
    empty = clip(tmp_path, "e", [Image.new("RGBA", (120, 140), (0, 0, 0, 0))] * 4)
    proc = _cli("--piece", f"one={empty}:0:3", "--key", "green", "--gate", "--out-dir", str(tmp_path / "out"))
    assert proc.returncode == chain.EXIT_ERROR
    assert holds_mod.NOTHING in proc.stderr and proc.stdout == ""
    report = json.loads((tmp_path / "out" / "chain.report.json").read_text())
    assert report["status"] == "failed" and report["read"]["inspected"] == 0
    assert not (tmp_path / "out" / "keyed").exists()


def test_a_chain_of_one_frame_reads_no_frame_against_another_and_is_an_error(tmp_path):
    d = clip(tmp_path, "a")
    proc = _cli("--piece", f"one={d}:3:3", "--key", "green", "--out-dir", str(tmp_path / "out"))
    assert proc.returncode == chain.EXIT_ERROR and holds_mod.NOTHING in proc.stderr
    assert json.loads((tmp_path / "out" / "chain.report.json").read_text())["read"]["inspected"] == 0


def test_a_chain_none_of_whose_joins_can_be_read_is_an_error(tmp_path):
    """Pieces that do not move have no step to read a join in: a chain does not pass on a join it could not read."""
    still = clip(tmp_path, "s", [walk_at(0)] * 6)
    with pytest.raises(chain.ChainRefused, match="no join was read: one into two: the pieces do not move"):
        make(tmp_path, [f"one={still}:0:2", f"two={still}:3:5"], holds=[holds_file(tmp_path, (0, 5))], interpolate=lambda a, b, t: a)
    assert json.loads((tmp_path / "out" / "chain.report.json").read_text())["status"] == "failed"


def test_a_join_that_cannot_be_read_beside_one_that_can_warns(tmp_path):
    blank = Image.new("RGBA", (120, 140), (0, 0, 0, 0))
    frames = walk(10) + [blank] + walk(10)
    d = clip(tmp_path, "a", frames)
    out = make(tmp_path, [f"one={d}:0:5", f"two={d}:6:10", f"three={d}:12:15"], interpolate=lambda a, b, t: pytest.fail("nothing to make"), near=0)
    assert [j["reads"] for j in out["joins"]] == [True, False]
    assert any("could not be read: no figure on two 10" in w for w in out["warnings"])


# --- the record ------------------------------------------------------------------------------------------------------

def test_the_record_gives_each_frame_its_source_judged_values_and_refused_tries(tmp_path):
    d = clip(tmp_path, "a", fps=24.0)
    calls = []

    def fussy(a, b, t):
        """The stand-in's frame, but its first try at each place shows the frame before it: `held`, refused."""
        calls.append(t)
        return a if len(calls) == 1 else good(a, b, t)

    good = between()
    out = chain.make_chain([f"one={d}:0:4"], tmp_path / "out", key="green", interpolate=fussy, places=places_file(tmp_path, [1, 1.5, 2, 3]))
    report = json.loads((tmp_path / "out" / "chain.report.json").read_text())
    assert report["kind"] == chain.REPORT_KIND and report["status"] == "passed" and out["exit"] == chain.EXIT_OK
    made = report["frames"][1]
    assert made["source"]["between"] == [{"piece": "one", "frame": 1}, {"piece": "one", "frame": 2}]
    assert made["place"] == 1.5 and made["source"]["t"] != 0.5  # made again at another fraction
    assert made["refused"][0]["t"] == 0.5 and "held" in made["refused"][0]["faults"] and "a" in made["refused"][0]["shows"]
    for value in ("outline_loss", "dark_excess", "hole", "doubled", "contact"):
        assert value in made["judged"] and value in made["refused"][0]["judged"]
    assert report["frames"][0] == {"frame": 0, "place": 1.0, "source": {"piece": "one", "frame": 1}, "refused": []}
    assert report["time"] == {"kind": "places", "fps": 24.0, "places_file": str((tmp_path / "places.json").resolve()), "frames": 4}
    assert report["interpolator"] == {"kind": "injected"} and out["report"].endswith("chain.report.json")
    piece = report["pieces"][0]
    assert (piece["name"], piece["from"], piece["to"], piece["key"]) == ("one", 0, 4, "green") and len(piece["source"]["sha256"]) == 64


def test_a_join_tells_its_pose_distance_in_steps_and_the_closer_poses_and_takes_none(tmp_path):
    """The walk's cycle comes back: the join far from its pose has a closer one a few frames off, and it stays where it was given."""
    d = clip(tmp_path, "a")
    out = make(tmp_path, [f"one={d}:0:6", f"two={d}:14:20"], near=3, bridge=[1])
    join = out["joins"][0]
    assert join["from"] == {"piece": "one", "frame": 6} and join["into"] == {"piece": "two", "frame": 14}
    assert join["reads"] is True and join["steps"] > 1 and join["step"] > 0 and join["near"] == 3
    closer = join["closer"]
    assert closer and closer[0]["steps"] < join["steps"] and all(a["steps"] <= b["steps"] for a, b in zip(closer, closer[1:]))
    assert len(closer) <= chain.NEAR_SHOWN and join["closer_found"] >= len(closer)
    assert all(abs(c["from"]["frame"] - 6) <= 3 and abs(c["into"]["frame"] - 14) <= 3 for c in closer)
    assert out["frames"][6]["source"] == {"piece": "one", "frame": 6} and out["frames"][8]["source"] == {"piece": "two", "frame": 14}


def test_a_frame_of_a_piece_is_delivered_byte_for_byte_and_a_chain_with_none_to_make_copies_them_all(tmp_path):
    d = clip(tmp_path, "a")
    out = make(tmp_path, [f"one={d}:0:5", f"two={d}:6:9"], interpolate=lambda a, b, t: pytest.fail("nothing to make"))
    got = sorted(Path(out["keyed_dir"]).glob("*.png"))
    want = sorted((d / "keyed").glob("*.png"))[:10]
    assert [p.read_bytes() for p in got] == [p.read_bytes() for p in want]
    assert [p.name for p in got] == [f"frame-{k + 1:04d}.png" for k in range(10)]
    assert out["held_fill"]["made"] == [] and out["read"]["unlisted"] == 0


def test_a_frame_made_keeps_the_canvas_and_where_the_figure_stands(tmp_path):
    d = clip(tmp_path, "a")
    out = make(tmp_path, [f"one={d}:0:4"], places=places_file(tmp_path, [1, 1.5, 2]))
    got = delivered(out)
    assert got[1].size == (120, 140) and got[1].tobytes() == walk_at(1.5).tobytes()


def test_an_earlier_run_s_frames_are_not_handed_over_by_a_failed_one(tmp_path):
    d = clip(tmp_path, "a")
    assert make(tmp_path, [f"one={d}:0:4"])["exit"] == 0
    held = walk(N + 6)
    held[4] = held[3]
    bad = clip(tmp_path, "bad", held)
    out = make(tmp_path, [f"one={bad}:0:4"], interpolate=lambda a, b, t: pytest.fail("the closing pose is kept"))
    assert out["exit"] == chain.EXIT_SHOWN_AGAIN and not (tmp_path / "out" / "keyed").exists()
    assert make(tmp_path, [f"one={d}:0:4"])["exit"] == 0
    assert not (tmp_path / "out" / "keyed.refused").exists()


def test_the_command_line_says_what_became_of_the_chain(tmp_path):
    d = clip(tmp_path, "a")
    proc = _cli("--piece", f"one={d}:0:5", "--piece", f"two={d}:6:9", "--key", "green", "--out-dir", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stderr
    said = json.loads(proc.stdout)
    assert said["frames"] == 10 and said["made"] == 0 and said["unlisted"] == 0 and said["poses_cells"] == {"before": "10/10", "after": "10/10"}
    assert said["refuse"] == list(held_fill.FAULTS)
    assert said["joins"][0]["from"] == {"piece": "one", "frame": 5} and said["refused_dir"] is None


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed")
def test_clips_from_video_frames_say_their_own_key_and_rate_and_two_keys_refuse_the_chain(tmp_path):
    """Asked `--key auto`, `video-frames` names the key it read, and a chain reads it and the rate from its report."""
    from sprite_gen.video import frames as frames_mod

    def keyed(name: str, key: tuple[int, int, int], body: tuple[int, int, int]) -> tuple[Path, dict]:
        src = tmp_path / name / "src"
        src.mkdir(parents=True)
        for k in range(12):
            im = Image.new("RGB", (128, 128), key)
            ImageDraw.Draw(im).rectangle((40, 30, 56 + 3 * k, 100), fill=body)
            im.save(src / f"f-{k:04d}.png")
        video = tmp_path / name / "clip.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "24", "-i", str(src / "f-%04d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12", str(video)], check=True)
        return tmp_path / name / "frames", frames_mod.run_frames(video, tmp_path / name / "frames", key="auto", allow_edge_contact=False, report_path=None)

    green, read_green = keyed("g", (0, 255, 0), (200, 40, 40))
    magenta, read_magenta = keyed("m", (255, 0, 255), (40, 200, 200))
    assert read_green["key_read"] == "green" and read_magenta["key_read"] == "magenta"
    out = chain.make_chain([f"one={green}:0:5", f"two={green}:6:11"], tmp_path / "out", interpolate=lambda a, b, t: pytest.fail("nothing to make"))
    assert out["exit"] == chain.EXIT_OK and out["key"] == "green" and out["time"] == {"kind": "clip", "fps": 24.0, "frames": 12}
    assert {p["key_from"] for p in out["pieces"]} == {"frames.report.json"}
    with pytest.raises(chain.ChainRefused, match="keyed differently .*one green, two magenta"):
        chain.make_chain([f"one={green}:0:5", f"two={magenta}:6:11"], tmp_path / "out2")


@pytest.mark.real_rife
def test_rife_makes_a_chain_s_bridge_and_places_with_none_shown_again(tmp_path):
    d = clip(tmp_path, "a", fps=24.0)
    proc = _cli("--piece", f"one={d}:0:6", "--piece", f"two={d}:7:12", "--bridge", "0", "--out-fps", "48", "--out-dir", str(tmp_path / "out"), "--key", "green")
    report = json.loads((tmp_path / "out" / "chain.report.json").read_text())
    assert report["interpolator"]["kind"] != "injected" and report["time"]["out_fps"] == 48
    assert proc.returncode == 0, proc.stderr
    assert report["read"]["unlisted"] == 0 and gate(tmp_path / "out" / "keyed") == 0
    assert Image.open(sorted((tmp_path / "out" / "keyed").glob("*.png"))[1]).size == (120, 140)
