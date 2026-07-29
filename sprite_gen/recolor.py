# SPDX-License-Identifier: Apache-2.0
"""Deterministic palette-swap bake: one base sheet + a recolor spec -> N variant
sheets, in a single command.

Dot art lives and dies by palette control, so this bakes colour variants into
finished sheets instead of tinting at runtime: a baked sheet is QA-able and
costs the renderer nothing. The swap is exact by default — a pixel's RGB must
equal a source colour to be replaced — because a fuzzy match would recolour
anti-aliased edge pixels the artist never intended. A ``tolerance`` mode exists
for soft-edged art, but it is opt-in.

No Silent Fallback is the load-bearing rule here (see Alex's eight invariants):
a colour outside the map is never quietly left behind. Every bake reports, per
variant, exactly how many pixels each source colour replaced, which map sources
never matched (a spec typo), and which opaque colours in the sheet the map did
not cover (passed through unchanged, but named and counted). Same input bytes ->
same output bytes: the bake does no floating-point resampling and PIL's PNG
encoder is deterministic, so a variant sheet is reproducible for regression QA.

Two operations share this module:

- ``recolor-palette`` scans a base sheet and drafts a palette map (the distinct
  opaque colours, by frequency) so a consumer can author a recolor spec without
  eyedropping by hand.
- ``recolor`` bakes the variants from ``base sheet + spec``.

Geometry is never touched — recolour only rewrites RGB on matching pixels and
preserves alpha — so a manifest describing the base atlas describes every
variant. Pass ``--manifest`` and each variant gets a sibling manifest with its
sheet-filename fields repointed, ready to drop into the runtime beside the base.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.runio import atomic_save_image, atomic_write_text

# Opaque-enough to count as art. Edge pixels below this are treated as
# background bleed, not recolour targets (the extraction chain already soft-keys
# them). 8/255 matches compose-gif's alpha floor so the two agree on "a pixel".
DEFAULT_ALPHA_THRESHOLD = 8
# A passthrough report on a photographic sheet could list thousands of colours;
# cap the list but announce the cap (no silent truncation) so "0 passthrough"
# and "capped at 64 of 900" never look the same.
PASSTHROUGH_REPORT_CAP = 64

PALETTE_KIND = "sprite-gen-palette"
SPEC_KIND = "sprite-gen-recolor"
REPORT_KIND = "sprite-gen-recolor-report"

# Manifest string fields that name the atlas image, repointed per variant. Only
# these known keys are rewritten; an unknown schema is copied verbatim.
MANIFEST_SHEET_FIELDS = ("atlas", "game_input", "sprite_sheet_alpha", "sprite_sheet")


def parse_hex(value: str) -> tuple[int, int, int]:
    """Parse ``#RRGGBB`` (or ``RRGGBB``) into an ``(r, g, b)`` tuple."""
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"expected #RRGGBB, got {value!r}")
    try:
        return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError(f"invalid hex colour {value!r}") from exc


def format_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _load_rgba(path: Path) -> np.ndarray:
    """Load an image as an ``(H, W, 4)`` uint8 array."""
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)


def _packed_rgb(arr: np.ndarray) -> np.ndarray:
    """Pack the RGB channels of an ``(H, W, 4)`` array into ``(H, W)`` uint32."""
    rgb = arr[:, :, :3].astype(np.uint32)
    return (rgb[:, :, 0] << 16) | (rgb[:, :, 1] << 8) | rgb[:, :, 2]


def _pack_color(rgb: tuple[int, int, int]) -> int:
    return (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]


def _color_histogram(
    arr: np.ndarray, opaque: np.ndarray
) -> list[tuple[tuple[int, int, int], int]]:
    """Distinct opaque colours as ``[(rgb, pixels), ...]``, sorted by count desc
    then hex asc — a total order, so the draft is deterministic."""
    packed = _packed_rgb(arr)[opaque]
    if packed.size == 0:
        return []
    values, counts = np.unique(packed, return_counts=True)
    out = []
    for value, count in zip(values.tolist(), counts.tolist()):
        rgb = ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)
        out.append((rgb, int(count)))
    out.sort(key=lambda item: (-item[1], item[0]))
    return out


# --- palette extraction helper ------------------------------------------------


