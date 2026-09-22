"""Synthetic regressions for the 2026-09 field fixes:

* gait half-period guard — a walker whose two half-strides are pixel-identical must
  still come back as a two-step period;
* one-shot detection is not bound by the periodic window's lower edge;
* `--anchor feet` removes in-canvas drift (not the step) and reports it;
* raised-limb states get a wide canvas, and video-set can force shape/anchor.
"""
from __future__ import annotations

import math
import pytest
from pathlib import Path

import numpy as np
from PIL import Image

from sprite_gen.video import batch as batch_mod
from sprite_gen.video import canvas as canvas_mod
from sprite_gen.video import loop as loop_mod


def _symmetric_walker(tmp_path: Path, *, period: int, n: int, drift_per_frame: float = 0.0, size=(220, 64), body_half: int = 6) -> list[Path]:
    """Body block + TWO legs swinging in opposition with `period`. At t + period/2 the legs
    have swapped places, so the silhouette is identical: the half period repeats exactly
    as well as the full one (the costume-hides-the-legs case a pixel rule cannot split).
    `drift_per_frame` walks the whole body across the canvas like a model that ignored
    "stays centered"."""
    d = tmp_path / "keyed"
    d.mkdir(exist_ok=True)
    files = []
    for t in range(n):
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        cx = 40 + t * drift_per_frame
        for y in range(10, 40):
            for x in range(round(cx) - body_half, round(cx) + body_half):
                im.putpixel((x, y), (200, 60, 60, 255))
        phase = 2 * math.pi * t / period
        for sign in (1, -1):
            leg_x = round(cx + sign * 8 * math.sin(phase))
            for y in range(40, 58):
                for x in range(leg_x - 3, leg_x + 3):
                    if 0 <= x < size[0]:
                        im.putpixel((x, y), (60, 60, 200, 255))
        p = d / f"frame-{t:04d}.png"
        im.save(p)
        files.append(p)
    return files


def test_gait_guard_takes_the_two_step_period_when_halves_look_alike(tmp_path: Path) -> None:
    files = _symmetric_walker(tmp_path, period=24, n=120)
    D = loop_mod.distance_matrix(files)
    plain = loop_mod.detect_cycle(D, min_len=6, max_len=48)
    gait = loop_mod.detect_cycle(D, min_len=6, max_len=48, gait_floor=round(0.6 * 24))
    # without the guard the symmetric halves fool the depth rule into one step
    assert plain["period_global"] in (11, 12, 13)
    assert plain["half_period_guard"] == {"applied": False}
    # with it, the doubled period is taken and the report says so
    assert gait["period_global"] in (23, 24, 25)
    assert gait["half_period_guard"]["applied"] is True
    assert gait["half_period_guard"]["from"] in (11, 12, 13)
    assert gait["length"] in (22, 23, 24, 25, 26)


def test_gait_guard_respects_a_period_at_or_above_the_floor(tmp_path: Path) -> None:
    # 24 frames at 24 fps is a full second: a plausible two-step walk, so even though the
    # symmetric legs make 48 repeat just as well, the guard does not double it
    files = _symmetric_walker(tmp_path, period=48, n=150)
    D = loop_mod.distance_matrix(files)
    gait = loop_mod.detect_cycle(D, min_len=6, max_len=96, gait_floor=round(0.6 * 24))
    assert gait["period_global"] in (23, 24, 25)
    assert gait["half_period_guard"]["applied"] is False


def test_gait_guard_refuses_a_lone_step_when_the_double_repeats_much_worse() -> None:
    # A distance matrix from a scalar signal: a 6-frame beat riding on a steady drift.
    # 6 repeats best; 12 repeats about twice as badly (twice the drift) — well outside
    # GAIT_DOUBLE_TOL — so under a gait floor of 10 the clip holds one step only.
    # Keeping 6 would loop half a stride without a word (a quadruped whose near and
    # far legs read alike does exactly this); the selector refuses under the same
    # words as a flat profile and says which double it weighed.
    n = 120
    f = np.array([math.sin(2 * math.pi * t / 6) + 0.05 * t for t in range(n)])
    D = np.abs(f[:, None] - f[None, :]).astype(np.float32)
    with pytest.raises(SystemExit) as exc:
        loop_mod.detect_cycle(D, min_len=4, max_len=40, gait_floor=10)
    assert str(exc.value).startswith("video-loop: no periodic cycle found")
    assert "one step (6 frames" in str(exc.value)
    g = exc.value.diagnostics["half_period_guard"]
    assert g["applied"] is False and g["below_floor"] == 6 and g["double_candidate"] in (11, 12, 13) and "why" in g


