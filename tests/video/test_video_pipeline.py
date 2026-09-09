# SPDX-License-Identifier: Apache-2.0
"""Video -> sprite pipeline contracts on synthetic frames: canvas profiles, edge-contact
keying, global-period cycle detection (incl. the 1.5-cycle trap), seam gate, GIF/WebP
verification, and the batch runner's stagger/429 handling."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video import batch as batch_mod
from sprite_gen.video import canvas as canvas_mod
from sprite_gen.video import frames as frames_mod
from sprite_gen.video import loop as loop_mod

GREEN = (0, 255, 0)
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAS_IMG2WEBP = shutil.which("img2webp") is not None


def _still(tmp_path: Path, size=(120, 160), key=GREEN, name="still.png") -> Path:
    im = Image.new("RGB", size, key)
    for y in range(40, 150):
        for x in range(40, 80):
            im.putpixel((x, y), (200, 40, 40))
    p = tmp_path / name
    im.save(p)
    return p


# --- canvas -------------------------------------------------------------------


def test_profile_table_routes_states_and_shape_overrides() -> None:
    assert canvas_mod.profile_for("jump").shape == "tall"
    assert canvas_mod.profile_for("attack").shape == "wide"
    assert canvas_mod.profile_for("walk").shape == "square"
    assert canvas_mod.profile_for(None).shape == "square"
    assert canvas_mod.profile_for("jump", shape="wide").shape == "wide"
    with pytest.raises(SystemExit, match="unknown --shape"):
        canvas_mod.profile_for("jump", shape="round")


def test_tall_canvas_keeps_headroom_and_key(tmp_path: Path) -> None:
    still = Image.open(_still(tmp_path))
    out, rep = canvas_mod.pad_canvas(still, canvas_mod.profile_for("jump"))
    w, h = out.size
    assert rep["shape"] == "tall" and abs(w / h - 3 / 4) < 0.02
    assert rep["offset"][1] == h - 160  # still sits at the bottom
    assert h - 160 >= round(h * 0.34) - 1  # headroom above the still
    assert out.getpixel((0, 0)) == GREEN and rep["key_rgb"] == list(GREEN)
    assert out.getpixel((rep["offset"][0] + 50, h - 100)) == (200, 40, 40)  # still content intact


def test_wide_canvas_puts_room_in_front_of_the_facing(tmp_path: Path) -> None:
    still = Image.open(_still(tmp_path))
    right, rep_r = canvas_mod.pad_canvas(still, canvas_mod.profile_for("attack"), facing="right")
    left, rep_l = canvas_mod.pad_canvas(still, canvas_mod.profile_for("attack"), facing="left")
    assert rep_r["offset"][0] == 0 and rep_l["offset"][0] == left.width - 120
    assert abs(right.width / right.height - 16 / 9) < 0.02


def test_square_canvas_never_shrinks_and_refuses_non_flat_corners(tmp_path: Path) -> None:
    still = Image.open(_still(tmp_path))
    out, rep = canvas_mod.pad_canvas(still, canvas_mod.profile_for("walk"))
    assert out.size == (160, 160) and rep["offset"] == [20, 0]
    bad = Image.new("RGB", (60, 60), GREEN)
    bad.putpixel((0, 0), (255, 255, 255))
    with pytest.raises(SystemExit, match="not one flat colour"):
        canvas_mod.pad_canvas(bad, canvas_mod.profile_for("walk"))


def test_run_canvas_writes_png_and_report(tmp_path: Path) -> None:
    still = _still(tmp_path)
    rep = canvas_mod.run_canvas(still, tmp_path / "o" / "canvas.png", state="jump", shape=None, facing="right", headroom=None, lead=None, report_path=tmp_path / "o" / "r.json")
    assert (tmp_path / "o" / "canvas.png").is_file() and rep["kind"] == "sprite-gen-video-canvas-report"
    assert json.loads((tmp_path / "o" / "r.json").read_text())["shape"] == "tall"


# --- frames ----------------------------------------------------------------------


def _raw_frames(tmp_path: Path, n: int, *, touch_top: bool = False) -> list[Path]:
    d = tmp_path / "raw"
    d.mkdir()
    files = []
    for k in range(n):
        im = Image.new("RGB", (80, 100), GREEN)
        top = 0 if touch_top else 20
        for y in range(top, 90):
            for x in range(25 + (k % 3), 55 + (k % 3)):
                im.putpixel((x, y), (180, 60, 30))
        p = d / f"frame-{k:04d}.png"
        im.save(p)
        files.append(p)
    return files


def test_key_frames_keys_green_and_reports_alpha(tmp_path: Path) -> None:
    files = _raw_frames(tmp_path, 4)
    rep = frames_mod.key_frames(files, tmp_path / "keyed", key="green")
    assert rep["frames"] == 4 and rep["edge_contacts"] == []
    assert rep["alpha_zero_pct_min"] > 50
    keyed = Image.open(tmp_path / "keyed" / "frame-0000.png").convert("RGBA")
    assert keyed.getpixel((0, 0))[3] == 0 and keyed.getpixel((40, 50))[3] > 0


def test_key_frames_fails_loud_on_edge_contact_unless_allowed(tmp_path: Path) -> None:
    files = _raw_frames(tmp_path, 3, touch_top=True)
    with pytest.raises(SystemExit, match="touches the frame edge"):
        frames_mod.key_frames(files, tmp_path / "keyed", key="green")
    rep = frames_mod.key_frames(files, tmp_path / "keyed2", key="green", check_edges=False)
    assert rep["frames"] == 3


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_run_frames_extracts_a_real_clip(tmp_path: Path) -> None:
    import subprocess

    files = _raw_frames(tmp_path, 12)
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "24", "-i", str(tmp_path / "raw" / "frame-%04d.png"), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "12", str(clip)], check=True)
    rep = frames_mod.run_frames(clip, tmp_path / "fr", key="green", allow_edge_contact=False, report_path=None)
    assert rep["frames"] == 12 and abs(rep["fps"] - 24) < 0.01
    assert Path(rep["report"]).is_file() and len(list(Path(rep["keyed_dir"]).glob("*.png"))) == 12


# --- loop ----------------------------------------------------------------------------


def _gait_frames(tmp_path: Path, *, period: int, n: int, size=(64, 64), stamp: bool = False) -> list[Path]:
    """A 'walker': a body block plus one leg that swings with `period`; the swing is
    mirror-symmetric every half period (the classic 1.5-cycle trap) but a side marker
    breaks the symmetry slightly, exactly like a near/far leg in a side view."""
    d = tmp_path / "keyed"
    d.mkdir()
    files = []
    for t in range(n):
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        for y in range(10, 40):
            for x in range(26, 38):
                im.putpixel((x, y), (200, 60, 60, 255))
        phase = 2 * math.pi * t / period
        leg_x = 32 + round(10 * math.sin(phase))
        for y in range(40, 58):
            for x in range(leg_x - 3, leg_x + 3):
                if 0 <= x < size[0]:
                    im.putpixel((x, y), (60, 60, 200, 255))
        # near-leg marker only on the first half of the period
        if math.sin(phase) >= 0:
            im.putpixel((leg_x, 50), (255, 255, 0, 255))
        if stamp:  # recolour one BODY pixel per frame so no two frames are identical (a detached
            # marker would be erased as a speck; GIF writers merge byte-identical frames)
            im.putpixel((26 + t % 12, 10 + (t // 12) % 30), (90, 200, 90, 255))
        p = d / f"frame-{t:04d}.png"
        im.save(p)
        files.append(p)
    return files


def test_detect_cycle_finds_the_full_period_not_the_half_or_1_5x(tmp_path: Path) -> None:
    files = _gait_frames(tmp_path, period=12, n=100)
    D = loop_mod.distance_matrix(files)
    cycle = loop_mod.detect_cycle(D, min_len=4, max_len=40)
    assert cycle["period_global"] == 12
    assert cycle["length"] in (11, 12, 13)
    assert cycle["ratio"] < 1.0


def test_detect_cycle_refuses_empty_window() -> None:
    D = np.zeros((10, 10), dtype=np.float32)
    with pytest.raises(SystemExit, match="window"):
        loop_mod.detect_cycle(D, min_len=9, max_len=8)


def test_profiles_scale_windows_with_clip_length() -> None:
    p = loop_mod.profile_for("idle")
    assert p.min_frac > loop_mod.profile_for("walk").min_frac
    # a 6 s / 24 fps clip: the walk floor must admit a 13-frame bounce (legless body) and the
    # ceiling a 28-frame gait (2026-09-09: slime 13, wolf 24, biped 25 all resolved by the depth rule)
    lo, hi = round(144 * loop_mod.profile_for("walk").min_frac), round(144 * loop_mod.profile_for("walk").max_frac)
    assert lo <= 13 and hi >= 28
    assert loop_mod.profile_for("unknown-state") is loop_mod.STATE_PROFILES["default"]


@pytest.mark.skipif(not HAS_IMG2WEBP, reason="img2webp not installed")
def test_run_loop_emits_strip_gif_webp_and_verifies(tmp_path: Path) -> None:
    files = _gait_frames(tmp_path, period=12, n=96)
    rep = loop_mod.run_loop(tmp_path / "keyed", tmp_path / "out", fps=24.0, state="walk", min_len=None, max_len=None, n_out=8, seam_max=2.0, name="walker", report_path=None)
    assert rep["cycle"]["period_global"] == 12
    assert rep["resampled_seam_ratio"] <= 2.0
    strip = Image.open(rep["strip"]["path"])
    assert strip.size == (rep["strip"]["w"] * rep["strip"]["frames"], rep["strip"]["h"])
    assert rep["strip"]["body_h"] <= rep["strip"]["h"]
    assert rep["gif"]["n_frames"] == 8 and rep["gif"]["loop"] == 0 and rep["gif"]["corners_transparent"]
    assert rep["webp"]["n_frames"] == 8 and rep["webp"]["stale_rgb_under_alpha0"] == 0
    assert json.loads((tmp_path / "out" / "walker.strip.json").read_text())["frames"] == rep["strip"]["frames"]
    assert len(list((tmp_path / "out" / "cycle").glob("frame-*.png"))) == rep["cycle"]["length"]


def test_run_loop_seam_gate_fails_loud_on_noise(tmp_path: Path, monkeypatch) -> None:
    d = tmp_path / "keyed"
    d.mkdir()
    rng = np.random.default_rng(7)
    for t in range(40):
        arr = np.zeros((48, 48, 4), dtype=np.uint8)
        mask = rng.random((48, 48)) > 0.6
        arr[..., 0][mask] = 200
        arr[..., 3][mask] = 255
        Image.fromarray(arr, "RGBA").save(d / f"frame-{t:04d}.png")
    with pytest.raises(SystemExit, match="seam ratio|no periodic cycle"):
        loop_mod.run_loop(d, tmp_path / "out", fps=24.0, state="walk", min_len=None, max_len=None, n_out=6, seam_max=2.0, name="noise", report_path=None)
    assert not (tmp_path / "out" / "noise.gif").exists()


def _one_shot_frames(tmp_path: Path, n: int = 144, hop: tuple[int, int] = (60, 76), size=(64, 64), jitter: int = 2) -> list[Path]:
    """A body that stands (with a little rest jitter, like a real keyed clip) except for ONE
    hop between the given frames — what a video model does when it ignores 'over and over'
    (2026-09-09 quadruped run)."""
    d = tmp_path / "keyed"
    d.mkdir()
    files = []
    rng = np.random.default_rng(3)
    for t in range(n):
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        lift = 0
        if hop[0] <= t < hop[1]:
            lift = round(14 * math.sin(math.pi * (t - hop[0]) / (hop[1] - hop[0])))
        dx = int(rng.integers(-jitter, jitter + 1))
        dy = int(rng.integers(-jitter, jitter + 1))
        for y in range(20 - lift + dy, 58 - lift + dy):
            for x in range(24 + dx, 40 + dx):
                im.putpixel((x, y), (200, 60, 60, 255))
        p = d / f"frame-{t:04d}.png"
        im.save(p)
        files.append(p)
    return files


def test_one_shot_detector_cuts_rest_excursion_rest(tmp_path: Path) -> None:
    files = _one_shot_frames(tmp_path)
    D = loop_mod.distance_matrix(files)
    periodic = loop_mod.detect_cycle(D, min_len=16, max_len=65)
    assert periodic["periodicity"] < loop_mod.PERIODICITY_MIN  # one hop is not a period
    cycle = loop_mod.detect_one_shot(D, min_len=9, max_len=65)
    assert cycle["kind"] == "one-shot"
    assert cycle["start"] <= 60 and cycle["start"] + cycle["length"] >= 75  # covers the hop, rest on both sides
    assert cycle["ratio"] < 2.0  # rest -> rest seam within the gate


def test_one_shot_detector_fails_loud_when_nothing_moves(tmp_path: Path) -> None:
    files = _one_shot_frames(tmp_path, n=60, hop=(0, 0), jitter=0)
    D = loop_mod.distance_matrix(files)
    with pytest.raises(SystemExit, match="never leaves its rest pose"):
        loop_mod.detect_one_shot(D, min_len=4, max_len=60)


@pytest.mark.skipif(not HAS_IMG2WEBP, reason="img2webp not installed")
def test_run_loop_auto_fails_over_to_one_shot_only_for_action_states(tmp_path: Path) -> None:
    _one_shot_frames(tmp_path)
    # walk must not repeat once: the gate stays a hard failure
    with pytest.raises(SystemExit, match="no periodic cycle"):
        loop_mod.run_loop(tmp_path / "keyed", tmp_path / "walk", fps=24.0, state="walk", min_len=None, max_len=None, n_out=8, seam_max=2.0, name="w", report_path=None)
    # jump may happen once: recorded failover, periodic attempt kept in the report
    rep = loop_mod.run_loop(tmp_path / "keyed", tmp_path / "jump", fps=24.0, state="jump", min_len=None, max_len=None, n_out=8, seam_max=2.0, name="j", report_path=None)
    assert rep["cycle"]["kind"] == "one-shot" and rep["cycle_mode"] == "auto"
    assert rep["periodic_attempt"]["periodicity"] < loop_mod.PERIODICITY_MIN
    assert rep["gif"]["loop"] == 0 and rep["webp"]["stale_rgb_under_alpha0"] == 0
    # forcing periodic keeps the old behaviour
    with pytest.raises(SystemExit, match="no periodic cycle"):
        loop_mod.run_loop(tmp_path / "keyed", tmp_path / "jump2", fps=24.0, state="jump", min_len=None, max_len=None, n_out=8, seam_max=2.0, name="j2", report_path=None, cycle_mode="periodic")


def test_strip_cap_is_on_pixels_not_cells() -> None:
    # 100 wide frames (body 700 px across): 64 cells would be 44,800 px — Chrome cannot show it
    frames = []
    for t in range(100):
        im = Image.new("RGBA", (720, 300), (0, 0, 0, 0))
        for y in range(40 + (t % 7), 300):
            for x in range(10, 710):
                im.putpixel((x, y), (200, 60, 60, 255))
        frames.append(im)
    strip, meta = loop_mod.build_strip(frames, cycle_seconds=100 / 24)
    assert strip.width <= loop_mod.STRIP_MAX_WIDTH
    assert meta["frames"] == meta["cell_cap"] < 64 and meta["subsampled"] is True
    assert strip.width == meta["w"] * meta["frames"]


def test_gif_frame_count_follows_cycle_length_at_a_fixed_playback_rate(tmp_path: Path) -> None:
    """A 2.5 s jump used to get the same 12 frames as a 1.1 s walk and play at ~5 fps
    (2026-09-09, lead: '점프만 혼자 왜 프레임이 느리냐')."""
    (tmp_path / "long").mkdir()
    _gait_frames(tmp_path / "long", period=60, n=150, stamp=True)  # 60 frames @ 24 fps = 2.5 s
    rep = loop_mod.run_loop(tmp_path / "long" / "keyed", tmp_path / "long-out", fps=24.0, state="jump", min_len=None, max_len=None, n_out=None, seam_max=2.0, name="j", report_path=None)
    assert rep["n_out"] == 30 and 80 <= rep["delay_ms"] <= 90
    (tmp_path / "short").mkdir()
    _gait_frames(tmp_path / "short", period=26, n=100, stamp=True)  # 1.08 s
    rep2 = loop_mod.run_loop(tmp_path / "short" / "keyed", tmp_path / "short-out", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=2.0, name="w", report_path=None)
    assert 12 <= rep2["n_out"] <= 14 and abs(rep2["delay_ms"] - rep["delay_ms"]) <= 15  # ~1.1 s at 12 fps; the period may resolve to 24-26
    explicit = loop_mod.run_loop(tmp_path / "short" / "keyed", tmp_path / "short-out2", fps=24.0, state="walk", min_len=None, max_len=None, n_out=8, seam_max=2.0, name="w8", report_path=None)
    assert explicit["n_out"] == 8
    assert rep["n_out"] <= rep["cycle"]["length"] and rep2["n_out"] <= rep2["cycle"]["length"]


def test_drop_specks_erases_detached_slivers_only() -> None:
    im = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    for y in range(5, 35):
        for x in range(10, 30):
            im.putpixel((x, y), (200, 0, 0, 255))
    im.putpixel((0, 0), (200, 0, 0, 255))  # a 1-px speck
    im.putpixel((1, 0), (200, 0, 0, 255))
    out, dropped = loop_mod._drop_specks(im, 0.01)
    assert dropped == 1 and out.getpixel((0, 0))[3] == 0 and out.getpixel((20, 20))[3] == 255


# --- batch -------------------------------------------------------------------------------


def test_prompt_uses_state_and_view_and_optional_character() -> None:
    p = batch_mod.build_prompt("side", "walk", "The armored knight")
    assert p.startswith("2D game sprite animation. The armored knight moves in place on a treadmill")
    assert "seen from the exact side" in p
    assert "The character" in batch_mod.build_prompt("back", "jump", None)


def test_motion_templates_do_not_assume_a_body_plan() -> None:
    # the templates were first written for a biped; a quadruped or a legless blob must not
    # be prompted into a contradiction (2026-09-09 generalization run)
    for state, text in batch_mod.MOTION_TEXT.items():
        for word in (
            "bipedal",
            "knees",
            "arms pumping",
            "arms swinging",
            "limbs alternating",
            "Feet never",
            "alternating strides",
            "crouch,",  # biped verb; jump uses "compress" instead
        ):
            assert word not in text, (state, word)


def test_run_set_staggers_retries_429_and_tables_failures(tmp_path: Path, monkeypatch) -> None:
    base = _still(tmp_path)
    calls: list[tuple[str, float]] = []
    import time as _time

    def fake_video(image, prompt, out, report, *, duration, resolution, log):
        calls.append((out.parent.name, _time.monotonic()))
        item = out.parent.name
        if item == "side-run" and sum(1 for c in calls if c[0] == item) == 1:
            log.write_text("video: generation request refused (HTTP 429): resource-exhausted")
            return 1
        if item == "side-jump":
            log.write_text("video: generation failed for good")
            return 1
        out.write_bytes(b"\x00\x00\x00\x18ftypisom")
        report.write_text("{}")
        log.write_text("ok")
        return 0

    monkeypatch.setattr(batch_mod.time, "sleep", lambda s: None)  # no real backoff waits
    monkeypatch.setattr(frames_mod, "run_frames", lambda clip, out_dir, **kw: {"fps": 24.0, "frames": 10, "alpha_zero_pct_min": 60.0, "alpha_zero_pct_max": 70.0, "keyed_dir": str(out_dir / "keyed")})
    monkeypatch.setattr(batch_mod.frames_mod, "run_frames", frames_mod.run_frames)
    monkeypatch.setattr(batch_mod.loop_mod, "run_loop", lambda frames_dir, out_dir, **kw: {"cycle": {"length": 12, "period_global": 12, "ratio": 0.3}, "resampled_seam_ratio": 0.5, "n_out": 12, "gif": {"file": "x.gif"}, "webp": {"file": "x.webp"}, "strip": {"path": "x.png"}})

    payload = batch_mod.run_set(bases={"side": base}, states=["walk", "run", "jump"], root=tmp_path / "set", character=None, duration=6, resolution="720p", key="green", concurrency=3, force=False, gap=0.0, video_runner=fake_video)

    by = {r["item"]: r for r in payload["items"]}
    assert by["side-walk"]["ok"] and by["side-walk"]["clip"]["attempts"] == [0]
    assert by["side-run"]["ok"] and by["side-run"]["clip"]["attempts"] == [1, 0]  # one 429 retry
    assert not by["side-jump"]["ok"] and "clip generation failed" in by["side-jump"]["error"]
    assert payload["failed"] == ["side-jump"]
    table = (tmp_path / "set" / "table.md").read_text()
    assert "| side | jump | - | - | - | - | - | FAIL" in table and "| side | walk | periodic | 12 | 12 | 0.50 | 12 | OK |" in table
    assert (tmp_path / "set" / "set.report.json").is_file()
    assert (tmp_path / "set" / "side-walk" / "canvas.png").is_file()  # canvas ran for real
