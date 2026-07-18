# SPDX-License-Identifier: Apache-2.0
"""Inspect generated sprite rows for closed-loop correction signals."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from PIL import Image, ImageChops

from sprite_gen import extract
from sprite_gen.layout import frames_dir_rel, raw_rel, state_frame_total
from sprite_gen.runio import acquire_run_dir_lock, atomic_write_text, read_guard, relative_posix
from sprite_gen.segment import segment_strip


HISTOGRAM_BINS = 64
D_HASH_BITS = 64
DEFAULT_HISTOGRAM_MIN = 0.0
DEFAULT_DHASH_MIN = 0.55
DEFAULT_MOTION_MIN = 0.01


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--states", default="all")
    parser.add_argument("--report", default="sprite-inspect.report.json")
    parser.add_argument("--histogram-min", type=float, default=DEFAULT_HISTOGRAM_MIN)
    parser.add_argument("--dhash-min", type=float, default=DEFAULT_DHASH_MIN)
    parser.add_argument("--motion-min", type=float, default=DEFAULT_MOTION_MIN)
    parser.add_argument("--key-threshold", type=float, default=80.0)
    parser.add_argument("--fringe-key-threshold", type=float, default=180.0)
    parser.add_argument("--fringe-delta", type=float, default=18.0)
    parser.add_argument("--fringe-unmix-reach", type=int, default=None)
    parser.add_argument("--spill-max-fraction", type=float, default=None)
    parser.add_argument("--chroma-mode", choices=("rgb", "ycbcr"), default=None)
    parser.add_argument("--no-write", action="store_true")
    return parser


def _namespace_from_kwargs(**kwargs: object) -> argparse.Namespace:
    parser = _build_parser()
    values: dict[str, object] = {}
    remaining = dict(kwargs)
    for action in parser._actions:
        if action.dest == "help":
            continue
        default = [] if action.nargs == "*" else action.default
        if default is argparse.SUPPRESS:
            default = None
        value = remaining.pop(action.dest, default)
        if getattr(action, "required", False) and value is None:
            raise TypeError(f"missing required argument: {action.dest}")
        values[action.dest] = value
    if remaining:
        names = ", ".join(sorted(remaining))
        raise TypeError(f"unexpected keyword argument(s): {names}")
    return argparse.Namespace(**values)


def _state_list(request: dict[str, Any], states: str) -> list[str]:
    if states == "all":
        return list(request["states"])
    selected = [state.strip() for state in states.split(",") if state.strip()]
    unknown = [state for state in selected if state not in request["states"]]
    if unknown:
        raise SystemExit(f"unknown state(s) in request: {', '.join(unknown)}")
    return selected


def _rgba(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        return opened.convert("RGBA")


def _frame_sort_key(path: Path) -> int:
    stem = path.stem
    if stem.endswith(".plain"):
        stem = stem.removesuffix(".plain")
    try:
        return int(stem.split("-")[1])
    except (IndexError, ValueError):
        return 10**9


def _load_extracted_frames(run_dir: Path, state: str, request: dict[str, Any]) -> tuple[list[Image.Image], list[str]]:
    state_dir = run_dir / frames_dir_rel(request, state)
    if not state_dir.is_dir():
        return [], []
    paths = sorted(
        (path for path in state_dir.glob("frame-*.png") if not path.name.endswith(".plain.png")),
        key=_frame_sort_key,
    )
    return [_rgba(path) for path in paths], [relative_posix(path, run_dir) for path in paths]


def _strip_for_state(run_dir: Path, request: dict[str, Any], state: str, args: argparse.Namespace) -> Image.Image | None:
    raw_path = run_dir / raw_rel(request, state)
    if not raw_path.is_file():
        return None
    chroma_config = dict(request.get("chroma") or {})
    chroma_mode = args.chroma_mode if args.chroma_mode is not None else str(chroma_config.get("mode", "rgb"))
    chroma_key = tuple(int(value) for value in request["chroma_key"]["rgb"])
    unmix_reach = (
        args.fringe_unmix_reach
        if args.fringe_unmix_reach is not None
        else int(chroma_config.get("unmix_reach", 4))
    )
    spill_max_fraction = (
        args.spill_max_fraction
        if args.spill_max_fraction is not None
        else float(chroma_config.get("spill_max_fraction", 0.005))
    )
    with Image.open(raw_path) as opened:
        if chroma_mode == "ycbcr":
            notes: list[str] = []
            return extract.remove_chroma_background_ycbcr(opened, chroma_key, notes)
        return extract.remove_chroma_background(
            opened,
            chroma_key,
            args.key_threshold,
            args.fringe_key_threshold,
            args.fringe_delta,
            unmix_reach=unmix_reach,
            spill_max_fraction=spill_max_fraction,
        )


def _frames_from_raw(strip: Image.Image, expected: int, request: dict[str, Any]) -> tuple[list[Image.Image], int]:
    cell_width, cell_height, safe_margin_x, safe_margin_y = extract.cell_geometry(request["cell"])
    segments, natural = segment_strip(strip, expected)
    frames: list[Image.Image] = []
    for left, right in segments:
        frames.append(
            extract.fit_to_cell(
                strip.crop((left, 0, right, strip.height)),
                cell_width,
                cell_height,
                safe_margin_x,
                safe_margin_y,
                request.get("fit") or {},
            )
        )
    return frames, natural


def _rgb_histogram(image: Image.Image) -> list[float]:
    bins = [0] * HISTOGRAM_BINS
    total = 0
    for red, green, blue, alpha in image.convert("RGBA").getdata():
        if alpha <= 16:
            continue
        index = (red // 64) * 16 + (green // 64) * 4 + (blue // 64)
        bins[index] += 1
        total += 1
    if total == 0:
        return [0.0] * HISTOGRAM_BINS
    return [value / total for value in bins]


def histogram_intersection(left: list[float], right: list[float]) -> float:
    return sum(min(a, b) for a, b in zip(left, right))


def _dhash(image: Image.Image) -> int:
    flattened = Image.new("RGBA", image.size, (255, 255, 255, 255))
    flattened.alpha_composite(image.convert("RGBA"))
    small = flattened.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
    pixels = list(small.getdata())
    value = 0
    bit = 0
    for y in range(8):
        row = y * 9
        for x in range(8):
            if pixels[row + x] > pixels[row + x + 1]:
                value |= 1 << bit
            bit += 1
    return value


def dhash_similarity(left: int, right: int) -> float:
    return 1.0 - ((left ^ right).bit_count() / D_HASH_BITS)


def _alpha_centroid(image: Image.Image) -> tuple[float, float] | None:
    alpha = image.getchannel("A")
    total = 0
    weighted_x = 0
    weighted_y = 0
    for y in range(image.height):
        for x in range(image.width):
            value = alpha.getpixel((x, y))
            if value <= 10:
                continue
            total += value
            weighted_x += x * value
            weighted_y += y * value
    if total == 0:
        return None
    return weighted_x / total, weighted_y / total


def _motion_presence(frames: list[Image.Image]) -> float:
    if len(frames) < 2:
        return 0.0
    values: list[float] = []
    for left, right in zip(frames, frames[1:]):
        a = left.convert("RGBA").resize((64, 64), Image.Resampling.BILINEAR)
        b = right.convert("RGBA").resize((64, 64), Image.Resampling.BILINEAR)
        diff = ImageChops.difference(a, b)
        total = 0
        for pixel in diff.getdata():
            total += pixel[0] + pixel[1] + pixel[2] + pixel[3]
        values.append(total / (64 * 64 * 4 * 255))
    return mean(values) if values else 0.0


def _similarity_summary(frames: list[Image.Image]) -> dict[str, Any]:
    histograms = [_rgb_histogram(frame) for frame in frames]
    hashes = [_dhash(frame) for frame in frames]
    hist_pairs = [
        histogram_intersection(histograms[i], histograms[j])
        for i in range(len(histograms))
        for j in range(i + 1, len(histograms))
    ]
    dhash_pairs = [
        dhash_similarity(hashes[i], hashes[j])
        for i in range(len(hashes))
        for j in range(i + 1, len(hashes))
    ]
    centroids = [_alpha_centroid(frame) for frame in frames]
    xs = [point[0] for point in centroids if point is not None]
    ys = [point[1] for point in centroids if point is not None]
    return {
        "histogram_intersection": {
            "min": min(hist_pairs) if hist_pairs else 1.0,
            "mean": mean(hist_pairs) if hist_pairs else 1.0,
        },
        "dhash_similarity": {
            "min": min(dhash_pairs) if dhash_pairs else 1.0,
            "mean": mean(dhash_pairs) if dhash_pairs else 1.0,
        },
        "motion_presence": _motion_presence(frames),
        "centroid_sigma": {
            "x": pstdev(xs) if len(xs) > 1 else 0.0,
            "y": pstdev(ys) if len(ys) > 1 else 0.0,
        },
    }


def _manifest_state_notes(run_dir: Path, state: str) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Per-state manifest row + failure notes for `state`, gathered from two sources:

    - `frames/frames-manifest.json` — the current COMPLETE generation (only ever published
      on a successful extract, so it carries the row for a good state, no failure errors).
    - `extract-failure.json` — the last failed extract's per-state diagnostics, written
      OUTSIDE canonical frames/ because a failed extract publishes no partial generation
      (whole-generation atomicity). This is the signal the automatic correction loop needs to
      know *which state failed and why* and drive error-driven regeneration; a successful
      extract removes it, so it never re-flags a now-good state.

    The row comes only from the published manifest (real frames); errors/warnings merge both."""
    manifest_row: dict[str, Any] | None = None
    state_errors: list[str] = []
    state_warnings: list[str] = []

    def _collect(manifest: dict, take_row: bool) -> None:
        nonlocal manifest_row
        if not manifest:
            return
        prefix = f"{state}:"
        for message in manifest.get("errors", []):
            text = str(message)
            if text.startswith(prefix) and text not in state_errors:
                state_errors.append(text)
        for message in manifest.get("warnings", []):
            text = str(message)
            if text.startswith(prefix) and text not in state_warnings:
                state_warnings.append(text)
        if take_row and manifest_row is None:
            for row in manifest.get("rows", []):
                if row.get("state") == state:
                    manifest_row = row
                    break

    # fail-loud on a corrupt/broken canonical record (No Silent Fallback), {} only if absent
    _collect(extract.load_frames_manifest(run_dir / "frames" / "frames-manifest.json"), take_row=True)
    _collect(extract.load_failure_evidence(run_dir / "extract-failure.json"), take_row=False)
    return manifest_row, state_errors, state_warnings


