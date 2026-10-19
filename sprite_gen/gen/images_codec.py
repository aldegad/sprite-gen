# SPDX-License-Identifier: Apache-2.0
# Extracted from openai_provider.py: shared wire codec; provider defaults remain with each provider.
"""Public OpenAI-compatible Images wire contract, without transport or credentials.

Callers resolve their own model/deployment and quality defaults. This module
validates image fields, encodes JSON/multipart bodies, publishes decoded images,
and extracts response rate-limit metadata.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .base import QUALITIES, GenRequest, publish_png

SUPPORTED_QUALITIES = QUALITIES
MAX_REFS = 16
PNG_OUTPUT_FORMAT = "png"
BACKGROUND_TRANSPARENT = "transparent"
BACKGROUND_OPAQUE = "opaque"
RATE_LIMIT_HEADER_PREFIX = "x-ratelimit-"

# Every concrete size satisfies the GPT image wire-format constraints.
SIZES = {
    "auto": "auto",
    "1:1": "1024x1024",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
    "4:3": "1024x768",
    "3:4": "768x1024",
    "16:9": "1536x864",
    "9:16": "864x1536",
    "2:1": "1440x720",
    "1:2": "720x1440",
    "3:1": "1536x512",
    "1:3": "512x1536",
}
DEFAULT_SIZE = SIZES["1:1"]
_REFERENCE_MIMES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def request_fields(request: GenRequest, *, model: str, quality: str, who: str) -> dict[str, str]:
    """Validate fields offline, using model and quality resolved by the caller."""
    if not request.prompt.strip():
        raise SystemExit(f"{who}: empty prompt")
    if len(request.refs) > MAX_REFS:
        raise SystemExit(f"{who}: at most {MAX_REFS} reference images are supported")
    if request.resolution is not None:
        raise SystemExit(
            f"{who}: resolution {request.resolution!r} is grok Imagine's vocabulary; "
            "a gpt-image size comes from --aspect-ratio"
        )
    if quality not in SUPPORTED_QUALITIES:
        raise SystemExit(
            f"{who}: unsupported quality {quality!r}; expected one of {', '.join(SUPPORTED_QUALITIES)}"
        )
    if request.aspect_ratio is None:
        size = DEFAULT_SIZE
    elif request.aspect_ratio in SIZES:
        size = SIZES[request.aspect_ratio]
    else:
        raise SystemExit(
            f"{who}: aspect ratio {request.aspect_ratio!r} has no gpt-image size; "
            f"expected one of {', '.join(SIZES)}"
        )
    return {
        "model": model, "prompt": request.prompt, "n": "1", "size": size,
        "quality": quality, "output_format": PNG_OUTPUT_FORMAT,
        "background": BACKGROUND_TRANSPARENT if request.native_alpha else BACKGROUND_OPAQUE,
    }


def json_request(fields: dict[str, str]) -> tuple[bytes, str]:
    """Encode a generation body with the API's numeric image count."""
    return json.dumps({**fields, "n": int(fields["n"])}).encode("utf-8"), "application/json"


def _reference(path: Path, index: int, who: str) -> tuple[str, str, bytes]:
    try:
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            mime = Image.MIME.get(source.format)
        if mime not in _REFERENCE_MIMES:
            raise ValueError("reference must be PNG, JPEG or WebP")
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise SystemExit(f"{who}: invalid reference image {path}") from exc
    # Positional names preserve input order without exposing filenames in headers.
    return f"image-{index}.{_REFERENCE_MIMES[mime]}", mime, data


def multipart_request(fields: dict[str, str], refs: list[Path], *, who: str) -> tuple[bytes, str]:
    """Encode ordered image[] edit parts; reference filenames never become headers."""
    boundary = "----sprite-gen-" + uuid.uuid4().hex
    marker = f"--{boundary}\r\n".encode("utf-8")
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(marker)
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(value.encode("utf-8") + b"\r\n")
    for index, ref in enumerate(refs):
        filename, mime, data = _reference(ref, index, who)
        chunks.append(marker)
        chunks.append(
            f'Content-Disposition: form-data; name="image[]"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
        )
        chunks.append(data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def publish_image(item: dict, path: Path, *, who: str) -> None:
    """Decode inline base64 and atomically publish only a verified image."""
    encoded = item.get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise SystemExit(f"{who}: response has no b64_json image; nothing published")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SystemExit(f"{who}: response image is invalid; nothing published") from exc
    publish_png(data, path, label=who)


def rate_limit_headers(headers: Any) -> dict[str, str]:
    """Return only the rate-limit headers, normalized to lowercase names."""
    return {name.lower(): value for name, value in headers.items()
            if name.lower().startswith(RATE_LIMIT_HEADER_PREFIX)}
