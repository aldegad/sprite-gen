# SPDX-License-Identifier: Apache-2.0
"""Shared xAI credentials and JSON transport for direct image/video generation.

The user's Grok subscription login takes precedence over XAI_API_KEY. The key
is used only when no login file exists. The Grok CLI alone writes/refreshes
that login. Never spawn an agent or switch sources after a login failure.
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

from . import refusal

API_BASE = "https://api.x.ai/v1"
AUTH_ENV = "XAI_API_KEY"
AUTH_SOURCE_API_KEY = "XAI_API_KEY"
AUTH_SOURCE_GROK_LOGIN = "grok-login"
HTTP_TIMEOUT_SECONDS = 120

# The refresh instruction for an expired login. The grok CLI refreshes the token
# lazily, on its next API round-trip after `expires_at` has passed (2026-09-13
# measurement: while the token is still valid no command moves expires_at).
# The command must be a non-agent round-trip: a bare prompt (`grok -p ok`) starts
# the coding agent, which reads the cwd, may write files and may call paid APIs
# on its own (2026-09-13 incident, sprite-gen worktree). `grok models` only lists
# models and exits. Run it from an empty directory anyway so nothing of the
# user's is in reach. `grok login` is the full re-sign-in for a revoked, missing
# or non-refreshable login.
GROK_REFRESH_COMMAND = "grok models"
GROK_REFRESH_WHERE = "from an empty directory (e.g. `cd \"$(mktemp -d)\"`)"
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
            f"xai: grok login path {auth_path} is not a readable file; "
            f"run `{GROK_LOGIN_COMMAND}`. Refusing API-credit fallback."
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
                f"  refresh it with `{GROK_REFRESH_COMMAND}` run {GROK_REFRESH_WHERE} — a non-agent round-trip; "
                f"never a bare prompt like `grok -p …`, which starts the coding agent in your cwd — "
                f"or sign in again with `{GROK_LOGIN_COMMAND}`. This tool never rewrites {auth_path} itself."
            )
    return Credential(token=token, source=AUTH_SOURCE_GROK_LOGIN, expires_at=expires_at if isinstance(expires_at, str) else None)


def resolve_credential(*, env: dict[str, str] | None = None, now: datetime | None = None) -> Credential:
    """Prefer the subscription login; a failed login never switches to API credit."""
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    auth_path = grok_home() / "auth.json"
    # Only absence allows the API key. Broken links/directories are invalid
    # logins too, not permission to switch to another billing source.
    try:
        auth_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SystemExit(f"xai: cannot inspect grok login file {auth_path}; refusing API-credit fallback") from exc
    else:
        return _load_grok_login(auth_path, now=now)
    api_key = (env.get(AUTH_ENV) or "").strip()
    if api_key:
        return Credential(token=api_key, source=AUTH_SOURCE_API_KEY)
    if AUTH_ENV in env and not api_key:
        raise SystemExit(f"xai: {AUTH_ENV} is set but empty and no grok login exists; run `{GROK_LOGIN_COMMAND}` or give the key a value")
    raise SystemExit(
        f"xai: no xAI credential — no grok login at {auth_path} and {AUTH_ENV} is not set.\n"
        f"  sign in with `{GROK_LOGIN_COMMAND}` (SuperGrok Imagine quota, no API key), "
        f"or export {AUTH_ENV}=<your xAI console key>."
    )


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
            return exc.code, refusal.RawBody(raw)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit("xai: request failed or timed out; no automatic retry (the server may have accepted it)") from exc
