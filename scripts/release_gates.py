# SPDX-License-Identifier: Apache-2.0
"""Which gates a release's change reaches, and what the release record says of each: the one table (docs/release.md).

The Linux lane (`scripts/linux_lane.sh`, GitHub-hosted runners) runs every test but RIFE's at the commit a release is
made from, every time. RIFE's tests need a GPU: those marked real_rife (the rife-ncnn-vulkan binary) and torch_rife (its
torch backend) run in the GPU lane (`scripts/cuda_lane.py` on the maintainer's NVIDIA host), and only where a change
since the commit that lane last passed at, or since the release before, reaches them: a file `GPU_LANE` names, or a test
file that marks a test real_rife or torch_rife. `scripts/release_publish.py` writes the gates block of the release page
from `record()`: the commit each gate passed at, or that it did not run and why. A reached gate without a passing run is
not a release until the maintainer says why it was not run (`--gpu-unmeasured`), which the page then says. Nothing is
passed over in silence.

Standard library only, as the other release scripts.

    .venv/bin/python scripts/release_gates.py --since v4.4.0 [--to HEAD]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The files whose change reaches the GPU lane. Its tests measure RIFE's frames (the binary on the host's Vulkan, the torch
# backend on CUDA) and what a cut makes of them, so: the modules that run RIFE or ask it for frames — each module of the
# package that imports rife, rife_torch or rife_install (tests/release/test_release_gates.py holds the list to that) —,
# what its tests read beside their own files, and what the lane installs and runs. A test file reaches it by its marks
# (`MARKED`), read from the file as it is at the commit measured.
GPU_LANE = (
    # RIFE and the modules that call it
    "sprite_gen/video/rife.py",
    "sprite_gen/video/rife_torch.py",
    "sprite_gen/video/rife_install.py",
    "sprite_gen/video/align.py",
    "sprite_gen/video/batch.py",
    "sprite_gen/video/chain.py",
    "sprite_gen/video/compare.py",
    "sprite_gen/video/held_fill.py",
    "sprite_gen/video/interpolation_quality.py",
    "sprite_gen/video/loop.py",
    "sprite_gen/video/repair.py",
    "sprite_gen/video/restoration.py",
    "sprite_gen/video/root_motion.py",
    "sprite_gen/video/stop.py",
    "sprite_gen/scene/render.py",
    "sprite_gen/cli.py",
    # what RIFE's tests read beside their own files: the suite's fixtures and pins, their recorded inputs and judgments
    "tests/conftest.py",
    "tests/picture_digest.py",
    "tests/video/*_inputs.py",
    "tests/fixtures/rife-*",
    # what the lane installs and runs
    "pyproject.toml",
    "scripts/cuda_lane.py",
    "scripts/release_gates.py",
)
MARKED = re.compile(r"\bpytest\.mark\.(real_rife|torch_rife)\b|\bmark\.(real_rife|torch_rife)\b")
GATE = "GPU lane"
WHAT = "RIFE's tests (real_rife on the host's Vulkan, torch_rife on CUDA), scripts/cuda_lane.py on the NVIDIA host"


class GateError(RuntimeError):
    """The record cannot be written: a reached gate with no passing run and no reason given, or a run that is no pass."""


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, encoding="utf-8")
    if proc.returncode != 0:
        raise GateError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


def reaches(path: str, text: str | None) -> bool:
    """Whether a change to `path` (its text at the commit measured, None where the change removed it) reaches the GPU
    lane: a file the table names, or a test file that marks a test real_rife or torch_rife."""
    if any(fnmatch.fnmatchcase(path, p) for p in GPU_LANE):  # `*` takes a `/` too: a pattern names a subtree
        return True
    return path.startswith("tests/") and path.endswith(".py") and text is not None and bool(MARKED.search(text))


def changed(root: Path, base: str, to: str) -> list[str]:
    """The files that differ between two commits, renames as a removal and an addition (both sides are read)."""
    out = _git(root, "diff", "--name-only", "--no-renames", f"{base}^{{commit}}", f"{to}^{{commit}}")
    return sorted(line for line in out.splitlines() if line)


def reached(root: Path, base: str, to: str) -> list[str]:
    """The files changed from `base` to `to` that reach the GPU lane; a test file is read on both sides, so removing a
    test's mark reaches it too."""
    found = []
    for path in changed(root, base, to):
        sides = []
        for commit in (base, to):
            proc = subprocess.run(["git", "-C", str(root), "show", f"{commit}:{path}"], capture_output=True)
            sides.append(proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else None)
        if any(reaches(path, text) for text in sides):
            found.append(path)
    return found


