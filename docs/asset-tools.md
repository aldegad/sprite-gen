# Independent asset tools

> Owns: Background recipes, repeating RGBA tiles, projected shadows, shared asset formats, motion/contact measurements and frames shown again · Index: [docs/README.md](README.md)

These commands accept finished artwork without a sprite-generation run or scene spec. `background` owns repeating tiles, `effects` owns shadow projection, and `qa` owns motion measurements. [Scene creation](scene.md) optionally consumes their outputs. Atlas and character-part assembly remain in `compose`.

## Background recipe

Use the existing [image generator](gen.md) and [cutout](sheet-slicing.md) for background art. Choose the layers before generating:

- Paint buildings and their immediate ground together when their overlap and perspective should remain fixed.
- Keep distant mountains, sky or foliage separate when they need different camera parallax.
- Request the full silhouette of roofs, trees and signs, with transparent or key-colour margin on every side. A cropped silhouette cannot be recovered by adding empty canvas later.
- Keep shadows separate when the scene will project them from its light. Generate enough horizontal overlap for a repeating strip and inspect the join at display scale.

Example prompt template for the existing generator:

> Side-view painted village frontage for a 2D game. Buildings and their immediate ground share one perspective. Show every roof and tree canopy completely, with clear margin above and at both sides. Keep the foreground path unobstructed. No characters, no cast shadows. Use a uniform key colour absent from the artwork. Leave matching texture and lighting in the left and right overlap regions.

Generate and remove the key with the existing tools, then run the tile command only if repetition is needed. This recipe adds no generation provider or required sprite stage.

## Repeating tiles

```bash
sprite-gen background-tile --source frontage.png --axis x --period 512 --overlap 32 --out tile.png
```

`--axis x|y` chooses the repeat direction. `--period` is the output length in source pixels; `--overlap` is the input overlap used to find a low-error quilting seam. Input must contain enough pixels for the requested period and overlap. The tool preserves RGBA, writes a new PNG and `<out-stem>.report.json`, and never overwrites its source. `--report` changes the JSON destination.

The report measures the join against ordinary interior differences, including alpha and colour over light/dark backgrounds. It records the selected seam and source crop. A numerical join score does not prove that architecture or silhouettes repeat convincingly: inspect at least two adjacent copies. The tool does not invent missing roofs, perspective or scenery.

Python APIs: `make_tile(image, period, *, axis="x", overlap=32)` and `inspect_tile(image, axis="x")` in `sprite_gen.background.tile`. The former returns the image and report; the latter only measures. `inspect_tile` is the one measure of a tile's join: [`pixel-draw`](pixel-draw.md#gates)'s G4 tile seam asks it of a drawn tile along x and along y and warns where it says `needs-review`.

## Shared asset input

`shadow`, `inspect-motion` and scene use `sprite_gen.spec.assets.load_asset(source, *, state=None, fps=None, anchor=None)`. It returns a `FrameSequence` with ordered RGBA frames, durations in seconds, a source-pixel anchor, source files and metadata. `size`, `duration`, `frame_at(t, loop=True)` and `fingerprints()` expose the shared view. Inputs remain unchanged.

Supported inputs:

| Input | Timing and selection |
|---|---|
| A static PNG | One frame; default duration one second |
| An external `sprite-gen-asset` descriptor | Explicit ordered frame durations in seconds |
| `video-loop` output `name.strip.json` | Reads matching `name.strip.png`; `frames`, `w`, `h`, `delay_ms` define the strip |
| Runtime atlas `manifest.json` | Reads `sprite_sheet_alpha`, `frame_layout.rows` rectangles and `animation.rows` timing; multi-state atlases require `--state` |

An arbitrary folder or animated GIF is not an implicit frame sequence. Export its frames and describe the order and durations explicitly:

```json
{
  "kind": "sprite-gen-asset",
  "version": 1,
  "anchor": [32, 80],
  "frames": [
    {"file": "frames/step-0.png", "duration": 0.1},
    {"file": "frames/step-1.png", "duration": 0.2},
    {"file": "frames/step-2.png", "duration": 0.1}
  ]
}
```

Frame paths resolve relative to the descriptor. All frames share one canvas. The default anchor is bottom centre `(width / 2, height)` in canvas coordinates; it does not guess the foot location from visible pixels. For standalone shadow/motion commands, `--fps` explicitly replaces frame durations, and `--anchor-x` / `--anchor-y` override the anchor together. Prefer an external descriptor for metadata that must be reused consistently by scene.

## Projected shadows

```bash
sprite-gen shadow --source walk.strip.json --out-dir shadows/ \
  --squash 0.25 --shear 0.8 --opacity 0.4 --blur 3 --color 20,15,30
```

