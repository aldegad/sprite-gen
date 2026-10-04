# SPDX-License-Identifier: Apache-2.0
"""Attaching the engine's sentences to a prompt: the one place that checks what is already said.

A prompt is the caller's own text and then the engine's pieces: the view sentence, the turn over a
reference, where a handed item is, the key background line, the layout guide; for a clip the hold, the
frame and camera rules, a Lite model's sentences and the side view's hold. Every piece goes on through
`Prompt.add`, and a piece the prompt already says is not said again:

- a sentence the prompt already carries, word for word, is dropped from the piece (a start still's prompt
  handed back to `gen --direction --handed`, a caller's motion paragraph that quotes an engine rule, two
  items whose sentences come out the same);
- a piece whose topic the prompt already covers is left out whole (`ALREADY_SAID`: a key background the
  caller named, a side view the clip prompt already holds), and one the prompt rules out is refused.

The caller's text is never edited. Where it and a piece disagree (`facing left` in the text, `--facing
right` on the command) or it repeats what a piece says, `Prompt.notes` says which words, and the verb
prints them: the engine cannot tell which of the two the caller meant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .chroma import named_key_background, ruled_out_key_backgrounds

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def sentences(text: str) -> list[str]:
    """`text` cut into sentences, each as written."""
    return [part for part in _SENTENCE_END.split(" ".join(text.split())) if part]


def _same(sentence: str) -> str:
    """What makes two sentences the same one: their words, whatever the case or the closing mark."""
    return sentence.lower().rstrip(".!? ")


# Words that turn a subject to one side of the picture, as a caller writes them.
_TURN = re.compile(
    r"\b(?:fac(?:ing|es?)|look(?:ing|s)?|turn(?:ed|ing|s)?|head(?:ing|s)?|walk(?:ing|s)?|run(?:ning|s)?|mov(?:ing|es?))"
    r"\s+(?:to(?:wards?)?\s+)?(?:the\s+)?(left|right)\b", re.IGNORECASE)
_SIDE = re.compile(r"\b(left|right)\b", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:the|a|an|its|his|her|their)\s+", re.IGNORECASE)
_CLAUSE_END = re.compile(r"[.;:!?\n]+")


# `facing` of a piece whose view is turned to neither side (front, back).
NO_TURN = "neither"


def turns_said(text: str) -> list[tuple[str, str]]:
    """(the words, left|right) for every place `text` turns the subject to a side."""
    return [(match.group(0), match.group(1).lower()) for match in _TURN.finditer(text)]


def _key_said(prompt: "Prompt", *, key: str | None = None, **_: Any) -> tuple[str, str] | None:
    named = named_key_background(prompt.text)
    if named:
        return f"the prompt already asks for a {named} key background", named
    if key is not None and key in ruled_out_key_backgrounds(prompt.text):
        raise SystemExit(f"prompt: the text rules out a {key} key background and the engine would ask for one; "
                         "choose the other key or drop the line from the prompt")
    return None


_SIDE_VIEW = re.compile(r"\bseen from the exact side, facing (left|right)\b", re.IGNORECASE)


def _side_view_said(prompt: "Prompt", *, facing: str | None = None, **_: Any) -> tuple[str, str] | None:
    held = {match.group(1).lower() for match in _SIDE_VIEW.finditer(prompt.text)}
    if not held:
        return None
    if facing in ("left", "right") and held != {facing}:
        raise SystemExit(f"prompt: the text says the side view faces {' and '.join(sorted(held))} and the engine "
                         f"would hold it facing {facing}; ask for the prompt and the clip with the same facing")
    return "the prompt already says the side view and its facing", facing or ""


# topic -> what in the prompt makes a piece on that topic unnecessary: (why, what was found), or None.
# A check raises SystemExit where the prompt says the opposite of the piece in the engine's own words.
ALREADY_SAID: dict[str, Callable[..., tuple[str, str] | None]] = {
    "key-background": _key_said,
    "side-view": _side_view_said,
}


@dataclass(frozen=True)
class Piece:
    """One engine piece and what became of it: attached whole, attached without the sentences the prompt
    already had (`why`), or left out (`added` False, `why`, and `found` — the key the prompt named). `asked`
    and `params` are what `add` was given, so `Prompt.replaced` can put the piece on again."""

    topic: str
    text: str
    sep: str
    added: bool
    why: str = ""
    found: str = ""
    asked: str = ""
    params: dict[str, Any] = field(default_factory=dict, compare=False)


class Prompt:
    """A prompt being put together: `own` (the first text, the caller's or a template's) and then the pieces.
    `caller` is the part of it the caller wrote, which the notes read; by default all of `own`."""

    def __init__(self, own: str, *, caller: str | None = None, sep: str = "\n\n") -> None:
        self.own = own
        self.caller = own if caller is None else caller
        self.sep = sep
        self.pieces: list[Piece] = []
        self.notes: list[dict[str, str]] = []

    @property
    def text(self) -> str:
        return self.own + "".join(piece.sep + piece.text for piece in self.pieces if piece.added)

    def piece(self, topic: str) -> Piece | None:
        return next((piece for piece in self.pieces if piece.topic == topic), None)

    def add(self, topic: str, text: str, *, sep: str | None = None, **params: Any) -> Piece:
        """Attach `text` as the piece on `topic` unless the prompt already says it. `params` are what the piece
        claims (`facing`, `key`, `handed`): they feed `ALREADY_SAID` and the notes on the caller's text."""
        sep = self.sep if sep is None else sep
        asked = text
        why = found = ""
        said = ALREADY_SAID[topic](self, **params) if topic in ALREADY_SAID else None
        if said:
            why, found = said
            text = ""
        elif text:
            have = {_same(sentence) for sentence in sentences(self.text)}
            kept = []
            for sentence in sentences(text):
                if _same(sentence) not in have:
                    kept.append(sentence)
                    have.add(_same(sentence))
            if len(kept) != len(sentences(text)):
                why = "the prompt already has " + ("these sentences" if not kept else "some of these sentences")
                text = " ".join(kept)
        piece = Piece(topic=topic, text=text, sep=sep, added=bool(text), why=why, found=found, asked=asked, params=params)
        self.pieces.append(piece)
        if piece.added:
            self.note(**params)
        return piece

    def replaced(self, topic: str, text: str, **params: Any) -> "Prompt":
        """The same prompt with the piece on `topic` taken out and `text` said last in its place (a correction
        of a sentence replaces it; said after it, the prompt would carry the sentence twice)."""
        out = Prompt(self.own, caller=self.caller, sep=self.sep)
        old = self.piece(topic)
        for piece in self.pieces:
            if piece.topic != topic:
                out.add(piece.topic, piece.asked, sep=piece.sep, **piece.params)
        out.add(topic, text, **{**(old.params if old else {}), **params})
        return out

    def note(self, *, facing: str | None = None, handed: list[Any] | None = None, **_: Any) -> None:
        """Note what the caller's text says against, or again after, a piece that turns the subject to `facing`
        (right, left, or `NO_TURN` for a front or back view) or puts the `handed` items on their sides. `add`
        calls it for the piece it attaches; a template that carries the turn in its own first text calls it
        itself."""
        if facing is not None:
            for words, side in turns_said(self.caller):
                if facing == NO_TURN:
                    self._say("conflict", "facing", f'the text says "{words}" and the engine\'s sentence is a view '
                              "turned to neither side")
                elif side == facing:
                    self._say("repeat", "facing", f'the text says "{words}" and so does the engine\'s sentence; it is said twice')
                else:
                    self._say("conflict", "facing", f'the text says "{words}" and the engine\'s sentence says {facing}')
        for item in handed or []:
            name = _ARTICLE.sub("", item.item).lower()
            for clause in _CLAUSE_END.split(self.caller):
                if name not in clause.lower():
                    continue
                sides = {side.lower() for side in _SIDE.findall(_TURN.sub(" ", clause))}
                if item.side in sides:
                    self._say("repeat", "handed", f"the text already says which side {item.item} is on; it is said twice")
                elif item.other in sides:
                    self._say("conflict", "handed", f'the text has {item.item} with "{item.other}" ("{clause.strip()}") '
                              f"and the engine's sentence puts it on the {item.where()}")

    def _say(self, kind: str, about: str, text: str) -> None:
        note = {"kind": kind, "about": about, "text": text}
        if note not in self.notes:
            self.notes.append(note)


def note_lines(prompt: Prompt) -> list[str]:
    """`prompt.notes` as lines for stderr: a conflict is a warning, a repeat a note."""
    return [("warning: " if note["kind"] == "conflict" else "note: ") + note["text"] for note in prompt.notes]
