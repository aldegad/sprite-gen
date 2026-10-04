# SPDX-License-Identifier: Apache-2.0
"""Every prompt the engine puts together, over the whole option table, drawn as text only.

A prompt is the caller's text plus the engine's pieces (`sprite_gen.gen.prompt_parts`). The table below
is every still, clip, start-still and correction prompt the options can make; each one is checked for a
sentence said twice, for words that turn the subject both ways, and for more than one key background.
A new option is a new row in a table here, not a new test.

The checks are written here, apart from the engine's own (`prompt_parts`), so they do not agree with it
by construction.
"""

from __future__ import annotations

import itertools
import re

import pytest

from sprite_gen import gen
from sprite_gen.gen import chroma, facing as facing_mod, handedness as h, prompt_parts
from sprite_gen.gen import video as video_mod
from sprite_gen.video import batch, clip_prompt

FACINGS = ("right", "left")
# --handed: none, one item, two on one wrist, one on each wrist
HANDED = {
    "none": None,
    "watch": ["the black smartwatch=left wrist"],
    "watch+bracelet": ["the black smartwatch=left wrist", "the gold bracelet=left wrist"],
    "watch+ribbon": ["the black smartwatch=left wrist", "the red ribbon=right wrist"],
}
SUBJECT = "A small rabbit mascot, 2D game sprite, full body"
# The caller's own text: what it already says about the key, the turn and the item. `turn` / `item` is
# the side its words name, to tell an agreeing repeat from a conflict with the options.
USER_TEXT = {
    "plain": {"text": f"{SUBJECT}."},
    "names-key": {"text": f"{SUBJECT}, on a background of pure magenta.", "key": "magenta"},
    "names-key-coloured": {"text": f"{SUBJECT}, magenta-colored background.", "key": "magenta"},
    "names-key-hex": {"text": f"{SUBJECT} on a flat #FF00FF background.", "key": "magenta"},
    "rules-out-other-key": {"text": f"{SUBJECT}, no green screen."},
    "says-turn": {"text": f"{SUBJECT}, facing right.", "turn": "right"},
    "says-item-side": {"text": f"{SUBJECT}, wearing a black smartwatch on its left wrist.", "item": "left"},
    "says-other-item-side": {"text": f"{SUBJECT}, wearing a black smartwatch on its right wrist.", "item": "right"},
}
STATES = ("idle", "walk", "run", "jump", "attack", "cheer")
MODELS = {"pro": "grok-imagine-video-1.5", "lite": "grok-imagine-video-1.5-lite"}
# --motion: the built-in sentence, the caller's own, one that quotes an engine rule, one that walks the other way
MOTION = {
    "built-in": {"text": None},
    "own": {"text": "The rabbit walks with a cheerful bounce, swinging both arms."},
    "quotes-engine": {"text": "The rabbit walks with a cheerful bounce. Camera completely locked, no zoom, no pan, no reframing."},
    "says-turn": {"text": "The rabbit walks to the right with a cheerful bounce.", "turn": "right"},
}


def _items(key: str) -> list[h.Handed] | None:
    return h.parse_all(HANDED[key]) or None


def _views() -> list[tuple[str, str | None]]:
    """(view, facing): a turned view both ways, front and back once."""
    return [(view, facing) for view in h.VIEWS for facing in (FACINGS if view in h.LATERAL_VIEWS else (None,))]


