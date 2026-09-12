# SPDX-License-Identifier: Apache-2.0
"""Synthetic motion measurements; no provider assets or real run directories."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from sprite_gen.qa.motion import analyze_motion
from sprite_gen.qa import motion


def sequence(xs=(24, 22, 20, 18, 16), *, durations=None, ys=None, two_feet=False):
    frames = []
    for index, x in enumerate(xs):
        frame = Image.new("RGBA", (48, 40))
        draw = ImageDraw.Draw(frame)
        draw.rectangle((15, 5, 30, 20), fill="white")
        y = 30 if ys is None else ys[index]
        draw.rectangle((x, y, x + 3, y + 2), fill="white")
        if two_feet:
            draw.rectangle((36, y, 39, y + 2), fill="white")
        frames.append(frame)
    return SimpleNamespace(
        frames=tuple(frames), durations=tuple(durations or [0.1] * len(frames)),
        anchor=(24.0, 40.0), source_files=(), metadata={}, fingerprints=lambda: {},
    )


FOOT_BOX = (8, 25, 34, 12)


def test_manual_stance_measures_signed_velocity_without_facing_inference():
    report = analyze_motion(sequence(), contacts="0:4", foot_box=FOOT_BOX)
    assert report["status"] == "verified"
    assert report["stride_verified"] is True
    assert report["source_fingerprints"] == {}
    assert report["foot_velocity_px_per_second"] == pytest.approx(-20)
    assert report["stride_px_per_second"] == pytest.approx(20)
    assert report["confidence"] >= 0.8
    assert report["duration_seconds"] == pytest.approx(0.5)
    assert report["facing"] is None
    assert report["contact_candidates"][0]["measurement_elapsed_seconds"] == pytest.approx(0.4)


@pytest.mark.parametrize("xs, expected", [((16, 18, 20, 22, 24), 20), ((24, 22, 20, 18, 16), -20)])
def test_facing_never_flips_velocity_or_reverses_frames(xs, expected):
    seq = sequence(xs)
    before = tuple(frame.tobytes() for frame in seq.frames)
    for facing in (None, "left", "right"):
        report = analyze_motion(seq, contacts=range(5), foot_box=FOOT_BOX, facing=facing)
        assert report["foot_velocity_px_per_second"] == pytest.approx(expected)
        assert report["stride_px_per_second"] == pytest.approx(abs(expected))
        assert report["facing"] == facing
    assert tuple(frame.tobytes() for frame in seq.frames) == before


def test_split_duplicate_holds_preserve_duration_and_stance_speed():
    original = sequence(durations=[0.2] * 5)
    repeated = sequence(
        (24, 24, 22, 22, 20, 20, 18, 18, 16, 16), durations=[0.07, 0.13] * 5,
    )
    a = analyze_motion(original, contacts="0:4", foot_box=FOOT_BOX)
    b = analyze_motion(repeated, contacts="0:9", foot_box=FOOT_BOX)
    assert b["duration_seconds"] == pytest.approx(a["duration_seconds"])
    assert b["stride_px_per_second"] == pytest.approx(a["stride_px_per_second"])
    assert b["foot_velocity_px_per_second"] == pytest.approx(-10)
    assert b["duplicate_frames"]["exact"] == 5
    assert b["duplicate_frames"]["exact_silhouette"] == 5
    assert len(b["frames"]) == 10
    assert b["frames"][-1]["duration_seconds"] == pytest.approx(0.13)
    assert b["contact_candidates"][0]["measurement_elapsed_seconds"] == pytest.approx(0.8)


def test_added_hold_time_reduces_speed_instead_of_being_dropped():
    original = analyze_motion(sequence(), contacts="0:4", foot_box=FOOT_BOX)
    repeated = analyze_motion(
        sequence((24, 24, 22, 22, 20, 20, 18, 18, 16, 16)),
        contacts="0:9", foot_box=FOOT_BOX,
    )
    assert repeated["duration_seconds"] == pytest.approx(original["duration_seconds"] * 2)
    assert repeated["stride_px_per_second"] == pytest.approx(original["stride_px_per_second"] / 2)


def test_nonuniform_frame_durations_determine_speed():
    seq = sequence((24, 23, 20, 18, 16), durations=[0.05, 0.15, 0.1, 0.1, 0.4])
    report = analyze_motion(seq, contacts=[(0, 4)], foot_box=FOOT_BOX)
    assert report["foot_velocity_px_per_second"] == pytest.approx(-20)
    assert report["duration_seconds"] == pytest.approx(0.8)


@pytest.mark.parametrize("kwargs", [{}, {"contacts": "0:4"}, {"foot_box": FOOT_BOX}])
def test_unannotated_motion_cannot_claim_stride(kwargs):
    report = analyze_motion(sequence(), **kwargs)
    assert report["motion"]["present"] is True
    assert report["status"] == "needs-review"
    assert report["stride_px_per_second"] is None
    assert report["foot_velocity_px_per_second"] is None
    assert report["warnings"]


def test_two_feet_in_roi_cannot_claim_stance_speed():
    report = analyze_motion(sequence(two_feet=True), contacts="0:4", foot_box=FOOT_BOX)
    assert report["status"] == "needs-review"
    assert report["stride_px_per_second"] is None
    assert any("multiple" in reason for reason in report["contact_candidates"][0]["reasons"])


def test_connected_legs_still_have_ambiguous_contact_lobes():
    seq = sequence(two_feet=True)
    for frame in seq.frames:
        ImageDraw.Draw(frame).rectangle((16, 28, 39, 30), fill="white")
    report = analyze_motion(seq, contacts="0:4", foot_box=FOOT_BOX)
    assert report["stride_px_per_second"] is None
    assert any("multiple" in reason for reason in report["contact_candidates"][0]["reasons"])


@pytest.mark.parametrize("ys", [(23, 21, 19, 21, 23), (30, 28, 26, 28, 30)])
def test_airborne_or_vertically_moving_foot_stays_unknown(ys):
    report = analyze_motion(sequence(ys=ys), contacts="0:4", foot_box=FOOT_BOX)
    assert report["status"] == "needs-review"
    assert report["stride_px_per_second"] is None
    assert report["foot_velocity_px_per_second"] is None


def test_stance_reversal_is_rejected():
    report = analyze_motion(sequence((24, 22, 20, 22, 24)), contacts="0:4", foot_box=FOOT_BOX)
    assert report["stride_px_per_second"] is None
    assert "horizontal reversal" in report["contact_candidates"][0]["reasons"]


def test_contact_spans_do_not_bridge_swing_and_conflicting_signs_stay_unknown():
    seq = sequence((24, 22, 20, 34, 16, 18, 20))
    report = analyze_motion(seq, contacts="0:2,4:6", foot_box=FOOT_BOX)
    assert len(report["contact_candidates"]) == 2
    assert all(item["status"] == "verified" for item in report["contact_candidates"])
    assert report["stride_px_per_second"] is None
    assert report["status"] == "needs-review"


def test_static_empty_and_single_frame_are_unknown_and_json_safe():
    for seq in (sequence((20, 20, 20)), sequence((20,)), sequence((20, 20))):
        report = analyze_motion(seq)
        assert report["status"] == "unknown"
        assert report["stride_px_per_second"] is None
        json.dumps(report, allow_nan=False)
    blank = sequence((20,))
    blank.frames = (Image.new("RGBA", (48, 40)),)
    report = analyze_motion(blank)
    assert report["frames"][0]["bbox"] is None
    assert report["bbox_stats"]["union"] is None


def test_silhouette_repeats_near_poses_edges_and_anchor_stats():
    frame = Image.new("RGBA", (48, 40))
    ImageDraw.Draw(frame).rectangle((0, 0, 20, 30), fill="white")
    recolored = frame.copy()
    ImageDraw.Draw(recolored).rectangle((0, 0, 20, 30), fill="red")
    near = recolored.copy()
    near.putpixel((20, 30), (0, 0, 0, 0))
    seq = sequence((20,) * 4)
    seq.frames = (frame, recolored, near, frame.copy())
    report = analyze_motion(seq)
    assert report["duplicate_frames"]["exact"] == 1
    assert report["duplicate_frames"]["exact_silhouette"] == 2
    assert report["duplicate_frames"]["near_silhouette"] == 1
    assert report["edge_contact"]["frames"] == [0, 1, 2, 3]
    assert report["edge_contact"]["sides"]["left"] == [0, 1, 2, 3]
    assert report["frames"][0]["anchor_px"] == [24.0, 40.0]
    assert report["frames"][0]["bbox_from_anchor"] == [-24.0, -40.0, -3.0, -9.0]


@pytest.mark.parametrize("kwargs", [
    {"contacts": "4:0"}, {"contacts": "0:8"}, {"contacts": "0:2,2:4"},
    {"contacts": [0, 0, 1]}, {"contacts": ""}, {"contacts": "-1:2"},
    {"contacts": [0, 2.5]}, {"foot_box": (0, 0, 0, 10)},
    {"foot_box": (0, 0, 49, 10)}, {"foot_box": (0, 0, 10.5, 10)},
    {"facing": "up"},
])
def test_invalid_annotations_fail_before_analysis(kwargs):
    with pytest.raises(ValueError):
        analyze_motion(sequence(), **kwargs)


@pytest.mark.parametrize("durations", [(0.1,), (0.1, 0, 0.1, 0.1, 0.1), (float("nan"),) * 5])
def test_invalid_timing_is_rejected(durations):
    seq = sequence()
    seq.durations = durations
    with pytest.raises(ValueError):
        analyze_motion(seq)


def test_timing_that_overflows_derived_speed_is_rejected():
    seq = sequence(durations=[1e-320] * 5)
    with pytest.raises(ValueError, match="finite"):
        analyze_motion(seq, contacts="0:4", foot_box=FOOT_BOX)
    with pytest.raises(ValueError, match="finite"):
        analyze_motion(seq)


def descriptor(tmp_path):
    """Exercise the real shared adapter with a declared external sequence."""
    entries = []
    for index, frame in enumerate(sequence().frames):
        path = tmp_path / f"foot-{index}.png"
        frame.save(path)
        entries.append({"file": path.name, "duration": 0.1})
    path = tmp_path / "walk.json"
    path.write_text(json.dumps({"kind": "sprite-gen-asset", "version": 1, "frames": entries}))
    return path


def file_bytes(directory):
    return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def test_run_real_adapter_stdout_is_read_only_and_fingerprints_match(tmp_path, capsys):
    source = descriptor(tmp_path)
    before = file_bytes(tmp_path)
    assert motion.run(source=source, contacts="0:4", foot_box="8,25,34,12", anchor_x=12, anchor_y=38) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["stride_verified"] is True
    assert report["stride_px_per_second"] == pytest.approx(20)
    assert report["anchor_px"] == [12, 38]
    assert report["source_fingerprints"] == {
        str(tmp_path / name): hashlib.sha256(contents).hexdigest() for name, contents in before.items()
    }
    assert file_bytes(tmp_path) == before


def test_module_entrypoint_publishes_report_and_preserves_sources(tmp_path):
    source = descriptor(tmp_path)
    before = file_bytes(tmp_path)
    output = tmp_path / "reports" / "motion.json"
    result = subprocess.run(
        [sys.executable, "-m", "sprite_gen.qa.motion", "--source", str(source),
         "--contacts", "0:4", "--foot-box", "8,25,34,12", "--fps", "5",
         "--facing", "right", "--out", str(output)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    assert report == json.loads(result.stdout)
    assert report["stride_px_per_second"] == pytest.approx(10)
    assert report["duration_seconds"] == pytest.approx(1)
    assert not (output.parent / ".sprite-gen.lock").exists()
    for name, contents in before.items():
        assert (tmp_path / name).read_bytes() == contents


@pytest.mark.parametrize("target", ["walk.json", "foot-0.png", "symlink.json", "hardlink.json"])
def test_output_cannot_overwrite_any_source_or_alias(tmp_path, capsys, target):
    source = descriptor(tmp_path)
    output = tmp_path / target
    if target == "symlink.json":
        output.symlink_to(source)
    if target == "hardlink.json":
        os.link(source, output)
    before = file_bytes(tmp_path)
    assert motion.run(source=source, out=output) == 2
    assert "must not overwrite" in capsys.readouterr().err
    assert file_bytes(tmp_path) == before


@pytest.mark.parametrize("kwargs", [
    {"contacts": "0:999"}, {"foot_box": "8,25,99,12"}, {"anchor_x": 2},
    {"fps": 0}, {"facing": "north"},
])
def test_invalid_cli_input_creates_no_output_or_locks(tmp_path, capsys, kwargs):
    source = descriptor(tmp_path)
    before = file_bytes(tmp_path)
    output = tmp_path / "new" / "report.json"
    assert motion.run(source=source, out=output, **kwargs) == 2
    assert capsys.readouterr().err
    assert file_bytes(tmp_path) == before
    assert not output.parent.exists()


def test_publication_uses_output_lock_then_atomic_write_once(tmp_path, monkeypatch, capsys):
    source = descriptor(tmp_path)
    output = tmp_path / "reports" / "motion.json"
    calls = []
    real_write = motion.atomic_write_set

    def lock(directory, owner):
        assert directory == output.parent
        calls.append(("lock", owner))

    def write(payloads):
        assert calls == [("lock", "inspect-motion")]
        assert list(payloads) == [output]
        calls.append(("write",))
        real_write(payloads)

    monkeypatch.setattr(motion, "acquire_run_dir_lock", lock)
    monkeypatch.setattr(motion, "atomic_write_set", write)
    assert motion.run(source=source, out=output) == 0
    assert len(calls) == 2
    assert json.loads(output.read_text()) == json.loads(capsys.readouterr().out)


def test_live_output_lock_fails_without_retry_or_report(tmp_path):
    source = descriptor(tmp_path)
    output = tmp_path / "report.json"
    (tmp_path / ".sprite-gen.lock").write_text(json.dumps({"owner": "synthetic-writer", "pid": os.getpid()}))
    before = file_bytes(tmp_path)
    with pytest.raises(SystemExit, match="locked by synthetic-writer"):
        motion.run(source=source, out=output)
    assert file_bytes(tmp_path) == before


def test_atomic_publish_failure_keeps_existing_report_and_does_not_retry(tmp_path, monkeypatch, capsys):
    source = descriptor(tmp_path)
    output = tmp_path / "report.json"
    output.write_text("existing report")
    calls = []

    def fail_once(payloads):
        calls.append(payloads)
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(motion, "atomic_write_set", fail_once)
    assert motion.run(source=source, out=output) == 2
    assert len(calls) == 1
    assert output.read_text() == "existing report"
    assert "synthetic disk failure" in capsys.readouterr().err


def test_consistent_separate_stances_can_measure_without_bridging_swing():
    seq = sequence((24, 22, 20, 32, 24, 22, 20))
    report = analyze_motion(seq, contacts=[0, 1, 2, 4, 5, 6], foot_box=FOOT_BOX)
    assert report["contacts"] == [[0, 2], [4, 6]]
    assert report["stride_verified"] is True
    assert report["foot_velocity_px_per_second"] == pytest.approx(-20)


def test_invalid_stance_is_not_silently_dropped_from_aggregate():
    seq = sequence((24, 22, 20, 32, 24, 24, 24))
    report = analyze_motion(seq, contacts="0:2,4:6", foot_box=FOOT_BOX)
    assert report["contact_candidates"][0]["status"] == "verified"
    assert report["contact_candidates"][1]["status"] == "needs-review"
    assert report["stride_verified"] is False
    assert report["stride_px_per_second"] is None


def test_foot_roi_clipping_and_speed_changes_are_not_verified():
    clipped = analyze_motion(sequence(), contacts="0:4", foot_box=(16, 25, 12, 12))
    assert clipped["stride_verified"] is False
    inconsistent = analyze_motion(sequence((26, 25, 24, 23, 16)), contacts="0:4", foot_box=FOOT_BOX)
    assert inconsistent["stride_verified"] is False
    assert "foot velocity is inconsistent" in " ".join(inconsistent["contact_candidates"][0]["reasons"])


def test_report_names_the_measured_sequence_so_consumers_can_refuse_other_selections(tmp_path):
    from sprite_gen.spec.assets import load_asset, same_identity, sequence_identity

    sheet = Image.new("RGBA", (48 * 5, 40))
    for index, frame in enumerate(sequence().frames):
        sheet.paste(frame, (48 * index, 0))
    sheet.save(tmp_path / "atlas.png")
    rects = [{"x": 48 * i, "y": 0, "w": 48, "h": 40} for i in range(5)]
    (tmp_path / "atlas.json").write_text(json.dumps({
        "sprite_sheet_alpha": "atlas.png",
        "frame_layout": {"rows": {"walk": rects, "idle": [rects[0]] * 5}},
        "animation": {"rows": {s: {"durations_ms": [100] * 5, "loop": True} for s in ("walk", "idle")}},
    }))
    walk = load_asset(tmp_path / "atlas.json", state="walk")
    report = json.loads(json.dumps(analyze_motion(walk, contacts="0:4", foot_box=FOOT_BOX)))
    assert report["sequence"] == sequence_identity(walk)
    assert report["sequence"]["state"] == "walk" and report["sequence"]["durations_seconds"] == [0.1] * 5
    assert same_identity(report["sequence"], walk)
    assert not same_identity(report["sequence"], load_asset(tmp_path / "atlas.json", state="idle"))
    assert not same_identity(report["sequence"], load_asset(tmp_path / "atlas.json", state="walk", fps=5))
    assert report["source_fingerprints"] == walk.fingerprints()
    assert "sequence" in report["method"]
    in_memory = analyze_motion(sequence(), contacts="0:4", foot_box=FOOT_BOX)
    assert in_memory["sequence"] == {"source": None, "state": None, "fps_override": None, "frame_count": 5,
                                     "size": [48, 40], "anchor_px": [24.0, 40.0], "durations_seconds": [0.1] * 5}


def test_in_process_publication_releases_the_output_lock(tmp_path, capsys):
    from sprite_gen.spec import runio

    source = descriptor(tmp_path)
    output = tmp_path / "reports" / "motion.json"
    assert motion.run(source=source, out=output) == 0
    assert not (output.parent / runio.LOCK_FILENAME).exists()
    assert (output.parent / runio.LOCK_FILENAME).resolve() not in runio._HELD_LOCKS
    assert motion.run(source=source, out=output) == 0
    assert json.loads(output.read_text())["kind"] == "sprite-gen-motion-report"
