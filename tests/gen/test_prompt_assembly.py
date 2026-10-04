# SPDX-License-Identifier: Apache-2.0
"""Every prompt the engine puts together with `--handed`, over the whole option table, drawn as text only.

A prompt is the caller's text plus the engine's pieces (`sprite_gen.gen.prompt_parts`). Without `--handed`
every prompt string is 2.22.0's, byte for byte (`test_prompt_freeze.py`). With it, the prompt is that same
prompt with the handed piece put in: nothing else changes. The table below is every still, clip, start-still
and correction prompt the options can make; each one is checked for that, for a handed sentence said twice,
for a part called bare and dressed at once, for an arm said to swing in a state that does not step, for
anything asked for (the caller's text, `--character`, a handed item, the key line) that never reached it, and
for the notes on the caller's words.
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
# --handed: none, one item, two on one wrist, one on each wrist, one on an ear (no arm sentence)
HANDED = {
    "none": None,
    "watch": ["the black smartwatch=left wrist"],
    "watch+bracelet": ["the black smartwatch=left wrist", "the gold bracelet=left wrist"],
    "watch+ribbon": ["the black smartwatch=left wrist", "the red ribbon=right wrist"],
    "ear-ribbon": ["the red ribbon=left ear"],
}
SUBJECT = "A small fox adventurer, 2D game sprite, full body"
# The caller's own text: what it already says about the key, the turn and the item. `turn` / `item` is
# the side its words name, to tell an agreeing repeat from a conflict with the options.
USER_TEXT = {
    "plain": {"text": f"{SUBJECT}."},
    "names-key": {"text": f"{SUBJECT}, magenta chroma-key background.", "key": "magenta"},
    "names-key-hex": {"text": f"{SUBJECT} on a flat #FF00FF background.", "key": "magenta"},
    "says-turn": {"text": f"{SUBJECT}, facing right.", "turn": "right"},
    "says-item-side": {"text": f"{SUBJECT}, wearing a black smartwatch on its left wrist.", "item": "left"},
    "says-other-item-side": {"text": f"{SUBJECT}, wearing a black smartwatch on its right wrist.", "item": "right"},
}
# --character: words the motion paragraphs below never use, so a clip that names the character got them from here
CHARACTER = "A small fox adventurer in a green cloak"
STATES = ("idle", "walk", "run", "jump", "attack", "cheer")
MODELS = {"pro": "grok-imagine-video-1.5", "lite": "grok-imagine-video-1.5-lite"}
# --motion: the built-in sentence, the caller's own, one that quotes an engine rule, one that walks the other way
MOTION = {
    "built-in": {"text": None},
    "own": {"text": "The fox walks with a cheerful bounce, swinging both arms."},
    "quotes-engine": {"text": "The fox walks with a cheerful bounce. Camera completely locked, no zoom, no pan, no reframing."},
    "says-turn": {"text": "The fox walks to the right with a cheerful bounce.", "turn": "right"},
}
SIDE_HOLD = "\n\nThe subject stays in exact side view, facing {facing}. No turning around."


def _items(key: str) -> list[h.Handed] | None:
    return h.parse_all(HANDED[key]) or None


def _views() -> list[tuple[str, str | None]]:
    """(view, facing): a turned view both ways, front and back once."""
    return [(view, facing) for view in h.VIEWS for facing in (FACINGS if view in h.LATERAL_VIEWS else (None,))]


def _still_rows():
    for user, (view, facing), handed, refs in itertools.product(USER_TEXT, _views(), HANDED, (False, True)):
        make = lambda items, u=USER_TEXT[user]["text"], v=view, f=facing, r=refs: gen.still_prompt(
            u, view=v, facing=f, handed=items, refs=r, key="magenta" if r else None)
        row = {"user": user, "facing": facing, "handed": handed, "plain": make(None).text}
        yield f"still/{user}/{view}@{facing}/{handed}/{'refs' if refs else 'text'}", make(_items(handed)), row
    # a reference run turned with --facing alone, no --direction
    for user, facing in itertools.product(USER_TEXT, FACINGS):
        parts = gen.still_prompt(USER_TEXT[user]["text"], facing=facing, refs=True, key="magenta")
        yield f"still/{user}/ref@{facing}", parts, {"user": user, "facing": facing, "handed": "none", "plain": parts.text}


def _clip_rows():
    for (view, facing), handed, state, model, motion in itertools.product(_views(), HANDED, STATES, MODELS, MOTION):
        if MOTION[motion]["text"] and state != "walk":
            continue
        make = lambda items, v=view, s=state, f=facing or "right", m=MOTION[motion]["text"], mo=MODELS[model]: (
            batch.clip_prompt_parts(v, s, CHARACTER, facing=f, motion=m, model=mo, handed=items))
        parts = make(_items(handed))
        row = {"user": None, "motion": motion, "facing": facing, "handed": handed, "state": state, "character": CHARACTER,
               "plain": make(None).text}
        yield f"clip/{view}@{facing}/{state}/{model}/{motion}/{handed}", parts, row
        if view == "side":  # handed on to `sprite-gen video --direction side`
            yield (f"clip+video/{view}@{facing}/{state}/{model}/{motion}/{handed}",
                   video_mod.side_view_prompt(parts.text, facing), {**row, "clip": parts})


def _start_still_rows():
    for view, key, handed in itertools.product(batch.WALK_START_TEXT, chroma.KEY_BACKGROUND_TEXT, HANDED):
        text = batch.walk_start_prompt(view, key, _items(handed))
        row = {"user": None, "facing": None, "handed": handed, "key": key, "plain": batch.walk_start_prompt(view, key)}
        yield f"start-still/{view}/{key}/{handed}", prompt_parts.Prompt(text, caller=""), row
        # handed back to `gen --ref --transparent --direction --handed`, as an agent reading `video-prompt` may
        again = gen.still_prompt(text, view=view, handed=_items(handed), refs=True, key=key)
        plain_again = gen.still_prompt(text, view=view, refs=True, key=key).text
        yield f"start-still+gen/{view}/{key}/{handed}", again, {**row, "own": text, "plain": plain_again}


ROWS = [*_still_rows(), *_clip_rows(), *_start_still_rows()]
_HANDED_ROWS = [r for r in ROWS if r[2]["handed"] != "none"]

# -- the checks ------------------------------------------------------------------------------------

_TURN_WORDS = re.compile(
    r"\bfac(?:ing|es?) (right|left)\b|\bto the (right|left)\b|\btoward the (?:upper |lower )?(right|left)\b"
    r"|\bthe (right|left) edge\b|\b(right|left) here means\b", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    return [" ".join(part.split()).lower().rstrip(".!?") for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]


def _turns(text: str) -> set[str]:
    return {next(side for side in match.groups() if side).lower() for match in _TURN_WORDS.finditer(text)}


def _handed_text(parts: prompt_parts.Prompt) -> str:
    piece = parts.piece("handed")
    return piece.text if piece is not None and piece.added else ""


def _without_handed(parts: prompt_parts.Prompt) -> str:
    """The prompt with its handed piece taken out: the caller's text and every other piece, as put together."""
    return parts.own + "".join(piece.sep + piece.text for piece in parts.pieces if piece.added and piece.topic != "handed")


