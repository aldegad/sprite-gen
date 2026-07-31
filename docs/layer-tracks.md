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
> and pinned by `tests/test_layer_contract.py`.

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
  (`request.get("fit")`, `request["cell"]`, …); `runio.load_request` only migrates
  retired keys. **Consequence:** layer validation lives in exactly one module
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
- The reserved column is a naming recommendation, not a restriction: any name
  matching `^[a-z][a-z0-9_]{0,31}$` is accepted. Whatever is used must be declared on
  **every** frame of that row — a landmark that exists on some frames only is
  rejected, because a composite would otherwise skip frames.
- `rig.landmarks.<state>` must cover the row's whole frame pool, `0 .. state_frame_total-1`,
  takes included (B10).
- Coordinates are integer cell coordinates of that state's frames, inside the cell.

### 3.2 Tracks

An undeclared row is `base` — the explicit default, so a legacy run reads as an
all-`base` run rather than as an unknown kind.

| Track | Draws | Requires | Forbids |
|---|---|---|---|
| `base` | the whole body | — | sharing a stack with another body element |
| `action_overlay` | a partial-body action drawn over the base | a `base` in the stack | coexisting with `full_body_override` |
| `prop_effect` | a prop or effect placed at a socket landmark | a body element; its `to` landmark declared on that body row | — |
| `full_body_override` | the whole body, replacing the base | being the only body element | `base` and `action_overlay` in the same stack |

`full_body_override` is the escape hatch for a motion that cannot be decomposed —
a two-handed swing owns the whole body, so the contract makes that explicit
instead of letting an overlay half-cover a base that is still walking.

### 3.3 Stack elements

| Key | Default | Meaning |
|---|---|---|
| `state` | required | the row this element draws |
| `from` | `"root"` | the element's own landmark |
| `to` | `"root"` | the landmark **on the body element** that `from` is placed onto |
| `mask` | none | run-relative path to an alpha mask (§4) |
| `allow_clip` | `false` | permit opaque pixels to fall outside the cell |

The body element is `stack[0]`; every other element is aligned onto it. A stack has
exactly one body element. A non-body element's row has either the same pool size as
the body row, or exactly one frame (a held pose).

## 4. Composition contract (deterministic)

The composer (implemented in the next step) bakes output frame `i` as:

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

Failure diagnostics the composer owns (everything that needs the run dir):

- a declared source frame missing from the published generation;
- a mask file missing, unreadable, or not the cell's size;
- an opaque pixel translated outside the cell while `allow_clip` is false — reported
  with the clipped pixel count and failed, never silently cropped;
- a declared landmark whose row was regenerated: the bake report records each source
  row's `state_revision`, and an element may declare `revision` to be checked, in
  which case a mismatch fails loud (same mechanism as `curation.anchors`).

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
recording the stack it came from.

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

- unknown `rig.profile`; malformed `rig` / `rig.landmarks`;
- landmarks for an unknown state; a frame key that is not a decimal index; a frame
  index outside the pool; a pool frame with no landmarks at all;
- a required landmark missing on any frame; a landmark name that does not match the
  pattern; a landmark declared on part of a row only;
- a coordinate that is not a pair of integers, or lands outside the cell;
- an unknown `track` value;
- a composite name that collides with a request state, or does not match
  `^[a-z][a-z0-9_]{0,63}$`; an empty stack; an unknown stack-element key;
- zero or several body elements; a body element that is not `stack[0]`;
- `action_overlay` under a `full_body_override`;
- an element whose pool size is neither the body's nor 1;
- `from` / `to` not declared on every frame of the row it refers to;
- a stack with no `rig` at all.

## 7. Open work carried by the later steps

- **Carry the layer keys through `prepare`** (B1): add `rig` and `layers` to the
  emitted request and `track` to `normalize_states`, and make the whitelist drop
  observable — a stderr note naming dropped top-level and per-state keys, so the
  `takes` class of silent loss stops with this feature instead of gaining a third
  instance. `test_prepare_does_not_carry_layer_keys_yet` is the canary that fails
  when this lands.
- **Composer + run-dir diagnostics** (§4) and the `layers/` writer (§5).
- **CLI surface** for the bake, plus the double-bake SHA-256 determinism regression.
- **`unpack_atlas` / import runs** are untouched by this contract for now: an
  imported run has no rig, so it stays a non-layer run.

## Related

- [`../SKILL.md`](../SKILL.md) — behavior contract (workflow, gates, runtime contract)
- [`run-contract.md`](run-contract.md) — stage table, run-dir tree, curation-view payload
- [`architecture.md`](architecture.md) — how the code realizes the contracts
- [`curation.md`](curation.md) — sidecar schema this contract deliberately does not extend
- [`recolor.md`](recolor.md) — the sibling-artifact-tree precedent (`variants/`)
