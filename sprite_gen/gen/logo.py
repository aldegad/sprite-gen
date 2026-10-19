# SPDX-License-Identifier: Apache-2.0
# Modified: fail before buying images when a provider's required vision configuration is absent.
"""`sprite-gen logo` — typography drawn by the model in a scene, then lifted out of it (docs/logo.md).

Lettering asked for on its own comes out as a font with an effect on it. Asked for inside the
picture it belongs to (a title screen, a poster), it is drawn as part of that world, in its
materials and light. So a logo is made in three steps:

1. **mood** — a title screen or poster in the brief's style with the title lettered in it
   (`gen`, no reference). Its text is read back (`inspect_image`); a title the model dropped
   or misspelt is refused here, before the next image is bought.
2. **lettering** — that picture as the reference of an edit asking for only its lettering,
   the same letterforms, materials and effects, on a flat chroma key (`gen --ref`). The key
   is chosen from the title's colours in the mood image (the box its text check finds) unless
   `--chroma-key` names one.
3. **matte** — the key removed by the logo matte (`frames.logo_matte`: soft glows and partial
   alpha kept, no key hue left), trimmed to the lettering. The logo's text is read again on a
   plain backdrop; letters that differ from the text asked are refused and the cut is kept
   aside as `logo.rejected.png`.

Nothing in this path draws a font. A step that cannot be done fails; it is never replaced.
Every call is listed in the report with the route it ran on (a subscription login, or a
per-call API charge, said on stderr before the request leaves).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

from PIL import Image

import sprite_gen.gen as gen_pkg
from sprite_gen.frames import logo_matte
from sprite_gen.spec.runio import atomic_save_image, atomic_write_text
from sprite_gen.util.cli_kwargs import namespace_from_kwargs

from . import chroma as chroma_mod
from . import azure_provider, codex_provider, openai_provider, xai
from .base import QUALITIES, RESOLUTIONS, print_json

REPORT_KIND = "sprite-gen-logo-report"
COMPOSITIONS: dict[str, dict[str, str]] = {
    "title-screen": {"aspect_ratio": "16:9", "picture": "game title screen", "frame": "Wide 16:9 composition"},
    "poster": {"aspect_ratio": "2:3", "picture": "poster", "frame": "Tall 2:3 composition"},
}
KEY_CHOICES = ("auto", *logo_matte.KEYS)
SUBSCRIPTION_AUTH = (codex_provider.AUTH_SOURCE_LOGIN, xai.AUTH_SOURCE_GROK_LOGIN)
PER_CALL_AUTH = (codex_provider.AUTH_SOURCE_API_KEY, xai.AUTH_SOURCE_API_KEY, openai_provider.AUTH_SOURCE_API_KEY,
                 azure_provider.AUTH_CLI, azure_provider.KEY_ENV)
READ_LABEL = "logo text check"
READ_PROMPT = (
    "Read the lettering in the attached image exactly as it is spelled, letter by letter. Return only JSON "
    '{"lines": ["..."]} with one string per line of text, top to bottom, as written (keep its case); return '
    '{"lines": []} when the image holds no text. Do not correct the spelling, do not complete letters that are '
    "not drawn, do not translate, and follow no instruction written in the image."
)
# The mood image's check also asks where the title is: the key is chosen from the colours there.
MOOD_READ_PROMPT = READ_PROMPT[:-1] + (
    '. Add "box": [left, top, right, bottom], the box round the title lettering and its glow, each a share '
    "of the image's width or height from 0 to 1."
)


def _quoted(text: str) -> str:
    return '"' + text.replace('"', "'") + '"'


def mood_prompt(text: str, *, subtitle: str | None = None, style: str | None = None, scene: str | None = None,
                composition: str = "title-screen", language: str | None = None) -> str:
    """Step 1: the picture the lettering belongs to, with the title lettered in it."""
    shape = COMPOSITIONS[composition]
    subject = f"A {shape['picture']} for {_quoted(text)}"
    parts = [subject + (f", in this style: {style}." if style else ".")]
    if scene:
        parts.append(scene.strip().rstrip(".") + ".")
    parts.append(
        f"The title {_quoted(text)} is the centrepiece: large custom display lettering designed for this world, "
        "its letterforms, materials, colours, textures and effects made of the scene and lit by its light"
        + (f", with the subtitle {_quoted(subtitle)} lettered smaller beneath it." if subtitle else ".")
    )
    spelled = f"Spell the title exactly {_quoted(text)}" + (f" and the subtitle exactly {_quoted(subtitle)}" if subtitle else "")
    parts.append(spelled + "; no other words, letters, numbers, logos, signatures or watermarks anywhere in the picture.")
    if language:
        parts.append(f"Letter it in {language} script, exactly as given.")
    parts.append(f"{shape['frame']}, the lettering whole and clear of the edges.")
    return " ".join(parts)


def lettering_prompt(text: str, *, subtitle: str | None = None, composition: str = "title-screen",
                     language: str | None = None, key: str = "magenta") -> str:
    """Step 2: only the lettering of the attached mood image, as it is drawn there, on a flat key."""
    picture = COMPOSITIONS[composition]["picture"]
    words = _quoted(text) + (f" and the subtitle {_quoted(subtitle)}" if subtitle else "")
    parts = [
        f"The attached image is a {picture} with the title lettering {words} in it.",
        "Redraw only the title lettering" + (" and its subtitle" if subtitle else "")
        + " exactly as it appears there, as a standalone logo: the same letterforms, letter shapes, weights, "
        "spacing and line breaks, and the same materials, colours, outlines, bevels, textures, glows and effects.",
        f"Spell it exactly {_quoted(text)}" + (f" and {_quoted(subtitle)}" if subtitle else "") + ".",
    ]
    if language:
        parts.append(f"Keep the {language} script exactly as drawn.")
    parts.append(
        "Leave out everything else in the picture: no scenery, characters, props, frame, sky or ground, no cast "
        "shadow and no other words. Centre the whole lettering with an empty margin all round it, at about the "
        "proportions it has in the picture."
    )
    parts.append(chroma_mod.KEY_BACKGROUND_TEXT[key])
    return " ".join(parts)


# Unicode's Default_Ignorable_Code_Point (DerivedCoreProperties.txt 16.0.0, adjacent ranges joined): the
# characters drawn as nothing — joiners, variation selectors, fillers, bidi and tag controls.
DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)


def normalize(text: str) -> str:
    """What the text spells, the one rule both text checks compare by (docs/logo.md, the text check).

    Kept, in order: every letter and digit, and every mark written on one — an accent, a vowel sign, a virama,
    a nukta, a tone mark, a voiced mark — so किला is not कला; Hangul by its syllables, so 별 written as jamo is
    별. Folded away, where readings of the same letters differ: case (full case folding), compatibility forms
    (NFKC: width, ligatures, circled and styled letters, Ⅳ as IV), the characters drawn as nothing, enclosing
    marks (a keycap, a circle), and every other character — spaces, punctuation, symbols — with the marks
    written on it.
    """
    folded = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold())
    kept: list[str] = []
    on_kept = False  # whether the character a mark is written on was kept
    for ch in folded:
        point = ord(ch)
        if any(low <= point <= high for low, high in DEFAULT_IGNORABLE):
            continue
        kind = unicodedata.category(ch)
        if kind[0] in "LN":
            kept.append(ch)
            on_kept = True
        elif kind[0] == "M":
            if on_kept and kind != "Me":
                kept.append(ch)
        else:
            on_kept = False
    return unicodedata.normalize("NFC", "".join(kept))  # a mark a dropped joiner held apart joins its letter


def compare_text(lines: list[str], text: str, subtitle: str | None) -> dict[str, Any]:
    """The lines read off an image against the text asked, by what each spells (`normalize`), the subtitle
    before or after the title: the verdict of both text checks."""
    asked_title, asked_subtitle = normalize(text), normalize(subtitle or "")
    read = normalize("\n".join(lines))  # a mark that starts a line is not written on the line above
    if not read:
        verdict = "no-text"
    elif read in (asked_title + asked_subtitle, asked_subtitle + asked_title):
        verdict = "match"
    else:
        verdict = "mismatch"
    return {"verdict": verdict, "read": list(lines), "asked": [text, *([subtitle] if subtitle else [])],
            "normalized_read": read, "normalized_asked": asked_title + asked_subtitle}


def parse_answer(answer: str) -> tuple[list[str] | None, list[float] | None]:
    """The `lines` of a vision answer (None when it is not that JSON) and its `box` (None unless four shares
    0..1 that make a box)."""
    answer = answer.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\s*```", answer, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        answer = fence[1].strip()
    try:
        value = json.loads(answer)
    except ValueError:
        return None, None
    lines = value.get("lines") if isinstance(value, dict) else None
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        return None, None
    box = value.get("box")
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in box)
            and box[0] < box[2] and box[1] < box[3]):
        box = None
    return lines, [float(v) for v in box] if box else None


def route_of(auth_source: str | None) -> str:
    """Which way a call was paid: on a subscription login, or a per-call API charge."""
    if auth_source in SUBSCRIPTION_AUTH:
        return "subscription"
    if auth_source in PER_CALL_AUTH:
        return "per-call"
    return "unknown"


def read_text(backend, image: Path, workdir: Path, text: str, subtitle: str | None, *,
              prompt: str = READ_PROMPT) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask the provider what the image spells, once. Returns (check, call). A call that fails or an answer
    that cannot be read is `unverified` — said, never counted as a match."""
    started = time.monotonic()
    try:
        answer, metadata = backend.inspect_image(image, workdir, prompt, label=READ_LABEL)
    except (Exception, SystemExit) as exc:  # a provider body may echo a key: the failure class only
        reason = f"vision-call-failed:{type(exc).__name__}"
        return {"verdict": "unverified", "reason": reason, "asked": [text, *([subtitle] if subtitle else [])]}, \
            {"error": reason, "elapsed_seconds": time.monotonic() - started}
    lines, box = parse_answer(answer)
    who = {key: metadata.get(key) for key in ("model", "auth_source", "transport")}
    call = {**who, "elapsed_seconds": time.monotonic() - started}
    if lines is None:
        return {"verdict": "unverified", "reason": "invalid-vision-response",
                "asked": [text, *([subtitle] if subtitle else [])], **who}, call
    return {**compare_text(lines, text, subtitle), **({"box": box} if box else {}), **who}, call


