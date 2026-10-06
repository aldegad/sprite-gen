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
| `measurement_engine.implementation_sha256` | Fingerprint of the video implementation and CLI that performed the measurement |
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
