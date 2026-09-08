# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen parts match` — register generated parts onto the base by pixels.

For every catalog part (top-most draw order first) the candidate image is
trimmed to its own alpha box, then searched over a small scale × integer
offset grid around the catalog bbox for the placement whose alpha-weighted
RGB difference against the base is smallest. Pixels already claimed by a
higher part (its placed alpha) are excluded from a lower part's score, so a
face is compared only where the bangs do not cover it.

The gate: a part passes when its best normalized difference is at or below its
tolerance; the run passes when every default variant passes AND the full
z-ordered composite of the placed defaults reproduces the base within the
catalog's composite tolerance. Every failure is reported by part with its best
score and placement — nothing is skipped or approximated silently.

Outputs, under `--out-dir` (the gen output dir by default):
    placed/<job>.png              part re-sampled to canvas size at its placement
    composite.png                 defaults stacked in z order (for the eye)
    parts-match.report.json       per-part placement, score, pass/fail; composite score
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.parts.catalog import DEFAULT_AGREE_FLOOR, DEFAULT_VARIANT, job_name, jobs, load_catalog, parts_by_z

SCALE_STEPS = (0.6, 0.7, 0.8, 0.88, 0.94, 1.0, 1.06)
COMPOSITE_TOLERANCE = 0.05
DEFAULT_OFFSET_REACH = 0.08  # fraction of the bbox size searched around the catalog placement
COARSE_STEP = 4


