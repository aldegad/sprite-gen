# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-set` — directions x states, end to end, one report per item.

For every (direction, state) pair: canvas -> `sprite-gen video` -> frames -> loop.
Clip generation is rate-limited by the xAI team quota (2 requests/second measured
2026-09-08: five parallel POSTs produced two HTTP 429s), so starts are staggered
and a 429 gets a bounded, logged retry. Stages are idempotent — an item whose
clip already exists reuses it unless `--force` — and a failure stops only that
item, never the batch. The batch ends with `set.report.json` and `table.md`; an
item that failed is listed with its stage and error, never silently dropped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.gen.facing import FACINGS, validate as validate_facing
from sprite_gen.video import facing as facing_mod
from sprite_gen.video import canvas as canvas_mod
from sprite_gen.video import frames as frames_mod
from sprite_gen.video import loop as loop_mod

START_GAP_SECONDS = 2.0
# Clip length the batch asks the video model for. 3 s holds two or more cycles of every
# repeating state (walk periods measured at 0.6-1.3 s, 2026-09-18) and a single action
# for jump/attack, which the one-shot cut handles; 6 s bought nothing but a longer wait
# and, for jump, more idle standing between hops.
DEFAULT_DURATION_SECONDS = 3
RETRY_BACKOFF_SECONDS = (15, 30)
VIEW_TEXT = {
    "side": "seen from the exact side, facing {facing}",
    "front": "seen from the front, facing the viewer directly",
    "back": "seen from directly behind, facing away from the viewer",
}
MOTION_TEXT = {
    "idle": "holds a relaxed idle in place: slow gentle breathing, a subtle weight sway, one natural blink if the face has eyes. The ground contact never slides.",
    "walk": "moves in place on a treadmill: a steady locomotion cycle for this body type with clear repeating ground contacts and an even left-right or front-back rhythm the body already has.",
    "run": "moves in place on a treadmill: a fast locomotion cycle for this body type with a bounding rhythm and clear repeating ground contacts.",
    "jump": "performs a modest vertical hop in place over and over: compress, spring up about half the body height, land softly, return to the exact starting stance, repeat at an even rhythm. Same height every time.",
    "attack": "performs the same melee attack over and over with the weapon it is already holding (bare hands only if it holds nothing), keeping every piece of its gear and outfit exactly as drawn: one clean strike in front, then returns to the exact ready stance, repeating at an even rhythm.",
    "cheer": "celebrates in place: rises into a raised, spread-out cheer pose, holds it for a beat, then settles back to the exact starting stance, repeating at an even rhythm.",
    "wave": "waves in place: lifts one side into a friendly wave, sways it a few times, then settles back to the exact starting stance, repeating at an even rhythm.",
}
COMMON_TEXT = (
    "2D game sprite animation. The character {motion} The character is {view}. Stays centered in the frame and does "
    "not move across the screen; the body and hair always stay fully inside the frame with margin. Camera completely "
    "locked, no zoom, no pan, no reframing. The background stays a perfectly flat, pure chroma-key fill for the whole "
    "clip — no shadows, no ground line, no particles, no lighting changes, no effects. Keep the design, colors and "
    "proportions exactly as in the image. Consistent, evenly paced motion so the animation loops."
)

_start_lock = threading.Lock()
_last_start = [0.0]


def _staggered_start(gap: float) -> None:
    with _start_lock:
        wait = _last_start[0] + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_start[0] = time.monotonic()


def build_prompt(direction: str, state: str, character: str | None, facing: str = "right") -> str:
    validate_facing(facing)
    motion = MOTION_TEXT.get(state, f"performs the '{state}' action in place, repeating at an even rhythm.")
    view = VIEW_TEXT.get(direction, f"seen from the {direction}").format(facing=facing)
    text = COMMON_TEXT.format(motion=motion, view=view)
    return text.replace("The character", character, 1) if character else text


def run_video_cli(image: Path, prompt: str, out: Path, report: Path, *, duration: int, resolution: str, log: Path) -> int:
    cmd = [sys.executable, "-m", "sprite_gen.gen.video", "--image", str(image), "--prompt", prompt, "--out", str(out), "--duration", str(duration), "--resolution", resolution, "--no-audio", "--report", str(report)]
    with log.open("w", encoding="utf-8") as fh:
        return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True).returncode


