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
images — its colour, and its coverage as a grey image — and put back together
(`sprite_gen/video/rife.py`). Coverage below 2/255 is dropped, so no faint halo is invented
around the body.

The two images are two runs of the flow, and where the picture is hard to follow — two legs
passing each other — they do not agree: the coverage says body where the colour run still
carries what lay around the body. Until 2.24 the colour was premultiplied over black, so that
disagreement came out as a black smear between the legs. The colour image now holds the
body's own colour around the body (`rife.bleed`): the colour from 0.8 % of the body's height
inside its solid edge — past a drawn outline — pushed out over the transparent area, coarse to
fine. A disagreement then reads as the body's fill. Where the frame has coverage its colour is
its own, outline included, and it is taken from the colour run as it comes out (no division by
the coverage). A limb RIFE cannot follow at all still comes out pale and soft, never black;
and where two drawings are too far apart for the flow — legs crossing a long way, as a clip
drawn on twos gives between its drawings — the legs come out as one shape of fill with no
outline between them or around them. `rife.smear` measures all three, and a set alignment takes
the nearer source frame where a made frame melted (section 4).

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
| `seam_pop` | the top band's jump into the loop's first frame over the loop's median step ("The seam pop" below); a pop over its bound adds a line to `warnings` |
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
  back view reaches 2.42 %), so one bound would flag kept back views or pass the refused one Only its
  step into the first frame is weighed, against the loop's own steps (below).
- **Why no gate by default**: at the reference bounds, 18 of the 46 takes exceed one — 16 of 30
  Lite, 2 of 16 Pro, none of the nine kept. Lite's head sways about three times as far as Pro's,
  so as a default gate it would refilm about every other Lite walk: the opposite of the aim, on
  the strength of two refusals. The bounds stay a reference until more takes are judged.

### The seam pop — a part held above the head, swinging on its own beat

A staff, a flag or a raised spear can sway on a beat of its own, slower than the steps. A cut one
step long then ends with it somewhere else: the body closes at the wrap and the held part jumps.
The seam ratio does not see it — it is an area measure, and a thin rod moved many times its usual
step changes few pixels — and the head's sideways bound above sees it only when the jump is large
in absolute terms. What sees it is the top band of `head.x`/`head.y`: on a body holding something
higher than its head, that band is the tip of what it holds.

Every walk and run loop's `jolt` carries `seam_pop` (`repair.seam_pop`), measured on the loop as it
plays:

| Field | What |
|---|---|
| `x`, `y` | per axis of the top band: `wrap_pct` (its step from the last frame into the first, % of the body height), `step_median_pct` (the loop's median step), `wrap_over_median` (the wrap over the median, the median a pixel at least: a step under a pixel is the tracker's rounding) and `wrap_is_largest` |
| `pop` | the larger `wrap_over_median` of the axes whose largest step is the wrap; 0 when neither's is |
| `reference`, `pops` | the bound (**10**, `repair.SEAM_POP_REFERENCE`) and whether `pop` exceeds it |

- **Only the wrap, and only when it is the largest step.** The question is whether the cut closes,
  not whether the top moves: a walk seen from behind bobs, and hair redrawn every frame jumps
  inside the loop as much as at the wrap — for either the wrap is ordinary, and `pop` stays 0.
- **`--anchor motion-auto` chooses again.** The search measures the same thing on the frames it
  chooses from (moved by the analysis translation, `loop.wrap_pop_on`), for its first choice only.
  If that pops, every candidate is measured, and among those that do not pop it takes the scores
  within the usual 15 % of the best and the smallest pop (`cycle.seam_pop`: `first_choice`,
  `applied`, `measured`, `unread`, `chosen`). A first choice that does not pop is kept, and nothing past it
  is measured, so a loop without such a part is cut exactly as before. If every candidate read pops,
  the first choice is kept (`why`) and the warning below says so.
