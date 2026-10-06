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
schedule. No interpolation is called. This operation has its own contract:
repair report `schema_version: 2`, comparison `schema_version: 3`,
`metric_version: source-restoration-v2`, `policy_version:
key-protected-source-copy-v1`, `scope: processing-defect-restoration`,
`operation: restore_active_cut`. The v1 comparison above is unchanged.
`source-restoration-v1`, an unreleased draft that copied the whole source cell,
is not this contract: its evidence and receipts are refused, never read as v2.

### What a restored cell is

In the target cell, a pixel that differs from the source at the same place and
the same source time becomes the source's pixel, **unless the source's pixel
would raise the key-colour guard there**. At those places the delivered pixel
stays. Every pixel of the output is the whole RGBA of one of the two at that
place: nothing is blended, moved, borrowed from another frame or recoloured,
and a colour hidden under zero coverage never gains coverage.

```text
K(p)       = alpha(p) x max(0, key hue excess(p) - 8)        whole numbers
differing  = places where the origin's cell and the source's differ in RGBA
protected  = differing places where K(source) > K(origin)
copied     = differing places that are not protected
output(p)  = source(p) where copied, origin(p) everywhere else
```

`K` is 255 times the alpha-weighted excess of `axes.key_colour`, with the same
full-spill bar and the same hue test (green: `G - max(R, B)`; magenta:
`min(R, B) - G`), so yellow, cyan, red and blue are not the key's hue. The mask
is made by the engine from the actual files and has no manual input: no
dilation, blur, key cleanup or tuning. When nothing is protected the output is
the whole source cell.

**The output's coverage must be the source's at every pixel.** Where a protected
place has another alpha in the origin than in the source, keeping the delivered
pixel would keep a silhouette the source does not have, so that proposal ends
with `status: unknown`, `common_failure: false`, reason
`protected-pixel-alpha-conflict` and empty `outputs`. Nothing is written. It
used that proposal's attempt; the next independent proposal may be tried. A
target whose every differing place is protected ends the same way with
`no-unprotected-source-pixel`. No protected pixel is given up to make a
candidate pass, however few they are.

### The origin

Restoration needs three sets of files, all read and hashed again on every call:

| Input | What it is |
|---|---|
| source | the clip, canvas, ordered keyed PNGs, extraction report and their manifest |
| origin | the five loop files the **first** restoration of this loop started from |
| baseline | the five loop files active now |

On a first request the origin is the baseline: pass the same five files twice.
After a candidate is adopted, the next request passes the adopted five files as
the baseline and **the same origin as before**. The origin is required and is
never guessed from the baseline. An application records the origin's object keys
and digests at the first request and supplies those bytes from then on; the
origin is evidence for recomputation, not a second copy of the active result.

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
  --origin-strip O.strip.png --origin-meta O.strip.json \
  --origin-report O.loop.json --origin-gif O.gif --origin-webp O.webp \
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
source-relative damage. `--proposal-index` is one-based, limited to 1–3. A cell
already restored, a strip or playback boundary cell and a cell that is not
played are never proposed. The stable `proposal_id` binds the baseline's five
digests, the origin's five digests, the source inputs and sequence, the
projection, the policy version, the target and the digests of its two masks.
Repeating the same index is the same proposal, not a retry strategy. The
application owns its deadline, attempted IDs and adoption transaction.

| Repair field | Contract |
|---|---|
| `status` | `candidate`, `no_change`, `unknown`, or `exhausted` |
| `common_failure` | A shared evidence failure; do not try the other proposals |
| `reasons` | Machine-readable explanation |
| `proposals_available`, `proposal_limit`, `proposal_index` | Available independent candidates and request budget |
| `proposal_id`, `target` | Selected identity and zero-based cell; null for a no-op |
| `applied_cells` | Cells earlier requests restored, in order, as rebuilt from the origin |
| `origin_artifacts`, `baseline_artifacts`, `candidate_artifacts` | Exact five-file byte receipts (`sha256`, `bytes` per file) |
| `source_artifacts`, `projection` | Source inputs, fixed transform, sample PTS and exact correspondence |
| `partial.copied`, `partial.protected` | The two masks of the target: `size`, `count`, row-major run lengths `rle` (the first run is clear) and `sha256` of the packed bits |
| `partial.alpha_equals_source` | Whether the output's coverage is the source's; `false` only with `protected-pixel-alpha-conflict` |
| `partial.protected_pixels` | `count`, `alpha_conflicts`, and for the first 256 in row order (`listed`) `x`, `y`, `origin_rgba`, `source_rgba`, `origin_key_weight`, `source_key_weight`, `alpha_equal` |
| `comparison` | Initial measurement of written files; compare again after finalization |
| `interpolation_calls` | Always zero in this operation |

