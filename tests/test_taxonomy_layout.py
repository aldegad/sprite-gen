# SPDX-License-Identifier: Apache-2.0
"""File taxonomy (layout: taxonomy/v1) e2e — 방향/자세 폴더 분리.

기본 계약: 방향 계약 런은 raw/<dir>/<pose>.png · frames/<dir>/<pose>/ ·
references/layout-guides/<dir>/<pose>.png 로 나뉜다. 상태 ID(<dir>_<pose>)와
manifest row.files 가 소비자(합성/뷰)의 경로 SSoT 다. legacy 런(layout 필드
없음)은 flat 그대로 — 기존 골든 테스트들이 그 회귀를 잡는다.
"""

import json
import random
import sys
from pathlib import Path

from PIL import Image

import sprite_gen.compose_atlas as compose_module
import sprite_gen.extract as extract_module
from conftest import run_script
from sprite_gen.layout import frames_dir_rel, raw_rel, row_frame_rel

MAGENTA = (255, 0, 255)


def _figure(width, height, seed):
    rng = random.Random(seed)
    art = Image.new("RGB", (width, height), MAGENTA)
    for y in range(height):
        for x in range(width):
            if rng.random() < 0.6:
                art.putpixel((x, y), (rng.randrange(30, 220), rng.randrange(30, 220), rng.randrange(30, 220)))
    return art


def _strip(frames, width=120, height=200, gap=36, seed=1):
    figure = _figure(20, 34, seed).resize((width, height), Image.Resampling.NEAREST)
    strip = Image.new("RGB", (frames * width + gap * (frames + 1), height + gap * 2), MAGENTA)
    for i in range(frames):
        strip.paste(figure, (gap + i * (width + gap), gap))
    return strip


def test_taxonomy_end_to_end(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    result = run_script(
        "prepare_sprite_run.py",
        "--out-dir", str(out_dir),
        "--character-id", "taxobot",
        "--cell-size", "64", "--safe-margin", "6",
        "--directions", "down,side", "--mirror", "left=side",
        "--request-json", json.dumps({
            "states": {
                "down_walk": {"frames": 2, "fps": 8, "loop": True, "action": "walk toward viewer"},
                "side_walk": {"frames": 2, "fps": 8, "loop": True, "action": "walk side view"},
            },
        }),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    request = json.loads((out_dir / "sprite-request.json").read_text(encoding="utf-8"))

    # 리졸버 계약: 방향/자세 폴더
    assert raw_rel(request, "down_idle") == "raw/down/idle.png"
    assert frames_dir_rel(request, "side_walk") == "frames/side/walk"

    # 택소노미 위치에 raw 를 넣고 추출
    for seed, state in enumerate(request["states"], start=3):
        rel = raw_rel(request, state)
        (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        _strip(int(request["states"][state]["frames"]), seed=seed).save(out_dir / rel)
    assert extract_module.run(run_dir=out_dir, min_used_pixels=200) == 0

    manifest = json.loads((out_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    rows = {r["state"]: r for r in manifest["rows"]}
    assert set(rows) == set(request["states"])
    for state, row in rows.items():
        assert row["ok"], row
        for rel in row["files"]:
            assert rel.startswith(frames_dir_rel(request, state) + "/"), rel
            assert (out_dir / rel).is_file(), rel
    # row files 가 소비자 경로 SSoT
    assert row_frame_rel(rows["down_walk"], 0) == "frames/down/walk/frame-0.png"

    # 합성까지 (manifest 경로 추종 확인)
    assert compose_module.run(run_dir=out_dir, min_used_pixels=200) == 0
    atlas_manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert set(atlas_manifest["frame_layout"]["rows"]) == set(request["states"])

    # 뷰 스냅샷도 같은 경로를 서빙
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import serve_curation
    snapshot = serve_curation.build_run_state(out_dir)
    walk = next(s for s in snapshot["states"] if s["name"] == "down_walk")
    assert walk["frames"][0]["present"]
    assert "/frames/down/walk/frame-0.png" in walk["frames"][0]["url"]
