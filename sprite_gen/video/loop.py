# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-loop` — cut one seamless cycle out of keyed frames and emit the
loop as cycle frames, a horizontal strip (+ metadata), a transparent GIF and a WebP.

Period first, seam second. A single-start "most similar later frame" search lands
on the 1.5-cycle look-alike of a gait (legs swapped) and produces a loop that
hitches at the wrap (2026-09-08 실측: side walk picked 39 frames where the period
was 28, run picked 25 where it was 17). So the true period is read from the
GLOBAL profile `P[L] = mean_j |f[j] - f[j+L]|` — its deepest local minimum inside
the state's window — and only then is the start chosen as the best seam for that
period. Idle motion is tiny and not strictly periodic: its window is opened to
most of the clip, where the seam is lowest.

Everything downstream is measured, never assumed: the seam ratio (wrap distance
over the mean adjacent-frame distance inside the cycle) gates the run, the GIF and
WebP are re-opened and verified (frame count, loop flag, transparent corners,
no stale RGB under alpha 0). The strip metadata carries `body_h` — the standing
body height (median per-frame bbox height) — so a consumer can scale a jump strip,
whose cells include air room, to the same on-screen body size as a walk strip.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.gif_utils import save_clean_gif

ANALYSIS_SIZE = 96  # thumbnail edge for the distance matrix
STRIP_MAX_CELLS = 64  # upper bound on cells even when they are narrow
STRIP_MAX_WIDTH = 32000  # Chrome refuses images wider than ~32767 px; the cap is on PIXELS — a 650 px cell allows only 49 cells (2026-09-09 wolf idle: 64 cells = 41,664 px, unrenderable)
STRIP_MAX_HEIGHT = 520
SEAM_RATIO_MAX = 2.0  # loop seam / mean adjacent distance inside the cycle
SPECK_MIN_FRACTION = 0.01  # detached components smaller than this fraction of the body are keying specks
PERIODICITY_MIN = 0.15  # the period must dip at least 15% below the profile mean (flat profile = no repeat)
GIF_FPS_DEFAULT = 24.0  # GIF/WebP playback density = the source rate: every cycle frame is kept, a fast action never looks slow (12 made a jump read sluggish, 2026-09-09)
GIF_FRAMES_MIN = 4
ONE_SHOT_MIN_CONTRAST = 3.0  # one-shot: the excursion peak must stand this far above the rest-pose noise (in MADs)
ONE_SHOT_PAD = 2  # one-shot: rest frames kept on each side of the excursion so the seam is rest -> rest
CYCLE_MODES = ("auto", "periodic", "one-shot", "fixed")


@dataclass(frozen=True)
class LoopProfile:
    min_frac: float  # window as a fraction of the clip's frame count
    max_frac: float
    why: str
    periodic: bool = True  # gate: the profile must show a real period (idle is exempt)
    one_shot_ok: bool = False  # the state is an action that may legitimately happen once (jump, attack) -> one-shot detector may take over


# State -> detection window. Fractions of the clip length so 6 s and 10 s clips both work.
STATE_PROFILES: dict[str, LoopProfile] = {
    "idle": LoopProfile(0.60, 0.95, "breathing is slow and not strictly periodic; the lowest seam is a long window", periodic=False),
    "walk": LoopProfile(0.06, 0.31, "full gait = two steps; the floor admits a legless body's fast bounce (~13 frames at 24 fps) — the 15% depth rule, not the window, rejects the one-step half period"),
    "run": LoopProfile(0.07, 0.23, "faster gait"),
    "jump": LoopProfile(0.11, 0.45, "crouch-spring-land-return", one_shot_ok=True),
    "attack": LoopProfile(0.11, 0.45, "swing and return to ready", one_shot_ok=True),
    "default": LoopProfile(0.10, 0.45, "generic in-place action", one_shot_ok=True),
}


def profile_for(state: str | None) -> LoopProfile:
    return STATE_PROFILES.get((state or "").strip().lower(), STATE_PROFILES["default"])


