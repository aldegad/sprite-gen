# SPDX-License-Identifier: Apache-2.0
"""The redrawn wrap of a pinned clip (`video-loop --wrap-blend`, sprite_gen/video/wrap_blend.py, docs/loop-repair.md
section 6). A video model given the first frame as the last draws it again; the loop's last half second takes the first
frame's drawing back, and only a redraw: a part, a line, a dot the two ends hold in different places is never drawn a
second time, nor is a cell that does not stand where the pin stands.

Synthetic clips only. The character is a pinned idle of 73 frames (72 cells and the pin, K = 12 at 24 fps) whose frames
after the first are the model's drawing of it — another texture, hair strands up to 0.6 px over, 0.5 px softer, another
tone — so the pin, the redraw's ruler and the after check pass, and the gate is the one thing between a structure on it
and the ramp. A flat figure has no redraw: its cases are read on the gate and the ramp alone."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import as_pinned
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import wrap_blend
from tests.video.test_idle_still import _still_frames

N, K, W, H, SS = 73, 12, 160, 224, 2
FPS = 24.0
SEAM_MAX = loop_mod.SEAM_RATIO_MAX


# --- the synthetic character: one pose in two drawings, the still and the model's redraw ------------------------------

def _box(x: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return x
    p = np.pad(x, ((r, r), (r, r)) + ((0, 0),) * (x.ndim - 2), mode="edge")
    c = np.cumsum(p, 0)
    c = np.concatenate([np.zeros_like(c[:1]), c], 0)
    x = (c[2 * r + 1:] - c[:-2 * r - 1]) / (2 * r + 1)
    c = np.cumsum(x, 1)
    c = np.concatenate([np.zeros_like(c[:, :1]), c], 1)
    return (c[:, 2 * r + 1:] - c[:, :-2 * r - 1]) / (2 * r + 1)


def _blur(x: np.ndarray, sigma: float) -> np.ndarray:
    """Three box passes for a Gaussian; a fractional radius blends two."""
    if sigma <= 0:
        return x
    r = sigma * np.sqrt(12 / 3 + 1) / 2 - 0.5
    lo = int(np.floor(r))
    f = r - lo
    a = x
    for _ in range(3):
        a = _box(a, lo)
    if f > 0:
        b = x
        for _ in range(3):
            b = _box(b, lo + 1)
        a = (1 - f) * a + f * b
    return a


def _texture(seed: int, h: int, w: int) -> np.ndarray:
    n = np.random.default_rng(seed).normal(0, 1, (h // 4 + 2, w // 4 + 2))
    n = np.kron(n, np.ones((4, 4)))[:h, :w]
    return _blur(n[..., None], 1.5)[..., 0]


def render(pose: dict[str, float], style: float, *, sigma_still: float = 0.0, sigma_model: float = 0.5,
           tone_model: tuple[int, int, int] = (-4, 3, 3), redraw: float = 0.3, strands: float = 0.6) -> np.ndarray:
    """One frame, straight RGBA floats 0..255. pose: dy, arm (degrees); style 0 is the still, 1 the model's drawing:
    another cloth texture (`redraw`), hair strands moved up to `strands` px, `sigma_model` px softer, `tone_model`."""
    h, w = H * SS, W * SS
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32) / SS
    dy = pose.get("dy", 0.0)
    rgb = np.zeros((h, w, 3), np.float32)
    a = np.zeros((h, w), np.float32)

    def put(mask: np.ndarray, colour: tuple[int, int, int]) -> None:
        nonlocal rgb
        m = np.clip(mask, 0, 1)[..., None]
        rgb = rgb * (1 - m) + np.array(colour, np.float32) * m
        a[:] = np.maximum(a, m[..., 0])

    def ellipse(cx: float, cy: float, rx: float, ry: float) -> np.ndarray:
        d = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
        return np.clip((1 - d) * min(rx, ry) * SS / 1.5, 0, 1)

    tex = (1 - redraw * style) * _texture(1, h, w) + redraw * style * _texture(2, h, w)
    m = np.clip(ellipse(80, 150 + dy, 34, 52), 0, 1)[..., None]
    cloth = np.array((70, 110, 160), np.float32) + 22 * tex[..., None] + 30 * (np.sin(xx / 3.0) > 0.6)[..., None]
    rgb = rgb * (1 - m) + cloth * m
    a = np.maximum(a, m[..., 0])
    ang = np.deg2rad(pose.get("arm", 100.0))
    sx, sy, length = 108, 112 + dy, 46
    ex, ey = sx + length * np.cos(ang), sy + length * np.sin(ang)
    t = np.clip(((xx - sx) * (ex - sx) + (yy - sy) * (ey - sy)) / (length * length), 0, 1)
    d = np.hypot(xx - (sx + t * (ex - sx)), yy - (sy + t * (ey - sy)))
    put(np.clip((7 - d) * SS / 1.5, 0, 1), (200, 150, 120))
    put(ellipse(ex, ey, 8, 8), (235, 190, 160))
    hy = 70 + dy
    put(ellipse(80, hy, 30, 32), (240, 200, 175))
    for eye in (68, 92):
        put(ellipse(eye, hy - 2, 5, 7), (40, 30, 60))
        put(ellipse(eye + 1.5, hy - 4, 1.6, 1.6), (255, 255, 255))
    put(np.clip((1.2 - np.abs(yy - (hy + 16))) * SS, 0, 1) * (np.abs(xx - 80) < 7), (150, 60, 60))
    for k in range(6):
        off = (k - 2.5) * 7 + strands * style * np.sin(k * 1.7)
        put(np.clip((1.0 - np.abs(xx - (80 + off + 0.25 * (yy - hy + 30)))) * SS, 0, 1) * ((yy > hy - 32) & (yy < hy - 14)),
            (120, 50, 30))
    sigma = (1 - style) * sigma_still + style * sigma_model
    pm = _blur(np.concatenate([rgb * a[..., None], a[..., None] * 255], -1), sigma * SS)
    pm = pm.reshape(H, SS, W, SS, 4).mean((1, 3))
    alpha = pm[..., 3:] / 255
    col = np.where(alpha > 1e-4, pm[..., :3] / np.maximum(alpha, 1e-4), 0) + style * np.array(tone_model, np.float32) * (alpha > 0)
    return np.clip(np.concatenate([np.clip(col, 0, 255), pm[..., 3:]], -1) + 0.5, 0, 255).astype(np.uint8).astype(np.float32)


def poses(case: str, n: int = N, breath: float = 2.0) -> list[dict[str, float]]:
    out = []
    for t in range(n):
        pose = {"dy": breath * np.sin(np.pi * t / (n - 1)) ** 2, "arm": 100.0}
        if case == "attack-late":
            # the arm is up from frame 6 until four frames before the pin, then falls back to rest
            pose["arm"] = 100.0 - 80.0 * np.clip((t - 6) / 6, 0, 1) * np.clip((n - 1 - t) / 4, 0, 1)
        out.append(pose)
    return out


@lru_cache(maxsize=None)
def character(case: str = "idle", n: int = N, breath: float = 2.0, settle: int = 6, **kw: object) -> tuple[np.ndarray, ...]:
    """A pinned clip: frame 0 the still, every later frame (the pin too) the model's drawing, which it takes over
    `settle` frames."""
    return tuple(render(p, min(1.0, t / settle), **kw) for t, p in enumerate(poses(case, n, breath)))  # type: ignore[arg-type]


# --- structures on a flat patch of the character's torso (the checkpoint's construction) -------------------------------

Y0, Y1, X0, X1 = 100, 190, 36, 124
CY, CX = 145, 80
AIR = (50, 138)  # transparent ground right of the head, above the arm


def _raster(points: list[tuple[float, float]], ss: int = 1) -> np.ndarray:
    g = np.zeros((H * ss, W * ss), np.float32)
    for y, x in points:
        yi, xi = int(np.floor(y * ss)), int(np.floor(x * ss))
        if 0 <= yi < H * ss and 0 <= xi < W * ss:
            g[yi, xi] = 1.0
    if ss > 1:
        g = np.minimum(g.reshape(H, ss, W, ss).mean((1, 3)) * ss, 1.0)
    return g


def _line(theta: float, length: float = 56, dy: float = 0.0, dx: float = 0.0, every: int = 1, aa: bool = False,
          centre: tuple[float, float] = (CY, CX)) -> list[tuple[float, float]]:
    t = np.arange(-length / 2, length / 2, 0.1)
    ys, xs = centre[0] + dy + t * np.sin(theta), centre[1] + dx + t * np.cos(theta)
    if aa:
        return list(zip(ys, xs))
    pts: list[tuple[int, int]] = []
    for y, x in zip(np.round(ys).astype(int), np.round(xs).astype(int)):
        if not pts or pts[-1] != (y, x):
            pts.append((y, x))
    return [(y + 0.5, x + 0.5) for y, x in pts[::every]]


def ink(kind: str, dy: int, dx: int, **kw: object) -> np.ndarray:
    if kind == "line":
        theta = np.deg2rad(float(kw["angle"]))
        if kw.get("air"):
            return _raster(_line(theta, 40, dy, dx, centre=AIR))
        return _raster(_line(theta, dy=dy, dx=dx, every=int(kw.get("every", 1)), aa=bool(kw.get("aa"))), ss=4 if kw.get("aa") else 1)
    g = np.zeros((H, W), np.float32)
    if kind == "oblique":  # the checkpoint's line, slope 1/3, line for line
        for y in range(110, 180):
            g[y, 55 + (y - 110) // 3 + dx] = 1.0
        return g
    if kind == "dots":
        s = int(kw.get("size", 1))
        y, x = AIR if kw.get("air") else (CY, CX)
        g[y + dy:y + dy + s, x + dx:x + dx + s] = 1.0
        return g
    if kind == "ring":
        return _raster([(CY + dy + 12 * np.sin(v), CX + dx + 12 * np.cos(v)) for v in np.arange(0, 2 * np.pi, 0.01)])
    if kind == "wave":
        return _raster([(CY + dy + 9 * np.sin(v / 5), CX + dx + v) for v in np.arange(-28, 28, 0.05)])
    if kind == "cross":
        return np.maximum(_raster(_line(0.0, 16, dy, dx)), _raster(_line(np.pi / 2, 16, dy, dx)))
    raise ValueError(kind)


def patch_clip(kind: str, d: int, *, contrast: int = 24, mode: str = "move", sign: int = -1, channel: int | None = None,
               noise: float = 0.0, ground: float = 0.0, texture: tuple[str, float] | None = None, soft: float | None = None,
               **kw: object) -> tuple[list[np.ndarray], np.ndarray]:
    """The character with its torso patch [100:190, 36:124] flat RGB 180 on every frame (a gradient of `ground` levels
    a px across x, a static `texture` of grain or a 1 px checker, decode noise of `noise` levels on every frame), and a
    structure `contrast` darker (lighter with sign +1, one channel with `channel`) moved d px across itself over the first
    six frames; `mode` appear: only in the still, vanish: only from frame 6 on. `air` structures are opaque strands on the
    transparent ground. Returns the frames and the structure's region: what it changes in the still or in the pin."""
    if kind == "line":
        th = np.deg2rad(float(kw["angle"]))
        ny, nx = np.cos(th), -np.sin(th)
    else:
        ny, nx = 0.0, 1.0
    base = character(**({} if soft is None else {"sigma_model": soft}))
    frames, inks = [], []
    for t, f0 in enumerate(base):
        f = f0.copy()
        yy, xx = np.mgrid[Y0:Y1, X0:X1].astype(np.float32)
        f[Y0:Y1, X0:X1, :3] = np.round(np.clip(180 + ground * (xx - CX), 0, 255))[..., None]
        f[Y0:Y1, X0:X1, 3] = 255
        if texture:
            name, amp = texture
            g = (np.random.default_rng(77).normal(0, amp, (Y1 - Y0, X1 - X0)) if name == "noise"
                 else amp * (2 * ((yy + xx) % 2) - 1.0))
            f[Y0:Y1, X0:X1, :3] = np.clip(np.round(f[Y0:Y1, X0:X1, :3] + g[..., None]), 0, 255)
        u = min(t / 6, 1)
        on = not ((mode == "appear" and t >= 6) or (mode == "vanish" and t < 6))
        m = ink(kind, int(round(d * ny * u)), int(round(d * nx * u)), **kw) if on else np.zeros((H, W), np.float32)
        if kw.get("air"):
            f[m > 0, :3] = (60, 40, 30)
            f[..., 3] = np.maximum(f[..., 3], np.round(255 * float(kw.get("alpha", 1.0)) * m))
        else:
            m[:Y0] = 0
            m[Y1:] = 0
            m[:, :X0] = 0
            m[:, X1:] = 0
            for c in ([0, 1, 2] if channel is None else [channel]):
                f[..., c] = np.clip(np.round(f[..., c] + sign * contrast * m), 0, 255)
        if noise:
            g = np.random.default_rng(1000 + t).normal(0, noise, (Y1 - Y0, X1 - X0, 3))
            f[Y0:Y1, X0:X1, :3] = np.clip(np.round(f[Y0:Y1, X0:X1, :3] + g), 0, 255)
        frames.append(f)
        inks.append(m)
    return frames, (inks[0] > 0) | (inks[-1] > 0)


