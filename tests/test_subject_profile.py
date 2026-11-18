# SPDX-License-Identifier: Apache-2.0
"""Subject profile: the sparse-frame floor is declared by what the run draws.

Synthetic boundary cases demonstrate that one absolute floor cannot scale across
cell resolutions. Profiles preserve debris rejection while deriving the floor
from drawable cell geometry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from conftest import run_script
from sprite_gen.spec.subject import cell_area, default_min_used_pixels, subject_kind
from sprite_gen.spec import subject as subject_mod

FRAMES = 3


def _make_run(run_dir: Path, density: int, subject: str | None, cell: int = 64) -> Path:
    """A hand-built minimal run: request + one magenta-keyed strip whose frames
    carry ~density opaque pixels each (solid square, no AA)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    request = {
        "version": 1, "kind": "sprite-gen-request", "engine": "component-row",
        "character": {"id": "profiled", "description": "", "base_image": None},
        "cell": {"shape": "square", "width": cell, "height": cell,
                 "safe_margin_x": 6, "safe_margin_y": 6, "size": cell, "safe_margin": 6},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255],
                       "selection": "explicit"},
        "states": {"burst": {"frames": FRAMES, "fps": 8, "loop": True}},
        "style": "test", "layout": "taxonomy/v1",
    }
    if subject:
        request["subject"] = subject
    (run_dir / "sprite-request.json").write_text(json.dumps(request) + "\n", encoding="utf-8")
    strip = Image.new("RGB", (cell * FRAMES, cell), (255, 0, 255))
    draw = ImageDraw.Draw(strip)
    side = max(1, round(density ** 0.5))
    for i in range(FRAMES):
        x = i * cell + cell // 2 - side // 2 + (i - 1) * 3
        y = cell // 2 - side // 2
        draw.rectangle([x, y, x + side - 1, y + side - 1], fill=(40, 40, 120))
    (run_dir / "raw").mkdir(exist_ok=True)
    strip.save(run_dir / "raw" / "burst.png")
    return run_dir


def _extract(run_dir: Path, *flags: str):
    return run_script("extract_sprite_row_frames.py", "--run-dir", str(run_dir), *flags)


def _manifest(run_dir: Path) -> dict:
    for name in ("frames/frames-manifest.json", "extract-failure.json"):
        p = run_dir / name
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise AssertionError("no extract output at all")


def _request_for_cell(width: int, height: int, margin_x: int, margin_y: int) -> dict:
    return {
        "cell": {
            "width": width,
            "height": height,
            "safe_margin_x": margin_x,
            "safe_margin_y": margin_y,
        }
    }


def test_the_subjects_are_named_once_and_each_has_a_floor() -> None:
    """Spelt out here, as the request writes them; the floor table is keyed by the declared names, one row each."""
    assert subject_mod.SUBJECTS == ("character", "effect")
    assert (subject_mod.SUBJECT_CHARACTER, subject_mod.SUBJECT_EFFECT) == subject_mod.SUBJECTS
    assert subject_mod.SUBJECT_DEFAULT == "character"
    assert list(subject_mod.SUBJECT_FLOOR_RATIOS) == list(subject_mod.SUBJECTS)


def test_profile_resolves_floor_and_rejects_unknown_kinds() -> None:
    request = _request_for_cell(64, 64, 6, 6)
    assert subject_kind(request) == "character"
    assert cell_area(request) == 64 * 64
    assert default_min_used_pixels(request) == 64
    assert default_min_used_pixels({**request, "subject": "effect"}) == 32
    with pytest.raises(SystemExit):
        subject_kind({**request, "subject": "creature"})


# One formula with one branch: a square and a rect cell pin its values; the 64² case is in
# test_profile_resolves_floor_and_rejects_unknown_kinds and every square size from 16 to 512 is walked
# by test_floor_is_monotone_across_every_square_size_from_16_to_512.
@pytest.mark.parametrize(
    ("width", "height", "margin_x", "margin_y", "character", "effect"),
    [
        (16, 16, 1, 1, 16, 8),
        (192, 208, 18, 19, 200, 100),
    ],
)
def test_floor_scales_continuously_for_square_and_rect_cells(
    width: int,
    height: int,
    margin_x: int,
    margin_y: int,
    character: int,
    effect: int,
) -> None:
    request = _request_for_cell(width, height, margin_x, margin_y)
    assert default_min_used_pixels(request) == character
    assert default_min_used_pixels({**request, "subject": "effect"}) == effect
    assert character in {effect * 2, effect * 2 - 1}


def test_floor_rejects_invalid_cell_geometry() -> None:
    with pytest.raises(SystemExit, match="positive"):
        default_min_used_pixels(_request_for_cell(0, 64, 0, 0))


