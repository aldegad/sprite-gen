# Layer Tracks — rig · track · composite contract (SSoT)

> Status: **contract** (normative). This doc owns the optional layer feature: the
> character rig profile, the per-row track kind, the composite stack, and what a
> layer bake is allowed to touch.
>
> Precedence, same as the rest of the docs: [`../SKILL.md`](../SKILL.md) owns the
> behavior contract, [`run-contract.md`](run-contract.md) owns the structural
> contract (stage table, run-dir tree, view payload), and
> [`architecture.md`](architecture.md) only ever describes the code. This doc owns
> the layer vocabulary and its boundaries; where it touches the run-dir tree or the
> stage table, `run-contract.md` still wins.
>
> Declaration validation is implemented by [`../sprite_gen/layers.py`](../sprite_gen/layers.py)
> and pinned by `tests/test_layer_contract.py`; the composite bake is
> [`../sprite_gen/compose_layers.py`](../sprite_gen/compose_layers.py), pinned by
> `tests/test_layer_compose.py`.

## 0. One sentence

A run may declare a **rig** (a character type profile plus integer landmarks per
frame) and a **track** per row (`base`, `action_overlay`, `prop_effect`,
`full_body_override`), which lets a **composite** stack rows onto each other by
integer pivot translation and arbitrary alpha masks — deterministically, and
without changing anything about a run that declares none of it.

## 1. The compatibility rule (read this first)

**A request with no `rig`, no `states.<state>.track`, and no `layers` is not a
layer run, and every byte it produces is what it produced before this feature
existed.** No new manifest key, no new sidecar field, no new file in the run dir,
no new validation that could reject a request that used to pass.

Concretely:

- `layers.has_layer_contract(request)` is the single opt-in test. False ⇒ the
  layer validator returns an empty error list and every layer-aware stage is a
  no-op.
- `manifest.json` gains `rig` / per-row `track` **only** when the request declares
  a rig. The non-layer manifest key set is pinned by
  `test_non_layer_run_manifest_surface_is_unchanged`.
- Composite output never enters `frames/`, never enters `frames-manifest.json`,
  and never becomes a row of the base atlas (§2, findings B3/B4).

## 2. Ownership audit — what already owns what

This is the audit this contract is built on. Each row is the state of the code as
shipped, with the enforcement point, so a layer change can be checked against a
boundary instead of a memory.

