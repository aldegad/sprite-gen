# SPDX-License-Identifier: Apache-2.0
"""Handedness: an asymmetric item's side, worked out per view and said in the still and clip prompts.
The table is written out here on its own, not derived from the module, so a wrong placement fails."""
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen import gen
from sprite_gen.gen import handedness as h
from sprite_gen.gen.base import ProviderRun
from sprite_gen.video import batch as batch_mod
from sprite_gen.video import clip_prompt

FIXTURES = Path(__file__).parents[1] / "fixtures" / "facing"
WATCH = "the black smartwatch=left wrist"

# Where the character's own LEFT side is: (view, facing) -> (picture side, depth).
OWN_LEFT = {
    ("front", None): ("right", None),
    ("back", None): ("left", None),
    ("side", "right"): (None, "far"),
    ("side", "left"): (None, "near"),
    ("front_diagonal", "right"): ("right", "far"),
    ("front_diagonal", "left"): ("right", "near"),
    ("back_diagonal", "right"): ("left", "far"),
    ("back_diagonal", "left"): ("left", "near"),
}


def test_parse_reads_item_side_and_part() -> None:
    assert h.parse(WATCH) == h.Handed("the black smartwatch", "left", "wrist")
    assert h.parse("  the star  hairpin = Right ") == h.Handed("the star hairpin", "right", "")
    assert h.parse("a bag = right shoulder strap").part == "shoulder strap"
    for bad in ("the watch", "the watch=", "=left wrist", "the watch=upper wrist"):
        with pytest.raises(SystemExit, match="handedness: expected"):
            h.parse(bad)


@pytest.mark.parametrize("view,facing", list(OWN_LEFT))
def test_placement_matches_the_table_and_the_right_side_is_its_mirror(view, facing) -> None:
    picture, depth = OWN_LEFT[(view, facing)]
    assert h.placement("left", view, facing) == {"picture": picture, "depth": depth}
    flip = {"left": "right", "right": "left", "near": "far", "far": "near", None: None}
    assert h.placement("right", view, facing) == {"picture": flip[picture], "depth": flip[depth]}


def test_a_turned_view_needs_its_facing() -> None:
    for view in ("side", "front_diagonal", "back_diagonal"):
        with pytest.raises(SystemExit, match="turned right or left"):
            h.placement("left", view)
    with pytest.raises(SystemExit, match="view must be one of"):
        h.placement("left", "top")
    assert set(h.VIEWS) == set(batch_mod.VIEW_TEXT)


@pytest.mark.parametrize("view,facing", list(OWN_LEFT))
def test_the_sentence_says_the_side_and_where_it_is_in_the_view(view, facing) -> None:
    text = h.text([h.parse(WATCH)], view, facing)
    assert text.startswith("The black smartwatch is on the character's own left wrist only;")
    assert "the right wrist has none" in text
    picture, depth = OWN_LEFT[(view, facing)]
    if picture:
        assert f"own left wrist is the one at the {picture} of the picture" in text
    if depth == "far" and view == "side":
        assert "on the far side, behind the body, so the black smartwatch is hidden" in text
    if depth == "near" and view == "side":
        assert "its left wrist is the near one" in text and "shows clearly" in text
    if depth == "near" and view != "side":
        assert "nearer the viewer and fully visible" in text
    assert text.endswith("whatever an attached picture shows.")



@pytest.mark.parametrize("view,facing", list(OWN_LEFT))
def test_the_clip_sentence_anchors_to_the_image_and_never_names_a_hidden_item(view, facing) -> None:
    """A clip starts from a still drawn with the item in place. Named where it is hidden, with what the near
    arm must not wear, a clip grew the item on the near arm; the far side of a side view names nothing."""
    clip = h.text([h.parse(WATCH)], view, facing, clip=True)
    assert "exactly as in the image" in clip and "for the whole clip" in clip and "attached picture" not in clip
    picture, depth = OWN_LEFT[(view, facing)]
    if view == "side" and depth == "far":
        assert clip == "The wrist in front of the body stays bare, exactly as in the image, for the whole clip."
        assert "watch" not in clip
    else:
        assert clip.startswith("The black smartwatch stays on the wrist ")
        assert clip.count("smartwatch") == 1 and "no band" not in clip and "strap" not in clip
        if picture:
            assert f"at the {picture} of the picture" in clip
        if view == "side":
            assert "the wrist in front of the body" in clip