def test_floor_is_monotone_across_every_square_size_from_16_to_512() -> None:
    prior_character = 0
    prior_effect = 0
    for size in range(16, 513):
        margin = int(size * 0.094)
        request = _request_for_cell(size, size, margin, margin)
        character = default_min_used_pixels(request)
        effect = default_min_used_pixels({**request, "subject": "effect"})
        assert character >= prior_character
        assert effect >= prior_effect
        assert character in {effect * 2, effect * 2 - 1}
        prior_character = character
        prior_effect = effect


def test_floor_is_monotone_when_each_rect_axis_grows() -> None:
    base = _request_for_cell(192, 208, 18, 19)
    wider = _request_for_cell(193, 208, 18, 19)
    taller = _request_for_cell(192, 209, 18, 19)
    for subject in (None, "effect"):
        def floor(request: dict) -> int:
            return default_min_used_pixels(
                request if subject is None else {**request, "subject": subject}
            )

        assert floor(wider) >= floor(base)
        assert floor(taller) >= floor(base)


def test_character_profile_rejects_a_sparse_effect_with_a_remedy(tmp_path: Path) -> None:
    # 36px/frame lies between the 64px-cell profile floors: character 64,
    # effect 32. The error names the way out.
    run = _make_run(tmp_path / "run", 40, subject=None)
    proc = _extract(run)
    assert proc.returncode != 0
    doc = _manifest(run)
    assert doc.get("ok") is False or doc.get("states")
    text = json.dumps(doc)
    assert "character profile floor" in text
    assert '\\"subject\\": \\"effect\\"' in text or '"subject": "effect"' in text.replace("\\", "")


def test_effect_profile_passes_a_sparse_synthetic_effect(tmp_path: Path) -> None:
    run = _make_run(tmp_path / "run", 40, subject="effect")
    proc = _extract(run)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    doc = _manifest(run)
    assert doc["ok"] is True
    px = [r["nontransparent_pixels"] for r in doc["rows"][0]["frame_records"]]
    assert all(32 <= p < 64 for p in px)
    # A profile-derived floor is not stamped into extract_args (an explicit flag is, below):
    # frames are a derived cache of (raw + request + engine), so heal must re-resolve from the request.
    assert "min_used_pixels" not in doc["extract_args"]
    # compose follows the same profile floor.
    compose = run_script("compose_sprite_atlas.py", "--run-dir", str(run))
    assert compose.returncode == 0, compose.stdout + compose.stderr
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["frame_layout"]["rows"]["burst"]


def test_effect_profile_still_catches_debris(tmp_path: Path) -> None:
    # 16px/frame sits below the 64px-cell effect floor (32).
    run = _make_run(tmp_path / "run", 20, subject="effect")
    assert _extract(run).returncode != 0
    assert "effect profile floor" in json.dumps(_manifest(run))


def test_explicit_flag_beats_the_profile_and_is_stamped(tmp_path: Path) -> None:
    run = _make_run(tmp_path / "run", 20, subject="effect")
    proc = _extract(run, "--min-used-pixels", "12")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    doc = _manifest(run)
    # explicit flag is recorded for heal reproduction (a profile-derived floor is not; see
    # test_effect_profile_passes_a_sparse_synthetic_effect)
    assert doc["extract_args"].get("min_used_pixels") == 12


def test_prepare_writes_the_subject_field_only_when_declared(tmp_path: Path) -> None:
    base = ["prepare_sprite_run.py", "--character-id", "profiled"]
    plain = run_script(*base, "--out-dir", str(tmp_path / "plain"))
    assert plain.returncode == 0, plain.stdout + plain.stderr
    request = json.loads((tmp_path / "plain" / "sprite-request.json").read_text(encoding="utf-8"))
    assert "subject" not in request  # legacy shape untouched

    flagged = run_script(*base, "--out-dir", str(tmp_path / "flagged"), "--subject", "effect")
    assert flagged.returncode == 0, flagged.stdout + flagged.stderr
    request = json.loads((tmp_path / "flagged" / "sprite-request.json").read_text(encoding="utf-8"))
    assert request["subject"] == "effect"

    via_json = run_script(*base, "--out-dir", str(tmp_path / "via-json"),
                          "--request-json", '{"subject": "effect"}')
    assert via_json.returncode == 0, via_json.stdout + via_json.stderr
    request = json.loads((tmp_path / "via-json" / "sprite-request.json").read_text(encoding="utf-8"))
    assert request["subject"] == "effect"

    bad = run_script(*base, "--out-dir", str(tmp_path / "bad"),
                     "--request-json", '{"subject": "creature"}')
    assert bad.returncode != 0
    assert "unknown subject kind" in bad.stderr
