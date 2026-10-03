# Loop repair — RIFE in-betweens for jump frames, the jolt index, one cycle per set

> Owns: Where RIFE runs and what it costs, how it is installed and what runs without it, jump-frame repair in `video-loop`, the jolt index and its gate, cycle alignment across a direction set · Index: [docs/README.md](README.md)

A walk loop cut from a generated clip can look right in every still and still hitch when it
plays: the video model redraws thin hair a little differently every frame, and now and then
a ponytail lands somewhere it was not one frame earlier. This doc owns the three repairs
`video-loop` and `video-set` make for that, all built on one optical-flow interpolator.

Not to be confused with [frame-interpolation.md](frame-interpolation.md) (a *generative*
in-between drawn by an image model, where RIFE was retired because it smears a changed
*appearance*) or [seamless-video-loop.md](seamless-video-loop.md) (RIFE bridging an ambient
clip's seam). Here RIFE only ever makes a frame between two neighbours of the *same* walk,
where the appearance does not change and only the motion does — the case optical flow is for.

## 1. RIFE — what runs, where, and what it costs

### What

[rife-ncnn-vulkan](https://github.com/nihui/rife-ncnn-vulkan) release **20221029**, model
**`rife-v4.6`** (shipped inside the release zip). The engine calls the binary as an external
tool, like `ffmpeg` and `img2webp`; nothing is vendored into the package.

| Release zip | SHA-256 (measured 2026-10-03) |
|---|---|
| `rife-ncnn-vulkan-20221029-ubuntu.zip` | `1e2c7ee7fa7daa326542d50622f0afedc80cf6f1858bda411d16385ffa5cdf68` |
| `rife-ncnn-vulkan-20221029-macos.zip` | `4a63a1f3c9c715773c57d2ee51df1b315ed20cd6c63103e45c483ecc4400b595` |
| `rife-ncnn-vulkan-20221029-windows.zip` | `d8e4d772d26cd8006ef0ad0bc82eb191b53c68677d1ae2f42506d74cbbbea606` |

### Install — `sprite-gen rife install`

```bash
sprite-gen rife install            # once per machine
```

It downloads the zip for this platform (macOS, universal; Linux and Windows, x86-64), checks
the SHA-256 above before anything is unpacked, and keeps only what the engine runs — the
binary, `rife-v4.6/`, `LICENSE` and `README.md` (and `vcomp140.dll` on Windows), about 35 MB of
the 430 MB zip. It lands in the user data directory:

| | Install root |
|---|---|
| any platform, when set | `$SPRITE_GEN_DATA_DIR/rife` |
| macOS, Linux | `$XDG_DATA_HOME/sprite-gen/rife`, else `~/.local/share/sprite-gen/rife` |
| Windows | `%LOCALAPPDATA%\sprite-gen\rife` |

It unpacks into a staging directory and renames it into place, so an interrupted install
leaves nothing half-written where the engine looks. A finished install of the same zip is left
alone (`--force` unpacks again). It ends with a check frame: one frame half way between two
discs through the installed binary, so a Linux machine with the files but no Vulkan driver
finds out at install time (`--no-check` skips it). `--zip FILE` installs from a zip already
downloaded (behind a proxy, in an image build), checked the same way; `--dir DIR` installs
elsewhere and prints the `SPRITE_GEN_RIFE=…` line the engine then needs. Other platforms (Linux
on ARM, for one) have no release build: build it from the repository and set `SPRITE_GEN_RIFE`.

The engine finds the binary by, in order: `SPRITE_GEN_RIFE` (the binary's path), then
`rife-ncnn-vulkan` on `PATH`, then the install root above. The model is `SPRITE_GEN_RIFE_MODEL`
or `rife-v4.6/` next to the binary. When an earlier place holds another RIFE, the install says
which one the engine runs.

### Without RIFE

RIFE is located only when a frame is to be made, so a smooth walk never needs it. When one is
to be made and no RIFE is found in those three places, nothing fails by default — a walk that
cut before RIFE existed still cuts — and nothing is left unsaid:

| Where | Default without RIFE | Asked for by name |
|---|---|---|
| `video-loop` jump repair (section 2) | `--repair auto`: the loop is cut as filmed; `jump_repair` reads `applied: false`, `why`, `rife` (what was not found), `install`, and the worst jump's score; one `video-loop: warning:` line on stderr | `--repair on`: fails with the install line |
| `video-set` cycle alignment (section 4) | `--align-cycles auto`: the state is skipped and every loop keeps its own length; `cycle_align.<state>` reads `applied: false` with `why`, `rife`, `install`; the set report lists it under `warnings`; one `video-set: warning:` line | `video-cycle-align`: fails with the install line |

A RIFE that is found and then fails (a binary that cannot reach Vulkan, say) is an error in every
mode: the install is there and broken, which a warning would hide. A jolt gate passed to a loop
whose repair could not run (section 3) reads the loop as filmed, and its failure says the
repair did not run.

RIFE reads three colour channels and no alpha. A frame is therefore interpolated as two
images — its colour premultiplied over black, and its coverage as a grey image — and put back
together unpremultiplied (`sprite_gen/video/rife.py`). Coverage below 2/255 is dropped, so no
faint halo is invented around the body.

### Where — measured 2026-10-03

One real pair: two frames of a Lite side walk two apart (545 x 544, premultiplied), the frame
between them as the truth. Time per RIFE call, wall clock, process start included.

| Where | How | Time per call | Mean abs error vs the true middle frame |
|---|---|---|---|
| Local Mac, M4 Max | Apple GPU through MoltenVK (`-g` auto) | 0.33–0.41 s | 3.32 (the frame either side: 7.77) |
| Local Mac, M4 Max | ncnn CPU path (`-g -1`) | 0.28–0.39 s | **39.3 — wrong output** |
| Modal, Linux, default reservation (as `run_job`) | Mesa llvmpipe software Vulkan (`-g` auto) | 2.8–3.0 s (first call 6.9 s) | 3.32 |
| Modal, Linux, `cpu=2` | llvmpipe | 1.6–3.0 s over two runs (first 4.3–7.4 s) | 3.32 |
| Modal, Linux, `cpu=8` | llvmpipe | 2.7 s (first 6.6 s) | 3.32 |
| Modal, Linux, `cpu=2` | ncnn CPU path (`-g -1`) | 0.72–0.85 s | **39.3 — wrong output** |
| Modal, T4 GPU | — | not run: the workspace has no payment method for GPU functions | — |

Read:

- **llvmpipe gives the same frame as the Apple GPU**, to the third decimal of the error. It is
  the CPU route on Linux. The binary's own CPU path (`-g -1`) is fast and wrong on both
  platforms with this model; the engine never passes it.
- The first call in a fresh container compiles llvmpipe's shaders (4–7 s); later calls in the
  same container do not.
- More cores barely help (8 cores: 2.7 s against 2.8–3.0 s), so the default reservation is
  the right size.

### Decision

**RIFE runs where the engine runs.** On a Mac that is the Apple GPU. In the app it is the
Modal job container itself, on CPU through llvmpipe — no GPU function and no second service:

```
apt_install("libvulkan1", "mesa-vulkan-drivers")    # the Vulkan loader + llvmpipe
curl -fsSL -o /tmp/rife.zip https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-ubuntu.zip
echo '1e2c7ee7fa7daa326542d50622f0afedc80cf6f1858bda411d16385ffa5cdf68  /tmp/rife.zip' | sha256sum -c -
unzip -q /tmp/rife.zip -d /opt && ln -s /opt/rife-ncnn-vulkan-20221029-ubuntu/rife-ncnn-vulkan /usr/local/bin/
```

`sprite-gen rife install` (after the package, as the image's own user) does the same download,
hash and unpack, and adds the check frame; CI installs RIFE that way.

Cost on Modal (prices read from modal.com/pricing on 2026-10-03: CPU $0.0000131 per core per
second, memory $0.00000222 per GiB per second): one repaired frame is two calls, about 6 s; a
loop repairs at most three frames, so at most about 20 s of one container — well under a cent.
Aligning a set's cycles (section 4) makes more frames: about 6 s per frame RIFE makes, so a
view that needs 20 made frames adds about two minutes on Modal and a few seconds on a Mac.
That is the reason section 4 keeps every frame that lands on a source frame.

A PyTorch RIFE was not chosen: it would add a framework of hundreds of megabytes to the image
for the same model, and llvmpipe already gives the GPU's frame.

### Licences

| Part | Licence | Source (read 2026-10-03) |
|---|---|---|
| rife-ncnn-vulkan (code, release binaries) | MIT, © 2020 nihui | `LICENSE` in the repository and in the release zip |
| RIFE (the network) | MIT | github.com/hzwer/ECCV2022-RIFE; the authors add that they "respect the commercial behavior of other developers" |
| RIFE v4.x weights (Practical-RIFE) | MIT — "The content of these links is under the same MIT license as this project." | github.com/hzwer/Practical-RIFE |
| ncnn | BSD 3-Clause | github.com/Tencent/ncnn |

sprite-gen ships none of these. A deployment that bakes the release zip into an image ships
the zip's `LICENSE` with it.

## 2. Jump frames — `video-loop --repair auto`

A walk or run loop (`--state walk|run`) is read, after it is cut and anchored, as it will play:
cyclic, the last frame followed by the first, inside the union box of the body over the loop
(the strip cells' own crop). For every step k → k+1 two numbers are taken — the mean change of
coverage over the whole box, and over the hair behind the body, each divided by its own median
over the loop — and the larger is the step's score.

| Rule | Value | Why |
|---|---|---|
| A jump | a score of at least **1.4** (`JUMP_RATIO`) | the ponytail cuts the experiment found sat at 1.5–2.5; a smooth take's worst step sits near 1.3 |
| The hair box | (0, 0.30)–(0.45, 0.80) of the union box for a right-facing body; mirrored for `--facing left` | below the head and behind the body, where a ponytail cut at the wrong moment jumps |
| The frame remade | the frame after the jump; but when the step *into* frame k is a jump too and larger than the step after k+1, frame k itself (a single stray frame breaks two steps) | remaking the frame after a stray frame leaves the stray standing |
| How | RIFE's frame half way between the frame's two neighbours | every other frame stays the video's own |
| How many | at most **3** (`MAX_REPAIRS`), worst first, scores re-read after each | a loop that needs more is jolting everywhere, not jumping once |
| Never | a frame next to one already made | two made frames side by side are made from each other and melt the legs |

Re-making every frame (an offset of half a frame) is not offered: it softens the frames that
were fine, and it was judged "not corrected" (2026-10-03).

The report's `jump_repair` carries `replaced` (cycle frame indices), each round's step, score and
its whole/hair parts, `score_max_before` / `score_max_after`, why it stopped, and which
interpolator made the frames. When a frame was replaced the seam gate measures the rendered cells
(`seam_measurement: rendered-cells`), because the source frames no longer say what plays.

RIFE is located only when a frame is to be made. A loop with a jump and no RIFE is cut as filmed
with a warning under `--repair auto`, and fails under `--repair on` (section 1, "Without RIFE").
`--repair off` cuts the loop as filmed and records `jump_repair: {"applied": false, "why":
"--repair off"}`. Other states are not touched (`"why": "not a gait state (…)"`).

Checked on the 2026-10-03 takes (`retime.py` in the experiment folder is the reference): the
engine replaces the same frames as the experiment on every take tried — Lite side E1
[0, 16, 8] (score 1.75 → 1.42), Lite back-diagonal NE2 [13, 0] (1.60 → 1.43), Pro side E1
[3, 15, 19], Pro back-diagonal NE2 [9], Lite front S2 [0, 11, 14], and nothing in the smooth
Lite side E2. About 0.7 s a replaced frame on the Mac's GPU.

## 3. The jolt index — reported on every walk, a gate only when asked

A loop whose steps change unevenly jolts even when no single step is a jump. Every walk or run
loop's report carries `jolt`, measured on the loop as it will play (after section 2's repair):

| Field | What |
|---|---|
| `index` | the **jolt index**: for every step, how far its coverage change strays from the mean of its two neighbours' changes; the mean of that over the median step. 0 for even steps; alternating big and small steps read high |
| `hair_index` | the same inside the hair box |
| `step_max_over_median` | the largest step over the median (what section 2 repairs) |
| `head.x`, `head.y` | the head's place frame by frame in % of the body's height over the loop: x (sideways) the coverage centroid of the top fifth, y (up and down) the body's top line. Each: `step_max_pct` (largest move in one frame), `step_median_pct`, `max_over_median`, `range_pct`, `worst_into_frame` |
| `reference`, `warnings` | the reference bounds (jolt index **0.43**, head sideways step **0.75** % of the body height) and what exceeds them, in words |
| `gate`, `gated`, `over`, `passed` | the bounds the caller passed, whether they were enforced, what exceeded them |
| `measured` | `after the jump repair` or `as filmed` |

**By default nothing fails on the jolt.** A loop beyond a reference bound is kept, its report
lists the reason under `warnings`, and `video-loop` prints a
`video-loop: warning: … (reference bound; the loop is kept …)` line on stderr. The aim is fewer
refilms — section 2 repairs what it can without a new clip — and the reference bounds rest on
too few judged takes to refilm on (below).

**A gate only when asked.** `--jolt-max` and/or `--head-step-max` turn the bound passed into a
gate on the repaired loop: beyond it the loop fails with
`video-loop: loop jolts — … regenerate the clip`, which a caller that buys a new clip can match.
Only the bounds passed are checked. With `--repair off` the loop is cut and reported as filmed,
never gated. A frame the head cannot be tracked in is named rather than read as smooth.

### Where the reference bounds come from — 46 takes, 2026-10-03

Eight-direction SD set, five generated views, Pro and Lite, four filming rounds (46 loops cut by
`video-loop --anchor motion-auto`), measured on the strip cells before and after section 2's
repair. Eleven of them were judged by the maintainer: nine kept for the final sets, two
refused for how they move — a Lite side walk whose every step alternated (its sibling take was
"completely natural"), and a Lite back-diagonal walk whose head "jumps".

| After the repair | min | p25 | median | p75 | p90 | max | the 9 kept reach | the 2 refused |
|---|---|---|---|---|---|---|---|---|
| jolt index | 0.055 | 0.178 | 0.212 | 0.243 | 0.260 | 0.414 | 0.414 (Pro back diagonal) | 0.237 (side), 0.196 (back diagonal) |
| head sideways step, % | 0.18 | 0.43 | 0.68 | 0.83 | 1.07 | 1.71 | 0.68 | 0.27 (side), **0.82** (back diagonal) |
| head top-line step, % | 0.49 | 0.98 | 1.24 | 1.71 | 2.47 | 3.17 | 2.42 (Lite back view) | 0.75, 1.71 |

Before the repair the jolt index's median was 0.258 (max 0.456) and the head sideways step's
0.70 % (max 2.88 %): the repair lowers both.

Read:

- **The head's sideways step is the only measure that separates the judged takes**: every kept
  take stays at or under 0.68 %, the back-diagonal take refused for its head moves 0.82 %. The
  reference 0.75 sits between them.
- **The jolt index does not separate them.** The refused side walk (0.237) sits inside the kept
  range, and a kept Pro back-diagonal walk reads 0.414 here — 0.4225 on the full-size cut frames
  `video-loop` itself reads (section 5). Any bound under that would flag a kept take, so the
  reference (0.43) sits at the kept takes' edge and flags nothing in this set. An alternating jolt
  like that side walk's is a known miss of this index.
- **The top line is reported, not bounded**: a walk seen from behind bobs more (the kept Lite
  back view reaches 2.42 %), so one bound would flag kept back views or pass the refused one.
- **Why no gate by default**: at the reference bounds, 18 of the 46 takes exceed one — 16 of 30
  Lite, 2 of 16 Pro, none of the nine kept. Lite's head sways about three times as far as Pro's,
  so as a default gate it would refilm about every other Lite walk: the opposite of the aim, on
  the strength of two refusals. The bounds stay a reference until more takes are judged.

## 4. One cycle for a direction set — `video-cycle-align`, `video-set --align-cycles auto`

Each direction of a walk is filmed on its own and comes out its own length (one Lite set:
16, 19, 21, 25 and 27 frames). A game that turns a character mid-stride wants every direction the
same number of frames, starting on the same step.

```bash
sprite-gen video-cycle-align --loop-dir set/front-walk/loop --loop-dir set/side-walk/loop \
  --loop-dir set/back-walk/loop [--length N] [--report set/walk.cycle-align.json]
```

- **Length**: the median of the set's own lengths (`--length` overrides). The median is the
  length that needs the fewest made frames across the set; a loop already that long is not
  touched.
- **Resample, offset 0**: frame k of a loop of L frames resampled to L* is the source at time
  k·L/L*, cyclic. A time within 0.03 of a source frame takes that frame as filmed; only a time
  between two frames is made, by RIFE at that fraction (section 1). An offset of half a frame,
  which would remake every frame, is not offered (section 2).
- **Foot strike**: each loop is then turned to start where the body is lowest (its solid top
  line lowest, smoothed 1-2-1) — both feet down just after a heel lands. A walk has two such
  moments; the first is taken.
- **Rebuilt in place**: `cycle/`, `<name>.strip.png` / `.strip.json`, `.gif`, `.webp` are
  rewritten at the loop's own cell rules (`cell_height_cap`, body-height target, anchor), at the
  loop's frame rate, so the aligned cycle lasts L*/fps seconds. `strip.json` gains
  `cycle_align` (`from`, `to`, `taken`, `made_by_rife`, `made_at`, `turned_by`, the seam ratio of
  the rebuilt cells, and the re-verified GIF/WebP).
- **The cut as filmed is kept** in `cycle.source/` on the first alignment, and every later
  alignment reads from there: running it again, or at another length, never resamples a
  resampled loop. Every loop of the set is resampled before any is rewritten, so a loop that
  cannot be made leaves the set as it was.
- Loops at different frame rates are refused (one length in frames would mean different
  durations), and so is a loop cut before `cell_height_cap` was recorded (cut it again).

`video-set` runs this after its loops are cut, once per walk or run state filmed in two or more
directions (`--align-cycles auto`, default; `off` keeps each loop's own length). Its report
carries `cycle_align` per state and `<state>.cycle-align.json`; a failed alignment is listed as
`cycle-align:<state>` and the loops stay as cut. Without RIFE the alignment is skipped, not
failed (section 1, "Without RIFE"); install it and run `video-cycle-align` on the set's loops.
Cutting a loop again with `video-loop` removes its `cycle.source/`, so the next alignment reads
the new cut.

How much RIFE that is, on the 2026-10-03 sets (the experiment's `finalize.py`): resampling to
the median made 18 of 21 frames for a Lite side loop of 27, 20 of 21 for a back-diagonal loop of
19, and none for the loop already 21 long; Pro, 22 of 24 for a front-diagonal loop of 34. The
final set showed a Lite back-diagonal walk that jumped; seen at three stages (as filmed, jumps
repaired, aligned), it jumped at all three, so the take — not the resampling — was the cause
(maintainer's judgement, 2026-10-03).

## 5. Regression — the 2026-10-03 eight-direction set through this engine

The kept picks of both sets (Pro and Lite, five views each) and the refused Lite side take, from
their keyed frames through `video-loop --state walk --anchor motion-auto --body-height 400
--strip-height 640` and then `video-cycle-align` per set, on an M4 Max with the Apple GPU. The
experiment's own scripts (`retime.py`, `finalize.py`) are the reference.

| | Cut (start, length) | Frames repaired | Jump score before → after | Jolt index | Head sideways step, % | Warning |
|---|---|---|---|---|---|---|
| Pro front | 26, 23 (same) | [9] (same) | 1.42 → 1.37 | 0.221 | 0.51 | — |
| Pro front diagonal | 1, 34 (same) | [0, 10] (experiment [0, 11]) | 2.00 → 1.66 | 0.079 | 0.18 | — |
| Pro side | 23, 24 (same) | [3, 15, 19] (same) | 1.81 → 1.64 | 0.244 | 0.44 | — |
| Pro back diagonal | 17, 24 (same) | [9] (same) | 2.53 → 2.08 | 0.4225 | 0.19 | — |
| Pro back | 7, 20 (same) | [6, 12] (same) | 1.72 → 1.44 | 0.245 | 0.42 | — |
| Lite front | 4, 25 (same) | [0, 11] (experiment [0, 11, 14]) | 1.69 → 1.38 | 0.230 | 0.50 | — |
| Lite front diagonal | 19, 21 (same) | [8, 18] (same) | 1.42 → 1.33 | 0.175 | 0.69 | — |
| Lite side | 25, 27 (same) | none (same) | 1.35 | 0.055 | 0.33 | — |
| Lite back diagonal (refused: "it jumps") | 30, 19 (same) | [13, 0] (same) | 1.60 → 1.42 | 0.192 | **0.82** | head sideways 0.82 % |
| Lite back | 50, 16 (same) | [15, 3] (same) | 1.82 → 1.50 | 0.163 | 0.57 | — |
| Lite side, refused take | 39, 23 (same) | [0, 16, 8] (same) | 1.72 → 1.42 | 0.238 | 0.28 | — |

| Set | Lengths as cut | One cycle | Frames made by RIFE (per view) | Experiment |
|---|---|---|---|---|
| Pro | 23, 34, 24, 24, 20 | 24 | 65 (23, 22, 0, 0, 20) | 24; 23, 22, 0, 0, 20 |
| Lite | 25, 21, 27, 19, 16 | 21 | 78 (20, 0, 18, 20, 20) | 21; 20, 0, 18, 20, 20 |

- Every cut is the one the 2.17 engine made; the set lengths and the frames RIFE made match the
  experiment view by view.
- The repair replaces the same frames as the experiment in 8 of 10 views. The two that differ are
  read at full size here and on the scaled-down strip there; both differences are one frame.
- At the reference bounds the only warning is the Lite back-diagonal take the maintainer refused —
  for its head, 0.82 % sideways in one frame.
- About 12–17 s a loop (mostly the motion analysis) and about 1 s per frame RIFE made.

A side-by-side video of the takes as filmed and as this engine leaves them, the numbers, and the
scripts that made them are kept with the experiment's material in the maintainer's notes.

## Related

- [video-pipeline.md](video-pipeline.md) — the pipeline these repairs run inside
- [loop-review.md](loop-review.md) — the decisions a loop records for review
- [docs/README.md](README.md) — documentation index
