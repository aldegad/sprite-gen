# SPDX-License-Identifier: Apache-2.0
"""Codex `image_gen` provider (ChatGPT OAuth, no API key).

Ported from the standalone `image-gen` skill (MIT, aldegad/image-gen). codex
does not persist a discrete file; `image_gen` returns the PNG **inline as base64**
inside the session rollout jsonl. We spawn a fresh `codex exec` in an empty
sandbox (fresh session breaks OpenAI's prompt cache so repeat prompts don't drag
in a previous image), parse the session id from stdout, then decode the inline
base64 deterministically. The model-reported path is never trusted.

Decisive flags (each verified in the image-gen skill):
- `--sandbox workspace-write`  image_gen needs write access, else the tool never registers.
- `--add-dir <Codex state root>/generated_images`  not in the default writable set; the root is `CODEX_HOME` when set and `~/.codex` otherwise.
- `--skip-git-repo-check`  the sandbox dir is not a git repo.
- NO `--ephemeral`  the session jsonl must survive on disk so we can extract from it.

How codex is signed in is one question, `codex_login`, asked by every caller: the default
provider resolution, `generate`, the facing check and the guided workflow. Signed in with
ChatGPT it runs on the subscription; signed in with an API key it bills that key per call,
said on stderr before the request leaves like every metered route (`announce_api_billing`).
Whether `codex exec` ended on codex's usage limit is the other, `usage_limit`: a codex that is
not signed in or whose usage is used up is what a call that names no provider turns into the
user's choice (`gen.run_on_default`).
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .base import (
    GEN_TIMEOUT_SECONDS,
    LOGINS_OFF,
    PROVIDER_CODEX,
    TRANSPARENCY_NATIVE,
    announce_api_billing,
    GenRequest,
    GenTimeoutError,
    ProviderRun,
    logins_on,
    provider_binary,
    provider_subprocess_env,
    verify_png,
)

AUTH_SOURCE_LOGIN = "codex-login"  # signed in with ChatGPT: the subscription
AUTH_SOURCE_API_KEY = "codex-api-key"  # signed in with an API key: billed per call
LOGIN_PROBE_TIMEOUT_SECONDS = 15
BINARY = "codex"
LOGIN_HINT = "`codex login` signs your ChatGPT subscription in"

# The driver: the model the `codex exec` session of a generation runs on (docs/gen.md, "The codex driver model").
# image_gen draws the picture; the session only calls it once, so it runs on a light model at low reasoning effort,
# named on every call over the login's own config.toml. Measured on three requests (plain, --ref, --transparent):
# gpt-6.1-terra at low called image_gen once in each and took as long as a heavier model. `--model` names another
# model for one call; `sprite-gen defaults save --codex-model / --codex-effort` saves one. The order: `--model`,
# the saved one, these. A driver that cannot run fails naming it: nothing falls back to codex's own default.
DRIVER_MODEL = "gpt-6.1-terra"
DRIVER_EFFORT = "low"
# The efforts the saved setting takes, the levels codex lists for its models (`low` is on every one). The value is
# written into `-c model_reasoning_effort="…"`, so only these are taken.
DRIVER_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Their keys among the settings every profile shares (`workflow.preferences`, `general`; `workflow.catalog`).
DRIVER_MODEL_SETTING = "codex_model"
DRIVER_EFFORT_SETTING = "codex_effort"
DRIVER_FROM_FLAG, DRIVER_FROM_SAVED, DRIVER_FROM_BUILT_IN = "--model", "saved", "built-in"
# What a model id may be: it is passed to `codex exec --model` as one argument.
DRIVER_MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")


@dataclass(frozen=True)
class Driver:
    """The model and effort a generation's `codex exec` runs on, and where each came from (`DRIVER_FROM_*`)."""

    model: str
    effort: str
    model_from: str
    effort_from: str

    def report(self) -> dict[str, str]:
        return {"model": self.model, "effort": self.effort, "model_from": self.model_from,
                "effort_from": self.effort_from}

    def words(self) -> str:
        return f"driver model {self.model!r} ({self.model_from}) at {self.effort} effort ({self.effort_from})"


