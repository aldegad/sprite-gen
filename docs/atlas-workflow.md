# Execute the existing row sprite pipeline

This page owns the agent's row execution procedure. Start and finish conversation choices belong to [user-workflow](user-workflow.md). Stage data, numeric settings and output ownership belong to [run-contract](run-contract.md).

## Lock the base before preparing a run

Use one accepted, uncropped, full-body idle image with the intended proportions, style, orientation and identity. A pixel-art run needs a measurable pixel grid in the base; changing prompt wording later cannot correct its style. If no suitable base exists, generate it with the chosen image provider and establish it as the reference before motion generation. Directional anchors replace the base in later row inputs; [directional-anchor-workflow](directional-anchor-workflow.md) owns that sequence and the `anchor` resolver.

## Execute

1. **Prepare.** Put numeric choices in `sprite-request.json`: cell dimensions and margins, chroma key, states, frame counts, fps and fit. Choose the key against the subject's actual colors using [chroma-alpha](chroma-alpha.md). Prepare generates prompts and empty layout guides from that request; do not hand-copy frame counts elsewhere. [states-and-frames](states-and-frames.md) owns frame recommendations. Layout guides carry geometry only.
2. **Generate rows.** Use `gen-set --provider codex` for the GPT image-sprite route. The provider that made the base is an independent choice. Attach the resolved identity reference and that state's generated layout guide. Extra motion references must be recorded in `qa-notes.md`. Independent rows use the existing batch concurrency; [gen](gen.md) owns its execution and reports.
3. **Extract.** Run the canonical extractor for every raw row. It removes chroma, separates characters, applies the requested pixel processing and places transparent frames. Never replace this with equal-width cuts or a one-line resize for a preview. A character crossing an imaginary slot can still be extracted intact when the poses are separable. Actual missing/occluded pixels are not restored by cropping. Choose sufficient output dimensions to preserve the desired scale; [pixel-unfake](pixel-unfake.md) owns fit options.
4. **Compose and verify.** Produce the atlas, runtime manifest and animation preview with existing compose tools. Inspect the frame manifest and output reports; reject empty frames, unwanted edge/chroma remnants and identity defects. Review motion as motion using [qa-motion](qa-motion.md). Do not hide failed motion by timing changes or silently replacing poses. A user's explicit slow preview request changes display speed, not the QA verdict.
5. **Deliver.** Send the checked files, then follow the finish stage in the user workflow. Curation display is optional. After curation edits, exports come from the curated result, not the unedited frame cache.

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen prepare --out-dir /absolute/run \
  --character-id example --base-image /absolute/base.png --request /absolute/request.json
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen gen-set --run-dir /absolute/run --provider codex
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen extract --run-dir /absolute/run
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen compose-atlas --run-dir /absolute/run
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen compose-gif --run-dir /absolute/run --out-dir /absolute/run/previews
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen inspect --run-dir /absolute/run
```

The runtime consumes `manifest.json.frame_layout`; it does not infer a grid from alpha. AI operates only at generation. Frame cleanup and atlas assembly remain deterministic, with one writer per run and atomic publication. [architecture](architecture.md) explains these boundaries. [subject-profiles](subject-profiles.md) covers effects and sparse subjects. [breathing](breathing.md), [recolor](recolor.md) and [layer-tracks](layer-tracks.md) own optional post-processing; none is an extra default question for every sprite request.
