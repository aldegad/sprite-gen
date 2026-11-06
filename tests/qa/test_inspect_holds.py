# SPDX-License-Identifier: Apache-2.0
"""`inspect-holds`: the frames on which a figure shows the picture it showed on the frame before, read on drawn
figures. Each case draws a figure whose arms and legs swing a few pixels a frame, with frames shown again put in by
hand; the cases read it by its alpha, by a key, inside a still box, by a log of the cells, from an mp4, and from a
strip, and hold the list of holds meant (listed or not) and the gate. A figure drawn in a few pixels is read, specks
beside a figure are not, and a reading that reads no frame against the frame before is an error, whatever left it
nothing to read."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen.qa import holds

GREEN = (0, 255, 0)
W, H = 320, 200


def figure(pose: int) -> Image.Image:
    """A figure 60 px wide and 110 high, on a clear canvas just its size: a striped body and head, its arms and legs
    swinging 4 px a pose apart (each pose changes several per cent of its pixels)."""
    im = Image.new("RGBA", (90, 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    swing = (pose % 7) * 4 - 12
    d.ellipse((30, 2, 60, 32), fill=(230, 190, 150, 255), outline=(40, 30, 20, 255), width=2)
    d.rectangle((30, 32, 60, 80), fill=(60, 90, 200, 255), outline=(20, 20, 60, 255), width=2)
    for y in range(36, 80, 8):
        d.line((32, y, 58, y), fill=(200, 200, 60, 255), width=2)
    d.rectangle((20 + swing // 2, 36, 28 + swing // 2, 74), fill=(230, 190, 150, 255))
    d.rectangle((62 - swing // 2, 36, 70 - swing // 2, 74), fill=(230, 190, 150, 255))
    d.rectangle((33 + swing, 80, 43 + swing, 118), fill=(40, 40, 40, 255))
    d.rectangle((47 - swing, 80, 57 - swing, 118), fill=(40, 40, 40, 255))
    return im


def scene(poses: list[int], *, at: list[tuple[int, int]] | None = None, background: tuple[int, ...] | None = None,
          second: list[int] | None = None) -> list[Image.Image]:
    """One frame per pose: the figure drawn at `at` (default the same place), on a clear canvas or an opaque
    `background`; a `second` figure's poses drawn far to its right."""
    frames = []
    for k, pose in enumerate(poses):
        im = Image.new("RGBA", (W, H), (*background, 255) if background else (0, 0, 0, 0))
        x, y = at[k] if at else (40, 50)
        im.alpha_composite(figure(pose), (x, y))
        if second is not None:
            im.alpha_composite(figure(second[k] + 3), (210, 60))
        frames.append(im)
    return frames


def save(frames: list[Image.Image], folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for k, im in enumerate(frames):
        im.save(folder / f"{k:04d}.png")
    return folder


def read(*args: str) -> tuple[int, dict, str]:
    proc = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "inspect-holds", *args], capture_output=True,
                          encoding="utf-8")
    report = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, report, proc.stderr


def runs(report: dict, name: str = "figure", *, listed: bool = False) -> list[tuple[int, int]]:
    return [(r["from"], r["to"]) for r in report["table"][name]["listedRuns" if listed else "unlistedRuns"]]


# A figure that moves on every frame but three: frame 3 shows frame 2's picture, frames 7 and 8 frame 6's.
POSES = [0, 1, 2, 2, 3, 4, 5, 5, 5, 6, 7, 8]


def test_a_figure_shown_again_is_found_frame_by_frame(tmp_path: Path) -> None:
    code, report, err = read("--source", str(save(scene(POSES), tmp_path / "frames")))
    assert code == 0, err
    row = report["table"]["figure"]
    assert row["frames"] == len(POSES) and row["holds"] == 3 and row["unlisted"] == 3 and row["listed"] == 0
    assert runs(report) == [(3, 3), (7, 8)]
    assert report["read_by"] == "alpha" and report["unlisted"] == 3


def test_a_figure_that_moves_on_every_frame_holds_none(tmp_path: Path) -> None:
    code, report, err = read("--source", str(save(scene(list(range(12))), tmp_path / "frames")))
    assert code == 0, err
    assert report["table"]["figure"]["holds"] == 0 and report["unlisted"] == 0