- **A cut whose top cannot be read is not one that closes.** A frame with nothing in the top band
  (a tip flung far above the rest in one frame) leaves the cut without a jump to read: it is
  recorded `skipped` (why) in place of `pop` — `first_choice.skipped`, or on a candidate row
  `seam_pop: null` with `seam_pop_skipped` — and counted under `unread`, apart from `measured`.
  It is never chosen on; a first choice that cannot be read is kept, nothing past it is measured,
  and `jolt.warnings` says the head could not be tracked.
- **A pop that stays is a warning line** — `video-loop: warning: the top of the silhouette jumps
  into the loop's first frame up or down … (over 10x): a part held above the head swings on its
  own beat and does not close at this cut`, in `jolt.warnings` like the bounds above. A sideways
  jump into frame 0 that the head bound already names is not said twice. A fixed or `--cycle
  periodic` cut is not chosen again; the warning is all it gets. Nothing fails on it.
- **The bound** sits between walks holding a staff that swings on its own beat and walks without
  one, both on the cut as it plays and on the source frames the cut is chosen from. The synthetic
  walker of `tests/video/test_loop_prop_seam.py` — steps every 24 frames, a staff above its head swaying every 61 — reads 12 to 18 at the cuts the area
  measure alone chose, and 0 at the cuts chosen again.
- **What it does not see**: a held part that never reaches the top band (a spear held level, a
  sword at the hip), and the lower end of a staff whose top closes while the shaft still swings
  about the hand. A clip like that may need a cut two steps long, which the search does not make;
  `--cycle fixed` cuts one ([loop review](loop-review.md)).

## 4. One cycle for a direction set — `video-cycle-align`, `video-set --align-cycles auto`

Each direction of a walk is filmed on its own and comes out its own length (one Lite set:
16, 19, 21, 25 and 27 frames). A game that turns a character mid-stride wants every direction the
same number of frames, starting on the same step.

```bash
sprite-gen video-cycle-align --loop-dir set/front-walk/loop --loop-dir set/side-walk/loop \
  --loop-dir set/back-walk/loop [--view front --view side@right --view back] [--start-foot right] \
  [--between auto|rife|nearest] [--length N] [--cycles back-walk=2] [--multi-cycle fail|warn] \
  [--foot side-walk=left] [--state walk] [--report set/walk.cycle-align.json]
```

