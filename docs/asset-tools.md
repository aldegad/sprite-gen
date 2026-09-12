# Independent asset tools

> Owns: Background recipes, repeating RGBA tiles, projected shadows, shared asset formats and motion/contact measurements · Index: [docs/README.md](README.md)

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

Python APIs: `make_tile(image, period, *, axis="x", overlap=32)` and `inspect_tile(image, axis="x")` in `sprite_gen.background.tile`. The former returns the image and report; the latter only measures.

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
