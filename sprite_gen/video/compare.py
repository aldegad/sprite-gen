# SPDX-License-Identifier: Apache-2.0
"""Compare final loop artifacts, with explicit limits on what can be ordered.

No quality values from an upstream loop report are used. That report supplies
source provenance only; follow-through may have changed every delivered pixel.
The v1 order is deliberately narrow: same source samples, timing and coordinates,
with unchanged coverage to prove preservation of the filmed silhouette motion.
"""

from __future__ import annotations

import argparse
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.video import evidence, follow, repair, rife
from sprite_gen.video.interpolation_quality import faults

METRIC_VERSION = "same-sample-defects-v1"
KIND = "sprite-gen-video-loop-comparison"


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _json(data: bytes, name: str) -> dict[str, Any]:
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


@dataclass
class Loop:
    frames: list[Image.Image]
    meta: dict[str, Any]
    source_report: dict[str, Any]
    artifacts: dict[str, Any]
    delay: float | None

    @classmethod
    def read(cls, strip: Path, meta: Path, report: Path) -> Loop:
        # Read each artifact once. Hash and measure the same snapshot, including JSON whitespace.
        data = {k: p.read_bytes() for k, p in (("strip", strip), ("meta", meta), ("report", report))}
        m, r = _json(data["meta"], "meta"), _json(data["report"], "report")
        n, w, h = (_positive_int(m.get(k), k) for k in ("frames", "w", "h"))
        with Image.open(io.BytesIO(data["strip"])) as im:
            if im.format != "PNG" or im.size != (n * w, h) or getattr(im, "n_frames", 1) != 1:
                raise ValueError("strip must be a single PNG whose size agrees with meta frames/w/h")
            sheet = im.convert("RGBA")
        frames = [sheet.crop((k * w, 0, (k + 1) * w, h)) for k in range(n)]
        if any(not f.getchannel("A").getbbox() for f in frames):
            raise ValueError("strip contains a fully transparent frame")
        delay = m.get("delay_ms")
        if delay is not None and (not _number(delay) or delay <= 0):
            raise ValueError("delay_ms must be finite and positive")
        artifacts = {k: {"sha256": evidence.digest(b), "bytes": len(b)} for k, b in data.items()}
        return cls(frames, m, r, artifacts, delay)

    def timing_issue(self) -> str | None:
        if self.delay is None:
            return "timing-missing"
        if "durations_ms" in self.meta:
            return "nonuniform-timing-unsupported"
        seconds = self.meta.get("cycle_seconds")
        if not _number(seconds):
            return "cycle-duration-missing"
        # The writer rounds delay_ms to .01 ms and cycle_seconds to .0001 s.
        if abs(seconds * 1000 - self.delay * len(self.frames)) > .005 * len(self.frames) + .05 + 1e-9:
            return "timing-inconsistent"
        return None

    def provenance_issue(self) -> str | None:
        cut = self.meta.get("source_cut")
        src = self.source_report.get("source")
        cycle = self.source_report.get("cycle")
        if not all(isinstance(v, dict) for v in (cut, src, cycle)):
            return "source-provenance-missing"
        if self.source_report.get("kind") != "sprite-gen-video-loop-report" or self.source_report.get("status") != "passed":
            return "source-report-unverified"
        sha = src.get("sha256")
        if (src.get("kind") != "keyed-frame-sequence" or not isinstance(sha, str) or len(sha) != 64
                or any(c not in "0123456789abcdef" for c in sha)
                or not isinstance(src.get("frames"), int) or isinstance(src["frames"], bool) or src["frames"] < 1
                or not _number(src.get("fps")) or src["fps"] <= 0):
            return "source-provenance-invalid"
        start, length, samples = cut.get("start"), cut.get("length"), cut.get("samples")
        if (not isinstance(start, int) or isinstance(start, bool) or start < 0
                or not isinstance(length, int) or isinstance(length, bool) or length < 2
                or start + length > src["frames"] or not isinstance(samples, list)
                or len(samples) != len(self.frames) or any(not isinstance(k, int) or isinstance(k, bool) for k in samples)
                or samples != sorted(set(samples)) or any(k < start or k >= start + length for k in samples)):
            return "source-samples-unverified"
        if (cut.get("source") != src or cycle.get("start") != start or cycle.get("length") != length
                or self.source_report.get("fps") != src["fps"] or self.source_report.get("frames_total") != src["frames"]):
            return "source-provenance-inconsistent"
        if self.meta.get("cycle_frames") != length:
            return "source-samples-unverified"
        expected = [start + round(k * length / len(samples)) for k in range(len(samples))]
        if samples != expected:
            return "source-samples-unverified"
        if not _number(self.meta.get("cycle_seconds")) or abs(self.meta["cycle_seconds"] - length / src["fps"]) > .00005 + 1e-9:
            return "source-timing-inconsistent"
        if "cycle_align" in self.meta:
            return "aligned-source-phase-unverified"
        if self.meta.get("loop") is not True:
            return "loop-playback-unverified"
        rect = self.meta.get("source_rect")
        if rect is not None and (not isinstance(rect, list) or len(rect) != 4 or not all(_number(v) for v in rect)
                                 or rect[2] <= rect[0] or rect[3] <= rect[1]):
            return "source-geometry-invalid"
        reported_strip = self.source_report.get("strip")
        if not isinstance(reported_strip, dict) or any(self.meta.get(k) != reported_strip.get(k)
                                                      for k in ("w", "h", "frames", "scale", "source_rect", "foot_anchor")):
            return "source-geometry-inconsistent"
        return None

    def describe(self) -> dict[str, Any]:
        return {"artifacts": self.artifacts, "frames": len(self.frames), "cell": list(self.frames[0].size),
                "timing": {"delay_ms": self.delay, "duration_ms": self.delay * len(self.frames) if self.delay else None},
                "source_cut": self.meta.get("source_cut"), "producer": self.source_report.get("producer"),
                "gait": self.source_report.get("gait"), "follow": "follow" in self.meta,
                "pixels_sha256": evidence.digest(b"".join(f.tobytes() for f in self.frames))}


