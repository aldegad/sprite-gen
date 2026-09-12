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

Screen anchor equals `at + (plane_velocity + layer_velocity) * time - (camera.at + camera.velocity * time) * parallax`. Raster dimensions are rounded from source dimensions and scale; the anchor follows the actual resized dimensions. For repeated strips, use the tile's measured period and inspect two or more copies for recognisable joins.

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
| `scene.report.json` | Source fingerprints, output list, encoding details and scene inspection |
| `placement.json` | Resolved placement and source references, camera, light, fps and duration |
| `layers/<id>.png` | Optional canvas-sized plates at time zero, with `--export-layers` |

Layer plates are static; animated layers remain references in `placement.json`. This output is not a self-contained project archive: copy all referenced assets when moving it.

## Inspection

`scene-inspect` reports viewport clipping, including completely offscreen layers, repeat joins, timing and loop measurements. It compares the actual last rendered output frame with the first as well as ordinary adjacent transitions; phase closure is separate from visual seam quality. A clean numerical result still needs visual review, especially for locomotion and repeating architecture.

`--require-loop` makes a failed loop check return nonzero. Without `--out`, the JSON is printed. An output report cannot overwrite a source, a source alias or the reserved writer lock. Inspection does not fix, reverse, flip or resample source frames.

Python APIs: `load_scene(path)` in `sprite_gen.scene.model`, `render_scene(spec, out_dir, **options)` in `sprite_gen.scene.render`, and `inspect_scene(scene, **options)` in `sprite_gen.scene.inspect_scene`. Each command also supports `python -m` with its module name.
