# SPDX-License-Identifier: Apache-2.0
"""Shared xAI credentials and JSON transport for direct image/video generation.

XAI_API_KEY takes precedence; otherwise use the user's Grok login. The Grok
CLI alone writes/refreshes that login. Never spawn an agent or switch sources.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

API_BASE = "https://api.x.ai/v1"
AUTH_ENV = "XAI_API_KEY"
AUTH_SOURCE_API_KEY = "XAI_API_KEY"
AUTH_SOURCE_GROK_LOGIN = "grok-login"
HTTP_TIMEOUT_SECONDS = 120

# The refresh instruction for an expired login. The grok CLI rewrites the token
# the next time it talks to the API — measured 2026-09-08 with a one-line prompt
# (`grok -p ok`): expires_at moved from 10:56Z to 18:26Z. `grok login` is the
# full re-sign-in for a revoked or missing login.
GROK_REFRESH_COMMAND = "grok -p ok --output-format plain"
GROK_LOGIN_COMMAND = "grok login"


def grok_home() -> Path:
    configured = os.environ.get("GROK_HOME")
    if configured is None:
        return Path.home() / ".grok"
    if not configured.strip():
        raise SystemExit("xai: GROK_HOME is set but empty; refusing to guess the grok home")
    return Path(configured).expanduser().resolve()


@dataclass(frozen=True)
class Credential:
    token: str = field(repr=False)
    source: str  # AUTH_SOURCE_API_KEY | AUTH_SOURCE_GROK_LOGIN
    expires_at: str | None = None


def _parse_expiry(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_grok_login(auth_path: Path, *, now: datetime) -> Credential:
    if not auth_path.is_file():
        raise SystemExit(
            f"xai: no xAI credential — {AUTH_ENV} is not set and there is no grok login at "
            f"{auth_path}.\n"
            f"  either sign in once with `{GROK_LOGIN_COMMAND}` (SuperGrok Imagine quota, no API key), "
            f"or export {AUTH_ENV}=<your xAI console key>."
        )
    try:
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"xai: cannot read grok login file {auth_path}: {exc}") from exc
    entries = [entry for entry in (auth.values() if isinstance(auth, dict) else []) if isinstance(entry, dict)]
    if len(entries) != 1:
        raise SystemExit(
            f"xai: grok login file {auth_path} holds {len(entries)} account entr"
            f"{'y' if len(entries) == 1 else 'ies'}; expected exactly one — refusing to pick one silently. "
            f"Run `{GROK_LOGIN_COMMAND}` to reset it."
        )
    entry = entries[0]
    token = entry.get("key")
    if not isinstance(token, str) or not token.strip():
        raise SystemExit(f"xai: grok login file {auth_path} has no access token; run `{GROK_LOGIN_COMMAND}`.")
    expires_at = entry.get("expires_at")
    if isinstance(expires_at, str) and expires_at.strip():
        try:
            expiry = _parse_expiry(expires_at)
        except ValueError as exc:
            raise SystemExit(f"xai: grok login file {auth_path} has an unreadable expires_at {expires_at!r}: {exc}") from exc
        if expiry <= now:
            raise SystemExit(
                f"xai: the grok login token expired at {expires_at} (now {now.isoformat()}); nothing was uploaded.\n"
                f"  refresh it with any grok CLI command that reaches the API, e.g. `{GROK_REFRESH_COMMAND}`, "
                f"or sign in again with `{GROK_LOGIN_COMMAND}`. This tool never rewrites {auth_path} itself."
            )
    return Credential(token=token, source=AUTH_SOURCE_GROK_LOGIN, expires_at=expires_at if isinstance(expires_at, str) else None)


def resolve_credential(*, env: dict[str, str] | None = None, now: datetime | None = None) -> Credential:
    """Pick the credential in the fixed order: XAI_API_KEY, then the grok login file."""
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    api_key = (env.get(AUTH_ENV) or "").strip()
    if api_key:
        return Credential(token=api_key, source=AUTH_SOURCE_API_KEY)
    if AUTH_ENV in env and not api_key:
        raise SystemExit(f"xai: {AUTH_ENV} is set but empty; unset it to use the grok login, or give it a value")
    return _load_grok_login(grok_home() / "auth.json", now=now)


HttpCall = Callable[[str, str, str, dict | None], tuple[int, Any]]


def http_json(method: str, url: str, token: str, body: dict | None = None, *, timeout: float = HTTP_TIMEOUT_SECONDS) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else {})
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit("xai: response was not valid JSON") from exc
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:400]}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit("xai: request failed or timed out; no automatic retry (the server may have accepted it)") from exc