def test_the_table_covers_every_place_a_prompt_is_put_together() -> None:
    kinds = {name.split("/")[0] for name, _, _ in ROWS}
    assert kinds == {"still", "clip", "clip+video", "start-still", "start-still+gen"}
    assert len(_HANDED_ROWS) > 1000


@pytest.mark.parametrize("name,parts,row", ROWS, ids=[name for name, _, _ in ROWS])
def test_handed_puts_in_its_piece_and_changes_nothing_else(name, parts, row) -> None:
    """With --handed the prompt is the one without it (2.22.0's) and the handed piece; without it, there is none."""
    if name.startswith("clip+video"):
        assert parts.text == row["clip"].text + SIDE_HOLD.format(facing=row["facing"])
        return
    if name.startswith("start-still/"):
        handed = row["handed"] != "none"
        assert parts.text == row["plain"] + (" " + h.text(_items(row["handed"]), name.split("/")[1]) if handed else "")
        return
    assert _without_handed(parts) == row["plain"], name
    if row["handed"] == "none":
        assert parts.piece("handed") is None and parts.text == row["plain"]


@pytest.mark.parametrize("name,parts,row", _HANDED_ROWS, ids=[name for name, _, _ in _HANDED_ROWS])
def test_no_handed_sentence_is_said_twice(name, parts, row) -> None:
    said = _sentences(parts.text)
    for sentence in _sentences(_handed_text(parts)):
        assert said.count(sentence) == 1, (sentence, parts.text)


