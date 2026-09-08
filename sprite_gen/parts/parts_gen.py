# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen parts gen` — generate every catalog part alone, in parallel.

Each job sends the provider the whole base image plus a padded crop of the
part's box as references, and asks for that part only on a flat chroma
background. The chroma path (`gen.generate_image(transparent=True)`) keys it
to RGBA; a result with no transparent pixels is a failure, never a layer.

Outputs, under `--out-dir`:
    <part>.png / <part>__<variant>.png      RGBA part candidates
    parts-gen.report.json                   one record per job (ok / error)

One failing job does not stop the others (they are independent provider
calls), but the command exits non-zero if any job failed, and the report names
every failure — No Silent Fallback.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np

from sprite_gen.gen import generate_image
from sprite_gen.parts.catalog import job_name, jobs, load_catalog

CHROMA_HEX = {"green": "#00FF00", "magenta": "#FF00FF"}
DEFAULT_WORKERS = 6
CROP_PAD = 0.25  # fraction of the box added on each side of the reference crop

PART_PROMPT = (
    "Reference 1 is the full character; reference 2 is a close crop of the region to draw. "
    "Draw ONLY this part of that exact character: {prompt} "
    "Reproduce it pixel-faithfully from the references: identical shape, position within the crop, "
    "line weight, colours and shading, same anime illustration style, same scale and viewing angle. "
    "Draw nothing else — no other body parts, no clothing, no background elements. "
    "Everything outside the part must be a perfectly flat solid {chroma_name} {chroma_hex} chroma key: "
    "no gradient, no shadow, no checkerboard. No text, no watermark."
)


def crop_reference(base: Image.Image, bbox: list[int], pad: float = CROP_PAD) -> Image.Image:
    x, y, w, h = bbox
    px, py = int(round(w * pad)), int(round(h * pad))
    box = (max(0, x - px), max(0, y - py), min(base.width, x + w + px), min(base.height, y + h + py))
    return base.crop(box)


def flatten_on_chroma(image: Image.Image, hex_color: str) -> Image.Image:
    """The provider sees a chroma-flat reference, so the crop's transparency reads as the key colour."""
    rgb = tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    out = Image.new("RGBA", image.size, rgb + (255,))
    out.alpha_composite(image.convert("RGBA"))
    return out.convert("RGB")