# --- a flat figure: no redraw, the gate read on its own ------------------------------------------------------------------

def _flat_base(t: int) -> np.ndarray:
    f = np.zeros((H, W, 4), np.float32)
    f[20:204, 20:140, :3] = 180 if t >= N - K - 1 else 180 + round(2 * np.sin(2 * np.pi * t / 9))
    f[20:204, 20:140, 3] = 255
    return f


def flat_clip(d: int, *, contrast: int = 120, mode: str = "move", line: bool = False) -> tuple[list[np.ndarray], np.ndarray]:
    """A flat figure and a 14x14 part (or a 1 px vertical line) `contrast` darker that moves d px over the first six
    frames and stays (appear: only in the still; vanish: only from frame 6 on). Returns the frames and the region the
    part changes in the still or in the pin."""
    frames, region = [], np.zeros((H, W), bool)
    for t in range(N):
        f = _flat_base(t)
        x = 60 + round(d * min(t / 6, 1))
        on = not ((mode == "appear" and t >= 6) or (mode == "vanish" and t < 6))
        here = np.zeros((H, W), bool)
        if on and line:
            here[60:160, x] = True
            f[here, :3] -= contrast
        elif on:
            here[76:76 + 14, x:x + 14] = True
            f[here, :3] = 180 - contrast
        if t in (0, N - 1):
            region |= here
        frames.append(f)
    return frames, region