@pytest.mark.parametrize("name,parts,row", ROWS, ids=[name for name, _, _ in ROWS])
def test_the_notes_say_where_the_callers_words_turn_or_place_the_item_the_other_way(name, parts, row) -> None:
    caller = {**USER_TEXT.get(row["user"] or "", {}), **MOTION.get(row.get("motion") or "", {})}
    facing = row["facing"]
    conflicts = {note["about"] for note in parts.notes if note["kind"] == "conflict"}
    if name.startswith("clip+video") or name.startswith("start-still"):
        return  # the engine's own words handed on: the notes are the first verb's
    if caller.get("turn") not in (None, facing):
        # the caller's own words turn another way than the view: they are left as written, and the notes say so
        assert "facing" in conflicts, name
        assert _turns(parts.text.replace(caller["text"], "")) <= ({facing} if facing else set())
    else:
        assert _turns(parts.text) <= ({facing} if facing else set()), (name, _turns(parts.text))
        assert "facing" not in conflicts

    if row["handed"] not in ("none", "ear-ribbon") and caller.get("item") == "right":
        assert "handed" in conflicts, name  # the smartwatch is --handed left
    else:
        assert "handed" not in conflicts, parts.notes


_CLIP_ROWS = [r for r in _HANDED_ROWS if r[0].startswith("clip/")]
# (a part or limb said bare, the same one said to wear an item): never both in one prompt
_BARE_AND_WORN = [
    (r"\bThe (\w[\w ]*?) nearer the viewer stays bare\b", "stays? on the {0} nearer the viewer"),
    (r"\bthe (arm) nearer the viewer stays bare\b", "on the {0} nearer the viewer"),
    (r"\bthe other (arm), on the far side of the body, stays bare\b", "on the {0} on the far side of the body"),
]


@pytest.mark.parametrize("name,parts,row", _CLIP_ROWS, ids=[r[0] for r in _CLIP_ROWS])
def test_no_clip_calls_a_limb_bare_and_dressed_or_swings_an_arm_that_stands_still(name, parts, row) -> None:
    text, handed = parts.text, _handed_text(parts)
    for bare, worn in _BARE_AND_WORN:
        for match in re.finditer(bare, text):
            assert not re.search(worn.format(re.escape(match.group(1))), text), text
    side = "/side@" in name
    walks = row["state"] in batch.GAIT_STATES
    on_wrist = row["handed"] != "ear-ribbon"
    # the arm sentences: only a stepping side view with an item on a wrist
    assert (h.ARMS_SWING_TEXT in handed) is (side and walks and on_wrist), handed
    assert ("swing" in handed) is (side and walks and on_wrist), handed
    if side and walks and on_wrist:
        # every item is on a wrist here: the far one is named and shows when its arm comes forward
        far = "the red ribbon" if (row["handed"] == "watch+ribbon" and row["facing"] == "left") else "the black smartwatch"
        if row["facing"] == "right" or row["handed"] == "watch+ribbon":
            assert "each time that arm swings forward" in handed and far[4:] in handed, handed
        assert "The wrist nearer the viewer stays bare" not in handed and "wrist in front of the body" not in handed
        assert text.count(h.ARMS_SWING_TEXT) == 1


