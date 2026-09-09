# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen gen-set` — generate every state row of a prepared run, N at a time.

The atlas pipeline's one AI step, as a command instead of prose. `prepare` already
wrote the numeric request, one prompt per state and one layout guide per state; this
runs `gen` for each state with the identity ref the run declares (`base-source.*`, or
the accepted direction anchor for a direction-contract action row — `sprite_gen.curate.anchor`
owns that choice) and writes `raw/<state>.png` where `extract` expects it.

Same contract as `video-set`: items are idempotent (an existing row is reused unless
`--force`), every item gets its own report, `table.md` names failures by stage, and the
exit code is non-zero when any item failed. There is no fallback provider: `--provider`
is honoured verbatim and the unspecified case resolves exactly like `gen` does
(`SPRITE_GEN_DEFAULT_PROVIDER` > codex, with the same observable codex→grok
availability failover recorded per item).

Direction-contract runs follow the run's own `references/generation-plan.json`: stage 1
(direction anchors, base-referenced) must be generated, extracted and an anchor frame
accepted before stage 2 rows can attach it, so a stage-2 row whose anchor is not
resolvable fails by name instead of quietly re-attaching the base.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from sprite_gen import gen as gen_mod
from sprite_gen.curate import anchor as anchor_mod
from sprite_gen.spec import layout
from sprite_gen.spec.runio import atomic_write_text

DEFAULT_CONCURRENCY = 6  # lead-verified 2026-08 (no provider throttling at 6-wide); serial one-by-one is the anti-pattern
REPORT_DIR = "reports"  # <run>/reports/gen-set/<state>.json — outside raw/ and frames/, the run contract's derived areas


def _row_states(request: dict[str, Any], wanted: list[str] | None) -> list[str]:
    """States to generate, in generation-plan order (anchors before rows), minus mirrored directions."""
    states = list(request.get("states") or {})
    if not states:
        raise SystemExit("gen-set: the request declares no states")
    directions = request.get("directions")
    mirrored = set((directions or {}).get("mirror") or {})
    ordered: list[str] = []
    if directions:
        anchors = [anchor_mod.anchor_state(request, d) for d in anchor_mod.directions(request)]
        ordered = [s for s in anchors if s in states] + [s for s in states if s not in anchors]
    else:
        ordered = states
    ordered = [s for s in ordered if not any(s.startswith(m + "_") for m in mirrored)]
    if wanted:
        unknown = [s for s in wanted if s not in states]
        if unknown:
            raise SystemExit(f"gen-set: state(s) not in the request: {', '.join(unknown)} (have: {', '.join(states)})")
        ordered = [s for s in ordered if s in wanted]
    return ordered


def _stages(request: dict[str, Any], states: list[str]) -> list[list[str]]:
    """Direction runs: anchors first (parallel), then rows. Flat runs: one stage."""
    directions = request.get("directions")
    if not directions:
        return [states]
    anchors = {anchor_mod.anchor_state(request, d) for d in anchor_mod.directions(request)}
    first = [s for s in states if s in anchors]
    second = [s for s in states if s not in anchors]
    return [stage for stage in (first, second) if stage]