def test_a_held_picture_the_camera_follows_is_still_a_hold(tmp_path: Path) -> None:
    """The camera follows the figure: its picture stands somewhere else on every frame, held or not, by up to 37 px."""
    at = [(10 + 17 * k + (k % 3), 40 + (k % 2) * 3) for k in range(len(POSES))]
    code, report, err = read("--source", str(save(scene(POSES, at=at), tmp_path / "frames")))
    assert code == 0, err
    assert runs(report) == [(3, 3), (7, 8)]


def test_two_figures_are_read_apart(tmp_path: Path) -> None:
    other = [0, 1, 2, 3, 4, 5, 6, 6, 7, 8, 8, 9]
    code, report, err = read("--source", str(save(scene(POSES, second=other), tmp_path / "frames")))
    assert code == 0, err
    assert sorted(report["table"]) == ["figure0", "figure1"]
    assert runs(report, "figure0") == [(3, 3), (7, 8)]
    assert runs(report, "figure1") == [(7, 7), (10, 10)]


def test_a_keyed_clip_is_read_by_its_key(tmp_path: Path) -> None:
    code, report, err = read("--source", str(save(scene(POSES, background=GREEN), tmp_path / "frames")), "--key", "green")
    assert code == 0, err
    assert report["read_by"] == "key" and runs(report) == [(3, 3), (7, 8)]


def test_opaque_frames_are_not_read_without_a_way_to_the_figure(tmp_path: Path) -> None:
    code, report, err = read("--source", str(save(scene(POSES, background=(128, 128, 128)), tmp_path / "frames")))
    assert code == 2 and not report
    assert "opaque" in err and "--key" in err and "--box" in err and "--log" in err


def test_a_box_reads_a_figure_over_a_still_background(tmp_path: Path) -> None:
    frames = scene(POSES, background=(128, 128, 128))
    code, report, err = read("--source", str(save(frames, tmp_path / "frames")), "--box", "30,40,110,140")
    assert code == 0, err
    assert report["read_by"] == "box" and runs(report) == [(3, 3), (7, 8)]


def test_a_log_reads_the_cells_by_name(tmp_path: Path) -> None:
    lines = [{"t": k / 60, "hero": f"run {p}", "figures": [f"stand {k // 4}", None], "kind": "run"}
             for k, p in enumerate(POSES)]
    log = tmp_path / "cells.jsonl"
    log.write_text("".join(json.dumps(line) + "\n" for line in lines))
    code, report, err = read("--log", str(log), "--log-figure", "hero", "--log-figure", "figures")
    assert code == 0, err
    assert report["read_by"] == "log" and sorted(report["table"]) == ["figure0", "hero"]
    assert runs(report, "hero") == [(3, 3), (7, 8)]
    assert runs(report, "figure0") == [(1, 3), (5, 7), (9, 11)]
    assert report["table"]["hero"]["unlistedRuns"][1]["drawings"] == ["run 5"]


def test_a_log_names_the_keys_that_hold_figures(tmp_path: Path) -> None:
    log = tmp_path / "cells.jsonl"
    log.write_text(json.dumps({"t": 0, "hero": "run 0"}) + "\n")
    code, _report, err = read("--log", str(log))
    assert code == 2 and "--log-figure" in err and "hero" in err


def test_a_listed_hold_is_listed_and_an_unlisted_one_fails_the_gate(tmp_path: Path) -> None:
    source = str(save(scene(POSES), tmp_path / "frames"))
    listed = tmp_path / "meant.holds.json"
    listed.write_text(json.dumps({"holds": [
        {"scene": "roof", "figure": "figure", "from": 7, "to": 8, "reason": "a stop the move means"},
        {"scene": "other", "figure": "figure", "from": 3, "to": 3, "reason": "another scene's"},
    ]}))
    code, report, err = read("--source", source, "--holds", str(listed), "--scene", "roof")
    assert code == 0, err
    row = report["table"]["figure"]
    assert (row["holds"], row["listed"], row["unlisted"]) == (3, 2, 1)
    assert runs(report) == [(3, 3)] and runs(report, listed=True) == [(7, 8)]
    assert row["listedRuns"][0]["reason"] == "a stop the move means"
    code, _report, err = read("--source", source, "--holds", str(listed), "--scene", "roof", "--gate")
    assert code == 1 and "1 frame" in err
    listed.write_text(json.dumps({"holds": [{"from": 3, "to": 8, "reason": "all of them meant"}]}))
    code, report, err = read("--source", source, "--holds", str(listed), "--gate")
    assert code == 0, err
    assert report["unlisted"] == 0 and report["table"]["figure"]["listed"] == 3