| Surface | Canonical owner | Single writer | Generation stamp | What layers may add |
|---|---|---|---|---|
| `sprite-request.json` | the run (numeric SSoT) | `prepare` writes it; `runio.load_request` is the only read gate | none | `rig`, `states.<state>.track`, `layers` |
| `frames/` + `frames-manifest.json` | derived cache of (raw + request + engine) | `extract` only, published as one transaction | `engine_revision` per row, self-healed by `heal_run` | **nothing** |
| `curation.json` | the human's edits | webview POST and `sprite-gen anchor --pick`, both via `curation.write_curation_atomic` | `run_revision` + per-state `revision` | **nothing** |
| `manifest.json` / `sprite-sheet-alpha.png` | `compose_atlas` output | `compose_atlas` | derived from the above | `rig` block, `animation.rows.<state>.track` |
| `variants/` | `recolor` bake | `recolor` | none (colourways survive re-bakes) | — (precedent for §5's `layers/`) |

### Boundary findings

- **B1 — `prepare` re-emits the request from a whitelist.** `prepare._run` builds a
  fresh dict (`version`/`kind`/`engine`/`character`/`cell`/`chroma_key`/`states`/
  `style`/`motion_phase_guides`, plus `directions`/`layout`/`fit` when present), and
  `normalize_states` rebuilds each state entry as `frames`/`fps`/`loop`/`action`.
  Anything else in `--request` / `--request-json` is dropped, and the command still
  exits 0 — measured, not inferred: a request carrying `rig`, `tracks` and
  `states.idle.takes` came back out with none of the three. So `takes` (a
  first-class contract key, `run-contract.md` §2) has to be hand-written into
  `sprite-request.json` today. **Consequence:** the layer contract may never assume
  request passthrough. `rig`, `states.<state>.track` and `layers` must be added to
  the carried set explicitly, and the drop must become observable (§7).
- **B2 — there is no central request schema.** Every stage reads the keys it wants
  (`request.get("fit")`, `request["cell"]`, …); `runio.load_request` only normalizes
  retired keys in memory (it never writes — `run-contract.md` §2-b-2). **Consequence:** layer validation lives in exactly one module
  (`sprite_gen/layers.py`) that every layer-aware entry point calls, instead of
  per-stage key checks that drift.
- **B3 — `frames/` is a derived cache with one writer.** `heal_run` re-derives a row
  from `raw/` whenever the engine revision moves, and a row with no `raw/` is kept
  with an observable `kept_stale` note. **Consequence:** a composite (whose source is
  other rows, not a raw strip) can never live in `frames/` — the next heal would
  either destroy it or permanently mark it stale.
- **B4 — request states and manifest rows must match exactly.**
  `extract._require_generation_consistency` fails loud when a request state has no
  manifest row, when a row names a state the request does not, and when a physical
  frame dir is an orphan. **Consequence:** a composite must not be declared in
  `request.states`. This is what settles the "is a composite just another row?"
  question — it cannot be, without either faking an extraction or weakening the
  consistency gate. Composite names are therefore required to be disjoint from state
  names.
- **B5 — `curation.json` is generation-stamped human truth.** A mismatched stamp
  drops the row's curation (with a backup and a stderr report). **Consequence:**
  machine-authored layer data must not live there. A rig in the sidecar would be
  silently dropped by an ordinary re-extract; a rig in the request survives it.
- **B6 — the manifest key set is built in one place.** `compose_atlas` assembles
  `manifest.json` as a closed dict, and `animation.rows.<state>` already carries
  per-row extensions (`frame_variant`, `durations_ms`, `breathe`).
  **Consequence:** `track` belongs beside them; `rig` belongs at the top level; both
  appear only for a rig run.
- **B7 — atlas cells are shared between identical instances.** `compose_atlas`
  reuses one cell for instances with the same (source frame, transform, pixel edits,
  breathe phase), so `frame_layout.rows.<state>` can repeat the same rect.
  **Consequence:** manifest landmark arrays are indexed by **play position** — same
  length and order as `frame_layout.rows.<state>` — never by unique cell. A consumer
  that zips landmarks against rects must get a 1:1 match.
- **B8 — one cell shape end-to-end.** There is no separate generation cell and atlas
  cell (`architecture.md` §4). **Consequence:** a stack composes rows of one run, in
  that run's cell. Cross-run or cross-cell stacking is rejected, not rescaled.
- **B9 — mirrored directions have no generated rows.** `directions.mirror` declares a
  runtime mirror. **Consequence:** a stack element must name a generated state;
  mirroring stays a runtime transform applied to the composite, so no landmark is
  mirrored at bake time.
- **B10 — a row's frame pool includes its takes.** `layout.state_frame_total` is the
  pool size, and curation indices live in that space. **Consequence:** landmark frame
  indices are **pool indices** (takes included), and a composite consumes the
  **curated play sequence** (`curation.state_plan`), exactly like `compose_atlas` —
  so a composite carries the human's picks, order, pixel edits and transforms instead
  of the raw extractor output (`run-contract.md` §2-c).

## 3. Request schema extension

All three keys are optional. Types are strict: coordinates are JSON integers, never
floats and never booleans.

```jsonc
{
  "rig": {
    "profile": "humanoid_biped",          // humanoid_biped | quadruped | blob_or_tentacle | prop
    "landmarks": {                        // state -> pool frame index (string) -> name -> [x, y]
      "down_walk": {
        "0": {"root": [32, 44], "crown": [32, 8], "hand_r": [40, 30]},
        "1": {"root": [32, 43], "crown": [32, 7], "hand_r": [41, 31]}
      },
      "watering_can": {
        "0": {"root": [10, 10], "grip": [8, 12]}
      }
    }
  },

  "states": {
    "down_walk":     {"frames": 2, "fps": 8, "loop": true, "track": "base"},
    "watering_can":  {"frames": 1, "fps": 8, "loop": false, "track": "prop_effect"}
  },

  "layers": {
    "down_walk_watering": {               // composite name — must NOT be a state name (B4)
      "stack": [                          // list order = draw order, bottom first
        {"state": "down_walk"},                                        // body element, index 0
        {"state": "watering_can", "from": "grip", "to": "hand_r",      // pivot pair
         "mask": "references/masks/can.png", "allow_clip": false}
      ],
      "fps": 8, "loop": true              // optional; default = the body element's state entry
    }
  }
}
```

### 3.1 Profiles and landmarks

`root` is the composition pivot for **every** profile. Nothing is inferred from
geometry: an undeclared landmark is an error, never a guess.

| Profile | Required on every frame | Reserved optional vocabulary |
|---|---|---|
| `humanoid_biped` | `root`, `crown` | `head`, `neck`, `hand_l`, `hand_r`, `foot_l`, `foot_r` |
| `quadruped` | `root` | `head`, `muzzle`, `tail`, `foot_fl`, `foot_fr`, `foot_bl`, `foot_br` |
| `blob_or_tentacle` | `root` | `mouth`, `eye_l`, `eye_r`, `tip_a`…`tip_d` |
| `prop` | `root` | `grip`, `tip`, `muzzle` |

- `crown` (정수리) is a **humanoid head landmark for generation and QA framing**, not
  a pivot. It is required only for `humanoid_biped`; forcing a head rule onto an
  octopus or a quadruped is explicitly out of scope.
- **Required landmarks are judged by profile AND track.** The profile answers *what
  this character is*; the track answers *what this row draws*. A `prop_effect` row
  draws a prop or an effect, not the character, so it is held to the `prop` profile's
  requirement — its own `root`, plus whatever self-meaning names (`grip`, `tip`) it
  declares — inside a `humanoid_biped` run just the same. A watering can has no
  정수리: requiring one would leave a single way out, declaring a made-up `crown` on
  the row the composer aligns against, which turns a required pivot into noise. The
  narrowing is `prop_effect` only — `base`, `action_overlay` and `full_body_override`
  all draw the body and keep the rig profile's full set, so a humanoid run still
  cannot ship a body frame without a crown.
- **An unknown `rig.profile` requires nothing, on every track.** Profile validity is
  judged before the `prop_effect` narrowing, so a run that declared no valid profile
  gets one error — the profile itself — and never a derived "missing `root`" on its
  prop rows next to it. This holds for any JSON type: a `rig.profile` that is a list
  or an object is reported like any other unknown value, not raised.
- The reserved column is a naming recommendation, not a restriction: any name
  matching `^[a-z][a-z0-9_]{0,31}$` is accepted. Whatever is used must be declared on
  **every** frame of that row — a landmark that exists on some frames only is
  rejected, because a composite would otherwise skip frames.
- `rig.landmarks.<state>` must cover the row's whole frame pool, `0 .. state_frame_total-1`,
  takes included (B10). A state **absent** from `rig.landmarks` is not a violation by
  itself — a rig run may declare landmarks for the rows it composes and leave the rest
  alone. It becomes a violation the moment a stack element refers to that row, and the
  base manifest simply carries no landmark array for it (§5).
- Coordinates are integer cell coordinates of that state's frames, inside the cell —
  **the baked instance's coordinates**, i.e. what the curation view shows after the
  human's transform and pixel edits, which is exactly what a composite draws. Curation
  therefore comes first and landmarks second; re-transforming a row after declaring its
  pivots moves the art out from under them, which is what the `revision` pin (§3.3) and
  the row fingerprints in the bake report are there to make provable.
- A **curated clone instance** lives outside the pool (its index is `>= state_frame_total`,
  `curation.state_clones`) and therefore has no declared landmarks. It carries its own
  transform, so borrowing the pivots of the frame it copies would be a guess: a clone in
  a layer row fails the bake and the base manifest's `rig` block, loudly, instead.

### 3.2 Tracks

An undeclared row is `base` — the explicit default, so a legacy run reads as an
all-`base` run rather than as an unknown kind.

| Track | Draws | Landmarks required per frame | Requires | Forbids |
|---|---|---|---|---|
| `base` | the whole body | the rig profile's set | — | sharing a stack with another body element |
| `action_overlay` | a partial-body action drawn over the base | the rig profile's set | a `base` in the stack | coexisting with `full_body_override` |
| `prop_effect` | a prop or effect placed at a socket landmark | the `prop` profile's set (`root`) | a body element; its `to` landmark declared on that body row | — |
| `full_body_override` | the whole body, replacing the base | the rig profile's set | being the only body element | `base` and `action_overlay` in the same stack |

`full_body_override` is the escape hatch for a motion that cannot be decomposed —
a two-handed swing owns the whole body, so the contract makes that explicit
instead of letting an overlay half-cover a base that is still walking.

### 3.3 Stack elements

| Key | Default | Meaning |
|---|---|---|
| `state` | required | the row this element draws |
| `from` | `"root"` | the element's own landmark |
| `to` | `"root"` | the landmark **on the body element** that `from` is placed onto |
| `mask` | none | run-relative path to an alpha mask (§4); must stay inside the run dir |
| `allow_clip` | `false` | permit opaque pixels to fall outside the cell |
| `revision` | none | pin: the source row's `state_revision` segments as of the landmark declaration (§4) |

The body element is `stack[0]`; every other element is aligned onto it. A stack has
exactly one body element. A non-body element's row has either the same pool size as
the body row, or exactly one frame (a held pose).

A **held pose is a pool of one**, not a row that curation happened to shorten. An
element whose pool matches the body plays the body's curated sequence position for
position; if curation leaves the two sequences different lengths, the bake fails
rather than choosing which body frames go unaccompanied.

## 4. Composition contract (deterministic)

The composer ([`../sprite_gen/compose_layers.py`](../sprite_gen/compose_layers.py),
`compose_layers.bake(run_dir, names=None)`) bakes output frame `i` as:

1. Start from a fully transparent cell of the run's `cell` geometry.
2. For each element in stack order (bottom first):
   1. Resolve the source instance from the element's **curated play sequence**
      (`curation.state_plan`), position `i`, or its only instance for a 1-frame
      element (B10, `run-contract.md` §2-c).
   2. If `mask` is declared, multiply alpha: `a' = (a * m + 127) // 255`, integer
      arithmetic, rounding half up. The mask is an arbitrary shape — any PNG of the
      cell's size; its alpha (or luminance for an `L` image) is `m`. A missing file or
      a size mismatch is a hard error.
   3. Translate by the integer offset `to_point - from_point`, where `to_point` is the
      body element's `to` landmark at position `i` and `from_point` is this element's
      `from` landmark. The body element itself translates by `(0, 0)`.
   4. `alpha_composite` onto the accumulator.
3. Emit the cell.

**No resampling, no rotation, no scaling.** Layer composition is integer translation
plus alpha compositing, which is why the same input produces the same bytes — the
verification is composing twice and comparing SHA-256 of the atlas and the manifest.
Rotation and scale remain curation's job, already baked into the source instance.

Two more properties of that loop, both load-bearing:

- The source instance is the **curated** one — the human's pick order, transform, pixel
  edits and breathe phase are already inside it, assembled from the same primitives
  `compose_atlas` uses. A composite is therefore a combination of what the atlas bakes,
  never of the raw extractor output.
- Identical composed cells **share one column**, exactly like the atlas (B7): the rect
  list in the composite manifest is indexed by play position and may repeat.

Failure diagnostics the composer owns (everything that needs the run dir):

- a declared source frame missing from the published generation;
- a mask file missing, unreadable, not the cell's size, or resolving outside the run dir;
- an art pixel translated outside the cell while `allow_clip` is false — reported with
  the clipped pixel count and failed, never silently cropped. "Art" is alpha > 8, the
  same floor `recolor` and the GIF export already use, so a soft key's sub-threshold
  edge bleed is not a clip;
- an element whose curated sequence does not match the body's (§3.3), and a body row
  curated down to no frames at all;
- an instance with no declared landmarks — a curated clone (§3.1);
- a declared landmark whose row was regenerated: the bake report records each source
  row's `state_revision`, and an element may declare `revision` to be checked, in
  which case a mismatch fails loud (same mechanism as `curation.anchors`: the stored
  segments must be a prefix of the current ones).

The bake is **all-or-nothing**. Every composite is composed in memory first; one
violation anywhere fails the call with the complete diagnostic list and writes nothing,
so `layers/` never holds a partially valid set.

## 5. Output and manifest extension

**Composites are a sibling artifact tree, not atlas rows** (B3/B4), following the
precedent `variants/` already set:

```text
<run-dir>/
  layers/<name>.png                 # composed atlas for that composite
  layers/<name>.manifest.json       # runtime manifest, same shape as manifest.json
  layers/layers.report.json         # per-composite provenance: stack, source revisions,
                                    #   per-element offsets, clipped-pixel counts
```

`layers/<name>.manifest.json` keeps the runtime shape a consumer already reads
(`game_input`, `degraded_static_fallback`, `animation.rows`, `frame_layout` with
absolute rects), so a runtime needs no new code path, and adds a `layers` block
recording the stack it came from. Concretely its key set is the base manifest's
minus `rig`, plus `layers`; its single row is named after the composite and carries
`track: "composite"`, because what it plays is a combination and not one of the four
declarable row kinds. `fps` / `loop` come from the composite spec, defaulting to the
body element's state entry.

`sprite_sheet_alpha_report` is **`null`** in a composite manifest. That field names
the alpha/extraction report *of this sheet*, and a composite has none: it is stacked
from rows that were already extracted and keyed, so no alpha report was ever produced
for it. The key stays (key-set parity above) and states the absence instead of
pointing at a document of another kind — `layers/layers.report.json` is the bake's
provenance record for **every** composite (`kind: "sprite-gen-layers-report"`), not
this sheet's alpha report, so a consumer that resolved the field would open the wrong
document. The provenance pointer is `layers.report` inside the `layers` block.

A bake replaces the composites it produced and leaves everything else in `layers/`
alone — the same rule `variants/` follows, and the only one compatible with baking a
subset by name. `layers/layers.report.json` is the record of what the last bake
produced, so a sheet that is not in it is a leftover, not a current output.

**Publishing is one transaction.** §4's all-or-nothing rule covers declaration and
composition; the write is held to the same rule. Every sheet, every manifest and the
report are rendered in memory, staged as temp files beside their targets, and only
then renamed in — so an `ENOSPC` on the second composite's sheet leaves the first
one exactly as it was, and the report never names a sheet that failed to land.
(A power loss between two renames can still land a subset; what this removes is the
failure the process can observe — reporting success over a half-written publish.)

The base run's `manifest.json` gains, **only for a rig run**:

```jsonc
{
  "rig": {
    "profile": "humanoid_biped",
    "landmarks": {
      "down_walk": [                       // one entry per PLAY POSITION, same order and
        {"root": [312, 108], "crown": [312, 72]},   // length as frame_layout.rows.down_walk (B7)
        {"root": [408, 107], "crown": [408, 71]}
      ]
    }
  },
  "animation": {"rows": {"down_walk": {"track": "base"}}}
}
```

Landmarks in the manifest are **atlas-absolute integers**, matching the
`frame_layout` philosophy: a runtime samples rects and pivots, it never recovers
geometry from alpha. That is what lets the runtime combine tracks live instead of
consuming a pre-baked combination for every direction × action pair.

## 6. Validation contract

`sprite_gen.layers.validate_layer_request(request)` returns **every** violation, in a
deterministic order; `require_valid_layer_request` raises with all of them at once.
It is filesystem-free, so it runs before a run dir is touched. Rejections:

- unknown `rig.profile` — one error, whatever JSON type it is, and no landmark
  requirement is derived from it on any track (§3.1); malformed `rig` / `rig.landmarks`;
- a composite `fps` that is not a positive integer, or a `loop` that is not a boolean;
- a malformed element `revision` pin (it must be a non-empty list of segment strings);
- landmarks for an unknown state; a frame key that is not a decimal index; a frame
  index outside the pool; a pool frame with no landmarks at all;
- a required landmark missing on any frame — required per row by profile **and** track,
  so a `prop_effect` row is held to the `prop` set (§3.1); a landmark name that does not
  match the pattern; a landmark declared on part of a row only;
- a coordinate that is not a pair of integers, or lands outside the cell;
- an unknown `track` value;
- a composite name that collides with a request state, or does not match
  `^[a-z][a-z0-9_]{0,63}$`; an empty stack; an unknown composite key
  (`stack` / `fps` / `loop` is the whole vocabulary); an unknown stack-element key;
- zero or several body elements; a body element that is not `stack[0]`;
- `action_overlay` under a `full_body_override`;
- an element whose pool size is neither the body's nor 1;
- `from` / `to` not declared on every frame of the row it refers to;
- a stack with no `rig` at all.

## 7. Using it — the CLI and where it sits in the run

### 7.1 Declaring

`prepare` carries `rig`, `layers` and `states.<state>.track` from `--request` /
`--request-json` into `sprite-request.json`, and validates them (§6) before it
creates the run dir — a malformed rig fails with every violation at once instead of
scaffolding a run whose first bake is what reports it.

Of the layer vocabulary it carries exactly those three keys and nothing else.
`prepare` re-emits the whole request from a whitelist (B1), so any key outside that
list is dropped — and every drop is now named on stderr:

```text
[prepare] dropped top-level request key(s) ['notes']: prepare re-emits sprite-request.json from …
[prepare] dropped states.idle key(s) ['takes']: a state entry is rebuilt as ['frames', 'fps', 'loop', 'action', 'track']
```

That note is the contract, not a courtesy: `states.<state>.takes` is a documented
first-class key (`run-contract.md` §2) that has to be written into
`sprite-request.json` after `prepare` runs, and it went missing silently for as long
as the whitelist said nothing. Landmarks are usually declared the same way — after
curation, since coordinates are the *baked instance's* (§3.1) — by editing
`sprite-request.json` directly.

### 7.2 Baking

```bash
# every declared composite
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen compose-layers \
  --run-dir <target>/assets/generated/sprites/<character-id>

# just the ones named (the rest of layers/ is left alone)
$ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/sprite-gen compose-layers \
  --run-dir <run> --names down_walk_watering,down_idle_watering
```

The `sprite-gen compose-layers` subcommand, the `-m sprite_gen.compose_layers` module
form and the `scripts/compose_layers.py` wrapper are three launch forms of one declaration
(`compose_layers.add_arguments` / `.run`), and `compose_layers.bake(run_dir,
names=None)` is the library form the three share.

`--names` is a selection, so it is spelled exactly: an empty entry (`a,,b`, a trailing
comma) is a typo and never "all", and a name listed twice is refused the same way.
Both ways of passing over a repeat lie about the result — de-duplicating answers a
selection nobody asked for, and keeping the repeat writes one sheet while
`layers.report.json` counts two composites. `bake(names=[...])` enforces it too
(`compose_layers.require_distinct_names`), so the library form cannot reach a bake the
CLI would have refused.

Order in the run: it consumes the **curated** rows and the same primitives
`compose-atlas` does, so it belongs after curation, beside step 4 — a composite is
a combination of what the atlas bakes. It self-heals the derived frame cache first
(same rule as `compose-atlas`), takes the run-dir write lock, and prints a JSON
summary naming each sheet, its manifest, its frame/cell counts and clipped-pixel
total; the full record is `layers/layers.report.json`.

Exit codes are the pipeline's: `0` with the summary, or a non-zero failure listing
**every** violation at once, having written nothing (§4). A run that declares no rig
is refused by name rather than treated as an empty bake — "nothing to compose" and
"this is not a layer run" are different answers.

## 8. Out of scope

- **`unpack_atlas` / import runs** are untouched by this contract: an imported run
  has no rig, so it stays a non-layer run.
- **Runtime track combination.** The manifest carries atlas-absolute landmarks per
  play position (§5) precisely so a runtime *can* combine tracks live; doing so is
  the consumer's job, not this pipeline's.

## Related

- [`../SKILL.md`](../SKILL.md) — behavior contract (workflow, gates, runtime contract)
- [`run-contract.md`](run-contract.md) — stage table, run-dir tree, curation-view payload
- [`architecture.md`](architecture.md) — how the code realizes the contracts
- [`curation.md`](curation.md) — sidecar schema this contract deliberately does not extend
- [`recolor.md`](recolor.md) — the sibling-artifact-tree precedent (`variants/`)
