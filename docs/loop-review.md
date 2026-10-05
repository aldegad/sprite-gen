# Loop automation and visual review

Use the normal pipeline first. No model needs to choose every cut:

```bash
sprite-gen video-loop --frames-dir keyed/ --out-dir loop/ --state run --fps 24
```

The caller must pass the **measured source fps**, not always 24. `video-set` obtains it
from `video-frames`. `video-loop` defaults to `body` for walk/run and `none` otherwise;
`video-set` defaults to `none`. The default clip
request remains 3 seconds; it does not determine the playback speed.

## What the script decides

- It measures repeat candidates from the keyed frames and the state's time window.
- It scores the last displayed frame -> first frame against ordinary adjacent motion,
  reducing repeated-pose stalls at a loop boundary.
- Gaits below the duration floor may use a double period, as before. Above that floor,
  similarly credible non-exact candidates at P and approximately 2P now retain 2P once
  when it fits the window and passes the periodicity gate. Keeping two real repetitions
  is preferable to discarding a potentially distinct phase. This may produce a two-cycle
  loop of a genuine short gait; frames keep their original timing.
- Nothing is cut shorter than the period found. A half or a third of it that repeats is
  recorded as a suspect (`cycle.fundamental.suspects`); look at
  whether the loop shows one stride or two, and tell `video-cycle-align` with `--cycles`
  ([loop repair](loop-repair.md) section 4).
- Near-exact short repeats, implausible longer repeats, and non-gait states are not
  extended by that ambiguity policy. There is no character-specific frame count.
- Alpha, frame counts and seam metrics are checked before the output is accepted.

A numeric pass establishes those facts. It does **not** establish correct foot contact,
anatomical left/right alternation, a believable gait, or that the source was drawn well.
`cycle.review_recommended = true` marks an ambiguous-harmonic choice; `false` means
that this particular heuristic did not flag ambiguity, not that visual quality is certified.
The direct CLI summary preserves that flag and the guard evidence. `video-set` also
propagates them in its item report and marks the table row `OK (review gait)`.

## Material for a vision-capable reviewer

Provide the original clip, the generated loop played at source speed for at least three
repeats, the JSON report, and an ordered contact sheet with original frame indices. For
an ambiguous harmonic include both P and 2P candidates using the same size and fps.
A single still cannot establish a gait cycle. If the model cannot consume video, provide
ordered frames spanning at least two candidate cycles, including several frames on both
sides of the wrap. Do not ask it to judge motion from an unordered image collage.

A human or a vision-capable model should check:

1. Do the limbs alternate, or does one phase restart before its counterpart appears?
2. At the wrap, does motion stall, snap, or reverse compared with nearby frames?
3. Does the torso translate across the canvas, or are only the limbs/clothing moving?
4. Are deformation or contact errors already present in the source clip?

Return the evidence frame indices, chosen mode/start/length, anchor, reason and any
uncertainty. Keep that decision separate from the unmodified automatic report. A model
without the required visual input must not claim it reviewed the gait. Ambiguous or
contradictory visual evidence should remain unresolved rather than inventing a precise cut.

## Automatic XY correction and local cycle selection

For a fully automatic walk/run candidate, opt into `motion-auto`:

```bash
sprite-gen video-frames --clip clip.mp4 --out-dir frames/ --key auto \
  --spill auto --reference canvas.png --report frames.report.json
sprite-gen video-loop --frames-dir frames/keyed/ --out-dir loop/ \
  --state walk --fps <measured-source-fps> --anchor motion-auto
```

`video-set --states walk,run --anchor motion-auto` uses the same path. Other
states and fixed/manual cuts are rejected before generation. The defaults above
remain unchanged. This mode accepts no supplied regions or frame indices.

The script searches a grid in the upper part of the foreground for two separated,
textured regions. It ranks their normalized cross-correlation across time and
requires both to track throughout the sequence. These are stable image regions,
not semantic detections of a face, torso, species or limb. Untrackable patches are
counted as rejected candidates; absence of a usable pair is an explicit failure.
The correlation at every offset of a search window comes out of FFTs: per channel,
the masked patch sum, its squared sum and its product with the centered reference are
window correlations, so the masked patch is never copied out once per offset. The
costs agree with that direct form to about 1e-6, and two offsets that tie within it
may swap. On three 3 s walk clips at 544 px the region search took 1.0 to 1.4 s of
CPU instead of 22.6 to 37.4 s, and full loops cut from the same frames gave the same
cycle and byte-identical strips.

