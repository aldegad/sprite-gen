# SPDX-License-Identifier: Apache-2.0
"""Video generation through Grok Imagine (xAI `/v1/videos/*`).

One call = one verified mp4 on disk. This is the engine module behind the
sprite-gen skill's standalone `video`, `video-extend`, `video-edit` and `video-resume`
commands:

- `video`         `POST /v1/videos/generations` — image-to-video (`--image`), a
                  pinned last frame (`--last-frame`, alone or with `--image`) and
                  reference-to-video (`--reference` x1..7, `<IMAGE_n>` tags, 0-based).
- `video-extend`  `POST /v1/videos/extensions` — continue a clip by N seconds.
- `video-edit`    `POST /v1/videos/edits` — change a clip with a prompt.
- `video-resume`  no POST — ask again for a generation an earlier run left unfinished,
                  by the `request_id` it named; nothing is bought twice.

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
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from sprite_gen.spec.runio import atomic_write_text, refuse_overlap
from sprite_gen.util.cli_kwargs import namespace_from_kwargs
from . import address, prompt_parts, refusal, retry
from . import handedness as handed_mod
from .base import PROVIDER_GROK, announce_api_billing, print_json
from .facing import FACINGS, validate as validate_facing
from sprite_gen.video import facing as facing_mod
from .xai import (
    AUTH_ENV, AUTH_SOURCE_API_KEY, AUTH_SOURCE_GROK_LOGIN,
    HTTP_TIMEOUT_SECONDS, GROK_REFRESH_COMMAND, GROK_REFRESH_WHERE, GROK_LOGIN_COMMAND,
    NO_ANSWER, Credential, HttpCall, TransportError, resolve_credential, http_json, no_answer, post,
    read_whole, rejected_fix, time_left,
)
from sprite_gen.util.console import utf8_streams

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
MODE_RESUME = "resume"
# What `video-resume` takes for an id: it goes into the `/videos/{id}` path, so no `/`, `?`, `#` or `%`.
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
POLL_INTERVAL_SECONDS = 4.0
# The poll timeout is the one time limit of a request: it counts from the POST's first send (a resume: its
# first poll) and bounds the waits before a refused POST goes again (`retry`), the polls, their waits and
# the clip's downloads alike. Each poll and download reads only what is left of it (xai.read_whole), and a
# send, poll or download whose wait would reach it, or woke at it or past it, is not asked (`retry.too_late`,
# read before the wait and again after it): the run ends on the refusal, or unfinished on the request_id.
# So a caller that stops the run at a hard limit gets its line back when it sets the timeout under that
# limit. The POST is not cut by it — its answer is the one way to the id; an answer that comes after the
# whole timeout ends the run at once.
POLL_TIMEOUT_SECONDS = int(os.environ.get("SPRITE_GEN_VIDEO_TIMEOUT_SECONDS", "600"))
# A poll xAI throttles (429) or fails on its own side (5xx, `retry.GET_AGAIN`), or one that gets no answer
# it can read (the connection failed or timed out, the answer broke on the way or was not JSON —
# xai.TransportError), is asked again, on the same request: the POST was accepted, so a new submission
# would buy the clip twice, and a GET buys nothing. The wait doubles while the polls fail
# (`retry.backoff`, the wait every direct provider call asks again after: 8 s, 16 s, … up to a minute).
# A clip the poll says is done is made, and may be billed: its request_id is the one way back to it. A
# download that gets no answer it can read, or that the clip's host refuses with one of
# `retry.GET_AGAIN`, is downloaded again after the same doubling wait (8 s, then 16 s), up to
# DOWNLOAD_ATTEMPTS downloads in all, within the poll timeout. One still failing, one refused with
# another status, bytes that are not an mp4, or a done poll that names no URL to download, end the run
# unfinished on that request_id: `video-resume` polls it again for a fresh URL, where a new submission
# would buy the clip twice.
DOWNLOAD_ATTEMPTS = 3
# Every mp4/mov starts with a size-prefixed `ftyp` box: bytes 4..8 spell it.
MP4_FTYP_OFFSET = 4
MP4_FTYP = b"ftyp"
_DONE_STATUSES = ("done", "complete", "completed")
_FAILED_STATUSES = ("failed", "error", "expired")


class GenerationUnfinished(SystemExit):
    """xAI accepted the request (it may bill it) and the run ended before it did: the poll timeout
    came, xAI rejected the credential on a poll, or the clip it made could not be downloaded (or its
    done poll named no URL to download it from).

    The line names `request_id=<id>` and the `video-resume` command that asks for it again, and
    carries no `(HTTP n)`, a refusal line's status: a caller that reads `(HTTP 429)` there sends the
    request again, unbilled only when it was refused. `to_dict()` is the report the CLI leaves at
    `--report`."""

    def __init__(self, message: str, *, verb: str, request_id: str, auth_source: str, model: Any, out: Path,
                 polls: int, polls_refused: int, polls_unanswered: int, last_http: int | None, last_status: Any,
                 last_error: str | None, elapsed_seconds: float, downloads: int = 0,
                 last_download: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.verb = verb
        self.request_id = request_id
        self.auth_source = auth_source
        self.model = model
        self.out = out
        self.polls = polls
        self.polls_refused = polls_refused
        self.polls_unanswered = polls_unanswered
        self.last_http = last_http
        self.last_status = last_status
        self.last_error = last_error  # what the transport said when the last poll got no answer it could read
        self.elapsed_seconds = elapsed_seconds
        self.downloads = downloads  # downloads of a clip the poll said is done; 0 when none was
        self.last_download = last_download  # how the last of them failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "sprite-gen-video-unfinished",
            "provider": "grok-imagine",
            "verb": self.verb,
            "auth_source": self.auth_source,
            "model": self.model,
            "request_id": self.request_id,
            "out": str(self.out),
            "polls": self.polls,
            "polls_refused": self.polls_refused,
            "polls_unanswered": self.polls_unanswered,
            "last_poll": {"http": self.last_http, "status": self.last_status,
                          **({"error": self.last_error} if self.last_error is not None else {})},
            **({"downloads": self.downloads, "last_download": self.last_download} if self.downloads else {}),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


# How a clip is downloaded: `(url, token, *, deadline)` -> its bytes, read whole by the deadline (http_download).
Download = Callable[..., bytes]


class DownloadRefused(SystemExit):
    """The clip's host answered its download with an error `status` (a 401 or 403 once more with the bearer token)."""

    def __init__(self, status: int, line: str) -> None:
        super().__init__(line)
        self.status = status


