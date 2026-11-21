# SPDX-License-Identifier: Apache-2.0
"""Claude calls on the person's own subscription first: is a login here, which credential a call runs
on, and one message answered through the unmodified `claude` binary (Claude Code) in print mode.

The order is `codex`'s and `grok`'s: the subscription the person already pays for, and an API key only
when no subscription login is here. `resolve_credential` answers it. A login that is here but refuses,
expires or reaches its usage limit fails the call and never switches to the key (`xai.resolve_credential`
keeps the same rule for Grok). Message Batches exist only on the API, so `--batch` names the key.
This tool never reads, stores or forwards the login: the binary signs itself in.

What the binary does, as code.claude.com/docs documents it (read 2026-10-15) and as measured with
claude 2.1.300 the same day:

- `claude auth status --json` exits 0 signed in and 1 not; `authMethod` is one of none, claude.ai,
  oauth_token, api_key, api_key_helper, third_party. A subscription is `claude.ai` (`/login`) or
  `oauth_token` (`claude setup-token`); the others bill an API key or a cloud account.
- In print mode an `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` in the environment is always used
  when present, so the child runs without them. `--bare` never reads the login and is not used.
- `-p --input-format stream-json --output-format stream-json --verbose` takes one user message of
  content blocks (text, a base64 image) on stdin and ends with a `result` event: `result` (the text),
  `structured_output` (with `--json-schema`), `usage` (the Messages API's), `stop_reason`, `is_error`,
  `subtype`, `api_error_status`. An answer that reaches the output cap is not returned as a cut answer:
  the binary asks for a continuation, which goes on in another assistant message (so the answer is every
  assistant text in order — `result` holds only the last part), and when continuations do not finish it
  ends `is_error` with "exceeded the N output token maximum" (`OUTPUT_CAP_ERROR`); that is the API's
  `stop_reason: max_tokens`.
- `--system-prompt` replaces Claude Code's own prompt (an empty one is accepted), `--tools ""` and
  `--strict-mcp-config` leave no tool, `--safe-mode` loads no CLAUDE.md, hook, skill, plugin or MCP
  server while the login still works, `--no-session-persistence` writes no session, `--effort` sets the
  effort and `CLAUDE_CODE_MAX_OUTPUT_TOKENS` the output cap.

Anthropic permits an end user signing in to the unmodified Claude Code binary with their own
subscription; it does not permit routing other people's requests through one
(code.claude.com/docs/en/legal-and-compliance.md). This route is for the person running the tool on
their own machine. A service that answers other people runs on `ANTHROPIC_API_KEY`: no login exists
in its container, so that is the route it gets.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from . import anthropic_api as api
from .base import LOGINS_ENV, env_api_key, logins_on, provider_binary, provider_subprocess_env

BINARY = "claude"
AUTH_SOURCE_LOGIN = "claude-login"
AUTH_SOURCE_API_KEY = api.AUTH_ENV
SUBSCRIPTION_METHODS = ("claude.ai", "oauth_token")
LOGIN_HINT = "`claude` then `/login` signs your Claude subscription in"

# What the child must not inherit: a key (print mode uses it instead of the login), an effort that
# would win over `--effort`, and the calling Claude Code session's own variables (an agent running
# this tool must not make the child part of its session). `CLAUDE_CODE_*` are Claude Code's; the
# documented ones that choose how it signs in stay.
CHILD_DROP = (api.AUTH_ENV, "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT")
CHILD_KEEP = ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
              "CLAUDE_CODE_USE_FOUNDRY")

STATUS_TIMEOUT_SECONDS = 30
# One whole call, thinking included: a 48x48 sheet of four frames at effort high answered in up to ten
# minutes on the API (2026-10-14). The binary keeps no stream open to us, so the bound is the call's.
CALL_TIMEOUT_SECONDS = float(os.environ.get("SPRITE_GEN_CLAUDE_CALL_TIMEOUT_SECONDS", "1800"))
OUTPUT_CAP_ERROR = re.compile(r"exceeded the \d+ output token maximum")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class Login:
    method: str  # SUBSCRIPTION_METHODS
    plan: str | None = None  # `subscriptionType` as the binary says it


@dataclass(frozen=True)
class Credential:
    source: str  # AUTH_SOURCE_LOGIN | AUTH_SOURCE_API_KEY
    token: str | None = field(default=None, repr=False)  # the API key; None on the login
    login: Login | None = None


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    return {key: value for key, value in provider_subprocess_env(env).items()
            if key not in CHILD_DROP and not (key.startswith("CLAUDE_CODE_") and key not in CHILD_KEEP)}


def login(env: Mapping[str, str] | None = None, *, run: Run = subprocess.run) -> Login | None:
    """The subscription login the binary would sign in with here, or None: the binary is not installed,
    nobody is signed in, or what signs it in is not a subscription. An answer that cannot be read is
    refused — not knowing is not the same as no login."""
    try:
        # The binary writes UTF-8. What is read is ASCII names, so a byte that is not UTF-8 is replaced, and an
        # answer that then does not parse is refused below instead of raising here.
        done = run([provider_binary(BINARY), "auth", "status", "--json"], env=child_env(env),
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=STATUS_TIMEOUT_SECONDS)
    except FileNotFoundError:
        return None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"pixel-draw: `claude auth status` did not answer ({exc}); "
                         "refusing to guess whether your Claude subscription is signed in") from exc
    try:
        status = json.loads(done.stdout)
    except json.JSONDecodeError:
        status = None
    if done.returncode not in (0, 1) or not isinstance(status, dict):
        raise SystemExit(f"pixel-draw: `claude auth status --json` answered exit {done.returncode} with "
                         f"{(done.stdout or done.stderr).strip()[:200]!r}; refusing to guess whether your "
                         "Claude subscription is signed in")
    method = status.get("authMethod")
    if not status.get("loggedIn") or method not in SUBSCRIPTION_METHODS:
        return None
    plan = status.get("subscriptionType")
    return Login(method=method, plan=plan if isinstance(plan, str) else None)


def resolve_credential(*, batch: bool = False, env: Mapping[str, str] | None = None,
                       run: Run = subprocess.run) -> Credential:
    """The subscription login first; `ANTHROPIC_API_KEY` only when no login is here, for a Message Batch, which
    only the API runs, or with SPRITE_GEN_LOGINS=off, which asks the binary nothing. Neither is a refusal naming
    both."""
    asks = not batch and logins_on(env)
    if asks:
        found = login(env, run=run)
        if found is not None:
            return Credential(AUTH_SOURCE_LOGIN, login=found)
    key = env_api_key(api.AUTH_ENV, env, who="pixel-draw")
    if key:
        return Credential(AUTH_SOURCE_API_KEY, token=key)
    if not batch and not asks:
        raise SystemExit(f"pixel-draw: no Claude credential — {LOGINS_ENV}=off reads no subscription login and "
                         f"{api.AUTH_ENV} is not set.\n  export {api.AUTH_ENV}=<your Claude Console API key> "
                         f"(platform.claude.com/settings/keys), or unset {LOGINS_ENV} to draw on your Claude subscription.")
    if batch:
        raise SystemExit(f"pixel-draw: --batch is Anthropic's Message Batches API, which runs on {api.AUTH_ENV} "
                         f"only, and it is not set.\n  export {api.AUTH_ENV}=<your Claude Console API key>, or "
                         "drop --batch to draw on your Claude subscription.")
    raise SystemExit(
        f"pixel-draw: no Claude credential — no subscription login (`claude auth status`) and {api.AUTH_ENV} "
        f"is not set.\n  {LOGIN_HINT} (no per-call charge), or export {api.AUTH_ENV}=<your Claude Console "
        "API key> (platform.claude.com/settings/keys).")


def _args(body: dict[str, Any]) -> list[str]:
    args = [provider_binary(BINARY), "-p", "--model", body["model"], "--effort", body["output_config"]["effort"],
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--system-prompt", body.get("system") or "", "--tools", "", "--strict-mcp-config",
            "--no-session-persistence", "--safe-mode", "--max-turns", "1"]
    schema = (body["output_config"].get("format") or {}).get("schema")
    if schema is not None:
        args += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    return args


def _message(body: dict[str, Any]) -> str:
    content = body["messages"][0]["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
    return json.dumps({"type": "user", "message": {"role": "user", "content": blocks}}) + "\n"


def send_message(body: dict[str, Any], *, label: str, env: Mapping[str, str] | None = None,
                 run: Run = subprocess.run) -> api.Reply:
    """`body` (an `anthropic_api.message_body`) answered on the subscription: its model, effort, system
    prompt, output cap, schema and content as the binary's flags and stdin, in an empty directory. The
    reply is the `result` event's; a cut answer comes back with `stop_reason` max_tokens and the text
    written before the cut, an error as `ApiError`."""
    started = time.monotonic()
    child = {**child_env(env), "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(body["max_tokens"])}
    with tempfile.TemporaryDirectory(prefix="sprite-gen-claude-") as cwd:
        try:
            # The binary writes UTF-8 and its reply is the drawing, so a byte that is not UTF-8 is never read as
            # another character. Where the streams are decoded decides what that stops: on Windows each on a reader
            # thread of its own, which leaves one it cannot decode None, so a stdout that does not decode fails the
            # call below and a stderr alone that does not is lost while the reply comes back; elsewhere both here,
            # where either fails the call.
            done = run(_args(body), input=_message(body), env=child, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="strict", timeout=CALL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise api.ApiError(f"{label}: `claude -p` gave no answer in {CALL_TIMEOUT_SECONDS:g}s and was stopped "
                               "(SPRITE_GEN_CLAUDE_CALL_TIMEOUT_SECONDS)", billed=True) from exc
        except UnicodeDecodeError as exc:
            raise api.ApiError(f"{label}: `claude -p` answered with a byte that is not UTF-8 ({exc}); the answer is "
                               "not read", billed=True) from exc
        except OSError as exc:
            raise api.ApiError(f"{label}: `claude -p` did not start ({exc})", billed=False) from exc
    if done.stdout is None:
        raise api.ApiError(f"{label}: `claude -p` answered with a byte that is not UTF-8 on stdout; the answer is not "
                           "read", billed=True)
    seconds = round(time.monotonic() - started, 3)
    result: dict[str, Any] | None = None
    message_id, texts, thinking = None, [], []
    for line in done.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            message = event.get("message") or {}
            message_id = message.get("id") or message_id
            for block in message.get("content") or []:
                if block.get("type") == "text":
                    texts.append(block.get("text") or "")
                elif block.get("type") == "thinking" and block.get("thinking"):
                    thinking.append(block["thinking"])
        elif event.get("type") == "result":
            result = event
    if result is None:
        said = (done.stderr or done.stdout).strip()[-300:]
        raise api.ApiError(f"{label}: `claude -p` ended with exit {done.returncode} and no result: {said!r}",
                           billed=True)
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    said = str(result.get("result") or "")
    if result.get("is_error"):
        if OUTPUT_CAP_ERROR.search(said):
            return api.Reply(text="".join(texts), thinking="\n".join(thinking), stop_reason="max_tokens",
                             usage=usage, model=body["model"], message_id=message_id, mode="subscription",
                             seconds=seconds)
        status = result.get("api_error_status")
        raise api.ApiError(f"{label}: the Claude subscription did not answer ({result.get('subtype')}"
                           f"{f', HTTP {status}' if status else ''}): {said[:300]}",
                           status=status if isinstance(status, int) else None,
                           billed=bool(usage.get("output_tokens")))
    # The answer is every text the assistant wrote, in order: past the output cap the binary asks for a
    # continuation and `result` holds only the last part (2026-10-15: fence and palette lost).
    structured = result.get("structured_output")
    text = json.dumps(structured, ensure_ascii=False) if structured is not None else ("".join(texts) or said)
    return api.Reply(text=text, thinking="\n".join(thinking), stop_reason=result.get("stop_reason"), usage=usage,
                     model=body["model"], message_id=message_id, mode="subscription", seconds=seconds)
