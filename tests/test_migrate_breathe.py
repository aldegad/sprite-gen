# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen migrate-breathe` — 폐기 사이드카를 옮기는 유일한 경로.

모듈 docstring 이 "무엇을 버렸는지 전부 출력한다" 를 계약으로 걸었으므로, 폐기 키가
하나라도 보고에서 빠지면 그 계약이 깨진다 (부리 note 2026-07-25: `hold` 가 목록에
없어 조용히 사라졌다).
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sprite_gen.curation import RETIRED_BREATHE_KEYS, state_breathe  # noqa: E402
from sprite_gen.migrate_breathe import migrate_entry, run  # noqa: E402


def _sidecar(tmp_path, breathe):
    data = {"version": 1, "kind": "sprite-gen-curation",
            "states": {"idle": {"selected": [0, 1], "breathe": breathe}, "jump": {"selected": [0]}}}
    (tmp_path / "curation.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path / "curation.json"


ALL_RETIRED = {"splits": [0.32, 0.55], "amplitude": 2, "subpixel": True, "hold": 3}


@pytest.mark.parametrize("key", sorted(RETIRED_BREATHE_KEYS))
def test_every_retired_key_is_reported_as_dropped(key):
    """버린 것을 전부 출력한다 — 목록에서 빠진 키는 조용히 사라진다."""
    fresh, dropped = migrate_entry({key: ALL_RETIRED[key], "breaths": 3})
    assert any(line.startswith(f"{key}=") for line in dropped), \
        f"{key} 를 버렸다는 보고가 없다: {dropped}"
    assert key not in fresh


def test_breaths_survives_and_the_rest_is_reset():
    fresh, _ = migrate_entry({**ALL_RETIRED, "breaths": 5})
    assert fresh["breaths"] == 5, "사람이 고른 호흡 횟수는 보존한다"
    assert set(fresh) == {"depth", "lag", "breaths"}
    assert not any(k in fresh for k in RETIRED_BREATHE_KEYS)


def test_a_non_integer_breaths_falls_back_loudly():
    fresh, dropped = migrate_entry({"splits": [0.5], "breaths": "셋"})
    assert fresh["breaths"] == 1
    assert any("breaths=" in line for line in dropped), "되돌린 사실이 보고돼야 한다"


def test_dry_run_does_not_touch_the_file(tmp_path, capsys):
    path = _sidecar(tmp_path, dict(ALL_RETIRED, breaths=3))
    before = path.read_text(encoding="utf-8")
    assert run(run_dir=tmp_path) == 0
    assert path.read_text(encoding="utf-8") == before, "dry-run 이 파일을 건드렸다"
    assert "dry-run" in capsys.readouterr().out


def test_apply_makes_the_sidecar_bakeable(tmp_path):
    path = _sidecar(tmp_path, dict(ALL_RETIRED, breaths=3))
    with pytest.raises(SystemExit):                      # 옮기기 전에는 loud reject
        state_breathe(json.loads(path.read_text(encoding="utf-8")), "idle")
    assert run(run_dir=tmp_path, apply=True) == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    cfg = state_breathe(data, "idle")                    # 이제 통과한다
    assert cfg["breaths"] == 3 and cfg["rigid_row"] is None
    assert data["states"]["jump"] == {"selected": [0]}, "관계없는 상태를 건드렸다"


def test_running_twice_is_idempotent(tmp_path, capsys):
    path = _sidecar(tmp_path, dict(ALL_RETIRED, breaths=2))
    run(run_dir=tmp_path, apply=True)
    once = path.read_text(encoding="utf-8")
    capsys.readouterr()
    assert run(run_dir=tmp_path, apply=True) == 0
    assert path.read_text(encoding="utf-8") == once
    assert "옮길 분할선 설정이 없다" in capsys.readouterr().out


def test_a_missing_sidecar_fails_loudly(tmp_path, capsys):
    assert run(run_dir=tmp_path) == 1
    assert "없다" in capsys.readouterr().out
