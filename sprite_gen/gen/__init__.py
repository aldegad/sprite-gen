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
        [--resolution 1k|1.5k|2k] [--layout-guide]
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
from . import handedness as handed_mod
from . import prompt_parts
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

# `--layout-guide`: the one-slot form of the guide `prepare` draws for every row
# (`draw_guide`, the same 9.4 % safe margin), attached after the caller's refs, with
# the anatomy lines of the row guide experiments on it: an orange line where the
# skull's crown goes and a teal line where the soles stand, each one safe margin
# INSIDE the safe box. On the box's own edges the lines read as frame edges: hair and
# hats fill the room above the crown and feet float off a floor taken for a border,
# so both were moved in by a margin. A single still drawn without the guide fills
# its frame top to bottom, and a tall subject then has nothing above the head for an
# in-place motion to bob into; a box alone is read loosely, the lines set the scale.
LAYOUT_GUIDE_LONG_EDGE = 1024
LAYOUT_GUIDE_NAME = "layout-guide.png"
CROWN_LINE = "#ff7a00"
CROWN_MARK = "#ffb15c"
FLOOR_LINE = "#00a7a7"
FLOOR_MARK = "#63d6d6"
def layout_guide_text(cell: dict[str, Any]) -> str:
    """The prompt's half of the guide: where the two lines are, as shares of the frame height."""
    height = int(cell["height"])
    crown = round(100 * int(cell["crown_y"]) / height)
    floor = round(100 * int(cell["floor_y"]) / height)
    return (
        "Layout: the last attached image is a layout guide, not part of the character. It shows the frame, "
        "its inner safe area (the blue box), the center line, an orange crown line and a teal floor line. "
        f"Place the anatomical top of the skull on the orange line, {crown}% of the frame height from the top, "
        f"and the lowest supporting sole on the teal line, {floor}% from the top, centered on the center line: "
        f"an upright crown-to-floor span of {floor - crown}% of the frame height. Ignore hair tufts, raised "
        "limbs, hats, accessories and props when identifying the crown; they may rise above the orange line "
        "but stay inside the blue box. The teal line sits INSIDE the safe area, above the bottom safe padding: "
        "it is a ground-contact line, not a frame edge. The supporting soles must touch it and may not float "
        "above it, and the space below it stays empty. The colored lines set the character's scale and height "
        "in the frame; they are not artwork. Do not reproduce the layout guide itself: no boxes, guide lines, "
        "center marks, labels, guide colors or the guide's grey background may appear in the output."
    )


def layout_guide_size(aspect_ratio: str | None) -> tuple[int, int]:
    """The guide's pixels for the requested ratio, long edge {LAYOUT_GUIDE_LONG_EDGE}; square without one.

    Only the proportions matter to the model, so the size is the ratio's own and not a
    provider's output size (codex has none to ask for).
    """
    if aspect_ratio in (None, "", "auto"):
        return LAYOUT_GUIDE_LONG_EDGE, LAYOUT_GUIDE_LONG_EDGE
    try:
        width_part, height_part = str(aspect_ratio).split(":")
        width, height = float(width_part), float(height_part)
        if width <= 0 or height <= 0:
            raise ValueError
    except ValueError as exc:
        raise SystemExit(f"gen: --layout-guide cannot read aspect ratio {aspect_ratio!r}") from exc
    scale = LAYOUT_GUIDE_LONG_EDGE / max(width, height)
    return round(width * scale), round(height * scale)


def layout_guide_cell(aspect_ratio: str | None) -> dict[str, Any]:
    """The one-slot guide's cell for the requested ratio: its size, safe margins and the two anatomy lines,
    one margin inside the safe box's top and bottom edges. What `layout_guide_text` says and
    `draw_layout_guide` draws."""
    # Imported here: `prepare` owns the row guide and pulls in the row machinery.
    from .prepare import normalize_cell

    width, height = layout_guide_size(aspect_ratio)
    cell = normalize_cell({"width": width, "height": height}, width, None)
    margin_y = int(cell["safe_margin_y"])
    return {**cell, "crown_y": 2 * margin_y, "floor_y": height - 1 - 2 * margin_y}


