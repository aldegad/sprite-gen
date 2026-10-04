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
        # a far arm swings out in front of the body, and what it wears shows then
        assert "the left wrist with the black smartwatch is on the far arm." in text
        assert "Wherever the far arm reaches out in front of the body, clear of its outline, the black smartwatch shows whole" in text
        assert "hidden or at most a sliver" not in text
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
        assert clip == "The wrist nearer the viewer stays bare, exactly as in the image, for the whole clip."
        assert "watch" not in clip
    else:
        assert clip.startswith("The black smartwatch stays on the wrist ")
        assert clip.count("smartwatch") == 1 and "no band" not in clip and "strap" not in clip
        if picture:
            assert f"at the {picture} of the picture" in clip
        if view == "side":
            assert "the wrist nearer the viewer" in clip and "in front of the body" not in clip


def test_a_far_part_that_does_not_swing_stays_hidden() -> None:
    """A pin on the far side of the head has nothing to bring it into view: drawn hidden, never named in a clip."""
    pin = [h.parse("the star pin=left side of the head")]
    assert h.limb("wrist") == "arm" and h.limb("upper arm") == "arm" and h.limb("ankle") == "leg"
    assert h.limb("side of the head") is None and h.limb("shoulder") is None and h.limb("") is None
    assert "on the far side, behind the body, so the star pin is hidden or at most a sliver of it shows" in h.text(pin, "side", "right")
    for gait in (False, True):
        clip = h.text(pin, "side", "right", clip=True, gait=gait)
        assert clip == "The side of the head nearer the viewer stays bare, exactly as in the image, for the whole clip."


def test_a_walk_shows_the_far_arms_item_when_that_arm_swings_forward() -> None:
    """2.22.0 never named a hidden item, and the far wrist's watch was in no frame of a side walk. A walk's far
    arm comes forward with every step: the sentence says the item shows then, and that the near arm is bare."""
    watch = [h.parse(WATCH)]
    far = h.text(watch, "side", "right", clip=True, gait=True)
    assert far == (h.ARMS_SWING_TEXT + " "
                   "The black smartwatch stays on the wrist that wears it in the image, on the arm on the far side of the "
                   "body, and shows each time that arm swings forward; the arm nearer the viewer stays bare, exactly as in "
                   "the image.")
    near = h.text(watch, "side", "left", clip=True, gait=True)
    assert near == (h.ARMS_SWING_TEXT + " "
                    "The black smartwatch stays on the wrist that wears it in the image, on the arm nearer the viewer; the "
                    "other arm, on the far side of the body, stays bare both where it shows behind the body and where it "
                    "swings out in front of it.")
    # no sentence holds an arm still, and a state that does not step keeps the 2.22.0 sentence
    assert "still" not in far + near and "for the whole clip" not in far + near
    assert h.text(watch, "side", "right", clip=True) == "The wrist nearer the viewer stays bare, exactly as in the image, for the whole clip."
    assert "shows each time that arm swings forward" in batch_mod.build_prompt("side", "walk", None, handed=watch)
    assert "shows each time that arm swings forward" in batch_mod.build_prompt("side", "run", None, handed=watch)
    assert "swing" not in batch_mod.build_prompt("side", "idle", None, handed=watch)
    assert h.ARMS_SWING_TEXT == "Both arms swing back and forth with each step, opposite to the legs."
    assert h.ARMS_SWING_TEXT not in h.text([h.parse("the red anklet=left ankle")], "side", "right", clip=True, gait=True)
    # the other views have a picture side to name and are unchanged by the state
    for view, facing in (("front", None), ("front_diagonal", "left"), ("back_diagonal", "right")):
        assert h.text(watch, view, facing, clip=True, gait=True) == h.text(watch, view, facing, clip=True)


def test_a_side_walk_says_what_its_first_frame_must_show() -> None:
    """A clip keeps what its first frame shows: the far arm's item is in the walk only if the still shows it."""
    watch = [h.parse(WATCH)]
    needs = h.first_frame_needs(watch, "side", "right", gait=True)
    assert len(needs) == 1 and "mid-stride, the far arm out in front of the body with the black smartwatch in view" in needs[0]
    assert h.first_frame_needs(watch, "side", "left", gait=True) == []          # the near arm is always in view
    assert h.first_frame_needs(watch, "side", "right") == []                    # an idle does not swing it
    assert h.first_frame_needs(watch, "front_diagonal", "right", gait=True) == []
    assert h.first_frame_needs([h.parse("the star pin=left side of the head")], "side", "right", gait=True) == []
    record = clip_prompt.plan_prompt(direction="side", state="walk", facing="right", last_frame=False, handed=watch)
    assert record["still_needs"] == needs
    assert "still_needs" not in clip_prompt.plan_prompt(direction="side", state="walk", facing="left", last_frame=False, handed=watch)


def test_two_items_do_not_call_a_wrist_bare_that_the_other_is_on() -> None:
    """One sentence per wrist. With an item on each wrist, a side clip said "the wrist in front of the body stays
    bare" and "the red ribbon stays on the wrist in front of the body" together; with two on one wrist it said the
    same sentence twice."""
    watch, ribbon, bracelet = h.parse(WATCH), h.parse("the red ribbon=right wrist"), h.parse("the gold bracelet=left wrist")
    for gait in (False, True):
        each = h.text([watch, ribbon], "side", "right", clip=True, gait=gait)
        assert "bare" not in each and each.count("The red ribbon") == 1
        both = h.text([watch, bracelet], "side", "right", clip=True, gait=gait)
        assert both.count("bare") == 1 and both.count(h.ARMS_SWING_TEXT) == (1 if gait else 0)
    assert h.text([watch, bracelet], "side", "left", clip=True).startswith("The black smartwatch and the gold bracelet stay on ")
    still = h.text([watch, ribbon], "side", "right")
    assert "the right wrist has none of it" in still and "with none of it" in still and "with none;" not in still
    assert "has none," in h.text([watch], "side", "right") and "none of it" not in h.text([watch, bracelet], "side", "right")


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