def _load_small(path: Path) -> np.ndarray:
    im = Image.open(path).convert("RGBA")
    im.thumbnail((ANALYSIS_SIZE, ANALYSIS_SIZE))
    a = np.asarray(im, dtype=np.float32) / 255.0
    rgb = a[..., :3] * a[..., 3:4]  # premultiplied: transparent pixels contribute 0
    return np.concatenate([rgb, a[..., 3:4]], axis=-1).reshape(-1)


def distance_matrix(files: list[Path]) -> np.ndarray:
    flat = np.stack([_load_small(f) for f in files])
    n = len(files)
    D = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        D[i] = np.abs(flat - flat[i]).mean(axis=1)
    return D


def detect_cycle(D: np.ndarray, *, min_len: int, max_len: int) -> dict[str, Any]:
    """Global period (deepest local minimum of the averaged profile) then the best-seam start."""
    n = D.shape[0]
    max_len = min(max_len, n - 2)
    if min_len < 2 or max_len < min_len:
        raise SystemExit(f"video-loop: window [{min_len}, {max_len}] is empty for {n} frames")
    adjacent = np.array([D[i, i + 1] for i in range(n - 1)])
    prof: dict[int, float] = {}
    for L in range(max(2, min_len // 2), max_len + 1):
        prof[L] = float(np.mean([D[j, j + L] for j in range(0, n - L, 2)]))
    cands = [L for L in range(min_len, max_len + 1) if L - 1 in prof and L + 1 in prof and prof[L] <= prof[L - 1] and prof[L] <= prof[L + 1]]
    if not cands:
        cands = list(range(min_len, max_len + 1))
    # The true period is the SMALLEST minimum that is about as deep as the deepest one:
    # exact repeats also dip at 2x and 3x the period, and a half-period look-alike dips
    # noticeably less (near/far limb difference) — so a 15% depth tolerance separates both.
    deepest = min(prof[L] for L in cands)
    period = min(L for L in cands if prof[L] <= deepest * 1.15 + 1e-4)  # abs floor: exact repeats sit at ~0
    profile_mean = float(np.mean([prof[L] for L in prof]))
    periodicity = (profile_mean - prof[period]) / profile_mean if profile_mean > 0 else 0.0
    best: dict[str, Any] | None = None
    for L in (period - 1, period, period + 1):
        if L < min_len or L > max_len:
            continue
        for i in range(0, n - L):
            seam = float(D[i, i + L])
            inner = float(adjacent[i : i + L - 1].mean())
            ratio = seam / inner if inner > 0 else math.inf
            if best is None or ratio < best["ratio"]:
                best = {"start": i, "length": L, "seam": seam, "inner_mean_adjacent": inner, "ratio": ratio}
    assert best is not None
    best["period_global"] = period
    best["periodicity"] = round(periodicity, 4)  # how far below the profile mean the period dips (0 = flat = no period)
    best["profile_minima"] = [[L, round(prof[L], 5)] for L in sorted(cands, key=lambda L: prof[L])[:6]]
    return best


def detect_one_shot(D: np.ndarray, *, min_len: int, max_len: int) -> dict[str, Any]:
    """A cycle for an action the model performed ONCE: rest -> excursion -> rest.

    The rest pose is the medoid frame (smallest mean distance to every other frame — in a
    clip that mostly stands still that is a standing frame). Frames whose distance to it
    rises above the rest noise (median + ONE_SHOT_MIN_CONTRAST MADs) are the excursion; the
    longest contiguous run of them, padded by ONE_SHOT_PAD rest frames on each side, is the
    cycle, so the loop seam is rest -> rest by construction. Fails loud when nothing stands
    out or the excursion does not fit the window — never returns a guess.
    """
    n = D.shape[0]
    rest = int(np.argmin(D.mean(axis=1)))
    e = D[rest]
    med = float(np.median(e))
    mad = float(np.median(np.abs(e - med))) or 1e-6
    contrast = (float(e.max()) - med) / mad
    if contrast < ONE_SHOT_MIN_CONTRAST:
        raise SystemExit(
            f"video-loop: no one-shot excursion either — the clip never leaves its rest pose "
            f"(peak {contrast:.1f} MADs above rest, need {ONE_SHOT_MIN_CONTRAST}); regenerate the clip"
        )
    active = e > med + ONE_SHOT_MIN_CONTRAST * mad
    best_run: tuple[int, int] | None = None
    j = 0
    while j < n:
        if active[j]:
            k = j
            while k + 1 < n and active[k + 1]:
                k += 1
            if best_run is None or (k - j) > (best_run[1] - best_run[0]):
                best_run = (j, k)
            j = k + 1
        else:
            j += 1
    assert best_run is not None
    a, b = best_run
    start = max(0, a - ONE_SHOT_PAD)
    end = min(n - 1, b + ONE_SHOT_PAD)
    L = end - start + 1
    if L < min_len or L > max_len:
        raise SystemExit(
            f"video-loop: the one-shot excursion spans {L} frames ({a}..{b} + {ONE_SHOT_PAD} rest each side), "
            f"outside the window [{min_len},{max_len}]; pass --min-len/--max-len if that length is intended"
        )
    adjacent = np.array([D[i, i + 1] for i in range(start, end)])
    inner = float(adjacent.mean())
    seam = float(D[start, end])
    return {
        "kind": "one-shot",
        "start": start,
        "length": L,
        "seam": seam,
        "inner_mean_adjacent": inner,
        "ratio": seam / inner if inner > 0 else math.inf,
        "period_global": None,
        "periodicity": None,
        "rest_frame": rest,
        "excursion": [a, b],
        "excursion_contrast": round(contrast, 2),
    }


def fixed_cycle(D: np.ndarray, *, start: int, length: int) -> dict[str, Any]:
    """An explicitly requested cut (`--cycle fixed --start N --length L`): no detection, no
    periodicity gate — the caller says which frames are the cycle and the report says so
    (kind = "fixed"). The seam is still measured and the seam gate still applies."""
    n = D.shape[0]
    if start < 0 or length < 2 or start + length > n:
        raise SystemExit(f"video-loop: --start {start} --length {length} does not fit {n} frames")
    end = start + length - 1
    adjacent = np.array([D[i, i + 1] for i in range(start, end)])
    inner = float(adjacent.mean())
    seam = float(D[start, end])
    return {"kind": "fixed", "start": start, "length": length, "seam": seam, "inner_mean_adjacent": inner,
            "ratio": seam / inner if inner > 0 else math.inf, "period_global": None, "periodicity": None}


def _drop_specks(image: Image.Image, min_fraction: float) -> tuple[Image.Image, int]:
    """Erase detached alpha components smaller than `min_fraction` of the largest one."""
    a = np.asarray(image)[..., 3] > 16
    H, W = a.shape
    seen = np.zeros_like(a, dtype=bool)
    comps: list[list[tuple[int, int]]] = []
    for y in range(H):
        for x in range(W):
            if a[y, x] and not seen[y, x]:
                q = deque([(y, x)])
                seen[y, x] = True
                pts: list[tuple[int, int]] = []
                while q:
                    cy, cx = q.popleft()
                    pts.append((cy, cx))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < H and 0 <= nx < W and a[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            q.append((ny, nx))
                comps.append(pts)
    if not comps:
        return image, 0
    big = max(len(c) for c in comps)
    px = image.load()
    dropped = 0
    for c in comps:
        if len(c) < max(8, big * min_fraction):
            for y, x in c:
                px[x, y] = (0, 0, 0, 0)
            dropped += 1
    return image, dropped


def _scrub(image: Image.Image) -> int:
    px = image.load()
    n = 0
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = px[x, y]
            if a == 0 and (r or g or b):
                px[x, y] = (0, 0, 0, 0)
                n += 1
    return n


def build_strip(frames: list[Image.Image], *, max_cells: int = STRIP_MAX_CELLS, max_height: int = STRIP_MAX_HEIGHT, max_width: int = STRIP_MAX_WIDTH, cycle_seconds: float, body_height: int | None = None) -> tuple[Image.Image, dict[str, Any]]:
    """Union-crop (no bottom pad so feet meet the floor), scale, bottom-align, tile horizontally.

    The cell count is capped by the strip's PIXEL width (`max_width`) as well as by
    `max_cells`: the crop is measured over every cycle frame first, then as many evenly
    spaced frames as fit are kept. Subsampling is recorded in the meta, never silent."""
    L = len(frames)
    boxes = [im.getchannel("A").point(lambda v: 255 if v >= 8 else 0).getbbox() for im in frames]
    boxes = [b for b in boxes if b]
    if not boxes:
        raise SystemExit("video-loop: every cycle frame is fully transparent")
    left = max(0, min(b[0] for b in boxes) - 8)
    top = max(0, min(b[1] for b in boxes) - 8)
    right = min(frames[0].width, max(b[2] for b in boxes) + 8)
    bottom = max(b[3] for b in boxes)
    # body_h = the STANDING height: the tallest frame whose feet touch the floor. A median
    # over the whole cycle undercounts a jump (crouch + airborne frames dominate) and then
    # over-scales it — the 2026-09-09 hero read 22 % taller than the walk beside it.
    floor = max(b[3] for b in boxes)
    grounded = [b[3] - b[1] for b in boxes if b[3] >= floor - 4] or [b[3] - b[1] for b in boxes]
    body_src = max(grounded)
    scale = min(1.0, max_height / (bottom - top))
    if body_height is not None:
        scale = min(scale, body_height / body_src)  # match the standing height; max_height stays an upper bound
    w = round((right - left) * scale)
    h = round((bottom - top) * scale)
    cap = max(1, min(max_cells, max_width // max(1, w)))
    idx = list(range(L))
    if L > cap:
        idx = [round(k * L / cap) for k in range(cap)]
    chosen = [frames[i] for i in idx]
    cells = [im.crop((left, top, right, bottom)).resize((w, h), Image.LANCZOS) for im in chosen]
    strip = Image.new("RGBA", (w * len(cells), h), (0, 0, 0, 0))
    for k, im in enumerate(cells):
        strip.alpha_composite(im, (k * w, 0))
    meta = {
        "frames": len(cells),
        "w": w,
        "h": h,
        "body_h": round(body_src * scale),
        "delay_ms": round(1000 * cycle_seconds / len(cells), 2),
        "cycle_frames": L,
        "cycle_seconds": round(cycle_seconds, 4),
        "subsampled": L > cap,
        "cell_cap": cap,
        "top_margin_px": top,
        "body_height_target": body_height,
    }
    return strip, meta


def img2webp_supports_exact(binary: str | None = None) -> bool:
    """libwebp added `-exact` to img2webp in 1.5.0 (checked against the 1.4.0 and 1.5.0 release
    binaries, 2026-09-09); older builds (Ubuntu 24.04: 1.3.x) reject the flag with "Unknown option"
    and would rewrite RGB under alpha 0. Detected from `-h`, never assumed."""
    binary = binary or shutil.which("img2webp")
    if not binary:
        return False
    try:
        proc = subprocess.run([binary, "-h"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "-exact" in (proc.stdout + proc.stderr)


def write_webp(frames: list[Image.Image], out: Path, *, delay_ms: int, workdir: Path) -> None:
    """Animated WebP through libwebp's img2webp with -exact (Pillow's animated writer drops `exact`
    and rewrites RGB under alpha 0 to white; measured 2026-09-08)."""
    img2webp = shutil.which("img2webp")
    if not img2webp:
        raise SystemExit("video-loop: `img2webp` not found on PATH — install libwebp (brew install webp) for exact-alpha WebP")
    if not img2webp_supports_exact(img2webp):
        raise SystemExit(
            "video-loop: this img2webp has no `-exact` option (libwebp < 1.5; Ubuntu 24.04 ships 1.3.x) — "
            "install libwebp >= 1.5 (brew install webp, or the official binaries from storage.googleapis.com/downloads.webmproject.org)"
        )
    workdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for k, im in enumerate(frames):
        p = workdir / f"webp-{k:03d}.png"
        im.save(p)
        paths.append(str(p))
    proc = subprocess.run([img2webp, "-loop", "0", "-lossless", "-exact", "-d", str(delay_ms), *paths, "-o", str(out)], capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        raise SystemExit(f"video-loop: img2webp failed: {proc.stderr.strip()[:300]}")


def verify_animation(path: Path, *, expect_frames: int, check_stale: bool) -> dict[str, Any]:
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    loop = im.info.get("loop")
    corners_ok = True
    stale = 0
    for k in range(n):
        im.seek(k)
        f = im.convert("RGBA")
        w, h = f.size
        corners_ok &= all(f.getpixel(c)[3] == 0 for c in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))
        if check_stale:
            stale += sum(1 for p in f.get_flattened_data() if p[3] == 0 and (p[0] or p[1] or p[2]))
    report = {"file": path.name, "format": im.format, "n_frames": n, "loop": loop, "size": list(im.size), "corners_transparent": corners_ok, "stale_rgb_under_alpha0": stale, "bytes": path.stat().st_size}
    problems = []
    if n != expect_frames:
        problems.append(f"{n} frames, expected {expect_frames}")
    if loop != 0:
        problems.append(f"loop={loop!r}, expected 0 (infinite)")
    if not corners_ok:
        problems.append("a corner is not transparent")
    if stale:
        problems.append(f"{stale} transparent pixels carry RGB")
    if problems:
        raise SystemExit(f"video-loop: {path.name} failed verification: {'; '.join(problems)}")
    return report


def run_loop(
    frames_dir: Path,
    out_dir: Path,
    *,
    fps: float,
    state: str | None,
    min_len: int | None,
    max_len: int | None,
    n_out: int | None,
    seam_max: float,
    name: str,
    report_path: Path | None,
    cycle_mode: str = "auto",
    gif_fps: float = GIF_FPS_DEFAULT,
    start: int | None = None,
    length: int | None = None,
    strip_height: int = STRIP_MAX_HEIGHT,
    body_height: int | None = None,
) -> dict[str, Any]:
    if cycle_mode not in CYCLE_MODES:
        raise SystemExit(f"video-loop: unknown --cycle {cycle_mode!r}; expected one of {', '.join(CYCLE_MODES)}")
    frames_dir = frames_dir.expanduser().resolve()
    files = sorted(frames_dir.glob("*.png"))
    if len(files) < 6:
        raise SystemExit(f"video-loop: need at least 6 keyed frames in {frames_dir}, found {len(files)}")
    prof = profile_for(state)
    n = len(files)
    lo = min_len if min_len is not None else max(4, round(n * prof.min_frac))
    hi = max_len if max_len is not None else max(lo + 2, round(n * prof.max_frac))
    D = distance_matrix(files)
    periodic_attempt: dict[str, Any] | None = None
    if cycle_mode == "fixed":
        if start is None or length is None:
            raise SystemExit("video-loop: --cycle fixed needs --start and --length")
        cycle = fixed_cycle(D, start=start, length=length)
    elif cycle_mode == "one-shot":
        cycle = detect_one_shot(D, min_len=lo, max_len=hi)
    else:
        cycle = detect_cycle(D, min_len=lo, max_len=hi)
        cycle["kind"] = "periodic"
        if prof.periodic and cycle["periodicity"] < PERIODICITY_MIN:
            flat = (
                f"the period profile is flat (periodicity {cycle['periodicity']:.2f} < {PERIODICITY_MIN}) in window [{lo},{hi}]"
            )
            if cycle_mode == "auto" and prof.one_shot_ok:
                # explicit, recorded failover: the action happened once (allowed for this state),
                # so cut rest -> excursion -> rest instead. The periodic attempt stays in the report.
                periodic_attempt = {"periodicity": cycle["periodicity"], "window": [lo, hi], "profile_minima": cycle["profile_minima"], "why_rejected": flat}
                cycle = detect_one_shot(D, min_len=lo, max_len=max(hi, round(n * 0.9)))
            else:
                raise SystemExit(
                    f"video-loop: no periodic cycle found — {flat}; the motion does not repeat, widen the window, "
                    "regenerate the clip, or pass --cycle one-shot for a single performed action"
                )
    i, L = cycle["start"], cycle["length"]
    # playback density, not a fixed count: a long cycle gets more frames so every state plays at
    # ~gif_fps (a fixed 12 made a 2.5 s jump hold each frame 210 ms while a 1.1 s walk held 90 ms)
    if n_out is None:
        n_out = max(GIF_FRAMES_MIN, round(L / fps * gif_fps))
    n_out = min(n_out, L)  # never ask for more distinct frames than the cycle holds

    out_dir = out_dir.expanduser().resolve()
    cycle_dir = out_dir / "cycle"
    cycle_dir.mkdir(parents=True, exist_ok=True)
    for old in cycle_dir.glob("frame-*.png"):
        old.unlink()
    frames: list[Image.Image] = []
    scrubbed = specks = 0
    for k, f in enumerate(files[i : i + L]):
        im = Image.open(f).convert("RGBA")
        scrubbed += _scrub(im)
        im, d = _drop_specks(im, SPECK_MIN_FRACTION)
        specks += d
        im.save(cycle_dir / f"frame-{k:03d}.png")
        frames.append(im)

    cycle_seconds = L / fps
    strip, strip_meta = build_strip(frames, max_height=strip_height, cycle_seconds=cycle_seconds, body_height=body_height)
    strip_path = out_dir / f"{name}.strip.png"
    strip.save(strip_path)
    atomic_write_text(out_dir / f"{name}.strip.json", json.dumps(strip_meta, indent=2) + "\n")

    # resampled GIF/WebP frames: same crop as the strip, n_out evenly across the cycle
    idx = [min(L - 1, i2) for i2 in (round(k * L / n_out) for k in range(n_out))]
    cells = [strip.crop((k * strip_meta["w"], 0, (k + 1) * strip_meta["w"], strip_meta["h"])) for k in range(strip_meta["frames"])]
    pick = [cells[min(len(cells) - 1, round(j * len(cells) / L))] for j in idx]
    seam_idx = [i + j for j in idx]
    resampled_adjacent = float(np.mean([D[seam_idx[k], seam_idx[k + 1]] for k in range(len(seam_idx) - 1)]))
    resampled_seam = float(D[seam_idx[-1], seam_idx[0]])
    seam_ratio = resampled_seam / resampled_adjacent if resampled_adjacent > 0 else math.inf
    delay_ms = max(20, round(1000 * cycle_seconds / n_out))
    gif_path = out_dir / f"{name}.gif"
    webp_path = out_dir / f"{name}.webp"
    if seam_ratio > seam_max:
        raise SystemExit(
            f"video-loop: loop seam ratio {seam_ratio:.2f} exceeds {seam_max} (period {L} frames from {i}); "
            "the cycle does not close — widen the window, regenerate with an 'evenly paced, returns to start' prompt, or pass --seam-max"
        )
    save_clean_gif(pick, gif_path, duration_ms=delay_ms, loop=0, alpha_threshold=128)
    write_webp(pick, webp_path, delay_ms=delay_ms, workdir=out_dir / ".webp-frames")
    shutil.rmtree(out_dir / ".webp-frames", ignore_errors=True)
    gif_report = verify_animation(gif_path, expect_frames=n_out, check_stale=False)
    webp_report = verify_animation(webp_path, expect_frames=n_out, check_stale=True)

    payload = {
        "kind": "sprite-gen-video-loop-report",
        "frames_dir": str(frames_dir),
        "out_dir": str(out_dir),
        "state": state,
        "fps": fps,
        "frames_total": n,
        "window": [lo, hi],
        "profile": prof.why,
        "cycle_mode": cycle_mode,
        "cycle": cycle,
        "periodic_attempt": periodic_attempt,
        "cycle_seconds": round(cycle_seconds, 4),
        "n_out": n_out,
        "gif_fps": gif_fps,
        "delay_ms": delay_ms,
        "resampled_seam_ratio": round(seam_ratio, 4),
        "seam_max": seam_max,
        "scrubbed_rgb_pixels": scrubbed,
        "specks_dropped": specks,
        "strip": {"path": str(strip_path), **strip_meta},
        "gif": gif_report,
        "webp": webp_report,
    }
    target = (report_path or (out_dir / f"{name}.loop.report.json")).expanduser().resolve()
    atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    payload["report"] = str(target)
    return payload


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--frames-dir", required=True, type=Path, help="keyed RGBA frames from `sprite-gen video-frames` (…/keyed)")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fps", type=float, default=24.0, help="frame rate of the keyed frames (from the frames report)")
    parser.add_argument("--state", help="motion state (idle/walk/run/jump/attack) — selects the detection window")
    parser.add_argument("--min-len", type=int, help="override: minimum cycle length in frames")
    parser.add_argument("--max-len", type=int, help="override: maximum cycle length in frames")
    parser.add_argument("--n-out", type=int, help="frames in the GIF/WebP (default: cycle seconds x --gif-fps)")
    parser.add_argument("--gif-fps", type=float, default=GIF_FPS_DEFAULT, help=f"GIF/WebP playback density (default {GIF_FPS_DEFAULT:g}); every state plays at this rate regardless of cycle length")
    parser.add_argument("--seam-max", type=float, default=SEAM_RATIO_MAX, help=f"loop seam gate (default {SEAM_RATIO_MAX})")
    parser.add_argument("--cycle", choices=CYCLE_MODES, default="auto", help="auto: periodic first, one-shot failover for action states (recorded in the report); periodic / one-shot force one detector; fixed cuts exactly --start/--length (no detection, reported as kind=fixed)")
    parser.add_argument("--start", type=int, help="fixed cut: first keyed frame of the cycle (with --cycle fixed)")
    parser.add_argument("--length", type=int, help="fixed cut: cycle length in frames (with --cycle fixed)")
    parser.add_argument("--strip-height", type=int, default=STRIP_MAX_HEIGHT, help=f"cell/strip/GIF height cap in px (default {STRIP_MAX_HEIGHT}); the cycle is scaled down to fit, never up")
    parser.add_argument("--body-height", type=int, help="scale so the STANDING height (tallest floor-contact frame) is this many px — the same value across states gives the same character size; --strip-height stays the cap")
    parser.add_argument("--name", default="loop", help="basename for strip/gif/webp outputs")
    parser.add_argument("--report", type=Path)


def run(**kwargs: object) -> int:
    payload = run_loop(
        Path(str(kwargs["frames_dir"])), Path(str(kwargs["out_dir"])),
        fps=float(kwargs.get("fps") or 24.0), state=kwargs.get("state"),  # type: ignore[arg-type]
        min_len=kwargs.get("min_len"), max_len=kwargs.get("max_len"), n_out=kwargs.get("n_out"),  # type: ignore[arg-type]
        seam_max=float(kwargs.get("seam_max") or SEAM_RATIO_MAX), name=str(kwargs.get("name") or "loop"), report_path=kwargs.get("report"),  # type: ignore[arg-type]
        cycle_mode=str(kwargs.get("cycle") or "auto"), gif_fps=float(kwargs.get("gif_fps") or GIF_FPS_DEFAULT),
        start=kwargs.get("start"), length=kwargs.get("length"), strip_height=int(kwargs.get("strip_height") or STRIP_MAX_HEIGHT),  # type: ignore[arg-type]
        body_height=kwargs.get("body_height"),  # type: ignore[arg-type]
    )
    summary = {k: payload[k] for k in ("state", "frames_total", "window", "cycle_seconds", "n_out", "delay_ms", "resampled_seam_ratio", "specks_dropped", "report")}
    summary["cycle"] = {k: payload["cycle"].get(k) for k in ("kind", "start", "length", "period_global", "ratio")}
    if payload["periodic_attempt"]:
        summary["periodic_attempt"] = payload["periodic_attempt"]["why_rejected"]
    summary["strip"] = {k: payload["strip"][k] for k in ("path", "frames", "w", "h", "body_h", "delay_ms")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-loop", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
