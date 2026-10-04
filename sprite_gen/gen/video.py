# SPDX-License-Identifier: Apache-2.0
"""Video generation through Grok Imagine (xAI `/v1/videos/*`).

One call = one verified mp4 on disk. This is the engine module behind the
sprite-gen skill's standalone `video`, `video-extend` and `video-edit` commands:

- `video`         `POST /v1/videos/generations` — image-to-video (`--image`), a
                  pinned last frame (`--last-frame`, alone or with `--image`) and
                  reference-to-video (`--reference` x1..7, `<IMAGE_n>` tags, 0-based).
- `video-extend`  `POST /v1/videos/extensions` — continue a clip by N seconds.
- `video-edit`    `POST /v1/videos/edits` — change a clip with a prompt.

Extension and editing only work on the classic `grok-imagine-video` model (the
1.5 model answers `400 … not supported for this model`, measured 2026-09-13), take
no resolution / aspect / model flags and inherit both from the input clip. The
`--image`-only body is unchanged by the other modes; the video pipeline
(`sprite_gen/video/*`) keeps calling exactly that.

Credentials are the user's own and never live in this repository. Two sources,
resolved in a fixed order and always reported (`auth_source`):

1. the grok CLI login file `~/.grok/auth.json` (SuperGrok Imagine quota via the
   OIDC access token the CLI stored at `grok login`). `GROK_HOME` relocates it.
2. `XAI_API_KEY` - an xAI console key, only when no grok login file exists. That
   route spends metered API credit, so it says so on stderr before it uploads;
   the subscription route stays silent (구독 우선 불변식 5, 수홍 2026-09-20).

The login token expires (about six hours, 2026-09-08 실측) and the grok CLI is the
only writer of that file, so an expired token is not refreshed here: the run
stops before uploading anything and says exactly which command refreshes it.
No silent fallback between the two sources, no retry with a different tool
(Grok Build's built-in `image_to_video` is a separate client that returns
HTTP 400 on Zero-Data-Retention teams; this direct call is what works).

Truth is the mp4 bytes on disk (`ftyp` box verified), never the API's status
string. Tokens and download URLs are never printed or written to the report.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from sprite_gen.spec.runio import atomic_write_text
from . import prompt_parts, refusal
from .base import announce_api_billing
from .facing import FACINGS, validate as validate_facing
from sprite_gen.video import facing as facing_mod
from .xai import (
    API_BASE, AUTH_ENV, AUTH_SOURCE_API_KEY, AUTH_SOURCE_GROK_LOGIN,
    HTTP_TIMEOUT_SECONDS, GROK_REFRESH_COMMAND, GROK_REFRESH_WHERE, GROK_LOGIN_COMMAND,
    Credential, HttpCall, grok_home, resolve_credential, http_json,
)

DEFAULT_MODEL = "grok-imagine-video-1.5"
# Extension and editing: the API rejects the 1.5 model on both endpoints, so the
# verbs force the classic model and offer no --model flag (2026-09-13 measurement).
CLASSIC_MODEL = "grok-imagine-video"
RESOLUTIONS = ("480p", "720p", "1080p")
ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")
DURATION_MIN, DURATION_MAX = 1, 15
# docs.x.ai model-capabilities/video/reference-to-video (2026-09-13): at most 7
# references, output capped at 720p. Tags are 0-based (`<IMAGE_0>` = first
# --reference): the probe's three-reference run took `<IMAGE_0>..<IMAGE_2>` and the
# docs' prose says "<IMAGE_0> … when you also pass images"; its samples say <IMAGE_1>.
REFERENCE_MAX = 7
REFERENCE_MAX_RESOLUTION = "720p"
REFERENCE_TAG = re.compile(r"<IMAGE_(\d+)>")
# docs.x.ai model-capabilities/video/extension|editing (2026-09-13).
EXTEND_INPUT_SECONDS = (2.0, 15.0)
# A "15-second" clip's container reports 15.04 s (24 fps padding) and the API took
# it (probe m4c, 2026-09-13), so the documented ceiling gets frame-padding slack.
EXTEND_INPUT_SLACK_SECONDS = 0.1
EXTEND_DURATION_MIN, EXTEND_DURATION_MAX, EXTEND_DURATION_DEFAULT = 2, 10, 6
EDIT_INPUT_MAX_SECONDS = 8.7
MODE_IMAGE_TO_VIDEO = "image-to-video"
MODE_FIRST_LAST = "first-last"
MODE_LAST_FRAME = "last-frame"
MODE_REFERENCE = "reference"
MODE_EXTEND = "extend"
MODE_EDIT = "edit"
POLL_INTERVAL_SECONDS = 4.0
POLL_TIMEOUT_SECONDS = int(os.environ.get("SPRITE_GEN_VIDEO_TIMEOUT_SECONDS", "600"))
# Every mp4/mov starts with a size-prefixed `ftyp` box: bytes 4..8 spell it.
MP4_FTYP_OFFSET = 4
MP4_FTYP = b"ftyp"
_DONE_STATUSES = ("done", "complete", "completed")
_FAILED_STATUSES = ("failed", "error", "expired")

def http_download(url: str, token: str) -> bytes:
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as first:
        if first.code not in (401, 403):
            raise SystemExit(f"video: download failed with HTTP {first.code}") from first
        request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.read()
        except urllib.error.HTTPError as second:
            raise SystemExit(f"video: download failed with HTTP {second.code} even with the bearer token") from second
    except urllib.error.URLError as exc:
        raise SystemExit(f"video: cannot download the clip: {exc.reason}") from exc


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _error_detail(body: Any, secret: str | None = None) -> str:
    # What the provider said, with keys and URL query strings masked — the same
    # mask the image lines use (refusal.masked). Masked before it is quoted,
    # escaped or cut, so the mask sees each string as xAI sent it and a key the
    # cut would split is still whole.
    body = refusal.masked(body, secret)
    if not isinstance(body, dict):
        return str(body)[:300]
    parts = [f"{key}={body[key]!r}" for key in ("code", "error", "message", "status", "raw") if body.get(key)]
    return ", ".join(parts) if parts else json.dumps(body)[:300]


def _redact_host(url: str) -> str | None:
    if not url.startswith("http"):
        return None
    parts = url.split("/")
    return parts[2] if len(parts) > 2 else None


def verify_mp4(data: bytes) -> None:
    if len(data) < MP4_FTYP_OFFSET + len(MP4_FTYP) or data[MP4_FTYP_OFFSET : MP4_FTYP_OFFSET + len(MP4_FTYP)] != MP4_FTYP:
        raise SystemExit(
            f"video: downloaded {len(data)} bytes but they are not an mp4 (no ftyp box) — refusing to publish"
        )


def _resolved(path: Path | None) -> Path | None:
    return None if path is None else path.expanduser().resolve()


def _require_still(path: Path, flag: str) -> None:
    if not path.is_file():
        raise SystemExit(f"video: {flag} still image not found: {path}")


def probe_duration_seconds(clip: Path, *, verb: str) -> float:
    """Container duration of a local clip via ffprobe. Loud when ffprobe is missing."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise SystemExit(f"{verb}: ffprobe is required to check the input clip's length before uploading (install ffmpeg)")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
        capture_output=True, text=True,
    )
    text = proc.stdout.strip()
    if proc.returncode != 0 or not text:
        raise SystemExit(f"{verb}: ffprobe could not read {clip}: {proc.stderr.strip()[:300] or 'no duration reported'}")
    try:
        return float(text)
    except ValueError as exc:
        raise SystemExit(f"{verb}: ffprobe reported an unreadable duration {text!r} for {clip}") from exc