def driver_from(general: dict, model: str | None = None) -> Driver:
    """The driver for `model` (`--model`, None when not given) over the saved settings `general`: `--model`, then the
    saved one, then DRIVER_MODEL; the effort is the saved one, then DRIVER_EFFORT."""
    saved_model, saved_effort = general.get(DRIVER_MODEL_SETTING), general.get(DRIVER_EFFORT_SETTING)
    if model:
        chosen, model_from = model, DRIVER_FROM_FLAG
    elif saved_model:
        chosen, model_from = saved_model, DRIVER_FROM_SAVED
    else:
        chosen, model_from = DRIVER_MODEL, DRIVER_FROM_BUILT_IN
    if saved_effort:
        return Driver(chosen, saved_effort, model_from, DRIVER_FROM_SAVED)
    return Driver(chosen, DRIVER_EFFORT, model_from, DRIVER_FROM_BUILT_IN)


def resolve_driver(model: str | None = None) -> Driver:
    """`driver_from` over the saved settings. A settings file that cannot be read stops the call: a driver saved in
    it is not replaced by the built-in one in silence."""
    # The settings owner imports this package (its catalog names these keys), so it is imported here.
    from sprite_gen.workflow.preferences import read_settings

    try:
        general = read_settings()["general"]
    except (ValueError, OSError) as exc:
        raise SystemExit(f"codex driver: cannot read the saved one: {exc}") from exc
    return driver_from(general, model)


def driver_refused(said: list[str], model: str) -> str | None:
    """The first of what codex said (`_extract_stream_errors`) that refuses the driver model by name, or None. codex
    0.169.1 says "The '<model>' model is not supported when using Codex with a ChatGPT account." and exits 1."""
    quoted = (f"'{model}'", f"`{model}`", f'"{model}"')
    return next((message for message in said if any(name in message for name in quoted)
                 and any(words in message.lower() for words in ("not supported", "does not exist", "not found"))),
                None)


# What `codex exec` says when the codex usage of the account behind it is used up (2026-10-16, codex-rs source at
# rust-v0.160.1 and rust-v0.153.4). codex classes three of its errors as its usage limit
# (`CodexErr::to_codex_protocol_error` -> `CodexErrorInfo::UsageLimitExceeded`, codex-rs/protocol/src/error.rs):
# `UsageLimitReached` (a 429 `usage_limit_reached`; its message names the plan, a workspace out of credits or a
# spend cap), `QuotaExceeded` (a 429 `insufficient_quota` and its billing codes) and `UsageNotIncluded` (a 429
# `usage_not_included`). That class reaches `codex exec --json` only as the message text: the `error` and
# `turn.failed` events carry `message` alone (codex-rs/exec/src/event_processor_with_jsonl_output.rs, exec_events.rs),
# the error event is not kept in the rollout (codex-rs/rollout/src/policy.rs), and codex exits 1. So the signal is
# the start of each message, as codex's own tests pin it (codex-rs/protocol/src/error_tests.rs); 0.160.1 writes the
# apostrophe of "You've" as U+2019, 0.153.4 as an ASCII one. A message is matched anywhere in what codex said,
# since a remote compaction puts its own words before it. No other failure is read as the usage limit.
USAGE_LIMIT_MESSAGES = (
    "You've hit your usage limit",  # UsageLimitReached, codex 0.153.4 and before
    "You’ve hit your usage limit",  # UsageLimitReached, codex 0.160.1
    "Your workspace is out of credits.",  # UsageLimitReached, a workspace's credits
    "You hit your spend cap set",  # UsageLimitReached, a workspace's spend cap
    "Quota exceeded. Check your plan and billing details.",  # QuotaExceeded
    "To use Codex with your ChatGPT plan, upgrade to Plus",  # UsageNotIncluded
)


def usage_limit(said: list[str]) -> str | None:
    """The first of what codex said (`_extract_stream_errors`) that is its usage limit (USAGE_LIMIT_MESSAGES), or
    None."""
    return next((message for message in said if any(words in message for words in USAGE_LIMIT_MESSAGES)), None)


class CodexUsageLimit(SystemExit):
    """`codex exec` ended on its usage limit. The failure of a named `--provider codex` as before (same message,
    exit 1); a call that named no provider turns it into the user's choice (`gen.run_on_default`). `said` is codex's
    message."""

    def __init__(self, message: str, said: str) -> None:
        super().__init__(message)
        self.said = said


@dataclass(frozen=True)
class CodexLogin:
    source: str | None  # AUTH_SOURCE_LOGIN | AUTH_SOURCE_API_KEY | None (not usable here)
    reason: str = ""  # why it is not usable


