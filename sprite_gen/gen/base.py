# SPDX-License-Identifier: Apache-2.0
"""Shared contract for sprite-gen image generation providers.

One generation call = prompt (+ optional reference images) -> one verified raw
PNG on disk. Providers own the model call and its timing; the orchestrator in
`sprite_gen.gen` owns the optional transparent chroma post-process and the
report. Truth is always the decoded PNG bytes on disk, never a model-reported
path or a "done" string (No Silent Fallback).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Transparency strategy — a capability each provider declares exactly once
# (`Provider.transparency`). The orchestrator reads it; nothing else decides.
#   native — the model returns a genuinely transparent PNG (alpha channel) when
#            the prompt asks for a transparent background; the raw alpha is
#            measured and published (codex `image_gen`, 2026-09-08 실측).
#   chroma — the model cannot return alpha; generate on a chroma key and key it
#            out deterministically downstream (grok Imagine: API/CLI 모두 JPEG).
TRANSPARENCY_NATIVE = "native"
TRANSPARENCY_CHROMA = "chroma"
TRANSPARENCY_STRATEGIES = (TRANSPARENCY_NATIVE, TRANSPARENCY_CHROMA)

# Child provider processes are independent execution contexts. They must not
# inherit parent orchestration identity or lifecycle controls, while ordinary
# variables such as PATH remain available. Suffix matching keeps the contract
# provider-neutral and covers any orchestration namespace.
_ORCHESTRATOR_SESSION_ENV_SUFFIXES = (
    "_RUNTIME_ENDPOINT_ID",
    "_MEMBER_ID",
    "_PROJECT_ID",
    "_PLAN_EXIT_GATE",
    "_STUDIO_PORT",
)


# 생성 서브프로세스 하드 타임아웃 — provider 스트림이 드물게 무출력으로 매달린다
# (회귀 2026-07-19: 15행 배치 중 1행의 codex exec 이 1시간 38분 무출력 — 같은 env
# 로 14행이 성공했으니 세션/훅 충돌이 아니라 provider 측 산발 스톨. 킬 외엔 답이
# 없으므로 바운드가 유일한 방어). 기본 180초 (maintainer 확정 2026-07-19; 실측 정상 최장 129초).
GEN_TIMEOUT_SECONDS = int(os.environ.get("SPRITE_GEN_GEN_TIMEOUT_SECONDS", "180"))


class GenTimeoutError(SystemExit):
    """생성 서브프로세스가 GEN_TIMEOUT_SECONDS 안에 끝나지 않아 킬됨 (관측 가능)."""


def provider_subprocess_env() -> dict[str, str]:
    """Environment for a headless generation subprocess.

    The parent environment minus known orchestrator session env families, so a
    spawned engine (`codex exec`, `grok`) is a clean standalone process — it
    neither impersonates the spawning agent nor gets strangled by the
    orchestrator's hooks. SSoT for every provider's `subprocess.run` env —
    providers must not spawn with the inherited env directly.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.endswith(_ORCHESTRATOR_SESSION_ENV_SUFFIXES)
    }
    return env


def provider_binary(name: str) -> str:
    """Resolve a provider CLI through PATH, including Windows PATHEXT shims.

    npm installs provider CLIs as ``.cmd`` shims on Windows. ``CreateProcess``
    does not resolve those from a bare name, while ``shutil.which`` does. A
    missing command stays bare so the provider spawn still owns the observable
    failure instead of introducing a second availability answer here.
    """
    return shutil.which(name) or name


def verify_png(path: Path) -> int:
    """Return the PNG byte count, or raise SystemExit if it is missing/not a PNG."""
    if not path.is_file():
        raise SystemExit(f"gen: expected a generated PNG at {path}, but no file was written")
    data = path.read_bytes()
    if data[:8] != PNG_MAGIC:
        raise SystemExit(f"gen: file at {path} is not a PNG (magic mismatch) — refusing to claim success")
    return len(data)


@dataclass
class GenRequest:
    """A single image generation request."""

    prompt: str
    raw: Path  # provider writes the generated PNG (chroma background included) here
    refs: list[Path] = field(default_factory=list)
    model: str | None = None
    aspect_ratio: str | None = None  # grok honours this; codex ignores it
    # Ask the model for a genuinely transparent background (alpha channel). Only
    # legal for a provider whose `transparency` is `native`; the orchestrator gates
    # it and the provider carries the request into its transport prompt.
    native_alpha: bool = False


@dataclass
class ProviderRun:
    """What a provider reports after writing `request.raw`."""

    provider: str
    elapsed_seconds: float
    model: str | None = None
    session_id: str | None = None  # codex rollout session id, when applicable
    extra: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    """A generation backend. `generate` must write a verified PNG to `request.raw`."""

    name: str
    transparency: str  # one of TRANSPARENCY_STRATEGIES — declared once per provider

    def generate(self, request: GenRequest, workdir: Path) -> ProviderRun: ...


@dataclass
class GenResult:
    """Full outcome of one `sprite-gen gen` invocation."""

    provider: str
    prompt: str
    out: Path
    raw: Path
    raw_bytes: int
    elapsed_seconds: float
    model: str | None = None
    session_id: str | None = None
    refs: list[Path] = field(default_factory=list)
    transparent: bool = False
    # Which transparency strategy produced `out` plus its measured stats
    # ({"strategy": "native"|"chroma", ...stats}); None when not transparent.
    alpha: dict[str, Any] | None = None
    # Chroma-key stats — populated only when the strategy was `chroma` (kept as
    # its own field so existing report readers keep working).
    chroma: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "sprite-gen-image-report",
            "provider": self.provider,
            "prompt": self.prompt,
            "out": str(self.out),
            "raw": str(self.raw),
            "raw_bytes": self.raw_bytes,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "model": self.model,
            "session_id": self.session_id,
            "refs": [str(ref) for ref in self.refs],
            "transparent": self.transparent,
            "alpha": self.alpha,
            "chroma": self.chroma,
            **({"extra": self.extra} if self.extra else {}),
        }
