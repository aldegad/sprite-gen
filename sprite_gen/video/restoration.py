# SPDX-License-Identifier: Apache-2.0
"""Restore one damaged delivered cell from its verified source time, without RIFE.

Source correspondence, proposal generation and final acceptance are separate.
A failed independent proposal never hides the next one; a missing shared source
bridge stops the operation. All quality measurements consume final pixels.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.frames.cutout import KEY_TARGETS
from sprite_gen.frames.extract import _key_channel_split, _key_excess_field, _SPILL_FULL_MIN_TINT
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.video import evidence, playback, rife, source
from sprite_gen.video.compare import KIND as COMPARISON_KIND, Loop
from sprite_gen.video.interpolation_quality import SMEAR_WARN, OUTLINE_WARN, faults

METRIC = "source-restoration-v1"
SCOPE = "processing-defect-restoration"
OPERATION = "restore_active_cut"
KIND = "sprite-gen-video-loop-restoration"
MAX_PROPOSALS = 3
ARTIFACTS = ("strip", "meta", "report", "gif", "webp")


def read_loop(paths: dict[str, Path]) -> tuple[Loop, dict[str, playback.Playback]]:
    loop = Loop.read(**{k: paths[k] for k in ("strip", "meta", "report")})
    animations = {k: playback.Playback.read(paths[k], k.upper()) for k in ("gif", "webp")}
    loop.artifacts.update({k: a.artifact for k, a in animations.items()})
    return loop, animations


def measure(frames: list[Image.Image], neighbours: list[Image.Image]) -> list[dict[str, float]]:
    return [rife.smear(f, neighbours[k - 1], neighbours[(k + 1) % len(frames)]) for k, f in enumerate(frames)]


def _key_field(frame: Image.Image, key: str) -> Any:
    # Alpha-weighted excess of the key channels. Includes newly restored coverage:
    # returning to a source pixel does not excuse reintroducing its key spill.
    a = np.asarray(frame, dtype=np.float64)
    channels, others = _key_channel_split(KEY_TARGETS[key])
    tint = _key_excess_field(a[..., :3], channels, others)
    return np.maximum(0, tint - _SPILL_FULL_MIN_TINT) * a[..., 3] / 255


def _source_key(src: source.Source) -> str | None:
    key = src.record["recipe"].get("key")
    if key == "auto":
        key = (src.record["recipe"].get("spill") or {}).get("key")
    return key if key in ("green", "magenta") else None


def proposals(baseline: Loop, projection: source.Projection) -> list[int]:
    ref = projection.frames
    actual, fixed, original = measure(baseline.frames, baseline.frames), measure(baseline.frames, ref), measure(ref, ref)
    schedule = playback.mapping(baseline)
    if schedule is None:
        return []
    shown = schedule[0]
    boundary = {0, len(ref) - 1, shown[0], shown[-1]}
    eligible = [k for k in projection.record["repair_hints"] if k in shown and k not in boundary
                and baseline.frames[k].tobytes() != ref[k].tobytes()
                and faults(actual[k]) and faults(fixed[k]) and not faults(original[k])]
    return sorted(eligible, key=lambda k: (-max(fixed[k]["outline_loss"], fixed[k]["dark_excess"]), k))[:MAX_PROPOSALS]


def proposal_id(baseline: Loop, src: source.Source, target: int) -> str:
    identity = {"baseline": baseline.artifacts, "source": src.record["source"],
                "operation": OPERATION, "metric": METRIC, "target": target}
    return evidence.digest(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())


def _base(baseline: Loop, candidate: Loop, src: source.Source) -> dict[str, Any]:
    return {"kind": COMPARISON_KIND, "schema_version": 2, "metric_version": METRIC, "scope": SCOPE,
            "operation": OPERATION, "measurement_engine": evidence.engine_identity(),
            "baseline": baseline.describe(), "candidate": candidate.describe(),
            "source": {"artifacts": src.artifacts, "sequence": src.record["source"]},
            "axes": {}, "gait": {"absolute": "unverified", "source_order": "unverified"},
            "limits": ["same-time processing damage only; no absolute gait or drawing-quality certification",
                       "source receipts bind supplied bytes; they are not signatures of the original producer"]}


def compare(baseline: Loop, candidate: Loop, src: source.Source, *,
            baseline_playback: dict[str, playback.Playback], candidate_playback: dict[str, playback.Playback],
            repair_evidence: dict[str, Any]) -> dict[str, Any]:
    result = _base(baseline, candidate, src)
    def finish(verdict: str, reasons: list[str], common: bool = False) -> dict[str, Any]:
        return {**result, "verdict": verdict, "reasons": reasons, "common_failure": common}
    if (repair_evidence.get("kind") != KIND or repair_evidence.get("metric_version") != METRIC
            or repair_evidence.get("baseline_artifacts") != baseline.artifacts
            or repair_evidence.get("source_artifacts") != src.artifacts
            or repair_evidence.get("candidate_artifacts") != candidate.artifacts):
        raise ValueError("repair evidence does not bind these exact input artifacts")
    projection, issue = source.project(baseline, src)
    if issue:
        return finish("unknown", [issue], True)
    assert projection is not None
    # The verifier may be a newer implementation. Require the same measured
    # projection and reference bytes, not equality of the old measuring engine's
    # identity; both engine receipts remain available for the caller to audit.
    previous_projection = repair_evidence.get("projection")
    if not isinstance(previous_projection, dict) or {
            k: v for k, v in previous_projection.items() if k != "measurement_engine"
    } != {k: v for k, v in projection.record.items() if k != "measurement_engine"}:
        raise ValueError("repair projection evidence differs from actual source projection")
    a_play, issue = playback.verify(baseline, baseline_playback)
    if issue:
        return finish("unknown", ["baseline:" + issue], True)
    b_play, issue = playback.verify(candidate, candidate_playback)
    if issue:
        return finish("unknown", ["candidate:" + issue])
    result["playback"] = {"baseline": a_play, "candidate": b_play}
    # Metadata is copied byte-for-byte by the writer. Accept whitespace differences,
    # but no other cut, crop, schedule, follow or rendering change.
    if candidate.meta != baseline.meta or candidate.source_report.get("cycle") != baseline.source_report.get("cycle"):
        return finish("unknown", ["candidate-schedule-or-geometry-changed"])
    if any(a_play[k]["durations_ms"] != b_play[k]["durations_ms"] or
           a_play[k]["strip_indices"] != b_play[k]["strip_indices"] for k in a_play):
        return finish("unknown", ["playback-schedule-changed"])
    candidate_projection, issue = source.project(candidate, src)
    if issue:
        return finish("unknown", ["candidate:" + issue])
    if candidate_projection is None or candidate_projection.record["crop"] != projection.record["crop"]:
        return finish("unknown", ["candidate-projection-changed"])
    ref = projection.frames
    changed = [k for k, (a, b) in enumerate(zip(baseline.frames, candidate.frames)) if a.tobytes() != b.tobytes()]
    result["preservation"] = {"changed_cells": changed, "unchanged_cells": [k for k in range(len(ref)) if k not in changed],
                              "samples": projection.record["samples"], "pts_seconds": projection.record["pts_seconds"],
                              "crop": projection.record["crop"], "boundaries_exact": not ({0, len(ref) - 1} & set(changed))}
    allowed = proposals(baseline, projection)
    if changed:
        if len(changed) != 1 or changed[0] not in allowed:
            return finish("unknown", ["changed-cells-outside-verified-proposal"])
        target = changed[0]
        if (repair_evidence.get("proposal_id") != proposal_id(baseline, src, target)
                or repair_evidence.get("target") != target):
            raise ValueError("repair proposal identity differs from actual changed cells")
        if candidate.frames[target].tobytes() != ref[target].tobytes():
            return finish("regressed", ["changed-cell-is-not-exact-source-time"])
    key = _source_key(src)
    if key is None:
        return finish("unknown", ["source-key-colour-unverified"], True)
    a_final, b_final, reference = measure(baseline.frames, baseline.frames), measure(candidate.frames, candidate.frames), measure(ref, ref)
    a_fixed, b_fixed = measure(baseline.frames, ref), measure(candidate.frames, ref)
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
    a_fields, b_fields = ([_key_field(f, key) for f in row] for row in (baseline.frames, candidate.frames))
    a_key, b_key = ([float(f.sum()) for f in fields] for fields in (a_fields, b_fields))
    introduced_key = [float(np.maximum(0, b - a).sum()) for a, b in zip(a_fields, b_fields)]
    axes["key_colour"] = {"baseline": a_key, "candidate": b_key, "key": key,
                          "introduced_excess": introduced_key, "tint_threshold": _SPILL_FULL_MIN_TINT,
                          "status": "regressed" if any(v > 1e-9 for v in introduced_key) else "non_regressing"}
    axes["seam"] = {"status": "non_regressing", "reason": "strip-and-playback-boundary-cells-and-durations-exact"}
    result["axes"] = axes
    result["gait"]["source_order"] = "preserved"
    cleared = [{"cell": k, "faults": sorted(set(faults(a_final[k])) - set(faults(b_final[k])))} for k in changed]
    result["cleared_faults"] = cleared
    regressed = [k + ":regressed" for k, v in axes.items() if v["status"] == "regressed"]
    if regressed:
        return finish("regressed", regressed)
    if not changed:
        return finish("non_regressing", ["identical-pixels-and-timing"])
    if any(faults(b_final[k]) or faults(b_fixed[k]) for k in changed) or not all(c["faults"] for c in cleared):
        return finish("unknown", ["interpolation-fault-not-cleared"])
    return finish("improved", ["verified-source-defect-restored"])


def _write_candidate(paths: dict[str, Path], out: Path, name: str, baseline: Loop, src: source.Source,
                     projection: source.Projection, target: int | None, identity: str | None) -> dict[str, Path]:
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
            frames[target] = projection.frames[target]
            w, h = frames[0].size
            strip = Image.new("RGBA", (w * len(frames), h))
            for k, frame in enumerate(frames):
                strip.paste(frame, (k * w, 0))
            strip.save(stage / names["strip"])
            shutil.copyfile(paths["meta"], stage / names["meta"])
            report = copy.deepcopy(baseline.source_report)
            report.update(producer=evidence.engine_identity(), source=src.record["source"])
            report["restoration"] = {"operation": OPERATION, "metric_version": METRIC, "target": target,
                                     "restored_cells": sorted(set(source.restored_cells(baseline.source_report)) | {target}),
                                     "proposal_id": identity, "baseline_artifacts": baseline.artifacts,
                                     "projection": projection.record,
                                     "upstream_quality": "historical; final pixels require source-restoration comparison"}
            for fmt in ("gif", "webp"):
                if isinstance(report.get(fmt), dict):
                    report[fmt]["file"] = names[fmt]
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


def restore(paths: dict[str, Path], src: source.Source, *, out_dir: Path, name: str, proposal_index: int = 1) -> dict[str, Any]:
    if type(proposal_index) is not int or not 1 <= proposal_index <= MAX_PROPOSALS:
        raise ValueError("proposal-index must be between 1 and 3")
    baseline, animations = read_loop(paths)
    result = {"kind": KIND, "schema_version": 1, "metric_version": METRIC, "scope": SCOPE, "operation": OPERATION,
              "measurement_engine": evidence.engine_identity(), "baseline_artifacts": baseline.artifacts,
              "source_artifacts": src.artifacts, "proposal_index": proposal_index,
              "proposal_limit": MAX_PROPOSALS, "interpolation_calls": 0, "outputs": {}}
    projection, issue = source.project(baseline, src)
    if not issue:
        _, issue = playback.verify(baseline, animations)
    if not issue and _source_key(src) is None:
        issue = "source-key-colour-unverified"
    if issue:
        return {**result, "status": "unknown", "common_failure": True, "reasons": [issue]}
    assert projection is not None
    targets = proposals(baseline, projection)
    result.update(projection=projection.record, proposals_available=len(targets))
    if proposal_index > max(1, len(targets)):
        return {**result, "status": "exhausted", "common_failure": False, "reasons": ["proposal-budget-exhausted"]}
    target = targets[proposal_index - 1] if targets else None
    identity = proposal_id(baseline, src, target) if target is not None else None
    result.update(target=target, proposal_id=identity, common_failure=False, reasons=[],
                  status="candidate" if target is not None else "no_change")
    outputs = _write_candidate(paths, out_dir, name, baseline, src, projection, target, identity)
    candidate, candidate_animations = read_loop(outputs)
    result.update(outputs={k: str(p) for k, p in outputs.items()}, candidate_artifacts=candidate.artifacts)
    # This is a preview, not authority for later bytes. The compare verb rereads and
    # measures every final input (including a caller's subsequent modifications).
    result["comparison"] = compare(baseline, candidate, src, baseline_playback=animations,
                                   candidate_playback=candidate_animations, repair_evidence=result)
    return result


def add_arguments(parser: argparse.ArgumentParser) -> None:
    for key in ARTIFACTS:
        parser.add_argument("--baseline-" + key, type=Path, required=True)
    source.add_inputs(parser)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--name", default="loop")
    parser.add_argument("--proposal-index", type=int, choices=range(1, MAX_PROPOSALS + 1), default=1)
    parser.add_argument("--report", type=Path, required=True)


def run(**kwargs: Any) -> int:
    try:
        inputs = source.input_paths(kwargs)
        paths = {k: Path(kwargs["baseline_" + k]) for k in ARTIFACTS}
        output = Path(kwargs["report"])
        out = Path(kwargs["out_dir"])
        name = kwargs.get("name", "loop")
        protected = [*paths.values(), *inputs.values(), *inputs["source_frames_dir"].glob("*.png")]
        protected += [out / (name + ext) for ext in (".strip.png", ".strip.json", ".loop.json", ".gif", ".webp")]
        if output.resolve() in {p.resolve() for p in protected}:
            raise ValueError("repair report cannot overwrite an input")
        payload = restore(paths, source.Source.read(**inputs), out_dir=out,
                          name=name, proposal_index=kwargs.get("proposal_index", 1))
        output.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=2, allow_nan=False) + "\n"
        atomic_write_text(output, text)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise SystemExit(f"video-loop-repair: {exc}") from exc
    print(text, end="")
    return 0