- **Two cycles in one loop are stopped, and counted by whoever looks**: one length for the set is
  one beat only if every loop holds one cycle. A loop that holds two strides, resampled to the
  set's length, walks twice as fast as the rest, arms and legs alike — and pixels cannot tell it
  from a loop of one stride whose two steps look alike ([video pipeline](video-pipeline.md)
  section 4, "The fundamental period"). So before anything is resampled each loop is screened as a
  ring (`align.cycle_screen`, the rule `video-loop` records by, `period.verdict`): a half or a
  third of it that repeats and is no shorter than the state's gait floor makes the loop a
  **suspect** — whatever its pose, which is recorded as evidence. A run set stops nearly every
  time. The state is the one `video-loop` writes in
  `strip.json` (`state`); `--state` gives it for loops cut before.
  - A set with a suspect is **stopped before anything is rewritten** (`--multi-cycle fail`, the
    default): the message and the report name each loop, where it returns (frames and seconds),
    the evidence (`pose`, `steps`), the arguments that settle it, and the command to run once
    they are counted (`command`: the same alignment with `--cycles <loop>=<1|2>` for each, to
    fill in). With no vision call to ask, the agent looks at the loop's frames and gives the count. The report is written with
    `applied: false`, `refused: "cycle-suspects"` and `suspects` (per loop: `dir`, `name`,
    `length`, `status`, `candidates`, `settle`); `align.CycleSuspects` carries the same list.
  - **`--cycles <loop>=k`** is the count that comes back (repeatable; `<loop>` is the
    `--loop-dir`, its strip's name, its directory's name, or for a `video-set` item's `loop`
    directory the item's name). `1`: it holds one cycle, aligned as it is. `2` or `3`: one cycle is
    taken out of the loop as filmed (`cycle.source/`) — round(L/k) frames, from the start whose
    frame one cycle on is the most like it, read as a ring (`align.take_cycle`) — and that is what
    is resampled; the loop row records `cycles_given` and `cycle_taken` (`from`, `start`,
    `length`, `exact`, `repeat_over_step`, `seam_ratio`). `video-loop` is not run again, and a
    later alignment reads the same filmed frames. A count given where the screen found nothing at
    1/k is still honoured, with a warning.
  - Nothing else changes how many cycles a loop holds: without `--cycles` every loop is resampled
    from all its filmed frames.
  - `--multi-cycle warn` aligns a suspect set as it is, a warning per suspect.
  - Who counts: a person looking at the loop, or a vision call asked how often each foot lands.
    A loop counted once is not remembered — the next alignment needs the same `--cycles`.

- **Length**: the median of the set's own lengths (`--length` overrides). The median is the
  length that needs the fewest made frames across the set; a loop already that long is not
  touched.
- **Resample, offset 0**: frame k of a loop of L frames resampled to L* is the source at time
  k·L/L*, cyclic. A time within 0.03 of a source frame takes that frame as filmed; only a time
  between two frames is made, by RIFE at that fraction (section 1). An offset of half a frame,
  which would remake every frame, is not offered (section 2).
- **Between two source frames** (`--between`): `auto` (default) makes the frame with RIFE,
  measures it (below) and keeps it unless it has a fault, where the nearer source frame is taken
  instead and named; `rife` keeps every made frame and names the faulty ones; `nearest` takes the
  nearer source frame every time, so nothing is made and no RIFE is needed, and the motion keeps
  the filmed frames at up to half a frame off their time (a loop stretched longer shows a frame
  twice, which the GIF and WebP hold as one frame of twice the delay). `video-set` passes
  `--align-between`. `auto` runs RIFE for every frame between two source frames, as `rife` does,
  and needs it installed the same way.
- **Smear and melt, per made frame**: `cycle_align.smear` lists every frame RIFE made, with its
  `method` — `rife` (kept) or `nearest` (the nearer source frame taken instead) — its `faults`,
  and what it has that neither source frame beside it has (`rife.smear`):
  - `dark_excess`: dark pixels (luma under 70/255) inside the body beyond the darker neighbour's
    count, as a fraction of its solid pixels — a black smear raises it, a dark part that only
    moved (a hat, a watch) does not. Over 0.1 % it is the fault `smear`.
  - `outline_loss`: of the frame's coverage edge (alpha from 0.1, so a pale ghost limb's edge
    counts), the share with no dark solid pixel within 2 px, beyond the less outlined
    neighbour's, as a fraction of its edge. Legs that crossed too far for the flow melt into one
    shape of fill and lose their outline where they meet the air; a limb left as a ghost has none.
    Over 5 % it is the fault `outline`. A step the flow follows keeps its outline; a figure drawn
    without outlines loses none against neighbours that have none, and is not judged by it.
  - `partial_excess`: part-covered pixels beyond the more ragged neighbour's, as a fraction of
    its solid pixels. Reported, not judged: a melted frame and a clean one read alike on it.

  Every fault is a line in the report's `warnings` (and on stderr, and in `video-set`'s
  `warnings`), whichever `--between`: under `auto` it says the nearer source frame was taken
  there; under `rife` that the frame is kept, to look at in `cycle/`. The report counts the
  frames kept from RIFE (`made_by_rife`) and those replaced (`replaced`); a loop row lists both
  (`made_at`, `nearest_at`).

  Why `outline_loss` and not the dark count's other side: a frame half way between two drawings
  has fewer dark pixels than the darker one wherever the two differ — a watch half hidden, an
  outline between overlapping legs — so a clean frame reads below zero too. On two outlined legs
  (`tests/video/test_rife.py`) RIFE's clean frames of a short step read `dark_excess` −1.3 %,
  lower than its melted frame of a long crossing (−1.1 %); `outline_loss` reads under 3 % for the
  short step at every fraction and 32 % for the melted frame. The 5 % line is where a frame stops
  reading as a little soft at a foot and starts reading as melted; it is a reference, not a
  measured optimum, and frames near it are worth a look either way.