def _measure(loop: Loop) -> dict[str, Any]:
    frames = loop.frames
    rgba = [np.asarray(f, dtype=np.float64) / 255 for f in frames]
    # Fixed native cell area; no private per-cut median in the denominator.
    premul = [np.dstack((a[..., :3] * a[..., 3:], a[..., 3])) for a in rgba]
    seam = float(np.abs(premul[-1] - premul[0]).mean())
    quality = [rife.smear(f, frames[k - 1], frames[(k + 1) % len(frames)]) for k, f in enumerate(frames)]
    body: dict[str, Any]
    try:
        y, x, height, worn = follow.body_motion(frames)
        body = {"x_px": x.tolist(), "y_px": y.tolist(), "height_px": height, "worn_px": worn}
    except ValueError as exc:
        body = {"unknown": str(exc)}
    try:
        head = repair.head_track([a[..., 3] for a in rgba])
    except ValueError as exc:
        head = {"unknown": str(exc)}
    return {"seam": {"premultiplied_rgba_mae": seam,
                     "per_second": seam * 1000 / loop.delay if loop.delay else None},
            "interpolation": {"frames": quality, "faults": [faults(q) for q in quality]},
            "body_motion": body, "top_band_motion": head}


def _axis(a: Any, b: Any, comparable: bool, reason: str, status: str = "unknown") -> dict[str, Any]:
    return {"baseline": a, "candidate": b, "comparable": comparable, "reason": reason, "status": status}


def _order(a: list[float], b: list[float]) -> str:
    # No tradeoffs/averaging: one worsening component is a regression.
    delta = np.asarray(b) - np.asarray(a)
    if (delta > 1e-9).any():
        return "regressed"
    return "improved" if (delta < -1e-9).any() else "non_regressing"