def test_gait_guard_takes_the_full_gaits_own_minimum_near_twice_the_step() -> None:
    # A stationary profile shaped like the cat clip: the one-step repeat dips at 12,
    # the full stride's own minimum sits at 22 — not at 24, which is no minimum at
    # all — and repeats about as well. Under a 14-frame floor the guard must look
    # past 2p +- 1, find 22, and take it.
    n = 96
    g = np.full(n, 0.05, dtype=np.float32)
    g[12] = 0.02
    g[22] = 0.024
    g[11] = g[13] = g[21] = g[23] = 0.045
    D = np.abs(np.subtract.outer(np.arange(n), np.arange(n)))
    D = g[D].astype(np.float32)
    c = loop_mod.detect_cycle(D, min_len=12, max_len=36, gait_floor=14)
    g2 = c["half_period_guard"]
    assert g2["applied"] is True and g2["from"] == 12 and g2["to"] == 22
    assert c["period_global"] == 22 and c["length"] in (21, 22, 23)


def test_feet_anchor_declares_the_foot_pivot_for_the_spec_loader(tmp_path: Path) -> None:
    files = _symmetric_walker(tmp_path, period=12, n=12)
    frames = [Image.open(f).convert("RGBA") for f in files]
    _, plain = loop_mod.build_strip(frames, cycle_seconds=0.5)
    _, meta = loop_mod.build_strip(frames, cycle_seconds=0.5, anchor="feet")
    assert "anchor" not in plain  # loader falls back to bottom-centre as before
    assert meta["anchor"] == [meta["foot_x"], meta["h"]]


def test_walk_and_run_profiles_are_gaits_with_a_floor() -> None:
    assert loop_mod.profile_for("walk").gait and loop_mod.profile_for("walk").min_seconds == 0.6
    assert loop_mod.profile_for("run").gait and loop_mod.profile_for("run").min_seconds == 0.35
    assert not loop_mod.profile_for("idle").gait and not loop_mod.profile_for("jump").gait


def _rest_then_short_hop(tmp_path: Path, *, n: int = 80, hop: tuple[int, int] = (40, 44)) -> list[Path]:
    d = tmp_path / "keyed"
    d.mkdir(exist_ok=True)
    files = []
    for t in range(n):
        im = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        dy = -10 if hop[0] <= t <= hop[1] else 0
        for y in range(20 + dy, 44 + dy):
            for x in range(18, 30):
                im.putpixel((x, y), (200, 60, 60, 255))
        p = d / f"frame-{t:04d}.png"
        im.save(p)
        files.append(p)
    return files


def test_one_shot_accepts_a_short_excursion_below_the_periodic_window(tmp_path: Path) -> None:
    files = _rest_then_short_hop(tmp_path)
    D = loop_mod.distance_matrix(files)
    # 5 active frames + 2 rest each side = 9 < the periodic lower bound of 16
    cycle = loop_mod.detect_one_shot(D, min_len=16, max_len=70)
    assert cycle["kind"] == "one-shot"
    assert cycle["length"] == 9
    assert cycle["excursion"] == [40, 44]