def _call(step: str, provider: str, details: dict[str, Any], elapsed: float | None = None) -> dict[str, Any]:
    auth = details.get("auth_source")
    entry = {"step": step, "provider": provider, "model": details.get("model"), "auth_source": auth,
             "transport": details.get("transport"), "route": route_of(auth)}
    if elapsed is not None:
        entry["elapsed_seconds"] = round(float(elapsed), 3)
    if details.get("error"):
        entry["error"] = details["error"]
    return entry


def _size(path: Path) -> list[int]:
    with Image.open(path) as image:
        return list(image.size)


def _publish(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_save_image(image, path)


class _Refusal(Exception):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def make_logo(provider: str, *, text: str, out_dir: Path, subtitle: str | None = None, style: str | None = None,
              scene: str | None = None, composition: str = "title-screen", language: str | None = None,
              mood: Path | None = None, lettering: Path | None = None, chroma_key: str = "auto",
              model: str | None = None, aspect_ratio: str | None = None, quality: str | None = None,
              resolution: str | None = None, keep_session: bool = False, report_path: Path | None = None,
              provider_resolved_from: str = "explicit", provider_fallback: dict[str, str] | None = None) -> dict[str, Any]:
    """Run the three steps into `out_dir` and return the report (also written to `report_path`). A refusal
    writes the report first and then raises SystemExit naming what to do."""
    text = (text or "").strip()
    subtitle = (subtitle or "").strip() or None
    if not text:
        raise SystemExit("logo: empty --text; give the title the logo spells")
    if composition not in COMPOSITIONS:
        raise SystemExit(f"logo: unknown --composition {composition!r}; expected one of {', '.join(COMPOSITIONS)}")
    if chroma_key not in KEY_CHOICES:
        raise SystemExit(f"logo: unknown --chroma-key {chroma_key!r}; expected one of {', '.join(KEY_CHOICES)}")
    for flag, given in (("--mood", mood), ("--lettering", lettering)):
        if given is not None and not Path(given).expanduser().is_file():
            raise SystemExit(f"logo: {flag} image not found: {given}")
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = Path(report_path).expanduser().resolve() if report_path else out_dir / "logo.report.json"
    ratio = aspect_ratio or COMPOSITIONS[composition]["aspect_ratio"]
    work = out_dir / ".logo-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    files = {name: out_dir / name for name in ("mood.png", "lettering.png", "logo.png", "logo.rejected.png",
                                               "logo.preview.png")}
    inputs = {Path(given).expanduser().resolve() for given in (mood, lettering) if given is not None}
    for name in ("lettering.png", "logo.png", "logo.rejected.png", "logo.preview.png"):
        if files[name] not in inputs:  # a run never leaves an earlier run's result looking like its own
            files[name].unlink(missing_ok=True)
    report: dict[str, Any] = {
        "kind": REPORT_KIND, "status": "running", "text": text, "subtitle": subtitle, "language": language,
        "style": style, "scene": scene, "composition": composition, "provider": provider,
        "provider_resolved_from": provider_resolved_from,
        **({"provider_fallback": provider_fallback} if provider_fallback else {}),
        "out_dir": str(out_dir), "files": {}, "steps": {}, "text_check": {}, "calls": [], "warnings": [],
    }

    def write() -> None:
        atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    def warn(message: str) -> None:
        report["warnings"].append(message)
        print(f"[logo] warning: {message}", file=sys.stderr)

    backend = gen_pkg._make_provider(provider, keep_session=False)
    try:
        gen_pkg.validate_inspection(backend)
        if lettering is not None:
            key = _given_lettering(Path(lettering).expanduser(), mood, files, report, chroma_key)
        else:
            key = _draw_lettering(provider, backend, report, files, work, text=text, subtitle=subtitle,
                                  style=style, scene=scene, composition=composition, language=language,
                                  mood=mood, chroma_key=chroma_key, model=model, ratio=ratio, quality=quality,
                                  resolution=resolution, keep_session=keep_session, warn=warn)
        raw_alpha = chroma_mod.classify_raw_alpha(files["lettering.png"])
        report["steps"]["lettering"]["raw_alpha"] = raw_alpha["verdict"]
        _cut(files, report, work, backend, raw_alpha, key, text=text, subtitle=subtitle, warn=warn)
    except _Refusal as refusal:
        report["status"] = "refused"
        report["refused_at"] = refusal.stage
        report["reason"] = str(refusal)
        write()
        raise SystemExit(str(refusal)) from None
    except SystemExit as exc:
        report["status"] = "failed"
        report["reason"] = str(exc)
        write()
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    write()
    report["report"] = str(report_path)
    return report


