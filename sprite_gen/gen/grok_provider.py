# SPDX-License-Identifier: Apache-2.0
"""Direct Grok Imagine image generation/editing, with no Grok Build process.

`quality` and `resolution` are the two knobs Imagine prices an image on
(docs.x.ai, 2026-09-20 확인): quality for grok-imagine-image-2.0 only, resolution
naming an output-size tier (a tier, not a pixel count — `1.5k` rendered 1408x1408
at 1:1). Both are sent only when the caller asks for one, so a request that names
neither stays exactly what it was.
"""
from __future__ import annotations

import base64
import binascii
import io
import time
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import refusal, xai
from .base import (
    GEN_TIMEOUT_SECONDS,
    RESOLUTIONS,
    TRANSPARENCY_CHROMA,
    GenRequest,
    ProviderRun,
    announce_api_billing,
    publish_png,
)

DEFAULT_MODEL = "grok-imagine-image-2.0"
MAX_REFS = 5
# The subset of the shared `--quality` vocabulary Imagine can honour. Anything
# finer belongs to another backend, so it is refused here rather than billed as
# whatever the service picked. Omitted = `auto`, which the service resolves to
# low for generations and medium for edits.
#
# Both sets were read off the server, not the prose (2026-09-20 probes on the
# subscription route). Deserialization alone is not the answer: `quality` accepts
# `high` into the shared enum and then refuses it per model —
#   HTTP 400 "This model only supports the following quality value(s): low,
#   medium, auto."
# — so `high` is refused here instead of costing a round-trip. `resolution` 1.5k
# is served (HTTP 200) although the capability guide's prose lists only 1k and 2k;
# the REST schema enum has all three. An unknown field is not refused at all (a
# probe carrying one rendered normally), which is exactly why these names are
# checked here rather than left to the service to notice.
SUPPORTED_QUALITIES = ("auto", "low", "medium")
SUPPORTED_RESOLUTIONS = RESOLUTIONS
ASPECT_RATIOS = ("auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
                 "2:1", "1:2", "19.5:9", "9:19.5", "20:9", "9:20", "21:9", "5:2")


def _reference(path: Path) -> dict:
    try:
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            mime = Image.MIME.get(source.format)
        if mime not in ("image/png", "image/jpeg", "image/webp"):
            raise ValueError("reference must be PNG, JPEG or WebP")
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise SystemExit(f"grok-gen: invalid reference image {path}") from exc
    return {"type": "image_url", "url": f"data:{mime};base64," + base64.b64encode(data).decode("ascii")}


def _request_body(request: GenRequest) -> tuple[str, dict]:
    if not request.prompt.strip():
        raise SystemExit("grok-gen: empty prompt")
    if len(request.refs) > MAX_REFS:
        raise SystemExit(f"grok-gen: at most {MAX_REFS} reference images are supported")
    if request.aspect_ratio is not None and request.aspect_ratio not in ASPECT_RATIOS:
        raise SystemExit(f"grok-gen: unsupported aspect ratio {request.aspect_ratio!r}")
    # Both knobs are declared capabilities, not hints: a level outside what
    # Imagine serves fails here instead of being dropped from the body and billed
    # as whatever the service picked (No Silent Fallback).
    if request.quality is not None and request.quality not in SUPPORTED_QUALITIES:
        raise SystemExit(
            f"grok-gen: unsupported quality {request.quality!r}; grok Imagine takes "
            f"{', '.join(SUPPORTED_QUALITIES)} — a finer level belongs to --provider openai"
        )
    if request.resolution is not None and request.resolution not in SUPPORTED_RESOLUTIONS:
        raise SystemExit(
            f"grok-gen: unsupported resolution {request.resolution!r}; "
            f"expected one of {', '.join(SUPPORTED_RESOLUTIONS)}"
        )
    body = {"model": request.model or DEFAULT_MODEL, "prompt": request.prompt,
            "n": 1, "response_format": "b64_json"}
    if request.aspect_ratio and len(request.refs) != 1:
        body["aspect_ratio"] = request.aspect_ratio
    # Sent only when asked for. An omitted knob keeps the service default and the
    # request identical to what the same call was already billed for.
    if request.quality is not None:
        body["quality"] = request.quality
    if request.resolution is not None:
        body["resolution"] = request.resolution
    if request.refs:
        images = [_reference(Path(ref)) for ref in request.refs]
        if len(images) == 1:
            body["image"] = images[0]
        else:
            body["images"] = images
        return "/images/edits", body
    return "/images/generations", body


