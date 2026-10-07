# SPDX-License-Identifier: Apache-2.0
"""A synthetic, public end-to-end example of source restoration (docs/loop-comparison.md).

Everything here is drawn: a small round walker, twelve frames at twice the cell's size on a
transparent ground, standing in for the keyed frames `video-frames` writes from a clip. The clip
is those frames over a flat green, encoded only so that the source manifest binds real
presentation timestamps; nothing is keyed from it. The delivered loop stands in for one an earlier
engine wrote: its cells are the frames cut by the loop's own recipe (scrub, speck cleanup, the
cell resample), and two of them stand for in-between frames a jump repair adopted although they
had melted: the outline and the belt drawn over in the body's colour, the feet left a pale ghost.
Its loop report names those two cells as the repair's, and says it is a stand-in.

From there on nothing is drawn by hand. The official verbs run in a subprocess, as a user runs
them: `video-source-manifest` receipts the source, `video-loop-repair` writes a candidate and
`video-loop-compare` measures the final files. The first candidate is adopted as the next baseline
with the same origin; the second request restores the other cell; a third must be a byte-exact
no-op. The script stops with an error unless both comparisons are `improved` and the third
`non_regressing`, every cell but the restored ones stays byte for byte the delivered one, the
timing and the coverage are kept, no key excess is introduced, and the origin's five files are
unchanged at the end.

    .venv/bin/python scripts/dev/source_restoration_demo.py --out-dir <a new directory>

`<out-dir>/summary.json` holds no path and no time: run twice on one machine it is the same bytes.
`<out-dir>/showcase/` holds the delivered GIF and the restored GIF exactly as the engine wrote
them, and each of them again with every pixel drawn `SCALE` x `SCALE` for a page (`-x3.gif`),
made here from its decoded frames and checked against them, not by the engine. It is a new
synthetic example, not a measurement of any delivered loop.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.util.resample import resize_cell
from sprite_gen.video import loop
from sprite_gen.video.frames import drop_specks
from sprite_gen.video.interpolation_quality import OUTLINE_WARN, SMEAR_WARN
from sprite_gen.video.playback import Playback, visible_bytes

N, FPS = 12, 24.0  # one cycle of twelve frames, half a second
ZOOM = 2  # a source frame is twice the cell's size, so every cell is a resample, as a delivered loop's are
W, H = 96, 112  # the cell
SW, SH = W * ZOOM, H * ZOOM
DAMAGED = (4, 9)  # the cells the stand-in jump repair made: interior, apart, not at the loop's seam
DELAY = round(1000 * (N / FPS) / N)  # the export's whole-millisecond delay, as `playback.mapping` reads it
GHOST = 96  # the coverage a melted in-between leaves the feet at
SCALE = 3  # page pixels per cell pixel in the enlarged GIFs
ARTIFACTS = {"strip": ".strip.png", "meta": ".strip.json", "report": ".loop.json", "gif": ".gif", "webp": ".webp"}

INK = (34, 26, 30, 255)  # every outline: dark, as `rife.smear` reads an outline
EYE = (40, 32, 44, 255)
BODY = (236, 148, 74, 255)
BELLY = (250, 216, 164, 255)
CHEEK = (240, 120, 112, 255)
SHINE = (255, 252, 246, 255)
FOOT = (156, 98, 64, 255)
FOOT_LINE = (112, 70, 50, 255)  # lighter than `rife.DARK_LUMA`: a stepping foot never counts as dark
# Two colours under the green key's bar, one led by red and one by blue. Where the belt's halves
# meet, the cell's resample mixes them evenly into (105, 114, 105): one step over the bar, made by
# the resample, in every delivered cell (docs/video-pipeline.md, "Cells").
WARM, COOL = (120, 114, 90, 255), (90, 114, 120, 255)
SEAM = 97  # the belt's first COOL column: odd, so one cell column takes two source columns of each
SWING = (0, 2, 4, 6, 4, 2, 0, -2, -4, -6, -4, -2)  # a foot's step; even, so a cell moves by whole pixels
BOB = (0, 2, 4, 2, 0, -2) * 2  # the body rises and falls twice a cycle; even for the same reason
LIFT = 4  # how far a foot is raised while it steps forward


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def receipt(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {"sha256": sha(data), "bytes": len(data)}


def draw(k: int) -> Image.Image:
    """Frame k of the walk, at the source's size: one connected figure, its feet stepping under a bobbing body.

    The legs stand still under the body and only the feet step: whatever meets the dark outline
    then meets it the same way in every frame, so no frame has more dark pixels than its
    neighbours and the walk itself carries no interpolation fault.
    """
    im = Image.new("RGBA", (SW, SH))
    d = ImageDraw.Draw(im)
    b = BOB[k]
    ahead = SWING[(k + 1) % N] - SWING[k]  # > 0 while the left foot steps forward, < 0 while the right does
    for leg, step, lift in ((72, SWING[k], LIFT if ahead > 0 else 0), (120, -SWING[k], LIFT if ahead < 0 else 0)):
        d.rectangle((leg - 7, 168, leg + 7, 210), fill=FOOT, outline=FOOT_LINE, width=4)
        d.ellipse((leg + step - 16, 200 - lift, leg + step + 16, 222 - lift), fill=FOOT, outline=FOOT_LINE, width=4)
    d.ellipse((26, 118 + b, 58, 148 + b), fill=BODY, outline=INK, width=4)
    d.ellipse((134, 118 + b, 166, 148 + b), fill=BODY, outline=INK, width=4)
    d.line((96, 72 + b, 106, 44 + b), fill=INK, width=4)
    d.ellipse((98, 28 + b, 116, 46 + b), fill=CHEEK, outline=INK, width=4)
    body = (44, 66 + b, 148, 186 + b)
    d.ellipse(body, fill=BODY, outline=INK, width=6)
    d.ellipse((70, 120 + b, 122, 178 + b), fill=BELLY)
    d.rectangle((54, 140 + b, SEAM - 1, 153 + b), fill=WARM)
    d.rectangle((SEAM, 140 + b, 138, 153 + b), fill=COOL)
    d.ellipse(body, outline=INK, width=6)  # the belt's ends sit under the outline, which stays whole
    for x in (72, 108):
        d.ellipse((x, 94 + b, x + 12, 114 + b), fill=EYE)
        d.rectangle((x + 3, 98 + b, x + 6, 101 + b), fill=SHINE)
    d.ellipse((60, 116 + b, 74, 126 + b), fill=CHEEK)
    d.ellipse((118, 116 + b, 132, 126 + b), fill=CHEEK)
    d.arc((86, 108 + b, 106, 124 + b), 20, 160, fill=INK, width=3)
    return im


def cleaned(frame: Image.Image) -> Image.Image:
    """The loop's own cleanup, before the cell resample (`source.RECIPE`)."""
    c = frame.copy()
    loop._scrub(c)
    return drop_specks(c, alpha_over=16, diagonal=False, apart=0)[0]


