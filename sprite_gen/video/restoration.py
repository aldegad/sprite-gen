# SPDX-License-Identifier: Apache-2.0
"""Restore one damaged delivered cell from its verified source time, without RIFE.

A source pixel is copied only where it does not raise the key guard over the
origin's pixel. Where it would, and the excess is one step that the resample
made out of source pixels that have none, the source pixel is written with its
key hue capped to the bar; anywhere else the origin's pixel stays. The cell's
whole coverage must still be the source's. The origin is the five files the
first restoration started from: every request rebuilds the current baseline
from it and the source, so a receipt is recomputed, never trusted.

Source correspondence, proposal generation and final acceptance are separate.
A failed independent proposal never hides the next one; a missing shared source
bridge stops the operation. All quality measurements consume final pixels.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.frames.cutout import KEY_TARGETS
from sprite_gen.frames.decontam import _cap_key_hue
from sprite_gen.frames.extract import _key_channel_split, _key_excess_field, _SPILL_FULL_MIN_TINT
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util import resample
from sprite_gen.video import evidence, playback, rife, source
from sprite_gen.video.compare import KIND as COMPARISON_KIND, Loop
from sprite_gen.video.interpolation_quality import SMEAR_WARN, OUTLINE_WARN, faults

METRIC = "source-restoration-v3"
POLICY = "key-protected-source-copy-one-step-cap-v1"
SCOPE = "processing-defect-restoration"
OPERATION = "restore_active_cut"
KIND = "sprite-gen-video-loop-restoration"
REPAIR_SCHEMA = 3
COMPARISON_SCHEMA = 4
MAX_PROPOSALS = 3
ARTIFACTS = ("strip", "meta", "report", "gif", "webp")
LISTED = 256  # capped or protected pixels a report spells out; the masks always hold all of them

# The guard in whole numbers is the same formula only while the bar is a whole number.
_BAR = int(_SPILL_FULL_MIN_TINT)
assert _BAR == _SPILL_FULL_MIN_TINT
# The only excess that is capped: one step over the bar, the least a whole-number channel can
# change. It bounds the change; it is not a promise about how any deeper cap would look.
CAP_DEPTH = 1


def read_loop(paths: dict[str, Path]) -> tuple[Loop, dict[str, playback.Playback]]:
    loop = Loop.read(**{k: paths[k] for k in ("strip", "meta", "report")})
    animations = {k: playback.Playback.read(paths[k], k.upper()) for k in ("gif", "webp")}
    loop.artifacts.update({k: a.artifact for k, a in animations.items()})
    return loop, animations


def measure(frames: list[Image.Image], neighbours: list[Image.Image]) -> list[dict[str, float]]:
    return [rife.smear(f, neighbours[k - 1], neighbours[(k + 1) % len(frames)]) for k, f in enumerate(frames)]


def _key_hue(rgba: Any, key: str) -> Any:
    channels, others = _key_channel_split(KEY_TARGETS[key])
    return _key_excess_field(rgba[..., :3].astype(np.int64), channels, others)


def key_weight(rgba: Any, key: str) -> Any:
    """Alpha times the key hue's excess over the full-spill bar, per pixel, in whole numbers.

    255 times the alpha-weighted excess the comparison reports. Includes newly
    restored coverage: returning to a source pixel does not excuse its key spill.
    """
    return rgba[..., 3].astype(np.int64) * np.maximum(0, _key_hue(rgba, key) - _BAR)


def _source_key(src: source.Source) -> str | None:
    key = src.record["recipe"].get("key")
    if key == "auto":
        key = (src.record["recipe"].get("spill") or {}).get("key")
    return key if key in ("green", "magenta") else None


def source_key_weight(cropped: Image.Image, size: tuple[int, int], key: str) -> Any:
    """The most key weight among the source pixels each cell pixel's colour was mixed from.

    Zero where every visible source pixel under it is at or under the bar: an
    excess there was made by mixing them, not carried by any of them. The windows
    are the resample's own (`resample.mix_windows`).
    """
    weight = key_weight(np.asarray(cropped, dtype=np.uint8), key)
    return resample.window_extrema(weight, *resample.mix_windows(cropped.size, size))[1]


def made_excess(reference: Image.Image, cropped: Image.Image, key: str) -> Any:
    """Where the source's cell is exactly `CAP_DEPTH` over the bar and no source pixel under it is over."""
    s = np.asarray(reference, dtype=np.uint8)
    return ((s[..., 3] > 0) & (_key_hue(s, key) == _BAR + CAP_DEPTH)
            & (source_key_weight(cropped, reference.size, key) == 0))


