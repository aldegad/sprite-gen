# SPDX-License-Identifier: Apache-2.0
"""Direction contract: base(=down idle) -> per-direction anchors -> rows.

`--directions` runs scaffold the directional-anchor workflow structurally:
missing <dir>_idle anchors are synthesized, prompts lock facing per direction
(anchor rows derive from the base, action rows from their direction anchor),
mirrored directions are recorded as skipped-by-contract, and the generation
chain is written to references/generation-plan.json.
"""

import json
from pathlib import Path

import pytest

from conftest import run_script


def _prepare(tmp_path: Path, *extra: str):
    out_dir = tmp_path / "run"
    result = run_script(
        "prepare_sprite_run.py",
        "--out-dir", str(out_dir),
        "--character-id", "dirbot",
        "--request-json", json.dumps({
            "states": {
                "down_walk": {"frames": 6, "fps": 8, "loop": True, "action": "walking toward the viewer"},
                "side_walk": {"frames": 6, "fps": 8, "loop": True, "action": "walking in side view"},
                "up_walk": {"frames": 6, "fps": 8, "loop": True, "action": "walking away from the viewer"},
            },
        }),
        *extra,
    )
    return out_dir, result


def test_directions_scaffold_anchors_and_plan(tmp_path: Path) -> None:
    out_dir, result = _prepare(tmp_path, "--directions", "down,side,up", "--mirror", "left=side")
    assert result.returncode == 0, result.stdout + result.stderr

    request = json.loads((out_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert request["directions"] == {"set": ["down", "side", "up"], "mirror": {"left": "side"}, "anchor_suffix": "idle"}
    # 방향 앵커가 합성되어 states 에 존재하고, 택소노미 경로(<dir>/<pose>)에 가이드/프롬프트 생성
    assert request["layout"] == "taxonomy/v1"
    for anchor in ("down_idle", "side_idle", "up_idle"):
        assert anchor in request["states"], anchor
        d = anchor.split("_", 1)[0]
        assert (out_dir / "prompts" / d / "idle.txt").is_file()
        assert (out_dir / "references" / "layout-guides" / d / "idle.png").is_file()

    # 프롬프트 방향 잠금: 앵커는 base 기반 + canonical 문구, 행은 앵커 기반 문구
    anchor_prompt = (out_dir / "prompts" / "up" / "idle.txt").read_text(encoding="utf-8")
    assert "CANONICAL DIRECTION ANCHOR" in anchor_prompt
    assert "facing away from the viewer" in anchor_prompt
    row_prompt_text = (out_dir / "prompts" / "side" / "walk.txt").read_text(encoding="utf-8")
    assert "accepted direction anchor" in row_prompt_text
    assert "pure side profile view facing camera-right" in row_prompt_text

    plan = json.loads((out_dir / "references" / "generation-plan.json").read_text(encoding="utf-8"))
    assert plan["kind"] == "sprite-gen-generation-plan"
    stage1, stage2 = plan["order"][0], plan["order"][1]
    assert [i["state"] for i in stage1["items"]] == ["down_idle", "side_idle", "up_idle"]
    assert all(i["role"] == "direction-anchor" and "base-source.*" in i["refs"] for i in stage1["items"])
    walk_item = next(i for i in stage2["items"] if i["state"] == "side_walk")
    assert "raw/side/idle.png" in walk_item["refs"][0]
    assert walk_item["refs"][1] == "references/layout-guides/side/walk.png"
    # 미러 방향은 생성 생략이 계약으로 기록된다 (조용한 누락 금지)
    assert plan["mirrored_directions"][0]["direction"] == "left"
    assert plan["mirrored_directions"][0]["mirror_of"] == "side"


def test_directions_reject_unprefixed_state(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    result = run_script(
        "prepare_sprite_run.py",
        "--out-dir", str(out_dir),
        "--character-id", "dirbot",
        "--request-json", json.dumps({"states": {"wave": {"frames": 4, "fps": 6, "loop": False, "action": "wave"}}}),
        "--directions", "down,side",
    )
    assert result.returncode != 0
    assert "does not start with a declared direction prefix" in (result.stdout + result.stderr)


def test_directions_mirror_source_must_be_generated(tmp_path: Path) -> None:
    out_dir, result = _prepare(tmp_path, "--directions", "down,side,up", "--mirror", "left=right")
    assert result.returncode != 0
    assert "not in directions.set" in (result.stdout + result.stderr)