For a normal input, `no_change` copies all five files byte-for-byte and compares
as `non_regressing`. If the legacy recipe cannot uniquely explain every
unmodified cell of the origin, the result is shared `unknown` without a
candidate. Historical repair indices are hints only: a target must have a final
and source-relative interpolation fault, and its source reference must pass the
common policy. A request whose proposals exist but are each refused stays
`unknown` or `regressed` per proposal and keeps the active result; it is not
reported as `no_change`.

The supported projection is scrub, small-component cleanup, optional integer
body ramp, one shared integer crop and the standard resampler. New loop reports
also record hashes before repair. Legacy crops are bounded by the recorded
scale recipe and canvas, with a search limit of 4096 hypotheses; multiple exact
matches remain unknown. Frame-specific alignment, different cuts, subsampling,
size hold, motion anchoring, cycle alignment, or unproven follow warps are not
accepted. Do not rerun follow on a restoration output.

### The receipt and what a later request rebuilds

A candidate's metadata is the baseline's, byte for byte. Its loop report is the
origin's own report with one key added, `restoration`; the historical
`jump_repair`, `producer` and animation records stay as the origin wrote them
and are not a new quality verdict. The five final digests are in the repair and
comparison reports, not in the loop report, which cannot hold its own digest.

| `restoration` field | Contract |
|---|---|
| `operation`, `metric_version`, `policy_version` | This contract |
| `origin_artifacts` | The origin's five byte receipts |
| `source_artifacts`, `source` | Source input receipts and the ordered keyed sequence identity |
| `projection` | Recipe, crop, cell, scale, wrap, samples, PTS, reference pixel hashes |
| `applied` | One entry per restored cell, in the order they were adopted; a cell appears once |
| `applied[].target`, `source_index` | The cell and its source frame |
| `applied[].copied`, `protected` | The two masks, as in the repair report |
| `applied[].origin_rgba_sha256`, `source_rgba_sha256`, `output_rgba_sha256` | The cell in the origin, in the source projection and as restored |
| `applied[].alpha_equals_source` | Always `true` for an applied cell |
| `applied[].proposal_id`, `baseline_artifacts`, `baseline_pixels_sha256` | The proposal, the five files it was proposed on and their decoded cells |
| `applied[].measurement_engine` | The implementation that wrote the entry; a later one recomputes everything else |

Nothing in a receipt is trusted. Every request reads the origin, the source and
the baseline, proves the projection on **the origin's** normal cells, then
makes each applied cell again from the origin and the source in the recorded
order. Each must have been a proposal of the loop as it stood before it, must
give the recorded masks and digests, and must pass the protections on that
loop. The cells read from the baseline must then equal the rebuilt ones: an
applied cell its partial copy, every other cell the origin's. The rebuilt
pixels are an expectation to compare with; they never replace the baseline's
files. A restored cell therefore stays verified although it is no longer the
source's whole cell.

These are errors, with nonzero exit: an origin that already carries a receipt;
a baseline that differs from the origin without a receipt; a receipt naming
another origin or source; an applied entry that is repeated, out of range, not
a proposal of its prefix, or whose mask, digests or protections the inputs do
not reproduce; an emptied or extended receipt; changed metadata bytes or a
changed report outside the receipt; and baseline pixels that differ from the
rebuilt ones in any cell. A receipt of another metric or policy version is
shared `unknown` with `restoration-receipt-policy-unsupported`.

An earlier file digest in `applied[].baseline_artifacts` is a label bound into
that entry's proposal ID: the first must be the origin's, and the pixels each
stands for are recomputed (`baseline_pixels_sha256`). Digests are not a
signature. A caller that replaces the origin, the source and their manifest
together presents a different history, which the engine cannot tell from the
first; the application's stored origin digests and its revision check own that
boundary.

