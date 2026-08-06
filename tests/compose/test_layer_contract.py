# SPDX-License-Identifier: Apache-2.0
"""Layer-track contract: declaration validation + the compatibility boundaries it must not cross.

Three groups:

1. `sprite_gen.compose.layers` — the request-level contract (profiles, tracks, stacks).
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

from sprite_gen.compose import layers
from sprite_gen.gen import prepare
from sprite_gen.curate.curation import empty_curation

from conftest import run_script

ROOT = Path(__file__).resolve().parents[2]


def _request(**overrides) -> dict:
    """A minimal humanoid rig request: `walk` (base, 2 frames) + `can` (prop, 1 frame).

    The prop row declares `root` and `grip` and no `crown` — a watering can has no
    정수리, and the contract judges required landmarks by profile *and* track
    (§3.1). It is the documented example, so the whole file exercises that rule.
    """
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
                "can": {"0": {"root": [10, 10], "grip": [8, 12]}},
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


def test_a_prop_row_in_a_humanoid_run_is_not_held_to_the_body_requirement() -> None:
    """A watering can has no 정수리 (§3.1).

    The rig profile answers "what is this character", the track answers "what does
    this row draw" — and `crown` is a body landmark. Requiring it on a
    `prop_effect` row leaves one way out: declare a fake crown on the can, which
    puts a made-up coordinate on the row the composer aligns against. The
    requirement is judged by profile AND track so that way out is never needed.
    """
    request = _request()
    assert "crown" not in request["rig"]["landmarks"]["can"]["0"]

    assert layers.validate_layer_request(request) == []


@pytest.mark.parametrize("track", ["base", "action_overlay", "full_body_override"])
def test_a_body_bearing_row_keeps_the_profile_requirement(track: str) -> None:
    """The narrowing is `prop_effect` only — every row that draws the body keeps `crown`."""
    request = _request()
    request["states"]["can"]["track"] = track

    errors = layers.validate_layer_request(request)
    assert any("miss required landmark 'crown'" in e and "rig.landmarks.can" in e
               for e in errors), errors


def test_a_prop_row_still_requires_its_own_root() -> None:
    """Narrowed, not exempt: a prop is held to the `prop` profile, which needs `root`."""
    request = _request()
    del request["rig"]["landmarks"]["can"]["0"]["root"]

    errors = layers.validate_layer_request(request)
    assert any("miss required landmark 'root'" in e and "rig.landmarks.can" in e
               for e in errors), errors


@pytest.mark.parametrize("profile", sorted(layers.PROFILES))
def test_required_landmarks_reads_profile_and_track_together(profile: str) -> None:
    for track in layers.TRACKS:
        expected = ("root",) if track == "prop_effect" else layers.PROFILES[profile]["required"]
        assert layers.required_landmarks(profile, track) == expected


# An unknown `rig.profile` owns exactly one error. The three tests below pin that
# from both sides — the helper and the whole validator — because the requirement
# is an ORDER: profile validity is judged before the track narrowing, so a
# `prop_effect` row can never borrow the `prop` profile out of a run that
# declared no valid profile at all.

UNKNOWN_PROFILES = [
    pytest.param("mecha", id="unknown-string"),
    pytest.param(None, id="null"),
    pytest.param(3, id="number"),
    pytest.param(True, id="bool"),
    pytest.param(["humanoid_biped"], id="list-unhashable"),
    pytest.param({"profile": "humanoid_biped"}, id="dict-unhashable"),
]


@pytest.mark.parametrize("profile", UNKNOWN_PROFILES)
def test_an_unknown_profile_requires_nothing_on_every_track(profile) -> None:
    """Including `prop_effect`, which is the track that has an override to fall back to.

    A list or an object is a JSON shape `rig.profile` can really arrive as, and
    an unhashable value must come back as "requires nothing" rather than as a
    TypeError from the dict lookup.
    """
    for track in layers.TRACKS:
        assert layers.required_landmarks(profile, track) == ()
    assert layers.known_profile(profile) is None


@pytest.mark.parametrize("profile", UNKNOWN_PROFILES)
def test_an_unknown_profile_is_the_only_error_it_causes(profile) -> None:
    """The regression: the run's prop row must not also be told it is missing `root`.

    Judging the track override first made `required_landmarks(<unknown>,
    'prop_effect')` return the `prop` profile's `('root',)`, so an unknown-profile
    run that happened to contain a prop row got a second, derived error next to
    the real one — and a malformed (unhashable) profile crashed the validator
    outright instead of being reported.
    """
    request = _request()
    request["rig"]["profile"] = profile
    # Strip what a prop row would be held to under the override, so a leaked
    # requirement shows up as an error instead of being silently satisfied.
    del request["rig"]["landmarks"]["can"]["0"]["root"]

    errors = layers.validate_layer_request(request)
    assert [e for e in errors if e.startswith("rig.profile must be one of")], errors
    assert not [e for e in errors if "required landmark" in e], errors


def test_a_valid_profile_still_owns_its_requirements() -> None:
    """The guard narrows nothing for a declared profile — the §3.1 rules stand."""
    request = _request()
    del request["rig"]["landmarks"]["walk"]["0"]["crown"]
    del request["rig"]["landmarks"]["can"]["0"]["root"]

    errors = layers.validate_layer_request(request)
    assert not [e for e in errors if e.startswith("rig.profile must be one of")], errors
    assert any("miss required landmark 'crown'" in e and "rig.landmarks.walk" in e
               for e in errors), errors
    assert any("miss required landmark 'root'" in e and "rig.landmarks.can" in e
               for e in errors), errors
    assert layers.known_profile("humanoid_biped") == "humanoid_biped"


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


def test_unknown_composite_key_is_rejected() -> None:
    """The composite spec has an owner, so a typo is a violation and not a silent default.

    `layers.<name>` used to pass over an unknown top-level key while a stack
    element rejected one — the asymmetry the CLI step closes. `loops: false` must
    not ship a composite that loops because nothing read the key.
    """
    request = _request()
    request["layers"]["walk_with_can"]["loops"] = False

    errors = layers.validate_layer_request(request)
    assert any("layers.walk_with_can has unknown key(s) ['loops']" in e for e in errors)
    assert any("allowed: stack, fps, loop" in e for e in errors)


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

    assert layers.landmark_map(request, "can", 0) == {"root": (10, 10), "grip": (8, 12)}
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


def _prepare(tmp_path: Path, request: dict, *args: str):
    out_dir = tmp_path / "run"
    result = run_script("prepare_sprite_run.py", "--out-dir", str(out_dir),
                        "--character-id", "rigbot", "--request-json", json.dumps(request), *args)
    return out_dir, result


def test_prepare_carries_the_layer_keys_into_the_request(tmp_path: Path) -> None:
    """The explicit-carry rule (`docs/layer-tracks.md` §7), formerly a canary.

    `prepare` rebuilds the request from a whitelist, so `rig` / `layers` /
    `states.<state>.track` reach `sprite-request.json` only because they are in
    the carried set by name — never by passthrough.
    """
    rig = {"profile": "humanoid_biped",
           "landmarks": {"idle": {"0": {"root": [10, 20], "crown": [10, 4]},
                                  "1": {"root": [11, 20], "crown": [11, 4]}}}}
    composites = {"idle_only": {"stack": [{"state": "idle"}], "fps": 8, "loop": True}}
    out_dir, result = _prepare(tmp_path, {
        "rig": rig,
        "layers": composites,
        "cell": {"width": 64, "height": 64},
        "states": {"idle": {"frames": 2, "track": "base"}},
    })
    assert result.returncode == 0, result.stdout + result.stderr

    request = json.loads((out_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert request["rig"] == rig
    assert request["layers"] == composites
    assert request["states"]["idle"]["track"] == "base"
    assert layers.has_layer_contract(request) is True
    assert layers.validate_layer_request(request) == []


def test_prepare_writes_no_track_for_an_undeclared_row(tmp_path: Path) -> None:
    """An undeclared row IS `base`; writing that default would make every run a layer run."""
    out_dir, result = _prepare(tmp_path, {"states": {"idle": {"frames": 2}}})
    assert result.returncode == 0, result.stdout + result.stderr

    request = json.loads((out_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert set(request) == NON_LAYER_REQUEST_KEYS
    assert "track" not in request["states"]["idle"]
    assert layers.has_layer_contract(request) is False


def test_prepare_names_every_key_it_drops(tmp_path: Path) -> None:
    """The whitelist drop is observable — that is what ends the `takes` class of loss.

    `states.<state>.takes` is a documented first-class key (`run-contract.md` §2)
    that has to be written into `sprite-request.json` after the fact; it went
    missing without a word for as long as `prepare` said nothing about it.
    """
    out_dir, result = _prepare(tmp_path, {
        "states": {"idle": {"frames": 2, "takes": [{"label": "blink", "frames": 1}],
                            "breathe": {"depth": 0.06}}},
        "notes": "hand-written",
        "character": {"id": "someone-else"},
    })
    assert result.returncode == 0, result.stdout + result.stderr

    assert "dropped top-level request key(s) ['notes']" in result.stderr
    assert "ignored incoming ['character']" in result.stderr
    assert "dropped states.idle key(s) ['breathe', 'takes']" in result.stderr
    request = json.loads((out_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert "takes" not in request["states"]["idle"]
    assert request["character"]["id"] == "rigbot"


def test_prepare_says_nothing_when_the_request_is_carried_whole(tmp_path: Path) -> None:
    """A note that fires on every run is a note nobody reads."""
    _out_dir, result = _prepare(tmp_path, {
        "cell": {"width": 64, "height": 64},
        "states": {"idle": {"frames": 2, "fps": 4, "loop": True, "action": "idle"}},
        "style": "flat",
    })

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[prepare]" not in result.stderr


def test_prepare_rejects_an_invalid_rig_before_scaffolding_the_run(tmp_path: Path) -> None:
    """Filesystem-free validation, so a bad declaration never leaves a half-made run."""
    out_dir, result = _prepare(tmp_path, {
        "cell": {"width": 64, "height": 64},
        "states": {"idle": {"frames": 2, "track": "hover"}},
        "rig": {"profile": "mecha", "landmarks": {"idle": {"0": {"root": [10, 20]}}}},
    })

    assert result.returncode != 0
    assert "invalid layer contract" in result.stderr
    assert "rig.profile must be one of" in result.stderr
    assert "states.idle.track must be one of" in result.stderr
    assert not out_dir.exists()


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
    schema_doc = (ROOT / "sprite_gen" / "curate" / "curation.py").read_text(encoding="utf-8")
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
    assert "sprite_gen/compose/layers.py" in text


def test_layer_doc_is_publishable() -> None:
    text = _read("docs/layer-tracks.md")

    for pattern, why in FORBIDDEN_PATTERNS:
        assert not pattern.search(text), f"docs/layer-tracks.md leaks {why}"


def test_layer_doc_is_linked_from_the_hub_and_the_contracts() -> None:
    assert "docs/layer-tracks.md" in _read("SKILL.md")
    assert "layer-tracks.md" in _read("docs/run-contract.md")
    assert "layer-tracks.md" in _read("docs/architecture.md")


def test_the_bake_command_is_documented_where_an_agent_looks() -> None:
    """A CLI nobody can find is a library. The hub names it; the doc explains it."""
    skill = _read("SKILL.md")
    doc = _read("docs/layer-tracks.md")

    assert "sprite-gen compose-layers" in skill
    assert "scripts/compose_layers.py" in skill, "the script map's required_scripts list"
    for clause in ("sprite-gen compose-layers", "--names", "sprite_gen/compose/compose_layers.py"):
        assert clause in doc, f"docs/layer-tracks.md does not document {clause}"


def test_the_doc_owns_the_prepare_carry_and_drop_rule() -> None:
    """The whitelist and its stderr note are contract text, not an implementation detail."""
    doc = _read("docs/layer-tracks.md")

    assert "[prepare] dropped" in doc
    assert "states.<state>.takes" in doc
    for key in ("rig", "layers", "track"):
        assert key in doc, f"docs/layer-tracks.md does not name carried key {key}"
    # The per-state rebuild list is quoted in the doc's example note; a change to
    # the carried set has to reach the doc, not just the code.
    assert str(list(prepare.STATE_KEYS_CARRIED)) in doc
