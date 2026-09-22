"""An uncertain registration must not hide either its failure or a bad raw seam."""
import json
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sprite_gen.video import auto_motion, batch, loop, motion_anchor


def bouncing_walker(k, *, bounce=60, drift=0):
    x = 110 + drift * k
    y = 95 + round(bounce * math.sin(k * 2 * math.pi / 24))
    im = Image.new("RGBA", (400, 300))
    d = ImageDraw.Draw(im)
    d.rectangle((x, y, x+28, y+30), fill=(210, 150, 60, 255))
    d.rectangle((x+20, y+8, x+24, y+12), fill=(10, 30, 50, 255))
    d.rectangle((x-3, y+32, x+30, y+78), fill=(20, 90, 180, 255))
    d.rectangle((x+10, y+35, x+15, y+73), fill=(210, 190, 40, 255))
    leg = round(12 * math.sin(k * 2 * math.pi / 24))
    d.rectangle((x+leg, y+79, x+6+leg, y+119), fill=(80, 30, 60, 255))
    d.rectangle((x+24-leg, y+79, x+30-leg, y+119), fill=(30, 60, 90, 255))
    return im


def write_source(path, **kwargs):
    path.mkdir()
    for k in range(73):
        bouncing_walker(k, **kwargs).save(path / f"{k:03}.png")
    return path


def test_real_boundary_match_emits_verified_unchanged_cycle_and_exposes_it(tmp_path, capsys):
    keyed = write_source(tmp_path / "keyed")
    out = tmp_path / "out"
    args = ["--frames-dir", str(keyed), "--out-dir", str(out), "--state", "walk"]
    assert loop.main(args + ["--anchor", "motion-auto"]) == 0
    summary = json.loads(capsys.readouterr().out)
    report = json.loads((out / "loop.loop.report.json").read_text())
    motion = report["motion_anchor"]
    assert summary["motion_anchor"] == motion
    assert motion["applied"] is False
    match = motion["rejected_measurement"]
    assert abs(match["dy"] - match["center_xy"][1]) == match["search_radius_xy"][1]
    assert motion["endpoint_xy"] == [0, 0]
    assert report["resampled_seam_ratio"] <= report["seam_max"] == 2
    assert report["gif"]["n_frames"] == report["webp"]["n_frames"] == report["n_out"]
    for k, p in enumerate(sorted((out / "cycle").glob("*.png"))):
        np.testing.assert_array_equal(np.asarray(Image.open(p)),
                                      np.asarray(bouncing_walker(report["cycle"]["start"] + k)))
    # The same real match remains an error for explicitly requested correction.
    fixed = args + ["--anchor", "motion", "--cycle", "fixed",
                    "--start", str(report["cycle"]["start"]), "--length", str(report["cycle"]["length"])]
    for box in motion["regions"]:
        fixed += ["--anchor-region", ",".join(map(str, box))]
    with pytest.raises(SystemExit, match="search boundary"):
        loop.main(fixed)


def test_uncertain_match_does_not_accept_bad_uncorrected_seam(tmp_path, monkeypatch):
    keyed = write_source(tmp_path / "keyed", bounce=3, drift=2)
    def uncertain(*args, **kwargs):
        raise motion_anchor.FineSearchBoundaryError({"k": 2, "dx": 14, "dy": 0})
    monkeypatch.setattr(motion_anchor, "correct_motion", uncertain)
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="loop seam ratio"):
        loop.main(["--frames-dir", str(keyed), "--out-dir", str(out), "--state", "walk",
                   "--anchor", "motion-auto"])
    report = json.loads((out / "loop.loop.report.json").read_text())
    assert report["status"] == "failed" and report["resampled_seam_ratio"] > 2
    assert report["motion_anchor"]["applied"] is False
    assert not (out / "loop.gif").exists() and not (out / "loop.webp").exists()


def test_other_registration_failures_are_not_converted_to_uncorrected_output():
    frames = [bouncing_walker(k) for k in range(24)]
    with pytest.raises(ValueError, match="coarse match reached"):
        auto_motion.correct_cycle(frames, [], coarse_dx=64)
    with pytest.raises(ValueError, match="no foreground"):
        auto_motion.correct_cycle(frames, [(0, 0, 5, 5)] * 2, coarse_dx=0)


def test_batch_table_discloses_uncorrected_output(tmp_path):
    text = batch.write_table([{"ok": True, "direction": "side", "state": "walk", "loop": {
        "cycle": 24, "period": None, "seam_ratio": 1.0, "n_out": 24,
        "review_recommended": True, "motion_anchor": {"applied": False},
    }}], tmp_path / "table.md")
    assert "OK (uncorrected; review gait)" in text