def codex_login() -> CodexLogin:
    """How `codex` is signed in here, by `codex login status`: exit 0 and "ChatGPT" in what it
    says is the subscription; exit 0 otherwise is signed in with an API key. No CLI, a probe that
    fails or does not answer, and a non-zero exit are not usable, with the reason. What the CLI
    says is never repeated: an API-key login can name its key. With SPRITE_GEN_LOGINS=off codex is
    not asked and is not usable: its login is all it runs on."""
    if not logins_on():
        return CodexLogin(None, LOGINS_OFF)
    binary = provider_binary(BINARY)
    try:
        completed = subprocess.run([binary, "login", "status"], capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", env=provider_subprocess_env(),
                                   timeout=LOGIN_PROBE_TIMEOUT_SECONDS)
    except FileNotFoundError:
        return CodexLogin(None, "codex CLI not found on PATH")
    except (OSError, subprocess.SubprocessError) as exc:
        return CodexLogin(None, f"codex login-status probe failed: {type(exc).__name__}")
    if completed.returncode != 0:
        return CodexLogin(None, f"codex not logged in (codex login status exit {completed.returncode})")
    said = (completed.stdout + completed.stderr).lower()
    return CodexLogin(AUTH_SOURCE_LOGIN if "chatgpt" in said else AUTH_SOURCE_API_KEY)


def announce_codex_billing(login: CodexLogin, what: str = "") -> None:
    """The stderr charge line for a codex signed in with an API key; nothing on the subscription."""
    if login.source == AUTH_SOURCE_API_KEY:
        announce_api_billing("codex", "the API key codex is signed in with", f"{what} — {LOGIN_HINT}.")


# codex carries the inline base64 on a version-specific record. All are
# first-class canonical records (not fallbacks) — read whichever the running
# codex emits. v0.140.0: response_item `image_generation_call`. v0.144.1:
# event_msg `image_generation_end` (+status, saved_path — saved_path untrusted).
# v0.149.1: event_msg `item_completed` wrapping an Extension item with kind
# `image_gen.generation`.
_RESULT_TYPES = ("image_generation_call", "image_generation_end")
_IMAGE_GEN_EXTENSION_KIND = "image_gen.generation"
# The session id that names the rollout jsonl. v0.144.1 `codex exec --json` emits
# it as `{"type":"thread.started","thread_id":"<uuid>"}` on stdout; older codex
# printed a `session id: <uuid>` text line. Both are canonical per version.
_SID_RE = re.compile(r"session id: ([0-9a-f-]+)")
# The official way to invoke a codex skill explicitly is the `$<skill>` prompt
# mention, and image generation lives in the bundled `imagegen` system skill
# (`<Codex state root>/skills/.system/imagegen/agents/openai.yaml` ships
# `default_prompt: "Use $imagegen to make or edit an image for this project."`).
# We state the trigger rather than describing the tool in prose so the invocation
# contract is explicit and single-owned here.
#
# Scope of the trigger: naming the skill does not create the tool. Whether the
# built-in `image_gen` tool is offered to the session at all is a capability of
# the account behind the active Codex state root (see `_no_image_records_message`),
# so this trigger is an alignment with the official invocation contract, not a fix
# for a session that is never offered the tool.
_SKILL_TRIGGER = "$imagegen"
# Native transparency (2026-09-08 실측, codex 0.153.4): the bundled `imagegen` skill
# says "For transparent images, ask built-in image_gen for a transparent background
# and preserve the generated alpha", and the completed `image_gen.generation` item
# reports `transparentBackground: true` next to the real RGBA PNG. There is no
# tool parameter — the request rides on the prompt, so this adapter owns the
# exact wording (the caller's prompt is still passed through verbatim).
_NATIVE_ALPHA_INSTRUCTION = (
    "배경은 진짜 투명(알파 채널이 있는 PNG)으로 만들어라 — image_gen 에 transparent "
    "background 를 요청하고 생성된 알파를 그대로 보존해라. 체커보드 무늬·흰색·단색 배경을 "
    "그림으로 그려 넣는 것은 금지다."
)


@dataclass(frozen=True)
class InlineResult:
    """One completed image_gen call as recorded in the rollout."""

    result: str  # inline base64 PNG
    # `transparentBackground` as codex reported it (0.149+ Extension item);
    # None when the record type does not carry the field. Reported, never trusted —
    # the orchestrator measures the decoded PNG's alpha itself.
    transparent_background: bool | None = None