# --- reading a blend -----------------------------------------------------------------------------------------------------

def _image(f: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(f, 0, 255).astype(np.uint8), "RGBA")


def pin_closes(frames: list[np.ndarray]) -> bool:
    features = np.stack([loop_mod._small_features(_image(f)) for f in frames])
    cycle = loop_mod.pinned_cycle(loop_mod._distances(features), seam_max=SEAM_MAX)
    return cycle["pin_error"] <= cycle["pin_tolerance"]


def applied(frames: list[np.ndarray]) -> tuple[list[np.ndarray], dict]:
    """What the loop writes for a pinned cut of these frames: `wrap_blend.apply` on the cells and the pin."""
    cells, record = wrap_blend.apply([_image(f) for f in frames[:-1]], _image(frames[-1]), fps=FPS, seam_max=SEAM_MAX)
    return [np.asarray(c, dtype=np.float32) for c in cells], record


def gated(frames: list[np.ndarray]) -> list[np.ndarray]:
    """The gate and the ramp alone (no ruler, no after check): the cells of the ramp as the blend would write them."""
    cells = frames[:-1]
    ramp = [cells[k] for k in range(len(cells) - K, len(cells))]
    settled, _ = wrap_blend.gate(cells[0], frames[-1], ramp)
    out, _ = wrap_blend.blend(cells[0], frames[-1], ramp, settled)
    return list(cells[:len(cells) - K]) + out