def _publish_image(item: dict, path: Path) -> None:
    # Inline bytes avoid signed download URLs and bearer forwarding. Imagine may
    # answer with JPEG; `publish_png` re-encodes it without resizing.
    encoded = item.get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise SystemExit("grok-gen: response has no b64_json image; nothing published")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SystemExit("grok-gen: response image is invalid; nothing published") from exc
    publish_png(data, path, label="grok-gen")


class GrokProvider:
    name = "grok"
    transparency = TRANSPARENCY_CHROMA

    def inspect_facing(self, path: Path, workdir: Path) -> tuple[str, dict]:
        from .facing_vision import grok_inspect
        return grok_inspect(path)

    def generate(self, request: GenRequest, workdir: Path) -> ProviderRun:
        if request.native_alpha:
            raise SystemExit("grok-gen: grok Imagine cannot return an alpha channel; generate on a chroma key instead")
        endpoint, body = _request_body(request)
        # The login is preferred (xai.resolve_credential); reaching the key means no
        # login exists, and that spends API credit instead of the Grok subscription.
        credential = xai.resolve_credential()
        # What this image is priced on, named in the same breath as the charge:
        # Imagine bills quality x resolution, so 2k medium costs twice 1k low.
        priced = ", ".join(f"{knob}={value}" for knob, value in
                           (("quality", request.quality), ("resolution", request.resolution)) if value)
        if credential.source == xai.AUTH_SOURCE_API_KEY:
            announce_api_billing(self.name, xai.AUTH_ENV,
                                 f"{f' ({priced})' if priced else ''} — "
                                 f"`{xai.GROK_LOGIN_COMMAND}` signs your Grok subscription in.")
        started = time.monotonic()
        status, reply = xai.http_json("POST", xai.API_BASE + endpoint, credential.token,
                                      body, timeout=GEN_TIMEOUT_SECONDS)
        # Error bodies can include prompts, credentials or URLs; do not echo them.
        # Only the provider's code leaves, format-checked. The REST image answer
        # has no moderation field (docs.x.ai reference; a 2026-09-30 probe's items
        # carried `b64_json` and `mime_type` only), so a code is all there is.
        refused = refusal.read(self.name, reply) if status != 200 else None
        if status in (401, 403):
            remedy = (f"run `{xai.GROK_LOGIN_COMMAND}` to renew the login" if credential.source == xai.AUTH_SOURCE_GROK_LOGIN
                      else f"check {xai.AUTH_ENV}")
            raise SystemExit(f"grok-gen: credential {credential.source} rejected (HTTP {status}){refused.suffix()}; {remedy}")
        # The one other thing worth naming is the combination the docs scope to
        # grok-imagine-image-2.0, which another model is entitled to reject.
        if refused is not None:
            scoped = f" (sent with {priced}, documented for {DEFAULT_MODEL})" if priced and body["model"] != DEFAULT_MODEL else ""
            raise SystemExit(f"grok-gen: {refused.reason('image request failed')} (HTTP {status}){refused.suffix()}; "
                             f"no retry or provider fallback{scoped}")
        items = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise SystemExit("grok-gen: expected exactly one image in the response; nothing published")
        _publish_image(items[0], request.raw)
        return ProviderRun(provider=self.name, elapsed_seconds=time.monotonic() - started,
                           model=body["model"], extra={"auth_source": credential.source,
                           "transport": "xai-api", "endpoint": endpoint,
                           "aspect_ratio_source": "reference" if len(request.refs) == 1 else "request-or-auto",
                           # Absent = the service default, which the report must not
                           # invent a name for (`auto` differs per endpoint).
                           **{knob: body[knob] for knob in ("quality", "resolution") if knob in body}})
