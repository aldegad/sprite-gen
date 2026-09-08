# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen parts rig` — rig.json + a seek-safe HTML runtime from matched parts.

Reads the catalog and the `parts match` report, and writes:
    rig.json        z-ordered parts, groups with pivots, variant files (canvas-sized PNGs)
    rig.html        an HTML fragment: one absolutely positioned <img> per part variant,
                    group wrappers with transform-origin at the group pivot
    rig-keys.js     GSAP keys for a paused timeline (`window.__rigKeys(tl, start)`)
    rig-keys.json   the same keys as data (mouth variant per frame, blinks, sway)

Every key is derived deterministically: mouth openness from the narration's
RMS envelope (ffmpeg → 16 kHz mono PCM → per-frame RMS, quantized against the
clip's own peak), blinks on a fixed cadence seeded by the clip length, head
sway as a slow sine. The same inputs always produce the same keys, which is
what a frame-stepping renderer (HyperFrames) requires.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from sprite_gen._deps import np
from sprite_gen.parts.catalog import DEFAULT_VARIANT, job_name, load_catalog, parts_by_z

MOUTH_LEVELS = ("closed", "half", "open")
MOUTH_THRESHOLDS = (0.12, 0.38, 0.70)  # fraction of the clip's peak RMS → closed | half | open | o
BLINK_PERIOD = 3.4
BLINK_CLOSE = 0.08
BLINK_HOLD = 0.06
SWAY_PERIOD = 5.2
SWAY_DEGREES = 1.2


def rms_envelope(audio: Path, fps: int, *, sample_rate: int = 16000) -> list[float]:
    """Per-frame RMS of the audio, normalized to the clip peak (0..1)."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("parts rig: ffmpeg is required to read the audio envelope")
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", str(sample_rate),
                          "-f", "s16le", "-"], check=True, capture_output=True).stdout
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    window = max(1, sample_rate // fps)
    frames = int(math.ceil(len(samples) / window))
    env = np.zeros(frames, dtype=np.float32)
    for i in range(frames):
        chunk = samples[i * window:(i + 1) * window]
        env[i] = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
    peak = float(env.max()) if frames else 0.0
    if peak <= 0:
        return [0.0] * frames
    # light smoothing so a single loud sample does not flap the mouth
    kernel = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    smoothed = np.convolve(env / peak, kernel, mode="same")
    return [round(float(v), 4) for v in smoothed]


def mouth_variant(level: float, available: set[str]) -> str:
    if level < MOUTH_THRESHOLDS[0] or "closed" not in available and DEFAULT_VARIANT in available:
        return "closed" if "closed" in available else DEFAULT_VARIANT
    if level < MOUTH_THRESHOLDS[1]:
        return "half" if "half" in available else ("open" if "open" in available else DEFAULT_VARIANT)
    if level < MOUTH_THRESHOLDS[2] or "o" not in available:
        return "open" if "open" in available else DEFAULT_VARIANT
    return "o"


def mouth_keys(envelope: list[float], fps: int, available: set[str], *, start: float = 0.0) -> list[dict[str, Any]]:
    """Variant changes only (run-length), as (t, variant)."""
    keys: list[dict[str, Any]] = []
    current = None
    for i, level in enumerate(envelope):
        variant = mouth_variant(level, available)
        if variant != current:
            keys.append({"t": round(start + i / fps, 4), "variant": variant})
            current = variant
    return keys


def blink_keys(duration: float, *, start: float = 0.0, seed: str = "") -> list[dict[str, Any]]:
    """Fixed-cadence blinks with a deterministic phase from the seed."""
    digest = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)
    phase = (digest % 1000) / 1000.0 * BLINK_PERIOD
    keys: list[dict[str, Any]] = []
    t = phase + 0.6
    while t + BLINK_CLOSE * 2 + BLINK_HOLD < duration:
        keys.append({"t": round(start + t, 4), "variant": "half"})
        keys.append({"t": round(start + t + BLINK_CLOSE, 4), "variant": "closed"})
        keys.append({"t": round(start + t + BLINK_CLOSE + BLINK_HOLD, 4), "variant": "half"})
        keys.append({"t": round(start + t + BLINK_CLOSE * 2 + BLINK_HOLD, 4), "variant": "open"})
        t += BLINK_PERIOD + ((digest >> 8) % 7) * 0.1
    return keys


def sway_keys(duration: float, *, start: float = 0.0, step: float = 0.5) -> list[dict[str, Any]]:
    keys: list[dict[str, Any]] = []
    t = 0.0
    while t <= duration:
        keys.append({"t": round(start + t, 4), "rotation": round(SWAY_DEGREES * math.sin(2 * math.pi * t / SWAY_PERIOD), 3)})
        t += step
    return keys


def build_rig(catalog_path: Path, match_dir: Path) -> dict[str, Any]:
    catalog = load_catalog(catalog_path)
    report_path = match_dir / "parts-match.report.json"
    if not report_path.is_file():
        raise SystemExit(f"parts rig: no match report at {report_path} — run `parts match` first")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("ok"):
        raise SystemExit(f"parts rig: match report is not passing (failed: {report.get('failed')}) — a rig from unmatched parts is not a rig")
    placed = {r["job"]: r for r in report["parts"] if r.get("ok")}
    parts_out: list[dict[str, Any]] = []
    for part in parts_by_z(catalog):
        variants: dict[str, str] = {}
        for vname in part.get("variants", {DEFAULT_VARIANT: ""}):
            name = job_name(part["id"], vname)
            if name not in placed:
                raise SystemExit(f"parts rig: {name} has no passing placement in the match report")
            variants[vname] = f"placed/{name}.png"
        parts_out.append({"id": part["id"], "z": part["z"], "group": part.get("group", "none"),
                          "pivot": list(part["pivot"]), "placement": placed[part["id"]]["placement"],
                          "variants": variants})
    groups = {name: {"pivot": list(g["pivot"]), "members": [p["id"] for p in parts_out if p["group"] == name]}
              for name, g in catalog.get("groups", {}).items()}
    return {"kind": "sprite-gen-parts-rig", "version": 1, "character": catalog.get("character"),
            "canvas": dict(catalog["canvas"]), "base": catalog["base"], "groups": groups, "parts": parts_out,
            "match_report": str(report_path.resolve())}


def render_html(rig: dict[str, Any], *, prefix: str = "rig", asset_prefix: str = "") -> str:
    w, h = rig["canvas"]["width"], rig["canvas"]["height"]
    lines = [f'<div id="{prefix}" class="rig" style="position:relative;width:{w}px;height:{h}px;overflow:hidden">']
    by_group: dict[str, list[dict[str, Any]]] = {}
    for part in rig["parts"]:
        by_group.setdefault(part["group"], []).append(part)
    order = ["none"] + [g for g in rig["groups"] if g != "none"]
    for gname in order:
        members = by_group.get(gname, [])
        if not members:
            continue
        if gname != "none":
            px, py = rig["groups"][gname]["pivot"]
            lines.append(f'<div id="{prefix}-g-{gname}" class="rig-group" style="position:absolute;inset:0;transform-origin:{px}px {py}px">')
        for part in members:
            for vname, rel in part["variants"].items():
                vid = f"{prefix}-{part['id']}" + ("" if vname == DEFAULT_VARIANT else f"__{vname}")
                hidden = "" if vname == DEFAULT_VARIANT else "opacity:0;"
                lines.append(f'  <img id="{vid}" class="rig-part" src="{asset_prefix}{rel}" '
                             f'style="position:absolute;left:0;top:0;width:{w}px;height:{h}px;{hidden}z-index:{part["z"]}">')
        if gname != "none":
            lines.append("</div>")
    lines.append("</div>")
    return "\n".join(lines) + "\n"


def render_keys_js(keys: dict[str, Any], *, prefix: str = "rig") -> str:
    """A function that stamps every key onto a paused GSAP timeline (opacity/rotation sets only)."""
    out = ["// generated by sprite-gen parts rig — deterministic; do not hand-edit",
           f"window.__rigKeys = function (tl, start) {{ start = start || 0; const P = {json.dumps(prefix)};",
           "  const show = (part, variant, names, t) => { for (const n of names) tl.set('#' + P + '-' + part + (n === 'default' ? '' : '__' + n), { opacity: n === variant ? 1 : 0 }, start + t); };"]
    for track in keys["mouth"]:
        names = json.dumps(track["variants"])
        for k in track["keys"]:
            out.append(f"  show({json.dumps(track['part'])}, {json.dumps(k['variant'])}, {names}, {k['t']});")
    for track in keys["blink"]:
        names = json.dumps(track["variants"])
        for k in track["keys"]:
            out.append(f"  show({json.dumps(track['part'])}, {json.dumps(k['variant'])}, {names}, {k['t']});")
    for track in keys["sway"]:
        for k in track["keys"]:
            out.append(f"  tl.set('#' + P + '-g-' + {json.dumps(track['group'])}, {{ rotation: {k['rotation']} }}, start + {k['t']});")
    out.append("};")
    return "\n".join(out) + "\n"


def build_keys(rig: dict[str, Any], *, audio: Path | None, fps: int, duration: float | None,
               start: float = 0.0, mouth_part: str = "mouth", eyelid_parts: tuple[str, ...] = ("eyelid_l", "eyelid_r"),
               head_group: str = "head") -> dict[str, Any]:
    parts = {p["id"]: p for p in rig["parts"]}
    keys: dict[str, Any] = {"fps": fps, "start": start, "mouth": [], "blink": [], "sway": []}
    if audio is not None:
        env = rms_envelope(audio, fps)
        duration = duration or len(env) / fps
        if mouth_part in parts:
            available = set(parts[mouth_part]["variants"])
            keys["mouth"].append({"part": mouth_part, "variants": sorted(available),
                                  "keys": mouth_keys(env, fps, available, start=start)})
    if duration is None:
        raise SystemExit("parts rig: --duration is required when no --audio is given")
    seed = f"{audio.name if audio else ''}:{duration:.3f}"
    for eyelid in eyelid_parts:
        if eyelid in parts and {"open", "half", "closed"} <= set(parts[eyelid]["variants"]):
            keys["blink"].append({"part": eyelid, "variants": sorted(parts[eyelid]["variants"]),
                                  "keys": blink_keys(duration, start=start, seed=seed)})
    if head_group in rig["groups"]:
        keys["sway"].append({"group": head_group, "keys": sway_keys(duration, start=start)})
    keys["duration"] = round(duration, 4)
    return keys


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--catalog", required=True, type=Path)
    p.add_argument("--match-dir", required=True, type=Path, help="dir holding parts-match.report.json + placed/")
    p.add_argument("--out-dir", type=Path, default=None, help="default: --match-dir")
    p.add_argument("--audio", type=Path, default=None, help="narration to drive the mouth (mp3/wav)")
    p.add_argument("--duration", type=float, default=None, help="seconds (required without --audio)")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--start", type=float, default=0.0, help="timeline offset for every key")
    p.add_argument("--prefix", default="rig", help="DOM id prefix")
    p.add_argument("--asset-prefix", default="", help="path prefix for <img src> in rig.html")


def run(*, catalog: Path, match_dir: Path, out_dir: Path | None = None, audio: Path | None = None,
        duration: float | None = None, fps: int = 30, start: float = 0.0, prefix: str = "rig",
        asset_prefix: str = "") -> int:
    catalog, match_dir = Path(catalog), Path(match_dir)
    out_dir = Path(out_dir) if out_dir else match_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rig = build_rig(catalog, match_dir)
    (out_dir / "rig.json").write_text(json.dumps(rig, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "rig.html").write_text(render_html(rig, prefix=prefix, asset_prefix=asset_prefix), encoding="utf-8")
    keys = build_keys(rig, audio=Path(audio) if audio else None, fps=fps, duration=duration, start=start)
    (out_dir / "rig-keys.json").write_text(json.dumps(keys, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "rig-keys.js").write_text(render_keys_js(keys, prefix=prefix), encoding="utf-8")
    print(json.dumps({"ok": True, "parts": len(rig["parts"]), "groups": list(rig["groups"]),
                      "mouth_keys": sum(len(t["keys"]) for t in keys["mouth"]),
                      "blink_keys": sum(len(t["keys"]) for t in keys["blink"]),
                      "sway_keys": sum(len(t["keys"]) for t in keys["sway"]), "out_dir": str(out_dir)}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build rig.json + HTML runtime + GSAP keys from matched parts.")
    add_arguments(parser)
    args = parser.parse_args(argv)
    return run(catalog=args.catalog, match_dir=args.match_dir, out_dir=args.out_dir, audio=args.audio,
               duration=args.duration, fps=args.fps, start=args.start, prefix=args.prefix, asset_prefix=args.asset_prefix)


if __name__ == "__main__":
    raise SystemExit(main())