def shown(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The largest change between a and b as either shows over black or over white, per pixel, 0..255."""
    def grounds(c: np.ndarray) -> np.ndarray:
        p = np.concatenate([c[..., :3] * c[..., 3:] / 255, c[..., 3:]], -1)
        return np.concatenate([p[..., :3], p[..., :3] + 255 - p[..., 3:]], -1)
    return np.abs(grounds(a) - grounds(b)).max(-1)


def change(out: list[np.ndarray], frames: list[np.ndarray], region: np.ndarray, cells: range | None = None) -> float:
    cells = cells if cells is not None else range(len(out) - K, len(out))
    return max(float(shown(out[k], frames[k])[region].max()) for k in cells)


def identical(out: list[np.ndarray], frames: list[np.ndarray], region: np.ndarray, cells: range | None = None) -> bool:
    cells = cells if cells is not None else range(len(out) - K, len(out))
    return all(np.array_equal(out[k][region], frames[k][region]) for k in cells)


def keyed(tmp_path: Path, frames: list[np.ndarray] | tuple[np.ndarray, ...], name: str = "keyed") -> Path:
    d = tmp_path / name
    d.mkdir()
    for t, f in enumerate(frames):
        _image(f).save(d / f"frame-{t:04d}.png")
    return d


def cut(tmp_path: Path, frames_dir: Path, out: str, **kw: object) -> dict:
    kw = {"cycle_mode": "pinned", "seam_max": SEAM_MAX, **kw}
    return loop_mod.run_loop(frames_dir, tmp_path / out, fps=FPS, state="idle", min_len=None, max_len=None, n_out=None,
                             name="idle", report_path=None, **kw)  # type: ignore[arg-type]


def outputs(out_dir: Path, cells: str = "cycle") -> dict[str, bytes]:
    """The cut's cells, strip, GIF and WebP as written (a loop refused on its pin hands none over and keeps its cells as
    `cycle.refused/`, docs/video-pipeline.md section 4)."""
    files = sorted((out_dir / cells).glob("frame-*.png")) + [out_dir / f"idle.{x}" for x in ("strip.png", "gif", "webp")]
    return {f.name: f.read_bytes() for f in files if f.exists()}


# --- 1. a redraw: tone and a little softness ------------------------------------------------------------------------------

REDRAW = {"redraw": 0.0, "strands": 0.0, "breath": 6.0}  # the model's tone and 0.5 px of softness, nothing else


@pytest.mark.slow
def test_a_pin_that_redraws_the_first_frame_has_its_wrap_blended_to_under_a_step(tmp_path: Path) -> None:
    frames = character(**REDRAW)
    # The blend alone: an idle's wrap that jumps is the seam bridge's too (docs/loop-repair.md section 2), which cuts
    # the off clip again round its wrap where RIFE is installed; `--repair off` keeps the bridge out of both cuts.
    report = cut(tmp_path, keyed(tmp_path, frames), "auto", repair="off")
    blend = report["wrap_blend"]
    assert blend["applied"] is True and blend["K"] == K
    assert blend["residual_ratio"] > SEAM_MAX and blend["wrap_ratio_before"] > SEAM_MAX
    assert blend["wrap_ratio_after"] < 1.0
    assert blend["cells"] == list(range(N - 1 - K, N - 1))
    assert blend["equalize"] == {"sigma": 0.5, "blurred": "still"}
    assert {"pose_share", "presence_share", "never_settled", "ramp_step_max_ratio", "bound_ratio", "seconds"} <= set(blend)
    strip = json.loads((tmp_path / "auto" / "idle.strip.json").read_text())
    assert strip["wrap_blend"] == {"cells": blend["cells"], "K": K}
    off = cut(tmp_path, keyed(tmp_path, frames, "keyed-off"), "off", wrap_blend="off", repair="off")
    assert off["wrap_blend"] == {"applied": False, "why": "--wrap-blend off"} and "wrap_blend" not in off["strip"]
    a, b = outputs(tmp_path / "auto"), outputs(tmp_path / "off")
    changed = {name for name in a if a[name] != b[name]}
    assert changed == {f"frame-{k:03d}.png" for k in blend["cells"]} | {"idle.strip.png", "idle.gif", "idle.webp"}


# --- 2. a ramp cell that does not stand where the pin stands ---------------------------------------------------------------

@pytest.mark.slow
def test_a_cell_whose_arm_is_not_yet_where_the_pin_holds_it_keeps_its_arm_as_filmed() -> None:
    frames = list(character("attack-late"))
    assert pin_closes(frames)
    out, record = applied(frames)
    assert record["applied"] is True
    # The arm in its own pose against the pin's, in one drawing and at the pin's breath: where it is not where the pin
    # holds it. Raised (cells 60..68) and on its way down while 40 degrees or more off, it is the filmed arm; on the last
    # cell, 20 degrees off, its fringe is within the slack a cell stands by (1.5 x the redraw + 2 on a 7x7 mean).
    model = poses("attack-late")
    pin = render(model[-1], 1.0)
    for k in range(N - 1 - K, N - 1):
        apart = shown(render({**model[k], "dy": model[-1]["dy"]}, 1.0), pin) > 24
        if abs(model[k]["arm"] - model[-1]["arm"]) >= 40:
            assert np.array_equal(out[k][apart], frames[k][apart]), k
        else:
            assert float(shown(out[k], frames[k])[apart].max()) <= 1, k


# --- 3. a part the two ends hold in different places ---------------------------------------------------------------------

@pytest.mark.parametrize("d", [2, 4, 8, 16, 32, 64])
def test_a_part_moved_between_the_ends_of_a_flat_figure_is_never_drawn_twice(d: int) -> None:
    frames, region = flat_clip(d)
    assert identical(gated(frames), frames, region)


@pytest.mark.parametrize("mode", ["appear", "vanish"])
def test_a_part_only_one_end_holds_is_never_drawn_on_the_other(mode: str) -> None:
    frames, region = flat_clip(16, mode=mode)
    assert identical(gated(frames), frames, region)


def _part_on_the_torso(d: int, mode: str = "move") -> tuple[list[np.ndarray], np.ndarray]:
    """A 10x10 part (RGB 60) on the character's torso moving d px over the first six frames."""
    base = character()
    frames = []
    for t, f in enumerate(base):
        f = f.copy()
        x = 50 + round(d * min(t / 6, 1))
        if not ((mode == "appear" and t >= 6) or (mode == "vanish" and t < 6)):
            f[150:160, x:x + 10, :3] = 60
        frames.append(f)
    return frames, ((frames[0] != base[0]).any(-1) | (frames[-1] != base[-1]).any(-1))


