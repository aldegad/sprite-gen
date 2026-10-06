# Comparing delivered loops

`video-loop-compare` reads the **final strip and metadata**, including any
`video-follow` changes. A loop report supplies provenance; its seam score and
pass status do not describe later pixels and are never used as comparison scores.

```bash
sprite-gen video-loop-compare \
  --baseline-strip current.strip.png --baseline-meta current.strip.json \
  --baseline-report current.loop.json \
  --candidate-strip final.strip.png --candidate-meta final.strip.json \
  --candidate-report candidate.loop.json --report comparison.json
```

All inputs are read only. Exit 0 means a comparison was produced, including an
`unknown` verdict. Invalid JSON, image/metadata geometry disagreement, empty
frames, invalid numeric timing and I/O failures are errors, with nonzero exit.
The output cannot overwrite an input. The JSON written to `--report` is also
printed to stdout.

## Report contract

The report has `kind: "sprite-gen-video-loop-comparison"`, `schema_version: 1`
and `metric_version: "same-sample-defects-v1"`.

| Field | Meaning |
|---|---|
| `verdict` | `improved`, `non_regressing`, `regressed`, or `unknown` |
| `reasons` | Machine-readable codes explaining the verdict; an input-specific reason starts with `baseline:` or `candidate:` |
| `baseline`, `candidate` | The actual artifacts, decoded pixels, frame count, cell size, timing, source cut, producer and follow presence |
| `<side>.artifacts.{strip,meta,report}.{sha256,bytes}` | SHA-256 and size of each **original file's bytes**; JSON whitespace is significant |
| `<side>.pixels_sha256` | SHA-256 of the ordered decoded RGBA cell bytes |
| `measurement_engine.implementation_sha256` | Fingerprint of the video, keying, resampling, GIF and CLI code that performed the measurement |
| `axes.<axis>` | `baseline`, `candidate`, `comparable`, `reason`, `status` for that axis |
| `limits` | What the comparison does not establish |

Freeze the bytes to be uploaded before invoking the comparison, then upload
those exact bytes. An application binds their digests to its active revision,
checks that revision again before activation, and owns its acceptance decision.
An application that automatically replaces a result should accept **only
`improved`**. Revision IDs, database transactions and billing are outside the
engine. A comparator error must remain an error; it is not permission to adopt
a candidate on an old seam score.

## What v1 can order

The order is deliberately limited to measured defects at **the same original
sample times, playback timing, crop and scale**. `video-loop` records the
ordered original keyed PNG sequence hash, count and fps in `source`, and a
`source_cut` in strip metadata. The source identity is read before cleanup,
size hold and anchoring. The strip builder records its actual `sample_indices`
and `source_rect`; foot anchoring has no fixed source rectangle.

The comparator requires consistent source evidence on both sides. It does not
regenerate a baseline, infer provenance from a filesystem path, rotate a cycle,
resample time, align each body to remove its bob, or assign a foot identity from
a silhouette peak. A different cut, a cycle-aligned strip without a proven
source mapping, legacy evidence, missing/inconsistent timing or unverified
coordinates returns `unknown`. Different cuts with different seam-ratio
denominators are therefore never ranked by those ratios.

For comparable inputs, each frame's `dark_excess`, `outline_loss` and
`partial_excess` is measured against its two actual final neighbours with
`rife.smear`. Positive excess is compared frame by frame, without averaging
away a worse frame. The seam is the mean absolute difference of premultiplied
RGBA over the shared native cell canvas, also reported per second. A worsening
component yields `regressed`; no weighted total score is constructed.

An improvement additionally needs **byte-identical alpha coverage at each
sample**. This proves preservation of the silhouette's pose, bob and size
without making an aesthetic assumption that less motion is better. Changed
coverage with no measured regression is `unknown`: v1 cannot certify the
intended deformation of feet, hair or soft parts. This is a limitation, not a
request to hold every frame still. Equal pixels and timing are
`non_regressing`, never `improved`. Non-regression describes these axes only,
not the drawing's overall quality.

Colour changes must be confined to frames that previously failed the shared
interpolation policy (`smear` or `outline`), and those changed frames must now
pass it. Other drawings stay byte-identical. A smaller seam obtained by
flattening colour animation is not sufficient: it returns
`changed-unfaulted-drawings-unverified`. A remaining interpolation fault returns
`interpolation-fault-remains`. These are `unknown` unless another comparable
axis already proves a regression.

