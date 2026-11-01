# SPDX-License-Identifier: Apache-2.0
"""Where each provider sprite-gen calls is: its address, read from the environment at every call.

sprite-gen calls three providers over HTTP itself, with no SDK: OpenAI, xAI and Anthropic. Each has one
base address, the documented API root with its version path, and one environment variable that replaces it,
`SPRITE_GEN_<PROVIDER>_BASE_URL`. Unset, empty or blank is the documented address, so nothing changes for anyone who
sets none. The variable is read when a request is made, never at import, so a process that sets it after importing
the engine is followed. Any other value must be an http:// or https:// address with no query or fragment, or the
call is refused naming the variable.

A URL a provider hands back to be fetched (a finished clip from xAI, a Message Batch's results file from Anthropic)
points at the provider's own hosts. With the provider's variable set it is fetched through the same base:
`GET <base>/sprite-gen/fetch?url=<the URL, percent-encoded>`, with the headers it would have carried, so a relay
that forwards the provider's API forwards it too. Without the variable it is fetched where it points, as before.

Every direct provider call builds its URL here (docs/gen.md "Provider addresses" lists them):
`openai_provider` (images, vision), `xai` callers `grok_provider` (images), `facing_vision.grok_inspect` (vision)
and `video` (generations, extensions, edits, polls, the clip's download), and `anthropic_api` (messages, batches, the
results file). The subscription logins (`codex`, `claude`) are the binaries' own network and do not read these
variables; `SPRITE_GEN_LOGINS=off` turns them off (`base.logins_on`).
"""
from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Provider:
    name: str
    env: str  # the variable that replaces `default`
    default: str  # the documented API root, version path included


# The three providers' names, each written once: the keys every caller and every table of them (`retry`,
# `workflow.capabilities`) names a provider by. A host, not an image provider: `OPENAI` is the API sprite-gen calls,
# `base.PROVIDER_OPENAI` the image provider that calls it, two names that happen to be spelled alike.
OPENAI, XAI, ANTHROPIC = "openai", "xai", "anthropic"

PROVIDERS = {provider.name: provider for provider in (
    Provider(OPENAI, "SPRITE_GEN_OPENAI_BASE_URL", "https://api.openai.com/v1"),
    Provider(XAI, "SPRITE_GEN_XAI_BASE_URL", "https://api.x.ai/v1"),
    Provider(ANTHROPIC, "SPRITE_GEN_ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
)}
FETCH_PATH = "/sprite-gen/fetch"


def _named(provider: str, env: Mapping[str, str] | None) -> str | None:
    """The base the variable names, trailing slash dropped, or None where it is unset or blank."""
    variable = PROVIDERS[provider].env
    named = ((os.environ if env is None else env).get(variable) or "").strip()
    if not named:
        return None
    split = urllib.parse.urlsplit(named)
    if split.scheme not in ("http", "https") or not split.hostname or split.query or split.fragment:
        raise SystemExit(f"{variable}={named!r}: name an http:// or https:// address with no query or fragment "
                         f"(the provider's API root, version path included: {PROVIDERS[provider].default}), "
                         "or leave it unset")
    return named.rstrip("/")


def base(provider: str, *, env: Mapping[str, str] | None = None) -> str:
    """The base address `provider`'s calls go to now: its variable, or the documented address."""
    return _named(provider, env) or PROVIDERS[provider].default


def url(provider: str, path: str, *, env: Mapping[str, str] | None = None) -> str:
    """The URL of `path` (from `/`, query included) on `provider`'s base."""
    return base(provider, env=env) + path


def fetched(provider: str, returned: str, *, env: Mapping[str, str] | None = None) -> str:
    """Where to fetch `returned`, a URL `provider` handed back: through the variable's base when it is set, else as
    it is."""
    named = _named(provider, env)
    if named is None:
        return returned
    return f"{named}{FETCH_PATH}?url={urllib.parse.quote(returned, safe='')}"
