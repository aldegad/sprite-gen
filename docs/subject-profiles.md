# Subject profiles — character vs effect, and the sparse-frame floor

The sparse-frame floor (`--min-used-pixels`) exists to catch **empty frames and
extraction debris**. How many opaque pixels count as debris is a property of
*what the run draws*, not of arithmetic: a character fills its cell; a bone
shard, an orb, a spell spark legitimately does not. So the floor is declared,
not computed:

```jsonc
// sprite-request.json
{ "subject": "effect" }   // or omit the field entirely = character
```

```bash
sprite-gen prepare --subject effect ...   # writes the field for you
```

| profile | floor (opaque px / frame) | tuned for |
|---|---|---|
| `character` (field absent — legacy default, byte-identical) | 400 | 256px-cell full-body figures |
| `effect` | 48 | small projectiles, sparks, glyphs on 64px cells |

An explicit `--min-used-pixels` always beats the profile. Profile-derived
floors are **not** stamped into `extract_args` (frames are a derived cache of
raw + request + engine, so changing `subject` later re-resolves on heal);
explicit flags are stamped and reproduce exactly.

## Why 48 — measured, not chosen

Real incident (2026-07-31, a Flame game using sprite-gen for all art): seven
64px-cell effect runs — bone shard, orb, talisman, curse, souls, spike, jijeon
— generated correctly, extracted the exact requested frame counts at **70–208
opaque px per frame**, and were then rejected wholesale by the character floor:
400px on a 64px cell demands 9.8% coverage, a solid 20×20 block. The runs sat
abandoned for months while the game shipped procedural placeholder shapes. The
failure text ("empty or too sparse (91 pixels)") never said it was a tunable
floor, so the failures read as generation failures.

Validation battery (synthetic strips at controlled densities, 64px cells, both
profiles, no CLI flags):

| opaque px/frame | character profile | effect profile |
|---|---|---|
| 9 · 25 · 36 (debris zone) | FAIL | **FAIL** — the guard is scoped, not neutered |
| 64 · 81 · 144 · 289 (production effect band) | FAIL | PASS |
| 784+ | PASS | PASS |
| real fx runs (70–208 px, 7 runs) | FAIL (the incident) | PASS with only the declaration |
| dense character control (7921 px @256) | PASS | — |
| sparse character control (81 px @256) | FAIL (correct: debris) | — |

48 sits below the smallest legitimate production effect frame observed (70px)
and above typical chroma debris (< 20px).

## Best practices

- **Declare the subject in the request, not in shell flags.** The request is
  the run's numeric SSoT; a floor override buried in a wrapper script is
  invisible to heal, to the curation view, and to the next person. The
  profile travels with the run.
- **Keep effects on small cells (64px) with a generous safe margin.** The
  incident runs did this right — small cell keeps the atlas tight and the
  runtime scale honest.
- **Read `frames-manifest.json` the moment `ok` is false.** It names every
  rejected frame with its measured pixel count. All seven abandoned runs
  contained a complete, correct diagnosis nobody read: counts matched the
  request exactly, only the floor verdict differed.
- **Reserve `--min-used-pixels` for genuine outliers** (a deliberately tiny
  spark below 48px), and let it be stamped into `extract_args` so heal
  reproduces the exception exactly.
- **Watch the auto chroma key against wispy effects.** The fx runs keyed on
  auto-selected cyan with healthy subject distance (≥ 215) — that is the
  pattern to keep: solid-core effects with hues far from the key. A
  translucent glow near the key hue would unmix into the background and land
  in the debris zone for real.

## Worst practices (each one observed)

- **Abandoning an `ok: false` run without reading the diagnosis.** Seven runs,
  eight months, procedural fallbacks in production — and the fix was one
  declaration. Failure evidence is written for reading.
- **Validating effects with character expectations.** Any absolute floor tuned
  on one subject kind silently mislabels another. If you find yourself
  lowering the floor for every effect run by hand, the run is mislabeled —
  declare the subject instead.
- **Global floor overrides in generation scripts.** `--min-used-pixels 50` on
  every extract call "fixes" effects and simultaneously disables the debris
  guard for every character run that script touches.
- **Treating the extractor's rejection as a generation failure.** Before
  rerolling (or giving up on) a row whose frames were *found in the right
  count*, compare the measured pixel counts against the floor — a correct
  count with sub-floor pixels is a profile problem, not an art problem.
