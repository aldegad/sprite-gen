# SPDX-License-Identifier: Apache-2.0
"""Credential readiness is not proof of a paid subscription or remaining quota."""
from __future__ import annotations

import shutil
import subprocess

from sprite_gen.gen.base import provider_binary, provider_subprocess_env
from sprite_gen.gen.video import AUTH_SOURCE_API_KEY, resolve_credential


def probe_access(provider: str, *, video: bool = False) -> dict:
    result = {"provider": provider, "login": "unknown", "subscription": "unknown", "quota": "unknown",
              "billing": "subscription", "reason": "Subscription and media access need user confirmation when not independently known."}
    if provider == "codex":
        if shutil.which("codex") is None:
            return {**result, "login": "unavailable", "reason": "codex CLI is not installed"}
        try:
            check = subprocess.run([provider_binary("codex"), "login", "status"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=15, env=provider_subprocess_env())
        except (OSError, subprocess.SubprocessError):
            return {**result, "reason": "codex login status could not be checked"}
        if check.returncode != 0:
            return {**result, "login": "unavailable", "reason": "codex login status failed; sign in and check again"}
        # Never publish the CLI's raw output: some authentication modes can name keys.
        output = (check.stdout + check.stderr).lower()
        if "chatgpt" not in output:
            return {**result, "reason": "login succeeded but ChatGPT subscription authentication was not identified"}
        return {**result, "login": "ready"}
    if provider != "grok":
        raise ValueError(f"unknown provider: {provider}")
    if not video and shutil.which("grok") is None:
        return {**result, "login": "unavailable", "reason": "grok CLI is not installed"}
    try:
        # Image generation uses CLI login. Video may instead use the configured
        # API key; the existing video resolver alone owns that precedence.
        credential = resolve_credential(env=None if video else {})
    except (SystemExit, OSError, ValueError):
        return {**result, "login": "unavailable", "reason": "grok credential missing, expired or unreadable; check grok login"}
    if credential.source == AUTH_SOURCE_API_KEY:
        return {**result, "login": "ready", "billing": "api-credit",
                "reason": "Video will use XAI_API_KEY and separate API credit; confirm this billing choice."}
    return {**result, "login": "ready"}