@pytest.mark.slow
@pytest.mark.parametrize("d,mode", [(2, "move"), (8, "move"), (32, "move"), (16, "appear"), (16, "vanish")])
def test_a_part_moved_on_a_redrawn_character_is_held_while_the_rest_is_blended(d: int, mode: str) -> None:
    frames, region = _part_on_the_torso(d, mode)
    assert pin_closes(frames)
    out, record = applied(frames)
    assert record["applied"] is True  # the pin, the ruler and the after check pass: the gate alone holds the part
    assert identical(out, frames, region)


@pytest.mark.slow
@pytest.mark.timeout(900)  # 16 to 24 calls of the binary at 160 x 224 (the held fill's frames between, both cuts): seconds on a GPU, minutes on a CPU's Vulkan driver
def test_the_checkpoints_part_on_a_flat_figure_is_cut_as_filmed_with_a_warning(tmp_path: Path, capsys, rife_runs) -> None:
    frames = []
    for t in range(N):
        f = _flat_base(t)
        x = 60 + round(16 * min(t / 6, 1))
        f[76:90, x:x + 14, :3] = 60
        frames.append(f)
    # The blend alone: the wrap the blend leaves jumps, so where RIFE is installed the seam bridge would make frames round
    # it in both cuts (on a CPU Vulkan driver, longer than the test's time); `--repair off` keeps the bridge out of both.
    report = cut(tmp_path, keyed(tmp_path, frames), "auto", repair="off")
    blend = report["wrap_blend"]
    assert blend["applied"] is False and blend["after_check"] == "refused" and blend["residual_ratio"] > SEAM_MAX
    assert "the wrap blend was not used" in capsys.readouterr().err
    cut(tmp_path, keyed(tmp_path, frames, "keyed-off"), "off", wrap_blend="off", repair="off")
    assert outputs(tmp_path / "auto") == outputs(tmp_path / "off")
    assert as_pinned(rife_runs(), {"rife-v4.25": (8, 24), "rife-v4.6": 16}), rife_runs()


