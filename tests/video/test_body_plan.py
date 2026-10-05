# SPDX-License-Identifier: Apache-2.0
"""A body that is not a person is not told a person's walk (`--body-plan`, `sprite_gen.video.body_plan`).

The measured Lite walk calm swung the arms, the Lite back-diagonal head hold moved the arms, the idle
planted both feet and settled the chest, shoulders and arms, and the front or back walk's mid-step redraw
put one foot under each hip with the arms swinging: said of a horse, it walks on a person's legs or stands
up on two. A quadruped, a body without legs, and a scene of a person leading a horse each get prompts
with no part they lack; no body, or one biped, keeps every prompt byte for byte. Subjects are synthetic.
"""

from __future__ import annotations

import itertools
import json
import re

import pytest
from PIL import Image, ImageDraw

from sprite_gen import gen
from sprite_gen.gen.base import ProviderRun
from sprite_gen.video import batch, body_plan as bp, clip_prompt

LITE, PRO = "grok-imagine-video-1.5-lite", "grok-imagine-video-1.5"
STATES = ("walk", "run", "idle", "jump")
QUADRUPED = bp.parse_all(["quadruped"])
LEGLESS = bp.parse_all(["legless"])
SCENE = bp.parse_all(["the man=biped", "the horse=quadruped"])
# Words that name a part only a person has, or count a person's feet.
PERSON = re.compile(r"\b(arms?|hands?|wrists?|elbows?|shoulders?|chest|hips?|hip-width|knees?|both feet|one foot|the other foot|"
                    r"shoes?|heels?|toes?)\b", re.IGNORECASE)
# A body without legs has no feet at all, counted or not.
FEET = re.compile(r"\b(feet|foot)\b", re.IGNORECASE)