def _still_rows():
    for user, (view, facing), handed, refs in itertools.product(USER_TEXT, _views(), HANDED, (False, True)):
        parts = gen.still_prompt(USER_TEXT[user]["text"], view=view, facing=facing, handed=_items(handed), refs=refs,
                                 key="magenta" if refs else None)
        row = {"user": user, "facing": facing, "handed": handed}
        yield f"still/{user}/{view}@{facing}/{handed}/{'refs' if refs else 'text'}", parts, row
        if refs and facing:  # the facing correction's regeneration
            retry = parts.replaced("facing", facing_mod.prompt_suffix(facing, retry=True, view=True))
            yield f"still-retry/{user}/{view}@{facing}/{handed}", retry, row
    # a reference run turned with --facing alone, no --direction, and its correction
    for user, facing in itertools.product(USER_TEXT, FACINGS):
        parts = gen.still_prompt(USER_TEXT[user]["text"], facing=facing, refs=True, key="magenta")
        row = {"user": user, "facing": facing, "handed": "none"}
        yield f"still/{user}/ref@{facing}", parts, row
        yield f"still-retry/{user}/ref@{facing}", parts.replaced("facing", facing_mod.prompt_suffix(facing, retry=True)), row


def _clip_rows():
    for (view, facing), handed, state, model, motion in itertools.product(_views(), HANDED, STATES, MODELS, MOTION):
        if MOTION[motion]["text"] and state != "walk":
            continue
        parts = batch.clip_prompt_parts(view, state, "The rabbit mascot", facing=facing or "right",
                                        motion=MOTION[motion]["text"], model=MODELS[model], handed=_items(handed))
        row = {"user": None, "motion": motion, "facing": facing, "handed": handed, "state": state}
        yield f"clip/{view}@{facing}/{state}/{model}/{motion}/{handed}", parts, row
        if view == "side":  # handed on to `sprite-gen video --direction side`
            yield (f"clip+video/{view}@{facing}/{state}/{model}/{motion}/{handed}",
                   video_mod.side_view_prompt(parts.text, facing), row)


def _start_still_rows():
    for view, key, handed in itertools.product(batch.WALK_START_TEXT, chroma.KEY_BACKGROUND_TEXT, HANDED):
        text = batch.walk_start_prompt(view, key, _items(handed))
        row = {"user": None, "facing": None, "handed": handed}
        yield f"start-still/{view}/{key}/{handed}", prompt_parts.Prompt(text, caller=""), row
        # handed back to `gen --ref --transparent --direction --handed`, as an agent reading `video-prompt` may
        again = gen.still_prompt(text, view=view, handed=_items(handed), refs=True, key=key)
        yield f"start-still+gen/{view}/{key}/{handed}", again, row


ROWS = [*_still_rows(), *_clip_rows(), *_start_still_rows()]

# -- the checks ------------------------------------------------------------------------------------

_TURN_WORDS = re.compile(
    r"\bfac(?:ing|es?) (right|left)\b|\bto the (right|left)\b|\btoward the (?:upper |lower )?(right|left)\b"
    r"|\bthe (right|left) edge\b|\b(right|left) here means\b", re.IGNORECASE)
_KEY_ASK = re.compile(
    r"#(?:ff00ff|00ff00)\b|\b(?:magenta|green)(?:[- ]colou?red)? (?:chroma-key )?(?:background|backdrop|screen)\b"
    r"|\bbackground of pure (?:magenta|green)\b", re.IGNORECASE)
