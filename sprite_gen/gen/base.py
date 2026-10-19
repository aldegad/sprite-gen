# SPDX-License-Identifier: Apache-2.0
# Modified: optional configuration preflight for provider-specific vision deployments.
"""Shared contract for sprite-gen image generation providers.

One generation call = prompt (+ optional reference images) -> one verified raw
PNG on disk. Providers own the model call and its timing; the orchestrator in
`sprite_gen.gen` owns the optional transparent chroma post-process and the
report. Truth is always the decoded PNG bytes on disk, never a model-reported
path or a "done" string (No Silent Fallback).
"""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from PIL import Image, UnidentifiedImageError

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

# Quality is a per-provider capability over one shared vocabulary. This tuple is
# the `--quality` surface (the union); each provider declares the subset it can
# honour and fails loud on a name outside it, so a paid-for level is never
# quietly downgraded to whatever the backend felt like (No Silent Fallback).
QUALITIES = ("auto", "low", "medium", "high", "xhigh", "max")

# Output resolution — the second billed knob, over one shared vocabulary of grok
# Imagine's output-size tiers, which it prices together with `quality`. The names
# are tiers, not pixel counts: `1.5k` rendered 1408x1408 at 1:1, not 1536
# (2026-09-20 실측 on the XAI_API_KEY route), so nothing here derives a size from
# the name — the tier goes to the service verbatim and the service sizes. A
# provider that sizes differently (openai derives a gpt-image `size` from
# `--aspect-ratio`) refuses the flag rather than accepting money for a resolution
# it will not deliver. Distinct from the video RESOLUTIONS
# (`480p`/`720p`/`1080p`), which names a frame height.
RESOLUTIONS = ("1k", "1.5k", "2k")

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
# OpenAI·Grok 직접 호출(이미지, OpenAI 방향 검사)에는 첫 전송부터 센 호출 기한 — 다시 보내기 전 대기 포함 (gen.retry).
GEN_TIMEOUT_SECONDS = int(os.environ.get("SPRITE_GEN_GEN_TIMEOUT_SECONDS", "180"))


class GenTimeoutError(SystemExit):
    """생성 서브프로세스가 GEN_TIMEOUT_SECONDS 안에 끝나지 않아 킬됨 (관측 가능)."""


def provider_subprocess_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for a headless generation subprocess.

    The parent environment (`env`, default `os.environ`) minus known orchestrator
    session env families, so a spawned engine (`codex exec`, `claude -p`) is a clean
    standalone process — it neither impersonates the spawning agent nor gets
    strangled by the orchestrator's hooks. SSoT for every provider's
    `subprocess.run` env — providers must not spawn with the inherited env directly.
    """
    source = os.environ if env is None else env
    return {
        key: value
        for key, value in source.items()
        if not key.endswith(_ORCHESTRATOR_SESSION_ENV_SUFFIXES)
    }


def env_api_key(name: str, env: Mapping[str, str] | None = None, *, who: str, also: str = "") -> str | None:
    """The API key in the environment variable `name` (`env`, default `os.environ`),
    None when it is not set. Set but empty is refused, naming it: an empty key is a
    mistake to show, not an absence to route around; `also` is said after it. The one
    reading of a provider key; what a missing key means (another route, or a refusal)
    is the caller's."""
    source = os.environ if env is None else env
    key = (source.get(name) or "").strip()
    if key:
        return key
    if name in source:
        raise SystemExit(f"{who}: {name} is set but empty; give it a value or unset it{also}")
    return None


def provider_binary(name: str) -> str:
    """Resolve a provider CLI through PATH, including Windows PATHEXT shims.

    npm installs provider CLIs as ``.cmd`` shims on Windows. ``CreateProcess``
    does not resolve those from a bare name, while ``shutil.which`` does. A
    missing command stays bare so the provider spawn still owns the observable
    failure instead of introducing a second availability answer here.
    """
    return shutil.which(name) or name


# The words of the two notices a person must see on their terminal even when a batch verb keeps a
# child's output in a log (`run_logged`): a call billed per call, and a default provider switched.
API_CHARGE_WORDS = "this is a per-call API charge, not a subscription"
PROVIDER_SWITCH_WORDS = "— falling back to"

