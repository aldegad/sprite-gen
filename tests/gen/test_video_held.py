# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video`, `video-extend`, `video-edit` and `video-resume` read the clip they publish for a picture shown
again (docs/loop-repair.md section 7) and name it, changing nothing: a provider that repeats a frame is warned of, and
its report records the reading (`held`). Synthetic clips, made with ffmpeg; no network — the HTTP layer is a fake."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from sprite_gen.gen import video
from tests.gen.test_video import _FakeApi, _still, clock  # noqa: F401  (the clock fixture)

W = H = 96
GREEN = (20, 240, 30)


def _frame(k: float, *, background=None) -> Image.Image:
    """A red square sliding across, 3 px a frame, on the green canvas a clip is filmed on (or `background`)."""
    a = np.zeros((H, W, 3), np.uint8)
    a[:] = GREEN if background is None else background
    x = round(10 + 3 * k)
    a[40:56, x:x + 16] = (220, 30, 40)
    return Image.fromarray(a, "RGB")


def _mp4(path: Path, times: list[float], *, background=None) -> Path:
    """An H.264 clip at 24 fps of the square at `times`: a time given twice in a row is a frame shown again."""
    folder = path.with_suffix("")
    folder.mkdir(parents=True)
    for i, t in enumerate(times):
        _frame(t, background=background).save(folder / f"{i:04d}.png")
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-framerate", "24", "-i", str(folder / "%04d.png"),
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_a_clip_that_shows_a_frame_again_is_read_so_and_one_that_does_not_is_read_clean(tmp_path):
    steady = video.read_held(_mp4(tmp_path / "steady.mp4", list(range(16))))
    assert steady["read"] is True and steady["frames"] == 16 and steady["held_runs"] == []
    assert steady["background"] is not None and (steady["poses"], steady["cells"]) == (16, 16)
    assert video.held_lines(tmp_path / "steady.mp4", steady) == []
    again = video.read_held(_mp4(tmp_path / "again.mp4", [0, 1, 2, 3, 4, 5, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]))
    assert again["held_runs"] == [{"start": 5, "length": 2}] and (again["poses"], again["cells"]) == (15, 16)
    assert video.held_lines(tmp_path / "again.mp4", again) == [
        "again.mp4 shows one picture on frames 5-6 (15/16 poses/frames): the clip repeats a frame there, kept as the "
        "provider made it; video-loop makes such cells again between the poses beside them (docs/loop-repair.md section 7)"]


def test_a_hold_the_motion_means_and_the_clip_s_opening_and_closing_pose_are_read_and_not_warned_of(tmp_path):
    clip = _mp4(tmp_path / "holds.mp4", [0, 0, 1, 2, 3, 3, 3, 3, 3, 4, 5, 6, 7, 7])
    record = video.read_held(clip)
    assert [(r["start"], r["length"]) for r in record["held_runs"]] == [(0, 2), (4, 5), (12, 2)]
    assert [r["why"] for r in record["held_runs"]] == [
        "the clip's opening pose", "held 5 frames, longer than a drawing on threes (3): a hold the motion means",
        "the clip's closing pose"]
    assert video.held_lines(clip, record) == []


def test_a_clip_filmed_whole_is_read_on_every_pixel(tmp_path):
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 255, (H, W, 3), dtype=np.uint8)  # no flat canvas round it: a scene filmed whole
    folder = tmp_path / "scene"
    folder.mkdir()
    for i, t in enumerate([0, 1, 2, 2, 3, 4]):
        a = np.asarray(_frame(t)).copy()
        square = np.all(a == (220, 30, 40), axis=-1)
        a[~square] = noise[~square]
        Image.fromarray(a).save(folder / f"{i:04d}.png")
    clip = tmp_path / "scene.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-framerate", "24", "-i", str(folder / "%04d.png"),
                    "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv444p", str(clip)], check=True)
    record = video.read_held(clip)
    assert record["background"] is None and record["held_runs"] == [{"start": 2, "length": 2}]


def test_a_file_ffmpeg_cannot_read_is_named_not_read(tmp_path):
    clip = tmp_path / "fake.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)
    record = video.read_held(clip)
    assert record["read"] is False and record["why"].startswith(("ffprobe found no video stream", "ffmpeg could not read"))
    assert video.held_lines(clip, record)[0].startswith("fake.mp4: its frames were not read for a picture shown again — ")


def test_a_clip_of_the_canvas_alone_is_named_not_read(tmp_path):
    """Every frame the green canvas, no subject: two frames of the canvas alone are one picture to the rule, so read on,
    the clip was one hold of eight frames the motion means, and nothing was said. No subject is no reading."""
    folder = tmp_path / "blank"
    folder.mkdir()
    for i in range(8):
        Image.new("RGB", (W, H), GREEN).save(folder / f"{i:04d}.png")
    clip = tmp_path / "blank.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-framerate", "24", "-i", str(folder / "%04d.png"),
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(clip)], check=True)
    record = video.read_held(clip)
    assert record["read"] is False and record["frames"] == 8 and record["background"] is not None
    assert record["why"].startswith("no frame of the 8 shows a subject: every pixel is within 0.12 of the colour")
    assert video.held_lines(clip, record) == [f"blank.mp4: its frames were not read for a picture shown again — {record['why']}"]


def _run(tmp_path: Path, monkeypatch, payload: bytes) -> tuple[dict, Path]:
    api = _FakeApi()
    api.payload = payload
    monkeypatch.setattr(video, "resolve_credential", lambda **_: video.Credential("t", video.AUTH_SOURCE_API_KEY))
    monkeypatch.setattr(video, "http_json", api.call)
    monkeypatch.setattr(video, "http_download", api.download)
    out, report = tmp_path / "out" / "clip.mp4", tmp_path / "report.json"
    rc = video.run(image=_still(tmp_path), prompt="sway", prompt_file=None, out=out, duration=6, resolution="720p",
                   aspect_ratio=None, model=video.DEFAULT_MODEL, generate_audio=None, report=report)
    assert rc == 0 and out.read_bytes() == payload  # the provider's bytes, published as they came
    return json.loads(report.read_text(encoding="utf-8")), out


def test_the_cli_warns_of_a_frame_the_provider_shows_again_and_records_the_reading(tmp_path, monkeypatch, clock, capsys):  # noqa: F811
    payload = _mp4(tmp_path / "made" / "again.mp4", [0, 1, 2, 3, 3, 4, 5, 6, 7, 8]).read_bytes()
    report, _ = _run(tmp_path, monkeypatch, payload)
    assert report["held"]["read"] is True and report["held"]["held_runs"] == [{"start": 3, "length": 2}]
    err = capsys.readouterr().err
    assert "video: warning: clip.mp4 shows one picture on frames 3-4 (9/10 poses/frames)" in err


def test_the_cli_reads_a_clip_that_shows_no_frame_again_and_says_nothing(tmp_path, monkeypatch, clock, capsys):  # noqa: F811
    payload = _mp4(tmp_path / "made" / "steady.mp4", list(range(10))).read_bytes()
    report, _ = _run(tmp_path, monkeypatch, payload)
    assert report["held"]["read"] is True and report["held"]["held_runs"] == []
    assert (report["held"]["poses"], report["held"]["cells"]) == (10, 10)
    assert "warning" not in capsys.readouterr().err