def test_a_list_is_read_as_its_writer_wrote_it(tmp_path: Path) -> None:
    """A list that names scenes is read for the scene named; a hold without a reason, or with its frames the wrong way
    round, refuses the list (as holds lists are read elsewhere)."""
    source = str(save(scene(POSES), tmp_path / "frames"))
    listed = tmp_path / "meant.holds.json"
    listed.write_text(json.dumps({"holds": [{"scene": "roof", "figure": "figure", "from": 3, "to": 3, "reason": "meant"}]}))
    code, _report, err = read("--source", source, "--holds", str(listed))
    assert code == 2 and "--scene" in err and "roof" in err
    listed.write_text(json.dumps({"holds": [{"from": 3, "to": 3, "reason": " "}]}))
    code, _report, err = read("--source", source, "--holds", str(listed))
    assert code == 2 and "reason" in err
    listed.write_text(json.dumps({"holds": [{"from": 4, "to": 3, "reason": "meant"}]}))
    code, _report, err = read("--source", source, "--holds", str(listed))
    assert code == 2 and "from" in err
    # A folder: every *.holds.json under it.
    (tmp_path / "lists" / "deep").mkdir(parents=True)
    (tmp_path / "lists" / "deep" / "a.holds.json").write_text(json.dumps({"holds": [{"from": 3, "to": 3, "reason": "meant"}]}))
    (tmp_path / "lists" / "b.holds.json").write_text(json.dumps({"holds": [{"from": 7, "to": 8, "reason": "meant too"}]}))
    code, report, err = read("--source", source, "--holds", str(tmp_path / "lists"), "--gate")
    assert code == 0, err
    assert report["table"]["figure"]["listed"] == 3


def strip(tmp_path: Path, cells: list[Image.Image], meta: dict) -> Path:
    w, h = cells[0].size
    sheet = Image.new("RGBA", (w * len(cells), h), (0, 0, 0, 0))
    for k, cell in enumerate(cells):
        sheet.alpha_composite(cell, (k * w, 0))
    sheet.save(tmp_path / "walk.strip.png")
    path = tmp_path / "walk.strip.json"
    path.write_text(json.dumps({"frames": len(cells), "w": w, "h": h, "delay_ms": 41.67, **meta}))
    return path


def test_a_strip_reads_its_own_holds_as_listed(tmp_path: Path) -> None:
    cells = [figure(p) for p in [0, 1, 2, 3, 3, 3, 3, 4, 5, 5, 6, 7]]
    path = strip(tmp_path, cells, {"loop": False, "holds": [
        {"from": 3, "to": 6, "kind": "motion-hold", "reason": "held 4 cells, longer than a drawing on threes"}]})
    code, report, err = read("--source", str(path), "--gate")
    assert code == 1, err
    row = report["table"]["figure"]
    assert runs(report, listed=True) == [(4, 6)] and runs(report) == [(9, 9)]
    assert row["listedRuns"][0]["reason"].startswith("held 4 cells")


def test_a_looping_strip_is_read_across_its_wrap(tmp_path: Path) -> None:
    """A loop plays its last cell then its first: a first cell that shows the last cell's picture is shown again."""
    cells = [figure(p) for p in [0, 1, 2, 3, 4, 5, 0]]
    code, report, err = read("--source", str(strip(tmp_path, cells, {"loop": True})))
    assert code == 0, err
    assert runs(report) == [(0, 0)] and report["wrap"] is True
    code, report, err = read("--source", str(strip(tmp_path, cells, {"loop": False})))
    assert code == 0, err
    assert runs(report) == [] and report["wrap"] is False


