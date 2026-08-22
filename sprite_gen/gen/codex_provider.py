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
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .base import GEN_TIMEOUT_SECONDS, GenRequest, GenTimeoutError, ProviderRun, provider_binary, provider_subprocess_env, verify_png

# codex carries the inline base64 on a version-specific record. Both are
# first-class canonical records (not a fallback) — read whichever the running
# codex emits. v0.140.0: response_item `image_generation_call`. v0.144.1:
# event_msg `image_generation_end` (+status, saved_path — saved_path untrusted).
# v0.149.0: the model calls image_gen from inside the `exec` custom tool
# (`tools.image_gen__imagegen(...)`) and the PNG comes back as a
# `custom_tool_call_output` whose `output` list carries an `input_image`
# item with a `data:image/png;base64,...` URL.
_RESULT_TYPES = ("image_generation_call", "image_generation_end")
_EXEC_OUTPUT_TYPE = "custom_tool_call_output"
_DATA_URL_RE = re.compile(r"^data:image/png;base64,(?P<b64>[A-Za-z0-9+/=]+)$")


def _exec_output_images(payload: dict) -> list[str]:
    """Inline PNGs carried by a v0.149.0 `exec` tool output record."""
    output = payload.get("output")
    if not isinstance(output, list):
        return []
    found: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "input_image":
            continue
        match = _DATA_URL_RE.match(str(item.get("image_url", "")))
        if match:
            found.append(match.group("b64"))
    return found
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


def _collect_inline_results(rollout: Path) -> list[str]:
    results: list[str] = []
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
            if payload.get("type") == _EXEC_OUTPUT_TYPE:
                results.extend(_exec_output_images(payload))
                continue
            if payload.get("type") not in _RESULT_TYPES or not payload.get("result"):
                continue
            status = payload.get("status")
            if status is not None and status != "completed":
                raise SystemExit(f"codex-gen: image_gen call ended with status={status!r} in {rollout}")
            results.append(payload["result"])
    return results


def _decode_png(b64: str, dest: Path) -> None:
    raw = base64.b64decode(b64)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    verify_png(dest)


def _build_prompt(user_prompt: str) -> str:
    """Wrap the caller's prompt in the transport-level skill trigger.

    The sprite-request prompt is the caller's SSoT and is passed through verbatim;
    this adapter is the one place that owns the codex invocation contract.
    """
    return (
        f"{_SKILL_TRIGGER} 스킬로 built-in image_gen 도구를 정확히 1번 호출해서 "
        "다음 프롬프트의 이미지 1장만 생성해줘.\n"
        "미리보기 전용이라 생성된 파일은 기본 경로에 그대로 두면 된다.\n"
        "파일 저장·이동·복사·셸 명령·코드 작성·경로 보고 전부 금지. 생성만 하고 끝.\n\n"
        "프롬프트:\n"
        f"{user_prompt}\n"
    )


def _no_image_records_message(rollout: Path, codex_home: Path) -> str:
    """State an empty rollout as a capability failure, not a list of guesses.

    Built-in image generation is a capability of the account behind the active
    Codex state root. When the session is not offered the tool, it cannot be
    talked into it: not by the prompt, not by the model, and not by a config
    toggle. The only remedy is to run against a state root whose account provides
    it, so that is what this message says.
    """
    return (
        "codex-gen: the built-in image_gen tool never ran in this codex session — "
        f"zero {' / '.join(_RESULT_TYPES)} records in {rollout}\n"
        f"  the prompt carries the official {_SKILL_TRIGGER} skill trigger, so the tool was "
        "not offered to the session rather than merely not chosen.\n"
        f"  the account behind this Codex state root ({codex_home}) does not provide the "
        "built-in image_gen capability.\n"
        "  select a Codex state root whose account provides it (`codex login status`), or "
        "generate with another provider. A config feature toggle does not grant it."
    )


class CodexProvider:
    """Generate one image through codex `image_gen`."""

    name = "codex"

    def __init__(self, *, keep_session: bool = False) -> None:
        self.keep_session = keep_session

    def generate(self, request: GenRequest, workdir: Path) -> ProviderRun:
        codex_home = _resolve_codex_home()
        if not codex_home.is_dir():
            raise SystemExit(
                f"codex-gen: Codex state root does not exist or is not a directory: {codex_home}"
            )
        sessions_root = codex_home / "sessions"
        preexisting_rollouts = set(_rollout_files(sessions_root))
        gen_dir = codex_home / "generated_images"
        try:
            gen_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SystemExit(f"codex-gen: cannot create Codex generated-images dir {gen_dir}: {exc}") from exc
        cmd = [
            provider_binary("codex"),
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
        ]
        if request.model:
            cmd += ["--model", request.model]
        for ref in request.refs:
            cmd += ["-i", str(Path(ref).expanduser().resolve())]
        cmd += ["-", ]

        prompt = _build_prompt(request.prompt)
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
            raise SystemExit(
                f"codex-gen: codex exec exited {completed.returncode}\n{detail}"
            )

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
            raise SystemExit(_no_image_records_message(rollout, codex_home))
        _decode_png(results[-1], request.raw)

        if not self.keep_session:
            try:
                rollout.unlink()
            except OSError as exc:
                raise SystemExit(f"codex-gen: failed to remove consumed rollout {rollout}: {exc}") from exc

        return ProviderRun(
            provider=self.name,
            elapsed_seconds=elapsed,
            model=request.model,
            session_id=session_id,
            extra={"inline_results": len(results), "rollout_cleaned": not self.keep_session},
        )
