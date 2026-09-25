# Video → sprite pipeline (engine SSoT)

> Owns: Pipeline B engine contract: state canvas, keyed frames, true-period and one-shot cycles, strip/GIF/WebP, the batch · Index: [docs/README.md](README.md)

One still becomes a whole motion set: the still is padded into the canvas a state
needs, Grok Imagine animates it in place, the clip is keyed frame by frame, and one
seamless cycle is cut out as a strip, a transparent GIF and a WebP — every stage
measured and reported, nothing recovered silently. Everything here was first run by
hand on 2026-09-08 (15 loops: 3 directions × 5 states) and the rules below are the
ones that survived that day.

```
still ──video-canvas──▶ canvas.png ──video──▶ clip.mp4 ──video-frames──▶ keyed/*.png ──video-loop──▶ strip · gif · webp
                                                                                                  └── video-set runs all four per (direction, state)
```

| Verb | Module | In → out |
|---|---|---|
| `sprite-gen video-canvas` | `sprite_gen/video/canvas.py` | still → padded still (state canvas) + report |
| `sprite-gen video` | `sprite_gen/gen/video.py` ([gen](video.md)) | still + prompt → mp4 + report |
| `sprite-gen video-frames` | `sprite_gen/video/frames.py` | mp4 → `raw/`, `keyed/` RGBA frames + report |
| `sprite-gen video-loop` | `sprite_gen/video/loop.py` | keyed frames → `cycle/`, `<name>.strip.png` + `.strip.json`, `<name>.gif`, `<name>.webp` + report |
| `sprite-gen video-set` | `sprite_gen/video/batch.py` | bases × states → one folder per item, `set.report.json`, `table.md` |

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
| `attack` | wide | 16:9 | 35 % above the still, at least 28 % in front (facing side), 20 % behind | weapon swings rise overhead and extend in front; a long weapon drawn back reaches behind |
| `projectile` | wide | 16:9 | 34 % in front | the projectile travels away |
| everything else | square | 1:1 | — | in-place motion fits the still |

`--shape tall|wide|square` overrides the row; `--headroom` / `--lead` / `--trail` tune the room
(`--trail` is the empty fraction of the width kept behind the subject, for a weapon drawn
back before the strike); `--facing left` mirrors the wide layout. Headroom is a fraction of the full canvas
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

An attack is a timed strike, not a repeat. `MOTION_TEXT["attack"]` asks for the same attack
twice, each a windup (about 0.5 s), one strike in front (about 0.25 s), a held impact pose
(about 0.3 s) and a recovery to the exact starting stance (about 0.5 s), with what the subject
holds kept in the hand nearest the viewer and the body never turning. Its template
(`ACTION_COMMON_TEXT`) drops the "evenly paced" line, keeps what the subject holds inside the
frame, and asks for crisp frames without motion blur. `video-set` asks attack clips for 4 s
(`STATE_DURATION_SECONDS`; every other state keeps 3 s, and `--duration` overrides every state)
and pins the clip to end on the frame it starts from: `--last-frame` is the canvas itself
(first-last mode, see [video.md](video.md)), so the strike has to come back to the still.

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
rules stay the engine's, and an attack keeps its "twice in a row" sentence.

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

The report also carries per-frame alpha coverage and an **edge-contact check**: any
opaque pixel in the top/left/right 4-pixel bands fails the run. Each contact pixel is
classified by its *raw* colour — the declared key's hue family is **`residual`**
(background the matte did not erase), anything else is **`subject`** — and the two
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
per clip, learned on the first frame, so edge colours cannot flicker between palettes. The
default `off` keeps frames byte-identical. Method, guards and measurements:
[chroma-alpha.md](chroma-alpha.md#decontam--give-the-edge-the-subjects-own-colour-back).

## 3b. Canvas shape for raised limbs and wide costumes

`video-set` picks the canvas from the state row alone. Two things that are not a jump or
an attack still leave a 1:1 frame: limbs raised in a celebration, and a costume that is
wider than the body (a skirt, a veil, a held object). Both fail `video-frames`'s
edge-contact check — the clip was made, the frames were cut, and the run stops at the
gate. The state table now routes `cheer`, `wave` and `celebrate` to the wide canvas, and
`video-set --shape wide` forces it for every state of a batch when the costume is the
reason. The same `--shape` is what `video-canvas` already took for a single still.

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
frames) of observed repeat context. `--min-len/--max-len` override the window.
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

Gates, all fail-loud: no period (profile flat, below the recorded `periodicity_min`), loop seam ratio
above `--seam-max` (2.0), GIF/WebP re-opened and checked (frame count, `loop=0`,
transparent corners, no RGB under alpha 0 in the WebP).

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
seam numerator/denominator and repetition/return evidence. Undefined ratios are JSON
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
  overhead, and scaling that to N shrank the character against its walk. The sidecar says
  which (`body_ref`: `first-frame` or `tallest-grounded`) and records `body_src_h` and `scale`. `delay_ms = cycle_seconds / frames`, so a
  24 fps clip yields 41.67 ms cells; render at 24 fps to keep one cell per frame
  (a 30 fps render of 24 fps cells is a 5:4 pulldown and judders).
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
is each state's own default (3 s, attack 4 s) unless `--duration` sets one for all, and an
attack or idle clip is pinned to end on its canvas (an idle is then cut whole, `--cycle pinned`). States use differently shaped canvases (square, tall, wide),
so the same character films at different pixel heights; `--body-height N` gives every state's loop the
same standing-height target and keeps the character one size across the set. At a low
`--resolution`, `--fit tight` frames every item without room so that target is reached by
scaling down rather than up, and lets the subject reach the frame edge. Items
are idempotent (an existing clip is reused unless `--force`); one failure stops only
its item and is listed in `table.md` with its stage and error. Exit code is non-zero
when any item failed.

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