def _require_mp4_clip(clip: Path, *, verb: str) -> None:
    if clip.suffix.lower() != ".mp4":
        raise SystemExit(f"{verb}: --video must be an .mp4 file (the API accepts only mp4), got {clip.name!r}")
    if not clip.is_file():
        raise SystemExit(f"{verb}: input clip not found: {clip}")
    with clip.open("rb") as handle:
        head = handle.read(MP4_FTYP_OFFSET + len(MP4_FTYP))
    if len(head) < MP4_FTYP_OFFSET + len(MP4_FTYP) or head[MP4_FTYP_OFFSET:] != MP4_FTYP:
        raise SystemExit(f"{verb}: {clip} is not an mp4 (no ftyp box); refusing to upload it")


@dataclass
class VideoRequest:
    """`sprite-gen video`: one of image / last_frame / reference_images is required."""

    image: Path | None
    prompt: str
    out: Path
    duration: int = 6
    resolution: str = "720p"
    aspect_ratio: str | None = None
    model: str = DEFAULT_MODEL
    generate_audio: bool | None = None  # None = API default
    last_frame: Path | None = None
    reference_images: list[Path] = field(default_factory=list)
    direction: str | None = None
    facing: str = "right"
    facing_fix: str = "none"

    @property
    def mode(self) -> str:
        if self.reference_images:
            return MODE_REFERENCE
        if self.image is not None and self.last_frame is not None:
            return MODE_FIRST_LAST
        if self.last_frame is not None:
            return MODE_LAST_FRAME
        return MODE_IMAGE_TO_VIDEO


