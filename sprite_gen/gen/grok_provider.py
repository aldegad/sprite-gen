# SPDX-License-Identifier: Apache-2.0
"""Grok Imagine provider (xAI OAuth, no API key).

grok build ships an Imagine skill with `image_gen` (text -> new image) and
`image_edit` (edit an existing image). Unlike codex, grok's tool result is not
inline base64 — grok (the agent) holds the produced file and can move it. We run
grok headless with `--always-approve` (media/shell must be auto-approved; plain
`--write`/acceptEdits blocks tool execution and returns an empty answer) and
instruct it to write the final PNG to an exact absolute path. Truth is the PNG on
disk at that path, never grok's text (No Silent Fallback).

Model note: image tools route to the grok-build model, which 400s on
`reasoningEffort` — so this provider never passes `--effort`.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .base import GenRequest, ProviderRun, provider_subprocess_env, verify_png


def _build_prompt(request: GenRequest) -> str:
    raw = str(request.raw)
    aspect = request.aspect_ratio or "auto"
    lines = [
        "You are generating exactly one image, then saving it. Do only this:",
        "",
    ]
    if request.refs:
        ref_list = ", ".join(str(Path(r).expanduser().resolve()) for r in request.refs)
        lines += [
            f"1. Call `image_edit` once using these source image(s) as reference: {ref_list}",
            "   Apply this instruction while keeping the referenced subject/style consistent:",
            f"   {request.prompt}",
        ]
    else:
        lines += [
            f"1. Call `image_gen` once with aspect_ratio `{aspect}` and this prompt:",
            f"   {request.prompt}",
        ]
    lines += [
        f"2. Save the produced image to EXACTLY this absolute path as a PNG file: {raw}",
        "   Overwrite it if it already exists. Use the shell to copy/convert the produced",
        "   file to that path if the tool wrote it elsewhere. Do not resize or restyle it.",
        f"3. Print only the final absolute path ({raw}). Do not do anything else — no extra",
        "   images, no edits, no commentary, no other files.",
    ]
    return "\n".join(lines)


class GrokProvider:
    """Generate one image through grok Imagine `image_gen` / `image_edit`."""

    name = "grok"

    def generate(self, request: GenRequest, workdir: Path) -> ProviderRun:
        request.raw.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "grok",
            "-p",
            _build_prompt(request),
            "--output-format",
            "plain",
            "--sandbox",
            "workspace",
            "--always-approve",
            "--cwd",
            str(workdir),
        ]
        if request.model:
            cmd += ["-m", request.model]

        started = time.monotonic()
        # env: parent minus orchestrator session env — same reason as codex
        # (base.provider_subprocess_env). grok hooks are off today, but a headless
        # generation subprocess must never carry the parent worker's endpoint
        # identity regardless of the engine's current hook config.
        completed = subprocess.run(cmd, capture_output=True, text=True, env=provider_subprocess_env())
        elapsed = time.monotonic() - started
        if completed.returncode != 0:
            tail = (completed.stderr or "").strip().splitlines()[-20:]
            raise SystemExit(
                f"grok-gen: grok exited {completed.returncode} (empty answer + non-zero = blocked exec or login).\n"
                + "\n".join(tail)
            )
        # Verify the real file grok wrote, not its text. Missing/not-a-PNG fails loudly.
        verify_png(request.raw)

        return ProviderRun(
            provider=self.name,
            elapsed_seconds=elapsed,
            model=request.model,
            session_id=None,
            extra={"stdout_tail": "\n".join((completed.stdout or "").strip().splitlines()[-5:])},
        )
