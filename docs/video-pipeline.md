# Video → sprite pipeline (engine SSoT)

> Owns: Pipeline B engine contract: state canvas, keyed frames, true-period and one-shot cycles, strip/GIF/WebP, the batch, a set's sheet · Index: [docs/README.md](README.md)

One still becomes a whole motion set: the still is padded into the canvas a state
needs, Grok Imagine animates it in place, the clip is keyed frame by frame, and one
seamless cycle is cut out as a strip, a transparent GIF and a WebP — every stage
measured and reported, nothing recovered silently. Everything here was first run by
hand on 2026-09-08 (15 loops: 3 directions × 5 states) and the rules below are the
ones that survived that day.

```
still ──video-canvas──▶ canvas.png ──video──▶ clip.mp4 ──video-frames──▶ keyed/*.png ──video-loop──▶ strip · gif · webp ──video-cycle-align──▶ one cycle length (a set)
                    │                     ▲                                                       └── video-set runs all four per (direction, state), then aligns each walk or run across its directions
                    └──video-prompt──▶ your agent's video MCP (ZCRE)
```

| Verb | Module | In → out |
|---|---|---|
| `sprite-gen video-canvas` | `sprite_gen/video/canvas.py` | still → padded still (state canvas) + report |
| `sprite-gen video` | `sprite_gen/gen/video.py` ([gen](video.md)) | still + prompt → mp4 + report |
| `sprite-gen video-prompt` | `sprite_gen/video/clip_prompt.py` | (direction, state) → the prompt `video-set` sends, for a clip made by a video MCP on your agent ([below](#a-clip-from-a-video-mcp-on-your-agent--zcre)) |
| `sprite-gen video-frames` | `sprite_gen/video/frames.py` | mp4 → `raw/`, `keyed/` RGBA frames + report |
| `sprite-gen video-loop` | `sprite_gen/video/loop.py` | keyed frames → `cycle/`, `<name>.strip.png` + `.strip.json`, `<name>.gif`, `<name>.webp` + report |
| `sprite-gen video-set` | `sprite_gen/video/batch.py` | bases × states → one folder per item, `set.report.json`, `table.md` |
| `sprite-gen video-cycle-align` | `sprite_gen/video/align.py` | the loop directories of one walk or run, one per direction → one cycle length, each loop turned to start on a foot strike, + report ([loop repair](loop-repair.md) section 4) |
| `sprite-gen video-follow` | `sprite_gen/video/follow.py` | a loop directory + an ellipse → the strip, GIF and WebP with that region following the body ([section 6](#6-follow-through--video-follow)) |
| `sprite-gen video-follow-inspect` | `sprite_gen/video/follow_inspect.py` | a loop directory + the ellipses `video-follow` takes → a record of where each is carried in every cell and what the move changes there, and two boards of every cell; the loop directory is only read ([section 6](#inspecting-a-follow-through--video-follow-inspect)) |
| `sprite-gen video-set-sheet` | `sprite_gen/video/set_sheet.py` | the loop directories of one set, each with its `--view` → one sheet: a row per direction in compass order, one cell size, one ground line, every row as cut, + a record and an optional centre picture; the loop directories are only read ([section 7](#7-one-sheet-for-a-set--video-set-sheet)) |

Wrappers: `scripts/video_canvas.py`, `scripts/video_frames.py`, `scripts/video_loop.py`,
`scripts/video_set.py`. Binaries: `ffmpeg`/`ffprobe` (frames), `img2webp` from libwebp
(WebP with exact alpha). Both are declared in `SKILL.md` `required_bins`.

## 1. Canvas — the input frame decides the output frame

Grok Imagine keeps the input image's framing and **ignores `aspect_ratio` on
image-to-video** (a `3:4` request still came back 960×960). A jump whose hair leaves
the frame cannot be fixed by prompt — it was fixed by padding the still. So the canvas
is a property of the motion state, owned by one table (`STATE_CANVAS`):

| State | Shape | Ratio | Room | Why |
|---|---|---|---|---|
| `jump` | tall | 3:4 | 34 % head-room above the still | airborne frames need height |
| `attack` | wide | 16:9 | 20 % above the still, at least 28 % in front (facing side), 20 % behind | a weapon raised overhead needs room above, a swing extends in front, and a long weapon drawn back reaches behind |
| `projectile` | wide | 16:9 | 34 % in front | the projectile travels away |
| everything else | square | 1:1 | — | in-place motion fits the still |

The attack's room above is 20 %, not more. On a square or upright still that puts at least
a quarter of the still's height above it, and with the still's own margin over the crown
(8 % of its height is enough) that holds a weapon raised about a third of the body above
the head. More room only made the body a smaller part of the clip, so `video-loop
--body-height` scaled it up to the delivery height; at 20 % it scales down. A still cropped
at the crown gets only the quarter — pass `--headroom 0.26` or more for an overhead swing.
At this headroom the 16:9 ratio, not `--lead`, sets the width of a square still's canvas,
so a smaller lead changes nothing.

`--shape tall|wide|square` overrides the row; `--headroom` / `--lead` / `--trail` tune the room
(`--trail` is the empty fraction of the width kept behind the subject, for a weapon drawn
back before the strike); `--facing left` mirrors the wide layout. A forced shape is that shape's
own row, not the state's: `--shape tall` is the jump row, and `--shape wide` is a forced-wide
row with 35 % above, 28 % in front and 20 % behind — not the attack row. It lands on states
whose own row is another shape, a jump among them, and 35 % on 16:9 keeps about the room a
jump's tall row leaves above a square or upright still. A still wider than it is tall keeps
less above it under forced wide than under the tall row (a 3:2 still about three fifths),
because there the wide canvas's height follows the still's width. A walk or run forced wide
has a row of its own (`STATE_SHAPE_CANVAS`): the same 28 % in front and 20 % behind, and nothing
above — its feet stay on the ground. Above a square still the jump's 35 % set the width as well
(1575 rows on 16:9 are 2800 columns, 1216 of them in front of the still), and a walker that small
in its frame is one a video model may reframe in its first frames (section 4, "A lead-in"). A
square still forced wide as a walk is 1969 × 1108. Headroom is a fraction of the full canvas
height; wide canvases grow both dimensions to preserve their ratio without shrinking
the still. A still whose corners are not one flat colour
is refused — a non-flat background cannot be extended without guessing.

**`--fit tight` adds no room.** It is the framing for a fixed body height at a low
clip resolution: the room above makes the subject a small part of the clip, and a
`--body-height` target then has to upscale it. Tight drops the empty rows above and
below the subject (keeping headroom of 4 % of the subject's height), keeps the still's full width,
and pads to the nearest framing the video model returns — 9:16, 1:1 or 16:9, with the
midpoints at 3:4 and 4:3 — by adding height above or width on both sides. The subject is never
scaled or cut, a long, low subject in a square still becomes a 16:9 frame it fills,
and an upright one keeps its square. A motion that leaves the frame is clipped: pair it
with `video-frames --allow-subject-edge-contact`. Tight picks its own shape, so it
refuses `--shape` / `--headroom` / `--lead` / `--trail`; the report says `fit` and
the `tight` rows it kept.

**The canvas owns key normalization.** Image models paint "`#00FF00`" a little
differently every run — (8, 166, 25) on 2026-09-11 — and the video model reproduces
the input colour almost exactly (a pure-key input came back as (16, 239, 11)). So when
the flat corners are a green/magenta key at *any* brightness (`--key auto`, the
default; `--key green|magenta` to insist, refused when the corners are not that
family), the still's background is repainted to the **exact declared key** and the
padding is that same pure key. The repaint mask is the `cutout` chroma matte's own
alpha-0 set — the pixels the canvas repaints are exactly the pixels `video-frames` will
erase, and the subject stays byte-identical. A corner that survives the matte fails loud
(the still is not on a key the engine can cut). `--key white` keeps the old behaviour: no
chroma key, the padding is the corner colour. The report records `key`, `key_painted`
(the colour the model actually used) and `normalized_px`.

## 2. Clip — `sprite-gen video`

Unchanged from [video.md](video.md): the user's own credential, fail-loud, `ftyp`-verified
mp4. For loops, prompt for **in-place, evenly paced, returns-to-start** motion on a flat
chroma fill ("walks in place on a treadmill", "hop … return to the exact starting
stance … same height every time"). `video-set` carries those templates
(`MOTION_TEXT` / `VIEW_TEXT`). They describe the gait "for this body type" and never
name limbs — the first drafts said "bipedal … knees … arms pumping", which prompted a
quadruped and a legless blob into a contradiction (2026-09-09).

A walk seen from the front or from behind, and a run seen from the front, take their own
sentence (`VIEW_MOTION_TEXT`) instead: the steps lift and land straight forward and back
under the body, toward the viewer or away from it "as if on a treadmill", and the body never
turns to the side, steps sideways or crosses its feet; a walk also asks for a calm, natural
cycle with small, even steps that never kick a leg out to the side. The body-neutral walk
sentence asks for "an even left-right or front-back rhythm", and a character facing the
viewer read that as stepping sideways: crossed feet, side kicks, a body turned to
three-quarters. Measured on Grok (subscription), 480p 3 s, nine humanoid characters twice
each, judged on frame sheets with the variant hidden: a front walk kept its facing and walked
in 7 of 18 clips with the old sentence and 16 of 18 with the new one. Without the calm-steps
clause it was 12 to 13 of 18, and five crossed their feet or kicked sideways. A back walk: 8
of 10 with the old sentence, 10 of 10 with the new one. A front run: 11 of 18 and 15 of 18
(a run keeps its stride and has no calm-steps clause). "on a treadmill" said as a place was
sometimes drawn under the feet; "as if on a treadmill" was not. A side view and a run seen
from behind keep the state's sentence (the second was not measured). A walk drawn at a
three-quarter angle kept that angle in about half the clips (6 of 10) with the old sentence
and in 5 of 10 with one that said to walk "toward the way its body faces in the image", and
otherwise mostly turned to a side view: no sentence tried held a three-quarter view.

Since 2.15.0 the walk says less. Spelling the steps out ("each one lifting and landing straight
forward and back under the body") made some takes march in place, knees lifted to the waist and
arms held stiff, so a front or back walk now says only that it walks naturally, which way it
faces and that it stays in place: "walks naturally in place, facing the viewer, as if on a
treadmill, without coming any closer" (and "facing away from the viewer … without moving any
farther away"). On one character, 480p 3 s on the API, two clips per sentence: the 2.13
sentence lifted the knees to the waist in one of two; "walks naturally" swung the arms, kept the
knees low and kept facing the viewer in both; a third sentence that asked for low heel-to-toe
steps and a loose arm swing kept the feet low but shuffled. Two clips each is a small sample:
the default is generic on purpose, because how a character walks (high steps, a stroll, a
march) is the caller's to say. The front run keeps its sentence.

Since 2.16.0 every walk and run takes that form, from every view: the side walk "walks
naturally in place, as if on a treadmill, without moving across the screen" (it said "moves in
place on a treadmill" as a place and asked for "an even left-right or front-back rhythm"), the
side run "runs naturally in place …", and the front and back runs "runs naturally in place,
facing (away from) the viewer, as if on a treadmill …" (the front run spelled its strides out
and said "toward the viewer" and "without coming any closer" in one clause). On one character,
480p 3 s on the API: the front run kept facing in 2 of 2, the side run ran in 2 of 2, the back
run kept facing away in the one clip whose frames passed; the side walk walked in 2 of 2.

Since 2.17.0 there are two three-quarter views, `front_diagonal` and `back_diagonal`: turned to the
right, the way an isometric game's character walks down and up to the right (turned over, they face
left; a character with an item on one side has them drawn facing left instead, see
[handedness](#handedness--an-item-on-one-side)). A walk or run in one says where it heads on the screen in those words: "walks naturally in
place, as if on a treadmill, heading diagonally away from the viewer toward the upper right, like a
character walking up and to the right in an isometric game, without moving across the screen. It
keeps the exact three-quarter back angle of the image the whole time: its back stays turned toward
the viewer at that angle and its face stays hidden. It never turns into a side view." (and "toward
the viewer and to the right … three-quarter front angle" for the front one). It is filmed pinned to
its first frame (`PINNED_GAIT_VIEWS`, `pins_last_frame`) with the return sentence, and cut as any
walk (`--anchor motion-auto`). The clip's view sentence points at the image ("seen from a
three-quarter back angle, turned exactly as in the image"); a still drawn at a diagonal from another
picture takes `STILL_VIEW_TEXT` instead (`still_view_text`), which says that the whole body turns
about 45 degrees, what that angle shows of the chest or back and of the legs (the next paragraph),
and which way the feet point — told less, a three-quarter back view came out as a
side view or looking back over the shoulder. Measured on the API, 480p 3 s, pinned, five humanoid
characters twice each, judged on frame sheets: the front and the back diagonal walk kept their angle
in 10 of 10 each; "toward the way its body faces in the image" kept the front one in 4 to 5 of 5 and,
on another character, the back one in 0 of 2. Idle and attack keep their angle in a diagonal (4 of
4). A diagonal run is weaker: 2 of 4 kept the angle, the others turned toward a side view.

**What a diagonal still shows of its turn.** Through 2.36.0 the diagonal still sentences said only
the turn, the head and the feet ("the whole body and head turned about 45 degrees … the feet
pointing toward the lower right"), and a still that turns only its head and shoes, the chest (back)
and the legs standing as from the front (straight behind), meets those words. The sentences now say
what the angle shows: not a front (back) view with only the head turned; the middle of the chest
(back) and of the waist about three quarters of the way across the body toward its far edge; the
shoulder and side nearer the viewer seen broad; the far shoulder and arm partly hidden behind the
body; the legs at the same angle, the far foot set a little higher and partly behind the near leg;
and not to follow a reference picture's angle. The far side of a front diagonal is the side it
faces, of a back diagonal the other one, so `STILL_VIEW_TEXT` takes `{other}` (the side opposite
`{facing}`) beside `{facing}`, and facing left is the same sentence with the two sides swapped. The
clip's view sentence is unchanged: a clip starts from the still. The body plan's sentences
(`STILL_VIEW_TEXT_ANY_BODY`) are unchanged too: they count no chest, shoulders or feet and take none
of these clauses. The words were chosen by a before-and-after comparison on the still model; its
measurements are kept in private evidence and none is recorded here.
`tests/gen/test_prompt_freeze.py` holds the old words and the new (`MEASURED`): a further rewording
fails there until it is compared again ([prompt-assembly](prompt-assembly.md)).

A front or back still redrawn from a picture seen from another side has its own still sentence too:
with the clip's one line ("seen from the front, facing the viewer directly"),
a front still redrawn from a side picture could keep part of the picture's turn, the feet frontal
and the head and chest turned a little toward the side the picture faces. `still_view_text("front")`
says the whole body and head face the viewer squarely, the chest, hips and toes point straight at
the viewer, the face is centred between both ears, and it is not turned toward either side even
when a reference picture shows the character from another angle; `still_view_text("back")` says the
same from behind, with the face hidden and not looking back over the shoulder. The sentence is used
whatever the reference's view: a caller does not always know it (an uploaded picture), and on a
front reference it changed nothing. The clip keeps its one line, since a clip starts from a still
already drawn at its view. Measured on codex `image_gen` (subscription), five humanoid characters,
three takes per arm, layout guide on, judged blind on full-resolution crops and with an eye-offset
metric: from a side reference the old line already drew a frontal face in 15 of 15 by eye (the turn
did not reproduce on this model), the new sentence in 15 of 15; from behind, both drew a straight
back in 15 of 15; from a front reference, the old line 14 of 15 and the new 15 of 15.

An attack is one timed strike, not a repeat. `MOTION_TEXT["attack"]` asks for one attack: a windup
(about 0.5 s), one strike in front (about 0.25 s), a held impact pose (about 0.3 s) and a recovery
to the exact starting stance (about 0.5 s), then `HOLD_TEXT["attack"]`:
every grip stays the one the image shows — one hand stays one hand, both hands stay both hands,
nothing let go or switched to the other hand — a hand the strike does not use stays where it is
drawn with whatever it holds (a shield, a lantern), and the body never turns. Naming one hand
for a weapon the still draws in both hands made the clip let go and grab again mid-attack.
A caller's own motion paragraph (`build_prompt(motion=...)`) gets the same `HOLD_TEXT` sentence
after it, once: a request interpreter writes its motion before the still
exists and cannot know where the other hand's item is drawn, so it writes the choreography and
the engine says what stays put. Its template
(`ACTION_COMMON_TEXT`) drops the "evenly paced" line, keeps what the subject holds inside the
frame, and asks for crisp frames without motion blur. `video-set` asks attack clips for 2 s
(`STATE_DURATION_SECONDS`; every other state keeps 3 s, and `--duration` overrides every state)
and pins the clip to end on the frame it starts from: `--last-frame` is the canvas itself
(first-last mode, see [video.md](video.md)), so the strike has to come back to the still. That
clip is stance -> strike -> stance, so it is cut whole like an idle (`--cycle pinned`,
`PINNED_LOOP_STATES`): the loop starts on the ready stance and never is a fragment of the strike
or a seam between two strikes. Asked for the same attack twice and cut by the one-shot or period
search, a clip gave loops that started mid-strike, held both strikes, or kept only the held pose;
asked for one attack in 3 or 4 s, it held the impact pose for about half the clip.

An idle holds its feet and is pinned too. `MOTION_TEXT["idle"]` keeps both feet planted flat for
the whole clip, limits the motion to breathing, a settle of the arms, hair and loose cloth and
one blink, and names walking and marching in place as what not to do — a side-view full body
asked for "a subtle weight sway" and an evenly paced loop tends to step in place. Its template
(`PINNED_LOOP_TEXT`) replaces the "evenly paced motion so the animation loops" line with the
return: the last frame comes back to the exact pose of the first. `video-set` pins idle clips
to the canvas (`PIN_LAST_FRAME_STATES`) and, because such a clip starts and ends on the same
frame, cuts it with `video-loop --cycle pinned` (`PINNED_LOOP_STATES`) instead of searching
it for a repeat.

`build_prompt(direction, state, character, facing, motion=...)` takes a caller's own motion
paragraph — whole sentences about the subject, such as a request interpreter writes per
request — in place of the built-in state sentence. The frame, camera, background and design
rules stay the engine's, and an attack keeps its `HOLD_TEXT` sentence after the paragraph. A
walk or run gets `GAIT_HOLD_TEXT` for its view after the paragraph instead: it stays in place as
if on a treadmill and keeps facing the viewer, away from the viewer or `{facing}` the whole time,
so a caller describes the gait (a sneak, a march, a lazy stroll) and the engine keeps it on the
spot. On one character, one clip each: a march lifted the knees high as asked and stayed in
place, a lazy stroll seen from behind kept facing away, and a tiptoe sneak kept facing the viewer
but read only as a slightly hunched walk.

`build_prompt(..., pinned=True)` says the clip is pinned to end on its first frame, so any state
gets `PINNED_LOOP_TEXT` (the return to the first pose) instead of the evenly paced repeat; left
out, only `PINNED_LOOP_STATES` do. A caller that retries a walk pinned after no cycle was found
says True; two such front-walk clips closed on their first frame (seam 0.12 and 0.24 of an
ordinary step).

`build_prompt(..., model=...)` names the clip model. **A Lite walk is calmed**: for
`grok-imagine-video-1.5-lite` (`LITE_VIDEO_MODELS`) the built-in walk sentence is followed by
`LITE_WALK_TEXT` — a slow, relaxed walk, small low steps, a gentle arm swing close to the body, no
bounce, never running. Lite read "walks naturally" bigger than Pro: long, bouncy steps and a wide
arm swing, a run more than a walk (2026-10-03, one SD character in five views, two takes each);
with the clause every view walked. A caller's own walk paragraph is left as written. **A Lite
back-diagonal walk also holds its head** (`LITE_HEAD_TEXT`, after the calm clause): its head swayed
2.7–3.8 % of the body height side to side against Pro's 0.88 %, and with the sentence four takes
swayed 1.7–3.1 % (best 1.84 %). It was measured in that view only, so it is said in that view only.
The measured sentence ended "only the legs, arms and the end of the ponytail move"; the engine's
says "the ends of the hair", since most characters have no ponytail. Pro prompts are unchanged.

**A front or back walk starts mid-step.** From a standing still the clip model makes the first
step itself and walks askew — the feet drawn to one line under the body, or the body turned
three-quarters: 1 of 14 front clips walked straight from a standing still, 14 of 16 from the same
characters redrawn mid-step (2026-10-02, four characters, blind judged; three rewordings of the walk
sentence kept 0 of 14 from a standing SD still). `WALK_START_TEXT[direction]` is the redraw sentence for the front and the
back — the same 2D sprite, same design, caught mid-step with one foot planted under its hip and
the other lifted a little under its own, hips and shoulders square, arms swinging gently — and
`walk_start_prompt(direction, key)` adds the background line for a green or magenta key
(`starts_mid_step(state, direction)` says which clips need it). The design is the reference
image's to keep: the sentence is drawn with the base still attached. `video-set` does this before
the canvas (`--walk-start redraw`, the default): one `sprite-gen gen --ref <base>` call per front or
back walk, on the base's own key, kept as `walk-start.png` with its report and reused while the
prompt and base are the same; a base on no chroma key is refused with `--walk-start as-given`, which
films from the base itself. Side and diagonal walks, and every other state, film from the base.

### A body that is not a person — `--body-plan`

The measured sentences above were measured on people, and five of them name what only a person has:
the Lite walk calm swings the arms (`LITE_WALK_TEXT`), the Lite back-diagonal head hold moves the
arms (`LITE_HEAD_TEXT`), the idle plants both feet and settles the chest, shoulders and arms
(`MOTION_TEXT["idle"]`), the front or back mid-step redraw puts one foot under each hip with the
arms swinging (`WALK_START_TEXT`), and the view sentence the still is drawn with points the chest,
hips and the toes or heels of both feet at the viewer, or the backs of the shoes (`STILL_VIEW_TEXT`). Said of a horse, a pig or a dog, they walk it on a person's legs or
stand it up on two. The walk and run sentences themselves name no limb (since 2.0.0's "`video-set`
motion templates no longer assume a biped"); these came later and were said to every body.

`video-set --body-plan`, `video-prompt --body-plan`, `gen --direction --body-plan`, `prepare
--body-plan` and
`build_prompt(body_plan=...)`, `walk_start_prompt(body_plan=...)`, `still_view_text(body_plan=...)`,
`gen.still_prompt(body_plan=...)` name what the subject stands on (`sprite_gen/video/body_plan.py`):
`biped` (the default), `quadruped` or `legless` for the character, or one `<figure>=<plan>` per figure
of a scene (`--body-plan "the man=biped" --body-plan "the horse=quadruped"`). No body plan, or one
biped, keeps every prompt byte for byte. Any other body gets:

| Sentence | for a body that is not one biped |
|---|---|
| after the motion sentence (or the caller's `--motion`) | what it stands on (`body_plan.text`): "It stays on all four legs, as in the image, and never rises onto its hind legs." / "It has no legs, as in the image, and never grows legs or feet." / "Each figure keeps the body it has in the image: the man on two legs; the horse on all four legs, never rising onto its hind legs." |
| a walk or run seen from behind | after what it stands on, `BACK_GAIT_TEXT_ANY_BODY`: "Its body stays level, square to the viewer and straight the whole time, never turning or angling to either side, so neither side of it comes into view, and it does not sway from side to side; any tail stays raised where it is in the image, only its tip swaying a little." (a scene: every figure). A four-legged back walk turned until a flank showed and swayed; with this and the still drawn square (below) it held square, and without the tail clause its tail swung on its own beat and the walk found no loop (2.38.0). A person's back walk is unchanged |
| idle | `IDLE_TEXT_ANY_BODY`: the same stillness, standing or resting as in the image, no feet counted, no chest, shoulders or arms |
| attack | `ATTACK_TEXT_ANY_BODY`: the same timed strike "with what it is already holding, or with its own body if it holds nothing", then `HOLD_TEXT_ANY_BODY["attack"]`: anything it holds stays held the same way, a part the strike does not use stays where it is drawn, no hand counted. Which part strikes (a bite, a head-butt, a forefoot) is not guessed: a caller who knows says it with `--motion`, which gets `HOLD_TEXT_ANY_BODY` after it |
| Lite walk calm | `LITE_WALK_TEXT_LEGGED` (no arm swing; never trotting or galloping) or, with a figure without legs, `LITE_WALK_TEXT_LEGLESS` (no steps or feet) |
| Lite back-diagonal head hold | `LITE_HEAD_TEXT_ANY_BODY`: what moves is what it moves on and its loose ends (a tail, a mane, hair) |
| the still's view sentence (`still_view_text`, `gen --direction`) | `STILL_VIEW_TEXT_ANY_BODY` for the front, back and diagonal views (where the body and head point; no feet, chest, hips, shoulders or shoes; the back also square and symmetric left to right, the head centred, neither side showing more, any tail from the middle, 2.38.0), the side view's `VIEW_TEXT` as it is, each ending in what it stands on (`body_plan.still_text`): ", standing on all four legs, not rearing onto its hind legs" / ", resting on its base without legs, never growing any" / ", each figure with the body it has: the man on two legs; the horse on all four legs, never rising onto its hind legs" |
| front or back mid-step redraw | `WALK_START_TEXT_ANY_BODY`: one leg lifted, the others planted, then what it stands on; a body without legs has no step to catch and films from its base (`starts_mid_step` is false, `video-prompt` gives no `start_still`) |

Only the biped sentences were measured on clips; the others say no part the body lacks and are not
yet measured. `--handed` keeps its own sentences (its arm sentences are for an item on an arm).
`video-set`, `video-prompt --json` and `gen`'s `extra.view` record the plan as `body_plan`. An app
that draws the still itself with `still_view_text` and films with `build_prompt` passes the same
`body_plan` to both: the clip starts from that still.

#### The sheet rows — `prepare --body-plan`

The rows `prepare` writes for an image model (`prompts/<state>.txt`, [atlas workflow](atlas-workflow.md))
were measured on people too. The walk and run rows moved "body, arm, leg, hair, and prop", the front and
three-quarter-front walks "alternating leg, arm, shoulder", the diagonal runs traded "foot-contact phases"
of "the left and right legs", the wave was "arm pose only: arm down, arm raised, hand tilted" (its default
action "friendly hand wave gesture"), every row's anchor lock spent its motion on "arm counter-swing" and
turned "the body, feet, shoulders", and a diagonal row read its facing by "shoulder overlap, hand/foot
placement". `prepare --body-plan` (the same plans and scene form; repeatable; over the request's
`body_plan`) gives a body that is not one biped:

| Row sentence | for a body that is not one biped |
|---|---|
| walk, run, `running-right` / `-left` (`STATE_REQUIREMENTS`) | `STATE_REQUIREMENTS_ANY_BODY`: locomotion through "the movement of the body, whatever it moves on, loose parts such as hair, a mane or a tail, and props only" |
| `frontwalk`, `45_frontwalk` | "alternating steps of whatever it walks on and body-height changes"; "the contact and passing poses" |
| the diagonal runs | "alternating contact phases of whatever it moves on so the two sides clearly trade forward reach" |
| wave | the one side that lifts, as the clip's wave does (`MOTION_TEXT["wave"]`): "lowered, lifted, swaying, returning", the body planted where it stands or rests; the default action `DEFAULT_ACTIONS_ANY_BODY["wave"]` where the request names none |
| anchor lock (`ANCHOR_LOCK`) | `ANCHOR_LOCK_ANY_BODY`: "the contacts of whatever it moves on, body height, body lean, head bob, the bounce of hair, a mane or a tail"; rotate "the body, face angle, and gaze"; "step phase", "contacts" |
| a diagonal row's facing | "the silhouette of hair, a mane or a tail, the overlap of near and far parts" |
| every row, last state requirement | what it stands on (`body_plan.text`), as after a clip's motion sentence |

The run records the plan in `sprite-request.json` as `body_plan` (absent without one), and a run
prepared again from that request keeps it. No body plan, or one biped, writes every row prompt as
2.32.0 did, byte for byte (`tests/fixtures/prepare-rows-v2.32.0.json.gz`, `tests/gen/test_prompt_freeze.py`).
The new rows are not yet measured on an image model. A request's own `action` is its own words and is
never rewritten.

### A clip from a video MCP on your agent — ZCRE

sprite-gen never calls a video MCP. When your own agent (Claude, Codex) has one connected — ZCRE's
remote MCP (`https://mcp.zcre.ai/mcp`, signed in with your own ZCRE account, paid from your own
credits, under ZCRE's terms) — the agent makes the clip with that MCP's tools and sprite-gen does the
rest, stage by stage, with the verbs `video-set` runs: `video-prompt` prints the prompt `video-set`
would send, `video-frames` and `video-loop` cut each mp4 the agent saved, and for a walk or run cut in
two or more directions `video-cycle-align` gives them one cycle length, each turned to start on a
foot strike ([loop repair](loop-repair.md) section 4). `video-set` itself is not run on this route, so
what it does for a set happens only when you run it. What crosses the boundary is a PNG, a prompt and
an mp4.

```bash
sprite-gen video-canvas --still still.png --state walk --facing right --out item/canvas.png --report item/canvas.report.json
sprite-gen video-prompt --direction side --state walk --facing right --no-last-frame --json > item/prompt.json
# the agent, with its ZCRE tools and grok-imagine-video-1.5: prepare_upload for item/canvas.png and the
# transfer it describes, query_zcre upload to confirm it, query_zcre quote with prompt.json's prompt and
# duration, the credits shown to the user and approved, create_generation with that quote, query_zcre
# status until it succeeds, the output saved as item/clip.mp4
sprite-gen video-frames --clip item/clip.mp4 --out-dir item/frames --spill auto --reference item/canvas.png
sprite-gen video-loop --frames-dir item/frames/keyed --out-dir item/loop --fps <fps in item/frames/frames.report.json> --state walk --cycle auto --anchor motion-auto --facing right --name side-walk
# a set, once the walk is cut in every direction (each as above, in its own folder):
sprite-gen video-cycle-align --loop-dir front/loop --loop-dir side/loop --loop-dir back/loop --view front --view side@right --view back --report walk.cycle-align.json
```

A walk or run is cut with `--anchor motion-auto`. A clip on this route has no end frame to bring it
back to its first frame's size, and `motion-auto` is the anchor that measures the size over the clip
and holds it before the cycle search
([section 4](#4-loop--period-first-seam-second-then-the-gait-floor)); `video-loop`'s own default for a
walk or run, `body`, does not. A jump takes no `--anchor`: `motion-auto` is refused for a state that
is not a walk or run. `--fps` is the frames report's own, never a number written in by hand.

The set stage is the same step `video-set` runs after its loops are cut (`--align-cycles auto`, its
default): one `--loop-dir` per direction of the same walk or run, one `--view` each in the same
order, so that every loop starts as the same own foot lands wherever its view can tell the feet
apart. A frame that falls between two source frames is made by RIFE, and the command fails where one
is needed and RIFE is not installed; `--between nearest` makes none. A frame RIFE made that melted
(lost its outline where legs crossed too far) is replaced by the nearer source frame and named
(`--between auto`, the default; [loop repair](loop-repair.md) section 4). A loop whose view cannot
tell its feet apart — or tells them apart by too small a margin (`foot_why` `low-margin`) — starts
on its larger strike and is listed under the report's `unnamed_feet` with its two strike frames in
`cycle/`: look at the first, say which own foot lands there, and run the same alignment again with
`--foot <loop>=left|right` — that loop alone turns to start on the set's foot
(`start_foot_source: "given"`).

`--character` or `--motion` words that turn the subject another way than `--facing`, or put a
`--handed` item on its other side, come back in `warnings`; the prompt is the same either way
([prompt-assembly](prompt-assembly.md)).

`video-prompt --json` also gives the duration (the state's own, as `video-set`), whether the clip
ends on the canvas (`last_frame`), the loop cut (`cycle`) and the canvas, clip, frames and loop
commands with placeholders; a front or back walk adds `start_still`, the mid-step redraw `video-set`
makes before its canvas (`sprite-gen gen --ref`, your image provider). Its `loop` command names no
`--anchor` and it lists no set command: add `--anchor motion-auto` for a walk or run, and run
`video-cycle-align` for a set, as above. `--character`, `--motion`, `--model` and `--body-plan` reach the
prompt as they reach `build_prompt`. What the ZCRE route supports:

| | Through ZCRE's MCP |
|---|---|
| Model | `grok-imagine-video-1.5` (Grok Imagine Pro), image-to-video only. There is no Lite model there, so `LITE_WALK_TEXT` never applies. |
| End frame | None. `--no-last-frame` refuses what `video-set` films pinned — idle, attack, a walk or run in a diagonal (`pins_last_frame`). `--unpinned` films one anyway, with a warning: the prompt asks for an evenly paced repeat (an attack for one strike), the loop is searched instead of cut whole, and it may not close. Walk, run and jump from the side, front and back are the route. |
| Resolution | 480p, 720p, 1080p. The clip keeps the canvas's ratio (a 1024² canvas came back 544² at 480p and 960² at 720p). |
| Audio | Always an AAC track; there is no option to leave it out. `video-frames` reads the picture alone, records `audio_streams`, and the loop outputs carry no sound. |
| Cover image | The mp4 also holds a one-frame mjpeg cover (`attached_pic`); `video-frames` skips it (`cover_streams`) and reads the first video stream that is not one (`stream_index`). |
| Picture | H.264 `yuv420p` (4:2:0), 24 fps, as a `sprite-gen video` clip; the frames report records `codec` and `pix_fmt`. |
| Side facing | `video-set`'s facing observation is not run on this route; check the still faces `--facing`. |
| Set | `video-set`'s cycle alignment (`--align-cycles auto`) is not run on this route: once a walk or run is cut in two or more directions, run `video-cycle-align` on their loop directories, with each one's `--view`. |
| Size | A clip here has no end frame, so a walk or run may grow or shrink as it plays: cut it with `--anchor motion-auto`, which holds the size before the search (section 4). `video-loop`'s default anchor for a walk or run, `body`, does not hold it. |

Measured 2026-10-04, one side walk end to end on that route: a 1024² still, `video-canvas` (square,
1.8 s), 720p 3 s, quoted and charged 52 credits, 33.6 s from submission to `succeeded`; the clip was
960×960, 24 fps, 73 frames, H.264 `yuv420p` with an AAC track and a cover image; `video-frames` kept
73 frames and no edge contact, and `video-loop --cycle auto`, at its default anchor (`body`), cut a periodic 20-frame walk (seam 0.72)
with three frames remade by RIFE and a jolt warning (index 0.85), as a Pro walk can carry.

### Handedness — an item on one side

A watch on one wrist, a pin on one side of the head, a bag on one shoulder: a right-facing view turned
over puts the item on the other side. Mirroring stays the default — five views make eight directions —
because drawing the left-facing views costs as much again. For a character whose item must stay put,
draw them instead, and say where the item is.

**The item, once.** `--handed "<item>=<left|right> [body part]"` names an item and the character's own
side it is on (`handedness.parse`; repeatable): `"the black smartwatch=left wrist"`. The engine works
out where that side is in each view (`handedness.placement`):

| view | own left side, turned right | own left side, turned left |
|---|---|---|
| `front` | the picture's right | (not turned) |
| `back` | the picture's left | (not turned) |
| `side` | the far side, behind the body | the near side, toward the viewer |
| `front_diagonal` | the picture's right, far side | the picture's right, near side |
| `back_diagonal` | the picture's left, far side | the picture's left, near side |

The own right side is the mirror of every row. A side view has no picture side — both arms cross the
middle of the body — only near and far.

**The still.** `sprite-gen gen --direction <view> [--facing right|left] --handed …` adds the engine's
view sentence (`still_view_text`, the one a diagonal or front still is drawn with) and, per item, the
handedness sentence (`handedness.text`): the item is on the own left wrist only and the right wrist has
none; where that wrist is in this view (at the picture's right, on the far side); and that these
sentences decide the side whatever an attached picture shows. A side or diagonal view needs `--facing`,
a front or back view takes none. With `--handed`, `--facing-fix mirror` is refused and `regen` never
falls back to mirroring a still-opposite retry: a mirror moves the item. Do not attach a picture whose
item is on the other side — a mirrored still, or a side picture drawn the wrong way: check it with
`handed-check` first. A front or back picture, where the item's side is plain, is the safe reference.

**The far arm of a side view.** A side view hides the far side behind the body, except for what swings
out from behind it. A walking character's far arm comes forward of the body with every step, and what
its wrist or hand wears shows then. So an item on a wrist, a hand, a forearm or an elbow
(`handedness.limb`, the one table: `SWINGING_ARM_PARTS`) is said, on the far arm, to show wherever that
arm is out in front of the body and to be hidden where the body covers it. Every other far item — on an
ear, the head, a tail, the body, a leg, a shoulder, the upper arm — is said hidden, as in 2.22.0, and
gets no arm sentence: a far shoulder stays behind the body as the character walks, and a leg's item
showing as it steps forward was never filmed. 2.22.0 said every far item hidden and never named it in
the clip, and a watch on the far wrist was then in no frame of the side walk.

A clip keeps what its first frame shows and makes up what it hides. For a walk or run with an item on
the far arm, draw the side still **mid-stride with both arms in view**, and say the pose in your
prompt — the engine's sentence says where the item shows, not how the character stands:

> caught in the middle of a walking stride: the far arm swings forward, its forearm and hand out in
> front of the chest, clear of the body's outline, with the watch on that wrist in plain sight; the
> near arm swings back past the body and shows whole

`video-prompt --json` names this under `still_needs` (and on stderr), and `video-set` under each
item's `still_needs`, whenever a walk or run has an item on the far wrist or hand of a side view.

**The clip.** `video-prompt --handed …` and `video-set --handed …` end the clip prompt with where the
item stays (`build_prompt(handed=...)`). The clip starts from a still already drawn with the item in
place, so the sentence anchors to the image and names the item once, positively, where it shows: "The
black smartwatch stays on the wrist at the right of the picture, nearer the viewer, for the whole clip,
exactly as in the image, and on no other wrist." In a side view's walk or run with an item on a wrist,
a hand, a forearm or an elbow, the arms are said to swing (`handedness.ARMS_SWING_TEXT`: "Both arms
swing back and forth with each step, opposite to the legs."), and then:

- on the far arm: "The black smartwatch stays on the wrist that wears it in the image, on the arm on
  the far side of the body, and shows each time that arm swings forward; the arm nearer the viewer
  stays bare, exactly as in the image."
- on the near arm: "The black smartwatch stays on the wrist that wears it in the image, on the arm
  nearer the viewer; the other arm, on the far side of the body, stays bare both where it shows behind
  the body and where it swings out in front of it."

No sentence holds an arm still: the arms swing with the walk. The other arm is said bare, never as a
list of what it must not wear — a clip prompt that lists that draws it there. Where nothing brings the
far side into view — any other part, or a state that does not step (idle, attack) — the
item is not named at all: "The wrist nearer the viewer stays bare, exactly as in the image, for the
whole clip." A side view names the near part "nearer the viewer", not "in front of the body": in a
mid-stride still the far arm is the one in front of the body. Items on the same wrist share one sentence, and with an item on each wrist neither is
called bare. `handed-check --strap` finds a bare band that a clip still grows on the other arm. A front
or back walk's mid-step redraw (`walk_start_prompt`, `video-prompt`'s `start_still`) ends with the
still's handedness sentences too: redrawn from the base alone, the model may put the item on the other
wrist.

**Left-facing views, drawn.** `video-set --facing right,left` films every side and diagonal view both
ways, each from its own still: `--base side@right=E.png --base side@left=W.png --base
front_diagonal@right=SE.png --base front_diagonal@left=SW.png …` (front and back once, as before). A
turned view with no still for one facing is refused, naming the `gen --direction … --facing …` call
that draws it — the engine never turns one over for the other. Items are named `side-left-walk`, the
table reads `side (left)`, each side still is inspected for its own facing into its own copy
(`side-left.facing.png`), every gait is aligned across all eight (`--align-cycles auto`), and a
left-facing diagonal is canvassed, prompted ("heading diagonally toward the viewer and to the left")
and cut (`video-loop --facing left`) as a left one. One facing keeps the item names and table as before.

**The check.** `sprite-gen handed-check --strip <loop>.strip.png --direction <view> [--facing …]
[--state walk] --handed … --marker '#RRGGBB'` (or `--image`, `--frames-dir`) finds the item by a saturated colour
nothing else on the character has — a watch's screen — and checks every frame:

- a view with a picture side: every blob of that hue is on that side of the body's centre (the mean x
  of its opaque pixels);
- every view: the item in at most one place (blobs within 2 % of the body height are one; one smaller
  than a quarter of the largest is a speck — a clip's colour blocks tint outlines toward the item's
  hue — recorded and drawn, not judged);
- the near side of a diagonal view: the item shows in at least half the frames; of a side view, in at
  least three quarters — a near arm is in view the whole walk, a far arm's item for the half of it the
  arm is forward, and in a side view that share is what tells the two apart. Against `--reference` a
  frame counts only when the item is more than a sliver (its largest blob larger than half the item
  seen whole);
- the far side of a side view, an item on a wrist, a hand, a forearm or an elbow (`handedness.limb`):
  every blob is out in front of the body — on the facing side of the body's centre by at least a tenth
  of the body's height (`far_in_front`). An item that shows at or behind the centre is on the near arm,
  the wrong one. With `--state walk` or `run` it must also show in at least a quarter of the frames
  (`far_shown`); without `--state`, or for a state that does not step, that is reported unchecked.
  Against `--reference` a sliver is not judged for where it is;
- the far side of a side view, any other item (an anklet, a shoulder piece, a pin on the head): no
  blob larger than half the item seen whole on `--reference` (a keyed front or back picture;
  `far_hidden`).

Without `--reference` the size rules are not checked; the report says so (`far_hidden` /
`near_whole`: `checked: false`) and a frame counts as shown by any blob.

`--strap '#RRGGBB' --zone TOP,BOTTOM` adds the item's band seen without its marker: pixels of that
colour in those rows of the body (fractions of its height), eroded so outlines drop out, wider and
taller than 1.6 % of the body height and away from every marker blob, are another place the item
shows. It is opt-in because dark colours are common — outlines, a hat, motion smear on the legs — and
the zone is the character's wrist height. `--board` draws every frame with its blobs ringed (green as
expected, red not) and bands boxed, for what pixels cannot judge: an unmarked item, an item hidden on
the wrong hand. The exit code is 1 on a failure.

A set that made its left-facing views by mirroring fails: a front view turned over puts the item on
the wrong side; a side walk facing right (the item on the far arm, in view while that arm is forward)
turned over shows it in too few frames for a near arm; one facing left (the item on the near arm,
crossing the body) turned over shows it behind the body's centre; a diagonal turned over moves it
across the picture. `tests/qa/test_handed_check.py` draws each view as it should look, passes it, and
fails it turned over.

## 3. Frames — extract, key, check the edges

`ffmpeg` extracts every frame; the clip's real fps is recorded (never assumed). Each
frame goes through the same `cutout` engine imported stills use (`--key auto` reads
the corners; green/magenta route to the extract matte). The matte keys from the
background colour **as the model painted it**, not only from the pure key: the flat
border colour is detected per frame (`extract.detect_background_key_rgb`, the mode of an
RGB histogram over the corner/border samples that are the key's hue family) and a pixel
is erased when it is within the key radius of *either* the pure key or that painted
colour. The 96 radius is unchanged — what moved is its centre. Before this (2026-09-11)
a (8, 162, 24) green sat at distance 96.38 from pure green while (7, 163, 24) sat at
95.34, so a clip was keyed half-and-half pixel by pixel and the edge check read the
leftover background as a clipped subject. The report records `chroma_key_painted` per
frame.

**The border is classified by a looser rule than the interior** (2026-09-12). The
detector's "hue family" test used to be `extract.is_key_family`, the interior rule, whose
two-channel balance (the dimmer keyed channel ≥ 0.8 of the brighter) exists to keep hot
pink (250, 77, 150) and purple (213, 112, 246) alive *inside* the subject. Grok paints
`#FF00FF` as (216, 46, 147) / (225, 52, 155) — blue/red ≈ 0.68 — so that rule said "not
the key" for a colour that filled the whole border, the detector fell back to the declared
key, and at ~120 from pure magenta the entire background survived. A flat border is
already evidence of *background*, so the border rule `extract.is_border_key_candidate`
drops the balance and keeps the hue signature: every keyed channel lit (≥ 64), every
unkeyed channel dark (< 64) and under 35 % of the brightest keyed channel. It is a
superset of the interior rule (family ∪ signature), so nothing the old detector found is
lost, and hot pink / purple fail it on a different axis (their green channel is lit). One
function classifies the border everywhere — `detect_background_key_rgb`, `cutout --key
auto`, `video-canvas --key` and the edge-contact split below. The interior rule is
untouched. And because the painted colour's authority is border evidence, its erase ball
is bounded by the background it came from: it erases the signature pixels plus whatever
inside the ball is 8-connected to them (the antialiased rim, whose blend with a lit
subject colour lifts the unkeyed channel over the bar), never an isolated look-alike patch
inside the subject — hot pink sits 46 from Grok's magenta. The declared key's ball stays a
colour ball, position-blind.

Two consequences worth knowing. Dark outline halo (the antialiased blend between subject
and a dark-painted key) is now erased with the background — on the three 2026-09-11
walk clips that was outline pixels only (0 newly opaque, interior holes ≤ 7 px per
frame, seam ratios moving in the third decimal, periods and start frames identical).
And **a key-family subject colour is at more risk the darker the painted background**:
the erase ball follows the detected key, so on a still painted (20, 120, 25) a dark
olive (40, 70, 35) inside the subject (distance 54.8) is erased, while the same olive
survives untouched on (8, 162, 24), (5, 200, 10) or pure-key backgrounds. Choose the key
away from the subject's hues ([chroma-alpha.md](chroma-alpha.md)) — that rule now covers
the painted key's darker variants too.

Every keyed frame first has its **specks** erased (`drop_specks`): a piece of the frame — its
opaque pixels, corners joining them — under 1 % of the frame's largest piece, the body
(`SPECK_MIN_FRACTION`, at least 8 px), and more than a tenth of the body's height away from it
(`SPECK_APART`). That is a fleck the model drew drifting across the background, which survived the
matte. Small pieces near the body stay: the outline's loose pixels, a shadow drawn under a shoe,
a part of a shoe the matte cut loose. A frame with no speck is not written again; each report
row says how many its frame lost (`specks`) and the report totals them (`specks.dropped`,
`specks.frames`). Without this a fleck that crossed an edge band in one frame was read as the
subject framed too tight, and `--anchor motion-auto`, which never cleans a frame, kept it in
the loop's cells and widened them to reach it.

The report also carries per-frame alpha coverage and an **edge-contact check**, read after the
specks are gone: any opaque pixel in the top/left/right 4-pixel bands fails the run. Each contact pixel is
classified by its *raw* colour and the piece of the keyed frame it is in (its opaque pixels, corners
joining them, as the specks are read): the declared key's hue family in a piece with nothing else in
it is **`residual`** (background the matte did not erase); anything else is **`subject`** — a
key-tinted pixel joined to the subject included, since a body that reaches a band brings its
antialiased rim, the key blended into it, there first, and on a frame where only that rim is in the
band, read by its colour alone, the clip was refused as leftover background. The two
defects fail with different messages: residual-only contact points at `video-canvas`
(normalize the base still and regenerate); subject contact means the model framed too
tight and points at a taller/wider canvas. When both occur the message names both.
`residual` can also be reported on the antialiased fringe where a subject genuinely
touches the edge (a key-tinted blend pixel with low alpha), so a "framed too tight"
message with a small residual count is still a framing problem, not a key problem.
`--allow-edge-contact` turns the whole check off. `--allow-subject-edge-contact`
accepts only the subject at the edge — the clipping a `--fit tight` canvas chooses —
and still fails a frame where the key alone reaches the edge, which is a keying defect
whatever the framing. A frame where the subject touches the edge passes with the
key-tinted pixels beside it (fringe, or the key reflected on metal): the same reading the
refusal message gives a mixed contact. Contacts stay in the report either way, with `edge_policy` saying which rule
applied (`refuse`, `subject-allowed`, `off`).

## 3a. Spill — key colour the model painted into the subject

A video model does not only leave the key around the subject; it paints it *onto* the
subject — a green sheen across polished metal, a tint on a pale surface. Those pixels are
opaque, often many pixels in from the edge, and form patches far larger than the chroma
engine's trapped-spill cap (clusters up to 0.5 % of the subject). The engine leaves a large
key-tinted patch alone on purpose, because in a still it is usually the subject's own
key-coloured material. In a clip it usually is not.

The still the clip was made from settles it. `video-frames --spill` takes:

| Mode | What is corrected |
|---|---|
| `small` (default) | only small key-tinted clusters — the still pipeline's rule, byte-identical output |
| `full` | key-tinted clusters of any size, including faint tints (colour only: alpha is unchanged) |
| `auto` | keys `--reference` (the still) with the same matte and counts its interior key-hued pixels at the same threshold used by `full`; a share ≤ 0.5 % means the still has no key-coloured material of its own → `full`, otherwise `small` |

`video-set` passes `--spill auto` with each item's `canvas.png` as the reference (override
with `--spill small|full`), so a green-free character loses the reflections while a
character that *is* green keeps its colour. The decision and its numbers are recorded in
the frames report under `spill`. The correction is the engine's own `despill_color` blend
model (observed = (1−k)·subject + k·key, solved for the subject), so colours without key
tint are untouched. `small` keeps the conservative tint threshold of 40; `full` lowers it to 8.
For `full`, a key hue requires every keyed channel to exceed every non-keyed
channel: `G − max(R, B)` for green, `min(R, B) − G` for magenta. This same
excess selects pixels for correction and drives the `auto` reference check, so yellow/cyan
are not mistaken for green, or red/blue for magenta. The average-channel tint
metric remains unchanged in `small` and the edge matte.

The blend fraction still uses the linear average-channel tint, not hue excess.
Full correction recovers mean brightness but does not amplify colour differences
within the keyed or non-keyed channel group. Otherwise a small red/blue imbalance
can become a strong secondary cast when much of the observed colour is key light.
This is bounded colour recovery, not reconstruction of the original material:
blue or purple already present without the key hue remains unchanged.

The `auto` reference test discounts dark pixels (all channels below 64) in the
matte's 4-pixel edge-unmix band. Such contamination along an antialiased outline
is weak evidence of an intentional material. Bright green/magenta accents still
count even on an edge. The report names the metric, band and dark-only policy.
Genuine key-coloured material above the 0.5% share still keeps the conservative
mode. Tiny accents or dark, edge-only material can fall below that reference test; use `--spill small` when preserving those is essential.

The reference is keyed on its subject window only. A canvas padded for motion room
(`video-canvas`) is mostly key, and the matte's memory grows with every pixel it keys,
so keying the whole canvas made the judgment cost grow with the padding: about 0.14 GiB
per megapixel, past 4 GiB for a 30-megapixel canvas. The window is the box of pixels the
hard cut cannot erase (farther than 96 from the declared key, and not the painted key's
own colour), plus one keyed pixel all round. The painted key is read on the whole still,
and with that ring the window gives exactly the counts the whole still gives, so the
decision does not change. The still itself is only decoded and read a band of rows at
a time. A window over 6 megapixels is keyed on every n-th pixel each way instead, so no
reference costs more than that to judge. The report records `reference_size`,
`reference_window` and `reference_stride` (1 = every pixel of the window).

### Edges: `--decontam palette`

`--spill` fixes key colour painted *into* the subject. The strands and outlines at its
edge are a different problem. H.264 4:2:0 has already averaged the key into the chroma of
anything one or two pixels wide, and despilling what is left turns thin red strands
orange. `video-frames --decontam palette` (also `video-set --decontam palette`) re-explains
each edge pixel as a blend of the local key background with one colour the subject owns,
and writes that colour at the pixel's observed luma. It uses the video fit and one palette
per clip, learned on the first frame, so edge colours cannot flicker between palettes. Within
the edge band the edge gate reads, it gives no coverage to a pixel the matte left transparent,
so it adds no edge contact: a clip with none under `off` has none under `palette`. The
default `off` keeps frames byte-identical. Method, guards and measurements:
[chroma-alpha.md](chroma-alpha.md#decontam--give-the-edge-the-subjects-own-colour-back).

## 3b. Canvas shape for raised limbs and wide costumes

`video-set` picks the canvas from the state row alone. Two things that are not a jump or
an attack still leave a 1:1 frame: limbs raised in a celebration, and a costume that is
wider than the body (a skirt, a veil, a held object). Both fail `video-frames`'s
edge-contact check — the clip was made, the frames were cut, and the run stops at the
gate. The state table now routes `cheer`, `wave` and `celebrate` to the wide canvas, and
`video-set --shape wide` forces it for every state of a batch when the costume is the
reason. The same `--shape` is what `video-canvas` already took for a single still. The
forced canvas is the forced-wide row (35 % above), so a jump in that batch keeps its
head-room; an attack in it gets that row too, not its own 20 %. A walk or run gets its own wide
row, with nothing above (section 1).

## 4. Loop — period first, seam second, then the gait floor

The 2026-09-08 lesson: a single-start "most similar later frame" search lands on the
**1.5-cycle look-alike** of a gait (legs swapped) and produces a loop that hitches at
the wrap (side walk picked 39 frames where the period was 28; run picked 25 where it
was 17). `video-loop` therefore:

1. builds the distance matrix `D` on 96-px premultiplied thumbnails;
2. reads the **global period profile** `P[L] = mean_j |f[j] − f[j+L]|` and takes the
   *smallest* local minimum that is within 15 % of the deepest one — exact repeats dip
   again at 2× and 3× the period, the half-period look-alike dips noticeably less;
3. only then ranks **starts** for that period (± 1 frame):
   `seam = D[i+L-1][i]` over the mean adjacent distance inside the cycle.
   Penalise distance from 1 in log space, so a repeated pose at the wrap does
   not win just because its distance is small. For walks and runs, also compare
   corresponding frames one cycle apart in the neighbourhood of the cut: a
   quarter-cycle on either side, clipped to available source pairs. Add their
   mean distance divided by the candidate's mean adjacent distance to the wrap
   penalty. This favours a coherent repeating region over an accidental endpoint
   match, without preferring an early or late start. Other states keep wrap-only
   ranking. `cycle.selection` records the half-open source-pair range, repeat
   error, normalised error, wrap penalty and combined score; `next_frame_distance`
   retains the single-frame diagnostic. Fixed cuts do not use this ranking.
   The seam is an area measure: a thin part (a staff, a flag) that jumps at the wrap
   changes few pixels and hardly moves it. `--anchor motion-auto` also weighs the top
   of the silhouette at the wrap and chooses again when it jumps, and every walk or run
   reports that jump and warns on it ([loop repair](loop-repair.md) section 3,
   "The seam pop"). Once the top jumps, the held part is read below its top too, and a staff
   that closes only on two steps is cut two steps long — one cycle (`cycle.steps`, "The held
   side"). The cut taken is screened for one step of a cycle twice as long, over the whole clip
   (`cycle.step_screen`, "The step screen"); nothing is cut on it, and `--steps` is the count
   that cuts again.

This neighbourhood check measures temporal consistency, not anatomical leg
identity. A consistently repeated malformed motion can still score well; visual
review remains necessary when correct limb alternation matters.

Windows come from the state profile (`STATE_PROFILES`). **Gait states take theirs in
seconds**, because a stride is a fact about the body, not about the clip length: walk
0.5–1.6 s, run 0.3–1.2 s, the ceiling capped at half the clip (a cycle must be seen twice
to be confirmed). The other states are fractions of the clip length: idle 60–95 %
(breathing is slow and not periodic — the lowest seam is a long window, and idle is
exempt from the periodicity gate), jump 11–45 %. Attack keeps the 11 % floor and
searches up to 2.5 seconds while retaining at least 0.5 seconds (and at least eight
frames) of observed repeat context. `--min-len/--max-len` override the window — on every path that
sets a cut's length, the gait fallback, `--steps` and the one-shot failover included; a cut outside
them fails rather than being written.
Attack's periodicity floor is `0.15 + 0.85 * max(0, 1 - (n - lag) / lag)`:
less than a full period of comparison requires a deeper dip. Reports include both
available pairs and the every-other-frame profile sample count. This is a coverage
policy, not a statistical confidence estimate. Gait windows and ranking are unchanged.
The walk floor is low on purpose: a legless body "walks" as a fast bounce (about 13
frames at 24 fps) while a gait is 24–28, and it is the 15 % depth rule and the gait
floor — not the window — that reject the one-step half period (on a biped and a
quadruped the half period dipped only 55–70 % as deep as the full one). Walk used to be
6–31 % of the clip; at the 3 s default that ceiling was 0.96 s and put a 1.0–1.3 s
stride out of reach (2026-09-18: one of four walks refused, another cut at a half step).
The ceiling is a bound in seconds rather than simply "half the clip" because the
periodicity gate measures the period's dip against the profile mean over the whole
window, and a ceiling that grows with the clip inflates that mean until a single hop
in a jittering stand passes as a walk.

For gait states, a duration above the floor is not proof that both phases are present.
If a local minimum near twice the chosen period is within the existing 25 % repeat-error
tolerance and still passes the periodicity gate, the detector retains the longer candidate
once, inside the requested window. Near-exact repeats (repeat error at most 10 % of an
ordinary adjacent step) stay short. This is a conservative response to ambiguous harmonics:
a genuine short gait may be shown twice, at the same source speed. It does not identify
anatomical left/right contacts. The report records `half_period_guard.reason =
"ambiguous-harmonic"` and `cycle.review_recommended = true`. See [loop review](loop-review.md)
for the visual review contract and manual overrides.

**The fundamental period: recorded, never cut on the pixels' word.** A motion that repeats every
P frames repeats every 2P as well, and 2P can be the better repeat: a clip drawn on twos (a new
drawing every other frame) whose cycle is an odd number of frames shows each cycle half a drawing
off the one before, so the profile dips deeper two cycles on than one, and the minimum taken above
is two cycles. Played alone that loop is clean; resampled to the length of a set whose other loops
hold one cycle ([loop repair](loop-repair.md) section 4) it walks at twice their speed.

The engine does not cut it shorter. Half a gait cycle on, the legs are back in place with near and
far swapped; where the two legs are drawn alike (one colour of trousers, white fur) that one step
and a true cycle half a drawing late are **the same picture**. Every rule tried that took the half
as a cycle — an absolute pixel distance, the legs' step count, the pose on the motion's path — cut
some one-stride walk or run to its half step, and the leg signal fails both ways (a heel kick
swings the stride twice a step; a diagonal view's two steps open the feet unequally). So
`video-loop` cuts the period it always cut, and only **records** what a half or a third of it shows
(`cycle.fundamental`, `sprite_gen/video/period.py`):

- each candidate is the profile's own minimum within a frame of P/2 and P/3 (`checked`: `period`,
  `of`, `divisor`, `cycles`), no shorter than the gait floor (walk 0.6 s, run 0.35 s; other states
  the window's floor — under the floor a repeat is one step, the half-period guard's case);
- it is measured: `periodicity` (its dip below the profile's mean), `pose` (how far the frame one
  candidate on lies off the motion's own path — over the short spans (a, b) around a frame, the
  least `D[a][x] + D[x][b] − D[a][b]` — over that frame's own distance, both in playback steps;
  `off_path`, `distance`), and, for the record only, the legs' `steps` and `follow`
  (`sprite_gen/video/legs.py`, the signal the foot-strike turn reads);
- one rule, `period.verdict`, decides (`verdict`, `why`): a candidate that repeats — dips 0.15 or
  more below the profile's mean — is a `suspect`, and the suspects are listed (`suspects`). That is
  the whole rule. Nothing says "this is a cycle", and nothing says "this is not one": the pose and
  the legs are evidence for whoever looks, never a reason to refuse. A rule that refused "another
  pose" (pose 0.8 or over) missed most two-stride loops whose second stride was redrawn a little
  differently — moved a pixel, a few per cent lighter, the foot kicked higher — because in pixels
  that is off the motion's path just as a step with the legs swapped is.

`review_recommended` keeps its meaning (the ambiguous-harmonic retention above). The record is for
the set stage: `video-cycle-align` screens every loop by the same rule, stops a set on a suspect,
and takes one cycle out of a loop only when told how many it holds (`--cycles`, [loop
repair](loop-repair.md) section 4). The local search of `--anchor motion-auto` records the same. A
loop whose steps are known (`steps: 2` in `strip.json`: cut two steps long so a held part closes, or
cut again on a count with `video-loop --steps`) returns half way at its second step; the set stage
reads that return as the step it is, not a second cycle. The screen also looks the other way there:
a cut that may be one step of a cycle twice as long (`cycle.step_screen`, read over the whole clip)
stops the set the same way, and only a count — `video-loop --steps 1` — cuts it again.
The rule leans one way on purpose: a suspect costs a look, a miss a loop that plays twice as fast.
A run's half step repeats as well as most of its strides do, so a run set is suspected nearly
every time and stops until its loops are counted; a walk's half step is under its floor more
often. What still escapes it is a second stride that drifts across the picture in steps of four
pixels or more without the body anchor's ramp, and any loop whose half is under the gait floor
(a walk faster than 0.6 s a step).

**The size is held before the search** (`--anchor motion-auto`, `--size-hold auto`, the default).
A walk or run filmed from its first frame only (no end frame) often grows or shrinks as it plays:
a front walk comes a little closer with every step, a back walk goes away. The loop is then cut on
frames that change size, so the frame one cycle after the first is not the first's size and the
loop pops at the wrap. The change is read one cycle on (`gait_fallback.cycle_drift`): the lag at
which the pose, cut out about its feet, matches itself best (in the state's window, at most half
the clip), and each frame's height (to a fraction of a pixel: the rows' coverage summed) against
the frames one, two, … of those lags later. The median of those changes per frame, carried over
the clip, is the `drift`. At 1 % or more (`SIZE_HOLD_MIN`) every frame is scaled back to the
first frame's size about its fitted foot point before the cycle search, so the search, the seam
and the cells all read frames of one size. Below 1 % the frames are searched as filmed.

Why one cycle on, and not a line through the clip (2.24.0): a clip that starts from a standing
pose and settles into the walk over its first steps (knees bending, the body leaning in) is a few
pixels shorter from then on, and a line through every frame reads that one change of pose as a
body that shrinks the whole way, and held it. The same pose a cycle later is the same size
unless the body really changes, and the few frames of the first pose do not move a median of the
rest. The other way round, a settle can
pull the line flat over a walk that does grow; one cycle on, that growth is still read and held.
On a synthetic walker that stands 6 px taller for its first frames and then walks at one size,
the line read −2.2 % and the hold, so scaled, then found no cycle at all; one cycle on reads 0
and the clip is cut as filmed (`tests/video/test_gait_fallback.py`).

The report says what was measured and done (`size_hold`: `drift`, `applied`, `padding_ltrb`, and
the evidence: `method: one-cycle-on`, `lag`, `pose_match` — the pose's mismatch at that lag over
its mean across the window, 0 for an exact repeat — `pairs`, `per_lag`, the change over one lag,
and `drift_fitted_line`, what a line through the clip would have read). `--size-hold off` searches
the frames as filmed, as before 2.24.0. When the hold came in (2.24.0, read off the line) it was
measured on one front catwalk filmed from its first frame (+2.0 % over 3 s): the engine's seam
ratio of the cut went from 1.44 to 1.19.

**A lead-in is left out of the search** (walk and run, `--cycle auto` or `periodic`). A video
model may reframe a small subject in a clip's first frames: the body grows (or shrinks) by a third
or more in under half a second, then walks at its new size. Searched with those frames, the
motion analysis reads the reframing as the clip's motion and the walk's repeat is lost; the gait
fallback, which then scaled by a line through the clip, read the step as a steady growth and
shrank the walk. So the clip's sizes are read one cycle on (`gait_fallback.cycle_drift`, the
hold's model, a median over the whole clip). A clip opens on a lead-in when its first frame's
height and size (the square root of the frame's summed coverage) are both off that model by 10 % or
more, the same way (`LEAD_IN_MIN`): a reframing scales the whole body and moves both, a pose moves
one — an item held up from the second frame on makes the first frame shorter and no lighter, legs
spread in a side step add size and no height. The lead-in (`gait_fallback.lead_in`) runs on while
the height stays that far off, that way; a walk's own frames stray from the model by a few percent. The search — the size hold, the motion
analysis, the cycle search and the gait fallback — reads the clip after it as a clip of its own,
its window included. What it finds, and what it refuses, is said in the clip's own frame numbers:
every `start` and `context_pair_range` in the report's `cycle`, refused candidates too, so a cut
taken again from them (`--cycle fixed --start`) is the frames they name. The report records it on
every walk and run (`lead_in`: `frames`, 0 for none; `height_change`, the walk's height where the
lead-in ends over the first frame's, less 1; `first_off` and `first_off_size`; `search_from`, the
first frame the search read). A cut the caller names (`fixed`, `pinned`) is the caller's frames, lead-in or not. A clip
too short to read its size one cycle on has no lead-in (`why`). On a synthetic walker filmed at
0.63 of its size and reframed over 9 frames, the search as filmed found no cycle, nor did the
fallback; after its 5-frame lead-in it cuts the 24-frame walk (`tests/video/test_edge_speck_lead_in.py`).

**Nothing is cut when a frame is scaled up.** A clip that shrinks is scaled up about its feet,
and when the feet also rose in the frame (a back walk going away toward the horizon) the crown
lands above the frame. Every frame is first widened by the room the furthest one reaches past
each edge (`gait_fallback.undo_padding`, the same for every frame; `padding_ltrb` in the
report), so no part is lost. A frame that needs no room keeps its size and bytes. The gait
fallback's own scale-back (below) widens the same way. The foot point is a least-squares line
with exactly rounded sums (`sprite_gen.util.lsq`, as are the fallback's fitted heights), the same to
the last bit on every machine. With np.polyfit, whose last bit differs between LAPACK builds, feet
that stand on the frame's bottom edge read a hair past it or short of it, and whether the frames
got 2 px of room below — and so the frames the cut is searched on — turned on that bit.

Gates, all fail-loud: no period (profile flat, below the recorded `periodicity_min`), loop seam ratio
above `--seam-max` (2.0), GIF/WebP re-opened and checked (frame count, `loop=0`,
transparent corners, no RGB under alpha 0 in the WebP).

A walk or run loop then has its jump frames repaired before the strip is built: a frame that
breaks a step 1.4× the loop's median (whole body, or the hair behind it) is replaced by RIFE's
frame between its two neighbours, at most three and never two side by side (`--repair auto`,
the default; `--repair off` cuts as filmed). The report's `jump_repair` names the frames. RIFE
is installed once with `sprite-gen rife install`; without it a loop that needs a frame is cut as
filmed with a warning (`--repair on` fails instead). See [loop repair](loop-repair.md). The repaired loop's jolt index and the head's frame-to-frame
moves are reported (`jolt`); beyond the reference bounds that is a warning line, and a gate
(`video-loop: loop jolts — …`) only when `--jolt-max` / `--head-step-max` are passed.

**Held drawings: recorded on every loop.** A video model may film a walk at 12 drawings a second
inside a 24 fps clip, each drawing shown for two frames ("on twos", or three: "on threes"). The
report's `drawings` says so, read over the whole clip as keyed, before any cut
(`sprite_gen/video/held.py`): `hold` (1 every frame a drawing, 2 or 3; `null` with a `why` for a
clip too short or still), `drawings` and `drawings_per_second`, `held_steps`, and `contrast` per
hold — the held steps over the change steps, the evidence. `cycle_drawings` is the same reading over
the cut alone, on the clip's steps from the cut's first frame into the frame after its last (`start`,
`length`, `steps`; `null` with a `why` under 12 steps, one window). `strip.json` carries both
(`drawings`: `hold`, `drawings_per_second`, `contrast`, `frames`; `cycle_drawings`: `start`,
`length`, `steps`, `hold`, `drawings_per_second`, `contrast`, `why`) for `video-cycle-align`, which
reads the cut's: a clip may hold its drawings for only part of its length. The second frame of a pair is rarely a
byte copy: the model redraws it a little, so its step is small, not zero, and a rule that calls a
step held under a fraction of the median step misses it (with half the steps held the median falls
between the two kinds). So the judgement reads the rhythm: for a hold of 2 or 3, in windows of 12
steps, the phase whose every second (or third) step is the change, its held steps' median over its
change steps' median; under 0.3 the clip is held, and each step is then held when it is under the
geometric midpoint of the two kinds' means. A motion filmed every frame has no such rhythm — its
steps swell and shrink with the stride over a dozen frames — and a clip whose steps alternate long
and short but move every frame reads above it; so does a frame repeated now and then (a 20 fps
clip carried at 24). Nothing is refused here: played at its own rate a held loop is ordinary
limited animation. It matters when `video-cycle-align` stretches it ([loop repair](loop-repair.md)
section 4, "Held drawings").

### One-shot actions — `--cycle auto|periodic|one-shot`

A video model asked to jump "over and over" sometimes jumps once and stands for the
rest of the clip. That is not a period, and the periodicity gate says so. For states
that *are* single actions by nature (`jump`, `attack`, unknown states; never `walk`,
`run`, `idle`) `auto` then runs a second, different detector instead of failing: the
detector finds close endpoint poses enclosing an excursion. The endpoints must differ
by at most a quarter of the departure, with two observed rest frames on either side
of at least four active frames. A component touching the clip's start or end is
rejected, never repaired with padding. Departure must span at least three ordinary
playback steps to reject incoherent jitter. Among candidates containing at least
95 % of the largest departure, the shortest cut wins (then lower seam ratio).

Acceptance uses either peak contrast above the distance-profile median (3 MADs) or
departure from the endpoint pair relative to their mean subject mass (0.4). Unlike a
global medoid, the pair can identify ready poses even when a held strike occupies
most of the clip. `cycle.excursion_rule`, `return_pair`, `excursion_over_step` and
`return_distance_over_departure` expose that evidence. These pixel measurements do
not identify an anatomical ready pose or guarantee character consistency.

The failover is explicit: `cycle.kind = "one-shot"` and `periodic_attempt` preserve
both decisions. The strip sidecar carries `kind` and `loop: false`, so scene loaders
play it once on a trigger. GIF/WebP previews still loop, and the same seam and
animation gates apply. `--cycle periodic` refuses a weak periodic candidate;
`--cycle one-shot` forces return detection. Stationary clips, jitter and actions
without an observed return fail loud. Selection and seam refusals write the requested
report before exiting: `status = "failed"`, error, window, candidate start/length,
seam numerator/denominator and repetition/return evidence. A walk or run refused by the
`--anchor motion-auto` search names the windows it measured and refused, best first
([loop review](loop-review.md), "The gait fallback"). Undefined ratios are JSON
`null`; success records `status = "passed"`. `--cycle fixed --start N --length L` skips detection and cuts exactly
those frames — for a clip that holds too few repeats for the periodicity gate but whose
cycle is known (the 2026-09-09 reel jump: 2.3 hops in 145 frames). It is an explicit
instruction, not a failover: the report says `kind = "fixed"`, and the seam gate still
applies.

`--cycle pinned` is for a clip pinned to end on its first frame (`video --last-frame` set to
the start image). The cycle is every frame but the last, which re-renders the first, so the
wrap plays like the step into that last frame. The gate is the pin, not the seam ratio: the
last frame has to land within `max(seam_max x the mean step, PIN_NOISE_MAX)` of the first, on
the analysis thumbnail. A near-still clip moves so little per frame that the ratio reads the
re-render noise of the pinned frame as a jump; a clip that ends in another pose still fails,
with its own message (`the pinned clip does not end on its first frame`). The report says
`kind = "pinned"` and records `pin_error` and `pin_tolerance`.

Outputs:

- `cycle/frame-NNN.png` — the cycle frames, RGB under alpha 0 scrubbed, detached specks
  below 1 % of the body erased.
- `<name>.strip.png` + `<name>.strip.json` — a horizontal strip (union-cropped with 8 transparent
  columns on each side even where the subject reaches the frame's side edge, **no bottom
  pad** so feet meet the floor, bottom-aligned, ≤ 64 cells **and ≤ 32 000 px wide** because
  Chrome refuses images near 32 767 px — the cap is on pixels, so a 650 px cell allows 49
  cells and the meta says `subsampled`; ≤ 520 px tall) with `frames · w · h · body_h · delay_ms ·
  cycle_frames · cycle_seconds`. `body_h` is the **standing height** — the tallest frame whose feet touch the floor
  (a median over the cycle undercounts a jump, whose crouch and airborne frames dominate,
  and then over-scales it by ~22 %, 2026-09-09). Scale a jump strip — whose cells include
  air room — by `body_h`, not `h`, and it reads the same size as a walk strip;
  `--body-height N` does that scaling in the pipeline so every state comes out at the same
  character size (`--strip-height` stays the cap). With a target, the standing height is
  measured on the **clip's first frame** instead — the base still's pose, which every clip
  starts from — because the tallest grounded frame of an attack is its windup with the weapon
  overhead, and scaling that to N shrank the character against its walk. A video model may
  reframe the subject after the first frame — a lead-in (section 4), or less — and then the walk
  is filmed at another size than that pose: read on the first frame, filmed small, the standing
  height scaled the walk up, a lead-in's into the cell cap. So a walk or run cut for a
  `--body-height` has its size read against the first frame's (`gait_fallback.size_change`) on
  three lengths at once: the height, the mass (the square root of the summed coverage) and the
  breadth (the widest row of the upper half of the body), each the middle of the cut's frames
  over the first frame's. A pose moves them apart — a walk is shorter than its standing pose and
  broader, an item held up is taller and no heavier — and a reframing moves all three the same
  way, so the cut's change of size is the least of the three when all go one way, and none when
  they part. It is the cautious reading: a reframing smaller than what the pose itself moves one
  of the lengths by, the other way, stays a pose (a small shrink under a walk's broader body,
  for one). At 1 % or more (`SIZE_HOLD_MIN`) the standing height is read on the cut's own first
  frame (`body_ref: cut-first-frame`, and `body_ref_frame`, the clip frame it was read on). The
  report records the reading (`cut_size`: `change`, `height`, `mass`, `breadth`, `min`). The
  sidecar says which frame (`body_ref`: `first-frame`, `cut-first-frame` or
  `tallest-grounded`) and records `body_src_h` and `scale`. `delay_ms = cycle_seconds / frames`, so a
  24 fps clip yields 41.67 ms cells; render at 24 fps to keep one cell per frame
  (a 30 fps render of 24 fps cells is a 5:4 pulldown and judders).
- **Cells** are scaled with their coverage and their colour taken apart (`resize_cell`; a cell
  at its own size is copied as it is). Coverage keeps LANCZOS's edge, held between the least and
  the most coverage of the source pixels the colour mixes from, so nothing spills outside the
  silhouette and nothing opens inside it. Colour is a Hamming mix of premultiplied colour, a
  filter with no negative lobe, so every pixel's colour is a mix of the colours under it. Each
  channel therefore stays within the colours under it, but the key hue's excess (green:
  `G - max(R, B)`) does not: a colour led by red mixed with one led by blue is led by neither as
  far, so a cell can carry a little more key hue than any source pixel under it. Every filter
  that mixes colours does this, in a cell and again wherever the cell is scaled later; the
  resample knows no key and caps nothing (`tests/video/test_strip_resample.py`). The one
  operation that compares a cell with the key bar, source restoration, handles it itself
  ([loop-comparison.md](loop-comparison.md#what-a-restored-cell-is)). The
  strip used LANCZOS over premultiplied RGBA before, which weighs the colours across an edge
  against each other and divides by the edge's low coverage: on a keyed frame, whose edge holds
  a light rim and ink with a little of the key's green, it drew a lighter rim and greener ink
  than the frame had. On the synthetic outlined figure in `tests/video/test_strip_resample.py`
  (a light rim, green-tinted ink, strands 0.5-1.4 px wide), LANCZOS left 1,462 pixels at 0.97x
  and 1,750 at 1.03x with a colour none of the source pixels under them had; the split leaves
  none, and the strands keep LANCZOS's coverage. Colour is no longer sharpened, so a 1 px line
  inside the body comes out a little softer than LANCZOS drew it when a cell is enlarged. The
  GIF and WebP are cut from these cells. The function lives in `sprite_gen/util/resample.py`
  and is the resample of every place that scaled a keyed picture with LANCZOS: a row frame fitted to its cell and the twin
  beside a pixel-unfake frame (`extract`), a sliced sheet's figures (`slice-sheet`) and a
  scene's layers (`scene-render`) go through it too. Its twin for an affine map,
  `transform_cell`, does the same for the places that moved, scaled, rotated or sheared a keyed
  picture with BICUBIC: the gait fallback's scaled-back frames (`video-loop`, "The gait
  fallback" in [loop-review.md](loop-review.md)) and a curated transform on a smooth row
  (`curation.apply_transform`). Coverage keeps BICUBIC's edge, held to the coverage of the
  2 x 2 source pixels the colour mixes from, and colour is a BILINEAR mix of premultiplied
  colour. BICUBIC has the same negative lobe, smaller: on the synthetic figure it left 1,088 to
  1,864 pixels with a colour none of the four source pixels under them had when a frame was
  scaled back by 0.84x to 1.19x, and 840 to 2,102 for a half-pixel move, a 10° rotation, a
  0.75x shrink, a shear and a 1.3x grow with a 15° rotation; `transform_cell` leaves none. The
  same cost applies: colour is not sharpened. A pixel row's curated transform samples NEAREST
  and is untouched. The scene GIF's ffmpeg downscale stays LANCZOS: it scales opaque composited
  frames, where nothing is divided by a low coverage.
- `<name>.gif` — `n_out` frames evenly across the cycle, 1-bit alpha, disposal 2, `loop=0`.
  `n_out` is a **playback density, not a fixed count**: `round(cycle_seconds × --gif-fps)`
  (default 24 fps = the source rate, so every cycle frame is kept; floor 4, never more than
  the cycle holds). A fixed 12 made a 2.5 s jump hold each frame 210 ms while a 1.1 s walk
  held 90 ms, and even an even 12 fps read sluggish on a jump (2026-09-09). `--n-out` and
  `--gif-fps` still override; `--strip-height` caps the cell/strip/GIF height. Scaling up
  requires an explicit `--body-height` target and still respects that cap.
- `<name>.webp` — same frames, lossless, `img2webp -exact` (Pillow's animated WebP writer
  does not pass `exact` and rewrites RGB under transparent pixels).

## 5. Set — the batch

`sprite-gen video-set --base side=side.png --base front=front.png --states idle,walk,run,jump,attack --out-dir set/`
runs canvas → video → frames → loop for every (direction, state). The xAI team quota
is **2 requests per second** (five parallel starts produced two HTTP 429s): starts are
staggered (`--start-gap 2`) and a 429 gets a bounded, logged retry (15 s, 30 s). Clip length
is each state's own default (3 s, attack 2 s) unless `--duration` sets one for all, and an
attack or idle clip is pinned to end on its canvas and cut whole (`--cycle pinned`). States use differently shaped canvases (square, tall, wide),
so the same character films at different pixel heights; `--body-height N` gives every state's loop the
same standing-height target and keeps the character one size across the set. At a low
`--resolution`, `--fit tight` frames every item without room so that target is reached by
scaling down rather than up, and lets the subject reach the frame edge. Items
are idempotent (an existing clip is reused unless `--force`); one failure stops only
its item and is listed in `table.md` with its stage and error. Exit code is non-zero
when any item failed.

A front or back walk films from its base redrawn mid-step (`--walk-start redraw`, default; one
image generation each, `--still-provider` picks the provider; see §2). After the loops are cut,
every walk or run filmed in two or more directions is given one cycle
length: each loop is resampled to the set's median length (only the frames that fall between two
source frames are made, by RIFE, and one that melted takes the nearer source frame instead (`--align-between
auto`, the default; `rife` keeps every made frame, `nearest` makes none))
and turned to start as the same own foot lands in every view — read off the legs, never off an
ear or a hat's point, and the foot told apart by the item's own view and facing (`--align-cycles
auto`, the default; `off` keeps each loop's own length). The same step stands alone as
`sprite-gen video-cycle-align --loop-dir … --view …`. `set.report.json` carries `cycle_align`
per state (with `start_foot` and `start_foot_source` per item, and the loops whose foot nobody named
under `unnamed_feet`, told back with `--align-foot <item>=left|right`), a failed alignment is listed as `cycle-align:<state>`,
and a made frame with a smear, one that melted and was replaced, or a loop whose foot could not be named is a line under
`warnings`. A held loop stretched past what an interpolator bridges is named for a new take: the
state's `retake` (reason `held-drawings`, with its numbers; per item under `cycle_align.retake`) and
a warning line ([loop repair](loop-repair.md) section 4, "Held drawings"). Two cases skip the alignment with a warning instead of failing it (`applied: false`,
`reason`, and a line under the report's `warnings`): no RIFE (`rife-not-installed`), and a loop
that may hold more than one cycle (`cycle-suspects`, the loops under `suspects`; count them, then
`video-cycle-align --cycles <loop>=<k>`). See [loop repair](loop-repair.md) section 4.

## 6. Follow-through — `video-follow`

A clip model draws a walk's body moving, but a soft part hanging off it (a chest, a belly, a
pouch on a strap) mostly moves with the body as one piece, even when the prompt asks it to
bounce. `video-follow` puts that follow-through back on a cut loop:

```bash
sprite-gen video-follow --loop-dir set/front-walk/loop --region 136,164,60,50 [--region …] \
  [--gain 2.5] [--on-fold refuse|lower] [--freq 2.4] [--zeta 0.6] [--board follow.png] \
  [--read-on <sha256>] [--stretch-floor D]
```

- **The region** is an ellipse over the part in the strip's first cell, in cell pixels
  (`cx,cy,rx,ry`; repeatable). It is carried with the body's bob from cell to cell. Somebody has
  to say where it is — the engine does not find it: look at the first cell (or ask a vision model
  for the four numbers once per direction; a mirrored direction takes the mirror's region).
- **The motion** is the body's own: how far each cell's body lies from the first cell's, up and
  down and side to side. The silhouette (alpha ≥ 128) is worn down: worn by r, a pixel stays when
  the square of side 2·r + 1 around it is all body. Its depth is the most the first cell can be
  worn down by and still keep a pixel, plus one; worn by a quarter of that, what swings on the
  body and is thinner than it (a tail, a ponytail, an ear, a sword or a rod held up, the legs and
  arms) is gone and the head and torso are left. Each cell is laid where its silhouette, worn by
  every whole number of pixels from a quarter to half of that depth, overlaps the first cell's,
  worn alike, most — the overlaps of every level counted in whole pixels and summed, a tie going
  to the smaller move. A pixel counts once for each level it is still in, so the deepest of the
  body counts most and a part that comes and goes cannot outweigh it, while a torso that is
  shallower in one cell (an arm swung away from it, long hair lifted off it) still counts at its
  shallower levels. A part as thick as the body outlasts the wearing — ears as wide as the head —
  and a cell that has it elsewhere than the first cell does, laid on the first cell alone, lies
  ears on ears with its body tens of pixels off. So the cells are laid twice. Laid on the first
  cell, each is moved back by its lay and the cells are counted: per level and pixel, how many
  more of them are in that level there than are not (none, where no more are). The head and
  torso are in every cell at one place and count the whole number of cells; what swings — ears
  up in some cells and flopped onto the face in others, a leg forward and back — is at any one
  place in half of the cells or fewer and counts nothing. Each cell is then laid on that count
  as it was on the first cell, and its lay less the first cell's own is the motion. A thick part
  that keeps one place in most of the cells is body to this read, which sees outlines only. The
  top of the body was read before: whatever came to the top was the
  motion, and a tail tip, a flopping ear or a sword raised over the head made it jump by the
  part's whole swing from one cell to the next. A cell with nothing in it a quarter as deep as the first
  cell's body is refused, and so is a strip with no part of the body at one place in more than
  half of its cells. The part is a damped mass on the body — its offset x from
  where the body carries it answers x'' + 2ζωx' + ω²x = −body'' — solved in the loop's periodic
  steady state per harmonic of the cycle (the first six), so it lags the bob and settles, with no
  kick at a foot strike, and the last frame leads into the first. `--freq` (2.4 Hz) and `--zeta`
  (0.6) set the part; `--gain` (2.5) scales the physical answer and nothing else. On a front
  walk with a 12 px bob in a 600 px body that is a move of about ±5 px; `--gain 1` is the mass as
  measured, `0` gives back the strip as it was.
- **Only the region moves.** Inside it every pixel is moved by the offset times a weight that is 1
  at the centre and 0 at the rim (cos²), sampled as premultiplied bilinear colour; outside it no
  pixel changes. A move so large that the weight's slope folds the picture over
  (offset × π / (2 · radius) ≥ 1) is refused: lower `--gain` or give the region larger radii.
- **`--on-fold lower`** lowers the gain for you instead of refusing, region by region. The move
  is the gain times the move at gain 1, so the gain at which a region folds is
  2 · radius / (π · move at gain 1), and a region that folds at `--gain` takes the largest gain
  under that, in steps of 0.01; a region that does not fold keeps `--gain`. Every part hangs on
  the same body and answers the same motion, but how far a part can move before it folds is its
  own size, so a small part (an ear) does not hold a large one (a chest) back. No region goes under 1, the
  mass as measured: one that would have to is **held** — it does not move at all, so a
  follow-through is never quietly weaker than the motion it answers — and named, on stderr and in
  the record. The strip is refused only when every region is held (the message names the largest
  region and the largest gain that would not fold it). Where regions of different gains overlap,
  the larger move wins; the move there is nowhere steeper than either region's own, so nothing
  folds. A strip whose regions all take one gain — one region, or regions that fold alike, or
  none that folds — is written as one gain for the strip writes it, byte for byte, and its `gain` is
  then the `--gain` that gives this strip by itself. The default stays `refuse`, with the same
  message as 2.24.0; a strip that does not fold is the same under either.
- **What it writes**: the strip, GIF and WebP over the loop's own (both animations re-opened and
  checked as `video-loop` checks them), and `follow` in `<name>.strip.json` (the regions, the
  settings, the body's height in the first cell `body_px`, the first and last level it was worn
  down to `body_worn_px`, the body's bob `body_bob_px` (side to side, up and down), `dx_px`/`dy_px` per cell, `reach_px`; `gain` is the gain used and
  `gain_requested` the one asked for, and `fold` says whether it was lowered, the move asked for,
  the move at gain 1, and per region its smaller radius, how near the used move is to folding it
  (`ratio`, under 1) and the gain at which it folds). Where the regions took gains of their own,
  each region's entry also says the gain it moved by (`gain`, 0 when held) and whether it was
  held (`held`); the strip's `gain` is then the largest of them, `dx_px`/`dy_px` and `reach_px`
  are that gain's (a region moves by its own gain over that one times them), and `fold.ratio` is
  the nearest any region came to folding. A lowered gain is also named on stderr — per region,
  with the `--region` it was given, where they differ — and so is a held region; the printed
  summary then carries `region_gains`.
  All four are written and checked beside the loop first and moved over its files only once the
  record is written too, so a run that fails on the way — an animation that fails its check, a
  record that does not serialise — leaves the strip, GIF, WebP and record as they were.
  `cycle/` is left as cut. The strip as it was is kept as `follow.source.png`; running
  `video-follow` again reads from it, so a second run never moves a moved strip. `--board` writes the cells before and after where the part
  sits lowest and highest, on white.
- **Order**: after `video-cycle-align`. An alignment rebuilds the strip from the cut, removes
  `follow.source.png` and the `follow` record, and says so (`follow_cleared` in its loop row); a
  new cut with `video-loop` removes them too. Run `video-follow` again after either — with
  `--read-on` where the regions were read on the cut (below).
- A one-shot (`kind: one-shot`) is refused: the follow-through is a loop's steady state.

#### Regions read on the cut, followed on the aligned loop — `--read-on`

The regions are where somebody looked: in the first cell of the strip they were read on. An
alignment turns a loop to start on a foot strike, so its first cell is another; given the same
regions, `video-follow` reads the motion again from that cell 0 and the regions land on another
place of the body, and a stretched loop moves them by a motion read anew. `--read-on <sha256>`
names the strip they were read on — its file's sha256, as cut, before any follow-through (64
lowercase hexadecimal digits; anything else is refused before anything is read):

- **`same`**: it is this loop's strip as cut (`follow.source.png` once a follow-through moved it).
  The follow-through is the one without `--read-on`, byte for byte, and the record says so.
- **`transported`**: it is the cut this loop was aligned from. `video-cycle-align` records that cut
  the first time it aligns the loop (`cycle_align.origin`: its sha256, cell size, scale and crop
  origin, each cell's pixels by sha256, and the body's motion as `video-follow` reads it there),
  keeps it on every later alignment, and records where each cell of the new strip is from
  (`cycle_align.cells_from`: `{"source": i}`, a frame of `cycle.source/`, or `{"between": [i, j],
  "t": t}`, a frame made between two). Each cell is carried by the cut's reading of the cut's cell it
  is, the regions stay where they were read (the cut's cell 0), and the damped mass is solved on
  this loop's order, length and frame time: a turned loop gives the cut's follow-through cell for
  cell, a stretched one gives the new cycle's offsets on the cut's motion, duplicates and all.
- **`uncertain`**, refused: the cut is the one, but a cell was made between two of its frames
  (RIFE, or any `between`), or a cell is not the cut's cell pixel for pixel — a crop or a scale
  changed and nothing records how, or a cell was drawn again since. A made cell has no reading of
  its own, and none is taken from the nearer frame by rounding. A cut made before its strip
  recorded its crop (`source_rect`) says nothing of where it was cropped; there the cells' pixels
  decide alone, as they do for every cell anyway.
- **`no-match`**, refused: neither — another clip, another cut, a strip nothing here was cut or
  aligned from. Locate the regions again on this strip.

A refusal writes nothing but the strip as cut kept beside the loop, as on any refusal. Without
`--read-on` nothing of this is read or written, and the record holds no new key. With it the record
carries `read_on` (`sha256`, `relation`; for `transported` also `policy` and `cut_cells`, the cut's
cell each cell is) and `carry_basis`. The printed summary carries `read_on` (the relation).

#### A least area each move leaves — `--stretch-floor D`

The fold rule keeps a region from folding, and so lets the region's steepest pixel take nearly
nothing of the source: a line there is drawn across many pixels. `--stretch-floor D` (0 to under
1) asks every region's move to leave each pixel at least D of its area. The weight's steepest
slope is π/2 over each radius, so a move (ox, oy) leaves 1 − (π/2)·hypot(ox/rx, oy/ry) of a
pixel's area, read by the way the region moves (the fold rule reads the smaller radius whichever
way it moves). Each region takes the largest gain, in steps of 0.01, that keeps D in every cell
— **and never one above the fold rule's**: where the direction would allow more (a region moving
along its long axis) the fold rule's gain stands, so a floor only ever lowers. Under it the fold
rule's handling stands: a region the floor takes under 1 is held and named, the strip is refused
when every region is, and `--on-fold refuse` refuses a move under the floor. There is no default:
without `--stretch-floor` the fold rule alone decides, byte for byte. A floor the move already
keeps draws the same strip, GIF and WebP. The record carries `stretch` (`policy`, `floor`, and per
region `per_gain` — the area a unit of gain takes at its worst cell, `least_cell` —, `rule_gain`,
`floor_gain`, `gain`, `held`, `least_area`, `under_measured`) and `carry_basis`; a lowered or held
region is named on stderr with what lowered it, the fold or the floor. On a loop aligned from the
cut, give `--read-on` too: the floor is then read on the cut's motion, so a turned loop is lowered,
held or refused as the cut is.

The floor holds the stretch, and only the stretch: what a move also does to a line — it tilts it,
and where two regions overlap it bends it at their seam — lowers only as the gain lowers.
`video-follow-inspect` writes all three (below).

What carries a region into a cell is the lay of the body's outline (`carry_basis: {"reading":
"outline-lay", "region": "unverified"}`). Two strips whose alpha is the same in every cell are
carried alike, whatever moved inside the outline; whose pixels are inside a carried ellipse is not
read, and nothing here moves a region on a guess of it.

### Inspecting a follow-through — `video-follow-inspect`

The ellipse is given in the first cell, where somebody looked. Into every other cell it is carried
by the body's motion, and there nobody looked: an arm that swings across a chest is inside the
chest's ellipse in those cells and moves with it, and so does a sleeve of the chest's own colour,
which no count of colours finds. The engine does not read what a pixel belongs to.
`video-follow-inspect` writes what somebody needs in order to look, and moves nothing:

```bash
sprite-gen video-follow-inspect --loop-dir set/front-run/loop --region 136,164,60,50 [--region …] \
  [--gain 2.5] [--on-fold refuse|lower] [--freq 2.4] [--zeta 0.6] [--read-on <sha256>] [--stretch-floor D] \
  --out-dir inspect/front-run [--scale 2] [--columns 8]
```

- **Asked as `video-follow` is, answered by the same code.** It takes `video-follow`'s arguments
  (all but `--board`), reads the strip as cut — `follow.source.png` where a follow-through was
  already written, the strip itself where none was — and solves it where `video-follow` does
  (`follow.read_request`, `follow.solve`): the carry, each region's gain, the offsets and the
  moved cells are computed in one place, and both commands read them there. What `video-follow`
  refuses it refuses in the same words under its own name, and then writes nothing.
- **The loop directory is read and never written**: not the strip, not its meta, and not the
  `follow.source.png` that `video-follow` keeps on its first run. An `--out-dir` at or under the
  loop directory is refused. Run before `video-follow`, it changes nothing `video-follow` writes.
- **What is inside an ellipse is not read.** Every region is written `ownership: "unknown"` in
  every cell, the first included, from its centre to its rim; no cell is marked or held on a
  guess. `solid_px` counts the body's silhouette under the ellipse (alpha ≥ 128), not whose
  pixels they are. Saying which cells hold the part alone is for a person or a model that looks
  at the board of the cells as cut, and for a second reader apart from the first.
  `video-follow` takes no such list: it moves every cell.
- **`follow-inspect.json`** (`kind: video-follow-inspection`, `schema_version: 2`):

  | key | what it holds |
  |---|---|
  | `input_id` | one sha256 over what the answer is read from: the schema version, the strip's sha256, the meta's `cut_sha256`, the regions, the settings, the policy and the engine's version |
  | `inputs.strip` | `file`, `sha256`, `bytes`: the strip as cut that was read |
  | `inputs.meta` | `file`, `sha256`, `bytes`: `<name>.strip.json` as read; `cut_sha256`: its sha256 without the `follow` record `video-follow` adds (keys sorted, no spaces); `follow_recorded` |
  | `inputs.cut` | `frames`, `w`, `h`, `delay_ms`, as read from the meta |
  | `inputs.regions`, `inputs.settings` | the ellipses in the order given; `gain`, `on_fold`, `freq_hz`, `zeta`, `read_on` and `stretch_floor` (null where not asked) |
  | `inputs.policy`, `inputs.engine` | the way the motion is read and a region moved, by name (`follow.POLICY`) with the constants it is fixed by, and the ways a reading is carried (`transport`) and a floor kept (`stretch_floor`), by name; the engine's `name` and `version` |
  | `follow` | the follow-through as `video-follow` records it in the strip's meta, without `gif` and `webp` |
  | `carry_basis` | `{"reading": "outline-lay", "region": "unverified"}`: what carries a region, and that whose pixels it holds is not read |
  | `regions[i]` | `index`, `ellipse` as given, `radius_px`, `gain` (0 when held), `held`, `gain_limit`; over all cells the largest `reach_px`, `fold_ratio`, `bend` and `bend_deg`, the least `jacobian_min`, the sum of `changed_px`; `ring`, its colour on the boards |
  | `seams` | per pair of moving regions that meet in some cell (`regions`: [i, j]), the largest seam: `jump`, `deg`, the `cell` and the point `at` (x, y) |
  | `cells[k]` | `index`, `carry_px` (across, down), `changed_px`, `jacobian_min`, `seams` (as above, per pair that meets in that cell), `box` (x0, y0, x1, y1: the cell's picture on both boards) and `regions[i]` |
  | `cells[k].regions[i]` | `ellipse`: the region carried (centre plus `carry_px`, the radii as given); `offset_px` (across, down) and `reach_px`: the region's own move in that cell, 0 when held; `fold_ratio`; `jacobian_min` and `jacobian_min_at` (x, y); `bend` and `bend_deg`; `ellipse_px`, `solid_px`; `changed_px`; `ownership` |
  | `boards` | `scale`, `columns`, `size`, `ground`; `source` and `moved`, each `file`, `sha256` (the PNG file) and `pixels_sha256` (its pixels) |

- **`carry_px`** is how far the cell's body lies from the first cell's, in whole pixels: what
  every region's ellipse is carried by in that cell, and what `video-follow` carries it by. It
  was written nowhere before; from the record's rounded offsets it cannot be worked back for a
  strip of more than thirteen cells (the answer keeps six harmonics of the cycle).
- **`jacobian_min`** is the least area of the source that one pixel of the moved cell takes, over
  the pixels of the region's ellipse: 1 where nothing moves, 0 where the picture stops — one line
  of the source drawn across many — and under 0 where it would run backwards, which is what the
  fold check refuses. It is read on the pixels, between each pixel's neighbours on either side
  across and down, so for a region a few pixels across it reads a little over the steepest point
  of the weight. A region that `--on-fold lower` lowered moves at the largest gain that does not
  fold it, so in the cell where it moves most it is drawn nearly stopped: not folded, and
  stretched. `jacobian_min_at` is the pixel.
  Where regions overlap the move is the larger one's, so a region's numbers there are what is
  drawn in its ellipse, not its own move alone. `changed_px` counts the pixels whose RGBA the
  move changes; outside every carried ellipse it changes none.
- **A move does three things to the picture, and each is written apart.** It stretches it — the
  least area, `jacobian_min` (D), the one `--stretch-floor` holds. It tilts a line: the move's
  steepest slope, |offset|·π/(2·smaller radius), `bend` (G), with `bend_deg` = atan G — the same
  number as `fold_ratio`, named for what it does: at 1 a line tilts 45°, and a region `--on-fold
  lower` lowered moves just under it. A round region moving along a radius has G = 1 − D, but a
  region moving along its long axis keeps D high while G stays near 1, so neither stands for the
  other. And where two regions overlap, `video-follow` moves each pixel by the larger of their
  weighted moves (by the larger weight where they move by one gain), so across the curve where the
  two are equal the move's gradient jumps from one region's to the other's and a straight line
  bends there: `seams`, the largest jump on that curve — the norm of the difference of the two
  gradients, each the offset times the weight's gradient — and its angle `deg`. It is looked for
  along rows a quarter pixel apart, between points inside both ellipses where the larger move
  changes hands, and found there by halving. A held region has no seam. Quality is read from these
  three, not from `fold.ratio`; a floor lowers the bend and the seam only as it lowers the gain.
- **The record names what it was read from.** It holds no path, no time and nothing of the
  machine: the same loop asked the same way writes the same record, byte for byte, anywhere.
  `input_id` is the same before and after `video-follow` is run on the loop, and another after a
  new cut, an alignment, a changed region or setting (`read_on` and `stretch_floor` among them), or
  another engine version — what somebody
  saw on one cut's boards is not evidence for another cut. `--scale` and `--columns` change the
  boards and their sha256, not `input_id` and not a number of the record but the `box`es.
  `pixels_sha256` is over a board's pixels (RGB, row by row), so another PNG writer gives the same
  one; `sha256` is the file's. The offsets, ratios and areas are rounded to 4 places.
- **The boards** hold every cell of the strip in order, `--columns` to a row, each under its
  number (the first cell is 0), over a flat grey, `--scale` board pixels to a cell pixel. Each
  region's carried ellipse is ringed in its colour, dashed with black. The ring is on the board
  pixels just outside the ellipse — the first the move does not touch — so inside an ellipse the
  cell shows whole (but for another region's ring crossing it), and at `--scale 2` or more no
  cell pixel is covered whole. `follow-inspect.source.png` has the cells as cut;
  `follow-inspect.moved.png` has them as `video-follow` would write them, laid and ringed alike.
  They are two files so that whoever says what is inside an ellipse can be given the cells as cut
  alone, without the answer beside them.

## 7. One sheet for a set — `video-set-sheet`

Each direction of a set is cut on its own, and a strip is the union of its own cycle's bodies: a side
walk's cell is wider than a front one's, a direction with a long ear taller. Laid side by side as they
are — a 3×3 board, a sheet for a game engine — the directions stand at different heights and the board
shows it as it plays. `video-set-sheet` lays them on one sheet that a board or an engine can place as it
is:

```bash
sprite-gen video-set-sheet --loop-dir S/loop --view front --loop-dir W/loop --view side@left \
  --loop-dir N/loop --view back --loop-dir E/loop --view side@right --center base.png --out-dir walk-set --name walk
```

- **Rows.** One per direction given, in the compass order **S, SW, W, NW, N, NE, E, SE** — clockwise from
  the viewer, the order a character turns in — whatever order the loops are given in. `--view` names the
  row, as `video-cycle-align` takes it: `front` S, `back` N, `side@right` E, `side@left` W,
  `front_diagonal@right` SE, `front_diagonal@left` SW, `back_diagonal@right` NE, `back_diagonal@left` NW
  (`@left` faces the screen's left). A strip turned over to face the other way is a direction like any
  other. A direction given twice, a front or back view with a side (its picture turned over is not a
  compass direction) and a count of `--view` other than the loops' are refused before anything is written.
- **One cell.** Each strip is laid on its pivot — the one its sidecar declares (`anchor`: the foot line
  under `video-loop --anchor feet`), else its bottom centre, where `video-loop` stands the feet — and the
  cell is the union of every pixel of every cell of every direction (and of the centre picture), centred
  on that pivot: every cell's pivot is its bottom centre (`anchor` in the record), and no pixel is dropped.
- **One ground line.** `baseline_y` is the lowest body pixel of any cell of any direction — body as
  `video-loop` reads one, alpha 8 and over: that pixel is row `baseline_y − 1` of its cell. A fainter
  pixel (a soft shadow under the feet) is no body: it is kept, and the cell reaches below the line to hold
  it, so `cell_h` is `baseline_y` or more. A direction whose strip leaves room under its feet stands that
  much above the line, as cut: each row's `body_bottom_y` says where its lowest body pixel ends.
- **No row scaled.** Every cell of the sheet is the strip's own cell, pixel for pixel. One size across
  the set is `video-loop --body-height`'s; the sheet never sizes a direction on its own. A board that
  shows the sheet smaller scales it as one, and stands each cell's `baseline_y` on its ground.
- **The centre picture** (`--center`): a transparent still — the set's base, the picture a board shows in
  its middle. It is scaled to the rows' standing height (the median of their `body_h`, against the
  still's own standing height read as `video-loop` reads one: its box at alpha 8 and over), its body
  stood on the ground line and centred on the pivot, alone in a cell of the sheet's size
  (`<name>.center.png`); a fainter shadow under it is kept below the line. A
  still with no transparent background is refused (cut it off first: `sprite-gen cutout`), and so are
  rows without `body_h` beside one.
- **Lengths.** Loops of different lengths, in frames or in frame time, are laid all the same, one frame
  a column and the shorter rows' last cells empty; the record says `same_length: false` and a warning line
  names each direction's length. `video-cycle-align` gives a set one cycle length.

The record, `<name>.sheet.json` (kind `sprite-gen-video-set-sheet`, schema 1): `sheet`, `order` (the
rows' codes), `columns`, `cell_w`, `cell_h`, `baseline_y`, `anchor` (`[cell_w / 2, baseline_y]`),
`same_length`; `rows`, one per row with its `row`, `code`, `view`, `frames`, `delay_ms`, the strip's
`name`, the loop directory as `input`, the strip's `strip_sha256`, `body_h` and `body_bottom_y`; and
`center` — null, or the picture's `png`, `input`, `sha256`, its own `standing_h`, the rows' `body_h` it
was scaled to, the `scale` and its `box` in the cell. The loop directories are only read: no strip,
frame or frame time is changed.

## What the rules were measured on

Every threshold above (the 15 % period tolerance, the 2.0 seam gate, the 0.15
periodicity floor, the state windows, the tall/wide canvas rooms, the 2 s stagger) was
set on one hand-run set of 15 loops (3 directions × 5 states, one SD biped) on
2026-09-08 and every loop of that set passed the gates as written. On 2026-09-09 the
same rules were run against two deliberately different bodies — a quadruped and a
legless blob, generated for the test — with idle, walk and jump each. Two rules turned
out to be *that biped's* rules and were generalized: the walk window floor (the blob's
bounce was faster than any gait) and the assumption that an action state repeats (the
quadruped jumped once). Everything else held unchanged, and the original biped set
still resolves to the same periods afterwards. The subjects are not in this repository;
the synthetic fixtures under `tests/video/` pin every rule named here.

## Related

- [docs/README.md](README.md) — documentation index

### One-shot length is the clip's own fact

The periodic window (`LoopProfile.min_frac` / `max_frac`) bounds *repeats*. A one-shot
(`--cycle one-shot`, or the `auto` failover for action states) has no repeat to bound:
the excursion is as long as the model performed it. `detect_one_shot` therefore no longer
refuses an excursion shorter than the periodic window's lower edge — only a degenerate
cut under `ONE_SHOT_MIN_LEN` (4 frames) or one longer than the clip is refused. A short
set-down, a nod, a flinch come back as the frames they are.

### `--anchor feet` — undo in-canvas drift

"Stays centered in the frame" is a request, not a guarantee: the model may walk the
subject across an in-place canvas, and the union crop that `build_strip` uses keeps that
drift inside every cell, so a runtime that places the strip by its cell box sees the body
slide back and forth once per cycle. `--anchor feet` (on `video-loop` and `video-set`)
removes that drift and nothing else. Drift is a slow translation and a gait is periodic,
so a straight line fitted to the body's centre (the mean x of every opaque pixel) across
the cycle carries the drift and not the step. Each cell is shifted by that line only,
so every cell stands on the same **mean** foot line — the mean x of the opaque pixels in
the lowest `FOOT_BAND` (8 %) of each frame's bbox, averaged over the cycle.

Do not pin each frame's own foot line. In an in-place walk one foot is lifted out of the
floor band every step, so the per-frame foot line jumps to the planted foot by about the
stride; pinning it makes a body that stood still lurch back and forth by that much. The
first version of this option did exactly that, and the synthetic lifting-leg walker in
`tests/video` pins the rule.

The strip meta gains `foot_anchor`, `drift_px` (the drift removed across the cycle, in
source pixels), `foot_sway_px` (how far the planted foot moves within the gait — kept, only
reported) and `foot_x` (the mean foot column inside every cell), plus the spec loader's
`anchor` as `[foot_x, h]` so a scene stands the sprite on its foot line; `video-set`'s
table row carries `drift_px`. The default stays `none`: existing strips do not change,
and `drift_px` / `foot_sway_px` are 0 when they were not measured.
