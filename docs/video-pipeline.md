# Video → sprite pipeline (engine SSoT)

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
`--facing left` mirrors the wide layout. The padding is filled with the still's own
corner colour (its chroma key), and a still whose corners are not one flat colour is
refused — a non-flat background cannot be extended without guessing.

## 2. Clip — `sprite-gen video`

Unchanged from [video.md](video.md): the user's own credential, fail-loud, `ftyp`-verified
mp4. For loops, prompt for **in-place, evenly paced, returns-to-start** motion on a flat
chroma fill ("walks in place on a treadmill", "hop … return to the exact starting
stance … same height every time"). `video-set` carries those templates
(`MOTION_TEXT` / `VIEW_TEXT`).

## 3. Frames — extract, key, check the edges

`ffmpeg` extracts every frame; the clip's real fps is recorded (never assumed). Each
frame goes through the same `cutout` engine imported stills use (`--key auto` reads
the corners; green/magenta route to the extract matte). The report carries per-frame
alpha coverage and an **edge-contact check**: any opaque pixel in the top/left/right
4-pixel bands means the model framed too tight, and the run fails loud pointing at
`video-canvas`. `--allow-edge-contact` accepts the clipping on purpose.

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
and idle is exempt from the periodicity gate), walk 10–31 %, run 7–23 %, jump/attack
11–45 %. `--min-len/--max-len` override.

Gates, all fail-loud: no period (profile flat, `periodicity < 0.15`), loop seam ratio
above `--seam-max` (2.0), GIF/WebP re-opened and checked (frame count, `loop=0`,
transparent corners, no RGB under alpha 0 in the WebP).

Outputs:

- `cycle/frame-NNN.png` — the cycle frames, RGB under alpha 0 scrubbed, detached specks
  below 1 % of the body erased.
- `<name>.strip.png` + `<name>.strip.json` — a horizontal strip (union-cropped, **no bottom
  pad** so feet meet the floor, bottom-aligned, ≤ 64 cells because Chrome caps image
  dimensions near 32 767 px, ≤ 520 px tall) with `frames · w · h · body_h · delay_ms ·
  cycle_frames · cycle_seconds`. `body_h` is the standing body height (median per-frame
  bbox height): scale a jump strip — whose cells include air room — by `body_h`, not `h`,
  and it reads the same size as a walk strip. `delay_ms = cycle_seconds / frames`, so a
  24 fps clip yields 41.67 ms cells; render at 24 fps to keep one cell per frame
  (a 30 fps render of 24 fps cells is a 5:4 pulldown and judders).
- `<name>.gif` — `n_out` frames evenly across the cycle, 1-bit alpha, disposal 2, `loop=0`.
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
set on one hand-run set of 15 loops (3 directions × 5 states) on 2026-09-08 and every
loop of that set passed the gates as written. The set itself is operator data and is
not in this repository; the synthetic fixtures under `tests/video/` pin the same rules.
