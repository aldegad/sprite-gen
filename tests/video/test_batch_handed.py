# SPDX-License-Identifier: Apache-2.0
"""A set filmed facing right and left from stills drawn each way (no mirroring), with an asymmetric
item's side in every clip prompt. Real canvases, an offline video runner, vision and loop."""
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen.gen import handedness as h
from sprite_gen.video import batch

FIXTURES = Path(__file__).parents[1] / "fixtures" / "facing"
WATCH = [h.parse("the black smartwatch=left wrist")]


@pytest.fixture
def offline(monkeypatch):
    loops = []
    monkeypatch.setattr(batch.facing_mod.vision, "grok_inspect", lambda path, **kw: (
        "left" if "left" in Path(path).name else "right", {}))
    monkeypatch.setattr(batch.frames_mod, "run_frames", lambda clip, out, **kw: {
        "fps": 24, "frames": 10, "alpha_zero_pct_min": 50, "alpha_zero_pct_max": 60, "keyed_dir": str(out)})

    def run_loop(*a, **kw):
        loops.append((kw["name"], kw["facing"]))
        return {"cycle": {"length": 10, "period_global": 10, "ratio": 0.2}, "resampled_seam_ratio": 0.2, "n_out": 10,
                "gif": {"file": "loop.gif"}, "webp": {"file": "loop.webp"}, "strip": {"path": "strip.png"}}
    monkeypatch.setattr(batch.loop_mod, "run_loop", run_loop)
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)
    return loops


def _video(prompts):
    def video(image, prompt, out, report, **kw):
        prompts[out.parent.name] = prompt
        out.write_bytes(b"test-video")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0
    return video


def _bases():
    return {"front": FIXTURES / "front.png",
            "side@right": FIXTURES / "right.png", "side@left": FIXTURES / "left.png",
            "front_diagonal@right": FIXTURES / "right.png", "front_diagonal@left": FIXTURES / "left.png"}


def test_both_facings_film_every_turned_view_twice_from_its_own_still(tmp_path, offline) -> None:
    prompts: dict[str, str] = {}
    result = batch.run_set(bases=_bases(), states=["walk"], root=tmp_path, character=None, duration=3,
                           resolution="720p", key="green", concurrency=1, force=False, gap=0, facing="right,left",
                           video_runner=_video(prompts), walk_start="as-given", align_cycles="off", handed=WATCH)
    names = [i["item"] for i in result["items"]]
    assert names == ["front-walk", "side-right-walk", "side-left-walk", "front_diagonal-right-walk",
                     "front_diagonal-left-walk"]
    assert result["ok"] == 5 and result["handed"] == [vars(WATCH[0])]
    for name, turned in (("side-right-walk", "right"), ("side-left-walk", "left"),
                         ("front_diagonal-right-walk", "right"), ("front_diagonal-left-walk", "left")):
        item = next(i for i in result["items"] if i["item"] == name)
        assert item["turned"] == turned
        canvas = json.loads((tmp_path / name / "canvas.report.json").read_text())
        assert canvas["facing"] == turned
        direction = item["direction"]
        assert prompts[name] == batch.build_prompt(direction, "walk", None, facing=turned, handed=WATCH)
        assert prompts[name].endswith(h.text(WATCH, direction, turned, clip=True, gait=True))
    assert "and to the left" in prompts["front_diagonal-left-walk"]
    assert "to the right" not in prompts["front_diagonal-left-walk"]
    # each side still is inspected for its own facing, into its own copy, never turned over
    side_left = next(i for i in result["items"] if i["item"] == "side-left-walk")["facing"]
    assert side_left["requested"] == "left" and side_left["action"] == "none"
    assert (tmp_path / "side-left.facing.png").read_bytes() == (FIXTURES / "left.png").read_bytes()
    assert (tmp_path / "side-right.facing.png").read_bytes() == (FIXTURES / "right.png").read_bytes()
    assert dict(offline) == {"front-walk": "right", "side-right-walk": "right", "side-left-walk": "left",
                             "front_diagonal-right-walk": "right", "front_diagonal-left-walk": "left"}
    table = (tmp_path / "table.md").read_text()
    assert "| side (left) | walk |" in table and "| front | walk |" in table