@dataclass
class ExtendRequest:
    """`sprite-gen video-extend`: continue `video` by `duration` seconds (classic model)."""

    video: Path
    prompt: str
    out: Path
    duration: int = EXTEND_DURATION_DEFAULT
    model: str = CLASSIC_MODEL


@dataclass
class EditRequest:
    """`sprite-gen video-edit`: change `video` as the prompt says (classic model)."""

    video: Path
    prompt: str
    out: Path
    model: str = CLASSIC_MODEL


@dataclass
class VideoResult:
    out: Path
    bytes: int
    model: str
    request_id: str
    auth_source: str
    elapsed_seconds: float
    polls: int
    duration_requested: int
    duration_reported: float | None = None
    host: str | None = None
    image: Path | None = None
    prompt: str = ""
    resolution: str = ""
    aspect_ratio: str | None = None
    generate_audio: bool | None = None
    mode: str = MODE_IMAGE_TO_VIDEO
    inputs: dict[str, Any] = field(default_factory=dict)  # role -> local path(s); never URLs
    input_duration: float | None = None  # extend / edit: ffprobe seconds of the input clip
    output_duration: float | None = None  # extend / edit: ffprobe seconds of the published clip
    extra: dict[str, Any] = field(default_factory=dict)
    facing: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "sprite-gen-video-report",
            "provider": "grok-imagine",
            "auth_source": self.auth_source,
            "model": self.model,
            "mode": self.mode,
            "request_id": self.request_id,
            "image": str(self.image) if self.image else None,
            "inputs": self.inputs,
            "prompt": self.prompt,
            "out": str(self.out),
            "bytes": self.bytes,
            "host": self.host,
            "duration_requested": self.duration_requested,
            "duration_reported": self.duration_reported,
            **({"input_duration": self.input_duration} if self.input_duration is not None else {}),
            **({"output_duration": self.output_duration} if self.output_duration is not None else {}),
            "resolution": self.resolution,
            "aspect_ratio": self.aspect_ratio,
            "generate_audio": self.generate_audio,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "polls": self.polls,
            **({"extra": self.extra} if self.extra else {}),
            **({"facing": self.facing} if self.facing is not None else {}),
        }


def _validate(request: VideoRequest) -> None:
    validate_facing(request.facing, request.facing_fix)
    if request.direction not in (None, "side", "front", "back", "front_diagonal", "back_diagonal"):
        raise SystemExit("video: --direction must be side, front, back, front_diagonal or back_diagonal")
    if request.facing_fix not in facing_mod.FIXES:
        raise SystemExit("video: --facing-fix must be mirror or none")
    if request.direction == "side" and request.mode != MODE_IMAGE_TO_VIDEO:
        raise SystemExit("video: --direction side requires a single --image, without --last-frame or --reference")
    if not request.prompt.strip():
        raise SystemExit("video: empty prompt; pass --prompt or --prompt-file")
    if request.image is None and request.last_frame is None and not request.reference_images:
        raise SystemExit("video: nothing to animate — pass --image, --last-frame and/or --reference")
    if request.image is not None:
        _require_still(request.image, "--image")
    if request.last_frame is not None:
        _require_still(request.last_frame, "--last-frame")
        if request.model == CLASSIC_MODEL:
            raise SystemExit(f"video: --last-frame needs {DEFAULT_MODEL}; the classic {CLASSIC_MODEL} rejects last_frame")
    references = request.reference_images
    tags = sorted({int(n) for n in REFERENCE_TAG.findall(request.prompt)})
    if references:
        if len(references) > REFERENCE_MAX:
            raise SystemExit(f"video: at most {REFERENCE_MAX} --reference images, got {len(references)}")
        for path in references:
            _require_still(path, "--reference")
        if request.resolution == "1080p":
            raise SystemExit(
                f"video: reference-to-video is capped at {REFERENCE_MAX_RESOLUTION}; --resolution 1080p is not available with --reference"
            )
        if request.model == CLASSIC_MODEL and request.image is not None:
            raise SystemExit(f"video: the classic {CLASSIC_MODEL} rejects --image combined with --reference; use {DEFAULT_MODEL}")
        out_of_range = [n for n in tags if n >= len(references)]
        if out_of_range:
            raise SystemExit(
                f"video: prompt tags {', '.join(f'<IMAGE_{n}>' for n in out_of_range)} but only {len(references)} --reference "
                f"image(s) were given — tags are 0-based: <IMAGE_0>..<IMAGE_{len(references) - 1}>"
            )
    elif tags:
        raise SystemExit(
            f"video: prompt mentions {', '.join(f'<IMAGE_{n}>' for n in tags)} but no --reference image was given"
        )
    if not (DURATION_MIN <= request.duration <= DURATION_MAX):
        raise SystemExit(f"video: --duration must be {DURATION_MIN}..{DURATION_MAX} seconds, got {request.duration}")
    if request.resolution not in RESOLUTIONS:
        raise SystemExit(f"video: --resolution must be one of {', '.join(RESOLUTIONS)}, got {request.resolution!r}")
    if request.aspect_ratio is not None and request.aspect_ratio not in ASPECT_RATIOS:
        raise SystemExit(f"video: --aspect-ratio must be one of {', '.join(ASPECT_RATIOS)}, got {request.aspect_ratio!r}")


