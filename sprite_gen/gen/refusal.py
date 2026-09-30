# SPDX-License-Identifier: Apache-2.0
"""What a provider refusal line says about why the provider refused.

The line shape is a contract other programs read:

    <verb>: <reason> (HTTP <n>)[ code=<code>][ key=value ...]<tail>: <body>

`(HTTP <n>)` stays exactly that — callers find the provider status with
`\\(HTTP (\\d{3})\\)` — and `code=` follows it after one space. The code is the
provider's own machine code (`error.code`, which OpenAI's image guide calls the
stable discriminator), and only when it looks like an identifier. When the code
is one the provider documents as a content-policy block, `<reason>` is the fixed
marker `CONTENT_POLICY`, so a caller can tell "change the request" from auth,
quota or parameter errors without knowing any provider's vocabulary.

`<body>` is the provider's error body as received — JSON on one line, anything
else as its text — so an open-source tool shows the whole error and the product
that wraps it decides what its own users see. Only two things are masked
(`redact`): strings shaped like an API key (the credential the request carried
among them) and the query string of a URL, where a signature lives. The body comes last, so nothing in it can move what a caller
reads off the front of the line.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

CODE_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")

CONTENT_POLICY = "refused by the provider's content policy"

# A key is a known prefix and a long run of key characters. One the provider
# already masked (`sk-proj-****…abcd`) carries `*` and is left as it came. Only a
# letter or digit before the prefix makes it part of a word (`task-…`); after
# `_` or `-` (`api_key_sk-…`) the key starts there.
_KEY = re.compile(r"(?<![A-Za-z0-9])(sk|xai)-([A-Za-z0-9_*-]+)")
_KEY_MIN_LENGTH = 16
_BEARER = re.compile(r"(?i)(?<![A-Za-z0-9])(bearer\s+)[A-Za-z0-9._~+/=-]{16,}")
_URL_QUERY = re.compile(r"(?i)\b(https?://[^\s?#\"'<>\\]+)\?[^\s#\"'<>\\]*")
REDACTED = "[redacted]"

# Which provider codes are a content-policy block, per provider; the one table.
# OpenAI images: `moderation_blocked` (developers.openai.com image-generation
# guide, "Handling blocked requests", 2026-09-30). xAI has none: its documented
# video failure code `invalid_argument` covers moderation and parameter errors
# alike, so listing it would tell a caller with a bad parameter to change the
# content. An xAI clip blocked by moderation is recognised by
# `video.respect_moderation: false` instead (sprite_gen.gen.video).
POLICY_CODES: dict[str, frozenset[str]] = {
    "openai": frozenset({"moderation_blocked"}),
    "grok": frozenset(),
}


class RawBody(dict):
    """An error body that was not JSON: `{"raw": text}` to the callers that read
    keys, and the text itself to `said`."""

    def __init__(self, text: str) -> None:
        super().__init__(raw=text)
        self.text = text


def _key(match: re.Match[str]) -> str:
    rest = match.group(2)
    if "*" in rest or len(rest) < _KEY_MIN_LENGTH:
        return match.group(0)
    return f"{match.group(1)}-{REDACTED}"


def redact(text: str, secret: str | None = None) -> str:
    """`text` with key-shaped strings and URL query strings masked; nothing else.

    `secret` is the credential the request was sent with: an echo of it is a key
    whatever its shape (a login token need not start with `sk-` or `xai-`).
    """
    if secret and len(secret) >= 8:
        text = text.replace(secret, REDACTED)
    text = _KEY.sub(_key, text)
    text = _BEARER.sub(lambda m: m.group(1) + REDACTED, text)
    return _URL_QUERY.sub(lambda m: f"{m.group(1)}?{REDACTED}", text)


def said(body: Any, secret: str | None = None) -> str:
    """`: <body>` for the end of a refusal line — the body as received, on one
    line and redacted — or "" when the provider sent nothing."""
    if isinstance(body, RawBody):
        text = body.text.strip().replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    elif body in (None, {}, ""):
        text = ""
    else:
        text = json.dumps(body, ensure_ascii=False)
    return f": {redact(text, secret)}" if text else ""


def identifier(value: Any) -> str | None:
    """`value` when it is a string shaped like a code, else None."""
    return value if isinstance(value, str) and CODE_PATTERN.fullmatch(value) else None


def provider_code(body: Any) -> str | None:
    """The code of an error body: `error.code` when `error` is an object, else a top-level `code`.

    OpenAI and a failed xAI video poll answer with an `error` object; xAI's
    synchronous errors put `code` next to an `error` string.
    """
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    return identifier(error.get("code") if isinstance(error, dict) else body.get("code"))


@dataclass(frozen=True)
class Refusal:
    code: str | None
    policy: bool
    details: tuple[tuple[str, str], ...] = ()

    def reason(self, otherwise: str) -> str:
        return CONTENT_POLICY if self.policy else otherwise

    def suffix(self) -> str:
        pairs = ((("code", self.code),) if self.code else ()) + self.details
        return "".join(f" {key}={value}" for key, value in pairs)


def read(provider: str, body: Any, details: tuple[tuple[str, str], ...] = ()) -> Refusal:
    code = provider_code(body)
    return Refusal(code=code, policy=code in POLICY_CODES[provider], details=details)