def _capped(rgba: Any, key: str) -> Any:
    """The engine's key-hue cap to the bar on whole pixels: the keyed channels come down, nothing else moves."""
    channels, others = _key_channel_split(KEY_TARGETS[key])
    out = rgba.copy()
    out[..., :3] = _cap_key_hue(rgba[..., :3], channels, others, _BAR).astype(np.uint8)
    return out


@dataclass
class Partial:
    frame: Image.Image  # per place: the source's pixel, the source's with its key hue capped, or the origin's
    copied: Any
    capped: Any
    protected: Any
    alpha_conflict: bool  # a protected pixel whose coverage is not the source's


def partial(origin: Image.Image, reference: Image.Image, cropped: Image.Image, key: str) -> Partial:
    """Where the two differ, the source's pixel unless it would raise the key guard there.

    A place where it would is capped only when its excess is a made one of one
    step (`made_excess`): the source's pixel with the keyed channels one lower,
    its coverage unchanged. Any other such place keeps the origin's whole RGBA.
    No colour is moved or taken from another place, and a hidden colour never
    gains coverage.
    """
    b, s = (np.asarray(f, dtype=np.uint8) for f in (origin, reference))
    differs = (b != s).any(axis=-1)
    raised = differs & (key_weight(s, key) > key_weight(b, key))
    capped = raised & made_excess(reference, cropped, key)
    protected = raised & ~capped
    copied = differs & ~raised
    out = np.where(copied[..., None], s, b)
    out[capped] = _capped(s[capped], key)
    return Partial(Image.fromarray(out), copied, capped, protected, bool((b[..., 3] != s[..., 3])[protected].any()))


def _mask(mask: Any) -> dict[str, Any]:
    """A mask as row-major run lengths, the first run clear, and the digest of its packed bits."""
    flat = np.asarray(mask, dtype=bool).ravel()
    bounds = np.concatenate(([0], np.flatnonzero(flat[1:] != flat[:-1]) + 1, [flat.size]))
    runs = np.diff(bounds).tolist()
    h, w = mask.shape
    return {"size": [w, h], "count": int(flat.sum()), "rle": [0, *runs] if flat[0] else runs,
            "sha256": evidence.digest(f"{w}x{h}:".encode() + np.packbits(flat).tobytes())}


def _describe(part: Partial, origin: Image.Image, reference: Image.Image, cropped: Image.Image,
              key: str) -> dict[str, Any]:
    """The three masks, and the capped and the protected pixels spelled out with what decided each."""
    b, s, f = (np.asarray(im, dtype=np.uint8) for im in (origin, reference, part.frame))
    kb, ks, hue = key_weight(b, key), key_weight(s, key), _key_hue(s, key)
    under = source_key_weight(cropped, reference.size, key)
    (top, bottom), (left, right) = resample.mix_windows(cropped.size, reference.size)
    capped, protected = np.argwhere(part.capped), np.argwhere(part.protected)
    return {"copied": _mask(part.copied), "capped": _mask(part.capped), "protected": _mask(part.protected),
            "alpha_equals_source": not part.alpha_conflict,
            "capped_pixels": {"count": len(capped), "listed": min(len(capped), LISTED), "pixels": [
                {"x": int(x), "y": int(y), "origin_rgba": b[y, x].tolist(), "source_rgba": s[y, x].tolist(),
                 "output_rgba": f[y, x].tolist(),
                 "source_window": [int(left[x]), int(top[y]), int(right[x]), int(bottom[y])]}
                for y, x in capped[:LISTED]]},
            "protected_pixels": {"count": len(protected),
                                 "alpha_conflicts": int((b[..., 3] != s[..., 3])[part.protected].sum()),
                                 "listed": min(len(protected), LISTED), "pixels": [
                {"x": int(x), "y": int(y), "origin_rgba": b[y, x].tolist(), "source_rgba": s[y, x].tolist(),
                 "origin_key_weight": int(kb[y, x]), "source_key_weight": int(ks[y, x]),
                 "source_key_excess": int(hue[y, x]), "source_window_key_weight": int(under[y, x]),
                 "alpha_equal": bool(b[y, x, 3] == s[y, x, 3])} for y, x in protected[:LISTED]]}}


