# SPDX-License-Identifier: Apache-2.0
"""Score sprite inspection reports and produce correction hints."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from sprite_gen.runio import atomic_write_text


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect-report", required=True, type=Path)
    parser.add_argument("--output", default="sprite-score.report.json")
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


def _hint_for_row(row: dict[str, Any], *, histogram_min: float, dhash_min: float) -> list[str]:
    state = row["state"]
    expected = int(row.get("expected_frames", 0))
    found = int(row.get("found_frames", 0))
    metrics = row.get("metrics") or {}
    hints: list[str] = []
    if found != expected:
        hints.append(
            f"{state}: The previous strip was read as {found} pose(s), but the request requires exactly "
            f"{expected}. Regenerate as exactly {expected} full-body poses in {expected} equal invisible "
            "horizontal slots. Keep clear gutters between poses; no limbs, props, shadows, or effects may cross a slot boundary."
        )
    hist_min = float((metrics.get("histogram_intersection") or {}).get("min", 1.0))
    if histogram_min > 0 and hist_min < histogram_min:
        hints.append(
            f"{state}: Frame-to-frame color identity drift was detected (RGB histogram similarity {hist_min:.3f}). "
            "Copy the accepted anchor palette, outfit colors, hair color, face markings, and outline weight in every pose."
        )
    row_dhash_min = float((metrics.get("dhash_similarity") or {}).get("min", 1.0))
    if row_dhash_min < dhash_min:
        hints.append(
            f"{state}: Silhouette drift was detected (dHash similarity {row_dhash_min:.3f}). "
            "Keep the same body proportions and camera angle; only change the limb motion needed for this action."
        )
    motion = float(metrics.get("motion_presence", 1.0))
    if motion < 0.01 and expected > 1:
        hints.append(
            f"{state}: Adjacent frames are too similar (motion presence {motion:.4f}). "
            "Make the action visibly progress across the row while preserving the same character identity and foot baseline."
        )
    for message in [*row.get("errors", []), *row.get("warnings", [])]:
        text = str(message)
        if "chroma-adjacent" in text:
            hints.append(
                f"{state}: Visible chroma residue remained after extraction. Use a flat clean chroma background and keep key-colored pixels away from the character."
            )
        elif "edge" in text:
            hints.append(
                f"{state}: Sprite content touched the frame edge. Leave the requested safe margin around every full-body pose."
            )
        elif "pitch" in text or "runlen" in text or "grid" in text:
            hints.append(
                f"{state}: Pixel-grid pitch instability was detected. Regenerate with a clearer true low-resolution pixel grid, or rerun extraction with the runlen crosscheck visible and review the before/after grid proof."
            )
    return hints


def score_inspection(report: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    hints: list[str] = []
    thresholds = report.get("thresholds") or {}
    histogram_min = float(thresholds.get("histogram_min", 0.0))
    dhash_min = float(thresholds.get("dhash_min", 0.55))
    for row in report.get("rows", []):
        expected = int(row.get("expected_frames", 0))
        found = int(row.get("found_frames", 0))
        errors = list(row.get("errors", []))
        warnings = list(row.get("warnings", []))
        metrics = row.get("metrics") or {}
        score = 100.0
        if found != expected:
            score -= 35 + 10 * abs(found - expected)
        score -= 13 * len(errors)
        score -= 3 * len(warnings)
        if float(metrics.get("motion_presence", 1.0)) < 0.01 and expected > 1:
            score -= 12
        if float((metrics.get("dhash_similarity") or {}).get("min", 1.0)) < dhash_min:
            score -= 10
        if histogram_min > 0 and float((metrics.get("histogram_intersection") or {}).get("min", 1.0)) < histogram_min:
            score -= 10
        score = max(0.0, min(100.0, score))
        row_hints = _unique(_hint_for_row(row, histogram_min=histogram_min, dhash_min=dhash_min))
        hints.extend(row_hints)
        rows.append(
            {
                "state": row.get("state"),
                "score": round(score, 2),
                "candidate_rank": found * 100 - len(errors) * 10 - len(warnings),
                "expected_frames": expected,
                "found_frames": found,
                "errors": errors,
                "warnings": warnings,
                "hints": row_hints,
            }
        )
    scores = [float(row["score"]) for row in rows]
    return {
        "ok": bool(rows) and all(row["score"] >= 90 and not row["errors"] for row in rows),
        "kind": "sprite-gen-score-report",
        "source_report": report.get("run_dir"),
        "overall_score": round(mean(scores), 2) if scores else 0.0,
        "candidate_rank": sum(int(row["candidate_rank"]) for row in rows),
        "rows": rows,
        "hints": _unique(hints),
    }


def _run(args: argparse.Namespace) -> int:
    report = json.loads(args.inspect_report.read_text(encoding="utf-8"))
    scored = score_inspection(report)
    if not args.no_write:
        output = Path(args.output)
        if not output.is_absolute():
            output = args.inspect_report.parent / output
        atomic_write_text(output, json.dumps(scored, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in scored.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return 0 if scored["ok"] else 1


def run(**kwargs: object) -> int:
    return _run(_namespace_from_kwargs(**kwargs))


def main() -> int:
    return _run(_build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
