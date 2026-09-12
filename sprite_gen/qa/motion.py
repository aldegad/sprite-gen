# SPDX-License-Identifier: Apache-2.0
"""Inspect motion without editing frames or inferring travel from a silhouette.

Manual measurement example::

    python -m sprite_gen.qa.motion --source walk.json --contacts 0:4,8:12 \
        --foot-box 8,25,34,12 --facing right --out motion.json

Contacts are zero-based inclusive stance spans (or consecutive frame indices).
The caller asserts that the SAME foot is planted throughout each span. foot_box
is a fixed source-pixel x,y,width,height ROI enclosing only that foot throughout
its sweep. It must exclude the other foot and body. Facing is an annotation,
never a request to flip, reverse, or apply a speed. Confidence measures agreement
with these manual assumptions; pixels alone cannot establish ground contact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from numbers import Integral
from pathlib import Path
import sys
from typing import TYPE_CHECKING

from sprite_gen._deps import np
from PIL import Image

from sprite_gen.spec.assets import sequence_identity
from sprite_gen.spec.runio import acquire_run_dir_lock, atomic_write_set, release_run_dir_lock

if TYPE_CHECKING:
    from sprite_gen.spec.assets import FrameSequence


NEAR_SILHOUETTE_IOU = 0.98


def _number(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _contact_spans(contacts, count):
    if contacts is None:
        return []
    if isinstance(contacts, str):
        entries = []
        for token in contacts.split(","):
            parts = token.split(":")
            if len(parts) not in (1, 2) or not all(p.strip().isdigit() for p in parts):
                raise ValueError("contacts must be indices or inclusive spans, e.g. 0:4,8:12")
            values = tuple(int(p) for p in parts)
            entries.append(values[0] if len(values) == 1 else values)
    else:
        try:
            entries = list(contacts)
        except TypeError as exc:
            raise ValueError("contacts must be indices or inclusive spans") from exc
    if not entries:
        raise ValueError("contacts cannot be empty")
    spans = []
    previous_was_index = False
    for entry in entries:
        is_index = isinstance(entry, Integral) and not isinstance(entry, bool)
        if is_index:
            start = end = int(entry)
        else:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise ValueError("contacts must contain integer indices or (start, end) spans")
            start, end = entry
            if any(not isinstance(v, Integral) or isinstance(v, bool) for v in entry):
                raise ValueError("contact indices must be integers")
            start, end = int(start), int(end)
        if not 0 <= start <= end < count:
            raise ValueError("contact span is reversed or outside the frame sequence")
        if spans and start <= spans[-1][1]:
            raise ValueError("contacts must be ordered and cannot overlap or repeat")
        if is_index and previous_was_index and spans and start == spans[-1][1] + 1:
            spans[-1][1] = end
        else:
            spans.append([start, end])
        previous_was_index = is_index
    return spans


def _foot_roi(foot_box, size):
    if foot_box is None:
        return None
    if isinstance(foot_box, str):
        parts = foot_box.split(",")
        try:
            foot_box = tuple(int(p) for p in parts)
        except ValueError as exc:
            raise ValueError("foot_box must be integer x,y,width,height") from exc
    if not isinstance(foot_box, (list, tuple)) or len(foot_box) != 4:
        raise ValueError("foot_box must be integer x,y,width,height")
    if any(not isinstance(v, Integral) or isinstance(v, bool) for v in foot_box):
        raise ValueError("foot_box must be integer x,y,width,height")
    x, y, width, height = map(int, foot_box)
    if min(x, y) < 0 or min(width, height) <= 0 or x + width > size[0] or y + height > size[1]:
        raise ValueError("foot_box must fit inside the frame canvas with positive dimensions")
    return x, y, width, height


def _bbox(mask):
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def _components(mask):
    """Count 8-connected shapes only up to two; no selection of a 'best' blob."""
    remaining = set(zip(*np.nonzero(mask)))
    count = 0
    while remaining:
        count += 1
        if count == 2:
            return count
        stack = [remaining.pop()]
        while stack:
            y, x = stack.pop()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    neighbor = y + dy, x + dx
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        stack.append(neighbor)
    return count


def _foot_sample(mask, box, index, start):
    x, y, width, height = box
    region = mask[y:y + height, x:x + width]
    bbox = _bbox(region)
    sample = {"frame": index, "time_seconds": start, "bbox": None, "x": None, "y": None}
    reasons = []
    if bbox is None:
        return sample, ["foot ROI is empty in a contact frame"]
    left, top, right, bottom = bbox
    if _components(region) > 1:
        reasons.append("multiple disconnected shapes in foot ROI")
    # Two legs can be connected above their soles: connectivity alone is not
    # evidence for one foot. This band is only an ambiguity veto, never a tracker.
    band = region[max(top, bottom - 2):bottom].any(axis=0)
    lobes = np.count_nonzero(band & ~np.r_[False, band[:-1]])
    if lobes > 1:
        reasons.append("multiple contact lobes in foot ROI")
    if left == 0 or right == width:
        reasons.append("foot touches horizontal ROI boundary; tracking may be clipped")
    ys, xs = np.nonzero(region)
    sample.update(
        bbox=[left + x, top + y, right + x, bottom + y],
        x=float(xs.mean() + x + 0.5), y=float(ys.mean() + y + 0.5),
        area=int(region.sum()),
    )
    return sample, reasons


def _stance_candidate(masks, starts, durations, span, box):
    first, last = span
    candidate = {
        "frames": span, "status": "needs-review", "confidence": 0.0,
        "evidence": "manual contacts and foot ROI", "reasons": [], "samples": [],
        "duration_seconds": sum(durations[first:last + 1]),
        "measurement_elapsed_seconds": None,
        "foot_velocity_px_per_second": None, "stride_px_per_second": None,
    }
    reasons = candidate["reasons"]
    for index in range(first, last + 1):
        sample, defects = _foot_sample(masks[index], box, index, starts[index])
        candidate["samples"].append(sample)
        reasons.extend(defects)
    samples = candidate["samples"]
    if any(sample["bbox"] is None for sample in samples):
        candidate["reasons"] = list(dict.fromkeys(reasons))
        return candidate
    if max(s["bbox"][3] for s in samples) - min(s["bbox"][3] for s in samples) > 1:
        reasons.append("foot baseline moves vertically; contact may be airborne")
    if max(s["y"] for s in samples) - min(s["y"] for s in samples) > 1:
        reasons.append("foot centroid moves vertically; stance is uncertain")
    areas = [s["area"] for s in samples]
    if max(areas) > min(areas) * 1.25:
        reasons.append("foot silhouette area changes; tracking identity is uncertain")
    for axis in (0, 1):
        extents = [s["bbox"][axis + 2] - s["bbox"][axis] for s in samples]
        if max(extents) - min(extents) > max(1, min(extents) * 0.25):
            reasons.append("foot silhouette dimensions change; tracking identity is uncertain")
            break

    # Identical held observations carry time, but do not become extra regression
    # votes. Their first timestamps survive; the next movement retains EVERY
    # intervening duration. The final pose's hold is not a displacement interval.
    observations = []
    for sample in samples:
        if not observations or any(sample[key] != observations[-1][key] for key in ("x", "y", "bbox", "area")):
            observations.append(sample)
    candidate["pose_onset_frames"] = [s["frame"] for s in observations]
    if len(observations) < 3:
        reasons.append("need at least three distinct tracked poses for a stance measurement")
    if len(observations) >= 2:
        times = np.array([s["time_seconds"] for s in observations])
        xs = np.array([s["x"] for s in observations])
        elapsed = float(times[-1] - times[0])
        candidate["measurement_elapsed_seconds"] = elapsed
        velocity = _number(float(xs[-1] - xs[0]) / elapsed, "foot velocity")
        candidate["measured_velocity_candidate_px_per_second"] = velocity
        deltas = np.diff(xs)
        if (deltas > 0.25).any() and (deltas < -0.25).any():
            reasons.append("horizontal reversal")
        if abs(float(xs[-1] - xs[0])) < 1:
            reasons.append("insufficient net horizontal displacement")
        residuals = xs - (xs[0] + velocity * (times - times[0]))
        rmse = float(np.sqrt(np.mean(residuals ** 2)))
        candidate["fit_rmse_px"] = rmse
        if rmse > max(0.5, float(np.ptp(xs)) * 0.1):
            reasons.append("foot velocity is inconsistent across the declared stance")
        if not reasons:
            candidate.update(
                status="verified", confidence=0.9,
                foot_velocity_px_per_second=velocity, stride_px_per_second=abs(velocity),
            )
    candidate["reasons"] = list(dict.fromkeys(reasons))
    return candidate


def analyze_motion(sequence: FrameSequence, *, contacts=None, foot_box=None, facing=None) -> dict:
    """Return JSON-ready evidence, preserving sequence images and all frame time.

    ``contacts`` accepts ``"0:4,8:12"``, ``[(0, 4), (8, 12)]``, or an ordered
    iterable of indices (consecutive indices become one span). Spans never bridge
    the loop boundary. ``foot_box`` accepts a 4-tuple or ``"x,y,w,h"`` in pixels.
    Anchor coordinates follow FrameSequence's source-pixel contract. Positive
    foot velocity means right in image coordinates. Stride is its magnitude,
    conditional on the manually asserted stationary-ground contact, not a claim
    about travel direction. A report without verified contact returns null speed.
    ``sequence`` records which sequence was measured (source, atlas state, fps
    override, frame count, size, anchor, effective durations) so a consumer can
    refuse to apply the stride to any other selection of the same files.
    """
    frames, raw_durations = tuple(sequence.frames), tuple(sequence.durations)
    if not frames or len(frames) != len(raw_durations):
        raise ValueError("motion analysis needs one duration per frame and at least one frame")
    if any(not isinstance(frame, Image.Image) for frame in frames):
        raise ValueError("sequence frames must be PIL images")
    width, height = frames[0].size
    if min(width, height) <= 0 or any(frame.size != (width, height) for frame in frames):
        raise ValueError("sequence frames must share a nonempty canvas")
    durations = [_number(value, "duration") for value in raw_durations]
    if any(value <= 0 for value in durations):
        raise ValueError("frame durations must be positive")
    if len(sequence.anchor) != 2:
        raise ValueError("anchor requires x,y in source pixels")
    anchor = [_number(v, "anchor") for v in sequence.anchor]
    if facing not in (None, "left", "right"):
        raise ValueError("facing must be left or right")
    spans = _contact_spans(contacts, len(frames))
    box = _foot_roi(foot_box, (width, height))
    starts = []
    elapsed = 0.0
    for duration in durations:
        starts.append(elapsed)
        next_time = elapsed + duration
        if not math.isfinite(next_time) or next_time <= elapsed:
            raise ValueError("frame timing exceeds finite timestamp precision")
        elapsed = next_time

    masks, rows = [], []
    seen_frames, seen_masks = set(), set()
    unique_masks = []
    exact = exact_silhouette = near_silhouette = adjacent_exact = 0
    changed = silhouette_changed = 0
    silhouette_change = 0.0
    sides = {name: [] for name in ("left", "top", "right", "bottom")}
    previous_pixels = None
    previous_hash = None
    for index, frame in enumerate(frames):
        pixels = np.asarray(frame.convert("RGBA"))
        mask = pixels[:, :, 3] > 0
        image_hash = hashlib.sha256(pixels.tobytes()).digest()
        mask_hash = hashlib.sha256(mask.tobytes()).digest()
        exact += image_hash in seen_frames
        adjacent_exact += image_hash == previous_hash
        seen_frames.add(image_hash)
        previous_hash = image_hash
        if mask_hash in seen_masks:
            exact_silhouette += 1
        else:
            for earlier in unique_masks:
                union = np.count_nonzero(mask | earlier)
                if union and np.count_nonzero(mask & earlier) / union >= NEAR_SILHOUETTE_IOU:
                    near_silhouette += 1
                    break
            unique_masks.append(mask)
            seen_masks.add(mask_hash)
        if previous_pixels is not None:
            changed += bool(np.any(np.any(pixels != previous_pixels, axis=2) & (mask | masks[-1])))
            union = np.count_nonzero(mask | masks[-1])
            difference = np.count_nonzero(mask ^ masks[-1])
            silhouette_changed += bool(difference > 0)
            silhouette_change += difference / union if union else 0.0
        previous_pixels = pixels
        masks.append(mask)
        bbox = _bbox(mask)
        edge_sides = []
        if bbox:
            for side, touches in zip(sides, (bbox[0] == 0, bbox[1] == 0, bbox[2] == width, bbox[3] == height)):
                if touches:
                    sides[side].append(index)
                    edge_sides.append(side)
        ys, xs = np.nonzero(mask)
        rows.append({
            "frame": index, "time_seconds": starts[index], "duration_seconds": durations[index],
            "bbox": bbox, "anchor_px": anchor.copy(),
            "bbox_from_anchor": [v - anchor[i % 2] for i, v in enumerate(bbox)] if bbox else None,
            "centroid_px": [float(xs.mean() + 0.5), float(ys.mean() + 0.5)] if bbox else None,
            "opaque_pixels": int(mask.sum()), "edge_sides": edge_sides,
        })
    bboxes = [row["bbox"] for row in rows if row["bbox"] is not None]
    bounds = {
        "union": [min(b[0] for b in bboxes), min(b[1] for b in bboxes),
                  max(b[2] for b in bboxes), max(b[3] for b in bboxes)] if bboxes else None,
        "width_range": [min(b[2] - b[0] for b in bboxes), max(b[2] - b[0] for b in bboxes)] if bboxes else None,
        "height_range": [min(b[3] - b[1] for b in bboxes), max(b[3] - b[1] for b in bboxes)] if bboxes else None,
        "bottom_range": [min(b[3] for b in bboxes), max(b[3] for b in bboxes)] if bboxes else None,
    }
    warnings = []
    candidates = [_stance_candidate(masks, starts, durations, span, box) for span in spans] if box else []
    status = "needs-review" if changed else "unknown"
    velocity = stride = None
    confidence = 0.0
    if not spans or box is None:
        warnings.append("Ground contact is unknown: provide both manual contacts and a single-foot ROI; silhouette bottoms do not identify a planted foot.")
    if candidates:
        verified = [c for c in candidates if c["status"] == "verified"]
        if len(verified) != len(candidates):
            warnings.append("At least one declared stance failed contact checks; no aggregate stride is verified.")
            status = "needs-review"
        else:
            velocities = [c["foot_velocity_px_per_second"] for c in verified]
            if min(velocities) * max(velocities) <= 0:
                warnings.append("Declared stance spans disagree on foot motion sign.")
                status = "needs-review"
            elif max(map(abs, velocities)) > min(map(abs, velocities)) * 1.25:
                warnings.append("Declared stance spans disagree on speed by more than 25 percent.")
                status = "needs-review"
            else:
                measured_time = sum(c["measurement_elapsed_seconds"] for c in verified)
                velocity = sum(c["foot_velocity_px_per_second"] * c["measurement_elapsed_seconds"] for c in verified) / measured_time
                stride, confidence, status = abs(velocity), 0.9, "verified"
                if facing and (velocity > 0) == (facing == "right"):
                    warnings.append("Measured foot motion points toward the stated facing; review the annotations. Measurement sign is unchanged.")
    edges = sorted({index for indices in sides.values() for index in indices})
    if edges:
        warnings.append("Visible pixels touch the canvas edge; clipping is possible.")
    if len(bboxes) < len(frames):
        warnings.append("The sequence contains empty silhouettes.")
    if exact or exact_silhouette or near_silhouette:
        warnings.append("Repeated poses retain all source frame durations; no frames were removed.")
    return {
        "kind": "sprite-gen-motion-report", "version": 1, "status": status,
        "frame_count": len(frames), "duration_seconds": elapsed, "size": [width, height],
        "anchor_px": anchor, "facing": facing, "contacts": spans, "foot_box": list(box) if box else None,
        "duplicate_frames": {"exact": exact, "adjacent_exact": adjacent_exact,
                             "exact_silhouette": exact_silhouette, "near_silhouette": near_silhouette},
        "motion": {"present": bool(changed), "changed_transitions": changed,
                   "silhouette_changed_transitions": silhouette_changed,
                   "silhouette_change_per_second": _number(float(silhouette_change) / elapsed, "motion rate")},
        "frames": rows, "bbox_stats": bounds, "edge_contact": {"frames": edges, "sides": sides},
        "contact_candidates": candidates, "confidence": confidence,
        "sequence": sequence_identity(sequence),
        "source_fingerprints": sequence.fingerprints(), "stride_verified": status == "verified",
        "foot_velocity_px_per_second": velocity, "stride_px_per_second": stride,
        "warnings": warnings,
        "method": {
            "silhouette": "alpha > 0 in source canvas coordinates; bbox right/bottom are exclusive",
            "near_silhouette_iou": NEAR_SILHOUETTE_IOU,
            "duplicates": "Counts frames matching an earlier frame; near excludes exact silhouette matches. No time or frames are removed.",
            "motion": "Adjacent visible pixel changes; silhouette XOR/union summed over full duration. No loop-seam assumption.",
            "contact": "User-declared same-foot stance spans plus isolated ROI alpha centroid; no automatic contact or facing inference.",
            "timing": "Cumulative frame-start seconds; repeated tracked poses use their first onset with all intervening durations retained. Final hold is included in duration but has no displacement endpoint.",
            "velocity": "Endpoint horizontal displacement / pose-onset elapsed seconds; positive = image right. Stride is absolute velocity under manual ground-contact assumptions.",
            "confidence": "0.9 = at least 3 distinct poses, stable baseline/shape, monotonic near-linear motion and consistent spans. Heuristic conditional confidence, not a calibrated contact probability.",
            "sequence": "Identity of the analysed sequence: source, atlas state, fps override, frame count, size, anchor and effective per-frame durations. A consumer applying the stride must match all of it and source_fingerprints; another state or frame rate of the same files is a different sequence.",
        },
    }


def add_arguments(parser):
    """Register inspect-motion arguments on the parent's CLI subparser."""
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--state", help="Atlas state, required when the source has several")
    parser.add_argument("--fps", type=float, help="Explicit frame-rate override")
    parser.add_argument("--anchor-x", type=float, help="Anchor x in source pixels; supply with --anchor-y")
    parser.add_argument("--anchor-y", type=float, help="Anchor y in source pixels; supply with --anchor-x")
    parser.add_argument("--contacts", help="Manual same-foot stance: zero-based inclusive spans, e.g. 0:4,8:12")
    parser.add_argument("--foot-box", help="Fixed x,y,width,height pixel ROI isolating one foot throughout each stance")
    parser.add_argument("--facing", choices=("left", "right"), help="Annotation only; never flips frames or measurement sign")
    parser.add_argument("--out", type=Path, help="Optional JSON report; otherwise stdout only")


