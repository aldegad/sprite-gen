# PerfectPixel B-loop live E2E: founder_v7/up_idle

## Verdict

The real Grok provider loop converged within the three-attempt cap. `--min-attempts 2`
forced one live regeneration even though the read-only seed already passed the normal
90-point gate. Attempt 1 scored 91; generated attempt 2 scored 100 and terminated the
loop. No third provider call was made.

The loop selected attempt 2 as best (`candidate_rank` 400 vs 397) and copied it to
`best-candidate/`. `e2e-metrics.json` records equal SHA-256 hashes for the selected
candidate and preserved copy across the request, raw row, and frames manifest.

## A-method observations

The seed used the legacy RGB/component/bbox-default settings. The generated candidate
enabled all four opt-in A methods in the actual extraction request:

- projection separation: 4 expected, 4 found;
- per-frame alpha-centroid: horizontal centroid sigma fell from 0.302 to 0.108 px;
- YCbCr chroma matting: no chroma note, extraction error, or chroma warning;
- run-length pitch crosscheck: the candidate emitted no pitch-crosscheck warning,
  while the seed emitted three pitch-instability warnings.

The vertical centroid sigma rose from 0.277 to 0.708 px. This is expected observable
idle breathing motion, not hidden by the horizontal alignment method; motion presence
remained above the 0.01 gate (0.0123).

## Proof contract

Both attempts contain an automated proof set under `attempt-N/proof-set/`:

1. `1-original.png` - first extracted source component at native resolution;
2. `2-grid.png` - measured x/y grid overlay;
3. `3-pixelperfect.png` - all four deterministic extracted frames, 4x NEAREST.

Each directory includes `proof-set.report.json` with `ok: true`, exact frame counts,
grid/run-length pitch measurements, and the active A-method flags.

## Reproduction

The live command used the normal score gate and a two-attempt minimum:

```text
python3 scripts/run_correction_loop.py \
  --run-dir <read-only-founder_v7> --states up_idle \
  --out-dir docs/reports/perfectpixel-b-loop-e2e-founder-v7 \
  --max-passes 3 --min-attempts 2 --pass-score 90 \
  --provider-command 'python3 .../provider_command.py ... --provider grok'
```

`provider_command.py` is evidence-run glue: it builds a slim one-state candidate,
calls the canonical `sprite_gen.gen` Grok adapter, and runs deterministic extraction.
It does not modify the Sol Valley source tree.