`body_motion` reuses the common-body tracker from `video-follow`.
`top_band_motion` is the existing silhouette-top observation, which may follow
an accessory instead of a head. Both are descriptive; reducing their motion
is not scored as improvement. Rigid scale pumping is not inferred from a
changing silhouette bounding box.

Both automatic and fixed gait cuts emit `gait.status: "unverified"` with
`reason: "own-foot-contacts-not-identified"`, cut-local silhouette signals and
any existing search screens. These signals neither identify the character's
own feet nor prove two alternating contacts. Comparing the same source samples
preserves their relative phase; it does not certify that the source was a
correct gait cycle.

Common reason codes include `identical-pixels-and-timing`,
`measured-defect-reduced-with-motion-preserved`, `no-measured-defect-improvement`,
`different-source-samples-phase-unverified`, `different-timing`,
`spatial-basis-unverified`, `changed-coverage-pose-preservation-unverified`,
`changed-unfaulted-drawings-unverified`, `interpolation-fault-remains`,
`<axis>:regressed`, and the input-prefixed `source-provenance-missing`,
`source-provenance-invalid`, `source-provenance-inconsistent`,
`source-report-unverified`, `source-samples-unverified`,
`source-geometry-invalid`, `source-geometry-inconsistent`,
`source-timing-inconsistent`, `aligned-source-phase-unverified`,
`loop-playback-unverified`, `timing-missing`, `cycle-duration-missing`,
`timing-inconsistent`, `nonuniform-timing-unsupported`.

See [loop repair](loop-repair.md) for proposal rejection and the interpolation
quality bounds, and [video pipeline](video-pipeline.md) for processing stages.

## Restoring the active cut from source

`video-loop-repair` proposes a separate output for one damaged interior cell.
It preserves the cut, metadata, normal cells, boundary cells, and actual GIF/WebP
schedule. No interpolation is called. This operation has its own comparison
contract: `schema_version: 2`, `metric_version: source-restoration-v1`,
`scope: processing-defect-restoration`, `operation: restore_active_cut`.
The v1 comparison above is unchanged.

First retain the actual clip, canvas, keyed PNGs, and extraction report. New
extractions can use `video-frames --source-manifest source.json --reference
canvas.png` alongside their normal extraction flags. To inventory an existing
extraction without extracting it again:

```bash
sprite-gen video-source-manifest \
  --clip clip.mp4 --canvas canvas.png --frames-report frames.json \
  --frames-dir frames/keyed --out source.json
```

The receipt binds input bytes, ordered keyed PNG bytes, actual video PTS,
extraction report recipe and measurement implementation/environment. It is an
inventory of supplied evidence, not a signature or an assertion of a historical
producer's identity. Preserve the extraction environment log with the manifest.
Consumers must retain immutable source files. The source sequence and input
hashes are read and checked again, not trusted from a `verified` flag.

```bash
sprite-gen video-loop-repair \
  --baseline-strip B.strip.png --baseline-meta B.strip.json \
  --baseline-report B.loop.json --baseline-gif B.gif --baseline-webp B.webp \
  --source-frames-dir frames/keyed --source-manifest source.json \
  --source-clip clip.mp4 --source-canvas canvas.png --source-frames-report frames.json \
  --out-dir candidate-1 --name loop --proposal-index 1 --report repair-1.json
```

`--out-dir` must not exist. Artifacts are built in a sibling staging directory
and published together. `outputs` maps `strip`, `meta`, `report`, `gif`, `webp`
to `loop.strip.png`, `loop.strip.json`, `loop.loop.json`, `loop.gif`, `loop.webp`.
`--name` sets their common stem. The repair report is separate and must not
replace any input or candidate artifact.

The engine enumerates up to three independent single-cell proposals, ranked by
source-relative damage. `--proposal-index` is one-based, limited to 1–3. The
stable `proposal_id` binds baseline file digests, source sequence, policy and
target. Repeating the same index is the same proposal, not a retry strategy.
The application owns its deadline, attempted IDs and adoption transaction.

