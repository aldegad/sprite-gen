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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ── 자식 엔진 env 위생 (일반 원칙, SoC) ──────────────────────────────
# 스폰된 생성 엔진(codex exec / grok)은 독립 프로세스다 — 부모(이 스크립트를
# 부른 에이전트/오케스트레이터 세션)의 세션 신분 env 를 상속하면 안 된다.
# 상속 시 실사고 2건: (1) 자식의 프롬프트가 부모 신분으로 오귀속돼 부모의
# 대화 채널로 방송됨(2026-07-18), (2) 부모 오케스트레이터의 훅이 자식을
# 멤버로 착각해 턴 종료를 무한 차단 — 무출력 행(2026-07-19, 수홍 확정
# "서브에이전트의 서브에이전트" 버그). 근본 수리는 해당 오케스트레이터
# 소유(훅이 신분 없는 세션을 no-op 해야 함)지만, 엔진은 어떤 오케스트레이터
# 아래서든 자식을 깨끗하게 스폰할 책임이 있다. 알려진 세션 env 접두어 가족만
# 지운다 — PATH 등 일반 env 는 그대로.
_ORCHESTRATOR_SESSION_ENV_PREFIXES = ("KUMA_",)


def provider_subprocess_env() -> dict[str, str]:
    """Environment for a headless generation subprocess.

    The parent environment minus known orchestrator session env families, so a
    spawned engine (`codex exec`, `grok`) is a clean standalone process — it
    neither impersonates the spawning agent nor gets strangled by the
    orchestrator's hooks. SSoT for every provider's `subprocess.run` env —
    providers must not spawn with the inherited env directly.
    """
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(_ORCHESTRATOR_SESSION_ENV_PREFIXES)}
    return env


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
            "chroma": self.chroma,
            **({"extra": self.extra} if self.extra else {}),
        }
