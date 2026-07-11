# SPDX-License-Identifier: Apache-2.0
"""Unified image generation layer for sprite-gen.

Single source of truth for provider-backed image generation: codex (`image_gen`,
ChatGPT OAuth) and grok (Imagine, xAI OAuth). One call = prompt (+ optional refs)
-> one verified raw PNG, with an optional deterministic transparent chroma
post-process. The general `image-gen` skill is a thin shuttle over `sprite-gen gen`.

CLI:
    sprite-gen gen --provider codex|grok --prompt "..." --out DEST.png
        [--ref REF.png ...] [--transparent [--chroma-key magenta|green]]
        [--white-check CHECK.png] [--model ID] [--aspect-ratio 1:1]
        [--report REPORT.json] [--keep-session]
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from sprite_gen.runio import atomic_write_text

from . import chroma as chroma_mod
from .base import GenRequest, GenResult, verify_png
from .codex_provider import CodexProvider
from .grok_provider import GrokProvider

PROVIDERS = ("codex", "grok")


def _make_provider(name: str, *, keep_session: bool):
    if name == "codex":
        return CodexProvider(keep_session=keep_session)
    if name == "grok":
        return GrokProvider()
    raise SystemExit(f"gen: unknown provider {name!r}; expected one of {', '.join(PROVIDERS)}")


def generate_image(
    provider: str,
    prompt: str,
    out: Path,
    *,
    refs: list[Path] | None = None,
    model: str | None = None,
    aspect_ratio: str | None = None,
    transparent: bool = False,
    chroma_key: str = "magenta",
    white_check: Path | None = None,
    keep_session: bool = False,
    workdir: Path | None = None,
) -> GenResult:
    """Generate one image and return a GenResult. Raises SystemExit on any failure."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise SystemExit("gen: empty prompt; pass --prompt or --prompt-file")
    out = out.expanduser().resolve()
    refs = [Path(r).expanduser().resolve() for r in (refs or [])]
    for ref in refs:
        if not ref.is_file():
            raise SystemExit(f"gen: reference image not found: {ref}")

    backend = _make_provider(provider, keep_session=keep_session)
    owns_workdir = workdir is None
    workdir = Path(workdir).expanduser().resolve() if workdir else Path(tempfile.mkdtemp(prefix="sprite-gen-gen-"))
    workdir.mkdir(parents=True, exist_ok=True)
    raw = workdir / "raw.png"

    try:
        request = GenRequest(prompt=prompt, raw=raw, refs=refs, model=model, aspect_ratio=aspect_ratio)
        run = backend.generate(request, workdir)
        raw_bytes = verify_png(raw)

        chroma_stats: dict[str, Any] | None = None
        out.parent.mkdir(parents=True, exist_ok=True)
        if transparent:
            chroma_stats = chroma_mod.key_transparent(raw, out, key=chroma_key, white_check=white_check)
        else:
            shutil.copyfile(raw, out)
        verify_png(out)

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
            chroma=chroma_stats,
            extra=run.extra,
        )
    finally:
        if owns_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def _run(args: argparse.Namespace) -> int:
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
    result = generate_image(
        args.provider,
        prompt or "",
        args.out,
        refs=args.ref,
        model=args.model,
        aspect_ratio=args.aspect_ratio,
        transparent=args.transparent,
        chroma_key=args.chroma_key,
        white_check=args.white_check,
        keep_session=args.keep_session,
        workdir=args.workdir,
    )
    payload = result.to_dict()
    if args.report:
        report_path = Path(args.report).expanduser().resolve()
        atomic_write_text(report_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        payload["report"] = str(report_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprite-gen gen", description=__doc__)
    add_arguments(parser)
    return parser


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", required=True, choices=PROVIDERS)
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ref", action="append", type=Path, default=[], help="reference image (repeatable)")
    parser.add_argument("--model")
    parser.add_argument("--aspect-ratio", help="grok only, e.g. 1:1 16:9 9:16")
    parser.add_argument("--transparent", action="store_true", help="chroma-key the raw PNG to transparent RGBA")
    parser.add_argument("--chroma-key", choices=sorted(chroma_mod.KEYS), default="magenta")
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