def test_a_range_reads_its_frames_and_the_one_before(tmp_path: Path) -> None:
    code, report, err = read("--source", str(save(scene(POSES), tmp_path / "frames")), "--frames", "7-11")
    assert code == 0, err
    assert runs(report) == [(7, 8)] and report["table"]["figure"]["frames"] == 5
    assert report["first_frame"] == 7


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_an_mp4_is_read_by_its_key(tmp_path: Path) -> None:
    """A keyed clip encoded as a video model's is (libx264, crf 18): the frames shown again are found through it."""
    folder = save([im.convert("RGB").convert("RGBA") for im in scene(POSES, background=GREEN)], tmp_path / "frames")
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-framerate", "24", "-i", str(folder / "%04d.png"), "-c:v", "libx264",
                    "-crf", "18", "-pix_fmt", "yuv420p", str(clip)], check=True)
    code, report, err = read("--source", str(clip), "--key", "green")
    assert code == 0, err
    assert runs(report) == [(3, 3), (7, 8)] and report["table"]["figure"]["frames"] == len(POSES)


def test_the_python_reading_matches_the_command(tmp_path: Path) -> None:
    frames = scene(POSES)
    report = holds.inspect(holds.alpha_figures(frames), first=0)
    assert [(r["from"], r["to"]) for r in report["table"]["figure"]["unlistedRuns"]] == [(3, 3), (7, 8)]


def test_a_report_is_written_where_asked(tmp_path: Path) -> None:
    out = tmp_path / "out" / "holds.json"
    code, report, err = read("--source", str(save(scene(POSES), tmp_path / "frames")), "--report", str(out))
    assert code == 0, err
    assert json.loads(out.read_text()) == report and report["kind"] == holds.REPORT_KIND


# --- a figure in a few pixels, specks, and a reading of nothing -------------------------------------------------------

BLUE = (70, 130, 220, 255)


def block(w: int, h: int, *, size: tuple[int, int] = (24, 24), at: tuple[int, int] = (8, 6)) -> Image.Image:
    """A canvas of `size`, clear but for a solid block w x h at `at`."""
    im = Image.new("RGBA", size)
    im.paste(BLUE, (at[0], at[1], at[0] + w, at[1] + h))
    return im


def dotted(im: Image.Image, dots: list[tuple[int, int, int]]) -> Image.Image:
    """`im` with a dot of 1, 2 or 3 px (a speck) at each (x, y, px), the same on every frame it is put on."""
    im = im.copy()
    for x, y, px in dots:
        im.paste((200, 50, 50, 255), (x, y, x + px, y + 1))
    return im


@pytest.mark.parametrize("w, h", [(7, 9), (8, 8)], ids=["63px", "64px"])
def test_a_figure_drawn_in_a_few_pixels_is_read(tmp_path: Path, w: int, h: int) -> None:
    """One block, 7 x 9 (63 px) or 8 x 8 (64 px), shown three times. Before, a figure under 64 px was a speck: 63 px read
    no figure, an empty table, and the gate passed (exit 0) on two frames shown again; the only figure of a frame is
    read now, however small, and 64 px reads as it did."""
    code, report, err = read("--source", str(save([block(w, h)] * 3, tmp_path / "frames")), "--gate")
    assert code == 1, err
    assert report["unlisted"] == 2 and report["inspected"] == 1 and runs(report) == [(1, 2)]
    assert (report["table"]["figure"]["frames"], report["table"]["figure"]["steps"]) == (3, 2)
    assert "2 frames show the picture of the frame before" in err and "figure 1-2" in err


def test_specks_beside_a_figure_are_still_left_out(tmp_path: Path) -> None:
    """Dots of 1, 2 and 3 px, far from the figure and the same on every frame (each would show its picture again on
    every frame, were it a figure), beside the striped figure and beside a figure of 63 px: neither reads a dot."""
    dots = [(250, 20, 1), (270, 100, 2), (300, 180, 3)]
    code, report, err = read("--source", str(save([dotted(im, dots) for im in scene(POSES)], tmp_path / "big")))
    assert code == 0, err
    assert sorted(report["table"]) == ["figure"] and runs(report) == [(3, 3), (7, 8)]
    small = dotted(block(7, 9, size=(48, 32)), [(30, 4, 1), (40, 20, 2), (24, 28, 3)])
    code, report, err = read("--source", str(save([small] * 3, tmp_path / "small")), "--gate")
    assert code == 1, err
    assert sorted(report["table"]) == ["figure"] and runs(report) == [(1, 2)]


