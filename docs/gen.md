# `sprite-gen gen` — provider-backed image generation (engine SSoT)

> Owns: `sprite-gen gen` / `gen-set`: providers, default resolution and what a default codex that cannot run turns into, transparency strategy per provider, the billed knobs (`--quality`, `--resolution`), when a refused provider request is sent again (every direct provider call), row usage · Index: [docs/README.md](README.md)

Generation is a first-class engine module (`sprite_gen/gen/`), not an external
skill. One call = a prompt (+ optional reference images) → one **verified** PNG on
disk, with an optional transparent output whose strategy is decided per provider
(native alpha or deterministic chroma keying). The general `image-gen` skill is a
thin shuttle over this command.

Providers are the backends the maintainer uses day to day. Others (Gemini,
OpenRouter, fal, BytePlus, …) are absent because nobody here uses them, not because
of a design objection; a contributed provider is welcome when its author will keep
maintaining it ([#37](https://github.com/aldegad/sprite-gen/issues/37)). A backend
whose terms forbid reaching a subscription login from third-party software is not
added, however it is invoked
([#36](https://github.com/aldegad/sprite-gen/pull/36), Antigravity).

| Provider | Backend | Auth | Billing | Output truth | Transparency strategy |
|---|---|---|---|---|---|
| `codex` | codex `image_gen` | ChatGPT OAuth | your ChatGPT subscription | inline base64 in the session rollout jsonl, decoded deterministically | **`native`** — image_gen returns a real alpha channel when asked (measured, then published) |
| `grok` | direct Imagine `/images/generations` or `/images/edits` | Grok login (preferred) or `XAI_API_KEY` | your SuperGrok Imagine quota; console credit only on the key route | inline bytes decoded and re-encoded as a verified PNG | `chroma` — Imagine returns JPEG only; generate on a key and matte it out |
| `openai` | direct Images `/v1/images/generations` or `/v1/images/edits` | `OPENAI_API_KEY` | **billed per call** — for servers and SaaS | inline base64 decoded and published as a verified PNG | **`native`** — `background: transparent` returns a real alpha channel |
| `azure` | Azure OpenAI / Foundry v1 Images API | Azure CLI / Entra, or explicitly selected API key | **billed per call**, explicit-only | inline base64 decoded and published as a verified PNG | **`native`**, subject to the configured deployment's capabilities |

Azure setup, deployment-name overrides, optional vision, and its no-retry/no-auth-fallback
contract are documented in [Azure images](azure.md). It is not a default,
guided subscription provider, or codex-unavailable fallback.

The strategy is declared **once**, on the adapter (`Provider.transparency`), and is
the only place that says what a backend can do. See
[Transparent output](#transparent-output--strategy-per-provider).

## Subscription first — `openai` runs only when you chose it

`codex` and `grok` run on a subscription the user already pays for. `openai`
reaches the same family of GPT image models over the REST API with nothing but a
key — which is what a headless container (a Modal worker, CI, a SaaS backend) can
have, and where an interactive ChatGPT login cannot exist — and every call is
billed against that key. So it is registered, documented, and runs **only when the
user chose it** (maintainer 확정 2026-09-20, widened 2026-10-16 from "only when
named"):

- it runs when `--provider openai` names it, or when it is the user's own answer
  for a default codex that cannot run ([below](#when-codex-cannot-run--ask-or-what-you-chose));
- it is never a default: `SPRITE_GEN_DEFAULT_PROVIDER=openai` is **refused**, not
  honoured, and nothing reaches it unasked;
- the guided flow (`sprite-gen workflow`) never offers it as the image provider; it
  offers it once, as one answer to "codex cannot run here — what now?", billing named;
- having `OPENAI_API_KEY` in the environment changes no route by itself;
- every call it does make prints one stderr line naming the charge *before* the
  request leaves.

If you are one person generating sprites, use `codex` or `grok`.

## Default provider selection

`--provider` is **optional**. When omitted, the backend is resolved by a fixed
precedence (maintainer 확정 2026-07-17):

1. **`SPRITE_GEN_DEFAULT_PROVIDER`** env var (`codex` or `grok`) — the user override.
   An unknown value fails loud, and so do `openai` and `azure`: a per-call API backend is
   never a default ([subscription first](#subscription-first--openai-runs-only-when-you-chose-it)).
2. **`codex`** — the hard default (GPT `image_gen`).

A resolved default of `codex` that cannot run here turns into what the user chose
for that ([below](#when-codex-cannot-run--ask-or-what-you-chose)). The grok default
(`SPRITE_GEN_DEFAULT_PROVIDER=grok`) has no reverse fallback — a down grok fails
loud at generation time.

An **explicit `--provider`** is always honored verbatim — it is never overridden by
the choice below. An explicitly named provider that is down fails loud (the provider
adapter raises; a named codex whose usage is used up fails with codex's own words,
exit 1), preserving the operator's stated intent.

Every generation reports which backend actually ran: `provider` (the real
backend), `provider_resolved_from` (`explicit` / `SPRITE_GEN_DEFAULT_PROVIDER` /
`hard-default` / `fallback-from-codex`), and `provider_fallback` when a default
codex that could not run was switched to the provider chosen for that.

## When codex cannot run — ask, or what you chose

A call that names no provider (`gen`, `logo`, `gen-set`, `video-set`'s mid-step
redraw) runs on codex. When codex **cannot run here** it does not switch on its own
(maintainer 확정 2026-10-16; until 2.44.0 it went to `grok` unasked). Codex cannot
run when

- **it is not signed in** (`cause: login`): `codex_provider.codex_login` — the CLI is
  not on PATH, `codex login status` fails or does not answer, or exits non-zero.
  Known before anything is generated.
- **its usage is used up** (`cause: usage-limit`): `codex exec` ends on one of the
  errors codex itself classes as its usage limit, read from what it says
  (`codex_provider.usage_limit`, below). Known only after `codex exec` ran, so the
  same choice applies there: the call runs again on the chosen provider.

What happens then is the user's choice, one of three:

| Choice | What a call that names no provider does |
|---|---|
| `ask` (the default) | Nothing is generated. One stderr line `sprite-gen ask codex-unavailable: {…}` and **exit 75**: the line's JSON holds `cause`, `reason` (the reading's own words), `question`, and `choices` — the flag that runs each answer (`--provider openai`, `--provider grok`; `video-set`: `--still-provider …`), `stop` null. The agent asks the user ([SKILL](../SKILL.md#when-codex-cannot-run--exit-75)). |
| `grok` | Runs on grok. One stderr line says codex could not run, why, and whose choice grok was; the report keeps `provider_fallback` (`from`, `to`, `cause`, `reason`, `default_source`, `chosen_by`). |
| `openai` | Runs on the OpenAI API, billed per call: the same switch line and `provider_fallback`, and the charge line every `openai` call prints. |

The choice is read in this order, only when codex cannot run:
`SPRITE_GEN_CODEX_UNAVAILABLE` (`ask` / `grok` / `openai`, an unknown value fails
loud), then the saved one (`sprite-gen defaults save --codex-unavailable …`, in the
[one settings file](user-workflow.md#one-settings-owner); `chosen_by: saved`), then
`ask`. The guided flow asks it once and saves it.

`gen-set` and `video-set` run `gen` per row or item with its output in a log. A
child that asked does not fail its row in silence: the set stops with the same
question and exit 75 once the rows under way have ended (a `gen-set` starts no
further stage; a `video-set` lets its other items finish, as they need no codex), its
report and `table.md` keep the question (`asked`), and the line names the set's own
flag. Rerun the set with the answer's flag; finished rows and items are reused.

**The usage-limit signal** (2026-10-16, codex-rs source at `rust-v0.160.1` and
`rust-v0.153.4`). codex classes three errors as its usage limit
(`CodexErr::to_codex_protocol_error` → `CodexErrorInfo::UsageLimitExceeded`,
`codex-rs/protocol/src/error.rs`): `UsageLimitReached` (an HTTP 429
`usage_limit_reached`), `QuotaExceeded` (429 `insufficient_quota` and its billing
codes) and `UsageNotIncluded` (429 `usage_not_included`; `codex-rs/codex-api/src/api_bridge.rs`).
`codex exec --json` carries only their message: its `error` and `turn.failed` events
hold `message` alone (`codex-rs/exec/src/event_processor_with_jsonl_output.rs`), the
error event is not kept in the rollout (`codex-rs/rollout/src/policy.rs`), and codex
exits 1. So the signal is the start of each message as codex's own tests pin it
(`codex-rs/protocol/src/error_tests.rs`): "You've hit your usage limit" (the
apostrophe is ASCII in 0.153.4 and U+2019 in 0.160.1), "Your workspace is out of
credits.", "You hit your spend cap set", "Quota exceeded. Check your plan and billing
details." and "To use Codex with your ChatGPT plan, upgrade to Plus". Any other
failure of `codex exec` is a failure, not the question. These sentences were read
from the source, not seen from a used-up account; a codex release that rewords them
is a failure until this list follows it.

## Direct Grok calls and authentication

`sprite-gen gen --provider grok` → `GrokProvider` → xAI Imagine API. The same
path works from any agent engine; no Grok Build subprocess is started.
The default is `grok-imagine-image-2.0`. `--model` selects an **image API model**,
not a Grok Build reasoning model.

Image and video calls share `sprite_gen/gen/xai.py` for credentials and JSON
transport. The user's Grok subscription login (`GROK_HOME` or `~/.grok`) takes
precedence even when `XAI_API_KEY` is set. Only an absent login file permits the
API key (console credits); an invalid, expired or rejected login never does. See
[authentication and expiry](video.md#setup--pick-one-credential)
for setup; the video modes (last-frame pin, references, `video-extend`, `video-edit`) are in [video.md](video.md#modes--which-flags-call-what). No Grok executable is needed during generation with a valid credential.
No agent or credential fallback is started on failure; a refusal xAI documents as a request not
taken (429) is sent again, nothing else is ([a refused request sent again](#a-refused-request-sent-again)).

New images use `/v1/images/generations`; one reference uses `/v1/images/edits`
with `image`, two to five use `images` in input order. A single-reference edit
inherits its source aspect ratio; `--aspect-ratio` applies to generation and
multi-reference edits. Invalid reference files and more than five references
fail before upload. Base64 output is requested, decoded, and converted to a real
PNG without resizing before atomic publication. Missing or malformed image data
fails without replacing an existing raw output.

Image reports include `extra.auth_source`, `extra.transport: "xai-api"`,
`extra.endpoint`, and `extra.aspect_ratio_source`. Tokens, signed URLs and raw
API error bodies are not reported. Skipping the agent removes its startup and
reasoning overhead; speed and account-quota savings are not benchmarked guarantees.

API contracts: [image generation](https://docs.x.ai/developers/model-capabilities/images/generation),
[image editing](https://docs.x.ai/developers/model-capabilities/images/editing),
[multi-image editing](https://docs.x.ai/developers/model-capabilities/images/multi-image-editing).

## Direct OpenAI calls and authentication

`sprite-gen gen --provider openai` → `OpenAIProvider` → the OpenAI Images REST API.
No `codex` CLI, no browser sign-in, no subprocess: the whole credential is
`OPENAI_API_KEY`, read from the environment at call time and never written
anywhere. Unset, the run stops naming the variable and where to get a key; set but
empty, it says so. It never falls back to the codex ChatGPT login or to another key;
a refusal OpenAI documents as a request not taken is sent again, nothing else is
([a refused request sent again](#a-refused-request-sent-again)) — and see
[subscription first](#subscription-first--openai-runs-only-when-you-chose-it) for why
nothing routes here on its own.

The default model is `gpt-image-2.5-flare`; `--model` selects another gpt-image
model. New images use `POST /v1/images/generations` (JSON); attaching `--ref` uses
`POST /v1/images/edits`, which takes **multipart/form-data** with the references as
repeated `image[]` parts in the order given (up to 16). A reference that is not a
readable PNG, JPEG or WebP fails before upload, and parts are named by position, not
by the local filename.

gpt-image models always answer with inline base64 (the API does not accept
`response_format`), so there is no signed URL to follow and no bearer token to
forward. The payload is decoded, verified as a PNG and published atomically without
resizing; a missing, malformed or multi-image response fails without replacing an
existing raw output. HTTP 401/403 names the credential; any other non-200 fails
with the status, the provider's code and the error body the provider sent.

A refusal line is `<verb>: <reason> (HTTP <n>)[ code=<code>][ key=value ...]; …: <body>`:
`code=` is the provider's `error.code` when it matches `^[A-Za-z0-9_.:-]{1,64}$`,
and `<reason>` is `refused by the provider's content policy` when that code is a
documented policy block (`sprite_gen/gen/refusal.py` `POLICY_CODES`: OpenAI
`moderation_blocked`, followed by `moderation_stage=input|output|unknown`; Azure
[`contentFilter` or `content_policy_violation`](https://learn.microsoft.com/azure/foundry/openai/how-to/dall-e#quickstart)).
Unknown Azure codes, authentication failures and quota errors are not labelled
as policy blocks. The
`(HTTP <n>)` part never changes shape. The video verbs use the same `code=` and
reason, and a clip xAI reports with `respect_moderation: false` is that refusal
with `respect_moderation=false` in place of a code (docs/video.md). A clip xAI
accepted and the poll timeout cut short is no refusal: its line names
`request_id=<id>` and carries no `(HTTP <n>)`, so nothing that sends a refused
request again sends it (docs/video.md, An unfinished generation).

`<body>` is the provider's error body as received, last on the line: JSON on one
line, anything else as its text (line breaks written as `\n`), nothing when the body
was empty. Two things in it are masked with `[redacted]` (`refusal.masked`): strings
shaped like an API key (`sk-…`, `xai-…`, a `Bearer` value, and the credential the
request carried; a key the provider already masked stays as it came) and the query
string of any URL wherever the URL starts, where a signature lives. Each string is
masked as the provider sent it, before the body is put on one line, so only a letter
or digit of the provider's own text in front makes `sk-`, `xai-` or `bearer` part of
a word (`task-…`); a line break, a tab or any other character does not. The body can
repeat the prompt, so a product that shows sprite-gen's stderr to its own users
decides what to cut there.
The video verbs print what xAI said the same way, through the same mask. Because the
body comes after everything else, nothing it says moves the status, reason or code
a caller reads off the front.

`--aspect-ratio` picks the `size`. gpt-image has no long-edge preset, so the ratio
maps to one concrete size out of a table that satisfies the documented constraints
(both sides divisible by 16, ratio within 1:3..3:1, 655,360–8,294,400 pixels) and is
checked by the test suite: `1:1` 1024x1024, `3:2` 1536x1024, `2:3` 1024x1536, `4:3`
1024x768, `3:4` 768x1024, `16:9` 1536x864, `9:16` 864x1536, `2:1` 1440x720, `1:2`
720x1440, `3:1` 1536x512, `1:3` 512x1536, plus `auto`. Omitted = 1024x1024. A ratio
with no exact gpt-image size (`19.5:9`, say) is refused rather than rounded to a
nearby one you would be billed for.

`--transparent` asks for `background: transparent` with `output_format: png` —
genuine alpha, the same `native` strategy as codex, and the same measurement gate
afterwards. Measured 2026-09-20 on `gpt-image-2.5-flare` at `--quality low`: 84 % of
the frame at alpha 0, the subject itself at alpha 251–254 (so `partial_alpha_pct`
covers the body, exactly as codex `image_gen` does — downstream extraction treats
`alpha ≤ 16` as transparent, so this is usable as-is).

Image reports include `extra.auth_source`, `extra.transport: "openai-api"`,
`extra.endpoint`, `extra.quality`, `extra.size`, `extra.background`, when the
API returns it, `extra.usage` (token counts only — what the call cost, never what it
contained), and `extra.rate_limit`: every `x-ratelimit-*` header the response carried,
names lower-cased and values as sent (`{"x-ratelimit-remaining-requests": "49", …}`),
`{}` when none came. OpenAI's rate-limits guide names request, token and project-token
limits there and lists images per minute among the limits without naming a header for
it, so whatever the images endpoints send is what the report holds.

API contract: [OpenAI Images](https://developers.openai.com/api/images/) (field names
and enums confirmed there 2026-09-20).

## A refused request sent again

One rule for every direct provider call — the OpenAI and Grok image calls here, the
facing check, the clip request and polls of the video verbs (docs/video.md), and the
Anthropic transport of `pixel-draw` (docs/pixel-draw.md) — in `sprite_gen/gen/retry.py`:

- **A POST is sent again only when its provider documents the answer as a request it
  did not take.** That request made nothing, so asking again buys nothing twice. What
  the providers say, read 2026-10-13:

  | Provider | Sent again | Not sent again |
  |---|---|---|
  | OpenAI ([error codes](https://developers.openai.com/api/docs/guides/error-codes), [rate limits](https://developers.openai.com/api/docs/guides/rate-limits)) | 429 "Rate limit reached for requests" and `slow_down`; 503 `server_is_overloaded` — the two its SDKs retry | 500 "The server had an error while processing your request"; the billing 429s (`credit_balance_exhausted`, `organization_spend_limit_exceeded`, `project_spend_limit_exceeded`, `organization_usage_limit_exceeded`, type `insufficient_quota`): "Don't retry quota, billing, or other errors that require you to take action" |
  | xAI ([debugging](https://docs.x.ai/developers/debugging), [rate limits](https://docs.x.ai/developers/rate-limits)) | 429 "You are sending requests too frequently and have reached the rate limit" | everything else — it documents no 5xx |
  | Anthropic ([errors](https://platform.claude.com/docs/en/api/errors), [rate limits](https://platform.claude.com/docs/en/api/rate-limits)) | 429 `rate_limit_error`; 529 `overloaded_error`; 500 `api_error` ("Retry the request with exponential backoff") | 504 `timeout_error` "The request timed out while processing"; the spend-cap 429 (`error.details.error_code: enforced_spend_limit_reached`, no `retry-after`), which "fails until access resumes"; 502, 503 |

- **The wait** is the answer's `retry-after` when the run waits that long (at most
  120 s; a longer one is not waited out early — the request is not sent again), else
  the doubling wait the video polls wait by: 8 s, then 16 s. **Twice at most**, each
  said on stderr, the status in plain digits:
  `[gen] openai refused the request (429 rate_limit_exceeded); nothing was made — asking again in 8s (1 of 2)`.
  A refusal still there after that ends the call as any refusal does, its line saying
  `sent 3 times, no provider fallback` where it says `no retry or provider fallback`.
- **The waits are inside the call's time limit**, counted from its first send:
  `SPRITE_GEN_GEN_TIMEOUT_SECONDS` (180) for an OpenAI or Grok image and the OpenAI
  facing check, 120 s for the Grok facing check — each send reading only what is left
  of it — the poll timeout for a clip (docs/video.md), `--batch-wait` for the reads of
  a Message Batch (docs/pixel-draw.md). A wait that would reach the limit is not
  waited: the refusal ends the call after one line,
  `[gen] openai refused the request (429 rate_limit_exceeded); nothing was made — not asking again: the 16s wait would reach the time limit (12s left)`.
  The limit is read again after the wait, right before the send: a sleep may wake
  later than it was asked to, and one that woke at the limit or past it sends nothing —
  the refusal ends the call the same way,
  `[gen] openai refused the request (429 rate_limit_exceeded); nothing was made — not asking again: the time limit came during the 8s wait`.
  A realtime `pixel-draw` call has no time limit of its own.
- **A request that got no answer it could read is never sent again** — the connection
  broke, it timed out, or the answer broke on the way, a refusal whose body broke
  included (`xai.error_body`: a refusal's status alone is not an answer read whole).
  The server may have taken it.
- **A GET buys nothing**, so one answered 429, 500, 502, 503, 504 or 529 is asked again
  after the same doubling wait: the video polls for as long as their poll timeout lets
  them (docs/video.md), the reads of a Message Batch twice.

`video-set` runs each clip once: a 429 is sent again inside `sprite-gen video`, and a
second run on top would make every one of its POSTs a second retry loop.

## CLI

```bash
sprite-gen gen \
  [--provider codex|grok|openai|azure] # optional; default = SPRITE_GEN_DEFAULT_PROVIDER env → codex; a codex that cannot run asks (exit 75) or runs what you chose for that. azure must be explicitly named; openai/azure bill per call
  --prompt "…"            # or --prompt-file PROMPT.txt
  --out DEST.png \
  [--ref REF.png ...]     # repeatable; Grok accepts up to five references, the shared OpenAI/Azure codec up to sixteen
  [--transparent [--alpha-mode auto|native|chroma] [--chroma-key magenta|green]] \
  [--white-check CHECK.png] \
  [--aspect-ratio 1:1]    # grok/openai/azure; OpenAI/Azure map it to a gpt-image size
  [--quality low|medium|high|xhigh|max|auto] # billed effort; openai/azure send the range, grok takes auto/low/medium
  [--resolution 1k|1.5k|2k]  # grok output-size tier, priced with --quality
  [--model ID] \
  [--layout-guide [figure|ui-kit]]  # attach a layout guide: the one-slot figure guide (bare flag) or the UI kit layout; see below
  [--direction side|front|back|front_diagonal|back_diagonal [--facing right|left]] # add the engine's view sentence
  [--handed "the black smartwatch=left wrist" ...]  # with --direction: where an item on one side is in that view
  [--body-plan quadruped | --body-plan "the horse=quadruped" ...]  # with --direction: the view for that body
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
  default codex that could not run was switched to the provider chosen for that), plus the provider `extra` block
  (`auth_source`, `transport`, `endpoint`, and the knobs the request actually
  carried).

- **`--direction`** adds the view sentence a sprite still is drawn with (`still_view_text`; a side or
  diagonal view also takes `--facing`, a front or back view refuses it) and records `extra.view`.
  **`--body-plan`** (with `--direction`; `quadruped`, `legless`, or `"<figure>=<plan>"` per figure of a
  scene) says that view without the feet, chest, hips, shoulders and shoes a person's still is drawn
  with, ending in what the body stands on; see
  [video-pipeline](video-pipeline.md#a-body-that-is-not-a-person----body-plan).
  **`--handed`** adds, per item, which of the character's own sides it is on and where that side is in
  the view; with it, `--facing-fix mirror` is refused and `regen` never mirrors. See
  [video-pipeline](video-pipeline.md#handedness--an-item-on-one-side).
- **The engine's sentences go on after yours, as written.** The view, the turn over a reference, the
  handed item, the key background and the layout guide are attached in one place; without `--handed`
  the prompt is 2.22.0's, byte for byte. Leave out of your text what an option says. Where your own
  text says the opposite of an option ("facing left" with `--facing right`) it is left as written and
  stderr warns; the report lists it under `extra.prompt_notes`. See
  [prompt-assembly](prompt-assembly.md).

## `--quality` and `--resolution` — the two billed knobs

They are one shared vocabulary; what a backend can honour is a per-provider
capability, declared in its adapter:

| Provider | `--quality` | `--resolution` |
|---|---|---|
| `codex` | refused — `image_gen` exposes no effort dial | refused |
| `grok` | `auto` `low` `medium` | `1k` `1.5k` `2k` |
| `openai` | `auto` `low` `medium` `high` `xhigh` `max` | refused — a gpt-image `size` comes from `--aspect-ratio` |
| `azure` | same vocabulary, subject to the deployed model | refused — a gpt-image `size` comes from `--aspect-ratio` |

- **Omitted = the provider's own default.** The request body is then byte-identical
  to what the same call already sent, so nothing about existing runs changes.
- **A level a provider cannot honour fails before the call.** It is never dropped
  from a body you are about to pay for, and never downgraded to whatever the
  backend felt like returning (No Silent Fallback). "Refused" above is a loud error
  naming the flag, not a silently ignored argument.
- **grok prices an image on the pair**, so `2k` + `medium` costs more than `1k` +
  `low`. The resolution names are **tiers, not pixel counts**: the service decides
  the pixels, and `1.5k` came back 1408x1408 at `--aspect-ratio 1:1` (실측
  2026-09-20) — not 1536, and not 1024. Nothing here derives a size from the name.
- The grok subsets were read off the server, not off the prose (2026-09-20 probes):
  `quality` deserializes the wider shared enum and *then* refuses per model
  (`high` → HTTP 400 "This model only supports the following quality value(s):
  low, medium, auto."), `resolution: 1.5k` renders (HTTP 200) although the
  capability guide's prose lists only 1k and 2k, and an **unknown field is not
  refused at all** — a probe carrying one rendered normally. That last one is why
  these names are checked locally instead of being left to the service to notice.
- Every call that spends API credit rather than a subscription prints the knobs it
  is being billed on in the same stderr line as the charge.

## Transparent output — strategy per provider

`--transparent` does not mean "chroma key" any more. Each adapter declares the one
strategy it can execute, and `--alpha-mode auto` (the default) follows it:

| Strategy | Who | What happens | Refused when |
|---|---|---|---|
| `native` | `codex` (**first choice**, 2026-09-08), `openai` (`background: transparent`) | On codex the transport prompt asks image_gen for a genuinely transparent background (the bundled `imagegen` skill honours "transparent background" and keeps the generated alpha; codex reports `transparentBackground: true` on the completed item); on openai the request carries `background: transparent` with `output_format: png`, which the API answers with a real alpha channel. Either way the decoded PNG's alpha is **measured**: no alpha band or `alpha_zero_pct: 0.0` refuses to publish, RGB under alpha 0 is scrubbed, partial alpha (1–254) is left as produced and reported as `partial_alpha_pct`. | The model drew a checkerboard / flat background (RGB image) — nothing can recover alpha from that, so the run fails instead of silently keying. |
| `chroma` | `grok` (only option), `codex` with `--alpha-mode chroma` | Generate on a `#FF00FF` (or `#00FF00`) background — pick the key by subject colour (magenta subjects → green key) — and matte it out through the frame extractor's canonical YCbCr matte (`remove_chroma_background_ycbcr`), which keys from the background chroma it detects on the borders (`detect_background_key_ycc`) rather than from the pure key alone — the RGB matte behind `cutout`/`extract`/`slice-sheet` does the same since 2026-09-11 (`detect_background_key_rgb`), so every chroma path tolerates the slightly-off green/magenta generators actually paint. Gradients and texture within that chroma family are supported. | `alpha_zero_pct: 0.0` after keying, or stale RGB under alpha 0; and, before keying, a background painted as a transparency checkerboard (below). |

- **`auto` steps down to `chroma` when `--ref` is attached**, on either native provider. Measured
  2026-09-08 (plan `sprite-gen/parts-rig`): codex `image_gen` with reference images
  returned real alpha in 1/6 runs and drew a checkerboard (RGB) in 5/6, while the same
  prompts on a `#00FF00` key + chroma keying succeeded 6/6. The decision is made before
  the model runs, printed to stderr, and recorded as `alpha.strategy_source:
  "refs-attached"` (`provider-default` / `explicit` otherwise). The measurement is
  codex's; openai's edit path is not separately measured and inherits the same
  conservative default. `--alpha-mode native`
  still forces native alpha with refs — and fails loud on an RGB result.
- **The step down asks for its key.** A planned key needs a key background in the
  prompt, so the engine adds the `--chroma-key` key's line
  (`chroma.KEY_BACKGROUND_TEXT`, the sentence a clip's mid-step redraw also ends on)
  to a prompt that names no key, and leaves a prompt that does as it is — no second
  line. A prompt names a key with the key's hex code (`#FF00FF`, `00ff00`) or the
  key's name right before "background", "backdrop", "chroma key", "key" or "screen"
  (`chroma.named_key_background`); a colour in the subject ("a green frog") is not a
  key. A prompt that names the other key is left on it: the matte reads the key off
  the borders. Write the key as "a magenta background" or "#FF00FF": "no green screen"
  reads as naming green, and "a background of pure magenta" is not read
  ([prompt-assembly](prompt-assembly.md#known-faults-kept-for-now)).
  `alpha.key_background` records `{"injected": true|false, "key": …}`,
  stderr says which, and the report's `prompt` is the prompt sent. Without the line a
  reference on white came back on opaque white (3/3, 2026-10-04) and keying white took
  the outline and the cream fills with it. With it (the same prompt otherwise, three
  takes on that RGB reference and two on the transparent one), all five came back on
  a magenta key and were keyed with the outline and cream whole. Only `auto`'s step
  down adds the line: `--alpha-mode chroma` is the caller's own key and prompt, and a
  native run asks for alpha instead.
- **A ref run whose raw already has a transparent background is not keyed.** The key
  is planned before the model runs, but the raw is read before it is applied
  (`chroma.classify_raw_alpha`): a raw with an alpha band, at least 5 % of its pixels
  at alpha 0 and at least half of its one-pixel border at alpha 0 is `real-alpha`, and
  it is published on its own alpha through the `native` check
  (`alpha.strategy: "native"`, `alpha.strategy_source: "refs-attached-raw-alpha"`).
  Keying it reads the RGB left under alpha 0 as the background colour and mattes the
  outline and light fills away. A raw with no alpha band or no alpha-0 pixel — a drawn
  checkerboard, a key background, an opaque RGBA — is `no-alpha` and keyed as before
  (`strategy_source: "refs-attached"`), but a painted checkerboard is refused before
  keying (below). Alpha 0 that misses either bar is `ambiguous`:
  keyed as before, with a `warning` in the report and on stderr. The verdict and its
  numbers are in `alpha.raw_alpha` on every ref run that planned the key. The check
  belongs to `auto`'s step down only — `--alpha-mode chroma` keys whatever comes back.
  A `real-alpha` raw has no key to remove, so `--decontam` is skipped on it, not
  refused after the paid call: `alpha.decontam` records `{"requested": …, "skipped":
  …}` and stderr says so (a warning for `palette`, which demands the pass). The check
  is the counts, not the rounded percentages: a raw with a single alpha-0 pixel is
  `ambiguous`, reported as `0.0`. The judgment of the alpha band is one function,
  `chroma.alpha_verdict`: [`pixel-draw`](pixel-draw.md#gates)'s G3 asks it of every frame
  it drew (a sprite or icon frame must be `real-alpha`, a tile `no-alpha`).
  Measured 2026-10-04 (codex `image_gen`, subscription, one transparent front still as
  the reference, three takes per arm): with the step down's prompt (no transparency
  request, no key colour), a transparent (RGBA) reference came back with real alpha
  3/3 and an RGB one (the same still on white) on an opaque white background 3/3; with
  the native request (`--alpha-mode native`), both came back with real alpha 3/3 —
  no checkerboard in 6 runs, against 5 of 6 on 2026-09-08. The step down is kept
  until a larger measurement replaces that one.
- **A painted transparency checkerboard is refused before keying.** Asked for a key
  background, a model sometimes paints the grey and white checkerboard that editors show
  behind transparent pixels instead. The matte reads the key off the borders, so it reads
  the checkerboard and keys nearly the whole image away: a UI kit sheet keyed that way has
  nothing left to cut. Every chroma key checks the raw first
  (`chroma.painted_checkerboard`). Each edge is read on its 8 nearest lines. A line is
  a checkerboard row when at least 90 % of it is grey (channels within 24 of each other),
  it holds two greys at least 12 apart (10th and 90th percentiles), and it changes from
  one to the other in at least 6 runs between changes, whose median is 4 to 128 px and
  three quarters of which lie within a quarter of it (or 2 px). A checkerboard alternates along both axes, so
  the raw is one when the top or bottom edge and the left or right edge show it. Stripes
  alternate along one axis only, a key background is not grey, and a checkered subject
  inside a key background does not reach the edges. The run then fails with its own
  reason, before anything is published:
  `chroma: the background is a painted transparency checkerboard (8 px squares along the
  top, bottom, left and right edges), not the magenta key the image was asked for; keying
  it would erase the image — generate it again`. A caller generates again.
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
  How codex is signed in is asked once per call by `codex_provider.codex_login` (`codex login status`), the same reading the default resolution, the facing check and the guided workflow use: signed in with ChatGPT is the subscription (`auth_source: codex-login`); signed in with an API key bills that key per call, said on stderr before `codex exec` runs (`codex-api-key`); not signed in is refused with the reason before codex runs (an omitted `--provider` asks or runs what was chosen instead, [above](#when-codex-cannot-run--ask-or-what-you-chose)). A `codex exec` that ends on codex's usage limit is `codex_provider.CodexUsageLimit`: the same failure for a named codex, the same choice for a default one. What `codex login status` prints is never repeated: an API-key login can name its key.
  A codex call is stopped after `SPRITE_GEN_GEN_TIMEOUT_SECONDS` (180) without an answer and reported as a stall (`gen-timeout`).

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
- **grok** — uses the [direct API contract](#direct-grok-calls-and-authentication) above.
- **openai** — uses the [direct API contract](#direct-openai-calls-and-authentication)
  above: one HTTPS request on `OPENAI_API_KEY`, no CLI and no subprocess, which is
  the whole reason it exists (a container has a key, not a browser). Runs only when
  chosen, and billed per call.

## Sprite-row usage

In the atlas pipeline (SKILL.md §2) the rows of a prepared run are generated by
`sprite-gen gen-set --run-dir <run>`: every non-mirrored state, `--concurrency` 6 at a
time (lead-verified: no provider throttling at 6), each row with the identity ref the run declares (`base-source.*`, or the accepted
direction anchor for a direction-contract action row — `sprite_gen.curate.anchor` owns
that choice) plus its layout guide, into `raw/<state>.png`. Each row gets
`reports/gen-set/<state>.json`, the batch writes `reports/gen-set/table.md` and
`set.report.json`, existing rows are reused unless `--force`, a direction run generates
its anchors before its rows and stops when an anchor failed, and the exit code is
non-zero when any row failed. `--provider` (`codex`, `grok` or `openai`) is honoured verbatim;
unspecified, it resolves exactly as `gen` does (above): a switch to the provider chosen for a
codex that cannot run is recorded per row with the row's `auth_source`, and rows that asked stop
the set with the question ([above](#when-codex-cannot-run--ask-or-what-you-chose)). Each row's
output goes to its log, but a row's billing line and a provider switch also print on the
terminal as they happen (`base.run_logged`), so a metered row is never announced only in a file.

One row by hand is the same call `gen-set` makes: `--provider codex` (or `grok`) with
`prompts/<state>.txt`, writing `raw/<state>.png`.
The row prompts are `prepare`'s, and were measured on people: a walk row moves "body, arm, leg", a
front walk "alternating leg, arm, shoulder", a wave "arm pose only". For a body that is not one biped,
`prepare --body-plan quadruped` (or `legless`, or `"<figure>=<plan>"` per figure) writes every row
without a part the body lacks and says what it stands on
([sheet rows](video-pipeline.md#the-sheet-rows--prepare---body-plan)).
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

## `--layout-guide` — room above the head and under the feet

A single still drawn from words tends to fill its frame top to bottom. Animated in place
(a walk bobs, a jump rises), the head then touches the top of the frame. `--layout-guide`
draws the one-slot form of the row guide `prepare` writes for every row and attaches it
after any `--ref`:

- the frame, its inner safe box at the row guide's margin (9.4 % of the frame, as
  `prepare`'s `DEFAULT_SAFE_MARGIN_RATIO`), and the centre line;
- an orange **crown line** and a teal **floor line**, each one margin *inside* the safe
  box (about 19 % and 81 % of the frame height). On the box's own edges a crown line
  leaves hair and hats to fill the room above it, and a floor line reads as a frame
  edge that the feet float above.

The prompt gains a paragraph saying what each line means: the anatomical top of the
skull on the orange line (hair, hats, raised limbs and props ignored, but kept inside
the box), the supporting soles on the teal line, the two lines as scale and height
rather than artwork, and nothing of the guide itself in the output. The guide is drawn
for `--aspect-ratio` (square without one) and the report records the cell it used in
`extra.layout_guide` (size, safe margins, `crown_y`, `floor_y`); the report's `refs` are
still the caller's own.

`--layout-guide ui-kit` attaches the UI kit layout instead: one box per element of a game
UI kit, and a paragraph naming what to draw in each box, with straight edges between
corner ornaments and empty centres. [`ui-kit`](ui-kit.md) cuts the sheet along the same
boxes. The ratio chooses the layout: `--aspect-ratio 1:1` (or none) draws the square
layout's seven frames (panel, button, pressed button, card frame, two bar frames, timer
badge); `--aspect-ratio 2:3` draws the whole kit, those seven at the same places and, below
them, the two bar fills, an item slot, a counter plate, a lit and an unlit dot and a
joystick base and knob. Any other ratio is refused. `extra.layout_guide` is then
`{"kind": "ui-kit", "width", "height", "boxes"}`.

The guide is an attached image, so it changes the call: openai sends it to
`/images/edits` and bills it as image input, grok sends it as a reference, and
`--transparent` steps down to chroma keying as it does for any `--ref`. How closely a
model follows the guide differs from model to model, so measure the one you use
(`--model`) on your own subjects before relying on it.

## `--trim-alpha` — the bottom edge is the foot line

A generated transparent still carries an unpredictable band of empty alpha under the
feet and around the sides; two stills placed on the same floor line then stand at
different heights, and a still swapped for a strip cell (whose feet sit on the cell
bottom) jumps. `--trim-alpha` (only with `--transparent`) crops the published PNG to the
bbox of its opaque pixels (alpha ≥ 8) after the transparency step, so the image's bottom
edge *is* the ground-contact line. The subject is never cut — only fully transparent
margin goes — and the report records `extra.trim_alpha` with the `bbox`, `before` /
`after` sizes and the `margin_px` removed on each side. The `.raw.png` beside the output
is untouched.
