# SPDX-License-Identifier: Apache-2.0
"""은퇴한 `pixel_perfect` 키 -> 현행 `pixel_unfake` 이관 (plan sprite-gen/pixel-unfake-rename).

용어 근거 (vault `domains/tools/spritefusion-pixel-snapper.md` 각주): "픽셀 퍼펙트" 는 광의
통용어(UI 정합·충돌판정)의 오용이고, 이 파이프라인이 하는 일의 정확한 명칭은 격자 스냅 /
재양자화 — 커뮤니티 속칭 **unfake** (unfake.js 유래). 어휘를 코드·스키마까지 통일하되, 기존
런(solvell 94개)은 일회성 이관으로 무손실 통과해야 한다.

이관 계약: 구키만 있으면 신키로 재기록 + loud 로그 · 두 키 동시 = hard fail · 신키만 있으면
무동작 (멱등).
"""

from __future__ import annotations

import json

import pytest

from sprite_gen.curation import (CURATION_FILENAME, frame_variant, load_curation_report,
                                 run_revision)
from sprite_gen.runio import load_request


def _write_request(run_dir, fit: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "sprite-request.json").write_text(json.dumps({
        "version": 1, "kind": "sprite-gen-request", "engine": "component-row",
        "character": {"id": "migbot", "description": "", "base_image": None},
        "cell": {"shape": "square", "width": 96, "height": 96, "safe_margin_x": 8,
                 "safe_margin_y": 8, "size": 96, "safe_margin": 8},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255],
                       "selection": "fallback"},
        "states": {"walk": {"frames": 2, "fps": 8, "loop": True, "action": "x"}},
        "fit": fit,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_legacy_request_key_is_migrated_and_rewritten(tmp_path, capsys) -> None:
    """구키 런은 읽는 순간 신키로 재기록된다 — 메모리에서만 바꾸면 디스크에 구키가 영구히
    남아 SSoT 가 둘이 된다 (이관이지 폴백이 아니다: 1회, 관측 가능)."""
    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": True, "logical_height": 48})

    request = load_request(run_dir)
    assert request["fit"]["pixel_unfake"] is True
    assert "pixel_perfect" not in request["fit"]
    assert "migrated retired fit key" in capsys.readouterr().out

    on_disk = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert on_disk["fit"] == {"pixel_unfake": True, "logical_height": 48}
    # 멱등: 두 번째 로드는 아무것도 하지 않는다
    load_request(run_dir)
    assert "migrated" not in capsys.readouterr().out


def test_both_request_keys_present_fails_loud(tmp_path) -> None:
    """어느 쪽이 진실인지 코드가 고를 수 없다 — 고르는 순간 그게 조용한 폴백이다."""
    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": False, "pixel_unfake": True})
    with pytest.raises(SystemExit) as excinfo:
        load_request(run_dir)
    assert "two truths" in str(excinfo.value)


def test_migration_is_deferred_while_a_pipeline_writer_holds_the_lock(tmp_path, capsys) -> None:
    """파이프라인 writer 가 락을 쥐고 있으면 재기록을 미룬다 (그 시점 run dir 는 스왑 중일
    수 있다). 메모리 값은 이미 현행 키라 호출부 동작은 정상이다."""
    from sprite_gen.runio import LOCK_FILENAME

    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": True})
    (run_dir / LOCK_FILENAME).write_text("held by another writer", encoding="utf-8")

    request = load_request(run_dir)
    assert request["fit"]["pixel_unfake"] is True          # 메모리는 현행 키
    assert "in memory only" in capsys.readouterr().out
    on_disk = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert "pixel_perfect" in on_disk["fit"]               # 디스크는 다음 로드에서

    (run_dir / LOCK_FILENAME).unlink()
    load_request(run_dir)
    assert "pixel_unfake" in json.loads(
        (run_dir / "sprite-request.json").read_text(encoding="utf-8"))["fit"]


def _write_curation(run_dir, doc: dict) -> None:
    """현재 세대 도장을 찍어 둔다 — 스탬프 없는 사이드카는 세대 게이트가 (정상적으로) 드롭해서
    이관 여부를 볼 수 없다. 여기서 검증하려는 건 이관이고 세대 판정이 아니다."""
    run_dir.mkdir(parents=True, exist_ok=True)
    doc = {**doc, "run_revision": run_revision(run_dir)}
    (run_dir / CURATION_FILENAME).write_text(json.dumps(doc), encoding="utf-8")


def test_legacy_sidecar_keys_are_migrated_on_load(tmp_path) -> None:
    """사이드카도 같은 계약 — 최상위 + 행별 둘 다."""
    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_unfake": True})
    _write_curation(run_dir, {"version": 1, "kind": "sprite-gen-curation",
                              "pixel_perfect": False,
                              "states": {"walk": {"pixel_perfect": True, "selected": [0]}}})
    doc = load_curation_report(run_dir)[0]
    assert doc["pixel_unfake"] is False and "pixel_perfect" not in doc
    assert doc["states"]["walk"]["pixel_unfake"] is True
    # 해석 SSoT 도 현행 키를 본다
    assert frame_variant(doc, "walk") == "pixel"
    assert frame_variant(doc) == "plain"


def test_both_sidecar_keys_present_fails_loud(tmp_path) -> None:
    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_unfake": True})
    _write_curation(run_dir, {"version": 1, "kind": "sprite-gen-curation",
                              "pixel_perfect": True, "pixel_unfake": False, "states": {}})
    with pytest.raises(SystemExit) as excinfo:
        load_curation_report(run_dir)
    assert "two truths" in str(excinfo.value)


def test_retired_cli_flag_reports_the_new_name(tmp_path) -> None:
    """구 플래그는 조용한 별칭이 아니라 안내와 함께 죽는다 (두 이름 공존 금지)."""
    from sprite_gen.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["prepare", "--out-dir", str(tmp_path / "o"), "--character-id", "x",
              "--fit-pixel-perfect"])
    message = str(excinfo.value)
    assert "--fit-pixel-unfake" in message and "retired" in message
