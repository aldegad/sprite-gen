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
| `attack` | wide | 16:9 | 28 % in front (facing side) | swings and weapons extend forward |
| `projectile` | wide | 16:9 | 34 % in front | the projectile travels away |
| everything else | square | 1:1 | — | in-place motion fits the still |

`--shape tall|wide|square` overrides the row; `--headroom` / `--lead` tune the room;
`--facing left` mirrors the wide layout. A still whose corners are not one flat colour
is refused — a non-flat background cannot be extended without guessing.

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
`--allow-edge-contact` accepts the clipping on purpose.

## 4. Loop — period first, seam second

The 2026-09-08 lesson: a single-start "most similar later frame" search lands on the
**1.5-cycle look-alike** of a gait (legs swapped) and produces a loop that hitches at
the wrap (side walk picked 39 frames where the period was 28; run picked 25 where it
was 17). `video-loop` therefore:

1. builds the distance matrix `D` on 96-px premultiplied thumbnails;
2. reads the **global period profile** `P[L] = mean_j |f[j] − f[j+L]|` and takes the
   *smallest* local minimum that is within 15 % of the deepest one — exact repeats dip
   again at 2× and 3× the period, the half-period look-alike dips noticeably less;
3. only then picks the **start** with the best seam for that period (± 1 frame):
   `seam = D[i][i+L]` over the mean adjacent distance inside the cycle.

Windows come from the state profile (`STATE_PROFILES`, fractions of the clip length):
idle 60–95 % (breathing is slow and not periodic — the lowest seam is a long window,
and idle is exempt from the periodicity gate), walk 6–31 %, run 7–23 %, jump/attack
11–45 %. `--min-len/--max-len` override. The walk floor is low on purpose: a legless
body "walks" as a fast bounce (about 13 frames at 24 fps) while a gait is 24–28, and it
is the 15 % depth rule — not the window — that rejects the one-step half period (on a
biped and a quadruped the half period dipped only 55–70 % as deep as the full one).

Gates, all fail-loud: no period (profile flat, `periodicity < 0.15`), loop seam ratio
above `--seam-max` (2.0), GIF/WebP re-opened and checked (frame count, `loop=0`,
transparent corners, no RGB under alpha 0 in the WebP).

### One-shot actions — `--cycle auto|periodic|one-shot`

A video model asked to jump "over and over" sometimes jumps once and stands for the
rest of the clip. That is not a period, and the periodicity gate says so. For states
that *are* single actions by nature (`jump`, `attack`, unknown states; never `walk`,
`run`, `idle`) `auto` then runs a second, different detector instead of failing: the
**rest pose** is the medoid frame (smallest mean distance to all others), frames whose
distance to it rises more than 3 MADs above the rest noise are the excursion, and the
longest such run padded by 2 rest frames on each side is the cycle — so the seam is
rest → rest by construction. The failover is explicit and recorded, not silent: the
report carries `cycle.kind = "one-shot"` plus `periodic_attempt` (the periodicity that
failed and the window), `table.md` has a `kind` column, and the same seam and
animation gates still apply. `--cycle periodic` keeps the old hard failure; `--cycle
one-shot` forces the excursion cut. A clip that never leaves its rest pose fails loud
in both detectors. `--cycle fixed --start N --length L` skips detection and cuts exactly
those frames — for a clip that holds too few repeats for the periodicity gate but whose
cycle is known (the 2026-09-09 reel jump: 2.3 hops in 145 frames). It is an explicit
instruction, not a failover: the report says `kind = "fixed"`, and the seam gate still
applies.

Outputs:

- `cycle/frame-NNN.png` — the cycle frames, RGB under alpha 0 scrubbed, detached specks
  below 1 % of the body erased.
- `<name>.strip.png` + `<name>.strip.json` — a horizontal strip (union-cropped, **no bottom
  pad** so feet meet the floor, bottom-aligned, ≤ 64 cells **and ≤ 32 000 px wide** because
  Chrome refuses images near 32 767 px — the cap is on pixels, so a 650 px cell allows 49
  cells and the meta says `subsampled`; ≤ 520 px tall) with `frames · w · h · body_h · delay_ms ·
  cycle_frames · cycle_seconds`. `body_h` is the **standing height** — the tallest frame whose feet touch the floor
  (a median over the cycle undercounts a jump, whose crouch and airborne frames dominate,
  and then over-scales it by ~22 %, 2026-09-09). Scale a jump strip — whose cells include
  air room — by `body_h`, not `h`, and it reads the same size as a walk strip;
  `--body-height N` does that scaling in the pipeline so every state comes out at the same
  character size (`--strip-height` stays the cap). `delay_ms = cycle_seconds / frames`, so a
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
staggered (`--start-gap 2`) and a 429 gets a bounded, logged retry (15 s, 30 s). Items
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