def melt(frame: Image.Image) -> Image.Image:
    """A stand-in for an in-between that melted: lines and belt in the body's colour, the feet a pale ghost."""
    out = frame.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            p = px[x, y]
            if p in (INK, WARM, COOL):
                px[x, y] = BODY
            elif p in (FOOT, FOOT_LINE):
                px[x, y] = p[:3] + (GHOST,)
    return out


def paths(directory: Path) -> dict[str, Path]:
    return {k: directory / ("loop" + ext) for k, ext in ARTIFACTS.items()}


def five(files: dict[str, Path]) -> dict[str, object]:
    return {k: receipt(files[k]) for k in ARTIFACTS}


def run_cli(log: Path, *args: str) -> None:
    """One official verb in its own process, stdout and stderr kept beside its outputs."""
    proc = subprocess.run([sys.executable, "-m", "sprite_gen.cli", *args], capture_output=True, text=True)
    log.write_text(f"$ sprite-gen {' '.join(args)}\nexit {proc.returncode}\n--- stdout\n{proc.stdout}--- stderr\n{proc.stderr}")
    if proc.returncode:
        raise SystemExit(f"source_restoration_demo: `sprite-gen {args[0]}` exited {proc.returncode}; see {log}")


def check(ok: bool, what: str, passed: list[str]) -> None:
    if not ok:
        raise SystemExit(f"source_restoration_demo: check failed: {what}")
    passed.append(what)