def proposals(frames: list[Image.Image], projection: source.Projection, shown: list[int],
              applied: list[int]) -> list[int]:
    ref = projection.frames
    actual, fixed, original = measure(frames, frames), measure(frames, ref), measure(ref, ref)
    boundary = {0, len(ref) - 1, shown[0], shown[-1]}
    eligible = [k for k in projection.record["repair_hints"] if k in shown and k not in boundary and k not in applied
                and frames[k].tobytes() != ref[k].tobytes()
                and faults(actual[k]) and faults(fixed[k]) and not faults(original[k])]
    return sorted(eligible, key=lambda k: (-max(fixed[k]["outline_loss"], fixed[k]["dark_excess"]), k))[:MAX_PROPOSALS]


def _portable(value: Any) -> Any:
    """A record without the identity of the engine that wrote it; a later engine recomputes the rest."""
    if isinstance(value, dict):
        return {k: _portable(v) for k, v in value.items() if k != "measurement_engine"}
    return [_portable(v) for v in value] if isinstance(value, list) else value


def proposal_id(current: dict[str, Any], origin: dict[str, Any], src: source.Source,
                projection: source.Projection, target: int, part: Partial) -> str:
    identity = {"current": current, "origin": origin,
                "source": {"artifacts": src.artifacts, "sequence": src.record["source"]},
                "projection": _portable(projection.record), "operation": OPERATION, "metric": METRIC,
                "policy": POLICY, "target": target,
                "mask": {k: _mask(m)["sha256"] for k, m in (("copied", part.copied), ("capped", part.capped),
                                                            ("protected", part.protected))}}
    return evidence.digest(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())


def _entry(target: int, part: Partial, origin: Loop, current: dict[str, Any], before: list[Image.Image],
           src: source.Source, projection: source.Projection) -> dict[str, Any]:
    """One applied cell. `current` is the five-file receipt of the baseline it was proposed on."""
    return {"target": target, "source_index": projection.record["samples"][target],
            "proposal_id": proposal_id(current, origin.artifacts, src, projection, target, part),
            "baseline_artifacts": current,
            "baseline_pixels_sha256": evidence.digest(b"".join(f.tobytes() for f in before)),
            "copied": _mask(part.copied), "capped": _mask(part.capped), "protected": _mask(part.protected),
            "origin_rgba_sha256": evidence.digest(origin.frames[target].tobytes()),
            "source_rgba_sha256": evidence.digest(projection.frames[target].tobytes()),
            "output_rgba_sha256": evidence.digest(part.frame.tobytes()),
            "alpha_equals_source": not part.alpha_conflict, "measurement_engine": evidence.engine_identity()}


def _receipt(origin: Loop, src: source.Source, projection: source.Projection,
             applied: list[dict[str, Any]]) -> dict[str, Any]:
    return {"operation": OPERATION, "metric_version": METRIC, "policy_version": POLICY,
            "origin_artifacts": origin.artifacts, "source_artifacts": src.artifacts,
            "source": src.record["source"], "projection": projection.record, "applied": applied,
            "upstream_quality": "historical; final pixels require source-restoration comparison"}


