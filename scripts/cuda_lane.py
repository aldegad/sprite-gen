# SPDX-License-Identifier: Apache-2.0
"""The GPU lane: every RIFE test on the maintainer's NVIDIA host, the rife-ncnn-vulkan binary on its Vulkan (real_rife)
and the torch backend on CUDA (torch_rife), which `scripts/linux_lane.sh` on GPU-less runners leaves to this lane
(docs/release.md). It runs where a change reaches them (`scripts/release_gates.py`), started by hand: nothing runs on
push or pull request, and no runner of the repository's starts it. Standard library only.

`pack` runs on one machine, the rest on the host, each call there under the ten minutes a call may hold a remote
machine:

    .venv/bin/python scripts/cuda_lane.py pack --requirements <list> --out <empty dir> [--host-spec <eco>:<name>@<version> ...]
    <python> -I <pack>/cuda_lane.py prepare --pack <pack> [--wheels <dir>] [--rife-zip <zip>]
    <python> -I <pack>/cuda_lane.py test --pack <pack> --chunk K/N [--budget <seconds>] [--leave <test> ...]  # each K
    <python> -I <pack>/cuda_lane.py verdict --pack <pack> --chunks N

`pack` runs where a checkout of the commit being measured is (no uncommitted changes) and where safedeps is. The list
names every package the host will install, one `name==version --hash=sha256:<hex>` each: the closure of the tree's
`[dev]` extra and of PyTorch TORCH_VERSION's CUDA build, for the host's platform and Python PINNED_PYTHON. Each of
them (PyTorch by its version without the local label) and each --host-spec (what the host installs for the lane apart
from the list, such as its Python) is checked with `safedeps check`. An answer that is not `clean` or
`already_approved` for that very version, a check that did not finish, and a safedeps that could not be run each stop
`pack` before it writes anything: there is then nothing for the host to install. Once every check has passed, `pack`
writes HEAD's tree as `git archive` writes it (source.tar.gz), SOURCE_SHA, the list as checked (requirements.txt),
what each check said (checks.json) and this file as HEAD holds it.

`prepare` runs on the host, in that directory, with Python PINNED_PYTHON. It refuses a directory whose checks.json does
not hold a passing check of every line of its requirements.txt. Then, as the Linux lane installs a clean clone: a new
virtual environment, every wheel of the list downloaded with its hash checked (from PyPI, or from --wheels for one PyPI
does not hold, such as PyTorch's CUDA build for Windows, which comes from PyTorch's own index), installed from those
files alone, the tree (`pip install -e src[dev]`, nothing more fetched), `pip check`, and RIFE (`sprite-gen rife
install`, the binary and its models into a directory of its own, its check frame through the binary on this host's
Vulkan). On Windows a path of WINDOWS_MAX_PATH characters or more cannot be written where long paths
are not enabled, so `prepare` refuses before installing anything when the longest name a wheel writes would not fit
under the virtual environment: the directory's path is then too long. Then the GPU and its driver (`nvidia-smi`),
PyTorch's CUDA (fails where PyTorch finds no GPU), the install's check frame on CUDA (`sprite-gen rife install
--backend torch` again, SPRITE_GEN_RIFE_DEVICE=cuda), and the tests to run: every test marked real_rife or torch_rife
(GATE_TESTS), collected. It keeps all of it in prepared.json and logs/.

`test --chunk K/N` runs share K of N of those tests (`pytest --shard K/N`, tests/conftest.py), none skipped: the binary
installed here (SPRITE_GEN_RIFE) and the torch backend on CUDA, those marked both setting the two side by side. A chunk
that runs past its budget (CHUNK_BUDGET seconds unless given) is stopped with every process it started, and fails. Each
chunk keeps what it ran, read from pytest's own report (--junitxml), and how in chunk-K-of-N.json, red or green, and
runs once. A test that cannot run within a chunk's budget is left by name (--leave, the same tests for every chunk of
the split): pytest deselects it and the chunk's record names it. `verdict --chunks N` passes where every chunk 1..N of
that split passed at the pack's commit on the environment `prepare` made, they left the same tests, and together they
ran every other test `prepare` collected, each once; it then writes run.json, and only then, naming the tests left. A
release whose GPU lane run left tests says why on its page (`release_publish.py --gpu-unmeasured`).

The engine finds only what this run installed: no SPRITE_GEN_* from outside but the run's own (SPRITE_GEN_RIFE, the
model, the device), an empty user data directory (LOCALAPPDATA on Windows, XDG_DATA_HOME elsewhere; a new one each
chunk), no rife-ncnn-vulkan on PATH. TEMP, pip's cache and CUDA's cache point into the directory, so the run writes
nowhere else and deleting the directory removes everything it wrote. A commit has passed the GPU lane where `verdict`
wrote run.json for it; run.json names the commit, the GPU, its driver, `pip freeze`, the tests it ran (`gates`) and
each chunk, and `scripts/release_publish.py --gpu-lane <run.json>` writes it into the release page.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import NoReturn
from xml.etree import ElementTree

PINNED_PYTHON = "3.14"
# The PyTorch the torch backend was measured on, as the Linux lane installs it (there its CPU build).
TORCH_VERSION = "2.13.0"
# Windows cannot write a path this long or longer where long paths are not enabled (MAX_PATH counts the closing null).
WINDOWS_MAX_PATH = 260
# What `safedeps check` may answer for a package the host then installs, at the very version asked.
PASSING = ("clean", "already_approved")
CHECK_TIMEOUT = 900  # seconds one `safedeps check` may take
PACK_FILES = ("source.tar.gz", "SOURCE_SHA", "requirements.txt", "checks.json", "cuda_lane.py")
PIN = re.compile(r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==([0-9A-Za-z.+!_-]+)((?:\s+--hash=sha256:[0-9a-f]{64})+)")
HOST_SPEC = re.compile(r"([a-z0-9]+):([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)@([0-9A-Za-z.+!_-]+)")
GATE_TESTS = "real_rife or torch_rife"
# Seconds one chunk's tests may take: with the interpreter's start, one call on the host stays under the ten minutes
# a call may hold a remote machine.
CHUNK_BUDGET = 540
CHUNK = re.compile(r"([1-9][0-9]*)/([1-9][0-9]*)")


def _fail(why: str) -> NoReturn:
    print(f"cuda_lane: {why}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _public(version: str) -> str:
    """The version without its local label (2.13.0+cu130 -> 2.13.0): what advisories are published against."""
    return version.split("+", 1)[0]


def read_pins(text: str) -> list[tuple[str, str]]:
    """The list's pins as (name, version): one `name==version --hash=sha256:<hex> ...` each, a line ending in a
    backslash continuing on the next, comments and blank lines aside. Anything else (an option, a URL, an editable, a
    pin without its hash, a name twice) is refused, and the list must pin PyTorch TORCH_VERSION."""
    pins: list[tuple[str, str]] = []
    logical = re.sub(r"\\[ \t]*\r?\n", " ", text)
    for n, line in enumerate(logical.splitlines(), 1):
        bare = re.sub(r"(^|\s)#.*$", "", line).strip()
        if not bare:
            continue
        m = PIN.fullmatch(bare)
        if not m:
            _fail(f"requirements line {n} is not `name==version --hash=sha256:<hex>`: {bare}")
        pins.append((m.group(1), m.group(2)))
    names = [_canonical(name) for name, _ in pins]
    twice = sorted({name for name in names if names.count(name) > 1})
    if twice:
        _fail(f"requirements name {', '.join(twice)} more than once")
    torch = [version for name, version in pins if _canonical(name) == "torch"]
    if [_public(v) for v in torch] != [TORCH_VERSION]:
        _fail(f"requirements must pin torch {TORCH_VERSION} (its CUDA build for the host), found {torch or 'none'}")
    return pins


def _host_specs(given: list[str]) -> list[tuple[str, str, str]]:
    specs = []
    for spec in given:
        m = HOST_SPEC.fullmatch(spec)
        if not m:
            _fail(f"--host-spec {spec} is not <ecosystem>:<name>@<version>")
        specs.append((m.group(1), m.group(2), m.group(3)))
    return specs


def _report(stdout: str) -> dict:
    """The JSON report `safedeps check --json` printed last, or {} where it printed none."""
    for line in reversed(stdout.splitlines()):
        try:
            report = json.loads(line)
        except ValueError:
            continue
        if isinstance(report, dict):
            return report
    return {}


def check_specs(specs: list[tuple[str, str, str]], safedeps: list[str]) -> list[dict]:
    """`safedeps check <ecosystem> <name>@<version> --json` for each spec in order: what each said. The first answer
    that does not pass that very version stops the lane, so nothing past it is checked or packed."""
    said = []
    for ecosystem, name, version in specs:
        spec = f"{ecosystem} {name}@{version}"
        try:
            proc = subprocess.run([*safedeps, "check", ecosystem, f"{name}@{version}", "--json"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=CHECK_TIMEOUT)
        except OSError as e:
            _fail(f"safedeps could not be run to check {spec} ({e}); nothing is packed, so the host installs nothing")
        except subprocess.TimeoutExpired:
            _fail(f"safedeps check {spec} did not finish in {CHECK_TIMEOUT} s; nothing is packed, so the host "
                  "installs nothing")
        report = _report(proc.stdout)
        rec = {"spec": spec, "exit": proc.returncode, "result": report.get("result"), "error": report.get("error"),
               "resolved_version": report.get("resolved_version"),
               "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        said.append(rec)
        print(f"safedeps check {spec}: exit {rec['exit']}, {rec['result']}"
              f"{' (' + rec['error'] + ')' if rec['error'] else ''}", flush=True)
        if proc.returncode != 0 or rec["result"] not in PASSING or rec["resolved_version"] != version:
            what = rec["result"] or "no report"
            if rec["error"]:
                what += f" ({rec['error']})"
            if rec["result"] in PASSING and rec["resolved_version"] != version:
                what += f" for {rec['resolved_version']}, not {version}"
            _fail(f"safedeps check {spec} answered exit {proc.returncode}, {what}: the host installs only what is "
                  "clean or already approved at that very version, so nothing is packed and the host installs nothing")
    return said


def pack(root: Path, requirements: Path, out: Path, host_specs: list[str], safedeps: list[str]) -> str:
    """The steps of `pack` above, from the checkout at root: HEAD's sha."""
    text = requirements.read_bytes().decode("utf-8")
    pins = read_pins(text)
    hosts = _host_specs(host_specs)
    root, out = root.resolve(), out.resolve()
    if out == root or root in out.parents:
        _fail(f"--out {out} must be outside the checkout")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        _fail(f"--out {out} is not an empty directory")

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout

    if git("status", "--porcelain", "--untracked-files=no").strip():
        _fail("the checkout has uncommitted changes")
    sha = git("rev-parse", "HEAD").strip()
    script = subprocess.run(["git", "-C", str(root), "show", f"{sha}:scripts/cuda_lane.py"], capture_output=True)
    if script.returncode:
        _fail(f"{sha} holds no scripts/cuda_lane.py")
    print(f"cuda_lane: commit {sha}, {len(pins)} packages, {len(hosts)} for the host", flush=True)
    checks = check_specs([("pypi", name, _public(version)) for name, version in pins] + hosts, safedeps)
    out.mkdir(parents=True, exist_ok=True)
    git("archive", "--format=tar.gz", "-o", str(out / "source.tar.gz"), sha)
    (out / "SOURCE_SHA").write_text(sha + "\n", encoding="ascii")
    (out / "requirements.txt").write_bytes(text.encode("utf-8"))
    (out / "cuda_lane.py").write_bytes(script.stdout)
    record = {"commit": sha, "requirements_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "checks": checks}
    (out / "checks.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"cuda_lane: packed {sha} into {out}; every package checked", flush=True)
    return sha


def verify_pack(where: Path) -> tuple[str, list[tuple[str, str]]]:
    """The pack's commit and pins, where it holds every file and a passing check of each pin; else the lane stops."""
    missing = [name for name in PACK_FILES if not (where / name).is_file()]
    if missing:
        _fail(f"{where} holds no {', '.join(missing)}: make it with `cuda_lane.py pack`")
    sha = (where / "SOURCE_SHA").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        _fail(f"SOURCE_SHA is not a commit: {sha!r}")
    text = (where / "requirements.txt").read_bytes().decode("utf-8")
    pins = read_pins(text)
    record = json.loads((where / "checks.json").read_text(encoding="utf-8"))
    if record.get("commit") != sha:
        _fail(f"checks.json is for {record.get('commit')}, not {sha}")
    if record.get("requirements_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
        _fail("requirements.txt is not the list that was checked")
    passed = {c.get("spec") for c in record.get("checks", []) if c.get("exit") == 0 and c.get("result") in PASSING}
    unchecked = [f"{name}=={version}" for name, version in pins if f"pypi {name}@{_public(version)}" not in passed]
    if unchecked:
        _fail(f"checks.json holds no passing check of {', '.join(unchecked)}; nothing is installed")
    return sha, pins


def longest_install_path(site_packages: str, wheels: list[Path]) -> tuple[int, str]:
    """The longest Windows path a wheel's files take under site_packages: its length and the path. Counted as if every
    member went into site-packages, which none of them outruns (a wheel's .data members go to shorter places)."""
    longest = (0, "")
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as z:
            for member in z.namelist():
                path = str(PureWindowsPath(site_packages, *member.split("/")))
                longest = max(longest, (len(path), path))
    return longest


def path_refusal(site_packages: str, wheels: list[Path]) -> str | None:
    """Why the wheels cannot be installed under site_packages on a Windows host without long paths, or None."""
    length, path = longest_install_path(site_packages, wheels)
    if length < WINDOWS_MAX_PATH:
        return None
    cut = length - WINDOWS_MAX_PATH + 1
    return (f"the longest path the wheels write is {length} characters, {path}; Windows writes none of "
            f"{WINDOWS_MAX_PATH} or more where long paths are not enabled: run from a directory whose path is shorter "
            f"by at least {cut} character" + ("s" if cut > 1 else ""))


def parse_chunk(text: str) -> tuple[int, int]:
    """`K/N`, 1 <= K <= N: share K of N."""
    m = CHUNK.fullmatch(text)
    if not m or int(m.group(1)) > int(m.group(2)):
        _fail(f"--chunk {text} is not K/N, 1 <= K <= N")
    return int(m.group(1)), int(m.group(2))


def junit_key(nodeid: str) -> str:
    """A test's pytest node id as pytest's --junitxml report names it: `<classname>::<name>`, the file's path dotted
    without .py and any class joined to it with dots."""
    path, bracket, params = nodeid.partition("[")
    names = path.split("::")
    names[0] = re.sub(r"\.py$", "", names[0].replace("/", "."))
    return ".".join(names[:-1]) + "::" + names[-1] + bracket + params


def junit_ran(xml: str | bytes) -> dict[str, list]:
    """What pytest's --junitxml report says ran: junit_key -> [passed | failed | error | skipped, seconds]."""
    root = ElementTree.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    ran: dict[str, list] = {}
    for case in root.iter("testcase"):
        outcome = next((said for tag, said in (("failure", "failed"), ("error", "error"), ("skipped", "skipped"))
                        if case.find(tag) is not None), "passed")
        ran[f"{case.get('classname')}::{case.get('name')}"] = [outcome, float(case.get("time") or 0)]
    return ran


def run_bounded(argv: list[str], budget: float, env: dict[str, str] | None,
                cwd: Path | None) -> tuple[int | None, str, bool]:
    """argv with its output (stderr into stdout): (exit, output, False), or (None, output so far, True) where it ran
    past budget seconds and was stopped with every process it started."""
    windows = os.name == "nt"
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if windows else {"start_new_session": True}
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", **group)
    try:
        said, _ = proc.communicate(timeout=budget)
        return proc.returncode, said, False
    except subprocess.TimeoutExpired:
        if windows:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        said, _ = proc.communicate()
        return None, said, True


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _layout(where: Path) -> dict[str, Path]:
    windows = os.name == "nt"
    paths = {name: where / name for name in ("src", "venv", "dl", "rife", "tmp", "logs")}
    paths["bin"] = paths["venv"] / ("Scripts" if windows else "bin")
    paths["py"] = paths["bin"] / ("python.exe" if windows else "python")
    paths["cli"] = paths["bin"] / ("sprite-gen.exe" if windows else "sprite-gen")
    return paths


DATA_KEY = "LOCALAPPDATA" if os.name == "nt" else "XDG_DATA_HOME"


def _environment(where: Path) -> dict[str, str]:
    """The run's environment: no SPRITE_GEN_* from outside, temporary files and caches inside where."""
    tmp = where / "tmp"
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("SPRITE_GEN_")}
    env.update(TEMP=str(tmp), TMP=str(tmp), TMPDIR=str(tmp), PIP_CACHE_DIR=str(where / "cache" / "pip"),
               CUDA_CACHE_PATH=str(where / "cache" / "cuda"), PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1",
               PIP_CONFIG_FILE=os.devnull, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return env


def _with_rife(env: dict[str, str], where: Path, binary: str, model: str, data: str) -> dict[str, str]:
    """The engine finds what `prepare` installed and nothing else: SPRITE_GEN_RIFE and the model named, an empty data
    directory, no binary on PATH."""
    return {**env, "PATH": os.pathsep.join([str(_layout(where)["bin"]), env.get("PATH", "")]),
            "SPRITE_GEN_RIFE": binary, "SPRITE_GEN_RIFE_MODEL": model, "SPRITE_GEN_RIFE_DEVICE": "cuda",
            DATA_KEY: data}


def _prepared(where: Path, sha: str) -> dict:
    """prepare's record in where, where it prepared the environment at sha; else the lane stops."""
    path = where / "prepared.json"
    rec = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not rec.get("prepared"):
        _fail(f"{where} was not prepared: run `cuda_lane.py prepare` first" +
              (f" (it stopped: {rec['why']})" if rec.get("why") else ""))
    if rec.get("commit") != sha:
        _fail(f"prepared.json is for {rec.get('commit')}, not {sha}")
    return rec


def prepare(where: Path, wheels: Path | None, rife_zip: Path | None) -> int:
    """The steps of `prepare` above, in the pack at where: 0 where every step passed."""
    where = where.resolve()
    sha, _ = verify_pack(where)
    here = "%d.%d" % sys.version_info[:2]
    if here != PINNED_PYTHON:
        _fail(f"this is Python {here}; the lane pins {PINNED_PYTHON}")
    windows = os.name == "nt"
    lay = _layout(where)
    src, venv, dl, rife_dir, tmp, logs = (lay[name] for name in ("src", "venv", "dl", "rife", "tmp", "logs"))
    made = [p.name for p in (src, venv, dl, rife_dir, tmp, logs) if p.exists()]
    if made:
        _fail(f"{where} already holds {', '.join(made)}; prepare each pack once")
    for d in (src, dl, rife_dir, tmp, logs):
        d.mkdir()
    py, cli = lay["py"], lay["cli"]
    env = _environment(where)
    env[DATA_KEY] = tempfile.mkdtemp(prefix="data-install-", dir=tmp)
    rec: dict = {"commit": sha, "python": sys.version, "gates": GATE_TESTS, "steps": [], "prepared": False}

    def done(why: str | None = None) -> int:
        if why:
            rec["why"] = why
        (where / "prepared.json").write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
        if rec["prepared"]:
            print(f"cuda_lane: prepared at {sha}; {len(rec['collected'])} tests ({GATE_TESTS}) to run in chunks "
                  "(`cuda_lane.py test --chunk K/N`)", flush=True)
            return 0
        print(f"cuda_lane: {why} at {sha}", file=sys.stderr, flush=True)
        return 1

    def step(name: str, argv: list[str], log: str, show: bool = True, cwd: Path | None = None,
             run_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        print(f"== {name}", flush=True)
        t0 = time.monotonic()
        proc = subprocess.run(argv, cwd=cwd, env=run_env or env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        said = proc.stdout + proc.stderr
        (logs / log).write_text(said, encoding="utf-8")
        secs = round(time.monotonic() - t0, 1)
        rec["steps"].append({"name": name, "exit": proc.returncode, "seconds": secs, "log": f"logs/{log}"})
        print(said.rstrip() if show or proc.returncode else f"   ({len(said.splitlines())} lines in logs/{log})")
        print(f"   exit {proc.returncode}, {secs} s", flush=True)
        return proc

    print(f"cuda_lane: commit {sha}, Python {sys.version.split()[0]}, in {where}", flush=True)
    with tarfile.open(where / "source.tar.gz") as t:
        t.extractall(src, filter="data")
    if step("virtual environment", [sys.executable, "-I", "-m", "venv", str(venv)], "venv.log").returncode:
        return done("the virtual environment could not be made")
    found = step("site-packages", [str(py), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                 "site-packages.log")
    if found.returncode:
        return done("the virtual environment's site-packages could not be read")
    site_packages = found.stdout.strip()
    links = ["--find-links", str(wheels.resolve())] if wheels else []
    pip = [str(py), "-m", "pip"]
    if step("download every wheel of the list, each hash checked",
            [*pip, "download", "--no-deps", "--only-binary=:all:", "--require-hashes", "-r",
             str(where / "requirements.txt"), "-d", str(dl), *links], "download.log", show=False).returncode:
        return done("a wheel of the list could not be downloaded or did not match its hash")
    if windows:
        refused = path_refusal(site_packages, sorted(dl.glob("*.whl")))
        if refused:
            return done(refused)
    if step("install the list from those wheels alone",
            [*pip, "install", "--no-deps", "--no-index", "--only-binary=:all:", "--require-hashes", "--find-links",
             str(dl), "-r", str(where / "requirements.txt")], "install.log", show=False).returncode:
        return done("the list could not be installed")
    if step("install the tree (pip install -e src[dev], nothing more fetched)",
            [*pip, "install", "--no-deps", "--no-index", "--no-build-isolation", "-e", f"{src}[dev]"],
            "tree.log", show=False).returncode:
        return done("the tree could not be installed")
    if step("pip check", [*pip, "check"], "pip-check.log").returncode:
        return done("the environment's requirements do not hold: the list is not the tree's closure")
    freeze = step("pip freeze", [*pip, "freeze", "--exclude-editable"], "pip-freeze.log")
    rec["pip_freeze"] = freeze.stdout
    model_args = ["--zip", str(rife_zip.resolve())] if rife_zip else []
    model = step("RIFE (sprite-gen rife install: the binary and its models, its check frame on this host's Vulkan)",
                 [str(cli), "rife", "install", "--dir", str(rife_dir), *model_args], "rife-install.log", show=False)
    if model.returncode:
        return done("RIFE could not be installed, or its check frame failed on this host's Vulkan")
    installed = json.loads(model.stdout)
    rec["binary"] = installed["binary"]
    rec["model"] = str(Path(installed["dir"]) / installed["model"])
    gate_env = _with_rife(env, where, rec["binary"], rec["model"], tempfile.mkdtemp(prefix="data-prepare-", dir=tmp))
    rec["env"] = {k: gate_env[k] for k in (DATA_KEY, "SPRITE_GEN_RIFE", "SPRITE_GEN_RIFE_MODEL",
                                           "SPRITE_GEN_RIFE_DEVICE", "TEMP", "PIP_CACHE_DIR", "CUDA_CACHE_PATH")}
    on_path = shutil.which("rife-ncnn-vulkan", path=gate_env["PATH"])
    if on_path:
        return done(f"rife-ncnn-vulkan is on PATH ({on_path}); the lane measures the binary it installed")
    try:
        smi = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True)
        rec["gpu"] = (smi.stdout or smi.stderr).strip()
    except OSError as e:
        rec["gpu"] = f"nvidia-smi could not be run: {e}"
    print(f"gpu {rec['gpu']}", flush=True)
    print(freeze.stdout.rstrip(), flush=True)
    probe = step("PyTorch", [str(py), "-c", "import torch; print('torch', torch.__version__, 'cuda', torch.version.cuda, "
                             "'cudnn', torch.backends.cudnn.version(), 'gpu', torch.cuda.get_device_name() if "
                             "torch.cuda.is_available() else None)"], "probe.log", cwd=src, run_env=gate_env)
    rec["probe"] = probe.stdout.strip()
    if probe.returncode != 0 or "gpu None" in probe.stdout:
        return done("PyTorch finds no CUDA GPU here")
    if step("RIFE torch backend (sprite-gen rife install --backend torch, its check frame on CUDA)",
            [str(cli), "rife", "install", "--backend", "torch", "--dir", str(rife_dir)], "check-frame.log",
            cwd=src, run_env=gate_env).returncode:
        return done("the install's check frame failed")
    # --full: every test, those marked slow too (a plain pytest is the fast bundle).
    collected = step(f"collect the RIFE tests (every test marked {GATE_TESTS})",
                     [str(py), "-m", "pytest", "-q", "--collect-only", "-p", "no:cacheprovider", "--full", "-m",
                      GATE_TESTS], "collect.log", show=False, cwd=src, run_env=gate_env)
    rec["collected"] = [line.strip() for line in collected.stdout.splitlines() if "::" in line]
    if collected.returncode != 0 or not rec["collected"]:
        return done(f"no test marked {GATE_TESTS} was collected")
    rec["prepared"] = True
    return done()


def chunk_args(k: int, n: int, leave: list[str], collected: list[str]) -> list[str]:
    """pytest's arguments for share k of n of the tests marked GATE_TESTS, those left deselected by name; a test left
    that `prepare` did not collect stops the lane."""
    for nodeid in leave:
        if nodeid not in collected:
            _fail(f"--leave {nodeid} is not a test prepare collected")
    return ["-m", GATE_TESTS, "--shard", f"{k}/{n}", *[arg for nodeid in leave for arg in ("--deselect", nodeid)]]


def run_chunk(where: Path, chunk: str, budget: float, leave: list[str] | None = None) -> int:
    """Chunk K/N of the tests `prepare` collected, in the pack at where: 0 where every test of it passed."""
    where = where.resolve()
    k, n = parse_chunk(chunk)
    sha, _ = verify_pack(where)
    prepared = _prepared(where, sha)
    leave = list(leave or [])
    args = chunk_args(k, n, leave, prepared.get("collected", []))
    out = where / f"chunk-{k}-of-{n}.json"
    if out.exists():
        _fail(f"{out.name} is there already; run each chunk once")
    lay = _layout(where)
    tmp, logs = lay["tmp"], lay["logs"]
    env = _with_rife(_environment(where), where, prepared["binary"], prepared["model"],
                     tempfile.mkdtemp(prefix=f"data-{k}-of-{n}-", dir=tmp))
    env["PYTHONUNBUFFERED"] = "1"  # what a chunk stopped at its budget printed is kept, the test it stopped in too
    junit = logs / f"chunk-{k}-of-{n}.xml"
    rec: dict = {"commit": sha, "gates": GATE_TESTS, "chunk": f"{k}/{n}",
                 "prepared_sha256": sha256_of(where / "prepared.json"), "budget": budget,
                 "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "log": f"logs/chunk-{k}-of-{n}.log", "left": leave, "passed": False}
    print(f"cuda_lane: commit {sha}, chunk {k}/{n} of the tests marked {GATE_TESTS}, budget {budget} s", flush=True)
    t0 = time.monotonic()
    code, said, over = run_bounded(
        [str(lay["py"]), "-m", "pytest", "-v", "-rs", "-p", "no:cacheprovider", "--full", "--basetemp",
         tempfile.mkdtemp(prefix=f"pytest-{k}-of-{n}-", dir=tmp), *args, "--junitxml", str(junit)], budget, env,
        lay["src"])
    rec["seconds"] = round(time.monotonic() - t0, 1)
    (logs / f"chunk-{k}-of-{n}.log").write_text(said, encoding="utf-8")
    print(said.rstrip(), flush=True)
    rec["exit"] = code
    rec["ran"] = junit_ran(junit.read_bytes()) if junit.is_file() else {}
    rec["skipped"] = ([line for line in said.splitlines() if line.startswith("SKIPPED")] or
                      [key for key, (outcome, _) in rec["ran"].items() if outcome == "skipped"])
    rec["pytest_summary"] = ([line for line in said.splitlines() if line.strip()] or [""])[-1]
    failed = [key for key, (outcome, _) in rec["ran"].items() if outcome in ("failed", "error")]
    if over:
        rec["why"] = f"the chunk ran past its budget of {budget} s and was stopped"
        begun = [line.split(" ", 1)[0] for line in said.splitlines() if "::" in line and line.startswith("tests/")]
        done_ = {line.split(" ", 1)[0] for line in said.splitlines()
                 if re.search(r" (PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b", line) and line.startswith("tests/")}
        rec["stopped_in"] = [nodeid for nodeid in begun if nodeid not in done_]
    elif code == 5 and not rec["ran"]:
        rec["passed"] = True  # no test of the split fell in this share
    elif code != 0 or failed:
        rec["why"] = "a RIFE test failed" + (f": {', '.join(failed)}" if failed else f" (pytest exit {code})")
    elif rec["skipped"]:
        rec["why"] = "a RIFE test skipped; the lane is meant to measure them"
    else:
        rec["passed"] = True
    out.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    if rec["passed"]:
        print(f"cuda_lane: chunk {k}/{n} passed at {sha}, {len(rec['ran'])} tests in {rec['seconds']} s", flush=True)
        return 0
    print(f"cuda_lane: chunk {k}/{n}: {rec['why']} at {sha}", file=sys.stderr, flush=True)
    return 1


def verdict(where: Path, n: int) -> int:
    """run.json where every chunk 1..n passed at the pack's commit on the environment `prepare` made and together they
    ran every test it collected, each once; else the lane stops and writes no run.json."""
    where = where.resolve()
    sha = (where / "SOURCE_SHA").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        _fail(f"SOURCE_SHA is not a commit: {sha!r}")
    prepared = _prepared(where, sha)
    digest = sha256_of(where / "prepared.json")
    collected = {junit_key(nodeid): nodeid for nodeid in prepared.get("collected", [])}
    if not collected:
        _fail("prepared.json collected no test")
    seen: dict[str, str] = {}
    chunks = []
    left: list[str] | None = None
    for k in range(1, n + 1):
        path = where / f"chunk-{k}-of-{n}.json"
        if not path.is_file():
            _fail(f"no chunk {k}/{n} ({path.name}): run `cuda_lane.py test --chunk {k}/{n}`")
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("chunk") != f"{k}/{n}":
            _fail(f"{path.name} holds chunk {rec.get('chunk')}")
        if rec.get("commit") != sha:
            _fail(f"chunk {k}/{n} is for {rec.get('commit')}, not {sha}")
        if rec.get("prepared_sha256") != digest:
            _fail(f"chunk {k}/{n} ran on another environment than this pack's prepare made")
        if rec.get("skipped"):
            _fail(f"chunk {k}/{n} skipped {'; '.join(rec['skipped'])}")
        if not rec.get("passed"):
            _fail(f"chunk {k}/{n} did not pass ({rec.get('why')})")
        if left is None:
            left = sorted(rec.get("left", []))
        elif sorted(rec.get("left", [])) != left:
            _fail(f"chunk {k}/{n} left other tests than chunk 1/{n}")
        for key in rec.get("ran", {}):
            if key in seen:
                _fail(f"{collected.get(key, key)} ran in more than one chunk ({seen[key]} and {k}/{n})")
            if key not in collected:
                _fail(f"{key} ran but was not collected by prepare")
            seen[key] = f"{k}/{n}"
        chunks.append({"chunk": rec["chunk"], "tests": len(rec.get("ran", {})), "seconds": rec.get("seconds"),
                       "started": rec.get("started"), "pytest_summary": rec.get("pytest_summary")})
    left_keys = {junit_key(nodeid): nodeid for nodeid in left or []}
    for key, nodeid in left_keys.items():
        if key not in collected:
            _fail(f"{nodeid} was left but prepare did not collect it")
        if key in seen:
            _fail(f"{nodeid} was left and ran")
    missing = [nodeid for key, nodeid in collected.items() if key not in seen and key not in left_keys]
    if missing:
        _fail(f"no chunk ran {', '.join(missing)}")
    run = {"commit": sha, "gates": GATE_TESTS, "passed": True, "tests": len(collected), "ran": len(seen),
           "left": left or [], "chunks": chunks,
           "prepared_sha256": digest,
           **{key: prepared[key] for key in ("python", "gpu", "probe", "pip_freeze", "binary", "model")
              if key in prepared}}
    (where / "run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    print(f"cuda_lane: the GPU lane ({GATE_TESTS}) passed at {sha}: {len(seen)} of {len(collected)} tests in {n} "
          f"chunks, {round(sum(c['seconds'] or 0 for c in chunks))} s" +
          (f"; left, not run: {', '.join(left)}" if left else ""), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pack", help="check every package with safedeps, then pack HEAD for the host")
    p.add_argument("--requirements", type=Path, required=True,
                   help="every package the host installs, `name==version --hash=sha256:<hex>` a line")
    p.add_argument("--out", type=Path, required=True, help="an empty directory outside the checkout")
    p.add_argument("--host-spec", action="append", default=[], metavar="ECOSYSTEM:NAME@VERSION",
                   help="what the host installs for the lane apart from the list (its Python, say); checked the same way")
    p.add_argument("--safedeps", default="safedeps", help="the safedeps command (default: safedeps on PATH)")
    r = sub.add_parser("prepare", help="on the NVIDIA host: build the pack's environment and collect the RIFE tests")
    r.add_argument("--pack", type=Path, required=True, help="the directory `pack` wrote, copied to the host")
    r.add_argument("--wheels", type=Path, help="a directory of the list's wheels PyPI does not hold (PyTorch's CUDA "
                   "build for Windows, from PyTorch's index)")
    r.add_argument("--rife-zip", type=Path, help="the release zip `sprite-gen rife install` would download, already "
                   "downloaded (the engine checks its pinned SHA-256 either way)")
    t = sub.add_parser("test", help="on the NVIDIA host: run chunk K of N of the RIFE tests")
    t.add_argument("--pack", type=Path, required=True)
    t.add_argument("--chunk", required=True, metavar="K/N", help="share K of N (pytest --shard K/N)")
    t.add_argument("--budget", type=float, default=CHUNK_BUDGET,
                   help=f"seconds the chunk's tests may take before they are stopped (default {CHUNK_BUDGET})")
    t.add_argument("--leave", action="append", default=[], metavar="NODEID",
                   help="a test that cannot run within a chunk's budget, deselected by name; give the same to every chunk")
    v = sub.add_parser("verdict", help="run.json, where every chunk of one split passed and together ran every test")
    v.add_argument("--pack", type=Path, required=True)
    v.add_argument("--chunks", type=int, required=True, metavar="N", help="the split's N")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.command == "pack":
        pack(Path(__file__).resolve().parents[1], a.requirements, a.out, a.host_spec, [a.safedeps])
        return 0
    if a.command == "prepare":
        return prepare(a.pack, a.wheels, a.rife_zip)
    if a.command == "test":
        return run_chunk(a.pack, a.chunk, a.budget, a.leave)
    return verdict(a.pack, a.chunks)


if __name__ == "__main__":
    sys.exit(main())
