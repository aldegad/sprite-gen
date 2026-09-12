# `sprite-gen video` — image to video with your own Grok login (engine SSoT)

> Owns: `sprite-gen video`: image to mp4 through Grok Imagine with the user's own credential · Index: [docs/README.md](README.md)

`sprite-gen video` animates one still into a short mp4 through **Grok Imagine**
(xAI `POST /v1/videos/generations`). It is the video counterpart of
[`sprite-gen gen`](gen.md): one call = one still (+ prompt) → one **verified** mp4
on disk plus a machine-readable report. The sprite-gen skill routes standalone
video requests here from any agent engine.

No credential is shipped with this repository. You bring your own, in one of two
forms, and every run reports which one it used.

## Setup — pick one credential

| `auth_source` | What you need | Billing | How to set it up |
|---|---|---|---|
| `grok-login` (default) | the `grok` CLI signed in once | your SuperGrok **Imagine quota** (no console spend) | install the grok CLI, run `grok login` (`--oauth` for a browser, `--device-auth` for a headless box). It writes `~/.grok/auth.json`; this tool only reads it. |
| `XAI_API_KEY` | an xAI console API key | console credit | `export XAI_API_KEY=xai-…` |

Resolution order is fixed for both images and videos: **the Grok subscription
login wins**, even when `XAI_API_KEY` is set. `GROK_HOME` relocates `~/.grok`.
Only when no login file exists can the configured API key use console credits.
An expired, unreadable, corrupt or API-rejected login stops the request; it never
switches to API credit. An empty API key is ignored when the login is usable,
but is an error when no login exists. With neither credential, the run stops
with both setup paths spelled out.

### The login token expires — and this tool does not refresh it

The grok CLI stores an OIDC access token that lasts about six hours
(2026-09-08 measurement: `expires_at` 10:56Z, refreshed to 18:26Z by the next grok
command). `sprite-gen video` reads `expires_at` **before uploading anything**; if
it has passed, the run fails with the refresh prescription instead of gambling on
a 403 mid-upload:

```
xai: the grok login token expired at 2026-09-08T10:56:34Z (now …); nothing was uploaded.
  refresh it with any grok CLI command that reaches the API, e.g. `grok -p ok --output-format plain`,
  or sign in again with `grok login`. This tool never rewrites ~/.grok/auth.json itself.
```

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

```bash
sprite-gen video \
  --image still.png \                # PNG / JPEG / WebP; sent inline as a data URL
  --prompt "Camera locked. Gentle idle sway, tail flick." \   # or --prompt-file
  --out clip.mp4 \
  [--duration 6]                     # 1..15 seconds (default 6)
  [--resolution 720p]                # 480p | 720p | 1080p (default 720p)
  [--aspect-ratio 1:1]               # 1:1 16:9 9:16 4:3 3:4 3:2 2:3 (default: the still's ratio)
  [--audio | --no-audio]             # default: the API's default (audio on)
  [--model grok-imagine-video-1.5]
  [--report clip.report.json]
```

Backward-compatible wrapper: `$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/generate_sprite_video.py …` (same args).

What happens, in order:

1. Validate the request (prompt, still, duration, resolution, aspect ratio) and
   resolve the credential — all before any network call.
2. `POST /v1/videos/generations` with `model`, `prompt`, `duration`, `resolution`,
   optional `aspect_ratio` / `generate_audio`, and `image.url` as a base64 data URL.
   A `401/403` names the credential source and its fix; any reply without a
   `request_id` fails with the API's own error text.
3. `GET /v1/videos/{request_id}` every 4 s until `status` is `done`. `failed`,
   `expired`, a non-2xx poll, or the timeout (`SPRITE_GEN_VIDEO_TIMEOUT_SECONDS`,
   default 600) fail loudly. Nothing is written on any failure path.
4. Download `video.url`, verify the bytes start with an mp4 `ftyp` box, then move
   the file into `--out` atomically (`.part` staging).

The report (`sprite-gen-video-report`) carries `auth_source`, `model`,
`request_id`, `bytes`, `host`, requested/reported duration, resolution, aspect
ratio, audio flag, `elapsed_seconds`, and `polls`. **Tokens and download URLs are
never printed or written** — only the download host (`vidgen.x.ai`).

## Quotas and limits

- Imagine quota is a weekly SuperGrok allowance; when it is exhausted the API
  refuses the POST and the run fails with that message (no retry loop here).
- Duration 1–15 s, resolutions 480p/720p/1080p, the seven aspect ratios above —
  from the xAI video docs as of 2026-09-08. A value outside those is rejected
  locally before the call.
- Output is whatever the model returns (typically H.264 mp4 with audio unless
  `--no-audio`). Downstream frame extraction is a separate step and not part of
  this command.

## Related

- [docs/README.md](README.md) — documentation index
