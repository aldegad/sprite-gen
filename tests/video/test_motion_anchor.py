"""Behavior of explicit XY drift correction on synthetic, fully known motion."""
import json
import math
import time
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
    args = ["--frames-dir", str(keyed), "--out-dir", str(out), "--state", "walk", "--repair", "off",
            "--cycle", "fixed", "--start", "0", "--length", "24", "--anchor", "motion",
            "--anchor-region", "55,27,86,60", "--anchor-region", "48,61,94,110", "--fill-held", "off"]
    # Anchored, cells 5 to 7 differ by one pixel: the default would make 6 and 7 again after the anchor
    # (docs/loop-repair.md section 7). Off, the cells are the anchor's own.
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


def _one_shift(references, moving, dx, dy):
    """The costs of the regions laid at (dx, dy), one masked patch read alone a region — `motion._register`'s reading of
    a shift before the window's patches were read together (812047a) — or fewer than two where a region reads none."""
    costs = []
    for (x0, y0, x1, y1), mask, reference, norm in references:
        if x0 - dx < 0 or y0 - dy < 0 or x1 - dx > moving.shape[1] or y1 - dy > moving.shape[0]:
            break
        patch = moving[y0 - dy:y1 - dy, x0 - dx:x1 - dx][mask]
        if not np.ptp(patch, axis=0).any():
            break
        patch = patch - patch.mean(axis=0)
        denominator = norm * float(np.sqrt(np.sum(patch * patch)))
        if denominator <= 1e-12:
            break
        costs.append(1 - float(np.sum(reference * patch) / denominator))
    return costs


def _register_one_patch_at_a_time(references, moving, center):
    cx, cy = center
    rx, ry = motion.SEARCH_RADIUS
    candidates = []
    for dy in range(cy - ry, cy + ry + 1):
        for dx in range(cx - rx, cx + rx + 1):
            costs = _one_shift(references, moving, dx, dy)
            if len(costs) == 2:
                candidates.append((sum(w * c for w, c in zip(motion.REGION_WEIGHTS, costs)), dx, dy, costs))
    if not candidates:
        raise ValueError("motion anchor found no measurable match inside the search window")
    candidates.sort()
    score, dx, dy, costs = candidates[0]
    if abs(dx - cx) == rx or abs(dy - cy) == ry:
        raise motion.FineSearchBoundaryError({
            "dx": dx, "dy": dy, "cost": score, "region_costs": costs,
            "center_xy": [cx, cy], "search_radius_xy": [rx, ry],
        })
    return {"dx": dx, "dy": dy, "cost": score, "region_costs": costs}


def _reading(register, references, moving, center):
    try:
        return "match", register(references, moving, center)
    except motion.FineSearchBoundaryError as exc:
        return "boundary", exc.measurement
    except ValueError as exc:
        return "none", str(exc)


def _moved(im, xy):
    out = Image.new("RGBA", im.size)
    out.paste(im, xy)
    return out


def _texture(size, seed, *, flat_left=False, hole=False):
    rgba = np.random.default_rng(seed).integers(0, 256, (size[1], size[0], 4), dtype=np.uint8)
    rgba[:, :, 3] = np.maximum(rgba[:, :, 3], 140)
    if flat_left:
        rgba[:, : size[0] // 2] = (40, 200, 90, 255)
    if hole:
        rgba[size[1] // 3: size[1] // 2, :, 3] = 0
    return Image.fromarray(rgba, "RGBA")


@pytest.mark.parametrize("case", ["walker", "walker-drift", "walker-edge", "search-boundary", "noise", "noise-flat-half",
                                  "noise-hole", "flat"])
def test_the_search_window_reads_each_shift_as_one_patch_read_alone_did(case):
    """`_register` reads the patches of a row of shifts together, a block of mask pixels at a time; its result — the
    match, its costs, a search-boundary measurement, or no match — is the reading of one patch at a time, value for
    value (the same Python floats, compared by repr), wherever a shift lays a region off the frame or on one value."""
    first, center, moving = walker(0), (0, 0), walker(0)
    regions = REGIONS
    if case == "walker":
        moving = walker(3)
    elif case == "walker-drift":
        moving, center = walker(23), (-22, -5)
    elif case == "walker-edge":  # the shifts past the frame's right edge read nothing, nor dy 28 for the head
        moving, center = _moved(walker(0), (80, 0)), (-80, 0)
    elif case == "search-boundary":
        moving = _moved(walker(0), (14, 0))
    elif case.startswith("noise"):
        first = _texture((200, 200), 7, flat_left=case == "noise-flat-half", hole=case == "noise-hole")
        moving, center, regions = _moved(first, (-5, 3)), (5, -3), [(70, 40, 150, 110), (60, 120, 140, 190)]
    elif case == "flat":  # every patch one value: no shift reads a match
        first = _texture((200, 200), 7)
        moving, center, regions = Image.new("RGBA", (200, 200), (40, 200, 90, 255)), (0, 0), [(70, 40, 150, 110), (60, 120, 140, 190)]
    references = motion._references(motion._features(first), regions)
    # a region of more mask pixels than a block takes, so the patch's sums carry from block to block
    assert max(int(mask.sum()) for _, mask, _, _ in references) > motion.ROW_BLOCK
    features = motion._features(moving)
    batched = _reading(motion._register, references, features, center)
    alone = _reading(_register_one_patch_at_a_time, references, features, center)
    assert repr(batched) == repr(alone) and batched == alone
    assert batched[0] == {"search-boundary": "boundary", "flat": "none"}.get(case, "match")


@pytest.mark.slow
def test_a_960_cut_is_corrected_in_a_fraction_of_the_time_reading_one_patch_at_a_time_takes():
    """Performance watch: the search of a 960 px cut, regions of some 30 000 mask pixels as a 960 px walk's are. Read one
    patch at a time, each corrected cut took most of a walk's loop stage, and the seam gate corrects up to five
    (`local_cycle.SEAM_TRIES` and the cut taken). Hosts differ several times over, so the bound is this host's own one
    patch at a time, timed on a few shifts and scaled to the window: the batched search takes less than half of it."""
    rng = np.random.default_rng(960)
    body = Image.fromarray(np.dstack([rng.integers(0, 256, (620, 300, 3), dtype=np.uint8),
                                      np.full((620, 300), 255, np.uint8)]), "RGBA")
    frames = []
    for k in range(6):
        im = Image.new("RGBA", (960, 960))
        im.paste(body, (330 + k, 170 + round(3 * math.sin(k))))
        frames.append(im)
    regions = [(384, 200, 576, 380), (384, 420, 576, 600)]
    references = motion._references(motion._features(frames[0]), regions)
    assert min(int(mask.sum()) for _, mask, _, _ in references) > 30000
    moving = motion._features(frames[5])
    shifts = [(dx, dy) for dy in range(-28, 29) for dx in range(-19, 10)]
    timed = []
    for _ in range(3):
        started = time.perf_counter()
        for dx, dy in shifts[:: len(shifts) // 12][:12]:
            assert len(_one_shift(references, moving, dx, dy)) == 2
        timed.append((time.perf_counter() - started) / 12)
    one_at_a_time = min(timed) * len(shifts) * len(frames)
    started = time.perf_counter()
    _, report = motion.correct_motion(frames, regions, coarse_dx=-5)
    took = time.perf_counter() - started
    assert report["method"] == "masked-ncc-boundary-velocity-v1"
    assert [m["dx"] for m in report["boundary_measurements"]] == [0, -1, -2, -3, -4, -5]
    assert took < one_at_a_time / 2, (took, one_at_a_time)