def test_feet_anchor_undoes_in_canvas_drift_and_reports_it(tmp_path: Path) -> None:
    files = _symmetric_walker(tmp_path, period=12, n=24, drift_per_frame=1.5)
    frames = [Image.open(f).convert("RGBA") for f in files]
    plain, plain_meta = loop_mod.build_strip(frames, cycle_seconds=1.0)
    anchored, meta = loop_mod.build_strip(frames, cycle_seconds=1.0, anchor="feet")
    assert plain_meta["foot_anchor"] == "none" and plain_meta["drift_px"] == 0
    assert meta["foot_anchor"] == "feet"
    assert 30 <= meta["drift_px"] <= 40  # 23 frames * 1.5 px of authored drift
    # every anchored cell has its foot line on the same column
    w, h = meta["w"], meta["h"]
    centres = []
    for k in range(meta["frames"]):
        cell = anchored.crop((k * w, 0, (k + 1) * w, h))
        a = np.asarray(cell.getchannel("A"))
        band = a[h - max(1, round(h * loop_mod.FOOT_BAND)) :, :]
        xs = np.nonzero(band >= 8)[1]
        centres.append(xs.mean())
    assert max(centres) - min(centres) <= 1.5
    assert abs(meta["foot_x"] - centres[0]) <= 2
    # and the plain strip still carries the drift (the bug this guards against)
    plain_centres = []
    pw = plain_meta["w"]
    for k in range(plain_meta["frames"]):
        cell = plain.crop((k * pw, 0, (k + 1) * pw, plain_meta["h"]))
        a = np.asarray(cell.getchannel("A"))
        band = a[plain_meta["h"] - max(1, round(plain_meta["h"] * loop_mod.FOOT_BAND)) :, :]
        plain_centres.append(np.nonzero(band >= 8)[1].mean())
    assert max(plain_centres) - min(plain_centres) > 20


def test_run_loop_rejects_an_unknown_anchor(tmp_path: Path) -> None:
    _symmetric_walker(tmp_path, period=12, n=30)
    import pytest

    with pytest.raises(SystemExit, match="--anchor"):
        loop_mod.run_loop(tmp_path / "keyed", tmp_path / "out", fps=24, state="walk", min_len=None, max_len=None, n_out=None, seam_max=9.0, name="x", report_path=None, anchor="hips")


def test_raised_limb_states_get_a_wide_canvas() -> None:
    for state in ("cheer", "wave", "celebrate"):
        prof = canvas_mod.profile_for(state)
        assert prof.shape == canvas_mod.SHAPE_WIDE and prof.lead >= 0.28
    assert canvas_mod.profile_for("idle").shape == canvas_mod.SHAPE_SQUARE


@pytest.mark.parametrize("state,anchor", [("cheer", "feet"), ("walk", "motion-auto")])
def test_video_set_passes_shape_and_anchor_through(tmp_path: Path, monkeypatch, state, anchor) -> None:
    seen: dict[str, object] = {}

    def fake_canvas(base, out, *, state, shape, facing, headroom, lead, report_path):
        seen["shape"] = shape
        Image.new("RGB", (32, 32), (0, 255, 0)).save(out)
        return {"shape": shape or "square", "canvas": [32, 32], "offset": [0, 0]}

    def fake_frames(clip, out_dir, *, key, allow_edge_contact, report_path, spill, reference):
        seen["spill"] = (spill, Path(reference).name)
        keyed = Path(out_dir) / "keyed"
        keyed.mkdir(parents=True, exist_ok=True)
        _symmetric_walker(Path(out_dir), period=12, n=30)
        return {"fps": 24.0, "frames": 30, "alpha_zero_pct_min": 0.0, "alpha_zero_pct_max": 0.0, "keyed_dir": str(keyed), "spill": {"mode": "full"}}

    def fake_loop(frames_dir, out_dir, **kw):
        seen["anchor"] = kw.get("anchor")
        return {"cycle": {"kind": "periodic", "length": 24, "period_global": 24, "ratio": 0.2}, "resampled_seam_ratio": 0.3, "n_out": 8, "gif": {"file": "g"}, "webp": {"file": "w"}, "strip": {"path": "s", "drift_px": 0}}

    def fake_video(image, prompt, out, report, *, duration, resolution, log):
        Path(out).write_bytes(b"x")
        Path(report).write_text("{}")
        Path(log).write_text("")
        return 0

    # This routing test must not inspect its synthetic still through a live provider.
    monkeypatch.setattr(batch_mod.facing_mod.vision, "grok_inspect", lambda *a, **kw: ("right", {}))
    monkeypatch.setattr(batch_mod.canvas_mod, "run_canvas", fake_canvas)
    monkeypatch.setattr(batch_mod.frames_mod, "run_frames", fake_frames)
    monkeypatch.setattr(batch_mod.loop_mod, "run_loop", fake_loop)
    base = tmp_path / "base.png"
    Image.new("RGB", (32, 32), (0, 255, 0)).save(base)
    payload = batch_mod.run_set(bases={"side": base}, states=[state], root=tmp_path / "set", character=None, duration=6, resolution="720p", key="green", concurrency=1, force=False, gap=0.0, video_runner=fake_video, shape="wide", anchor=anchor)
    assert payload["ok"] == 1
    # the frames step is told to judge spill from the item's own canvas still
    assert seen == {"shape": "wide", "anchor": anchor, "spill": ("auto", "canvas.png")}


