# `sprite-gen video` — image to video with your own Grok login (engine SSoT)

> Owns: `sprite-gen video` / `video-extend` / `video-edit`: stills and clips to mp4 through Grok Imagine with the user's own credential · Index: [docs/README.md](README.md)

`sprite-gen video` animates one still into a short mp4 through **Grok Imagine**
(xAI `POST /v1/videos/generations`). It is the video counterpart of
[`sprite-gen gen`](gen.md): one call = one still (+ prompt) → one **verified** mp4
on disk plus a machine-readable report. The sprite-gen skill routes standalone
video requests here from any agent engine.

The same engine also pins a **last frame**, guides a clip with **reference
images**, **extends** an existing clip (`video-extend`) and **edits** one with a
prompt (`video-edit`). The mode table below is the design memo for those modes
(2026-09-13); the pipeline verbs in [video-pipeline.md](video-pipeline.md)
(`video-canvas` → `video` → `video-frames` → `video-loop`, `video-set`) keep
calling `sprite-gen video --image …` exactly as before and know nothing about
the new modes.

No credential is shipped with this repository. You bring your own, in one of two
forms, and every run reports which one it used.

## Modes — which flags call what

Measured 2026-09-13 with the subscription login (`grok-login`) and the direct
REST calls below (vault `projects/sprite-gen/_assets/grok-video-mode-probe-2026-09-13/report.md`);
limits from docs.x.ai `model-capabilities/video/*` read the same day.

| Verb / flags | Endpoint · model | Body fields | Options allowed | Local pre-checks (before any call) | `mode` |
|---|---|---|---|---|---|
| `video --image A` | `POST /v1/videos/generations` · `grok-imagine-video-1.5` (`--model` free) | `image{url}` | duration 1–15, resolution, aspect, audio | still exists; unchanged contract (body snapshot pinned by test) | `image-to-video` |
| `video --image A --last-frame B` | same · 1.5 only | `image{url}`, `last_frame{url}` | same | both stills exist; classic `grok-imagine-video` rejected | `first-last` |
| `video --last-frame B` | same · 1.5 only | `last_frame{url}` | same | classic rejected | `last-frame` |
| `video --reference X [--reference Y …]` (+ optional `--image`) | same · 1.5 (classic accepts references alone, measured 2026-09-13, but not `--image` + references) | `reference_images:[{url}…]` (+ `image{url}`) | duration, aspect, audio; resolution **480p/720p only** | 1–7 references; prompt tags `<IMAGE_n>` with `0 ≤ n < count` (0-based); `--resolution 1080p` rejected; classic + `--image` + references rejected | `reference` |
| `video-extend --video in.mp4 --duration N` | `POST /v1/videos/extensions` · **`grok-imagine-video` forced** (1.5 answers `400 Video extension is not supported for this model`) | `video{url}`, `duration` | duration 2–10 (extension length only; default 6). No resolution / aspect / model flags: output inherits the input's, capped at 720p | `.mp4` with an `ftyp` box; ffprobe duration 2–15 s | `extend` |
| `video-edit --video in.mp4` | `POST /v1/videos/edits` · **`grok-imagine-video` forced** (1.5 answers `400 Video editing is not supported for this model`) | `video{url}` | none of duration / resolution / aspect / model: output inherits the input's, capped at 720p | `.mp4` with an `ftyp` box; ffprobe duration ≤ 8.7 s | `edit` |

Invariants:

- **The `--image`-only body is byte-for-byte what it was** (`model`, `prompt`,
  `duration`, `resolution`, `image.url`, optional `aspect_ratio` /
  `generate_audio`). A test snapshots it; the pipeline (`video-set` and the
  four pipeline verbs) is not touched by this work.
- One of `--image`, `--last-frame`, `--reference` is required; `--image` is no
  longer required on its own.
- `<IMAGE_n>` is **0-based**: `<IMAGE_0>` is the first `--reference`. The probe's
  three-reference run tagged `<IMAGE_0>…<IMAGE_2>` and the clip picked up the
  third image's scene under `<IMAGE_2>`; the docs' prose says the same
  ("`<AUDIO_0>` … with `<IMAGE_0>` … when you also pass images") while its code
  samples write `<IMAGE_1>`. A tag outside `0..count-1`, or any tag with no
  references, is refused locally.
- Extension and editing inherit resolution and length from the input and take
  **no** `--resolution` / `--aspect-ratio` / `--model`; `video-extend` verifies
  that the returned clip is at least as long as the input (the API returns
  original + extension as one clip: 15.04 s in → 20.04 s out in the probe).