The output contains `frame-NNN.png`, a horizontal `shadow.png`, and `shadow.asset.json` with the resulting anchor and original durations. `--state`, `--fps` and paired anchor overrides use the shared input contract.

`squash` controls projection length vertically, `shear` controls its sideways direction, and `opacity`, `blur`, `color` control appearance. Positive shear projects pixels above the anchor to the left. The projection canvas includes the entire shadow and blur margin.

Python API: `project_shadow(image, anchor, *, squash=0.25, shear=0.8, opacity=0.4, blur=3, color=(20,15,30)) -> (image, anchor)`. Place the returned shadow anchor at the sprite's world anchor. Scene calls this same function using its light settings; there is no separate scene shadow algorithm.

## Motion and contact evidence

```bash
# Read-only analysis: absent reliable contact, stride remains unverified.
sprite-gen inspect-motion --source walk.strip.json --out motion.json

# Manual same-foot stance evidence, zero-based inclusive ranges:
sprite-gen inspect-motion --source walk.strip.json \
  --contacts 0:4,8:12 --foot-box 20,70,32,10 --facing right --out stance.json
```

`--contacts` declares consecutive source frames during which the **same tracked foot** is planted. `--foot-box x,y,width,height` is a fixed source-canvas ROI that isolates that foot throughout the declared spans. Choose values from the actual clip; these example coordinates are not defaults. Two visible feet in the ROI, an absent/airborne foot, inconsistent baselines or insufficient distinct poses leave the measurement unverified. Manual contact is a user-supplied assumption: silhouette geometry alone cannot prove ground contact.

`--facing left|right` is an annotation. It does not flip pixels, change measurement sign or set scene direction. Without `--out`, JSON goes to stdout. Analysis never removes or edits frames.

Python API: `analyze_motion(sequence, *, contacts=None, foot_box=None, facing=None) -> dict` in `sprite_gen.qa.motion`.

| Report field | Meaning |
|---|---|
| `frame_count`, `duration_seconds` | Full original sequence count and elapsed time, including repeated pose holds |
| `duplicate_frames` | Exact image, adjacent exact, exact silhouette and near silhouette duplicate counts; near uses IoU >= 0.98 |
| `frames`, `bbox_stats`, `anchor_px`, `edge_contact` | Per-frame timing, measured bounds/anchor offsets and canvas edge contact |
| `motion` | Visible and silhouette changes; no walking-direction inference |
| `contact_candidates`, `confidence`, `warnings`, `method` | Contact measurements, reasons for rejection and the measurement assumptions |
| `stride_verified` | `true` only when the declared contact and isolated ROI pass the stance checks |
| `stride_px_per_second` | Absolute stance speed in source pixels/second, otherwise `null` |
| `foot_velocity_px_per_second` | Signed stance foot velocity, positive toward image right, otherwise `null` |
| `source_fingerprints` | Source-path SHA-256 mapping bound to the loaded inputs, for stale-measurement rejection |
| `sequence` | Selected source/state, fps override, frame count, size, anchor and effective per-frame durations; scene must match this as well as the source fingerprints |

Repeated tracked poses retain their complete elapsed duration. Velocity uses distinct pose onset times; duplicate holds add time but do not add independent votes. The last hold remains part of the clip duration without inventing a later displacement endpoint. Confidence is a conditional heuristic, not a calibrated probability of contact.

Scene consumes only verified measurements for the matching selected asset and requires an explicit direction relative to a declared plane or the world. Measurement does not automatically apply stride to any scene or sprite.

## Frames shown again

```bash
# A figure layer or keyed frames (RGBA): read by alpha.
sprite-gen inspect-holds --source figures/ --holds web/assets --scene roof --gate

# A clip filmed on a key colour, a still background, or a renderer's log of the cells.
sprite-gen inspect-holds --source clip.mp4 --key green
sprite-gen inspect-holds --source frames/ --box 400,120,300,520
sprite-gen inspect-holds --log cells.jsonl --log-figure hero --log-figure figures

# A cut, its own intended holds listed.
sprite-gen inspect-holds --source loop/walk.strip.json --gate
```

