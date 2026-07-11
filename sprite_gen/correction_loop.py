# SPDX-License-Identifier: Apache-2.0
"""Run a bounded inspect -> score -> correction-hint loop."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from sprite_gen import inspect as inspect_mod
from sprite_gen import score as score_mod
from sprite_gen.runio import atomic_write_text


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--states", default="all")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--max-passes", type=int, default=3)
    parser.add_argument("--min-attempts", type=int, default=1)
    parser.add_argument("--pass-score", type=float, default=90.0)
    parser.add_argument("--provider-command", help="optional command template; {prompt_file}, {next_run_dir}, {attempt}")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-preserve-best", action="store_true")
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


def _copy_run_dir(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name == ".sprite-gen.lock" or name == "__pycache__"}

    shutil.copytree(source, target, ignore=ignore)


def _write_hints(path: Path, hints: list[str]) -> None:
    body = "\n".join(f"- {hint}" for hint in hints) if hints else "- No correction hint; candidate passed."
    atomic_write_text(path, body + "\n")


def run_loop(
    run_dir: Path,
    *,
    states: str = "all",
    out_dir: Path | None = None,
    max_passes: int = 3,
    min_attempts: int = 1,
    pass_score: float = 90.0,
    provider_command: str | None = None,
    dry_run: bool = False,
    preserve_best: bool = True,
) -> dict[str, Any]:
    if max_passes < 1:
        raise SystemExit("--max-passes must be at least 1")
    if min_attempts < 1 or min_attempts > max_passes:
        raise SystemExit("--min-attempts must be between 1 and --max-passes")
    run_dir = run_dir.expanduser().resolve()
    out_dir = (out_dir or (run_dir / "correction-loop")).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    current = run_dir

    for attempt in range(1, max_passes + 1):
        attempt_dir = out_dir / f"attempt-{attempt}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        inspect_report = inspect_mod.inspect_run(current, states=states)
        scored = score_mod.score_inspection(inspect_report)
        atomic_write_text(attempt_dir / "inspect.json", json.dumps(inspect_report, ensure_ascii=False, indent=2) + "\n")
        atomic_write_text(attempt_dir / "score.json", json.dumps(scored, ensure_ascii=False, indent=2) + "\n")
        _write_hints(attempt_dir / "correction-hints.txt", scored["hints"])
        record = {
            "attempt": attempt,
            "run_dir": str(current),
            "inspect_report": str(attempt_dir / "inspect.json"),
            "score_report": str(attempt_dir / "score.json"),
            "hint_file": str(attempt_dir / "correction-hints.txt"),
            "ok": scored["ok"],
            "overall_score": scored["overall_score"],
            "candidate_rank": scored["candidate_rank"],
        }
        attempts.append(record)
        if best is None or (scored["candidate_rank"], scored["overall_score"]) > (
            best["candidate_rank"],
            best["overall_score"],
        ):
            best = record
        if attempt >= min_attempts and scored["ok"] and float(scored["overall_score"]) >= pass_score:
            break
        if attempt == max_passes:
            break
        if dry_run:
            break
        if not provider_command:
            raise SystemExit("correction loop needs --provider-command, or use --dry-run for an inspect/score/hint demo")
        next_run_dir = out_dir / f"candidate-{attempt + 1}"
        prompt_file = attempt_dir / "correction-hints.txt"
        command = provider_command.format(
            prompt_file=str(prompt_file),
            next_run_dir=str(next_run_dir),
            attempt=attempt + 1,
        )
        completed = subprocess.run(shlex.split(command), capture_output=True, text=True)
        atomic_write_text(
            attempt_dir / "provider.log",
            completed.stdout + ("\n--- stderr ---\n" + completed.stderr if completed.stderr else ""),
        )
        if completed.returncode != 0:
            raise SystemExit(f"provider command failed with exit {completed.returncode}: {command}")
        if not (next_run_dir / "sprite-request.json").is_file():
            raise SystemExit(f"provider command did not create a sprite-gen run dir: {next_run_dir}")
        current = next_run_dir

    assert best is not None
    preserved_best = None
    if preserve_best:
        preserved = out_dir / "best-candidate"
        _copy_run_dir(Path(best["run_dir"]), preserved)
        preserved_best = str(preserved)
    result = {
        "ok": bool(attempts and attempts[-1]["ok"] and float(attempts[-1]["overall_score"]) >= pass_score),
        "kind": "sprite-gen-correction-loop-report",
        "dry_run": dry_run,
        "min_attempts": min_attempts,
        "max_passes": max_passes,
        "attempts": attempts,
        "best_candidate": best,
        "preserved_best": preserved_best,
    }
    atomic_write_text(out_dir / "correction-loop.report.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def _run(args: argparse.Namespace) -> int:
    result = run_loop(
        args.run_dir,
        states=args.states,
        out_dir=args.out_dir,
        max_passes=args.max_passes,
        min_attempts=args.min_attempts,
        pass_score=args.pass_score,
        provider_command=args.provider_command,
        dry_run=args.dry_run,
        preserve_best=not args.no_preserve_best,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "attempts"}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def run(**kwargs: object) -> int:
    args = _namespace_from_kwargs(**kwargs)
    return _run(args)


def main() -> int:
    return _run(_build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