def judge(before: list[Image.Image], after: list[Image.Image], ref: list[Image.Image], key: str) -> dict[str, Any]:
    """The protections over final pixels, from one loop's cells to the next's."""
    changed = [k for k, (a, b) in enumerate(zip(before, after)) if a.tobytes() != b.tobytes()]
    a_final, b_final, reference = measure(before, before), measure(after, after), measure(ref, ref)
    a_fixed, b_fixed = measure(before, ref), measure(after, ref)
    axes: dict[str, Any] = {}
    for metric, threshold in (("outline_loss", OUTLINE_WARN), ("dark_excess", SMEAR_WARN)):
        a, b = ([max(0, q[metric] - threshold) for q in row] for row in (a_final, b_final))
        axes[metric] = {"baseline": [q[metric] for q in a_final], "candidate": [q[metric] for q in b_final],
                        "reference": [q[metric] for q in reference], "threshold": threshold,
                        "status": "regressed" if any(y > x + 1e-9 for x, y in zip(a, b)) else
                                  "improved" if any(y < x - 1e-9 for x, y in zip(a, b)) else "non_regressing"}
    a_partial, b_partial = ([max(0, q["partial_excess"] - r["partial_excess"]) for q, r in zip(row, reference)]
                            for row in (a_fixed, b_fixed))
    axes["introduced_partial"] = {"baseline": a_partial, "candidate": b_partial,
                                  "raw_baseline": [q["partial_excess"] for q in a_fixed],
                                  "raw_candidate": [q["partial_excess"] for q in b_fixed],
                                  "raw_reference": [q["partial_excess"] for q in reference],
                                  "status": "regressed" if any(b > a + 1e-9 for a, b in zip(a_partial, b_partial)) else "non_regressing"}
    a_fields, b_fields = ([key_weight(np.asarray(f, dtype=np.uint8), key) for f in row] for row in (before, after))
    raised = [np.maximum(0, b - a) for a, b in zip(a_fields, b_fields)]
    axes["key_colour"] = {"baseline": [int(f.sum()) / 255 for f in a_fields],
                          "candidate": [int(f.sum()) / 255 for f in b_fields], "key": key,
                          "introduced_excess": [int(r.sum()) / 255 for r in raised],
                          "introduced_pixels": [int((r > 0).sum()) for r in raised], "tint_threshold": _BAR,
                          "status": "regressed" if any(r.any() for r in raised) else "non_regressing"}
    cleared = [{"cell": k, "faults": sorted(set(faults(a_final[k])) - set(faults(b_final[k])))} for k in changed]
    regressed = [k + ":regressed" for k, v in axes.items() if v["status"] == "regressed"]
    if regressed:
        verdict, reasons = "regressed", regressed
    elif not changed:
        verdict, reasons = "non_regressing", ["identical-pixels-and-timing"]
    elif any(faults(b_final[k]) or faults(b_fixed[k]) for k in changed) or not all(c["faults"] for c in cleared):
        verdict, reasons = "unknown", ["interpolation-fault-not-cleared"]
    else:
        verdict, reasons = "improved", ["verified-source-defect-restored"]
    return {"verdict": verdict, "reasons": reasons, "axes": axes, "cleared_faults": cleared}


@dataclass
class Chain:
    projection: source.Projection
    key: str
    shown: list[int]
    applied: list[dict[str, Any]]

    def targets(self, frames: list[Image.Image]) -> list[int]:
        return proposals(frames, self.projection, self.shown, [e["target"] for e in self.applied])


def chain(origin: Loop, current: Loop, src: source.Source) -> tuple[Chain | None, str | None]:
    """Rebuild the current baseline from the origin and the source.

    The origin's own normal cells prove the projection. Each applied cell is made
    again from the origin and the source, in order, as a proposal of its prefix
    that passes the protections there; the pixels read from the current baseline
    must then be the rebuilt ones. A receipt the inputs do not reproduce is an error.
    """
    if "restoration" in origin.source_report:
        raise ValueError("origin carries a restoration receipt: supply the five files the first restoration started from")
    receipt = current.source_report.get("restoration")
    if receipt is None and current.artifacts != origin.artifacts:
        raise ValueError("current baseline differs from the origin and carries no restoration receipt")
    if receipt is not None:
        if not isinstance(receipt, dict):
            raise ValueError("malformed restoration receipt")
        if (receipt.get("metric_version"), receipt.get("policy_version")) != (METRIC, POLICY):
            return None, "restoration-receipt-policy-unsupported"
        if receipt.get("origin_artifacts") != origin.artifacts or receipt.get("source_artifacts") != src.artifacts:
            raise ValueError("restoration receipt names another origin or source")
    projection, issue = source.project(origin, src)
    if issue:
        return None, issue
    assert projection is not None
    key = _source_key(src)
    if key is None:
        return None, "source-key-colour-unverified"
    schedule = playback.mapping(origin)
    if schedule is None:
        return None, "playback-schedule-unverified"
    state = Chain(projection, key, schedule[0], [])
    if receipt is None:
        return state, None
    applied = receipt.get("applied")
    if not isinstance(applied, list) or not applied or any(not isinstance(e, dict) for e in applied):
        raise ValueError("restoration receipt lists no applied cell")
    if (current.artifacts["meta"] != origin.artifacts["meta"]
            or {k: v for k, v in current.source_report.items() if k != "restoration"} != origin.source_report):
        raise ValueError("current baseline metadata or report differs from the origin outside its restoration receipt")
    frames = list(origin.frames)
    for step, entry in enumerate(applied):
        target = entry.get("target")
        if type(target) is not int or target not in state.targets(frames):
            raise ValueError("an applied restoration was not a proposal of its prefix")
        previous = entry.get("baseline_artifacts")
        if not isinstance(previous, dict) or (step == 0 and previous != origin.artifacts):
            raise ValueError("an applied restoration names another baseline than its prefix")
        part = partial(origin.frames[target], projection.frames[target], projection.cropped[target], key)
        after = [part.frame if k == target else f for k, f in enumerate(frames)]
        if (part.alpha_conflict or _portable(entry) != _portable(_entry(target, part, origin, previous, frames, src, projection))
                or judge(frames, after, projection.frames, key)["verdict"] != "improved"):
            raise ValueError("an applied restoration is not reproduced by the origin, the source and the protections")
        state.applied.append(entry)
        frames = after
    if _portable(receipt) != _portable(_receipt(origin, src, projection, state.applied)):
        raise ValueError("restoration receipt differs from the one the origin and the source reproduce")
    if any(a.tobytes() != b.tobytes() for a, b in zip(frames, current.frames)):
        raise ValueError("current baseline pixels differ from the origin rebuilt with its restoration receipt")
    return state, None