def extract_palette(
    base: Path,
    *,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
    max_colors: int | None = None,
) -> dict:
    """Draft a palette map from a base sheet: its distinct opaque colours by
    frequency. This is authoring scaffolding, not a swap — the consumer edits
    the draft into a recolor spec's ``map`` targets."""
    arr = _load_rgba(base)
    opaque = arr[:, :, 3] > alpha_threshold
    histogram = _color_histogram(arr, opaque)
    dropped = 0
    if max_colors is not None and len(histogram) > max_colors:
        dropped = sum(count for _rgb, count in histogram[max_colors:])
        histogram = histogram[:max_colors]
    palette = {
        "version": 1,
        "kind": PALETTE_KIND,
        "source": base.name,
        "alpha_threshold": alpha_threshold,
        "color_count": len(histogram),
        "colors": [
            {"hex": format_hex(rgb), "rgb": list(rgb), "pixels": count}
            for rgb, count in histogram
        ],
    }
    if dropped:
        # No silent cap: --max-colors dropped real pixels; say how many.
        palette["dropped_colors_pixels"] = dropped
        palette["dropped_below_rank"] = max_colors
    return palette


# --- recolor bake -------------------------------------------------------------


def _load_spec(spec_path: Path) -> dict:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    if spec.get("kind") != SPEC_KIND:
        raise SystemExit(
            f"{spec_path}: not a recolor spec (kind must be {SPEC_KIND!r}, got {spec.get('kind')!r})"
        )
    variants = spec.get("variants")
    if not isinstance(variants, list) or not variants:
        raise SystemExit(f"{spec_path}: spec has no variants")
    match = spec.get("match", "exact")
    if match not in ("exact", "tolerance"):
        raise SystemExit(f"{spec_path}: match must be 'exact' or 'tolerance', got {match!r}")
    tolerance = int(spec.get("tolerance", 0))
    if match == "exact" and tolerance:
        # Two truths for one behaviour: exact means tolerance 0. Fail loud
        # rather than silently pick one.
        raise SystemExit(
            f"{spec_path}: match 'exact' with tolerance {tolerance} is contradictory; "
            f"set match 'tolerance' to use a non-zero tolerance"
        )
    names = [v.get("name") for v in variants]
    if len(set(names)) != len(names):
        raise SystemExit(f"{spec_path}: variant names must be unique, got {names}")
    for variant in variants:
        if not variant.get("name"):
            raise SystemExit(f"{spec_path}: every variant needs a name")
        if not isinstance(variant.get("map"), dict) or not variant["map"]:
            raise SystemExit(f"{spec_path}: variant {variant.get('name')!r} has an empty map")
    return {"match": match, "tolerance": tolerance, "variants": variants}


def _resolve_map(variant_map: dict, spec_path: Path, name: str) -> list[tuple[tuple[int, int, int], tuple[int, int, int]]]:
    pairs = []
    seen_sources: set[tuple[int, int, int]] = set()
    for src_hex, tgt_hex in variant_map.items():
        try:
            src = parse_hex(src_hex)
            tgt = parse_hex(tgt_hex)
        except ValueError as exc:
            raise SystemExit(f"{spec_path}: variant {name!r}: {exc}") from exc
        if src in seen_sources:
            raise SystemExit(f"{spec_path}: variant {name!r}: source {src_hex} mapped twice")
        seen_sources.add(src)
        pairs.append((src, tgt))
    return pairs


def _bake_variant_exact(
    arr: np.ndarray, opaque: np.ndarray, pairs
) -> tuple[np.ndarray, list[dict], np.ndarray]:
    """Exact-match bake. Returns (recoloured array, per-source hit records,
    covered mask)."""
    packed = _packed_rgb(arr)
    out = arr.copy()
    covered = np.zeros(arr.shape[:2], dtype=bool)
    records = []
    for src, tgt in pairs:
        mask = (packed == _pack_color(src)) & opaque & ~covered
        hits = int(mask.sum())
        if hits:
            out[mask, 0] = tgt[0]
            out[mask, 1] = tgt[1]
            out[mask, 2] = tgt[2]
            covered |= mask
        records.append({"from": format_hex(src), "to": format_hex(tgt), "pixels": hits})
    return out, records, covered


