# `sprite-gen video` — image to video with your own Grok login (engine SSoT)

> Owns: `sprite-gen video` / `video-extend` / `video-edit` / `video-resume`: stills and clips to mp4 through Grok Imagine with the user's own credential · Index: [docs/README.md](README.md)

`sprite-gen video` animates one still into a short mp4 through **Grok Imagine**
(xAI `POST /v1/videos/generations`). It is the video counterpart of
[`sprite-gen gen`](gen.md): one call = one still (+ prompt) → one **verified** mp4
on disk plus a machine-readable report.

The same engine also pins a **last frame**, guides a clip with **reference
images**, **extends** an existing clip (`video-extend`), **edits** one with a
prompt (`video-edit`) and **asks again** for a generation a run left unfinished,
by its `request_id` (`video-resume`).

No credential is shipped with this repository. You bring your own, in one of two
forms, and every run reports which one it used.

## Modes — which flags call what

| Verb / flags | Endpoint · model | Body fields | Options allowed | Local pre-checks (before any call) | `mode` |
|---|---|---|---|---|---|
| `video --image A` | `POST /v1/videos/generations` · `grok-imagine-video-1.5` (`--model` free) | `image{url}` | duration 1–15, resolution, aspect, audio | still exists | `image-to-video` |
| `video --image A --last-frame B` | same · 1.5 only | `image{url}`, `last_frame{url}` | same | both stills exist; classic `grok-imagine-video` rejected | `first-last` |
| `video --last-frame B` | same · 1.5 only | `last_frame{url}` | same | classic rejected | `last-frame` |
| `video --reference X [--reference Y …]` (+ optional `--image`) | same · 1.5 (classic accepts references alone but not `--image` + references) | `reference_images:[{url}…]` (+ `image{url}`) | duration, aspect, audio; resolution **480p/720p only** | 1–7 references; prompt tags `<IMAGE_n>` with `0 ≤ n < count` (0-based); `--resolution 1080p` rejected; classic + `--image` + references rejected | `reference` |
| `video-extend --video in.mp4 --duration N` | `POST /v1/videos/extensions` · **`grok-imagine-video` forced** | `video{url}`, `duration` | duration 2–10 (extension length only; default 6). No resolution / aspect / model flags: output inherits the input's, capped at 720p | `.mp4` with an `ftyp` box; ffprobe duration 2–15 s | `extend` |
| `video-edit --video in.mp4` | `POST /v1/videos/edits` · **`grok-imagine-video` forced** | `video{url}` | none of duration / resolution / aspect / model: output inherits the input's, capped at 720p | `.mp4` with an `ftyp` box; ffprobe duration ≤ 8.7 s | `edit` |
| `video-resume --request-id <id>` | **no POST** — `GET /v1/videos/{id}` only; the model is whatever the request was made with | — | none: the request is the one an earlier run submitted | the id is letters, digits, `.`, `_`, `-` | `resume` |

Invariants:

- `<IMAGE_n>` is **0-based**: `<IMAGE_0>` is the first `--reference`.

## Setup — pick one credential

| `auth_source` | What you need | Billing | How to set it up |
|---|---|---|---|
| `grok-login` (default) | the `grok` CLI signed in once | your SuperGrok **Imagine quota** (no console spend) | install the grok CLI, run `grok login` (`--oauth` for a browser, `--device-auth` for a headless box). It writes `~/.grok/auth.json`; this tool only reads it. |
| `XAI_API_KEY` | an xAI console API key | console credit | `export XAI_API_KEY=xai-…` |