def trim_to_alpha(image: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Crop to the alpha bounding box; returns (cropped, box) or raises on fully transparent input."""
    rgba = image.convert("RGBA")
    box = rgba.getchannel("A").getbbox()
    if box is None:
        raise ValueError("part image is fully transparent")
    return rgba.crop(box), box


def contain(part_size: tuple[int, int], box: tuple[int, int]) -> tuple[int, int]:
    """Largest size with the part's own aspect ratio that fits inside `box` (never stretched)."""
    pw, ph = part_size
    bw, bh = box
    k = min(bw / max(pw, 1), bh / max(ph, 1))
    return max(1, int(round(pw * k))), max(1, int(round(ph * k)))


def _place(part: Image.Image, canvas: tuple[int, int], x: int, y: int, w: int, h: int) -> np.ndarray:
    resampled = part.resize((max(1, w), max(1, h)), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    layer.paste(resampled, (x, y), resampled)
    return np.asarray(layer, dtype=np.float32)


AGREE_DIFF = 0.12      # per-pixel mean RGB distance (0..1) under which a pixel "agrees" with the base


def score_placement(layer: np.ndarray, base: np.ndarray, free: np.ndarray, area: float) -> tuple[float, float, float]:
    """(objective, colour, agree) for one placement.

    objective (minimize) = -(agreeing pixels - 2 x disagreeing pixels) / bbox area — a shrunken
    part covers fewer agreeing pixels and an oversized one pays for every pixel it spills onto
    something else, so the optimum is the placement that reproduces the most base pixels.
    colour = alpha-weighted mean RGB distance over the part's visible, unclaimed pixels (the gate).
    agree = fraction of those pixels within AGREE_DIFF of the base.
    """
    alpha = (layer[..., 3] / 255.0) * free
    weight = float(alpha.sum())
    if weight <= 0:
        return 2.0, 1.0, 0.0
    diff = np.abs(layer[..., :3] - base[..., :3]).mean(axis=-1) / 255.0
    colour = float((diff * alpha).sum() / weight)
    agreeing = float((alpha * (diff <= AGREE_DIFF)).sum())
    disagreeing = float(weight - agreeing)
    objective = -(agreeing - 2.0 * disagreeing) / max(area, 1.0)
    return float(objective), colour, float(agreeing / weight)


def expected_region(base: np.ndarray, free: np.ndarray, bbox: list[int]) -> np.ndarray:
    """Base pixels the part is expected to own: visible base alpha inside its bbox, not yet claimed."""
    region = np.zeros(base.shape[:2], dtype=np.float32)
    x, y, w, h = bbox
    region[y:y + h, x:x + w] = (base[y:y + h, x:x + w, 3] / 255.0) * free[y:y + h, x:x + w]
    return region


def _offsets(reach: int, step: int) -> list[int]:
    """Symmetric coarse grid that always contains 0 (the catalog placement itself)."""
    return sorted({0, *range(step, reach + 1, step), *range(-step, -reach - 1, -step), reach, -reach})


def _pyramid_factor(bbox: list[int], target: int = 96) -> int:
    """Downscale factor for the coarse pass so the part is at most ~target px on its long side."""
    return max(1, int(math.ceil(max(bbox[2], bbox[3]) / target)))


def _downscale(arr: np.ndarray, factor: int) -> np.ndarray:
    if factor == 1:
        return arr
    h, w = arr.shape[:2]
    h2, w2 = h // factor, w // factor
    trimmed = arr[:h2 * factor, :w2 * factor]
    if arr.ndim == 3:
        return trimmed.reshape(h2, factor, w2, factor, arr.shape[2]).mean(axis=(1, 3))
    return trimmed.reshape(h2, factor, w2, factor).mean(axis=(1, 3))


def register(part: Image.Image, base: np.ndarray, free: np.ndarray, bbox: list[int],
             *, reach: float = DEFAULT_OFFSET_REACH) -> dict[str, Any]:
    """Coarse-to-fine search of scale × integer offset around the catalog bbox. Deterministic.

    Coarse pass on a downscaled pyramid level (part and base window both reduced by the
    same integer factor, so a 900 px hair mass is searched at ~96 px), then a fine pass at
    full resolution around the coarse winner. Each candidate size is resampled once.
    """
    trimmed, _ = trim_to_alpha(part)
    bx, by, bw, bh = bbox
    H, W = base.shape[:2]
    rx, ry = max(2, int(round(bw * reach))), max(2, int(round(bh * reach)))
    grow = int(math.ceil(max(bw, bh) * (max(SCALE_STEPS) - 1.0))) + COARSE_STEP
    wx0, wy0 = max(0, bx - rx - grow), max(0, by - ry - grow)
    wx1, wy1 = min(W, bx + bw + rx + grow), min(H, by + bh + ry + grow)
    base_w, free_w = base[wy0:wy1, wx0:wx1], free[wy0:wy1, wx0:wx1]
    window = (wx1 - wx0, wy1 - wy0)
    area = float(bw * bh)
    f = _pyramid_factor(bbox)
    base_c, free_c = _downscale(base_w, f), _downscale(free_w, f)
    window_c = (base_c.shape[1], base_c.shape[0])

    def place_full(x: int, y: int, resampled: Image.Image) -> np.ndarray:
        layer = Image.new("RGBA", window, (0, 0, 0, 0))
        layer.paste(resampled, (x - wx0, y - wy0), resampled)
        return np.asarray(layer, dtype=np.float32)

    def place_coarse(x: int, y: int, resampled_c: Image.Image) -> np.ndarray:
        layer = Image.new("RGBA", window_c, (0, 0, 0, 0))
        layer.paste(resampled_c, ((x - wx0) // f, (y - wy0) // f), resampled_c)
        return np.asarray(layer, dtype=np.float32)

    per_scale: list[dict[str, Any]] = []
    for scale in SCALE_STEPS:
        w, h = contain(trimmed.size, (max(1, int(round(bw * scale))), max(1, int(round(bh * scale)))))
        cx, cy = bx + (bw - w) // 2, by + (bh - h) // 2
        resampled = trimmed.resize((w, h), Image.Resampling.LANCZOS)
        resampled_c = resampled.resize((max(1, w // f), max(1, h // f)), Image.Resampling.BOX) if f > 1 else resampled
        step = max(COARSE_STEP, f)
        best: dict[str, Any] | None = None
        for dy in _offsets(ry, step):
            for dx in _offsets(rx, step):
                obj, colour, agree = score_placement(place_coarse(cx + dx, cy + dy, resampled_c), base_c, free_c, area / (f * f))
                if best is None or obj < best["objective"]:
                    best = {"objective": obj, "score": colour, "agree": agree, "scale": scale,
                            "x": cx + dx, "y": cy + dy, "w": w, "h": h}
        assert best is not None
        # fine pass at full resolution around the coarse winner
        fine = max(step, 2)
        fx, fy = best["x"], best["y"]
        best["objective"] = None  # re-scored at full resolution below
        for dy in range(-fine, fine + 1):
            for dx in range(-fine, fine + 1):
                obj, colour, agree = score_placement(place_full(fx + dx, fy + dy, resampled), base_w, free_w, area)
                if best["objective"] is None or obj < best["objective"]:
                    best = {**best, "objective": obj, "score": colour, "agree": agree, "x": fx + dx, "y": fy + dy}
        per_scale.append(best)
    winner = min(per_scale, key=lambda b: (b["objective"], abs(b["scale"] - 1.0)))
    winner["score"] = round(winner["score"], 4)
    winner["agree"] = round(winner["agree"], 4)
    winner["objective"] = round(winner["objective"], 4)
    return winner


def match_parts(catalog_path: Path, parts_dir: Path, out_dir: Path | None = None,
                *, composite_tolerance: float = COMPOSITE_TOLERANCE) -> dict[str, Any]:
    catalog = load_catalog(catalog_path)
    base_img = Image.open((catalog_path.parent / catalog["base"]).resolve()).convert("RGBA")
    base = np.asarray(base_img, dtype=np.float32)
    canvas = (base_img.width, base_img.height)
    out_dir = out_dir or parts_dir
    placed_dir = out_dir / "placed"
    placed_dir.mkdir(parents=True, exist_ok=True)
    free = np.ones(base.shape[:2], dtype=np.float32)
    records: list[dict[str, Any]] = []
    placements: dict[str, dict[str, Any]] = {}
    # top-most first so a lower part is scored only where it is actually visible
    for part in reversed(parts_by_z(catalog)):
        name = part["id"]
        candidate = parts_dir / f"{name}.png"
        record: dict[str, Any] = {"job": name, "part": name, "variant": DEFAULT_VARIANT, "z": part["z"]}
        if not candidate.is_file():
            record.update({"ok": False, "error": "missing candidate"})
            records.append(record)
            continue
        try:
            best = register(Image.open(candidate), base, free, part["bbox"])
        except ValueError as exc:
            record.update({"ok": False, "error": str(exc)})
            records.append(record)
            continue
        tolerance = float(part.get("tolerance", 0.06))
        floor = float(part.get("agree_floor", DEFAULT_AGREE_FLOOR))
        ok = bool(best["score"] <= tolerance and best["agree"] >= floor)
        record.update({"ok": ok, "placement": best, "tolerance": tolerance, "agree_floor": floor})
        if not ok:
            record["error"] = (f"best score {best['score']} exceeds tolerance {tolerance}" if best["score"] > tolerance
                               else f"only {best['agree']:.0%} of the part agrees with the base (floor {floor:.0%})")
        records.append(record)
        placements[name] = best
        trimmed, _ = trim_to_alpha(Image.open(candidate))
        layer = _place(trimmed, canvas, best["x"], best["y"], best["w"], best["h"])
        Image.fromarray(layer.astype("uint8"), "RGBA").save(placed_dir / f"{name}.png")
        free = free * (1.0 - layer[..., 3] / 255.0)
    # variants inherit their default's placement (same box, same scale)
    for job in jobs(catalog):
        if job["variant"] == DEFAULT_VARIANT:
            continue
        name = job_name(job["part"], job["variant"])
        candidate = parts_dir / f"{name}.png"
        record = {"job": name, "part": job["part"], "variant": job["variant"], "z": job["z"]}
        placement = placements.get(job["part"])
        if not candidate.is_file():
            record.update({"ok": False, "error": "missing candidate"})
        elif placement is None:
            record.update({"ok": False, "error": "default variant did not place"})
        else:
            trimmed, _ = trim_to_alpha(Image.open(candidate))
            layer = _place(trimmed, canvas, placement["x"], placement["y"], placement["w"], placement["h"])
            Image.fromarray(layer.astype("uint8"), "RGBA").save(placed_dir / f"{name}.png")
            record.update({"ok": True, "placement": dict(placement), "inherited_from": job["part"]})
        records.append(record)
    # full composite of the defaults, bottom first
    composite = Image.new("RGBA", canvas, (0, 0, 0, 0))
    for part in parts_by_z(catalog):
        layer_path = placed_dir / f"{part['id']}.png"
        if layer_path.is_file():
            composite.alpha_composite(Image.open(layer_path).convert("RGBA"))
    composite.save(out_dir / "composite.png")
    comp = np.asarray(composite, dtype=np.float32)
    cover = np.minimum(comp[..., 3], base[..., 3]) / 255.0
    diff = np.abs(comp[..., :3] - base[..., :3]).mean(axis=-1) / 255.0
    composite_score = round(float((diff * cover).sum() / max(cover.sum(), 1.0)), 4)
    base_alpha = base[..., 3] / 255.0
    coverage = round(float((np.minimum(comp[..., 3] / 255.0, base_alpha)).sum() / max(base_alpha.sum(), 1.0)), 4)
    composite_ok = bool(composite_score <= composite_tolerance and coverage >= 0.97)
    report = {"kind": "sprite-gen-parts-match-report", "version": 1, "catalog": str(catalog_path.resolve()),
              "parts_dir": str(parts_dir.resolve()), "parts": records,
              "composite": {"score": composite_score, "coverage": coverage, "tolerance": composite_tolerance,
                            "ok": composite_ok, "path": str(out_dir / "composite.png")},
              "ok": composite_ok and all(r["ok"] for r in records),
              "failed": [r["job"] for r in records if not r["ok"]] + ([] if composite_ok else ["composite"])}
    (out_dir / "parts-match.report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--catalog", required=True, type=Path)
    p.add_argument("--parts-dir", required=True, type=Path, help="output dir of `parts gen`")
    p.add_argument("--out-dir", type=Path, default=None, help="default: --parts-dir")
    p.add_argument("--composite-tolerance", type=float, default=COMPOSITE_TOLERANCE)


def run(*, catalog: Path, parts_dir: Path, out_dir: Path | None = None,
        composite_tolerance: float = COMPOSITE_TOLERANCE) -> int:
    report = match_parts(Path(catalog), Path(parts_dir), Path(out_dir) if out_dir else None,
                         composite_tolerance=composite_tolerance)
    print(json.dumps({"ok": report["ok"], "failed": report["failed"], "composite": report["composite"]}, ensure_ascii=False))
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register generated parts onto the base by pixels.")
    add_arguments(parser)
    args = parser.parse_args(argv)
    return run(catalog=args.catalog, parts_dir=args.parts_dir, out_dir=args.out_dir,
               composite_tolerance=args.composite_tolerance)


if __name__ == "__main__":
    raise SystemExit(main())