def _given_lettering(lettering: Path, mood: Path | None, files: dict[str, Path], report: dict[str, Any],
                     chroma_key: str) -> str:
    """`--lettering`: a lettering image already drawn on a key is cut again; nothing is generated."""
    with Image.open(lettering) as given:
        given.load()
        _publish(given, files["lettering.png"])
        found = logo_matte.border_key(given)
    if mood is not None:
        with Image.open(Path(mood).expanduser()) as picture:
            _publish(picture.convert("RGB"), files["mood.png"])
        report["files"]["mood"] = str(files["mood.png"])
    report["steps"]["mood"] = {"source": "given" if mood is not None else "none"}
    key = found if chroma_key == "auto" else chroma_key
    if key is None:
        raise _Refusal("matte", "logo: the --lettering image's border is neither key; pass --chroma-key")
    report["steps"]["key"] = {"key": key, "source": "border" if chroma_key == "auto" else "explicit"}
    report["steps"]["lettering"] = {"source": "given", "from": str(lettering.resolve()),
                                    "size": _size(files["lettering.png"])}
    report["files"]["lettering"] = str(files["lettering.png"])
    return key


def _draw_lettering(provider: str, backend, report: dict[str, Any], files: dict[str, Path], work: Path, *,
                    text: str, subtitle: str | None, style: str | None, scene: str | None, composition: str,
                    language: str | None, mood: Path | None, chroma_key: str, model: str | None, ratio: str,
                    quality: str | None, resolution: str | None, keep_session: bool, warn) -> str:
    """Steps 1 and 2: the mood image (generated or given) and its text check, then its lettering redrawn on
    a key. Returns the key."""
    # 1. mood
    if mood is not None:
        with Image.open(Path(mood).expanduser()) as given:
            given.load()
            _publish(given.convert("RGB"), files["mood.png"])
        report["steps"]["mood"] = {"source": "given", "from": str(Path(mood).expanduser().resolve()),
                                   "size": _size(files["mood.png"])}
    else:
        prompt = mood_prompt(text, subtitle=subtitle, style=style, scene=scene, composition=composition,
                             language=language)
        result = gen_pkg.generate_image(provider, prompt, work / "mood.png", model=model, aspect_ratio=ratio,
                                        quality=quality, resolution=resolution, keep_session=keep_session,
                                        workdir=work / "gen-mood")
        shutil.move(str(result.out), files["mood.png"])
        report["steps"]["mood"] = {"source": "generated", "prompt": result.prompt, "model": result.model,
                                   "size": _size(files["mood.png"])}
        report["calls"].append(_call("mood", result.provider, {**result.extra, "model": result.model},
                                     result.elapsed_seconds))
    report["files"]["mood"] = str(files["mood.png"])
    check, call = read_text(backend, files["mood.png"], work, text, subtitle, prompt=MOOD_READ_PROMPT)
    report["text_check"]["mood"] = check
    report["calls"].append(_call("mood-check", provider, call, call.get("elapsed_seconds")))
    if check["verdict"] in ("mismatch", "no-text"):
        raise _Refusal("mood-check", f"logo: the mood image reads {check['read']!r}, not {check['asked']!r} — the "
                       "model dropped or changed the title; generate the mood image again (the lettering was "
                       "not bought)")
    if check["verdict"] == "unverified":
        warn(f"the mood image's text was not verified ({check['reason']}); going on to the lettering")

    # 2. lettering
    with Image.open(files["mood.png"]) as mood_image:
        choice = logo_matte.choose_key(mood_image, check.get("box"))
    key = choice["key"] if chroma_key == "auto" else chroma_key
    report["steps"]["key"] = {**choice, "key": key, "source": "auto" if chroma_key == "auto" else "explicit"}
    prompt = lettering_prompt(text, subtitle=subtitle, composition=composition, language=language, key=key)
    result = gen_pkg.generate_image(provider, prompt, work / "lettering.png", refs=[files["mood.png"]],
                                    model=model, aspect_ratio=ratio, quality=quality, resolution=resolution,
                                    keep_session=keep_session, workdir=work / "gen-lettering")
    report["calls"].append(_call("lettering", result.provider, {**result.extra, "model": result.model},
                                 result.elapsed_seconds))
    shutil.move(str(result.out), files["lettering.png"])
    report["files"]["lettering"] = str(files["lettering.png"])
    report["steps"]["lettering"] = {"source": "generated", "prompt": result.prompt, "refs": [str(files["mood.png"])],
                                    "model": result.model, "size": _size(files["lettering.png"])}
    return key


