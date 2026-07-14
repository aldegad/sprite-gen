# SPDX-License-Identifier: Apache-2.0
"""Projection-profile + DP segmentation (sprite_gen/segment.py) tests.

The fused fixture (tests/fixtures/run-fused/) draws three poses whose arms
touch the neighbouring pose: connected-component extraction sees ONE blob and
must fail, while the opt-in projection segmentation (fit.segmentation:
"projection" or --segmentation projection) must recover exactly the expected
frame count. The recorded golden manifest pins the split bit-for-bit, and the
existing separated golden run must stay bit-identical even with projection
enabled (the gutter rebuild is behaviour-preserving).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from conftest import FIXTURE_RUN, run_script
from sprite_gen.segment import (
    dp_n_cut,
    segment_boundaries,
    segment_strip,
    separate_fused_poses,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FUSED_RUN = FIXTURES / "run-fused"

EXPECTED_FUSED = json.loads(
    (FIXTURES / "expected-fused-frames-manifest.json").read_text(encoding="utf-8")
)
EXPECTED_SEPARATED = json.loads(
    (FIXTURES / "expected-frames-manifest.json").read_text(encoding="utf-8")
)


@pytest.fixture
def fused_run_dir(tmp_path: Path) -> Path:
    """A throwaway copy of the fused-pose fixture run dir."""
    run_dir = tmp_path / "run-fused"
    shutil.copytree(FUSED_RUN, run_dir)
    return run_dir


def _set_segmentation(run_dir: Path, mode: str | None) -> None:
    request_path = run_dir / "sprite-request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    fit = dict(request.get("fit") or {})
    if mode is None:
        fit.pop("segmentation", None)
    else:
        fit["segmentation"] = mode
    request["fit"] = fit
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --- integration: extract CLI on the fused fixture --------------------------


def test_fused_extraction_matches_golden_manifest(fused_run_dir: Path) -> None:
    result = run_script("extract_sprite_row_frames.py", "--run-dir", str(fused_run_dir))
    assert result.returncode == 0, result.stdout + result.stderr

    manifest = json.loads((fused_run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    manifest.pop("run_dir")
    # 엔진 소스 해시/기록 플래그는 휘발성 스탬프 — 골든 비교 대상이 아니다
    manifest.pop("engine_revision", None)
    manifest.pop("extract_args", None)
    for _row in manifest.get("rows", []):
        _row.pop("engine_revision", None)
    assert manifest == EXPECTED_FUSED
    assert "[segment] kick: projection split at columns" in result.stderr


def test_fused_strip_fails_without_opt_in(fused_run_dir: Path) -> None:
    # Default segmentation is components: the fused strip is one blob and the
    # run must fail loudly (this is the failure mode the port addresses).
    _set_segmentation(fused_run_dir, None)
    result = run_script("extract_sprite_row_frames.py", "--run-dir", str(fused_run_dir))
    assert result.returncode == 1
    # Strict whole-generation atomicity: a failed FIRST extract publishes no partial generation
    # to canonical frames/ — the per-state failure signal is written OUTSIDE frames/ as
    # extract-failure.json (still observable, still consumed by the correction loop).
    assert not (fused_run_dir / "frames").exists()
    failure = json.loads((fused_run_dir / "extract-failure.json").read_text(encoding="utf-8"))
    assert failure["ok"] is False
    assert any("could not extract 3 sprite components" in error for error in failure["errors"])


def test_cli_flag_enables_projection_without_request_opt_in(fused_run_dir: Path) -> None:
    _set_segmentation(fused_run_dir, None)
    result = run_script(
        "extract_sprite_row_frames.py",
        "--run-dir", str(fused_run_dir),
        "--segmentation", "projection",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((fused_run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    manifest.pop("run_dir")
    # 엔진 소스 해시/기록 플래그는 휘발성 스탬프 — 골든 비교 대상이 아니다
    manifest.pop("engine_revision", None)
    manifest.pop("extract_args", None)
    for _row in manifest.get("rows", []):
        _row.pop("engine_revision", None)
    assert manifest == EXPECTED_FUSED


def test_cli_flag_components_overrides_request_projection(fused_run_dir: Path) -> None:
    # Explicit CLI mode wins over the request opt-in (observable off switch).
    result = run_script(
        "extract_sprite_row_frames.py",
        "--run-dir", str(fused_run_dir),
        "--segmentation", "components",
    )
    assert result.returncode == 1
    # Failed first extract: no partial frames/ published; failure signal in extract-failure.json.
    assert not (fused_run_dir / "frames").exists()
    failure = json.loads((fused_run_dir / "extract-failure.json").read_text(encoding="utf-8"))
    assert any("could not extract 3 sprite components" in error for error in failure["errors"])


def test_projection_is_bit_identical_on_separated_golden_run(tmp_path: Path) -> None:
    # The gutter rebuild must be behaviour-preserving when poses are already
    # separated: the separated golden run with projection enabled must produce
    # the exact same manifest as the recorded components golden.
    run_dir = tmp_path / "run"
    shutil.copytree(FIXTURE_RUN, run_dir)
    result = run_script(
        "extract_sprite_row_frames.py",
        "--run-dir", str(run_dir),
        "--segmentation", "projection",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    manifest.pop("run_dir")
    # 엔진 소스 해시/기록 플래그는 휘발성 스탬프 — 골든 비교 대상이 아니다
    manifest.pop("engine_revision", None)
    manifest.pop("extract_args", None)
    for _row in manifest.get("rows", []):
        _row.pop("engine_revision", None)
    assert manifest == EXPECTED_SEPARATED


# --- unit: pure segmentation functions ---------------------------------------


def _bars_strip(spans: list[tuple[int, int]], width: int = 300, height: int = 40) -> Image.Image:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for start, end in spans:
        for x in range(start, end):
            for y in range(height):
                image.putpixel((x, y), (90, 90, 90, 255))
    return image


def test_segment_strip_counts_natural_poses_from_gutters() -> None:
    image = _bars_strip([(20, 60), (130, 170), (240, 280)])
    segments, natural = segment_strip(image, 3)
    assert natural == 3
    assert len(segments) == 3
    boundaries, _ = segment_boundaries(image, 3)
    assert boundaries is not None
    assert 60 <= boundaries[0] <= 130
    assert 170 <= boundaries[1] <= 240


def test_segment_boundaries_reports_failure_for_empty_strip() -> None:
    image = Image.new("RGBA", (200, 40), (0, 0, 0, 0))
    boundaries, natural = segment_boundaries(image, 3)
    assert boundaries is None
    assert natural == 0


def test_dp_n_cut_prefers_low_mass_columns() -> None:
    profile = [10.0] * 100
    for x in range(30, 36):
        profile[x] = 1.0
    for x in range(60, 66):
        profile[x] = 1.0
    cuts = dp_n_cut(profile, 0, 100, 3)
    assert cuts is not None and len(cuts) == 2
    assert 28 <= cuts[0] <= 38
    assert 58 <= cuts[1] <= 68


def test_separate_fused_poses_is_off_by_default() -> None:
    image = _bars_strip([(20, 60), (130, 170)])
    assert separate_fused_poses(image, 2) is image
    assert separate_fused_poses(image, 2, {"segmentation": "components"}) is image


def test_separate_fused_poses_leaves_strip_untouched_on_failure(capsys: pytest.CaptureFixture) -> None:
    # An empty strip cannot be segmented: the hook must report and return the
    # strip unchanged so the downstream extraction fails observably.
    image = Image.new("RGBA", (200, 40), (0, 0, 0, 0))
    assert separate_fused_poses(image, 3, {"segmentation": "projection"}, None, "empty") is image
    assert "projection segmentation found 0 pose(s)" in capsys.readouterr().err
