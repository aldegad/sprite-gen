# SPDX-License-Identifier: Apache-2.0
"""Run a bounded inspect -> score -> correction-hint loop."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sprite_gen.util.cli_kwargs import namespace_from_kwargs
from sprite_gen.qa import inspect as inspect_mod
from sprite_gen.qa import score as score_mod
from sprite_gen.spec.runio import LOCK_FILENAME, REQUEST_FILENAME, atomic_write_text, refuse_overlap, relative_posix, same_folder
from sprite_gen.util.console import utf8_streams

BEST_CANDIDATE = "best-candidate"
REPORT = "correction-loop.report.json"


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


def _skipped_by_copy(out_dir: Path) -> Callable[[Path], bool]:
    """What the best candidate's copy of a run dir leaves out: its lock, `__pycache__`, and the loop's out dir where
    it lies in the run dir, as the default one does (the copy would copy itself into itself). A link to the out dir
    is not left out: it is an input that reaches the loop's own output, refused before the loop starts."""
    return lambda path: (path.name in (LOCK_FILENAME, "__pycache__")
                         or (not path.is_symlink() and same_folder(path, out_dir)))


def _copied(source: Path, skipped: Callable[[Path], bool]) -> list[Path]:
    """Every entry the copy of `source` reads, as found there: each file, folder and link (`shutil.copytree` reads a
    linked file's bytes and a linked folder's content), the linked folders walked too."""
    found: list[Path] = []
    todo, walked = [source], set()
    while todo:
        with os.scandir(todo.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                if skipped(path):
                    continue
                found.append(path)
                if entry.is_dir():
                    folder = entry.stat()
                    if (folder.st_dev, folder.st_ino) not in walked:  # a folder linked twice, or a link loop, once
                        walked.add((folder.st_dev, folder.st_ino))
                        todo.append(path)
    return sorted(found)


def _attempt(out_dir: Path, attempt: int) -> dict[str, Path]:
    """What attempt `attempt` writes in the out dir: its folder and, in it, the inspection, the score, the hints and the
    provider's log (written when a provider runs after the attempt)."""
    folder = out_dir / f"attempt-{attempt}"
    return {"folder": folder, "inspect": folder / "inspect.json", "score": folder / "score.json",
            "hints": folder / "correction-hints.txt", "log": folder / "provider.log"}


def _candidate(out_dir: Path, attempt: int) -> Path:
    """Where the provider makes the run dir attempt `attempt` inspects."""
    return out_dir / f"candidate-{attempt}"


def _written(out_dir: Path, *, passes: int, preserve_best: bool) -> list[Path]:
    """Every entry the loop writes in the out dir, at the path it writes: each attempt's folder and its files, each
    candidate the provider makes (a loop of more than one pass runs a provider), the best candidate's copy and the
    report."""
    written: list[Path] = []
    for attempt in range(1, passes + 1):
        files = _attempt(out_dir, attempt)
        written += [path for key, path in files.items() if key != "log" or attempt < passes]
        if attempt > 1:
            written.append(_candidate(out_dir, attempt))
    return [*written, *([out_dir / BEST_CANDIDATE] if preserve_best else []), out_dir / REPORT]


def _take_away(path: Path) -> None:
    """Remove what an earlier loop left at `path`, one of the loop's own outputs, never following a link: a link or a
    file is unlinked, a folder removed with what it holds (`shutil.rmtree` unlinks the links in it)."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _copy_run_dir(source: Path, target: Path, skipped: Callable[[Path], bool]) -> None:
    _take_away(target)

    def ignore(folder: str, names: list[str]) -> set[str]:
        return {name for name in names if skipped(Path(folder) / name)}

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
    given = run_dir
    run_dir = run_dir.expanduser().resolve()
    out_dir = (out_dir or (run_dir / "correction-loop")).expanduser().resolve()
    # The loop never writes over, moves or deletes the run it reads: the run dir, and every entry its best candidate's
    # copy reads there (each with every link or folder its path looks up), is never anything the loop writes — the out
    # dir and each entry the loop writes in it, at the path it writes, a link already there followed — nor inside one.
    # The out dir may lie in the run dir (the default does): it is added to it, and the copy leaves it out.
    skipped = _skipped_by_copy(out_dir)
    found = _copied(run_dir, skipped) if run_dir.is_dir() else []
    written = _written(out_dir, passes=max_passes if provider_command and not dry_run else 1, preserve_best=preserve_best)
    refuse_overlap({"--run-dir": given, **{f"{relative_posix(p, run_dir)} in --run-dir": p for p in found}},
                   {"--out-dir": out_dir, **{f"{relative_posix(p, out_dir)} in --out-dir": p for p in written}},
                   replaces="the loop writes its attempts and best candidate there")
    out_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    current = run_dir

    for attempt in range(1, max_passes + 1):
        files = _attempt(out_dir, attempt)
        files["folder"].mkdir(parents=True, exist_ok=True)
        inspect_report = inspect_mod.inspect_run(current, states=states)
        scored = score_mod.score_inspection(inspect_report)
        atomic_write_text(files["inspect"], json.dumps(inspect_report, ensure_ascii=False, indent=2) + "\n")
        atomic_write_text(files["score"], json.dumps(scored, ensure_ascii=False, indent=2) + "\n")
        _write_hints(files["hints"], scored["hints"])
        record = {
            "attempt": attempt,
            "run_dir": str(current),
            "inspect_report": str(files["inspect"]),
            "score_report": str(files["score"]),
            "hint_file": str(files["hints"]),
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
        next_run_dir = _candidate(out_dir, attempt + 1)
        # The provider makes the run dir there: an earlier loop's candidate is taken away first, so nothing it held (a
        # link into the run among it) is written through.
        _take_away(next_run_dir)
        prompt_file = files["hints"]
        # The template is split first and each argument filled in after, so a substituted
        # path is never re-read as shell syntax: POSIX splitting would drop the backslashes
        # of a Windows path and break a path that holds a space or a quote.
        argv = [arg.format(prompt_file=str(prompt_file), next_run_dir=str(next_run_dir), attempt=attempt + 1)
                for arg in shlex.split(provider_command)]
        command = shlex.join(argv)
        # The provider meant is a sprite-gen command (docs/gen.md), which writes UTF-8. Its output only goes to the
        # log, so another command's byte that is not UTF-8 is replaced there rather than end the loop.
        completed = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
        atomic_write_text(
            files["log"],
            completed.stdout + ("\n--- stderr ---\n" + completed.stderr if completed.stderr else ""),
        )
        if completed.returncode != 0:
            raise SystemExit(f"provider command failed with exit {completed.returncode}: {command}")
        if not (next_run_dir / REQUEST_FILENAME).is_file():
            raise SystemExit(f"provider command did not create a sprite-gen run dir: {next_run_dir}")
        current = next_run_dir

    assert best is not None
    preserved_best = None
    if preserve_best:
        preserved = out_dir / BEST_CANDIDATE
        _copy_run_dir(Path(best["run_dir"]), preserved, skipped)
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
    atomic_write_text(out_dir / REPORT, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
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
    args = namespace_from_kwargs(_build_parser(), kwargs)
    return _run(args)


def main() -> int:
    utf8_streams()
    return _run(_build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