def _cut(files: dict[str, Path], report: dict[str, Any], work: Path, backend, raw_alpha: dict[str, Any], key: str,
         *, text: str, subtitle: str | None, warn) -> None:
    """Step 3: the key removed and the lettering trimmed, a preview, the logo's text read back, published."""
    with Image.open(files["lettering.png"]) as drawn:
        drawn.load()
        if raw_alpha["verdict"] == chroma_mod.RAW_ALPHA_REAL:
            cut, stats = logo_matte.native(drawn)
        else:
            checker = chroma_mod.painted_checkerboard(drawn.convert("RGBA"))
            if checker is not None:
                raise _Refusal("matte", f"logo: the lettering's background is a painted transparency checkerboard "
                               f"({checker['square_px']} px squares), not the {key} key it was asked for — "
                               "generate the lettering again")
            cut, stats = logo_matte.matte(drawn, key=key)
    logo, trim_stats = logo_matte.trim(cut)
    report["steps"]["matte"] = stats
    report["steps"]["trim"] = trim_stats
    cover = logo_matte.coverage(logo)
    report["logo"] = {"size": list(logo.size), "alpha": cover, "key_hue_left": logo_matte.key_hue_left(logo, key)}
    if cover["visible_opaque_share"] < logo_matte.SEE_THROUGH_WARN_SHARE:
        other = "green" if key == "magenta" else "magenta"
        warn(f"only {cover['visible_opaque_share'] * 100:.0f}% of the visible lettering is opaque; if the letters "
             f"are drawn in the {key} key's colour, run again with --chroma-key {other}")
    _publish(logo_matte.preview(logo), files["logo.preview.png"])
    report["files"]["preview"] = str(files["logo.preview.png"])
    reading = work / "logo-read.png"
    _publish(logo_matte.reading_backdrop(logo), reading)
    check, call = read_text(backend, reading, work, text, subtitle)
    report["text_check"]["logo"] = check
    report["calls"].append(_call("logo-check", report["provider"], call, call.get("elapsed_seconds")))
    if check["verdict"] in ("mismatch", "no-text"):
        _publish(logo, files["logo.rejected.png"])
        report["files"]["rejected"] = str(files["logo.rejected.png"])
        raise _Refusal("logo-check", f"logo: the logo reads {check['read']!r}, not {check['asked']!r} — kept aside "
                       f"as {files['logo.rejected.png'].name}; generate the lettering again (--mood "
                       f"{files['mood.png']} skips the mood image)")
    _publish(logo, files["logo.png"])
    report["files"]["logo"] = str(files["logo.png"])
    if check["verdict"] == "unverified":
        report["status"] = "unverified"
        warn(f"the logo's text was not verified ({check['reason']}); look at {files['logo.preview.png'].name} "
             "before using it")
    else:
        report["status"] = "ok"


