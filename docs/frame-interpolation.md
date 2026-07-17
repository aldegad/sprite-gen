# AI Frame Interpolation (in-betweens)

`sprite_gen/interpolate.py` (`scripts/interpolate_frames.py`) makes an **in-between
frame** between two frames of one state — a half-closed eyelid between open/closed
idle frames, a mid-swing arm between windup and strike.

## Doctrine position

"AI touches raw generation only" applies here too: interpolation is a raw-stage AI
step. The output is never a final frame — it is recorded as a **take**
(`raw/<state>.takes/<label>.png` + `states.<state>.takes` in `sprite-request.json`),
and logical frames are always baked by the deterministic extraction path (chroma
removal → components → grid snap → shared palette). The curator orders the new frame
in the play sequence like any other take frame.

## Backend: generative (codex / grok) — flow-based VFI retired

The backend is the engine's own generation layer (`sprite_gen.gen`): the two aligned
frames go in as reference images and the model **draws** the in-between pose.
Default provider `codex` (GPT `image_gen`); `--provider grok` selects xAI Imagine.

Flow-based VFI (RIFE) was retired (Soohong, 2026-07-17). Rationale, measured on a
real 3-way comparison (founder down_action arm swing, sheet `tween-3way-compare.png`
in the solvell tray): VFI interpolates *motion* photometrically, so appearance
changes come out as a blurry cross-fade; generative in-betweens drew the mid-pose in
clean discrete pixels. codex preserved identity best; grok drifted; RIFE smeared.

## Auth prerequisites (applies to the curator Tween button too)

Generation always runs on the **server machine's provider CLI** — the browser/webview
never sees or carries any credential. The Tween button POSTs to the local curation
server, which spawns `codex`/`grok` as a subprocess; those CLIs use their own
machine-local OAuth sessions.

- Required on the machine running the curation server / CLI:
  - `codex` CLI installed and logged in (ChatGPT OAuth) for `--provider codex`
  - `grok` CLI installed and logged in (xAI OAuth) for `--provider grok`
- Setup and provider topology: [`docs/gen.md`](gen.md) (the SKILL's
  `required_bins` names both CLIs).
- Not authenticated / CLI missing → the provider fails loudly and the message
  surfaces as-is (CLI stderr / curator status bar). No silent fallback between
  providers.

## Usage

```bash
python3 scripts/interpolate_frames.py \
  --run-dir <run> --state down_idle --between 1 2 \
  [--provider codex|grok] [--t 0.5] [--label blink_mid] [--extract]
```

- `--between A B` — frame indices on the state's primary strip (component order).
- `--provider` — generation backend; default `codex` (3-way comparison winner).
- `--t` — interpolation time inside (0, 1); default 0.5. Non-midpoint values are
  expressed in the prompt ("closer to A/B").
- `--label` — take label; default `tween_<A>_<B>_t<t>`. Re-running the same label
  overwrites the same take file and does not duplicate the `takes` entry (idempotent).
- `--extract` — run a **full-batch** re-extraction afterwards. Partial extraction is
  deliberately not offered: the run-wide shared palette is built per extraction batch
  (CHANGELOG v1.56.17 known limitation), so a single-state extract would shade that row
  from a different palette.

In the curator, the row-header **Tween** button does the same thing: open the
popover, click two cards to pick the pair (blue border), choose GPT/Grok, Generate.
While the follow-up full-batch re-extraction runs, `/api/run` reports busy (503)
instead of a manifest-consistency error.

## Pipeline internals

1. Chroma-remove the primary strip, extract components, tighten to solid bbox.
2. **Registration** (`register_row_frames`) puts both frames on one canvas aligned by
   upper-body alpha overlap — static pixels coincide, so the model reads the pair as
   "same drawing, only the moving part differs".
3. Canvas pads to /32 on the request chroma color; the prompt requires the same flat
   chroma background so the extractor keys it out later.
4. `tween_prompt` composes the prompt deterministically from request truth (character
   description, chroma color, t) and `sprite_gen.gen.generate_image` runs the provider
   with both frames attached as refs.
5. The generated mid frame is saved as the take raw; extraction re-derives the state
   row (the frame appears labeled `<label>#0`) and pixel-perfect quantization snaps it
   onto the logical grid.

Quality note: judge the result **after** pixel-perfect re-quantization, and touch up
stray pixels with the curator's pixel editor if needed. Identity drift is a reroll
(regenerate the take), not a local repair.

## Testing

`tests/test_frame_interpolation.py` covers the plumbing with an injected stub
interpolator (alignment canvas contract, prompt composition, take write, request
idempotency, loud rejections including unknown providers). Real provider generation
is exercised manually, not in CI.
