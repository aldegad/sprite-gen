# `sprite-gen gen` — provider-backed image generation (engine SSoT)

Generation is a first-class engine module (`sprite_gen/gen/`), not an external
skill. One call = a prompt (+ optional reference images) → one **verified** PNG on
disk, with an optional deterministic transparent-chroma post-process. The general
`image-gen` skill is a thin shuttle over this command.

Providers (Gemini/OpenRouter/fal/BytePlus are intentionally **not** included):

| Provider | Backend | Auth | Output truth |
|---|---|---|---|
| `codex` | codex `image_gen` | ChatGPT OAuth | inline base64 in the session rollout jsonl, decoded deterministically |
| `grok` | grok Imagine `image_gen` / `image_edit` | xAI OAuth | file grok is told to write, verified by PNG magic |

## Provider and visible-worker topology

Provider selection and Studio worker selection are orthogonal:

| Layer | Canonical path | Responsibility |
|---|---|---|
| Generation request | `generate_sprite_image.py --provider grok` | Select the engine provider for one image request. |
| Provider adapter | `GrokProvider` | Build the prompt, choose Imagine `image_gen` or `image_edit`, and verify the requested PNG. |
| Headless provider process | `grok -p --sandbox workspace --always-approve` | Execute the xAI-authenticated Imagine tool call. |
| Image model tool | Imagine `image_gen` / `image_edit` | Generate a new image, or edit from references. |
| Visible Studio worker | `kuma spawn` | Create the visible worker surface that may invoke the generation request; it does not select or replace the provider. |

Therefore the direct Grok chain is `generate_sprite_image.py --provider grok`
→ `GrokProvider` → `grok -p --always-approve` → Imagine
`image_gen`/`image_edit`. `GrokProvider` owns the headless agent process lifecycle;
the chain does not require or route through a separate user-facing skill/task, and
it is not a second visible-worker topology.

## CLI

```bash
sprite-gen gen --provider codex|grok \
  --prompt "…"            # or --prompt-file PROMPT.txt
  --out DEST.png \
  [--ref REF.png ...]     # repeatable; grok routes refs through image_edit
  [--transparent [--chroma-key magenta|green]] \
  [--white-check CHECK.png] \
  [--aspect-ratio 1:1]    # grok only (1:1, 16:9, 9:16, 4:3, 3:4, auto)
  [--model ID] \
  [--report REPORT.json] \
  [--keep-session]        # codex: keep the rollout jsonl instead of deleting it
```

Backward-compatible wrapper: `python3 scripts/generate_sprite_image.py …` (same args).

- **Non-transparent**: the raw PNG (chroma background included) is copied to `--out`.
- **`--transparent`**: the raw is keyed to clean RGBA via the ported transparent
  contract (`sprite_gen/gen/chroma.py`). Generate on a solid `#FF00FF` (or `#00FF00`)
  background — pick the key by subject colour (magenta subjects → green key). Any
  transparent pixel that still carries non-zero RGB **fails loudly** (No Silent Fallback).
- The pre-chroma raw is preserved next to the destination as `<out>.raw.png` for audit.
- `--report` writes a `sprite-gen-image-report` JSON: provider, prompt, out/raw paths,
  `raw_bytes`, `elapsed_seconds`, `session_id` (codex), and the chroma stats.

## How each provider works

- **codex** — spawns a fresh `codex exec --json` in an empty sandbox
  (`--sandbox workspace-write`, `--add-dir ~/.codex/generated_images`,
  `--skip-git-repo-check`, no `--ephemeral`). A fresh session breaks OpenAI's prompt
  cache so repeat prompts don't drag in a prior image. The session id comes from the
  `thread.started` event (older codex: a `session id:` text line — both supported); the
  inline base64 is decoded from the rollout jsonl (`image_generation_call` /
  `image_generation_end` records — both supported). The model-reported path is never
  trusted. The rollout jsonl (which holds the ~1–1.5 MB inline image) is deleted after
  extraction unless `--keep-session`.
- **grok** — runs `grok -p … --sandbox workspace --always-approve` (media/shell must be
  auto-approved; plain acceptEdits blocks tool execution and returns an empty answer).
  grok is instructed to write the final PNG to an exact absolute path; we then verify
  that file's PNG magic. No `--effort` is passed (the grok-build image model 400s on
  `reasoningEffort`). With `--ref`, grok uses `image_edit` on the reference instead of
  `image_gen`.

## Sprite-row usage

In the component-row pipeline (SKILL.md §2) generate each state row with
`--provider codex` (or `grok`) using `prompts/<state>.txt`, writing `raw/<state>.png`.
Keep the request chroma key on the background; frame extraction removes it downstream.
The correction loop (`sprite-gen correction-loop --provider-command …`) can drive this
`gen` command as its regeneration step so inspect → score → hint → regenerate closes
against a real provider.

## Speed

On a 4-frame idle mushroom row (see `docs/reports/perfectpixel-c-gen/`), grok generated
in ~18.4 s vs codex ~39.0 s (~2.1× faster). codex adhered better to negative constraints
("no grid lines"); grok added faint cell dividers. Pick per need: grok for speed, codex
for tighter prompt adherence.
