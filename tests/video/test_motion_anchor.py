"""Behavior of explicit XY drift correction on synthetic, fully known motion."""
import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import batch, loop, motion_anchor as motion

REGIONS = [(55, 27, 86, 60), (48, 61, 94, 110)]


def walker(k, *, drift=True):
    dx = k if drift else 0
    dy = round(k / 4) if drift else 0
    bob = round(3 * math.sin(2 * math.pi * k / 24))
    im = Image.new("RGBA", (180, 180))
    draw = ImageDraw.Draw(im)
    x, y = dx, dy + bob
    draw.rectangle((60+x, 32+y, 80+x, 54+y), fill=(200, 130, 70, 255))
    draw.rectangle((72+x, 38+y, 76+x, 42+y), fill=(15, 20, 30, 255))
    draw.rectangle((53+x, 65+y, 88+x, 105+y), fill=(30, 90, 190, 255))
    draw.rectangle((65+x, 70+y, 70+x, 100+y), fill=(240, 210, 80, 255))
    leg = round(10 * math.sin(2 * math.pi * k / 24))
    draw.rectangle((58+x+leg, 106+y, 65+x+leg, 143+y), fill=(50, 60, 70, 255))
    draw.rectangle((79+x-leg, 106+y, 85+x-leg, 143+y), fill=(90, 40, 80, 255))
    draw.point((60+x+leg, 110+y+k % 12), fill=(240, 240, 240, 255))
    return im


@pytest.fixture(scope="module")
def corrected():
    frames = [walker(k) for k in range(24)]
    return frames, motion.correct_motion(frames, REGIONS, coarse_dx=loop.body_wrap_offset(frames, search=64))


def test_removes_xy_drift_but_retains_bob_and_leg_motion(corrected):
    frames, (output, report) = corrected
    assert report["endpoint_xy"] == [-23, -6]
    left, top, _, _ = report["padding_ltrb"]
    for k, (frame, (dx, dy)) in enumerate(zip(output, report["shifts_xy"])):
        # The head follows the original periodic bob, within integer quantization.
        pixels = np.asarray(frame)
        head = np.all(pixels == (200, 130, 70, 255), axis=2)
        yy, xx = np.nonzero(head)
        assert xx.min() == 60 + left
        assert abs(yy.min() - top - 32 - round(3 * math.sin(2 * math.pi * k / 24))) <= 1
        # Every source pixel, including moving legs, survives one translation.
        crop = frame.crop((left+dx, top+dy, left+dx+180, top+dy+180))
        np.testing.assert_array_equal(np.asarray(crop), np.asarray(frames[k]))
        assert pixels[:, :, 3].sum() == np.asarray(frames[k])[:, :, 3].sum()


def test_padding_preserves_pixels_at_all_canvas_edges():
    frames = [walker(k) for k in range(6)]
    for im in frames:
        for xy in [(0, 0), (179, 0), (0, 179), (179, 179)]:
            im.putpixel(xy, (201, 51, 101, 128))
        im.putpixel((1, 1), (123, 45, 67, 0))
    output, report = motion.correct_motion(frames, REGIONS, coarse_dx=-5)
    left, top, _, _ = report["padding_ltrb"]
    for source, target, (dx, dy) in zip(frames, output, report["shifts_xy"]):
        np.testing.assert_array_equal(np.asarray(target.crop((left+dx, top+dy, left+dx+180, top+dy+180))), np.asarray(source))


@pytest.mark.parametrize("regions,error", [
    ([(0, 0, 10, 10), REGIONS[1]], "no foreground"),
    ([(61, 45, 69, 50), REGIONS[1]], "no measurable texture"),
    ([(-1, 0, 10, 10), REGIONS[1]], "outside"),
    ([(1.5, 0, 10, 10), REGIONS[1]], "integer"),
    ([REGIONS[0]], "exactly two"),
])
def test_invalid_regions_fail_explicitly(regions, error):
    with pytest.raises(ValueError, match=error):
        motion.correct_motion([walker(0)] * 6, regions, coarse_dx=0)