# --- 4. a 1 px line on a flat figure ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("contrast", [24, 48, 96])
@pytest.mark.parametrize("d", [2, 8, 32])
def test_a_one_pixel_line_moved_on_a_flat_figure_is_never_drawn_twice(contrast: int, d: int) -> None:
    frames, region = flat_clip(d, contrast=contrast, line=True)
    assert identical(gated(frames), frames, region)


# --- 5. a ramp cell that swings off the pin's place where the ends differ by a redraw ---------------------------------------

def test_stripes_that_swing_three_pixels_inside_the_ramp_keep_their_swing() -> None:
    frames, swing = [], range(N - K - 1, N - K + 8)
    for t in range(N):
        f = _flat_base(t)
        off = 0.0 if t == 0 else 0.8 + (3.0 if t in swing else 0.0)  # a 0.8 px redraw, then a 3 px swing and back
        for x0 in range(30, 125, 5):
            p = x0 + off
            i = int(np.floor(p))
            f[30:190, i, :3] -= (1 - (p - i)) * 100
            f[30:190, i + 1, :3] -= (p - i) * 100
        frames.append(f)
    region = np.zeros((H, W), bool)
    region[28:192, 26:132] = True
    out = gated(frames)
    assert identical(out, frames, region, range(N - 1 - K, swing.stop))


# --- 6. a strand at the silhouette's edge ------------------------------------------------------------------------------------

