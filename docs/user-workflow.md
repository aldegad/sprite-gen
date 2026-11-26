# Image and sprite requests

This document owns the conversation flow. `sprite_gen/workflow/catalog.py` owns the two journeys and their choices; `workflow` derives questions and engine steps from it.

## Start with the user's request

| Request | Choices |
|---|---|
| Make sprites | Base image provider (GPT or Grok), then motion method (Grok video or GPT image rows) |
| Make an image | Image provider (GPT or Grok) |

Inspect the request before asking anything. Pass choices already named by the user as explicit arguments. An existing sprite base image skips base-image generation and its provider question. An image request may still attach references to `gen --ref` for editing.

The command is read-only: it checks available credentials, reads defaults and returns JSON. `status` is `ready`, `needs-input` or `blocked`; exit 0 means ready and exit 2 means read the questions or blockers.

1. Check the returned access state and explain only what needs the user's attention.
2. Ask the missing generation choices in `questions`, using their labels rather than CLI identifiers. For sprites, explain that the first provider creates the base drawing, while the motion method animates it. Until it is answered, `questions` also holds `codex_unavailable` ("지피티(codex)를 쓸 수 없을 때 — 로그인이 없거나 사용량을 다 썼을 때 — 어떻게 할까요?": stop and ask, Grok, or the OpenAI API billed per call). It is asked once: save the answer at once with `defaults save --codex-unavailable <answer> --expected-revision <settings_revision>` (the question's `save: general`, no `--kind`), and it is not asked again.
3. Run `workflow` again with the answers. Request choices override saved values without modifying them. When choices came from saved defaults, briefly announce them and do not ask again.
4. Gather only missing creative inputs: subject, style, requested motions and output needs. Lock the base before motion generation.
5. Only when the start stage returns `status: ready`, run the returned pipeline through the existing tools. A populated `pipeline` is a proposal, not permission to ignore unresolved questions or blockers. Always pass its explicit provider to `gen`/`gen-set`; do not rely on the low-level CLI's omitted-provider resolution. No availability-driven provider switch is allowed in this workflow.
6. Complete the pipeline's QA and deliver the output files. Raw images are evidence, not replacements for extracted sprite frames. Follow [atlas-workflow](atlas-workflow.md), [video-pipeline](video-pipeline.md) or [gen](gen.md), as returned by the guide.

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen workflow --kind sprite \
  --base-image /absolute/base.png --motion-method gpt-rows --confirmed-access codex
```

## Authentication and subscriptions

Login, subscription and quota are separate facts. Codex uses `codex login status`; Grok uses the shared image/video credential reader, without rewriting its auth file. Neither probe proves the current media entitlement or remaining quota: those fields remain `unknown`. When access has not independently been established for the current account and request, ask the user once and pass `--confirmed-access codex` or `--confirmed-access grok`. An explicit account/access statement already in the conversation can answer this question; never manufacture confirmation from a saved provider preference. Known failed/missing credentials remain blocked even with confirmation. A successful generation is not a permanent entitlement cache.

The guide names the separate API-credit billing and requires `--confirm-api-billing` from an explicit user choice before that route becomes ready.

Native token parsing and shared credential priority stay owned by [video](video.md).

## Finish after delivering files

Call the finish stage with the actual generation choices, including the existing base when one was used, and an existing result path. Finish does not repeat authentication probes or generation.

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen workflow --kind image --stage finish \
  --image-provider codex --result /absolute/output.png
```

Ask whether to open the curation view only when `curation` is not already selected or saved. Apply `open` or `skip`, then call finish again with `--curation open|skip`. Use [curation](curation.md) to open the correct result: sprite run, video-derived frames, or a standalone image candidate set. Deliver files before opening the view; skipping it is a valid completed request. If the user edits their picks, export from the updated curation state.

When the profile has no saved defaults, finish then offers: "이번 선택을 다음 작업에도 기본으로 사용할까요?" Save only after an affirmative answer. Once a profile exists, ordinary one-off overrides do not prompt to replace it; changing defaults requires the user's explicit request. Creative content, prompts, paths and one-off actions are never part of defaults.

## One settings owner

`preferences.json` in `~/.config/sprite-gen/` owns the `sprite` and `image` profiles separately, and beside them the `general` settings every profile shares: `codex_unavailable` (`ask`, `grok` or `openai`; what a call that names no provider does when codex cannot run — the rule is [gen](gen.md#when-codex-cannot-run--ask-or-what-you-chose)'s), and `codex_model` and `codex_effort` (the model and effort a codex generation runs on — [gen](gen.md#the-codex-driver-model)'s). `defaults show`, `save` and `clear` also print `codex_driver`, the driver in effect with these settings. `SPRITE_GEN_CONFIG_DIR` explicitly relocates that directory. A profile may be partial, for example saving only motion and curation when an existing base skipped image-provider selection. Missing fields prompt when needed. This storage is independent of a project or run; a run's numeric recipe and curation edits keep their existing owners.

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen defaults show
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen defaults save --kind sprite \
  --image-provider codex --motion-method grok-video --curation open \
  --expected-revision REVISION_FROM_SHOW
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen defaults clear --kind image \
  --expected-revision REVISION_FROM_SHOW
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen defaults save --codex-unavailable ask \
  --expected-revision REVISION_FROM_SHOW
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen defaults save --codex-model gpt-6.1-terra --codex-effort low \
  --expected-revision REVISION_FROM_SHOW
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen defaults clear --setting codex_model \
  --expected-revision REVISION_FROM_SHOW
```

Use the exact `settings_revision` from the guide, or `revision` from show (`missing` for no file).

Two writers with the same revision cannot overwrite each other: the first committed write wins; the second fails and must reread/reconfirm.