def test_two_figures_drawn_in_a_few_pixels_are_both_read(tmp_path: Path) -> None:
    """Two blocks of about 40 px apart: the left one shown four times, the right one a row taller each frame. A part
    half the frame's largest or more is a figure, so both are read: the left holds on 1-3, the right on none."""
    frames = []
    for k in range(4):
        im = block(6, 7, size=(48, 24), at=(4, 6))
        im.paste((220, 130, 70, 255), (30, 6, 36, 11 + k))
        frames.append(im)
    code, report, err = read("--source", str(save(frames, tmp_path / "frames")))
    assert code == 0, err
    assert sorted(report["table"]) == ["figure0", "figure1"] and report["inspected"] == 2
    assert runs(report, "figure0") == [(1, 3)] and runs(report, "figure1") == []


def test_each_figure_s_steps_and_the_figures_inspected_are_counted(tmp_path: Path) -> None:
    source = str(save(scene(POSES), tmp_path / "frames"))
    _code, report, _err = read("--source", source)
    assert report["inspected"] == 1 and report["table"]["figure"]["steps"] == len(POSES) - 1
    _code, report, _err = read("--source", source, "--frames", "7-11")
    assert report["table"]["figure"]["steps"] == 5  # 7..11, each against the frame before
    cells = [figure(p) for p in [0, 1, 2, 3, 4, 5, 0]]
    _code, report, _err = read("--source", str(strip(tmp_path, cells, {"loop": True})))
    assert report["table"]["figure"]["steps"] == 7  # six steps and the wrap


def test_frames_that_show_no_figure_are_no_reading(tmp_path: Path) -> None:
    """Three clear frames: no figure, so no frame read against the frame before. Before, an empty table and unlisted 0
    passed the gate (exit 0, nothing on stderr); it is an error now, with or without --gate, its report printed and
    written with `inspected` 0 and one line on stderr."""
    source = str(save([Image.new("RGBA", (24, 24))] * 3, tmp_path / "frames"))
    out = tmp_path / "holds.json"
    for gate in (["--gate"], []):
        code, report, err = read("--source", source, "--report", str(out), *gate)
        assert code == 2
        assert (report["inspected"], report["unlisted"], report["table"]) == (0, 0, {})
        assert json.loads(out.read_text()) == report
        assert err.splitlines() == ["inspect-holds: no frame was read against the frame before: no figure on frames 0-2 "
                                    "(no pixel with alpha over 8)"]


def test_a_box_that_holds_nothing_on_a_frame_shows_no_picture_there(tmp_path: Path) -> None:
    """A figure in the box on frames 0-2, then gone: before, two clear crops were one picture shown again (holds at 4
    and 5); a frame whose box holds nothing now shows no figure there."""
    frames = scene([0, 1, 2]) + [Image.new("RGBA", (W, H))] * 3
    code, report, err = read("--source", str(save(frames, tmp_path / "frames")), "--box", "30,40,110,140", "--gate")
    assert code == 0, err
    row = report["table"]["figure"]
    assert (row["frames"], row["steps"], row["holds"]) == (3, 2, 0) and report["inspected"] == 1