`inspect-holds` counts, figure by figure, the frames on which a figure shows the picture it showed on the frame before: on screen it stands still for a frame and then goes on. It reads a delivery whatever made it: a cut, clips joined into one move, a scene rendered frame by frame. Frames are numbered from 0 in the order they are read (a folder's PNGs in name order, a video's frames, a strip's cells); `--frames FROM-TO` reads those frames and the one before `FROM`, to compare with. Nothing is changed.

The figure is read one of four ways:

| Way | Reads | For |
|---|---|---|
| alpha (the default) | the frames' alpha; several figures are told apart by where they stand, followed from frame to frame | a figure layer rendered on a clear background, keyed frames, a strip |
| `--key auto\|green\|magenta\|white` | each frame keyed as `video-frames` keys a clip, then its alpha | a clip filmed on a key colour (mp4 or frames) |
| `--box x,y,w,h` | that box of each frame, in place | a figure over a background that does not move |
| `--log FILE` with `--log-figure KEY` | the cell each figure shows: one JSON object a line, one line a frame from 0; `KEY` holds a cell (a string or a number, an object as its values joined by spaces, null where not shown) or a list of them (`figures` gives `figure0`, `figure1`, ...) | a renderer that logs its cells; exact, no pixel read |

Read by alpha, or by `--key`, a figure is the solid pixels of a frame (alpha over 8, on 0..255) gathered within 6 px of each other; each such group is a part of the frame. A part is a speck, and is not read, where it holds under 2 % of the pixels of the frame's largest part, or under 64 px; the 64 px floor is never more than half the frame's largest part. So the largest part of a frame is always a figure however few pixels it has, and a part half the largest part's size or more is a figure too: two small figures in one frame are both read. Dots of 1, 2 and 3 px far from the figure, the same on every frame, are not read, beside a large figure or beside a 63 px one. With `--box`, a frame whose box holds no pixel with alpha over 8 (the frame is clear there, or the box lies past the frame's edge) shows no figure there; two such frames are no picture shown again.

An opaque frame whose background moves is not read: its background changes more pixels from one frame to the next than a figure's step does, so a held picture and a moving one cannot be told apart. Opaque frames given with none of `--key`, `--box` or `--log` are refused (exit 2). Read such a delivery by its figure layer, a key, or a log.

A renderer that draws a figure at a new scale, turn or sub-pixel place on every frame draws a held picture afresh each time: the frame changes more of the figure's pixels than the rule below allows and is not read as a hold. Read such a render by its log.

Two pictures are one picture by the rule a cut's held cells are read by ([loop-repair.md section 7](loop-repair.md)): at most `CHANGED_MAX` of the figure's pixels change by more than `PIXEL_SAME`, on its thumbnail and as drawn. The figure is read alone, each pair placed first where its alpha's boxes meet, then within `SEARCH_PX` (3) pixels of it where the fewest pixels change: a camera that follows a figure moves its held picture across the screen, and it is still the picture shown again. Figures that touch read as one.

`--holds FILE` names the holds a delivery means, in the format a holds list is written in: `{"holds": [{"scene", "figure", "from", "to", "reason"}]}`, frames `from` to `to` inclusive. A folder reads every `*.holds.json` under it, and `--holds` repeats. `scene` and `figure` may be left out, for a list of one video or of every figure; a list that names scenes is read for the one `--scene` names (without `--scene` it is refused). A hold without a reason, or whose `from` is after its `to`, refuses the list. A strip's own `holds` (the holds its cut means, `motion-hold`, `drawn`, `opening`, `closing`) are listed with it, and a strip that loops is read across its wrap too (its first cell against its last: a hold at frame 0).

The report (stdout, and `--report PATH`) gives each figure's `frames`, `steps` (its frames read against the frame before; on a loop read across its wrap, the wrap is one step), `holds`, `listed`, `unlisted`, `unlistedRuns` and `listedRuns`, each run `from`, `to` and `drawings` (the cell a log names, or the frame the picture was first shown on), a listed run its `reason`, `unlisted` over every figure and `inspected` (the number of figures read on one step or more); `read_by`, `first_frame`, `wrap`, the lists read and the rule's numbers. `--gate` exits 1 where any hold is on no list, else 0; without it the command exits 0 whatever holds it finds, and an unlisted hold is a line on stderr. Errors exit 2.

A reading that reads no frame against the frame before is an error, not a reading that found no hold: it exits 2, with or without `--gate`. Its report is still printed on stdout and written to `--report`, with `inspected` 0, and one line on stderr says why. This is the case where there is no frame (an empty folder; `--frames` that starts past the last frame of a folder, a video, a strip or a log; a log of no line), where no frame shows a figure (clear frames; frames whose every pixel has alpha 8 or under; a key that leaves nothing; a box that holds nothing; a log whose `--log-figure` keys name no cell on any line), or where no figure is shown on two frames in a row (one frame, for example). For three clear frames the line is:

```
inspect-holds: no frame was read against the frame before: no figure on frames 0-2 (no pixel with alpha over 8)
```

Python API: `inspect(figures, *, first=0, start=None, listed=(), wrap=False, same=None)`, `alpha_figures(frames)`, `log_figures(path, keys)`, `same_figure(a, b)` and `read(...)` in `sprite_gen.qa.holds`. `inspect(...)` and `read(...)` raise `NothingRead` where nothing was read; `NothingRead` is a `ValueError`, and its `report` attribute is the report, with `inspected` 0.