SNAPSHOTS = {
    'horse/side/walk/lite': (
        '2D game sprite animation. A brown horse walks naturally in place, as if on a treadmill, without '
        'moving across the screen. It stays on all four legs, as in the image, and never rises onto its hind '
        'legs. The character is seen from the exact side, facing right. Stays centered in the frame and does '
        'not move across the screen; the body and hair always stay fully inside the frame with margin. Camera'
        ' completely locked, no zoom, no pan, no reframing. The background stays a perfectly flat, pure '
        'chroma-key fill for the whole clip — no shadows, no ground line, no particles, no lighting changes, '
        'no effects. Keep the design, colors and proportions exactly as in the image. Consistent, evenly '
        'paced motion so the animation loops. A slow, relaxed, unhurried walk: small, low steps with the feet'
        ' barely leaving the ground and no bounce — never trotting, galloping, running, jogging, skipping or '
        'hopping.'
    ),
    'scene/side/walk/lite': (
        '2D game sprite animation. A man leading a brown horse by its reins walks naturally in place, as if '
        'on a treadmill, without moving across the screen. Each figure keeps the body it has in the image: '
        'the man on two legs; the horse on all four legs, never rising onto its hind legs. The character is '
        'seen from the exact side, facing right. Stays centered in the frame and does not move across the '
        'screen; the body and hair always stay fully inside the frame with margin. Camera completely locked, '
        'no zoom, no pan, no reframing. The background stays a perfectly flat, pure chroma-key fill for the '
        'whole clip — no shadows, no ground line, no particles, no lighting changes, no effects. Keep the '
        'design, colors and proportions exactly as in the image. Consistent, evenly paced motion so the '
        'animation loops. A slow, relaxed, unhurried walk: small, low steps with the feet barely leaving the '
        'ground and no bounce — never trotting, galloping, running, jogging, skipping or hopping.'
    ),
    'horse/side/idle': (
        '2D game sprite animation. A brown horse stays still in a relaxed idle pose, standing or resting on '
        'the ground exactly as in the image for the whole clip: slow, gentle breathing that softly rises and '
        'falls in the body, a slight settle of loose parts such as hair, a mane, a tail or cloth, and one '
        'natural blink if a face has eyes. Whatever it stands or rests on never lifts, steps, shuffles or '
        'slides — no walking, no marching in place, no turning. It stays on all four legs, as in the image, '
        'and never rises onto its hind legs. The character is seen from the exact side, facing right. Stays '
        'centered in the frame and does not move across the screen; the body and hair always stay fully '
        'inside the frame with margin. Camera completely locked, no zoom, no pan, no reframing. The '
        'background stays a perfectly flat, pure chroma-key fill for the whole clip — no shadows, no ground '
        'line, no particles, no lighting changes, no effects. Keep the design, colors and proportions exactly'
        ' as in the image. The last frame returns to the exact pose of the first frame, so the animation '
        'loops seamlessly.'
    ),
    'horse/front/walk-start/green': (
        'Redraw the character in the attached image as the same 2D game sprite, keeping the design, outfit, '
        'colors, body proportions and art style exactly as they are. Only the pose changes: the character is '
        'caught mid-step while walking straight toward the viewer, facing the viewer directly with the body '
        'square. One leg is lifted a little while the others stay planted, each under the body where it is in'
        ' the image, with clear gaps between the legs. It stays on all four legs, as in the image, and never '
        'rises onto its hind legs. Full body, centered, seen at eye level, with generous empty margin all '
        'around. The entire background is one perfectly flat, uniform pure green chroma-key fill (#00FF00) '
        'with no gradient, no texture, no shadow and no ground line.'
    ),
    'scene/back/walk-start/green': (
        'Redraw the figures in the attached image as the same 2D game sprite, keeping the design, outfit, '
        'colors, hair, body proportions and art style exactly as they are, and keeping the same view: seen '
        'directly from behind, the back of every figure toward the viewer. Only the pose changes: every '
        'figure is caught mid-step while walking straight away from the viewer, with the body square to the '
        'viewer and not turned to either side. Each figure that walks on legs has one leg lifted a little and'
        ' the others planted, with clear gaps between the legs. Each figure keeps the body it has in the '
        'image: the man on two legs; the horse on all four legs, never rising onto its hind legs. Full body, '
        'centered, seen at eye level, with generous empty margin all around; all of the hair, mane and tail '
        'stay well inside the image. The entire background is one perfectly flat, uniform pure green chroma-'
        'key fill (#00FF00) with no gradient, no texture, no shadow and no ground line.'
    ),
    'horse/front/still': (
        'seen from the front: the whole body and head turned to face the viewer squarely, the face centred and '
        'looking straight out of the image, not turned toward either side even when a reference picture shows it '
        'from another angle, standing on all four legs, not rearing onto its hind legs'
    ),
    'scene/back_diagonal/still': (
        'seen from a three-quarter back angle: every figure\'s whole body and head turned about 45 degrees away from '
        'the viewer toward the upper right, halfway between facing away and facing right, the face hidden and not '
        'looking back, and the body pointing diagonally up and to the right so its back faces the viewer at an angle,'
        ' each figure with the body it has: the man on two legs; the horse on all four legs, never rising onto its '
        'hind legs'
    ),
}


def _every_prompt(body_plan, character):
    """Each clip prompt (built-in and a caller's paragraph, every model) and video-prompt record for the
    walk, run, idle and jump in every view, and each mid-step redraw."""
    for view, state, model in itertools.product(batch.VIEW_TEXT, STATES, (None, PRO, LITE)):
        yield f"{view}/{state}/{model}", batch.build_prompt(view, state, character, model=model, body_plan=body_plan)
        yield (f"{view}/{state}/{model}/motion",
               batch.build_prompt(view, state, character, motion="It ambles along, one cycle about 1 s.", model=model,
                                  body_plan=body_plan))
    for view, state in itertools.product(batch.VIEW_TEXT, STATES):
        record = clip_prompt.plan_prompt(direction=view, state=state, character=character, model=LITE, body_plan=body_plan)
        yield f"video-prompt/{view}/{state}", record["prompt"]
        if "start_still" in record:
            yield f"video-prompt/{view}/{state}/start", record["start_still"]["prompt"]