def _bake_variant_tolerance(
    arr: np.ndarray, opaque: np.ndarray, pairs, tolerance: int
) -> tuple[np.ndarray, list[dict], np.ndarray]:
    """Tolerance bake: an opaque pixel within Chebyshev ``tolerance`` of a
    source is recoloured to that source's target; nearest source (L2) wins,
    ties broken by map order. Deterministic — pure integer arithmetic."""
    rgb = arr[:, :, :3].astype(np.int32)
    out = arr.copy()
    best_dist = np.full(arr.shape[:2], np.iinfo(np.int32).max, dtype=np.int32)
    best_idx = np.full(arr.shape[:2], -1, dtype=np.int32)
    for idx, (src, _tgt) in enumerate(pairs):
        delta = np.abs(rgb - np.array(src, dtype=np.int32))
        cheb = delta.max(axis=2)
        l2 = (delta * delta).sum(axis=2)
        within = opaque & (cheb <= tolerance)
        # Strict-less keeps the earlier map entry on a tie (stable, deterministic).
        take = within & (l2 < best_dist)
        best_dist = np.where(take, l2, best_dist)
        best_idx = np.where(take, idx, best_idx)
    covered = best_idx >= 0
    records = []
    for idx, (src, tgt) in enumerate(pairs):
        mask = best_idx == idx
        hits = int(mask.sum())
        if hits:
            out[mask, 0] = tgt[0]
            out[mask, 1] = tgt[1]
            out[mask, 2] = tgt[2]
        records.append({"from": format_hex(src), "to": format_hex(tgt), "pixels": hits})
    return out, records, covered


def _passthrough_report(
    arr: np.ndarray, opaque: np.ndarray, covered: np.ndarray
) -> tuple[list[dict], int, int]:
    """Opaque colours the map did not cover: passed through unchanged, but named
    and counted. Returns (top colours, total distinct count, total pixels)."""
    leftover = opaque & ~covered
    histogram = _color_histogram(arr, leftover)
    total_colors = len(histogram)
    total_pixels = sum(count for _rgb, count in histogram)
    top = [
        {"hex": format_hex(rgb), "pixels": count}
        for rgb, count in histogram[:PASSTHROUGH_REPORT_CAP]
    ]
    return top, total_colors, total_pixels


