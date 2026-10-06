# SPDX-License-Identifier: Apache-2.0
"""A fleck far from the body is not the subject touching the edge, and a clip's first frames that reframe a
small subject are not part of its walk.

* `video-frames` erases a speck — a small piece far from the body — before the edge check reads the frame.
* `video-loop` reads a walk's lead-in (the first frames off the walk's size) and searches the clip after it;
  what it finds, and what it refuses, is said in the clip's own frame numbers. The gait fallback scales back
  by the size read one cycle on, as the hold does. A cut after a lead-in reads the standing height on its
  own first frame.
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import align, canvas, frames as frames_mod, gait_fallback, loop
from tests.video import test_gait_fallback as walks

MAGENTA = (255, 0, 255)
BODY = (180, 60, 30)
FLECK = (255, 180, 220)  # a pale pink the magenta matte keeps, not the key's hue


# --- the key stage's specks ---------------------------------------------------------------------------------


def _raw(tmp_path: Path, n: int, paint) -> list[Path]:
    """`n` frames of a 30 x 70 body on magenta, `paint(k, image)` adding to frame k."""
    d = tmp_path / "raw"
    d.mkdir()
    files = []
    for k in range(n):
        image = Image.new("RGB", (120, 100), MAGENTA)
        image.paste(BODY, (20, 20, 50, 90))
        paint(k, image)
        files.append(d / f"frame-{k:04d}.png")
        image.save(files[-1])
    return files


def test_a_fleck_far_from_the_body_is_erased_before_the_edge_check(tmp_path):
    # Five pale pixels crossing the right edge band in one frame, 66 px from a body 70 px tall: the edge
    # check read them as the subject framed too tight, and the clip was sent back for a wider canvas.
    def fleck(k, image):
        if k == 1:
            image.paste(FLECK, (116, 50, 119, 51))
            image.paste(FLECK, (117, 51, 119, 52))
    files = _raw(tmp_path, 3, fleck)
    report = frames_mod.key_frames(files, tmp_path / "keyed", key="magenta")
    assert report["edge_contacts"] == [] and report["edge_policy"] == "refuse"
    assert [row["specks"] for row in report["rows"]] == [0, 1, 0]
    assert report["specks"]["dropped"] == 1 and report["specks"]["frames"] == 1
    keyed = Image.open(tmp_path / "keyed" / "frame-0001.png").convert("RGBA")
    assert keyed.getpixel((117, 50))[3] == 0 and keyed.getpixel((35, 50))[3] == 255
    assert all(row["edge"] == {"top": 0, "left": 0, "right": 0} for row in report["rows"])


def test_small_pieces_near_the_body_and_a_large_one_far_from_it_are_the_subjects(tmp_path):
    # A small shadow drawn 2 px under the feet, a loose pixel one off the outline (both under the speck
    # size, 21 px here), and a block far from the body but too large to be a speck: all kept.
    def pieces(k, image):
        image.paste((40, 40, 40), (30, 92, 40, 93))
        image.putpixel((51, 40), (200, 50, 50))
        image.paste((60, 160, 60), (80, 30, 100, 60))
    files = _raw(tmp_path, 2, pieces)
    report = frames_mod.key_frames(files, tmp_path / "keyed", key="magenta")
    assert [row["specks"] for row in report["rows"]] == [0, 0] and report["specks"]["dropped"] == 0
    keyed = Image.open(tmp_path / "keyed" / "frame-0000.png").convert("RGBA")
    assert keyed.getpixel((35, 92))[3] > 0 and keyed.getpixel((51, 40))[3] > 0 and keyed.getpixel((90, 45))[3] > 0


def test_the_loop_cut_reads_its_loose_pieces_as_before():
    """`video-loop`'s own pass on a cut that is not motion-reviewed: every island of solid pixels (alpha over
    16, side by side) under the speck size, wherever it is — the outline's islands and a drawn shadow too."""
    image = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    image.paste((200, 0, 0, 255), (10, 5, 40, 45))  # 1200 px: a speck is under 12
    image.paste((40, 40, 40, 255), (12, 48, 22, 49))  # a 10 px shadow, 3 px under the body
    image.putpixel((41, 20), (200, 0, 0, 255))  # one transparent column off the body
    image.putpixel((42, 21), (200, 0, 0, 255))  # ... joined to the pixel above only at a corner
    cut, dropped = frames_mod.drop_specks(image.copy(), alpha_over=16, diagonal=False, apart=0)
    assert dropped == 3 and all(cut.getpixel(xy)[3] == 0 for xy in ((15, 48), (41, 20), (42, 21)))
    assert cut.getpixel((20, 20))[3] == 255
    # The key stage keeps all three: within a tenth of the body's height of it.
    keyed, kept = frames_mod.drop_specks(image)
    assert kept == 0 and keyed is image