- The report (`sprite-gen-video-report`) gains `mode` and `inputs` (local paths
  by role, never URLs or tokens); every existing field stays.

## Setup — pick one credential

| `auth_source` | What you need | Billing | How to set it up |
|---|---|---|---|
| `grok-login` (default) | the `grok` CLI signed in once | your SuperGrok **Imagine quota** (no console spend) | install the grok CLI, run `grok login` (`--oauth` for a browser, `--device-auth` for a headless box). It writes `~/.grok/auth.json`; this tool only reads it. |
| `XAI_API_KEY` | an xAI console API key | console credit | `export XAI_API_KEY=xai-…` |

Resolution order is fixed for both images and videos: **the Grok subscription
login wins**, even when `XAI_API_KEY` is set. `GROK_HOME` relocates `~/.grok`.
Only when no login file exists can the configured API key use console credits.
An expired, unreadable, corrupt or API-rejected login stops the request; it never
switches to API credit. A clip that does leave on `XAI_API_KEY` says so on stderr
before it uploads — one line per submitted job, naming the duration and resolution
it is billed on; the subscription route stays silent. An empty API key is ignored when the login is usable,
but is an error when no login exists. With neither credential, the run stops
with both setup paths spelled out.

### The login token expires — and this tool does not refresh it

The grok CLI stores an OIDC access token that lasts about six hours. Any
long-lived grok session refreshes it proactively about five minutes before
`expires_at` (2026-09-13 measurement: a resident session rewrote `auth.json` at
00:15Z for a 00:20Z expiry), so the token only actually expires when no grok
process has run for hours. `sprite-gen video` reads `expires_at` **before
uploading anything**; if it has passed, the run fails with the refresh
prescription instead of gambling on a 403 mid-upload:

```
xai: the grok login token expired at 2026-09-08T10:56:34Z (now …); nothing was uploaded.
  refresh it with `grok models` run from an empty directory (e.g. `cd "$(mktemp -d)"`) — a non-agent round-trip; never a bare prompt like `grok -p …`, which starts the coding agent in your cwd — or sign in again with `grok login`. This tool never rewrites ~/.grok/auth.json itself.
```

Why `grok models` and not a prompt: `grok -p ok` is not a ping. It starts the
coding agent in the current directory, which reads it, may write files and may
call paid APIs on its own (2026-09-13: run inside a worktree it overwrote a
script and spent a video generation). `grok models` only runs the CLI's startup
auth path (`auth: silent refresh` in `~/.grok/logs/unified.jsonl`), prints the
model list and exits, writing nothing outside `~/.grok`. Run it from an empty
directory anyway. If it prints a login error instead of the model list, the
refresh token is gone too: `grok login`.

Why not refresh it here: `auth.json` is the grok CLI's file, the refresh token in
it may rotate, and a second writer would break the login the user relies on
everywhere else. The same shape as `sprite-gen gen`'s `codex login status` gate.

### Direct API transport

Grok Build's built-in `image_to_video` tool posts without `output.upload_url`, and
on Zero-Data-Retention teams the API answers `HTTP 400 — Zero Data Retention teams
must provide output.upload_url for video generation`. The same login calling
`/v1/videos/generations` directly (no `output.upload_url`) succeeds and bills the
Imagine quota (verified 2026-08-22, re-verified 2026-09-08). So this engine calls
the API itself and never routes through the agent-side tool.

## CLI

### `sprite-gen video` — generations (image-to-video, first/last frame, references)

```bash
sprite-gen video \
  [--image still.png] \                # first frame / the still to animate (PNG / JPEG / WebP, inline data URL)
  [--last-frame end.png] \             # pin the closing frame (grok-imagine-video-1.5 only); alone or with --image
  [--reference a.png --reference b.png …] \   # 1..7 references; name them in the prompt as <IMAGE_0>, <IMAGE_1>, …
  --prompt "Camera locked. Gentle idle sway, tail flick." \   # or --prompt-file
  --out clip.mp4 \
  [--duration 6]                     # 1..15 seconds (default 6)
  [--resolution 720p]                # 480p | 720p | 1080p (default 720p; 1080p is refused with --reference)
  [--aspect-ratio 1:1]               # 1:1 16:9 9:16 4:3 3:4 3:2 2:3 (default: the still's ratio)
  [--audio | --no-audio]             # default: the API's default (audio on)
  [--model grok-imagine-video-1.5]
  [--report clip.report.json]
```

At least one of `--image`, `--last-frame`, `--reference` is required. The mode is
derived from the flags (see the table above) and written to the report as `mode`.

Backward-compatible wrapper: `$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/generate_sprite_video.py …` (same args).

What happens, in order:

1. Validate the request (prompt, stills, duration, resolution, aspect ratio, the
   mode rules: ≤ 7 references, `<IMAGE_n>` tags in `0..count-1`, no tag without a
   reference, no 1080p with references, no `last_frame` / `image`+`reference` on the
   classic model) and resolve the credential — all before any network call.
2. `POST /v1/videos/generations` with `model`, `prompt`, `duration`, `resolution`,
   then `image.url` / `last_frame.url` / `reference_images[].url` as base64 data
   URLs, optional `aspect_ratio` / `generate_audio`. A `401/403` names the
   credential source and its fix; any reply without a `request_id` fails with the
   API's own error text.
3. `GET /v1/videos/{request_id}` every 4 s until `status` is `done`. `failed`,
   `expired`, a non-2xx poll, or the timeout (`SPRITE_GEN_VIDEO_TIMEOUT_SECONDS`,
   default 600) fail loudly. Nothing is written on any failure path.
4. Download `video.url`, verify the bytes start with an mp4 `ftyp` box, then move
   the file into `--out` atomically (`.part` staging).

Reference-to-video tends to lock the opening composition to a referenced scene
image (probe m3: the clip opened on the third reference's framing), so put
character references first and scene references last, or leave the scene out.

### `sprite-gen video-extend` — continue a clip

```bash
sprite-gen video-extend \
  --video clip.mp4 \                   # 2..15 s mp4 (a 15-second clip's 15.04 s container is accepted)
  --prompt "she presses the attack, camera tracks low" \
  --out longer.mp4 \
  [--duration 6]                     # seconds ADDED, 2..10 (default 6)
  [--report longer.report.json]
```

Model is fixed to the classic `grok-imagine-video` (`grok-imagine-video-1.5`
answers `400 Video extension is not supported for this model`). There are no
`--resolution` / `--aspect-ratio` / `--model` flags: the output inherits the
input's, capped at 720p. The input is checked locally (`.mp4`, `ftyp` box,
ffprobe length) before anything is uploaded; ffprobe missing is an error, not a
skipped check. The API returns **input + extension as one clip** (15.04 s in →
20.04 s out); a result shorter than the input is refused and not written. The
report carries `mode: "extend"`, `inputs.video`, `input_duration` (ffprobe
seconds) and `duration_requested` (the seconds added).

### `sprite-gen video-edit` — change a clip with a prompt

```bash
sprite-gen video-edit \
  --video clip.mp4 \                   # at most 8.7 s
  --prompt "change her hakama to solid black, keep everything else" \
  --out edited.mp4 \
  [--report edited.report.json]
```

Same fixed classic model, same inherited resolution / aspect / length (the probe's
8.00 s input came back 7.71 s — the API trims the tail). Inputs over 8.7 s are
refused locally with an ffmpeg trim hint; there is no `--duration` at all.

### The report

`sprite-gen-video-report` carries `auth_source`, `model`, `mode`, `request_id`,
`inputs` (local paths by role: `image`, `last_frame`, `reference_images`,
`video`), `bytes`, `host`, requested/reported duration (`input_duration` for
extend / edit), resolution, aspect ratio, audio flag, `elapsed_seconds`, and
`polls`. The pre-modes `image` field stays. **Tokens and download URLs are never
printed or written** — only the download host (`vidgen.x.ai`).

## Quotas and limits

- Imagine quota is a weekly SuperGrok allowance; when it is exhausted the API
  refuses the POST and the run fails with that message (no retry loop here).
- A refused request or a failed poll names the provider's code after the status,
  `(HTTP 400) code=invalid_argument`, read from a top-level `code` or the `error`
  object. `invalid_argument` covers moderation and bad parameters alike, so it is
  not called a content-policy refusal; a clip that comes back `done` with
  `video.respect_moderation: false` is:
  `<verb>: generation <id> refused by the provider's content policy (HTTP 200) respect_moderation=false; nothing was written`.
- Duration 1–15 s, resolutions 480p/720p/1080p, the seven aspect ratios above —
  from the xAI video docs as of 2026-09-08. Reference-to-video: ≤ 7 images, 720p
  max; extension: input 2–15 s, 2–10 s added; editing: input ≤ 8.7 s (docs as of
  2026-09-13). A value outside those is rejected locally before the call.
- Output is whatever the model returns (typically H.264 mp4 with audio unless
  `--no-audio`). Downstream frame extraction is a separate step and not part of
  this command.

## Related

- [video-pipeline.md](video-pipeline.md#a-clip-from-a-video-mcp-on-your-agent--zcre) — a clip your agent makes with a video MCP (ZCRE) instead of this command
- [docs/README.md](README.md) — documentation index
