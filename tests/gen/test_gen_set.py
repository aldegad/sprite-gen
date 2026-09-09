# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen gen-set` — the atlas pipeline's row generation as a batch command.

Contract pinned here: rows come from the prepared run (prompt + guide + identity ref),
run N at a time, every row gets a report, `table.md` names failures by stage, existing
rows are reused unless --force, a direction run generates anchors before rows and stops
when an anchor failed, and the exit code is non-zero on any failure. No provider is
called — a fake runner stands in for `sprite-gen gen`.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from PIL import Image

from conftest import run_script
from sprite_gen.gen import gen_set


FLAT_STATES = {
    "idle": {"frames": 4, "fps": 6, "loop": True, "action": "standing"},
    "walk": {"frames": 6, "fps": 8, "loop": True, "action": "walking"},
    "jump": {"frames": 5, "fps": 8, "loop": False, "action": "jumping"},
}
DIRECTION_STATES = {
    "down_walk": {"frames": 6, "fps": 8, "loop": True, "action": "walking toward the viewer"},
    "side_walk": {"frames": 6, "fps": 8, "loop": True, "action": "walking in side view"},
}


def _prepare(tmp_path: Path, *extra: str, states: dict | None = None) -> Path:
    out_dir = tmp_path / "run"
    result = run_script(
        "prepare_sprite_run.py",
        "--out-dir", str(out_dir), "--character-id", "setbot",
        "--request-json", json.dumps({"states": states or FLAT_STATES}),
        *extra,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    Image.new("RGB", (64, 64), (255, 0, 255)).save(out_dir / "base-source.png")
    return out_dir


def _fake_runner(fail_states: set[str] = frozenset(), record: list | None = None):
    lock = threading.Lock()

    def runner(prompt_file: Path, out: Path, refs: list[Path], report: Path, *, provider, model, log) -> int:
        with lock:
            if record is not None:
                record.append((out.name, [r.name for r in refs], provider))
        log.write_text("fake gen\n", encoding="utf-8")
        state = out.stem
        if state in fail_states:
            log.write_text("gen: provider refused\n", encoding="utf-8")
            return 3
        Image.new("RGB", (256, 64), (255, 0, 255)).save(out)
        report.write_text(json.dumps({"provider": provider or "codex", "provider_resolved_from": "explicit" if provider else "hard-default"}), encoding="utf-8")
        return 0

    return runner


def test_gen_set_generates_every_row_with_run_refs_and_reports(tmp_path: Path) -> None:
    run_dir = _prepare(tmp_path)
    seen: list = []
    payload = gen_set.run_set(run_dir=run_dir, states=None, provider="codex", model=None, concurrency=3, force=False, gen_runner=_fake_runner(record=seen))
    assert payload["failed"] == [] and payload["not_started"] == [] and payload["ok"] == 3
    assert sorted(n for n, _, _ in seen) == ["idle.png", "jump.png", "walk.png"]
    for _name, refs, provider in seen:
        assert refs[0] == "base-source.png" and refs[1].endswith(".png") and provider == "codex"
    for state in ("idle", "walk", "jump"):
        assert (run_dir / "raw" / f"{state}.png").is_file()
        assert (run_dir / "reports" / "gen-set" / f"{state}.json").is_file()
    table = (run_dir / "reports" / "gen-set" / "table.md").read_text(encoding="utf-8")
    assert "| walk | codex | explicit |" in table and "| status |" in table
    assert json.loads((run_dir / "reports" / "gen-set" / "set.report.json").read_text())["kind"] == "sprite-gen-gen-set-report"


def test_gen_set_reuses_existing_rows_and_force_regenerates(tmp_path: Path) -> None:
    run_dir = _prepare(tmp_path)
    gen_set.run_set(run_dir=run_dir, states=["idle"], provider=None, model=None, concurrency=1, force=False, gen_runner=_fake_runner())
    seen: list = []
    again = gen_set.run_set(run_dir=run_dir, states=["idle"], provider=None, model=None, concurrency=1, force=False, gen_runner=_fake_runner(record=seen))
    assert seen == [] and again["items"][0]["reused"] is True
    assert "| idle | - | - | - | reused |" in (run_dir / "reports" / "gen-set" / "table.md").read_text()
    forced = gen_set.run_set(run_dir=run_dir, states=["idle"], provider=None, model=None, concurrency=1, force=True, gen_runner=_fake_runner(record=seen))
    assert len(seen) == 1 and forced["items"][0].get("reused") is None


def test_gen_set_names_failures_and_exits_non_zero(tmp_path: Path, monkeypatch, capsys) -> None:
    run_dir = _prepare(tmp_path)
    monkeypatch.setattr(gen_set, "run_gen_cli", _fake_runner(fail_states={"walk"}))
    rc = gen_set.run(run_dir=run_dir, states=None, provider="grok", model=None, concurrency=2, force=False)
    assert rc == 1
    table = (run_dir / "reports" / "gen-set" / "table.md").read_text()
    assert "| walk | - | - |" in table and "FAIL: gen exited 3" in table
    assert not (run_dir / "raw" / "walk.png").exists() and (run_dir / "raw" / "idle.png").is_file()


def test_gen_set_refuses_unknown_state_and_unprepared_dir(tmp_path: Path) -> None:
    run_dir = _prepare(tmp_path)
    with pytest.raises(SystemExit, match="not in the request"):
        gen_set.run_set(run_dir=run_dir, states=["fly"], provider=None, model=None, concurrency=1, force=False, gen_runner=_fake_runner())
    with pytest.raises(SystemExit, match="not a prepared run"):
        gen_set.run_set(run_dir=tmp_path / "nowhere", states=None, provider=None, model=None, concurrency=1, force=False, gen_runner=_fake_runner())


def test_gen_set_direction_run_generates_anchors_first_and_stops_on_anchor_failure(tmp_path: Path) -> None:
    run_dir = _prepare(tmp_path, "--directions", "down,side", "--mirror", "left=side", states=DIRECTION_STATES)
    order: list = []
    payload = gen_set.run_set(run_dir=run_dir, states=None, provider="codex", model=None, concurrency=4, force=False, gen_runner=_fake_runner(fail_states={"idle"}, record=order))
    # stage 1 = the two direction anchors (down_idle, side_idle -> raw/<dir>/idle.png); both fail here
    assert sorted(n for n, _, _ in order) == ["idle.png", "idle.png"]
    assert sorted(payload["failed"]) == ["down_idle", "side_idle"]
    # stage 2 rows were not started — the base is never re-attached in place of a missing anchor
    assert payload["not_started"] and all("_idle" not in s for s in payload["not_started"])
    assert not any(s.startswith("left_") for s in payload["states"]), "mirrored directions are skipped by contract"


def test_run_gen_cli_invokes_the_cli_gen_verb(tmp_path: Path, monkeypatch) -> None:
    """The row call is `python -m sprite_gen.cli gen …` — the package has no __main__, so
    `-m sprite_gen.gen` is not a runnable module (2026-09-09 real-run regression)."""
    import subprocess

    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        class R:  # noqa: D401 — minimal CompletedProcess stand-in
            returncode = 0
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    log = tmp_path / "x.log"
    rc = gen_set.run_gen_cli(tmp_path / "p.txt", tmp_path / "o.png", [tmp_path / "a.png", tmp_path / "b.png"], tmp_path / "r.json", provider="codex", model=None, log=log)
    assert rc == 0
    cmd = captured["cmd"]
    assert cmd[1:4] == ["-m", "sprite_gen.cli", "gen"]
    assert cmd.count("--ref") == 2 and "--provider" in cmd and "--model" not in cmd


def _broken_report_runner(record: list | None = None):
    def runner(prompt_file: Path, out: Path, refs: list[Path], report: Path, *, provider, model, log) -> int:
        if record is not None:
            record.append(out.name)
        log.write_text("fake gen\n", encoding="utf-8")
        Image.new("RGB", (256, 64), (255, 0, 255)).save(out)
        report.write_text("{not json", encoding="utf-8")
        return 0
    return runner


def test_gen_set_broken_report_is_a_named_failure_and_never_reused(tmp_path: Path) -> None:
    """Validator finding 2026-09-09: a PNG plus an unreadable report used to crash the batch
    (JSONDecodeError, no table) and the next run reused the row on file existence alone."""
    run_dir = _prepare(tmp_path)
    first = gen_set.run_set(run_dir=run_dir, states=["idle"], provider="codex", model=None, concurrency=1, force=False, gen_runner=_broken_report_runner())
    assert first["failed"] == ["idle"] and "report unreadable" in first["items"][0]["error"]
    assert not (run_dir / "raw" / "idle.png").exists(), "no half state: the unproven row is removed"
    assert "FAIL: gen exited 0 but its report unreadable" in (run_dir / "reports" / "gen-set" / "table.md").read_text()
    # second run must generate again, not reuse
    calls: list = []
    second = gen_set.run_set(run_dir=run_dir, states=["idle"], provider="codex", model=None, concurrency=1, force=False, gen_runner=_fake_runner(record=calls))
    assert calls == [("idle.png", ["base-source.png", "idle.png"], "codex")] and second["ok"] == 1 and second["items"][0].get("reused") is None


def test_gen_set_row_without_a_real_report_is_regenerated_with_a_reason(tmp_path: Path) -> None:
    run_dir = _prepare(tmp_path)
    (run_dir / "raw").mkdir(exist_ok=True)
    Image.new("RGB", (256, 64), (255, 0, 255)).save(run_dir / "raw" / "walk.png")  # a row image from nowhere, no report
    calls: list = []
    payload = gen_set.run_set(run_dir=run_dir, states=["walk"], provider="codex", model=None, concurrency=1, force=False, gen_runner=_fake_runner(record=calls))
    item = payload["items"][0]
    assert len(calls) == 1 and item["ok"] and item.get("reused") is None and item["regenerated_because"] == "report missing"
