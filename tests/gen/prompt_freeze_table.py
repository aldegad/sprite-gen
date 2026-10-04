# SPDX-License-Identifier: Apache-2.0
"""Every prompt the engine sends without `--handed`, drawn through the verbs' own entry points.

The prompts a character with no handed item is drawn and filmed from are frozen at 2.22.0 (tag
`013355b`): a change to them is made only after a before-and-after comparison on the app's default
clip model shows it is better, never as a side effect of the handed sentences. This table is that
freeze's evidence. It draws every prompt with entry points 2.22.0 already had — `batch.build_prompt`
(a clip, built-in or `--motion`, every model and pin), `clip_prompt.plan_prompt` (`video-prompt`),
`gen.generate_image` (a still: view, facing, reference, key line, layout guide, and the
`--facing-fix regen` regeneration), `video.generate_video` (`--direction side`) and
`batch.walk_start_prompt` (the mid-step redraw) — so the same code runs against the tag's source and
records what it sent.

Only the prompt strings are recorded: the notes and warnings a verb prints beside a prompt are not
part of the freeze.

Record (from the repo root, the tag's source unpacked somewhere):

    git archive 013355b | tar -x -C /tmp/sprite-gen-2.22.0
    python tests/gen/prompt_freeze_table.py /tmp/sprite-gen-2.22.0 tests/fixtures/prompts-v2.22.0.json.gz

`tests/gen/test_prompt_freeze.py` draws the table again against this tree and compares.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import itertools
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator

DIRECTIONS = ("side", "front", "back", "front_diagonal", "back_diagonal")
LATERAL = ("side", "front_diagonal", "back_diagonal")
FACINGS = ("right", "left")
# the built-in states, and one the engine has no sentence for
STATES = ("idle", "walk", "run", "jump", "attack", "cheer")
CHARACTER = "A small fox adventurer in a green cloak"
MOTIONS = {
    "built-in": None,
    "own": "The fox walks with a cheerful bounce, swinging both arms.",
    "quotes-engine": "The fox walks with a cheerful bounce. Camera completely locked, no zoom, no pan, no reframing.",
    "says-turn": "The fox walks to the right with a cheerful bounce.",
}
CLIP_MODELS = {"default": None, "pro": "grok-imagine-video-1.5", "lite": "grok-imagine-video-1.5-lite"}
SUBJECT = "A small fox adventurer, 2D game sprite, full body"
# the caller's still text: plain, and what it may already say about the key or the turn
STILL_TEXTS = {
    "plain": f"{SUBJECT}.",
    "names-key": f"{SUBJECT}, magenta chroma-key background.",
    "names-key-green": f"{SUBJECT} on a green screen.",
    "names-key-hex": f"{SUBJECT} on a flat #FF00FF background.",
    "names-key-of": f"{SUBJECT}, on a background of pure magenta.",
    "names-key-coloured": f"{SUBJECT}, magenta-colored background.",
    "rules-out-key": f"{SUBJECT}, no green screen.",
    "says-turn": f"{SUBJECT}, facing right.",
    # the engine's own view and reference sentences, already in the text (a prompt handed on): said again
    "quotes-engine": (f"{SUBJECT}. Seen from the exact side, facing right. The subject must be facing right (toward "
                      "the right edge of the image), regardless of the reference image's orientation. Preserve the "
                      "subject's design."),
}
FAKE_PNG_SIZE = (8, 8)


def _error(exc: BaseException) -> str:
    return f"!{type(exc).__name__}: {exc}"


def clip_cases() -> Iterator[tuple[str, Callable[[], str]]]:
    """`batch.build_prompt`: every view, facing, state, motion paragraph, clip model, pin and `--character`."""
    from sprite_gen.video import batch

    for direction, facing, state, motion, model, pinned, character in itertools.product(
            DIRECTIONS, FACINGS, STATES, MOTIONS, CLIP_MODELS, (None, True, False), (None, CHARACTER)):
        name = (f"clip/{direction}@{facing}/{state}/{motion}/{model}/pinned={pinned}/"
                f"{'character' if character else 'no-character'}")
        yield name, lambda d=direction, f=facing, s=state, m=MOTIONS[motion], mo=CLIP_MODELS[model], p=pinned, c=character: (
            batch.build_prompt(d, s, c, facing=f, motion=m, pinned=p, model=mo))


def video_prompt_cases() -> Iterator[tuple[str, Callable[[], str]]]:
    """`video-prompt` (`clip_prompt.plan_prompt`): its `prompt`, with and without an end-frame pin."""
    from sprite_gen.video import clip_prompt

    for direction, facing, state, motion, model, pin, character in itertools.product(
            DIRECTIONS, FACINGS, STATES, ("built-in", "own"), ("pro", "lite"), ("last-frame", "unpinned"),
            (None, CHARACTER)):
        name = f"video-prompt/{direction}@{facing}/{state}/{motion}/{model}/{pin}/{'character' if character else 'no-character'}"
        yield name, lambda d=direction, f=facing, s=state, m=MOTIONS[motion], mo=CLIP_MODELS[model], p=pin, c=character: (
            clip_prompt.plan_prompt(direction=d, state=s, facing=f, character=c, motion=m, model=mo,
                                    last_frame=p == "last-frame", unpinned=p == "unpinned")["prompt"])


def walk_start_cases() -> Iterator[tuple[str, Callable[[], str]]]:
    """The mid-step start still's redraw prompt, with each key line."""
    from sprite_gen.video import batch

    for direction, key in itertools.product(batch.WALK_START_TEXT, (None, "green", "magenta")):
        yield f"start-still/{direction}/{key}", lambda d=direction, k=key: batch.walk_start_prompt(d, k)


