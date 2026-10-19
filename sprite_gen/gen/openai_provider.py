# SPDX-License-Identifier: Apache-2.0
# Modified: consume the public shared Images codec; retain OpenAI-owned defaults and transport.
"""Direct OpenAI Images generation/editing with an API key and no ChatGPT login.

The `codex` provider reaches the same family of models through the ChatGPT
subscription (an interactive OAuth login and a `codex` CLI on PATH). Neither
exists inside a headless container, so this adapter talks to the REST endpoints
with `OPENAI_API_KEY` alone: `POST /v1/images/generations`, or
`POST /v1/images/edits` (multipart) when reference images are attached.

Field names and enums are the documented Images API surface (developers.openai.com,
2026-09-20 확인): gpt-image models always answer with base64 (`response_format` is
not accepted), `quality` is low|medium|high|xhigh|max|auto, `background` is
transparent|opaque|auto and transparency needs `output_format` png or webp, and a
custom `size` must have both sides divisible by 16, an aspect ratio within
1:3..3:1, and a total pixel count between 655,360 and 8,294,400.

This backend is for servers and SaaS and is billed per call, so sprite-gen runs it
only when the user chose it (구독 우선 불변식, 수홍 2026-09-20, 2026-10-16): when
`--provider openai` names it, or as the user's own answer for a default codex that
cannot run (`gen.codex_unavailable_choice`) — never as a default, never as the guided
flow's image provider, never unasked — and every call announces the charge on stderr
before it leaves. Personal use belongs on a subscription provider (`codex`, `grok`).

This provider never falls back to codex or to another key: a missing credential, a
rejected key or a failed request is the observable outcome. A refusal OpenAI documents as
a request not taken (429 but its billing codes, 503 overloaded) is sent again, twice at
most and each time said on stderr — `retry`, the rule every direct provider call keeps.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import images_codec as images, refusal, retry
# Preserve existing public constants/metadata helper while the codec owns their implementation.
from .images_codec import (
    BACKGROUND_OPAQUE, BACKGROUND_TRANSPARENT, DEFAULT_SIZE, MAX_REFS, PNG_OUTPUT_FORMAT,
    RATE_LIMIT_HEADER_PREFIX, SIZES, SUPPORTED_QUALITIES, rate_limit_headers,
)
from .xai import NO_ANSWER, error_body, read_whole, time_left
from .base import (
    GEN_TIMEOUT_SECONDS,
    TRANSPARENCY_NATIVE,
    GenRequest,
    ProviderRun,
    announce_api_billing,
    env_api_key,
)

API_BASE = "https://api.openai.com/v1"
AUTH_ENV = "OPENAI_API_KEY"
AUTH_SOURCE_API_KEY = AUTH_ENV

DEFAULT_MODEL = "gpt-image-2.5-flare"
# The documented default is `auto` (the model picks the effort). It is sent
# explicitly so the billed level is always visible in the request and the report.
DEFAULT_QUALITY = "auto"
# `error.moderation_details.moderation_stage` on a `moderation_blocked` answer
# (image-generation guide, 2026-09-30): whether the prompt/input images or the
# drawn image was blocked. Only these documented values reach the line.
MODERATION_STAGES = ("input", "output", "unknown")

def resolve_credential(env: dict[str, str] | None = None) -> str:
    """Return the OpenAI API key (`base.env_api_key`), or fail loud naming the variable that is missing.
    This provider never falls back to the codex ChatGPT login: that is `codex`'s route, and it needs
    an interactive browser sign-in that a container does not have."""
    key = env_api_key(AUTH_ENV, env, who="openai-gen",
                      also=". This provider never falls back to the codex ChatGPT login.")
    if key:
        return key
    raise SystemExit(
        f"openai-gen: no OpenAI credential — {AUTH_ENV} is not set.\n"
        f"  export {AUTH_ENV}=<your OpenAI API key> (platform.openai.com/api-keys). "
        "This provider never falls back to the codex ChatGPT login, which needs an "
        "interactive browser sign-in that a container does not have."
    )


TRANSPORT_FAILED = "openai-gen: request failed or timed out; no automatic retry (the server may have accepted it)"


def http_image(url: str, token: str, data: bytes, content_type: str, *, timeout: float,
               deadline: float | None = None) -> tuple[int, Any, Any]:
    """POST an images request and return (status, parsed JSON, response headers).

    A transport error is terminal: the server may already have accepted and billed
    the request, so it is never retried here. A refusal whose body broke on the way
    is one too (`xai.error_body`). A `deadline` bounds the waits as it does in
    `xai.http_json`: an answer that has not come, or not whole, by then is none.
    """
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=time_left(timeout, deadline)) as response:
            raw = read_whole(response, timeout, deadline).decode("utf-8")
            return response.status, (json.loads(raw) if raw else {}), response.headers
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit("openai-gen: response was not valid JSON") from exc
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, error_body(exc, timeout, deadline), exc.headers
        except NO_ANSWER as cut:
            raise SystemExit(TRANSPORT_FAILED) from cut
    except NO_ANSWER as exc:
        raise SystemExit(TRANSPORT_FAILED) from exc


def _ask(url: str, token: str, data: bytes, content_type: str) -> tuple[int, Any, Any, int]:
    """POST one request, sent again as `retry` says. SPRITE_GEN_GEN_TIMEOUT_SECONDS is its time limit from
    the first send: the waits before a refused one goes again count in it, and each send reads only what is
    left of it."""
    deadline = time.monotonic() + GEN_TIMEOUT_SECONDS
    return retry.ask(lambda: http_image(url, token, data, content_type, timeout=GEN_TIMEOUT_SECONDS, deadline=deadline),
                     provider="openai", who="openai", label="gen", kind=refusal.provider_code, deadline=deadline)


def _refusal(reply: Any) -> refusal.Refusal:
    error = reply.get("error") if isinstance(reply, dict) else None
    moderation = error.get("moderation_details") if isinstance(error, dict) else None
    stage = moderation.get("moderation_stage") if isinstance(moderation, dict) else None
    details = (("moderation_stage", stage),) if stage in MODERATION_STAGES else ()
    return refusal.read("openai", reply, details)


class OpenAIProvider:
    name = "openai"
    # `background: transparent` returns a real alpha channel on gpt-image-2.5.
    transparency = TRANSPARENCY_NATIVE

    def inspect_facing(self, path: Path, workdir: Path) -> tuple[str, dict]:
        from . import facing_vision as vision
        return self._inspect(vision.request_body(path, vision.OPENAI_MODEL), vision.FACING_LABEL)

    def inspect_image(self, path: Path, workdir: Path, prompt: str, *, label: str = "vision check") -> tuple[str, dict]:
        from . import facing_vision as vision
        return self._inspect(vision.request_body(path, vision.OPENAI_MODEL, prompt=prompt, detail="high"), label)

    def _inspect(self, body: dict, label: str) -> tuple[str, dict]:
        from . import facing_vision as vision
        token = resolve_credential()
        announce_api_billing(self.name, AUTH_ENV, f" ({label}).")
        data = json.dumps(body).encode("utf-8")
        status, reply, _headers, _asks = _ask(API_BASE + "/responses", token, data, "application/json")
        text, metadata = vision.response_text(status, reply, label=label)
        return text, {**metadata, "auth_source": AUTH_SOURCE_API_KEY, "transport": "openai-api"}

    def generate(self, request: GenRequest, workdir: Path) -> ProviderRun:
        fields = images.request_fields(request, model=request.model or DEFAULT_MODEL,
                                       quality=request.quality or DEFAULT_QUALITY, who="openai-gen")
        refs = [Path(ref) for ref in request.refs]
        if refs:
            endpoint = "/images/edits"
            data, content_type = images.multipart_request(fields, refs, who="openai-gen")
        else:
            endpoint = "/images/generations"
            data, content_type = images.json_request(fields)
        token = resolve_credential()
        announce_api_billing(
            self.name,
            AUTH_ENV,
            f" (model={fields['model']}, quality={fields['quality']}, size={fields['size']}).",
        )
        started = time.monotonic()
        status, reply, headers, asks = _ask(API_BASE + endpoint, token, data, content_type)
        # The provider's code (and the moderation stage) go in front, format-checked;
        # the body the provider sent goes last, as received, with keys and URL
        # query strings masked (refusal.said).
        refused = _refusal(reply) if status != 200 else None
        if status in (401, 403):
            raise SystemExit(
                f"openai-gen: credential {AUTH_SOURCE_API_KEY} rejected (HTTP {status}){refused.suffix()}; "
                f"check the key's value and that its project may call {fields['model']}{refusal.said(reply, token)}"
            )
        if refused is not None:
            raise SystemExit(
                f"openai-gen: {refused.reason('image request failed')} (HTTP {status}){refused.suffix()}; "
                f"{retry.tail(asks)}{refusal.said(reply, token)}"
            )
        items = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise SystemExit("openai-gen: expected exactly one image in the response; nothing published")
        images.publish_image(items[0], request.raw, who="openai-gen")
        usage = reply.get("usage") if isinstance(reply, dict) else None
        return ProviderRun(
            provider=self.name,
            elapsed_seconds=time.monotonic() - started,
            model=fields["model"],
            extra={
                "auth_source": AUTH_SOURCE_API_KEY,
                "transport": "openai-api",
                "endpoint": endpoint,
                "quality": fields["quality"],
                "size": fields["size"],
                "background": fields["background"],
                # Token counts only — what the call cost, never what it contained.
                **({"usage": usage} if isinstance(usage, dict) else {}),
                "rate_limit": rate_limit_headers(headers),
            },
        )