def draw_layout_guide(path: Path, aspect_ratio: str | None) -> dict[str, Any]:
    """Draws the one-slot guide at `path` and answers its cell (`layout_guide_cell`)."""
    from .prepare import draw_guide

    from PIL import Image, ImageDraw

    cell = layout_guide_cell(aspect_ratio)
    width, height = int(cell["width"]), int(cell["height"])
    draw_guide(path, 1, {key: value for key, value in cell.items() if key not in ("crown_y", "floor_y")})
    # The anatomy lines as the row experiments drew them on a 256 px cell (5 px lines, 16 px x 7 px
    # centre marks), scaled to this guide.
    scale = height / 256
    margin_x = int(cell["safe_margin_x"])
    crown_y, floor_y = int(cell["crown_y"]), int(cell["floor_y"])
    center = width // 2
    line, mark, half = max(1, round(5 * scale)), max(1, round(7 * scale)), round(8 * scale)
    image = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for y, colour, accent in ((crown_y, CROWN_LINE, CROWN_MARK), (floor_y, FLOOR_LINE, FLOOR_MARK)):
        draw.line((margin_x, y, width - 1 - margin_x, y), fill=colour, width=line)
        draw.line((center - half, y, center + half, y), fill=accent, width=mark)
    image.save(path)
    return cell


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
# The key planned this way is applied only to a raw that needs it: a raw that came
# back with a real transparent background anyway (`chroma.classify_raw_alpha`,
# 2026-10-04: a transparent `--ref` is answered with alpha) is published on its own
# alpha, measured as a native one (STRATEGY_SOURCE_REFS_RAW_ALPHA), because keying
# it again mattes the outline away. A checkerboard or key background is still keyed.
# Because the key is the engine's plan, the engine asks for it: a prompt that names no
# key background gets the chosen key's line (`chroma.KEY_BACKGROUND_TEXT`), since a ref
# prompt without one came back on white and keying white takes the outline with it.
STRATEGY_SOURCE_PROVIDER = "provider-default"
STRATEGY_SOURCE_REFS = "refs-attached"
STRATEGY_SOURCE_REFS_RAW_ALPHA = "refs-attached-raw-alpha"
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
    view: str | None = None,
    handed: list[handed_mod.Handed] | None = None,
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
    layout_guide: bool = False,
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
    _check_view(view, facing, facing_fix, handed)
    out = out.expanduser().resolve()
    refs = [Path(r).expanduser().resolve() for r in (refs or [])]
    for ref in refs:
        if not ref.is_file():
            raise SystemExit(f"gen: reference image not found: {ref}")

    backend = _make_provider(provider, keep_session=keep_session)
    # Decided before the model runs: the strategy shapes the transport prompt
    # (native asks for alpha) and the post-process (chroma keys it out).
    strategy: str | None = None
    strategy_source: str | None = None
    if transparent:
        # The layout guide is an attached image as much as a --ref is: the same step down applies.
        attached_for_strategy = [*refs, Path(LAYOUT_GUIDE_NAME)] if layout_guide else refs
        strategy, strategy_source = resolve_transparency_strategy(backend, alpha_mode, refs=attached_for_strategy)
        if decontam == "palette" and strategy != TRANSPARENCY_CHROMA:
            # before the provider is called: a paid generation must not end in this refusal
            raise SystemExit(f"gen: --decontam removes a chroma key; this image would use {strategy} alpha "
                             "(pass --alpha-mode chroma to key it instead)")
    # The key is the engine's plan when `auto` stepped down for the refs, so the engine asks for it: a
    # ref prompt with no key background is answered on white or another light ground, and keying that
    # takes the outline and light fills with it (2026-10-04). A key the prompt already names stays.
    parts = still_prompt(prompt, view=view, facing=facing, handed=handed, refs=bool(refs),
                         key=chroma_key if strategy_source == STRATEGY_SOURCE_REFS else None,
                         layout=layout_guide_cell(aspect_ratio) if layout_guide else None)
    prompt = parts.text
    for line in prompt_parts.note_lines(parts):
        print(f"[gen] {line}", file=sys.stderr)
    key_background: dict[str, Any] | None = None
    if (key_piece := parts.piece("key-background")) is not None:
        key_background = {"injected": key_piece.added, "key": key_piece.found or chroma_key}
        print(
            f"[gen] {len(refs)} reference image(s) attached — using chroma keying instead of "
            f"{backend.name}'s native alpha (native output with refs is unstable, 2026-09-08); "
            + (f"added a {chroma_key} key background line to the prompt; " if key_piece.added
               else f"the prompt already asks for a {key_piece.found} key background; ")
            + "a result that already has a transparent background keeps its own alpha. "
            "Pass --alpha-mode native to force it.",
            file=sys.stderr,
        )
    owns_workdir = workdir is None
    workdir = Path(workdir).expanduser().resolve() if workdir else Path(tempfile.mkdtemp(prefix="sprite-gen-gen-"))
    workdir.mkdir(parents=True, exist_ok=True)
    raw = workdir / "raw.png"

    try:
        guide_cell: dict[str, Any] | None = None
        attached = list(refs)
        if layout_guide:
            guide = workdir / LAYOUT_GUIDE_NAME
            guide_cell = draw_layout_guide(guide, aspect_ratio)
            # Last, so "the last attached image" in the prompt is the guide whatever the caller attached.
            attached.append(guide)
        request = GenRequest(
            prompt=prompt,
            raw=raw,
            refs=attached,
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
                backend, request, run, workdir, facing=facing, fix=facing_fix, mirror_ok=not handed)
            raw = request.raw
        raw_bytes = verify_png(raw)

        # Only the key `auto` planned because of the refs is checked against the raw; an
        # explicit --alpha-mode chroma is the caller's own key and runs as asked.
        raw_alpha: dict[str, Any] | None = None
        decontam_skipped: dict[str, Any] | None = None
        if strategy_source == STRATEGY_SOURCE_REFS:
            raw_alpha = chroma_mod.classify_raw_alpha(raw)
            if raw_alpha["verdict"] == chroma_mod.RAW_ALPHA_REAL:
                if decontam != "off":
                    # The generation is paid for and its alpha is good: decontam has no key to
                    # remove, so it is skipped and said, not refused (see the refusal before the call).
                    decontam_skipped = {
                        "requested": decontam,
                        "skipped": f"the raw came back with a transparent background "
                                   f"({raw_alpha['alpha_zero_pct']}% alpha 0) and has no key to remove",
                    }
                    print(f"[gen] {'warning: ' if decontam == 'palette' else ''}--decontam {decontam} skipped: "
                          f"{decontam_skipped['skipped']}", file=sys.stderr)
                strategy, strategy_source = TRANSPARENCY_NATIVE, STRATEGY_SOURCE_REFS_RAW_ALPHA
                print(
                    f"[gen] the raw came back with a transparent background ({raw_alpha['alpha_zero_pct']}% "
                    f"alpha 0, {raw_alpha['border_alpha_zero_pct']}% of the border) — publishing its own "
                    "alpha instead of keying it.",
                    file=sys.stderr,
                )
            elif raw_alpha["verdict"] == chroma_mod.RAW_ALPHA_AMBIGUOUS:
                raw_alpha["warning"] = (
                    f"the raw has some transparent pixels ({raw_alpha['alpha_zero_pct']}% alpha 0, "
                    f"{raw_alpha['border_alpha_zero_pct']}% of the border) but not a transparent background "
                    f"(needs {chroma_mod.RAW_ALPHA_MIN_ZERO_PCT}% and {chroma_mod.RAW_ALPHA_MIN_BORDER_ZERO_PCT}% "
                    "of the border); keyed as planned — check the outline, or pass --alpha-mode native"
                )
                print(f"[gen] warning: {raw_alpha['warning']}", file=sys.stderr)
        ref_stats = {
            **({"key_background": key_background} if key_background is not None else {}),
            **({"raw_alpha": raw_alpha} if raw_alpha is not None else {}),
            **({"decontam": decontam_skipped} if decontam_skipped is not None else {}),
        }

        chroma_stats: dict[str, Any] | None = None
        alpha_stats: dict[str, Any] | None = None
        out.parent.mkdir(parents=True, exist_ok=True)
        if strategy == TRANSPARENCY_NATIVE:
            alpha_stats = {
                "strategy": TRANSPARENCY_NATIVE,
                "strategy_source": strategy_source,
                **ref_stats,
                **chroma_mod.verify_native_alpha(raw, out, white_check=white_check),
            }
        elif strategy == TRANSPARENCY_CHROMA:
            chroma_stats = chroma_mod.key_transparent(raw, out, key=chroma_key, white_check=white_check,
                                                      decontam=decontam)
            alpha_stats = {"strategy": TRANSPARENCY_CHROMA, "strategy_source": strategy_source,
                           **ref_stats, **chroma_stats}
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
                   **({"facing": facing_report} if facing_report else {}),
                   **({"view": {"direction": view, "facing": facing if view in handed_mod.LATERAL_VIEWS else None,
                                "handed": [vars(h) for h in handed or []]}} if view else {}),
                   **({"layout_guide": guide_cell} if guide_cell else {}),
                   **({"prompt_notes": parts.notes} if parts.notes else {})},
        )
    finally:
        if owns_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def _check_view(view: str | None, facing: str | None, facing_fix: str, handed: list[handed_mod.Handed] | None) -> None:
    """Refuse a `--direction` / `--facing` / `--handed` combination that cannot be drawn, before anything runs."""
    if view is None:
        if handed:
            raise SystemExit("gen: --handed needs --direction: where an item shows depends on the view")
        return
    handed_mod.validate_view(view, facing)
    if view not in handed_mod.LATERAL_VIEWS and facing is not None:
        raise SystemExit(f"gen: the {view} view is not turned to a side; drop --facing")
    if handed and facing_fix == "mirror":
        raise SystemExit("gen: --facing-fix mirror turns the picture over and moves every --handed item to the other "
                         "side; use regen (it never mirrors with --handed) or none")


