# SPDX-License-Identifier: Apache-2.0
"""장기 heal 의 비차단 계약 (plan sprite-gen/long-op-loading-ux).

엔진 갱신 후 첫 /api/run 이 heal 을 요청 안에서 동기로 돌리면 첫 탭이 진행률
UI 없이 빈 화면으로 수십 분 멈춘다 (실사고 2026-07-20). maybe_heal 은 heal 을
백그라운드로 발사하고 그레이스만 대기한다: 장기 heal 은 즉시 busy, 신선 케이스는
기존과 같은 동기 경로, heal 리포트는 유실 없이 다음 호출에 1회 첨부된다.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import serve_curation  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_heal_state():
    serve_curation._heal_state["thread"] = None
    serve_curation._heal_state["pending_report"] = None
    yield
    thread = serve_curation._heal_state["thread"]
    if thread is not None:
        thread.join(timeout=10)
    serve_curation._heal_state["thread"] = None
    serve_curation._heal_state["pending_report"] = None


def test_long_heal_returns_busy_within_grace(tmp_path: Path, monkeypatch) -> None:
    release = {"at": time.monotonic() + 4.0}

    def slow_heal(run_dir):
        while time.monotonic() < release["at"]:
            time.sleep(0.05)
        return {"healed": ["walk"], "kept_stale": [], "failed": [], "notes": []}

    monkeypatch.setattr(serve_curation, "heal_run", slow_heal)
    started = time.monotonic()
    busy, report = serve_curation.maybe_heal(tmp_path)
    elapsed = time.monotonic() - started
    assert busy is True and report is None
    # 그레이스(1.5s) + 여유 안에 응답해야 첫 탭이 로딩 UI 를 본다
    assert elapsed < serve_curation._HEAL_GRACE_SECONDS + 1.0

    # heal 완료 후: busy 해제 + 리포트가 유실 없이 1회 첨부된다
    serve_curation._heal_state["thread"].join(timeout=10)
    busy2, report2 = serve_curation.maybe_heal(tmp_path)
    assert busy2 is False
    assert report2 is not None and report2["healed"] == ["walk"]
    # 두 번째 소비에서는 사라진다 (1회 첨부) — 단 이 호출이 새 heal 을 발사하므로
    # no-op 헬로 대체해 검사한다
    monkeypatch.setattr(serve_curation, "heal_run", lambda run_dir: {
        "healed": [], "kept_stale": [], "failed": [], "notes": []})
    busy3, report3 = serve_curation.maybe_heal(tmp_path)
    assert busy3 is False and report3 is None


def test_fresh_heal_stays_synchronous(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(serve_curation, "heal_run", lambda run_dir: {
        "healed": [], "kept_stale": [], "failed": [], "notes": []})
    started = time.monotonic()
    busy, report = serve_curation.maybe_heal(tmp_path)
    assert busy is False and report is None  # no-op 리포트는 첨부하지 않는다 (기존 계약)
    assert time.monotonic() - started < 1.0


def test_heal_failure_is_observable_not_fatal(tmp_path: Path, monkeypatch) -> None:
    def broken_heal(run_dir):
        raise SystemExit("raw missing")

    monkeypatch.setattr(serve_curation, "heal_run", broken_heal)
    busy, report = serve_curation.maybe_heal(tmp_path)
    if busy:  # 스케줄링상 그레이스를 넘겼으면 완주 후 재조회
        thread = serve_curation._heal_state["thread"]
        if thread is not None:
            thread.join(timeout=10)
        with serve_curation._heal_lock:
            report = serve_curation._heal_state["pending_report"]
    assert report is not None
    assert any("heal skipped" in note for note in report["notes"])