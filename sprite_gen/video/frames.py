# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-frames` — turn a generated clip into keyed RGBA frames.

ffmpeg extracts every frame (the clip's own fps is recorded, not assumed), then
each frame goes through the same `cutout` engine imported stills use (chroma
green/magenta -> the extract matte; `auto` reads the corners). The report carries
per-frame alpha coverage and an **edge-contact check**: a frame whose subject
touches the top/left/right edge means the model framed too tight (a jump whose
hair left the frame, 2026-09-08) and the run fails loud — the fix is a taller or
wider canvas (`video-canvas`), not a quiet crop.

An opaque edge pixel is not always the subject. When the model painted the key
a little off and part of that background survives the matte, the edge band is
"touched" by leftover *background* (2026-09-11: a (8, 162, 24) green read as
"framed too tight"). The check therefore classifies every contact pixel by its
raw colour — the declared key's hue signature (`extract.is_border_key_candidate`,
the border rule: an edge pixel is border evidence) is **residual background**,
anything else is the **subject** — and the two fail
with different messages: residual points at `video-canvas` normalization of the
base still, subject contact at a taller/wider canvas.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.frames.cutout import cutout
from sprite_gen.frames.extract import is_border_key_candidate
from sprite_gen.spec.runio import atomic_write_text

EDGE_ROWS = 4  # rows/cols inspected at each edge
EDGE_MAX_PIXELS = 0  # any opaque pixel on the top/left/right edge band = contact


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise SystemExit(f"video-frames: `{binary}` not found on PATH — install ffmpeg (brew install ffmpeg)")
    return path


def probe(clip: Path) -> dict[str, Any]:
    ffprobe = _require("ffprobe")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
         "-show_entries", "format=duration", "-of", "json", str(clip)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"video-frames: ffprobe failed on {clip}: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout)
    stream = (data.get("streams") or [{}])[0]
    num, den = (stream.get("r_frame_rate") or "24/1").split("/")
    fps = float(num) / float(den or 1)
    return {"width": stream.get("width"), "height": stream.get("height"), "fps": fps,
            "nb_frames": int(stream["nb_frames"]) if str(stream.get("nb_frames", "")).isdigit() else None,
            "duration": float(data.get("format", {}).get("duration") or 0)}


def extract(clip: Path, raw_dir: Path) -> list[Path]:
    ffmpeg = _require("ffmpeg")
    raw_dir.mkdir(parents=True, exist_ok=True)
    for old in raw_dir.glob("frame-*.png"):
        old.unlink()
    proc = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(clip), str(raw_dir / "frame-%04d.png")], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"video-frames: ffmpeg failed on {clip}: {proc.stderr.strip()[:300]}")
    files = sorted(raw_dir.glob("frame-*.png"))
    if not files:
        raise SystemExit(f"video-frames: ffmpeg wrote no frames for {clip}")
    return files


def edge_contact(image: Image.Image) -> dict[str, int]:
    """Opaque pixels in the top/left/right edge bands (the floor edge is expected contact)."""
    alpha = image.convert("RGBA").getchannel("A")
    w, h = alpha.size
    px = alpha.load()
    top = sum(1 for y in range(min(EDGE_ROWS, h)) for x in range(w) if px[x, y] > 0)
    left = sum(1 for x in range(min(EDGE_ROWS, w)) for y in range(h) if px[x, y] > 0)
    right = sum(1 for x in range(max(0, w - EDGE_ROWS), w) for y in range(h) if px[x, y] > 0)
    return {"top": top, "left": left, "right": right}


def classify_edge_contact(raw: Image.Image, keyed: Image.Image, chroma_key: tuple[int, int, int] | None) -> dict[str, int]:
    """Split the opaque edge-band pixels into `subject` and `residual` (leftover key background).

    A contact pixel whose *raw* colour carries the declared key's hue signature
    (`is_border_key_candidate`) is background the matte failed to erase, not the
    subject. Without a chroma key
    (matte route) every contact is the subject.
    """
    alpha = keyed.convert("RGBA").getchannel("A").load()
    rgb = raw.convert("RGB").load()
    w, h = keyed.size
    band = set()
    band.update((x, y) for y in range(min(EDGE_ROWS, h)) for x in range(w))
    band.update((x, y) for x in range(min(EDGE_ROWS, w)) for y in range(h))
    band.update((x, y) for x in range(max(0, w - EDGE_ROWS), w) for y in range(h))
    subject = residual = 0
    for x, y in band:
        if alpha[x, y] <= 0:
            continue
        if chroma_key is not None and is_border_key_candidate(rgb[x, y], chroma_key):
            residual += 1
        else:
            subject += 1
    return {"subject": subject, "residual": residual}