def _parse_session_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("thread_id"):
            return str(event["thread_id"])
    hits = _SID_RE.findall(stdout)
    return hits[-1] if hits else None


def _extract_stream_errors(stdout: str) -> list[str]:
    """Pull the human-readable failure cause out of the `codex exec --json` stream.

    codex reports a fatal error (e.g. an unsupported model) as a `turn.failed` or
    top-level `error` event on **stdout**, not stderr — so surfacing only the exit
    code and stderr leaves the real reason invisible. Unwraps the API error JSON
    that codex nests inside `message`."""
    msgs: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.failed":
            raw = (event.get("error") or {}).get("message")
        elif event.get("type") == "error":
            raw = event.get("message")
        else:
            continue
        if not raw:
            continue
        text = raw
        try:
            inner = json.loads(raw)
            if isinstance(inner, dict):
                text = (inner.get("error") or {}).get("message") or inner.get("message") or raw
        except (json.JSONDecodeError, TypeError):
            pass
        text = str(text).strip()
        if text and text not in msgs:
            msgs.append(text)
    return msgs


def _resolve_codex_home() -> Path:
    """Return the one state root used by both the Codex child and this adapter."""
    configured = os.environ.get("CODEX_HOME")
    if configured is None:
        return Path.home() / ".codex"
    if not configured.strip():
        raise SystemExit("codex-gen: CODEX_HOME is set but empty; refusing to use the default root")
    return Path(configured).expanduser().resolve()


def _rollout_files(sessions_root: Path) -> tuple[Path, ...]:
    """List rollout files deterministically without following a second state root."""
    if not sessions_root.exists():
        return ()
    if not sessions_root.is_dir():
        raise SystemExit(f"codex-gen: Codex sessions root is not a directory: {sessions_root}")
    try:
        return tuple(
            sorted(
                (path.resolve() for path in sessions_root.rglob("rollout-*.jsonl") if path.is_file()),
                key=lambda path: str(path),
            )
        )
    except OSError as exc:
        raise SystemExit(f"codex-gen: cannot scan Codex sessions root {sessions_root}: {exc}") from exc


def _resolve_rollout(
    session_id: str,
    sessions_root: Path,
    *,
    preexisting: set[Path] | frozenset[Path] | None = None,
) -> Path:
    """Resolve exactly one newly-created rollout for one Codex session."""
    suffix = f"-{session_id}.jsonl"
    hits = [path for path in _rollout_files(sessions_root) if path.name.endswith(suffix)]
    if not hits:
        raise SystemExit(
            f"codex-gen: no rollout jsonl for session {session_id!r} under {sessions_root}"
        )
    if len(hits) > 1:
        rendered = "\n".join(f"  - {path}" for path in hits)
        raise SystemExit(
            f"codex-gen: multiple rollout jsonl files for session {session_id!r} under "
            f"{sessions_root}; refusing to choose by mtime:\n{rendered}"
        )
    rollout = hits[0]
    stale = {path.resolve() for path in (preexisting or ())}
    if rollout in stale:
        raise SystemExit(
            f"codex-gen: pre-existing stale rollout for session {session_id!r}: {rollout}"
        )
    return rollout


def _collect_inline_results(rollout: Path) -> list[InlineResult]:
    results: list[InlineResult] = []
    with rollout.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get("payload", {}) or {}
            if payload.get("type") == "item_completed":
                item = payload.get("item", {}) or {}
                if item.get("type") != "Extension" or item.get("kind") != _IMAGE_GEN_EXTENSION_KIND:
                    continue
                status = item.get("status")
                if status != "completed":
                    raise SystemExit(
                        f"codex-gen: image_gen call ended with status={status!r} in {rollout}"
                    )
                result = item.get("result")
                if not result:
                    raise SystemExit(
                        f"codex-gen: completed image_gen call has no result in {rollout}"
                    )
                reported = item.get("transparentBackground")
                results.append(
                    InlineResult(
                        result=result,
                        transparent_background=reported if isinstance(reported, bool) else None,
                    )
                )
                continue
            if payload.get("type") not in _RESULT_TYPES or not payload.get("result"):
                continue
            status = payload.get("status")
            if status is not None and status != "completed":
                raise SystemExit(f"codex-gen: image_gen call ended with status={status!r} in {rollout}")
            results.append(InlineResult(result=payload["result"]))
    return results