- **What `auto` costs**: a frame replaced shows the nearer source frame, so the motion there
  steps as filmed, up to half a frame off its time, and the loop shows fewer distinct drawings a
  second than `rife` — but no melted one. A clip drawn on twos has a long step between drawings
  everywhere, so it is replaced most; the frames that land on a small step stay RIFE's.
- **Held drawings — a take to film again** (`retake`, reason `held-drawings`): a loop whose
  clip shows each drawing for two or three frames has a gap between drawings two or three frames
  wide. Played at its own rate that is limited animation, 12 or 8 drawings a second. Made longer,
  or merely resampled, the frames in each gap are made across a step two or three times as long
  as an every-frame clip's, and where the legs swap places inside it — the near leg passing behind
  — no interpolator draws them: a flow model blends the two drawings into a ghost or a melted
  shape, which `auto` replaces with the nearer source frame, and that drawing is held a frame
  longer: the loop halts there. A better interpolator is not the fix; a take drawn every frame is.
  - **The hold is the cut's**: `video-loop` measures it on the clip's steps as keyed, before any
    anchor moves a frame, twice, and writes both into `strip.json` ([video
    pipeline](video-pipeline.md) section 4): over the cut (`cycle_drawings`: `start`, `length`,
    `steps`, `hold`, `drawings_per_second`, `contrast`, and a `why` where it was not read) and over
    the whole clip (`drawings`: `hold`, `drawings_per_second`, `contrast`, `frames`). The alignment
    reads the cut's (`source: "span"`, the cycle's drawings its frames at the cut's rate, the
    clip's reading beside it under `clip`): a clip held for part of its length and drawn every
    frame for the rest is held where it was cut, so a cycle cut from its every-frame part is not
    named because the clip reads as held, and one cut from its held part is not passed because the
    clip reads as drawn every frame. The cut's steps run from its first frame into the frame after
    its last, which the cycle returns to, so a cut that starts mid-pair reads as the pairs it holds.
    A cut of fewer than 12 steps (`held.SPAN_MIN_STEPS`, one window of the clip's reading) is not
    read on its own: the clip's reading stands (`source: "clip"`), and the row's `span_why` says
    why — as it does for a loop cut before the cut's record. Both are kept through a rebuild. The
    cut frames themselves are not read while a record exists: `--anchor motion-auto` shifts each,
    so a pair's repeat may no longer read as one; only a loop cut before either record is measured
    on its own cycle, read as a ring (`source: "cycle"`).
  - **The rule**: a loop is named when its cut is held (`hold` 2 or 3), the set's length leaves
    it under **13 drawings a second** (`align.RETAKE_DRAWINGS_MIN`), and **one or more** frames
    between its drawings were not made (`align.RETAKE_UNMADE_MIN`) — taken from the nearer source
    frame (`auto`'s replacements, every made time under `nearest`) or made with a fault and kept
    (`rife`). Each loop row carries `drawings` and `retake` (the record, or `null`): `reason`,
    `hold`, `source`, `drawings` (in the cycle), `drawings_per_second_filmed`,
    `drawings_per_second` (at the set's length), `frames_per_drawing` (the gap, in frames of the
    aligned loop), `unmade`, and the `limits`. The report's `retake` lists every such loop (`dir`,
    `name` and the same numbers), a warning line names it ("film this direction again
    (held-drawings) — …", by its directory where two loops share a strip name), and `video-set`
    carries both into its record.
  - **Not named**: a held loop at its own length (nothing is made — on twos it plays at 12 a
    second, as filmed), a held loop whose made frames all kept their outline, a held loop squeezed
    to 13 drawings a second or more (its drawings come closer than on twos), and a loop drawn every
    frame with a replaced frame (one soft step, not a gap the take cannot fill).
  - **Exit code and words**: the set is still aligned and written (`applied: true`, exit 0) — the
    loop is the best this take allows, and the caller decides: a pipeline with a retake budget
    films that direction again and aligns the set anew; one without delivers it with the warning.
    Nothing passes quietly: the line is on stderr and in `warnings`, and `retake` is the
    machine-readable reason.
  - **Why 13**: a clip on twos shows 12 drawings a second. At that rate or slower each frame not
    made is a drawing held a frame longer at the moment the legs cross, which reads as a halt; a
    line a little above 12 keeps a cut that is not all pairs, or a cycle made a frame or two
    longer, on the same side. The lines are references, not measured optima, and a loop near them
    is worth a look either way.
- **Foot strike**: each loop is then turned to start as a heel lands (`align.foot_strike`), read
  off one signal smoothed 1-2-1 — never off the frame's top edge, which a long ear, a hat's
  point or an antenna owns and which flops on its own rhythm:
  - `stride` — a side or diagonal walk: the width of the foot band (the lowest 8 % of the frame)
    is widest as the front heel lands.
  - `reach` — a front or back walk, whose feet pass one behind the other: the foot nearer the
    viewer is drawn lowest, and lowest when the feet are furthest apart, so the lowest solid row is.
  - `body_low` — a body with no legs to read: its top line lowest, the top line being the first
    row whose longest solid run is at least half the frame's widest, so a narrow ear or antenna is
    passed over.

  With a view (`--view`, below) the view picks the signal, and the swings only say whether there
  are legs to read: a side or diagonal view turns on `stride` where the foot band swings by 15 %
  of the body's height or the lowest row by 1.5 %; a front or back view turns on `reach` where the
  lowest row swings by 1.5 %. A short-legged figure's swings mislead a threshold either way: from
  the front its foot band widens past 15 % as the foot behind lifts out of the band, mid-step, and
  from the side a short step opens the feet by less than 15 %. Without a view the picture alone
  says: `stride` where the foot band swings by 15 % or more, else `reach` where the lowest row
  swings by 1.5 %, else `body_low` (and the report says no view was given). A loop with a view and
  no legs to read has `foot_why` with both swings.

  A body that hardly bobs still turns on its legs; the body's bob is read only where there are
  no legs. The loop row says which (`turned_on`) and the two swings (`stride_swing`,
  `reach_swing`).
- **The same foot in every view** (`--view`, one per `--loop-dir` in order: `front`, `back`, or
  `side|front_diagonal|back_diagonal@right|left` for the way it faces; `video-set` passes each
  item's own): a walk lands twice a cycle, half a cycle apart. Each loop starts as the same own
  foot lands (`--start-foot`, default right), told apart by how the view draws the feet
  (`align.strike_foot`; which own side is where is `handedness.placement`):
  - front or back (`depth`): the feet are side by side in the picture, the one nearer the viewer
    drawn lower. From the front the landing foot is the one stepping toward the viewer, the
    lower; from the back it is the one stepping away, the higher.
  - side or diagonal (`shade`): the feet are one in front of the other, so the picture cannot
    place them, but the far leg is drawn in shade. At the strike where the front foot is the
    lighter against the back one, the near leg is in front.

  The cue must follow the step — its once-a-cycle swing (first harmonic) at least a quarter of
  its spread over the cycle, or it is the drawing's own flicker — and, averaged over each strike
  frame and its two neighbours, the two strikes must differ by 0.025 in luma (shade,
  `align.SHADE_MARGIN`) or 1 % of the body's height (depth, `align.FOOT_MARGIN`); otherwise the
  foot is not named and `foot_why` starts **`low-margin`** (with the margin and the bar). A view seen
  from behind a diagonal can shade its legs too evenly for this, and is then left unnamed rather than
  guessed. The shade's bar is set from drawn walks whose feet were checked by eye: it lies above the
  margins at which the shade named a foot wrong and under those at which it named one right. A foot
  named wrong turns the loop half a cycle off with nobody asked; a foot left unnamed costs one look
  (a person or a vision call, `unnamed_feet` below), so a margin near the bar is left for the look.
  A loop row carries `view`, `start_foot` and `foot` (the cue, the strikes, their values, the
  margin, the feet); a loop whose foot is not named starts on its larger strike with
  `start_foot: null` and `foot_why`, and the report's `warnings` say so — a set whose loops do
  not all name their foot may not start on one foot. Without `--view` no foot is named and the
  report says that once. A character drawn without shading on the far leg leaves its side and
  diagonal views unnamed; mirroring a loop afterwards (a left view made from a right one) swaps
  its own feet. Every loop row says who named its foot: `start_foot_source` `"engine"` (the view's
  cue), `"given"` (`--foot`, below) or `null` (nobody), and `strikes` — its two strikes as frames of
  the rebuilt `cycle/`, the larger first.
- **A foot the engine cannot name is told, one loop at a time**: each loop whose foot is not named
  — its view's cue does not follow the step, parts the strikes by less than the margin
  (`low-margin`), or there are no legs to read — is listed in the report's **`unnamed_feet`**:
  per loop `dir`, `name`, `view`, `foot_why`,
  `candidates` (the two strikes: `strike` 0 and 1, `frame` in `cycle/`, `path` to that frame; the
  first is the frame the loop now starts on, the second half a cycle on) and `settle` (the two
  `--foot` arguments). Whoever looks — a person, the agent, or a vision call shown the two frames —
  says which own foot lands on the first candidate, and that comes back as
  **`--foot <loop>=left|right`** (repeatable; `<loop>` is named as for `--cycles`: the `--loop-dir`,
  its strip's name, its directory's name, or a `video-set` item's name). That loop alone starts as
  `--start-foot` lands — on the first candidate if it is that foot, half a cycle on if it is the
  other — and every other loop comes out byte for byte as it would have. The row records
  `start_foot_source: "given"`, `foot_given` (the feet at the two strikes) and, where the engine had
  not named it, `foot_unnamed_why`; the report records `feet_given`. A foot given for a loop the
  view did name is still taken, and where it disagrees the row carries `foot_disagrees` (the cue's
  reading) and a warning says so. A `--foot` that names no loop, two loops, or something other than
  `left`/`right` is refused before anything is rewritten. The answer is about the loop as filmed —
  the strikes are read from `cycle.source/` resampled again — so it holds for every later alignment
  at the same length, and like `--cycles` it is not remembered: the next alignment needs the same
  `--foot`. The algorithm that names feet is not changed by it.
- **Rebuilt in place**: `cycle/`, `<name>.strip.png` / `.strip.json`, `.gif`, `.webp` are
  rewritten at the loop's own cell rules (`cell_height_cap`, body-height target, anchor), at the
  loop's frame rate, so the aligned cycle lasts L*/fps seconds. `strip.json` gains
  `cycle_align` (`from`, `to`, `between`, `taken`, `made_by_rife`, `made_at`, `smear` and/or
  `nearest_at`, `drawings`, `retake`, `turned_by`, `turned_on`, `view`, `start_foot`, `start_foot_source`, `strikes`, the seam ratio of the rebuilt
  cells, and the re-verified GIF/WebP).
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
A set stopped on a suspect is skipped the same way (`reason: "cycle-suspects"`, the loops under
`suspects`, a warning naming them): count their cycles and run `video-cycle-align --cycles`.
A state whose loops leave a foot unnamed carries them under `cycle_align.<state>.unnamed_feet`; the
answer goes back as `video-set --align-foot <item>=left|right` (the item's name, e.g. `side-walk`;
only an item of a walk or run filmed in two or more directions is taken, any other is refused before
filming), or as `video-cycle-align --foot` on the set's loops. A foot given for an item that failed,
or whose state was not aligned, is named in that state's `feet_unused` and a warning.
Cutting a loop again with `video-loop` removes its `cycle.source/`, so the next alignment reads
the new cut. An alignment also clears a follow-through (`video-follow`, which moved the old
cells): `follow.source.png` and the strip's `follow` record are removed and the loop's row says
`follow_cleared`; run `video-follow` again after it.

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