def test_the_clip_prompt_ends_with_the_handedness_sentence() -> None:
    items = [h.parse(WATCH)]
    plain = batch_mod.build_prompt("front_diagonal", "walk", None, facing="left")
    with_item = batch_mod.build_prompt("front_diagonal", "walk", None, facing="left", handed=items)
    assert with_item == plain + " " + h.text(items, "front_diagonal", "left", clip=True)
    front = batch_mod.build_prompt("front", "walk", None, facing="right", handed=items, motion="It strolls.")
    assert front.endswith(h.text(items, "front", None, clip=True))
    assert batch_mod.build_prompt("side", "walk", None, handed=None) == batch_mod.build_prompt("side", "walk", None)


def test_video_prompt_carries_handedness_and_turns_a_left_diagonal_loop_left() -> None:
    record = clip_prompt.plan_prompt(direction="back_diagonal", state="walk", facing="left", last_frame=False,
                                     unpinned=True, handed=[h.parse(WATCH)])
    assert record["handed"] == [{"item": "the black smartwatch", "side": "left", "part": "wrist"}]
    assert "toward the upper left" in record["prompt"] and "upper right" not in record["prompt"]
    assert "stays on the wrist at the left of the picture, nearer the viewer" in record["prompt"]
    assert "--facing left" in record["commands"]["canvas"] and "--facing left" in record["commands"]["loop"]
    front = clip_prompt.plan_prompt(direction="front", state="walk", facing="left", last_frame=False)
    assert "--facing right" in front["commands"]["loop"] and "handed" not in front


class Backend:
    name = "openai"
    transparency = "native"

    def __init__(self, observed="right", regenerated="right"):
        self.observed, self.regenerated = observed, regenerated
        self.generations = []

    def generate(self, request, workdir):
        self.generations.append(request)
        source = self.regenerated if len(self.generations) > 1 else self.observed
        shutil.copyfile(FIXTURES / f"{source}.png", request.raw)
        return ProviderRun(self.name, 1, model="test-image", extra={})

    def inspect_facing(self, path, workdir):
        direction = self.regenerated if len(self.generations) > 1 else self.observed
        return json.dumps({"direction": direction, "confidence": 0.9}), {"model": "test-vision"}


def _gen(tmp_path, monkeypatch, backend, **kwargs):
    monkeypatch.setattr(gen, "_make_provider", lambda *a, **kw: backend)
    return gen.generate_image("openai", "a toy robot", tmp_path / "out.png", **kwargs)


def test_gen_direction_adds_the_view_and_handedness_sentences(tmp_path, monkeypatch) -> None:
    backend = Backend("left")
    items = [h.parse(WATCH)]
    result = _gen(tmp_path, monkeypatch, backend, refs=[FIXTURES / "front.png"], view="front_diagonal",
                  facing="left", handed=items)
    prompt = backend.generations[0].prompt
    assert batch_mod.still_view_text("front_diagonal", "left")[1:] in prompt
    assert h.text(items, "front_diagonal", "left") in prompt
    assert result.extra["view"] == {"direction": "front_diagonal", "facing": "left",
                                    "handed": [{"item": "the black smartwatch", "side": "left", "part": "wrist"}]}


@pytest.mark.parametrize("kwargs,error", [
    ({"handed": [h.parse(WATCH)]}, "--handed needs --direction"),
    ({"view": "side", "handed": [h.parse(WATCH)]}, "turned right or left"),
    ({"view": "front", "facing": "right"}, "drop --facing"),
    ({"view": "side", "facing": "left", "facing_fix": "mirror", "handed": [h.parse(WATCH)]}, "moves every --handed item"),
])
def test_gen_refuses_before_generating(tmp_path, monkeypatch, kwargs, error) -> None:
    backend = Backend()
    with pytest.raises(SystemExit, match=error):
        _gen(tmp_path, monkeypatch, backend, refs=[FIXTURES / "front.png"], **kwargs)
    assert backend.generations == []


def test_a_handed_regeneration_that_still_faces_the_other_way_is_not_mirrored(tmp_path, monkeypatch) -> None:
    backend = Backend(observed="left", regenerated="left")
    result = _gen(tmp_path, monkeypatch, backend, refs=[FIXTURES / "front.png"], view="side", facing="right",
                  facing_fix="regen", handed=[h.parse(WATCH)])
    assert len(backend.generations) == 2
    assert Image.open(result.out).tobytes() == Image.open(FIXTURES / "left.png").tobytes()
    report = result.extra["facing"]
    assert "fallback" not in report and "not mirrored" in report["reason"]
    # without an item the same regeneration is mirrored, as before
    plain = _gen(tmp_path, monkeypatch, Backend(observed="left", regenerated="left"), refs=[FIXTURES / "front.png"],
                 facing="right", facing_fix="regen")
    assert plain.extra["facing"]["fallback"] == "mirror"