def test_cheer_motion_template_names_no_limbs() -> None:
    text = batch_mod.MOTION_TEXT["cheer"] + batch_mod.MOTION_TEXT["wave"]
    for limb in ("arm", "leg", "knee", "hand", "foot", "wing"):
        assert limb not in text.lower()


def _lifting_walker(tmp_path: Path, *, period: int, n: int, drift_per_frame: float = 0.0, size=(220, 64)) -> list[Path]:
    """A body that stays put while its legs take turns: each half of the period one leg
    stands on the floor and the other is lifted clear of the lowest rows. The mean x of
    the floor band therefore jumps from one foot to the other every step even though the
    body never moves — the in-place walk a video model actually returns."""
    d = tmp_path / "keyed"
    d.mkdir(exist_ok=True)
    files = []
    for t in range(n):
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        cx = 60 + t * drift_per_frame
        for y in range(10, 40):
            for x in range(round(cx) - 6, round(cx) + 6):
                im.putpixel((x, y), (200, 60, 60, 255))
        phase = 2 * math.pi * t / period
        stride = round(14 * math.sin(phase))
        for sign in (1, -1):
            leg_x = round(cx + sign * stride)
            lifted = sign * stride < 0  # the trailing leg is in the air
            bottom = 50 if lifted else 58
            for y in range(40, bottom):
                for x in range(leg_x - 3, leg_x + 3):
                    if 0 <= x < size[0]:
                        im.putpixel((x, y), (60, 60, 200, 255))
        p = d / f"frame-{t:04d}.png"
        im.save(p)
        files.append(p)
    return files


def _cell_body_centres(strip: Image.Image, meta: dict) -> list[float]:
    """x of the body block (the red rows above the legs) inside every cell."""
    w, h = meta["w"], meta["h"]
    out = []
    for k in range(meta["frames"]):
        cell = np.asarray(strip.crop((k * w, 0, (k + 1) * w, h)))
        red = (cell[:, :, 0] > 150) & (cell[:, :, 2] < 120) & (cell[:, :, 3] >= 8)
        out.append(float(np.nonzero(red)[1].mean()))
    return out


def test_feet_anchor_does_not_sway_a_body_that_stands_still(tmp_path: Path) -> None:
    # The per-frame foot line of an in-place walk jumps between the planted feet; pinning
    # it made the whole body lurch back and forth by the stride. Only drift is removed.
    files = _lifting_walker(tmp_path, period=12, n=24)
    frames = [Image.open(f).convert("RGBA") for f in files]
    anchored, meta = loop_mod.build_strip(frames, cycle_seconds=1.0, anchor="feet")
    body = _cell_body_centres(anchored, meta)
    assert max(body) - min(body) <= 1.5
    assert meta["drift_px"] <= 2  # nothing moved, nothing was removed
    assert meta["foot_sway_px"] >= 10  # the planted foot does change — reported, not erased


def test_feet_anchor_removes_drift_but_keeps_the_step(tmp_path: Path) -> None:
    files = _lifting_walker(tmp_path, period=12, n=24, drift_per_frame=1.5)
    frames = [Image.open(f).convert("RGBA") for f in files]
    anchored, meta = loop_mod.build_strip(frames, cycle_seconds=1.0, anchor="feet")
    body = _cell_body_centres(anchored, meta)
    assert max(body) - min(body) <= 1.5
    assert 30 <= meta["drift_px"] <= 40  # 23 frames * 1.5 px of authored drift
