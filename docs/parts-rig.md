# Parts Rig — catalog · gen · match · rig contract (SSoT)

> Status: **contract** (normative). This doc owns the parts-rig feature: how one
> character image is split into generated body-part layers, registered back onto
> the original by pixels, and exported as a JSON rig with a seek-safe HTML runtime.
> Code: `sprite_gen/parts/` (`catalog.py`, `parts_gen.py`, `match.py`, `rig.py`),
> pinned by `tests/parts/test_parts_contract.py` on synthetic shapes only.
> It is independent of the component-row atlas pipeline and of
> [`layer-tracks.md`](layer-tracks.md) (a bake-time row compositor); it shares only
> the landmark discipline — integer coordinates, declared or rejected, never inferred.

## 0. One sentence

A **catalog** names every part of one base image (draw order, box, pivot, group,
prompt); **gen** draws each part alone from the base + a crop of its box; **match**
puts each part back where the pixels say it belongs and refuses anything that does
not reproduce the base; **rig** turns the matched layers into `rig.json`, an HTML
fragment, and deterministic GSAP keys (lip-sync, blink, sway).

## 1. Why generate parts instead of cutting them

Cutting a flat illustration leaves holes: the face behind the bangs, the eyeball
under the eyelid, the neck under the choker do not exist in the pixels. Generating
each part alone from the same reference fills those occluded areas with the
character's own art, so a layer can move without exposing a gap. The price is that
a generated part can drift (shape, colour, scale) — which is exactly what the
`match` gate measures and refuses.

## 2. Catalog (`sprite-gen-parts-catalog`, version 1)

```jsonc
{
  "kind": "sprite-gen-parts-catalog", "version": 1,
  "character": "example",
  "base": "base.png",                         // relative to the catalog file; RGBA
  "canvas": {"width": 1024, "height": 1536},  // must equal the base image size
  "chroma_key": "green",                      // green | magenta — the part-generation key
  "groups": {"head": {"pivot": [512, 700]}},  // rotation/translation origins for the runtime
  "parts": [
    {"id": "hair_back", "z": 0, "bbox": [180, 40, 700, 1300], "pivot": [512, 400], "group": "head",
     "prompt": "the hair behind the head and shoulders"},
    {"id": "mouth", "z": 12, "bbox": [440, 720, 150, 70], "pivot": [515, 755], "group": "head",
     "prompt": "the mouth and lips only",
     "variants": {"default": "", "closed": "lips together", "half": "slightly open", "open": "open, teeth visible", "o": "rounded o shape"},
     "tolerance": 0.08}
  ]
}
```

Rules (validated by `catalog.validate_catalog`, every violation listed, in order):

- `id` matches `^[a-z][a-z0-9_]{0,31}$` and is unique; `z` is a unique integer (draw order, bottom first).
- `bbox` is `[x, y, w, h]` integers inside the canvas; `pivot` is `[x, y]` integers inside the bbox.
- `group` names a declared group or is omitted (`none`). Groups carry a `pivot`.
- `variants` is an object `variant -> prompt suffix` and must contain `default` (its suffix may be empty).
  The runtime recognises `mouth` variants `closed | half | open | o` and eyelid variants `open | half | closed` by name; anything else is a plain swap.
- `tolerance` (0..1 exclusive, default 0.06) is the part's colour gate in `match`; `agree_floor` (0..1], default 0.85) its agreement gate.
- Top-level `composite: {tolerance, coverage}` declares the whole-composite gate (defaults 0.05 / 0.97).
- Coordinates are integers, never floats or booleans. Nothing is inferred from pixels.

Adding a part or a variant is one catalog entry; `gen`, `match` and `rig` enumerate it.

## 3. `sprite-gen parts-gen`

`--catalog <json> --out-dir <dir> [--provider codex|grok] [--workers 6] [--only a,b__open]`

Per job (part × variant), in parallel: the base is flattened onto the chroma colour
and sent as reference 1; a crop of the bbox padded by 25% is reference 2; the prompt
asks for that part only, pixel-faithful, on a flat chroma key; the result is keyed to
RGBA by `gen.generate_image(transparent=True)`. A result with no transparent pixels
is recorded as a failure. Writes `<job>.png` (+ `.raw.png`) and `parts-gen.report.json`
(`ok`, `failed[]`, one record per job with alpha stats). Partial `--only` runs
merge existing job records; `ran` identifies jobs attempted this time and a retained
failure keeps `ok: false`. Exit 1 if any recorded job failed.

## 4. `sprite-gen parts-match` — the gate