def _base(origin: Loop, baseline: Loop, candidate: Loop, src: source.Source) -> dict[str, Any]:
    return {"kind": COMPARISON_KIND, "schema_version": COMPARISON_SCHEMA, "metric_version": METRIC,
            "policy_version": POLICY, "scope": SCOPE, "operation": OPERATION,
            "measurement_engine": evidence.engine_identity(), "origin": origin.describe(),
            "baseline": baseline.describe(), "candidate": candidate.describe(),
            "source": {"artifacts": src.artifacts, "sequence": src.record["source"]},
            "axes": {}, "gait": {"absolute": "unverified", "source_order": "unverified"},
            "limits": ["same-time processing damage only; no absolute gait or drawing-quality certification",
                       "a restored cell is, at each place, the source's pixel, the source's pixel with its key hue "
                       "capped one step to the bar, or the origin's; never the whole source drawing",
                       "one step is the least a channel can change: a bound on the cap, not a verdict on how it looks",
                       "source and origin receipts bind supplied bytes; they are not signatures of the original producer"]}


def compare(origin: Loop, baseline: Loop, candidate: Loop, src: source.Source, *,
            baseline_playback: dict[str, playback.Playback], candidate_playback: dict[str, playback.Playback],
            repair_evidence: dict[str, Any]) -> dict[str, Any]:
    result = _base(origin, baseline, candidate, src)
    def finish(verdict: str, reasons: list[str], common: bool = False) -> dict[str, Any]:
        return {**result, "verdict": verdict, "reasons": reasons, "common_failure": common}
    if ((repair_evidence.get("kind"), repair_evidence.get("schema_version"), repair_evidence.get("metric_version"),
         repair_evidence.get("policy_version")) != (KIND, REPAIR_SCHEMA, METRIC, POLICY)
            or repair_evidence.get("origin_artifacts") != origin.artifacts
            or repair_evidence.get("baseline_artifacts") != baseline.artifacts
            or repair_evidence.get("source_artifacts") != src.artifacts
            or repair_evidence.get("candidate_artifacts") != candidate.artifacts):
        raise ValueError("repair evidence does not bind these exact input artifacts")
    state, issue = chain(origin, baseline, src)
    if issue:
        return finish("unknown", [issue], True)
    assert state is not None
    projection, ref = state.projection, state.projection.frames
    # The verifier may be a newer implementation. Require the same measured
    # projection and reference bytes, not equality of the old measuring engine's
    # identity; both engine receipts remain available for the caller to audit.
    if _portable(repair_evidence.get("projection")) != _portable(projection.record):
        raise ValueError("repair projection evidence differs from actual source projection")
    result["provenance"] = {"applied": state.applied}
    a_play, issue = playback.verify(baseline, baseline_playback)
    if issue:
        return finish("unknown", ["baseline:" + issue], True)
    b_play, issue = playback.verify(candidate, candidate_playback)
    if issue:
        return finish("unknown", ["candidate:" + issue])
    result["playback"] = {"baseline": a_play, "candidate": b_play}
    # The writer copies the metadata byte for byte: no cut, crop, schedule, follow or rendering change.
    if candidate.artifacts["meta"] != baseline.artifacts["meta"]:
        return finish("unknown", ["candidate-metadata-bytes-changed"])
    if any(a_play[k]["durations_ms"] != b_play[k]["durations_ms"] or
           a_play[k]["strip_indices"] != b_play[k]["strip_indices"] for k in a_play):
        return finish("unknown", ["playback-schedule-changed"])
    changed = [k for k, (a, b) in enumerate(zip(baseline.frames, candidate.frames)) if a.tobytes() != b.tobytes()]
    result["preservation"] = {"changed_cells": changed, "unchanged_cells": [k for k in range(len(ref)) if k not in changed],
                              "samples": projection.record["samples"], "pts_seconds": projection.record["pts_seconds"],
                              "crop": projection.record["crop"], "boundaries_exact": not ({0, len(ref) - 1} & set(changed))}
    if not changed and candidate.artifacts != baseline.artifacts:
        return finish("unknown", ["unchanged-pixels-with-changed-artifacts"])
    if changed:
        if len(changed) != 1 or changed[0] not in state.targets(baseline.frames):
            return finish("unknown", ["changed-cells-outside-verified-proposal"])
        target = changed[0]
        part = partial(origin.frames[target], ref[target], projection.cropped[target], state.key)
        entry = _entry(target, part, origin, baseline.artifacts, baseline.frames, src, projection)
        if repair_evidence.get("proposal_id") != entry["proposal_id"] or repair_evidence.get("target") != target:
            raise ValueError("repair proposal identity differs from actual changed cells")
        result["restoration"] = {"target": target, "proposal_id": entry["proposal_id"], **_describe(
            part, origin.frames[target], ref[target], projection.cropped[target], state.key)}
        if part.alpha_conflict:
            return finish("unknown", ["protected-pixel-alpha-conflict"])
        if candidate.frames[target].tobytes() != part.frame.tobytes():
            return finish("regressed", ["changed-cell-is-not-the-verified-source-copy"])
        receipt = _receipt(origin, src, projection, [*state.applied, entry])
        if _portable(candidate.source_report) != _portable({**baseline.source_report, "restoration": receipt}):
            return finish("unknown", ["candidate-report-differs-from-verified-receipt"])
    measured = judge(baseline.frames, candidate.frames, ref, state.key)
    result["axes"] = {**measured["axes"], "seam": {
        "status": "non_regressing", "reason": "strip-and-playback-boundary-cells-and-durations-exact"}}
    result["gait"]["source_order"] = "preserved"
    result["cleared_faults"] = measured["cleared_faults"]
    return finish(measured["verdict"], measured["reasons"])