def _propagate_manifest(manifest_path: Path, out_dir: Path, sheet_name: str, variant_name: str) -> str:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base_sheet = manifest_path.name  # only for reference in log
    for field in MANIFEST_SHEET_FIELDS:
        if isinstance(manifest.get(field), str):
            manifest[field] = sheet_name
    target = out_dir / f"{variant_name}.manifest.json"
    atomic_write_text(target, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return target.name


def bake(
    base: Path,
    spec_path: Path,
    out_dir: Path,
    *,
    manifest: Path | None = None,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
    report_name: str = "recolor.report.json",
) -> dict:
    spec = _load_spec(spec_path)
    arr = _load_rgba(base)
    opaque = arr[:, :, 3] > alpha_threshold
    out_dir.mkdir(parents=True, exist_ok=True)

    variant_reports = []
    for variant in spec["variants"]:
        name = variant["name"]
        pairs = _resolve_map(variant["map"], spec_path, name)
        if spec["match"] == "exact":
            recolored, records, covered = _bake_variant_exact(arr, opaque, pairs)
        else:
            recolored, records, covered = _bake_variant_tolerance(
                arr, opaque, pairs, spec["tolerance"]
            )
        sheet_name = f"{name}.png"
        atomic_save_image(Image.fromarray(recolored, "RGBA"), out_dir / sheet_name)

        substituted = sum(record["pixels"] for record in records)
        unused = [record for record in records if record["pixels"] == 0]
        top, pass_colors, pass_pixels = _passthrough_report(arr, opaque, covered)
        variant_report = {
            "name": name,
            "sheet": sheet_name,
            "substituted_pixels": substituted,
            "substitutions": sorted(records, key=lambda r: r["from"]),
            "unused_sources": sorted(unused, key=lambda r: r["from"]),
            "passthrough_pixels": pass_pixels,
            "passthrough_color_count": pass_colors,
            "passthrough_colors": top,
        }
        if pass_colors > len(top):
            variant_report["passthrough_colors_truncated"] = pass_colors - len(top)
        if manifest is not None:
            variant_report["manifest"] = _propagate_manifest(
                manifest, out_dir, sheet_name, name
            )
        variant_reports.append(variant_report)

    report = {
        "ok": True,
        "kind": REPORT_KIND,
        "base": base.name,
        "match": spec["match"],
        "tolerance": spec["tolerance"],
        "alpha_threshold": alpha_threshold,
        "variant_count": len(variant_reports),
        "variants": variant_reports,
    }
    atomic_write_text(out_dir / report_name, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


# --- CLI ----------------------------------------------------------------------


def _resolve_base_and_manifest(
    base: Path | None,
    run_dir: Path | None,
    atlas: str,
    manifest: Path | None,
    out_dir: Path | None,
) -> tuple[Path, Path | None, Path]:
    """Resolve the base sheet, optional manifest, and out dir from either an
    explicit ``--base`` or a ``--run-dir`` (mutually exclusive — no two truths)."""
    if (base is None) == (run_dir is None):
        raise SystemExit("pass exactly one of --base <sheet.png> or --run-dir <dir>")
    if run_dir is not None:
        resolved_base = run_dir / atlas
        if not resolved_base.is_file():
            raise SystemExit(f"no atlas {atlas!r} in run dir: {run_dir}")
        resolved_manifest = manifest
        if resolved_manifest is None:
            candidate = run_dir / "manifest.json"
            resolved_manifest = candidate if candidate.is_file() else None
        resolved_out = out_dir if out_dir is not None else run_dir / "variants"
        return resolved_base, resolved_manifest, resolved_out
    if out_dir is None:
        raise SystemExit("--out-dir is required with --base")
    return base, manifest, out_dir


def add_recolor_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base", type=Path, help="base sheet PNG to recolour")
    p.add_argument("--run-dir", type=Path, help="run dir; base = <run-dir>/<atlas>, manifest auto-resolved")
    p.add_argument("--atlas", default="sprite-sheet-alpha.png", help="atlas filename inside --run-dir")
    p.add_argument("--spec", required=True, type=Path, help="recolor spec JSON")
    p.add_argument("--out-dir", type=Path, help="output dir (default <run-dir>/variants)")
    p.add_argument("--manifest", type=Path, help="source manifest to propagate per variant")
    p.add_argument("--alpha-threshold", type=int, default=DEFAULT_ALPHA_THRESHOLD)
    p.add_argument("--report", default="recolor.report.json")


def run(
    *,
    base: Path | None = None,
    run_dir: Path | None = None,
    atlas: str = "sprite-sheet-alpha.png",
    spec: Path,
    out_dir: Path | None = None,
    manifest: Path | None = None,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
    report: str = "recolor.report.json",
) -> int:
    resolved_base, resolved_manifest, resolved_out = _resolve_base_and_manifest(
        base, run_dir, atlas, manifest, out_dir
    )
    report_data = bake(
        resolved_base,
        spec,
        resolved_out,
        manifest=resolved_manifest,
        alpha_threshold=alpha_threshold,
        report_name=report,
    )
    summary = {
        "ok": report_data["ok"],
        "base": report_data["base"],
        "match": report_data["match"],
        "variants": [
            {
                "name": v["name"],
                "sheet": str(resolved_out / v["sheet"]),
                "substituted_pixels": v["substituted_pixels"],
                "passthrough_pixels": v["passthrough_pixels"],
                "passthrough_color_count": v["passthrough_color_count"],
            }
            for v in report_data["variants"]
        ],
        "report": str(resolved_out / report),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def add_palette_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base", required=True, type=Path, help="base sheet PNG to scan")
    p.add_argument("--out", type=Path, help="write the palette map JSON here (default: stdout)")
    p.add_argument("--alpha-threshold", type=int, default=DEFAULT_ALPHA_THRESHOLD)
    p.add_argument("--max-colors", type=int, help="keep only the top-N colours by frequency")


def run_palette(
    *,
    base: Path,
    out: Path | None = None,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
    max_colors: int | None = None,
) -> int:
    palette = extract_palette(base, alpha_threshold=alpha_threshold, max_colors=max_colors)
    text = json.dumps(palette, ensure_ascii=False, indent=2) + "\n"
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(out, text)
        print(json.dumps({"ok": True, "palette": str(out), "color_count": palette["color_count"]}, ensure_ascii=False, indent=2))
    else:
        print(text, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bake deterministic palette-swap variant sheets.")
    add_recolor_arguments(parser)
    args = parser.parse_args(argv)
    return run(**vars(args))


if __name__ == "__main__":
    raise SystemExit(main())
