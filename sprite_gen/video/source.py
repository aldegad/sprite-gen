# SPDX-License-Identifier: Apache-2.0
"""Byte receipts for extracted source files and a deliberately exact loop projection.

A receipt is not a declaration that a loop matches its source. ``project`` must
also reproduce every unmodified delivered cell before any source pixel is used.
Unsupported processing stays unknown; malformed or changed inputs are errors.
"""

from __future__ import annotations

import io
import json
import math
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, __version__ as pillow_version

from sprite_gen._deps import np
from sprite_gen.video import evidence
from sprite_gen.util.resample import resize_cell

KIND = "sprite-gen-video-source-manifest"
RECIPE = "scrub-specks-body-ramp-union-v1"


def timestamps(clip: Path, stream_index: int) -> list[float]:
    """Presentation timestamps from the actual picture stream, not index / fps."""
    from sprite_gen.video.frames import _require
    proc = subprocess.run([_require("ffprobe"), "-v", "error", "-select_streams", str(stream_index),
                           "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(clip)],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode:
        raise ValueError(f"cannot read source timestamps: {proc.stderr[:300]}")
    return [float(row["best_effort_timestamp_time"]) for row in json.loads(proc.stdout)["frames"]]


def artifact(data: bytes) -> dict[str, Any]:
    return {"sha256": evidence.digest(data), "bytes": len(data)}


INPUTS = ("source_manifest", "source_frames_dir", "source_clip", "source_canvas", "source_frames_report")


def add_inputs(parser: Any, *, required: bool = True) -> None:
    for name in INPUTS:
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=required)


def input_paths(kwargs: dict[str, Any]) -> dict[str, Path]:
    if any(kwargs.get(k) is None for k in INPUTS):
        raise ValueError("source comparison needs manifest, keyed directory, clip, canvas and frames report")
    return {k: Path(kwargs[k]) for k in INPUTS}


def manifest(*, clip: Path, canvas: Path, frames_report: Path,
             files: list[Path], timestamps: list[float]) -> dict[str, Any]:
    """Record existing extraction output; the caller obtains PTS from the clip."""
    report = json.loads(frames_report.read_bytes())
    if len(files) != len(timestamps) or not files:
        raise ValueError("source timestamp count differs from keyed frame count")
    fps = report["fps"]
    return {"kind": KIND, "schema_version": 1, "receipt_engine": evidence.engine_identity(),
            "receipt_environment": {"python": platform.python_version(), "platform": platform.platform(),
                                    "pillow": pillow_version, "numpy": np.__version__},
            "extraction_engine": report.get("producer"),
            "inputs": {k: artifact(p.read_bytes()) for k, p in
                       (("clip", clip), ("canvas", canvas), ("frames_report", frames_report))},
            "recipe": {k: report.get(k) for k in ("key", "spill", "decontam", "edge_policy", "stream_index")},
            "fps": fps, "source": evidence.source_identity(files, fps),
            "frames": [{"file": p.name, "pts_seconds": t, **artifact(p.read_bytes())}
                       for p, t in zip(files, timestamps)]}


@dataclass
class Source:
    frames: list[Image.Image]
    record: dict[str, Any]
    artifacts: dict[str, Any]

    @classmethod
    def read(cls, *, source_manifest: Path, source_frames_dir: Path, source_clip: Path,
             source_canvas: Path, source_frames_report: Path) -> Source:
        paths = {"manifest": source_manifest, "clip": source_clip, "canvas": source_canvas,
                 "frames_report": source_frames_report}
        data = {k: p.read_bytes() for k, p in paths.items()}
        m = json.loads(data["manifest"])
        report = json.loads(data["frames_report"])
        if not isinstance(m, dict) or m.get("kind") != KIND or m.get("schema_version") != 1:
            raise ValueError("unsupported source manifest")
        if any(m.get("inputs", {}).get(k) != artifact(data[k]) for k in ("clip", "canvas", "frames_report")):
            raise ValueError("source input bytes do not match manifest")
        fps = m.get("fps")
        if (isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps)
                or fps <= 0 or fps != report.get("fps")):
            raise ValueError("invalid source fps")
        rows = m.get("frames")
        files = sorted(source_frames_dir.glob("*.png"))
        if (not isinstance(rows, list) or not files or len(files) != len(rows)
                or any(not isinstance(r, dict) for r in rows)
                or [p.name for p in files] != [r.get("file") for r in rows]):
            raise ValueError("source ordered file list differs from manifest")
        images, hashes = [], []
        previous = -math.inf
        for p, r in zip(files, rows):
            blob = p.read_bytes()
            if any(r.get(k) != v for k, v in artifact(blob).items()):
                raise ValueError(f"source frame bytes differ: {p.name}")
            t = r.get("pts_seconds")
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t <= previous:
                raise ValueError("source timestamps must be finite and strictly increasing")
            previous = t
            with Image.open(io.BytesIO(blob)) as im:
                if im.format != "PNG":
                    raise ValueError("source must contain PNG frames")
                images.append(im.convert("RGBA"))
            hashes.append(bytes.fromhex(evidence.digest(blob)))
        identity = {"kind": "keyed-frame-sequence", "sha256": evidence.digest(b"".join(hashes)),
                    "frames": len(images), "fps": fps}
        if m.get("source") != identity or report.get("frames") != len(images):
            raise ValueError("source sequence identity differs from actual files")
        if any(im.size != images[0].size for im in images):
            raise ValueError("source frame sizes differ")
        if images[0].size != (report.get("width"), report.get("height")):
            raise ValueError("source dimensions differ from extraction report")
        actual_pts = timestamps(source_clip, report["stream_index"])
        if actual_pts != [r["pts_seconds"] for r in rows]:
            raise ValueError("source timestamps differ from actual clip")
        if m.get("recipe") != {k: report.get(k) for k in ("key", "spill", "decontam", "edge_policy", "stream_index")}:
            raise ValueError("source recipe differs from extraction report")
        return cls(images, m, {k: artifact(b) for k, b in data.items()})