def _build_generation_body(request: VideoRequest, image: Path | None, last_frame: Path | None, references: list[Path]) -> dict[str, Any]:
    # Key order matters to nobody but the snapshot test: the --image-only body is
    # exactly what it was before the other modes existed.
    body: dict[str, Any] = {
        "model": request.model,
        "prompt": request.prompt,
        "duration": request.duration,
        "resolution": request.resolution,
    }
    if image is not None:
        body["image"] = {"url": _data_url(image)}
    if last_frame is not None:
        body["last_frame"] = {"url": _data_url(last_frame)}
    if references:
        body["reference_images"] = [{"url": _data_url(path)} for path in references]
    if request.aspect_ratio:
        body["aspect_ratio"] = request.aspect_ratio
    if request.generate_audio is not None:
        body["generate_audio"] = request.generate_audio
    return body


@dataclass
class _Published:
    data: bytes
    request_id: str
    model: str | None
    duration_reported: float | None
    host: str | None
    polls: int
    elapsed_seconds: float


def _submit_poll_publish(
    *,
    verb: str,
    endpoint: str,
    body: dict[str, Any],
    out: Path,
    credential: Credential,
    call: HttpCall,
    download: Callable[[str, str], bytes],
    sleep: Callable[[float], None],
    poll_timeout: float | None,
    accept: Callable[[Path, dict[str, Any]], None] | None = None,
) -> _Published:
    """POST `body` to `endpoint`, poll `/videos/{id}`, download, verify, publish atomically.

    `accept(staged_path, final_poll)` may refuse the clip (raise SystemExit) while it is
    still the `.part` file — `video-extend` measures the staged bytes with ffprobe; the
    staged file is removed and `out` is never written.

    Every billed request leaves from here, which is why the API-credit notice lives
    here too: one line per submitted job, whichever verb asked for it."""
    if credential.source == AUTH_SOURCE_API_KEY:
        # Reaching the key means no grok login exists (resolve_credential's fixed
        # order), so this clip spends metered API credit instead of the Imagine
        # subscription quota. What sets the amount travels with the charge: Imagine
        # prices a clip by length x output size, so a 15 s 1080p clip is nothing
        # like a 3 s 480p one (`video-edit` sends neither and names neither).
        priced = ", ".join(f"{knob}={body[knob]}{'s' if knob == 'duration' else ''}"
                           for knob in ("duration", "resolution") if knob in body)
        announce_api_billing(verb, AUTH_ENV,
                             f"{f' ({priced})' if priced else ''} — "
                             f"`{GROK_LOGIN_COMMAND}` signs your Grok subscription in.")
    started = time.monotonic()
    status, reply = call("POST", f"{API_BASE}/videos/{endpoint}", credential.token, body)
    if status in (401, 403):
        raise SystemExit(
            f"{verb}: xAI rejected the credential ({credential.source}) with HTTP {status}: {_error_detail(reply, credential.token)}\n"
            + (
                f"  the grok login token may have been revoked or rotated — run `{GROK_REFRESH_COMMAND}` {GROK_REFRESH_WHERE} or `{GROK_LOGIN_COMMAND}`."
                if credential.source == AUTH_SOURCE_GROK_LOGIN
                else f"  check the {AUTH_ENV} value."
            )
        )
    request_id = reply.get("request_id") if isinstance(reply, dict) else None
    if status not in (200, 202) or not request_id:
        refused = refusal.read("grok", reply)
        raise SystemExit(
            f"{verb}: {refused.reason('generation request refused')} (HTTP {status}){refused.suffix()}: {_error_detail(reply, credential.token)}"
        )

    deadline = time.monotonic() + (POLL_TIMEOUT_SECONDS if poll_timeout is None else poll_timeout)
    polls = 0
    video_url: str | None = None
    duration_reported: float | None = None
    model_reported: str | None = None
    final: dict[str, Any] = {}
    while True:
        polls += 1
        http, poll = call("GET", f"{API_BASE}/videos/{request_id}", credential.token, None)
        poll_status = poll.get("status") if isinstance(poll, dict) else None
        if http in (200, 202) and poll_status in _DONE_STATUSES:
            final = poll
            video = poll.get("video") or {}
            # docs.x.ai videos reference: `respect_moderation` is false (and the url
            # empty) when the clip broke moderation rules — the one xAI signal that
            # is a content-policy block and nothing else. No provider code exists.
            if isinstance(video, dict) and video.get("respect_moderation") is False:
                raise SystemExit(
                    f"{verb}: generation {request_id} {refusal.CONTENT_POLICY} (HTTP {http}) "
                    "respect_moderation=false; nothing was written"
                )
            video_url = video.get("url") if isinstance(video, dict) else None
            duration_reported = video.get("duration") if isinstance(video, dict) else None
            model_reported = poll.get("model")
            break
        if http not in (200, 202) or poll_status in _FAILED_STATUSES:
            # A failed poll carries `error` as an object `{code, message}`.
            refused = refusal.read("grok", poll)
            raise SystemExit(
                f"{verb}: generation {request_id} {refused.reason(f'ended with status={poll_status!r}')} "
                f"(HTTP {http}){refused.suffix()}: {_error_detail(poll, credential.token)}"
            )
        if time.monotonic() >= deadline:
            raise SystemExit(
                f"{verb}: generation {request_id} still {poll_status!r} after {polls} polls — poll timeout; "
                "nothing was written"
            )
        sleep(POLL_INTERVAL_SECONDS)

    if not video_url:
        raise SystemExit(f"{verb}: generation {request_id} is done but reported no video url")
    data = download(video_url, credential.token)
    verify_mp4(data)
    elapsed = time.monotonic() - started

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    tmp.write_bytes(data)
    if accept is not None:
        try:
            accept(tmp, final)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, out)
    return _Published(
        data=data, request_id=str(request_id), model=model_reported, duration_reported=duration_reported,
        host=_redact_host(video_url), polls=polls, elapsed_seconds=elapsed,
    )


