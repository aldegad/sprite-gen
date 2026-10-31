# Video → sprite pipeline (engine SSoT)

> Owns: Pipeline B engine contract: state canvas, keyed frames, true-period and one-shot cycles, strip/GIF/WebP, a single play's root motion and its air retimed, obstacle plate painted and erased (--prop-plate), the batch, a set's sheet, a walk or run's stop · Index: [docs/README.md](README.md)

One still becomes a whole motion set: the still is padded into the canvas a state
needs, Grok Imagine animates it in place, the clip is keyed frame by frame, and one
seamless cycle is cut out as a strip, a transparent GIF and a WebP — every stage
measured and reported, nothing recovered silently.

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
| `sprite-gen video-frames` | `sprite_gen/video/frames.py` | mp4 → `raw/`, `keyed/` RGBA frames (`keyed.refused/` when refused) + report |
| `sprite-gen video-loop` | `sprite_gen/video/loop.py` | keyed frames → `cycle/`, `<name>.strip.png` + `.strip.json`, `<name>.gif`, `<name>.webp` + report |
| `sprite-gen video-set` | `sprite_gen/video/batch.py` | bases × states → one folder per item, `set.report.json`, `table.md` |
| `sprite-gen video-cycle-align` | `sprite_gen/video/align.py` | the loop directories of one walk or run, one per direction → one cycle length, each loop turned to start on a foot strike, + report ([loop repair](loop-repair.md) section 4) |
| `sprite-gen video-sway` | `sprite_gen/video/sway.py` | a loop directory + an ellipse -> the strip, GIF and WebP with that region swaying with the body (old name `video-follow`) ([section 6](#6-the-sway-layer--video-sway)) |
| `sprite-gen video-sway-inspect` | `sprite_gen/video/sway_inspect.py` | a loop directory + the ellipses `video-sway` takes -> a record of where each is carried in every cell and what the move changes there, and two boards of every cell; the loop directory is only read ([section 6](#inspecting-a-sway--video-sway-inspect)) |
| `sprite-gen video-set-sheet` | `sprite_gen/video/set_sheet.py` | the loop directories of one set, each with its `--view` → one sheet: a row per direction in compass order, one cell size, one ground line, every row as cut, + a record and an optional centre picture; the loop directories are only read ([section 7](#7-one-sheet-for-a-set--video-set-sheet)) |
| `sprite-gen video-stop` | `sprite_gen/video/stop.py` | keyed frames + a walk or run loop → one-shot stop strip (`loop: false`, `hold_last: true`), GIF, WebP, report ([section 8](#8-a-walk-or-run-that-stops--video-stop)) |

## 1. Canvas — the input frame decides the output frame

Grok Imagine keeps the input image's framing and **ignores `aspect_ratio` on
image-to-video**. So the canvas
is a property of the motion state, owned by one table (`STATE_CANVAS`).

A still cropped
at the crown gets only the quarter — pass `--headroom 0.26` or more for an overhead swing.

`--shape tall|wide|square` overrides the row; `--headroom` / `--lead` / `--trail` tune the room
(`--trail` is the empty fraction of the width kept behind the subject, for a weapon drawn
back before the strike); `--facing left` mirrors the wide layout. A still whose corners are not one flat colour
is refused — a non-flat background cannot be extended without guessing.

**A walk or run from the side keeps room around its still (`GAIT_ROOM`).** Filmed from the side, the stride
reaches in front of the standing silhouette; behind it, the stride and a part that trails behind the body (a big
tail, a long coat) swing past it, in the picture's plane. When either meets the left or right edge, `video-frames` refuses the
clip ([section 3](#3-frames--extract-key-check-the-edges)). So the `walk` and `run` rows read the still's room
(`canvas.gait_room`) when the view is `side`. The room is the empty columns between the silhouette and the left and right edges of the square the row
makes of the still (`max(w, h)` wide, the still centred, so a still taller than it is wide gains room on either
side). It is counted on two sides: in front of the subject (the way it faces) and behind it, each as a fraction of
the silhouette's height. The silhouette is every pixel of the normalized still that is not the exact key
(`canvas.subject_box`; on a `--key white` base, every pixel more than the corner tolerance off the corner colour).
Each side keeps its `GAIT_ROOM`: 0.15 of the silhouette's height in front, for the stride, and 0.25 behind,
for the stride and whatever trails behind the body. A side that keeps less gains the whole pixels it lacks, and the square grows by the same number of rows, all of them above the still. So the frame stays
square and the still is never scaled. A still that leaves the room on both sides gets the canvas it got before, byte for byte.

The square is widened rather than moved to the 16:9 wide row: a walk is filmed at 1:1, and the wide row
(`GAIT_WIDE`, 0.28 in front and 0.20 behind) films a square still's subject at about two thirds of the height a
1:1 clip gives it. Only the side view is read (`ROOM_VIEWS`): a diagonal walk strides half into the picture, and
a front or back walk wholly into it. A run is read at the walk's bounds.

The report's `room` records the view, the silhouette box on the still, `front_px` / `behind_px` and their
fractions `front` / `behind`, `need` / `need_px` (each side's bound),
`short` (the sides under the bound, front first), `added_px`
(the columns each side gained) and `added_rows` (the rows the square gained above the still). A widened canvas's
`why` says why. `video-set` passes each item's view and copies
`room` into the item's `canvas` (with the `why` when the square was widened). `video-canvas` reads the room only
with `--view side`; without `--view`, the canvas is the state's row as before. `--shape square` keeps the
still's own square whatever its room, `--shape wide` frames a walk or run on `GAIT_WIDE` as before, and
`--fit tight` adds no room.

What the room takes from sprite-gen, and what is new:

| Part | Taken from | Why |
|---|---|---|
| The silhouette on the still | `canvas.subject_box`: the reading `--fit tight` makes of the subject's rows, widened to its columns (`subject_rows` became `subject_box`; tight reads its rows from it, byte for byte) | One reading of where the subject is on a still the canvas normalized |
| The background it is read on | `canvas.normalize_key`: the `cutout` matte's alpha-0 set repainted to the exact key, the pixels `video-frames` erases | The silhouette is what the clip's frames will keep |
| The square it is read in, and the placement | The square row of `pad_canvas` (`max(w, h)`, the still centred and standing on the bottom edge) | The room is counted in the frame the clip is made from |
| The view names | `handedness.VIEWS`, which `video-prompt --direction` and `video-set --base` take | One vocabulary for the view |
| The gate the room keeps the clip off | `video-frames`' edge-contact check (`frames.edge_contact`, `frames.classify_edge_contact`), unchanged | The canvas cannot see the clip; the gate still judges it |
| Written new: `gait_room`, `GAIT_ROOM`, `ROOM_VIEWS` | — | No engine function reads, before a clip exists, the room a motion needs around a still. `frames.extract.safe_area_fit` judges a finished frame's size against a sheet cell's safe area, a centred fit rather than the room on one side of a still. The edge check reads frames once the clip exists. |

**`--fit tight` adds no room.** It is the framing for a fixed body height at a low
clip resolution: the room above makes the subject a small part of the clip, and a
`--body-height` target then has to upscale it. A motion that leaves the frame is clipped: pair it
with `video-frames --allow-subject-edge-contact`.

**The canvas owns key normalization.** Image models paint "`#00FF00`" a little
differently every run and the video model reproduces
the input colour almost exactly. So when
the flat corners are a green/magenta key at *any* brightness (`--key auto`, the
default; `--key green|magenta` to insist, refused when the corners are not that
family), the still's background is repainted to the **exact declared key** and the
padding is that same pure key. A corner that survives the matte fails loud
(the still is not on a key the engine can cut). `--key white` keeps the old behaviour: no
chroma key, the padding is the corner colour.

### Prop plate: `--prop-plate`

A move over an obstacle (a vault, a jump over a low wall) needs something to clear. When asked in words for a wall in the background colour, a video model draws a black slab, and keying leaves that slab in the keyed frames. The obstacle is therefore painted into the still the clip is made from as a flat magenta plate, RGB (255, 0, 255), on the green key, which keying does not take and a later erase pass removes. `video-canvas --prop-plate` (off by default) paints this plate in front of the subject on the side it faces (`--facing`), standing on the subject's foot row (the plate's bottom row is the subject's bottom row). Sized from `canvas.subject_box` on the canvas whose background was normalized to the exact green key, its height is 0.49 of the subject height, its thickness is 0.20, and its near side is 0.84 of the subject height ahead of the subject's front. The flag requires a green key and `--report`. A run writes no canvas and refuses if the key is not green ("--prop-plate paints a magenta plate on a green key"), if `--report` is omitted, for state `effect` ("an effect keeps its source canvas; drop --prop-plate"), or if the plate falls past the canvas edge on the facing side ("frame the subject with room in front (--shape wide, --lead)"). The report records `prop_plate`: `box` (left, top, right, bottom in canvas pixels, right and bottom one past), `rgb`, `subject`, and `of_subject_height` (`height`, `width`, `gap`). This box is the single value used for painting and erasing; `video-frames` reads it from the report with no second copy.

`video-frames --prop-plate <canvas report json>` takes the canvas report path, joining the inputs a run never overwrites. It is checked before frame extraction and refused before keying if the file is not a canvas report or holds no plate ("holds no plate; the canvas was made without --prop-plate"). The box is scaled to the clip's frames by the ratio of frame size to canvas size rounded outward on each axis, then widened by a 3 px rim on each side (`erase_box`). On a run that passed, every keyed frame is erased inside `erase_box` before handover: a pixel becomes fully transparent when red and blue lead green by more than 35 and red and blue are within 90 of each other, or when each channel (with alpha as one of the channels) is within 40 of the median that pixel shows over the whole clip. The median represents the plate and its surroundings because the subject crosses for part of the clip only; checking alpha prevents transparent background pixels from reading as black, keeping the subject's dark outline intact across the rim. Refused runs keep the plate in their refused folder. The frames report adds `prop_plate` (`canvas_report`, `erase_box`, `rim`, `frames`, `frames_changed`, `erased_px`). `video-loop` cuts of erased frames carry no plate; `video-set` does not paint or erase a plate.

The video model must keep the plate where the canvas has it. In a frame where the model draws the plate one pixel off, the plate's sides stay as the codec's grey blend of magenta and green about one pixel wide down each side, which neither erase rule takes (in synthetic tests, frames with the plate one pixel to the right keep 188 and 95 such pixels). Subject edge pixels tinted magenta by the codec are erased with the plate (2 pixels in each of two frames in synthetic tests). No other guard, retry, or measurement is added.

```bash
sprite-gen video-canvas --still still.png --out canvas.png --state jump --key green --facing right --prop-plate --report canvas.json
# film canvas.png into clip.mp4 (section 2)
sprite-gen video-frames --clip clip.mp4 --out-dir frames --key green --reference canvas.png --prop-plate canvas.json
sprite-gen video-loop --frames-dir frames/keyed --out-dir loop --fps 24 --state jump
```

## 2. Clip — `sprite-gen video`

For loops, prompt for **in-place, evenly paced, returns-to-start** motion on a flat
chroma fill ("walks in place on a treadmill", "hop … return to the exact starting
stance … same height every time"). `video-set` carries those templates
(`MOTION_TEXT` / `VIEW_TEXT`). They describe the gait "for this body type" and never
name limbs.

There are two three-quarter views, `front_diagonal` and `back_diagonal`: turned to the
right, the way an isometric game's character walks down and up to the right (turned over, they face
left; a character with an item on one side has them drawn facing left instead, see
[handedness](#handedness--an-item-on-one-side)).

A caller's own motion paragraph (`build_prompt(motion=...)`) gets the same `HOLD_TEXT` sentence
after it, once: a request interpreter writes its motion before the still
exists and cannot know where the other hand's item is drawn, so it writes the choreography and
the engine says what stays put.

`build_prompt(direction, state, character, facing, motion=...)` takes a caller's own motion
paragraph — whole sentences about the subject, such as a request interpreter writes per
request — in place of the built-in state sentence. The frame, camera, background and design
rules stay the engine's, and an attack keeps its `HOLD_TEXT` sentence after the paragraph. A
walk or run gets `GAIT_HOLD_TEXT` for its view after the paragraph instead: it stays in place as
if on a treadmill and keeps facing the viewer, away from the viewer or `{facing}` the whole time,
so a caller describes the gait (a sneak, a march, a lazy stroll) and the engine keeps it on the
spot.

**A front or back walk starts mid-step.** `video-set` does this before
the canvas (`--walk-start redraw`, the default): one `sprite-gen gen --ref <base>` call per front or
back walk, on the base's own key, kept as `walk-start.png` with its report and reused while the
prompt and base are the same; a base on no chroma key is refused with `--walk-start as-given`, which
films from the base itself. Side and diagonal walks, and every other state, film from the base.

### A body that is not a person — `--body-plan`

`video-set --body-plan`, `video-prompt --body-plan`, `gen --direction --body-plan`, `prepare
--body-plan` and
`build_prompt(body_plan=...)`, `walk_start_prompt(body_plan=...)`, `still_view_text(body_plan=...)`,
`gen.still_prompt(body_plan=...)` name what the subject stands on (`sprite_gen/video/body_plan.py`):
`biped` (the default), `quadruped` or `legless` for the character, or one `<figure>=<plan>` per figure
of a scene (`--body-plan "the man=biped" --body-plan "the horse=quadruped"`). Any other body gets:

| Sentence | for a body that is not one biped |
|---|---|
| attack | `ATTACK_TEXT_ANY_BODY`: the same timed strike "with what it is already holding, or with its own body if it holds nothing", then `HOLD_TEXT_ANY_BODY["attack"]`: anything it holds stays held the same way, a part the strike does not use stays where it is drawn, no hand counted. Which part strikes (a bite, a head-butt, a forefoot) is not guessed: a caller who knows says it with `--motion`, which gets `HOLD_TEXT_ANY_BODY` after it |

Only the biped sentences were measured on clips; the others say no part the body lacks and are not
yet measured. An app
that draws the still itself with `still_view_text` and films with `build_prompt` passes the same
`body_plan` to both: the clip starts from that still.

#### The sheet rows — `prepare --body-plan`

The run records the plan in `sprite-request.json` as `body_plan` (absent without one), and a run
prepared again from that request keeps it.
A request's own `action` is its own words and is
never rewritten.

### A clip from a video MCP on your agent — ZCRE

sprite-gen never calls a video MCP. When your own agent (Claude, Codex) has one connected — ZCRE's
remote MCP (`https://mcp.zcre.ai/mcp`, signed in with your own ZCRE account, paid from your own
credits, under ZCRE's terms) — the agent makes the clip with that MCP's tools and sprite-gen does the
rest, stage by stage, with the verbs `video-set` runs: `video-prompt` prints the prompt `video-set`
would send, `video-frames` and `video-loop` cut each mp4 the agent saved, and for a walk or run cut in
two or more directions `video-cycle-align` gives them one cycle length, each turned to start on a
foot strike ([loop repair](loop-repair.md) section 4). `video-set` itself is not run on this route, so
what it does for a set happens only when you run it: `--view` names the still's view to `video-canvas`, as `video-set`
does, so a side walk keeps its room ([section 1](#1-canvas--the-input-frame-decides-the-output-frame)). What crosses the boundary is a PNG, a prompt and
an mp4.

```bash
sprite-gen video-canvas --still still.png --state walk --facing right --view side --out item/canvas.png --report item/canvas.report.json
sprite-gen video-prompt --direction side --state walk --facing right --no-last-frame --json > item/prompt.json
# the agent, with its ZCRE tools and grok-imagine-video-1.5: prepare_upload for item/canvas.png and the
# transfer it describes, query_zcre upload to confirm it, query_zcre quote with prompt.json's prompt and
# duration, the credits shown to the user and approved, create_generation with that quote, query_zcre
# status until it succeeds, the output saved as item/clip.mp4
sprite-gen video-frames --clip item/clip.mp4 --out-dir item/frames --spill auto --reference item/canvas.png
sprite-gen video-loop --frames-dir item/frames/keyed --out-dir item/loop --fps <fps in item/frames/frames.report.json> --state walk --cycle auto --anchor motion-auto --view side --facing right --name side-walk
# a set, once the walk is cut in every direction (each as above, in its own folder):
sprite-gen video-cycle-align --loop-dir front/loop --loop-dir side/loop --loop-dir back/loop --view front --view side@right --view back --report walk.cycle-align.json
```

A walk or run is cut with `--anchor motion-auto`. A clip on this route has no end frame to bring it
back to its first frame's size, and `motion-auto` is the anchor that measures the size over the clip
and holds it before the cycle search
([section 4](#4-loop--period-first-seam-second-then-the-gait-floor)); `video-loop`'s own default for a
walk or run, `body`, does not. A jump takes no `--anchor`: `motion-auto` is refused for a state that
is not a walk or run. `--fps` is the frames report's own, never a number written in by hand. Give the
clip's view (`--view`) so a front, back or diagonal walk is read for crossings that mark cycles;
`video-set` passes each item's view and cuts every walk or run whose loop is searched this way by
default.

The set stage is the same step `video-set` runs after its loops are cut (`--align-cycles auto`, its
default): one `--loop-dir` per direction of the same walk or run, one `--view` each in the same
order, so that every loop starts as the same own foot lands wherever its view can tell the feet
apart. A frame that falls between two source frames is made by RIFE, and the command fails where one
is needed and RIFE is not installed; `--between nearest` makes none. A loop whose view cannot
tell its feet apart — or tells them apart by too small a margin (`foot_why` `low-margin`) — starts
on its larger strike and is listed under the report's `unnamed_feet` with its two strike frames in
`cycle/`: look at the first, say which own foot lands there, and run the same alignment again with
`--foot <loop>=left|right` — that loop alone turns to start on the set's foot
(`start_foot_source: "given"`).
Every aligned loop's report row (and its `strip.json`, under `cycle_align`) records its beat
(`beat`): how far the frames the alignment moved off their own time jolt the legs, in beats; under
`--between auto` a frame RIFE could not make cleanly is made again at another fraction of its two
source frames, or its jolt is shared by the frames beside it, where the nearer source frame would
break the beat; where nothing keeps it, the loop is named in the warnings. A loop whose `--view` is
`back` or `back_diagonal` is also read for stepping the same foot twice in a row (`same_foot`),
named in the warnings with whether any other cut of its clip would alternate; `--same-foot refuse`
stops the set before anything is rewritten instead (both are explained in
[loop repair](loop-repair.md) section 4).

`video-set`'s own alignment (`batch.align_gaits`) leaves out a loop named a take to film again for
`start-stop`, a loop whose cut nothing witnesses whole cycles (`cycle-unwitnessed`: its crossings
left on steps unwitnessed, or its cut held by the cut witness), which may hold half a cycle or two,
or a loop its body rises and falls four times in (`cycle-four-steps`: it holds two cycles where its
strip counts two steps, and aligned as one cycle it would play twice as fast): its loop is kept as
cut, the rest are aligned without it, and the state's record names it under `left_out` with its
reasons. A loop left out whose step screen suspects it is one step is told in its warning what
settles that (`video-loop --steps 1`), as `video-cycle-align` says when it stops on such a loop.

`video-prompt --json` also gives the duration (the state's own, as `video-set`), whether the clip
ends on the canvas (`last_frame`), the loop cut (`cycle`) and the canvas, clip, frames and loop
commands with placeholders; a front or back walk adds `start_still`, the mid-step redraw `video-set`
makes before its canvas (`sprite-gen gen --ref`, your image provider). `--character`, `--motion`, `--model` and `--body-plan` reach the
prompt as they reach `build_prompt`. What the ZCRE route supports:

| | Through ZCRE's MCP |
|---|---|
| Model | `grok-imagine-video-1.5` (Grok Imagine Pro), image-to-video only. There is no Lite model there, so `LITE_WALK_TEXT` never applies. |
| End frame | None. `--no-last-frame` refuses what `video-set` films pinned — idle, attack, a walk or run in a diagonal (`pins_last_frame`). `--unpinned` films one anyway, with a warning: the prompt asks for an evenly paced repeat (an attack for one strike), the loop is searched instead of cut whole, and it may not close. Walk, run and jump from the side, front and back are the route. |
| Resolution | 480p, 720p, 1080p. The clip keeps the canvas's ratio. |
| Side facing | `video-set`'s facing observation is not run on this route; check the still faces `--facing`. |

### Handedness — an item on one side

A watch on one wrist, a pin on one side of the head, a bag on one shoulder: a right-facing view turned
over puts the item on the other side. Mirroring stays the default — five views make eight directions —
because drawing the left-facing views costs as much again. For a character whose item must stay put,
draw them instead, and say where the item is.

**The item, once.** `--handed "<item>=<left|right> [body part]"` names an item and the character's own
side it is on (`handedness.parse`; repeatable): `"the black smartwatch=left wrist"`. The engine works
out where that side is in each view (`handedness.placement`).

**The still.** `sprite-gen gen --direction <view> [--facing right|left] --handed …` adds the engine's
view sentence (`still_view_text`, the one a diagonal or front still is drawn with) and, per item, the
handedness sentence (`handedness.text`): the item is on the own left wrist only and the right wrist has
none; where that wrist is in this view (at the picture's right, on the far side); and that these
sentences decide the side whatever an attached picture shows. A side or diagonal view needs `--facing`,
a front or back view takes none. With `--handed`, `--facing-fix mirror` is refused and `regen` never
falls back to mirroring a still-opposite retry: a mirror moves the item. Do not attach a picture whose
item is on the other side — a mirrored still, or a side picture drawn the wrong way: check it with
`handed-check` first. A front or back picture, where the item's side is plain, is the safe reference.

A clip keeps what its first frame shows and makes up what it hides. For a walk or run with an item on
the far arm, draw the side still **mid-stride with both arms in view**, and say the pose in your
prompt — the engine's sentence says where the item shows, not how the character stands:

> caught in the middle of a walking stride: the far arm swings forward, its forearm and hand out in
> front of the chest, clear of the body's outline, with the watch on that wrist in plain sight; the
> near arm swings back past the body and shows whole

`video-prompt --json` names this under `still_needs` (and on stderr), and `video-set` under each
item's `still_needs`, whenever a walk or run has an item on the far wrist or hand of a side view.

No sentence holds an arm still: the arms swing with the walk. The other arm is said bare, never as a
list of what it must not wear — a clip prompt that lists that draws it there.

**Left-facing views, drawn.** `video-set --facing right,left` films every side and diagonal view both
ways, each from its own still: `--base side@right=E.png --base side@left=W.png --base
front_diagonal@right=SE.png --base front_diagonal@left=SW.png …` (front and back once, as before). A
turned view with no still for one facing is refused, naming the `gen --direction … --facing …` call
that draws it — the engine never turns one over for the other.

**The check.** `sprite-gen handed-check --strip <loop>.strip.png --direction <view> [--facing …]
[--state walk] --handed … --marker '#RRGGBB'` (or `--image`, `--frames-dir`) finds the item by a saturated colour
nothing else on the character has — a watch's screen — and checks every frame.

`--strap '#RRGGBB' --zone TOP,BOTTOM` adds the item's band seen without its marker: pixels of that
colour in those rows of the body (fractions of its height), eroded so outlines drop out, wider and
taller than 1.6 % of the body height and away from every marker blob, are another place the item
shows. It is opt-in because dark colours are common — outlines, a hat, motion smear on the legs — and
the zone is the character's wrist height. `--board` draws every frame with its blobs ringed (green as
expected, red not) and bands boxed, for what pixels cannot judge: an unmarked item, an item hidden on
the wrong hand. The exit code is 1 on a failure.

## 3. Frames — extract, key, check the edges

`ffmpeg` extracts every frame; the clip's real fps is recorded (never assumed).

Choose the key
away from the subject's hues ([chroma-alpha.md](chroma-alpha.md)).

The report also carries per-frame alpha coverage and an **edge-contact check**, read after the
specks are gone: any opaque pixel in the top/left/right 4-pixel bands is contact. Each contact pixel is
classified by its *raw* colour and the piece of the keyed frame it is in (its opaque pixels, corners
joining them, as the specks are read): the declared key's hue family in a piece with nothing else in
it is **`residual`** (background the matte did not erase); anything else is **`subject`** — a
key-tinted pixel joined to the subject included. The two
defects fail with different messages: residual-only contact points at `video-canvas`
(normalize the base still and regenerate); subject contact means the model framed too
tight and points at a taller/wider canvas. When both occur the message names both.
`residual` can also be reported on the antialiased fringe where a subject genuinely
touches the edge (a key-tinted blend pixel with low alpha), so a "framed too tight"
message with a small residual count is still a framing problem, not a key problem.
`--allow-edge-contact` turns the whole check off. `--allow-subject-edge-contact`
accepts only the subject at the edge — the clipping a `--fit tight` canvas chooses —
and still fails a frame where the key alone reaches the edge, which is a keying defect
whatever the framing.

When filmed from a canvas holding an obstacle plate, `video-frames --prop-plate` erases the plate from every keyed frame before handover ([section 1](#prop-plate---prop-plate)).

**A drawing cut by its own frame**. A bust or half-length drawing runs off the frame
itself (its sleeve is cut off at the left, its hair at the top). In every frame of its clip the
subject touches those edges, and no taller or wider canvas gives it room there. The new default, `edge_policy: "border"`,
reads the drawing's own border on the clip's first frame (keyed, its specks erased). A side (top,
left or right) is a border side when the subject's pixels on that side's outermost row or column
cover at least 2 % of the side's length (`BORDER_MIN_FRACTION`, 0.02). A strand's tip grazing the
edge covers less than that; a drawing cut there covers far more. A pixel that is only near the
edge, inside the 4-pixel band but not on the outermost line, does not make a border. This reading is implemented by `frames.extract.own_border` using `FRAME_SIDES` and `BORDER_MIN_FRACTION`. The Breathe layer's `pixel` mode uses the same function when wording its refusal of a frame pushed past a border side ([breathing](breathing.md)).

Subject contact on a border side is accepted in every frame, wherever it is along that side.
Subject contact on any other side is still the clip framed too tight and refused, with the same
message as before. Example: a bust cut at the top and left whose hand leaves the right is
refused. The numbers in a refusal message (top / left / right px of the worst frame) are now the
pixels the border does not take, side by side. So a caller that reads those numbers to choose a
taller or wider canvas reads the side that needs room, not the border sides.

Leftover key background is still refused, with one exception: the body's rim the matte cut
loose. A thin outline drawn with the key's colour blended into it carries the key's hue, so the
matte erases most of it and can leave a few pixels at the edge between strands of hair, on their
own; read by colour they are residual. A key-only (residual) piece is accepted only when all of
these hold: it is on a border side; the same frame's subject reaches that side's band; the whole
piece holds fewer than 8 pixels (`SPECK_MIN_PX`, the speck size); and every pixel of the piece
lies within 10 px of the frame's body (`RIM_REACH`). Distance is counted as a square: each way
and diagonally one pixel is 1. The body is the frame's largest piece that holds a pixel off the
key's colour, as the speck pass reads the largest piece as the body. The whole piece counts, in
the band and out of it: an 8-pixel piece with 4 of its pixels in the band is refused. A corner pixel lies in two bands; it is accepted only when both sides
accept it.

When a side is read as the border, `video-frames`
prints one line on stderr naming the sides and pointing at `edge_border` in the report. Known limit: on a side that is already a border side, a hand that
newly leaves the frame there is accepted, because by position it cannot be told from the sleeve
moving along the edge.

**What a refused run leaves**. Now `keyed/` exists only after a run
that passed. The frames are keyed in a hidden working folder beside it, renamed `keyed/` when
the run passes, and left as `keyed.refused/` when it is refused (by the edge check, or by a
frame keyed to 0 % transparency). A refused run leaves no `keyed/`, not even an earlier run's:
the earlier one is removed when the run starts reading the clip. A pass removes
`keyed.refused/`. The report (`frames.report.json`, or `--report`) is written either way. A run stopped by an argument error (an unknown flag
value, a missing clip) touches nothing.

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
character that *is* green keeps its colour. Tiny accents or dark, edge-only material can fall below that reference test; use `--spill small` when preserving those is essential.

### Edges: `--decontam`

`--spill` fixes key colour painted into the subject; strands and outlines at its edge are a different problem: H.264 4:2:0 chroma mixes the key into anything one to three pixels wide, leaving an olive rim on red hair or turning it orange under despill. `video-frames` and `video-set` default to `--decontam auto`, which re-explains each edge pixel as a blend of the local key background with colours the subject owns and writes that colour at the pixel's observed luma. Where `auto` cannot run (a white key, or a subject with no interior deeper than 6 px) and under `--alpha-mode brightness`, it leaves frames exactly as keyed and records the reason in the report. On stderr, `video-frames` prints a one-line warning (and `video-set` lists it under `warnings`) when `auto` did not run on any frame, skipped frames, found key-coloured subject material, or kept edge pixels beside a small key-coloured part as the matte keyed them.

Pass `--decontam off` to keep the matte as it is. Method, guards and measurements: [chroma-alpha.md](chroma-alpha.md#decontam--give-the-edge-the-subjects-own-colour-back).

## 3b. Canvas shape for raised limbs and wide costumes

`video-set` picks the canvas from the state row, and for a walk or run from the side from its
still's room too ([section 1](#1-canvas--the-input-frame-decides-the-output-frame), `GAIT_ROOM`). Two things that are not a jump or
an attack still leave a 1:1 frame: limbs raised in a celebration, and a costume that is
wider than the body (a skirt, a veil, a held object). Both fail `video-frames`'s
edge-contact check — the clip was made, the frames were cut, and the run stops at the
gate. The state table now routes `cheer`, `wave` and `celebrate` to the wide canvas, and
`video-set --shape wide` forces it for every state of a batch when the costume is the
reason. A side walk or run whose still leaves a big tail or a wide costume too little room no longer needs
`--shape wide`: its square is widened by the room the still lacks.

## Video Processing Pipeline and Terminal Gate

```bash
sprite-gen video-canvas --still still.png --state death --out canvas.png
sprite-gen video-prompt --direction side --state death > prompt.txt
sprite-gen video --image canvas.png --prompt-file prompt.txt --duration 3 --resolution 480p --out clip.mp4
sprite-gen video-frames --clip clip.mp4 --out-dir frames --spill auto --reference canvas.png
sprite-gen video-loop --frames-dir frames/keyed --fps 24 --state death --cycle once --out-dir out
```

In the sequence above, replace 24 with the `fps` value from `frames.report.json`. For batch operations, `sprite-gen video-set --end-frame` takes `ITEM=PATH` (e.g., `--end-frame side-death=final.png`). With no seam gate or wrap correction, nonloop insists on `--anchor none` and `--fit state`, with no optional override to anchor. See [states-and-frames.md](states-and-frames.md) for the state table.

This provides a conservative geometric screen; inspect semantic pose visually.

In `sprite-gen video`, `--image` is the start frame and `--last-frame` is the end frame. Unpinned execution is the default when omitting terminal canvas arguments. Nonloop clip cache identity includes end image SHA256; a changed end pin cache rejects until user explicitly `--force` regenerates.

## 4. Loop — period first, seam second, then the gait floor

`--min-len/--max-len` override the window — on every path that
sets a cut's length, the gait fallback, `--steps` and the one-shot failover included; a cut outside
them fails rather than being written.

See [loop review](loop-review.md)
for the visual review contract and manual overrides.

**Held cells: found, filled, read again: the default step of every cut.** A cut whose neighbour cells show the same
picture (a frame shown twice or three times: a model that stalls, a rate carried to another by repeating frames)
stops on it as it plays. `video-loop` reads each cut it makes for them (loop, one-shot, once, once-return, so every
cut of `video-set` and of a clip from `video`) and by default makes each run of them again with RIFE's frames between
the pose before and the pose after (`--fill-held auto`), the cut's length, beat, first and last cells and wrap kept,
a part standing still in both kept still; a run longer than three cells, the ends of a cut played once, a loop's last
cells and a cut drawn on twos or threes (limited animation, `held_fill.drawn`) stay as filmed, and so does a run no
frame between is taken for (a fault, or a frame that shows the picture beside it again), named in a warning. Then it
reads the cut again. The report and the strip metadata record the held runs (first cell, how many) and the poses over
the cells (`held_fill`); tell the person that one line: `poses/cells`, e.g. `24/27` before and `27/27` after. With no
RIFE the cells are delivered as filmed and a warning names the motion and the install line; `--fill-held off` cuts as
before. A cut asked for above its clip's rate (`--out-fps 60` on a 24 fps clip) is made of frames between, never
frames shown again ([loop repair](loop-repair.md) section 7).
A cut a game shows smaller than it was cut is given `--judge-scale` at that share (`--judge-scale 0.6` for cells
drawn at 60 %), so a frame between refused only for its outline as cut is taken back where its line keeps its ink at
the size it is seen; every other reading and the frames delivered stay as cut ([loop repair](loop-repair.md) section 7).

**The fundamental period: recorded, never cut on the pixels' word.** A motion that repeats every
P frames repeats every 2P as well, and 2P can be the better repeat: a clip drawn on twos (a new
drawing every other frame) whose cycle is an odd number of frames shows each cycle half a drawing
off the one before, so the profile dips deeper two cycles on than one, and the minimum taken above
is two cycles. Played alone that loop is clean; resampled to the length of a set whose other loops
hold one cycle ([loop repair](loop-repair.md) section 4) it walks at twice their speed.

The engine does not cut it shorter. Half a gait cycle on, the legs are back in place with near and
far swapped; where the two legs are drawn alike (one colour of trousers, white fur) that one step
and a true cycle half a drawing late are **the same picture**. So
`video-loop` cuts the period it always cut, and only **records** what a half or a third of it shows
(`cycle.fundamental`, `sprite_gen/video/period.py`).

**A cut under the gait floor is one step: the clip is asked for two, and the cut is named a half step
where it gives none.** A walk or run loops on an even number of steps — two, one cycle; a loop of one
step plays the same leg leading every time round. The gait floor (`LoopProfile.min_seconds`: walk 0.6 s,
run 0.35 s) is the duration under which a period is one step, and the half-period guard reads it in
whole frames, rounded, to choose among candidates: a run cut 8 frames long at 24 fps (0.33 s) is taken.
Such a periodic cut (`--cycle auto` or `periodic`) whose steps nothing counted — no `cycle.steps`: not
the clip's crossings, `--steps` or the held side's two lengths — is one step.

Under `--anchor motion-auto` it is cut two lengths long where the clip says it holds two
(`loop._two_lengths`). One step on, the legs are back in place with near and far swapped, and where the
two are drawn alike (one colour of trousers) the silhouette repeats; the body's turn, the shading across
the torso and the folds of the clothes come back only two steps on. So each length of [2L − 1, 2L + 1]
(inside an explicit `--min-len`/`--max-len`) is cut as `--steps 1` cuts it, and about each cut's wrap
(two frames either side of its first, `TWO_LENGTHS_AROUND`) the frames as filmed are read two lengths on
over one length on, the same frames (`two_lengths_on`). The nearest at most **0.85**
(`period.STEP_RATIO_MAX`, the step screen's bound for the same ratio, which the screen reads over the
whole clip, where a run that changes its pace does not repeat) is taken where the seam gate and the seam
pop gate pass it as they will the loop; `cycle.steps` is `{"count": 2, "by": "two-lengths", "first_cut",
"window"}` and `cycle.two_lengths` records `first_cut`, `tried` (each length's cut: `near`, `one`,
`ratio`, or why it was not read), `cut` and `taken`. The frames are read as filmed, not on the search's
distances: the analysis canvas moves every frame by the drift it reads, one vertical slope over the clip,
and a slope tilted by frames that open higher moves frames two steps apart further than one step apart
where the body runs in place, so two lengths on read no nearer there. A drift the clip does walk only
makes two lengths on read farther: it keeps a cut one step, never doubles one.
The cut so chosen then goes to the cut's witness like any other (`loop._witnessed`, docs/loop-repair.md
section 3, "The step count").

A cut the clip gives no two — two lengths on no nearer, one length on the same picture, no window to
cut, a gate that would not pass it — and a cut under any other anchor are kept as cut, recorded in
`cycle.half_step` (`rule`, `state`, `seconds`, `gait_floor` in seconds and frames, `why` the steps were
not counted, `two_steps`: twice the cut and whether the clip holds it, `two_lengths`: why the clip gave
none) and named in a warning line (`loop.half_step`, `HALF_STEP_RULE`). `--steps 1` cuts it two lengths
long.

**The size is held before the search or cut** (`--size-hold auto`, the default). A walk or run clip filmed from its first frame only often grows or shrinks as it plays, so the frame one cycle after the first is not the first's size and the loop pops at the wrap. Under `--size-hold auto`, a walk or run cut automatically with `--anchor motion-auto` (before the cycle search) or cut by hand with `--cycle fixed` under any anchor (before the cut is read, over the whole clip) is held at its first frame's size. When a clip's height changes by 1 % or more over the clip, read one cycle on, frames are scaled back to the first frame's size about their feet. Where the size is held and the feet climb or sink by 1 % of the first frame's height or more over the clip, frames are also shifted so their feet stand on the first frame's row. This stops a receding walk's jump at the wrap from moving into place: the motion anchor's fine search corrects a climb within 28 rows over the cut but leaves the cut uncorrected when feet climb further than that, and the body anchor moves frames sideways only.

The cut's seam and step count are measured on the held frames. A fixed cut is held only where the cut itself shows the change, comparing each frame of the cut with the frame one cycle of the cut's length later, or, where the clip ends before that frame, with the frame one cycle earlier, and feet are moved only where the cut shows them move. Where the cut does not show the change, it is cut as filmed, and the report says why. The report records `size_hold` with `padding_ltrb`, records `size_hold.feet` when size is held, and also records `size_hold.cut_frames` on a held fixed cut. Fixed cuts with `--anchor motion` record the shifted target boxes in `size_hold.anchor_regions`. Automatic searches under `--anchor body`, `feet`, or `none`, `--cycle pinned`, and cuts of states that are not a walk or run are not held. `video-loop-repair` restores a held fixed cut from its source frames by holding them again by those moves (a report without them, from 3.1.0, is not restored).

Passing `--size-hold off` on a fixed cut keeps the filmed frames pixel for pixel and records `size_hold: {"applied": false, "why": "--size-hold off"}`. Under `--anchor motion-auto`, `--size-hold off` searches the filmed frames first, but if the first search finds no cycle, the gait fallback still scales back a size change of 3 % or more with feet left where filmed and searches again.

**A lead-in is left out of the search** (walk and run, `--cycle auto` or `periodic`). A video
model may reframe a small subject in a clip's first frames: the body grows (or shrinks) by a third
or more in under half a second, then walks at its new size. What it finds, and what it refuses, is said in the clip's own frame numbers:
every `start` and `context_pair_range` in the report's `cycle`, refused candidates too, so a cut
taken again from them (`--cycle fixed --start`) is the frames they name. A cut the caller names (`fixed`, `pinned`) is the caller's frames, lead-in or not.

**The body turn is avoided by the cut search.** A video model asked to walk at the still's angle sometimes turns the body inside the clip: a back-diagonal walk turns to a full back view half way and back. A loop cut across the turn plays the body swinging round. Every walk or run with `--anchor motion-auto` reads each frame to find turns. No window whose cut holds a frame of a turn is taken while another window holds a cycle. The search refuses that window with the reason `the body turns (clip frames a-b)`. Two-step cuts by the clip's crossings that hold a turn are left out (`step_count.turned_out` counts them), and so are the held side's windows two lengths long.

The reading (`torso-side-v2` in `sprite_gen/video/body_turn.py`) reads each frame's torso against the clip's first frame: the still the clip was filmed from, a standing pose. It does so whether or not the clip opens on a lead-in, where the video model reframes the still to the walk's size over the first frames, which the cut search leaves out. It does not read against the first frame after the lead-in: that frame is a stride's pose, and a walk draws the mirror of a stride's pose half a cycle later, so a synthetic walk that does not turn, read against it, read a turn every stride (least -0.34). Read against the still filmed small, the same synthetic walk (its first 8 frames filmed from 0.6 of its size) reads +0.87 at least where it does not turn (+0.92 without the lead-in); turning over frames 30 to 40, its turn over frames 32 to 38, the same frames as without the lead-in, least -0.71 (-0.63 without the lead-in). Turns of 4 frames under 0, drawn from 8 places (the facing over the mirror over frames 8 to 15, 10 to 17, 12 to 19, 14 to 21, 16 to 23, 20 to 27, 24 to 31 and 30 to 37), each read over the same 4 frames with the lead-in as without it (frames 10 to 13 for the turn drawn over frames 8 to 15, and so on); `torso-side-v1` read 7 of the 8, not the one drawn over frames 10 to 17, with the lead-in or without it. The torso crop runs from the neck down half the body below it, centred on the head's centre, as wide as the first frame's torso. In `torso-side-v2`, each frame's box top and the still's torso width are read by mass under the named bound `MASS_SHARE` = 0.01, the same hundredth as the speck bound of `sprite_gen/video/frames.py` (`SPECK_MIN_FRACTION`). The box's top is the row with more than a hundredth of the frame's solid pixels above it, and the still's torso width (the crop's width, held for every frame) is read about the head's centre from the columns of the still's torso rows with more than a hundredth of those rows' solid pixels beside them. Under `torso-side-v1`, the box ran from the frame's topmost solid pixel and the width from the torso rows' outermost columns, so a piece apart from the body moved them: on the synthetic walkers, a 3 px fleck over the head for 10 frames read a turn over those frames, a 6 px fleck near the head read a turn, a 6 px fleck beside the head in the still alone read a turn over the whole clip (frames 1 to 72), an ornament cut loose beside the head hid the turn the walk made, and an antenna cut loose from the head over frames 30 to 39 hid the turn too. `MASS_SHARE` turnovers: at 0.005 the antenna over the head (1 px wide and 31 px tall, 0.008 of the walker's pixels, cut loose from the head over frames 30 to 39) tops the box and the walk reads a turn every stride (frames 14 to 22, 37 to 46 and 62 to 70); at 0.008 and 0.009 no synthetic reading changes; at 0.011 a front walker with a 70 px tail reads one of its turns from a frame earlier; at 0.0125 the 7 px fleck over the head over frames 30 to 39 (49 px) no longer tops the box, and its frames read no turn. Of the test suite's 42 named walkers, `torso-side-v1` reads 8 wrong and `torso-side-v2` none, and across 186 placements of a 3 px fleck anywhere along the canvas's top with an antenna or whisker cut loose over frames 30 to 39, `torso-side-v1` reads a turn at 34 and `torso-side-v2` at none. The box's bottom is the frame's last solid row, as `torso-side-v1` read it: the one end of the box not read by mass. Where the legs cross each stride they lay their pixels over each other, and a bottom read by mass (the row with a hundredth of the frame's solid pixels under it, as the top is read) rises with them: on the lead-in walker, a row over its last solid row in most frames and 3 rows over where the legs cross, moving the crop's rows with it; read either way, the walkers turning over frames 30 to 40 and the 4-frame turns drawn from 8 places, with the lead-in and without it, read their turns over the same frames. The neck is the narrowest row of the first frame between 10 % and 55 % of its height, taking the topmost row on a tie. A body with no neck narrower than its head (a hood widening down to the shoulders) has its narrowest row at the top of that range, so its crop holds the head and the chest, which turn with the body. On a synthetic hooded walker the neck reads at 0.11 of the height (the range's top row) and its turn reads as on a walker with a neck (least -0.67; +0.91 where it does not turn). Every crop is centred on the head's centre (`HEAD_CORE` = 0.5): the middle of the solid pixels of the top 15 % of the body, now taken only over the columns of those rows holding at least `HEAD_CORE` (0.5, half) of the fullest column's solid pixels, so a 6 px fleck or an ornament cut loose beside the head does not count. At 0.3 the columns of a 6 px fleck beside the head in the still alone count, and the still's centre moves 1.5 px; at 0.35 the columns of the ornament cut loose beside the head count, and it moves 0.9 px. With each frame read against the still at its nearest within `TOL` (described in the next paragraph), the fleck's walk still reads no turn and the ornament walker still reads its turn over frames 32 to 38. No synthetic walker's reading changes with `HEAD_CORE` anywhere from 0 to 0.9 (the suite's 42 named walkers and its 27 other synthetic walkers). Under the named bound `HEAD_REACH` = 0.3, that middle is now read again on the pixels within `HEAD_REACH` of the body's height of it, until it settles; where no pixel of those rows lies further out, the middle is the plain mass centre as before. A part swung up beside the head (a tail on a beat of its own, a held prop) pulls a plain mass centre aside on every swing, and the torso then reads as turning with it. The test suite's side walker with a 60 px tail on a 45-frame beat read a turn on every swing (least -0.19); with `HEAD_REACH` it reads none (least +0.11) and is cut as before (on it, 0.3 reads no turn and 0.35 a turn every swing). Where no solid pixel of those rows lies within `HEAD_REACH` of their middle, the top's parts lie apart on either side of the middle (a prop held as high as the head and apart from it, or a body too small for any pixel to lie that near), and the top does not tell which part is the head, so the frame is not read: its `side` is null and `body_turn` gains `unread`, a list of `{frames, why}` (each reason with the frames it applies to), while a clip whose first frame is not read records `read: false`, a `why`, and verdict `not-read`, running the cut search once for the same cut and bytes as without the reading. The largest part is not taken for the head because a prop can weigh what the head does: on the test suite's synthetic walker holding a staff beside it (its ornament at the head's height and apart from it, its handle in the hand), the ornament and the head hold the same number of solid pixels in those rows, 513 each, with the middle falling between them and no pixel within 39.6 px of it (0.3 of the 132 px body), and a crop centred on a prop would read turns the body does not make, which the cut search would leave out of the cut, or `--body-turn refuse` would refuse. Every other place where the reading would select nothing is also not read, never read as 0 and never centred on a middle no pixel holds: a first frame whose torso rows hold no solid pixel (a part apart above the body tops its box, so its narrowest row lies in the gap; on a synthetic walker with a pennant 240 px above its head, the neck reads at 0.11 and the rows from there down half the box are empty); a crop of no width (a torso one column wide under the head's centre, or a body one pixel high); a crop with nothing in it, which would have read 0, a torso as near the still as its mirror; and a clip with no frames; and a frame that is not read ends a run of turned frames, as a frame with no body already did.

Each crop's axis is the middle of the head's centre column (the column c spans c to c + 1). The crop's edges lie where they fall, between pixels too: the crop is resampled from the frame about them, and mirrored about the axis. A body that is its own mirror about its head crops as its own mirror wherever its centre falls: the walker drawn all in one grey (its silhouette alone, its own mirror about the middle of column 150) reads its head and shoulders and its torso 0.000000 off their mirror. `torso-side-v1` rounded each edge of the box to a whole pixel and mirrored the crop about the box's middle, the column's edge, half a pixel off the axis: in one grey, the head and shoulders lay 0.050 off their mirror and the torso 0.028, a side the silhouette does not show. In its own tones the still reads 0.102 off its mirror (`torso-side-v1`: 0.146). The still and every frame are also read about the axes either side of the head's axis because a piece beside the head whose columns each hold over half the fullest column's pixels moves the head's centre: a column 3 px wide and 12 px tall, 6 px right of the head and 2 rows under its top (36 px, under a hundredth of the walker; each of its columns holds 12 of the head band's 19 rows), moves it 1.4 px. Read about the centre alone, `torso-side-v1` read the walk with that column in the still turned over frames 1 to 72 (least -0.14), and with the column over frames 30 to 39 of the lead-in walker turned there (least -0.18). `torso-side-v2` reads both with no turn (least +0.64 and +0.83), and the same walkers turning over frames 30 to 40 read their turn over frames 32 to 38. A 6 px square in the same place in the still holds 6 rows of each column, under `HEAD_CORE`, and moves no centre; `torso-side-v1`, which read the head's centre as the plain mass centre, read the walk with it turned over frames 1 to 72 (least -0.14). With the still's centre read 0.25 to 1 px off either way, the walk reads no turn and no frame's side moves 0.07 (at most 0.062, at 0.5 px); `torso-side-v1` read the walk turned every stride from 0.75 px right (least -0.06). Besides the crop about the head's axis, each crop is cut about the axes `STEP` (0.25 px) apart either side of it, each as wide as the crop about the axis. `GUARD` = 2.5 px: the still's head and shoulders, cropped about the axis, are read against their mirrors about every axis within 2.5 px of it; their side (`still_side`, the side `SIDE_SHOWN_MIN` bounds) is 1 minus the largest of those correlations, how far they lie off their nearest mirror. `TOL` = 2 px: the still's torso is cropped about every axis within 2 px of its head's axis, and mirrored about each. Each frame's torso, cropped about its own head's axis, is read against the still's torso and against its mirror at their nearest: in `side` = (v.s - v.m) / (1 - s.m), v.s is the largest of its correlations with those crops and v.m the largest with those mirrors, and s.m is the largest of the still's torso crop about its own axis with those mirrors (1 - s.m is the report's `torso_still_side`). At `GUARD` from 0 to 2.0, the walker seen square from behind with the 3 by 12 px column 6 px right of its head in the still alone lies 0.093 (at 2.0) to 0.256 (at 0) off its nearest mirror, over `SIDE_SHOWN_MIN`, so the still is read, but its walk lies 0.000 at the median (`walk_side`), under `WALK_SIDE_MIN`, so the clip is not read; with `WALK_SIDE_MIN` set to 0 as well, it reads turns it does not make (frames 15 to 21 and 39 to 43) at every one of those values; at 2.25 the still lies 0.075 off its nearest mirror and is not read. At 2.5 it lies 0.060 off it, at 3 and 4 0.053, and is not read. At `STEP` 1 px the same still lies 0.093 off its nearest mirror and is read, but its walk lies 0.000 and the clip is not read; with `WALK_SIDE_MIN` at 0 as well it reads turns (frames 3 to 9, 27 to 32 and 51 to 57); at 0.75 the still lies 0.075 off it and is not read; at 0.5 and under it is not read either. At `TOL` 0, the lead-in walker with a 3 by 12 or a 2 by 18 px column beside its head over frames 30 to 39 reads a turn over frames 30 to 39 (least -0.26 with the 3 by 12 px column, -0.25 with the 2 by 18); from 0.5 to 4 it reads none. Margins at `TOL` 2: with every frame's head centre but the still's read the same distance off, the lead-in walker first reads a turn 3.75 px right (frames 1 to 4), and the side walker with a 60 px tail and its arms 3.25 px left; at `TOL` 1, at 2.5 and 2.25 px. With the still's centre alone read off, no synthetic walker reads a turn within 4 px either way.

Each frame's `side` = (v.s - v.m) / (1 - s.m), where v is the frame's crop, s the first frame's and m the first frame's mirrored about the head's centre (each a unit vector of luma times alpha and alpha); v.s and v.m are each taken at their largest over the first frame's torso crops and mirrors about the axes within `TOL` of its head's axis, and s.m at the largest of the first frame's own crop with those mirrors. A value of 1 means the torso shows the side the still shows; 0 means it is as near the still as its mirror (a body seen square from behind or in front); under 0 means it faces nearer the still's mirror. A pose the walk changes moves both correlations alike; a turn moves them apart. Each clip is read only against its own first frame, never against a named motion or direction. Four named module constants define the report field `bounds`, bounding whether the clip is read and what counts as a turn. `SIDE_SHOWN_MIN` = 0.08 sets how far the still's head and shoulders (the top 45 % of its body) lie off their nearest mirror about the axes within `GUARD` for the clip to be read at all. Under it the still is seen square (a front or back walk) and the facing is not read, because a back walk's far step lays its torso nearer its mirror each step. About their nearest mirror, the synthetic walkers seen at an angle lie 0.090 or more off it (the hooded walker 0.090, the lead-in walker 0.098, the plain walker 0.102, the side walker with a 60 px tail 0.397); the walker seen square from behind lies 0.0002 off it, and with a 36 px piece beside its head in the still alone (a 3 by 12 or 2 by 18 px column, or a 6 by 6 px square, against the head or 6 px from it, on either side) 0.049 to 0.060. At 0.06, the still of the square walker with the 3 by 12 px column 6 px right of its head is read, but its walk is not; at 0.095 the hooded walker is not read. The report field `walk_side` in `body_turn` and bound `WALK_SIDE_MIN` (0.045) gate front or back walks whose still alone shows a side. For each frame after the still, its head and shoulders (the top 45 % of the body, cropped about its head as wide as the still's crop) are compared with the still and its mirror within `GUARD` (2.5 px) of the still's head centre, giving nearness to the still minus nearness to the mirror (v.s - v.m); `walk_side` is the median across the read frames after the still of each frame's v.s - v.m, not counting any frame that flips under `HELD_MAX` (0.5 %) of the drawing last counted's solid pixels so each drawing counts once. Each frame of a square walk lies as near the still's mirror as its frame half a stride on lies near the still, so the median of every frame leans toward a half of the stride that the clip holds longer than the other: for the synthetic walker seen square from behind, its still taken 6 frames into its stride (the still 0.148 off its nearest mirror), each drawing of the first half of each stride held 3 frames and each drawing of the second half 1 frame (147 frames), the median of every frame lay 0.075 (0.074 with a 36 px piece beside its head in the still), over `WALK_SIDE_MIN`, and the clip was read, with turns over frames 19 to 29, 67 to 77 and 115 to 125 that it does not make, and no warning line, whereas counting each drawing once, its walk lies 0.000, as drawn on ones, and it is not read, with the warning line. A clip comes to the reading through a codec and the keying, which leave hardly a held frame as it was: when the same walker (still taken 6 frames into its stride, each drawing of the first half of each stride held 3 frames and each of the second half 1 frame, 147 frames) was put through libx264 at crf 0, 18, 23 and 28 and through `video-frames`' keying, at most 2 of its 74 held frames came back the same pixels as the frame before, and the rest with up to 0.9 % of their solid pixels flipped (under 0.5 % in 99 of 100 at crf 23, libx264's default). Counted again wherever not the same pixels, its walk lay 0.076 to 0.083 and it read turns that it does not make, whereas counted once under `HELD_MAX` it lies at most 0.013 and is not read. Held against the drawing last counted, a large or slow body is counted each time it has moved `HELD_MAX` from the drawing last counted. The synthetic walker over one stride, each drawing reached from the one before over frames that each flip under 0.4 % of the frame before's solid pixels: seen square from behind (its still taken 6 frames into its stride) its `walk_side` lies under `WALK_SIDE_MIN` and it is not read; seen at an angle its `walk_side` lies over `WALK_SIDE_MIN` and it is read. A drawing of the synthetic walkers flips 1.9 % or more of the previous drawing's solid pixels, except the front walker with a 70 px tail (0.44 % at one drawing, none at another), which reads square either way; its `walk_side` moved from 0.013 to 0.000, so the synthetic square walks now all lie at 0.000. Where the still passes `SIDE_SHOWN_MIN` and `walk_side` falls between -0.045 and +0.045, the walk is seen square: `read` is false, `verdict` is `not-read` (`why` starting "the walk is seen square"), and the loop is cut as search alone cuts it. Under `--body-turn warn` and `refuse`, `video-loop` prints one warning line beginning "video-loop: warning: the walk reads square against a still that shows a side (still_side", giving the still's side and `walk_side`, naming what it can be (a front or back walk, or a body that turned for much of the walk, to face the camera or to its mirror), stating its facing is not read, and ending "check the loop's facing"; `body_turn.square(reading)` returns this text or None. `TURN_SIDE_MAX` = 0 marks any frame reading under 0 as turned. `TURN_RUN_MIN` = 4 requires 4 turned frames in a row or more to count as a turn. Turns are counted in frames, the time the clip plays them, not in drawings: a walk held on twos holds a turn as long as one drawn on ones, in half the drawings. A shorter dip is the walk's own. A synthetic walker facing past its mirror 3 frames in a row reads no turn even 0.61 under 0; 4 frames in a row read one. A clip with no turn (or one not read) is searched once, exactly as before: the same cut, the same bytes.

Where no window without the turn holds a cycle, the flag `--body-turn` decides the outcome. Under `warn` (the default), the search runs again as before, and the cut it took before is kept with a warning line naming the turned frames, the least reading and its frame, the bound, the cut and the frames of the turn it holds. Where no cut holds a cycle at all, the clip is refused in the words it was refused in before (`video-loop: no periodic cycle found`), after a warning line naming the turn. An app that matches the old refusal lines keeps matching them. Under `refuse` (which needs `--anchor motion-auto`), the clip is refused in a line of its own, the first line the run prints: `video-loop: the body turns in the walk`. That line names the turned frames, the least reading and its frame, the bound, that no cut that leaves those frames out holds two steps that repeat (or, where a cut was found that holds the turn, which cut and which frames), and the prescription to film it again with the torso and hips kept at the still's angle. Nothing is handed over; the report is written with `status: failed`. App builders can attach a retake rule, such as `loop-body-turned`, to this refusal line. Every report on a walk or run under `--anchor motion-auto` includes the `body_turn` field: `reading` (`torso-side-v2`), `bounds` (the four), `reference` (0: the frame read against, the clip's first, lead-in or not), `still_side`, `walk_side`, `read` (true or false), `why` (where not read), `neck`, `torso_still_side`, `side` (each frame's reading), `turns` (each `{from, to, least, at}`), `mode` (`warn` or `refuse`), `verdict`, `refused` (how many windows were refused for it), `cut` (`frames`, the cut's first and last clip frame, and `holds`, the turned frames it holds), `avoiding` (the search's refusal where the search that avoided the turn found nothing), and `unread` (a list of `{frames, why}`, each reason with the frames it applies to, appearing only where at least one frame was not read). The `verdict` is one of `none` (read, no turn), `not-read` (a square still, a walk that reads square against a still that shows a side, a first frame with no body, or a first frame the reading cannot read (no head to centre on, no solid pixel across its torso rows, or a crop of no width or with nothing in it)), `clear` (a turn, and the cut holds none of it without refusing anything), `avoided` (windows were refused for the turn and the cut leaves it out), `kept` (no cut without the turn holds a cycle; the cut taken before is kept, warned), `unfound` (no cycle with the turn or without it; refused as before, warned), or `refused` (`--body-turn refuse`). The command's JSON summary gains `body_turn` (`verdict`, `turns`, `cut`) where a turn was read.

What it does not see:
- A square still (a front or back walk) is not read, so a front or back walk that turns to a diagonal is not seen.
- A walk turned to the still's mirror over about half its frames lies as near the mirror as the still at the median and is not read, with the warning line: the synthetic walker turned to its mirror over 39 to 41 of its 73 frames about their middle (38 to 41 hooded), that is 52 to 56 % of the clip; over 38 or fewer (37 hooded), or 42 or more, it is read with its turn.
- A walk that turns in every cycle over 14 to 16 frames of each 24-frame cycle (58 to 67 %) is not read, with the warning line; over 13 or fewer it is read.
- A walk turned to square and held there (a side walk that turns to face the camera and walks on toward it, or turns its back and stays) over 41 or more of its 73 frames (56 % and more; 40 or more hooded) is a square walk by its frames and is not read, with the warning line; for example the walker turned square at frame 24 and held to the end (49 frames) lies 0.006.
- A frame is counted once only where under `HELD_MAX` of the drawing last counted's solid pixels flip. A part of the stride that a clip plays slower in drawings of its own (not held), or a held frame under noise over `HELD_MAX` (at crf 28, some of the synthetic walker's held frames), counts frame by frame, so a square walk played that way lies off the still's mirror at the median toward that part; over `WALK_SIDE_MIN` it is read and reads turns it does not make. And a drawing that flips under `HELD_MAX` of the drawing last counted (a large or slow walker where its stride turns) is taken for that drawing held and is not counted.
- A shallow turn: a three-quarter walk that turns to the side each stride reads shallow, and a dip under 4 frames, or one that does not go under 0, is the walk's own.
- It does not make a walk where there is none: where the frames before the turn do not repeat either, avoiding the turn finds no cycle; the reading changes the reason given, not the result.
- A body whose narrowest row in 10 % to 55 % of its height is its waist (no neck and a head narrower than its waist) has its crop start at the waist; no such body has been measured.
- A body whose only side-to-side difference is its swinging arms, drawn symmetric about the head (the test suite's synthetic side walker, two arms either side of the head's centre, a tone apart), reads its own stride as a turn (least -0.50, every half cycle), because its stride is its own mirror. Every window holds such a turn, so `warn` keeps the cut the search took before, byte for byte, but `refuse` would refuse it.
- A clip whose first frame holds a prop as high as the head and apart from it (or a part apart above the body over its torso rows) is not read at all, so a turn in it is not seen.
- A piece apart over a hundredth of the body is read as the body's own: a 7 px fleck over the head over frames 30 to 39 (49 px of the 3849 px walker) tops the box and those frames read as a turn, as under `torso-side-v1`.
- The box's bottom is the last solid row: a piece apart under the feet runs the box down to it. In some frames (3 to 10 px square, 1 to 45 px under the lowest row the feet step on, over frames 30 to 39), the box runs down to it and no turn reads. In the still, far enough under the feet (from about 25 px under, the distance depending on the piece's size, on the synthetic walker), the still's torso reads as its own mirror and the clip is not read (verdict `not-read`, cut as the search alone cuts it), as under `torso-side-v1`.
- The neck is read on the still's rows 2 % of its height apart: a box a pixel taller or shorter can move the neck a step of that grid, and every frame's torso rows with it.
- A piece beside the still's head and shoulders (its head or an arm) can leave the clip unread: pieces under a hundredth of the body (a column or a row 1 or 3 px wide, a near-square, an L or a diagonal; 12, 24, 36 or 38 px), placed in the head's rows right or left of it (against it or apart), over the head (against it, apart, or at its corner), beside the neck or beside the arm, in the still alone, in every frame but the still, or over frames 30 to 39: 957 placements on each of four walkers that do not turn (the plain walker, the lead-in walker, the hooded walker, and the side walker with a 60 px tail and its arms), each frame's pieces erased first as keying erases them (`frames.drop_specks` with its defaults). Of the pieces under a hundredth of every frame they are drawn on and left by the keying (863, 372, 808 and 148 placements), none reads a turn; 11, 1, 22 and 0 leave the clip unread: each a piece in the still alone, 6 or 20 px right or left of the head (21 of the 34) or beside an arm (13), laying the still's head and shoulders under 0.08 off their nearest mirror.
- A front or back walk whose still shows a side because of a piece beside its head is now not read. This case is closed by `WALK_SIDE_MIN`: that square walker's still is read when `SIDE_SHOWN_MIN` is lowered to 0.06 (or `GUARD` set to 2, or `STEP` to 1), but its walk is not (`walk_side` 0.000); only with `WALK_SIDE_MIN` also at 0 does it read turns (frames 15 to 21 and 39 to 43). Where the still passes `SIDE_SHOWN_MIN` and the walk is seen square, `video-loop` prints one warning line under `--body-turn warn` and `refuse` alike, beginning "video-loop: warning: the walk reads square against a still that shows a side (still_side" and ending "check the loop's facing", giving the still's side and `walk_side`, naming what it can be (a front or back walk, or a body that turned for much of the walk, to face the camera or to its mirror), and stating that its facing is not read, while the cut and its bytes remain what the search alone makes.
- `GUARD` and `TOL` are pixels of the frame, not shares of the body. The plain walker drawn 1.5, 2 and 3 times as large, with the 3 by 12 px column drawn as many times as large beside its head in the still (under a hundredth of the body at every size): the column moves the head's centre 1.8, 2.7 and 4.0 px, and the walk reads no turn, least +0.78, +0.44 and +0.31 (without the column +0.85, +0.92 and +0.94); turning over frames 30 to 40, each reads its turn over frames 32 to 38. A larger body's margin narrows.
- Over 447 synthetic turns of 6 to 12 frames (from frames 4 to 62; on ones, on twos, and after an 8-frame lead-in), `torso-side-v2` reads every frame `torso-side-v1` reads as turned, over the same frames or more, and 14 turns that `torso-side-v1` does not read; 54 clips read other frames than under `torso-side-v1`, and there is no clip where `torso-side-v1` reads a turn that `torso-side-v2` does not. Each of those 54 clips was cut by `video-loop` under `--body-turn warn` with each reading: 37 avoid their turn under both (verdict `avoided`); 13 where `torso-side-v1` reads no turn avoid the turn `torso-side-v2` reads; 3 are `clear` under both; 1 where `torso-side-v1` reads no turn is `clear` of the turn `torso-side-v2` reads. In none does the cut taken under `torso-side-v2` hold a frame `torso-side-v1` reads as turned that the cut under `torso-side-v1` leaves out, and no clip goes from `avoided` or `clear` to `kept`, `unfound` or `refused`.

Gates, all fail loud: no period (profile flat, below the recorded `periodicity_min`), loop seam ratio above `--seam-max` (2.0), GIF/WebP reopened and checked (frame count, `loop=0`, transparent corners, no RGB under alpha 0 in the WebP). On every walk or run loop that plays round, the step gate reads cells between drawings and refuses the loop when its largest step that is not a pixel's jitter, divided by the mean of the other steps, exceeds `STEP_RATIO_MAX` (2.5); a step is a pixel's jitter when aligning its drawing with the previous drawing takes at most `STEP_JITTER_PX` (1.5) pixels of the frames as filmed, measured by coverage so colour has no effect, and the step with that move taken back is within 2.5 of the mean. A fixed cut of a walk or run reads clip foot crossings to judge the cut, refusing an odd step count that would swap legs at the wrap. Both checks are lifted by `--seam-max` past 2.5; see `docs/loop-repair.md` section 3, "The step gate". Every walk or run loop gated by its seam ratio (not a pinned clip) is also refused when the top of its silhouette (the head, or a part held above it) jumps into the loop's first frame more than `--seam-pop-max` times its median step (default 10; `jolt.seam_pop.pop`, read on the loop as it plays); a top that cannot be read is not refused (`seam_pop_gate` says `unreadable`). `--seam-max` does not lift this gate; `--seam-pop-max off` turns it off, leaving a warning line as before; see `docs/loop-repair.md` section 3, "The seam pop gate".

See [loop repair](loop-repair.md).

**Held drawings: recorded on every loop.** A video model may film a walk at 12 drawings a second
inside a 24 fps clip, each drawing shown for two frames ("on twos", or three: "on threes"). Nothing is refused here: played at its own rate a held loop is ordinary
limited animation. It matters when `video-cycle-align` stretches it ([loop repair](loop-repair.md)
section 4, "Held drawings").

**Every cut's feet: recorded on every walk.** `video-loop` reads every cut of the cut's length the
clip holds (after any lead-in), each as the loop it would make, on the keyed frames of every walk or
run it cuts, and records it as `clip_feet` in `strip.json` and its report: `length`, `cuts`,
`same_foot` (how many step the same foot twice), `alternate`, `unread`, `seam_in_step` (cuts whose
seam falls inside a step, counted apart), and `same_foot_at` (the first clip frame of each that
does). This is recorded whatever the view; `video-cycle-align` reads it only where the view reads
the feet (back and back_diagonal; [loop repair](loop-repair.md) section 4, "Same foot twice").

### One-shot actions — `--cycle auto|periodic|one-shot`

A video model asked to jump "over and over" sometimes jumps once and stands for the
rest of the clip. That is not a period, and the periodicity gate says so. For states
that *are* single actions by nature (`jump`, `attack`, unknown states; never `walk`,
`run`, `idle`) `auto` then runs a second, different detector instead of failing.

The failover is explicit: `cycle.kind = "one-shot"` and `periodic_attempt` preserve
both decisions. The strip sidecar carries `kind` and `loop: false`, so scene loaders
play it once on a trigger. GIF/WebP previews still loop, and the same seam and
animation gates apply. `--cycle periodic` refuses a weak periodic candidate;
`--cycle one-shot` forces return detection. Stationary clips, jitter and actions
without an observed return fail loud. A walk or run refused by the
`--anchor motion-auto` search names the windows it measured and refused, best first
([loop review](loop-review.md), "The gait fallback"). `--cycle fixed --start N --length L` skips detection and cuts exactly
those frames — for a clip that holds too few repeats for the periodicity gate but whose
cycle is known. It is an explicit
instruction, not a failover: the report says `kind = "fixed"`, and the seam gate still
applies.

Every `video-loop` refusal that writes `status = "failed"` (selection, the motion anchor, the
jump repair, the jolt gate, the pin gate, the seam gate, a strip with no body to crop, and a
GIF or WebP that could not be written or failed its verification) now removes what an earlier
cut handed over in the output folder: `cycle/`, `<name>.strip.png`, `<name>.strip.json`,
`<name>.gif`, `<name>.webp`, and `cycle.source/` and `follow.source.png`, which a later
alignment and follow-through keep beside them. A cut the
refused run had already written (a strip with no body, the pin and seam gates, the GIF and
WebP) is kept as `cycle.refused/`, to look at. The next cut that passes removes it; a refusal that comes before
the cut removes an earlier one. An argument error touches nothing.

`--cycle pinned` is for a clip pinned to end on its first frame (`video --last-frame` set to
the start image). The cycle is every frame but the last, which re-renders the first, so the
wrap plays like the step into that last frame. Where the pinned frame redraws the first, `--wrap-blend auto` (the default) takes the first frame's drawing back over the last half second, a redraw only, recorded as `wrap_blend`. Passing `--wrap-blend off` cuts as filmed, but [the seam bridge](loop-repair.md#the-seam-bridge-a-loop-that-is-not-a-walk-whose-wrap-jumps) can still make frames round the wrap of a loop that is not a walk or run; for such a loop, pass `--repair off --wrap-blend off` for the earlier bytes. See [loop repair](loop-repair.md#6-the-redrawn-wrap-a-pinned-clip-whose-last-frame-the-model-drew-again) for the gating and blending rules.

`--cycle once --start S --length L` plays exactly those frames once, a move cut out of a longer motion that settles
on no pose ([root motion](#root-motion--a-cut-played-once-where-the-body-is-what-it-stands-on-its-air)).

A one-shot cut is accepted only where its two end frames return: the distance between the end frames is at most a quarter
of how far the body departs between them, bounded by `ONE_SHOT_RETURN_MAX` (0.25). Other existing rules then apply to what
lies between (contrast or moved mass over `ONE_SHOT_MIN_MOVED` 0.4, at least four active frames, a departure at least
three times the steps between frames, and rest frames on both sides). A jump whose body crosses the canvas lands where it
did not stand, so no two frames return and the one-shot cut refuses it. Where no pair returns, the refusal line names the
closest return: `the clip does not come back to a pose it left: the closest return, frames A-B, is R of its departure away, over 0.25 (moved M, need 0.4)`.
The report written with `--report` gains `cycle.closest_return` with keys `pair`, `ratio`, `seam`, `departure` and `moved`.

Such a jump is cut with `video-loop --cycle once --start S --length L`, starting from the closest return's frames: pass
`--start A` and `L` = B - A + 1, then move either end. Where a pair does return but what lies between is not an action
(too little contrast or moved, fewer than four active frames, or a departure under three times the steps between its
frames), or where no pair departs at all, the refusal gives the 3.12.0 line as before:
`the clip never leaves its rest pose and returns with observed endpoints (moved M, need 0.4)`.

`--cycle once-return` is for a clip pinned to end on its first frame that plays once (`video-set
--play once-return`, [play modes](#play-modes)). It keeps every frame, the last too, so the strip
starts and ends on the first pose and a game plays it once and goes on from the pose it started in.

Outputs:

- `cycle/frame-NNN.png` — the cycle frames, RGB under alpha 0 scrubbed, detached specks
  below 1 % of the body erased.
- `<name>.strip.png` + `<name>.strip.json` — a horizontal strip (union-cropped with 8 transparent
  columns on each side even where the subject reaches the frame's side edge, **no bottom
  pad** so feet meet the floor, bottom-aligned, ≤ 64 cells **and ≤ 32 000 px wide** because
  Chrome refuses images near 32 767 px — the cap is on pixels, so a 650 px cell allows 49
  cells and the meta says `subsampled`; ≤ 520 px tall) with `frames · w · h · body_h · delay_ms ·
  cycle_frames · cycle_seconds`. `body_h` is the **standing height** — the tallest frame whose feet touch the floor. Scale a jump strip — whose cells include
  air room — by `body_h`, not `h`, and it reads the same size as a walk strip;
  `--body-height N` does that scaling in the pipeline so every state comes out at the same
  character size (`--strip-height` stays the cap). `delay_ms = cycle_seconds / frames`, so a
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
  GIF and WebP are cut from these cells. The function lives in `sprite_gen/util/resample.py`
  and is the resample of every place that scaled a keyed picture with LANCZOS: a row frame fitted to its cell and the twin
  beside a pixel-unfake frame (`extract`), a sliced sheet's figures (`slice-sheet`) and a
  scene's layers (`scene-render`) go through it too. Its twin for an affine map,
  `transform_cell`, does the same for the places that moved, scaled, rotated or sheared a keyed
  picture with BICUBIC: the gait fallback's scaled-back frames (`video-loop`, "The gait
  fallback" in [loop-review.md](loop-review.md)) and a curated transform on a smooth row
  (`curation.apply_transform`). Coverage keeps BICUBIC's edge, held to the coverage of the
  2 x 2 source pixels the colour mixes from, and colour is a BILINEAR mix of premultiplied
  colour. A pixel row's curated transform samples NEAREST
  and is untouched. The scene GIF's ffmpeg downscale stays LANCZOS: it scales opaque composited
  frames, where nothing is divided by a low coverage.
- `<name>.gif` — `n_out` frames evenly across the cycle, 1-bit alpha, disposal 2, `loop=0`. `--n-out` and
  `--gif-fps` still override; `--strip-height` caps the cell/strip/GIF height. Scaling up
  requires an explicit `--body-height` target and still respects that cap.
- `<name>.webp` — same frames, lossless, `img2webp -exact`.

### Root motion — a cut played once: where the body is, what it stands on, its air

A cut played once with a body (`once`, `once-return`, `one-shot`: a jump, a vault, a strike; not an effect's
lifetime) carries its root motion in the strip sidecar, `root_motion`, by default. The cells are the cut's as
before (`sprite_gen/video/root_motion.py`). Everything is read on the strip's cells, at their times as they play:

- `root` — each cell's root, in cell px from the cell's top-left: the subject's mass centre (the mean of its pixels,
  alpha over 0, as `inspect-motion` reads a frame's `centroid_px`). **A game draws cell k with that point on the
  body's place.** Moving the body by the roots' steps plays the cut as filmed; moving it by the game's own physics
  plays the same cells on the game's path. The cells keep the clip's pixels (a jump rises inside its cell), so a game
  that draws every cell at one place gets the motion as filmed without reading the key.
- `ground_y` — the floor's row, cell px: the lowest row any frame of the clip reaches. A cell stands on it where its
  lowest body row is within 4 source px of it (`nonloop.on_floor`, the frames the strip's standing height reads).
- `contacts` — each run of cells on the ground (`by: ground`) or in a contact the caller names over frames of the
  clip, in `inspect-motion`'s spans (`--contact hand=12:20`): a hand on a wall the clip does not draw, which no floor
  shows.
- `air` — each run of cells touching nothing: `takeoff` and `landing` (the cells before and after), `seconds` between
  them, `apex_px` (how far the root rises over the take-off's, cell px) and `apex_cell`. An air whose take-off or
  landing is not in the cut has `seconds: null`.
- With `--meters-per-px M` (the metres a pixel of the clip as filmed holds: the standing height in metres over the
  strip's `body_src_h`): `meters_per_px` (a cell px's), `root_m` (each root from the first cell's, metres, up
  positive) and each air's `apex_m`.

Tell the person each air in one line, its seconds and its apex (`air 0.42 s, apex 0.21 m`: the summary's `air`, a
`video-set` item's `loop.air`).

The root is the mass centre (the centre of mass), not a body part: in the air it is the point that falls on a parabola
under gravity whatever the arms and legs do. On a synthetic jump whose mass centre flies under a known gravity it is
read within 0.46 px (0.47 % of the body's standing height, `tests/video/test_root_motion.py`). As the legs tuck and
stretch it moves inside the body, by several percent of the body's height on a hard tuck, so a game that pins a body
part to its path reads that part itself.

**A move cut out of a longer motion — `--cycle once --start S --length L`.** A vault in a run settles on no pose,
so `once` finds no end and `one-shot` no return. Given both, `once` plays exactly those frames of the clip once
(`terminal.kind: given`), its end pose not read.

**The air given its own length, gravity and speed — `--air-retime gravity=G,air=T,speed=V`.** Each air with its
take-off and its landing in the cut is given `T` seconds, a gravity of `G` m/s² and a speed along of `V` m/s, any of
them, each the clip's own where not given (its gravity read off the root's rise over the line from take-off to
landing). `G` and `V` need `--meters-per-px`. The cut is made again at its rate (`--out-fps`, or the clip's): the
cells up to the take-off and from the landing at the clip's pace, the air's spread evenly over its new seconds, each
showing the clip's pose at the same share of its air, made with RIFE between the two frames it falls between where
it falls between them (`held_fill.at_times`, the frames between [the held cells](loop-repair.md) get), never a
frame shown again. Each air cell is then moved whole, by whole pixels, until its root stands on the parabola from the
take-off's root to the landing's; the landing and every cell after it are moved along by as much further as the air
now carries the body, so a planted foot stays planted. The take-off, the landing and the cells before the take-off are
the frames as filmed. The strip sidecar's `air_retime` says what was asked and, for each air, `seconds_before` and
`seconds`, `apex_before_px` and `apex_px` (`apex_m`), the gravity and speed used, `moved_along_px`; and the cells made,
the cells shown again, the poses over the cells; `root_motion` is read on the retimed cells. A cut with no such air,
or a loop, is cut as filmed and says why. With no RIFE installed and a frame to make, the air is cut as filmed and a
warning names the motion, its air cells and the install line; an air given its own seconds at the clip's rate (every
cell a frame) needs none. `video-set --air-retime --meters-per-px` hands both to every cut.

A vault cut out of a run, its hand on a wall the clip does not draw: `--cycle once --start 10 --length 40
--contact hand=12:20 --meters-per-px 0.005 --air-retime gravity=9.81,air=0.6,speed=4` gives the flight from the
hand leaving the wall to the landing 0.6 s at 9.81 m/s² and 4 m/s, its seconds held to whole cells (14 cells at 24
fps, 0.583 s), and says its seconds and apex.

What it takes from sprite-gen, and what it writes new:

| Question | Answered by |
|---|---|
| Where is the body in a frame | `qa.motion.centroid`, `inspect-motion`'s `centroid_px`, taken out of `analyze_motion` |
| Does a frame stand on the floor | `nonloop.on_floor`, the strip's standing-height rule, taken out of `loop.build_strip` |
| Which frames does a named contact span | `qa.motion.contact_spans`, `inspect-motion --contacts` |
| A cell at a time between two frames, judged, never a frame shown again | `held_fill.at_times`, taken out of `held_fill.upsample`, with RIFE located once a frame is to be made (`loop._located`) |
| The poses over the cells | `held_fill.same_steps`, `held_fill.reading` |
| The air, its seconds and apex; the clip's gravity; the cells' times and places on the parabola | new (`root_motion.read`, `retime`): no engine function read an air or a gravity |

A jump in place given `speed=` goes the `--facing` way. A landing on something higher than the clip's floor (a box,
a ledge) reads as air until the body comes down to the floor: name it with `--contact`.

## 5. Set — the batch

`sprite-gen video-set --base side=side.png --base front=front.png --states idle,walk,run,jump,attack --out-dir set/`
runs canvas → video → frames → loop for every (direction, state). The xAI team quota
is **2 requests per second**: starts are
staggered (`--start-gap 2`); each clip runs once. Clip length
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
image generation each, `--still-provider` picks the provider; see §2). See [loop repair](loop-repair.md) section 4.

### Play modes

`--play` gives how a motion plays as a value of the request, and `--motion` gives the
motion's own words, so a motion the engine has no state for — a punch, typing at a desk, a block — is
filmed under a name of your own. The modes and the presets are `sprite_gen/video/play.py`.

```bash
sprite-gen video-set --base side=side.png --base front=front.png --states idle,punch,type_at_desk,slash \
  --play punch=once-return --motion "punch=It throws one straight punch with its front fist and pulls it back." \
  --play type_at_desk=loop --motion "type_at_desk=It sits at a desk and types on a keyboard." \
  --play slash=lifetime --motion "slash=A white crescent arcs from left to right." --out-dir set/
```

| `--play` | the clip | the prompt asks for | the cut (`video-loop --cycle`) | `strip.json` |
|---|---|---|---|---|
| `loop` | as the state is filmed: an idle or attack pinned to its first frame, a walk or run pinned in a diagonal view, any other unpinned | an evenly paced repeat, or the return to the first pose where pinned | `pinned` for an idle or attack, otherwise `periodic` | `loop: true` |
| `once-return` | pinned to end on its first frame | one action, then the first pose again (after your text: `It does this once, then returns to the exact pose of the first frame.`) | `once-return`: the whole clip, its last frame too, gated by the pin | `loop: false`, `hold_last: false`, `returns: true`; the first and last cells are the first pose |
| `once-hold` | unpinned (`--end-frame ITEM=PATH` pins a terminal pose) | one action, its last pose held still for 0.4 s | `once`: the first settled pose whose drawing is not its first, a change inside an unchanged body outline included (a terminal state's by its own body geometry) | `loop: false`, `hold_last: true` |
| `lifetime` | framed as an effect and pinned to an empty black frame | an effect that appears and is gone | `lifetime` | `loop: false`, `blend: add` |

`STATE=…` applies to every view of that state and `ITEM=…` (`side-punch=…`) to one item, whose own
value wins over its state's.

Named states are presets. Without `--play` every state is filmed, prompted and cut: `effect`
as a lifetime, the six terminal states (`death`, `fall`, `land`, `cast`, `hurt_hold`, `transform`) as a
once-hold and every other state as a loop, each with its own sentence, canvas row and cut. A state of one nature plays only that mode — `effect`
a lifetime, a terminal state a once-hold, a cyclic state (`idle`, `walk`, `run`, `talk`, `blink`,
`breathe`) a loop — and another motion is filmed under a name of its own. A named state's sentence is
used in its preset mode, and the attack's (one strike, then a recovery to the exact starting stance) in
once-return as well: the same prompt and the same pinned clip, cut whole and played once. In any other
mode, and for a word the engine does not name, the mode's own sentence names the motion (`performs the
'punch' action once in place, then returns to the exact starting stance.`) unless `--motion` gives one.

A mode the request names is the cut's: a `loop` asked for is searched as a loop (`periodic`) and
refused when it does not repeat, never failed over to a single play the way a preset's `auto` cut of a
`jump` or of an unnamed state may be ([one-shot actions](#one-shot-actions----cycle-autoperiodicone-shot)).
The canvas row is the state's (a motion of your own films on the square default; `--shape` widens
every canvas of the set), except that a lifetime is framed as an effect whatever its name.

`video-prompt --play MODE` does the same for a clip made elsewhere: the record's `play`, `last_frame`,
`cycle` and commands follow the mode. A source with no end frame films a once-return only with
`--unpinned`, as a searched return (`--cycle one-shot`: rest, action, rest; `loop: false`), and
refuses a lifetime.

Not measured on a clip model: the sentences for a motion of your own (`PLAY_TEXT`, `RETURN_TEXT`).
The once-return template is the one an attack is filmed with (`ACTION_COMMON_TEXT`).

## 6. The Sway layer — `video-sway`

A clip model draws a walk's body moving, but a soft part hanging off it (a chest, a belly, a pouch on a strap) mostly moves with the body as one piece, even when the prompt asks it to bounce. The Sway layer puts that sway back on a cut loop: a soft part of a looping body sways with the body's own motion, moving an ellipse of the strip as a damped mass on the body's bob, after `video-cycle-align`.

`video-sway` replaces `video-follow` (in Python: `sprite_gen.video.sway`, `sway_loop`, `sway_offsets`). The old names (`video-follow`, `video-follow-inspect`, `sprite_gen.video.follow`, `follow_loop`, `follow_offsets`, `sprite_gen.video.follow_inspect`) still run, produce the same files, and print their old name in messages. The loop's record of a sway is read under the key `sway` or `follow`. This release still writes it under `follow`, the strip as cut is still `follow.source.png`, and inspection still writes `follow-inspect.json`, `follow-inspect.source.png`, and `follow-inspect.moved.png` with the same keys, so tools that read those names keep working. Moving written names to `sway` is a later change made together with the tools that read them.

```bash
sprite-gen video-sway --loop-dir set/front-walk/loop --region 136,164,60,50 [--region ...] \
  [--gain 2.5] [--on-fold refuse|lower] [--freq 2.4] [--zeta 0.6] [--board follow.png] \
  [--read-on <sha256> [--read-on-cut STRIP]] [--stretch-floor D] [--gain-across G] \
  [--band PX|auto] [--band-margin PX|auto] [--band-up FLAT[,PX|auto]] [--hold-mask MASK.png|DIR] \
  [--region-mask MASK.png|DIR [--region-rim SCALE] [--region-shrink PX] [--region-crease] \
   [--region-over [--region-over-solid] [--region-rim-round] [--region-membrane N [--region-membrane-unpin] \
    [--region-clear-alpha A] [--region-floor-beside N] [--region-own-outline G]] [--hold-mask MASK.png|DIR]] \
   [--region-shear S]]
sprite-gen video-sway --frames set/front-idle/loop/cycle --out swayed --region cx,cy,rx,ry[,flat] [--fps F] \
  [--breathe breathed/breathe.json [--lag 0.15]] [--reference FRAME.png] [--gain G] [--gain-across G] [--on-fold ...] [--stretch-floor D] \
  [--band PX|auto] [--band-margin PX|auto] [--band-up FLAT[,PX|auto]] [--hold-mask MASK.png|DIR] \
  [--region-mask MASK.png|DIR [--region-rim SCALE] [--region-shrink PX] [--region-crease] \
   [--region-over [--region-over-solid] [--region-rim-round] [--region-membrane N [--region-membrane-unpin] \
    [--region-clear-alpha A] [--region-floor-beside N] [--region-own-outline G]] [--hold-mask MASK.png|DIR]] \
   [--region-shear S]]
```

- **The region** is an ellipse over the part in the strip's first cell, in cell pixels
  (`cx,cy,rx,ry`; repeatable). It is carried with the body's bob from cell to cell. Somebody has
  to say where it is — the engine does not find it: look at the first cell (or ask a vision model
  for the four numbers once per direction; a mirrored direction takes the mirror's region).
- **The motion** is the body's own: how far each cell's body lies from the first cell's, up and
  down and side to side. `--freq` (2.4 Hz) and `--zeta`
  (0.6) set the part; `--gain` (2.5) scales the physical answer and nothing else. On a front
  walk with a 12 px bob in a 600 px body that is a move of about ±5 px; `--gain 1` is the mass as
  measured, `0` gives back the strip as it was.
- **Only the region moves.** Inside it every pixel is moved by the offset times a weight that is 1
  at the centre and 0 at the rim (cos²), sampled as premultiplied bilinear colour; outside it no
  pixel changes. A move so large that the weight's slope folds the picture over
  (offset × π / (2 · radius) ≥ 1) is refused: lower `--gain` or give the region larger radii.
  A cell in which every region's offset is 0 is copied byte for byte, including colour under clear pixels; this holds for every sway, including `--gain 0`. A move clears that colour.
- **`--on-fold lower`** lowers the gain for you instead of refusing, region by region. No region goes under 1, the
  mass as measured: one that would have to is **held** — it does not move at all, so a
  follow-through is never quietly weaker than the motion it answers — and named, on stderr and in
  the record. The default stays `refuse`; a strip that does not fold is the same under either.
- **What it writes**: the strip, GIF and WebP over the loop's own (both animations re-opened and
  checked as `video-loop` checks them).
  `cycle/` is left as cut. The strip as it was is kept as `follow.source.png`; running
  `video-sway` again reads from it, so a second run never moves a moved strip. `--board` writes the cells before and after where the part
  sits lowest and highest, on white.
- **Order**: after `video-cycle-align`. An alignment rebuilds the strip from the cut, removes
  `follow.source.png` and the `follow` record, and says so (`follow_cleared` in its loop row); a
  new cut with `video-loop` removes them too. Run `video-sway` again after either — with
  `--read-on` where the regions were read on the cut (below).
- A one-shot (`kind: one-shot`) is refused: the follow-through is a loop's steady state.

#### Regions read on the cut, followed on the aligned loop — `--read-on`

The regions are where somebody looked: in the first cell of the strip they were read on. An
alignment turns a loop to start on a foot strike, so its first cell is another; given the same
regions, `video-sway` reads the motion again from that cell 0 and the regions land on another
place of the body, and a stretched loop moves them by a motion read anew. `--read-on <sha256>`
names the strip they were read on — its file's sha256, as cut, before any follow-through (64
lowercase hexadecimal digits; anything else is refused before anything is read):

- **`recut`**: the regions were read on another cut of this loop's own source clip, given by
  `--read-on-cut STRIP`. It pairs the cuts when both records name the same source in `source_cut`
  (its `source`) and this loop's cut holds the source frame of the cut's cell 0, at its cell j.
  The report `video-loop` wrote beside a cut is no longer looked for or read for `--read-on-cut`,
  and need not be beside the strip. Each cut is now read by its record of its cells alone.
  `video-loop` writes it in the strip's record (`<name>.strip.json`, not the report) under
  `cell_record`, bound to the strip by `cell_record.strip_sha256`, the sha256 of the strip PNG it
  describes. `cell_record.cells` holds one entry per cell of the strip, in order: `source_frame`,
  the source frame the cell is (a whole number); `anchor_move_px`, where the cut's anchor moved
  that frame before the crop, across and down, in whole px (nothing (`[0, 0]`) at `--anchor none`,
  round(`wrap_dx_px` x k / L) across at `--anchor body` for frame k of a cut L frames long, and the
  left and top of `padding_ltrb` plus that frame's entry of `shifts_xy` at `--anchor motion` and
  `--anchor motion-auto`); `scale`, the factor each step that holds a clip's frames to one size
  scaled the frame by about its feet before the cut, `size_hold` (the size hold) and `gait_fallback`
  (the gait fallback), 1 where the step did not hold the clip's frames; and `as_filmed`, true where
  the cell is that frame as filmed but for its anchor's move. Its pixels at the end of the cut are
  its pixels anchored, before any repair (the receipt `video-loop` writes in its report,
  `source_projection.before_repair_pixels_sha256`), and no step held the clip's frames to one size.
  A cell the jump repair, the seam bridge, the overlapped wrap or the wrap blend made again, gave
  way or redrew is not as filmed by its pixels; no list of those steps is read. In a cut at
  `--out-fps` (cells each made at a time of their own, more cells than the cut has frames),
  `cell_record` still holds one entry per cell, and now reads a retimed cut the same way as a cut
  at `--out-fps`: each cell's `source_frame` is the frame the cell is, and for a cell made between
  two frames the cut's frame at or before its time (the frame the seam reads it as), with that
  frame's `anchor_move_px` and `scale`. `as_filmed` is true only where the cell's pixels are that
  frame's pixels anchored before any repair, so a cell that repeats a frame unchanged is as filmed,
  and a cell made between two frames, a cell moved onto the parabola or along, and every cell of a
  cut the retime laid on a larger canvas are not as filmed, and `video-sway --read-on-cut` does not
  pair them. The cells before the take-off that the retime neither moved nor laid on a larger canvas
  stay as filmed. `video-loop` now
  writes that receipt (`source_projection`) in the report of every cut but `--anchor feet` and a
  lifetime cut, so also at `--anchor motion` and `motion-auto` (`recipe`
  `motion-anchor-correction-v1` there). On a loop aligned from its cut (`cycle_align`), the aligned
  strip is another strip, so `video-cycle-align` does not carry `cell_record` over to it. Its first
  alignment keeps the cut's own strip record, byte for byte, beside the cut's frames as filmed
  (`cycle.source/<name>.strip.json`), and later alignments keep it. There the cut's record is read
  from that file, bound to the strip the alignment's origin names
  (`cycle_align.origin.strip_sha256`); the regions sit on the aligned cell that is cut cell j and
  are carried as `transported` carries them. On an unaligned loop they are read on cell j and
  carried from there. The regions' centres, a `--region-mask` PNG, and a single `--hold-mask` PNG
  are moved pixel for pixel onto cell j by the whole pixel shift the two crops and cell sizes set
  that frame apart, each crop taken on the frame as filmed: its `source_rect` less the cell's
  `anchor_move_px`. A pixel may lie at most `RECUT_DRIFT` (1.0 px) off that shift anywhere in the
  cell. The pair's policy is now `same-clip/source-frame/whole-px-shift/2`. Then the two cells are
  compared: the cut's cell 0 laid on this loop's cell j at the shift must be the same pixels (RGBA)
  wherever they overlap. A record changed to call a cell the frame as filmed is held to that
  frame's pixels this way. Before crops are read, the run is refused no-match and the loop left as
  it was, saying which and naming the field: a cut with no `cell_record` (a cut `video-loop` made
  before it wrote one: every cut made by 3.5.0 or before), on the cut `--read-on-cut` names or on
  this loop's ("it holds no record of them (cell_record), as a cut video-loop made before it wrote
  one does"); a record of another strip (`cell_record.strip_sha256` is not the strip's sha256); a
  record that does not give its cells as `video-loop` writes it, naming the field, for instance
  `cell_record.cells[2].source_frame` (`cell_record` not an object, `cells` not one entry per cell of
  the strip, a cell's entry not an object, `source_frame` not a whole number, `anchor_move_px` not
  two whole px, a `scale` factor missing or not a positive number, `as_filmed` not true or false);
  a loop aligned from its cut whose cut's record is not kept beside its frames as filmed
  (`cycle.source/<name>.strip.json`); a record that names no clip it is cut from (`source_cut`),
  two cuts of different clips, or a source frame this loop's cut does not hold. Every cell's entry
  is read this way, not only the cell paired. Refusal no-match also occurs if either cell paired was
  scaled by a hold (its `scale.size_hold` or `scale.gait_fallback` not 1: no whole px shift undoes a
  scale), or is not as filmed (`as_filmed` false). Only the two cells paired are read for this; a
  cell elsewhere in either cut that a hold scaled or a repair made leaves the pair as it is.
  Refusal no-match also occurs if the two cells are not the same pixels where they overlap at the
  shift, saying how many of how many px differ, or if the strip `--read-on-cut` names is a loop
  aligned from its cut (`cycle_align`), whose cells are not the cut's frames in order. A run is
  refused uncertain if a cut records no crop, if crops, scales and anchors set a pixel more than 1 px
  off one whole shift, or if an aligned loop holds no cell for cut cell j. `STRIP` is that cut's
  strip PNG, whose bytes must have the sha256 `--read-on` names, with its record beside it as
  `video-loop` writes one using the same file name ending `.json` instead of `.png`, refusing with
  "--read-on-cut names the strip --read-on names by its sha256; it needs --read-on" when `--read-on`
  is not given and "wants the strip's PNG and its record beside it" when either file is missing.
  Masks are moved by the shift alone with no polygon grown and no scaling, while a mask pixel that
  would leave the cell refuses the run, and a folder of masks (one per cell) is refused under
  `--read-on-cut` with "regions read on another cut (--read-on-cut) are drawn on its cell 0 alone:
  give one PNG". `video-sway` takes `--read-on-cut` on `--loop-dir` only, and `video-sway-inspect`
  does not take it.
- **`no-match`**, refused: neither — another clip, another cut no record pairs with this one
  (`--read-on-cut` names the cut that one does), a strip nothing here was cut or aligned from.
  Locate the regions again on this strip.

#### A least area each move leaves — `--stretch-floor D`

The fold rule keeps a region from folding, and so lets the region's steepest pixel take nearly
nothing of the source: a line there is drawn across many pixels. `--stretch-floor D` (0 to under
1) asks every region's move to leave each pixel at least D of its area. On a loop aligned from the
cut, give `--read-on` too: the floor is then read on the cut's motion, so a turned loop is lowered,
held or refused as the cut is.

#### A cycle's frames at their own size: `--frames <folder> --out <new folder>`

A loop's strip holds at most 64 cells and 32,000 px across, so a cycle of large frames is cut down to fit it. The strip stays the delivery format; `video-sway --frames <folder> --out <new folder> [--fps F]` sways the full cycle before that cut.

- **The frames** are PNGs in name order, all one size, from a `video-loop` `cycle/` folder or the output of `sprite-gen breathe`. Regions are given in frame pixels. `--frames` and `--loop-dir` are alternatives, and one is required.
- **The output** contains the moved frames under their original names, with `sway.json` beside them. The output must be new or empty, and may not be equal to, inside, or containing the frames folder (compared as real paths); unequal frame sizes are refused before anything is made. A failed run leaves nothing, and the frames folder is only read.
- **The time** comes from `--fps` or, for a whole `video-loop` `cycle/`, from the loop's strip meta. A conflicting `--fps` is refused. Other frames folders need `--fps` unless `--breathe` supplies the drive. `--read-on` and `--read-on-cut` are refused with `--frames`; `--frames` requires `--out`; `--out` and `--fps` are refused with `--loop-dir`.
- **The record** in `sway.json` has `frames`, `frame_ms` (null on the breath), and the same record as the loop's `follow`, with `source` naming the frames folder. Body motion is read at the frames' full size: frames reduced first give other offsets, and on large frames reading can take minutes. The old name `video-follow --frames ...` writes the same bytes.

#### On the breath: `--breathe <breathe.json>`

On a breathing idle the body moves only a pixel or two while the head stays still; the outline turns that into whole-pixel steps, and the damped mass answers those steps, so the part barely moves at its default settings or sways to the steps when pushed further. `--breathe <breathe.json>` drives the part from the Breathe layer's breath instead.

- **The record** is the `breathe.json` written beside breathed frames; it supplies config, phases and `resolved.anatomy`. Its config's `inhale` is the in-breath share; a record without it reads as the default 0.40. Its config's `start_cell` and `loop_cells` say which cells of which loop the frames are; a record without them reads as the loop itself from its first cell. Only `mode: smooth` is accepted; a pixel breath is refused because its travelling wave moves the first cell. The config must pass the Breathe layer's checks, the phases are recomputed at the record's share, start cell and loop cells and must match the record (so `breath_peaks` phases written at another share are accepted when they match), and `frames`, the phase count and the swayed cell count must agree. A hand-written record is checked the same way and can state the in-breath share of a breath made elsewhere.
- **The drive** uses the Breathe layer's own breath curve at the record's in-breath share. The part follows the chest's rise late, by `--lag` of a breath, and is moved by `gain` times the gap between the chest's lift and that late lift, against the chest's rise in the record's `chest_dir`; 0 points straight up, so that offset points straight down.
- **The rest**: the first cell, rests between breaths and the close have zero offset. Each cell's ellipse is still carried by the body's outline.
- **The lag** is a share of one breath, above 0 and below `1/π`; default 0.15. A longer lag reaches further and peaks later; reach depends on lag rather than breath length.
- **A stretch of a loop** is a breathed row whose record carries `loop_cells` and `start_cell`: a way out of the loop or a way in, breathed on the loop's beat. The stretch's cell i is offset exactly as the loop's cell (start_cell + i) mod loop_cells is, from the stretch's first cell on. The fold rule (`--on-fold`) and `--stretch-floor` read the loop's whole breath, every phase of the loop, not only the phases the stretch carries, so a stretch moves by the same gain as its loop and the seams between them do not jump. The record's `reach_px`, its `fold` block and the floor's `least_cell` are then the loop's; `least_cell` is a cell of the loop.
- **The reference frame**: `--reference FRAME.png` names the frame the regions were read on when it is not the first of the frames, normally the loop set's first cell; the regions are carried from it, as on the loop's own cells. `body_px` and `body_worn_px` come from it, `body_bob_px` spans it too, and `sway.json` records `reference` with the file's `path` and `sha256`. A frame of another size than the frames is refused. Without it the regions are read on the first frame.
- **The settings and record** use a default `--gain` of 1 on the breath, against 2.5 on body motion. `--freq` and `--zeta` are refused with `--breathe`; `--fps` is refused with `--breathe --frames`; `--lag` requires `--breathe`; `--reference` is refused with `--loop-dir`. The record's `drive` carries `kind: breath`, `breathe_sha256`, `lag`, `lift_px`, `heading_deg`, `inhale`, `start_cell`, `loop_cells`, and `breaths` or `breath_peaks` in place of `freq_hz`, `zeta` and `harmonics`. Its way is `common-lay/breath-late/cos2-bilinear/1`. `--loop-dir` also accepts this drive when its strip has one cell per phase.

```bash
sprite-gen breathe <cycle> <breathed> --mode smooth --breaths 2
sprite-gen video-sway --frames <breathed> --out <swayed> --breathe <breathed>/breathe.json --region <cx,cy,rx,ry>
sprite-gen breathe way-out/ way-out-breathed/ --mode smooth --breaths 2 --loop-cells 96 --start-cell 40 --reference loop/frame-000.png
sprite-gen video-sway --frames way-out-breathed --out way-out-swayed --breathe way-out-breathed/breathe.json --reference loop/frame-000.png --region <cx,cy,rx,ry>
```

#### A region moved whole, and across apart from down: `cx,cy,rx,ry,flat`, `--gain-across G`

`--region cx,cy,rx,ry[,flat]` gives a region an optional fifth number, `flat`, from 0 to 0.9: the share of the way from centre to rim that moves in full; over the rest of the way the weight falls from 1 to 0 by cos². A large region weighted by cos² alone presses its outer part inward; one flat region can move the part whole instead of laying many small circles over it.

The fold rule and `--stretch-floor` read the radius left after the flat centre, the smaller radius x `(1 - flat)`, and the record's `fold.regions[i].radius_px` is that reduced radius. A `flat` of 0, or one left out, gives the four-number region's strip, record and inspection byte for byte. Values outside the range are refused.

`--gain-across G` sets the gain across on its own, and `--gain` then sets the gain down; without it, across uses `--gain`. When `--on-fold lower` or the floor lowers a region's gain, its across move falls in the same proportion. `--gain 0` with a non-zero `--gain-across` is refused. The record carries `gain_across`, the across gain the strip's `gain` moved by, and carries `gain_across_requested` only when given.

#### A region moved as one mass: `--band PX|auto`, `--band-margin`, `--band-up`

Without `--band`, a region's weight is 1 at the ellipse's centre and falls to 0 at its rim by cos². On a large soft part (a chest), only the middle moves fully; the rest of the part is pressed in or stretched. The fifth region parameter `flat` moves a share of the way from the centre in full, but inside the same ellipse, so the part's own pixels still reach the falling slope when they move, and the fold rule then reads a small radius.

`--band PX|auto` moves each region as one mass. It is off by default; when it is not given, nothing of it is read or written, and the strip, the record, and everything else remain byte for byte what they were. The weight is 1 over the region's ellipse grown by the margin on every side (the flat part), and falls from 1 to 0 by cos² over a band of `PX` px past it, measured along the ray from the region's centre. The outer ellipse's radii are the flat part's radii plus the band. Past the band the weight is 0. The whole part moves by the cell's offset, and only what is around it gives. Outside every region's band no pixel changes, byte for byte, including the colour under clear pixels.

`--band-margin PX|auto` (default `auto`) controls how far past the ellipse the weight stays 1 in every direction. In `auto` mode, the margin is `ceil(reach) + 1`, which is the move's reach plus one pixel: a pixel of the part lands where the weight is still 1 only if the flat part reaches as far as the part moves. The move's reach at the requested gain (`--gain`) is read first, and the band and margin are then read off it in two stages. `--band auto` is `ceil(reach x pi / 2) + 1`, the narrowest band that the move does not fold.

The fold rule reads each region by its narrowest band instead of its radius: a move folds where `reach x pi / (2 x band) >= 1`. When a move folds, `--on-fold lower` lowers the gain as before, and `--on-fold refuse` refuses with the message "folds a region's band of N px over itself; lower --gain or give a wider --band". The record's `fold.regions[i].radius_px` is the band. If a move passes the fold rule and still folds the picture in some cell (`jacobian_min` 0 or under), it is refused as the engine's fault and nothing is written.

Where the flat ellipse and the outer one differ in shape (above the centre under `--band-up`, where the flat part is short and the band long), the band measured across its ring is narrower than its px, so the weight falls steeper than the fold rule reads. After the fold rule, each region's steepest slope along the move is read over every pixel of every cell: per unit of gain, the most that the move at gain `1`, dotted with the weight's gradient, reaches. A pixel moved by gain `g` keeps `1 - g x steepest` of its area, so the region folds where `g x steepest >= 1`. `--on-fold refuse` then refuses, naming the cell and the area a pixel keeps at the gain asked, and says to lower `--gain` or give a wider `--band` (or `--band-up`, when given). `--on-fold lower` lowers that region's gain to the largest whole number of `0.01` steps whose steepest slope stays under `1`; a region that would go under gain `1` is held, and the run is refused only when every region is held. The record's `follow.band.regions[i].steepest` holds `per_gain`, `cell` and `ratio` (the gain times `per_gain`, under `1` on any written strip). A move that passes the fold rule and still folds the picture in some cell (`jacobian_min` 0 or under) is refused as the engine's fault and nothing is written, staying as the last check after the steepest slope.

`--band-up FLAT[,PX|auto]` gives the part above the region's centre its own shape. The flat part stops `FLAT` (over 0, at most 1) of the region's vertical radius above the centre, and above that the weight falls over `PX` px. When omitted or set to `auto`, `PX` is the band, or as far as `HANG_PAST` (3 px) above the region's top, whichever is further: the whole ellipse moves at least some, and nothing above it is left pressed. The upper part gives and the rest moves whole (for a chest that hangs from the shoulders, the shoulders stay put). Without `--band-up`, the shape above is the same as below. `--band-margin` and `--band-up` need `--band`; alone they are refused.

With `--band`, each region is carried into each cell by its own pixels rather than by the lay of the body's outline. The solid pixels (alpha 128 or more) under the region's ellipse in the cell it was read on (the first cell; the reference frame with `--reference`; or under `--read-on` transported, the cut's first cell where the loop holds it) are searched in each cell in the loop's order from the cell the region was read on, within `CARRY_SEARCH` (9) whole pixels either way of where the cell before had the region's pixels (around the cell's own body lay only where the cell before was carried by the lay, or is the cell the region was read on; or, with `--reference`, for the first cell), by the least squared difference of premultiplied colour over those pixels, counting past the cell edge as clear. A parabola through the least difference and its neighbours across and down gives the subpixel position. Where that is uncertain (the least is on the 9 px search edge, tied with a place more than 1 px away, or the difference does not curve up across or down, such as unpatterned cloth), the region is carried by the body's lay in that cell. Each such cell is named in a warning line on stderr ending with the record key `follow.band.lay` on `--loop-dir` and `sway.json band.lay` on `--frames`, and the record's `carry_basis` reports `{"reading": "outline-lay", "region": "own-pixels"}`.

Combining `--band` with a region's fifth number `flat` (other than 0, which counts as four numbers and is accepted) or with `--stretch-floor` is refused with a reason and a non-zero exit; nothing is written. Out-of-range values (a band of 0 or less, a negative margin, a `FLAT` of 0 or over 1, or text that is not a number or `auto`) are refused by the command line with exit 2. Only four ledger counts warn: `band_on_regions_px`, `pressed_px`, `stretched_px` and `outside_moved_px`. For each that is not 0 in some cell, one warning line on stderr says in how many cells, up to how many px and in which cell (for `pressed_px` and `stretched_px` also the least or the most area and where), ending with the record key: `follow.band.ledger.<key>` on `--loop-dir`, `sway.json band.ledger.<key>` on `--frames`. `jacobian_min` gives no warning. The option works on `--loop-dir` and on `--frames ... --out ...` alike, and under `--read-on` where a turned loop carried from its cut is moved cell for cell as the cut is. The alias `video-follow` takes the same flags. `video-sway-inspect` does not take `--band`.

#### Pixels a band leaves as they are: `--hold-mask`

`--hold-mask MASK.png|DIR` sets pixels that the band moves round and leaves as they are, whatever part of the figure they belong to, such as an arm that hangs over a moving chest, or a belt or a dress below it. The band's move field itself is not changed. It works only with `--band`, or with `--region-mask` and `--region-over`, where it names the pixels the layer leaves out, only those the region mask does not cover (the region keeps those it covers and moves them), and on a band every pixel it names is held; without `--band` and without `--region-over`, it is refused with the message "--hold-mask keeps pixels as they are while a band moves its regions round them; it needs --band" (a non-zero exit, writing nothing, the loop left as it was). It is off by default; when it is not given, nothing of it is read or written, and strips, frames, and records remain byte for byte as with `--band` alone (and as before without `--band`).

The mask is a PNG drawn on the cell the regions were read on (the first cell; the reference frame with `--reference`; or under `--read-on`, the cut's first cell where the loop holds it), pixel for pixel the cell's size. A pixel is held where the PNG's alpha is 128 (`ALPHA_SOLID`) or more; where the PNG has no alpha, where its grey value is 128 or more. When given as one mask, it is split into its 8-connected pieces. Each piece is carried into every cell by its own solid pixels (alpha 128 or more under it), using the same search a band uses to carry a region (`carry_mask`, built out of the band's `carry_own`): in the loop's order from the cell it was drawn on, within `CARRY_SEARCH` (9) px of where the cell before had it, by the least squared difference of premultiplied colour, with a parabola for the subpixel position. Where that is uncertain (such as unpatterned cloth) or the piece covers no solid pixel, that piece is carried by the body's lay in that cell, and a warning line names it. The carried pieces are then grown by `HOLD_GROW` (3) px in 4-neighbour steps, what a carry may be off by where the part held changes its shape. Alternatively, `--hold-mask` takes a folder of PNGs, one per cell, in file-name order; each is held as given in its cell (no carry, no growth).

In each cell, three sets of pixels are kept byte for byte: the mask's pixels (as carried and grown, or as given); the soft edge, which is every pixel that is not opaque (alpha over 0 and under 255) within `edge` 4-neighbour steps of the mask, where `edge = ceil(reach) + HOLD_EDGE` and `HOLD_EDGE` is 2 px (reach is the band's move reach); and the air, which is every clear pixel (alpha 0) within the same distance. This also keeps the half-clear rim of a gap beside the mask, such as a notch at the waist.

Every other pixel is read as the band reads it (premultiplied bilinear, the same sampling), except that a read headed into what is kept stops short. Within a cell, every move is `s` times one direction (the groups' offsets are one motion times their gains). For each pixel, the code finds how far back along that direction the read can go before its four sampled pixels reach a kept pixel or one within `HOLD_MARGIN` (1) px of them (`free_back`, followed an eighth of a pixel at a time). The read goes back `f = min(s, h)` px, where `h` is `HOLD_SLOPE` (0.5) times that free distance, then taken at its least within `HOLD_ROUND` (1) px each way and averaged over as far, then lowered until no pixel is more than `HOLD_SLOPE` over any of its 8 neighbours (`read_share`). The share of the move rises by at most `HOLD_SLOPE` per pixel in any direction, and the gap the part leaves is drawn from the part itself, stretched in order. The read `p - f x o` (`o` the move's unit direction) takes `1 - d_o f` of the source's area: where `f = h` a read takes at least `1 - HOLD_SLOPE` of the source's area, which read between neighbours across and down, as the ledger reads it, is at least `1 - HOLD_SLOPE x (|ox| + |oy|)` (at least `1 - HOLD_SLOPE x sqrt(2)`, about 0.29), while where `f = s` (the whole move) a read takes the band move's own area, over 0 wherever the band does not fold; where `f` turns from the one to the other, the two mix between neighbours and the bound does not hold, and the ledger reads those pixels separately (`read_turn_folded_px`, `read_turn_least`). A read whose four pixels still touch a pixel it may not (a solid pixel may not read any kept pixel; a clear pixel may not read a held one) is read from the others where they carry half its weight or more ("renormalised"), or else walked back by 1/`HOLD_STEPS` (1/16) of its move at a time until they do ("walked"). Where every read takes its whole move and touches no kept pixel, the cell is the band's, byte for byte.

In strip metadata under `follow.hold` (and `hold` in `sway.json` on `--frames`), the record stores `policy` (`own-pixels-grown/kept-edge-air/read-short-sloped/1`), `asked` (`per_cell`, and `sha256`: the file's, or a list per file for a folder), `grow_px` (3; null for a folder), `edge_px`, `margin_px` (1), `slope` (0.5), `round_px` (1), and `steps` (16). It records `pieces` (each piece's `px` and `box` on the cell it was drawn on, empty for a folder), `carry` (`search_px` of 9, and per cell and piece `px`, `from` (`base`, `pixels` or `lay`), `why` for lay, `least`, and `curvature`, null for a folder), and `lay` (`{cell, piece, why}` for each piece carried by the lay). Its `ledger` lists per cell, with `<key>_max` values beside each count: `held_px`, `soft_px`, `air_px`, `short_px`, `renormalised_px`, `walked_px`, `read_folded_px` (opaque pixels, not kept and not beside a kept one, whose read takes 0 or less of the source's area, read off the move each read took, as `jacobian` reads it), `read_folded_edge_px` (the same beside a kept pixel, where the part moves under the mask and the read is meant to jump), `read_least` (the least area a read of the first kind takes, and where: [area, x, y]; null where nothing moves), `read_turn_folded_px`, and `read_turn_least` (the same for pixels of the first kind where the read turns from the whole move to the share round the hold, where the bound does not hold). Warning lines on stderr, one line each, report a piece carried by the lay (in how many cells and why, ending with `follow.hold.lay` on `--loop-dir` or `sway.json hold.lay` on `--frames`), and for `renormalised_px`, `walked_px`, and `read_folded_px` when not 0 in some cell (in how many cells, up to how many px and in which cell, ending with `follow.hold.ledger.<key>` on `--loop-dir` or `sway.json hold.ledger.<key>`). Command stdout JSON gains `hold` with `pieces` (count), `lay_cells`, and `read_folded_px_max`.

A run is refused with a non-zero exit, writing nothing and leaving the loop as it was, when `--hold-mask` is used without `--band` and without `--region-mask` ("--hold-mask keeps pixels as they are while a band moves its regions round them; it needs --band"), or with `--region-mask` without `--region-over` ("--region-mask holds every visible pixel outside the region already; --hold-mask is a band's, or with --region-over the pixels its layer leaves out"); when a mask is of another size than the cells ("--hold-mask is WxH, the cells are WxH: a mask is drawn on a cell, pixel for pixel"); when a mask holds no pixel ("holds no pixel (alpha 128 or more, or that light where it has no alpha)"); when a file is not read ("...: not read (...)"); when a folder count of PNGs does not match the count of cells ("--hold-mask holds N masks and there are M cells: a folder gives one mask per cell, in name order"); or when a folder has no PNG in it ("a folder of masks with no PNG in it"). The option works on `--loop-dir` and on `--frames ... --out ...` alike, and under `--read-on`. The alias `video-follow` takes the same flag. `video-sway-inspect` does not take `--hold-mask`.

#### A region given as a mask: `--region-mask`

With `--region` alone a region is an ellipse whose weight falls from its centre, and with `--band` an ellipse grown by a margin moves whole over a band of pixels outside it, so either way pixels near the part move with it: the belly or waist under a chest, an arm beside it. `--region-mask MASK.png|DIR` gives the region as a mask of the part's own pixels instead of the ellipse's weight: the part moves, and every visible pixel outside it and its soft edge keeps its bytes in every cell, at any gain. The ellipses (`--region`) are still required: they still say each region's gain (the fold rule's and `--on-fold`'s) and where `--band-up`'s shape sits. It is off by default; when it is not given nothing of it is read or written, and strips, frames and records are byte for byte what they were (with `--band`, with `--band` and `--hold-mask`, and with neither).

The mask is a PNG drawn on the cell the regions were read on (the first cell; the reference frame with `--reference`; or under `--read-on`, the cut's first cell where the loop holds it), pixel for pixel the cell's size. A pixel is in the mask where the PNG's alpha is 128 (`ALPHA_SOLID`) or more; where the PNG has no alpha, where its grey value is 128 or more (read exactly as `--hold-mask` reads its PNG). Alternatively, it takes a folder of PNGs, one per cell, in file-name order, where each is the region in its own cell as given (no carry). When `--region-crease` is passed (off by default), before it is worn in, the mask's lower edge is moved to the crease under it (`crease_of`): in each column of the mask's solid pixels whose lowest pixel has a solid pixel under it (the part lies on more of the figure there), the darkest solid pixel by luma (0.299 R + 0.587 G + 0.114 B) from `CREASE_UP` (5) rows above that lowest pixel to `CREASE_DOWN` (3) rows below it is found; the rows are then taken as the median over `CREASE_SPAN` (2) columns either way (5 columns), rounded down; the mask is cut under that row and filled down to it with the column's solid pixels, while a column whose lowest pixel has a clear pixel under it (the part's free edge, over the air) is left as it is. Under `--region-shrink PX` (default `SHRINK_DEFAULT`, 2; 0 keeps the mask as given; a whole number, 0 or more), the mask is worn in by PX 4-neighbour steps (a pixel stays where it and its 4 neighbours are all in the mask; past the cell's edge none is), so that a mask drawn a little large holds what lies just outside the part. Crease runs first, then shrink. With `--region-crease`, the mask ends on the crease before the wear and the wear (`--region-shrink`) wears the whole mask in, the crease row too, like every other edge. So the record's `mask.box` ends `--region-shrink` rows above the crease row (2 rows at the default). Under `--region-over`, the region reaches down to the crease again in every cell (see that subsection). One mask is carried into every cell by its own pixels (`carry_region`): the solid pixels (alpha 128 or more) of the mask as trimmed (before it is worn in) on the cell it was drawn on, matched by the band's search (`carry_match`: least squared difference of premultiplied colour within `CARRY_SEARCH`, 9 px, with a parabola for the subpixel position), in the loop's order from the cell it was drawn on. In each cell two guesses are searched round: first where the cell before had the region (from the cell it was drawn on, for the first one), second round the cell's own body lay (the outline's motion); of the guesses that settle (a sure match) the one with the lesser difference is taken (the cell before's on a tie), and the record says which ("before" or "lay"). Where neither settles (cloth with no pattern, the least on the search's edge), the cell keeps the carry of the cell before, never the lay alone, and the record and a warning line say which cell and why (for each guess, why it did not settle), because a part moves little from one cell to the next, and the lay can lie far off it where the outline changes. The worn-in mask is moved by that carry (read bilinear, the pixels it covers half or more of), and in each cell the region is those pixels that are solid in that cell.

In each cell, the pixels are partitioned into sets (`region_sets`). The region is the mask's pixels in that cell, as above. Its soft edge is every pixel not opaque (alpha over 0 and under 128) within `REGION_SOFT` (2) 4-neighbour steps of the region, not in it, and not beside (1 step) another solid pixel outside the region; it moves with the region. Held pixels are every other visible pixel (alpha over 0), and each keeps its bytes in every cell. The air is every clear pixel (alpha 0) within `ceil(reach) + REGION_AIR` (2 px) 8-neighbour steps of the region, where reach is the move's reach at the gain the regions move by; the region moves into it, while clear pixels further away stay clear. `--region-over` changes the soft edge and the held pixels as its section below says.

Over the region, its soft edge and the air the weight is `sin²(pi/2 x d)`, where `d` is the distance to the nearest held pixel in rim units: an ellipse `Lx` px across and `Ly` px down, `d = min over held pixels q of sqrt(((x - qx)/Lx)² + ((y - qy)/Ly)²)`, and 1 where no held pixel is nearer (computed exactly, the least along each row then down each column, via `rim_distance`). Elsewhere the weight is 0. So the weight is 0 at the held pixels and rises to 1 (the whole move) inside the region, at least `Lx` across and `Ly` down from every held pixel. `Lx` and `Ly` are read off the move: each starts at the fold rule's least band for the largest move that way over the cells, `ceil(most x pi / 2) + 1`, times `--region-rim SCALE` (default `RIM_DEFAULT`, 1.0; 1 or more), rounded up; then both are widened together, a px at a time, until `(pi/2) x sqrt((dx/Lx)² + (dy/Ly)²)` is at most `RIM_SLOPE` (0.9) in every cell, where dx and dy are the cell's move across and down at the gain the regions move by (`rim_bands`). The move does not fold because `sin²(pi/2 x d)` rises at most `pi/2` per unit of `d`, and `d` rises at most `1/Lx` per px across and `1/Ly` per px down, so along the move a pixel keeps at least `1 - RIM_SLOPE` (0.1) of its area, and a move goes less far than the held pixels lie, so no read lands on a held pixel with half its weight. A wider `--region-rim` (1.5, say) makes a gentler rim: less of the part moves whole. Without `--band-up`, the fold rule reads no radius under `--region-mask` (the rim is read off the move instead), and no gain is lowered by it: in the record each region's fold entry has `radius_px` null, `ratio` 0 and `gain_limit` null. `--gain` sets how far the part moves, as before, and the ledger records the most any region pixel moves per cell.

With `--band-up FLAT[,PX|auto]`, the weight is the lesser of the rim's and the upper yield's, where the yield is the weight a band would give with `--band auto --band-margin auto --band-up FLAT[,PX|auto]` at the gain asked (each region's ellipse carried by its own pixels as a band carries it); so above the centre the part yields while below it the rim rules. The yield is checked as a band is: the fold rule reads its narrowest band, and then its steepest slope (under `--on-fold refuse` refused, saying to give `--band-up` a wider band, under `--on-fold lower` the gain lowered); the rim is then read off the move at the gain the regions move by. The yield's carry by the lay warns as under `--band`.

Every pixel of the region, its soft edge and the air is read at its whole move using premultiplied bilinear sampling (the band's sampling, `draw_held` with no short read). A read whose four pixels take a held pixel is read from the other three where they carry half its weight or more ("renormalised"), else walked back by 1/`HOLD_STEPS` (1/16) of its move at a time until they do ("walked"); the rim keeps reads from putting half their weight on held pixels, so the walked count is 0. Under `--region-over` the walked count is no longer always 0: a read in the air beside the figure under the region, past the band, can take a held pixel of that figure and is walked back (and, as for any count of these that is not 0, the warning line reports it).

In strip metadata under `follow.region_mask` (and `region_mask` in `sway.json` on `--frames`), the record stores `policy` (`own-pixels-mask/held-outside/rim-sin2/1`), `asked` (`per_cell`, `sha256`: the file's, or a list per file for a folder, `rim`: the scale, `shrink_px`, `crease`: true/false, `up`: `{flat, band}` or null, and when given, `over: true`, `hold` (the hold mask's `{per_cell, sha256}`, or null), and `shear: S`), `soft_px` (2), and `air_px` (`ceil(reach) + 2`). It records `rim` (`across_px` for Lx, `down_px` for Ly, `least_px`: the two bands it started at, `slope`: the steepest `(pi/2) x sqrt(...)` over the cells, `slope_bound`: 0.9, and when given, `shear_px` ([across, down]: the least rim the shear asks)); `mask` (`px`: its pixel count as worn in, `box`, and `carried_by_px`: the solid pixels it was carried by; null for a folder); `crease` (per mask, `rows`: each column's row, keyed by column, and `free_columns`; null when not asked); `carry` (`search_px` of 9 and `cells`: per cell `px`, `from`: `base`, `pixels` or `before`, `guess`: `before` or `lay` where carried by its pixels, `why` where it kept the cell before's carry, `least`, and `curvature`; null for a folder); `kept` (`{cell, why}` for each cell that kept the carry of the cell before); and `up` (the upper yield's shape as a band's record: `reach_px`, `band_px`, `margin_px`, per region `flat_px`, `band_px` and `steepest`, its `carry` and `lay`; null without `--band-up`). The strip's `carry_basis` is the band's (`CARRY_BASIS_BAND`).

The record's `ledger` lists per cell, with `<key>_max` beside each count: `region_px`, `soft_px`, `air_px`, `held_px`, `held_changed_px` (held pixels the drawn cell does not keep byte for byte; 0), `move_most` ([px, x, y] of the region pixel that moves most; null where none moves) with `move_most_max`, `pressed_px`, `pressed_box`, `area_least`, `stretched_px`, `stretched_box`, `area_most` (as the band's ledger counts them: solid moved pixels left under `PRESSED` 0.85 or over `STRETCHED` 1.15 of their area), `folded_px` (moved pixels whose move takes 0 or less of the source's area), `jacobian_min` (the least area any moved pixel takes; the move and the read are one field), `read_held_weight` ([weight, x, y]: the most weight a read's four pixels put on held pixels; null where none), `read_held_half_px` (reads that put half or more on them), `renormalised_px`, and `walked_px`. Warning lines on stderr, one each, report the cells that kept the carry of the cell before ("--region-mask keeps the carry of the cell before in N cells, its own pixels found surely neither round where that cell had them nor round the lay (cell k: why; ...)", ending `follow.region_mask.kept`); a region whose `--band-up` yield was carried by the lay (ending `follow.region_mask.up.lay`); and for `held_changed_px`, `pressed_px`, `stretched_px`, `read_held_half_px`, `renormalised_px` and `walked_px` when not 0 in some cell: "--region-mask: <what> in X of N cells, up to M px (cell k)" (for pressed and stretched also the least or the most area and where), ending `follow.region_mask.ledger.<key>` on `--loop-dir` and `sway.json region_mask.ledger.<key>` on `--frames`. Command stdout JSON gains `region_mask` with `kept_cells`, `rim_px` ([Lx, Ly]), `move_most_px` and `walked_px_max`.

As the last check, a run is refused, named the engine's fault, with nothing written if `jacobian_min` is 0 or under in any cell: "--region-mask's rim (Lx px across, Ly down) keeps a move of R px taking at most 0.9 of a pixel's area, and cell k's move folds the picture all the same (...); this is the engine's fault - nothing was written". A run is also refused with a non-zero exit, writing nothing and leaving the loop as it was: with `--band` ("--region-mask moves the region's own pixels and holds every visible pixel round it, the weight falling over a rim inside it; --band moves an ellipse over a band outside it: give one of them"); with `--band-margin` ("--band-margin shapes a band; with --region-mask the rim is read off the move (--region-rim)"); with a region's fifth number `flat` ("--region-mask gives the region as a mask, and a region's fifth number (flat, ...) shapes the ellipse's weight: give the region as cx,cy,rx,ry with --region-mask"); with `--stretch-floor` ("--stretch-floor reads the weight that falls from a region's centre; with --region-mask the rim is widened with the move"); with `--hold-mask` without `--region-over` ("--region-mask holds every visible pixel outside the region already; --hold-mask is a band's, or with --region-over the pixels its layer leaves out"); under `--region-over`, `--hold-mask` is refused as a band's `--hold-mask` PNG reading refuses (size, no pixel, not read, folder count, no PNG); when `--region-rim` is under 1 or not finite, such as `nan` ("--region-rim is how many times the fold rule's least band the rim starts at, 1 or more; got X"); when `--region-shrink` is negative, and, for Python callers, also for a `region_shrink` that is not a whole number ("--region-shrink is the px the mask is worn in by, a whole number, 0 or more; got X"); when `--region-rim`, `--region-shrink` or `--region-crease` is given without `--region-mask` ("--region-rim, --region-shrink and --region-crease shape a region mask; they need --region-mask"); when `--region-over`, `--region-over-solid`, `--region-rim-round`, `--region-shear` or `--region-membrane` is given without `--region-mask` ("--region-over, --region-over-solid, --region-rim-round, --region-shear and --region-membrane shape a region mask; they need --region-mask"); when `--region-membrane-unpin`, `--region-clear-alpha`, `--region-floor-beside` or `--region-own-outline` is given without `--region-mask` ("--region-membrane-unpin, --region-clear-alpha, --region-floor-beside and --region-own-outline shape a region mask's membrane; they need --region-mask"); when `--region-clear-alpha` is outside 1 to 127, or not a whole number for Python callers ("--region-clear-alpha is the alpha under which a pixel is read as clear, a whole number from 1 to 127 (under a solid pixel's); got X"); when `--region-floor-beside` is negative or not whole ("--region-floor-beside is px, a whole number, 0 or more; got X"); when `--region-own-outline` is negative or not whole ("--region-own-outline is px, a whole number, 0 or more; got X"); when `--region-shear` is 0 or under, or not finite ("--region-shear is the most a move one way may rise across the rim the other way, px a px, over 0; got X"; for Python callers, a value that is not a number gives "--region-shear is px a px, over 0; got X"); when a mask is of another size than the cells ("--region-mask is WxH, the cells are WxH: a mask is drawn on a cell, pixel for pixel"); when a folder holds a count of PNGs that is not the cell count ("--region-mask holds N masks and there are M cells: a folder gives one mask per cell, in name order"); when a mask covers no solid pixel of the cell it was drawn on ("--region-mask holds no solid pixel of the cell it was drawn on: nothing of the region to carry"); when a shrink leaves nothing ("--region-shrink N wears the region mask away: no pixel of it is left to move"); and as `--hold-mask`'s PNG reading refuses: when a file is not read, when a mask holds no pixel, or when a folder has no PNG.

The options (`--region-mask`, `--region-over`, `--region-over-solid`, `--region-rim-round`, `--region-membrane`, `--region-shear`, `--region-membrane-unpin`, `--region-clear-alpha`, `--region-floor-beside` and `--region-own-outline`) work on `--loop-dir` and on `--frames ... --out ...` alike (the frames get the strip's cells and the same record), with `--reference`, and under `--read-on`, where a turned loop carried from its cut is moved cell for cell as the cut is. The alias `video-follow` takes the same flags. `video-sway-inspect` does not take them.

#### A region laid over the figure under it: `--region-over`

With `--region-mask` alone, every visible pixel outside the region is held and is an anchor of the rim (the weight is 0 at held pixels and rises by `sin²(pi/2 x d)` over the rim). Where the region lies on more of the figure (a part resting on the body under it), the held pixels right under its lower edge pin that edge: the lower edge barely moves while the rest of the part does. `--region-over` lays the region over the figure under it instead. It is off by default; when it is not given, nothing of it is read or written, and strips, frames and records are byte for byte what they were.

Per cell (`layer_sets`, then `lay_over`), under the region's lower edge (per column: the rows from the first row under the region's lowest pixel in that column; for a column past the region's sides, under the lowest pixel of the region's nearest column, the leftmost on a tie; in the region's columns and `air` px past them on each side; `air` = `ceil(reach) + REGION_AIR`, the same reach as the air the region moves into), every held pixel, solid or not, is no anchor of the rim (none of the pixels `--hold-mask` leaves out). So the lower edge moves with the rest of the region. The band is those pixels (solid or not) within `air` 8-neighbour steps of the region and its soft edge. They move too, and the layer is drawn over them. The layer (`lay_over`) draws only the band: the region's own pixels (the region, its soft edge and its outline against the air, premultiplied) read at the move, laid by their alpha over the band pixel's own colour where the layer lands (alpha over 0), the band pixel's bytes where it does not; its outline pixels are the layer alone. The air the region moves into is drawn by `draw_held` at the move, so it shows what the move brings into it, never the held figure beside it. Where the region lifts off the figure under it, its pixels read the figure under its lower edge at the move, as any read does (no own-column read, no clamp to the figure's first row any more). A piece `--hold-mask` leaves out right under the lower edge is held: it keeps its bytes, and a read of the region's that would take it is read from its other pixels, so no hole opens; none of those pixels counts as lifted (the piece is no longer drawn up into the lift). Since where the region lifts its pixels are plain reads at the move, a region pixel beside the figure's soft side whose read takes that soft side and the air past it is as soft as that read: where the region moves toward the figure, its column over that soft side shows thinner than the side in the rows the region leaves. `draw_held`'s counts (`renormalised_px`, `walked_px`, `read_held_weight`) now read every pixel it draws: the region, its soft edge, the air and where the region lifts (the band and what a membrane moves are not held pixels for them). So under `--region-over` the walked count is no longer always 0: a read in the air beside the figure under the region, past the band, can take a held pixel of that figure and is walked back (and, as for any count of these that is not 0, the warning line reports it). The ledger records `lifted_px`: the region's pixels whose read at the move falls under half on the region and its soft edge and half or more on the pixels under its lower edge.

Under `--region-over`, the region's soft edge is every pixel not opaque (alpha over 0, under 128) within `REGION_SOFT` (2) 4-neighbour steps of the region, beside another solid pixel or not, none left out; it moves with the region. (Without `--region-over`, a soft pixel beside another solid pixel outside the region is held.) Left beside the figure under it, it would stay behind as a ghost of the region's outline. Under the region's lower edge, only such a pixel that touches no solid pixel outside the region (8-neighbour) is the soft edge; one that does is the figure's own soft side, which stays where it is (it is in the band). A solid band pixel that is the region's own outline against the air (beside a pixel that is neither solid nor left out, within 2 px of the region and its soft edge, and touching no other solid pixel outside the region by 4-neighbour) goes with the layer and leaves the air behind it. An outline pixel that touches the figure under it or a pixel left out keeps its bytes as the band does.

With `--region-over`, `--hold-mask MASK.png|DIR` names pixels the layer leaves out, such as an arm resting on the part. The pixels `--hold-mask` names that the region mask covers in that cell are the region's: they move with it, are no anchors of the rim and are not held. Only the pixels it names that the region mask does not cover are left out as before: never the region's, never in its soft edge, never in the band or the membrane, never grown over by `--region-own-outline`, never drawn over; held, anchors of the rim, keeping their bytes. This is only so with `--region-over`. On a band (`--band` with `--hold-mask`, the other path that takes it) every pixel `--hold-mask` names is held as before, and a pixel it names is never the region's there. The mask is read and carried exactly as a band's `--hold-mask` (one PNG drawn on the cell the regions were read on, split into 8-connected pieces, each carried by its own solid pixels and grown by `HOLD_GROW` (3) px, with the same warning when a piece is carried by the lay; or a folder of one PNG per cell in name order, held as given). The held pixels the record and the ledger count are then those the drawn cell keeps: every held pixel but the band pixels the layer lands on.

With `--region-crease`, in every cell the region reaches down to the crease again (`crease_reach`): the crease's rows (its columns and rows on the cell the mask was drawn on; a column between two crease columns takes the nearer one's row, the left one's on a tie) are carried into the cell by the mask's carry there (the column rounded to the nearest, half to even; the row rounded down), and in each crease column where the region holds a pixel, the cell's solid pixels from the region's lowest pixel down to the crease's row join the region. The crease is the picture's own edge, so the layer reaches it in every cell while the wear still wears the mask in everywhere else. Without `--region-over` nothing reaches back down: the worn mask is the region.

In the record, `asked` gains `over: true` and `hold` (the hold mask's `{per_cell, sha256}`, or null) only when `--region-over` is given. The record gains `over`: `policy` (`under-edge-free/band-laid-by-alpha/air-and-lift-by-move/crease-reach/2`), `band_px` (the band's reach, `ceil(reach) + 2`), and `left_out` (null without `--hold-mask`; else `grow_px` (3, or null for a folder), `pieces` (`{px, box}` per piece; none for a folder), `carry` (`search_px` 9 and per cell the carry of each piece, as a hold's; null for a folder), and `lay` (`{cell, piece, why}` for each piece carried by the lay)). A warning line on stderr names the cells where a left-out piece was carried by the lay: "--hold-mask (what --region-over leaves out) is carried by the body's lay in N cells, its own pixels not found surely (cell k, piece i: why; ...)", ending `follow.region_mask.over.left_out.lay` (or `sway.json region_mask.over.left_out.lay` on `--frames`).

Per cell, only with `--region-over`, the ledger lists each count with its `<key>_max`: `band_px`, `over_px` (band pixels the layer covers half or more, its outline not counted), `edge_px` (the outline that goes with the layer), `lifted_px` (the region's pixels whose read at the move falls under half on the region and its soft edge and half or more on the pixels under its lower edge), `left_out_px` (the pixels `--hold-mask` names that the region mask covers, which the region keeps), `cover_depth_px` (how deep the layer covers the band: the straight-line distance from the region and its soft edge to the farthest pixel it covers; at most the band's reach), and `edge_folded_px` (folds within 1 px, by 4-neighbour, of a held pixel: where the layer stops and the band keeps its bytes the move steps from the move to 0, which is the layer's edge, not a fold of the move; these are counted apart, and `folded_px`, `jacobian_min` and the last check's refusal read every other moved pixel as before). `draw_held`'s counts (`renormalised_px`, `walked_px`, `read_held_weight`) now read every pixel it draws: the region, its soft edge, the air and where the region lifts (the band and what a membrane moves are not held pixels for them).

#### The rim widened for the shear: `--region-shear S`

The rim's width each way (`Lx` across, `Ly` down) is read off the move along that way alone (`rim_bands`). A part that moves mostly down gets a narrow rim across, so its move down rises from 0 to the whole move across only a few px: a step across the rim of up to `pi/2 x (most move down) / Lx` px per px, which shows as a tear along the part's side.

`--region-shear S` (px per px, over 0) widens the rim so that a move one way rises across the rim the other way by at most S px per px: `Lx` at least `ceil(pi/2 x (most move down) / S)` and `Ly` at least `ceil(pi/2 x (most move across) / S)`, the most over the cells at the gain the regions move by (`shear_floor`). Each is taken as the larger of that and the rim `rim_bands` reads (after its widening to `RIM_SLOPE`), so it only lowers the slope. `--region-rim` still scales the rim it starts at. Without it the rim is read as before. Unlike widening `--region-rim`, which widens both ways by the move along each way, `--region-shear` widens each way by the move the other way.

In the record, `asked` gains `shear: S` only when given, and `rim` gains `shear_px` ([across, down]: the least rim the shear asks) only when given.

With `--region-over` or `--region-shear`, the ledger also reads the region's edges per cell:
- `step_across_most` and `step_down_most`: the most the drawn move steps between two pixels side by side and between two one over the other, both visible in the cell as cut, `|m(p) - m(q)|` px per px, and where (`[step, x, y]`, x and y the left or upper pixel of the two; null where no two are) (`move_steps`); under `--region-over` the band's pixels are not read (the layer slides over them). Over the cells, their largest are recorded as `step_across_max` and `step_down_max`.
- `still_strip`: the strip a region drawn short of its side leaves standing (`still_strip`): per row of the region, at each end of each run of it, how many solid pixels lie outward before the figure's side there (a pixel not solid, or a pixel `--hold-mask` leaves out), none of them the region's (0: the region reaches its side). An end whose way out meets the region again, or the cell's edge, is not read. It records per cell `{median, most, ends, covered}`: the median and the most width, how many ends were read, and how many are no wider than the rim across (`Lx`); null where no end is read.
- `tip`: the region's tip (`tip_shape`): the side (right or left) of its `TIP_COLS` (10) outermost columns, with its soft edge, more of whose pixels touch the air (the right on a tie); a window of those columns and their rows with `TIP_AIR` (8) px of air round them (`box`); a ring of pixels within `TIP_RING` (3) px of the source's outline against the air in the window, leaving out pixels within `TIP_OFF` (2) px of another solid pixel (`ring_px`); `shape_px`: how far the drawn outline is from the source's outline moved by the shift that fits it best, the sum of `|alpha drawn - alpha source moved|` over the ring at its least over the shift (whole px within `TIP_SEARCH` (6) px, then a quarter and a twentieth of a px round the best) over the sum of the source alpha's gradient on the ring: the mean px the tip's outline is off its own shape once its move is taken away (0: the tip moved whole); `shift`: that shift; and `move_mean`: the mean move of the tip's own solid pixels in the window, from the move field (a shift fits a straight outline anywhere along it, so the shift is no measure of the move). It is recorded per cell, or null where the region has no pixel. Over the cells, `tip_shape_max` and `tip_move_max` record `[value, cell]`.

#### Only the solid pixels under it: `--region-over-solid`

Under `--region-over` alone, every held pixel under the region's lower edge, solid or not, is no anchor of the rim, and a soft pixel under the lower edge that touches a solid pixel outside the region is the figure's own soft side, remaining in the band. With `--region-over-solid`, only the solid pixels (alpha 128 or more) under the lower edge are free of the rim and placed in the band for the layer to draw over. The rest of the figure there (alpha 1 to 127) stays where it is, remains an anchor of the rim, and is never drawn over. The region's soft edge (pixels not opaque within `REGION_SOFT` (2) 4-neighbour steps of the region, none left out) goes with the region under its lower edge as well, whatever it touches, rather than being handed to the figure where it touches a solid pixel outside the region.

The option requires `--region-over`; without it, the run is refused ("--region-over-solid says which pixels under a region laid over the figure are laid over; it needs --region-over"). It is off by default; when it is not given, nothing of it is read or written, and strips, frames and records are byte for byte what they were.

In the record, `asked` gains `over_solid: true` only when given, and `over` gains `solid: "under-edge-solid-free/soft-edge-with/1"` (`OVER_SOLID_POLICY`) only when given.

#### The rim held round beside it: `--region-rim-round`

Under `--region-over`, what lies under the lower edge is no anchor of the rim. The held figure beside the region (in columns the region does not reach, above its lower edge) stops at the row of the region's lower edge in its nearest column. Where the region's lower edge runs lower further in, the region's pixels below that corner are distant from it by rows over the rim's height `Ly`. `Ly` is the fold rule's band for the move down (`rim_bands`: the most move down times pi/2, rounded up, plus 1 px, widened only until the slope is at most `RIM_SLOPE`, 0.9), so it is just wide enough not to fold, not wide enough to rise gently. `--region-shear` widens `Ly` only by the move across, which is small for a part that moves mostly down and leaves `Ly` as it is, so the move down rises over those few rows by up to `pi/2 x (move down) / Ly` px per px. That produces a steep step from one row to the next inside the region.

`--region-rim-round` holds the rim round beside the region instead. The held pixels beside the region (in the columns it does not reach, above its lower edge, `floor`) hold the rim round. Their distance is read over a circle as wide as the wider of the rim's two widths (`r = max(Lx, Ly)`), leaving every other anchor as before. Below the corner, the move down then rises by at most `pi/2 x (move) / r` px per px. The flag requires `--region-over`; without it, the run is refused ("--region-rim-round holds the rim round beside a region laid over the figure, where what lies under its lower edge holds none of it; it needs --region-over"). It is off by default; when it is not given, nothing of it is read or written, and strips, frames and records are byte for byte what they were.

In the record, `asked` gains `rim_round: true` only when given. `rim` gains `round` (`{policy: "beside-above-floor/round-wider-width/1", px: r}`, `RIM_ROUND_POLICY`) only when given.

#### A membrane under its lower edge: `--region-membrane N`

`--region-membrane N` (a whole number, 0 or more) stretches the figure under the region with its move instead of letting the region slide over it. It requires `--region-over`; without it, the run is refused ("--region-membrane moves the figure under a region laid over it; it needs --region-over"). A negative or non-whole value is refused ("--region-membrane is how far under the region's lower edge the membrane reaches, a whole number of px past the first, 0 or more; got X"). What lies under the region's lower edge (the `below` set, solid or not, and with `--region-over-solid` the not-opaque pixels there too) within N + 1 px of the region and its soft edge (straight-line px, a round rim N + 1 px wide) moves as a membrane, leaving out pixels that `--hold-mask` leaves out and outline pixels over the band that go with the layer. The membrane (`--region-membrane N`) is now solved by passes, not conjugate gradients. Each pixel it moves starts at the move of the nearest pixels of the region and its soft edge carried out to it (in N + 2 rounds, each pixel not yet reached beside one that is, by 8-neighbour, takes the mean of their moves; a pixel never reached keeps its own), falling as cos² of its distance to them over N + 1 px. Then pass after pass, all of them at once, each becomes the mean of its 4 neighbours' moves that are the figure (the region's own move on the region, its soft edge and its outline over the band; 0 on every other figure pixel; a clear pixel is no neighbour), until a pass moves none of them `MEMBRANE_STEP` (1e-4 px) or more, `MEMBRANE_PASSES` (1200) passes at most. No piece is left out any more: every pixel the membrane takes moves by it (the old rule that left out a piece touching no other figure pixel is gone).

Membrane pixels are drawn by their move (`draw_held`) and are never laid over: the band the layer draws over loses them, and they are not held. The move steps down from the region's last row to the row under it by a share of the step taken when the region slides over the figure. A membrane too short for the move folds the picture. When a fold falls on a membrane pixel or within 1 px (8-neighbour) of one, the final check refuses by naming the membrane, reporting the move hung over N + 1 px, the cell and the pixel area, and stating that a longer membrane or a smaller move is needed, ending with "nothing was written" (instead of the rim message that calls a fold the engine's fault). It is off by default; when it is not given, nothing of it is read or written, and strips, frames and records are byte for byte what they were.

In the record, `asked` gains `membrane_px: N` only when given. `asked` gains `membrane_unpin: true`, `clear_alpha: A`, `floor_beside_px: N` and `own_outline_px: G`, each only when given with `--region-membrane` (and N, G over 0). `over.membrane` records `policy` (`under-edge-within-n/spread-cos2-start/figure-4-mean-passes/2`, `MEMBRANE_POLICY`), `reach_px` (N + 1), `step_px` (1e-4) and `passes_most` (1200). The ledger records per cell (with `<key>_max` summaries): `membrane_px` (pixels moved), `membrane_most` ([px, x, y] the most one of them moves, or null where none moves), and `membrane_folded_px` (folds on the membrane or within 1 px of it).

#### The membrane's own keys: `--region-membrane-unpin`, `--region-clear-alpha`, `--region-floor-beside`, `--region-own-outline`

`--region-membrane-unpin`: what the membrane moves is no anchor of the rim either, so the region's move does not fall to none at its first rows under its lower edge (with `--region-over-solid` a pixel not opaque under the lower edge is both moved by the membrane and an anchor of the rim without it).

`--region-clear-alpha A` (a whole number from 1 to 127): a pixel under alpha A is read as clear by the sets the region moves and holds (its soft edge, the held pixels, the air, what lies under its lower edge, the band) and by the membrane's figure; the cell's bytes stay its own. A speck of dust beside the figure holds nothing.

`--region-floor-beside N` (a whole number, 0 or more; 0 as without it): beside the region's columns its lower edge is the lowest pixel of its columns within N px of that side, not that of its nearest column alone.

`--region-own-outline G` (a whole number, 0 or more; 0 as without it): before the sets, the region grows G 4-neighbour steps over the cell's solid pixels within G 4-neighbour steps of a clear pixel, never under its lower edge's line (each column's rows under the lowest region pixel of the nearest region column, the region as it stands before the growth), never a pixel `--hold-mask` leaves out. The outline its mask, carried into the cell, leaves out moves with it: those pixels are the region's, not held, no anchors.

Refusals (non-zero exit, nothing written, the loop as it was): any of the four without `--region-mask`: "--region-membrane-unpin, --region-clear-alpha, --region-floor-beside and --region-own-outline shape a region mask's membrane; they need --region-mask"; `--region-clear-alpha` outside 1 to 127, or not a whole number for Python callers: "--region-clear-alpha is the alpha under which a pixel is read as clear, a whole number from 1 to 127 (under a solid pixel's); got X"; `--region-floor-beside` negative or not whole: "--region-floor-beside is px, a whole number, 0 or more; got X"; `--region-own-outline` negative or not whole: "--region-own-outline is px, a whole number, 0 or more; got X". In the record, `asked` gains `membrane_unpin: true`, `clear_alpha: A`, `floor_beside_px: N` and `own_outline_px: G`, each only when given with `--region-membrane` (and N, G over 0). All four options are each only with `--region-membrane` (each needs `--region-mask` to be given at all; given with a region mask but without a membrane they shape nothing: the same bytes and the same record, none of them named); all off by default; off, strips, frames and records are byte for byte what they were. They work on `--loop-dir` and on `--frames ... --out ...` alike, with `--reference` and under `--read-on`; the alias `video-follow` takes them; `video-sway-inspect` does not.

### Inspecting a sway — `video-sway-inspect`

The ellipse is given in the first cell, where somebody looked. Into every other cell it is carried by the body's motion, and there nobody looked: an arm that swings across a chest is inside the chest's ellipse in those cells and moves with it, and so does a sleeve of the chest's own colour, which no count of colours finds. The Sway layer does not read what a pixel belongs to. `video-sway-inspect` (old name `video-follow-inspect`) writes what somebody needs in order to look, and moves nothing:

```bash
sprite-gen video-sway-inspect --loop-dir set/front-run/loop --region 136,164,60,50 [--region …] \
  [--gain 2.5] [--on-fold refuse|lower] [--freq 2.4] [--zeta 0.6] [--read-on <sha256>] [--stretch-floor D] \
  [--breathe <breathe.json> [--lag 0.15]] [--gain-across G] --out-dir inspect/front-run [--scale 2] [--columns 8]
```

- It takes `--breathe`, `--lag`, `--gain-across` and flat regions as `video-sway` does, and stays on a loop directory without `--frames`.
- **What is inside an ellipse is not read.** Every region is written `ownership: "unknown"` in
  every cell, the first included, from its centre to its rim; no cell is marked or held on a
  guess. `solid_px` counts the body's silhouette under the ellipse (alpha ≥ 128), not whose
  pixels they are. Saying which cells hold the part alone is for a person or a model that looks
  at the board of the cells as cut, and for a second reader apart from the first.
  `video-sway` takes no such list: it moves every cell.
- `follow-inspect.source.png` has the cells as cut;
  `follow-inspect.moved.png` has them as `video-sway` would write them, laid and ringed alike.
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
- **The centre picture** (`--center`): a transparent still — the set's base, the picture a board shows in
  its middle. A
  still with no transparent background is refused (cut it off first: `sprite-gen cutout`), and so are
  rows without `body_h` beside one.
- **Lengths.** Loops of different lengths, in frames or in frame time, are laid all the same, one frame
  a column and the shorter rows' last cells empty; the record says `same_length: false` and a warning line
  names each direction's length. `video-cycle-align` gives a set one cycle length.

## 8. A walk or run that stops — `video-stop`

A game character that stops running jumps from a running frame straight to its standing picture. Frames made between them (RIFE in-betweens) are the wrong tool: as [loop repair](loop-repair.md) notes, RIFE smears a changed appearance, and where two drawings are too far apart for the flow, legs come out as one shape of fill; a running frame and a separately drawn standing picture are both. Every walk or run clip filmed from a still (image to video) already holds a filmed "stand → set off" at its start: the body stands, sets off, and runs into the gait the loop is cut from. Played backwards from the frame where the clip reaches the loop, that start is the body running into the standing still — filmed frames, no frame made between two others, ending on the clip's first frame, the still as filmed. `video-stop` cuts this as a one-shot clip beside the loop: a strip, its sidecar (`loop: false`, `hold_last: true`), a GIF, a WebP and a report. Played backwards, motion runs backwards (a cape, a planted foot), which is a cost to look at. This example cuts a run's stop and plays it twice as fast:

```bash
sprite-gen video-stop --frames-dir run/frames/keyed --loop run/loop/run.strip.json --out-dir run/stop --speed 2
```

- **Speed and the frame cap.** `--speed 2` plays every other frame (still kept last); `--max-frames N` (at least 2) spaces N frames evenly from the first to the still. Each frame is shown one clip tick (`--fps` defaults to the loop's `source_cut` fps, else 24). Strips wider than 32,000 px are refused; pass `--max-frames` or `--speed 2`.

The outputs are `<name>.strip.png`, `<name>.strip.json`, `<name>.gif` (plays once), `<name>.webp` (plays once), and `<name>.stop.report.json` (or `--report PATH`).

## Related

- [docs/README.md](README.md) — documentation index