def write_source(out: Path) -> dict[str, Path]:
    keyed, green = out / "source" / "keyed", out / "work" / "green"
    keyed.mkdir(parents=True)
    green.mkdir(parents=True)
    frames = [draw(k) for k in range(N)]
    for k, f in enumerate(frames):
        f.save(keyed / f"frame-{k:04d}.png")
        screen = Image.new("RGB", f.size, (0, 255, 0))
        screen.paste(f, mask=f.getchannel("A"))
        screen.save(green / f"frame-{k:04d}.png")
    canvas = out / "source" / "canvas.png"
    frames[0].save(canvas)
    clip = out / "source" / "clip.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("source_restoration_demo: ffmpeg is not on PATH")
    subprocess.run([ffmpeg, "-v", "error", "-framerate", str(int(FPS)), "-i", str(green / "frame-%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-fflags", "+bitexact", "-flags:v", "+bitexact",
                    "-map_metadata", "-1", str(clip)], check=True)
    report = out / "source" / "frames.json"
    report.write_text(json.dumps({
        "kind": "sprite-gen-video-frames-report", "fps": FPS, "frames": N, "width": SW, "height": SH,
        "stream_index": 0, "key": "green",
        "note": "synthetic: the keyed frames are drawn by scripts/dev/source_restoration_demo.py, not keyed from the clip"},
        indent=2) + "\n")
    manifest = out / "source" / "source.json"
    run_cli(out / "logs" / "video-source-manifest.log", "video-source-manifest", "--clip", str(clip),
            "--canvas", str(canvas), "--frames-report", str(report), "--frames-dir", str(keyed), "--out", str(manifest))
    return {"source_manifest": manifest, "source_frames_dir": keyed, "source_clip": clip,
            "source_canvas": canvas, "source_frames_report": report}


def write_delivered(out: Path, source: dict[str, Path]) -> tuple[dict[str, Path], list[Image.Image]]:
    """The stand-in for a loop an earlier engine delivered, cut from the source by the loop's recipe."""
    identity = json.loads(source["source_manifest"].read_bytes())["source"]
    keyed = sorted(source["source_frames_dir"].glob("*.png"))
    clean = []
    for p in keyed:
        with Image.open(p) as im:
            clean.append(cleaned(im.convert("RGBA")))
    cells = [resize_cell(melt(f) if k in DAMAGED else f, (W, H)) for k, f in enumerate(clean)]
    meta = {"frames": N, "w": W, "h": H, "delay_ms": round(1000 / FPS, 2), "cycle_seconds": round(N / FPS, 4),
            "cycle_frames": N, "subsampled": False, "loop": True, "scale": round(1 / ZOOM, 4),
            "source_rect": [0, 0, SW, SH], "sample_indices": list(range(N)), "foot_anchor": "none",
            "source_cut": {"source": identity, "start": 0, "length": N, "samples": list(range(N))}}
    report = {"kind": "sprite-gen-video-loop-report", "status": "passed", "fps": FPS, "frames_total": N,
              "source": identity, "cycle": {"start": 0, "length": N}, "anchor": "none",
              "jump_repair": {"replaced": list(DAMAGED)}, "strip": meta, "n_out": N, "delay_ms": DELAY,
              "note": ("synthetic stand-in for a delivered loop: cells cut by the loop recipe from drawn keyed frames; "
                       f"cells {DAMAGED[0]} and {DAMAGED[1]} drawn as melted in-betweens (scripts/dev/source_restoration_demo.py)")}
    files = paths(out / "delivered")
    files["strip"].parent.mkdir(parents=True)
    sheet = Image.new("RGBA", (N * W, H))
    for k, cell in enumerate(cells):
        sheet.paste(cell, (k * W, 0))
    sheet.save(files["strip"])
    files["meta"].write_text(json.dumps(meta, indent=2) + "\n")
    files["report"].write_text(json.dumps(report, indent=2) + "\n")
    save_clean_gif(cells, files["gif"], duration_ms=DELAY, alpha_threshold=128)
    loop.write_webp(cells, files["webp"], delay_ms=DELAY, workdir=out / "work" / "webp")
    return files, cells


def request(out: Path, n: int, baseline: dict[str, Path], origin: dict[str, Path],
            source: dict[str, Path]) -> tuple[dict, dict, dict[str, Path]]:
    """One `video-loop-repair` request and the `video-loop-compare` of the files it wrote."""
    here = out / f"request-{n}"
    here.mkdir()
    given = [a for side, files in (("baseline", baseline), ("origin", origin)) for k in ARTIFACTS
             for a in (f"--{side}-{k}", str(files[k]))]
    inputs = [a for k, p in source.items() for a in ("--" + k.replace("_", "-"), str(p))]
    run_cli(out / "logs" / f"request-{n}.repair.log", "video-loop-repair", *given, *inputs,
            "--out-dir", str(here / "candidate"), "--name", "loop", "--proposal-index", "1",
            "--report", str(here / "repair.json"))
    repair = json.loads((here / "repair.json").read_bytes())
    candidate = {k: Path(p) for k, p in repair["outputs"].items()}
    if set(candidate) != set(ARTIFACTS):
        raise SystemExit(f"source_restoration_demo: request {n} wrote no candidate ({repair['status']}, {repair['reasons']})")
    made = [a for k in ARTIFACTS for a in (f"--candidate-{k}", str(candidate[k]))]
    run_cli(out / "logs" / f"request-{n}.compare.log", "video-loop-compare", *given, *made, *inputs,
            "--repair-evidence", str(here / "repair.json"), "--report", str(here / "comparison.json"))
    return repair, json.loads((here / "comparison.json").read_bytes()), candidate


def strip_cells(files: dict[str, Path]) -> list[Image.Image]:
    with Image.open(files["strip"]) as im:
        sheet = im.convert("RGBA")
    return [sheet.crop((k * W, 0, (k + 1) * W, H)) for k in range(N)]


def summarise(n: int, repair: dict, comparison: dict, candidate: dict[str, Path], baseline: str) -> dict:
    partial = repair.get("partial") or {}
    key = comparison.get("axes", {}).get("key_colour", {})
    return {"request": n, "baseline": baseline, "origin": "delivered", "status": repair["status"],
            "target": repair.get("target"), "proposals_available": repair.get("proposals_available"),
            "applied_cells": repair.get("applied_cells"), "proposal_id": repair.get("proposal_id"),
            "interpolation_calls": repair["interpolation_calls"],
            "masks": {m: (partial.get(m) or {}).get("count") for m in ("copied", "capped", "protected")},
            "alpha_equals_source": partial.get("alpha_equals_source"),
            "candidate": five(candidate),
            "comparison": {"sha256": receipt(candidate["strip"].parent.parent / "comparison.json")["sha256"],
                           "verdict": comparison["verdict"], "reasons": comparison["reasons"],
                           "cleared_faults": comparison.get("cleared_faults"),
                           "changed_cells": comparison.get("preservation", {}).get("changed_cells"),
                           "boundaries_exact": comparison.get("preservation", {}).get("boundaries_exact"),
                           "durations_ms": {k: v["durations_ms"] for k, v in comparison.get("playback", {}).get("candidate", {}).items()},
                           "key_introduced_pixels": key.get("introduced_pixels"),
                           "gait": comparison.get("gait")}}


def enlarged(gif: Path, out: Path, passed: list[str]) -> dict[str, object]:
    """`gif`'s decoded frames, each pixel drawn SCALE x SCALE, on its schedule, checked against them.

    One GIF per file: a frame's palette holds 255 colours, and a delivered cell and its restored
    one together can hold more, so two loops side by side in one GIF would not be shown exactly.
    """
    shown = Playback.read(gif, "GIF")
    frames = [f.resize((W * SCALE, H * SCALE), Image.Resampling.NEAREST) for f in shown.frames]
    save_clean_gif(frames, out, duration_ms=shown.durations[0], alpha_threshold=128)
    written = Playback.read(out, "GIF")
    differ = [k for k, (f, e) in enumerate(zip(written.frames, frames)) if visible_bytes(f) != visible_bytes(e)]
    check(written.durations == shown.durations and len(written.frames) == len(frames) and not differ,
          f"{out.name} shows exactly {gif.parent.name}/{gif.name}'s frames, enlarged, on its schedule"
          + (f" (frames {differ} differ)" if differ else ""), passed)
    return {"file": f"showcase/{out.name}", **receipt(out), "frames": len(written.frames), "durations_ms": written.durations}


def showcase(out: Path, before: Path, after: Path, passed: list[str]) -> dict[str, object]:
    """The two engine GIFs as written, and each enlarged for a page, checked against its frames."""
    shown = out / "showcase"
    shown.mkdir()
    shutil.copyfile(before, shown / "source-restoration-before.gif")
    shutil.copyfile(after, shown / "source-restoration-after.gif")
    a, b = Playback.read(before, "GIF"), Playback.read(after, "GIF")
    check(a.durations == b.durations and len(a.frames) == len(b.frames), "the two engine GIFs play the same schedule", passed)
    made_here = f"each pixel of the engine GIF drawn {SCALE} x {SCALE}; made by this script, not by the engine"
    return {"before": {"file": "showcase/source-restoration-before.gif", **receipt(shown / "source-restoration-before.gif"),
                       "what": "delivered/loop.gif as written"},
            "after": {"file": "showcase/source-restoration-after.gif", **receipt(shown / "source-restoration-after.gif"),
                      "what": "request-2/candidate/loop.gif as written by video-loop-repair"},
            "before_enlarged": {**enlarged(before, shown / f"source-restoration-before-x{SCALE}.gif", passed), "what": made_here},
            "after_enlarged": {**enlarged(after, shown / f"source-restoration-after-x{SCALE}.gif", passed), "what": made_here}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, required=True, help="a new directory; it must not exist")
    args = parser.parse_args(argv)
    out = args.out_dir
    if out.exists():
        raise SystemExit(f"source_restoration_demo: {out} exists; give a new directory")
    if not loop.img2webp_supports_exact():
        raise SystemExit("source_restoration_demo: img2webp from libwebp >= 1.5 (its -exact flag) is required")
    (out / "logs").mkdir(parents=True)
    passed: list[str] = []
    source = write_source(out)
    delivered, delivered_cells = write_delivered(out, source)
    shutil.rmtree(out / "work")
    origin = five(delivered)

    first, compared_1, adopted = request(out, 1, delivered, delivered, source)
    second, compared_2, restored = request(out, 2, adopted, delivered, source)
    third, compared_3, again = request(out, 3, restored, delivered, source)

    for n, r, c in ((1, first, compared_1), (2, second, compared_2)):
        check(r["status"] == "candidate" and r["interpolation_calls"] == 0, f"request {n} wrote a candidate without interpolation", passed)
        check(c["verdict"] == "improved" and c["reasons"] == ["verified-source-defect-restored"],
              f"request {n}: video-loop-compare says improved", passed)
        check([x["cell"] for x in c["cleared_faults"]] == [r["target"]] and all(x["faults"] for x in c["cleared_faults"]),
              f"request {n}: the restored cell's fault is cleared", passed)
        check(c["preservation"]["changed_cells"] == [r["target"]] and c["preservation"]["boundaries_exact"],
              f"request {n}: one interior cell changed", passed)
        check(r["partial"]["alpha_equals_source"] and r["partial"]["protected"]["count"] == 0,
              f"request {n}: the cell's coverage is the source's and no place is protected", passed)
        check(r["partial"]["capped"]["count"] > 0, f"request {n}: the belt's made one-step key excess is capped", passed)
        check(not any(c["axes"]["key_colour"]["introduced_pixels"]), f"request {n}: no key excess introduced in any cell", passed)
        play = c["playback"]
        check(all(play["baseline"][k]["durations_ms"] == play["candidate"][k]["durations_ms"]
                  and play["baseline"][k]["strip_indices"] == play["candidate"][k]["strip_indices"] for k in ("gif", "webp")),
              f"request {n}: GIF and WebP schedules unchanged", passed)
    reference = compared_1["axes"]
    check(max(reference["dark_excess"]["reference"]) < SMEAR_WARN / 2
          and max(reference["outline_loss"]["reference"]) < OUTLINE_WARN / 2,
          "the drawn walk itself stays under half of each interpolation fault bound", passed)
    check(sorted([first["target"], second["target"]]) == list(DAMAGED) and second["applied_cells"] == [first["target"]],
          "the two requests restored the two damaged cells, the second rebuilt on the first", passed)
    check(third["status"] == "no_change" and compared_3["verdict"] == "non_regressing"
          and five(again) == five(restored), "a third request is a byte-exact no-op", passed)
    final = strip_cells(restored)
    check(all(final[k].tobytes() == delivered_cells[k].tobytes() for k in range(N) if k not in DAMAGED),
          "every other cell is byte for byte the delivered one", passed)
    check(five(delivered) == origin, "the origin's five files are unchanged", passed)
    with open(restored["meta"], "rb") as m, open(delivered["meta"], "rb") as d:
        check(m.read() == d.read(), "the metadata bytes are the delivered ones", passed)

    shown = showcase(out, delivered["gif"], restored["gif"], passed)
    manifest = json.loads(source["source_manifest"].read_bytes())
    ffmpeg = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    summary = {
        "kind": "sprite-gen-source-restoration-demo", "schema_version": 1,
        "what": "a new synthetic example drawn by this script; not a measurement of any delivered loop",
        "contract": {k: compared_1[k] for k in ("schema_version", "metric_version", "policy_version", "scope", "operation")},
        "repair_schema": first["schema_version"], "engine": compared_1["measurement_engine"],
        "environment": {"python": platform.python_version(), **{k: manifest["receipt_environment"][k] for k in ("pillow", "numpy")},
                        "ffmpeg": ffmpeg},
        "drawn": {"frames": N, "fps": FPS, "source_size": [SW, SH], "cell": [W, H], "damaged_cells": list(DAMAGED),
                  "damage": "outline and belt drawn in the body's colour, feet at coverage %d" % GHOST},
        "source": {"sequence": manifest["source"], "manifest": receipt(source["source_manifest"]),
                   "clip": receipt(source["source_clip"]), "canvas": receipt(source["source_canvas"]),
                   "frames_report": receipt(source["source_frames_report"])},
        "delivered": origin,
        "requests": [summarise(1, first, compared_1, adopted, "delivered"),
                     summarise(2, second, compared_2, restored, "request-1"),
                     summarise(3, third, compared_3, again, "request-2")],
        "showcase": shown, "checks": passed}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"summary": str(out / "summary.json"), "checks": len(passed),
                      "verdicts": [compared_1["verdict"], compared_2["verdict"], compared_3["verdict"]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