def read_report(report: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a row's gen report. Returns (report, None) or (None, why) — a missing,
    unreadable or provider-less report is never treated as a finished row."""
    if not report.is_file():
        return None, "report missing"
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"report unreadable ({exc.__class__.__name__})"
    if not isinstance(data, dict) or not data.get("provider"):
        return None, "report has no provider — not a finished gen report"
    return data, None


def run_gen_cli(prompt_file: Path, out: Path, refs: list[Path], report: Path, *, provider: str | None, model: str | None, log: Path) -> int:
    import subprocess

    cmd = [sys.executable, "-m", "sprite_gen.cli", "gen", "--prompt-file", str(prompt_file), "--out", str(out), "--report", str(report)]
    for ref in refs:
        cmd += ["--ref", str(ref)]
    if provider:
        cmd += ["--provider", provider]
    if model:
        cmd += ["--model", model]
    with log.open("w", encoding="utf-8") as fh:
        return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True).returncode


def run_item(
    *,
    run_dir: Path,
    request: dict[str, Any],
    state: str,
    provider: str | None,
    model: str | None,
    force: bool,
    gen_runner: Callable[..., int] | None = None,
) -> dict[str, Any]:
    gen_runner = gen_runner or run_gen_cli  # late-bound so a test can stand in for the provider call
    out = run_dir / layout.raw_rel(request, state)
    prompt_file = run_dir / layout.prompt_rel(request, state)
    guide = run_dir / layout.guide_rel(request, state)
    report_dir = run_dir / REPORT_DIR / "gen-set"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"{state.replace('/', '_')}.json"
    log = report_dir / f"{state.replace('/', '_')}.log"
    result: dict[str, Any] = {"state": state, "out": str(out), "report": str(report)}
    started = time.monotonic()
    try:
        if not prompt_file.is_file():
            raise SystemExit(f"prompt missing: {prompt_file} — run `sprite-gen prepare` first")
        if not guide.is_file():
            raise SystemExit(f"layout guide missing: {guide} — run `sprite-gen prepare` first")
        if out.is_file() and not force:
            prior, why = read_report(report)
            if prior is not None:
                result["reused"] = True
                result["provider"] = prior.get("provider")
                result["provider_resolved_from"] = prior.get("provider_resolved_from")
                result["ok"] = True
                return result
            result["regenerated_because"] = why  # the row image exists but its report does not prove it — generate again, say why
        identity = anchor_mod.identity_ref(run_dir, state, request, quiet=True)
        refs = [identity, guide]
        result["refs"] = [str(r) for r in refs]
        out.parent.mkdir(parents=True, exist_ok=True)
        rc = gen_runner(prompt_file, out, refs, report, provider=provider, model=model, log=log)
        if rc != 0 or not out.is_file():
            tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:] if log.exists() else []
            raise SystemExit(f"gen exited {rc}; " + (" | ".join(tail) if tail else f"see {log}"))
        gen_report, why = read_report(report)
        if gen_report is None:
            for partial in (out, report):
                if partial.exists():
                    partial.unlink()
            raise SystemExit(f"gen exited 0 but its {why}; the row and the report were removed so the next run regenerates")
        result["provider"] = gen_report.get("provider")
        result["provider_resolved_from"] = gen_report.get("provider_resolved_from")
        if gen_report.get("provider_fallback"):
            result["provider_fallback"] = gen_report["provider_fallback"]
        result["elapsed_seconds"] = round(time.monotonic() - started, 1)
        result["ok"] = True
    except SystemExit as exc:
        result["ok"] = False
        result["error"] = str(exc)
        result["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return result


def write_table(results: list[dict[str, Any]], path: Path) -> str:
    lines = ["| state | provider | resolved from | seconds | status |", "|---|---|---|---|---|"]
    for r in results:
        if r.get("ok") and r.get("reused"):
            lines.append(f"| {r['state']} | - | - | - | reused |")
        elif r.get("ok"):
            lines.append(f"| {r['state']} | {r.get('provider') or '-'} | {r.get('provider_resolved_from') or '-'} | {r.get('elapsed_seconds', '-')} | OK |")
        else:
            lines.append(f"| {r['state']} | - | - | {r.get('elapsed_seconds', '-')} | FAIL: {r.get('error', '')[:100]} |")
    text = "\n".join(lines) + "\n"
    atomic_write_text(path, text)
    return text


def run_set(
    *,
    run_dir: Path,
    states: list[str] | None,
    provider: str | None,
    model: str | None,
    concurrency: int,
    force: bool,
    gen_runner: Callable[..., int] | None = None,
) -> dict[str, Any]:
    gen_runner = gen_runner or run_gen_cli
    run_dir = run_dir.expanduser().resolve()
    if not (run_dir / "sprite-request.json").is_file():
        raise SystemExit(f"gen-set: {run_dir} is not a prepared run (no sprite-request.json) — run `sprite-gen prepare` first")
    request = anchor_mod.load_request(run_dir)
    ordered = _row_states(request, states)
    results: list[dict[str, Any]] = []
    for stage in _stages(request, ordered):
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            futures = {ex.submit(run_item, run_dir=run_dir, request=request, state=s, provider=provider, model=model, force=force, gen_runner=gen_runner): s for s in stage}
            for fut in as_completed(futures):
                r = fut.result()
                results.append(r)
                print(json.dumps({k: r[k] for k in ("state", "ok") if k in r} | ({"error": r["error"]} if not r.get("ok") else {}), ensure_ascii=False), flush=True)
        if any(not r.get("ok") for r in results if r["state"] in stage) and len(_stages(request, ordered)) > 1:
            # a failed anchor stage cannot feed stage 2 — stop here and say so, do not attach the base instead
            break
    results.sort(key=lambda r: ordered.index(r["state"]))
    report_dir = run_dir / REPORT_DIR / "gen-set"
    report_dir.mkdir(parents=True, exist_ok=True)
    table = write_table(results, report_dir / "table.md")
    skipped = [s for s in ordered if s not in {r["state"] for r in results}]
    payload = {
        "kind": "sprite-gen-gen-set-report",
        "run_dir": str(run_dir),
        "states": ordered,
        "ok": sum(1 for r in results if r.get("ok")),
        "failed": [r["state"] for r in results if not r.get("ok")],
        "not_started": skipped,
        "items": results,
    }
    atomic_write_text(report_dir / "set.report.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(table)
    return payload


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True, type=Path, help="a prepared run (sprite-request.json, prompts/, references/layout-guides/)")
    parser.add_argument("--states", help="comma list; default = every non-mirrored state in the request")
    parser.add_argument("--provider", choices=("codex", "grok"), help="honoured verbatim; default resolves like `gen` (env > codex, observable failover)")
    parser.add_argument("--model")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help=f"rows generated at once (default {DEFAULT_CONCURRENCY})")
    parser.add_argument("--force", action="store_true", help="regenerate rows that already exist")


def run(**kwargs: object) -> int:
    states = [s.strip() for s in str(kwargs.get("states") or "").split(",") if s.strip()] or None
    payload = run_set(
        run_dir=Path(str(kwargs["run_dir"])), states=states,
        provider=kwargs.get("provider"), model=kwargs.get("model"),  # type: ignore[arg-type]
        concurrency=int(kwargs.get("concurrency") or DEFAULT_CONCURRENCY), force=bool(kwargs.get("force")),
    )
    return 0 if not payload["failed"] and not payload["not_started"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen gen-set", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