@pytest.mark.parametrize("body_plan, character", [(QUADRUPED, "A brown horse"), (LEGLESS, "A green slime"),
                                                  (SCENE, "A man leading a brown horse by its reins")])
def test_no_walk_run_idle_or_jump_prompt_names_a_part_the_body_lacks(body_plan, character) -> None:
    named = {name: sorted({m.group(0).lower() for m in PERSON.finditer(text)})
             for name, text in _every_prompt(body_plan, character)}
    assert len(named) > 130
    assert not {name: words for name, words in named.items() if words}


def _every_still(body_plan, character):
    """The view sentence a still is drawn with, at every view and facing: as the app takes it
    (`still_view_text`) and as `gen --direction` adds it to the caller's prompt (`gen.still_prompt`)."""
    for view in batch.VIEW_TEXT:
        for facing in ("right", "left") if view in gen.handed_mod.LATERAL_VIEWS else (None,):
            yield f"still_view_text/{view}/{facing}", batch.still_view_text(view, facing or "right", body_plan=body_plan)
            yield (f"still_prompt/{view}/{facing}",
                   gen.still_prompt(f"{character}, pixel art.", view=view, facing=facing, body_plan=body_plan).text)


@pytest.mark.parametrize("body_plan, character", [(QUADRUPED, "A brown horse"), (LEGLESS, "A green slime"),
                                                  (SCENE, "A man leading a brown horse by its reins")])
def test_no_still_view_sentence_names_a_part_the_body_lacks(body_plan, character) -> None:
    """The still a clip starts from is drawn with the view sentence; a front still of a horse "with the toes of
    both feet pointing at the viewer" stands it up on two before the clip begins."""
    no = FEET if bp.legless(body_plan) else None
    named = {name: sorted({m.group(0).lower() for m in PERSON.finditer(text)}
                          | ({m.group(0).lower() for m in no.finditer(text)} if no else set()))
             for name, text in _every_still(body_plan, character)}
    assert len(named) == 2 * 8
    assert not {name: words for name, words in named.items() if words}
    for name, text in _every_still(body_plan, character):
        assert bp.still_text(body_plan) in text, name


def test_without_a_person_the_v2_31_prompts_did_name_a_persons_parts() -> None:
    """What the report was about: a Lite walk, an idle and the front or back redraw of a horse, as 2.31.0 said it,
    and the front, back and back-diagonal stills a walk starts from."""
    assert "arm swing" in batch.build_prompt("side", "walk", "A brown horse", model=LITE)
    assert "both feet planted flat" in batch.build_prompt("side", "idle", "A brown horse")
    assert "directly under its own hip" in batch.walk_start_prompt("front", "green")
    assert "the toes of both feet" in batch.still_view_text("front")
    assert "the heels of both feet" in batch.still_view_text("back")
    assert "the backs of the shoes" in batch.still_view_text("back_diagonal")


@pytest.mark.parametrize("name", list(SNAPSHOTS))
def test_the_prompts_for_a_horse_and_a_man_leading_one(name) -> None:
    who, view, state, *rest = name.split("/")
    body_plan, character = {"horse": (QUADRUPED, "A brown horse"),
                            "scene": (SCENE, "A man leading a brown horse by its reins")}[who]
    if state == "walk-start":
        drawn = batch.walk_start_prompt(view, rest[0], body_plan=body_plan)
    elif state == "still":
        drawn = batch.still_view_text(view, body_plan=body_plan)
    else:
        drawn = batch.build_prompt(view, state, character, model=LITE if rest == ["lite"] else None, body_plan=body_plan)
    assert drawn == SNAPSHOTS[name]