def _write_candidate(paths: dict[str, Path], out: Path, name: str, baseline: Loop, target: int | None,
                     frame: Image.Image | None, receipt: dict[str, Any] | None) -> dict[str, Path]:
    from sprite_gen.video.loop import write_webp
    from sprite_gen.util.gif_utils import save_clean_gif
    if out.exists():
        raise ValueError("candidate directory must be new (never overwrite or repeat a proposal)")
    if not name or Path(name).name != name or name in (".", ".."):
        raise ValueError("name must be a filename stem")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".restoration-", dir=out.parent))
    names = {k: name + suffix for k, suffix in (("strip", ".strip.png"), ("meta", ".strip.json"),
                                               ("report", ".loop.json"), ("gif", ".gif"), ("webp", ".webp"))}
    try:
        if target is None:
            for key, filename in names.items():
                shutil.copyfile(paths[key], stage / filename)
        else:
            frames = list(baseline.frames)
            frames[target] = frame
            w, h = frames[0].size
            strip = Image.new("RGBA", (w * len(frames), h))
            for k, cell in enumerate(frames):
                strip.paste(cell, (k * w, 0))
            strip.save(stage / names["strip"])
            shutil.copyfile(paths["meta"], stage / names["meta"])
            # The loop report stays the origin's own record; only the receipt is added.
            report = {**baseline.source_report, "restoration": receipt}
            (stage / names["report"]).write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
            indices, delay = playback.mapping(baseline)
            selected = [frames[k] for k in indices]
            save_clean_gif(selected, stage / names["gif"], duration_ms=delay, alpha_threshold=128)
            write_webp(selected, stage / names["webp"], delay_ms=delay, workdir=stage / ".webp")
            shutil.rmtree(stage / ".webp")
        stage.rename(out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {k: out / filename for k, filename in names.items()}


def restore(paths: dict[str, Path], origin_paths: dict[str, Path], src: source.Source, *,
            out_dir: Path, name: str, proposal_index: int = 1) -> dict[str, Any]:
    if type(proposal_index) is not int or not 1 <= proposal_index <= MAX_PROPOSALS:
        raise ValueError("proposal-index must be between 1 and 3")
    baseline, animations = read_loop(paths)
    origin, _ = read_loop(origin_paths)
    result = {"kind": KIND, "schema_version": REPAIR_SCHEMA, "metric_version": METRIC, "policy_version": POLICY,
              "scope": SCOPE, "operation": OPERATION, "measurement_engine": evidence.engine_identity(),
              "origin_artifacts": origin.artifacts, "baseline_artifacts": baseline.artifacts,
              "source_artifacts": src.artifacts, "proposal_index": proposal_index,
              "proposal_limit": MAX_PROPOSALS, "interpolation_calls": 0, "outputs": {}}
    state, issue = chain(origin, baseline, src)
    if not issue:
        _, issue = playback.verify(baseline, animations)
    if issue:
        return {**result, "status": "unknown", "common_failure": True, "reasons": [issue]}
    assert state is not None
    projection = state.projection
    targets = state.targets(baseline.frames)
    result.update(projection=projection.record, proposals_available=len(targets),
                  applied_cells=[e["target"] for e in state.applied])
    if proposal_index > max(1, len(targets)):
        return {**result, "status": "exhausted", "common_failure": False, "reasons": ["proposal-budget-exhausted"]}
    target = targets[proposal_index - 1] if targets else None
    result.update(target=target, proposal_id=None, common_failure=False, reasons=[], status="no_change")
    frame = receipt = None
    if target is not None:
        part = partial(origin.frames[target], projection.frames[target], projection.cropped[target], state.key)
        entry = _entry(target, part, origin, baseline.artifacts, baseline.frames, src, projection)
        result.update(proposal_id=entry["proposal_id"], status="candidate", partial=_describe(
            part, origin.frames[target], projection.frames[target], projection.cropped[target], state.key))
        if part.alpha_conflict or not (part.copied | part.capped).any():
            return {**result, "status": "unknown", "reasons": [
                "protected-pixel-alpha-conflict" if part.alpha_conflict else "no-unprotected-source-pixel"]}
        frame, receipt = part.frame, _receipt(origin, src, projection, [*state.applied, entry])
    outputs = _write_candidate(paths, out_dir, name, baseline, target, frame, receipt)
    candidate, candidate_animations = read_loop(outputs)
    result.update(outputs={k: str(p) for k, p in outputs.items()}, candidate_artifacts=candidate.artifacts)
    # This is a preview, not authority for later bytes. The compare verb rereads and
    # measures every final input (including a caller's subsequent modifications).
    result["comparison"] = compare(origin, baseline, candidate, src, baseline_playback=animations,
                                   candidate_playback=candidate_animations, repair_evidence=result)
    return result


def add_arguments(parser: argparse.ArgumentParser) -> None:
    for side in ("baseline", "origin"):
        for key in ARTIFACTS:
            parser.add_argument(f"--{side}-{key}", type=Path, required=True)
    source.add_inputs(parser)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--name", default="loop")
    parser.add_argument("--proposal-index", type=int, choices=range(1, MAX_PROPOSALS + 1), default=1)
    parser.add_argument("--report", type=Path, required=True)


def run(**kwargs: Any) -> int:
    try:
        inputs = source.input_paths(kwargs)
        paths, origin = ({k: Path(kwargs[f"{side}_{k}"]) for k in ARTIFACTS} for side in ("baseline", "origin"))
        output = Path(kwargs["report"])
        out = Path(kwargs["out_dir"])
        name = kwargs.get("name", "loop")
        protected = [*paths.values(), *origin.values(), *inputs.values(), *inputs["source_frames_dir"].glob("*.png")]
        protected += [out / (name + ext) for ext in (".strip.png", ".strip.json", ".loop.json", ".gif", ".webp")]
        if output.resolve() in {p.resolve() for p in protected}:
            raise ValueError("repair report cannot overwrite an input")
        payload = restore(paths, origin, source.Source.read(**inputs), out_dir=out,
                          name=name, proposal_index=kwargs.get("proposal_index", 1))
        output.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=2, allow_nan=False) + "\n"
        atomic_write_text(output, text)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise SystemExit(f"video-loop-repair: {exc}") from exc
    print(text, end="")
    return 0
