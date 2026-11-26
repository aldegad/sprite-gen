# SPDX-License-Identifier: Apache-2.0
"""Single-call vision transports for the existing generation providers.

REST input/output contract: OpenAI and xAI Responses image understanding docs.
No generated image is sent to a different provider or authentication route.
One question per call: the facing check asks `facing.INSPECTION_PROMPT` (the default,
its body unchanged); `sprite-gen logo` asks what the lettering spells (`inspect_image`).
"""
from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time
from pathlib import Path

from .base import GEN_TIMEOUT_SECONDS, provider_binary, provider_subprocess_env
from .facing import INSPECTION_PROMPT

OPENAI_MODEL = "gpt-4.1-mini"
GROK_MODEL = "grok-4.6"
# What codex reads a picture on: a logo's lettering (`inspect_image`, docs/logo.md, the text check) and a facing
# check (`inspect_facing`, docs/gen.md, "The codex driver model"). Named on the call over the login's own config, so
# a check reads on the same model and effort on every machine. Not the light driver of a generation
# (`codex_provider.DRIVER_MODEL`): the driver only calls image_gen, a read is the verdict a gate acts on.
CODEX_READ_MODEL = "gpt-6-sol"
CODEX_READ_EFFORT = "high"
FACING_LABEL = "facing vision check"


def _tag(label: str) -> str:
    """The prefix of a failure line: "facing vision check" -> "facing-vision"."""
    return label.removesuffix(" check").replace(" ", "-")


def request_body(path: Path, model: str, *, prompt: str = INSPECTION_PROMPT, detail: str = "low") -> dict:
    return {"model": model, "store": False, "max_output_tokens": 256,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": "data:image/png;base64," +
                 base64.b64encode(path.read_bytes()).decode("ascii"), "detail": detail},
            ]}]}


def response_text(status: int, reply, *, label: str = FACING_LABEL) -> tuple[str, dict]:
    if status != 200:
        raise SystemExit(f"{_tag(label)}: HTTP {status}; not asked again, no fallback")
    if not isinstance(reply, dict):
        raise ValueError("invalid vision envelope")
    text = "".join(part["text"] for item in reply.get("output", [])
                   if item.get("type") == "message"
                   for part in item.get("content", []) if part.get("type") == "output_text")
    return text, {"model": reply.get("model"), "usage": reply.get("usage"),
                  "response_status": reply.get("status")}


def codex_inspect(path: Path, workdir: Path, *, prompt: str = INSPECTION_PROMPT, label: str = FACING_LABEL,
                  model: str | None = None, effort: str | None = None) -> tuple[str, dict]:
    # Isolated cwd and ephemeral session: no project instructions, no stale
    # output reuse and no persistent vision rollouts. Uses the existing login,
    # on its config's model and effort unless the call names them.
    from .codex_provider import BINARY, announce_codex_billing, codex_login
    login = codex_login()
    if login.source is None:
        raise SystemExit(f"{_tag(label)}: {login.reason}")
    announce_codex_billing(login, f" ({label})")
    with tempfile.TemporaryDirectory(prefix="facing-", dir=workdir) as directory:
        output = Path(directory) / "answer.txt"
        command = [provider_binary(BINARY), "exec", "--sandbox", "read-only",
                   "--skip-git-repo-check", "--ephemeral", "--color", "never", "-C", directory]
        if model:
            command += ["--model", model]
        if effort:
            command += ["-c", f'model_reasoning_effort="{effort}"']
        command += ["-i", str(path.resolve()), "-o", str(output), "-"]
        completed = subprocess.run(command, input=prompt, capture_output=True,
                                   text=True, encoding="utf-8", env=provider_subprocess_env(),
                                   timeout=GEN_TIMEOUT_SECONDS)
        if completed.returncode:
            raise SystemExit(f"{_tag(label)}: codex exited {completed.returncode}")
        named = {key: value for key, value in (("model", model), ("effort", effort)) if value}
        return output.read_text(encoding="utf-8"), {"transport": "codex-exec", "auth_source": login.source, **named}


def grok_inspect(path: Path, *, credential=None, call=None, prompt: str = INSPECTION_PROMPT,
                 detail: str = "low", label: str = FACING_LABEL) -> tuple[str, dict]:
    from . import address, xai
    from .base import announce_api_billing
    credential = credential or xai.resolve_credential()
    if credential.source == xai.AUTH_SOURCE_API_KEY:
        announce_api_billing("grok", xai.AUTH_ENV, f" ({label}).")
    # The question's time limit is the transport's read timeout, from its first send: the waits before a
    # refused one goes again count in it (`retry`), and each send reads only what is left of it.
    send, deadline = call or xai.http_json, time.monotonic() + xai.HTTP_TIMEOUT_SECONDS
    status, reply, _sends = xai.post(lambda *a: send(*a, deadline=deadline), address.url(address.XAI, "/responses"),
                                     credential.token, request_body(path, GROK_MODEL, prompt=prompt, detail=detail),
                                     who="grok", label=_tag(label), deadline=deadline)
    text, metadata = response_text(status, reply, label=label)
    return text, {**metadata, "auth_source": credential.source, "transport": "xai-api"}