def test_a_motion_auto_loop_of_a_clip_with_a_fleck_is_cut_without_it(tmp_path):
    # The fleck drifts across the background far from the walker, never into the edge band: keyed and cut
    # with `--anchor motion-auto`, which never cleans a frame, the cells are only as wide as the walker.
    raw = tmp_path / "raw"
    raw.mkdir()
    files = []
    for k in range(73):
        image = Image.new("RGBA", (280, 180), MAGENTA + (255,))
        image.alpha_composite(walks.walker(k))
        x = 250 - 2 * (k % 25)
        image = image.convert("RGB")
        image.paste(FLECK, (x, 40, x + 3, 42))
        files.append(raw / f"frame-{k:04d}.png")
        image.save(files[-1])
    report = frames_mod.key_frames(files, tmp_path / "keyed", key="magenta")
    assert all(row["specks"] == 1 for row in report["rows"])
    out = loop.run_loop(tmp_path / "keyed", tmp_path / "out", fps=24.0, state="walk", min_len=None, max_len=None,
                        n_out=None, seam_max=loop.SEAM_RATIO_MAX, name="w", report_path=None, anchor="motion-auto",
                        repair="off")
    walker_width = walks.walker(0).getchannel("A").getbbox()
    assert out["strip"]["w"] <= walker_width[2] - walker_width[0] + 2 * 8 + 8  # the walker, its margins, a little sway


# --- a walk's lead-in -----------------------------------------------------------------------------------------


def zoomed(k: int, *, period: int = 24, frames: int = 9, start: float = 0.63) -> Image.Image:
    """The walker, filmed small and reframed over its first `frames` frames to its walking size: scaled from
    `start` about its feet, easing out (x1.59 over 0.4 s at the defaults)."""
    image = walks.walker(k, period=period)
    t = min(1.0, k / frames)
    s = start + (1 - start) * (1 - (1 - t) ** 2)
    if s >= 1:
        return image
    ax, ay = walks.FOOT
    return image.convert("RGBa").transform(image.size, Image.Transform.AFFINE, (1 / s, 0, ax * (1 - 1 / s), 0, 1 / s, ay * (1 - 1 / s)),
                                           resample=Image.Resampling.BICUBIC).convert("RGBA")


def test_a_lead_in_is_the_first_frames_off_the_walks_size():
    lead = gait_fallback.lead_in([zoomed(k) for k in range(73)], min_lag=12, max_lag=36)
    assert 4 <= lead["frames"] <= 7 and lead["first_off"] < -0.3 and 0.45 < lead["height_change"] < 0.65
    shrinking = gait_fallback.lead_in([zoomed(k, start=1.5) for k in range(73)], min_lag=12, max_lag=36)
    assert shrinking["frames"] >= 3 and shrinking["first_off"] > 0.3 and shrinking["height_change"] < -0.25
    # A walk, a walk toward the camera, a first pose that settles into the walk: none has a lead-in.
    for clip in ([walks.walker(k) for k in range(73)], [walks.walker(k, grow=0.2) for k in range(73)],
                 [walks.settling(k) for k in range(73)]):
        found = gait_fallback.lead_in(clip, min_lag=12, max_lag=36)
        assert found["frames"] == 0 and abs(found["first_off"]) < gait_fallback.LEAD_IN_MIN
    short = gait_fallback.lead_in([zoomed(k) for k in range(20)], min_lag=12, max_lag=36)
    assert short["frames"] == 0 and "too short" in short["why"]


def test_the_search_reads_the_walk_after_its_lead_in(tmp_path):
    # Searched as filmed, the reframing hides the walk's repeat and the gait fallback fails too.
    code, report, _ = walks.run(tmp_path, [zoomed(k) for k in range(73)], "--size-hold", "off")
    assert code == 0
    lead = report["lead_in"]
    assert 4 <= lead["frames"] <= 7 and lead["search_from"] == lead["frames"]
    cycle = report["cycle"]
    assert abs(cycle["length"] - 24) <= 1 and "gait_fallback" not in report
    # Every frame number is the clip's own: a caller cuts again from them on the whole clip.
    assert cycle["start"] >= lead["frames"] and all(row["start"] >= lead["frames"] for row in cycle["candidates"])
    assert report["window"] == list(loop.profile_for("walk").window(73 - lead["frames"], 24.0))