For selection only, the measured horizontal trajectory is locally fitted and the
vertical linear trend is removed. Local lag minima can then identify a repeat in
a clip with changing drift or cadence. Selection requires repeat depth, active
motion around both boundaries, and a supported doubled recurrence when a short
step falls below the gait floor. Candidate ranking also penalizes drift that a
single linear correction could not remove. Among scores within 15% of the best,
the earliest repeat is used; exact cut quantization may differ by one frame from
the lag minimum. When the top of the silhouette jumps into that cut's first frame —
a staff or flag held above the head, swinging on its own beat — the candidates are measured
and the cut is chosen again among those whose top was read and closes ([loop repair](loop-repair.md)
section 3, "The seam pop"; `cycle.seam_pop`). A first choice whose top closes, or cannot be read, is kept and
nothing more is measured. An explicit `--max-len` is never widened; without one, only the gait
fallback below looks past the state's window, and only after this search found nothing.

### The gait fallback: a slow walk, or a walk toward the camera

When the local search finds no cycle, `motion-auto` looks once more, for two things a
front or back gait does that the first search cannot see past. (Since 2.24.0 a clip that changes
size by 1 % or more, read one cycle on, is held at its first frame's size before the first
search — [video-pipeline.md](video-pipeline.md) section 4, `size_hold` — so the fallback below
seldom finds anything left to scale back unless `--size-hold off` was passed. It still reads the
line through the clip, so a clip the hold left as filmed, whose line reads 3 % or more, is scaled
back here if the first search found no cycle.)

- **It walked toward the camera, or away from it.** Asked to walk in place, a front walk
  sometimes comes closer, and the body grows through the clip, so the same pose never matches
  itself in size. A straight-line fit of the subject's opaque height measures it; at 3 % or
  more over the clip (`SCALE_DRIFT_MIN`) every frame is scaled back to the first frame's fitted
  height about its fitted foot point, with its coverage and its colour mapped apart
  (`transform_cell`, [video-pipeline.md](video-pipeline.md) "Cells") so the soft edge keeps its
  colour and gets no lighter rim or key tint from the resample. The
  search, the cells and the seam gate then all read the scaled-back frames. On the walk clips
  we have kept, the median change is 0.5 % and nine in ten stay under 2.7 %.
- **It walked slowly.** A calm walk in long clothing can take longer than half the clip per
  cycle, the most the first search confirms (a 3 s clip at 24 fps: 36 frames). The second
  search allows up to 60 % of the clip and at most 2 s (`LONG_CYCLE_FRACTION`,
  `LONG_CYCLE_SECONDS`; 43 frames for that clip), so a cycle is still seen once whole and
  repeating for the rest.

Every other gate is the same, the seam limit included. A clip the first search cuts is cut
exactly as before. The report records the first search's reason, the measured drift, whether
it was undone and the window (`gait_fallback`); if the second search fails too, the error
keeps its `no periodic cycle found` start and says what was tried. The failed report's `cycle`
then names the windows the search measured and refused (`status: "refused"`, `window`,
`refused_count`, and up to 12 `candidates`: `start`, `length`, `score`, `ratio`,
`context_repeat_over_step`, `drift_line_residual_analysis_px`, `seam_pop`, and `refused`, why),
best score first, those whose top could not be read (`seam_pop: null`, `seam_pop_skipped`) after
those whose top closes, and those whose top jumps at the wrap last, so a caller that must deliver
anyway (a forced `--cycle fixed`) cuts one the engine measured rather than the whole clip. The scaled-back frames are
written to `<out-dir>/.gait-fallback-frames` and removed once the strip is built or the run
fails.

Measured on six stored 3 s front walks that had failed with `no periodic cycle found`: four
loop now. A long kimono, which hides the legs, walked a 38 to 39 frame cycle (one clip also
grew 3.9 %, two grew 11 % and 16 %), and a front horse walk grew 6.4 % with a 42 frame cycle.
Of the other two, one still shows no repeat and one finds a 38 frame candidate that the seam
gate refuses (2.81).