def test_search_boundary_and_short_cycle_are_errors():
    with pytest.raises(ValueError, match="coarse match reached"):
        motion.correct_motion([walker(0)] * 6, REGIONS, coarse_dx=64)
    with pytest.raises(ValueError, match="at least six"):
        motion.correct_motion([walker(0)] * 5, REGIONS, coarse_dx=0)
    reference = motion._references(motion._features(walker(0)), REGIONS)
    # Best match lands exactly at dx=-14, the bounded fine search edge.
    shifted = Image.new("RGBA", (180, 180))
    shifted.paste(walker(0), (14, 0))
    with pytest.raises(ValueError, match="search boundary"):
        motion._register(reference, motion._features(shifted), (0, 0))


def test_cli_writes_corrected_cycle_and_gates_rendered_cells(tmp_path, corrected):
    source, (expected, report) = corrected
    keyed = tmp_path / "keyed"
    keyed.mkdir()
    for k, im in enumerate(source):
        im.save(keyed / f"{k:03}.png")
    out = tmp_path / "output"
    args = ["--frames-dir", str(keyed), "--out-dir", str(out), "--state", "walk",
            "--cycle", "fixed", "--start", "0", "--length", "24", "--anchor", "motion",
            "--anchor-region", "55,27,86,60", "--anchor-region", "48,61,94,110"]
    assert loop.main(args) == 0
    result = json.loads((out / "loop.loop.report.json").read_text())
    assert result["status"] == "passed"
    assert result["cycle"]["start"] == 0 and result["cycle"]["length"] == 24
    assert result["cycle"]["kind"] == "fixed"
    assert result["motion_anchor"] == report
    assert result["seam_measurement"] == "rendered-cells"
    assert result["scrubbed_rgb_pixels"] == result["specks_dropped"] == 0
    assert result["resampled_seam_ratio"] <= 2
    for path, im in zip(sorted((out / "cycle").glob("*.png")), expected):
        np.testing.assert_array_equal(np.asarray(Image.open(path)), np.asarray(im))
    # Changing the limit still refuses the output; motion mode never skips the gate.
    with pytest.raises(SystemExit, match="loop seam ratio"):
        loop.main(args + ["--seam-max", "0.001"])
    assert json.loads((out / "loop.loop.report.json").read_text())["status"] == "failed"


def test_contract_refused_before_reading_files_or_generating(tmp_path):
    with pytest.raises(SystemExit, match="requires --cycle fixed"):
        loop.main(["--frames-dir", "/missing", "--out-dir", str(tmp_path), "--anchor", "motion"])
    with pytest.raises(ValueError, match="requires --anchor motion"):
        motion.validate_request("body", "fixed", 24, REGIONS)
    with pytest.raises(ValueError, match="two --anchor-region"):
        motion.validate_request("motion", "fixed", 24, None)
    with pytest.raises(SystemExit, match="reviewed regions"):
        batch.run_set(bases={}, states=["walk"], root=tmp_path / "batch", character=None,
                      duration=3, resolution="720p", key="green", concurrency=1,
                      force=False, gap=0, anchor="motion")
    assert not (tmp_path / "batch").exists()


def test_failed_registration_writes_a_report_before_output(tmp_path):
    keyed = tmp_path / "keyed"
    keyed.mkdir()
    for k in range(6):
        walker(k).save(keyed / f"{k}.png")
    out = tmp_path / "result"
    with pytest.raises(SystemExit, match="no foreground"):
        loop.main(["--frames-dir", str(keyed), "--out-dir", str(out), "--cycle", "fixed",
                   "--start", "0", "--length", "6", "--anchor", "motion",
                   "--anchor-region", "0,0,10,10", "--anchor-region", "48,61,94,110"])
    report = json.loads((out / "loop.loop.report.json").read_text())
    assert report["status"] == "failed" and report["anchor"] == "motion"
    assert not (out / "cycle").exists()