def _edge_strand() -> tuple[list[np.ndarray], np.ndarray]:
    """A dark 1 px strand against a flat figure's right edge, on the transparent ground, moved 7 px out."""
    frames, region = [], np.zeros((H, W), bool)
    for t in range(N):
        f = _flat_base(t)
        x = 140 + round(7 * min(t / 6, 1))
        f[60:160, x] = (40, 40, 40, 255)
        if t in (0, N - 1):
            region[60:160, x] = True
        frames.append(f)
    return frames, region


def test_a_strand_off_the_silhouette_is_held_over_white_where_black_alone_lets_it_through(monkeypatch) -> None:
    frames, region = _edge_strand()
    assert identical(gated(frames), frames, region)
    # Over black alone, the transparent neighbour (0, 0, 0, 0) puts the dark strand in range: the second ground holds it.
    monkeypatch.setattr(wrap_blend, "_grounds", lambda p: p)
    assert not identical(gated(frames), frames, region)


# --- 7. the softness match -----------------------------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize("kw,expected", [
    ({}, {"sigma": 0.5, "blurred": "still"}),  # the model draws 0.5 px softer: the still is blurred
    ({"sigma_still": 0.5, "sigma_model": 0.0}, {"sigma": 0.5, "blurred": "pin"}),  # the still is the softer: the pin
    ({"sigma_model": 1.5}, {"sigma": 0.7, "blurred": "still"}),  # never wider than 0.7 px
])
def test_the_sharper_end_is_blurred_toward_the_softer_never_past_seven_tenths_of_a_pixel(kw: dict, expected: dict) -> None:
    frames = character(**kw)
    assert wrap_blend.equalize(frames[0], frames[-1])[2] == expected


# --- 8. no redraw ------------------------------------------------------------------------------------------------------------