@dataclass
class Projection:
    frames: list[Image.Image]
    record: dict[str, Any]


def project(baseline: Any, source: Source) -> tuple[Projection | None, str | None]:
    """Prove one shared crop against all cells outside the recorded repair hints."""
    from sprite_gen.video import loop
    from sprite_gen.video.frames import drop_specks

    m, report = baseline.meta, baseline.source_report
    if report.get("kind") != "sprite-gen-video-loop-report" or report.get("status") != "passed":
        return None, "source-report-unverified"
    if "follow" in m:
        return None, "follow-source-warp-unverified"
    n = len(baseline.frames)
    cycle = report.get("cycle", {})
    start, length = cycle.get("start"), cycle.get("length")
    if (type(start) is not int or type(length) is not int or start < 0 or length != n
            or n < 3 or start + n > len(source.frames) or m.get("subsampled") is not False
            or m.get("cycle_frames") != n or m.get("loop") is not True):
        return None, "source-schedule-unsupported"
    if m.get("sample_indices", list(range(n))) != list(range(n)):
        return None, "source-samples-mismatch"
    for drawings in (m.get("cycle_drawings"), report.get("cycle_drawings")):
        if drawings is not None and (drawings.get("start") != start or drawings.get("length") != n):
            return None, "source-drawings-mismatch"
    reported = report.get("strip")
    if not isinstance(reported, dict) or any(m.get(k) != reported.get(k) for k in
            ("frames", "w", "h", "delay_ms", "cycle_seconds", "scale", "source_rect", "foot_anchor", "wrap_dx_px")):
        return None, "source-geometry-inconsistent"
    fps = source.record["fps"]
    if (report.get("fps") != fps or report.get("frames_total") != len(source.frames)
            or baseline.timing_issue() or abs(m["cycle_seconds"] - n / fps) > .00005 + 1e-9):
        return None, "source-timing-unverified"
    pts = [row["pts_seconds"] for row in source.record["frames"]]
    if any(abs((b - a) - 1 / fps) > 1e-5 for a, b in zip(pts, pts[1:])):
        return None, "nonuniform-source-timing-unsupported"
    if report.get("source") is not None and report["source"] != source.record["source"]:
        return None, "source-sequence-mismatch"
    if "source_cut" in m and m["source_cut"] != {
            "source": source.record["source"], "start": start, "length": n,
            "samples": list(range(start, start + n))}:
        return None, "source-samples-mismatch"
    anchor = m.get("foot_anchor")
    if anchor not in ("none", "body") or report.get("anchor") != anchor:
        return None, "source-anchor-unsupported"
    if any(k in m for k in ("cycle_align", "motion_anchor")) or report.get("size_hold", {}).get("applied"):
        return None, "source-processing-unsupported"
    hints = (report.get("jump_repair") or {}).get("replaced", [])
    if (not isinstance(hints, list) or any(type(k) is not int or not 0 <= k < n for k in hints)
            or len(set(hints)) != len(hints) or len(hints) > n // 2):
        return None, "repair-locations-unverified"
    keep = [k for k in range(n) if k not in hints]
    frames = []
    for frame in source.frames[start:start + n]:
        frame = frame.copy()
        loop._scrub(frame)
        frames.append(drop_specks(frame, alpha_over=16, diagonal=False, apart=0)[0])
    dx = loop.body_wrap_offset(frames) if anchor == "body" else 0
    if anchor == "body" and m.get("wrap_dx_px") != dx:
        return None, "source-anchor-mismatch"
    frames = loop.ramp_frames(frames, dx)
    native = report.get("source_projection")
    if native is not None and (native.get("recipe") != RECIPE or native.get("wrap_dx_px") != dx
            or native.get("before_repair_pixels_sha256") != [evidence.digest(f.tobytes()) for f in frames]):
        return None, "source-processing-receipt-mismatch"
    boxes = [frames[k].getchannel("A").point(lambda v: 255 if v >= 8 else 0).getbbox() for k in keep]
    if any(b is None for b in boxes):
        return None, "empty-source-subject"
    w, h = m["w"], m["h"]
    rect = m.get("source_rect")
    if rect is not None:
        if (not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int for v in rect)
                or rect[2] <= rect[0] or rect[3] <= rect[1]):
            raise ValueError("invalid source_rect")
        candidates = [rect]
    else:
        # A legacy strip omits the crop. Bounds come only from its recorded scale
        # recipe, frame canvas and the union of the known unmodified subjects.
        target, standing, cap, top = (m.get(k) for k in
                                     ("body_height_target", "body_src_h", "cell_height_cap", "top_margin_px"))
        if any(type(v) is not int or v <= 0 for v in (target, standing, cap)) or type(top) is not int or top < 0:
            return None, "legacy-projection-recipe-missing"
        left_max, right_min = min(b[0] for b in boxes) - 8, max(b[2] for b in boxes) + 8
        candidates = []
        for bottom in range(max(b[3] for b in boxes), frames[0].height + 1):
            if bottom <= top:
                continue
            scale = min(cap / (bottom - top), target / standing)
            if round(scale, 4) != m.get("scale") or round((bottom - top) * scale) != h:
                continue
            for width in range(max(1, math.floor((w - .5) / scale)), math.ceil((w + .5) / scale) + 1):
                if round(width * scale) != w:
                    continue
                for left in range(max(-8, right_min - width), left_max + 1):
                    if left + width <= frames[0].width + 8:
                        candidates.append([left, top, left + width, bottom])
                        if len(candidates) > 4096:
                            return None, "legacy-projection-search-budget"
    hits = []
    for crop in candidates:
        if all(resize_cell(frames[k].crop(crop), (w, h)).tobytes() == baseline.frames[k].tobytes() for k in keep):
            hits.append(crop)
            if len(hits) > 1:
                return None, "legacy-projection-ambiguous"
    if not hits:
        return None, "legacy-source-correspondence-unverified"
    crop = hits[0]
    projected = [resize_cell(frame.crop(crop), (w, h)) for frame in frames]
    receipt = {"recipe": RECIPE, "crop": crop, "cell": [w, h], "wrap_dx_px": dx,
               "source": source.record["source"], "inputs": source.artifacts,
               "measurement_engine": evidence.engine_identity(),
               "scale_xy": [w / (crop[2] - crop[0]), h / (crop[3] - crop[1])],
               "samples": list(range(start, start + n)), "pts_seconds": pts[start:start + n],
               "unmodified_cells": keep, "repair_hints": hints, "exact_projections": 1,
               "reference_pixels_sha256": [evidence.digest(f.tobytes()) for f in projected]}
    return Projection(projected, receipt), None


def add_arguments(parser: Any) -> None:
    for name in ("clip", "canvas", "frames_report", "frames_dir", "out"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)


def run(**kwargs: Any) -> int:
    """Receipt existing keyed output without repeating extraction; not a source attestation."""
    from sprite_gen.spec.runio import atomic_write_text
    clip, canvas, report, directory, out = (Path(kwargs[k]) for k in
                                           ("clip", "canvas", "frames_report", "frames_dir", "out"))
    files = sorted(directory.glob("*.png"))
    try:
        if out.resolve() in {p.resolve() for p in (clip, canvas, report, *files)}:
            raise ValueError("manifest cannot overwrite a source input")
        record = manifest(clip=clip, canvas=canvas, frames_report=report, files=files,
                          timestamps=timestamps(clip, json.loads(report.read_bytes())["stream_index"]))
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(out, json.dumps(record, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"video-source-manifest: {exc}") from exc
    print(json.dumps({"manifest": str(out), "source": record["source"]}))
    return 0