| Repair field | Contract |
|---|---|
| `status` | `candidate`, `no_change`, `unknown`, or `exhausted` |
| `common_failure` | A shared evidence failure; do not try the other proposals |
| `reasons` | Machine-readable explanation |
| `proposals_available`, `proposal_limit`, `proposal_index` | Available independent candidates and request budget |
| `proposal_id`, `target` | Selected identity and zero-based cell; null for a no-op |
| `baseline_artifacts`, `candidate_artifacts` | Exact five-file byte receipts |
| `source_artifacts`, `projection` | Source inputs, fixed transform, sample PTS and exact correspondence |
| `comparison` | Initial measurement of written files; compare again after finalization |
| `interpolation_calls` | Always zero in this operation |

For a normal input, `no_change` copies all five files byte-for-byte and compares
as `non_regressing`. If the legacy recipe cannot uniquely explain every
unmodified cell, the result is shared `unknown` without a candidate. Historical
repair indices are hints only: a target must have a final and source-relative
interpolation fault, and its source reference must pass the common policy.

The supported projection is scrub, small-component cleanup, optional integer
body ramp, one shared integer crop and the standard resampler. New loop reports
also record hashes before repair. Legacy crops are bounded by the recorded
scale recipe and canvas, with a search limit of 4096 hypotheses; multiple exact
matches remain unknown. Frame-specific alignment, different cuts, subsampling,
size hold, motion anchoring, cycle alignment, or unproven follow warps are not
accepted. Do not rerun follow on a restoration output.

Compare the final artifacts using the **same** source inputs:

```bash
sprite-gen video-loop-compare \
  --baseline-strip B.strip.png --baseline-meta B.strip.json \
  --baseline-report B.loop.json --baseline-gif B.gif --baseline-webp B.webp \
  --candidate-strip candidate-1/loop.strip.png --candidate-meta candidate-1/loop.strip.json \
  --candidate-report candidate-1/loop.loop.json \
  --candidate-gif candidate-1/loop.gif --candidate-webp candidate-1/loop.webp \
  --source-frames-dir frames/keyed --source-manifest source.json \
  --source-clip clip.mp4 --source-canvas canvas.png --source-frames-report frames.json \
  --repair-evidence repair-1.json --report comparison-1.json
```

The comparator rebuilds the reference from actual source files and reads all
five final artifacts. Changed JSON whitespace changes its byte receipt. A
changed file no longer bound by the repair evidence is an input error. All
verdicts return exit 0; malformed, missing or changed source/evidence files and
encoder/decoder failures return nonzero. An unsupported mapping is `unknown`,
not an execution error disguised as a normal result.

`improved` requires an exact source-time replacement, at least one cleared
actual fault, unchanged normal cells and no worsening protected axis. The
common outline/smear thresholds apply to every final frame, including neighbours
of the restored one. Raw partial coverage is reported; the protected quantity
is excess over the fixed source reference with common source neighbours.
Restoring natural antialiasing may increase raw partial coverage. Alpha-weighted
key-colour excess must not increase. Other colour changes cannot be accepted:
the changed cell must exactly equal its source projection. Boundary pixels and
actual playback intervals stay identical. GIF quantization and WebP decoding
are checked against the strip export mapping, with their actual integer delays
recorded separately from fractional strip timing.

For an outline-specific UI success message, require `verdict == improved` and
an `outline` entry in `cleared_faults[].faults`. `axes` contains raw baseline,
reference and candidate values for outline/smear, source-relative partial
coverage and key colour. `gait.source_order == preserved` proves source motion
order only; `gait.absolute == unverified` remains explicit. These measurements
do not claim to repair mistakes already present in the source drawing.

A candidate-specific unknown or regression may be followed by the next unused
proposal within budget. Shared unknown stops the request. Upload and activate
only the final five digests returned by comparison; a file's existence or the
initial repair report is not authority to replace the active revision.

Every proposal in one request starts from the same immutable baseline. Proposal
2 does not include proposal 1. After adoption, a subsequent request must supply
the adopted five files as its new baseline, so its proposal IDs and baseline
digests change. The candidate loop report records cumulative `restored_cells`;
those cells join the next bridge's exact normal-cell checks and cannot be
silently replaced again. Its historical `jump_repair.replaced` remains intact.
Once all supported damage has been restored, the next request is a byte-exact
no-op. A client must never apply an old proposal or compare receipt to the new
baseline revision.

Key-colour protection uses the existing full-spill tint threshold and the key
hue channels. `axes.key_colour.introduced_excess` sums only positive per-pixel
increases on the fixed canvas. A reduction elsewhere cannot offset newly
introduced spill, even when the cell's total tint decreases.