# The one question sprite-gen hands back instead of an image (docs/gen.md "When codex cannot run"): a call that
# named no provider found codex unable to run, and what the user chose for that is to be asked. Nothing was
# generated. It is one stderr line, these words and a JSON object (`cause`, `reason`, `question`, and `choices`:
# the flag that runs each answer), and this exit code. A batch verb whose child asked reads the child's line from
# its log and stops with the same question, naming its own flag.
CODEX_UNAVAILABLE_EXIT = 75
CODEX_UNAVAILABLE_ASK = "sprite-gen ask codex-unavailable: "


class CodexUnavailableAsk(SystemExit):
    """The question was asked instead of generating; `question` is the JSON object of its line."""

    def __init__(self, question: dict[str, Any]) -> None:
        super().__init__(CODEX_UNAVAILABLE_EXIT)
        self.question = question


def codex_unavailable_question(cause: str, reason: str, *, flag: str = "--provider") -> dict[str, Any]:
    """The question's JSON object. `cause` is `login` (codex is not signed in here: `codex_provider.codex_login`) or
    `usage-limit` (codex said its usage is used up: `codex_provider.usage_limit`); `reason` says which, in the
    reading's own words; `flag` is the asking verb's flag that runs an answer."""
    return {
        "cause": cause,
        "reason": reason,
        "question": f"codex cannot run here ({reason}). Generate with the OpenAI API (billed per call), "
                    "with Grok, or stop?",
        "choices": {"openai": f"{flag} openai", "grok": f"{flag} grok", "stop": None},
    }


def ask_codex_unavailable(question: dict[str, Any]) -> CodexUnavailableAsk:
    """Print the question (`codex_unavailable_question`) on stderr and return the exit that ends the call with it."""
    print(CODEX_UNAVAILABLE_ASK + json.dumps(question, ensure_ascii=False), file=sys.stderr, flush=True)
    return CodexUnavailableAsk(question)


def asked_codex_unavailable(output: str) -> dict[str, Any] | None:
    """The question in a call's output (its last question line), or None when it asked none."""
    for line in reversed(output.splitlines()):
        if line.startswith(CODEX_UNAVAILABLE_ASK):
            try:
                return json.loads(line[len(CODEX_UNAVAILABLE_ASK):])
            except ValueError:
                return None
    return None


def child_asked(rc: int, log: Path) -> dict[str, Any] | None:
    """The question a child `sprite-gen gen` run through `run_logged` asked instead of generating: its exit code is
    CODEX_UNAVAILABLE_EXIT and its line is in `log`. None for any other ending."""
    if rc != CODEX_UNAVAILABLE_EXIT or not log.is_file():
        return None
    return asked_codex_unavailable(log.read_text(encoding="utf-8", errors="replace"))


def announce_api_billing(provider: str, env_name: str, detail: str = "") -> None:
    """Say on stderr, before the request leaves, that this call bills API credit.

    sprite-gen is a subscription-first tool: codex runs on a ChatGPT login and
    grok prefers its Grok login. A route that instead spends metered API credit
    is never silent about it, because the person paying only finds out otherwise
    on the invoice (수홍 2026-09-20, 구독 우선 불변식 5).
    """
    print(f"[gen] {provider}: running on {env_name} — {API_CHARGE_WORDS}{detail}", file=sys.stderr, flush=True)


def validate_inspection(backend: Provider) -> None:
    """Preflight optional inspection configuration before a workflow buys images."""
    validate = getattr(backend, "validate_inspection", None)
    if validate is not None:
        validate()


def run_logged(cmd: list[str], log: Path) -> int:
    """Run a child command with all its output in `log`. The lines that announce a per-call charge
    or a default provider switched for being unavailable also go to this process's stderr as they
    come: a batch verb (`gen-set`, `video-set`) keeps each item's output in its own log, and those
    two lines are for the person running it, before the request leaves."""
    with log.open("w", encoding="utf-8") as fh:
        child = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace")
        assert child.stdout is not None
        for line in child.stdout:
            fh.write(line)
            if API_CHARGE_WORDS in line or PROVIDER_SWITCH_WORDS in line:
                print(line.rstrip("\n"), file=sys.stderr, flush=True)
        return child.wait()