def test_one_biped_or_none_keeps_every_prompt_byte_for_byte() -> None:
    biped = bp.parse_all(["biped"])
    for view, state, model in itertools.product(batch.VIEW_TEXT, (*STATES, "attack", "cheer"), (None, PRO, LITE)):
        plain = batch.build_prompt(view, state, "The knight", model=model)
        assert batch.build_prompt(view, state, "The knight", model=model, body_plan=biped) == plain
        assert batch.build_prompt(view, state, "The knight", model=model, body_plan=[]) == plain
        motion = "It marches with the knees high, one cycle about 1 s."
        assert (batch.build_prompt(view, state, "The knight", motion=motion, model=model, body_plan=biped)
                == batch.build_prompt(view, state, "The knight", motion=motion, model=model))
    for view in batch.WALK_START_TEXT:
        assert batch.walk_start_prompt(view, "green", body_plan=biped) == batch.walk_start_prompt(view, "green")
    for view, facing in itertools.product(batch.VIEW_TEXT, ("right", "left")):
        plain = batch.still_view_text(view, facing)
        assert batch.still_view_text(view, facing, body_plan=biped) == plain
        assert batch.still_view_text(view, facing, body_plan=[]) == plain
        turned = facing if view in gen.handed_mod.LATERAL_VIEWS else None
        assert (gen.still_prompt("The knight.", view=view, facing=turned, refs=True, body_plan=biped).text
                == gen.still_prompt("The knight.", view=view, facing=turned, refs=True).text)


def test_the_body_plan_says_what_it_stands_on_after_the_motion_and_before_the_view() -> None:
    horse = batch.build_prompt("front", "run", "A brown horse", body_plan=QUADRUPED)
    assert horse.index("runs naturally") < horse.index(bp.ONE_TEXT["quadruped"]) < horse.index("is seen from the front")
    told = batch.build_prompt("side", "walk", "A brown horse", motion="It trots lightly.", body_plan=QUADRUPED)
    assert told.startswith(f"2D game sprite animation. It trots lightly. {bp.ONE_TEXT['quadruped']} It stays in place")


def test_a_body_without_legs_has_no_mid_step_redraw() -> None:
    assert batch.starts_mid_step("walk", "front", QUADRUPED) and batch.starts_mid_step("walk", "back", SCENE)
    assert not batch.starts_mid_step("walk", "front", LEGLESS)
    assert "start_still" not in clip_prompt.plan_prompt(direction="front", state="walk", body_plan=LEGLESS)
    with pytest.raises(SystemExit, match="no mid-step start still"):
        batch.walk_start_prompt("front", "green", body_plan=LEGLESS)


@pytest.mark.parametrize("specs, match", [
    (["horse"], "expected"),
    (["=quadruped"], "expected"),
    (["quadruped", "the man=biped"], "names each figure"),
    (["the horse=quadruped", "The Horse=biped"], "named once"),
])
def test_a_body_plan_is_one_plan_or_one_named_plan_per_figure(specs, match) -> None:
    with pytest.raises(SystemExit, match=match):
        bp.parse_all(specs)
    assert bp.parse_all(["Quadruped"]) == [bp.Body("quadruped")]
    assert bp.parse_all(["the  horse = quadruped"]) == [bp.Body("quadruped", "the horse")]


