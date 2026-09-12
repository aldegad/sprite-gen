# SPDX-License-Identifier: Apache-2.0
"""Image-to-video through Grok Imagine (xAI `POST /v1/videos/generations`).

One call = one still (+ prompt) -> one verified mp4 on disk. This is the engine
module behind the sprite-gen skill's standalone `video` command.

Credentials are the user's own and never live in this repository. Two sources,
resolved in a fixed order and always reported (`auth_source`):

1. the grok CLI login file `~/.grok/auth.json` (SuperGrok Imagine quota via the
   OIDC access token the CLI stored at `grok login`). `GROK_HOME` relocates it.
2. `XAI_API_KEY` - an xAI console key, only when no grok login file exists.

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
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sprite_gen.spec.runio import atomic_write_text
from .xai import (
    API_BASE, AUTH_ENV, AUTH_SOURCE_API_KEY, AUTH_SOURCE_GROK_LOGIN,
    HTTP_TIMEOUT_SECONDS, GROK_REFRESH_COMMAND, GROK_LOGIN_COMMAND,
    Credential, HttpCall, grok_home, resolve_credential, http_json,
)

DEFAULT_MODEL = "grok-imagine-video-1.5"
RESOLUTIONS = ("480p", "720p", "1080p")
ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")
DURATION_MIN, DURATION_MAX = 1, 15
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


def _error_detail(body: Any) -> str:
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


@dataclass
class VideoRequest:
    image: Path
    prompt: str
    out: Path
    duration: int = 6
    resolution: str = "720p"
    aspect_ratio: str | None = None
    model: str = DEFAULT_MODEL
    generate_audio: bool | None = None  # None = API default


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
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "sprite-gen-video-report",
            "provider": "grok-imagine",
            "auth_source": self.auth_source,
            "model": self.model,
            "request_id": self.request_id,
            "image": str(self.image) if self.image else None,
            "prompt": self.prompt,
            "out": str(self.out),
            "bytes": self.bytes,
            "host": self.host,
            "duration_requested": self.duration_requested,
            "duration_reported": self.duration_reported,
            "resolution": self.resolution,
            "aspect_ratio": self.aspect_ratio,
            "generate_audio": self.generate_audio,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "polls": self.polls,
            **({"extra": self.extra} if self.extra else {}),
        }


def _validate(request: VideoRequest) -> None:
    if not request.prompt.strip():
        raise SystemExit("video: empty prompt; pass --prompt or --prompt-file")
    if not request.image.is_file():
        raise SystemExit(f"video: still image not found: {request.image}")
    if not (DURATION_MIN <= request.duration <= DURATION_MAX):
        raise SystemExit(f"video: --duration must be {DURATION_MIN}..{DURATION_MAX} seconds, got {request.duration}")
    if request.resolution not in RESOLUTIONS:
        raise SystemExit(f"video: --resolution must be one of {', '.join(RESOLUTIONS)}, got {request.resolution!r}")
    if request.aspect_ratio is not None and request.aspect_ratio not in ASPECT_RATIOS:
        raise SystemExit(f"video: --aspect-ratio must be one of {', '.join(ASPECT_RATIOS)}, got {request.aspect_ratio!r}")


def generate_video(
    request: VideoRequest,
    *,
    credential: Credential | None = None,
    call: HttpCall | None = None,
    download: Callable[[str, str], bytes] | None = None,
    sleep: Callable[[float], None] | None = None,
    poll_timeout: float | None = None,
) -> VideoResult:
    """Generate one clip and return a VideoResult. Raises SystemExit on any failure."""
    # Late-bound so a test that monkeypatches the module-level transport never
    # reaches the network through a default captured at definition time.
    call = call or http_json
    download = download or http_download
    sleep = sleep or time.sleep
    _validate(request)
    credential = credential or resolve_credential()
    out = request.out.expanduser().resolve()
    image = request.image.expanduser().resolve()

    body: dict[str, Any] = {
        "model": request.model,
        "prompt": request.prompt,
        "duration": request.duration,
        "resolution": request.resolution,
        "image": {"url": _data_url(image)},
    }
    if request.aspect_ratio:
        body["aspect_ratio"] = request.aspect_ratio
    if request.generate_audio is not None:
        body["generate_audio"] = request.generate_audio

    started = time.monotonic()
    status, reply = call("POST", f"{API_BASE}/videos/generations", credential.token, body)
    if status in (401, 403):
        raise SystemExit(
            f"video: xAI rejected the credential ({credential.source}) with HTTP {status}: {_error_detail(reply)}\n"
            + (
                f"  the grok login token may have been revoked or rotated — run `{GROK_REFRESH_COMMAND}` or `{GROK_LOGIN_COMMAND}`."
                if credential.source == AUTH_SOURCE_GROK_LOGIN
                else f"  check the {AUTH_ENV} value."
            )
        )
    request_id = reply.get("request_id") if isinstance(reply, dict) else None
    if status not in (200, 202) or not request_id:
        raise SystemExit(f"video: generation request refused (HTTP {status}): {_error_detail(reply)}")

    deadline = time.monotonic() + (POLL_TIMEOUT_SECONDS if poll_timeout is None else poll_timeout)
    polls = 0
    video_url: str | None = None
    duration_reported: float | None = None
    model_reported: str | None = None
    while True:
        polls += 1
        http, poll = call("GET", f"{API_BASE}/videos/{request_id}", credential.token, None)
        poll_status = poll.get("status") if isinstance(poll, dict) else None
        if http in (200, 202) and poll_status in _DONE_STATUSES:
            video = poll.get("video") or {}
            video_url = video.get("url") if isinstance(video, dict) else None
            duration_reported = video.get("duration") if isinstance(video, dict) else None
            model_reported = poll.get("model")
            break
        if http not in (200, 202) or poll_status in _FAILED_STATUSES:
            raise SystemExit(
                f"video: generation {request_id} ended with status={poll_status!r} (HTTP {http}): {_error_detail(poll)}"
            )
        if time.monotonic() >= deadline:
            raise SystemExit(
                f"video: generation {request_id} still {poll_status!r} after {polls} polls — poll timeout; "
                "nothing was written"
            )
        sleep(POLL_INTERVAL_SECONDS)

    if not video_url:
        raise SystemExit(f"video: generation {request_id} is done but reported no video url")
    data = download(video_url, credential.token)
    verify_mp4(data)
    elapsed = time.monotonic() - started

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, out)

    return VideoResult(
        out=out,
        bytes=len(data),
        model=model_reported or request.model,
        request_id=str(request_id),
        auth_source=credential.source,
        elapsed_seconds=elapsed,
        polls=polls,
        duration_requested=request.duration,
        duration_reported=duration_reported,
        host=_redact_host(video_url),
        image=image,
        prompt=request.prompt,
        resolution=request.resolution,
        aspect_ratio=request.aspect_ratio,
        generate_audio=request.generate_audio,
    )


def _print_json(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:
        sys.stdout.write(text)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--image", required=True, type=Path, help="the still to animate (PNG/JPEG/WebP)")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--out", required=True, type=Path, help="destination .mp4")
    parser.add_argument("--duration", type=int, default=6, help=f"seconds, {DURATION_MIN}..{DURATION_MAX} (default 6)")
    parser.add_argument("--resolution", choices=RESOLUTIONS, default="720p")
    parser.add_argument("--aspect-ratio", choices=ASPECT_RATIOS, default=None, help="default: the still's own ratio")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    audio = parser.add_mutually_exclusive_group()
    audio.add_argument("--audio", dest="generate_audio", action="store_true", default=None, help="ask for generated audio")
    audio.add_argument("--no-audio", dest="generate_audio", action="store_false", help="silent clip")
    parser.add_argument("--report", type=Path, help="write the sprite-gen-video-report JSON here")


def _run(args: argparse.Namespace) -> int:
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
    request = VideoRequest(
        image=args.image,
        prompt=(prompt or "").strip(),
        out=args.out,
        duration=args.duration,
        resolution=args.resolution,
        aspect_ratio=args.aspect_ratio,
        model=args.model,
        generate_audio=args.generate_audio,
    )
    result = generate_video(request)
    payload = result.to_dict()
    if args.report:
        report_path = Path(args.report).expanduser().resolve()
        atomic_write_text(report_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        payload["report"] = str(report_path)
    _print_json(payload)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprite-gen video", description=__doc__)
    add_arguments(parser)
    return parser


def run(**kwargs: object) -> int:
    parser = _build_parser()
    known = {action.dest for action in parser._actions if action.dest != "help"}
    unexpected = set(kwargs) - known
    if unexpected:
        raise TypeError(f"unexpected keyword argument(s): {', '.join(sorted(unexpected))}")
    namespace = argparse.Namespace(**{dest: kwargs.get(dest, parser.get_default(dest)) for dest in known})
    return _run(namespace)


def main(argv: list[str] | None = None) -> int:
    return _run(_build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