def side_view_prompt(prompt: str, facing: str) -> prompt_parts.Prompt:
    """`--direction side`: the prompt with the side view held for the whole clip, after whatever the prompt
    already says (2.22.0's prompt to the byte). Words in the prompt that turn the subject are noted."""
    parts = prompt_parts.Prompt(prompt)
    parts.add("side-view", f"The subject stays in exact side view, facing {facing}. No turning around.", facing=facing)
    return parts


def generate_video(
    request: VideoRequest,
    *,
    credential: Credential | None = None,
    call: HttpCall | None = None,
    download: Callable[[str, str], bytes] | None = None,
    sleep: Callable[[float], None] | None = None,
    poll_timeout: float | None = None,
) -> VideoResult:
    """Generate one clip (`/videos/generations`) and return a VideoResult. Raises SystemExit on any failure."""
    # Late-bound so a test that monkeypatches the module-level transport never
    # reaches the network through a default captured at definition time.
    call = call or http_json
    download = download or http_download
    sleep = sleep or time.sleep
    _validate(request)
    credential = credential or resolve_credential()
    out = request.out.expanduser().resolve()
    image = _resolved(request.image)
    last_frame = _resolved(request.last_frame)
    references = [path.expanduser().resolve() for path in request.reference_images]

    facing_report = None
    if request.direction == "side":
        parts = side_view_prompt(request.prompt, request.facing)
        for line in prompt_parts.note_lines(parts):
            print(f"[video] {line}", file=sys.stderr)
        request = replace(request, prompt=parts.text)
        corrected = out.with_suffix(".facing.png")
        facing_report = facing_mod.prepare_still(image, corrected, facing=request.facing,
                                                 fix=request.facing_fix, credential=credential, call=call)
        image = corrected
    body = _build_generation_body(request, image, last_frame, references)
    published = _submit_poll_publish(
        verb="video", endpoint="generations", body=body, out=out, credential=credential,
        call=call, download=download, sleep=sleep, poll_timeout=poll_timeout,
    )
    inputs: dict[str, Any] = {}
    if image is not None:
        inputs["image"] = str(image)
    if last_frame is not None:
        inputs["last_frame"] = str(last_frame)
    if references:
        inputs["reference_images"] = [str(path) for path in references]
    return VideoResult(
        out=out,
        bytes=len(published.data),
        model=published.model or request.model,
        request_id=published.request_id,
        auth_source=credential.source,
        elapsed_seconds=published.elapsed_seconds,
        polls=published.polls,
        duration_requested=request.duration,
        duration_reported=published.duration_reported,
        host=published.host,
        image=image,
        prompt=request.prompt,
        resolution=request.resolution,
        aspect_ratio=request.aspect_ratio,
        generate_audio=request.generate_audio,
        mode=request.mode,
        inputs=inputs,
        facing=facing_report,
    )


