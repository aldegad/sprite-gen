# SPDX-License-Identifier: Apache-2.0
"""Direct Grok Imagine image generation/editing, with no Grok Build process."""
from __future__ import annotations

import base64
import binascii
import io
import os
import tempfile
import time
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import xai
from .base import GEN_TIMEOUT_SECONDS, TRANSPARENCY_CHROMA, GenRequest, ProviderRun, verify_png

DEFAULT_MODEL = "grok-imagine-image-2.0"
MAX_REFS = 5
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
    body = {"model": request.model or DEFAULT_MODEL, "prompt": request.prompt,
            "n": 1, "response_format": "b64_json"}
    if request.aspect_ratio and len(request.refs) != 1:
        body["aspect_ratio"] = request.aspect_ratio
    if request.refs:
        images = [_reference(Path(ref)) for ref in request.refs]
        if len(images) == 1:
            body["image"] = images[0]
        else:
            body["images"] = images
        return "/images/edits", body
    return "/images/generations", body


def _publish_image(item: dict, path: Path) -> None:
    # Inline bytes avoid signed download URLs and bearer forwarding.
    encoded = item.get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise SystemExit("grok-gen: response has no b64_json image; nothing published")
    try:
        data = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            # Imagine may return JPEG. Encode a real PNG without resizing.
            png = io.BytesIO()
            source.convert("RGBA" if "A" in source.getbands() else "RGB").save(png, format="PNG")
    except (ValueError, binascii.Error, OSError, UnidentifiedImageError) as exc:
        raise SystemExit("grok-gen: response image is invalid; nothing published") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".png", delete=False) as tmp:
        temp = Path(tmp.name)
    try:
        temp.write_bytes(png.getvalue())
        verify_png(temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class GrokProvider:
    name = "grok"
    transparency = TRANSPARENCY_CHROMA

    def generate(self, request: GenRequest, workdir: Path) -> ProviderRun:
        if request.native_alpha:
            raise SystemExit("grok-gen: grok Imagine cannot return an alpha channel; generate on a chroma key instead")
        endpoint, body = _request_body(request)
        credential = xai.resolve_credential()
        started = time.monotonic()
        status, reply = xai.http_json("POST", xai.API_BASE + endpoint, credential.token,
                                      body, timeout=GEN_TIMEOUT_SECONDS)
        if status in (401, 403):
            remedy = (f"run `{xai.GROK_LOGIN_COMMAND}` to renew the login" if credential.source == xai.AUTH_SOURCE_GROK_LOGIN
                      else f"check {xai.AUTH_ENV}")
            raise SystemExit(f"grok-gen: credential {credential.source} rejected (HTTP {status}); {remedy}")
        # Error bodies can include prompts, credentials or URLs; do not echo them.
        if status != 200:
            raise SystemExit(f"grok-gen: image request failed (HTTP {status}); no retry or provider fallback")
        items = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise SystemExit("grok-gen: expected exactly one image in the response; nothing published")
        _publish_image(items[0], request.raw)
        return ProviderRun(provider=self.name, elapsed_seconds=time.monotonic() - started,
                           model=body["model"], extra={"auth_source": credential.source,
                           "transport": "xai-api", "endpoint": endpoint,
                           "aspect_ratio_source": "reference" if len(request.refs) == 1 else "request-or-auto"})
