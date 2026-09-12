# SPDX-License-Identifier: Apache-2.0
"""Periodic background crops, joined with a hard minimum-error RGBA quilt.

The source must contain ``period + overlap`` pixels along the selected axis;
``1 <= overlap < period``. A bounded search chooses a crop. Its trailing
overlap B replaces part of the leading overlap A along a connected seam.
Every output pixel is copied intact from A or B, including its original alpha.
There is no resampling, blur, compositing, or colour interpolation.

Reports describe local pixel differences, not semantic continuity of a painting.
Inspect repeated tiles visually even when the numerical checks pass.
"""

from __future__ import annotations

import argparse
import io
import json
from numbers import Integral
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.spec.runio import acquire_run_dir_lock, atomic_write_set, release_run_dir_lock


MAX_CANDIDATES = 32
_ASSESSMENT = (
    "Local pixel discontinuity checks only; review repeated tiles visually for "
    "cut silhouettes, repeated landmarks, and painting continuity."
)


def _pixels(image: Image.Image, axis: str) -> Any:
    if axis not in ("x", "y"):
        raise ValueError("axis must be x or y")
    if not isinstance(image, Image.Image):
        raise TypeError("image must be a PIL image")
    if min(image.size) < 1:
        raise ValueError("image dimensions must be positive")
    pixels = np.asarray(image.convert("RGBA"))
    return pixels if axis == "x" else pixels.transpose(1, 0, 2)


def _differences(a: Any, b: Any) -> tuple[Any, Any]:
    """Visible RGB on both black and white, plus independent alpha (0..255)."""
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    alpha_delta = a[..., 3] - b[..., 3]
    black_delta = a[..., :3] * (a[..., 3:] / 255) - b[..., :3] * (b[..., 3:] / 255)
    white_delta = black_delta - alpha_delta[..., None]
    color = np.maximum(np.abs(black_delta), np.abs(white_delta)).mean(axis=-1)
    return color, np.abs(alpha_delta)


def _stats(values: Any) -> dict[str, float]:
    if not values.size:
        return {"mean": 0.0, "p95": 0.0, "max": 0.0}
    return {"mean": float(values.mean()), "p95": float(np.percentile(values, 95)),
            "max": float(values.max())}


def _alpha_stats(values: Any) -> dict[str, float]:
    return {**_stats(values),
            "mismatch_fraction": float(np.count_nonzero(values) / values.size) if values.size else 0.0}


def _limits(interior: dict[str, float]) -> dict[str, float]:
    return {key: max(floor, 2 * interior[key])
            for key, floor in (("mean", 3.0), ("p95", 8.0), ("max", 16.0))}


def _exceeds(measured: dict[str, float], limits: dict[str, float]) -> bool:
    return any(measured[key] > limit for key, limit in limits.items())


def _ratio(edge: float, interior: float) -> float | None:
    # JSON has no infinity. Null explicitly means a nonzero edge over a zero
    # baseline; the absolute metrics and thresholds still determine the verdict.
    return edge / interior if interior else (0.0 if edge == 0 else None)


def inspect_tile(image: Image.Image, axis: str = "x") -> dict[str, Any]:
    """Measure the actual wrap edge against ordinary interior neighbours.

    RGB is compared as displayed over both black and white, so invisible RGB
    cannot inflate a score and transparent silhouettes cannot disappear from it.
    All differences are in 8-bit channel units. ``pass`` is a local heuristic,
    never a guarantee of seamless artwork. An axis of length one lacks evidence.
    """
    pixels = _pixels(image, axis)
    edge_color, edge_alpha = _differences(pixels[:, -1], pixels[:, 0])
    inner_color, inner_alpha = _differences(pixels[:, :-1], pixels[:, 1:])
    color, alpha = _stats(edge_color), _alpha_stats(edge_alpha)
    interior_color, interior_alpha = _stats(inner_color), _alpha_stats(inner_alpha)
    color_limits, alpha_limits = _limits(interior_color), _limits(interior_alpha)
    reasons = []
    if pixels.shape[1] < 2:
        reasons.append("No interior neighbours along the tiling axis to establish a baseline.")
    if _exceeds(color, color_limits):
        reasons.append("Wrap edge color difference exceeds interior variation.")
    if _exceeds(alpha, alpha_limits):
        reasons.append("Wrap seam alpha mismatch exceeds interior variation.")
    return {
        "schema_version": 1, "axis": axis, "size": list(image.size),
        "period": int(pixels.shape[1]),
        "status": "needs-review" if reasons else "pass",
        "requires_visual_review": True, "assessment": _ASSESSMENT,
        "review_reasons": reasons,
        "color_metric": "mean per-channel absolute difference, worst of black/white composites (0..255)",
        "edge_color_difference": color,
        "interior_color_difference": interior_color,
        "edge_to_interior_color_ratio": _ratio(color["mean"], interior_color["mean"]),
        "seam_alpha_mismatch": alpha,
        "interior_alpha_difference": interior_alpha,
        "edge_to_interior_alpha_ratio": _ratio(alpha["mean"], interior_alpha["mean"]),
        "thresholds": {"color": color_limits, "alpha": alpha_limits},
    }