def _validate_extend(request: ExtendRequest, *, probe: Callable[[Path], float]) -> float:
    if not request.prompt.strip():
        raise SystemExit("video-extend: empty prompt; pass --prompt or --prompt-file")
    if request.model != CLASSIC_MODEL:
        raise SystemExit(f"video-extend: only the classic {CLASSIC_MODEL} supports extension (the API answers 400 for {request.model})")
    if not (EXTEND_DURATION_MIN <= request.duration <= EXTEND_DURATION_MAX):
        raise SystemExit(
            f"video-extend: --duration is the extension length, {EXTEND_DURATION_MIN}..{EXTEND_DURATION_MAX} seconds, got {request.duration}"
        )
    clip = request.video.expanduser().resolve()
    _require_mp4_clip(clip, verb="video-extend")
    seconds = probe(clip)
    low, high = EXTEND_INPUT_SECONDS
    if not (low <= seconds <= high + EXTEND_INPUT_SLACK_SECONDS):
        raise SystemExit(
            f"video-extend: the input clip is {seconds:.2f}s; the API extends clips of {low:g}..{high:g}s — trim it first"
        )
    return seconds


def extend_video(
    request: ExtendRequest,
    *,
    credential: Credential | None = None,
    call: HttpCall | None = None,
    download: Callable[[str, str], bytes] | None = None,
    sleep: Callable[[float], None] | None = None,
    poll_timeout: float | None = None,
    probe: Callable[[Path], float] | None = None,
) -> VideoResult:
    """Continue one clip (`/videos/extensions`): the result is input + `duration` seconds in one mp4."""
    call = call or http_json
    download = download or http_download
    sleep = sleep or time.sleep
    probe = probe or (lambda clip: probe_duration_seconds(clip, verb="video-extend"))
    input_seconds = _validate_extend(request, probe=probe)
    credential = credential or resolve_credential()
    out = request.out.expanduser().resolve()
    clip = request.video.expanduser().resolve()
    body: dict[str, Any] = {
        "model": request.model,
        "prompt": request.prompt,
        "duration": request.duration,
        "video": {"url": _data_url(clip)},
    }

    measured: dict[str, float] = {}

    def accept(staged: Path, final: dict[str, Any]) -> None:
        # The API returns original + extension as one clip, but its poll reports
        # `video.duration` = the seconds ADDED (5 for a 15.04 s → 20.04 s run, live
        # 2026-09-13), so the length check measures the bytes, never the status.
        seconds = probe(staged)
        measured["output"] = seconds
        if seconds + EXTEND_INPUT_SLACK_SECONDS < input_seconds:
            raise SystemExit(
                f"video-extend: the returned clip is {seconds:.2f}s for a {input_seconds:.2f}s input — not an extension; nothing was written"
            )

    published = _submit_poll_publish(
        verb="video-extend", endpoint="extensions", body=body, out=out, credential=credential,
        call=call, download=download, sleep=sleep, poll_timeout=poll_timeout, accept=accept,
    )
    return VideoResult(
        out=out, bytes=len(published.data), model=published.model or request.model, request_id=published.request_id,
        auth_source=credential.source, elapsed_seconds=published.elapsed_seconds, polls=published.polls,
        duration_requested=request.duration, duration_reported=published.duration_reported, host=published.host,
        prompt=request.prompt, mode=MODE_EXTEND, inputs={"video": str(clip)}, input_duration=input_seconds,
        output_duration=measured.get("output"),
    )