_KEY_RULED_OUT = re.compile(r"\bno (?:magenta|green) (?:background|screen)\b", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    return [" ".join(part.split()).lower().rstrip(".!?") for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]


def _turns(text: str) -> set[str]:
    return {next(side for side in match.groups() if side).lower() for match in _TURN_WORDS.finditer(text)}


def _key_asks(text: str) -> int:
    return len(_KEY_ASK.findall(_KEY_RULED_OUT.sub(" ", text)))


def test_the_table_covers_every_place_a_prompt_is_put_together() -> None:
    kinds = {name.split("/")[0] for name, _, _ in ROWS}
    assert kinds == {"still", "still-retry", "clip", "clip+video", "start-still", "start-still+gen"}
    assert len(ROWS) > 1000


@pytest.mark.parametrize("name,parts,row", ROWS, ids=[name for name, _, _ in ROWS])
def test_no_prompt_says_a_sentence_twice_turns_both_ways_or_asks_for_two_keys(name, parts, row) -> None:
    text = parts.text
    said = _sentences(text)
    assert len(said) == len(set(said)), sorted({s for s in said if said.count(s) > 1})

    assert _key_asks(text) <= 1, text

    caller = {**USER_TEXT.get(row["user"] or "", {}), **MOTION.get(row.get("motion") or "", {})}
    facing = row["facing"]
    conflicts = {note["about"] for note in parts.notes if note["kind"] == "conflict"}
    if name.startswith("clip+video"):
        # the clip prompt handed on: its notes were the clip row's, and the side view is not held a second time
        assert parts.piece("side-view").added is False and parts.notes == []
        return
    if caller.get("turn") not in (None, facing):
        # the caller's own words turn another way than the view: they are left as written, and the notes say so
        assert "facing" in conflicts, name
        assert _turns(text.replace(caller["text"], "")) <= ({facing} if facing else set())
    else:
        assert _turns(text) <= ({facing} if facing else set()), (name, _turns(text))
        assert "facing" not in conflicts

    if row["handed"] != "none" and caller.get("item") == "right":
        assert "handed" in conflicts, name  # the smartwatch is --handed left
    else:
        assert "handed" not in conflicts, parts.notes


@pytest.mark.parametrize("name,parts,row", [r for r in ROWS if r[0].startswith("still")], ids=[r[0] for r in ROWS if r[0].startswith("still")])
def test_a_still_carries_the_key_line_unless_its_text_names_a_key(name, parts, row) -> None:
    piece = parts.piece("key-background")
    if piece is None:
        return
    named = USER_TEXT[row["user"]].get("key")
    assert piece.added is (named is None) and piece.found == (named or "")
    assert (chroma.KEY_BACKGROUND_TEXT["magenta"] in parts.text) is (named is None)


# -- the known cases ---------------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,named", [
    ("a rabbit on a background of pure magenta", "magenta"),
    ("a rabbit, magenta-colored background", "magenta"),
    ("a rabbit, magenta coloured backdrop", "magenta"),
    ("background: solid green", "green"),
    ("a rabbit keyed on pure magenta", "magenta"),
    ("a rabbit drawn on a green key", "green"),
    ("a rabbit, no green screen", None),
    ("a rabbit without a green background", None),
    ("a rabbit, not on a magenta background", None),
    ("no green screen; use a magenta background", "magenta"),
    ("a rabbit with no shadow, on a magenta background", "magenta"),
    ("a knight holding a green key", None),
    ("a background of trees with green leaves", None),
    ("a hero in a green screen-printed shirt", None),
])
def test_a_key_background_is_read_however_it_is_worded_and_not_when_ruled_out(prompt: str, named: str | None) -> None:
    """2.22.0 missed "background of pure magenta" and "magenta-colored background", so the key line went on a
    second time, and read "no green screen" as asking for green, so no line went on at all."""
    assert chroma.named_key_background(prompt) == named
    key = "green" if "magenta" in chroma.ruled_out_key_backgrounds(prompt) else "magenta"
    parts = gen.still_prompt(prompt, refs=True, key=key)
    assert parts.piece("key-background").added is (named is None)
    assert parts.text.count("chroma-key fill (#") == (1 if named is None else 0)


def test_a_key_the_text_rules_out_is_not_the_one_the_engine_asks_for() -> None:
    assert chroma.ruled_out_key_backgrounds("a rabbit, no green screen") == {"green"}
    with pytest.raises(SystemExit, match="rules out a green key background"):
        gen.still_prompt("a rabbit, no green screen", refs=True, key="green")
    # the text names the other key itself: nothing is added, so nothing is refused
    named = gen.still_prompt("no green screen; use a magenta background", refs=True, key="green")
    assert named.piece("key-background").found == "magenta" and not named.piece("key-background").added