def run_item(
    *,
    item: str,
    direction: str,
    state: str,
    base: Path,
    root: Path,
    character: str | None,
    duration: int,
    resolution: str,
    key: str,
    force: bool,
    gap: float,
    video_runner: Callable[..., int] = run_video_cli,
    shape: str | None = None,
    anchor: str = "none",
    spill: str = "auto",
    facing: str = "right",
    facing_fix: str = "none",
    prepare_side: Callable[[Path], tuple[Path, dict]] | None = None,
) -> dict[str, Any]:
    if anchor == "motion-auto" and not loop_mod.profile_for(state).gait:
        raise SystemExit("video-set: --anchor motion-auto requires walk/run states")
    if anchor == "motion":
        raise SystemExit("video-set: motion anchor requires reviewed regions; use video-loop --cycle fixed")
    validate_facing(facing, facing_fix)
    if facing_fix not in facing_mod.FIXES:
        raise SystemExit("video-set: --facing-fix must be mirror or none")
    item_dir = root / item
    item_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"item": item, "direction": direction, "state": state, "dir": str(item_dir)}
    try:
        prompt = build_prompt(direction, state, character, facing=facing)
        clip = item_dir / "clip.mp4"
        clip_report = item_dir / "clip.report.json"
        reuse_clip = clip.exists() and clip_report.exists() and not force
        if reuse_clip:
            try:
                previous = json.loads(clip_report.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise SystemExit("cannot verify cached clip prompt; use --force to regenerate") from exc
            if not isinstance(previous, dict) or previous.get("prompt") != prompt:
                raise SystemExit("cached clip prompt differs (including facing); use --force to regenerate")
        canvas_png = item_dir / "canvas.png"
        canvas_report_path = item_dir / "canvas.report.json"
        if reuse_clip:
            try:
                canvas_report = json.loads(canvas_report_path.read_text(encoding="utf-8"))
                if not isinstance(canvas_report, dict) or not canvas_png.is_file():
                    raise ValueError("missing canvas")
                if not all(key in canvas_report for key in ("shape", "canvas", "offset")):
                    raise ValueError("incomplete canvas report")
                if direction == "side":
                    prior = canvas_report.get("facing_check") or {}
                    if not isinstance(prior, dict):
                        raise ValueError("invalid cached facing")
                    if (prior.get("requested") != facing or prior.get("fix") != facing_fix
                            or prior.get("source_sha256") != facing_mod.digest(base)):
                        raise ValueError("unverified cached facing")
            except (OSError, ValueError) as exc:
                raise SystemExit("cannot verify cached canvas/facing; use --force to regenerate") from exc
        else:
            still = base
            facing_report = None
            if direction == "side":
                if prepare_side is not None:
                    still, facing_report = prepare_side(base)
                else:
                    still = item_dir / "facing.png"
                    facing_report = facing_mod.prepare_still(base, still, facing=facing, fix=facing_fix)
            canvas_report = canvas_mod.run_canvas(still, canvas_png, state=state, shape=shape, facing=facing if direction not in ("front", "back") else "right", headroom=None, lead=None, report_path=canvas_report_path)
            if facing_report is not None:
                canvas_report["facing_check"] = facing_report
                atomic_write_text(canvas_report_path, json.dumps(canvas_report, ensure_ascii=False, indent=2) + "\n")
        result["canvas"] = {k: canvas_report[k] for k in ("shape", "canvas", "offset")}
        if "facing_check" in canvas_report:
            result["facing"] = canvas_report["facing_check"]

        if reuse_clip:
            result["clip"] = {"reused": True}
        else:
            attempts: list[int] = []
            for attempt in range(1 + len(RETRY_BACKOFF_SECONDS)):
                _staggered_start(gap)
                rc = video_runner(canvas_png, prompt, clip, clip_report, duration=duration, resolution=resolution, log=item_dir / "clip.log")
                attempts.append(rc)
                if rc == 0 and clip.exists():
                    break
                text = (item_dir / "clip.log").read_text(encoding="utf-8", errors="replace") if (item_dir / "clip.log").exists() else ""
                if "HTTP 429" in text and attempt < len(RETRY_BACKOFF_SECONDS):
                    time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                    continue
                break
            result["clip"] = {"attempts": attempts}
            if attempts[-1] != 0 or not clip.exists():
                raise SystemExit(f"clip generation failed after {len(attempts)} attempt(s); see {item_dir / 'clip.log'}")

        fr = frames_mod.run_frames(clip, item_dir / "frames", key=key, allow_edge_contact=False, report_path=item_dir / "frames.report.json", spill=spill, reference=canvas_png)
        result["frames"] = {k: fr[k] for k in ("fps", "frames", "alpha_zero_pct_min", "alpha_zero_pct_max")}
        result["frames"]["spill"] = fr.get("spill", {}).get("mode")
        lp = loop_mod.run_loop(Path(fr["keyed_dir"]), item_dir / "loop", fps=float(fr["fps"]), state=state, min_len=None, max_len=None, n_out=None, seam_max=loop_mod.SEAM_RATIO_MAX, name=item, report_path=item_dir / "loop.report.json", anchor=anchor)
        result["loop"] = {"kind": lp["cycle"].get("kind", "periodic"), "cycle": lp["cycle"]["length"], "period": lp["cycle"]["period_global"], "cycle_ratio": round(lp["cycle"]["ratio"], 3), "seam_ratio": lp["resampled_seam_ratio"], "n_out": lp["n_out"], "drift_px": lp["strip"].get("drift_px", 0), "gif": lp["gif"]["file"], "webp": lp["webp"]["file"], "strip": lp["strip"]["path"]}
        result["loop"]["review_recommended"] = lp["cycle"].get("review_recommended", False)
        result["loop"]["half_period_guard"] = lp["cycle"].get("half_period_guard")
        if lp.get("motion_anchor", {}).get("applied") is False:
            result["loop"]["motion_anchor"] = lp["motion_anchor"]
        result["ok"] = True
    except SystemExit as exc:
        result["ok"] = False
        result["error"] = str(exc)
    return result


def write_table(results: list[dict[str, Any]], path: Path) -> str:
    lines = ["| direction | state | kind | cycle | period | seam | frames | status |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        if r.get("ok"):
            lp = r["loop"]
            status = "OK (review gait)" if lp.get("review_recommended") else "OK"
            if lp.get("motion_anchor", {}).get("applied") is False:
                status = "OK (uncorrected; review gait)"
            lines.append(f"| {r['direction']} | {r['state']} | {lp.get('kind', 'periodic')} | {lp['cycle']} | {lp['period'] if lp['period'] is not None else '-'} | {lp['seam_ratio']:.2f} | {lp['n_out']} | {status} |")
        else:
            lines.append(f"| {r['direction']} | {r['state']} | - | - | - | - | - | FAIL: {r.get('error', '')[:80]} |")
    text = "\n".join(lines) + "\n"
    atomic_write_text(path, text)
    return text


def run_set(
    *,
    bases: dict[str, Path],
    states: list[str],
    root: Path,
    character: str | None,
    duration: int,
    resolution: str,
    key: str,
    concurrency: int,
    force: bool,
    gap: float,
    video_runner: Callable[..., int] = run_video_cli,
    shape: str | None = None,
    anchor: str = "none",
    spill: str = "auto",
    facing: str = "right",
    facing_fix: str = "none",
) -> dict[str, Any]:
    if anchor == "motion-auto" and any(not loop_mod.profile_for(state).gait for state in states):
        raise SystemExit("video-set: --anchor motion-auto requires walk/run states")
    if anchor == "motion":
        raise SystemExit("video-set: motion anchor requires reviewed regions; use video-loop --cycle fixed")
    validate_facing(facing, facing_fix)
    if facing_fix not in facing_mod.FIXES:
        raise SystemExit("video-set: --facing-fix must be mirror or none")
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for direction, base in bases.items():
        if not base.is_file():
            raise SystemExit(f"video-set: base still for '{direction}' not found: {base}")
    items = [(f"{d}-{s}", d, s) for d in bases for s in states]
    results: list[dict[str, Any]] = []
    # States share a base. A run-local lock ensures exactly one vision call per
    # side still, even when several state workers reach it together.
    prepared: dict[Path, tuple[Path, dict]] = {}
    prepare_lock = threading.Lock()

    def prepare_side(base: Path) -> tuple[Path, dict]:
        with prepare_lock:
            if base not in prepared:
                corrected = root / "side.facing.png"
                report = facing_mod.prepare_still(base, corrected, facing=facing, fix=facing_fix)
                prepared[base] = (corrected, report)
            return prepared[base]

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futures = {ex.submit(run_item, item=i, direction=d, state=s, base=bases[d], root=root, character=character, duration=duration, resolution=resolution, key=key, force=force, gap=gap, video_runner=video_runner, shape=shape, anchor=anchor, spill=spill, facing=facing, facing_fix=facing_fix, prepare_side=prepare_side): i for i, d, s in items}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            print(json.dumps({k: r[k] for k in ("item", "ok") if k in r} | ({"error": r["error"]} if not r.get("ok") else {"seam": r["loop"]["seam_ratio"]}), ensure_ascii=False), flush=True)
    results.sort(key=lambda r: [i for i, _, _ in items].index(r["item"]))
    table = write_table(results, root / "table.md")
    payload = {"kind": "sprite-gen-video-set-report", "root": str(root), "states": states, "facing": facing, "facing_fix": facing_fix, "directions": list(bases), "ok": sum(1 for r in results if r.get("ok")), "failed": [r["item"] for r in results if not r.get("ok")], "items": results}
    atomic_write_text(root / "set.report.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(table)
    return payload


def _parse_bases(values: list[str]) -> dict[str, Path]:
    bases: dict[str, Path] = {}
    for v in values:
        if "=" not in v:
            raise SystemExit(f"video-set: --base expects direction=path, got {v!r}")
        d, p = v.split("=", 1)
        d = d.strip()
        if d not in VIEW_TEXT:
            raise SystemExit(f"video-set: --base direction must be one of {', '.join(sorted(VIEW_TEXT))}, got {d!r}")
        bases[d] = Path(p).expanduser().resolve()
    if not bases:
        raise SystemExit("video-set: at least one --base direction=path is required")
    return bases


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base", action="append", default=[], help="direction=still.png (repeatable: side=..., front=..., back=...)")
    parser.add_argument("--facing", choices=FACINGS, default="right", help="side-view facing for both prompt and canvas (default right); ignored for front/back")
    parser.add_argument("--facing-fix", choices=facing_mod.FIXES, default="none", help="side inputs: record only (none, default), or opt into mirror")
    parser.add_argument("--states", default="idle,walk,run,jump,attack", help="comma list of motion states")
    parser.add_argument("--out-dir", required=True, type=Path, help="batch root; one folder per direction-state")
    parser.add_argument("--character", help="short subject phrase used in the prompts (e.g. 'The armored knight')")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_SECONDS, help=f"seconds per clip (default {DEFAULT_DURATION_SECONDS}); a repeating motion holds enough cycles at 3 s and a longer clip only costs more (2026-09-18)")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--key", choices=("auto", "green", "magenta", "white"), default="auto")
    parser.add_argument("--concurrency", type=int, default=3, help="parallel clip generations (starts are staggered regardless)")
    parser.add_argument("--start-gap", type=float, default=START_GAP_SECONDS, help="seconds between clip request starts")
    parser.add_argument("--shape", choices=canvas_mod.SHAPES, help="force one canvas shape for every state (e.g. wide for a costume or arms that leave a 1:1 frame)")
    parser.add_argument("--anchor", choices=tuple(a for a in loop_mod.ANCHOR_MODES if a != "motion"), default="none", help="motion-auto: automatic regions, local period and XY correction (walk/run only); feet: remove in-canvas drift")
    parser.add_argument("--spill", choices=frames_mod.SPILL_MODES, default="auto", help="auto: judge key reflections from each item's canvas still (default); small / full: force")
    parser.add_argument("--force", action="store_true", help="regenerate clips that already exist")


def run(**kwargs: object) -> int:
    payload = run_set(
        bases=_parse_bases(list(kwargs.get("base") or [])),  # type: ignore[arg-type]
        states=[s.strip() for s in str(kwargs.get("states") or "").split(",") if s.strip()],
        root=Path(str(kwargs["out_dir"])), character=kwargs.get("character"),  # type: ignore[arg-type]
        duration=int(kwargs.get("duration") or DEFAULT_DURATION_SECONDS), resolution=str(kwargs.get("resolution") or "720p"), key=str(kwargs.get("key") or "auto"),
        concurrency=int(kwargs.get("concurrency") or 3), force=bool(kwargs.get("force")), gap=float(kwargs.get("start_gap") or START_GAP_SECONDS),
        facing=str(kwargs.get("facing") or "right"), facing_fix=str(kwargs.get("facing_fix") or "none"),
        shape=(str(kwargs["shape"]) if kwargs.get("shape") else None), anchor=str(kwargs.get("anchor") or "none"), spill=str(kwargs.get("spill") or "auto"),
    )
    return 0 if not payload["failed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-set", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
