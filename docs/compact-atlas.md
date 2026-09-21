# Compact atlas pages

`compact-atlas` is a deterministic post-compose tool. It trims transparent pixels
without resampling, packs the resulting rectangles into equal square pages, and writes
the page index plus logical-cell offsets into a new manifest.

```bash
sprite-gen compact-atlas --run-dir <run> \
  --page-size 2048 --max-pages 4 --gutter 2 --alpha-padding 1 \
  --max-empty-percent 25
```

The default policy keeps every animation clip on one Texture2DArray slice. Empty space
is measured against all allocated fixed-size slices, including gutter. If that ratio is
above `--max-empty-percent`, the report records `thresholdExceeded: true` but still keeps
clip locality.

Add `--allow-clip-split-over-threshold` to opt into a second global MaxRects pass only
when the threshold is exceeded. That pass may place frames from one clip on multiple
slices. `compact-atlas.report.json.clipsSpanningPages` lists every affected clip, so the
choice is observable rather than implicit.

Outputs are non-destructive:

- `<page-prefix>-0.png` through `<page-prefix>-N.png`, each exactly `page-size` square.
- `manifest.compact.json`, whose frame rects contain `page`, `x/y/w/h`, and
  `sourceX/sourceY`.
- `compact-atlas.report.json`, including occupancy, policy, page count and split clips.

The original `sprite-sheet-alpha.png` and `manifest.json` remain compose/correction SOTs.
Installers copy the compact manifest under the runtime name only after QA.