def test_a_refusal_after_a_lead_in_names_windows_in_the_clips_frames(tmp_path):
    # Too slow a walk to repeat: refused, with the windows measured — every one after the lead-in, numbered
    # as the clip is, which `--cycle fixed --start` cuts on.
    code, report, _ = walks.run(tmp_path, [zoomed(k, period=200) for k in range(73)], "--size-hold", "off")
    assert str(code).startswith("video-loop: no periodic cycle found")
    lead = report["lead_in"]["frames"]
    assert lead >= 4
    starts = [row["start"] for row in report["cycle"]["candidates"]]
    assert starts and min(starts) >= lead


def test_the_gait_fallback_scales_back_by_the_size_read_one_cycle_on(tmp_path):
    # A walker that settles from its standing first pose and grows 5 %: a line through every frame reads
    # under 3 % and the frames were searched as filmed; one cycle on, the growth is read and scaled back.
    frames = [walks.settling(k, grow=0.05) for k in range(73)]
    assert gait_fallback.scale_drift(frames)["drift"] < gait_fallback.SCALE_DRIFT_MIN
    code, report, output = walks.run(tmp_path, frames, "--size-hold", "off")
    assert code == 0 and abs(report["cycle"]["length"] - 24) <= 1
    fallback = report["gait_fallback"]
    assert fallback["scale_undone"] is True and fallback["scale_drift"]["method"] == "one-cycle-on"
    assert fallback["scale_drift"]["drift"] > gait_fallback.SCALE_DRIFT_MIN
    cells = sorted((output / "cycle").glob("frame-*.png"))
    boxes = gait_fallback.subject_boxes([Image.open(p).convert("RGBA") for p in cells])
    assert np.ptp(boxes[:, 3] - boxes[:, 1]) <= 2


def _height(path: Path) -> int:
    box = Image.open(path).convert("RGBA").getchannel("A").point(lambda v: 255 if v >= 8 else 0).getbbox()
    return box[3] - box[1]


def _fixed(tmp_path: Path, clip: list[Image.Image], start: int) -> tuple[dict, dict, Path]:
    keyed = tmp_path / "keyed"
    keyed.mkdir()
    for k, image in enumerate(clip):
        image.save(keyed / f"{k:03}.png")
    out = loop.run_loop(keyed, tmp_path / "out", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None,
                        seam_max=1000.0, name="w", report_path=None, cycle_mode="fixed", start=start, length=24,
                        body_height=120, anchor="body", repair="off")
    return out, json.loads((tmp_path / "out" / "w.strip.json").read_text()), keyed


def test_a_cut_after_a_lead_in_reads_the_standing_height_on_its_own_first_frame(tmp_path):
    # Read on the first frame, filmed small, the standing height scaled the walk up by half again.
    out, meta, keyed = _fixed(tmp_path, [zoomed(k) for k in range(73)], 12)
    assert out["lead_in"]["frames"] >= 4 and out["lead_in"]["search_from"] == 0  # a named cut is the caller's frames
    assert meta["body_ref"] == "cut-first-frame" and meta["body_ref_frame"] == 12
    assert meta["body_src_h"] == _height(keyed / "012.png") > _height(keyed / "000.png") + 30
    assert meta["scale"] == round(120 / meta["body_src_h"], 4)
    # `video-cycle-align` rebuilds the strip at the same standing height.
    for name in ("a", "b"):
        shutil.copytree(tmp_path / "out", tmp_path / name)
    align.align_set([tmp_path / "a", tmp_path / "b"], between="nearest")
    aligned = json.loads((tmp_path / "a" / "w.strip.json").read_text())
    assert (aligned["body_ref"], aligned["body_ref_frame"], aligned["body_src_h"], aligned["scale"]) == \
        ("cut-first-frame", 12, meta["body_src_h"], meta["scale"])


def test_a_cut_of_a_clip_with_no_lead_in_reads_its_first_frame_as_before(tmp_path):
    out, meta, keyed = _fixed(tmp_path, [walks.walker(k) for k in range(73)], 12)
    assert out["lead_in"]["frames"] == 0
    assert meta["body_ref"] == "first-frame" and "body_ref_frame" not in meta
    assert meta["body_src_h"] == _height(keyed / "000.png")