def _output_path(out, source, sequence):
    path = Path(out).expanduser().resolve()
    protected = {Path(source).expanduser().resolve(), *(Path(p).resolve() for p in sequence.source_files)}
    if path in protected or (path.exists() and any(p.exists() and path.samefile(p) for p in protected)):
        raise ValueError("motion report must not overwrite an asset source file")
    if path.name == ".sprite-gen.lock" or path.name.endswith(".sg-rwlock"):
        raise ValueError("motion report must not overwrite an output lock")
    if path.exists() and not path.is_file():
        raise ValueError("motion report output must be a file")
    for ancestor in path.parents:
        if ancestor.exists() and not ancestor.is_dir():
            raise ValueError("motion report parent must be a directory")
    return path


def run(**kwargs) -> int:
    """Analyze once; return 0 for a valid report (including unknown), 2 on error."""
    from sprite_gen.spec.assets import load_asset

    parser = argparse.ArgumentParser(add_help=False)
    add_arguments(parser)
    allowed = {action.dest: action.default for action in parser._actions}
    unexpected = kwargs.keys() - allowed.keys()
    if unexpected:
        raise TypeError(f"unexpected keyword arguments: {', '.join(sorted(unexpected))}")
    args = argparse.Namespace(**(allowed | kwargs))
    try:
        if args.source is None:
            raise ValueError("source is required")
        if (args.anchor_x is None) != (args.anchor_y is None):
            raise ValueError("anchor-x and anchor-y must be supplied together")
        anchor = None if args.anchor_x is None else (args.anchor_x, args.anchor_y)
        sequence = load_asset(Path(args.source), state=args.state, fps=args.fps, anchor=anchor)
        report = analyze_motion(sequence, contacts=args.contacts, foot_box=args.foot_box, facing=args.facing)
        report["source_files"] = [str(p) for p in sequence.source_files]
        report["source"] = str(Path(args.source).expanduser().resolve())
        payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.out is not None:
            output = _output_path(args.out, args.source, sequence)
            output.parent.mkdir(parents=True, exist_ok=True)
            acquire_run_dir_lock(output.parent, "inspect-motion")
            try:
                atomic_write_set({output: payload})
            finally:
                release_run_dir_lock(output.parent)
        print(payload, end="")
        return 0
    except (OSError, ValueError) as exc:
        print(f"inspect-motion: {exc}", file=sys.stderr)
        return 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
