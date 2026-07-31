# Engine export — Aseprite-compatible JSON atlas

The exporter re-describes the composed run in a format engines already parse —
no image is re-encoded, and everything curation baked is already inside the
rects. Run it **after** `compose-atlas`:

| command | output | pairs with | carries loop? |
|---|---|---|---|
| `sprite-gen export-aseprite` | `exports/aseprite.json` | Phaser 3.50+ `load.aseprite`, anything with an Aseprite importer | no (Aseprite tags can't) |
| `sprite-gen export-aseprite --format json-hash --split-states` | `exports/aseprite/<state>.json` | Flutter/Flame `SpriteAnimation.fromAsepriteData` | no (set on the component) |

Flutter/Flame consumers with a few lines to spare should prefer the
manifest-direct recipe below — it carries strictly more (loop, per-frame
durations, every state from one file) than the Aseprite path.

## Aseprite-compatible JSON atlas

`manifest.json.frame_layout` is the runtime SSoT, and its instance-order rect
list was already "Aseprite JSON 과 동형 패턴" by design (`compose_atlas.py`).
`export-aseprite` completes that isomorphism into a file engines already know
how to read, so a consumer with an Aseprite loader needs **zero hand-written
glue** for sprite-gen output.

```bash
sprite-gen export-aseprite --run-dir <run-dir>
# → <run-dir>/exports/aseprite.json  (pairs with sprite-sheet-alpha.png)
```

Run it **after** `compose-atlas` — the export reads `manifest.json`, so
everything curation baked (selection, order, transforms, pixel edits, clones,
breathe cells) is already inside the rects it re-describes. No image is
re-encoded; `meta.image` points at the existing atlas.

## Output shape

Aseprite `json-array` form, mirroring what Aseprite's own CLI export writes:

```json
{
  "frames": [
    { "filename": "0", "frame": {"x": 0, "y": 0, "w": 64, "h": 64},
      "rotated": false, "trimmed": false,
      "spriteSourceSize": {"x": 0, "y": 0, "w": 64, "h": 64},
      "sourceSize": {"w": 64, "h": 64}, "duration": 125 }
  ],
  "meta": {
    "app": "sprite-gen", "version": "…", "image": "sprite-sheet-alpha.png",
    "format": "RGBA8888", "size": {"w": 192, "h": 128}, "scale": "1",
    "frameTags": [ {"name": "idle", "from": 0, "to": 1, "direction": "forward"} ]
  }
}
```

Mapping from the manifest:

| manifest | aseprite.json |
|---|---|
| `frame_layout.rows.<state>[i]` (instance order, reused rects repeat) | `frames[]` entries in the same global order — repeated rect, new index |
| `animation.rows.<state>.durations_ms[i]` (timing SSoT; fps fallback) | `frames[i].duration` |
| each state | `meta.frameTags[]` entry `{name, from, to, direction: "forward"}` over the state's global index range |
| `game_input` / `frame_layout.sheetWidth/Height` | `meta.image` / `meta.size` |

`filename` is the **stringified global frame index** — the form Aseprite's
documented export settings produce and the key Phaser's
`createFromAseprite` looks frames up by (`frameKey = i.toString()`).

## Consuming from Phaser (3.50+)

```js
this.load.aseprite('hero', 'sprite-sheet-alpha.png', 'aseprite.json');
// ...
this.anims.createFromAseprite('hero');
sprite.play({ key: 'idle', repeat: -1 });
```

## Flutter / Flame

Two ways in, by decreasing fidelity:

**1. Manifest-direct (recommended).** Flame's `SpriteAnimationData` can express
everything the manifest carries, so a ~25-line loader beats any interchange
format. The one contract detail loaders get wrong: when
`animation.rows.<state>.durations_ms` exists it is the timing SSoT — use it
per frame instead of a uniform `1 / fps`, or hold frames and loop delays
silently flatten out.

```dart
final manifest = jsonDecode(await Flame.assets.readFile('images/hero/manifest.json'));
final image = await Flame.images.load('hero/sprite-sheet-alpha.png');
final layoutRows = manifest['frame_layout']['rows'] as Map<String, dynamic>;
final animRows = manifest['animation']['rows'] as Map<String, dynamic>;

final animations = <String, SpriteAnimation>{};
for (final state in layoutRows.keys) {
  final rects = (layoutRows[state] as List).cast<Map<String, dynamic>>();
  final meta = animRows[state] as Map<String, dynamic>;
  final durations = (meta['durations_ms'] as List?)?.cast<num>();   // timing SSoT
  final fallback = 1 / ((meta['fps'] as num?)?.toDouble() ?? 6);
  final frames = [
    for (final (i, r) in rects.indexed)
      SpriteAnimationFrameData(
        srcPosition: Vector2((r['x'] as num).toDouble(), (r['y'] as num).toDouble()),
        srcSize: Vector2((r['w'] as num).toDouble(), (r['h'] as num).toDouble()),
        stepTime: durations != null ? durations[i] / 1000 : fallback,
      ),
  ];
  animations[state] =
      SpriteAnimation.fromFrameData(image, SpriteAnimationData(frames, loop: meta['loop'] as bool? ?? true));
}
```

**2. Zero-glue via `fromAsepriteData`.** Flame core reads Aseprite JSON, but
with two constraints baked into its source: `frames` must be a **map**
(json-hash), and `meta.frameTags` is never read — the whole file becomes one
animation. Hence the two flags:

```bash
sprite-gen export-aseprite --run-dir <run-dir> --format json-hash --split-states
# → exports/aseprite/idle.json, walk.json, …  (one animation each)
```

```dart
final image = await Flame.images.load('hero/sprite-sheet-alpha.png');
final idle = SpriteAnimation.fromAsepriteData(
    image, jsonDecode(await Flame.assets.readFile('images/hero/idle.json')));
```

Per-frame durations survive this path (`duration` ms → `stepTime`); `loop`
does not (set it on the component). Flame does not support trimmed sheets —
sprite-gen always exports `trimmed: false` full cells, so that constraint is
already satisfied.

## Limits (honest)

- **`loop` is not represented.** Aseprite frame tags have no loop flag; loop
  policy stays in `manifest.json.animation.rows.<state>.loop` and the engine
  decides at play time (e.g. Phaser `repeat: -1`, Flame component config).
- **Structural contract, not a runtime test.** Key structure is pinned to real
  Aseprite CLI exports and to the fields the consumers read — Phaser's
  `createFromAseprite`, Flame's `fromAsepriteData`
  (`tests/test_export_aseprite.py`); an in-browser Phaser load or a running
  Flame app is not part of the suite.
- Other engine-native formats (Godot SpriteFrames, Unity, Spine) remain
  manifest consumers via hand-written glue.