def tree(root: Path, commit: str) -> str:
    return _git(root, "rev-parse", f"{commit}^{{tree}}").strip()


def previous_release(root: Path, tag: str) -> str | None:
    """The release tag before `tag` on its history (`vX.Y.Z`), None where there is none."""
    proc = subprocess.run(["git", "-C", str(root), "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*",
                           f"{tag}^{{commit}}^"], capture_output=True, encoding="utf-8")
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def read_run(path: Path) -> dict:
    """A GPU lane run's record (`run.json`, scripts/cuda_lane.py): it must say it passed, at which commit."""
    rec = json.loads(path.read_text(encoding="utf-8"))
    if not rec.get("passed") or not re.fullmatch(r"[0-9a-f]{40}", str(rec.get("commit", ""))):
        raise GateError(f"{path} is not a passing GPU lane run (passed: {rec.get('passed')}, why: {rec.get('why')})")
    if rec.get("gates") != "real_rife or torch_rife":
        raise GateError(f"{path} ran {rec.get('gates') or 'the torch tests alone'}, not every RIFE test: no GPU lane pass")
    return rec


def record(root: Path, tag: str, *, gpu_run: dict | None = None, unmeasured: str | None = None,
           lane_commit: str | None = None) -> list[str]:
    """The gates block of the release page for `tag`, one line a gate. The GPU lane line is one of: it passed at the
    tag's tree; it last passed at another commit and no file it reaches changed since; it did not run and no file it
    reaches changed since the release before; it did not run, for the reason given, though files it reaches changed
    (named). A reached gate with no passing run and no reason stops the record (GateError)."""
    lines = []
    tag_tree = tree(root, tag)
    if lane_commit:
        if tree(root, lane_commit) != tag_tree:
            raise GateError(f"the Linux lane's package is from {lane_commit}, whose tree is not {tag}'s")
        lines.append(f"- Linux lane (every test but RIFE's, GitHub-hosted runners): passed at {lane_commit}, the tree of {tag}; "
                     "the package attached is that run's")
    else:
        lines.append("- Linux lane: no package from it attached (built where this page was published)")
    if gpu_run is not None and tree(root, gpu_run["commit"]) == tag_tree:
        lines.append(f"- {GATE} ({WHAT}): passed at {gpu_run['commit']}, the tree of {tag}" +
                     (f", on {gpu_run['gpu']}" if gpu_run.get("gpu") else ""))
        return lines
    base = gpu_run["commit"] if gpu_run is not None else previous_release(root, tag)
    if base is None:
        files = ["(no earlier release or GPU lane run to measure the change from)"]
    else:
        files = reached(root, base, tag)
    if not files:
        since = (f"it last passed at {base}" + (f", on {gpu_run['gpu']}" if gpu_run.get("gpu") else "")
                 if gpu_run is not None else f"{base}, the release before")
        lines.append(f"- {GATE} ({WHAT}): did not run — no file it reaches changed since {since} (scripts/release_gates.py)")
        return lines
    if not unmeasured:
        raise GateError(f"files the {GATE} reaches changed since {base or 'the start'}: {', '.join(files)}; run it at "
                        f"{tag} (scripts/cuda_lane.py) and give its run.json, or say why not (--gpu-unmeasured)")
    lines.append(f"- {GATE} ({WHAT}): NOT RUN ({unmeasured}), though files it reaches changed since "
                 f"{base or 'the start'}: {', '.join(files)}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", required=True, help="the commit or tag the change is measured from (the release before, "
                    "or the commit the GPU lane last passed at)")
    ap.add_argument("--to", default="HEAD", help="the commit to be released (default HEAD)")
    ap.add_argument("--checkout", type=Path, default=REPO_ROOT)
    a = ap.parse_args(argv)
    try:
        files = reached(a.checkout.resolve(), a.since, a.to)
    except GateError as exc:
        print(f"release_gates: {exc}", file=sys.stderr)
        return 2
    if files:
        print(f"{GATE}: reached from {a.since} to {a.to} by\n" + "\n".join(f"  {f}" for f in files))
    else:
        print(f"{GATE}: not reached from {a.since} to {a.to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