def despill(path: Path, chroma: str) -> int:
    """Remove the key hue that survives keying along antialiased edges: a pixel whose key
    channel exceeds the other two is clamped to their max. Returns the pixel count touched.
    Safe for these subjects because a green (or magenta) cast is never native art here."""
    image = Image.open(path).convert("RGBA")
    data = np.asarray(image, dtype=np.int16).copy()
    r, g, b, a = data[..., 0], data[..., 1], data[..., 2], data[..., 3]
    if chroma == "green":
        limit = np.maximum(r, b)
        mask = (a > 0) & (g > limit + 6)
        data[..., 1] = np.where(mask, limit, g)
    else:  # magenta: R and B high, G low
        limit = g
        mask = (a > 0) & (np.minimum(r, b) > limit + 6)
        data[..., 0] = np.where(mask, np.minimum(r, limit + (r - limit) // 2), r)
        data[..., 2] = np.where(mask, np.minimum(b, limit + (b - limit) // 2), b)
    touched = int(mask.sum())
    if touched:
        Image.fromarray(data.astype("uint8"), "RGBA").save(path)
    return touched


def alpha_stats(path: Path) -> dict[str, float]:
    image = Image.open(path)
    if image.mode != "RGBA":
        return {"alpha_zero_pct": 0.0, "opaque_pct": 0.0, "mode": image.mode}
    hist = image.getchannel("A").histogram()
    total = image.width * image.height
    return {"alpha_zero_pct": round(100.0 * hist[0] / total, 2),
            "opaque_pct": round(100.0 * sum(hist[250:]) / total, 2), "mode": "RGBA"}


def _one(job: dict[str, Any], *, provider: str, base_path: Path, base: Image.Image, chroma: str,
         out_dir: Path, workdir: Path) -> dict[str, Any]:
    name = job_name(job["part"], job["variant"])
    record: dict[str, Any] = {"job": name, "part": job["part"], "variant": job["variant"], "bbox": job["bbox"]}
    try:
        ref_dir = workdir / name
        ref_dir.mkdir(parents=True, exist_ok=True)
        crop_path = ref_dir / "crop.png"
        flatten_on_chroma(crop_reference(base, job["bbox"]), CHROMA_HEX[chroma]).save(crop_path)
        base_ref = ref_dir / "base.png"
        flatten_on_chroma(base, CHROMA_HEX[chroma]).save(base_ref)
        prompt = PART_PROMPT.format(prompt=job["prompt"], chroma_name=chroma, chroma_hex=CHROMA_HEX[chroma])
        out = out_dir / f"{name}.png"
        result = generate_image(provider, prompt, out, refs=[base_ref, crop_path], transparent=True,
                                chroma_key=chroma, workdir=ref_dir / "gen")
        despilled = despill(out, chroma)
        stats = alpha_stats(out)
        record.update({"ok": True, "out": str(out), "raw": str(result.raw), "elapsed_seconds": result.elapsed_seconds,
                       "provider": result.provider, "alpha": stats, "despilled_pixels": despilled})
        if stats["mode"] != "RGBA" or stats["alpha_zero_pct"] <= 0.0:
            record.update({"ok": False, "error": "generated part has no transparent pixels after keying"})
    except SystemExit as exc:  # generate_image fails loud with SystemExit
        record.update({"ok": False, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001 — every job failure must land in the report
        record.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return record


def generate_parts(catalog_path: Path, out_dir: Path, *, provider: str = "codex", workers: int = DEFAULT_WORKERS,
                   only: list[str] | None = None, workdir: Path | None = None) -> dict[str, Any]:
    catalog = load_catalog(catalog_path)
    base_path = (catalog_path.parent / catalog["base"]).resolve()
    if not base_path.is_file():
        raise SystemExit(f"parts gen: base image not found: {base_path}")
    base = Image.open(base_path).convert("RGBA")
    if (base.width, base.height) != (catalog["canvas"]["width"], catalog["canvas"]["height"]):
        raise SystemExit(f"parts gen: base is {base.width}x{base.height} but canvas declares "
                         f"{catalog['canvas']['width']}x{catalog['canvas']['height']}")
    chroma = catalog.get("chroma_key", "green")
    todo = jobs(catalog)
    if only:
        wanted = set(only)
        todo = [job for job in todo if job["part"] in wanted or job_name(job["part"], job["variant"]) in wanted]
        if not todo:
            raise SystemExit(f"parts gen: --only matched no catalog job: {sorted(wanted)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = workdir or (out_dir / ".work")
    workdir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        records = list(pool.map(lambda job: _one(job, provider=provider, base_path=base_path, base=base, chroma=chroma,
                                                 out_dir=out_dir, workdir=workdir), todo))
    report = {"kind": "sprite-gen-parts-gen-report", "version": 1, "catalog": str(catalog_path.resolve()),
              "base": str(base_path), "provider": provider, "chroma_key": chroma, "jobs": records,
              "ok": all(r["ok"] for r in records), "failed": [r["job"] for r in records if not r["ok"]]}
    (out_dir / "parts-gen.report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--catalog", required=True, type=Path, help="parts catalog JSON (base path is relative to it)")
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--provider", default="codex", choices=["codex", "grok"])
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    p.add_argument("--only", default=None, help="comma-separated part ids or part__variant names")


def run(*, catalog: Path, out_dir: Path, provider: str = "codex", workers: int = DEFAULT_WORKERS,
        only: str | None = None) -> int:
    report = generate_parts(Path(catalog), Path(out_dir), provider=provider, workers=workers,
                            only=[s.strip() for s in only.split(",") if s.strip()] if only else None)
    print(json.dumps({k: report[k] for k in ("ok", "failed", "provider")}, ensure_ascii=False))
    for record in report["jobs"]:
        if not record["ok"]:
            print(f"[parts gen] FAILED {record['job']}: {record['error']}", file=sys.stderr)
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate every catalog part alone, in parallel.")
    add_arguments(parser)
    args = parser.parse_args(argv)
    return run(catalog=args.catalog, out_dir=args.out_dir, provider=args.provider, workers=args.workers, only=args.only)


if __name__ == "__main__":
    raise SystemExit(main())