def _minimum_seam(error: Any) -> tuple[Any, float]:
    """Minimum accumulated error, with neighbouring rows moving at most one px.

    ``cut`` is the LAST B pixel, not the first A pixel. In particular cut=0
    must copy B at x=0: the wrap then joins adjacent source pixels P-1 and P.
    Reserve the overlap's final column for A when overlap > 1. A one-column
    overlap necessarily copies B there and switches to A at the next column.
    """
    height, overlap = error.shape
    width = max(1, overlap - 1)
    costs = error[0, :width].astype(np.float64)
    steps = np.zeros((height, width), dtype=np.int8)
    for row in range(1, height):
        left = np.concatenate(([np.inf], costs[:-1]))
        right = np.concatenate((costs[1:], [np.inf]))
        steps[row, (left < costs) & (left <= right)] = -1
        steps[row, (right < costs) & (right < left)] = 1
        costs = np.minimum(costs, np.minimum(left, right)) + error[row, :width]
    seam = np.empty(height, dtype=np.int32)
    seam[-1] = int(np.argmin(costs))
    score = float(costs[seam[-1]] / height)
    for row in range(height - 1, 0, -1):
        seam[row - 1] = seam[row] + steps[row, seam[row]]
    return seam, score


def _crop_box(start: int, length: int, cross: int, axis: str) -> list[int]:
    return [start, 0, start + length, cross] if axis == "x" else [0, start, cross, start + length]


def make_tile(
    image: Image.Image, period: int, *, axis: str = "x", overlap: int = 32,
) -> tuple[Image.Image, dict[str, Any]]:
    """Return an RGBA tile and JSON-safe report, leaving ``image`` untouched.

    The tile is ``period`` pixels along ``axis`` with the other dimension
    unchanged. At most MAX_CANDIDATES evenly spaced crop starts are evaluated,
    including both endpoints. The seam search is exact within each candidate,
    but the bounded crop search does not claim a global optimum.
    """
    for name, value in (("period", period), ("overlap", overlap)):
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise TypeError(f"{name} must be an integer")
    period, overlap = int(period), int(overlap)
    if period < 2 or not 1 <= overlap < period:
        raise ValueError("geometry requires period >= 2 and 1 <= overlap < period")
    pixels = _pixels(image, axis)
    height, length, _ = pixels.shape
    slack = length - period - overlap
    if slack < 0:
        raise ValueError(
            f"source has {length} pixels along {axis}; period + overlap requires {period + overlap}"
        )
    starts = np.unique(np.linspace(0, slack, min(MAX_CANDIDATES, slack + 1)).round().astype(int))
    best_score = float("inf")
    best_start = 0
    best_seam = None
    for start in starts:
        a = pixels[:, start:start + overlap].astype(np.float32)
        b = pixels[:, start + period:start + period + overlap].astype(np.float32)
        # Premultiplied RGB removes hidden RGB from the energy; alpha remains an
        # independent equally weighted channel. Selection still copies raw RGBA.
        a[..., :3] *= a[..., 3:] / 255
        b[..., :3] *= b[..., 3:] / 255
        seam, score = _minimum_seam(np.square(a - b).mean(axis=-1))
        if score < best_score:
            best_score, best_start, best_seam = score, int(start), seam
    assert best_seam is not None
    crop = pixels[:, best_start:best_start + period + overlap]
    result = crop[:, :period].copy()
    use_b = np.arange(overlap)[None, :] <= best_seam[:, None]
    result[:, :overlap] = np.where(use_b[..., None], crop[:, period:], crop[:, :overlap])
    tile = Image.fromarray(result if axis == "x" else result.transpose(1, 0, 2))
    report = inspect_tile(tile, axis)

    # The wrap is now a natural source adjacency. It can pass while the new
    # internal cut is bad, so inspect that cut separately against SOURCE detail.
    # Include vertical mask transitions as well as each row's horizontal cut.
    rows = np.arange(height)
    seam_color, seam_alpha = _differences(result[rows, best_seam], result[rows, best_seam + 1])
    reference_color, reference_alpha = _differences(crop[:, :-1], crop[:, 1:])
    turn_rows = np.flatnonzero(np.diff(best_seam))
    if turn_rows.size:
        turn_cols = np.maximum(best_seam[turn_rows], best_seam[turn_rows + 1])
        turn_color, turn_alpha = _differences(result[turn_rows, turn_cols], result[turn_rows + 1, turn_cols])
        seam_color = np.concatenate((seam_color, turn_color))
        seam_alpha = np.concatenate((seam_alpha, turn_alpha))
        vertical_color, vertical_alpha = _differences(crop[:-1], crop[1:])
        reference_color = np.concatenate((reference_color.ravel(), vertical_color.ravel()))
        reference_alpha = np.concatenate((reference_alpha.ravel(), vertical_alpha.ravel()))
    quilt_color, quilt_alpha = _stats(seam_color), _alpha_stats(seam_alpha)
    reference_color_stats, reference_alpha_stats = _stats(reference_color), _alpha_stats(reference_alpha)
    quilt_color_limits, quilt_alpha_limits = _limits(reference_color_stats), _limits(reference_alpha_stats)
    if _exceeds(quilt_color, quilt_color_limits):
        report["review_reasons"].append("Internal quilt color difference exceeds source interior variation.")
    if _exceeds(quilt_alpha, quilt_alpha_limits):
        report["review_reasons"].append("Internal quilt alpha mismatch exceeds source interior variation.")
    report.update({
        "status": "needs-review" if report["review_reasons"] else "pass",
        "method": "crop-overlap-minimum-error-quilt",
        "source_size": list(image.size),
        "source_crop": _crop_box(best_start, period + overlap, height, axis),
        "core_crop": _crop_box(best_start, period, height, axis),
        "overlap": overlap,
        "search": {"evaluated_candidates": int(starts.size), "candidate_limit": MAX_CANDIDATES,
                   "available_crops": slack + 1, "strategy": "evenly-spaced including endpoints"},
        "quilt_seam": {
            "positions": best_seam.tolist(), "position_meaning": "last tail/B pixel per cross-axis row",
            "mean_squared_error": best_score,
            "color_difference": quilt_color, "alpha_mismatch": quilt_alpha,
            "source_interior_color_difference": reference_color_stats,
            "source_interior_alpha_difference": reference_alpha_stats,
            "thresholds": {"color": quilt_color_limits, "alpha": quilt_alpha_limits},
        },
    })
    return tile, report