### Comparing the final artifacts

Compare the final artifacts using the **same** source inputs and origin:

```bash
sprite-gen video-loop-compare \
  --baseline-strip B.strip.png --baseline-meta B.strip.json \
  --baseline-report B.loop.json --baseline-gif B.gif --baseline-webp B.webp \
  --origin-strip O.strip.png --origin-meta O.strip.json \
  --origin-report O.loop.json --origin-gif O.gif --origin-webp O.webp \
  --candidate-strip candidate-1/loop.strip.png --candidate-meta candidate-1/loop.strip.json \
  --candidate-report candidate-1/loop.loop.json \
  --candidate-gif candidate-1/loop.gif --candidate-webp candidate-1/loop.webp \
  --source-frames-dir frames/keyed --source-manifest source.json \
  --source-clip clip.mp4 --source-canvas canvas.png --source-frames-report frames.json \
  --repair-evidence repair-1.json --report comparison-1.json
```

The comparator rebuilds the baseline from the origin and the source, makes the
target's masks and partial copy again, and reads all five final artifacts.
Changed JSON whitespace changes its byte receipt. A changed file no longer
bound by the repair evidence is an input error. All verdicts return exit 0;
malformed, missing or changed source/origin/evidence files and encoder/decoder
failures return nonzero. An unsupported mapping is `unknown`, not an execution
error disguised as a normal result.

The report carries `origin`, `baseline` and `candidate` (each with its five
`artifacts`), `source.artifacts` and `source.sequence`, `provenance.applied`
(the earlier cells as rebuilt), `restoration` (the changed cell's `target`,
`proposal_id`, masks and protected pixels, as in the repair report),
`preservation`, `playback`, `axes`, `cleared_faults` and `gait`.

`improved` requires that the one changed cell is byte-exact the partial copy
made from the origin and the source, its coverage the source's, the candidate's
receipt the recomputed one, at least one cleared actual fault, unchanged normal
cells and metadata bytes, and no worsening protected axis. Changed pixels
outside the copied mask return `regressed` with
`changed-cell-is-not-the-verified-source-copy`; a receipt that differs returns
`unknown` with `candidate-report-differs-from-verified-receipt`; changed
metadata bytes `candidate-metadata-bytes-changed`; equal pixels with any other
changed file `unchanged-pixels-with-changed-artifacts`.

The common outline/smear thresholds apply to every final frame, including
neighbours of the restored one. Raw partial coverage is reported; the protected
quantity is excess over the fixed source reference with common source
neighbours. Restoring natural antialiasing may increase raw partial coverage.
Boundary pixels and actual playback intervals stay identical. GIF quantization
and WebP decoding are checked against the strip export mapping, with their
actual integer delays recorded separately from fractional strip timing.

Key-colour protection is read again from the final strip, whatever the mask
promised. `axes.key_colour.introduced_excess` sums only the positive per-pixel
increases of the alpha-weighted excess on the fixed canvas, per cell, and
`introduced_pixels` counts the pixels that increased. The sum is not a pixel
count. A reduction elsewhere cannot offset newly introduced spill, even when
the cell's total tint decreases.

For an outline-specific UI success message, require `verdict == improved` and
an `outline` entry in `cleared_faults[].faults`. `axes` contains raw baseline,
reference and candidate values for outline/smear, source-relative partial
coverage and key colour. `gait.source_order == preserved` proves source motion
order only; `gait.absolute == unverified` remains explicit. A restored cell is
the source's pixel or the delivered one at each place; these measurements do
not claim the source's whole drawing, nor to repair mistakes already in it.

A candidate-specific unknown or regression may be followed by the next unused
proposal within budget. Shared unknown stops the request. Upload and activate
only the final five digests returned by comparison; a file's existence or the
initial repair report is not authority to replace the active revision.

Every proposal in one request starts from the same immutable baseline. Proposal
2 does not include proposal 1. After adoption, a subsequent request must supply
the adopted five files as its new baseline with the unchanged origin, so its
proposal IDs and baseline digests change. Once all supported damage has been
restored, the next request is a byte-exact no-op. A client must never apply an
old proposal or compare receipt to the new baseline revision.
