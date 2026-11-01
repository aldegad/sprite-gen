# Optional scene creation

> Owns: Scene spec, planes, camera, lighting, measured stride application, render/inspection and output contracts · Index: [docs/README.md](README.md)

`scene-render` places finished assets without generating or modifying them. `scene-inspect` measures the same composition without exporting a video. Both accept a versioned JSON spec. Sprite generation can end with an atlas or loop; this workflow is optional, and artwork from other tools can enter here directly.

## A complete static scene

Save this as `scene.json` beside any static `actor.png`. The default asset anchor is bottom centre; `at` places that anchor in world pixels.

```json
{
  "kind": "sprite-gen-scene",
  "version": 1,
  "canvas": {"width": 640, "height": 360, "background": [235, 235, 240, 255]},
  "fps": 20,
  "duration": 2,
  "assets": {"actor": {"source": "actor.png"}},
  "planes": {"ground": {"velocity": [0, 0]}},
  "camera": {"at": [0, 0], "velocity": [0, 0]},
  "light": {"squash": 0.25, "shear": 0.8, "opacity": 0.4, "blur": 3, "color": [20, 15, 30]},
  "layers": [
    {"id": "actor", "asset": "actor", "at": [320, 310], "plane": "ground", "shadow": true}
  ]
}
```

```bash
sprite-gen scene-render --spec scene.json --out-dir render/ --formats png,mp4 --export-layers
sprite-gen scene-inspect --spec scene.json --out scene-check.json
```

