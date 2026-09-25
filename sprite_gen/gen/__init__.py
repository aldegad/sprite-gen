# SPDX-License-Identifier: Apache-2.0
"""Unified image generation layer for sprite-gen.

Single source of truth for provider-backed image generation: codex (`image_gen`,
ChatGPT OAuth), grok (Imagine, xAI OAuth) and openai (Images REST, OPENAI_API_KEY).
One call = prompt (+ optional refs) -> one verified raw PNG, with an optional
deterministic transparent chroma post-process. The general `image-gen` skill is a
thin shuttle over `sprite-gen gen`.

codex and openai reach the same family of GPT image models by different routes:
codex needs an interactive ChatGPT login and the `codex` CLI on PATH, openai needs
only an API key, which is what a headless container (Modal worker, CI) can have.

sprite-gen is subscription-first (수홍 2026-09-20). openai exists for servers and
SaaS and is billed per call, so it runs ONLY when `--provider openai` names it: it
is never a default, never a saved preference, never an offered choice in the guided
flow, and never the target of an availability fallback. Having OPENAI_API_KEY in
the environment changes no route by itself.

Transparency is a per-provider strategy (`Provider.transparency`, declared once
in each adapter): codex `image_gen` and openai (`background: transparent`) return a
genuinely transparent PNG when asked (`native`), grok Imagine cannot and is keyed
out of a chroma background (`chroma`). `--transparent` follows the provider's
strategy unless `--alpha-mode` overrides it.

CLI:
    sprite-gen gen --provider codex|grok|openai --prompt "..." --out DEST.png
        [--ref REF.png ...] [--transparent [--alpha-mode auto|native|chroma]
        [--chroma-key magenta|green]] [--white-check CHECK.png] [--model ID]
        [--aspect-ratio 1:1] [--quality low|medium|high|xhigh|max|auto]
        [--resolution 1k|1.5k|2k]
        [--report REPORT.json] [--keep-session]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from sprite_gen.spec.runio import atomic_write_text

from . import chroma as chroma_mod
from . import facing as facing_mod
from .base import (
    QUALITIES,
    RESOLUTIONS,
    TRANSPARENCY_CHROMA,
    TRANSPARENCY_NATIVE,
    TRANSPARENCY_STRATEGIES,
    GenRequest,
    GenResult,
    GenTimeoutError,
    provider_binary,
    provider_subprocess_env,
    verify_png,
)
from .codex_provider import CodexProvider
from .grok_provider import GrokProvider
from .openai_provider import OpenAIProvider

PROVIDERS = ("codex", "grok", "openai")
# `--alpha-mode`: `auto` reads the provider's declared strategy (the SSoT);
# `native` / `chroma` force one. Forcing `native` on a chroma-only provider fails
# loud — a strategy the backend cannot execute is not a fallback candidate.
ALPHA_MODE_AUTO = "auto"
ALPHA_MODES = (ALPHA_MODE_AUTO, *TRANSPARENCY_STRATEGIES)

# Default-provider policy (maintainer 확정 2026-07-17): the default backend is codex
# (GPT `image_gen`). If codex is unavailable in the environment (CLI missing or
# not logged in) the default resolution falls back to grok — but OBSERVABLY, never
# silently (No Silent Fallback): the chosen provider and the fallback reason are
# reported. A user can change the default with SPRITE_GEN_DEFAULT_PROVIDER. An
# EXPLICIT `--provider` is always honored exactly (no availability fallback) — an
# explicitly named provider that is down fails loud at generation time.
DEFAULT_PROVIDER_ENV = "SPRITE_GEN_DEFAULT_PROVIDER"
HARD_DEFAULT_PROVIDER = "codex"
# Providers that may be reached without being named. A per-call API-billed backend
# is not one of them: it is explicit-only, so no default, preference or fallback
# can route a subscription user onto metered credit (구독 우선 불변식 1-2).
EXPLICIT_ONLY_PROVIDERS = ("openai",)
_CODEX_PROBE_TIMEOUT_SECONDS = 15


def _make_provider(name: str, *, keep_session: bool):
    if name == "codex":
        return CodexProvider(keep_session=keep_session)
    if name == "grok":
        return GrokProvider()
    if name == "openai":
        return OpenAIProvider()
    raise SystemExit(f"gen: unknown provider {name!r}; expected one of {', '.join(PROVIDERS)}")


# Why `auto` steps down to chroma when reference images are attached (2026-09-08
# 실측, plan sprite-gen/parts-rig): codex image_gen with `--ref` returned real
# alpha in 1/6 runs and drew a checkerboard (RGB) in 5/6, while the same prompts
# on a #00FF00 key + chroma keying succeeded 6/6. The edit path is not a reliable
# alpha source, so `auto` does not gamble on it — the decision is made before the
# model runs, printed, and recorded in the report (`alpha.strategy_source`).
# An explicit `--alpha-mode native` still forces it (and fails loud on RGB).
STRATEGY_SOURCE_PROVIDER = "provider-default"
STRATEGY_SOURCE_REFS = "refs-attached"
STRATEGY_SOURCE_EXPLICIT = "explicit"


def resolve_transparency_strategy(backend, alpha_mode: str, *, refs: list[Path] | None = None) -> tuple[str, str]:
    """Decide which transparency strategy this generation runs.

    Returns (strategy, source). The provider's declared `transparency` is the
    only source of what it can do; `alpha_mode` may pick `chroma` on a native
    provider (the prompt already carries a key background) but can never pick
    `native` on a chroma-only provider. `auto` on a native provider steps down to
    `chroma` when reference images are attached (see STRATEGY_SOURCE_REFS).
    """
    if alpha_mode not in ALPHA_MODES:
        raise SystemExit(f"gen: unknown --alpha-mode {alpha_mode!r}; expected one of {', '.join(ALPHA_MODES)}")
    declared = getattr(backend, "transparency", None)
    if declared not in TRANSPARENCY_STRATEGIES:
        raise SystemExit(
            f"gen: provider {getattr(backend, 'name', backend)!r} declares no transparency "
            f"strategy (got {declared!r}); expected one of {', '.join(TRANSPARENCY_STRATEGIES)}"
        )
    if alpha_mode == ALPHA_MODE_AUTO:
        if declared == TRANSPARENCY_NATIVE and refs:
            return TRANSPARENCY_CHROMA, STRATEGY_SOURCE_REFS
        return declared, STRATEGY_SOURCE_PROVIDER
    if alpha_mode == TRANSPARENCY_NATIVE and declared != TRANSPARENCY_NATIVE:
        raise SystemExit(
            f"gen: --alpha-mode native is not a capability of provider {backend.name!r} "
            f"(its transparency strategy is {declared!r}); use --alpha-mode chroma or another provider"
        )
    return alpha_mode, STRATEGY_SOURCE_EXPLICIT


def _codex_available() -> tuple[bool, str]:
    """Is codex usable here? Returns (ok, reason_if_not).

    Two checks, cheapest first: the `codex` CLI on PATH, then `codex login status`
    (exit 0 = logged in). Any probe error/timeout counts as unavailable with a
    stated reason — never a silent pass. Monkeypatchable in tests.
    """
    if shutil.which("codex") is None:
        return False, "codex CLI not found on PATH"
    try:
        completed = subprocess.run(
            [provider_binary("codex"), "login", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=provider_subprocess_env(),
            timeout=_CODEX_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"codex login-status probe failed: {exc}"
    if completed.returncode != 0:
        tail = (completed.stdout or completed.stderr or "").strip().splitlines()[-3:]
        detail = f": {' '.join(tail)}" if tail else ""
        return False, f"codex not logged in (codex login status exit {completed.returncode}{detail})"
    return True, ""


def resolve_default_provider() -> tuple[str, dict[str, str] | None]:
    """Resolve the provider to use when `--provider` is not given.

    Precedence: SPRITE_GEN_DEFAULT_PROVIDER env > hard default (codex). When the
    resolved default is codex but codex is unavailable, fall back to grok and return
    fallback metadata (from/to/reason/default_source) so the switch is observable.
    Returns (provider, fallback_or_None).

    An EXPLICIT_ONLY provider can never come out of here: not as the hard default,
    not out of the env, and not as a fallback target. A codex outage reaches grok
    (another subscription route) or nothing at all — never metered API credit.
    """
    configured = os.environ.get(DEFAULT_PROVIDER_ENV, "").strip()
    if configured:
        if configured not in PROVIDERS:
            raise SystemExit(
                f"gen: {DEFAULT_PROVIDER_ENV}={configured!r} is not a known provider; "
                f"expected one of {', '.join(PROVIDERS)}"
            )
        if configured in EXPLICIT_ONLY_PROVIDERS:
            raise SystemExit(
                f"gen: {DEFAULT_PROVIDER_ENV}={configured!r} is refused — {configured} bills per call "
                f"against an API key and must be named explicitly (`--provider {configured}`), never "
                f"stood up as a default. Use {', '.join(p for p in PROVIDERS if p not in EXPLICIT_ONLY_PROVIDERS)} "
                "for a subscription route."
            )
        default, source = configured, DEFAULT_PROVIDER_ENV
    else:
        default, source = HARD_DEFAULT_PROVIDER, "hard-default"

    # The availability-driven fallback is codex -> grok only (the mandated default):
    # one subscription route to another, never to a per-call API key. A grok default
    # that is down fails loud at generation time rather than silently reverse-falling
    # back to codex.
    if default == "codex":
        ok, reason = _codex_available()
        if not ok:
            return "grok", {
                "from": "codex",
                "to": "grok",
                "reason": reason,
                "default_source": source,
            }
    return default, None


def trim_to_alpha(path: Path, *, threshold: int = 8) -> dict[str, Any]:
    """Crop a transparent PNG in place to the bbox of its opaque pixels.

    A generated still carries an unpredictable band of empty alpha below the feet
    (and around the sides); two stills placed on the same floor line then stand at
    different heights. Trimming makes the image's bottom edge the ground-contact
    line, which is what every placement (scene, strip, runtime) anchors on. The
    subject is never cut — only fully transparent margin goes."""
    from PIL import Image

    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        before = rgba.size
        box = rgba.getchannel("A").point(lambda v: 255 if v >= threshold else 0).getbbox()
        if box is None:
            raise SystemExit(f"gen: --trim-alpha: {path} is fully transparent; nothing to keep")
        cropped = rgba.crop(box)
        cropped.save(path)
    return {
        "bbox": list(box),
        "before": list(before),
        "after": list(cropped.size),
        "margin_px": {"left": box[0], "top": box[1], "right": before[0] - box[2], "bottom": before[1] - box[3]},
    }


def generate_image(
    provider: str,
    prompt: str,
    out: Path,
    *,
    refs: list[Path] | None = None,
    facing: str | None = None,
    facing_fix: str = "none",
    model: str | None = None,
    aspect_ratio: str | None = None,
    quality: str | None = None,
    resolution: str | None = None,
    transparent: bool = False,
    alpha_mode: str = ALPHA_MODE_AUTO,
    chroma_key: str = "magenta",
    white_check: Path | None = None,
    trim_alpha: bool = False,
    keep_session: bool = False,
    workdir: Path | None = None,
    decontam: str = "off",
) -> GenResult:
    """Generate one image and return a GenResult. Raises SystemExit on any failure."""
    if decontam not in ("off", "auto", "palette"):
        raise SystemExit(f"gen: unknown --decontam {decontam!r}; expected off|auto|palette")
    if decontam == "palette" and not transparent:
        raise SystemExit("gen: --decontam applies to the chroma key of a --transparent image; add --transparent")
    prompt = (prompt or "").strip()
    if not prompt:
        raise SystemExit("gen: empty prompt; pass --prompt or --prompt-file")
    if facing is not None:
        facing_mod.validate(facing, facing_fix)
    out = out.expanduser().resolve()
    refs = [Path(r).expanduser().resolve() for r in (refs or [])]
    for ref in refs:
        if not ref.is_file():
            raise SystemExit(f"gen: reference image not found: {ref}")

    if refs and facing is not None:
        prompt += "\n\n" + facing_mod.prompt_suffix(facing)
    backend = _make_provider(provider, keep_session=keep_session)
    # Decided before the model runs: the strategy shapes the transport prompt
    # (native asks for alpha) and the post-process (chroma keys it out).
    strategy: str | None = None
    strategy_source: str | None = None
    if transparent:
        strategy, strategy_source = resolve_transparency_strategy(backend, alpha_mode, refs=refs)
        if decontam == "palette" and strategy != TRANSPARENCY_CHROMA:
            # before the provider is called: a paid generation must not end in this refusal
            raise SystemExit(f"gen: --decontam removes a chroma key; this image would use {strategy} alpha "
                             "(pass --alpha-mode chroma to key it instead)")
        if strategy_source == STRATEGY_SOURCE_REFS:
            print(
                f"[gen] {len(refs)} reference image(s) attached — using chroma keying instead of "
                f"{backend.name}'s native alpha (native output with refs is unstable, 2026-09-08). "
                "Pass --alpha-mode native to force it.",
                file=sys.stderr,
            )
    owns_workdir = workdir is None
    workdir = Path(workdir).expanduser().resolve() if workdir else Path(tempfile.mkdtemp(prefix="sprite-gen-gen-"))
    workdir.mkdir(parents=True, exist_ok=True)
    raw = workdir / "raw.png"

    try:
        request = GenRequest(
            prompt=prompt,
            raw=raw,
            refs=refs,
            model=model,
            aspect_ratio=aspect_ratio,
            quality=quality,
            resolution=resolution,
            native_alpha=strategy == TRANSPARENCY_NATIVE,
        )
        # 타임아웃 1회 관측 가능 재시도 — 산발 provider 스톨은 같은 호출 재시도로
        # 대부분 통과한다 (회귀 2026-07-19). 두 번째도 스톨이면 fail loud.
        try:
            run = backend.generate(request, workdir)
        except GenTimeoutError as exc:
            print(f"[gen] {exc} — retrying once", file=sys.stderr)
            run = backend.generate(request, workdir)
        verify_png(raw)
        facing_report = None
        if refs and facing is not None:
            request, run, facing_report = facing_mod.prepare_correction(
                backend, request, run, workdir, facing=facing, fix=facing_fix)
            raw = request.raw
        raw_bytes = verify_png(raw)

        chroma_stats: dict[str, Any] | None = None
        alpha_stats: dict[str, Any] | None = None
        out.parent.mkdir(parents=True, exist_ok=True)
        if strategy == TRANSPARENCY_NATIVE:
            alpha_stats = {
                "strategy": TRANSPARENCY_NATIVE,
                "strategy_source": strategy_source,
                **chroma_mod.verify_native_alpha(raw, out, white_check=white_check),
            }
        elif strategy == TRANSPARENCY_CHROMA:
            chroma_stats = chroma_mod.key_transparent(raw, out, key=chroma_key, white_check=white_check,
                                                      decontam=decontam)
            alpha_stats = {"strategy": TRANSPARENCY_CHROMA, "strategy_source": strategy_source, **chroma_stats}
        else:
            shutil.copyfile(raw, out)
        verify_png(out)
        if facing_report and (facing_report["action"] == "mirror" or facing_report.get("fallback") == "mirror"):
            facing_mod.mirror(out)
            if white_check is not None and transparent:
                facing_mod.mirror(white_check)
        trim_stats: dict[str, Any] | None = None
        if trim_alpha:
            if not transparent:
                raise SystemExit("gen: --trim-alpha needs --transparent (there is no alpha to trim on an opaque image)")
            trim_stats = trim_to_alpha(out)

        # Preserve the pre-chroma raw next to the destination for auditability.
        raw_keep = out.with_suffix(out.suffix + ".raw.png")
        shutil.copyfile(raw, raw_keep)

        return GenResult(
            provider=run.provider,
            prompt=prompt,
            out=out,
            raw=raw_keep,
            raw_bytes=raw_bytes,
            elapsed_seconds=run.elapsed_seconds,
            model=run.model,
            session_id=run.session_id,
            refs=refs,
            transparent=transparent,
            alpha=alpha_stats,
            chroma=chroma_stats,
            extra={**run.extra, **({"trim_alpha": trim_stats} if trim_stats else {}),
                   **({"facing": facing_report} if facing_report else {})},
        )
    finally:
        if owns_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def _run(args: argparse.Namespace) -> int:
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
    # Explicit --provider is honored verbatim; only the unspecified case resolves the
    # default (env > codex) with an observable codex->grok availability fallback.
    provider = args.provider
    fallback: dict[str, str] | None = None
    if provider is not None:
        resolved_from = "explicit"
    else:
        provider, fallback = resolve_default_provider()
        if fallback:
            resolved_from = f"fallback-from-{fallback['from']}"
            print(
                f"sprite-gen gen: default provider '{fallback['from']}' unavailable "
                f"({fallback['reason']}) — falling back to '{fallback['to']}'. "
                f"Set {DEFAULT_PROVIDER_ENV} or pass --provider to control this.",
                file=sys.stderr,
            )
        elif os.environ.get(DEFAULT_PROVIDER_ENV, "").strip():
            resolved_from = DEFAULT_PROVIDER_ENV
        else:
            resolved_from = "hard-default"
    result = generate_image(
        provider,
        prompt or "",
        args.out,
        refs=args.ref,
        facing=None if args.facing == "preserve" else args.facing,
        facing_fix=args.facing_fix,
        model=args.model,
        aspect_ratio=args.aspect_ratio,
        quality=args.quality,
        resolution=args.resolution,
        transparent=args.transparent,
        alpha_mode=args.alpha_mode,
        chroma_key=args.chroma_key,
        white_check=args.white_check,
        trim_alpha=bool(getattr(args, "trim_alpha", False)),
        keep_session=args.keep_session,
        workdir=args.workdir,
        decontam=str(getattr(args, "decontam", None) or "off"),
    )
    payload = result.to_dict()
    # `provider` in the payload is always the backend that actually generated the
    # image; `provider_resolved_from` records HOW it was chosen and
    # `provider_fallback` records a default->fallback switch when one happened
    # (No Silent Fallback — the report names which provider was used and why).
    payload["provider_resolved_from"] = resolved_from
    if fallback:
        payload["provider_fallback"] = fallback
    if args.report:
        report_path = Path(args.report).expanduser().resolve()
        atomic_write_text(report_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        payload["report"] = str(report_path)
    _print_json(payload)
    return 0


def _print_json(payload: dict) -> None:
    """Emit the machine-readable report as UTF-8 on every host locale.

    A generated prompt may contain Korean or an em dash. On a Windows cp949
    redirected stream, writing that through ``TextIOWrapper`` can fail after
    the image already exists. The binary buffer is the CLI byte contract and
    avoids mutating process-global stdout configuration. Captured/in-memory
    streams without a buffer already accept Unicode and use the text path.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:
        sys.stdout.write(text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprite-gen gen", description=__doc__)
    add_arguments(parser)
    return parser


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default=None,
        help=(
            f"generation backend; default resolves via {DEFAULT_PROVIDER_ENV} env "
            "then codex, with an observable grok fallback if codex is unavailable. "
            "codex and grok run on a subscription login; openai is for servers and SaaS "
            "and is billed per call on OPENAI_API_KEY, so it runs only when named here"
        ),
    )
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ref", action="append", type=Path, default=[], help="reference image (repeatable)")
    parser.add_argument("--model")
    parser.add_argument("--aspect-ratio", help="grok and openai, e.g. 1:1 16:9 9:16 (openai maps it to a gpt-image size; codex ignores it)")
    parser.add_argument(
        "--quality",
        choices=QUALITIES,
        default=None,
        help=(
            "rendering effort billed for this image; openai carries the whole range "
            "(low..max, auto = the model decides) and grok takes low / medium / auto. "
            "Omitted = the provider's own default. A provider that cannot honour the "
            "level fails instead of downgrading it"
        ),
    )
    parser.add_argument(
        "--resolution",
        choices=RESOLUTIONS,
        default=None,
        help=(
            "output size tier, priced together with --quality: grok Imagine's "
            "1k / 1.5k / 2k (tier names, not pixel counts — 1.5k renders 1408px at 1:1). "
            "Omitted = the provider's own default (grok: 1k). openai sizes from "
            "--aspect-ratio and codex from neither, so both refuse this flag "
            "instead of ignoring it"
        ),
    )
    parser.add_argument(
        "--transparent",
        action="store_true",
        help=(
            "publish a transparent RGBA PNG using the provider's transparency strategy: "
            "codex asks image_gen for real alpha and openai asks for background=transparent (native), "
            "grok is keyed out of a chroma background"
        ),
    )
    parser.add_argument(
        "--alpha-mode",
        choices=ALPHA_MODES,
        default=ALPHA_MODE_AUTO,
        help=(
            "transparency strategy for --transparent: auto = the provider's declared strategy "
            "(native on codex and openai, but chroma whenever --ref is attached — native alpha with refs is unstable); "
            "chroma forces chroma keying (e.g. a codex prompt that already carries a key background); "
            "native forces native alpha and is refused on a provider that cannot return alpha"
        ),
    )
    parser.add_argument(
        "--decontam",
        choices=("off", "auto", "palette"),
        default="off",
        help="chroma keying: re-explain key-tinted edges (hair strands, outlines) with the subject's own "
        "colours after the matte. off (default) publishes the matte as it is; auto runs it where it "
        "applies (chroma strategy, a subject interior); palette demands it",
    )
    parser.add_argument("--chroma-key", choices=sorted(chroma_mod.KEYS), default="magenta")
    parser.add_argument("--facing", choices=(*facing_mod.FACINGS, "preserve"), default="preserve", help="with --ref: required direction; preserve (default) leaves prompt and pixels unchanged")
    parser.add_argument("--facing-fix", choices=facing_mod.FIXES, default="none", help="with --ref and --facing: record only (none, default), or opt into mirror / one regen")
    parser.add_argument("--trim-alpha", action="store_true", help="with --transparent: crop the published PNG to its opaque bbox so the bottom edge is the foot line (margins reported)")
    parser.add_argument("--white-check", type=Path, help="write a white-composite check image")
    parser.add_argument("--keep-session", action="store_true", help="codex: do not delete the rollout jsonl")
    parser.add_argument("--report", type=Path, help="write the generation report JSON here")
    parser.add_argument("--workdir", type=Path, help="reuse this working dir instead of a temp dir")


def run(**kwargs: object) -> int:
    parser = _build_parser()
    known = {action.dest for action in parser._actions if action.dest != "help"}
    unexpected = set(kwargs) - known
    if unexpected:
        raise TypeError(f"unexpected keyword argument(s): {', '.join(sorted(unexpected))}")
    namespace = argparse.Namespace(**{dest: kwargs.get(dest, parser.get_default(dest)) for dest in known})
    return _run(namespace)


def main(argv: list[str] | None = None) -> int:
    return _run(_build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
