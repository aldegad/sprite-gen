# SPDX-License-Identifier: Apache-2.0
"""Antigravity CLI (`agy`) provider — Google AI Pro subscription, no API key.

Same category as `codex_provider`: a subscription-backed agent CLI, not a bare
image API. `agy -p "<prompt>" --output-format json` runs one non-interactive
turn; the agent picks the image model itself, writes the PNG to disk, and reports
what it did in a free-text `response` field. There is no structured
`output_path`, so the transport contract this adapter owns is:

1. **We name the destination.** The prompt states `request.raw`'s absolute path as
   the one file to write, and `--add-dir` puts that directory in the agent's
   workspace so writing there needs no extra approval. Naming the exact path is
   cleaner than parsing a relative path back out of prose, and it was verified to
   work first try (2026-09-12 실측: `raw.png` landed byte-exact at the named path).
2. **We still parse the response.** The reported path(s) are recorded in
   `extra.reported_paths` and used as the resolution route whenever the named
   destination was *not* written, so an agent that saved elsewhere is recovered
   instead of silently failing. Reported, never trusted.
3. **The bytes on disk decide.** `request.raw` is unlinked before the spawn, so
   its existence afterwards proves *this* run wrote it (a reused `--workdir`
   cannot hand us a stale image), and `verify_png` decodes it before we claim
   success (No Silent Fallback, see `base.py`'s module docstring).

Transparency — why `chroma` (a deliberate reversal, 2026-09-12):
`Provider.transparency` selects which post-process `sprite_gen.gen.generate_image`
runs: `native` -> `chroma.verify_native_alpha(raw, out)` (measure alpha the model
already produced), `chroma` -> `chroma.key_transparent(raw, out)` (matte a
`#FF00FF`/`#00FF00` background out with our own tested YCbCr keyer).

agy's image model returns an **opaque** raster, so alpha can only come from a
segmentation step. The first cut of this adapter let the agent supply that step: it
was asked for "a transparent background", and it answered by installing and running
`rembg` on its own initiative, which did produce real alpha (measured: corners
alpha=0, subject alpha=255). That worked — and is exactly why it was replaced.
**Transparency belongs to sprite-gen's pipeline, not to an agent's improvisation:**

- *Determinism.* "The agent decides how to matte" is a different program on every
  run. It can pick a different tool, change its parameters, silently skip the step,
  or (as observed) spend most of a ~130 s first run downloading ~1 GB of U^2-Net
  weights. `key_transparent` is code in this repo, with tests and a measured contract.
- *One extraction path.* The chroma matte is the same `remove_chroma_background_ycbcr`
  behind `extract` / `cutout` / `slice-sheet` / grok. Every additional way to obtain
  alpha is another way for results to disagree.
- *Category.* On transparency agy belongs with grok — a backend that cannot return
  alpha, so we generate on a key and matte it out — even though its auth model (a
  Google AI Pro subscription CLI) puts it with codex. Backend capability decides the
  strategy; the auth model does not.

The difference from grok is that grok is a bare API: it paints exactly the background
the caller's prompt describes, so its adapter says nothing about the key and the
sprite-row prompts carry it. An agent left unbriefed picks its own background and,
measured, mattes it out unasked — so this adapter must both state the key and forbid
the improvisation. It reads the key from `request.chroma_key`, which the orchestrator
sets to the very key it will matte out, so the painted background and the matte can
never disagree.

Decisive flags (each read off `agy --help`, 2026-09-12):
- `--output-format json`  one JSON object on stdout (`status`, `response`,
  `duration_seconds`, `num_turns`, `usage`, `conversation_id`). Without it the
  output is prose only and `status` is unavailable.
- `--add-dir <dir>`  puts a directory in the agent's workspace. Used for the
  destination directory and for each reference image's directory.
- `--model <id>`  honours `request.model` when the caller pinned one.
- NO `--dangerously-skip-permissions`  tool approval is a **one-time local setup**
  (`agy` -> `/config` -> `Tool Permission` -> `always-proceed`), deliberately not
  something a provider adapter turns on behind the operator's back. A machine that
  skipped that setup stalls on the approval prompt and is killed by the timeout
  below, whose message says so.

Capability limit — one image per call, not a sprite row:
- A single-subject prompt completes reliably in ~27-65 s. A MULTI-POSE ROW prompt (the
  shape `prepare.py` writes for `gen-set`/`reroll`: several distinct gait poses, one
  identity, no grid or labels) did not complete at all — 2026-09-12 실측: agy's own
  5-minute `--print-timeout` expired with the turn still in progress, ~240k tokens
  spent (218k input via cache, 21k output, 9.8k thinking), `status: "SUCCESS"` with an
  empty response and no file anywhere. This adapter refused it correctly (no
  recoverable candidate -> SystemExit), but the capability is simply not there.
- `sprite_gen.gen.ROW_PROVIDERS` therefore excludes agy, and `gen-set`/`reroll` take
  that list while `gen`/`interpolate` (one figure per call) take the full `PROVIDERS`.
  Only one prompt shape was tried, so treat this as unproven rather than impossible.

Security posture — read this before trusting the instructions above:
- **The anti-self-matting rule is prose, and prose is not enforcement.** "Do NOT run
  rembg, any background-removal, segmentation, matting or cutout tool" is a request in
  a prompt. Nothing sandboxes or rate-limits what the agy agent actually does with its
  shell access, and this exact agent has already installed and run a Python package
  unprompted (that is the measured behaviour this design exists to stop). Assume the
  instruction can be ignored on any given run. What is real, and does not depend on the
  agent cooperating, is the check afterwards: `key_transparent` fails loudly on an image
  with no chroma left to key, so a disobeyed prompt surfaces as a failed run rather than
  a silently wrong sprite.
- **`--add-dir` is a workspace grant, not a jail.** It is what the agent is pointed at,
  not a limit on what it can reach; the operator's `always-proceed` tool permission is
  what actually lets it act. Everything this adapter does with agy's output therefore
  treats it as untrusted: the destination is unlinked before the spawn so only a file
  from THIS run can be published, recovery candidates parsed out of the response are
  resolved (defeating `..` and symlinks) and refused unless they land inside
  `write_roots`, reference images are never eligible as output, and `verify_png` decodes
  the bytes before success is claimed.
- **The residual risk is stated, not solved.** A cooperating-but-wrong agent that writes
  a valid PNG of the wrong thing to the right path is indistinguishable from a correct
  run at this layer. Curation/QA downstream is where that is caught.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from .base import (
    TRANSPARENCY_CHROMA,
    GenRequest,
    GenTimeoutError,
    ProviderRun,
    provider_binary,
    provider_subprocess_env,
    verify_png,
)

# agy gets its own hard timeout instead of base.GEN_TIMEOUT_SECONDS (180 s), for
# two measured reasons:
#   1. A full run is ~63 s (2026-09-12 실측: 62.96 s reported `duration_seconds`,
#      single turn) and was ~130 s while the agent was still doing its own rembg
#      segmentation. The chroma design removes the ML step, but an agent turn is
#      still plan + generate + save, so 180 s leaves little headroom on a slow day.
#   2. agy has its own `--print-timeout` (default 5m0s) for waiting on the
#      background tools it spawns. Our kill must sit ABOVE that, otherwise we
#      shoot the child while it is still inside its own bounded wait and lose the
#      structured JSON error it was about to print. 420 s = agy's 300 s ceiling
#      plus ~2 min of process startup/teardown slack.
# The bound itself is not optional — an agent CLI that stalls with no output has
# no remedy but a kill (same regression class as base.GEN_TIMEOUT_SECONDS).
AGY_GEN_TIMEOUT_SECONDS = int(os.environ.get("SPRITE_GEN_AGY_TIMEOUT_SECONDS", "420"))

# Hex for each `chroma.KEYS` name, stated in the prompt alongside the colour word.
# A hex triplet is unambiguous where "magenta" is not (pink? purple? fuchsia?), and
# `remove_chroma_background_ycbcr` keys from the background chroma it detects on the
# borders rather than from the pure value, so a near-miss still mattes — but the
# closer the generator paints, the cleaner the fringe.
_KEY_HEX = {"magenta": "#FF00FF", "green": "#00FF00"}

# The background instruction is TWO independent pieces, on purpose. They were one
# block until a review caught the consequence (2026-09-12), and the split is the whole
# fix — see `_build_prompt` for which piece applies when.
#
# 1. `_NO_SELF_MATTE_INSTRUCTION` — UNCONDITIONAL, every single agy run.
#
#    "sprite-gen owns transparency, agy never does" is an invariant of this adapter, not
#    a property of one code path, so its instruction cannot be conditional either. That
#    is not theoretical: this agent installed and ran `rembg` unprompted on the previous
#    design, purely because it inferred a cutout was wanted — and the row/tween/reroll
#    pipelines (`gen_set.run_gen_cli`, `reroll`, `interpolate.gen_interpolator`) call
#    `generate_image` WITHOUT `transparent=True`, because their prompts already carry the
#    run's key and `extract` does the matting later. Gating this half on "are we keying
#    right now" therefore left every pipeline row unprotected: a prompt that says "flat
#    magenta background" and nothing else is exactly the input that provoked the
#    unasked-for segmentation in the first place. An alpha'd row would then reach
#    `remove_chroma_background_ycbcr` with no chroma left to key.
#
#    The refusal is spelled out per tool class (segmentation, matting, alpha,
#    checkerboard) and the reason is given — an agent that understands WHY the flat
#    backdrop is wanted is far less likely to "improve" on it.
#
# 2. `_CHROMA_BACKDROP_TEMPLATE` — only when we know the key (`request.chroma_key`).
#
#    Naming a colour is only safe when it is THE colour that will be matted out. On the
#    pipeline paths we do not know it: `prepare.py` picks the run's key per character
#    (auto magenta/green from the base image) and writes it into each row prompt itself,
#    while `generate_image`'s own `chroma_key` argument is just its "magenta" default
#    there. Injecting that default would contradict a green run's prompt. So on those
#    paths the caller's prompt stays the single source of the backdrop, and this adapter
#    adds only piece 1.
#
# Written in English because agy's own tooling/skill vocabulary is English.
_NO_SELF_MATTE_INSTRUCTION = (
    "BACKGROUND HANDLING — this is mandatory on every image you save here:\n"
    "  - The saved PNG must be OPAQUE. Do NOT remove the background. Do NOT run rembg, "
    "any background-removal, segmentation, matting or cutout tool. Do NOT produce an "
    "alpha channel, and do NOT draw a checkerboard pattern to imply transparency.\n"
    "  - The caller removes the background itself, downstream, with its own matte. A "
    "pre-removed background, a soft/blended subject edge, or a saved alpha channel "
    "breaks that step. Hand back the flat, opaque, fully-rendered image."
)
_CHROMA_BACKDROP_TEMPLATE = (
    "  - Place the subject on a COMPLETELY FLAT, SOLID, UNIFORM {name} chroma-key "
    "backdrop, hex {hex}. One single colour across every background pixel: no gradient, "
    "no vignette, no shading, no lighting falloff, no texture, no drop shadow on the "
    "backdrop, no scenery, no floor line, no horizon. Flat {hex} behind the subject, "
    "nothing else — that exact colour is what the caller keys out."
)

# Response parsing. `response` is prose written by an agent, not a schema, so every
# shape observed or plausibly emitted gets its own pattern and they are all tried;
# the caller then keeps whichever candidate actually exists on disk. Observed
# 2026-09-12: a bare absolute path on its own line. Documented elsewhere in agy's
# output: a markdown `[name](file:///...)` link and a `**File:**`/`**Folder:**`
# detail pair. None of these is a "fallback" for another — they are alternate
# renderings of the same fact, so all are canonical.
_MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)>\s]+)>?\s*\)")
_FILE_URL_RE = re.compile(r"file://[^\s)\]\"'<>]+")
_FILE_DETAIL_RE = re.compile(r"\*\*File:\*\*\s*`?([^\n`*]+?)`?\s*(?:\n|$)")
_FOLDER_DETAIL_RE = re.compile(r"\*\*Folder:\*\*\s*`?([^\n`*]+?)`?\s*(?:\n|$)")
# A bare path on its own line — Windows drive-letter or POSIX absolute, image suffix.
_BARE_PATH_RE = re.compile(
    r"(?mi)^\s*`?((?:[A-Za-z]:[\\/]|/)[^\n`*]+?\.(?:png|webp|jpe?g))`?\s*$"
)
_IMAGE_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg")


def _from_file_url(token: str) -> str:
    """Decode a `file://` URL to a filesystem path (percent-decoded, drive-aware).

    `file:///D:/x/image%20test/boy.png` -> `D:/x/image test/boy.png`. urlparse
    leaves the Windows form as `/D:/...`, so the leading slash is dropped when a
    drive letter follows it.
    """
    parsed = urlparse(token)
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path


def reported_paths(response: str) -> list[str]:
    """Every filesystem path the agent's prose claims, in first-seen order.

    Order is stable and duplicate-free so the caller's choice is deterministic
    and the list can be published in the report as what the agent said.
    """
    found: list[str] = []

    def add(token: str) -> None:
        token = token.strip().strip("`").strip()
        if not token:
            return
        if token.lower().startswith("file://"):
            token = _from_file_url(token)
        if not token.lower().endswith(_IMAGE_SUFFIXES):
            return
        if token not in found:
            found.append(token)

    for match in _MD_LINK_RE.finditer(response):
        add(match.group(1))
    for match in _FILE_URL_RE.finditer(response):
        add(match.group(0))
    for match in _BARE_PATH_RE.finditer(response):
        add(match.group(1))
    # A `**File:**` detail names a bare filename and the `**Folder:**` beside it in the
    # SAME details block supplies its directory. Pair them by position, never by cross
    # product: a response listing two saves must not be able to produce file A under
    # folder B. That is not cosmetic — a wrong pairing that happens to name some real
    # file inside an `--add-dir` workspace would be copied out and published, and
    # `verify_png` only proves a file is a PNG, not that it is the right PNG.
    #
    # "Beside it" = nearest `**Folder:**` AFTER the `**File:**` (agy's details blocks
    # list File then Folder), falling back to the nearest one BEFORE it so a
    # Folder-then-File block still pairs correctly. Only those two candidates are ever
    # considered, so a distant unrelated block can never be joined on.
    folders = [
        (match.start(), match.group(1).strip().strip("`"))
        for match in _FOLDER_DETAIL_RE.finditer(response)
    ]
    for match in _FILE_DETAIL_RE.finditer(response):
        name = match.group(1).strip().strip("`")
        add(name)  # already absolute, or resolvable against the workdir
        if not name or Path(name).is_absolute():
            continue
        after = next((text for start, text in folders if start > match.start()), None)
        before = next(
            (text for start, text in reversed(folders) if start < match.start()), None
        )
        for folder in (after, before):
            if folder:
                add(str(Path(folder) / name))
                break
    return found


def _background_instruction(key: str | None) -> str:
    """The background block for one run: the never-self-matte rule, plus the key if known.

    An unknown key fails here rather than reaching the model: generating against a
    colour the matte does not know is a guaranteed 0.0%-keyed failure two minutes
    later, and a named error now is cheaper than a mystery then.
    """
    if key is None:
        return _NO_SELF_MATTE_INSTRUCTION
    hex_value = _KEY_HEX.get(key)
    if hex_value is None:
        raise SystemExit(
            f"agy-gen: unknown chroma key {key!r}; expected one of {', '.join(sorted(_KEY_HEX))}"
        )
    return (
        _NO_SELF_MATTE_INSTRUCTION
        + "\n"
        + _CHROMA_BACKDROP_TEMPLATE.format(name=key, hex=hex_value)
    )


def _build_prompt(
    user_prompt: str,
    destination: Path,
    refs: list[Path],
    *,
    chroma_key: str | None = None,
) -> str:
    """Wrap the caller's prompt in the agy transport contract.

    The caller's prompt is the SSoT and is passed through verbatim; everything
    around it (the exact destination path, the reference-image list, the chroma
    backdrop instruction) is this adapter's transport, owned in exactly one place.

    The "never matte it yourself" rule goes in on EVERY run, keyed or not — it is an
    invariant of this adapter, and the row/tween/reroll pipelines (which never pass
    `transparent=True`) need it most. `chroma_key` adds the second, colour-naming half
    and is set only when the orchestrator is about to matte that exact key out; when it
    is None the backdrop colour stays entirely the caller's prompt's business, so a
    sprite row that already carries its run's own key gets no contradictory second one.
    """
    lines = [
        "Generate exactly ONE image with a real generative image model.",
        "Do not draw it procedurally with code (PIL/ImageMagick/SVG) — that is not a generation.",
        "",
    ]
    if refs:
        # agy's print mode has no image-input flag (`-i` is `--prompt-interactive`,
        # not an image). References ride the workspace instead: each reference's
        # directory is passed with `--add-dir` and the absolute paths are named
        # here. Verified 2026-09-12 — the agent opened a PNG from an added dir and
        # described its contents correctly, so this is a real vision input, not a
        # hopeful instruction.
        lines.append(
            "First OPEN AND LOOK AT these reference images and follow them for identity, "
            "style, palette and framing:"
        )
        lines += [f"  - {ref}" for ref in refs]
        lines.append("")
    lines += ["Prompt:", user_prompt.strip(), ""]
    lines += [_background_instruction(chroma_key), ""]
    lines += [
        "Save the final PNG at exactly this absolute path, overwriting anything already there:",
        str(destination),
        "",
        "Write no other copy of the image, and do not move or rename it afterwards.",
        "When you are done, reply with that absolute path and nothing else.",
    ]
    return "\n".join(lines)


def _parse_envelope(stdout: str) -> dict:
    """Parse `--output-format json`'s single JSON object off stdout.

    Tolerates leading/trailing noise (a banner or an update notice) by scanning
    lines for the first one that parses as a JSON object carrying `status`, then
    falling back to the whole stream. A stream with no such object is a transport
    failure and says so with the head of what was actually printed.
    """
    text = (stdout or "").strip()
    if text:
        try:
            whole = json.loads(text)
            if isinstance(whole, dict):
                return whole
        except json.JSONDecodeError:
            pass
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and "status" in event:
                return event
    head = "\n".join(text.splitlines()[:20]) or "(nothing on stdout)"
    raise SystemExit(
        "agy-gen: agy printed no JSON envelope despite --output-format json — "
        f"refusing to guess what happened:\n{head}"
    )


def _resolve_candidate(token: str, workdir: Path) -> Path | None:
    """Fully resolved path for one reported token, or None if it cannot be resolved.

    A relative token is workdir-relative. `resolve()` is what makes the containment
    check in `_within` meaningful: it normalises away `..` traversal AND follows
    symlinks, so a link sitting inside the workspace that points at an external file
    is judged by its real target, not by where the link happens to live.
    """
    path = Path(token).expanduser()
    if not path.is_absolute():
        path = workdir / path
    try:
        return path.resolve()
    except OSError:
        # A malformed token (illegal characters, a too-long path) is not a candidate.
        return None


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    """Is an already-resolved path inside one of these already-resolved roots?"""
    return any(path == root or path.is_relative_to(root) for root in roots)


class AgyProvider:
    """Generate one image through the Antigravity CLI (`agy`)."""

    name = "agy"
    # See the module docstring: agy's model cannot return alpha, so the adapter has it
    # paint a flat key backdrop and sprite-gen's own `key_transparent` mattes that out.
    # Deliberately NOT `native` — letting the agent segment for us worked once and is
    # non-deterministic by construction.
    transparency = TRANSPARENCY_CHROMA

    def generate(self, request: GenRequest, workdir: Path) -> ProviderRun:
        destination = Path(request.raw).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Unlink first so "the file exists afterwards" is proof THIS run wrote it.
        # `gen` normally hands out a fresh temp workdir, but `--workdir` lets a
        # caller reuse one, and a stale raw.png from an earlier attempt would
        # otherwise be published as a success.
        destination.unlink(missing_ok=True)

        refs = [Path(ref).expanduser().resolve() for ref in request.refs]
        for ref in refs:
            if not ref.is_file():
                raise SystemExit(f"agy-gen: reference image not found: {ref}")

        # One --add-dir per distinct directory the agent must touch: the output
        # directory plus every reference's directory (deduplicated, stable order).
        add_dirs: list[str] = []
        for directory in (destination.parent, *(ref.parent for ref in refs)):
            text = str(directory)
            if text not in add_dirs:
                add_dirs.append(text)

        # The boundary the recovery path will accept a file from. Deliberately NARROWER
        # than `add_dirs`: `--add-dir` is a read+write grant covering the reference
        # directories too, whereas these are the only two places agy is TOLD to write
        # (the output directory, and the working directory it is launched in, which is
        # where a relative save lands). A generated image appearing anywhere else is not
        # something to trust and copy — it is something to refuse and report.
        write_roots: tuple[Path, ...] = tuple(
            dict.fromkeys((destination.parent.resolve(), Path(workdir).expanduser().resolve()))
        )

        if request.native_alpha:
            # Unreachable through `generate_image` (`resolve_transparency_strategy`
            # refuses `--alpha-mode native` on a chroma provider), so this guards a
            # direct caller rather than the CLI — and it fails before the model runs,
            # exactly like grok's equivalent refusal, instead of returning an opaque
            # PNG that something downstream would have to notice.
            raise SystemExit(
                "agy-gen: agy cannot return an alpha channel; it generates on a chroma key "
                "and sprite-gen mattes it out (use --alpha-mode chroma or another provider)"
            )

        prompt = _build_prompt(
            request.prompt, destination, refs, chroma_key=request.chroma_key
        )
        cmd = [provider_binary("agy"), "--output-format", "json"]
        for directory in add_dirs:
            cmd += ["--add-dir", directory]
        if request.model:
            cmd += ["--model", request.model]
        cmd += ["-p", prompt]

        started = time.monotonic()
        # A headless provider must not inherit parent orchestration identity or
        # lifecycle controls (see base.provider_subprocess_env).
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=provider_subprocess_env(),
                cwd=str(workdir),
                timeout=AGY_GEN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise GenTimeoutError(
                f"agy-gen: no completion within {AGY_GEN_TIMEOUT_SECONDS}s — child killed "
                "(gen-timeout). A normal turn is ~63s (2026-09-12 실측), so a run that never "
                "returns at all usually means agy is waiting on a tool-approval prompt — set "
                "Tool Permission to always-proceed in `agy` -> /config."
            ) from None
        elapsed = time.monotonic() - started
        stdout = completed.stdout or ""

        if completed.returncode != 0:
            tail = (completed.stderr or "").strip().splitlines()[-20:]
            detail = "\n".join(tail).strip() or (
                "\n".join(stdout.splitlines()[:20]) or "(no error detail on stdout/stderr)"
            )
            raise SystemExit(f"agy-gen: agy exited {completed.returncode}\n{detail}")

        envelope = _parse_envelope(stdout)
        status = envelope.get("status")
        response = str(envelope.get("response") or "")
        if status != "SUCCESS":
            raise SystemExit(
                f"agy-gen: agy reported status={status!r} (expected 'SUCCESS'); no image published.\n"
                f"  conversation_id: {envelope.get('conversation_id')}\n"
                f"  response: {response.strip()[:800] or '(empty)'}"
            )

        claimed = reported_paths(response)
        # The instructed destination wins when it was actually written; otherwise
        # the reported paths are the recovery route. Either way the choice is made
        # by what is on disk, never by what the agent said (No Silent Fallback).
        source: Path | None = destination if destination.is_file() else None
        recovered_from: str | None = None
        rejected: list[str] = []
        if source is None:
            # Recovery reads paths out of agent-written prose, so every candidate is
            # untrusted input and gets bounded before anything is copied. Two rules:
            #
            #   Containment — the resolved path must be inside `write_roots` (the
            #   output directory and the working directory: the only two places agy is
            #   told to write). Anything else is refused, whatever it is. Without this,
            #   an absolute path, a `~/...` token or a `../../` traversal in the prose
            #   could name any readable file on the machine and it would be copied in
            #   and published as the sprite — `verify_png` proves a file is a PNG, never
            #   that it is the right PNG. Because `_resolve_candidate` resolves first,
            #   a symlink inside the workspace pointing outside it is judged by its
            #   target and refused too.
            #
            #   Not an input — the reference images are deliberately NOT part of the
            #   boundary even though their directories are in `--add-dir`: those are
            #   read-only inputs, and republishing an identity ref as this run's
            #   generation is exactly the silent wrong-file outcome being prevented. A
            #   ref that happens to sit inside the workdir is skipped by name.
            ref_set = {ref.resolve() for ref in refs}
            for token in claimed:
                candidate = _resolve_candidate(token, workdir)
                if candidate is None:
                    continue
                if not _within(candidate, write_roots):
                    rejected.append(f"{token}  (outside the workspace: {candidate})")
                    continue
                if candidate in ref_set:
                    rejected.append(f"{token}  (that is a reference image, not the output)")
                    continue
                if candidate.is_file():
                    source, recovered_from = candidate, token
                    break
        if source is None:
            rendered = "\n".join(f"  - {token}" for token in claimed) or "  (none)"
            refusals = (
                "\n  refused before copying:\n" + "\n".join(f"  - {line}" for line in rejected)
                if rejected
                else ""
            )
            raise SystemExit(
                "agy-gen: agy reported SUCCESS but no usable generated image exists on disk — "
                "refusing to claim success.\n"
                f"  instructed destination (not written): {destination}\n"
                f"  writes are only accepted under: {', '.join(str(root) for root in write_roots)}\n"
                f"  paths parsed out of the response:\n{rendered}{refusals}\n"
                f"  response: {response.strip()[:800] or '(empty)'}"
            )
        if source != destination:
            shutil.copyfile(source, destination)

        # Truth is the decoded bytes, not the agent's report.
        verify_png(destination)

        usage = envelope.get("usage")
        return ProviderRun(
            provider=self.name,
            elapsed_seconds=elapsed,
            model=request.model,
            # agy's conversation id is the closest analogue to codex's rollout
            # session id — same role (resume/inspect the turn), so it rides the
            # dedicated field as well as `extra`.
            session_id=envelope.get("conversation_id"),
            extra={
                "transport": "agy-cli",
                "conversation_id": envelope.get("conversation_id"),
                "num_turns": envelope.get("num_turns"),
                "usage": usage if isinstance(usage, dict) else None,
                # agy's own wall clock for the turn, next to our measured `elapsed_seconds`.
                "agy_duration_seconds": envelope.get("duration_seconds"),
                "reported_paths": claimed,
                # Non-None only when the agent ignored the instructed destination and
                # we recovered the image from a path it reported — visible, not silent.
                "recovered_from": recovered_from,
                # Which backdrop this run asked for, next to the `chroma` stats the
                # orchestrator publishes for the matte that consumed it.
                "chroma_key_requested": request.chroma_key,
                "refs": [str(ref) for ref in refs],
            },
        )
