# C-gen provider comparison — codex vs grok (same row prompt)

Deliverable evidence for plan `solvell/perfectpixel-port-spritegen` gate **C-gen**.
Both providers ran the **same** sprite-row prompt through `sprite-gen gen`, with the
same deterministic transparent chroma contract (`--transparent --chroma-key magenta`).

- Prompt SSoT: [`row-prompt.txt`](row-prompt.txt) (4-frame idle mushroom strip on solid `#FF00FF`).
- Reports: [`codex.report.json`](codex.report.json), [`grok.report.json`](grok.report.json).
- Visual proof (both white-composited, downscaled side by side): [`proof-side-by-side.png`](proof-side-by-side.png).
  The full-size raw / keyed / white-check PNGs (~3.6 MB) were pruned after verification to
  keep the engine repo lean (matches the text-only B-loop report convention); the report
  JSONs record their original paths, sizes, and byte counts.
- Command shape:
  ```
  sprite-gen gen --provider <codex|grok> --prompt-file row-prompt.txt \
    --out <provider>-mushroom.png --transparent --chroma-key magenta \
    --white-check <provider>-mushroom.white.png --report <provider>.report.json
  ```

## Speed + output

| Provider | Generation call | Raw size | Raw bytes | Chroma stale RGB | Model call |
|---|---:|---|---:|---:|---|
| codex (`image_gen`, ChatGPT OAuth) | **39.02 s** | 2172×724 | 1,211,487 | 0 | inline base64 in rollout jsonl |
| grok (Imagine `image_gen`, xAI OAuth) | **18.42 s** | 1280×720 | 530,743 | 0 | file on disk, verified by PNG magic |

**grok is ~2.1× faster** on this run (18.42 s vs 39.02 s), matching the plan's rationale
for adopting a grok backend ("GPT 대비 생성 속도 우위가 채택 사유", plan Notes). Both paths
produced a real, verified 4-frame mushroom strip; the deterministic magenta chroma key
cleared the solid background on both with `stale_transparent_rgb_pixels=0`.

## Honest quality note (not a pipeline defect)

- **codex** followed "no grid lines / no labels" cleanly — four well-separated frames,
  no dividers, clean transparent cut (see `codex-mushroom.white.png`).
- **grok** drew faint pink vertical **divider lines** between cells despite the explicit
  "no grid lines" instruction (see `grok-mushroom.white.png`). Those thin lines are a
  lighter pink than the keyed `#FF00FF` background, so they are outside the magenta key
  band and survive — this is grok's lower prompt-adherence on negative constraints, an
  image-model behaviour, **not** a `sprite_gen.gen` defect. The solid background itself
  keyed perfectly. For sprite rows destined for the extractor, prefer codex or add a
  post-key divider strip; the correction-loop (B gate) would flag the frame-count/grid
  artifact and inject a corrective hint on the next pass.

## What this proves for C-gen

1. `sprite_gen/gen/` is the single generation SSoT: one CLI (`sprite-gen gen`), two
   provider adapters (codex inline-base64 extraction, grok save-to-path + verify),
   plus the ported transparent chroma contract.
2. The same row prompt generates on **both** providers end-to-end, and the speed
   comparison is recorded here + in the two report JSONs.
3. Truth is always the decoded/verified PNG on disk (No Silent Fallback): codex decodes
   the inline base64, grok verifies the file it was told to write; a missing or non-PNG
   result fails loudly instead of reporting success.