def _decode_png(b64: str, dest: Path) -> None:
    raw = base64.b64decode(b64)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    verify_png(dest)


def _build_prompt(user_prompt: str, *, native_alpha: bool = False) -> str:
    """Wrap the caller's prompt in the transport-level skill trigger.

    The sprite-request prompt is the caller's SSoT and is passed through verbatim;
    this adapter is the one place that owns the codex invocation contract. With
    `native_alpha` the transport also carries the transparent-background request
    (`_NATIVE_ALPHA_INSTRUCTION`) — that is how image_gen is told to return alpha.
    """
    alpha_line = f"{_NATIVE_ALPHA_INSTRUCTION}\n" if native_alpha else ""
    return (
        f"{_SKILL_TRIGGER} 스킬로 built-in image_gen 도구를 정확히 1번 호출해서 "
        "다음 프롬프트의 이미지 1장만 생성해줘.\n"
        "미리보기 전용이라 생성된 파일은 기본 경로에 그대로 두면 된다.\n"
        "파일 저장·이동·복사·셸 명령·코드 작성·경로 보고 전부 금지. 생성만 하고 끝.\n"
        f"{alpha_line}\n"
        "프롬프트:\n"
        f"{user_prompt}\n"
    )


def _no_image_records_message(rollout: Path, codex_home: Path, driver: Driver) -> str:
    """State an empty rollout as a capability failure, not a list of guesses.

    Built-in image generation is offered by the account behind the active Codex
    state root, on the models that carry it. When the session is not offered the
    tool, it cannot be talked into it: not by the prompt and not by a config
    toggle. The remedies are a driver model that carries it and a state root whose
    account provides it, so that is what this message says.
    """
    return (
        "codex-gen: the built-in image_gen tool never ran in this codex session — "
        f"zero {' / '.join((*_RESULT_TYPES, _IMAGE_GEN_EXTENSION_KIND))} records in {rollout}\n"
        f"  the prompt carries the official {_SKILL_TRIGGER} skill trigger, so the tool was "
        "not offered to the session rather than merely not chosen.\n"
        f"  the session ran on the {driver.words()}; a model that does not carry image_gen "
        f"ends the same way: name another with --model (the built-in driver is {DRIVER_MODEL!r}).\n"
        f"  on a driver that carries it, the account behind this Codex state root ({codex_home}) "
        "does not provide the built-in image_gen capability: select a Codex state root whose "
        "account provides it (`codex login status`), or generate with another provider. A config "
        "feature toggle does not grant it."
    )


def _driver_refused_message(driver: Driver, said: str) -> str:
    to_change = {DRIVER_FROM_FLAG: "drop --model or name another",
                 DRIVER_FROM_SAVED: "save another with `sprite-gen defaults save --codex-model MODEL`, clear the "
                                    "saved one with `sprite-gen defaults clear --setting codex_model`, or name one "
                                    "with --model",
                 DRIVER_FROM_BUILT_IN: "name one with --model, or save one with `sprite-gen defaults save "
                                       "--codex-model MODEL`"}[driver.model_from]
    return (f"codex-gen: codex did not run the {driver.words()}: {said}\n"
            f"  nothing fell back to codex's own default model; {to_change}.")