def still_prompt(prompt: str, *, view: str | None = None, facing: str | None = None,
                 handed: list[handed_mod.Handed] | None = None, refs: bool = False, key: str | None = None,
                 layout: dict[str, Any] | None = None) -> prompt_parts.Prompt:
    """The prompt a still is drawn from: the caller's text, then the engine's pieces (`prompt_parts.Prompt.add`;
    without `handed`, 2.22.0's prompt to the byte). In order:

    - `view` (`--direction`): the sentence for drawing the still at that view, turned to `facing` (the side and
      diagonal views), then where each `handed` item is in it (`handedness.text`);
    - with `refs` and a `facing`: the turn over the reference (`facing.prompt_suffix`);
    - `key` (green / magenta, a ref run `auto` planned to key): the key background line, unless the prompt
      names a key background already;
    - `layout` (the `--layout-guide` cell): where the guide's lines are.
    """
    parts = prompt_parts.Prompt(prompt)
    if view is not None:
        # The view sentences are the video pipeline's (a still is drawn for the clip that starts from it).
        from sprite_gen.video.batch import still_view_text

        text = still_view_text(view, facing or "right")
        parts.add("view", text[0].upper() + text[1:] + ".", facing=facing or prompt_parts.NO_TURN)
        if handed:
            parts.add("handed", handed_mod.text(handed, view, facing), sep=" ", handed=handed)
    if refs and facing is not None:
        parts.add("facing", facing_mod.prompt_suffix(facing), facing=facing)
    if key is not None:
        parts.add("key-background", chroma_mod.KEY_BACKGROUND_TEXT[key], key=key)
    if layout is not None:
        parts.add("layout", layout_guide_text(layout))
    return parts


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
        view=getattr(args, "direction", None),
        handed=handed_mod.parse_all(list(getattr(args, "handed", None) or [])) or None,
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
        layout_guide=bool(getattr(args, "layout_guide", False)),
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
            "(native on codex and openai, but chroma whenever --ref is attached — native alpha with refs is unstable — "
            "with the --chroma-key background line added to a prompt that names no key); "
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
    parser.add_argument("--direction", choices=handed_mod.VIEWS, help="draw the still at this sprite view: the engine's view sentence is added to the prompt (side and diagonal views also take --facing right|left)")
    parser.add_argument("--handed", action="append", default=[], metavar="ITEM=SIDE [PART]", help="with --direction: an asymmetric item on one of the character's own sides, e.g. 'the black smartwatch=left wrist' (repeatable); the prompt says where it is in this view, and the still is never mirrored")
    parser.add_argument("--trim-alpha", action="store_true", help="with --transparent: crop the published PNG to its opaque bbox so the bottom edge is the foot line (margins reported)")
    parser.add_argument(
        "--layout-guide",
        action="store_true",
        help=(
            "attach a one-slot layout guide (frame, inner safe area at the row guide's 9.4 %% margin, "
            "center line) after any --ref and ask for the whole subject inside the safe area, with room "
            "above the head and below the feet; drawn for --aspect-ratio, square without one"
        ),
    )
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
