"""The compact row prompt (`prompt_profile: "compact"`) is opt-in, short, and keeps the rules that matter."""

from __future__ import annotations

from sprite_gen.gen import prepare


def request(profile=None, directions=None):
    req = {
        "cell": {"width": 256, "height": 256, "safe_margin_x": 24, "safe_margin_y": 24},
        "chroma_key": {"name": "green", "hex": "#00FF00"},
        "character": {"id": "enemy_test_4f", "description": "pixel chibi enemy, facing left"},
        "style": prepare.STYLE_DEFAULT,
        "states": {},
    }
    if profile is not None:
        req["prompt_profile"] = profile
    if directions is not None:
        req["directions"] = directions
    return req


ENTRY = {"frames": 4, "fps": 3, "loop": True, "action": "ready stance, weapon low in front in both hands"}


def test_default_profile_is_unchanged_and_long():
    full = prepare.row_prompt(request(), "ready1", ENTRY)
    assert "Anchor lock" in full
    assert "Output only the sprite strip image." in full


def test_compact_profile_is_much_shorter_and_keeps_the_key_rules():
    full = prepare.row_prompt(request(), "ready1", ENTRY)
    compact = prepare.row_prompt(request("compact"), "ready1", ENTRY)
    assert len(compact) < len(full) * 0.35
    assert len(compact) < 1800
    for must in ("exactly 4 full-body frames", "256x256", "#00FF00", "24px", "24px",
                 "ready stance, weapon low", "The last frame loops back to the first.",
                 "Output only the sprite strip image."):
        assert must in compact, must
    assert "Anchor lock" not in compact
    assert "Transparency and artifact rules" not in compact


def test_compact_profile_skips_loop_sentence_for_one_shot_states():
    one_shot = dict(ENTRY, loop=False, action="one strike toward the left")
    compact = prepare.row_prompt(request("compact"), "attack1", one_shot)
    assert "loops back" not in compact


def test_compact_profile_falls_back_to_full_for_direction_runs():
    compact_dir = prepare.row_prompt(request("compact", directions={"set": ["left"]}), "ready1", ENTRY)
    assert "Anchor lock" in compact_dir


def test_profiles_are_the_two_known_values():
    assert prepare.PROMPT_PROFILES == ("full", "compact")


def test_loop_sentence_is_not_repeated_when_the_action_already_says_it():
    entry = dict(ENTRY, action="a loop; the last frame loops back to the first")
    compact = prepare.row_prompt(request("compact"), "ready1", entry)
    assert compact.count("loops back") == 1
