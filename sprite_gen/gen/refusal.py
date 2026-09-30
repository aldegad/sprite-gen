# SPDX-License-Identifier: Apache-2.0
"""What a provider refusal line may say about why the provider refused.

Error bodies stay unprinted on the image paths: they can echo the prompt, a key or
a signed URL. The one field worth carrying out is the provider's own machine code
(`error.code`, which OpenAI's image guide calls the stable discriminator), and only
when it looks like an identifier — anything else could be prose from the body.

The line shape is a contract other programs read:

    <verb>: <reason> (HTTP <n>) code=<code>[ key=value ...]<tail>

`(HTTP <n>)` stays exactly that — callers find the provider status with
`\\(HTTP (\\d{3})\\)` — and `code=` follows it after one space. When the code is
one the provider documents as a content-policy block, `<reason>` is the fixed
marker `CONTENT_POLICY`, so a caller can tell "change the request" from auth,
quota or parameter errors without knowing any provider's vocabulary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CODE_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")

CONTENT_POLICY = "refused by the provider's content policy"

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
