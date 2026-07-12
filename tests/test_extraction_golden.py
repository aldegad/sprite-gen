# SPDX-License-Identifier: Apache-2.0
"""Golden regression for connected-component frame extraction.

Fixed synthetic row strips (tests/fixtures/run/raw/) go through
extract_sprite_row_frames.py and the resulting frames-manifest.json must match
the recorded expectations bit-for-bit: frame counts, extraction method, per
frame bbox and pixel counts. Any change in extraction behaviour shows up here.
"""

import json
from pathlib import Path

from PIL import Image

from conftest import run_script

EXPECTED = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "expected-frames-manifest.json").read_text(encoding="utf-8")
)


def test_extraction_matches_golden_manifest(fixture_run_dir: Path) -> None:
    result = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir))
    assert result.returncode == 0, result.stdout + result.stderr

    manifest = json.loads((fixture_run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    manifest.pop("run_dir")
    assert manifest == EXPECTED


def test_extracted_frames_are_cell_sized_rgba(fixture_run_dir: Path) -> None:
    result = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir))
    assert result.returncode == 0, result.stdout + result.stderr

    for row in EXPECTED["rows"]:
        for relative in row["files"]:
            frame_path = fixture_run_dir / relative
            assert frame_path.is_file(), f"missing {relative}"
            with Image.open(frame_path) as frame:
                assert frame.mode == "RGBA"
                assert frame.size == (EXPECTED["cell"]["width"], EXPECTED["cell"]["height"])


def test_extraction_fails_without_raw_strip(fixture_run_dir: Path) -> None:
    (fixture_run_dir / "raw" / "walk.png").unlink()
    result = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir))
    assert result.returncode == 1
    # Strict whole-generation atomicity: a failed FIRST extract publishes no partial generation
    # to canonical frames/; the observable ok:false failure signal lives in extract-failure.json.
    assert not (fixture_run_dir / "frames").exists()
    failure = json.loads((fixture_run_dir / "extract-failure.json").read_text(encoding="utf-8"))
    assert failure["ok"] is False
    assert any("missing raw strip" in error for error in failure["errors"])