class _Sent(Exception):
    """Raised by the fake model once the prompt under test was sent: nothing after it is drawn."""


def _png(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", FAKE_PNG_SIZE, (255, 0, 255)).save(path)


@contextlib.contextmanager
def _patched(obj: Any, name: str, value: Any) -> Iterator[None]:
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _still(text: str, *, view: str | None, facing: str | None, refs: bool, transparent: bool, key: str,
           layout: bool, regen: bool) -> str:
    """The prompt(s) `gen` sends for one still: the first, and with `regen` the facing regeneration's after it."""
    from sprite_gen import gen
    from sprite_gen.gen import facing as facing_mod

    sent: list[str] = []

    class Backend:
        name = "fake"
        transparency = "native"

        def generate(self, request, workdir):
            sent.append(request.prompt)
            if not regen or len(sent) > 1:
                raise _Sent
            _png(request.raw)
            from sprite_gen.gen.base import ProviderRun

            return ProviderRun(self.name, 1, model="fake-image", extra={})

    opposite = "left" if facing == "right" else "right"
    with tempfile.TemporaryDirectory() as tmp, \
            _patched(gen, "_make_provider", lambda *a, **kw: Backend()), \
            _patched(facing_mod, "inspect", lambda *a, **kw: {"direction": opposite, "confidence": 1.0}), \
            contextlib.redirect_stderr(io.StringIO()):
        ref = Path(tmp) / "ref.png"
        _png(ref)
        try:
            gen.generate_image("fake", text, Path(tmp) / "out.png", refs=[ref] if refs else None, facing=facing,
                               facing_fix="regen" if regen else "none", view=view, transparent=transparent,
                               chroma_key=key, layout_guide=layout, aspect_ratio="3:4" if layout else None,
                               workdir=Path(tmp) / "work")
        except _Sent:
            pass
        except SystemExit as exc:
            return _error(exc)
    return "\n\n=== regeneration ===\n\n".join(sent)


def still_cases() -> Iterator[tuple[str, Callable[[], str]]]:
    """`gen`: the caller's text with every view, facing, reference, key line and layout guide, and the facing
    correction's regeneration."""
    views = (None, *DIRECTIONS)
    for user, view, facing, refs, transparent, layout in itertools.product(
            STILL_TEXTS, views, (None, *FACINGS), (False, True), (False, True), (False, True)):
        if facing is not None and view is not None and view not in LATERAL:
            continue  # refused for a front or back view: one row below says so
        # the key line goes on only for a reference run `auto` keys; the other key only matters there
        keys = ("magenta", "green") if refs and transparent else ("magenta",)
        for key in keys:
            for regen in ((False, True) if refs and facing else (False,)):
                name = (f"still/{user}/{view}@{facing}/{'ref' if refs else 'text'}/"
                        f"{'transparent-' + key if transparent else 'opaque'}/{'layout' if layout else 'no-layout'}"
                        f"{'/regen' if regen else ''}")
                yield name, lambda u=STILL_TEXTS[user], v=view, f=facing, r=refs, t=transparent, k=key, l=layout, g=regen: (
                    _still(u, view=v, facing=f, refs=r, transparent=t, key=k, layout=l, regen=g))
    yield "still/plain/front@right/refused", lambda: _still(STILL_TEXTS["plain"], view="front", facing="right", refs=False,
                                                            transparent=False, key="magenta", layout=False, regen=False)


def _side_video(prompt: str, facing: str) -> str:
    from sprite_gen.gen import video as video_mod

    sent: list[str] = []

    def body(request, *_a, **_kw):
        sent.append(request.prompt)
        raise _Sent

    with tempfile.TemporaryDirectory() as tmp, \
            _patched(video_mod, "_build_generation_body", body), \
            _patched(video_mod.facing_mod, "prepare_still", lambda image, corrected, **kw: _png(corrected) or {}), \
            contextlib.redirect_stderr(io.StringIO()):
        image = Path(tmp) / "still.png"
        _png(image)
        request = video_mod.VideoRequest(image=image, prompt=prompt, out=Path(tmp) / "clip.mp4", direction="side",
                                         facing=facing)
        try:
            video_mod.generate_video(request, credential=object(), call=lambda *a, **kw: None)
        except _Sent:
            pass
        except SystemExit as exc:
            return _error(exc)
    return sent[0]


def side_video_cases() -> Iterator[tuple[str, Callable[[], str]]]:
    """`video --direction side`: the caller's prompt, a `video-prompt` side walk handed on, and one turned the
    other way."""
    from sprite_gen.video import batch

    for facing in FACINGS:
        other = "left" if facing == "right" else "right"
        prompts = {
            "own": "The fox trots in place.",
            "own-with-hold": f"The fox trots in place. The subject stays in exact side view, facing {facing}. No turning around.",
            "video-prompt-walk": batch.build_prompt("side", "walk", CHARACTER, facing=facing),
            "video-prompt-walk-other-facing": batch.build_prompt("side", "walk", CHARACTER, facing=other),
        }
        for label, prompt in prompts.items():
            yield f"video-side/{facing}/{label}", lambda p=prompt, f=facing: _side_video(p, f)


def cases() -> Iterator[tuple[str, Callable[[], str]]]:
    for group in (clip_cases, video_prompt_cases, walk_start_cases, still_cases, side_video_cases):
        yield from group()


def draw() -> dict[str, str]:
    """Every case's prompt, or `!Error: ...` where the verb refuses it."""
    out = {}
    for name, make in cases():
        try:
            out[name] = make()
        except SystemExit as exc:
            out[name] = _error(exc)
    return out


def write(path: Path, table: dict[str, str]) -> None:
    data = json.dumps(table, ensure_ascii=False, indent=1, sort_keys=True).encode() + b"\n"
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as out:
        out.write(data)


def read(path: Path) -> dict[str, str]:
    with gzip.open(path, "rb") as file:
        return json.loads(file.read())


if __name__ == "__main__":
    source, target = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    sys.path.insert(0, str(source))
    import sprite_gen

    assert Path(sprite_gen.__file__).resolve().is_relative_to(source), sprite_gen.__file__
    write(target, draw())
    print(f"{target}: {len(read(target))} prompts from {source}")