The selected original frames receive the same integer XY correction described
below, using regions discovered again in the selected interval. Analysis resampling
never enters the emitted cycle. Reports include `automatic_motion_analysis`,
`automatic_motion_regions`, local candidates, the duration guard and every final
translation. The unchanged seam limit measures the actual rendered cells.

Automatic results carry `review_recommended=true`: these measurements cannot
establish limb identity or fix changing drawings. Validate unfamiliar body shapes
and motions visually; no animal-wide quality claim follows from humanoid examples.

If the selected cycle's fine XY match reaches its bounded search edge,
`motion-auto` discards that uncertain correction and submits the unchanged selected
frames to the same rendered-cell seam gate. It emits them only if that gate and
normal animation verification pass. Reports and the CLI summary explicitly say
`applied: false`, retain the rejected match and record zero translations; this is
an uncorrected output, not a successful registration. Failed period selection,
unusable regions, coarse-search limits and other errors still fail. The explicit
`motion` mode also keeps its strict registration contract.

## Explicit adjustments

If the visible full cycle is clear but the automatic candidate is wrong, make the choice
explicit and keep the report's `kind = fixed`:

```bash
sprite-gen video-loop --frames-dir keyed/ --out-dir reviewed/ --state run \
  --fps 24 --cycle fixed --start <zero-based-index> --length <frame-count>
```

Do not double a frame count unconditionally or duplicate existing frames. Choose a
continuous interval in the original sequence. `--min-len/--max-len` can instead narrow
an evidenced search interval while leaving the exact cut to the detector.

### Explicit XY motion correction

For a visually reviewed fixed cut, `--anchor motion` measures both XY placement and
the average movement at the wrap. Supply two rectangles in the **first selected
source frame**, head first and torso second, excluding swinging hair, limbs and
accessories. Coordinates are original pixels, with exclusive right/bottom edges:

```bash
sprite-gen video-loop --frames-dir keyed/ --out-dir reviewed/ --state walk \
  --fps 24 --cycle fixed --start 4 --length 24 --anchor motion \
  --anchor-region 60,20,90,50 --anchor-region 50,50,100,100
```

The rectangles are explicit inputs, not automatically detected regions. This mode
requires at least six frames and is unavailable in `video-set`, where the cycle and
its regions have not been reviewed. Existing automatic selection and default anchors
are unchanged. A fixed cut remains `kind=fixed`; correction does not prove a gait cycle.

Masked normalized cross-correlation of premultiplied RGBA (head weight 0.7, torso
0.3) matches the first/last three frames against the first. With measured last-frame
translation `m`, mean boundary velocity `v` and length `L`, the correction endpoint
is `round((m-v)*(L-1)/L)`. One integer XY ramp runs from zero to that endpoint;
it does not pin each frame or remove the original bob. Empty/flat regions and
search-boundary matches are errors, never a fallback to another anchor.

Cycle PNGs preserve the supplied keyed pixels (no additional speck removal), with
common transparent padding to avoid clipping. Strip scaling, GIF palette conversion
and output frame-density settings still apply. The sidecar/report records regions,
measurements, endpoint, every shift and padding. For this mode the unchanged seam
limit gates the **rendered, resampled cells**, recorded as `seam_measurement=rendered-cells`.
This tests the corrected output, not the drifting input. It cannot repair missing
poses, changing colors, limb identity or an incorrectly chosen cycle.

Use `--anchor feet` only when the source shows slow in-canvas translation. It removes a
linear horizontal trend; it is not a universal stabilizer, does not pin each planted foot,
and can alter an already stationary subject. Compare with `--anchor none` and
`feet` on identical frames before selecting it. Normal body bounce, clothing motion and
a model's changing anatomy are not position drift.

Do not raise `--seam-max` merely to make a failed result pass. A source lacking a complete
usable cycle may require a longer/new clip. Regeneration is a new generation request,
not a hidden fallback inside loop extraction.

## Reproducibility

Store the source identity, engine version, exact CLI arguments, selected report and
whether a person/model overrode the automatic choice. Keep the same fps for comparisons.
Render comparison videos from PNG strips or lossless frame outputs, not palette GIFs.
A reviewed example is not evidence that all future inputs will automatically attain its
quality. Consumers can route `review_recommended` to review while retaining the automatic
result as an explicitly labelled candidate.