def _validate_edit(request: EditRequest, *, probe: Callable[[Path], float]) -> float:
    if not request.prompt.strip():
        raise SystemExit("video-edit: empty prompt; pass --prompt or --prompt-file")
    if request.model != CLASSIC_MODEL:
        raise SystemExit(f"video-edit: only the classic {CLASSIC_MODEL} supports editing (the API answers 400 for {request.model})")
    clip = request.video.expanduser().resolve()
    _require_mp4_clip(clip, verb="video-edit")
    seconds = probe(clip)
    if seconds > EDIT_INPUT_MAX_SECONDS:
        raise SystemExit(
            f"video-edit: the input clip is {seconds:.2f}s; the API edits clips of at most {EDIT_INPUT_MAX_SECONDS}s — trim it first "
            f"(e.g. ffmpeg -i in.mp4 -t 8 -c copy in-8s.mp4)"
        )
    return seconds


def edit_video(
    request: EditRequest,
    *,
    credential: Credential | None = None,
    call: HttpCall | None = None,
    download: Callable[[str, str], bytes] | None = None,
    sleep: Callable[[float], None] | None = None,
    poll_timeout: float | None = None,
    probe: Callable[[Path], float] | None = None,
) -> VideoResult:
    """Edit one clip (`/videos/edits`); length, aspect and resolution come from the input."""
    call = call or http_json
    download = download or http_download
    sleep = sleep or time.sleep
    probe = probe or (lambda clip: probe_duration_seconds(clip, verb="video-edit"))
    input_seconds = _validate_edit(request, probe=probe)
    credential = credential or resolve_credential()
    out = request.out.expanduser().resolve()
    clip = request.video.expanduser().resolve()
    body: dict[str, Any] = {
        "model": request.model,
        "prompt": request.prompt,
        "video": {"url": _data_url(clip)},
    }
    measured: dict[str, float] = {}

    def accept(staged: Path, final: dict[str, Any]) -> None:
        # No length rule for edits (the API trims the tail: 8.00 s in → 7.71 s out),
        # but the report says what came back, measured from the bytes.
        measured["output"] = probe(staged)

    published = _submit_poll_publish(
        verb="video-edit", endpoint="edits", body=body, out=out, credential=credential,
        call=call, download=download, sleep=sleep, poll_timeout=poll_timeout, accept=accept,
    )
    return VideoResult(
        out=out, bytes=len(published.data), model=published.model or request.model, request_id=published.request_id,
        auth_source=credential.source, elapsed_seconds=published.elapsed_seconds, polls=published.polls,
        duration_requested=0, duration_reported=published.duration_reported, host=published.host,
        prompt=request.prompt, mode=MODE_EDIT, inputs={"video": str(clip)}, input_duration=input_seconds,
        output_duration=measured.get("output"),
    )


