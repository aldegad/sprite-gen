#!/usr/bin/env python3
"""Build a same-raw A/B proof for PR #6's subject-specific sparse floor."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)
EXTRACT = ROOT / "scripts" / "extract_sprite_row_frames.py"
COMPOSE = ROOT / "scripts" / "compose_sprite_atlas.py"
MAGENTA = (255, 0, 255)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(subject: str | None) -> dict[str, object]:
    doc: dict[str, object] = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": "pr6-proof", "description": "small spell shards", "base_image": None},
        "cell": {
            "shape": "square",
            "width": 64,
            "height": 64,
            "size": 64,
            "safe_margin_x": 6,
            "safe_margin_y": 6,
            "safe_margin": 6,
        },
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255], "selection": "explicit"},
        "states": {"burst": {"frames": 3, "fps": 6, "loop": True}},
        "style": "same raw A/B proof",
        "layout": "taxonomy/v1",
    }
    if subject is not None:
        doc["subject"] = subject
    return doc


def _make_raw(path: Path, *, debris: bool) -> None:
    strip = Image.new("RGB", (64 * 3, 64), MAGENTA)
    draw = ImageDraw.Draw(strip)
    if debris:
        for frame in range(3):
            x = frame * 64 + 30
            draw.rectangle((x, 30, x + 3, 33), fill=(88, 226, 255))
    else:
        colors = ((94, 228, 255), (135, 112, 255), (73, 255, 187))
        for frame, color in enumerate(colors):
            ox = frame * 64
            draw.rectangle((ox + 29, 29, ox + 34, 34), fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    strip.save(path)


def _run_case(root: Path, name: str, raw: Path, subject: str | None) -> dict[str, object]:
    run_dir = root / name
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    (run_dir / "sprite-request.json").write_text(
        json.dumps(_request(subject), indent=2) + "\n", encoding="utf-8"
    )
    shutil.copy2(raw, run_dir / "raw" / "burst.png")
    extract = subprocess.run(
        [str(PYTHON), str(EXTRACT), "--run-dir", str(run_dir)],
        text=True,
        capture_output=True,
        check=False,
    )
    manifest_path = run_dir / "frames" / "frames-manifest.json"
    failure_path = run_dir / "extract-failure.json"
    result_path = manifest_path if manifest_path.is_file() else failure_path
    result = json.loads(result_path.read_text(encoding="utf-8"))
    records = []
    for row in result.get("rows", []):
        records.extend(row.get("frame_records", []))
    if not records:
        for state in result.get("states", {}).values():
            records.extend(state.get("frame_records", []))
    counts = [int(record["nontransparent_pixels"]) for record in records]
    compose_rc = None
    if extract.returncode == 0:
        compose = subprocess.run(
            [str(PYTHON), str(COMPOSE), "--run-dir", str(run_dir)],
            text=True,
            capture_output=True,
            check=False,
        )
        compose_rc = compose.returncode
    return {
        "name": name,
        "subject": subject or "character (field absent)",
        "raw_sha256": _sha256(run_dir / "raw" / "burst.png"),
        "extract_rc": extract.returncode,
        "compose_rc": compose_rc,
        "ok": bool(result.get("ok")),
        "opaque_pixels": counts,
        "errors": result.get("errors", []),
        "run_dir": str(run_dir),
    }


def _font(size: int) -> ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _panel(raw: Image.Image, title: str, verdict: str, details: list[str], color: tuple[int, int, int]) -> Image.Image:
    panel = Image.new("RGB", (640, 420), (250, 250, 248))
    draw = ImageDraw.Draw(panel)
    draw.rounded_rectangle((20, 20, 620, 400), radius=18, fill=(255, 255, 255), outline=(215, 217, 220), width=2)
    draw.text((46, 44), title, font=_font(25), fill=(28, 31, 36))
    draw.rounded_rectangle((46, 88, 594, 150), radius=12, fill=tuple(min(255, c + 205) for c in color), outline=color, width=2)
    draw.text((66, 103), verdict, font=_font(22), fill=color)
    preview = raw.resize((384, 128), Image.Resampling.NEAREST)
    panel.paste(preview, (128, 174))
    for index, line in enumerate(details):
        draw.text((46, 318 + index * 27), line, font=_font(17), fill=(70, 74, 82))
    return panel


def _render_compare(out: Path, raw: Path, character: dict[str, object], effect: dict[str, object], debris: dict[str, object]) -> None:
    canvas = Image.new("RGB", (1320, 1030), (244, 245, 247))
    draw = ImageDraw.Draw(canvas)
    draw.text((42, 34), "PR #6 same-raw A/B: subject profile changes validation, not the artwork", font=_font(31), fill=(25, 28, 33))
    draw.text((42, 80), "Both upper panels use byte-identical raw/burst.png. Only sprite-request.json subject differs.", font=_font(20), fill=(83, 88, 98))
    raw_img = Image.open(raw).convert("RGB")
    left = _panel(
        raw_img,
        "Resolution formula: character floor = 64 px/frame",
        "REJECTED as too sparse",
        [f"opaque pixels: {character['opaque_pixels']}", "raw sha256: identical"],
        (165, 47, 47),
    )
    right = _panel(
        raw_img,
        "Resolution formula: effect floor = 32 px/frame",
        "ACCEPTED and atlas composed",
        [f"opaque pixels: {effect['opaque_pixels']}", "raw sha256: identical"],
        (35, 122, 72),
    )
    canvas.paste(left, (20, 120))
    canvas.paste(right, (660, 120))
    debris_img = Image.open(Path(debris["run_dir"]) / "raw" / "burst.png").convert("RGB")
    guard = _panel(
        debris_img,
        "Debris control: effect profile still keeps the guard",
        "REJECTED below 32 px/frame",
        [f"opaque pixels: {debris['opaque_pixels']}", "the guard is scoped, not disabled"],
        (151, 96, 24),
    )
    canvas.paste(guard, (340, 560))
    draw.text((42, 997), "Conclusion: #6 rescues legitimate small effects during extract/compose. It does not improve image-generation beauty or change curation UI.", font=_font(18), fill=(45, 49, 56))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    out_dir = args.out_dir.expanduser().resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    legitimate = out_dir / "inputs" / "legitimate-small-effect.png"
    debris_raw = out_dir / "inputs" / "debris-control.png"
    _make_raw(legitimate, debris=False)
    _make_raw(debris_raw, debris=True)
    runs = out_dir / "runs"
    character = _run_case(runs, "character", legitimate, None)
    effect = _run_case(runs, "effect", legitimate, "effect")
    debris = _run_case(runs, "effect-debris", debris_raw, "effect")
    report = {"character": character, "effect": effect, "effect_debris": debris}
    same_raw = character["raw_sha256"] == effect["raw_sha256"]
    report["assertions"] = {
        "same_raw_sha256": same_raw,
        "character_rejects": character["extract_rc"] != 0,
        "effect_accepts": effect["extract_rc"] == 0 and effect["compose_rc"] == 0,
        "effect_still_rejects_debris": debris["extract_rc"] != 0,
    }
    if not all(report["assertions"].values()):
        print(json.dumps(report, indent=2), file=sys.stderr)
        return 1
    (out_dir / "pr6-validation-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _render_compare(out_dir / "pr6-subject-profile-ab.png", legitimate, character, effect, debris)
    print(out_dir / "pr6-subject-profile-ab.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