def key_frames(
    raw_files: list[Path],
    keyed_dir: Path,
    *,
    key: str = "auto",
    check_edges: bool = True,
) -> dict[str, Any]:
    keyed_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    contacts: list[dict[str, Any]] = []
    for src in raw_files:
        dst = keyed_dir / src.name
        stats = cutout(src, dst, key=key)
        image = Image.open(dst).convert("RGBA")
        hist = image.getchannel("A").histogram()
        w, h = image.size
        alpha_zero_pct = round(100 * hist[0] / (w * h), 2)
        row = {"frame": src.name, "alpha_zero_pct": alpha_zero_pct, "route": stats.get("route")}
        if check_edges:
            contact = edge_contact(image)
            if any(v > EDGE_MAX_PIXELS for v in contact.values()):
                chroma = stats.get("chroma_key")
                contact.update(classify_edge_contact(Image.open(src), image, tuple(chroma) if chroma else None))
                contact["key_painted"] = stats.get("chroma_key_painted")
                contacts.append({"frame": src.name, **contact})
            row["edge"] = contact
        if hist[0] == 0:
            raise SystemExit(f"video-frames: {src.name} keyed to 0% transparency — the background is not the chroma key")
        rows.append(row)
    report = {
        "frames": len(rows),
        "alpha_zero_pct_min": min(r["alpha_zero_pct"] for r in rows),
        "alpha_zero_pct_max": max(r["alpha_zero_pct"] for r in rows),
        "edge_contacts": contacts,
        "rows": rows,
    }
    if contacts and check_edges:
        raise SystemExit(_edge_contact_message(contacts))
    return report


def _edge_contact_message(contacts: list[dict[str, Any]]) -> str:
    """Residual background and a clipped subject are different defects with different fixes."""
    worst = max(contacts, key=lambda c: c["top"] + c["left"] + c["right"])
    where = f"(worst {worst['frame']}: top {worst['top']} / left {worst['left']} / right {worst['right']} px)"
    subject_frames = [c for c in contacts if c["subject"] > 0]
    residual_frames = [c for c in contacts if c["residual"] > 0]
    if not subject_frames:
        painted = tuple(worst["key_painted"]) if worst.get("key_painted") else None
        return (
            f"video-frames: leftover chroma background reaches the frame edge in {len(contacts)} frame(s) {where} — "
            f"the key survived the matte (painted background {painted}); this is background, not the subject: "
            "normalize the base still with `sprite-gen video-canvas` (repaints the flat background to the exact key) "
            "and regenerate the clip"
        )
    message = (
        f"video-frames: the subject touches the frame edge in {len(subject_frames)} frame(s) {where} — "
        "the clip was framed too tight; regenerate from a taller/wider canvas (`sprite-gen video-canvas`) "
        "or pass --allow-edge-contact to accept the clipping"
    )
    if residual_frames:
        message += f"; {len(residual_frames)} frame(s) also carry leftover chroma background at the edge (`residual` in the report)"
    return message


def run_frames(clip: Path, out_dir: Path, *, key: str, allow_edge_contact: bool, report_path: Path | None) -> dict[str, Any]:
    clip = clip.expanduser().resolve()
    if not clip.is_file():
        raise SystemExit(f"video-frames: clip not found: {clip}")
    out_dir = out_dir.expanduser().resolve()
    meta = probe(clip)
    raw_dir = out_dir / "raw"
    keyed_dir = out_dir / "keyed"
    files = extract(clip, raw_dir)
    report = key_frames(files, keyed_dir, key=key, check_edges=not allow_edge_contact)
    payload = {"kind": "sprite-gen-video-frames-report", "clip": str(clip), "out_dir": str(out_dir), "raw_dir": str(raw_dir), "keyed_dir": str(keyed_dir), "key": key, **meta, **report}
    target = (report_path or (out_dir / "frames.report.json")).expanduser().resolve()
    atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    payload["report"] = str(target)
    return payload


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--clip", required=True, type=Path, help="mp4 from `sprite-gen video`")
    parser.add_argument("--out-dir", required=True, type=Path, help="writes raw/ and keyed/ here")
    parser.add_argument("--key", choices=("auto", "green", "magenta", "white"), default="auto", help="background key (auto reads the corners)")
    parser.add_argument("--allow-edge-contact", action="store_true", help="accept frames whose subject touches the top/left/right edge")
    parser.add_argument("--report", type=Path, help="report JSON (default <out-dir>/frames.report.json)")


def run(**kwargs: object) -> int:
    payload = run_frames(Path(str(kwargs["clip"])), Path(str(kwargs["out_dir"])), key=str(kwargs.get("key") or "auto"),
                         allow_edge_contact=bool(kwargs.get("allow_edge_contact")), report_path=kwargs.get("report"))  # type: ignore[arg-type]
    summary = {k: payload[k] for k in ("clip", "keyed_dir", "fps", "frames", "alpha_zero_pct_min", "alpha_zero_pct_max", "edge_contacts", "report")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-frames", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
