# SPDX-License-Identifier: Apache-2.0
"""Quickstart smoke: prepare -> extract -> compose on the golden fixture."""

import json
from pathlib import Path

from PIL import Image

from conftest import run_script


def test_prepare_writes_run_contract(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    result = run_script(
        "prepare_sprite_run.py",
        "--out-dir", str(out_dir),
        "--character-id", "smokebot",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    request = json.loads((out_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert request["kind"] == "sprite-gen-request"
    assert request["character"]["id"] == "smokebot"
    for state in request["states"]:
        assert (out_dir / "prompts" / f"{state}.txt").is_file()
        assert (out_dir / "references" / "layout-guides" / f"{state}.png").is_file()


def test_prepare_records_fit_cli_flags_in_request(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    result = run_script(
        "prepare_sprite_run.py",
        "--out-dir", str(out_dir),
        "--character-id", "smokebot",
        "--request-json", '{"fit": {"resample": "lanczos", "palette_size": 24}}',
        "--fit-resample", "kcentroid",
        "--fit-align-x", "foot-centroid",
        "--fit-pixel-perfect",
        "--fit-logical-height", "64",
        "--fit-outline", "0.5",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    request = json.loads((out_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert request["fit"] == {
        "resample": "kcentroid",  # CLI overrides the --request-json value
        "align_x": "foot-centroid",
        "pixel_perfect": True,
        "logical_height": 64,
        "outline": 0.5,
        "palette_size": 24,  # untouched --request-json key survives the merge
    }


def test_extract_then_compose_bakes_atlas(fixture_run_dir: Path) -> None:
    extract = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir))
    assert extract.returncode == 0, extract.stdout + extract.stderr

    compose = run_script("compose_sprite_atlas.py", "--run-dir", str(fixture_run_dir))
    assert compose.returncode == 0, compose.stdout + compose.stderr

    request = json.loads((fixture_run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    cell = request["cell"]
    states = list(request["states"])
    max_frames = max(int(entry["frames"]) for entry in request["states"].values())

    with Image.open(fixture_run_dir / "sprite-sheet-alpha.png") as atlas:
        assert atlas.size == (max_frames * cell["width"], len(states) * cell["height"])

    manifest = json.loads((fixture_run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["curation_applied"] is False
    layout_rows = manifest["frame_layout"]["rows"]
    assert set(layout_rows) == set(states)
    for row_index, state in enumerate(states):
        cells = layout_rows[state]
        assert len(cells) == int(request["states"][state]["frames"])
        for column, rect in enumerate(cells):
            assert rect == {
                "x": column * cell["width"],
                "y": row_index * cell["height"],
                "w": cell["width"],
                "h": cell["height"],
            }