`--catalog <json> --parts-dir <gen out> [--out-dir] [--composite-tolerance <override>]`

Top-most part first: the candidate is trimmed to its alpha box and **contain-fitted**
(its own aspect ratio, never stretched) into `bbox × scale` for scales `0.50 … 1.10`
in steps of 0.02, inside a window around the bbox (grown by ±8% and by the largest
scale). Pixels already claimed by a higher part are excluded (`free` mask), so a face
is compared only where the bangs do not cover it.

Two stages per part:

1. **FFT shortlist** (`register_fft.cost_map`) — for every scale, one masked-SSD cost map
   over every offset via FFT correlations (`SSD/(3·255²) − reward·W + miss`), plus the
   pure colour-error argmin. A few FFTs per scale regardless of offset count.
2. **Exact rescoring** — each shortlisted placement is scored with the agreement objective
   `−(agreeing − 2·disagreeing − 1.5·missed) / bbox area`, where a visible part pixel
   *agrees* when its mean RGB distance to the base is ≤ 0.12 and *missed* counts owned
   pixels left uncovered. An **owned** pixel is a base pixel inside the bbox, unclaimed,
   whose quantized colour (5 bits/channel) the part itself contains — a hair part owes
   red pixels, a shirt part does not. Shrinking loses agreement and gains misses;
   oversizing pays for every pixel spilled onto something else.

Reported per part: `score` (alpha-weighted mean RGB distance, gated by the part's
`tolerance`), `agree` (fraction agreeing, gated by the part's `agree_floor`, default
0.85), `scale`, `x`, `y`, `w`, `h`. Variants inherit their default's placement.

The defaults are stacked bottom-first into `composite.png`; `composite.score` (mean RGB
distance where both are opaque) must be ≤ `catalog.composite.tolerance` (default 0.05)
and `coverage` (base alpha reproduced) ≥ `catalog.composite.coverage` (default 0.97).
Real characters declare looser values than synthetic shapes (a generated hair mass
never reproduces strands pixel-for-pixel); the declaration is the gate, and loosening
it is a visible catalog edit, never a runtime fallback. Any part below its gate, any
missing candidate, or a failing composite makes `ok: false` and is named in `failed[]`.
Outputs `placed/<job>.png` (canvas-sized layers) and `parts-match.report.json`.
Measured 2026-09-08: a 14-part 1024×1536 character registers in ~25 s.

## 5. `sprite-gen parts-rig`

`--catalog <json> --match-dir <match out> [--out-dir] [--audio narration.mp3 | --duration s] [--fps 30] [--start 0] [--prefix rig] [--asset-prefix]`

Refuses a match report that is not `ok` or a missing placed layer. Writes:

- `rig.json` — canvas, groups (pivot + members), parts in z order with placement and
  `variants: {name: "placed/<job>.png"}`.
- `rig.html` — `<div id="<prefix>">` with one absolutely positioned canvas-sized `<img>`
  per variant (`id="<prefix>-<part>[__<variant>]"`, non-default `opacity:0`), grouped
  under wrappers with `data-rig-group="<group>"` and `transform-origin` at the
  group pivot. A group interrupted by another layer is split into consecutive z
  runs, each with a unique id and explicit z-index. All runs rotate together,
  preserving the composite draw order even across transformed stacking contexts.
- `rig-keys.json` / `rig-keys.js` — `window.__rigKeys(tl, start)` stamps GSAP `set`
  calls on a paused timeline: mouth variant per frame from the audio's RMS envelope
  (ffmpeg → 16 kHz mono → per-frame RMS using exact sample boundaries without cumulative rounding drift, normalized to the clip peak, thresholds
  0.12 / 0.38 / 0.70 → closed / half / open / o), fixed-cadence blinks (3.4 s + phase
  seeded from the clip length), and a slow sine sway on the `head` group (±1.2°).
  Same inputs → byte-identical outputs (pinned by test).

HyperFrames usage: paste `rig.html` inside a scene, load `rig-keys.js`, and call
`window.__rigKeys(tl, sceneStart)` before registering the timeline. Every key is a
`set`, so the runtime stays seek-safe.

## 6. What this feature does not do

- No Live2D / Spine runtime. `rig.json` is the source a later exporter could read.
- No mesh warping: parts translate/rotate as rigid layers; expression comes from variant swaps.
- No inference of boxes or pivots from pixels: the catalog is authored (a grid overlay
  of the base is the practical way to read coordinates).
- Public-repo hygiene: real character catalogs, bases and parts live with the character
  (never in this repository); fixtures here are synthetic shapes.