def http_download(url: str, token: str, *, deadline: float | None = None) -> bytes:
    """The clip's bytes, read whole — by `deadline` (a `time.monotonic()` instant) when one is given: each
    wait is cut to what is left of it (xai.read_whole). An error status raises DownloadRefused; a download
    that gets no answer it can read whole — the connection failed or timed out, the body broke on the way,
    the deadline came — raises xai.TransportError, its words in `reason`. The clip is fetched through the xAI base
    when SPRITE_GEN_XAI_BASE_URL is set (`address.fetched`), else from `url` itself."""
    request = urllib.request.Request(address.fetched(address.XAI, url))
    try:
        try:
            with urllib.request.urlopen(request, timeout=time_left(HTTP_TIMEOUT_SECONDS, deadline)) as response:
                return read_whole(response, HTTP_TIMEOUT_SECONDS, deadline)
        except urllib.error.HTTPError as first:
            if first.code not in (401, 403):
                raise DownloadRefused(first.code, f"video: download failed with HTTP {first.code}") from first
            request.add_header("Authorization", f"Bearer {token}")
            try:
                with urllib.request.urlopen(request, timeout=time_left(HTTP_TIMEOUT_SECONDS, deadline)) as response:
                    return read_whole(response, HTTP_TIMEOUT_SECONDS, deadline)
            except urllib.error.HTTPError as second:
                raise DownloadRefused(
                    second.code, f"video: download failed with HTTP {second.code} even with the bearer token"
                ) from second
    except NO_ANSWER as exc:
        reason = no_answer(exc).reason
        raise TransportError(reason, f"video: cannot download the clip: {reason}") from exc


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