def test_video_set_says_the_body_plan_in_the_redraw_and_the_clip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(batch.frames_mod, "run_frames", lambda clip, out, **kw: {
        "fps": 24, "frames": 10, "alpha_zero_pct_min": 50, "alpha_zero_pct_max": 60, "keyed_dir": str(out)})
    monkeypatch.setattr(batch.loop_mod, "run_loop", lambda *a, **kw: {
        "cycle": {"length": 10, "period_global": 10, "ratio": 0.2}, "resampled_seam_ratio": 0.2, "n_out": 10,
        "gif": {"file": "loop.gif"}, "webp": {"file": "loop.webp"}, "strip": {"path": "strip.png"}})
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)
    redraws: list[str] = []
    clips: dict[str, str] = {}

    def paint(base, prompt, out, report, *, log, provider=None):
        redraws.append(prompt)
        out.write_bytes(base.read_bytes())
        report.write_text(json.dumps({"prompt": prompt, "refs": [str(base)]}))
        return 0

    def video(image, prompt, out, report, **kw):
        clips[out.parent.name] = prompt
        out.write_bytes(b"test-video")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0

    base = Image.new("RGB", (128, 96), (0, 255, 0))
    ImageDraw.Draw(base).rectangle((20, 30, 107, 79), fill=(120, 60, 40))
    base.save(tmp_path / "front.png")
    result = batch.run_set(bases={"front": tmp_path / "front.png"}, states=["walk"], root=tmp_path / "set",
                           character="A brown horse", duration=3, resolution="720p", key="green", concurrency=1,
                           force=False, gap=0, video_runner=video, redraw_runner=paint, align_cycles="off",
                           body_plan=QUADRUPED)
    assert result["ok"] == 1 and result["body_plan"] == [{"plan": "quadruped", "figure": ""}]
    assert redraws == [batch.walk_start_prompt("front", "green", body_plan=QUADRUPED)]
    assert clips["front-walk"] == batch.build_prompt("front", "walk", "A brown horse", body_plan=QUADRUPED)
    seen = []
    monkeypatch.setattr(batch, "run_set", lambda **kw: seen.append(kw) or {"failed": []})
    assert batch.main(["--base", f"front={tmp_path / 'front.png'}", "--states", "walk", "--out-dir", str(tmp_path / "x"),
                       "--body-plan", "the man=biped", "--body-plan", "the horse=quadruped"]) == 0
    assert seen[0]["body_plan"] == SCENE


def test_video_prompt_takes_the_body_plan() -> None:
    record = clip_prompt.plan_prompt(direction="front", state="walk", character="A brown horse", body_plan=QUADRUPED)
    assert record["prompt"] == batch.build_prompt("front", "walk", "A brown horse", model=clip_prompt.DEFAULT_MODEL,
                                                  body_plan=QUADRUPED)
    assert record["start_still"]["prompt"] == batch.walk_start_prompt("front", "green", body_plan=QUADRUPED)
    assert record["body_plan"] == [{"plan": "quadruped", "figure": ""}]


class _Backend:
    name = "openai"
    transparency = "native"

    def __init__(self):
        self.prompts: list[str] = []

    def generate(self, request, workdir):
        self.prompts.append(request.prompt)
        Image.new("RGBA", (32, 32), (120, 60, 40, 255)).save(request.raw)
        return ProviderRun(self.name, 1, model="test-image", extra={})


def test_gen_direction_takes_the_body_plan(tmp_path, monkeypatch) -> None:
    backend = _Backend()
    monkeypatch.setattr(gen, "_make_provider", lambda *a, **kw: backend)
    result = gen.generate_image("openai", "A brown horse, pixel art.", tmp_path / "out.png", view="back",
                                body_plan=QUADRUPED)
    assert backend.prompts == [gen.still_prompt("A brown horse, pixel art.", view="back", body_plan=QUADRUPED).text]
    assert "both feet" not in backend.prompts[0] and bp.still_text(QUADRUPED) in backend.prompts[0]
    assert result.extra["view"]["body_plan"] == [{"plan": "quadruped", "figure": ""}]
    with pytest.raises(SystemExit, match="--body-plan needs --direction"):
        gen.generate_image("openai", "A brown horse.", tmp_path / "x.png", body_plan=QUADRUPED)
    assert len(backend.prompts) == 1
    seen = []
    monkeypatch.setattr(gen, "generate_image", lambda *a, **kw: seen.append(kw) or result)
    assert gen.main(["--provider", "openai", "--prompt", "A man leading a horse.", "--out", str(tmp_path / "s.png"),
                     "--direction", "front", "--body-plan", "the man=biped", "--body-plan", "the horse=quadruped"]) == 0
    assert seen[0]["body_plan"] == SCENE
