# SPDX-License-Identifier: Apache-2.0
"""Shared contract for sprite-gen image generation providers.

One generation call = prompt (+ optional reference images) -> one verified raw
PNG on disk. Providers own the model call and its timing; the orchestrator in
`sprite_gen.gen` owns the optional transparent chroma post-process and the
report. Truth is always the decoded PNG bytes on disk, never a model-reported
path or a "done" string (No Silent Fallback).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


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
