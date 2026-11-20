# SPDX-License-Identifier: Apache-2.0
"""The torch backend's CUDA gates, on the maintainer's NVIDIA host: the GPU run `scripts/linux_lane.sh` leaves to this
lane (docs/release.md). Nothing runs on push or pull request. Standard library only.

Two steps, on two machines:

    python3 scripts/cuda_lane.py pack --requirements <list> --out <empty dir> [--host-spec <eco>:<name>@<version> ...]
    <python> -I <pack>/cuda_lane.py run --pack <pack> [--wheels <dir>] [--rife-zip <zip>]

`pack` runs where a checkout of the commit being measured is (no uncommitted changes) and where safedeps is. The list
names every package the host will install, one `name==version --hash=sha256:<hex>` each: the closure of the tree's
`[dev]` extra and of PyTorch TORCH_VERSION's CUDA build, for the host's platform and Python PINNED_PYTHON. Each of
them (PyTorch by its version without the local label) and each --host-spec (what the host installs for the lane apart
from the list, such as its Python) is checked with `safedeps check`. An answer that is not `clean` or
`already_approved` for that very version, a check that did not finish, and a safedeps that could not be run each stop
`pack` before it writes anything: there is then nothing for the host to install. Once every check has passed, `pack`
writes HEAD's tree as `git archive` writes it (source.tar.gz), SOURCE_SHA, the list as checked (requirements.txt),
what each check said (checks.json) and this file as HEAD holds it.

`run` runs on the host, in that directory, with Python PINNED_PYTHON. It refuses a directory whose checks.json does not
hold a passing check of every line of its requirements.txt. Then, as the Linux lane installs a clean clone: a new
virtual environment, every wheel of the list downloaded with its hash checked (from PyPI, or from --wheels for one PyPI
does not hold, such as PyTorch's CUDA build for Windows, which comes from PyTorch's own index), installed from those
files alone, the tree (`pip install -e src[dev]`, nothing more fetched), `pip check`, and the model (`sprite-gen rife
install --backend torch --no-check`, into a directory of its own: the engine looks for no binary here, which is the
Linux lane's to measure). On Windows a path of WINDOWS_MAX_PATH characters or more cannot be written where long paths
are not enabled, so `run` refuses before installing anything when the longest name a wheel writes would not fit under
the virtual environment: the directory's path is then too long. Then:

  - the GPU and its driver (`nvidia-smi`), and PyTorch's CUDA (fails where PyTorch finds no GPU);
  - the install's check frame on CUDA (`sprite-gen rife install --backend torch` again, SPRITE_GEN_RIFE_DEVICE=cuda);
  - every test marked torch_rife and not real_rife, on CUDA, none skipped. Those marked both set this machine's binary
    beside the torch backend: the Linux lane runs them.

Nowhere the engine looks holds a binary: no SPRITE_GEN_* from outside, an empty user data directory (LOCALAPPDATA on
Windows, XDG_DATA_HOME elsewhere), no rife-ncnn-vulkan on PATH. TEMP, pip's cache and CUDA's cache point into the
directory, so the run writes nowhere else and deleting the directory removes everything it wrote. The run prints the
commit, the GPU, its driver, `pip freeze` and what each step said, keeps it in run.json and logs/, and exits 0 only
where every step passed. A commit has passed the CUDA lane where one run of it at that commit exited 0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import NoReturn

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
GATE_TESTS = "torch_rife and not real_rife"


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


def run(where: Path, wheels: Path | None, rife_zip: Path | None) -> int:
    """The steps of `run` above, in the pack at where: 0 where every step passed."""
    where = where.resolve()
    sha, _ = verify_pack(where)
    here = "%d.%d" % sys.version_info[:2]
    if here != PINNED_PYTHON:
        _fail(f"this is Python {here}; the lane pins {PINNED_PYTHON}")
    windows = os.name == "nt"
    src, venv, dl, rife_dir, tmp, logs = (where / name for name in ("src", "venv", "dl", "rife", "tmp", "logs"))
    made = [p.name for p in (src, venv, dl, rife_dir, tmp, logs) if p.exists()]
    if made:
        _fail(f"{where} already holds {', '.join(made)}; run each pack once")
    for d in (src, dl, rife_dir, tmp, logs):
        d.mkdir()
    bindir = venv / ("Scripts" if windows else "bin")
    py, cli = bindir / ("python.exe" if windows else "python"), bindir / ("sprite-gen.exe" if windows else "sprite-gen")
    data_key = "LOCALAPPDATA" if windows else "XDG_DATA_HOME"
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("SPRITE_GEN_")}
    env.update(TEMP=str(tmp), TMP=str(tmp), TMPDIR=str(tmp), PIP_CACHE_DIR=str(where / "cache" / "pip"),
               CUDA_CACHE_PATH=str(where / "cache" / "cuda"), PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1",
               PIP_CONFIG_FILE=os.devnull, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    env[data_key] = tempfile.mkdtemp(prefix="data-install-", dir=tmp)
    rec: dict = {"commit": sha, "python": sys.version, "steps": [], "passed": False}

    def done(why: str | None = None) -> int:
        if why:
            rec["why"] = why
        (where / "run.json").write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
        if rec["passed"]:
            print(f"cuda_lane: the torch backend's CUDA gates passed at {sha}", flush=True)
            return 0
        print(f"cuda_lane: {why} at {sha}", file=sys.stderr, flush=True)
        return 1

    def step(name: str, argv: list[str], log: str, show: bool = True, cwd: Path | None = None,
             extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        print(f"== {name}", flush=True)
        t0 = time.monotonic()
        proc = subprocess.run(argv, cwd=cwd, env={**env, **(extra or {})}, capture_output=True, text=True,
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
    model = step("the model (sprite-gen rife install --backend torch --no-check)",
                 [str(cli), "rife", "install", "--backend", "torch", "--dir", str(rife_dir), "--no-check", *model_args],
                 "model.log", show=False)
    if model.returncode:
        return done("the model could not be installed")
    installed = json.loads(model.stdout)
    rec["model"] = str(Path(installed["dir"]) / installed["model"])

    # Nowhere the engine looks for the binary holds one: an empty data directory, nothing on PATH, no SPRITE_GEN_RIFE.
    data = tempfile.mkdtemp(prefix="data-", dir=tmp)
    env.update(PATH=os.pathsep.join([str(bindir), env.get("PATH", "")]), SPRITE_GEN_RIFE_MODEL=rec["model"],
               SPRITE_GEN_RIFE_DEVICE="cuda")
    env[data_key] = data
    rec["env"] = {k: env[k] for k in (data_key, "SPRITE_GEN_RIFE_MODEL", "SPRITE_GEN_RIFE_DEVICE", "TEMP",
                                      "PIP_CACHE_DIR", "CUDA_CACHE_PATH")}
    on_path = shutil.which("rife-ncnn-vulkan", path=env["PATH"])
    if on_path:
        return done(f"rife-ncnn-vulkan is on PATH ({on_path}); the torch gates run where no binary is")
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
                             "torch.cuda.is_available() else None)"], "probe.log", cwd=src)
    rec["probe"] = probe.stdout.strip()
    if probe.returncode != 0 or "gpu None" in probe.stdout:
        return done("PyTorch finds no CUDA GPU here")
    if step("RIFE torch backend (sprite-gen rife install --backend torch, its check frame on CUDA)",
            [str(cli), "rife", "install", "--backend", "torch", "--dir", str(rife_dir)], "check-frame.log",
            cwd=src).returncode:
        return done("the install's check frame failed")
    # --full: every test, those marked slow too (a plain pytest is the fast bundle). No test marked: pytest exits 5
    # ("no tests ran"), and the lane with it.
    marked = step(f"RIFE torch tests on CUDA (every test marked {GATE_TESTS}, none skipped)",
                  [str(py), "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", "--full", "--basetemp",
                   tempfile.mkdtemp(prefix="pytest-", dir=tmp), "-m", GATE_TESTS], "pytest.log", cwd=src)
    rec["pytest_summary"] = ([line for line in marked.stdout.splitlines() if line.strip()] or [""])[-1]
    rec["skipped"] = [line for line in marked.stdout.splitlines() if line.startswith("SKIPPED")]
    if marked.returncode != 0:
        return done("a torch_rife test failed")
    if rec["skipped"]:
        return done("a torch_rife test skipped; this run is meant to measure them")
    rec["data_dir_after"] = sorted(p.name for p in Path(data).iterdir())
    rec["passed"] = True
    return done()


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
    r = sub.add_parser("run", help="on the NVIDIA host: build the pack's environment and run the gates")
    r.add_argument("--pack", type=Path, required=True, help="the directory `pack` wrote, copied to the host")
    r.add_argument("--wheels", type=Path, help="a directory of the list's wheels PyPI does not hold (PyTorch's CUDA "
                   "build for Windows, from PyTorch's index)")
    r.add_argument("--rife-zip", type=Path, help="the release zip `sprite-gen rife install` would download, already "
                   "downloaded (the engine checks its pinned SHA-256 either way)")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.command == "pack":
        pack(Path(__file__).resolve().parents[1], a.requirements, a.out, a.host_spec, [a.safedeps])
        return 0
    return run(a.pack, a.wheels, a.rife_zip)


if __name__ == "__main__":
    sys.exit(main())