def _nothing(tmp_path: Path, case: str) -> list[str]:
    """The arguments of a reading that is left nothing to read, one way or another."""
    if case == "no frame":
        (tmp_path / "empty").mkdir()
        return ["--source", str(tmp_path / "empty")]
    if case == "one frame":
        return ["--source", str(save([block(8, 8)], tmp_path / "one"))]
    if case == "frames past --frames":
        return ["--source", str(save([block(8, 8)] * 3, tmp_path / "three")), "--frames", "3-5"]
    if case == "alpha under ALPHA_MIN":
        faint = Image.new("RGBA", (24, 24))
        faint.paste((70, 130, 220, holds.ALPHA_MIN), (4, 4, 20, 20))
        return ["--source", str(save([faint] * 3, tmp_path / "faint"))]
    if case == "keyed to nothing":
        return ["--source", str(save([Image.new("RGBA", (64, 64), (*GREEN, 255))] * 3, tmp_path / "green")), "--key", "green"]
    if case == "box over clear frames":
        return ["--source", str(save([Image.new("RGBA", (24, 24))] * 3, tmp_path / "clear")), "--box", "2,2,10,10"]
    if case == "box past the edge":
        return ["--source", str(save([Image.new("RGBA", (24, 24), (128, 128, 128, 255))] * 3, tmp_path / "grey")),
                "--box", "40,40,10,10"]
    log = tmp_path / "cells.jsonl"
    if case == "log of no cell":
        log.write_text("".join(json.dumps({"t": k, "hero": None}) + "\n" for k in range(5)))
        return ["--log", str(log), "--log-figure", "hero"]
    if case == "log past --frames":
        log.write_text("".join(json.dumps({"t": k, "hero": "run 0"}) + "\n" for k in range(5)))
        return ["--log", str(log), "--log-figure", "hero", "--frames", "50-60"]
    path = strip(tmp_path, [figure(p) for p in range(4)], {"loop": False})
    if case == "strip past --frames":
        return ["--source", str(path), "--frames", "10-12"]
    assert case == "strip past --frames, in a box"
    return ["--source", str(path), "--frames", "10-12", "--box", "0,0,10,10"]


NOTHING_CASES = {
    "no frame": "no *.png frames",
    "one frame": "one frame read (frame 0), and no frame before it to read it against",
    "frames past --frames": "no frame in --frames 3-5: it has 3 frames (0-2)",
    "alpha under ALPHA_MIN": "no figure on frames 0-2 (no pixel with alpha over 8)",
    "keyed to nothing": "no figure on frames 0-2 once keyed with --key green (no pixel with alpha over 8)",
    "box over clear frames": "the box 2,2,10,10 holds no pixel with alpha over 8 on frames 0-2 (the frames are 24x24)",
    "box past the edge": "the box 40,40,10,10 holds no pixel with alpha over 8 on frames 0-2 (the frames are 24x24)",
    "log of no cell": "the log names no cell for hero on frames 0-4",
    "log past --frames": "no frame in --frames 50-60: it has 5 lines (0-4)",
    "strip past --frames": "no frame in --frames 10-12: it has 4 cells (0-3)",
    "strip past --frames, in a box": "no frame in --frames 10-12: it has 4 cells (0-3)",
}


@pytest.mark.parametrize("case", list(NOTHING_CASES))
def test_a_reading_left_nothing_to_read_is_an_error_whatever_left_it_so(tmp_path: Path, case: str, capsys) -> None:
    """Every way a reading is left no frame to read against the frame before (the input, the alpha floor, the key, the
    box, the log, the frames asked) ends the same way: exit 2 under --gate, the report with `inspected` 0, and one line
    on stderr saying why. Before, all but the first and the `strip past --frames` (refused as opaque) passed the gate
    with an empty reading (`frames past --frames` read only the frame before 3), and the boxes failed it on frames shown
    again that showed nothing."""
    code = holds.main([*_nothing(tmp_path, case), "--gate"])
    out, err = capsys.readouterr()
    assert code == 2
    report = json.loads(out)
    assert (report["inspected"], report["unlisted"]) == (0, 0)
    assert len(err.splitlines()) == 1 and err.startswith(f"inspect-holds: {holds.NOTHING}: ")
    assert err.rstrip().endswith(NOTHING_CASES[case])


def test_the_python_reading_raises_where_it_reads_nothing(tmp_path: Path) -> None:
    with pytest.raises(holds.NothingRead) as caught:
        holds.inspect(holds.alpha_figures([Image.new("RGBA", (24, 24))] * 3))
    assert caught.value.report == {"unlisted": 0, "inspected": 0, "table": {}}
    with pytest.raises(holds.NothingRead) as caught:
        holds.read(source=save([Image.new("RGBA", (24, 24))] * 3, tmp_path / "clear"))
    assert caught.value.report["inspected"] == 0 and caught.value.report["read_by"] == "alpha"
    assert str(caught.value).startswith(holds.NOTHING)
