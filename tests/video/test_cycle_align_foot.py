# SPDX-License-Identifier: Apache-2.0
"""A loop whose foot the engine cannot name is told it, one loop at a time (docs/loop-repair.md
section 4): the report lists it under `unnamed_feet` with its two strike frames in `cycle/`, and
`--foot <loop>=left|right` — which own foot lands on the first — turns that loop alone to start as
`--start-foot` lands, recorded `start_foot_source: "given"`.

The figures are the drawn walker of test_cycle_align: its right heel lands at phase 0, its left at
pi. Seen from the side its near (right) leg is drawn light over the shaded far leg, which names the
feet; drawn unshaded, nothing tells them apart and the foot is left unnamed."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from sprite_gen.video import align, batch
from sprite_gen.video import loop as loop_mod

from tests.video.test_cycle_align import _figure

N = 12  # frames a cycle; the loops are cut at phase 5/12 so no loop starts on a strike as cut
OUTPUTS = ("walk.strip.png", "walk.strip.json", "walk.gif", "walk.webp")


def _cut(root: Path, name: str, **kw) -> Path:
    keyed = root / f"{name}-keyed"
    keyed.mkdir(parents=True)
    for k in range(N + 6):
        _figure(2 * math.pi * ((k + 5) % N) / N, **kw).save(keyed / f"{k:04d}.png")
    out = root / name
    loop_mod.run_loop(keyed, out, fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=1000.0,
                      name="walk", report_path=None, cycle_mode="fixed", start=0, length=N, anchor="none", repair="off")
    return out


def _set(root: Path) -> list[Path]:
    """Front and side (shaded): the feet named. W: a side view drawn unshaded — not named."""
    return [_cut(root, "S", view="front"), _cut(root, "E", view="side"), _cut(root, "W", view="side", shaded=False)]


VIEWS = ["front", "side@right", "side@right"]


def _bytes(d: Path) -> dict[str, bytes]:
    return {name: (d / name).read_bytes() for name in OUTPUTS} | {p.name: p.read_bytes() for p in sorted((d / "cycle").glob("frame-*.png"))}


def _rows(report: dict) -> dict[str, dict]:
    return {Path(r["dir"]).name: r for r in report["loops"]}


def test_a_loop_whose_foot_is_not_named_is_listed_with_its_two_strike_frames(tmp_path):
    dirs = _set(tmp_path)
    report = align.align_set(dirs, views=VIEWS, report_path=tmp_path / "walk.cycle-align.json")
    rows = _rows(report)
    assert [rows[k]["start_foot_source"] for k in "SEW"] == ["engine", "engine", None]
    assert rows["W"]["start_foot"] is None and "shade does not follow the step" in rows["W"]["foot_why"]
    assert report["feet_given"] == {} and len(report["unnamed_feet"]) == 1
    entry = report["unnamed_feet"][0]
    assert entry["dir"] == str(dirs[2]) and entry["view"] == "side@right" and entry["foot_why"] == rows["W"]["foot_why"]
    # the first candidate is the frame the loop starts on; the second is half a cycle on
    assert [(c["strike"], c["frame"]) for c in entry["candidates"]] == [(0, 0), (1, N // 2)]
    assert [c["path"] for c in entry["candidates"]] == [str(dirs[2] / "cycle" / f"frame-{k:03d}.png") for k in (0, N // 2)]
    assert all(Path(c["path"]).is_file() for c in entry["candidates"])
    assert entry["settle"] == [f"--foot {dirs[2]}=left", f"--foot {dirs[2]}=right"]
    assert any("cycle/frame-000.png" in w and f"--foot {dirs[2]}=left|right" in w for w in report["warnings"])
    written = json.loads((tmp_path / "walk.cycle-align.json").read_text())
    assert written["unnamed_feet"] == report["unnamed_feet"]
    assert json.loads((dirs[2] / "walk.strip.json").read_text())["cycle_align"]["start_foot_source"] is None


@pytest.mark.parametrize("key", ["W", "dir"])
def test_the_foot_given_for_one_loop_turns_that_loop_alone_half_a_cycle(tmp_path, key):
    """Its left foot lands on the first strike: the loop starts half a cycle on, where its right
    lands. The other two loops come out byte for byte as they were."""
    dirs = _set(tmp_path)
    align.align_set(dirs, views=VIEWS)
    before = {d.name: _bytes(d) for d in dirs}
    report = align.align_set(dirs, views=VIEWS, feet={"W" if key == "W" else str(dirs[2]): "left"})
    rows = _rows(report)
    for d in dirs[:2]:
        assert _bytes(d) == before[d.name], d.name
    w = rows["W"]
    assert w["start_foot"] == "right" and w["start_foot_source"] == "given" and w["foot_given"] == ["left", "right"]
    assert "shade does not follow the step" in w["foot_unnamed_why"] and "foot_why" not in w
    assert w["strikes"] == [N // 2, 0]
    for k in range(N):  # frame k now is the frame half a cycle on as it was
        assert (dirs[2] / "cycle" / f"frame-{k:03d}.png").read_bytes() == before["W"][f"frame-{(k + N // 2) % N:03d}.png"]
    assert report["unnamed_feet"] == [] and report["feet_given"] == {str(dirs[2]): "left"}
    assert not any("foot not named" in line for line in report["warnings"])
    assert json.loads((dirs[2] / "walk.strip.json").read_text())["cycle_align"]["start_foot_source"] == "given"
    # told again, the same loop: the answer is about the loop as filmed, so nothing moves
    turned = _bytes(dirs[2])
    align.align_set(dirs, views=VIEWS, feet={"W": "left"})
    assert _bytes(dirs[2]) == turned


def test_the_foot_given_that_lands_first_keeps_the_start_and_records_who_said_it(tmp_path):
    dirs = _set(tmp_path)
    align.align_set(dirs, views=VIEWS)
    before = {d.name: _bytes(d) for d in dirs}
    report = align.align_set(dirs, views=VIEWS, feet={"W": "right"})
    assert {d.name: {k: v for k, v in _bytes(d).items() if k != "walk.strip.json"} for d in dirs} == \
           {k: {n: b for n, b in v.items() if n != "walk.strip.json"} for k, v in before.items()}
    w = _rows(report)["W"]
    assert w["strikes"] == [0, N // 2] and w["start_foot_source"] == "given" and w["foot_given"] == ["right", "left"]


def test_a_foot_given_against_the_views_own_reading_is_taken_and_named(tmp_path):
    dirs = _set(tmp_path)
    named = _rows(align.align_set(dirs, views=VIEWS))["E"]
    landing = named["foot"]["feet"][0]  # the own foot the shade says lands on the first strike
    other = "left" if landing == "right" else "right"
    report = align.align_set(dirs, views=VIEWS, feet={"E": other})
    e = _rows(report)["E"]
    assert e["start_foot_source"] == "given" and e["foot_disagrees"] == named["foot"]["feet"]
    assert e["turned_by"] != named["turned_by"]
    assert any(f"--foot says {other}" in line and f"said {landing}" in line for line in report["warnings"])


def test_a_foot_for_no_loop_or_not_a_foot_is_refused_before_anything_is_rewritten(tmp_path):
    dirs = _set(tmp_path)
    align.align_set(dirs, views=VIEWS)
    before = {d.name: _bytes(d) for d in dirs}
    with pytest.raises(SystemExit, match="--foot nope=left names 0 of the --loop-dir given"):
        align.align_set(dirs, views=VIEWS, feet={"nope": "left"})
    with pytest.raises(SystemExit, match="--foot walk=left names 3"):  # every loop's strip is `walk`
        align.align_set(dirs, views=VIEWS, feet={"walk": "left"})
    with pytest.raises(SystemExit, match="--foot W=up: the foot that lands on the loop's first strike is one of left, right"):
        align.align_set(dirs, views=VIEWS, feet={"W": "up"})
    with pytest.raises(SystemExit, match="--foot names .* twice"):
        align.align_set(dirs, views=VIEWS, feet={"W": "left", str(dirs[2]): "left"})
    assert {d.name: _bytes(d) for d in dirs} == before
    for bad in (["W=up"], ["W"], ["=left"], ["W=left", "W=right"]):
        with pytest.raises(SystemExit, match="--foot"):
            align.parse_feet(bad)
    with pytest.raises(SystemExit, match="names 0"):
        align.main([*(f"--loop-dir={d}" for d in dirs), *(f"--view={v}" for v in VIEWS), "--foot", "nope=left"])
    assert {d.name: _bytes(d) for d in dirs} == before


def test_the_command_takes_foot(tmp_path, capsys):
    dirs = _set(tmp_path)
    assert align.main([*(f"--loop-dir={d}" for d in dirs), *(f"--view={v}" for v in VIEWS), "--foot", "W=left",
                       "--report", str(tmp_path / "r.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["feet_given"] == {str(dirs[2]): "left"} and out["unnamed_feet"] == []
    assert [r["start_foot_source"] for r in out["loops"]] == ["engine", "engine", "given"]


def test_a_stopped_set_carries_the_foot_given_into_the_command_that_settles_it(tmp_path, monkeypatch):
    dirs = _set(tmp_path)
    real = align.cycle_screen

    def suspect_w(frames, **kw):
        screen = real(frames, **kw)
        if len(frames) == N and frames[0].tobytes() == align._source_frames(dirs[2])[0].tobytes():
            screen = {**screen, "suspects": [{"period": N // 2, "seconds": 0.25, "divisor": 2, "cycles": 2, "why": "a half returns"}]}
        return screen

    monkeypatch.setattr(align, "cycle_screen", suspect_w)
    with pytest.raises(align.CycleSuspects) as stop:
        align.align_set(dirs, views=VIEWS, feet={"W": "left"}, report_path=tmp_path / "r.json")
    assert f"--foot {dirs[2]}=left" in stop.value.command
    assert json.loads((tmp_path / "r.json").read_text())["feet_given"] == {str(dirs[2]): "left"}


def _rows_for_set(root: Path) -> list[dict]:
    rows = []
    for item, kw in (("front-walk", dict(view="front")), ("side-walk", dict(view="side", shaded=False))):
        d = root / item
        _cut(d, "loop", **kw)
        rows.append({"item": item, "direction": item.split("-")[0], "state": "walk", "turned": "right", "dir": str(d), "ok": True,
                     "loop": {"n_out": N}})
    return rows


def test_video_set_hands_an_items_foot_to_its_states_alignment(tmp_path, capsys):
    rows = _rows_for_set(tmp_path)
    first = batch.align_gaits(rows, tmp_path, "auto")
    assert [e["dir"] for e in first["walk"]["unnamed_feet"]] == [str(tmp_path / "side-walk" / "loop")]
    assert rows[1]["loop"]["cycle_align"]["start_foot_source"] is None
    told = batch.align_gaits(rows, tmp_path, "auto", feet={"side-walk": "left"})
    assert told["walk"]["applied"] and told["walk"]["unnamed_feet"] == []
    assert told["walk"]["feet_given"] == {str(tmp_path / "side-walk" / "loop"): "left"}
    assert [r["loop"]["cycle_align"]["start_foot_source"] for r in rows] == ["engine", "given"]
    assert "feet_unused" not in told["walk"]
    with pytest.raises(SystemExit, match="--align-foot back-walk=left names no item"):
        batch.align_gaits(rows, tmp_path, "auto", feet={"back-walk": "left"})


def test_video_set_names_a_foot_no_alignment_took(tmp_path, capsys):
    rows = _rows_for_set(tmp_path)
    rows.append({"item": "back-walk", "direction": "back", "state": "walk", "dir": str(tmp_path / "back-walk"), "ok": False, "error": "no clip"})
    out = batch.align_gaits(rows, tmp_path, "auto", feet={"back-walk": "left"})
    assert out["walk"]["applied"] and out["walk"]["feet_unused"] == {"back-walk": "left"}
    assert "--align-foot back-walk=left not applied" in capsys.readouterr().err


def test_video_set_refuses_a_foot_for_an_item_it_does_not_align_before_filming(tmp_path):
    from PIL import Image

    base = tmp_path / "front.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(base)
    side = tmp_path / "side.png"
    Image.new("RGBA", (8, 8), (0, 255, 0, 255)).save(side)

    def no_film(*a, **kw):
        raise AssertionError("refused before any clip is filmed")

    common = dict(bases={"front": base, "side": side}, root=tmp_path / "set", character=None, duration=None, resolution="480p",
                  key="green", concurrency=1, force=False, gap=0.0, video_runner=no_film)
    with pytest.raises(SystemExit, match="--align-foot front-idle=left: front-idle is not an item the set aligns"):
        batch.run_set(states=["idle", "walk"], align_feet={"front-idle": "left"}, **common)
    with pytest.raises(SystemExit, match="--align-foot front-walk=left: .*none"):
        batch.run_set(states=["walk"], align_cycles="off", align_feet={"front-walk": "left"}, **common)
    with pytest.raises(SystemExit, match="--align-foot expects ITEM|--align-foot expects LOOP"):
        batch.run(base=[f"front={base}"], out_dir=tmp_path / "x", align_foot=["front-walk=up"])