@pytest.mark.parametrize("name,parts,row", ROWS, ids=[name for name, _, _ in ROWS])
def test_every_part_asked_for_reaches_the_prompt(name, parts, row) -> None:
    """What the caller wrote and what the options ask for is in the prompt."""
    text = " ".join(parts.text.split())
    asked = [USER_TEXT[row["user"]]["text"]] if row["user"] else []
    asked += [MOTION[row["motion"]]["text"]] if row.get("motion") and MOTION[row["motion"]]["text"] else []
    asked += [row["character"]] if row.get("character") else []
    asked += [row["own"]] if row.get("own") else []
    for item in _items(row["handed"]) or []:
        # the one item left unnamed on purpose: on the far side of a side view where nothing steps it into sight
        # (a state that does not step, a part off the wrist), since a clip prompt that names a hidden item draws
        # it on the near arm (docs/video-pipeline.md)
        hidden = (name.startswith("clip") and "/side@" in name
                  and (row["state"] not in batch.GAIT_STATES or not h.limb(item.part))
                  and h.placement(item.side, "side", row["facing"])["depth"] == "far")
        if hidden:
            assert _ARTICLE.sub("", item.item) not in text, text
        else:
            asked.append(_ARTICLE.sub("", item.item))
    for words in asked:
        assert " ".join(words.split()) in text, (words, text)
    if row.get("key"):
        assert chroma.KEY_BACKGROUND_TEXT[row["key"]] in text, text
    if name.startswith("still") and parts.piece("key-background") is not None:
        named = USER_TEXT[row["user"]].get("key")
        assert (chroma.KEY_BACKGROUND_TEXT["magenta"] in text) or named, text


_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


# -- the known cases ---------------------------------------------------------------------------------

def test_a_start_still_handed_back_to_gen_says_its_handed_sentences_once() -> None:
    """The start still's prompt already carries the handed sentences; `gen --direction --handed` does not add
    them a second time (the one piece `prompt_parts` checks for what is already said)."""
    watch = h.parse_all(HANDED["watch"])
    text = batch.walk_start_prompt("front", "green", watch)
    again = gen.still_prompt(text, view="front", handed=watch, refs=True, key="green")
    assert again.piece("handed").added is False
    assert again.text.count(h.text(watch, "front")) == 1


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
    result = gen.generate_image("fake", "a fox facing left, a black smartwatch on its right wrist",
                                tmp_path / "out.png", view="side", facing="right",
                                handed=h.parse_all(["the black smartwatch=left wrist"]))
    assert {note["about"] for note in result.extra["prompt_notes"]} == {"facing", "handed"}
    err = capsys.readouterr().err
    assert '[gen] warning: the text says "facing left" and the engine\'s sentence says right' in err
    quiet = gen.generate_image("fake", "a fox", tmp_path / "quiet.png", view="side", facing="right")
    assert "prompt_notes" not in quiet.extra


def test_video_prompt_names_the_character_in_every_view_of_a_callers_walk() -> None:
    for view, state in itertools.product(h.VIEWS, batch.GAIT_STATES):
        record = clip_prompt.plan_prompt(direction=view, state=state, character=CHARACTER, facing="right",
                                         motion=MOTION["own"]["text"])
        assert record["prompt"].count(CHARACTER) == 1, record["prompt"]


def test_the_facing_correction_is_said_after_the_prompt_as_2_22_0_did() -> None:
    """`--facing-fix regen` sends the first prompt and the correction after it (`facing.prepare_correction`)."""
    assert facing_mod.prompt_suffix("left", retry=True).startswith("CORRECTION REQUIRED: redraw the subject's orientation. "
                                                                   "The subject must be facing left")