class CodexProvider:
    """Generate one image through codex `image_gen`."""

    name = PROVIDER_CODEX
    transparency = TRANSPARENCY_NATIVE

    def __init__(self, *, keep_session: bool = False) -> None:
        self.keep_session = keep_session

    def inspect_facing(self, path: Path, workdir: Path) -> tuple[str, dict]:
        from .facing_vision import CODEX_READ_EFFORT, CODEX_READ_MODEL, codex_inspect
        return codex_inspect(path, workdir, model=CODEX_READ_MODEL, effort=CODEX_READ_EFFORT)

    def inspect_image(self, path: Path, workdir: Path, prompt: str, *, label: str = "vision check") -> tuple[str, dict]:
        from .facing_vision import CODEX_READ_EFFORT, CODEX_READ_MODEL, codex_inspect
        return codex_inspect(path, workdir, prompt=prompt, label=label, model=CODEX_READ_MODEL,
                             effort=CODEX_READ_EFFORT)

    def generate(self, request: GenRequest, workdir: Path) -> ProviderRun:
        # `image_gen` exposes no effort or size dial, so neither knob is carried
        # into the prompt. Refusing beats returning a default-effort image under a
        # level the caller chose and believes they got (No Silent Fallback).
        for flag, value in (("--quality", request.quality), ("--resolution", request.resolution)):
            if value is not None:
                raise SystemExit(
                    f"codex-gen: {flag} is not carried into codex image_gen; "
                    "drop it or use --provider openai"
                )
        if not logins_on():
            raise SystemExit(f"codex-gen: {LOGINS_OFF}; codex runs on its own login only — "
                             "name --provider openai or grok, which run on an API key")
        codex_home = _resolve_codex_home()
        if not codex_home.is_dir():
            raise SystemExit(
                f"codex-gen: Codex state root does not exist or is not a directory: {codex_home}"
            )
        login = codex_login()
        if login.source is None:
            raise SystemExit(f"codex-gen: {login.reason}; {LOGIN_HINT}")
        driver = resolve_driver(request.model)
        announce_codex_billing(login)
        sessions_root = codex_home / "sessions"
        preexisting_rollouts = set(_rollout_files(sessions_root))
        gen_dir = codex_home / "generated_images"
        try:
            gen_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SystemExit(f"codex-gen: cannot create Codex generated-images dir {gen_dir}: {exc}") from exc
        cmd = [
            provider_binary(BINARY),
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--add-dir",
            str(gen_dir),
            "-C",
            str(workdir),
            # before the image list, which takes every path after it
            "--model",
            driver.model,
            "-c",
            f'model_reasoning_effort="{driver.effort}"',
        ]
        for ref in request.refs:
            cmd += ["-i", str(Path(ref).expanduser().resolve())]
        cmd += ["-", ]

        prompt = _build_prompt(request.prompt, native_alpha=request.native_alpha)
        started = time.monotonic()
        child_env = provider_subprocess_env()
        if "CODEX_HOME" in os.environ:
            child_env["CODEX_HOME"] = str(codex_home)
        # A headless provider must not inherit parent orchestration identity or
        # lifecycle controls (see base.provider_subprocess_env).
        try:
            completed = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", env=child_env,
                timeout=GEN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise GenTimeoutError(
                f"codex-gen: no completion within {GEN_TIMEOUT_SECONDS}s — "
                "provider stream stalled; child killed (gen-timeout)")
        elapsed = time.monotonic() - started
        stdout = completed.stdout or ""
        if completed.returncode != 0:
            stream_errors = _extract_stream_errors(stdout)
            tail = (completed.stderr or "").strip().splitlines()[-20:]
            detail = "\n".join(stream_errors + tail).strip() or "(no error detail on stdout/stderr)"
            message = f"codex-gen: codex exec exited {completed.returncode} on the {driver.words()}\n{detail}"
            said = usage_limit(stream_errors)
            if said:
                raise CodexUsageLimit(message, said)
            refused = driver_refused(stream_errors, driver.model)
            raise SystemExit(_driver_refused_message(driver, refused) if refused else message)

        session_id = _parse_session_id(stdout)
        if not session_id:
            raise SystemExit(
                "codex-gen: no thread/session id in codex stdout — image_gen was not reached.\n"
                "  check `codex login status` and `--sandbox workspace-write`."
            )
        rollout = _resolve_rollout(
            session_id,
            sessions_root,
            preexisting=preexisting_rollouts,
        )
        results = _collect_inline_results(rollout)
        if not results:
            raise SystemExit(_no_image_records_message(rollout, codex_home, driver))
        final = results[-1]
        _decode_png(final.result, request.raw)

        if not self.keep_session:
            try:
                rollout.unlink()
            except OSError as exc:
                raise SystemExit(f"codex-gen: failed to remove consumed rollout {rollout}: {exc}") from exc

        return ProviderRun(
            provider=self.name,
            elapsed_seconds=elapsed,
            model=driver.model,
            session_id=session_id,
            extra={
                "auth_source": login.source,
                "driver": driver.report(),
                "inline_results": len(results),
                "rollout_cleaned": not self.keep_session,
                # codex's own claim about the output; the orchestrator measures
                # the real alpha and publishes that, not this flag.
                "transparent_background_reported": final.transparent_background,
            },
        )