def test_a_turned_view_says_the_turn_once_and_the_reference_piece_only_pins_the_edge() -> None:
    """With --direction the view sentence says the turn. The reference piece said it again as a full profile
    ("facing left (toward the left edge of the image)"), against a three-quarter view's 45 degrees."""
    plain = gen.still_prompt("a rabbit", facing="left", refs=True)
    assert plain.text.endswith(facing_mod.prompt_suffix("left")) and "must be facing left" in plain.text
    for view in sorted(h.LATERAL_VIEWS):
        text = gen.still_prompt("a rabbit", view=view, facing="left", refs=True).text
        assert "must be facing" not in text
        assert text.endswith("Left here means toward the left edge of the image, regardless of the reference image's "
                             "orientation. Preserve the subject's design.")
    front = gen.still_prompt("a rabbit", view="front", refs=True)
    assert front.piece("facing") is None


def test_a_correction_takes_the_place_of_the_sentence_it_corrects() -> None:
    """The regeneration's prompt was the first prompt plus the correction: the facing sentence twice."""
    parts = gen.still_prompt("a rabbit", facing="right", refs=True, key="magenta")
    retry = parts.replaced("facing", facing_mod.prompt_suffix("right", retry=True)).text
    assert retry.count("The subject must be facing right") == 1 and retry.count("Preserve the subject's design.") == 1
    assert retry.endswith(facing_mod.prompt_suffix("right", retry=True))
    assert chroma.KEY_BACKGROUND_TEXT["magenta"] in retry


def test_a_side_clip_is_held_once_and_a_prompt_turned_the_other_way_is_refused() -> None:
    prompt = batch.build_prompt("side", "walk", None, facing="left")
    assert video_mod.side_view_prompt(prompt, "left").text == prompt
    own = video_mod.side_view_prompt("The fox trots in place.", "left").text
    assert own == "The fox trots in place.\n\nThe subject stays in exact side view, facing left. No turning around."
    with pytest.raises(SystemExit, match="faces left .* facing right"):
        video_mod.side_view_prompt(prompt, "right")


def test_an_engine_rule_the_motion_paragraph_quotes_is_not_said_again() -> None:
    rule = "Camera completely locked, no zoom, no pan, no reframing."
    text = batch.build_prompt("side", "walk", None, motion=f"The fox trots in place. {rule}")
    assert text.count(rule) == 1
    assert "Keep the design, colors and proportions exactly as in the image." in text


def test_video_prompt_warns_when_the_callers_words_turn_the_other_way() -> None:
    record = clip_prompt.plan_prompt(direction="side", state="walk", facing="right", motion="It walks to the left.")
    assert any('"walks to the left"' in line for line in record["warnings"])
    assert record["notes"][0]["kind"] == "conflict"
    quiet = clip_prompt.plan_prompt(direction="side", state="walk", facing="right", motion="It strolls.")
    assert quiet["warnings"] == [] and "notes" not in quiet


def test_gen_reports_what_the_callers_text_says_against_the_options(tmp_path, monkeypatch, capsys) -> None:
    from PIL import Image

    from sprite_gen.gen.base import ProviderRun

    class Backend:
        name = "fake"
        transparency = "native"

        def generate(self, request, workdir):
            Image.new("RGB", (8, 8), (255, 0, 255)).save(request.raw)
            return ProviderRun(self.name, 1, model="test-image", extra={})

    monkeypatch.setattr(gen, "_make_provider", lambda *a, **kw: Backend())
    result = gen.generate_image("fake", "a rabbit facing left, a black smartwatch on its right wrist",
                                tmp_path / "out.png", view="side", facing="right",
                                handed=h.parse_all(["the black smartwatch=left wrist"]))
    assert {note["about"] for note in result.extra["prompt_notes"]} == {"facing", "handed"}
    err = capsys.readouterr().err
    assert '[gen] warning: the text says "facing left" and the engine\'s sentence says right' in err
    quiet = gen.generate_image("fake", "a rabbit", tmp_path / "quiet.png", view="side", facing="right")
    assert "prompt_notes" not in quiet.extra
