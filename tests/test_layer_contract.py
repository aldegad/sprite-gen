# SPDX-License-Identifier: Apache-2.0
"""Layer-track contract: declaration validation + the compatibility boundaries it must not cross.

Three groups:

1. `sprite_gen.layers` — the request-level contract (profiles, tracks, stacks).
2. Compatibility pins — the request / curation / manifest surfaces the layer feature
   is allowed to extend but never to change for a run that does not opt in.
3. Doc surface — `docs/layer-tracks.md` is the published SSoT and must stay linked.

Boundary evidence behind the pins lives in `docs/layer-tracks.md` §2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sprite_gen import layers
from sprite_gen.curation import empty_curation

from conftest import run_script

ROOT = Path(__file__).resolve().parents[1]


def _request(**overrides) -> dict:
    """A minimal humanoid rig request: `walk` (base, 2 frames) + `can` (prop, 1 frame)."""
    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": "rigbot"},
        "cell": {"shape": "square", "width": 64, "height": 64, "size": 64},
        "states": {
            "walk": {"frames": 2, "fps": 8, "loop": True, "action": "walk", "track": "base"},
            "can": {"frames": 1, "fps": 8, "loop": False, "action": "prop", "track": "prop_effect"},
        },
        "rig": {
            "profile": "humanoid_biped",
            "landmarks": {
                "walk": {
                    "0": {"root": [32, 40], "crown": [32, 8], "hand_r": [40, 30]},
                    "1": {"root": [32, 41], "crown": [32, 9], "hand_r": [41, 31]},
                },
                "can": {"0": {"root": [10, 10], "crown": [10, 2], "grip": [8, 12]}},
            },
        },
        "layers": {
            "walk_with_can": {
                "stack": [
                    {"state": "walk"},
                    {"state": "can", "from": "grip", "to": "hand_r"},
                ]
            }
        },
    }
    request.update(overrides)
    return request


# ---------------------------------------------------------------- 1. contract


def test_valid_stack_has_no_errors() -> None:
    assert layers.validate_layer_request(_request()) == []


def test_validation_is_idempotent() -> None:
    request = _request()
    request["states"]["walk"]["track"] = "nonsense"
    first = layers.validate_layer_request(request)
    assert first == layers.validate_layer_request(request)


def test_request_without_layer_keys_is_not_a_layer_run() -> None:
    plain = json.loads((ROOT / "tests" / "fixtures" / "run" / "sprite-request.json").read_text(encoding="utf-8"))

    assert layers.has_layer_contract(plain) is False
    assert layers.validate_layer_request(plain) == []
    # An undeclared row is an explicit `base`, not an unknown kind.
    assert layers.state_track(plain, "idle") == "base"


def test_unknown_track_kind_is_rejected() -> None:
    request = _request()
    request["states"]["walk"]["track"] = "torso_only"

    errors = layers.validate_layer_request(request)
    assert any("states.walk.track" in e for e in errors)


def test_humanoid_requires_crown_on_every_frame() -> None:
    request = _request()
    del request["rig"]["landmarks"]["walk"]["1"]["crown"]

    errors = layers.validate_layer_request(request)
    assert any("crown" in e and "[1]" in e for e in errors)


@pytest.mark.parametrize("profile", ["quadruped", "blob_or_tentacle", "prop"])
def test_non_humanoid_profiles_do_not_require_crown(profile: str) -> None:
    request = _request()
    request["rig"]["profile"] = profile
    for frames in request["rig"]["landmarks"].values():
        for point_map in frames.values():
            point_map.pop("crown", None)

    assert layers.validate_layer_request(request) == []


def test_root_is_required_by_every_profile() -> None:
    request = _request()
    request["rig"]["profile"] = "blob_or_tentacle"
    del request["rig"]["landmarks"]["walk"]["0"]["root"]

    errors = layers.validate_layer_request(request)
    assert any("'root'" in e for e in errors)


def test_landmark_must_cover_the_whole_frame_pool_including_takes() -> None:
    request = _request()
    request["states"]["walk"]["takes"] = [{"label": "blink", "frames": 1}]

    errors = layers.validate_layer_request(request)
    assert any("missing frame(s) [2]" in e for e in errors)


def test_landmark_declared_on_some_frames_only_is_rejected() -> None:
    request = _request()
    request["rig"]["landmarks"]["walk"]["1"].pop("hand_r")
    # `to=hand_r` now resolves on frame 0 but not frame 1 — a half-declared row.
    errors = layers.validate_layer_request(request)
    assert any("whole row" in e for e in errors)


def test_non_integer_and_out_of_cell_landmarks_are_rejected() -> None:
    request = _request()
    request["rig"]["landmarks"]["walk"]["0"]["root"] = [32.5, 40]
    request["rig"]["landmarks"]["walk"]["1"]["crown"] = [32, 999]

    errors = layers.validate_layer_request(request)
    assert any("integers" in e for e in errors)
    assert any("outside the 64x64 cell" in e for e in errors)


@pytest.mark.parametrize("coordinate", [[True, 8], [32, False]])
def test_boolean_landmark_coordinates_are_rejected(coordinate: list) -> None:
    """JSON `true` is not the integer 1 here.

    `bool` is an `int` subclass in Python, so a coordinate check written as
    `isinstance(value, int)` accepts `[true, 8]` and silently composes at x=1.
    The contract says integers, never booleans (§3), and this is the test that
    holds that line — the float and out-of-cell cases cannot, because they fail
    on a different branch.
    """
    request = _request()
    request["rig"]["landmarks"]["walk"]["0"]["crown"] = coordinate

    errors = layers.validate_layer_request(request)
    assert any("crown" in e and "integers" in e for e in errors), errors


def test_unknown_profile_is_rejected() -> None:
    request = _request()
    request["rig"]["profile"] = "mecha"

    assert any("rig.profile" in e for e in layers.validate_layer_request(request))


def test_stack_needs_exactly_one_body_element() -> None:
    request = _request()
    request["states"]["can"]["track"] = "base"

    errors = layers.validate_layer_request(request)
    assert any("exactly one body element" in e for e in errors)


def test_full_body_override_forbids_action_overlay() -> None:
    request = _request()
    request["states"]["walk"]["track"] = "full_body_override"
    request["states"]["can"]["track"] = "action_overlay"
    request["states"]["can"]["frames"] = 2
    request["rig"]["landmarks"]["can"]["1"] = dict(request["rig"]["landmarks"]["can"]["0"])

    errors = layers.validate_layer_request(request)
    assert any("forbids" in e and "action_overlay" in e for e in errors)


def test_body_element_must_be_first_in_draw_order() -> None:
    request = _request()
    request["layers"]["walk_with_can"]["stack"].reverse()

    errors = layers.validate_layer_request(request)
    assert any("must be stack[0]" in e for e in errors)


def test_overlay_frame_count_must_match_body_or_be_one() -> None:
    request = _request()
    request["states"]["can"]["frames"] = 3
    for index in ("1", "2"):
        request["rig"]["landmarks"]["can"][index] = dict(request["rig"]["landmarks"]["can"]["0"])

    errors = layers.validate_layer_request(request)
    assert any("holds a single frame" in e for e in errors)


def test_alignment_landmark_must_exist_on_both_sides() -> None:
    request = _request()
    request["layers"]["walk_with_can"]["stack"][1]["to"] = "hand_l"

    errors = layers.validate_layer_request(request)
    assert any("aligns to='hand_l'" in e for e in errors)


def test_unknown_stack_element_key_is_rejected() -> None:
    request = _request()
    request["layers"]["walk_with_can"]["stack"][1]["rotate"] = 15

    errors = layers.validate_layer_request(request)
    assert any("unknown key(s) ['rotate']" in e for e in errors)


def test_composite_name_must_not_collide_with_a_request_state() -> None:
    request = _request()
    request["layers"]["walk"] = request["layers"].pop("walk_with_can")

    errors = layers.validate_layer_request(request)
    assert any("must not collide with a request state" in e for e in errors)


def test_stack_without_a_rig_is_rejected() -> None:
    request = _request()
    del request["rig"]

    errors = layers.validate_layer_request(request)
    assert any("needs a rig block" in e for e in errors)


def test_require_valid_layer_request_reports_every_violation_at_once() -> None:
    request = _request()
    request["states"]["walk"]["track"] = "torso_only"
    request["rig"]["profile"] = "mecha"

    with pytest.raises(SystemExit) as excinfo:
        layers.require_valid_layer_request(request)
    message = str(excinfo.value)
    assert "rig.profile" in message and "states.walk.track" in message


def test_resolved_stack_fills_pivot_defaults() -> None:
    resolved = layers.resolve_stack(_request(), "walk_with_can")

    assert resolved[0] == {"state": "walk", "from": "root", "to": "root",
                           "mask": None, "allow_clip": False, "revision": None}
    assert resolved[1]["from"] == "grip" and resolved[1]["to"] == "hand_r"


def test_an_element_may_pin_the_generation_its_landmarks_were_declared_against() -> None:
    request = _request()
    request["layers"]["walk_with_can"]["stack"][1]["revision"] = ["a1b2c3d4e5f6"]

    assert layers.validate_layer_request(request) == []
    assert layers.resolve_stack(request, "walk_with_can")[1]["revision"] == ["a1b2c3d4e5f6"]


@pytest.mark.parametrize("pin", ["a1b2c3d4e5f6", [], [7]])
def test_a_malformed_revision_pin_is_rejected(pin) -> None:
    request = _request()
    request["layers"]["walk_with_can"]["stack"][1]["revision"] = pin

    assert any("revision" in e for e in layers.validate_layer_request(request))


@pytest.mark.parametrize("key,value", [("fps", "8"), ("fps", 0), ("fps", True),
                                       ("loop", "yes"), ("loop", 1)])
def test_composite_playback_keys_are_typed(key: str, value) -> None:
    """`fps` / `loop` are copied into the composite manifest, so they are typed here."""
    request = _request()
    request["layers"]["walk_with_can"][key] = value

    assert any(f"layers.walk_with_can.{key}" in e for e in layers.validate_layer_request(request))


def test_landmark_map_reads_a_whole_frame_and_never_invents_one() -> None:
    request = _request()

    assert layers.landmark_map(request, "can", 0) == {"root": (10, 10), "crown": (10, 2),
                                                      "grip": (8, 12)}
    # A curated clone instance lives outside the declared pool: no map, no guess.
    assert layers.landmark_map(request, "can", 7) is None


# --------------------------------------------------- 2. compatibility pins


NON_LAYER_REQUEST_KEYS = {
    "version", "kind", "engine", "character", "cell", "chroma_key",
    "states", "style", "motion_phase_guides", "layout",
}

NON_LAYER_MANIFEST_KEYS = {
    "characterId", "engine", "game_input", "degraded_static_fallback",
    "curation_applied", "frame_variant", "sprite_sheet_alpha",
    "sprite_sheet_alpha_report", "base_image", "cell", "chroma_key",
    "animation", "frame_layout",
}


def test_prepare_emits_the_documented_non_layer_request_surface(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    result = run_script("prepare_sprite_run.py", "--out-dir", str(out_dir),
                        "--character-id", "rigbot")
    assert result.returncode == 0, result.stdout + result.stderr

    request = json.loads((out_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert set(request) == NON_LAYER_REQUEST_KEYS
    assert layers.has_layer_contract(request) is False


def test_prepare_does_not_carry_layer_keys_yet(tmp_path: Path) -> None:
    """Canary for the audit finding that motivates the explicit-carry rule.

    `prepare` rebuilds the request from a whitelist, so a `rig` / `track` key
    passed through `--request-json` is dropped without a word (the same reason
    `states.<state>.takes` must be hand-written today). The layer CLI step has to
    add both to the carried set; when it does, this test fails and is replaced by
    the positive assertion. Contract: `docs/layer-tracks.md` §7.
    """
    out_dir = tmp_path / "run"
    inline = json.dumps({
        "rig": {"profile": "humanoid_biped", "landmarks": {}},
        "states": {"idle": {"frames": 2, "track": "base"}},
    })
    result = run_script("prepare_sprite_run.py", "--out-dir", str(out_dir),
                        "--character-id", "rigbot", "--request-json", inline)
    assert result.returncode == 0, result.stdout + result.stderr

    request = json.loads((out_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert "rig" not in request
    assert "track" not in request["states"]["idle"]


def test_non_layer_run_manifest_surface_is_unchanged(fixture_run_dir: Path) -> None:
    extract = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir))
    assert extract.returncode == 0, extract.stdout + extract.stderr
    compose = run_script("compose_sprite_atlas.py", "--run-dir", str(fixture_run_dir))
    assert compose.returncode == 0, compose.stdout + compose.stderr

    manifest = json.loads((fixture_run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == NON_LAYER_MANIFEST_KEYS
    for row in manifest["animation"]["rows"].values():
        assert "track" not in row


def test_layer_data_never_enters_the_curation_sidecar() -> None:
    """The sidecar is human-edit truth, generation-stamped and droppable on re-extract.

    Layer declarations are machine-read request truth, so they must not appear in
    `curation.json` — otherwise a re-extract could silently drop a rig.
    """
    assert set(empty_curation()) == {"version", "kind", "states"}
    schema_doc = (ROOT / "sprite_gen" / "curation.py").read_text(encoding="utf-8")
    header = schema_doc.split("SCHEMA_VERSION")[0]
    for reserved in ("\"rig\"", "\"tracks\"", "\"landmarks\""):
        assert reserved not in header


# ------------------------------------------------------------- 3. doc surface

FORBIDDEN_PATTERNS = (
    (re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+"), "an absolute personal home path"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "an e-mail address"),
)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_layer_doc_declares_the_whole_vocabulary() -> None:
    text = _read("docs/layer-tracks.md")

    for profile in layers.PROFILES:
        assert profile in text, f"docs/layer-tracks.md does not name profile {profile}"
    for track in layers.TRACKS:
        assert track in text, f"docs/layer-tracks.md does not name track {track}"
    for key in ("rig.landmarks", "layers", "allow_clip", "full_body_override"):
        assert key in text
    assert "sprite_gen/layers.py" in text


def test_layer_doc_is_publishable() -> None:
    text = _read("docs/layer-tracks.md")

    for pattern, why in FORBIDDEN_PATTERNS:
        assert not pattern.search(text), f"docs/layer-tracks.md leaks {why}"


def test_layer_doc_is_linked_from_the_hub_and_the_contracts() -> None:
    assert "docs/layer-tracks.md" in _read("SKILL.md")
    assert "layer-tracks.md" in _read("docs/run-contract.md")
    assert "layer-tracks.md" in _read("docs/architecture.md")
