# SPDX-License-Identifier: Apache-2.0
"""`video-canvas --fit tight` drops the empty rows around the subject and adds no room;
`video-frames --allow-subject-edge-contact` accepts the clipping that follows but still
refuses leftover key background at the edge."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video import batch as batch_mod
from sprite_gen.video import canvas as canvas_mod
from sprite_gen.video import frames as frames_mod
from tests.video.test_canvas_key_normalize import _frames

GREEN = (0, 255, 0)
IVORY = (248, 247, 242)
SUBJECT = (200, 40, 40)


def _still(tmp_path: Path, size: tuple[int, int], box: tuple[int, int, int, int], bg=GREEN, name="still.png") -> Path:
    im = Image.new("RGB", size, bg)
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            # a gradient, so a scaled or shifted subject would not compare equal
            im.putpixel((x, y), (SUBJECT[0] - (x - x0), SUBJECT[1] + (y - y0) % 50, SUBJECT[2]))
    path = tmp_path / name
    im.save(path)
    return path


def _tight(tmp_path: Path, still: Path, **kw) -> tuple[Image.Image, dict]:
    out = tmp_path / "canvas.png"
    report = canvas_mod.run_canvas(still, out, state="attack", shape=None, facing="right", headroom=None, lead=None, report_path=None, fit="tight", **kw)
    return Image.open(out).convert("RGB"), report


def _subject_box(canvas: Image.Image, bg=GREEN) -> tuple[int, int, int, int]:
    data = np.asarray(canvas, dtype=np.int16)
    mask = np.abs(data - np.array(bg, dtype=np.int16)).max(axis=2) > 0
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def test_a_long_low_subject_in_a_square_still_becomes_a_wide_frame_it_fills(tmp_path: Path) -> None:
    box = (10, 50, 190, 130)  # 180 x 80 inside a 200 x 200 still: most of the height is empty
    canvas, report = _tight(tmp_path, _still(tmp_path, (200, 200), box))
    assert report["fit"] == "tight" and report["shape"] == "wide"
    assert canvas.size == (200, round(200 / (16 / 9)))
    x0, y0, x1, y1 = _subject_box(canvas)
    assert (x1 - x0, y1 - y0) == (180, 80)  # never scaled
    assert y1 == canvas.height  # stands on the bottom edge
    assert (y1 - y0) / canvas.height > 0.7  # the subject fills the height; it was 0.4 of the still
    assert report["tight"]["subject_rows"] == [50, 130]


def test_an_upright_subject_in_a_square_still_keeps_the_square(tmp_path: Path) -> None:
    box = (70, 20, 130, 190)  # 60 x 170: already fills the height
    canvas, report = _tight(tmp_path, _still(tmp_path, (200, 200), box))
    assert report["shape"] == "square" and canvas.size == (200, 200)
    x0, y0, x1, y1 = _subject_box(canvas)
    assert (x0, x1, y1) == (70, 130, 200)  # width kept as drawn, moved down onto the bottom edge


def test_the_subject_pixels_are_the_stills_own(tmp_path: Path) -> None:
    box = (10, 50, 190, 130)
    still = _still(tmp_path, (200, 200), box)
    canvas, report = _tight(tmp_path, still)
    x0, y0, x1, y1 = _subject_box(canvas)
    original = np.asarray(Image.open(still).convert("RGB").crop(box))
    assert np.array_equal(np.asarray(canvas.crop((x0, y0, x1, y1))), original)


def test_headroom_is_a_small_share_of_the_subject(tmp_path: Path) -> None:
    box = (10, 50, 190, 130)
    _, report = _tight(tmp_path, _still(tmp_path, (200, 200), box))
    assert report["tight"]["headroom_px"] == round(80 * canvas_mod.TIGHT_HEADROOM)
    assert report["tight"]["kept_rows"] == [50 - report["tight"]["headroom_px"], 130]


def test_a_narrow_band_snaps_to_the_nearest_framing_by_adding_width(tmp_path: Path) -> None:
    box = (10, 5, 50, 195)  # 60 x 200 still, 40 x 190 subject: taller than 9:16
    canvas, report = _tight(tmp_path, _still(tmp_path, (60, 200), box))
    assert report["shape"] == "tall"
    kept = report["tight"]["kept_rows"]
    band_h = kept[1] - kept[0]
    assert canvas.size == (round(band_h * 9 / 16), band_h)
    # the still's full width is kept and centred; the subject keeps its place inside it
    assert _subject_box(canvas)[0] == (canvas.width - 60) // 2 + 10


@pytest.mark.parametrize("size,bottom,shape", [
    ((200, 200), 152, "square"),  # band 200/152 = 1.32, under the 1:1 / 16:9 midpoint (4:3)
    ((200, 200), 148, "wide"),    # 200/148 = 1.35, over it
    ((60, 100), 79, "square"),    # 60/79 = 0.76, over the 9:16 / 1:1 midpoint (3:4)
    ((60, 100), 81, "tall"),      # 60/81 = 0.74, under it
])
def test_the_framing_is_the_nearest_ratio_either_side_of_the_midpoints(tmp_path: Path, size, bottom, shape) -> None:
    # a subject from the top row has no headroom to add, so the band is exactly `bottom` rows
    _, report = _tight(tmp_path, _still(tmp_path, size, (5, 0, 20, bottom)))
    assert report["tight"]["kept_rows"] == [0, bottom]
    assert report["shape"] == shape


def test_a_white_base_is_trimmed_by_its_corner_colour(tmp_path: Path) -> None:
    box = (10, 50, 190, 130)
    canvas, report = _tight(tmp_path, _still(tmp_path, (200, 200), box, bg=IVORY), key="white")
    assert report["shape"] == "wide" and report["key"] is None
    assert _subject_box(canvas, IVORY)[3] == canvas.height


def test_tight_refuses_the_state_room_flags(tmp_path: Path) -> None:
    still = _still(tmp_path, (200, 200), (10, 50, 190, 130))
    for extra in ({"shape": "wide"}, {"headroom": 0.2}, {"lead": 0.1}, {"trail": 0.1}):
        kw = {"shape": None, "headroom": None, "lead": None, **extra}
        with pytest.raises(SystemExit, match="--fit tight"):
            canvas_mod.run_canvas(still, tmp_path / "c.png", state="attack", facing="right", report_path=None, fit="tight", **kw)


def test_a_still_with_no_subject_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "empty.png"
    Image.new("RGB", (64, 64), GREEN).save(empty)
    with pytest.raises(SystemExit, match="no subject"):
        _tight(tmp_path, empty)


def test_the_state_fit_is_unchanged_and_says_so(tmp_path: Path) -> None:
    still = _still(tmp_path, (200, 200), (70, 20, 130, 190))
    report = canvas_mod.run_canvas(still, tmp_path / "c.png", state="attack", shape=None, facing="right", headroom=None, lead=None, report_path=None)
    assert report["fit"] == "state" and report["shape"] == "wide" and "tight" not in report


# --- frames: the subject may leave a tight frame, leftover background may not ---------


def test_subject_contact_passes_when_allowed_and_stays_in_the_report(tmp_path: Path) -> None:
    files = _frames(tmp_path, residual=False, touch_top=True)
    report = frames_mod.key_frames(files, tmp_path / "keyed", key="green", allow_subject=True)
    assert report["edge_policy"] == "subject-allowed"
    assert report["edge_contacts"] and all(c["subject"] > 0 for c in report["edge_contacts"])


def test_leftover_background_still_fails_when_the_subject_is_allowed(tmp_path: Path) -> None:
    files = _frames(tmp_path, residual=True, touch_top=True)
    with pytest.raises(SystemExit, match="leftover chroma background") as info:
        frames_mod.key_frames(files, tmp_path / "keyed", key="green", allow_subject=True)
    assert "framed too tight" not in str(info.value)


def test_the_default_policy_is_unchanged(tmp_path: Path) -> None:
    files = _frames(tmp_path, residual=False, touch_top=True)
    with pytest.raises(SystemExit, match="framed too tight"):
        frames_mod.key_frames(files, tmp_path / "keyed", key="green")
    assert frames_mod.key_frames(files, tmp_path / "keyed2", key="green", check_edges=False)["edge_policy"] == "off"


def test_the_cli_takes_the_new_flags() -> None:
    import argparse

    frames_parser = argparse.ArgumentParser()
    frames_mod.add_arguments(frames_parser)
    assert frames_parser.parse_args(["--clip", "c", "--out-dir", "o", "--allow-subject-edge-contact"]).allow_subject_edge_contact is True
    canvas_parser = argparse.ArgumentParser()
    canvas_mod.add_arguments(canvas_parser)
    assert canvas_parser.parse_args(["--still", "s", "--out", "o"]).fit == "state"
    assert canvas_parser.parse_args(["--still", "s", "--out", "o", "--fit", "tight"]).fit == "tight"
    set_parser = argparse.ArgumentParser()
    batch_mod.add_arguments(set_parser)
    assert set_parser.parse_args(["--out-dir", "x", "--fit", "tight"]).fit == "tight"


# --- video-set: tight frames every item and lets its subject reach the edge ----------


def test_video_set_fit_tight_reaches_canvas_and_frames(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_canvas(base, out, *, state, shape, facing, headroom, lead, report_path, fit="state"):
        seen["fit"] = fit
        Image.new("RGB", (32, 18), GREEN).save(out)
        return {"fit": fit, "shape": "wide", "canvas": [32, 18], "offset": [0, 0]}

    def fake_frames(clip, out_dir, **kw):
        seen["allow_subject"] = kw.get("allow_subject_edge_contact")
        raise SystemExit("stop after frames")

    def video(image, prompt, out, report, **kw):
        Path(out).write_bytes(b"x")
        Path(report).write_text("{}")
        Path(kw["log"]).write_text("")
        return 0

    monkeypatch.setattr(batch_mod.facing_mod.vision, "grok_inspect", lambda *a, **kw: ("right", {}))
    monkeypatch.setattr(batch_mod.canvas_mod, "run_canvas", fake_canvas)
    monkeypatch.setattr(batch_mod.frames_mod, "run_frames", fake_frames)
    base = tmp_path / "base.png"
    Image.new("RGB", (32, 32), GREEN).save(base)
    payload = batch_mod.run_set(bases={"side": base}, states=["attack"], root=tmp_path / "set", character=None, duration=None,
                                resolution="480p", key="green", concurrency=1, force=False, gap=0.0, video_runner=video, fit="tight")
    assert seen == {"fit": "tight", "allow_subject": True}
    assert payload["failed"]
    with pytest.raises(SystemExit, match="--fit tight"):
        batch_mod.run_set(bases={"side": base}, states=["attack"], root=tmp_path / "set2", character=None, duration=None,
                          resolution="480p", key="green", concurrency=1, force=False, gap=0.0, video_runner=video, fit="tight", shape="wide")
