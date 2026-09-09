# `sprite-gen gen` — provider-backed image generation (engine SSoT)

> Owns: `sprite-gen gen` / `gen-set`: providers, default resolution, transparency strategy per provider, row usage · Index: [docs/README.md](README.md)

Generation is a first-class engine module (`sprite_gen/gen/`), not an external
skill. One call = a prompt (+ optional reference images) → one **verified** PNG on
disk, with an optional transparent output whose strategy is decided per provider
(native alpha or deterministic chroma keying). The general `image-gen` skill is a
thin shuttle over this command.

Providers (Gemini/OpenRouter/fal/BytePlus are intentionally **not** included):

| Provider | Backend | Auth | Output truth | Transparency strategy |
|---|---|---|---|---|
| `codex` | codex `image_gen` | ChatGPT OAuth | inline base64 in the session rollout jsonl, decoded deterministically | **`native`** — image_gen returns a real alpha channel when asked (measured, then published) |
| `grok` | grok Imagine `image_gen` / `image_edit` | xAI OAuth | file grok is told to write, verified by PNG magic | `chroma` — Imagine returns JPEG only; generate on a key and matte it out |

The strategy is declared **once**, on the adapter (`Provider.transparency`), and is
the only place that says what a backend can do. See
[Transparent output](#transparent-output--strategy-per-provider).

## Default provider selection

`--provider` is **optional**. When omitted, the backend is resolved by a fixed
precedence (maintainer 확정 2026-07-17):

1. **`SPRITE_GEN_DEFAULT_PROVIDER`** env var (`codex` or `grok`) — the user override.
   An unknown value fails loud.
2. **`codex`** — the hard default (GPT `image_gen`).

If the resolved default is `codex` but codex is unavailable here (CLI not on PATH,
or `codex login status` reports not-logged-in), the resolution **falls back to
`grok` — observably, never silently**: a stderr notice is printed and the report
JSON records `provider_fallback` (`from`/`to`/`reason`/`default_source`). The
grok default (`SPRITE_GEN_DEFAULT_PROVIDER=grok`) has no reverse fallback — a down
grok fails loud at generation time.

An **explicit `--provider`** is always honored verbatim — it is never overridden by
the availability fallback. An explicitly named provider that is down fails loud
(the provider adapter raises), preserving the operator's stated intent.

Every generation reports which backend actually ran: `provider` (the real
backend), `provider_resolved_from` (`explicit` / `SPRITE_GEN_DEFAULT_PROVIDER` /
`hard-default` / `fallback-from-codex`), and `provider_fallback` when a fallback
happened.

## Provider and visible-worker topology

Provider selection and user-facing worker/agent creation are orthogonal:

| Layer | Canonical path | Responsibility |
|---|---|---|
| Generation request | `generate_sprite_image.py --provider grok` | Select the engine provider for one image request. |
| Provider adapter | `GrokProvider` | Build the prompt, choose Imagine `image_gen` or `image_edit`, and verify the requested PNG. |
| Headless provider process | `grok -p --sandbox workspace --always-approve` | Execute the xAI-authenticated Imagine tool call. |
| Image model tool | Imagine `image_gen` / `image_edit` | Generate a new image, or edit from references. |
| Visible worker/agent | (caller's orchestrator) | Creating a user-facing worker surface that may invoke the generation request is the orchestrator's own concern; it does not select or replace the provider. |

Therefore the direct Grok chain is `generate_sprite_image.py --provider grok`
→ `GrokProvider` → `grok -p --always-approve` → Imagine
`image_gen`/`image_edit`. `GrokProvider` owns the headless agent process lifecycle;
the chain does not require or route through a separate user-facing skill/task, and
it is not a second visible-worker topology.

## CLI

```bash
sprite-gen gen \
  [--provider codex|grok] # optional; default = SPRITE_GEN_DEFAULT_PROVIDER env → codex (observable grok fallback if codex is down)
  --prompt "…"            # or --prompt-file PROMPT.txt
  --out DEST.png \
  [--ref REF.png ...]     # repeatable; grok routes refs through image_edit
  [--transparent [--alpha-mode auto|native|chroma] [--chroma-key magenta|green]] \
  [--white-check CHECK.png] \
  [--aspect-ratio 1:1]    # grok only (1:1, 16:9, 9:16, 4:3, 3:4, auto)
  [--model ID] \
  [--report REPORT.json] \
  [--keep-session]        # codex: keep the rollout jsonl instead of deleting it
```

Backward-compatible wrapper: `$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/generate_sprite_image.py …` (same args).

- **Non-transparent**: the raw PNG (background included) is copied to `--out`.
- **`--transparent`**: publishes a clean RGBA PNG using the provider's transparency
  strategy (below). Either way a result with no transparent area, or any transparent
  pixel that still carries non-zero RGB, **fails loudly before the output or success
  report is published** (No Silent Fallback).
- The pre-process raw is preserved next to the destination as `<out>.raw.png` for audit.
- `--report` writes a `sprite-gen-image-report` JSON: provider, prompt, out/raw paths,
  `raw_bytes`, `elapsed_seconds`, `session_id` (codex), an `alpha` block
  (`strategy` + the measured stats), the `chroma` stats when chroma keying ran, and the
  provider-resolution fields (`provider_resolved_from`, and `provider_fallback` when a
  codex→grok default fallback occurred).

## Transparent output — strategy per provider

`--transparent` does not mean "chroma key" any more. Each adapter declares the one
strategy it can execute, and `--alpha-mode auto` (the default) follows it:

| Strategy | Who | What happens | Refused when |
|---|---|---|---|
| `native` | `codex` (**first choice**, 2026-09-08) | The transport prompt asks image_gen for a genuinely transparent background (the bundled `imagegen` skill honours "transparent background" and keeps the generated alpha; codex reports `transparentBackground: true` on the completed item). The decoded PNG's alpha is **measured**: no alpha band or `alpha_zero_pct: 0.0` refuses to publish, RGB under alpha 0 is scrubbed, partial alpha (1–254) is left as produced and reported as `partial_alpha_pct`. | The model drew a checkerboard / flat background (RGB image) — nothing can recover alpha from that, so the run fails instead of silently keying. |
| `chroma` | `grok` (only option), `codex` with `--alpha-mode chroma` | Generate on a `#FF00FF` (or `#00FF00`) background — pick the key by subject colour (magenta subjects → green key) — and matte it out through the frame extractor's canonical YCbCr matte (`remove_chroma_background_ycbcr`). Gradients and texture within that chroma family are supported. | `alpha_zero_pct: 0.0` after keying, or stale RGB under alpha 0. |

- **`auto` steps down to `chroma` when `--ref` is attached**, even on codex. Measured
  2026-09-08 (plan `sprite-gen/parts-rig`): codex `image_gen` with reference images
  returned real alpha in 1/6 runs and drew a checkerboard (RGB) in 5/6, while the same
  prompts on a `#00FF00` key + chroma keying succeeded 6/6. The decision is made before
  the model runs, printed to stderr, and recorded as `alpha.strategy_source:
  "refs-attached"` (`provider-default` / `explicit` otherwise). `--alpha-mode native`
  still forces native alpha with refs — and fails loud on an RGB result. So a ref run's
  prompt must carry the chroma key, exactly as the sprite-row pipeline already does.
- `--alpha-mode chroma` on codex is for prompts that already carry a key background
  (the sprite-row pipeline today): the native request is **not** added to the prompt
  and the raw is keyed like a grok run.
- `--alpha-mode native` on a `chroma`-only provider **fails loud before any model
  call** — a strategy the backend cannot execute is not a fallback candidate, and
  native → chroma never happens silently either (the prompt shapes are different).
- Why grok is chroma-only: Grok Imagine Image 2.0 returns `image/jpeg` from both the
  `/v1/images/*` API and the CLI `image_gen`/`image_edit` tools, and the official
  docs expose no background parameter — its "background removal" is a consumer-app
  tool (4/4 drawn checkerboards on 2026-09-08). The declaration lives in
  `sprite_gen/gen/grok_provider.py` and flips only with a new measurement.
- Measured codex output (2026-09-08, codex 0.153.4): `alpha_zero_pct ≈ 62`, body alpha
  ≈ 253 (so `partial_alpha_pct` is most of the subject), a ~1 px light fringe on a
  magenta composite. Downstream extraction treats `alpha ≤ 16` as transparent, so
  this is usable as-is; alpha snapping is deliberately not applied here.

## How each provider works

- **codex** — spawns a fresh `codex exec --json` in an empty sandbox
  (`--sandbox workspace-write`, `--add-dir <Codex state root>/generated_images`,
  `--skip-git-repo-check`, no `--ephemeral`). A fresh session breaks OpenAI's prompt
  cache so repeat prompts don't drag in a prior image. The session id comes from the
  `thread.started` event (older codex: a `session id:` text line — both supported); the
  inline base64 is decoded from the rollout jsonl (`image_generation_call` /
  `image_generation_end` records — both supported). The model-reported path is never
  trusted. The rollout jsonl (which holds the ~1–1.5 MB inline image) is deleted after
  extraction unless `--keep-session`.
  The adapter and child process share one Codex state root: when `CODEX_HOME` is set they use only that directory; when it is unset they use Codex's `~/.codex` default.
  Rollouts are selected by an exact session-id filename suffix.
  Missing, duplicate, or pre-existing stale matches fail rather than falling back to another root or choosing by modification time.
  The transport prompt names the skill with codex's official `$imagegen` mention, which is how a codex skill is invoked explicitly. The adapter owns that trigger alone; the caller's sprite-request prompt is passed through verbatim.

### When codex produces no image at all

A run that reaches a rollout but finds zero `image_generation_call` /
`image_generation_end` records means the built-in `image_gen` tool was never
offered to the session, not that the model declined to use it. Built-in image
generation is a **capability of the account behind the active Codex state root**.

A session that is not offered the tool cannot be talked into it. The `$imagegen`
mention names the skill, it does not create the tool; the model choice does not
change it; and no `config.toml` feature toggle grants it. The remedy is to point
`CODEX_HOME` at a Codex state root whose account provides image generation
(`codex login status`), or to use `--provider grok`. The adapter fails loudly with
exactly that, rather than falling back on its own.
- **grok** — runs `grok -p … --sandbox workspace --always-approve` (media/shell must be
  auto-approved; plain acceptEdits blocks tool execution and returns an empty answer).
  grok is instructed to write the final PNG to an exact absolute path; we then verify
  that file's PNG magic. No `--effort` is passed (the grok-build image model 400s on
  `reasoningEffort`). With `--ref`, grok uses `image_edit` on the reference instead of
  `image_gen`.

## Sprite-row usage

In the atlas pipeline (SKILL.md §2) the rows of a prepared run are generated by
`sprite-gen gen-set --run-dir <run>`: every non-mirrored state, `--concurrency` 4 at a
time, each row with the identity ref the run declares (`base-source.*`, or the accepted
direction anchor for a direction-contract action row — `sprite_gen.curate.anchor` owns
that choice) plus its layout guide, into `raw/<state>.png`. Each row gets
`reports/gen-set/<state>.json`, the batch writes `reports/gen-set/table.md` and
`set.report.json`, existing rows are reused unless `--force`, a direction run generates
its anchors before its rows and stops when an anchor failed, and the exit code is
non-zero when any row failed. `--provider` is honoured verbatim; unspecified, it
resolves exactly as `gen` does (above), and any codex→grok availability failover is
recorded per row.

One row by hand is the same call `gen-set` makes: `--provider codex` (or `grok`) with
`prompts/<state>.txt`, writing `raw/<state>.png`.
The row prompts still carry the request chroma key on the background and frame
extraction removes it downstream — rows are generated **without** `--transparent`, so
the native strategy does not apply to them yet (moving rows to native alpha is a
separate, measured change).
The correction loop (`sprite-gen correction-loop --provider-command …`) can drive this
`gen` command as its regeneration step so inspect → score → hint → regenerate closes
against a real provider.

## Speed

On a 4-frame idle mushroom row, grok generated
in ~18.4 s vs codex ~39.0 s (~2.1× faster). codex adhered better to negative constraints
("no grid lines"); grok added faint cell dividers. Pick per need: grok for speed, codex
for tighter prompt adherence.

## Related

- [docs/README.md](README.md) — documentation index