def _same_file(a: Path, b: Path) -> bool:
    return a == b or (a.exists() and b.exists() and a.samefile(b))


def _validate_paths(source: Path, out: Path, report: Path) -> None:
    if out.suffix.lower() != ".png" or report.suffix.lower() != ".json":
        raise ValueError("--out must be a PNG path and --report must be a JSON path")
    if not source.is_file():
        raise ValueError(f"source is not a file: {source}")
    for target in (out, report):
        if _same_file(source, target):
            raise ValueError(f"refusing to overwrite source: {target}")
        if target.exists() and not target.is_file():
            raise ValueError(f"output is not a regular file: {target}")
        for parent in target.parents:
            if parent.exists() and not parent.is_dir():
                raise ValueError(f"output parent is not a directory: {parent}")
    if _same_file(out, report):
        raise ValueError("PNG output and JSON report must be different files")
    if out in report.parents or report in out.parents:
        raise ValueError("an output file cannot be another output's parent directory")


def _publish(
    *, source: Path, out: Path, period: int, axis: str = "x", overlap: int = 32,
    report: Path | None = None,
) -> int:
    source = Path(source).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    report_path = Path(report).expanduser().resolve() if report is not None else out.with_suffix(".report.json")
    _validate_paths(source, out, report_path)
    with Image.open(source) as image:
        if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
            raise ValueError("--source must be a single-frame PNG")
        tile, metrics = make_tile(image, period, axis=axis, overlap=overlap)
    metrics.update({"source": str(source), "output": str(out)})
    png = io.BytesIO()
    tile.save(png, format="PNG")
    payloads = {out: png.getvalue(), report_path: json.dumps(metrics, indent=2, allow_nan=False) + "\n"}
    # Validate and encode everything before any filesystem mutation. Lock each
    # destination directory (stable ordering), since reports may live elsewhere.
    # The locks are task-scoped: whatever this call acquired is released when it
    # returns or fails, including after a partial acquisition, so an API caller
    # never keeps a writer lock until the process exits.
    _validate_paths(source, out, report_path)
    locked = []
    try:
        for directory in sorted({out.parent, report_path.parent}):
            directory.mkdir(parents=True, exist_ok=True)
            acquire_run_dir_lock(directory, "background-tile")
            locked.append(directory)
        _validate_paths(source, out, report_path)
        atomic_write_set(payloads)
    finally:
        for directory in reversed(locked):
            release_run_dir_lock(directory)
    print(f"background-tile: {metrics['status']} — {out} (report: {report_path})")
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", required=True, type=Path, help="independent single-frame PNG")
    parser.add_argument("--out", required=True, type=Path, help="output PNG; cannot overwrite the source")
    parser.add_argument("--period", required=True, type=int, help="tile length in pixels along --axis")
    parser.add_argument("--axis", choices=("x", "y"), default="x")
    parser.add_argument("--overlap", type=int, default=32,
                        help="1 <= overlap < period; source must contain period + overlap pixels")
    parser.add_argument("--report", type=Path, help="JSON report (default: <out-stem>.report.json)")


def run(**kwargs: Any) -> int:
    try:
        return _publish(**kwargs)
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit(f"background-tile: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
