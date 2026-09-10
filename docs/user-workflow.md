# Image and sprite requests

This document owns the conversation flow. `sprite_gen/workflow/catalog.py` owns the two journeys and their choices; `workflow` derives questions and engine steps from it. Commands in this document assume `SPRITE_GEN_ROOT` is the absolute installed repository path.

## Start with the user's request

| Request | Choices |
|---|---|
| Make sprites | Base image provider (GPT or Grok), then motion method (Grok video or GPT image rows) |
| Make an image | Image provider (GPT or Grok) |

Inspect the request before asking anything. Pass choices already named by the user as explicit arguments. An existing sprite base image skips base-image generation and its provider question. An image request may still attach references to `gen --ref` for editing.

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen workflow --kind sprite
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen workflow --kind image
```

The command is read-only: it checks available credentials, reads defaults and returns JSON. `status` is `ready`, `needs-input` or `blocked`; exit 0 means ready and exit 2 means read the questions or blockers. It never generates images, launches a browser, changes authentication or saves defaults.

1. Check the returned access state and explain only what needs the user's attention.
2. Ask the missing generation choices in `questions`, using their labels rather than CLI identifiers. For sprites, explain that the first provider creates the base drawing, while the motion method animates it.
3. Run `workflow` again with the answers. Request choices override saved values without modifying them. When choices came from saved defaults, briefly announce them and do not ask again.
4. Gather only missing creative inputs: subject, style, requested motions and output needs. Lock the base before motion generation.
5. Only when the start stage returns `status: ready`, run the returned pipeline through the existing tools. A populated `pipeline` is a proposal, not permission to ignore unresolved questions or blockers. Always pass its explicit provider to `gen`/`gen-set`; do not rely on the low-level CLI's omitted-provider resolution. GPT rows use `codex` even when Grok made the base. No availability-driven provider switch is allowed in this workflow.
6. Complete the pipeline's QA and deliver the output files. Chroma removal, extraction, placement and export are automatic stages, not user choices. Raw images are evidence, not replacements for extracted sprite frames. Follow [atlas-workflow](atlas-workflow.md), [video-pipeline](video-pipeline.md) or [gen](gen.md), as returned by the guide.

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen workflow --kind sprite \
  --base-image /absolute/base.png --motion-method gpt-rows --confirmed-access codex
```

## Authentication and subscriptions

Login, subscription and quota are separate facts. Codex uses `codex login status`; Grok uses the existing video's credential reader, without rewriting its auth file. Neither probe proves the current media entitlement or remaining quota: those fields remain `unknown`. When access has not independently been established for the current account and request, ask the user once and pass `--confirmed-access codex` or `--confirmed-access grok`. An explicit account/access statement already in the conversation can answer this question; never manufacture confirmation from a saved provider preference. Known failed/missing credentials remain blocked even with confirmation. A successful generation is not a permanent entitlement cache.

For Grok video, the existing resolver can select `XAI_API_KEY` when configured. The guide names the separate API-credit billing and requires `--confirm-api-billing` from an explicit user choice before that route becomes ready. It never silently switches from subscription use to API credit. No keys, tokens, account IDs or subscription assertions are saved as preferences, and provider diagnostics do not echo raw credential output.

The documented login commands are [Codex CLI login status](https://developers.openai.com/codex/cli/reference) and [Grok CLI login](https://docs.x.ai/build/cli/reference). These are authentication contracts, not subscription-verification endpoints. Native token parsing and video credential priority stay owned by [video](video.md).

## Finish after delivering files

Call the finish stage with the actual generation choices, including the existing base when one was used, and an existing result path. Finish does not repeat authentication probes or generation.

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen workflow --kind image --stage finish \
  --image-provider codex --result /absolute/output.png
```

Ask whether to open the curation view only when `curation` is not already selected or saved. Apply `open` or `skip`, then call finish again with `--curation open|skip`. Use [curation](curation.md) to open the correct result: sprite run, video-derived frames, or a standalone image candidate set. Deliver files before opening the view; skipping it is a valid completed request. If the user edits their picks, export from the updated curation state.

When the profile has no saved defaults, finish then offers: "이번 선택을 다음 작업에도 기본으로 사용할까요?" Save only after an affirmative answer. Once a profile exists, ordinary one-off overrides do not prompt to replace it; changing defaults requires the user's explicit request. Creative content, prompts, paths and one-off actions are never part of defaults.

## One settings owner

`preferences.json` in `~/.config/sprite-gen/` owns the `sprite` and `image` profiles separately. `SPRITE_GEN_CONFIG_DIR` explicitly relocates that directory. A profile may be partial, for example saving only motion and curation when an existing base skipped image-provider selection. Missing fields prompt when needed. This storage is independent of a project or run; a run's numeric recipe and curation edits keep their existing owners.

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen defaults show
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen defaults save --kind sprite \
  --image-provider codex --motion-method grok-video --curation open \
  --expected-revision REVISION_FROM_SHOW
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen defaults clear --kind image \
  --expected-revision REVISION_FROM_SHOW
```

Use the exact `settings_revision` from the guide, or `revision` from show (`missing` for no file). Save merges only supplied fields in the selected profile; clear removes only that profile. Both are explicit mutations. Reads never create or rewrite settings. Corrupt or unknown settings fail with a named error instead of resetting silently.

Writes use the package's cross-platform exclusive file lock, compare the observed revision under that lock, then atomically replace the complete JSON file. Two writers with the same revision cannot overwrite each other: the first committed write wins; the second fails and must reread/reconfirm. A repeated save with the current revision and identical values leaves bytes and mtime unchanged. Atomic replacement lets read-only queries see a complete old or new snapshot without creating a lock file.
