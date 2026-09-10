# SPDX-License-Identifier: Apache-2.0
"""Prepared guides carry geometry only, including when old recipes are reused."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from conftest import run_script
from sprite_gen.gen import prepare


@pytest.mark.parametrize("frames", [8, 16])
def test_reused_request_cannot_restore_pose_guides(tmp_path: Path, frames: int):
    payload = {"states": {"running-right": {
        "frames": frames, "fps": 16, "loop": True, "action": "running in place",
    }}}
    guide_pixels = []
    prompt_texts = []
    for name, old_value in (("clean", None), ("enabled", True), ("disabled", False)):
        request = dict(payload)
        if old_value is not None:
            request["motion_phase_guides"] = old_value
        out_dir = tmp_path / name
        result = run_script("prepare_sprite_run.py", "--out-dir", str(out_dir),
                            "--character-id", "runner", "--request-json", json.dumps(request))
        assert result.returncode == 0, result.stdout + result.stderr
        emitted = json.loads((out_dir / "sprite-request.json").read_text())
        assert "motion_phase_guides" not in emitted
        if old_value is not None:
            assert "motion_phase_guides" in result.stderr
            assert "dropped" in result.stderr
        with Image.open(next((out_dir / "references/layout-guides").rglob("*.png"))) as guide:
            assert guide.size == (frames * 256, 256)
            # A limb/head drawing would add colours beyond the empty geometry.
            assert {color for _count, color in guide.getcolors(maxcolors=256)} == {
                (246, 246, 246), (51, 51, 51), (47, 128, 237), (184, 200, 232),
            }
            guide_pixels.append(guide.tobytes())
        prompt = next((out_dir / "prompts").rglob("*.txt")).read_text()
        assert "Animation action: running in place" in prompt
        for retired in ("stick", "phase guide", "phase hints", "Motion phase requirements"):
            assert retired not in prompt
        prompt_texts.append(prompt)
    assert guide_pixels[0] == guide_pixels[1] == guide_pixels[2]
    assert prompt_texts[0] == prompt_texts[1] == prompt_texts[2]


@pytest.mark.parametrize("entrypoint", ["wrapper", "cli"])
def test_removed_flag_is_rejected_before_output(tmp_path: Path, entrypoint: str):
    args = ["--out-dir", str(tmp_path / "run"), "--character-id", "runner", "--motion-phase-guides"]
    if entrypoint == "wrapper":
        result = run_script("prepare_sprite_run.py", *args)
    else:
        result = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "prepare", *args],
                                text=True, capture_output=True)
    assert result.returncode != 0
    assert "unrecognized arguments: --motion-phase-guides" in result.stderr
    assert not (tmp_path / "run").exists()


def test_library_does_not_accept_removed_option(tmp_path: Path):
    with pytest.raises(TypeError, match="unexpected keyword argument.*motion_phase_guides"):
        prepare.run(out_dir=tmp_path / "run", character_id="runner", motion_phase_guides=True)
    assert not (tmp_path / "run").exists()
