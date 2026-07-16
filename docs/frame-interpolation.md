# AI Frame Interpolation (in-betweens)

`sprite_gen/interpolate.py` (`scripts/interpolate_frames.py`) makes an **in-between
frame** between two frames of one state — the canonical use case is a half-closed
eyelid between an open-eye and a closed-eye idle frame.

## Doctrine position

"AI touches raw generation only" applies here too: interpolation is a raw-stage AI
step. The output is never a final frame — it is recorded as a **take**
(`raw/<state>.takes/<label>.png` + `states.<state>.takes` in `sprite-request.json`),
and logical frames are always baked by the deterministic extraction path (chroma
removal → components → grid snap → shared palette). The curator orders the new frame
in the play sequence like any other take frame.

## Model

RIFE 4.9 ensemble ONNX — the same model artkit's frame interpolation uses
(Soohong confirmed 2026-07-17: RIFE is the practical optimum here; newer VFI exists
(RIFE 4.2x / FILM / EMA-VFI) but for 2-frame pixel-art micro-motion the pixel-perfect
re-quantization dominates final quality). The model auto-downloads to
`~/.cache/sprite-gen/` on first use; truncated/failed downloads abort loudly.

Runtime dependency: `pip install 'sprite-gen[interpolate]'` (onnxruntime). Missing
onnxruntime aborts with that hint — no silent skip.

## Usage

```bash
python3 scripts/interpolate_frames.py \
  --run-dir <run> --state down_idle --between 1 2 \
  [--t 0.5] [--label blink_mid] [--extract]
```

- `--between A B` — frame indices on the state's primary strip (component order).
- `--t` — interpolation time inside (0, 1); default 0.5.
- `--label` — take label; default `tween_<A>_<B>_t<t>`. Re-running the same label
  overwrites the same take file and does not duplicate the `takes` entry (idempotent).
- `--extract` — run a **full-batch** re-extraction afterwards. Partial extraction is
  deliberately not offered: the run-wide shared palette is built per extraction batch
  (CHANGELOG v1.56.17 known limitation), so a single-state extract would shade that row
  from a different palette.

## Pipeline internals

1. Chroma-remove the primary strip, extract components, tighten to solid bbox.
2. **Registration** (`register_row_frames`) puts both frames on one canvas aligned by
   upper-body alpha overlap — static pixels coincide, so the model morphs only the
   moving part instead of ghosting the whole body.
3. Canvas pads to /32 (RIFE constraint) on the request chroma color; a constant
   background interpolates to itself and the extractor keys it out later.
4. RIFE runs at native component resolution; the mid frame is saved as the take raw.
5. Extraction re-derives the state row; the interpolated frame appears in the pool
   labeled `<label>#0`, and pixel-perfect quantization snaps the model's soft blend
   onto the logical grid (a faded eye smudge becomes a legible half-closed pixel eye).

Quality note: expect the model output to look like a cross-fade at raw resolution —
that is fine. Judge the result **after** pixel-perfect re-quantization, and touch up
stray pixels with the curator's pixel editor if needed.

## Testing

`tests/test_frame_interpolation.py` covers the plumbing with an injected stub
interpolator (alignment canvas contract, take write, request idempotency, loud
rejections). Real-model inference is exercised manually, not in CI.