def _is_mp4(data: bytes) -> bool:
    return data[MP4_FTYP_OFFSET : MP4_FTYP_OFFSET + len(MP4_FTYP)] == MP4_FTYP


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
    if not _is_mp4(head):
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
class ResumeRequest:
    """`sprite-gen video-resume`: a generation xAI already accepted, by the request_id an unfinished run named."""

    request_id: str
    out: Path


@dataclass
class VideoResult:
    out: Path
    bytes: int
    model: str | None  # video-resume: what the poll reported, if it did
    request_id: str
    auth_source: str
    elapsed_seconds: float
    polls: int
    duration_requested: int | None  # video-resume: None, this run requested nothing
    duration_reported: float | None = None
    polls_refused: int = 0  # polls xAI answered 429 or 5xx, each asked again
    polls_unanswered: int = 0  # polls that got no answer they could read (xai.TransportError), each asked again
    downloads: int = 1  # downloads of the clip, the last one whole; more than one when one failed
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
    held: dict[str, Any] | None = None  # the published clip read for a picture shown again (`read_held`), by the CLI

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
            "polls_refused": self.polls_refused,
            "polls_unanswered": self.polls_unanswered,
            "downloads": self.downloads,
            **({"extra": self.extra} if self.extra else {}),
            **({"facing": self.facing} if self.facing is not None else {}),
            **({"held": self.held} if self.held is not None else {}),
        }


# A clip's frames read for a picture shown again (`read_held`). The canvas `video-canvas` films on is one flat colour: the
# commonest colour of the first frame's border (BORDER_PX wide, read in eighths of each channel), where BACKGROUND_SHARE
# or more of the border lies within BACKGROUND_TOL of it (each channel, 0..1), is the background, and a frame's pixels
# within BACKGROUND_TOL of it are not the subject. A clip with no such border (a scene filmed whole) is read on every pixel.
BORDER_PX = 4
BACKGROUND_TOL = 0.12
BACKGROUND_SHARE = 0.5


def _background(rgba: Any) -> list[float] | None:
    """The flat colour the clip is filmed on, read on its first frame's border (see above), or None."""
    from sprite_gen._deps import np

    b = BORDER_PX
    ring = np.concatenate([rgba[:b].reshape(-1, 4), rgba[-b:].reshape(-1, 4), rgba[:, :b].reshape(-1, 4),
                           rgba[:, -b:].reshape(-1, 4)])[:, :3].astype(np.float32) / 255
    bins = np.minimum(np.floor(ring * 8).astype(int), 7)
    codes = bins[:, 0] * 64 + bins[:, 1] * 8 + bins[:, 2]
    colour = np.median(ring[codes == int(np.bincount(codes).argmax())], axis=0)
    share = float((np.abs(ring - colour).max(axis=1) <= BACKGROUND_TOL).mean())
    return [round(float(c), 4) for c in colour] if share >= BACKGROUND_SHARE else None


