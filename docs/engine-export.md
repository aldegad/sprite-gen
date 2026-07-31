# Engine export — Aseprite-compatible JSON atlas

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

## Limits (honest)

- **`loop` is not represented.** Aseprite frame tags carry no loop flag; loop
  policy stays in `manifest.json.animation.rows.<state>.loop` and the engine
  decides at play time (e.g. Phaser `repeat: -1`).
- **Structural contract, not a runtime test.** The export's key structure is
  pinned to real Aseprite CLI exports and to the fields Phaser's loader reads
  (`tests/test_export_aseprite.py`); an in-browser Phaser load is not part of
  the test suite.
- One export target so far. Godot `SpriteFrames`/Unity native formats remain
  manifest consumers via hand-written glue.