def inspect_run(run_dir: Path, states: str = "all", **kwargs: object) -> dict[str, Any]:
    """Inspect the run holding a shared read_guard, so the combined read of frames/,
    frames-manifest.json, and extract-failure.json sees ONE consistent snapshot — never a
    mid-publish mix of a newly-swapped frames generation with a stale failure record. The
    correction loop reads through here, so its diagnostics stay isolated from a concurrent
    extract commit (which advances both surfaces under the matching publish_guard)."""
    with read_guard(Path(run_dir).expanduser().resolve()):
        return _inspect_run_impl(run_dir, states=states, **kwargs)


def _inspect_run_impl(run_dir: Path, states: str = "all", **kwargs: object) -> dict[str, Any]:
    args = _namespace_from_kwargs(run_dir=run_dir, states=states, no_write=True, **kwargs)
    run_dir = args.run_dir.expanduser().resolve()
    # Fail loud on a corrupt/inconsistent generation or orphan frames (frames with no manifest) —
    # otherwise the correction loop would read stale frames as ok:true (No Silent Fallback). A run
    # with no generation at all ({}) is fine: inspect falls back to raw-projection below.
    extract.load_consistent_frames_manifest(run_dir)
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    selected = _state_list(request, args.states)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    for state in selected:
        expected = state_frame_total(request, state)
        frames, files = _load_extracted_frames(run_dir, state, request)
        source = "frames"
        natural_found: int | None = None
        if not frames:
            strip = _strip_for_state(run_dir, request, state, args)
            if strip is None:
                row = {
                    "state": state,
                    "source": "missing",
                    "expected_frames": expected,
                    "found_frames": 0,
                    "natural_pose_count": 0,
                    "ok": False,
                    "errors": [f"{state}: missing extracted frames and raw strip"],
                    "warnings": [],
                }
                rows.append(row)
                errors.extend(row["errors"])
                continue
            frames, natural_found = _frames_from_raw(strip, expected, request)
            source = "raw-projection"

        found = len(frames) if source == "frames" else int(natural_found or len(frames))
        row_errors: list[str] = []
        row_warnings: list[str] = []
        if found != expected:
            row_errors.append(f"{state}: expected {expected} frame(s), inspect found {found}")
        manifest_row, manifest_errors, manifest_warnings = _manifest_state_notes(run_dir, state)
        row_errors.extend(manifest_errors)
        row_warnings.extend(manifest_warnings)
        if manifest_row is not None:
            row_errors.extend(str(error) for error in manifest_row.get("errors", []))
            row_warnings.extend(str(warning) for warning in manifest_row.get("warnings", []))
        metrics = _similarity_summary(frames)
        if args.histogram_min > 0 and metrics["histogram_intersection"]["min"] < args.histogram_min:
            row_warnings.append(
                f"{state}: RGB histogram identity similarity is low "
                f"({metrics['histogram_intersection']['min']:.3f} < {args.histogram_min:.3f})"
            )
        if metrics["dhash_similarity"]["min"] < args.dhash_min:
            row_warnings.append(
                f"{state}: dHash silhouette similarity is low "
                f"({metrics['dhash_similarity']['min']:.3f} < {args.dhash_min:.3f})"
            )
        if metrics["motion_presence"] < args.motion_min:
            row_warnings.append(
                f"{state}: motion presence is too low "
                f"({metrics['motion_presence']:.4f} < {args.motion_min:.4f})"
            )
        row = {
            "state": state,
            "source": source,
            "expected_frames": expected,
            "found_frames": found,
            "natural_pose_count": natural_found if natural_found is not None else found,
            "frame_files": files,
            "metrics": metrics,
            "ok": not row_errors,
            "errors": row_errors,
            "warnings": row_warnings,
        }
        rows.append(row)
        errors.extend(row_errors)
        warnings.extend(row_warnings)

    return {
        "ok": not errors,
        "engine": "component-row",
        "kind": "sprite-gen-inspect-report",
        "run_dir": str(run_dir),
        "states": selected,
        "thresholds": {
            "histogram_min": args.histogram_min,
            "dhash_min": args.dhash_min,
            "motion_min": args.motion_min,
        },
        "rows": rows,
        "errors": errors,
        "warnings": warnings,
    }


def _run(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    report = inspect_run(
        run_dir,
        states=args.states,
        histogram_min=args.histogram_min,
        dhash_min=args.dhash_min,
        motion_min=args.motion_min,
        key_threshold=args.key_threshold,
        fringe_key_threshold=args.fringe_key_threshold,
        fringe_delta=args.fringe_delta,
        fringe_unmix_reach=args.fringe_unmix_reach,
        spill_max_fraction=args.spill_max_fraction,
        chroma_mode=args.chroma_mode,
    )
    if not args.no_write:
        acquire_run_dir_lock(run_dir, "inspect_sprite_run")
        atomic_write_text(run_dir / args.report, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def run(**kwargs: object) -> int:
    return _run(_namespace_from_kwargs(**kwargs))


def main() -> int:
    return _run(_build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
