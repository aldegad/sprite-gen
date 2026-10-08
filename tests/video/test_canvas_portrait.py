# SPDX-License-Identifier: Apache-2.0
"""`--shape portrait` and `video-set --diagonal-gait-shape`: the roomy upright frame a diagonal walk or run was filmed
on in the crashbang village production (9:16, the figure 60 % of the height, 18 % above it). Real canvases; the clip,
frames and loop are stand-ins."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import batch, canvas

GREEN = (0, 255, 0)
BODY = (120, 60, 40)


def _still(path: Path | None = None, size=(200, 300), box=(70, 50, 129, 249), fill=GREEN) -> Image.Image | Path:
    """A still on a flat key with a subject filling `box` (inclusive), as image or saved at `path`."""
    image = Image.new("RGB", size, fill)
    ImageDraw.Draw(image).rectangle(box, fill=BODY)
    if path is None:
        return image
    image.save(path)
    return path


def _production(subject_w: int, subject_h: int) -> tuple[int, int, int, int]:
    """The production's own arithmetic (batch_motion.py, take pinned-portrait-roomy): canvas and where the subject goes."""
    canvas_h = math.ceil(subject_h / 0.60)
    canvas_w = round(canvas_h * 9 / 16)
    return canvas_w, canvas_h, (canvas_w - subject_w) // 2, round(canvas_h * 0.18)


def test_portrait_is_a_shape_of_its_own_for_every_state() -> None:
    assert "portrait" in canvas.SHAPES
    for state in ("walk", "run", "idle", "attack", None):
        profile = canvas.profile_for(state, shape="portrait")
        assert profile is canvas.PORTRAIT
        assert (profile.ratio, profile.headroom, profile.subject) == (9 / 16, 0.18, 0.60)
    # the other rows say nothing about the subject's share of the height
    assert all(canvas.profile_for(s).subject == 0.0 for s in ("walk", "jump", "attack", "cheer"))


def test_the_subject_fills_60_percent_of_a_9_16_canvas_with_18_percent_above_it() -> None:
    padded, report = canvas.pad_canvas(_still(), canvas.profile_for("walk", shape="portrait"))
    w, h, x, y = _production(60, 200)
    assert (w, h, x, y) == (188, 334, 64, 60)
    assert padded.size == (w, h) and report["canvas"] == [w, h] and report["offset"] == [x, y]
    assert report["shape"] == "portrait" and report["fit"] == "state" and abs(report["ratio"] - 9 / 16) < 0.01
    assert report["portrait"] == {"subject_box": [70, 50, 130, 250], "subject": [60, 200], "subject_height_pct": 59.88}
    # the subject is pasted whole and unscaled, on the exact key
    assert padded.crop((x, y, x + 60, y + 200)).getcolors() == [(60 * 200, BODY)]
    assert padded.getpixel((0, 0)) == GREEN and padded.getpixel((x - 1, y)) == GREEN and padded.getpixel((x, y - 1)) == GREEN
    assert padded.getpixel((x, y + 200)) == GREEN  # room below the feet: the rest of the height


def test_the_room_above_is_the_canvas_share_whatever_margin_the_still_had() -> None:
    """The still's own margin does not set the frame: the same subject drawn lower in a taller still is placed the same."""
    high, a = canvas.pad_canvas(_still(), canvas.PORTRAIT)
    low, b = canvas.pad_canvas(_still(size=(300, 500), box=(10, 280, 69, 479)), canvas.PORTRAIT)
    assert a["canvas"] == b["canvas"] and a["offset"] == b["offset"] and high.tobytes() == low.tobytes()


def test_a_subject_wider_than_the_frame_widens_it_and_is_never_cut() -> None:
    padded, report = canvas.pad_canvas(_still(size=(400, 200), box=(20, 40, 379, 159)), canvas.PORTRAIT)
    w, h = padded.size
    assert w == 360 and w / h == pytest.approx(9 / 16, abs=0.01) and report["portrait"]["subject_height_pct"] < 60
    x, y = report["offset"]
    assert padded.crop((x, y, x + 360, y + 120)).getcolors() == [(360 * 120, BODY)]


def test_portrait_on_a_white_base_reads_the_subject_by_the_corner_colour() -> None:
    padded, report = canvas.pad_canvas(_still(fill=(250, 250, 248)), canvas.PORTRAIT, key="auto")
    assert report["key"] is None and report["portrait"]["subject"] == [60, 200]
    assert padded.getpixel((0, 0)) == (250, 250, 248)


def test_a_headroom_that_leaves_no_room_below_is_refused() -> None:
    with pytest.raises(SystemExit, match="leaves no room below"):
        canvas.pad_canvas(_still(), canvas.PORTRAIT, headroom=0.45)
    _, report = canvas.pad_canvas(_still(), canvas.PORTRAIT, headroom=0.30)
    assert report["offset"][1] == round(report["canvas"][1] * 0.30)


def test_the_other_shapes_report_as_before() -> None:
    for state, shape in (("walk", None), ("jump", None), ("attack", None), ("walk", "wide"), ("idle", "tall")):
        _, report = canvas.pad_canvas(_still(), canvas.profile_for(state, shape))
        assert "portrait" not in report and list(report)[-1] == "why"