For animations, replace the PNG reference with an [external frame descriptor, loop strip or runtime atlas](asset-tools.md#shared-asset-input). Add `"state": "walk"` to an atlas reference when it has several states. Paths resolve relative to the scene spec; frame/image paths within asset descriptors resolve relative to those descriptors. Asset timing and anchors stay in the asset metadata.

A loop strip's cells play at the strip's own rate: its `fps` (`video-loop` writes it, unrounded to six places), or, on a strip written before it, its `delay_ms`. A `delay_ms` with a fraction of a millisecond that lies within 0.01 ms of 1000 over a whole number is that rate (41.67 ms is 24 fps, 16.67 ms is 60 fps); a whole number of milliseconds, and any other value, is read as it is. The same reading applies to a direction sheet's rows and a grid sheet's `delay_ms`. Read as 41.67 ms, a 24 fps strip drifted a scene a cell late: in a 24 fps scene its first cell showed twice and every cell after it one frame late; in a 20 fps scene every fifth frame showed the cell before the one its time falls on. A stride report measured on the rounded time still names the same cells.

## Cells at the scene's times

A layer whose asset is a cut of a filmed clip — a `video-loop` or `video-stop` strip, whose sidecar names its `source_cut` — is read for which of its cells each scene frame shows. Where a scene frame would show the cell the frame before it shows (the scene's frames come faster than the cells, as a 24 fps cut in a 60 fps scene does, or a `playback_rate` under 1 plays it slower), and nothing holds that cell on purpose, the layer is given at the scene's times: every frame of it is the cell its time falls on, or the frame RIFE makes between the two cells either side at its own time. Each frame made is judged as `video-loop` judges a frame it makes for a held cell ([loop-repair.md](loop-repair.md) section 7): one that drags a planted part, cross-fades two poses, opens a hole, draws a double outline or shows a pose beside it again is made again at another fraction within half a scene frame of its time, and one with no fraction left shows the nearer cell again, named. A layer that shows no cell again keeps its cells as its timing picks them, byte for byte; so does every layer with `--fill-held off`.

Held on purpose, and never made between:

| Held | Where |
|---|---|
| Not a video cut | A still, an atlas row, a pixel drawing, a frame descriptor, a strip that names no `source_cut`: its cells are drawings, each held as drawn |
| Before its start | A layer with `loop: false` and a negative `offset` holds its first cell until its time reaches it |
| A one-shot's end | A layer with `loop: false` holds its last cell from that cell's time on (`hold_last` on a `once` cut) |
| A hold its strip lists | The runs of cells the strip's `holds` names (`from` to `to`, both counted): within one, its cells as they stand; from its last cell to the next pose, frames made |

A frame that shows the cell a frame held on purpose shows (the first cell, on at its start) goes on holding it. RIFE is located the first time a frame is to be made, so a render that makes none needs no binary, and each frame between two cells at one fraction is made once a render. With no RIFE installed the layer keeps its cells, a `scene-render: warning:` line names the layer, the scene frames that show a cell again and the install command, and the report records it; it is never a failure. Any other RIFE failure stops the render, naming `--fill-held off`.

Each layer's row of `repeats`, in the inspection (`scene-inspect`, and `inspection.repeats` in `scene.report.json`), lists by which cell each scene frame shows, exactly, the scene frames that show the cell the frame before shows: as runs (`from`, `to`, both counted, the `cell` — or `{"between": [a, b], "t": t}` for a frame made — `intended` and `why`), with `repeated_frames` and `unintended_frames`. A video cut's row carries `fill_held`: the `mode`, whether the scene's times were `applied`, how many frames were `made`, the frames `shown_again`, the `interpolator`, and `why` with `unmade` (`frames`, `rife`, `install`) where nothing was made. A layer with frames not held on purpose adds a line to the inspection's `warnings`, and its `status` is `needs-review`.

## Spec ownership and units

| Field | Contract |
|---|---|
| `kind`, `version` | `sprite-gen-scene`, `1`; unknown fields are rejected |
| `canvas` | Integer `width`, `height`, RGB/RGBA byte `background`; at most 8192 per axis and 16 megapixels |
| `fps`, `duration` | Output sampling rate and seconds; `duration * fps` must be an integer from 1 to 10000; fps <= 120 |
| `assets` | Named `{source, state?}` references, or source strings; metadata is loaded once through the shared adapter |
| `planes` | Named `{velocity: [x,y]}` in world pixels/second; a layer's own velocity is relative to its plane |
| `camera` | `at`, `velocity`, in world pixels and pixels/second |
| `light` | `squash`, `shear`, `opacity`, `blur`, `color`, passed to the [shared shadow projection](asset-tools.md#projected-shadows) |
| `layers` | Nonempty ordered list, sorted stably by `z` before compositing |

Canvas origin is top left; positive x is right, positive y is down. Each layer has a unique `id` (letters/digits/dash/underscore) and named `asset`. Optional fields:

| Layer field | Default and meaning |
|---|---|
| `at` | `[0,0]`, world position of the asset anchor at scene time zero |
| `velocity` | `[0,0]`, motion relative to the selected plane, or world if none |
| `plane` | No plane; otherwise a declared plane name |
| `scale`, `opacity`, `z` | `1`, `1`, `0`; positive raster scale, alpha multiplier 0..1, stacking order |
| `parallax` | `1`, multiplier on camera displacement; 0 keeps a layer unaffected by the camera |
| `repeat_x`, `period` | `false`, source width; integer horizontal repeat period in source pixels |
| `shadow` | `false`; project the source silhouette using the scene light |
| `playback_rate`, `offset` | `1`, `0`; asset time is `scene_time * playback_rate + offset`, with offset in seconds |
| `loop` | Source loop flag, otherwise true; false holds the final frame after its end |
| `stride` | Optional verified motion report plus explicit direction; mutually exclusive with `velocity` |

Screen anchor equals `at + (plane_velocity + layer_velocity) * time - (camera.at + camera.velocity * time) * parallax`. Raster dimensions are rounded from source dimensions and scale; the anchor follows the actual resized dimensions. A layer is scaled with its coverage and its colour taken apart (`resize_cell`, the resample a loop's cells use: [video-pipeline.md](video-pipeline.md) "Cells"), so a keyed sprite gets no lighter rim and no key tint at its edge from being scaled; a layer at scale 1 is its frame, byte for byte. Colour is not sharpened, so a scaled layer with no transparency (a painted background) comes out a little softer than LANCZOS drew it. For repeated strips, use the tile's measured period and inspect two or more copies for recognisable joins.

## Applying measured stride

First run [`inspect-motion`](asset-tools.md#motion-and-contact-evidence) with genuine same-foot contact spans and an isolated ROI. Then replace the layer's `velocity` with:

```json
"stride": {"report": "stance.json", "direction": "right"}
```

Scene accepts only `stride_verified: true` with a positive `stride_px_per_second` and matching source/sequence identity. It rejects stale sources, another state or different measurement timing. Keep native asset durations consistent; a standalone `--fps` override is not automatically an instruction to change the scene's asset timing. Describe reusable timing in the asset metadata.

Stride magnitude is converted from source pixels/second to the rendered horizontal scale and multiplied by `playback_rate`. Explicit `direction` supplies the sign; the named plane velocity is then added. `facing` in the QA report never chooses scene direction. A left-moving stance foot can support rightward travel, but scene does not guess that relationship. Unverified or airborne clips require an explicit placement velocity chosen by the scene author.

## Outputs and failure behaviour

`--formats` selects a comma-separated subset of `png,mp4,gif`. MP4 requires `ffmpeg`, even canvas dimensions and an opaque background. GIF also requires an opaque background; export transparent content as PNG frames or layer plates.

The output directory is dedicated to one render and must be empty. Rendering and encoding stage before publication, protected by the shared run-directory writer lock. Invalid specs, changed sources, encoder errors or publication failures stop with a nonzero result. Sources and pre-existing outputs are preserved; the CLI does not regenerate assets or retry a failed encoder automatically.

`--fill-held auto` (the default) gives a layer cut from a filmed clip at the scene's times where it would show a cell again ([Cells at the scene's times](#cells-at-the-scenes-times)); `--fill-held off` gives every layer's cells as its timing picks them. `scene-inspect` takes the same flag and measures what that render would show. What was not made — no RIFE, a frame refused, `--fill-held off` — is printed as `scene-render: warning:` lines once the render is published.

GIF defaults preserve the scene frame rate, use 256 colours without dithering (for flat pixel-art colours), and cap width at 640 pixels. `--gif-width`, `--gif-fps`, `--gif-colors` and `--gif-dither` make those choices explicit; `sierra2_4a` can preserve gradual tones in painted scenery. GIF fps cannot exceed scene fps; more samples require rendering the scene itself at a higher rate. `--gif-max-bytes` defaults to 8,000,000: an oversized result fails and asks for an explicit budget or quality change. The encoder never silently lowers frame rate, resolution or colours.

```bash
sprite-gen scene-render --spec scene.json --out-dir gif-render/ --formats gif \
  --gif-width 600 --gif-fps 20 --gif-colors 256 --gif-dither sierra2_4a --gif-max-bytes 40000000
```

| Output | Meaning |
|---|---|
| `frames/frame-NNNNN.png` | All rendered frames when `png` is selected |
| `scene.mp4`, `scene.gif` | Selected encoded formats |
| `check-NNNNN.png` | Representative rendered frames for visual review |
| `scene.report.json` | Source fingerprints, output list, `fill_held`, encoding details and scene inspection (with each layer's `repeats`) |
| `placement.json` | Resolved placement and source references, camera, light, fps and duration |
| `layers/<id>.png` | Optional canvas-sized plates at time zero, with `--export-layers` |

Layer plates are static; animated layers remain references in `placement.json`. This output is not a self-contained project archive: copy all referenced assets when moving it.

## Inspection

`scene-inspect` reports viewport clipping, including completely offscreen layers, repeat joins, timing and loop measurements, and each layer's scene frames that show a cell again (`repeats`). It compares the actual last rendered output frame with the first as well as ordinary adjacent transitions; phase closure is separate from visual seam quality. A clean numerical result still needs visual review, especially for locomotion and repeating architecture.

`--require-loop` makes a failed loop check return nonzero. Without `--out`, the JSON is printed. An output report cannot overwrite a source, a source alias or the reserved writer lock. Inspection does not fix, reverse, flip or resample source frames.

Python APIs: `load_scene(path)` in `sprite_gen.scene.model`, `render_scene(spec, out_dir, **options)` and `Renderer(scene, fill_held=, interpolate=)` in `sprite_gen.scene.render`, and `inspect_scene(scene, **options)` in `sprite_gen.scene.inspect_scene`; `interpolate` stands in for RIFE. Each command also supports `python -m` with its module name.