def read_held(clip: Path) -> dict[str, Any]:
    """The published clip's frames read for a picture shown again, as `video-loop` reads a cut (`held_fill.same_steps`,
    docs/loop-repair.md section 7): each step of two frames on the subject (the frame less the canvas it is filmed on,
    `_background`), decoded by ffmpeg two frames at a time. The record: `read`, the frames, the background colour (None:
    every pixel), the poses over the frames, and each run of frames showing one picture (`start`, `length`), with `why`
    where the engine keeps such a run as the motion's own — longer than `held_fill.HOLD_MAX`, or the clip's opening or
    closing pose — and none where `video-loop` would make its frames again between the poses beside them. Nothing is
    changed: the provider's frames are the clip, and the material its loops are cut from. A clip ffmpeg cannot read is
    `read` false, with `why`."""
    from PIL import Image

    from sprite_gen._deps import np
    from sprite_gen.video import held_fill

    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return {"read": False, "why": "ffmpeg and ffprobe are needed to read its frames (install ffmpeg)"}
    probe = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "json", str(clip)], capture_output=True, text=True)
    try:
        stream = json.loads(probe.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except (ValueError, KeyError, IndexError, TypeError):
        return {"read": False, "why": f"ffprobe found no video stream ({probe.stderr.strip()[:200] or 'no answer'})"}
    size = width * height * 4
    proc = subprocess.Popen([ffmpeg, "-v", "error", "-nostdin", "-i", str(clip), "-map", "0:v:0", "-f", "rawvideo",
                             "-pix_fmt", "rgba", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    same: list[bool] = []
    background = None
    previous = None
    assert proc.stdout is not None
    while len(data := proc.stdout.read(size)) == size:
        rgba = np.frombuffer(data, np.uint8).reshape(height, width, 4).copy()
        if previous is None:
            background = _background(rgba)
        if background is not None:
            off = np.abs(rgba[..., :3].astype(np.float32) / 255 - np.asarray(background, np.float32)).max(axis=-1)
            rgba[off <= BACKGROUND_TOL, 3] = 0
        frame = Image.fromarray(rgba, "RGBA")
        if previous is not None:
            same.append(held_fill.same_steps([previous, frame])[0])
        previous = frame
    error = proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else ""
    if proc.wait() != 0 or previous is None:
        return {"read": False, "why": f"ffmpeg could not read its frames ({error[:200] or 'no frame'})"}
    n = len(same) + 1
    rows = []
    for run in held_fill.runs(same):
        why = (f"held {run['length']} frames, longer than a drawing on threes ({held_fill.HOLD_MAX}): a hold the motion means"
               if run["length"] > held_fill.HOLD_MAX else
               "the clip's opening pose" if run["start"] == 0 else
               "the clip's closing pose" if run["start"] + run["length"] == n else None)
        rows.append({**run, **({"why": why} if why else {})})
    reading = held_fill.reading(same)
    return {"read": True, "frames": n, "background": background, "pixel_same": held_fill.PIXEL_SAME,
            "changed_max": held_fill.CHANGED_MAX, "hold_max": held_fill.HOLD_MAX, "cells": reading["cells"],
            "poses": reading["poses"], "held_runs": rows}


def held_lines(clip: Path, record: dict[str, Any]) -> list[str]:
    """What the reading found, in words: each run of frames showing one picture that the engine does not keep as the
    motion's own, or why the frames were not read. A run kept as the motion's own says nothing."""
    from sprite_gen.video import held_fill

    if not record["read"]:
        return [f"{clip.name}: its frames were not read for a picture shown again — {record['why']}"]
    return [f"{clip.name} shows one picture on frames {r['start']}-{r['start'] + r['length'] - 1} "
            f"({held_fill.said(record)} poses/frames): the clip repeats a frame there, kept as the provider made it; "
            "video-loop makes such cells again between the poses beside them (docs/loop-repair.md section 7)"
            for r in record["held_runs"] if "why" not in r]


def _validate(request: VideoRequest) -> None:
    validate_facing(request.facing, request.facing_fix)
    if request.direction not in (None, *handed_mod.VIEWS):
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
    polls_refused: int
    polls_unanswered: int
    downloads: int
    elapsed_seconds: float


def _ask_again(request_id: str, out: Path) -> str:
    # `request_id=<id>` is what a caller reads the request off the line by; the command is the same ask.
    return (f"ask for request_id={request_id} again with `sprite-gen video-resume --request-id "
            f"{shlex.quote(request_id)} --out {shlex.quote(str(out))}` instead of submitting a new request; "
            "nothing was written")


def _staged(out: Path) -> Path:
    """Where a result is downloaded before it is published over `out`: `<out>.part` beside it."""
    return out.with_name(out.name + ".part")


def _submit_poll_publish(
    *,
    verb: str,
    endpoint: str,
    body: dict[str, Any],
    out: Path,
    credential: Credential,
    call: HttpCall,
    download: Download,
    sleep: Callable[[float], None],
    poll_timeout: float | None,
    accept: Callable[[Path, dict[str, Any]], None] | None = None,
) -> _Published:
    """POST `body` to `endpoint`, then `_poll_publish` the request xAI accepted.

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
    # A refusal xAI documents as a request not taken (429) is sent again (`retry`) while its wait leaves
    # some of the poll timeout, which counts from the first send.
    started = time.monotonic()
    timeout = POLL_TIMEOUT_SECONDS if poll_timeout is None else poll_timeout
    status, reply, _sends = post(call, address.url(address.XAI, f"/videos/{endpoint}"), credential.token, body, who="xai", label=verb,
                                 sleep=sleep, deadline=started + timeout)
    if status in (401, 403):
        raise SystemExit(
            f"{verb}: xAI rejected the credential ({credential.source}) with HTTP {status}: {_error_detail(reply, credential.token)}\n"
            f"  {rejected_fix(credential)}."
        )
    request_id = reply.get("request_id") if isinstance(reply, dict) else None
    if status not in (200, 202) or not request_id:
        refused = refusal.read(PROVIDER_GROK, reply)
        raise SystemExit(
            f"{verb}: {refused.reason('generation request refused')} (HTTP {status}){refused.suffix()}: {_error_detail(reply, credential.token)}"
        )
    return _poll_publish(
        verb=verb, request_id=str(request_id), model=body.get("model"), out=out, credential=credential, call=call,
        download=download, sleep=sleep, poll_timeout=timeout, started=started, accept=accept,
    )


def _poll_publish(
    *,
    verb: str,
    request_id: str,
    model: Any,
    out: Path,
    credential: Credential,
    call: HttpCall,
    download: Download,
    sleep: Callable[[float], None],
    poll_timeout: float | None,
    started: float,
    accept: Callable[[Path, dict[str, Any]], None] | None = None,
) -> _Published:
    """Poll `/videos/{request_id}` until done, download, verify, publish atomically.

    `accept(staged_path, final_poll)` may refuse the clip (raise SystemExit) while it is
    still the `.part` file — `video-extend` measures the staged bytes with ffprobe; the
    staged file is removed and `out` is never written.

    The poll timeout counts from `started`, the POST's first send (a resume: its first poll), and
    is one deadline for the polls and the downloads: `call` (as http_json) and `download` take it as
    `deadline=` and read only what is left of it, and a poll or download whose wait would reach it, or
    woke at it or past it, is not asked (`retry.too_late`). A run that ends before the request does — the
    deadline came, xAI rejected the credential, the done poll named no URL, or the clip it made could not
    be downloaded (DOWNLOAD_ATTEMPTS) — raises GenerationUnfinished naming the request."""
    timeout = POLL_TIMEOUT_SECONDS if poll_timeout is None else poll_timeout
    deadline = started + timeout
    polls = polls_refused = polls_unanswered = failed_in_a_row = downloads = 0
    video_url: Any = None
    duration_reported: float | None = None
    model_reported: str | None = None
    final: dict[str, Any] = {}

    def unfinished(line: str, http: int | None, poll_status: Any, lost: str | None,
                   last_download: dict[str, Any] | None = None) -> GenerationUnfinished:
        return GenerationUnfinished(
            f"{verb}: generation {request_id} {line}", verb=verb, request_id=request_id,
            auth_source=credential.source, model=model, out=out, polls=polls, polls_refused=polls_refused,
            polls_unanswered=polls_unanswered, last_http=http, last_status=poll_status, last_error=lost,
            elapsed_seconds=time.monotonic() - started, downloads=downloads, last_download=last_download,
        )

    if retry.too_late(deadline):
        # The POST is not cut by the deadline (its answer is the one way to the id); this one came after it.
        raise unfinished(f"was accepted after the poll timeout ({timeout:g}s) had run out, before any poll; "
                         f"it may still finish and be billed — {_ask_again(request_id, out)}", None, None, None)
    while True:
        polls += 1
        lost: str | None = None
        try:
            http, poll = call("GET", address.url(address.XAI, f"/videos/{request_id}"), credential.token, None, deadline=deadline)
        except TransportError as failed:
            lost, http, poll = failed.reason, None, None
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
        if lost is not None:
            polls_unanswered += 1
            failed_in_a_row += 1
        elif http in retry.GET_AGAIN:
            polls_refused += 1
            failed_in_a_row += 1
        elif http in (401, 403):
            # The token this request was submitted with stopped working (a login expires in about six
            # hours); asking again with it changes nothing. A fresh one can ask for the same request.
            fix = rejected_fix(credential)
            raise unfinished(
                f"was accepted, but xAI rejected the credential ({credential.source}) on poll {polls} (answer {http}); "
                f"it may still finish and be billed — {fix}, then {_ask_again(request_id, out)}",
                http, poll_status, None,
            )
        elif http not in (200, 202) or poll_status in _FAILED_STATUSES:
            # A failed poll carries `error` as an object `{code, message}`.
            refused = refusal.read(PROVIDER_GROK, poll)
            raise SystemExit(
                f"{verb}: generation {request_id} {refused.reason(f'ended with status={poll_status!r}')} "
                f"(HTTP {http}){refused.suffix()}: {_error_detail(poll, credential.token)}"
            )
        else:
            failed_in_a_row = 0
        wait = retry.backoff(failed_in_a_row) if failed_in_a_row else POLL_INTERVAL_SECONDS
        # The next poll goes only before the poll timeout (`retry.too_late`): one whose wait would reach it would
        # leave no time to read its answer, and a wait that woke at it or past it sends none.
        if not retry.too_late(deadline, wait):
            if lost is not None:
                print(f"[{verb}] poll {polls} of generation {request_id} got no answer ({lost}); "
                      f"asking again in {wait:g}s", file=sys.stderr)
            elif failed_in_a_row:
                # The status in plain digits: `(HTTP 429)` on this stream reads as a refused request to send again.
                print(f"[{verb}] poll {polls} of generation {request_id} was refused ({http}); "
                      f"asking again in {wait:g}s", file=sys.stderr)
            sleep(wait)
            if not retry.too_late(deadline):
                continue
        # The run ends here, on the last poll as it came, not at the deadline.
        if failed_in_a_row:
            last = f"got no answer (last: {lost})" if lost is not None else f"were refused (last answer {http})"
            stopped = f"was accepted, but its polls {last} until the poll timeout ({timeout:g}s, {polls} polls)"
        else:
            stopped = f"still {poll_status!r} after {polls} polls — poll timeout"
        raise unfinished(f"{stopped}; it may still finish and be billed — {_ask_again(request_id, out)}",
                         http, poll_status, lost)

    if not (isinstance(video_url, str) and video_url.lower().startswith(("https://", "http://"))):
        # Done is xAI's word that the clip is made, so it may be billed: a poll that names nothing to download
        # (no url, an empty one, not a web address) ends naming the request, and video-resume polls it afresh.
        raise unfinished(f"is done, but its poll named no video url to download; the clip is made and may be billed — "
                         f"{_ask_again(request_id, out)}", http, poll_status, None)
    # The clip is made: whatever stops its download, the run ends naming the request, never a second purchase.
    while True:
        downloads += 1
        said: str | None = None  # what the transport said when the download got no answer it could read
        refused: int | None = None  # the status the clip's host refused it with
        try:
            data = download(video_url, credential.token, deadline=deadline)
        except TransportError as failed:
            said = failed.reason
        except DownloadRefused as failed:
            refused = failed.status
        else:
            if _is_mp4(data):
                break
            # Bytes read whole that are not an mp4 are no verdict of xAI's on the request (that is
            # respect_moderation, above) and the same URL brings the same bytes: video-resume asks for a fresh one.
            raise unfinished(f"is done, but its download brought {len(data)} bytes that are not an mp4 (no ftyp box); "
                             f"the clip is made and may be billed — {_ask_again(request_id, out)}", http, poll_status, None,
                             {"http": None, "error": "not an mp4 (no ftyp box)", "bytes": len(data)})
        again = said is not None or refused in retry.GET_AGAIN
        wait = retry.backoff(downloads)
        # The next download goes only before the poll timeout (`retry.too_late`): one whose wait would reach it
        # would leave no time to read it, and a wait that woke at it or past it tries none.
        if again and downloads < DOWNLOAD_ATTEMPTS and not retry.too_late(deadline, wait):
            # The status in plain digits, as the poll notes say it.
            failure = f"got no answer ({said})" if said is not None else f"was refused ({refused})"
            print(f"[{verb}] download {downloads} of generation {request_id} {failure}; downloading again in {wait:g}s",
                  file=sys.stderr)
            sleep(wait)
            if not retry.too_late(deadline):
                continue
        if said is not None:
            failure = f"got no answer (last: {said})"
        elif again:
            failure = f"was refused (last answer {refused})"
        else:
            failure = f"was refused (answer {refused})"
        if again and downloads == DOWNLOAD_ATTEMPTS:
            failure += f" after {downloads} attempts"
        elif again:
            failure += f" until the poll timeout ({timeout:g}s, {downloads} attempt{'s' if downloads > 1 else ''})"
        raise unfinished(f"is done, but its download {failure}; the clip is made and may be billed — "
                         f"{_ask_again(request_id, out)}", http, poll_status, None,
                         {"http": refused, **({"error": said} if said is not None else {})})
    elapsed = time.monotonic() - started

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = _staged(out)
    tmp.write_bytes(data)
    if accept is not None:
        try:
            accept(tmp, final)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, out)
    return _Published(
        data=data, request_id=request_id, model=model_reported, duration_reported=duration_reported,
        host=_redact_host(video_url), polls=polls, polls_refused=polls_refused, polls_unanswered=polls_unanswered,
        downloads=downloads, elapsed_seconds=elapsed,
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
    download: Download | None = None,
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
        polls_refused=published.polls_refused,
        polls_unanswered=published.polls_unanswered,
        downloads=published.downloads,
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


def _published_at(out: Path) -> dict[str, Path]:
    """What publishing a result over `--out` writes: the download staged beside it, then `--out` itself."""
    out = out.expanduser().resolve()
    return {"--out": out, f"{_staged(out).name} beside --out": _staged(out)}


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
    # The result is downloaded to `<out>.part` and published over `--out`: the clip it continues is neither file (it would
    # be written over or replaced), a link already at either followed.
    refuse_overlap({"--video": request.video}, _published_at(request.out), verb="video-extend")
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
    download: Download | None = None,
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
        polls_refused=published.polls_refused, polls_unanswered=published.polls_unanswered,
        downloads=published.downloads, duration_requested=request.duration, duration_reported=published.duration_reported, host=published.host,
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
    # The result is downloaded to `<out>.part` and published over `--out`: the clip it edits is neither file (it would be
    # written over or replaced), a link already at either followed.
    refuse_overlap({"--video": request.video}, _published_at(request.out), verb="video-edit")
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
    download: Download | None = None,
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
        polls_refused=published.polls_refused, polls_unanswered=published.polls_unanswered,
        downloads=published.downloads, duration_requested=0, duration_reported=published.duration_reported, host=published.host,
        prompt=request.prompt, mode=MODE_EDIT, inputs={"video": str(clip)}, input_duration=input_seconds,
        output_duration=measured.get("output"),
    )


def resume_video(
    request: ResumeRequest,
    *,
    credential: Credential | None = None,
    call: HttpCall | None = None,
    download: Download | None = None,
    sleep: Callable[[float], None] | None = None,
    poll_timeout: float | None = None,
) -> VideoResult:
    """Ask again for a generation an earlier run left unfinished (`/videos/{request_id}`) and publish it.

    No POST, so nothing is bought twice and no API-credit notice is due. Polled, downloaded,
    verified and published as any clip; cut short again, it is unfinished on the same id. The
    clip is checked as an mp4 only: `video-extend`'s length rule needs the input clip."""
    call = call or http_json
    download = download or http_download
    sleep = sleep or time.sleep
    request_id = request.request_id.strip()
    if not REQUEST_ID.fullmatch(request_id):
        raise SystemExit(
            f"video-resume: --request-id takes the request_id an unfinished run named (letters, digits, '.', '_', '-'), "
            f"got {request.request_id!r}"
        )
    credential = credential or resolve_credential()
    out = request.out.expanduser().resolve()
    published = _poll_publish(
        verb="video-resume", request_id=request_id, model=None, out=out, credential=credential,
        call=call, download=download, sleep=sleep, poll_timeout=poll_timeout, started=time.monotonic(),
    )
    return VideoResult(
        out=out, bytes=len(published.data), model=published.model, request_id=published.request_id,
        auth_source=credential.source, elapsed_seconds=published.elapsed_seconds, polls=published.polls,
        polls_refused=published.polls_refused, polls_unanswered=published.polls_unanswered,
        downloads=published.downloads, duration_requested=None, duration_reported=published.duration_reported, host=published.host, mode=MODE_RESUME,
    )


def _add_prompt_and_report(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--out", required=True, type=Path, help="destination .mp4")
    parser.add_argument("--report", type=Path, help="write the sprite-gen-video-report JSON here")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--direction", choices=handed_mod.VIEWS, help="sprite view; side opts into facing inspection before video generation")
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


def add_resume_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request-id", required=True,
                        help="the request_id an unfinished video / video-extend / video-edit / video-resume run named")
    parser.add_argument("--out", required=True, type=Path, help="destination .mp4")
    parser.add_argument("--report", type=Path, help="write the sprite-gen-video-report JSON here")


def _read_prompt(args: argparse.Namespace) -> str:
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
    return (prompt or "").strip()


def _publish_report(args: argparse.Namespace, make: Callable[[], VideoResult], *, reads: dict[str, Path] | None = None,
                    verb: str | None = None) -> int:
    report_path = Path(args.report).expanduser().resolve() if args.report else None
    if reads and report_path is not None:
        # The report is written once the result is made: what the run reads is never that file.
        refuse_overlap(reads, {"--report": report_path}, verb=verb)
    try:
        result = make()
    except GenerationUnfinished as unfinished:
        # The request is xAI's now and may still finish: the report names it, so whoever ran this
        # asks for that id again instead of buying a second clip. Nothing goes to stdout.
        if report_path is not None:
            atomic_write_text(report_path, json.dumps(unfinished.to_dict(), ensure_ascii=False, indent=2) + "\n")
        raise
    # The clip as published, read for a picture shown again (docs/loop-repair.md section 7): named, never changed.
    result.held = read_held(result.out)
    for line in held_lines(result.out, result.held):
        print(f"{verb or 'video'}: warning: {line}", file=sys.stderr)
    payload = result.to_dict()
    if report_path is not None:
        atomic_write_text(report_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        payload["report"] = str(report_path)
    print_json(payload)
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
    return _publish_report(args, lambda: generate_video(request), verb="video")


def _run_extend(args: argparse.Namespace) -> int:
    request = ExtendRequest(video=args.video, prompt=_read_prompt(args), out=args.out, duration=args.duration)
    return _publish_report(args, lambda: extend_video(request), reads={"--video": request.video}, verb="video-extend")


def _run_edit(args: argparse.Namespace) -> int:
    request = EditRequest(video=args.video, prompt=_read_prompt(args), out=args.out)
    return _publish_report(args, lambda: edit_video(request), reads={"--video": request.video}, verb="video-edit")


def _run_resume(args: argparse.Namespace) -> int:
    request = ResumeRequest(request_id=args.request_id, out=args.out)
    return _publish_report(args, lambda: resume_video(request), verb="video-resume")


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


def _build_resume_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprite-gen video-resume", description="Ask again for a generation an earlier run left unfinished, by its request_id (GET /v1/videos/{id}, no new POST).")
    add_resume_arguments(parser)
    return parser


def run(**kwargs: object) -> int:
    return _run(namespace_from_kwargs(_build_parser(), kwargs))


def run_extend(**kwargs: object) -> int:
    return _run_extend(namespace_from_kwargs(_build_extend_parser(), kwargs))


def run_edit(**kwargs: object) -> int:
    return _run_edit(namespace_from_kwargs(_build_edit_parser(), kwargs))


def run_resume(**kwargs: object) -> int:
    return _run_resume(namespace_from_kwargs(_build_resume_parser(), kwargs))


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    return _run(_build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