def test_video_canvas_cli_takes_portrait(tmp_path: Path, capsys) -> None:
    still = _still(tmp_path / "still.png")
    assert canvas.main(["--still", str(still), "--state", "run", "--shape", "portrait", "--out", str(tmp_path / "c.png"),
                        "--report", str(tmp_path / "c.json")]) == 0
    report = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert report["shape"] == "portrait" and Image.open(tmp_path / "c.png").size == tuple(report["canvas"])
    with pytest.raises(SystemExit, match="--fit tight picks its own shape"):
        canvas.run_canvas(still, tmp_path / "t.png", state="walk", shape="portrait", facing="right", headroom=None,
                          lead=None, report_path=None, fit="tight")


# -- video-set --diagonal-gait-shape ----------------------------------------------------------------

@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(batch.frames_mod, "run_frames", lambda clip, out, **kw: {
        "fps": 24, "frames": 10, "alpha_zero_pct_min": 50, "alpha_zero_pct_max": 60, "keyed_dir": str(out)})
    monkeypatch.setattr(batch.loop_mod, "run_loop", lambda *a, **kw: {
        "cycle": {"length": 10, "period_global": 10, "ratio": 0.2}, "resampled_seam_ratio": 0.2, "n_out": 10,
        "gif": {"file": "loop.gif"}, "webp": {"file": "loop.webp"}, "strip": {"path": "strip.png"}})
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)


def _video(seen: dict[str, tuple[int, int]]):
    def video(image, prompt, out, report, **kw):
        with Image.open(image) as im:
            seen[out.parent.name] = im.size
        out.write_bytes(b"test-video")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0
    return video


def _set(tmp_path: Path, seen, **kw):
    bases = {view: _still(tmp_path / f"{view}.png") for view in ("front", "front_diagonal", "back_diagonal")}
    return batch.run_set(bases=bases, states=["walk", "run", "idle"], root=tmp_path / "set", character=None, duration=3,
                         resolution="720p", key="green", concurrency=1, force=False, gap=0, video_runner=_video(seen),
                         walk_start="as-given", align_cycles="off", **kw)


def _shapes(result) -> dict[str, str]:
    return {item["item"]: item["canvas"]["shape"] for item in result["items"]}


def test_only_the_diagonal_walks_and_runs_take_the_diagonal_gait_shape(tmp_path, offline) -> None:
    seen: dict[str, tuple[int, int]] = {}
    result = _set(tmp_path, seen, diagonal_gait_shape="portrait")
    assert result["ok"] == 9 and result["diagonal_gait_shape"] == "portrait"
    shapes = _shapes(result)
    for view in ("front_diagonal", "back_diagonal"):
        for state in ("walk", "run"):
            assert shapes[f"{view}-{state}"] == "portrait" and seen[f"{view}-{state}"] == (188, 334)
        assert shapes[f"{view}-idle"] == "square"
    assert {shapes[f"front-{s}"] for s in ("walk", "run", "idle")} == {"square"}
    assert batch.item_shape("side", "walk", None, "portrait") is None
    assert batch.item_shape("front_diagonal", "jump", "wide", "portrait") == "wide"


def test_without_it_every_item_keeps_its_own_canvas(tmp_path, offline) -> None:
    seen: dict[str, tuple[int, int]] = {}
    result = _set(tmp_path, seen)
    assert result["ok"] == 9 and "diagonal_gait_shape" not in result
    assert set(_shapes(result).values()) == {"square"}
    assert all(size == (300, 300) for size in seen.values())


def test_it_wins_over_the_sets_shape_for_the_diagonal_gaits_only(tmp_path, offline) -> None:
    result = _set(tmp_path, {}, shape="wide", diagonal_gait_shape="portrait")
    shapes = _shapes(result)
    assert shapes["back_diagonal-run"] == "portrait" and shapes["back_diagonal-idle"] == "wide"
    assert shapes["front-walk"] == "wide"


def test_a_cached_clip_on_another_canvas_is_not_reused(tmp_path, offline) -> None:
    assert _set(tmp_path, {})["ok"] == 9
    again: dict[str, tuple[int, int]] = {}
    result = _set(tmp_path, again, diagonal_gait_shape="portrait")
    failed = {item["item"] for item in result["items"] if not item["ok"]}
    assert failed == {f"{v}-{s}" for v in ("front_diagonal", "back_diagonal") for s in ("walk", "run")}
    assert all("cached canvas/facing" in item["error"] for item in result["items"] if not item["ok"])
    assert again == {}  # the other five reused their clips


@pytest.mark.parametrize("kw,error", [
    ({"diagonal_gait_shape": "round"}, "--diagonal-gait-shape must be one of portrait, wide, square"),
    ({"diagonal_gait_shape": "portrait", "fit": "tight"}, "drop --diagonal-gait-shape"),
])
def test_a_shape_it_cannot_frame_is_refused_before_any_clip(tmp_path, kw, error) -> None:
    with pytest.raises(SystemExit, match=error):
        _set(tmp_path, {}, **kw)


def test_video_set_cli_threads_the_diagonal_gait_shape(monkeypatch, tmp_path) -> None:
    seen = []
    monkeypatch.setattr(batch, "run_set", lambda **kw: seen.append(kw) or {"failed": []})
    argv = ["--base", f"back_diagonal={tmp_path / 'b.png'}", "--states", "run", "--out-dir", str(tmp_path)]
    assert batch.main([*argv, "--diagonal-gait-shape", "portrait"]) == 0 and seen[0]["diagonal_gait_shape"] == "portrait"
    assert batch.main(argv) == 0 and seen[1]["diagonal_gait_shape"] is None
    with pytest.raises(SystemExit):
        batch.main([*argv, "--diagonal-gait-shape", "tall"])
