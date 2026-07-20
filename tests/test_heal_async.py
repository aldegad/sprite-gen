# SPDX-License-Identifier: Apache-2.0
"""장기 heal 의 비차단 계약 (plan sprite-gen/long-op-loading-ux).

엔진 갱신 후 첫 /api/run 이 heal 을 요청 안에서 동기로 돌리면 첫 탭이 진행률
UI 없이 빈 화면으로 수십 분 멈춘다 (실사고 2026-07-20). maybe_heal 은 heal 을
백그라운드로 발사하고 그레이스만 대기하며 busy 여부만 반환한다. heal 리포트의
소비자는 take_heal_report() 하나 — /api/run 성공 스냅샷 경로 — 뿐이다.
/api/progress 폴링·다운로드가 heal 완료 직후 먼저 호출돼도 리포트가 유실되지
않는다 (validator kongkongi 재현 결함의 회귀 가드, 단일 소비자 원칙).
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


def _walk_report(run_dir):
    return {"healed": ["walk"], "kept_stale": [], "failed": [], "notes": []}


def _noop_report(run_dir):
    return {"healed": [], "kept_stale": [], "failed": [], "notes": []}


def test_long_heal_returns_busy_within_grace(tmp_path: Path, monkeypatch) -> None:
    release = {"at": time.monotonic() + 4.0}

    def slow_heal(run_dir):
        while time.monotonic() < release["at"]:
            time.sleep(0.05)
        return _walk_report(run_dir)

    monkeypatch.setattr(serve_curation, "heal_run", slow_heal)
    started = time.monotonic()
    busy = serve_curation.maybe_heal(tmp_path)
    elapsed = time.monotonic() - started
    assert busy is True
    # 그레이스(1.5s) + 여유 안에 응답해야 첫 탭이 로딩 UI 를 본다
    assert elapsed < serve_curation._HEAL_GRACE_SECONDS + 1.0

    # heal 완료 후: busy 해제 + 리포트가 단일 소비자에서 1회 나온다
    serve_curation._heal_state["thread"].join(timeout=10)
    monkeypatch.setattr(serve_curation, "heal_run", _noop_report)
    assert serve_curation.maybe_heal(tmp_path) is False
    report = serve_curation.take_heal_report()
    assert report is not None and report["healed"] == ["walk"]
    assert serve_curation.take_heal_report() is None  # 정확히 1회


def test_progress_poll_never_steals_the_heal_report(tmp_path: Path, monkeypatch) -> None:
    """validator kongkongi 재현 결함의 회귀 가드: 장기 heal 완료 직후
    /api/progress(또는 다운로드) 역할의 maybe_heal 이 먼저 호출돼도 리포트는
    남아 있어야 하고, /api/run 역할의 take_heal_report 가 정확히 1회 가져간다."""
    release = {"at": time.monotonic() + 1.7}

    def slow_heal(run_dir):
        while time.monotonic() < release["at"]:
            time.sleep(0.05)
        return _walk_report(run_dir)

    monkeypatch.setattr(serve_curation, "heal_run", slow_heal)
    assert serve_curation.maybe_heal(tmp_path) is True  # 장기 heal — busy 즉답
    serve_curation._heal_state["thread"].join(timeout=10)

    # 완료 직후: 트리 폴링/다운로드 경로가 먼저 도착하는 시나리오
    monkeypatch.setattr(serve_curation, "heal_run", _noop_report)
    assert serve_curation.maybe_heal(tmp_path) is False  # /api/progress 역할 — 미소비
    assert serve_curation.maybe_heal(tmp_path) is False  # download 역할 — 미소비

    # 그 다음 /api/run 성공 경로가 리포트를 정확히 1회 가져간다
    report = serve_curation.take_heal_report()
    assert report is not None and report["healed"] == ["walk"]
    assert serve_curation.take_heal_report() is None


def test_fresh_heal_stays_synchronous(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(serve_curation, "heal_run", _noop_report)
    started = time.monotonic()
    busy = serve_curation.maybe_heal(tmp_path)
    assert busy is False
    assert time.monotonic() - started < 1.0
    assert serve_curation.take_heal_report() is None  # no-op 리포트는 첨부하지 않는다


def test_heal_failure_is_observable_not_fatal(tmp_path: Path, monkeypatch) -> None:
    def broken_heal(run_dir):
        raise SystemExit("raw missing")

    monkeypatch.setattr(serve_curation, "heal_run", broken_heal)
    busy = serve_curation.maybe_heal(tmp_path)
    if busy:  # 스케줄링상 그레이스를 넘겼으면 완주 대기
        thread = serve_curation._heal_state["thread"]
        if thread is not None:
            thread.join(timeout=10)
    report = serve_curation.take_heal_report()
    assert report is not None
    assert any("heal skipped" in note for note in report["notes"])