def test_a_pin_that_is_the_first_frame_again_leaves_every_output_byte_for_byte(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    report = cut(tmp_path, _still_frames(tmp_path / "a"), "auto")
    blend = report["wrap_blend"]
    assert blend["applied"] is False and blend["residual_ratio"] <= SEAM_MAX and "draws the first as it is" in blend["why"]
    cut(tmp_path, _still_frames(tmp_path / "b"), "off", wrap_blend="off")
    assert outputs(tmp_path / "auto") == outputs(tmp_path / "off")


# --- 9. cuts the blend does not read -------------------------------------------------------------------------------------------

@pytest.mark.slow
def test_a_cut_that_is_not_pinned_is_not_touched(tmp_path: Path) -> None:
    # The redraw's wrap reads 2.9x the mean step on the seam gate's thumbnail: the gate is opened past it for both cuts.
    frames = character(**REDRAW)
    fixed = {"cycle_mode": "fixed", "start": 0, "length": N - 1, "seam_max": 4.0}
    report = cut(tmp_path, keyed(tmp_path, frames), "auto", **fixed)
    assert "wrap_blend" not in report and "wrap_blend" not in report["strip"]
    cut(tmp_path, keyed(tmp_path, frames, "keyed-off"), "off", **fixed, wrap_blend="off")
    assert outputs(tmp_path / "auto") == outputs(tmp_path / "off")


@pytest.mark.slow
def test_a_pinned_cut_the_body_anchor_moves_is_not_touched(tmp_path: Path) -> None:
    frames = character(**REDRAW)
    # The blend alone, as in section 1: `--repair off` keeps the seam bridge from making frames round the redraw's wrap.
    report = cut(tmp_path, keyed(tmp_path, frames), "auto", anchor="body", repair="off")
    assert report["wrap_blend"]["applied"] is False and "--anchor body" in report["wrap_blend"]["why"]
    cut(tmp_path, keyed(tmp_path, frames, "keyed-off"), "off", anchor="body", wrap_blend="off", repair="off")
    assert outputs(tmp_path / "auto") == outputs(tmp_path / "off")


# --- 10. a pin the gate refuses ------------------------------------------------------------------------------------------------

@pytest.mark.slow
def test_a_pin_the_gate_refuses_fails_as_before_with_its_cut_as_filmed(tmp_path: Path) -> None:
    frames = list(character(**REDRAW))
    last = np.zeros_like(frames[-1])
    last[:, 12:] = frames[-1][:, :-12]  # the clip ends 12 px off its first frame
    frames[-1] = last
    for out, kw in (("auto", {}), ("off", {"wrap_blend": "off"})):
        with pytest.raises(SystemExit, match="the pinned clip does not end on its first frame"):
            cut(tmp_path, keyed(tmp_path, frames, f"keyed-{out}"), out, **kw)
    report = json.loads((tmp_path / "auto" / "idle.loop.report.json").read_text())
    assert report["status"] == "failed" and report["wrap_blend"]["why"] == "the pin does not close: the clip is refused on it"
    written = outputs(tmp_path / "auto", "cycle.refused")
    assert len([name for name in written if name.startswith("frame-")]) == N - 1
    assert written == outputs(tmp_path / "off", "cycle.refused") and not (tmp_path / "auto" / "cycle").exists()


# --- 11. the after check -------------------------------------------------------------------------------------------------------

@pytest.mark.slow
def test_a_ramp_that_steps_past_the_cut_is_not_used_and_says_so() -> None:
    # A near-still cut that slides into the model's drawing over its whole length: each step carries a 72nd of the redraw,
    # each ramp cell takes a 13th of it back, a step the cut never takes.
    frames = list(character(redraw=0.0, strands=0.0, settle=N - 1, breath=0.5))
    assert pin_closes(frames)
    images = [_image(f) for f in frames[:-1]]
    cells, record = wrap_blend.apply(images, _image(frames[-1]), fps=FPS, seam_max=SEAM_MAX)
    assert record["applied"] is False and record["after_check"] == "refused"
    assert record["ramp_step_max_ratio"] > record["bound_ratio"]
    assert all(a is b for a, b in zip(cells, images))
    lines = wrap_blend.warnings(record, seam_max=SEAM_MAX)
    assert len(lines) == 1 and "cut as filmed" in lines[0]


# --- 12-15. structures on a flat patch of a redrawn character --------------------------------------------------------------

def test_the_checkpoints_oblique_line_is_held_line_for_line() -> None:
    frames, region = patch_clip("oblique", 8)
    assert pin_closes(frames)
    out, record = applied(frames)
    assert record["applied"] is True
    assert identical(out, frames, region)


THIN = {
    "dot1": dict(kind="dots", size=1, contrast=48), "dot2": dict(kind="dots", size=2), "dot3": dict(kind="dots", size=3),
    "dotted1": dict(kind="line", angle=18.43, every=2), "dotted2": dict(kind="line", angle=45, every=3),
    "ring": dict(kind="ring"), "wave": dict(kind="wave"), "cross": dict(kind="cross"),
    "aa-line": dict(kind="line", angle=18.43, aa=True), "light-line": dict(kind="line", angle=18.43, sign=+1),
    "red-line": dict(kind="line", angle=18.43, channel=0),
    "first-frame-only": dict(kind="line", angle=18.43, mode="appear"),
    "from-frame-6": dict(kind="line", angle=18.43, mode="vanish"),
}


@pytest.mark.parametrize("name", list(THIN))
def test_thin_structures_moved_on_a_flat_patch_change_by_two_levels_at_most(name: str) -> None:
    frames, region = patch_clip(d=8, **THIN[name])  # type: ignore[arg-type]
    out, record = applied(frames)
    assert record["applied"] is True
    assert change(out, frames, region) <= 2


GROUNDS = {
    "decode-noise-4": dict(noise=4.0), "gradient-4": dict(ground=4.0), "softness-0.7": dict(soft=0.8),
    "grain-3": dict(texture=("noise", 3.0)), "checker-4": dict(texture=("checker", 4.0)),
}


@pytest.mark.slow
@pytest.mark.parametrize("name", list(GROUNDS))
def test_the_oblique_line_on_a_busier_quiet_ground_changes_by_three_levels_at_most(name: str) -> None:
    # Read on the gate and the ramp: decode noise on every frame lifts the cut's median step, so the ruler alone keeps
    # that clip as filmed.
    frames, region = patch_clip("oblique", 8, **GROUNDS[name])  # type: ignore[arg-type]
    if name == "softness-0.7":
        assert wrap_blend.equalize(frames[0], frames[-1])[2]["sigma"] == 0.7
    assert change(gated(frames), frames, region) <= 3


@pytest.mark.parametrize("angle", [18.43, 71.57])
@pytest.mark.parametrize("d", [3, 8])
def test_a_strand_on_the_transparent_ground_off_the_silhouette_is_never_drawn_twice(angle: float, d: int) -> None:
    frames, region = patch_clip("line", d, angle=angle, air=True)
    out, record = applied(frames)
    assert record["applied"] is True
    assert identical(out, frames, region)
