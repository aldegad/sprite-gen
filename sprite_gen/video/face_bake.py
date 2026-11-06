# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen face-bake` — a line baked ahead: a body motion with the face layer talking to a voice, as frames, a
sound track and a report.

The frames are picked by the pack's own player (`sprite-gen-face-v1.mjs`, run with node through `face_plan.mjs`:
`mouthTrack`, then `planScene`), the same functions a live page calls, so a baked line and a live one show the same frames. The voice is
folded to mono first and padded after (`--lead`, `--tail`: silence before and after it), so a stereo voice is never
delayed on one channel alone. With `--play` a body clip plays from its start with its own sound until its own_mouth
span ends, and the line follows; without it the line is said on the loop from `--start-cell`.

Each frame is laid as a live page draws it (`face_layer.lay`: the body cell, the face sprite over it through the
cell's anchor, what of the body stays on top over that) and read where the face is (`face_layer.step_features`):

- **outside**: pixels changed where the layer's weight, through the cell's anchor, is 0. Must be 0.
- **jumps**: a frame whose step from the frame before is over the jump line times the larger of the body's own step,
  the layer's own step and the body's median step is over the line (`over_line`). The body and the layer moving in
  the same frame can each be within the line and still add up past it; a frame over the line and over the sum of the
  two steps as well is one the laying put in (`over_sum`), and a bake with one is refused.
- **held**: frames whose body cell shows the picture of the frame before (`held_fill.same_steps`, on the cells as the pack
  has them), the body standing still for a frame mid-motion (docs/loop-repair.md section 7). A run a clip means is
  recorded and not warned of: longer than `held_fill.HOLD_MAX` frames, the opening or closing pose of a clip played
  once (cells of one clip that is not the loop, from its first cell or to its last), or cells a pack's body row lists
  as `holds` (`from`, `to`, both in, a strip's `holds`). Any other is named in a warning and baked as the pack has it:
  the bake makes no frame.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.util.console import utf8_streams
from sprite_gen.video import face_layer as fl
from sprite_gen.video import face_pack
from sprite_gen.video import held_fill as held_fill_mod

PROG = "face-bake"
RATE = 48000
MATTE = "2a2430"

PLAN = Path(__file__).with_name("face_plan.mjs")


def load_pack(folder: Path) -> dict[str, Any]:
    """The pack's manifest, every file it lists checked against its digest."""
    try:
        pack = json.loads((folder / "face-pack.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{PROG}: --pack {folder}: {exc}") from None
    if pack.get("kind") != face_pack.PACK_KIND or pack.get("version") != face_pack.VERSION:
        raise SystemExit(f"{PROG}: --pack {folder}: not a {face_pack.PACK_KIND} version {face_pack.VERSION}")
    for name, digest in pack["files"].items():
        path = folder / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"{PROG}: --pack {folder}: {name} is missing or not the file the pack was made with")
    return pack


def decode(path: Path, seconds: float | None = None) -> np.ndarray:
    """A sound as mono float samples at RATE (ffmpeg folds the channels), cut or padded to `seconds` when given."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit(f"{PROG}: ffmpeg is required on PATH to read {path}")
    proc = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", str(RATE),
                           "-f", "f32le", "pipe:1"], capture_output=True)
    if proc.returncode:
        raise SystemExit(f"{PROG}: ffmpeg could not read the sound of {path}")
    x = np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)
    if seconds is not None:
        n = int(round(seconds * RATE))
        x = np.concatenate([x[:n], np.zeros(max(0, n - len(x)), np.float32)])
    return x


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.clip(np.round(x * 32767), -32768, 32767).astype("<i2").tobytes())


def plan_line(folder: Path, voice: np.ndarray, play: str | None, start: int, tags: list[str]) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        raise SystemExit(f"{PROG}: node is required on PATH: the pack's player (sprite-gen-face-v1.mjs) picks the frames")
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        (tmp / "samples.f32").write_bytes(voice.astype("<f4").tobytes())
        (tmp / "args.json").write_text(json.dumps({"rate": RATE, "tags": tags, "play": play, "start": start}), encoding="utf-8")
        proc = subprocess.run([node, str(PLAN), str(folder / face_pack.PLAYER_PATH.name), str(folder / "face-pack.json"),
                               str(tmp / "samples.f32"), str(tmp / "args.json"), str(tmp / "plan.json")], capture_output=True, encoding="utf-8")
        if proc.returncode:
            raise SystemExit(f"{PROG}: the pack's player failed: {proc.stderr.strip()[-600:]}")
        return json.loads((tmp / "plan.json").read_text(encoding="utf-8"))


def _matrix(six: list[float]) -> np.ndarray:
    return np.array([six[:3], six[3:], [0.0, 0.0, 1.0]])


def _meant(cells: list[tuple[str, int]], bodies: dict[str, dict[str, Any]]) -> tuple[str, str] | None:
    """Why a run of baked frames showing one body picture is a hold a clip means, and its kind, or None: the body
    cells it shows (`cells`, one a frame) read as the clips they are cells of."""
    if len(cells) > held_fill_mod.HOLD_MAX:
        return (f"held {len(cells)} frames, longer than a drawing on threes ({held_fill_mod.HOLD_MAX}): a hold the motion "
                "means"), "motion-hold"
    names = {name for name, _ in cells}
    if len(names) != 1:
        return None
    name = names.pop()
    b, ks = bodies[name], [k for _, k in cells]
    if ks != list(range(ks[0], ks[0] + len(ks))):
        return None  # across the loop's wrap
    for h in b.get("holds") or []:
        if h["from"] <= ks[0] and ks[-1] <= h["to"]:
            return str(h.get("reason") or f"a hold the clip {name} lists"), str(h.get("kind") or "listed")
    if b.get("loop"):
        return None
    if ks[0] == 0:
        return f"the clip {name}'s opening pose", "opening"
    if ks[-1] == len(b["cells"]) - 1:
        return f"the clip {name}'s closing pose", "closing"
    return None


def held_reading(shown: list[tuple[str, int]], same: list[bool], bodies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The bake's body read for a picture shown again: its frames, poses and held runs (`held_fill.reading`), each run
    with the body cells it shows and, where a clip means it, why (`_meant`)."""
    record = {**held_fill_mod.reading(same), "pixel_same": held_fill_mod.PIXEL_SAME,
              "changed_max": held_fill_mod.CHANGED_MAX, "hold_max": held_fill_mod.HOLD_MAX}
    for run in record["held_runs"]:
        cells = shown[run["start"]:run["start"] + run["length"]]
        run["body"] = [[name, k] for name, k in cells]
        meant = _meant(cells, bodies)
        if meant is not None:
            run["why"], run["kind"] = meant
    record.pop("cells")
    record["frames"] = len(shown)
    return record


def said(record: dict[str, Any]) -> str:
    """The body's poses over the bake's frames, as a person is told them: `poses/frames`."""
    return f"{record['poses']}/{record['frames']}"


def held_lines(record: dict[str, Any]) -> list[str]:
    """A warning for each held run of the bake no clip means."""
    lines = []
    for run in record["held_runs"]:
        if "why" in run:
            continue
        end = run["start"] + run["length"] - 1
        cells = ", ".join(f"{name}:{k}" for name, k in run["body"])
        lines.append(f"frames {run['start']}-{end} show one body picture ({cells}; {said(record)} poses/frames): "
                     "the body stands still there mid-motion, baked as the pack has it — make the pack from frames whose "
                     "held cells are made again (video-loop --fill-held auto, docs/loop-repair.md section 7)")
    return lines


def bake(pack_dir: Path, voice_path: Path, out: Path, *, play: str | None = None, start: int = 0, tags: list[str] | None = None,
         lead: float = 0.5, tail: float = 0.6) -> dict[str, Any]:
    pack = load_pack(pack_dir)
    tags = list(tags or [])
    bodies = {b["name"]: b for b in pack["body"]}
    if play is not None and play not in bodies:
        raise SystemExit(f"{PROG}: --play {play}: the pack's body clips are {', '.join(bodies)}")
    if lead < 0 or tail < 0:
        raise SystemExit(f"{PROG}: --lead and --tail are seconds of silence, 0 or more")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{PROG}: --out {out} is not empty")
    fps = float(pack["fps"])
    voice = decode(voice_path)
    padded = np.concatenate([np.zeros(int(round(lead * RATE)), np.float32), voice, np.zeros(int(round(tail * RATE)), np.float32)])
    result = plan_line(pack_dir, padded, play, start, tags)
    plan, track = result["plan"], result["track"]

    geom = fl.Geometry.read(pack["face"]["geometry"], tuple(pack["size"]))
    w0 = np.load(pack_dir / "weight.npy")
    sprites: dict[int, fl.Sprite] = {}
    keeps: dict[tuple[str, int], Any] = {}
    out.mkdir(parents=True, exist_ok=True)
    frames_dir = out / "frames"
    frames_dir.mkdir()

    def sprite(j: int) -> fl.Sprite:
        if j not in sprites:
            row = pack["face"]["frames"][j]
            sprites[j] = fl.Sprite(Image.open(pack_dir / row["file"]).convert("RGBA"), _matrix(row["to_ref"]))
        return sprites[j]

    def keep(name: str, k: int):
        if (name, k) not in keeps:
            rec = bodies[name]["keep"][k]
            keeps[name, k] = None if rec is None else (Image.open(pack_dir / rec["file"]).convert("RGBA"), tuple(rec["at"]))
        return keeps[name, k]

    rows, prev = [], None
    # the body's steps, read on the cells as the pack has them: a pair of cells once, however often it is shown
    steps: dict[tuple[tuple[str, int], tuple[str, int]], bool] = {}
    shown: list[tuple[str, int]] = []
    same: list[bool] = []
    last_cell = None
    for t, f in enumerate(plan["frames"]):
        b = bodies[f["body"]]
        cell = Image.open(pack_dir / b["cells"][f["cell"]]).convert("RGBA")
        here = (f["body"], f["cell"])
        if shown:
            pair = (shown[-1], here)
            if pair not in steps:
                steps[pair] = pair[0] == pair[1] or held_fill_mod.same_steps([last_cell, cell])[0]
            same.append(steps[pair])
        shown.append(here)
        last_cell = cell
        to_body = _matrix(b["to_body"][f["cell"]])
        sp = None if f["face"] is None else sprite(f["face"])
        laid = fl.lay(cell, sp, to_body, keep(f["body"], f["cell"]))
        laid.save(frames_dir / f"frame-{t:05d}.png")
        comp, body = np.asarray(laid, np.float32), np.asarray(cell, np.float32)
        w_ell = fl.warp(w0, np.linalg.inv(to_body), cell.size)
        outside = int(np.any(comp[w_ell <= 0] != body[w_ell <= 0], axis=1).sum()) if sp is not None else 0
        feats = [fl.step_features(fl.warp(comp, to_body, geom.size), geom), fl.step_features(fl.warp(body, to_body, geom.size), geom)]
        feats.append(fl.step_features(fl.warp(np.asarray(sp.image, np.float32), np.linalg.inv(sp.to_ref), geom.size), geom)
                     if sp is not None else feats[1])
        row = {"t": t, **f, "outside_changed_px": outside}
        if prev is not None:
            row["step"] = {k: float(np.abs(a - p).mean()) for k, a, p in zip(("comp", "body", "layer"), feats, prev)}
        prev = feats
        rows.append(row)
    med = float(np.median([r["step"]["body"] for r in rows[1:]])) if len(rows) > 1 else 0.0
    over_line, over_sum = [], []
    for r in rows[1:]:
        s = r["step"]
        if s["comp"] > fl.JUMP_LINE * max(s["body"], s["layer"], med):
            over_line.append(r["t"])
            if s["comp"] > s["body"] + s["layer"]:
                over_sum.append(r["t"])

    # the clip's own sound for its own_mouth span, silence while the line waits for the layer, the padded voice
    own, start_at = plan["own_mouth"], plan["line_start"]
    src = bodies[play].get("sound") if play is not None else None
    head = decode(pack_dir / src, own / fps) if src and own else np.zeros(int(round(own / fps * RATE)), np.float32)
    head = np.concatenate([head, np.zeros(int(round(start_at / fps * RATE)) - len(head), np.float32)])
    track_audio = np.concatenate([head, padded])
    frames = len(rows)
    track_audio = np.concatenate([track_audio, np.zeros(max(0, int(round(frames / fps * RATE)) - len(track_audio)), np.float32)])
    write_wav(out / "voice-baked.wav", track_audio)
    write_wav(out / "voice-padded.wav", padded)
    refused = []
    if over_sum:
        refused.append(f"frames {over_sum} step over the jump line and over the body's and the layer's steps together")
    if any(r["outside_changed_px"] for r in rows):
        refused.append("pixels changed outside the layer's weight")
    report = {"pack": str(pack_dir), "voice": str(voice_path), "play": play, "start_cell": start, "tags": tags, "lead_s": lead, "tail_s": tail,
              "fps": fps, "frames": frames, "set": plan["set"], "rest": plan["rest"], "own_mouth_frames": plan["own_mouth"],
              "line_start": plan["line_start"], "wait_frames": plan["wait"], "levels": track["levels"], "syllables": len(track["syllables"]),
              "talk_frames": sum(1 for r in rows if r["layer"] == "talk"), "rest_frames": sum(1 for r in rows if r["layer"] == "rest"),
              "off_frames": sum(1 for r in rows if r["layer"] == "off"),
              "outside_changed_px_max": max((r["outside_changed_px"] for r in rows), default=0),
              "jump_line": fl.JUMP_LINE, "body_median_step": med, "over_line": over_line, "over_sum": over_sum,
              "verdict": "refused" if refused else "pass", "refused": refused,
              "held": held_reading(shown, same, bodies), "rows": rows}
    (out / "bake.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for line in held_lines(report["held"]):
        print(f"{PROG}: warning: {line}", file=sys.stderr)
    return report


def video(out: Path, report: dict[str, Any], beside: Path | None, height: int | None, matte: str) -> Path:
    """The baked frames (and, beside them on the left, another line's frames of the same count) over a flat matte,
    with the baked sound, as an mp4."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit(f"{PROG}: ffmpeg is required on PATH for --video")
    frames = report["frames"]
    w, h = Image.open(out / "frames" / "frame-00000.png").size
    with tempfile.TemporaryDirectory() as tmp_name:
        inputs = []
        if beside is not None:
            left = sorted(beside.glob("frame-*.png"))
            if len(left) != frames:
                raise SystemExit(f"{PROG}: --beside {beside} holds {len(left)} frames, the bake {frames}: the two lines must be the same length")
            link = Path(tmp_name) / "left"
            link.mkdir()
            for k, p in enumerate(left):
                (link / f"frame-{k:05d}.png").symlink_to(p.resolve())
            inputs += ["-framerate", str(report["fps"]), "-i", str(link / "frame-%05d.png")]
        inputs += ["-framerate", str(report["fps"]), "-i", str(out / "frames" / "frame-%05d.png"), "-i", str(out / "voice-baked.wav")]
        panels = 2 if beside is not None else 1
        chain = f"color=c=0x{matte}:s={w * panels}x{h}:r={report['fps']}[bg]"
        last = "bg"
        for k in range(panels):
            chain += f";[{last}][{k}:v]overlay={k * w}:0:shortest=1[p{k}]"
            last = f"p{k}"
        scale = f",scale=-2:{height}:flags=lanczos" if height else ""
        chain += f";[{last}]format=yuv420p{scale}[v]"
        target = out / ("beside.mp4" if beside is not None else "baked.mp4")
        proc = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *inputs, "-filter_complex", chain, "-map", "[v]",
                               "-map", f"{panels}:a", "-c:v", "libx264", "-crf", "20", "-preset", "slow", "-c:a", "aac", "-b:a", "128k",
                               "-shortest", str(target)], capture_output=True, encoding="utf-8")
        if proc.returncode:
            raise SystemExit(f"{PROG}: ffmpeg could not write {target.name}: {proc.stderr.strip()[-400:]}")
    return target


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pack", type=Path, required=True, help="a face pack folder (sprite-gen video-face)")
    parser.add_argument("--voice", type=Path, required=True, help="the line's voice (any sound ffmpeg reads)")
    parser.add_argument("--out", type=Path, required=True, help="the folder to write (new or empty)")
    parser.add_argument("--play", help="a body clip of the pack to play from its start before the line (its own mouth and sound first)")
    parser.add_argument("--start-cell", type=int, default=0, help="without --play: the loop cell the line starts on (default 0)")
    parser.add_argument("--tags", nargs="*", default=[], help="the line's tags, e.g. [surprised]")
    parser.add_argument("--lead", type=float, default=0.5, help="seconds of silence before the voice (default 0.5)")
    parser.add_argument("--tail", type=float, default=0.6, help="seconds of silence after the voice (default 0.6)")
    parser.add_argument("--video", action="store_true", help="also write baked.mp4 (the frames over a flat matte, with the sound)")
    parser.add_argument("--beside", type=Path, help="also write beside.mp4: these frames (frame-*.png, as many) on the left, the bake on the right")
    parser.add_argument("--video-height", type=int, help="the mp4's height (default the frames')")
    parser.add_argument("--matte", default=MATTE, help=f"the mp4's background, six hex digits (default {MATTE})")


def run(**kwargs: object) -> int:
    out = Path(str(kwargs["out"]))
    report = bake(Path(str(kwargs["pack"])), Path(str(kwargs["voice"])), out, play=kwargs.get("play"),  # type: ignore[arg-type]
                  start=int(kwargs.get("start_cell") or 0), tags=list(kwargs.get("tags") or []),  # type: ignore[arg-type]
                  lead=float(0.5 if kwargs.get("lead") is None else kwargs["lead"]),  # type: ignore[arg-type]
                  tail=float(0.6 if kwargs.get("tail") is None else kwargs["tail"]))  # type: ignore[arg-type]
    made = []
    if kwargs.get("video"):
        made.append(str(video(out, report, None, kwargs.get("video_height"), str(kwargs.get("matte") or MATTE))))  # type: ignore[arg-type]
    if kwargs.get("beside") is not None:
        made.append(str(video(out, report, Path(str(kwargs["beside"])), kwargs.get("video_height"), str(kwargs.get("matte") or MATTE))))  # type: ignore[arg-type]
    print(json.dumps({"out": str(out), "frames": report["frames"], "set": report["set"], "rest": report["rest"],
                      "line_start": report["line_start"], "wait_frames": report["wait_frames"], "talk_frames": report["talk_frames"],
                      "over_line": report["over_line"], "over_sum": report["over_sum"], "outside_changed_px_max": report["outside_changed_px_max"],
                      "held": {"poses_frames": said(report["held"]),
                               "warned": sum(1 for r in report["held"]["held_runs"] if "why" not in r)},
                      "verdict": report["verdict"], "videos": made}, ensure_ascii=False, indent=2))
    if report["verdict"] != "pass":
        print(f"{PROG}: refused: " + "; ".join(report["refused"]))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog=f"sprite-gen {PROG}", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
