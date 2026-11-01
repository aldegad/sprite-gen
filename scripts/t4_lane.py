# SPDX-License-Identifier: Apache-2.0
"""The torch backend's CUDA gates, on an NVIDIA T4 rented from Modal: the GPU run `scripts/linux_lane.sh` leaves to this
lane (docs/release.md). GitHub-hosted CI is not used.

    modal run [--env <environment>] scripts/t4_lane.py [--whole-suite]

Run it from a checkout of the commit being measured with no uncommitted changes: it measures HEAD, its tree as
`git archive` writes it. The tree is built into an image as the Linux lane installs a clean clone: Python PINNED_PYTHON,
a new virtual environment holding PyTorch TORCH_VERSION from PyPI (its Linux wheel is the CUDA 13 build, the one
`pip install "sprite-gen[torch]"` takes; its dependencies come from PyPI too, never the PyTorch index's older copies),
then the tree (`pip install -e ".[dev]"`) and the model (`sprite-gen rife install --backend torch --no-check`, into a
directory of its own: the engine looks for no binary here, which is the Linux lane's to measure, and nothing here runs
Vulkan). The image holds the whole suite's tools as the Linux lane's host does (ffmpeg, img2webp from libwebp >= 1.5,
node, git). Then, on one T4:

  - the install's check frame on CUDA (`sprite-gen rife install --backend torch` again, SPRITE_GEN_RIFE_DEVICE=cuda);
  - with --whole-suite, the whole suite in that environment, PyTorch in it, on CUDA (`pytest --full --rife-unmeasured`:
    the tests marked slow run too, the tests marked real_rife skip, the binary not being reachable, and any other skip
    fails unless tests/conftest.py names it, as in the Linux lane's RIFE-less run);
  - every test marked torch_rife and not real_rife, on CUDA, none skipped. Those marked both set this machine's binary
    beside the torch backend: the Linux lane runs them.

It prints the function call's id, the GPU, its driver and what each step said, and exits 0 only where every step passed.
A commit has passed the T4 lane where one run of it at that commit exited 0.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import modal

PINNED_PYTHON = "3.14"
PYTHON_IMAGE = "python:3.14.9-slim-trixie"
# The PyTorch the torch backend was measured on, as the Linux lane installs it (there from PyTorch's CPU index).
TORCH_VERSION = "2.13.0"
GPU = "T4"
VENV = "/lane/venv"
SRC = "/lane/src"
RIFE_DIR = "/lane/rife"


def _fail(why: str) -> None:
    print(f"t4_lane: {why}", file=sys.stderr)
    raise SystemExit(1)


def _source() -> tuple[str, Path]:
    """HEAD and its tree as `git archive` writes it, from a checkout with no uncommitted changes."""
    root = Path(__file__).resolve().parents[1]

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout

    if git("status", "--porcelain", "--untracked-files=no").strip():
        _fail("the checkout has uncommitted changes")
    sha = git("rev-parse", "HEAD").strip()
    work = Path(tempfile.mkdtemp(prefix="t4-lane-"))
    atexit.register(shutil.rmtree, work, True)
    tree = work / "source.tar.gz"
    subprocess.run(["git", "-C", str(root), "archive", "--format=tar.gz", "-o", str(tree), sha], check=True)
    return sha, tree


# Only where `modal run` starts: the container imports this file again and needs no source of its own.
SHA, TREE = _source() if modal.is_local() else ("", Path("/nonexistent"))

image = (
    modal.Image.from_registry(PYTHON_IMAGE)
    .apt_install("ffmpeg", "webp", "nodejs", "git")
    .env({"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    .run_commands(f"python{PINNED_PYTHON} -m venv {VENV}", f"{VENV}/bin/pip install --quiet torch=={TORCH_VERSION}")
    .add_local_file(TREE, "/lane/source.tar.gz", copy=True)
    .run_commands(
        f"mkdir -p {SRC} && tar -xzf /lane/source.tar.gz -C {SRC} && rm /lane/source.tar.gz",
        f"printf '%s\\n' {SHA} > /lane/SOURCE_SHA",
        f'{VENV}/bin/pip install --quiet -e "{SRC}[dev]"',
        f"{VENV}/bin/pip freeze --exclude-editable > /lane/pip-freeze.txt",
        f"SPRITE_GEN_RIFE_BACKEND=torch {VENV}/bin/sprite-gen rife install --dir {RIFE_DIR} --no-check > /lane/rife-install.json",
    )
)
app = modal.App("sprite-gen-t4-lane", image=image)


def _step(steps: list[dict], name: str, argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    print(f"== {name}", flush=True)
    t0 = time.monotonic()
    proc = subprocess.run(argv, cwd=SRC, env=env, capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    steps.append({"name": name, "exit": proc.returncode, "seconds": round(time.monotonic() - t0, 1), "out": out})
    return proc


@app.function(gpu=GPU, cpu=2.0, memory=8192, timeout=7200)
def gates(sha: str, whole_suite: bool) -> dict:
    """The steps above, in this container: their exits and what they said."""
    built = Path("/lane/SOURCE_SHA").read_text().strip()
    rec: dict = {"commit": sha, "image_commit": built, "whole_suite": whole_suite, "steps": [], "passed": False}
    if built != sha:
        rec["why"] = f"the image holds {built}, not {sha}"
        return rec
    installed = json.loads(Path("/lane/rife-install.json").read_text())
    model = Path(installed["dir"]) / installed["model"]  # the model the engine runs by default (rife-v4.25)
    # Nowhere the engine looks for the binary holds one: an empty data directory, nothing on PATH, no SPRITE_GEN_RIFE.
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPRITE_GEN_")}
    env.update(PATH=f"{VENV}/bin:{env.get('PATH', '/usr/local/bin:/usr/bin:/bin')}", XDG_DATA_HOME=tempfile.mkdtemp(),
               SPRITE_GEN_RIFE_MODEL=str(model), SPRITE_GEN_RIFE_DEVICE="cuda")
    steps = rec["steps"]
    rec["pip_freeze"] = Path("/lane/pip-freeze.txt").read_text()
    smi = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                         capture_output=True, text=True)
    rec["gpu"] = (smi.stdout or smi.stderr).strip()
    probe = _step(steps, "PyTorch", [f"{VENV}/bin/python", "-c", "import torch; print('torch', torch.__version__, 'cuda', "
                                     "torch.version.cuda, 'cudnn', torch.backends.cudnn.version(), 'gpu', "
                                     "torch.cuda.get_device_name() if torch.cuda.is_available() else None)"], env)
    if probe.returncode != 0 or "gpu None" in probe.stdout:
        rec["why"] = "PyTorch finds no CUDA GPU here"
        return rec
    if _step(steps, "RIFE torch backend (sprite-gen rife install --backend torch, its check frame on CUDA)",
             [f"{VENV}/bin/sprite-gen", "rife", "install", "--backend", "torch", "--dir", RIFE_DIR], env).returncode != 0:
        rec["why"] = "the install's check frame failed"
        return rec
    # --full: every test, those marked slow too (a plain pytest is the fast bundle).
    pytest = [f"{VENV}/bin/python", "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", "--full"]
    if whole_suite and _step(steps, "the whole suite (PyTorch in the environment, on CUDA; the binary not reachable)",
                             [*pytest, "--basetemp", tempfile.mkdtemp(), "--rife-unmeasured"], env).returncode != 0:
        rec["why"] = "the whole suite failed"
        return rec
    # No test marked: pytest exits 5 ("no tests ran"), and the lane with it.
    marked = _step(steps, "RIFE torch tests on CUDA (every test marked torch_rife and not real_rife, none skipped)",
                   [*pytest, "--basetemp", tempfile.mkdtemp(), "-m", "torch_rife and not real_rife"], env)
    if marked.returncode != 0:
        rec["why"] = "a torch_rife test failed"
    elif any(line.startswith("SKIPPED") for line in marked.stdout.splitlines()):
        rec["why"] = "a torch_rife test skipped; this run is meant to measure them"
    else:
        rec["passed"] = True
    return rec


@app.local_entrypoint()
def main(whole_suite: bool = False) -> None:
    call = gates.spawn(SHA, whole_suite)
    print(f"t4_lane: commit {SHA}, {GPU}, function call {call.object_id}", flush=True)
    rec = call.get()
    print(f"gpu {rec.get('gpu')}")
    print(rec.get("pip_freeze", "").rstrip())
    for step in rec["steps"]:
        print(f"== {step['name']}: exit {step['exit']}, {step['seconds']} s")
        print(step["out"].rstrip())
    if not rec["passed"]:
        _fail(f"{rec.get('why')} at {SHA} (function call {call.object_id})")
    print(f"t4_lane: the torch backend's CUDA gates passed at {SHA} (function call {call.object_id})")