def compare(baseline: Loop, candidate: Loop) -> dict[str, Any]:
    a, b = baseline, candidate
    ma, mb = _measure(a), _measure(b)
    reasons = []
    for side, loop in (("baseline", a), ("candidate", b)):
        for issue in (loop.timing_issue(), loop.provenance_issue()):
            if issue:
                reasons.append(f"{side}:{issue}")
    same_samples = (not reasons and a.meta["source_cut"] == b.meta["source_cut"])
    if not reasons and not same_samples:
        reasons.append("different-source-samples-phase-unverified")
    same_timing = a.delay == b.delay and len(a.frames) == len(b.frames)
    if not same_timing:
        reasons.append("different-timing")
    # A fixed crop and scale put both arrays in the same coordinate system. Per-frame
    # foot anchoring or an unrecorded/legacy crop cannot establish this.
    geometry_keys = ("w", "h", "source_rect", "scale", "foot_anchor")
    same_geometry = (all(k in a.meta and k in b.meta and a.meta[k] == b.meta[k] for k in geometry_keys)
                     and isinstance(a.meta.get("source_rect"), list) and len(a.meta["source_rect"]) == 4
                     and _number(a.meta.get("scale")) and a.meta["scale"] > 0)
    if not same_geometry:
        reasons.append("spatial-basis-unverified")
    comparable = bool(same_samples and same_timing and same_geometry and not reasons)
    same_coverage = (comparable and all(x.getchannel("A").tobytes() == y.getchannel("A").tobytes()
                                       for x, y in zip(a.frames, b.frames)))
    same_pixels = comparable and all(x.tobytes() == y.tobytes() for x, y in zip(a.frames, b.frames))
    changed = [k for k, (x, y) in enumerate(zip(a.frames, b.frames)) if x.tobytes() != y.tobytes()] if comparable else []
    # A smaller seam may just be a flattened drawing. Only frames with a pre-existing
    # shared interpolation fault may change for an automatic improvement claim.
    confined = comparable and all(ma["interpolation"]["faults"][k] for k in changed)
    cleared = comparable and all(not mb["interpolation"]["faults"][k] for k in changed)
    axes = {
        "phase": _axis(a.meta.get("source_cut"), b.meta.get("source_cut"), bool(same_samples),
                       "same-source-samples" if same_samples else "source-phase-unverified",
                       "non_regressing" if same_samples else "unknown"),
        "silhouette_motion": _axis("coverage-as-delivered", "coverage-as-delivered", comparable,
                                  "identical-coverage-preserves-bob-and-pose" if same_coverage else "changed-coverage-pose-preservation-unverified",
                                  "non_regressing" if same_coverage else "unknown"),
        "body_motion": _axis(ma["body_motion"], mb["body_motion"], comparable and all("unknown" not in m["body_motion"] for m in (ma, mb)),
                             "observation-only-less-motion-is-not-better"),
        "top_band_motion": _axis(ma["top_band_motion"], mb["top_band_motion"], comparable and all("unknown" not in m["top_band_motion"] for m in (ma, mb)),
                                 "silhouette-top-not-an-identified-head"),
        "scale": _axis(a.meta.get("scale"), b.meta.get("scale"), comparable,
                       "identical-coverage-preserves-size" if same_coverage else "rigid-scale-not-inferred-from-bbox",
                       "non_regressing" if same_coverage else "unknown"),
        "drawing_preservation": _axis([], changed, comparable,
                                      "unchanged-unfaulted-drawings" if confined else "changed-unfaulted-drawings-unverified",
                                      "non_regressing" if confined else "unknown"),
    }
    seam_status = _order([ma["seam"]["premultiplied_rgba_mae"]], [mb["seam"]["premultiplied_rgba_mae"]]) if comparable else "unknown"
    axes["seam"] = _axis(ma["seam"], mb["seam"], comparable, "native-common-canvas-no-relative-denominator", seam_status)
    for metric in ("dark_excess", "outline_loss", "partial_excess"):
        va, vb = ([max(0.0, q[metric]) for q in m["interpolation"]["frames"]] for m in (ma, mb))
        axes[metric] = _axis(va, vb, comparable, "same-sample-excess-beyond-both-neighbours",
                             _order(va, vb) if comparable else "unknown")
    statuses = [axes[k]["status"] for k in ("seam", "dark_excess", "outline_loss", "partial_excess")]
    if not comparable:
        verdict = "unknown"
    elif "regressed" in statuses:
        verdict = "regressed"
        reasons.extend(f"{k}:regressed" for k, v in axes.items() if v["status"] == "regressed")
    elif not same_coverage:
        verdict = "unknown"
        reasons.append("changed-coverage-pose-preservation-unverified")
    elif same_pixels:
        verdict = "non_regressing"
        reasons.append("identical-pixels-and-timing")
    elif not confined:
        verdict = "unknown"
        reasons.append("changed-unfaulted-drawings-unverified")
    elif not cleared:
        verdict = "unknown"
        reasons.append("interpolation-fault-remains")
    elif "improved" in statuses:
        verdict = "improved"
        reasons.append("measured-defect-reduced-with-motion-preserved")
    else:
        verdict = "non_regressing"
        reasons.append("no-measured-defect-improvement")
    return {"kind": KIND, "schema_version": 1, "metric_version": METRIC_VERSION,
            "measurement_engine": evidence.engine_identity(), "verdict": verdict, "reasons": reasons,
            "baseline": a.describe(), "candidate": b.describe(), "axes": axes,
            "limits": ["same-sample defect comparison, not overall animation quality",
                       "own-foot identity and intended drawing changes are not certified",
                       "changed coverage requires pose review; no motion flattening or resampling"]}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    for side in ("baseline", "candidate"):
        for artifact in ("strip", "meta", "report"):
            parser.add_argument(f"--{side}-{artifact}", required=True, type=Path)
        for artifact in ("gif", "webp"):
            parser.add_argument(f"--{side}-{artifact}", type=Path)
    from sprite_gen.video import source
    source.add_inputs(parser, required=False)
    parser.add_argument("--repair-evidence", type=Path)
    parser.add_argument("--report", required=True, type=Path)