Resolution order is fixed for both images and videos: **the Grok subscription
login wins**, even when `XAI_API_KEY` is set. `GROK_HOME` relocates `~/.grok`.
Only when no login file exists can the configured API key use console credits.
An expired, unreadable, corrupt or API-rejected login stops the request; it never
switches to API credit.
`SPRITE_GEN_LOGINS=off` reads no login and runs on `XAI_API_KEY` alone.
`SPRITE_GEN_XAI_BASE_URL` sends the requests, polls and the clip's download through
another address ([provider addresses](gen.md#provider-addresses) and
[subscription logins off](gen.md#subscription-logins-off) in docs/gen.md).

### The login token expires — and this tool does not refresh it

`sprite-gen video` reads `expires_at` **before
uploading anything**; if it has passed, the run fails with the refresh
prescription instead of gambling on a 403 mid-upload:

```
xai: the grok login token expired at 2026-09-08T10:56:34Z (now …); nothing was uploaded.
  refresh it with `grok models` run from an empty directory (e.g. `cd "$(mktemp -d)"`) — a non-agent round-trip; never a bare prompt like `grok -p …`, which starts the coding agent in your cwd — or sign in again with `grok login`. This tool never rewrites ~/.grok/auth.json itself.
```

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

Reference-to-video tends to lock the opening composition to a referenced scene
image, so put
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

The API returns **input + extension as one clip**.

### `sprite-gen video-edit` — change a clip with a prompt

```bash
sprite-gen video-edit \
  --video clip.mp4 \                   # at most 8.7 s
  --prompt "change her hakama to solid black, keep everything else" \
  --out edited.mp4 \
  [--report edited.report.json]
```

### `sprite-gen video-resume` — ask again for an unfinished generation

```bash
sprite-gen video-resume \
  --request-id <id> \                  # the request_id an unfinished run named
  --out clip.mp4 \
  [--report clip.report.json]
```

Run it on the credential that submitted
the request — the unfinished report's `auth_source`; refresh an expired login first.

- Cut short again — the timeout, a rejected credential, or a download that failed —
  it is unfinished on the same id, `verb: "video-resume"` (below), and can be resumed
  again. Each resume polls afresh, so a clip whose download failed is fetched from the
  URL the new poll names.

### A picture shown again

A provider sometimes repeats a frame: two frames in a row show one picture, and the
motion stalls there. `video`, `video-extend`, `video-edit` and `video-resume` read the
clip they publish for that, frame by frame with ffmpeg, as video-loop reads its cells
(docs/loop-repair.md section 7): a flat canvas filmed round the subject (the commonest
colour of a 4 px border, if it is half the border or more) is not read, so the subject's
pixels decide; a scene filmed whole is read on every pixel. The clip is published as
the provider made it; nothing is filled. A repeated frame is named on stderr,

```
video: warning: clip.mp4 shows one picture on frames 40-41 (143/144 poses/frames): the clip repeats a frame there, kept as the provider made it; video-loop makes such cells again between the poses beside them (docs/loop-repair.md section 7)
```

and the report records the reading as `held`: `read`, `frames`, `poses`, `cells`,
`background` (the canvas colour, or `null`), the thresholds read with (`pixel_same`,
`changed_max`, `hold_max`) and `held_runs` (`start`, `length`). The clip's opening and
closing pose and a run longer than a drawing on threes (`hold_max`, 3) are recorded with
a `why` and not warned of — a still at either end, or a hold the motion means. With no
ffmpeg, or a file ffmpeg cannot read, `held` is `{"read": false, "why": …}` and the run
says its frames were not read; the clip is still published.
A clip whose every frame is the canvas alone (no pixel left once the canvas colour is
taken out) is not read either: `held` is `read` false, with `frames`, `background` and a
`why` (for an 8-frame clip: `no frame of the 8 shows a subject: every pixel is within 0.12
of the colour it is filmed on (...)`), and the warning line says its frames were not read
for a picture shown again; the clip is still published.

### An unfinished generation

xAI accepted the request and the run ended before it did — the poll timeout came with
its polls still pending, refused or unanswered (or before any poll), xAI rejected the
credential on a poll, or the poll said done and the clip could not be downloaded in
time or at all, or the poll named no URL to download it from. It may
finish and be billed — a clip that is done is made — so the run ends naming the
request and the `video-resume` command that asks for it again, never as a refusal to
send again:

```
video: generation <id> was accepted, but its polls were refused (last answer 429) until the poll timeout (600s, 14 polls); it may still finish and be billed — ask for request_id=<id> again with `sprite-gen video-resume --request-id <id> --out /abs/clip.mp4` instead of submitting a new request; nothing was written
video: generation <id> was accepted, but its polls got no answer (last: timed out) until the poll timeout (600s, 14 polls); it may still finish and be billed — ask for request_id=<id> again with `…` instead of submitting a new request; nothing was written
video: generation <id> still 'pending' after 150 polls — poll timeout; it may still finish and be billed — ask for request_id=<id> again with `…` instead of submitting a new request; nothing was written
video: generation <id> was accepted after the poll timeout (600s) had run out, before any poll; it may still finish and be billed — ask for request_id=<id> again with `…` instead of submitting a new request; nothing was written
video: generation <id> was accepted, but xAI rejected the credential (grok-login) on poll 30 (answer 401); it may still finish and be billed — refresh the login with `grok models` run from an empty directory (e.g. `cd "$(mktemp -d)"`) or `grok login`, then ask for request_id=<id> again with `…` instead of submitting a new request; nothing was written
video: generation <id> is done, but its download got no answer (last: IncompleteRead(1048576 bytes read, 3195904 more expected)) after 3 attempts; the clip is made and may be billed — ask for request_id=<id> again with `…` instead of submitting a new request; nothing was written
video: generation <id> is done, but its download was refused (last answer 503) after 3 attempts; the clip is made and may be billed — ask for request_id=<id> again with `…` instead of submitting a new request; nothing was written
video: generation <id> is done, but its download was refused (answer 404); the clip is made and may be billed — ask for request_id=<id> again with `…` instead of submitting a new request; nothing was written
video: generation <id> is done, but its download got no answer (last: timed out) until the poll timeout (210s, 1 attempt); the clip is made and may be billed — ask for request_id=<id> again with `…` instead of submitting a new request; nothing was written
video: generation <id> is done, but its poll named no video url to download; the clip is made and may be billed — ask for request_id=<id> again with `…` instead of submitting a new request; nothing was written
video: generation <id> is done, but its download brought 34 bytes that are not an mp4 (no ftyp box); the clip is made and may be billed — ask for request_id=<id> again with `…` instead of submitting a new request; nothing was written
```

A POST that gets no answer it can read is not unfinished: no `request_id` could be
read, so it ends at once and is never sent again. Its line is the transport's own
(`xai: request failed or timed out; no automatic retry (the server may have accepted
it)`), or `xai: response was not valid JSON` for a whole answer that is not JSON.

## Quotas and limits

- Imagine quota is a weekly SuperGrok allowance; when it is exhausted the API
  refuses the POST and the run fails with that message.
- A refused request or a failed poll names the provider's code after the status,
  `(HTTP 400) code=invalid_argument`, read from a top-level `code` or the `error`
  object. `invalid_argument` covers moderation and bad parameters alike, so it is
  not called a content-policy refusal; a clip that comes back `done` with
  `video.respect_moderation: false` is:
  `<verb>: generation <id> refused by the provider's content policy (HTTP 200) respect_moderation=false; nothing was written`.
- Output is whatever the model returns (typically H.264 mp4 with audio unless
  `--no-audio`). It is read for a picture shown again (above) and published as it
  came; downstream frame extraction is a separate step and not part of this command.

## Related

- [video-pipeline.md](video-pipeline.md#a-clip-from-a-video-mcp-on-your-agent--zcre) — a clip your agent makes with a video MCP (ZCRE) instead of this command
