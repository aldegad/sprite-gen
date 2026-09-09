# Engine export

> Owns: Aseprite-compatible export for Phaser and Flame · Index: [docs/README.md](README.md)

`sprite-gen export-aseprite` describes a composed sprite-gen atlas using the JSON schema consumed by Aseprite loaders. It does not create an editable `.aseprite` source file and it does not re-encode the PNG.

Run it after `compose-atlas`:

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen export-aseprite --run-dir <run-dir>
```

The default output is `<run-dir>/exports/aseprite.json`. It pairs with the existing `sprite-sheet-alpha.png` named by `manifest.json.game_input`. Because compose already applied `curation.json`, the export includes the final selected order, reused cells, transforms, pixel edits, clones, and breathe cells through the atlas rectangles it references.

## Mapping

| sprite-gen manifest | Aseprite-compatible JSON |
|---|---|
| `frame_layout.rows.<state>[i]` | `frames[]` rectangle in playback order |
| `animation.rows.<state>.durations_ms[i]` | frame `duration` in milliseconds |
| state name and global frame range | `meta.frameTags[]` |
| `game_input` | `meta.image` |
| `frame_layout.sheetWidth` / `sheetHeight` | `meta.size` |

Repeated playback instances keep repeated rectangles under distinct numeric frame keys. This preserves sprite-gen's shared-cell representation while presenting the sequential frame keys expected by engine loaders.

## Phaser

Phaser accepts Aseprite JSON in array or hash form and creates animations from `meta.frameTags`. The default `json-array` output uses stringified global frame indices, the tag ranges, and each frame's own duration.

```javascript
this.load.aseprite('hero', 'sprite-sheet-alpha.png', 'aseprite.json');
this.anims.createFromAseprite('hero');
this.add.sprite(100, 100, 'hero').play('idle');
```

Phaser's official API documents `load.aseprite` and `createFromAseprite`; its implementation looks up each tagged frame by the stringified numeric index and reads each frame's duration. Sources: [Phaser AnimationManager](https://docs.phaser.io/api-documentation/4.0.0/class/animations-animationmanager), [Phaser source](https://github.com/phaserjs/phaser/blob/master/src/animations/AnimationManager.js).

## Flutter and Flame

Flame's `SpriteAnimation.fromAsepriteData` expects `frames` to be a map and builds one animation from every value in that map. It does not read `meta.frameTags`. Export one hash file per state:

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen export-aseprite --run-dir <run-dir> --format json-hash --split-states
```

This writes `exports/aseprite/idle.json`, `walk.json`, and so on. Every file uses local indices beginning at `0`, points to the same atlas PNG, and preserves millisecond frame durations. The relevant behavior is visible in Flame's official [`SpriteAnimation.fromAsepriteData` source](https://github.com/flame-engine/flame/blob/main/packages/flame/lib/src/sprite_animation.dart).

## Limits

- The exporter is structurally tested against the fields the loaders consume, but the repository does not run a browser Phaser app or Flutter runtime in CI.
- `loop` remains in `manifest.json`. Aseprite frame tags carry direction and range but no sprite-gen loop policy, and Flame's Aseprite factory constructs its default looping animation without reading tags.
- The curation webview is unchanged. This is a post-compose CLI export, not a new webview download button.
- The exporter refuses malformed row mappings and output paths outside the run directory. Split-state files are staged as a complete directory and then published together.

## Related

- [docs/README.md](README.md) — documentation index