def test_one_facing_keeps_the_item_names_and_table(tmp_path, offline) -> None:
    prompts: dict[str, str] = {}
    result = batch.run_set(bases={"front_diagonal": FIXTURES / "left.png"}, states=["attack"], root=tmp_path,
                           character=None, duration=2, resolution="720p", key="green", concurrency=1, force=False,
                           gap=0, facing="left", video_runner=_video(prompts))
    assert [i["item"] for i in result["items"]] == ["front_diagonal-attack"]
    assert offline == [("front_diagonal-attack", "left")]  # a left diagonal is cut as a left one
    assert "| front_diagonal | attack |" in (tmp_path / "table.md").read_text()


@pytest.mark.parametrize("bases,facing,error", [
    ({"side": "right.png"}, "right,left", "needs a still for each"),
    ({"side@right": "right.png"}, "right,left", "draw it that way"),
    ({"side@left": "left.png"}, "right", "the set is filmed facing right"),
    ({"front@left": "front.png"}, "right", "not turned to a side"),
    ({"side": "right.png"}, "up", "--facing must be right, left or right,left"),
])
def test_bases_that_would_need_a_mirror_or_make_no_sense_are_refused(tmp_path, bases, facing, error) -> None:
    with pytest.raises(SystemExit, match=error):
        batch.run_set(bases={k: FIXTURES / v for k, v in bases.items()}, states=["walk"], root=tmp_path,
                      character=None, duration=3, resolution="720p", key="green", concurrency=1, force=False, gap=0,
                      facing=facing, video_runner=_video({}))


def test_mirror_correction_is_refused_with_an_item(tmp_path) -> None:
    with pytest.raises(SystemExit, match="moves every --handed item"):
        batch.run_set(bases={"side": FIXTURES / "right.png"}, states=["walk"], root=tmp_path, character=None,
                      duration=3, resolution="720p", key="green", concurrency=1, force=False, gap=0,
                      facing_fix="mirror", video_runner=_video({}), handed=WATCH)


def test_cli_threads_both_facings_and_items(monkeypatch, tmp_path) -> None:
    seen = []
    monkeypatch.setattr(batch, "run_set", lambda **kw: seen.append(kw) or {"failed": []})
    assert batch.main(["--base", f"side@right={FIXTURES / 'right.png'}", "--base", f"side@left={FIXTURES / 'left.png'}",
                       "--states", "walk", "--out-dir", str(tmp_path), "--facing", "right,left",
                       "--handed", "the black smartwatch=left wrist"]) == 0
    assert seen[0]["facing"] == "right,left" and seen[0]["handed"] == WATCH
    assert set(seen[0]["bases"]) == {"side@right", "side@left"}


def test_a_front_walk_redrawn_mid_step_is_told_where_the_item_is(tmp_path, offline) -> None:
    """The front and back walks film from the still redrawn mid-step; that redraw says the item's side too,
    so a redraw from the reference alone does not move it, and its clip prompt is anchored to that still."""
    redraws: list[str] = []

    def paint(base, prompt, out, report, *, log, provider=None):
        redraws.append(prompt)
        out.write_bytes(base.read_bytes())
        report.write_text(json.dumps({"prompt": prompt, "refs": [str(base)]}))
        return 0

    base = Image.new("RGB", (96, 128), (0, 255, 0))
    ImageDraw.Draw(base).rectangle((36, 20, 59, 109), fill=(120, 60, 40))
    base.save(tmp_path / "front.png")
    result = batch.run_set(bases={"front": tmp_path / "front.png"}, states=["walk"], root=tmp_path, character=None,
                           duration=3, resolution="720p", key="green", concurrency=1, force=False, gap=0,
                           video_runner=_video({}), redraw_runner=paint, align_cycles="off", handed=WATCH)
    assert result["ok"] == 1 and result["items"][0]["walk_start"]["redrawn"] is True
    assert redraws == [batch.walk_start_prompt("front", result["items"][0]["walk_start"]["key"], WATCH)]
    assert redraws[0].endswith(h.text(WATCH, "front")) and "at the right of the picture" in redraws[0]
