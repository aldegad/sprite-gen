# SPDX-License-Identifier: Apache-2.0
"""Parts rig contract on synthetic shapes: catalog validation, pixel registration
gate (pass + fail-loud), and rig/key export determinism. No real character data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen.parts import catalog as cat
from sprite_gen.parts import match, rig

CANVAS = (160, 200)


def _catalog(tmp: Path, **overrides) -> dict:
    data = {
        "kind": cat.KIND, "version": cat.VERSION, "character": "synthetic", "base": "base.png",
        "canvas": {"width": CANVAS[0], "height": CANVAS[1]}, "chroma_key": "green",
        "groups": {"head": {"pivot": [80, 120]}},
        "parts": [
            {"id": "body", "z": 0, "bbox": [30, 100, 100, 100], "pivot": [80, 150], "prompt": "the body"},
            {"id": "face", "z": 1, "bbox": [40, 20, 80, 90], "pivot": [80, 65], "group": "head", "prompt": "the face"},
            {"id": "mouth", "z": 2, "bbox": [65, 80, 30, 14], "pivot": [80, 87], "group": "head", "prompt": "the mouth",
             "variants": {"default": "", "closed": "lips together", "open": "mouth open"}},
            {"id": "eyelid_l", "z": 3, "bbox": [50, 45, 20, 10], "pivot": [60, 50], "group": "head", "prompt": "left eyelid",
             "variants": {"default": "", "open": "", "half": "half closed", "closed": "closed"}},
        ],
    }
    data.update(overrides)
    return data


def _draw_base(path: Path) -> None:
    img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((30, 100, 129, 199), fill=(40, 80, 200, 255))      # body
    d.ellipse((40, 20, 119, 109), fill=(250, 220, 200, 255))       # face
    d.rectangle((65, 80, 94, 93), fill=(200, 60, 80, 255))         # mouth
    d.rectangle((50, 45, 69, 54), fill=(30, 30, 30, 255))          # eyelid_l
    img.save(path)


def _part(path: Path, size: tuple[int, int], color: tuple[int, int, int], *, shape: str = "rect", pad: int = 6) -> None:
    """A part drawn alone on transparency, padded so trimming + placement is exercised."""
    img = Image.new("RGBA", (size[0] + pad * 2, size[1] + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    box = (pad, pad, pad + size[0] - 1, pad + size[1] - 1)
    (d.ellipse if shape == "ellipse" else d.rectangle)(box, fill=color + (255,))
    img.save(path)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    _draw_base(tmp_path / "base.png")
    (tmp_path / "catalog.json").write_text(json.dumps(_catalog(tmp_path)), encoding="utf-8")
    parts = tmp_path / "parts"
    parts.mkdir()
    _part(parts / "body.png", (100, 100), (40, 80, 200))
    _part(parts / "face.png", (80, 90), (250, 220, 200), shape="ellipse")
    _part(parts / "mouth.png", (30, 14), (200, 60, 80))
    _part(parts / "mouth__closed.png", (30, 14), (200, 60, 80))
    _part(parts / "mouth__open.png", (30, 20), (120, 20, 30))
    _part(parts / "eyelid_l.png", (20, 10), (30, 30, 30))
    for v in ("open", "half", "closed"):
        _part(parts / f"eyelid_l__{v}.png", (20, 10), (30, 30, 30))
    return tmp_path


# --- catalog -----------------------------------------------------------------

def test_valid_catalog_has_no_errors(workspace: Path) -> None:
    assert cat.validate_catalog(_catalog(workspace)) == []


@pytest.mark.parametrize("mutate, needle", [
    (lambda c: c["parts"].append(dict(c["parts"][0])), "duplicate part id"),
    (lambda c: c["parts"][1].__setitem__("z", 0), "duplicate draw order"),
    (lambda c: c["parts"][0].__setitem__("bbox", [30, 100, 200, 100]), "exceeds canvas"),
    (lambda c: c["parts"][0].__setitem__("pivot", [0, 0]), "inside bbox"),
    (lambda c: c["parts"][1].__setitem__("group", "tail"), "unknown group"),
    (lambda c: c["parts"][2].__setitem__("variants", {"open": ""}), "must include 'default'"),
    (lambda c: c["parts"][0].__setitem__("bbox", [30.0, 100, 100, 100]), "integers"),
    (lambda c: c.__setitem__("chroma_key", "blue"), "chroma_key"),
])
def test_catalog_violations_are_named(workspace: Path, mutate, needle: str) -> None:
    data = _catalog(workspace)
    mutate(data)
    errors = cat.validate_catalog(data)
    assert errors and any(needle in e for e in errors), errors


def test_jobs_are_draw_ordered_and_named(workspace: Path) -> None:
    names = [cat.job_name(j["part"], j["variant"]) for j in cat.jobs(_catalog(workspace))]
    assert names[:3] == ["body", "face", "mouth"]
    assert "mouth__open" in names and "eyelid_l__closed" in names


# --- match -------------------------------------------------------------------

def test_match_registers_synthetic_parts_and_passes(workspace: Path) -> None:
    report = match.match_parts(workspace / "catalog.json", workspace / "parts")
    assert report["ok"], report["failed"]
    by = {r["job"]: r for r in report["parts"]}
    assert by["body"]["placement"]["x"] == 30 and by["body"]["placement"]["y"] == 100
    assert by["mouth"]["placement"]["scale"] == 1.0
    assert by["mouth__open"]["inherited_from"] == "mouth"
    assert report["composite"]["score"] <= 0.02 and report["composite"]["coverage"] >= 0.97
    assert (workspace / "parts" / "placed" / "face.png").is_file()


def test_match_fails_loud_on_wrong_part(workspace: Path) -> None:
    _part(workspace / "parts" / "face.png", (80, 90), (10, 200, 10), shape="ellipse")  # wrong colour
    report = match.match_parts(workspace / "catalog.json", workspace / "parts")
    assert not report["ok"]
    assert "face" in report["failed"]
    face = next(r for r in report["parts"] if r["job"] == "face")
    assert "exceeds tolerance" in face["error"]


def test_match_reports_missing_candidate(workspace: Path) -> None:
    (workspace / "parts" / "body.png").unlink()
    report = match.match_parts(workspace / "catalog.json", workspace / "parts")
    assert not report["ok"] and "body" in report["failed"]
    assert next(r for r in report["parts"] if r["job"] == "body")["error"] == "missing candidate"


# --- rig ---------------------------------------------------------------------

def test_rig_refuses_unmatched_report(workspace: Path) -> None:
    _part(workspace / "parts" / "face.png", (80, 90), (10, 200, 10), shape="ellipse")
    match.match_parts(workspace / "catalog.json", workspace / "parts")
    with pytest.raises(SystemExit, match="not passing"):
        rig.build_rig(workspace / "catalog.json", workspace / "parts")


def test_rig_export_is_deterministic(workspace: Path) -> None:
    match.match_parts(workspace / "catalog.json", workspace / "parts")
    out_a = workspace / "out_a"
    out_b = workspace / "out_b"
    for out in (out_a, out_b):
        assert rig.run(catalog=workspace / "catalog.json", match_dir=workspace / "parts", out_dir=out, duration=12.0) == 0
    for name in ("rig.json", "rig.html", "rig-keys.js", "rig-keys.json"):
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name
    data = json.loads((out_a / "rig.json").read_text())
    assert [p["id"] for p in data["parts"]] == ["body", "face", "mouth", "eyelid_l"]
    assert data["groups"]["head"]["members"] == ["face", "mouth", "eyelid_l"]
    keys = json.loads((out_a / "rig-keys.json").read_text())
    assert keys["mouth"] == []  # no audio → no mouth track
    assert keys["blink"][0]["part"] == "eyelid_l" and len(keys["blink"][0]["keys"]) >= 4
    assert keys["sway"][0]["group"] == "head"
    html = (out_a / "rig.html").read_text()
    assert 'id="rig-mouth__open"' in html and "opacity:0" in html and 'id="rig-g-head"' in html
    js = (out_a / "rig-keys.js").read_text()
    assert "window.__rigKeys" in js and "rotation" in js


def test_mouth_quantization_uses_available_variants() -> None:
    assert rig.mouth_variant(0.0, {"default", "closed", "open"}) == "closed"
    assert rig.mouth_variant(0.5, {"default", "closed", "open"}) == "open"
    assert rig.mouth_variant(0.2, {"default", "closed", "half", "open"}) == "half"
    assert rig.mouth_variant(0.9, {"default", "closed", "open", "o"}) == "o"
    assert rig.mouth_variant(0.9, {"default", "closed", "open"}) == "open"
