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


def test_gen_references_alpha_failures_and_partial_report(workspace: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    from sprite_gen.parts import parts_gen
    calls = []
    fail = {"face"}

    def provider(name, prompt, out, **kwargs):
        calls.append((out.stem, kwargs))
        assert len(kwargs["refs"]) == 2
        assert Image.open(kwargs["refs"][0]).size == CANVAS
        assert Image.open(kwargs["refs"][1]).width <= CANVAS[0]
        assert kwargs["transparent"] and kwargs["chroma_key"] == "green"
        if out.stem in fail:
            Image.new("RGBA", (20, 20), (220, 200, 180, 255)).save(out)
        else:
            _part(out, (10, 10), (200, 100, 80))
        return SimpleNamespace(raw=out, elapsed_seconds=0, provider=name)

    monkeypatch.setattr(parts_gen, "generate_image", provider)
    out = workspace / "generated"
    first = parts_gen.generate_parts(workspace / "catalog.json", out, workers=2)
    assert not first["ok"] and first["failed"] == ["face"]
    assert len(calls) == len(cat.jobs(_catalog(workspace)))
    second = parts_gen.generate_parts(workspace / "catalog.json", out, only=["body"])
    assert not second["ok"] and second["failed"] == ["face"]
    assert second["ran"] == ["body"] and len(second["jobs"]) == len(first["jobs"])
    fail.clear()
    repaired = parts_gen.generate_parts(workspace / "catalog.json", out, only=["face"])
    assert repaired["ok"] and repaired["failed"] == []


def test_rms_uses_exact_frame_boundaries_for_long_audio(monkeypatch) -> None:
    from types import SimpleNamespace
    from sprite_gen._deps import np
    # 16000 / 30 is fractional: truncating each window adds 5 frames in 141s.
    samples = np.zeros(16000 * 141, dtype=np.int16)
    samples[16000 * 140:] = 10000
    monkeypatch.setattr(rig.shutil, "which", lambda _: "ffmpeg")
    monkeypatch.setattr(rig.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=samples.tobytes()))
    env = rig.rms_envelope(Path("synthetic.wav"), 30)
    assert len(env) == 141 * 30
    assert next(i for i, value in enumerate(env) if value > 0) == 140 * 30 - 1
    samples = samples[:100]
    assert len(rig.rms_envelope(Path("short.wav"), 30)) == 1


def test_rig_preserves_interleaved_group_draw_order(workspace: Path) -> None:
    from html.parser import HTMLParser
    data = _catalog(workspace)
    # A foreground ungrouped layer interrupts two head layers.
    data["parts"][2].pop("group")
    (workspace / "catalog.json").write_text(json.dumps(data))
    match.match_parts(workspace / "catalog.json", workspace / "parts")
    model = rig.build_rig(workspace / "catalog.json", workspace / "parts")
    wrappers, layers = [], []
    class Parser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get("class") == "rig-group":
                wrappers.append(attrs)
            if tag == "img":
                layers.append(attrs["id"])
    Parser().feed(rig.render_html(model))
    assert layers.index("rig-face") < layers.index("rig-mouth") < layers.index("rig-eyelid_l")
    assert len(wrappers) == 2
    assert all(w["data-rig-group"] == "head" for w in wrappers)
    assert "z-index:1;" in wrappers[0]["style"] and "z-index:3;" in wrappers[1]["style"]
    assert len({w["id"] for w in wrappers}) == 2


def test_rig_refuses_deleted_placed_layer(workspace: Path) -> None:
    match.match_parts(workspace / "catalog.json", workspace / "parts")
    (workspace / "parts" / "placed" / "mouth__open.png").unlink()
    with pytest.raises(SystemExit, match="missing placed layer: mouth__open"):
        rig.build_rig(workspace / "catalog.json", workspace / "parts")


def test_closed_blink_hides_the_underlying_eye() -> None:
    model = {"groups": {}, "parts": [
        {"id": "eye_l", "variants": {"default": "eye.png"}},
        {"id": "eyelid_l", "variants": dict.fromkeys(["default", "open", "half", "closed"], "lid.png")},
    ]}
    keys = rig.build_keys(model, audio=None, fps=30, duration=5)
    track = keys["blink"][0]
    assert track["eye_part"] == "eye_l"
    js = rig.render_keys_js(keys)
    closed = next(k for k in track["keys"] if k["variant"] == "closed")
    opened = next(k for k in track["keys"] if k["variant"] == "open")
    assert f'"eye_l", {{ opacity: 0 }}, start + {closed["t"]}' in js
    assert f'"eye_l", {{ opacity: 1 }}, start + {opened["t"]}' in js