def flags_over_spec(spec: Mapping[str, Any], flags: Mapping[str, Any], *, verb: str, source: str,
                    spelled: Mapping[str, str] | None = None) -> dict[str, Any]:
    """`spec` — what a spec file or a request says — with every flag given laid over the key it sets.

    A key given both ways takes the flag, and one stderr line names every such flag, so a value the
    file holds is never replaced in silence: the one answer of every verb that reads a file of the
    same keys as its flags (`prepare --request`, `pixel-draw --spec`). `flags` holds the flags that
    were given, by the key each sets; a dotted key (`cell.width`) sets a key of an object in `spec`.
    `spelled` names a key's flag where it is not `--key-with-dashes`."""
    merged = copy.deepcopy(dict(spec))
    both: list[str] = []
    for key, value in flags.items():
        *parents, leaf = key.split(".")
        node = merged
        for name in parents:
            node[name] = node = dict(node.get(name) or {})
        name = (spelled or {}).get(key, "--" + key.replace("_", "-"))
        if leaf in node and name not in both:
            both.append(name)
        node[leaf] = value
    if both:
        print(f"[{verb}] {', '.join(both)} given both as a flag and in {source}: the flag wins", file=sys.stderr)
    return merged


def print_json(payload: dict) -> None:
    """Emit the machine-readable report as UTF-8 on every host locale.

    A generated prompt may contain Korean or an em dash. On a Windows cp949
    redirected stream, writing that through ``TextIOWrapper`` can fail after
    the image already exists. The binary buffer is the CLI byte contract and
    avoids mutating process-global stdout configuration. Captured/in-memory
    streams without a buffer already accept Unicode and use the text path.
    One writer for every report `gen`, the video verbs and `pixel-draw` print.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:
        sys.stdout.write(text)


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
    aspect_ratio: str | None = None  # grok honours this; openai maps it to a size; codex ignores it
    # Rendering effort/fidelity level, from QUALITIES. None = the provider's own
    # default; a level the provider does not support is an error, not a downgrade.
    quality: str | None = None
    # Output resolution, from RESOLUTIONS. None = the provider's own default
    # (grok: `1k`); a provider that cannot size its output this way is an error,
    # not a downgrade.
    resolution: str | None = None
    # Ask the model for a genuinely transparent background (alpha channel). Only
    # legal for a provider whose `transparency` is `native`; the orchestrator gates
    # it and the provider carries the request into its transport prompt.
    native_alpha: bool = False


def publish_png(data: bytes, path: Path, *, label: str) -> None:
    """Publish decoded provider image bytes as a verified PNG at `path`.

    One writer for every provider: decode first (a corrupt or non-image payload
    fails before anything is published), re-encode only when the payload is not
    already a PNG — grok Imagine answers with JPEG — then verify the bytes and
    swap them in atomically. Nothing is resized. A failure leaves whatever was at
    `path` untouched, so a stale raw is never reused as a result.
    """
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            if source.format == "PNG":
                png = data
            else:
                buffer = io.BytesIO()
                source.convert("RGBA" if "A" in source.getbands() else "RGB").save(buffer, format="PNG")
                png = buffer.getvalue()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise SystemExit(f"{label}: response image is invalid; nothing published") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".png", delete=False) as tmp:
        temp = Path(tmp.name)
    try:
        temp.write_bytes(png)
        verify_png(temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


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

    def inspect_facing(self, path: Path, workdir: Path) -> tuple[str, dict[str, Any]]: ...

    # One vision call asking `prompt` about the image at `path`, on this provider's own login
    # (the facing check asks its own question through `inspect_facing`; `logo` asks what the
    # lettering spells). `label` names the call in a billing line and a failure.
    def inspect_image(self, path: Path, workdir: Path, prompt: str, *, label: str = ...) -> tuple[str, dict[str, Any]]: ...


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