def _print_json(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:
        sys.stdout.write(text)


def _add_prompt_and_report(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--out", required=True, type=Path, help="destination .mp4")
    parser.add_argument("--report", type=Path, help="write the sprite-gen-video-report JSON here")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--direction", choices=("side", "front", "back", "front_diagonal", "back_diagonal"), help="sprite view; side opts into facing inspection before video generation")
    parser.add_argument("--facing", choices=FACINGS, default="right", help="with --direction side: required direction (default right)")
    parser.add_argument("--facing-fix", choices=facing_mod.FIXES, default="none", help="with --direction side: record only (none, default), or opt into mirror")
    parser.add_argument("--image", type=Path, help="the still to animate / the first frame (PNG/JPEG/WebP)")
    parser.add_argument("--last-frame", type=Path, help=f"pin the closing frame ({DEFAULT_MODEL} only); alone or with --image")
    parser.add_argument(
        "--reference", dest="reference_images", action="append", type=Path, default=None, metavar="IMAGE",
        help=f"reference image, repeatable up to {REFERENCE_MAX}; refer to them in the prompt as <IMAGE_0>, <IMAGE_1>, … "
             f"({DEFAULT_MODEL} only, {REFERENCE_MAX_RESOLUTION} max)",
    )
    _add_prompt_and_report(parser)
    parser.add_argument("--duration", type=int, default=6, help=f"seconds, {DURATION_MIN}..{DURATION_MAX} (default 6)")
    parser.add_argument("--resolution", choices=RESOLUTIONS, default="720p")
    parser.add_argument("--aspect-ratio", choices=ASPECT_RATIOS, default=None, help="default: the still's own ratio")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    audio = parser.add_mutually_exclusive_group()
    audio.add_argument("--audio", dest="generate_audio", action="store_true", default=None, help="ask for generated audio")
    audio.add_argument("--no-audio", dest="generate_audio", action="store_false", help="silent clip")


def add_extend_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--video", required=True, type=Path, help=f"the mp4 to continue ({EXTEND_INPUT_SECONDS[0]:g}..{EXTEND_INPUT_SECONDS[1]:g} s)")
    _add_prompt_and_report(parser)
    parser.add_argument(
        "--duration", type=int, default=EXTEND_DURATION_DEFAULT,
        help=f"seconds to ADD, {EXTEND_DURATION_MIN}..{EXTEND_DURATION_MAX} (default {EXTEND_DURATION_DEFAULT}); "
             f"resolution and aspect ratio are inherited from the input (no flags), model is {CLASSIC_MODEL}",
    )


def add_edit_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--video", required=True, type=Path, help=f"the mp4 to edit (at most {EDIT_INPUT_MAX_SECONDS} s)")
    _add_prompt_and_report(parser)


def _read_prompt(args: argparse.Namespace) -> str:
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
    return (prompt or "").strip()


def _publish_report(args: argparse.Namespace, result: VideoResult) -> int:
    payload = result.to_dict()
    if args.report:
        report_path = Path(args.report).expanduser().resolve()
        atomic_write_text(report_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        payload["report"] = str(report_path)
    _print_json(payload)
    return 0


def _run(args: argparse.Namespace) -> int:
    request = VideoRequest(
        image=args.image,
        prompt=_read_prompt(args),
        out=args.out,
        duration=args.duration,
        resolution=args.resolution,
        aspect_ratio=args.aspect_ratio,
        model=args.model,
        generate_audio=args.generate_audio,
        last_frame=args.last_frame,
        reference_images=list(args.reference_images or []),
        direction=args.direction, facing=args.facing, facing_fix=args.facing_fix,
    )
    return _publish_report(args, generate_video(request))


def _run_extend(args: argparse.Namespace) -> int:
    request = ExtendRequest(video=args.video, prompt=_read_prompt(args), out=args.out, duration=args.duration)
    return _publish_report(args, extend_video(request))


def _run_edit(args: argparse.Namespace) -> int:
    request = EditRequest(video=args.video, prompt=_read_prompt(args), out=args.out)
    return _publish_report(args, edit_video(request))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprite-gen video", description=__doc__)
    add_arguments(parser)
    return parser


def _build_extend_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprite-gen video-extend", description=f"Continue a clip by N seconds via {CLASSIC_MODEL} (POST /v1/videos/extensions).")
    add_extend_arguments(parser)
    return parser


def _build_edit_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprite-gen video-edit", description=f"Edit a clip with a prompt via {CLASSIC_MODEL} (POST /v1/videos/edits).")
    add_edit_arguments(parser)
    return parser


def _run_kwargs(parser: argparse.ArgumentParser, runner: Callable[[argparse.Namespace], int], kwargs: dict[str, object]) -> int:
    known = {action.dest for action in parser._actions if action.dest != "help"}
    unexpected = set(kwargs) - known
    if unexpected:
        raise TypeError(f"unexpected keyword argument(s): {', '.join(sorted(unexpected))}")
    namespace = argparse.Namespace(**{dest: kwargs.get(dest, parser.get_default(dest)) for dest in known})
    return runner(namespace)


def run(**kwargs: object) -> int:
    return _run_kwargs(_build_parser(), _run, kwargs)


def run_extend(**kwargs: object) -> int:
    return _run_kwargs(_build_extend_parser(), _run_extend, kwargs)


def run_edit(**kwargs: object) -> int:
    return _run_kwargs(_build_edit_parser(), _run_edit, kwargs)


def main(argv: list[str] | None = None) -> int:
    return _run(_build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
