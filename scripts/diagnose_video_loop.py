# SPDX-License-Identifier: Apache-2.0
"""Measure existing keyed frames without changing loop selection or acceptance.

Run using the repository's Python environment. Outputs
are private diagnostic artifacts: keep them outside the repository when inputs
are private. Frame indices are zero-based. Wide-window candidates are evidence,
not accepted animations: low seam alone cannot establish a complete action.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import hashlib
import json
import math

from PIL import Image, ImageDraw

from sprite_gen._deps import np
from sprite_gen.video import loop
from sprite_gen.video.loop_vocab import CYCLE_ONE_SHOT, CYCLE_PERIODIC


def attempt(fn, **kwargs):
    try:
        return {"ok": True, "cycle": fn(**kwargs)}
    except SystemExit as exc:
        return {"ok": False, "error": str(exc)}


def measure(files: list[Path], fps: float, state: str) -> dict:
    if len(files) < 6 or fps <= 0:
        raise ValueError("need at least six frames and a positive fps")
    D = loop.distance_matrix(files)
    mass = loop.frame_masses(files)
    n = len(files)
    profile = loop.profile_for(state)
    lo, hi = profile.window(n, fps)
    adjacent = np.diag(D, 1)
    periodic = attempt(loop.detect_cycle, D=D, min_len=lo, max_len=hi,
                       gait_floor=round(profile.min_seconds * fps) if profile.gait else None)
    one_shot = attempt(loop.detect_one_shot, D=D, min_len=lo,
                       max_len=max(hi, round(n * .9)), frame_mass=mass)
    auto = periodic
    route = CYCLE_PERIODIC
    floor = loop.PERIODICITY_MIN
    if periodic["ok"]:
        floor = loop.periodicity_floor(n, periodic["cycle"]["period_global"], partial_repeat=profile.action_seconds is not None)
        periodic["cycle"]["periodicity_min"] = floor
    if periodic["ok"] and profile.periodic and periodic["cycle"]["periodicity"] < floor:
        if profile.one_shot_ok:
            auto, route = one_shot, CYCLE_ONE_SHOT
        else:
            auto, route = {"ok": False, "error": "periodicity gate"}, "periodicity-rejected"
    auto = {**auto, "route": route}
    if auto["ok"]:
        auto["seam_gate_pass"] = auto["cycle"]["ratio"] <= loop.SEAM_RATIO_MAX
    # Enumerate every interval, including the clip's final frame. Retain one
    # best seam per length; the production selector's different bounds stay intact.
    windows = []
    for length in range(4, n + 1):
        candidates = []
        for start in range(n - length + 1):
            end = start + length - 1
            inner = float(adjacent[start:end].mean())
            seam = float(D[start, end])
            ratio = seam / inner if inner > 0 else math.inf
            score = abs(math.log(ratio)) if ratio > 0 else math.inf
            candidates.append((score, start, ratio, seam, inner))
        _, start, ratio, seam, inner = min(candidates)
        lag_profile = float(D[np.arange(0, n-length, 2), np.arange(0, n-length, 2)+length].mean()) if length < n else None
        windows.append({"length": length, "profile": lag_profile, "start": start,
                        "ratio": ratio, "seam": seam, "inner_mean_adjacent": inner,
                        "start0_ratio": float(D[0, length-1]) / float(adjacent[:length-1].mean()) if adjacent[:length-1].mean() > 0 else math.inf,
                        "peak_from_start": float(D[start, start:start+length].max()),
                        "peak_over_start_mass": float(D[start, start:start+length].max()) / float(mass[start]) if mass[start] > 0 else None})
    rest = int(np.argmin(D.mean(axis=1)))
    tail_start = min(n-1, max(1, round(.5 * fps)))
    returned = tail_start + int(np.argmin(D[0, tail_start:]))
    return {"frames": n, "fps": fps, "state": state, "window": [lo, hi],
            "source_digest": hashlib.sha256(b"".join(hashlib.sha256(f.read_bytes()).digest() for f in files)).hexdigest(),
            "analysis_size": loop.ANALYSIS_SIZE, "seam_max": loop.SEAM_RATIO_MAX,
            "periodic": periodic, "one_shot": one_shot, "auto": auto,
            "wide_periodic": attempt(loop.detect_cycle, D=D, min_len=lo, max_len=round(n * .9)),
            "rest_medoid": rest, "mean_adjacent": float(adjacent.mean()),
            "first_return_after_seconds": .5, "first_return_frame": returned,
            "first_return_distance": float(D[0, returned]),
            "first_return_over_mass": float(D[0, returned]/mass[0]) if mass[0] else None,
            "adjacent": adjacent.tolist(), "distance_from_first": D[0].tolist(),
            "distance_from_medoid": D[rest].tolist(), "mass": mass.tolist(),
            "windows": windows}


def render(files: list[Path], data: dict, out: Path) -> None:
    # A contact sheet preserves the whole canvas so motion/drift remains visible.
    indices = sorted(set([*range(0, len(files), 3), len(files)-1]))
    width, cell_h, columns = 240, 160, 6
    sheet = Image.new("RGB", (width*columns, cell_h*math.ceil(len(indices)/columns)), "white")
    draw = ImageDraw.Draw(sheet)
    for j, index in enumerate(indices):
        frame = Image.open(files[index]).convert("RGBA")
        frame.thumbnail((width, cell_h-22))
        x, y = (j % columns)*width, (j // columns)*cell_h
        sheet.paste(frame, (x+(width-frame.width)//2, y+22), frame)
        draw.text((x+4, y+3), f"frame {index} | {index/data['fps']:.3f}s", fill="black")
    sheet.save(out / "contact.png")
    chart = Image.new("RGB", (1200, 650), "white")
    draw = ImageDraw.Draw(chart)
    series = [("Distance from first / medoid / adjacent", [data["distance_from_first"], data["distance_from_medoid"], data["adjacent"]]),
              ("Lag profile (all lags; production window marked)", [[w["profile"] for w in data["windows"] if w["profile"] is not None]]),
              ("Best seam ratio by window length (clipped at 10); red = gate 2", [[min(10, w["ratio"]) for w in data["windows"]]])]
    for row, (title, curves) in enumerate(series):
        top, bottom = 25+row*210, 195+row*210
        draw.text((15, top), title, fill="black")
        scale = max(max(c) for c in curves) or 1
        draw.text((15, top+18), f"max={scale:.5f}", fill="black")
        for color, curve in zip(("blue", "green", "orange"), curves):
            pts = [(60+i/(len(curve)-1)*1110, bottom-v/scale*125) for i, v in enumerate(curve)]
            draw.line(pts, fill=color, width=2)
        if row == 2:
            y = bottom-2/scale*125
            draw.line((60,y,1170,y),fill="red")
        if row > 0:
            for bound in data["window"]:
                x = 60+(bound-4)/(len(curves[0])-1)*1110
                draw.line((x,top+35,x,bottom),fill="gray")
        draw.text((60,bottom+2), "0" if row == 0 else "4",fill="black")
        draw.text((1130,bottom+2),str(data["frames"]-1 if row < 2 else data["frames"]),fill="black")
    chart.save(out / "curves.png")


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k:json_safe(v) for k,v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--fps", type=float, required=True)
    parser.add_argument("--state", default="attack")
    args = parser.parse_args()
    files = sorted(args.frames_dir.glob("*.png"))
    data = measure(files, args.fps, args.state)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "diagnosis.json").write_text(json.dumps(json_safe(data), indent=2, allow_nan=False)+"\n")
    render(files, data, args.out_dir)
    print(json.dumps(json_safe({k:v for k,v in data.items() if k not in {"windows", "adjacent", "distance_from_first", "distance_from_medoid", "mass"}}), indent=2))


if __name__ == "__main__":
    main()
