# SPDX-License-Identifier: Apache-2.0
"""`handed-check` on drawn figures: a body with two arms and a blue watch screen on its own left wrist.
Each view is drawn as it should look (the table below, written out by hand), passes, and turned over
(as a set that mirrors its left-hand views would) fails — the check catches a mirror by itself."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageOps

from sprite_gen.gen import handedness as h
from sprite_gen.qa import handed

WATCH = h.parse("the black smartwatch=left wrist")
BLUE = (30, 90, 235)
W, H = 200, 320
# Where the watch is drawn for each (view, facing): on the arm at the picture's left / right, on the
# near arm (a side view, in front of the body) or nowhere (a side view, behind the body).
DRAWN = {
    ("front", None): "right", ("back", None): "left",
    ("front_diagonal", "right"): "right", ("front_diagonal", "left"): "right",
    ("back_diagonal", "right"): "left", ("back_diagonal", "left"): "left",
    ("side", "right"): "hidden", ("side", "left"): "near",
}
VIEWS = list(DRAWN)


def figure(where: str, *, swing: int = 0, screen: int = 12, outline_split: bool = False) -> Image.Image:
    """A keyed figure: head and body, two arms, and the watch screen drawn `where`."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((60, 20, 140, 100), fill=(240, 240, 240, 255), outline=(20, 20, 20, 255), width=3)
    d.rounded_rectangle((65, 95, 135, 250), 20, fill=(240, 240, 240, 255), outline=(20, 20, 20, 255), width=3)
    d.rectangle((70, 250, 92, 300), fill=(240, 240, 240, 255))
    d.rectangle((108, 250, 130, 300), fill=(240, 240, 240, 255))
    arms = {"left": (40 + swing, 110, 62 + swing, 210), "right": (138 - swing, 110, 160 - swing, 210)}
    if where == "near":
        arms = {"near": (92 + swing, 120, 112 + swing, 215)}
    for box in arms.values():
        d.rectangle(box, fill=(235, 235, 235, 255), outline=(20, 20, 20, 255), width=2)
    if where in arms:
        x0, _, x1, _ = arms[where]
        cx = (x0 + x1) // 2
        d.rectangle((cx - screen // 2, 185, cx + screen // 2, 185 + screen), fill=BLUE + (255,))
        if outline_split:
            d.line((cx, 185, cx, 185 + screen), fill=(20, 20, 20, 255), width=1)
    return im


def loop(where: str, n: int = 6) -> list[Image.Image]:
    return [figure(where, swing=s) for s in (0, 2, 4, 6, 4, 2)[:n]]


def check(frames, view, facing, **kw):
    return handed.check(frames, item=WATCH, view=view, facing=facing, marker=BLUE, **kw)


@pytest.mark.parametrize("view,facing", VIEWS)
def test_each_view_drawn_as_its_handedness_says_passes(view, facing) -> None:
    report = check(loop(DRAWN[(view, facing)]), view, facing)
    assert report["ok"], report["fails"]
    assert report["expect"] == h.placement("left", view, facing)
    assert check(loop(DRAWN[(view, facing)]), view, facing, references=[figure("right")])["ok"]


@pytest.mark.parametrize("view,facing,as_view,as_facing", [
    ("front", None, "front", None),
    ("back", None, "back", None),
    ("side", "right", "side", "left"),                      # E turned over for W: the watch stays hidden
    ("front_diagonal", "right", "front_diagonal", "left"),  # SE for SW: the watch jumps to the picture's left
    ("back_diagonal", "right", "back_diagonal", "left"),    # NE for NW: the watch jumps to the picture's right
    ("side", "left", "side", "right"),
    ("front_diagonal", "left", "front_diagonal", "right"),
    ("back_diagonal", "left", "back_diagonal", "right"),
])
def test_a_mirrored_view_fails(view, facing, as_view, as_facing) -> None:
    """The counterexample: the right-facing view turned over, checked as the left-facing one, fails."""
    mirrored = [ImageOps.mirror(f) for f in loop(DRAWN[(view, facing)])]
    reference = [figure("right")]  # the front view: the watch in full view
    report = check(mirrored, as_view, as_facing, references=reference)
    assert not report["ok"]


def test_the_wrong_wrist_in_a_side_view_needs_a_reference_to_be_caught() -> None:
    """Facing right, the left wrist is the far one: a full screen on the near arm is the wrong wrist.
    Only the item seen whole (a front reference) tells a sliver from a full screen."""
    frames = loop("near")
    unchecked = check(frames, "side", "right")
    assert unchecked["ok"] and unchecked["rules"]["far_hidden"]["checked"] is False
    caught = check(frames, "side", "right", references=[figure("right")])
    assert not caught["ok"] and "on the far side" in caught["fails"][0]["why"][0]
    sliver = check([figure("near", screen=4)], "side", "right", references=[figure("right")])
    assert sliver["ok"]


def test_a_far_sliver_turned_over_is_not_the_near_item_shown() -> None:
    """Facing right, the far wrist's watch may show as a sliver as the arm swings. Turned over to face left,
    that sliver is on the arm behind the body — the own right wrist, the wrong one — and the near wrist is
    bare. A side view has no picture side, so only size against a reference tells the two apart; without
    one the near side's size is reported unchecked, not passed as checked."""
    reference = [figure("right")]
    right = [figure("near", swing=s, screen=4) for s in (0, 2, 4, 6, 4, 2)]
    assert check(right, "side", "right", references=reference)["ok"]
    mirrored = [ImageOps.mirror(f) for f in right]
    caught = check(mirrored, "side", "left", references=reference)
    assert not caught["ok"] and caught["fails"] == []
    assert caught["rules"]["near_shown"] == {"min": 0.5, "shown": 0, "frames": 6, "ok": False}
    assert caught["rules"]["near_whole"]["checked"] is True and caught["frames_shown"] == 0
    unchecked = check(mirrored, "side", "left")
    assert unchecked["rules"]["near_whole"] == {"checked": False, "why": "no --reference: the item seen whole is unknown"}
    # the near watch drawn in full counts as shown against the same reference
    drawn = check(loop("near"), "side", "left", references=reference)
    assert drawn["ok"] and drawn["rules"]["near_shown"]["shown"] == 6


def test_the_item_in_two_places_fails_and_a_split_screen_is_one() -> None:
    both = figure("right")
    d = ImageDraw.Draw(both)
    d.rectangle((45, 185, 57, 197), fill=BLUE + (255,))
    report = check([both], "front", None)
    assert not report["ok"] and "2 places" in report["fails"][0]["why"][0]
    assert check([figure("right", outline_split=True)], "front", None)["ok"]
    # a few tinted pixels elsewhere (a clip's colour blocks on an outline) are a speck, not a second watch
    speck = figure("right")
    ImageDraw.Draw(speck).rectangle((45, 185, 49, 189), fill=BLUE + (255,))
    report = check([speck], "front", None)
    assert report["ok"] and len(report["per_frame"][0]["specks"]) == 1


def test_a_near_item_hidden_in_most_frames_fails() -> None:
    frames = [figure("near")] + [figure("hidden")] * 3
    report = check(frames, "side", "left")
    assert not report["ok"] and report["rules"]["near_shown"] == {"min": 0.5, "shown": 1, "frames": 4, "ok": False}


def test_unusable_inputs_are_refused() -> None:
    with pytest.raises(SystemExit, match="saturated colour"):
        handed.check([figure("right")], item=WATCH, view="front", facing=None, marker=(128, 128, 128))
    with pytest.raises(SystemExit, match="no transparent pixels"):
        check([Image.new("RGBA", (10, 10), (255, 255, 255, 255))], "front", None)
    with pytest.raises(SystemExit, match="#RRGGBB"):
        handed.parse_marker("blue")
    with pytest.raises(SystemExit, match="no --reference shows the marker"):
        check([figure("hidden")], "side", "right", references=[figure("hidden")])


def test_cli_reads_a_strip_writes_a_board_and_exits_on_a_mirror(tmp_path) -> None:
    def strip(frames, name):
        sheet = Image.new("RGBA", (W * len(frames), H), (0, 0, 0, 0))
        for i, f in enumerate(frames):
            sheet.paste(f, (i * W, 0))
        path = tmp_path / f"{name}.strip.png"
        sheet.save(path)
        (tmp_path / f"{name}.strip.json").write_text(json.dumps({"frames": len(frames), "w": W, "h": H}))
        return path

    good = strip(loop("right"), "sw")
    bad = strip([ImageOps.mirror(f) for f in loop("right")], "sw-mirrored")
    base = [sys.executable, "-m", "sprite_gen.cli", "handed-check", "--direction", "front_diagonal", "--facing", "left",
            "--handed", "the black smartwatch=left wrist", "--marker", "#1e5aeb"]
    ok = subprocess.run([*base, "--strip", str(good), "--board", str(tmp_path / "ok.png"),
                         "--report", str(tmp_path / "ok.json")], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["ok"] and (tmp_path / "ok.png").is_file()
    assert json.loads((tmp_path / "ok.json").read_text())["frames"] == 6
    no = subprocess.run([*base, "--strip", str(bad)], capture_output=True, text=True)
    assert no.returncode == 1 and "on the picture's left, expected its right" in no.stderr


def test_a_bare_strap_on_the_far_arm_is_caught_only_with_strap() -> None:
    """Facing left, the watch is on the near arm; its strap alone on the far arm (no screen, behind the body's
    edge) is the item in a second place. The screen colour cannot see it; `strap` can. Outlines stay out."""
    frame = figure("near")
    ImageDraw.Draw(frame).rectangle((128, 186, 142, 200), fill=(15, 15, 15, 255))
    assert check([frame], "side", "left")["ok"]
    caught = check([frame], "side", "left", strap=(20, 20, 20), zone=(0.5, 0.8))
    assert not caught["ok"] and "without its marker" in caught["fails"][0]["why"][0]
    assert check([figure("near")], "side", "left", strap=(20, 20, 20), zone=(0.5, 0.8))["ok"]
    outside = check([frame], "side", "left", strap=(20, 20, 20), zone=(0.0, 0.45))
    # outside the zone nothing is looked at: no strap, and the near screen counts as hidden
    assert outside["per_frame"][0]["straps"] == [] and outside["rules"]["near_shown"]["ok"] is False
    with pytest.raises(SystemExit, match="--zone"):
        check([frame], "side", "left", zone=(0.8, 0.5))
