# SPDX-License-Identifier: Apache-2.0
"""`--camera-elevation DEG`: a camera that looks down, as an opt-in piece of every prompt a still or a clip is made from.

Without it every prompt is the one before it, byte for byte (`test_prompt_freeze.py` holds them at 2.22.0; a few
entry points are checked here too). With it the prompt is that same prompt and the camera sentence, at its place and
nothing else: a still says it after the view sentence (before the handed piece), or after the caller's text without
`--direction`; a clip says it after the view and the other rules, before the handed piece. The one exception is a front
or back walk's mid-step redraw, whose "seen at eye level" a camera from above contradicts: there those words are said
as the crashbang KUMA pack said them, and no sentence is added.

The expected sentences are written out here, apart from the engine's constants, so a change to the engine's words fails
here until it is made on purpose.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen import gen
from sprite_gen.gen import chroma, handedness as h, prompt_parts
from sprite_gen.video import batch, body_plan as body_mod, clip_prompt

DEG = 35
STILL_CAMERA = (
    "The camera is high above: a fixed orthographic top-down game camera looking down at the character from about 35 "
    "degrees above the horizon, not an eye-level side-scroller view. Because we look down from above, the top of the "
    "head and the tops of the shoulders are visible, the face, where it shows, is seen slightly from above, the legs "
    "look slightly shorter from foreshortening, and the tops of the feet are seen from above with the soles hidden. "
    "Parallel orthographic projection, no fisheye, no perspective convergence."
)
STILL_CAMERA_ANY_BODY = (
    "The camera is high above: a fixed orthographic top-down game camera looking down at the character from about 35 "
    "degrees above the horizon, not an eye-level side-scroller view. Because we look down from above, the top of the "
    "head and the upper side of the body are visible, the face, where it shows, is seen slightly from above, any legs "
    "look slightly shorter from foreshortening, and nothing is seen from below. Parallel orthographic projection, no "
    "fisheye, no perspective convergence."
)
STILL_CAMERA_SCENE = (
    "The camera is high above: a fixed orthographic top-down game camera looking down at every figure from about 35 "
    "degrees above the horizon, not an eye-level side-scroller view. Because we look down from above, the top of every "
    "head and the upper side of every body are visible, each face, where it shows, is seen slightly from above, any "
    "legs look slightly shorter from foreshortening, and nothing is seen from below. Parallel orthographic projection, "
    "no fisheye, no perspective convergence."
)
CLIP_CAMERA = "The high top-down camera angle (looking down about 35 degrees) stays exactly as in the image."
EYE_LEVEL = "seen at eye level"
REDRAW_CAMERA = "seen from the same high top-down camera angle as the image, looking down about 35 degrees"

TEXT = "A small fox adventurer, 2D game sprite, full body."
CHARACTER = "A small fox adventurer in a green cloak"
HANDED = {"none": None, "watch": ["the black smartwatch=left wrist"]}
BODIES = {"none": None, "biped": ["biped"], "quadruped": ["quadruped"], "scene": ["the man=biped", "the horse=quadruped"]}
STATES = ("idle", "walk", "run", "jump", "attack", "cheer")
MODELS = {"pro": "grok-imagine-video-1.5", "lite": "grok-imagine-video-1.5-lite"}
MOTIONS = {"built-in": None, "own": "The fox walks with a cheerful bounce, swinging both arms."}


def _items(key: str) -> list[h.Handed] | None:
    return h.parse_all(HANDED[key]) or None


def _body(key: str):
    return body_mod.parse_all(BODIES[key]) if BODIES[key] else None


def _views():
    return [(view, facing) for view in h.VIEWS for facing in (("right", "left") if view in h.LATERAL_VIEWS else (None,))]


def _without(parts: prompt_parts.Prompt, topic: str) -> str:
    return parts.own + "".join(piece.sep + piece.text for piece in parts.pieces if piece.added and piece.topic != topic)


def _topics(parts: prompt_parts.Prompt) -> list[str]:
    return [piece.topic for piece in parts.pieces if piece.added]


# -- without the flag, nothing changes ----------------------------------------------------------------

def test_without_the_flag_every_entry_point_is_the_prompt_it_was() -> None:
    for view, facing in _views():
        assert gen.still_prompt(TEXT, view=view, facing=facing, camera_elevation=None).text == \
            gen.still_prompt(TEXT, view=view, facing=facing).text
        for state in STATES:
            parts = batch.clip_prompt_parts(view, state, CHARACTER, facing=facing or "right", camera_elevation=None)
            assert parts.text == batch.build_prompt(view, state, CHARACTER, facing=facing or "right")
            assert parts.piece("camera") is None and "top-down" not in parts.text
    assert gen.still_prompt(TEXT).piece("camera") is None
    for view, key in itertools.product(batch.WALK_START_TEXT, (None, "green")):
        text = batch.walk_start_prompt(view, key, camera_elevation=None)
        assert text == batch.walk_start_prompt(view, key) and EYE_LEVEL in text and "top-down" not in text
    record = clip_prompt.plan_prompt(direction="back", state="walk")
    assert "camera_elevation" not in record and "top-down" not in record["prompt"]
    assert EYE_LEVEL in record["start_still"]["prompt"]


# -- a still --------------------------------------------------------------------------------------

def test_a_still_says_the_camera_after_its_view_or_after_the_text() -> None:
    plain = gen.still_prompt(TEXT, view="front").text
    assert gen.still_prompt(TEXT, view="front", camera_elevation=DEG).text == f"{plain} {STILL_CAMERA}"
    assert gen.still_prompt(TEXT, camera_elevation=DEG).text == f"{TEXT}\n\n{STILL_CAMERA}"
    # with a reference turned by --facing alone, the camera is where the view would be: before the turn
    turned = gen.still_prompt(TEXT, facing="left", refs=True, key="magenta", camera_elevation=DEG)
    assert _topics(turned) == ["camera", "facing", "key-background"]
    assert turned.text.startswith(f"{TEXT}\n\n{STILL_CAMERA}\n\n")
    # with a handed item: view, camera, then the handed sentences
    watch = _items("watch")
    handed = gen.still_prompt(TEXT, view="side", facing="right", handed=watch, camera_elevation=DEG)
    view = batch.still_view_text("side", "right")
    assert handed.text == f"{TEXT}\n\n{view[0].upper()}{view[1:]}. {STILL_CAMERA} {h.text(watch, 'side', 'right')}"


def test_a_still_for_a_body_that_is_not_a_person_says_no_shoulders_or_feet() -> None:
    horse = gen.still_prompt(TEXT, view="back", body_plan=_body("quadruped"), camera_elevation=DEG)
    assert horse.piece("camera").text == STILL_CAMERA_ANY_BODY
    assert "shoulders" not in STILL_CAMERA_ANY_BODY and "feet" not in STILL_CAMERA_ANY_BODY
    scene = gen.still_prompt(TEXT, view="front_diagonal", facing="right", body_plan=_body("scene"), camera_elevation=DEG)
    assert scene.piece("camera").text == STILL_CAMERA_SCENE
    biped = gen.still_prompt(TEXT, view="front", body_plan=_body("biped"), camera_elevation=DEG)
    assert biped.piece("camera").text == STILL_CAMERA


def _still_rows():
    for (view, facing), handed, body, refs in itertools.product([(None, None), *_views()], HANDED, BODIES, (False, True)):
        if view is None and (handed != "none" or body != "none"):
            continue  # gen refuses --handed and --body-plan without --direction
        make = lambda deg, v=view, f=facing, hd=handed, b=body, r=refs: gen.still_prompt(
            TEXT, view=v, facing=f, handed=_items(hd), body_plan=_body(b), refs=r, key="magenta" if r else None,
            camera_elevation=deg)
        yield f"still/{view}@{facing}/{handed}/{body}/{'refs' if refs else 'text'}", make(DEG), make(None), view, body


@pytest.mark.parametrize("name,parts,plain,view,body", list(_still_rows()), ids=[r[0] for r in _still_rows()])
def test_every_still_is_the_one_without_and_the_camera_at_its_place(name, parts, plain, view, body) -> None:
    assert _without(parts, "camera") == plain.text, name
    topics = _topics(parts)
    assert topics.count("camera") == 1
    camera = parts.piece("camera")
    if view is None:
        assert topics[0] == "camera" and camera.sep == "\n\n"
    else:
        assert topics[topics.index("view") + 1] == "camera" and camera.sep == " "
    expected = STILL_CAMERA if body in ("none", "biped") else STILL_CAMERA_SCENE if body == "scene" else STILL_CAMERA_ANY_BODY
    assert camera.text == expected
    assert parts.text.count(expected) == 1


# -- a clip ---------------------------------------------------------------------------------------

def test_a_clip_ends_its_rules_with_the_camera_line_before_the_handed_piece() -> None:
    plain = batch.build_prompt("back_diagonal", "walk", None, facing="left")
    assert batch.build_prompt("back_diagonal", "walk", None, facing="left", camera_elevation=DEG) == f"{plain} {CLIP_CAMERA}"
    # the diagonal's measured heading sentence stays as it was, isometric words and all
    assert "like a character walking up and to the left in an isometric game" in plain
    watch = _items("watch")
    handed = batch.clip_prompt_parts("side", "walk", CHARACTER, facing="right", handed=watch)
    with_camera = batch.clip_prompt_parts("side", "walk", CHARACTER, facing="right", handed=watch, camera_elevation=DEG)
    assert with_camera.text == (f"{batch.build_prompt('side', 'walk', CHARACTER, facing='right')} {CLIP_CAMERA} "
                                f"{handed.piece('handed').text}")


def _clip_rows():
    for (view, facing), state, model, motion, handed, pinned in itertools.product(
            _views(), STATES, MODELS, MOTIONS, HANDED, (None, True)):
        if MOTIONS[motion] and state != "walk":
            continue
        make = lambda deg, v=view, s=state, f=facing or "right", mo=MODELS[model], m=MOTIONS[motion], hd=handed, p=pinned: (
            batch.clip_prompt_parts(v, s, CHARACTER, facing=f, motion=m, model=mo, handed=_items(hd), pinned=p,
                                    camera_elevation=deg))
        yield f"clip/{view}@{facing}/{state}/{model}/{motion}/{handed}/pinned={pinned}", make(DEG), make(None)


@pytest.mark.parametrize("name,parts,plain", list(_clip_rows()), ids=[r[0] for r in _clip_rows()])
def test_every_clip_is_the_one_without_and_the_camera_line_before_the_handed_piece(name, parts, plain) -> None:
    assert _without(parts, "camera") == plain.text, name
    assert _topics(parts) == ["camera", *_topics(plain)]
    assert parts.piece("camera").text == CLIP_CAMERA and parts.piece("camera").sep == " "
    assert parts.text.count(CLIP_CAMERA) == 1


def test_a_callers_motion_that_already_says_the_camera_line_gets_it_once() -> None:
    motion = f"The fox walks with a cheerful bounce. {CLIP_CAMERA}"
    parts = batch.clip_prompt_parts("front", "walk", None, motion=motion, camera_elevation=DEG)
    assert parts.piece("camera").added is False and parts.text.count(CLIP_CAMERA) == 1
    assert parts.text == batch.build_prompt("front", "walk", None, motion=motion)


# -- the mid-step redraw ----------------------------------------------------------------------------

@pytest.mark.parametrize("view,key,handed,body", list(itertools.product(
    batch.WALK_START_TEXT, (None, *chroma.KEY_BACKGROUND_TEXT), HANDED, ("none", "biped", "quadruped", "scene"))))
def test_the_mid_step_redraw_keeps_the_camera_instead_of_eye_level(view, key, handed, body) -> None:
    plain = batch.walk_start_prompt(view, key, _items(handed), _body(body))
    with_camera = batch.walk_start_prompt(view, key, _items(handed), _body(body), camera_elevation=DEG)
    assert plain.count(EYE_LEVEL) == 1
    assert with_camera == plain.replace(EYE_LEVEL, REDRAW_CAMERA)
    assert EYE_LEVEL not in with_camera


# -- video-prompt ---------------------------------------------------------------------------------

@pytest.mark.parametrize("direction,state,model", [("front_diagonal", "walk", "pro"), ("back_diagonal", "run", "lite"),
                                                   ("side", "attack", "pro"), ("back", "walk", "lite")])
def test_video_prompt_says_the_camera_as_video_set_does(direction, state, model) -> None:
    record = clip_prompt.plan_prompt(direction=direction, state=state, facing="left", character=CHARACTER,
                                     model=MODELS[model], camera_elevation=DEG)
    assert record["camera_elevation"] == DEG
    assert record["prompt"] == batch.build_prompt(direction, state, CHARACTER, facing="left", model=MODELS[model],
                                                  camera_elevation=DEG)
    assert record["prompt"].endswith(CLIP_CAMERA)
    if "start_still" in record:
        assert REDRAW_CAMERA in record["start_still"]["prompt"] and EYE_LEVEL not in record["start_still"]["prompt"]


def test_video_prompt_cli_threads_the_camera(capsys) -> None:
    assert clip_prompt.main(["--direction", "front", "--state", "walk", "--camera-elevation", "35", "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["camera_elevation"] == 35 and record["prompt"].endswith(CLIP_CAMERA)


# -- the range ------------------------------------------------------------------------------------

BAD = (9, 81, 0, -35, 35.5, "35", True)


@pytest.mark.parametrize("deg", BAD)
def test_an_angle_out_of_range_or_not_whole_degrees_is_refused_everywhere(deg) -> None:
    calls = [
        lambda: gen.still_prompt(TEXT, view="front", camera_elevation=deg),
        lambda: gen.still_prompt(TEXT, camera_elevation=deg),
        lambda: batch.build_prompt("side", "walk", None, camera_elevation=deg),
        lambda: batch.walk_start_prompt("front", "green", camera_elevation=deg),
        lambda: clip_prompt.plan_prompt(direction="side", state="walk", camera_elevation=deg),
    ]
    for call in calls:
        with pytest.raises(SystemExit, match=r"--camera-elevation is whole degrees above the horizon, 10\.\.80"):
            call()


@pytest.mark.parametrize("deg", (10, 80))
def test_the_ends_of_the_range_are_accepted(deg) -> None:
    assert f"about {deg} degrees" in batch.build_prompt("side", "walk", None, camera_elevation=deg)
    assert f"about {deg} degrees above the horizon" in gen.still_prompt(TEXT, camera_elevation=deg).text


def test_gen_refuses_the_angle_before_a_provider_is_reached(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gen, "_make_provider", lambda *a, **kw: pytest.fail("a provider was reached"))
    with pytest.raises(SystemExit, match=r"gen: --camera-elevation"):
        gen.generate_image("fake", TEXT, tmp_path / "out.png", camera_elevation=90)


def test_video_set_refuses_the_angle_before_any_item(tmp_path) -> None:
    still = tmp_path / "side.png"
    Image.new("RGB", (32, 32), (0, 255, 0)).save(still)
    with pytest.raises(SystemExit, match=r"video-set: --camera-elevation"):
        batch.run_set(bases={"side": still}, states=["walk"], root=tmp_path / "set", character=None, duration=3,
                      resolution="720p", key="green", concurrency=1, force=False, gap=0,
                      video_runner=lambda *a, **kw: pytest.fail("a clip was asked for"), camera_elevation=5)
    assert not (tmp_path / "set" / "side-walk").exists()


# -- gen records it ---------------------------------------------------------------------------------

def test_gen_sends_the_camera_and_records_it(tmp_path, monkeypatch) -> None:
    from sprite_gen.gen.base import ProviderRun

    sent: list[str] = []

    class Backend:
        name = "fake"
        transparency = "native"

        def generate(self, request, workdir):
            sent.append(request.prompt)
            Image.new("RGB", (8, 8), (255, 0, 255)).save(request.raw)
            return ProviderRun(self.name, 1, model="test-image", extra={})

    monkeypatch.setattr(gen, "_make_provider", lambda *a, **kw: Backend())
    result = gen.generate_image("fake", TEXT, tmp_path / "out.png", view="front_diagonal", facing="right",
                                camera_elevation=DEG)
    assert sent == [gen.still_prompt(TEXT, view="front_diagonal", facing="right", camera_elevation=DEG).text]
    assert result.extra["camera_elevation"] == DEG
    plain = gen.generate_image("fake", TEXT, tmp_path / "plain.png", view="front")
    assert "camera_elevation" not in plain.extra and STILL_CAMERA not in sent[-1]


def test_gen_cli_threads_the_camera(tmp_path, monkeypatch) -> None:
    seen = []

    def stop(*a, **kw):
        seen.append(kw)
        raise SystemExit("stopped before generating")

    monkeypatch.setattr(gen, "generate_image", stop)
    for argv, deg in ((["--camera-elevation", "35"], 35), ([], None)):
        with pytest.raises(SystemExit, match="stopped before generating"):
            gen.main(["--prompt", TEXT, "--out", str(tmp_path / "o.png"), "--provider", "grok", *argv])
        assert seen[-1]["camera_elevation"] == deg


# -- video-set ------------------------------------------------------------------------------------

@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(batch.frames_mod, "run_frames", lambda clip, out, **kw: {
        "fps": 24, "frames": 10, "alpha_zero_pct_min": 50, "alpha_zero_pct_max": 60, "keyed_dir": str(out)})
    monkeypatch.setattr(batch.loop_mod, "run_loop", lambda *a, **kw: {
        "cycle": {"length": 10, "period_global": 10, "ratio": 0.2}, "resampled_seam_ratio": 0.2, "n_out": 10,
        "gif": {"file": "loop.gif"}, "webp": {"file": "loop.webp"}, "strip": {"path": "strip.png"}})
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)


def _video(prompts: dict[str, str]):
    def video(image, prompt, out, report, **kw):
        prompts[out.parent.name] = prompt
        out.write_bytes(b"test-video")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0
    return video


def _green_still(path: Path) -> Path:
    still = Image.new("RGB", (96, 128), (0, 255, 0))
    ImageDraw.Draw(still).rectangle((36, 20, 59, 109), fill=(120, 60, 40))
    still.save(path)
    return path


def _set(tmp_path: Path, prompts: dict[str, str], deg, **kw):
    return batch.run_set(bases={"front_diagonal": _green_still(tmp_path / "fd.png")}, states=["walk", "idle"],
                         root=tmp_path / "set", character=None, duration=3, resolution="720p", key="green",
                         concurrency=1, force=False, gap=0, video_runner=_video(prompts), align_cycles="off",
                         camera_elevation=deg, **kw)


def test_video_set_says_the_camera_in_every_clip_and_records_it(tmp_path, offline) -> None:
    prompts: dict[str, str] = {}
    result = _set(tmp_path, prompts, DEG)
    assert result["ok"] == 2 and result["camera_elevation"] == DEG
    for item in result["items"]:
        assert item["camera_elevation"] == DEG
        assert prompts[item["item"]] == batch.build_prompt(item["direction"], item["state"], None, facing="right",
                                                           camera_elevation=DEG)
    report = json.loads((tmp_path / "set" / "set.report.json").read_text(encoding="utf-8"))
    assert report["camera_elevation"] == DEG


def test_video_set_without_the_camera_records_nothing(tmp_path, offline) -> None:
    prompts: dict[str, str] = {}
    result = _set(tmp_path, prompts, None)
    assert "camera_elevation" not in result and all("camera_elevation" not in i for i in result["items"])
    assert all(CLIP_CAMERA not in p and "top-down" not in p for p in prompts.values())


def test_a_cached_clip_is_reused_only_for_the_same_camera(tmp_path, offline) -> None:
    prompts: dict[str, str] = {}
    assert _set(tmp_path, prompts, DEG)["ok"] == 2
    again: dict[str, str] = {}
    same = _set(tmp_path, again, DEG)
    assert same["ok"] == 2 and again == {} and all(i["clip"] == {"reused": True} for i in same["items"])
    other = _set(tmp_path, again, 50)
    assert other["ok"] == 0 and again == {}
    assert all("cached clip prompt differs" in i["error"] for i in other["items"])
    plain = _set(tmp_path, again, None)
    assert plain["ok"] == 0 and all("cached clip prompt differs" in i["error"] for i in plain["items"])


def test_a_front_walk_is_redrawn_mid_step_from_above_and_redrawn_again_for_another_angle(tmp_path, offline) -> None:
    redraws: list[str] = []

    def paint(base, prompt, out, report, *, log, provider=None):
        redraws.append(prompt)
        out.write_bytes(base.read_bytes())
        report.write_text(json.dumps({"prompt": prompt, "refs": [str(base)]}))
        return 0

    base = _green_still(tmp_path / "front.png")

    def run(deg):
        (tmp_path / "set" / "front-walk" / "clip.mp4").unlink(missing_ok=True)  # film again; the start still may be reused
        return batch.run_set(bases={"front": base}, states=["walk"], root=tmp_path / "set", character=None, duration=3,
                             resolution="720p", key="green", concurrency=1, force=False, gap=0, video_runner=_video({}),
                             redraw_runner=paint, align_cycles="off", camera_elevation=deg)

    first = run(DEG)
    assert first["ok"] == 1 and first["items"][0]["walk_start"]["reused"] is False
    assert redraws == [batch.walk_start_prompt("front", "green", camera_elevation=DEG)]
    assert REDRAW_CAMERA in redraws[0] and EYE_LEVEL not in redraws[0]
    same = run(DEG)
    assert same["ok"] == 1 and same["items"][0]["walk_start"]["reused"] is True and len(redraws) == 1
    other = run(50)
    assert other["ok"] == 1 and other["items"][0]["walk_start"]["reused"] is False
    assert len(redraws) == 2 and "looking down about 50 degrees" in redraws[1]
    eye_level = run(None)
    assert eye_level["ok"] == 1 and len(redraws) == 3 and redraws[2] == batch.walk_start_prompt("front", "green")


def test_video_set_cli_threads_the_camera(monkeypatch, tmp_path) -> None:
    seen = []
    monkeypatch.setattr(batch, "run_set", lambda **kw: seen.append(kw) or {"failed": []})
    assert batch.main(["--base", f"front={tmp_path / 'f.png'}", "--states", "walk", "--out-dir", str(tmp_path),
                       "--camera-elevation", "35"]) == 0
    assert seen[0]["camera_elevation"] == 35
    assert batch.main(["--base", f"front={tmp_path / 'f.png'}", "--states", "walk", "--out-dir", str(tmp_path)]) == 0
    assert seen[1]["camera_elevation"] is None