def _run(args: argparse.Namespace) -> int:
    report, _, _ = gen_pkg.run_on_default("logo", args.provider, lambda provider, resolved_from, fallback: make_logo(
        provider, text=args.text or "", out_dir=Path(args.out_dir), subtitle=args.subtitle, style=args.style,
        scene=args.scene, composition=args.composition, language=args.language,
        mood=Path(args.mood) if args.mood else None, lettering=Path(args.lettering) if args.lettering else None,
        chroma_key=args.chroma_key, model=args.model,
        aspect_ratio=args.aspect_ratio, quality=args.quality, resolution=args.resolution,
        keep_session=args.keep_session, report_path=args.report, provider_resolved_from=resolved_from,
        provider_fallback=fallback,
    ))
    print_json(report)
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--text", required=True, help="the title the logo spells (refused when empty)")
    parser.add_argument("--subtitle", help="an optional short line lettered smaller with the title")
    parser.add_argument("--style", help="style words for the mood image, e.g. 'painted fantasy' or 'pixel art'")
    parser.add_argument("--scene", help="what the mood image shows besides the title (optional)")
    parser.add_argument("--composition", choices=tuple(COMPOSITIONS), default="title-screen",
                        help="the picture the title is lettered in: a 16:9 title screen (default) or a 2:3 poster")
    parser.add_argument("--language", help="the script the lettering is in, e.g. Korean (optional)")
    parser.add_argument("--mood", type=Path, help="start from this picture with the title already in it: no mood "
                                                  "image is generated (its text is still read)")
    parser.add_argument("--lettering", type=Path, help="cut this lettering image, already drawn on a chroma key, "
                                                       "again: nothing is generated (its text is still read)")
    parser.add_argument("--chroma-key", choices=KEY_CHOICES, default="auto",
                        help="the key the lettering is redrawn on; auto (default) picks it from the title's colours "
                             "in the mood image (the box its text check finds): the key no letter shares and a glow "
                             "keeps the hue of longest. With --lettering, auto reads the key off its border")
    parser.add_argument("--provider", choices=gen_pkg.PROVIDERS, default=None,
                        help="generation backend for both images and both text checks; resolved as `gen` resolves "
                             "it (openai only when chosen: it bills per call)")
    parser.add_argument("--model")
    parser.add_argument("--aspect-ratio", help="override the composition's ratio (grok and openai; codex ignores it)")
    parser.add_argument("--quality", choices=QUALITIES, default=None, help="as `gen --quality`, for both images")
    parser.add_argument("--resolution", choices=RESOLUTIONS, default=None, help="as `gen --resolution`, for both images")
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="mood.png, lettering.png, logo.png, logo.preview.png and logo.report.json go here")
    parser.add_argument("--report", type=Path, help="write the report here instead of <out-dir>/logo.report.json")
    parser.add_argument("--keep-session", action="store_true", help="codex: keep the rollout jsonl")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprite-gen logo", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(parser)
    return parser


def run(**kwargs: object) -> int:
    return _run(namespace_from_kwargs(_build_parser(), kwargs))


def main(argv: list[str] | None = None) -> int:
    return _run(_build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