def run(**kwargs: Any) -> int:
    output = Path(kwargs["report"])
    paths = {s: {k: Path(kwargs[f"{s}_{k}"]) for k in ("strip", "meta", "report")} for s in ("baseline", "candidate")}
    try:
        from sprite_gen.video import source
        restoration_mode = any(kwargs.get(k) is not None for k in (*source.INPUTS, "repair_evidence"))
        protected = [p for row in paths.values() for p in row.values()]
        if restoration_mode:
            from sprite_gen.video import restoration
            inputs = source.input_paths(kwargs)
            if any(kwargs.get(f"{s}_{k}") is None for s in paths for k in ("gif", "webp")) or not kwargs.get("repair_evidence"):
                raise ValueError("source comparison requires baseline/candidate GIF, WebP and repair evidence")
            for side in paths:
                paths[side].update({k: Path(kwargs[f"{side}_{k}"]) for k in ("gif", "webp")})
            evidence_path = Path(kwargs["repair_evidence"])
            protected += [*inputs.values(), evidence_path, *inputs["source_frames_dir"].glob("*.png")]
            protected += [p for row in paths.values() for p in row.values()]
        elif any(kwargs.get(f"{s}_{k}") for s in paths for k in ("gif", "webp")):
            raise ValueError("playback comparison requires source inputs and repair evidence")
        if any(output.resolve() == p.resolve() for p in protected):
            raise ValueError("comparison report cannot overwrite an input artifact")
        if restoration_mode:
            a, ap = restoration.read_loop(paths["baseline"])
            b, bp = restoration.read_loop(paths["candidate"])
            payload = restoration.compare(a, b, source.Source.read(**inputs), baseline_playback=ap, candidate_playback=bp,
                                          repair_evidence=_json(evidence_path.read_bytes(), "repair evidence"))
        else:
            payload = compare(Loop.read(**paths["baseline"]), Loop.read(**paths["candidate"]))
        text = json.dumps(payload, indent=2, allow_nan=False) + "\n"
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(output, text)
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit(f"video-loop-compare: {exc}") from exc
    print(text, end="")
    return 0
