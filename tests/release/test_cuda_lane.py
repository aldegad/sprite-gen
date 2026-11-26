# SPDX-License-Identifier: Apache-2.0
"""The GPU lane (`scripts/cuda_lane.py`, docs/release.md) runs every RIFE test on the maintainer's NVIDIA host: the
binary on its Vulkan, the torch backend on CUDA, which the Linux lane (`scripts/linux_lane.sh`) on GPU-less runners
leaves to it. It installs the Python the Linux lane does and the PyTorch the torch extra was measured on.

The host installs nothing safedeps has not passed at that very version: `pack` stops at the first check that is not a
pass, a check that did not finish among them, before it writes anything, and `prepare` refuses a pack that holds no
passing check of every package before it makes anything. On Windows the run's directory must leave room under the
virtual environment for the longest name a wheel writes. safedeps is stood in by a script that answers as safedeps does.

The tests run in chunks, each one call under the remote machines' ten minutes: a chunk is stopped at its budget, with
everything it started, and says so; it reads which tests ran from pytest's own report; and the verdict is a pass only
where every chunk of one split passed at the pack's commit, on the environment `prepare` made, and together they ran
every test `prepare` collected, each once."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import cuda_lane  # noqa: E402

TORCH = cuda_lane.TORCH_VERSION
LIST = f"""# every package the host installs, as `pack` gets it
numpy==2.5.5 --hash=sha256:{'1' * 64}
pillow==13.0.0 \\
    --hash=sha256:{'2' * 64} \\
    --hash=sha256:{'3' * 64}
torch=={TORCH}+cu130 --hash=sha256:{'4' * 64}  # PyTorch's CUDA build for Windows
"""
PINS = [("numpy", "2.5.5"), ("pillow", "13.0.0"), ("torch", f"{TORCH}+cu130")]
HOST = ["nuget:python@3.14.10"]
CHECKED = ["pypi numpy@2.5.5", "pypi pillow@13.0.0", f"pypi torch@{TORCH}", "nuget python@3.14.10"]

# Answers as `safedeps check <ecosystem> <name>@<version> --json` gives them: one JSON line, then its exit status (0 clean
# or approved, 2 an advisory with no fix, 3 one exploited in the wild, 4 an error, no advisory source, a check that did
# not finish). A spec not named answers clean. Each spec asked is logged beside the answers.
SAFEDEPS = """
import json, sys
answers, check, ecosystem, spec, flag = sys.argv[1:6]
assert (check, flag) == ("check", "--json"), sys.argv
with open(answers + ".asked", "a", encoding="utf-8") as asked:
    asked.write(ecosystem + " " + spec + "\\n")
name, version = spec.rsplit("@", 1)
status, report = json.loads(open(answers, encoding="utf-8").read()).get(
    ecosystem + " " + spec, [0, {"result": "clean", "resolved_version": version}])
if report is not None:
    print(json.dumps({"command": "check", "ecosystem": ecosystem, "package": name, "input_range": version, **report}))
sys.exit(status)
"""


def _pinned(text: str, name: str) -> str:
    found = re.findall(rf'^{name}\s*=\s*"?([^"\s]+)"?$', text, re.MULTILINE)
    assert len(found) == 1, f"{name} is pinned on one line of its own, found {found}"
    return found[0]


def test_the_cuda_lane_installs_the_python_the_linux_lane_does_and_the_pytorch_the_extra_was_measured_on():
    linux = (ROOT / "scripts" / "linux_lane.sh").read_text(encoding="utf-8")
    cuda = (ROOT / "scripts" / "cuda_lane.py").read_text(encoding="utf-8")
    extra = re.search(r'^torch = \["torch>=([^,"]+),', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE)
    assert extra, "pyproject.toml's torch extra names its floor"
    assert _pinned(cuda, "TORCH_VERSION") == extra.group(1)
    assert _pinned(cuda, "PINNED_PYTHON") == _pinned(linux, "PINNED_PYTHON")


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A clean checkout of one commit holding the lane script."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "cuda_lane.py").write_bytes((ROOT / "scripts" / "cuda_lane.py").read_bytes())
    (root / "README.md").write_text("a tree\n", encoding="utf-8")
    git = ["git", "-C", str(root), "-c", "user.name=lane", "-c", "user.email=lane@example.invalid", "-c",
           "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "a commit"], check=True)
    return root


def _pack(checkout: Path, tmp_path: Path, answers: dict, safedeps: list[str] | None = None) -> str:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(LIST, encoding="utf-8")
    (tmp_path / "answers.json").write_text(json.dumps(answers), encoding="utf-8")
    (tmp_path / "safedeps.py").write_text(SAFEDEPS, encoding="utf-8")
    safedeps = safedeps or [sys.executable, "-I", str(tmp_path / "safedeps.py"), str(tmp_path / "answers.json")]
    return cuda_lane.pack(checkout, requirements, tmp_path / "pack", HOST, safedeps)


def _asked(tmp_path: Path) -> list[str]:
    asked = tmp_path / "answers.json.asked"
    return asked.read_text(encoding="utf-8").splitlines() if asked.exists() else []


NOT_A_PASS = {
    "a check that did not finish": (4, {"result": "error", "approved": False, "error": "check_did_not_finish"}),
    "an error": (4, {"result": "error", "error": "version_resolution_failed"}),
    "no advisory source": (4, {"result": "provider_unavailable", "resolved_version": "13.0.0"}),
    "a fixed version to take instead": (0, {"result": "patched_available", "resolved_version": "13.0.0",
                                            "suggested_spec": "13.0.1"}),
    "an advisory with no fix": (2, {"result": "cve_unpatched", "resolved_version": "13.0.0"}),
    "an advisory exploited in the wild": (3, {"result": "kev_hard_block", "resolved_version": "13.0.0"}),
    "a pass of another version": (0, {"result": "already_approved", "resolved_version": "13.0.1"}),
    "a pass that exits 4": (4, {"result": "clean", "resolved_version": "13.0.0"}),
    "no report": (0, None),
}


@pytest.mark.parametrize("answer", NOT_A_PASS.values(), ids=NOT_A_PASS.keys())
def test_pack_stops_at_a_check_that_is_not_a_pass_and_writes_nothing(checkout, tmp_path, capsys, answer):
    with pytest.raises(SystemExit) as stopped:
        _pack(checkout, tmp_path, {"pypi pillow@13.0.0": answer})
    assert stopped.value.code == 1
    assert not (tmp_path / "pack").exists()
    assert _asked(tmp_path) == CHECKED[:2], "nothing past the first check that is not a pass is checked"
    said = capsys.readouterr().err
    assert f"safedeps check pypi pillow@13.0.0 answered exit {answer[0]}, " in said
    assert "nothing is packed and the host installs nothing" in said


def test_pack_stops_where_the_check_of_the_host_s_own_python_did_not_finish(checkout, tmp_path, capsys):
    (tmp_path / "pack").mkdir()
    with pytest.raises(SystemExit):
        _pack(checkout, tmp_path, {"nuget python@3.14.10": NOT_A_PASS["a check that did not finish"]})
    assert list((tmp_path / "pack").iterdir()) == []
    assert _asked(tmp_path) == CHECKED
    assert "safedeps check nuget python@3.14.10 answered exit 4, error (check_did_not_finish)" in capsys.readouterr().err


def test_pack_stops_where_safedeps_cannot_be_run(checkout, tmp_path, capsys):
    with pytest.raises(SystemExit):
        _pack(checkout, tmp_path, {}, safedeps=[str(tmp_path / "no-safedeps-here")])
    assert not (tmp_path / "pack").exists()
    assert "safedeps could not be run to check pypi numpy@2.5.5" in capsys.readouterr().err


def test_pack_refuses_a_checkout_with_uncommitted_changes_before_checking_anything(checkout, tmp_path, capsys):
    (checkout / "README.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _pack(checkout, tmp_path, {})
    assert _asked(tmp_path) == []
    assert not (tmp_path / "pack").exists()
    assert "the checkout has uncommitted changes" in capsys.readouterr().err


def test_pack_writes_the_tree_the_list_the_checks_and_the_script_once_every_check_passed(checkout, tmp_path):
    sha = _pack(checkout, tmp_path, {})
    out = tmp_path / "pack"
    head = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()
    assert sha == head
    assert sorted(p.name for p in out.iterdir()) == sorted(cuda_lane.PACK_FILES)
    assert (out / "SOURCE_SHA").read_text(encoding="ascii") == f"{sha}\n"
    assert (out / "requirements.txt").read_text(encoding="utf-8") == LIST
    assert (out / "cuda_lane.py").read_bytes() == (checkout / "scripts" / "cuda_lane.py").read_bytes()
    with tarfile.open(out / "source.tar.gz") as tree:
        assert {"README.md", "scripts/cuda_lane.py"} <= set(tree.getnames())
    record = json.loads((out / "checks.json").read_text(encoding="utf-8"))
    assert record["commit"] == sha
    assert [(c["spec"], c["exit"], c["result"]) for c in record["checks"]] == [(s, 0, "clean") for s in CHECKED]
    assert _asked(tmp_path) == CHECKED
    assert cuda_lane.verify_pack(out) == (sha, PINS)


def _drop_pillow(out: Path) -> None:
    record = json.loads((out / "checks.json").read_text(encoding="utf-8"))
    record["checks"] = [c for c in record["checks"] if c["spec"] != "pypi pillow@13.0.0"]
    (out / "checks.json").write_text(json.dumps(record), encoding="utf-8")


def _pillow_did_not_finish(out: Path) -> None:
    record = json.loads((out / "checks.json").read_text(encoding="utf-8"))
    for c in record["checks"]:
        if c["spec"] == "pypi pillow@13.0.0":
            c.update(exit=4, result="error", error="check_did_not_finish")
    (out / "checks.json").write_text(json.dumps(record), encoding="utf-8")


def _list_grown(out: Path) -> None:
    with (out / "requirements.txt").open("a", encoding="utf-8") as f:
        f.write(f"filelock==4.2.12 --hash=sha256:{'5' * 64}\n")


SPOILED = {
    "a package with no check": (_drop_pillow, "checks.json holds no passing check of pillow==13.0.0"),
    "a check that did not finish": (_pillow_did_not_finish, "checks.json holds no passing check of pillow==13.0.0"),
    "a list changed after its check": (_list_grown, "requirements.txt is not the list that was checked"),
}


@pytest.mark.parametrize("spoiled", SPOILED.values(), ids=SPOILED.keys())
def test_prepare_refuses_a_pack_without_a_passing_check_of_every_package_before_it_makes_anything(checkout, tmp_path,
                                                                                                   capsys, spoiled):
    spoil, why = spoiled
    _pack(checkout, tmp_path, {})
    out = tmp_path / "pack"
    spoil(out)
    capsys.readouterr()
    with pytest.raises(SystemExit) as stopped:
        cuda_lane.prepare(out, None, None)
    assert stopped.value.code == 1
    assert why in capsys.readouterr().err
    assert sorted(p.name for p in out.iterdir()) == sorted(cuda_lane.PACK_FILES), "prepare made nothing"


@pytest.mark.parametrize("text, why", [
    ("numpy==2.5.5\n", "requirements line 1 is not"),
    (f"--index-url https://example.invalid/simple\n{LIST}", "requirements line 1 is not"),
    (f"-e .\n{LIST}", "requirements line 1 is not"),
    (f"numpy>=2.5 --hash=sha256:{'1' * 64}\n", "requirements line 1 is not"),
    (f"numpy==2.5.5 --hash=sha256:{'1' * 64}\n", f"requirements must pin torch {TORCH}"),
    (f"{LIST}NumPy==2.5.5 --hash=sha256:{'6' * 64}\n", "requirements name numpy more than once"),
], ids=["no hash", "an option", "an editable", "a range", "no torch", "a name twice"])
def test_the_list_pins_every_package_with_its_hash_and_pytorch_too(capsys, text, why):
    with pytest.raises(SystemExit):
        cuda_lane.read_pins(text)
    assert why in capsys.readouterr().err


def _wheel(path: Path, *members: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for member in members:
            z.writestr(member, b"")
    return path


def test_on_windows_the_run_directory_leaves_room_for_the_longest_name_a_wheel_writes(tmp_path):
    # As long as the longest name in PyTorch 2.13.0+cu130's Windows wheel (a licence file under its dist-info).
    folder = f"torch-{TORCH}+cu130.dist-info/licenses/"
    longest = folder + "x" * (173 - len(folder) - len(".txt")) + ".txt"
    assert len(longest) == 173
    wheels = [_wheel(tmp_path / "torch.whl", "torch/__init__.py", folder, longest),
              _wheel(tmp_path / "small.whl", "small/__init__.py")]
    tail = "\\venv\\Lib\\site-packages"

    def site_packages(stem_length: int) -> str:
        return "D:\\" + "r" * stem_length + tail

    # A path of 259 characters fits, one of 260 does not (MAX_PATH counts the closing null).
    fits = site_packages(259 - len("D:\\") - len(tail) - 1 - 173)
    length, path = cuda_lane.longest_install_path(fits, wheels)
    assert (length, path) == (259, fits + "\\" + longest.replace("/", "\\"))
    assert cuda_lane.path_refusal(fits, wheels) is None
    over = site_packages(260 - len("D:\\") - len(tail) - 1 - 173)
    assert cuda_lane.path_refusal(over, wheels) == (
        f"the longest path the wheels write is 260 characters, {over}\\{longest.replace('/', chr(92))}; Windows writes "
        "none of 260 or more where long paths are not enabled: run from a directory whose path is shorter by at least "
        "1 character")
    # Where a run once stopped (WinError 206): the run directory named by the whole commit.
    deep = "D:\\lane\\sprite-gen-cuda\\runs\\" + "0" * 40 + tail
    assert "run from a directory whose path is shorter by at least 7 characters" in cuda_lane.path_refusal(deep, wheels)
    assert cuda_lane.path_refusal("D:\\lane\\r\\0000000" + tail, wheels) is None


@pytest.mark.parametrize("text, chunk", [("1/6", (1, 6)), ("6/6", (6, 6)), ("1/1", (1, 1))])
def test_a_chunk_is_one_of_n(text, chunk):
    assert cuda_lane.parse_chunk(text) == chunk


@pytest.mark.parametrize("text", ["0/6", "7/6", "6", "a/6", "1/0", "-1/6", " 1/6"])
def test_a_chunk_outside_its_split_is_refused(text, capsys):
    with pytest.raises(SystemExit):
        cuda_lane.parse_chunk(text)
    assert f"--chunk {text} is not K/N" in capsys.readouterr().err


JUNIT = """<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="5">
<testcase classname="tests.video.test_rife_cuts" name="test_the_public_cut[pip-run-ncnn]" time="181.5" />
<testcase classname="tests.video.test_rife_torch.TestAlike" name="test_alike[rife-v4.25-mid50]" time="2.25" />
<testcase classname="tests.video.test_rife" name="test_fails" time="1.0"><failure message="no">x</failure></testcase>
<testcase classname="tests.video.test_rife" name="test_breaks" time="0.5"><error message="setup">y</error></testcase>
<testcase classname="tests.video.test_rife" name="test_skips" time="0.0"><skipped message="no RIFE" /></testcase>
</testsuite></testsuites>"""


def test_a_chunk_reads_which_tests_ran_and_how_from_pytest_s_own_report():
    ran = cuda_lane.junit_ran(JUNIT)
    assert ran == {
        cuda_lane.junit_key("tests/video/test_rife_cuts.py::test_the_public_cut[pip-run-ncnn]"): ["passed", 181.5],
        cuda_lane.junit_key("tests/video/test_rife_torch.py::TestAlike::test_alike[rife-v4.25-mid50]"): ["passed", 2.25],
        cuda_lane.junit_key("tests/video/test_rife.py::test_fails"): ["failed", 1.0],
        cuda_lane.junit_key("tests/video/test_rife.py::test_breaks"): ["error", 0.5],
        cuda_lane.junit_key("tests/video/test_rife.py::test_skips"): ["skipped", 0.0],
    }
    assert len({cuda_lane.junit_key(n) for n in ("tests/a/test_b.py::test_c", "tests/a/test_b.py::T::test_c",
                                                 "tests/a_b/test_c.py::test_c")}) == 3


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in reads a process with signal 0")
def test_a_command_past_its_budget_is_stopped_with_every_process_it_started(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    grandchild = "import time; time.sleep(120)"
    child = (f"import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
             f"open({str(pid_file)!r}, 'w').write(str(p.pid)); print('started', flush=True); time.sleep(120)")
    started = time.monotonic()
    code, said, over = cuda_lane.run_bounded([sys.executable, "-c", child], budget=3, env=None, cwd=None)
    assert over and code is None
    assert "started" in said
    assert time.monotonic() - started < 30
    pid = int(pid_file.read_text())
    stat = Path(f"/proc/{pid}/stat")
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        # Where nobody reaps an orphan (a container's first process), the ended grandchild stays a zombie.
        try:
            if stat.read_text().rpartition(")")[2].split()[0] == "Z":
                break
        except OSError:
            pass
        time.sleep(0.1)
    else:
        pytest.fail(f"the grandchild {pid} outlived the budget")
    code, said, over = cuda_lane.run_bounded([sys.executable, "-c", "print('quick')"], budget=30, env=None, cwd=None)
    assert (code, said.strip(), over) == (0, "quick", False)


def test_a_chunk_refuses_a_pack_that_was_not_prepared(checkout, tmp_path, capsys):
    _pack(checkout, tmp_path, {})
    out = tmp_path / "pack"
    capsys.readouterr()
    with pytest.raises(SystemExit) as stopped:
        cuda_lane.run_chunk(out, "1/2", 540)
    assert stopped.value.code == 1
    assert "was not prepared: run `cuda_lane.py prepare` first" in capsys.readouterr().err
    assert sorted(p.name for p in out.iterdir()) == sorted(cuda_lane.PACK_FILES), "the chunk made nothing"


COLLECTED = ["tests/video/test_rife_cuts.py::test_the_public_cut[pip-run-ncnn]",
             "tests/video/test_rife_torch.py::TestAlike::test_alike[rife-v4.25-mid50]",
             "tests/video/test_rife.py::test_melts"]
SHA = "a" * 40


def _prepared(tmp_path: Path, chunks: dict[int, list[str]], n: int | None = None) -> Path:
    """A pack prepared at SHA that collected COLLECTED, with chunk K of the split running chunks[K], each passed."""
    where = tmp_path / "lane"
    where.mkdir()
    (where / "SOURCE_SHA").write_text(SHA + "\n", encoding="ascii")
    prepared = {"commit": SHA, "gates": cuda_lane.GATE_TESTS, "prepared": True, "collected": COLLECTED,
                "gpu": "NVIDIA GeForce RTX 3060, 617.14, 12288 MiB", "probe": "torch 2.13.0+cu130 cuda 13.0"}
    (where / "prepared.json").write_text(json.dumps(prepared), encoding="utf-8")
    n = n or len(chunks)
    for k, nodeids in chunks.items():
        rec = {"commit": SHA, "gates": cuda_lane.GATE_TESTS, "chunk": f"{k}/{n}",
               "prepared_sha256": cuda_lane.sha256_of(where / "prepared.json"), "seconds": 300.0, "budget": 540,
               "ran": {cuda_lane.junit_key(i): ["passed", 100.0] for i in nodeids}, "skipped": [], "passed": True,
               "pytest_summary": f"{len(nodeids)} passed"}
        (where / f"chunk-{k}-of-{n}.json").write_text(json.dumps(rec), encoding="utf-8")
    return where


def _chunk(where: Path, k: int, n: int, **change) -> None:
    path = where / f"chunk-{k}-of-{n}.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec.update(change)
    path.write_text(json.dumps(rec), encoding="utf-8")


def test_the_verdict_is_a_pass_where_every_chunk_of_one_split_passed_and_together_they_ran_every_test(tmp_path):
    import release_gates

    where = _prepared(tmp_path, {1: COLLECTED[:1], 2: COLLECTED[1:]})
    assert cuda_lane.verdict(where, 2) == 0
    rec = release_gates.read_run(where / "run.json")
    assert (rec["commit"], rec["gates"], rec["passed"]) == (SHA, "real_rife or torch_rife", True)
    assert rec["gpu"] == "NVIDIA GeForce RTX 3060, 617.14, 12288 MiB"
    assert [c["chunk"] for c in rec["chunks"]] == ["1/2", "2/2"]


def _not_prepared(where: Path) -> None:
    (where / "prepared.json").write_text(json.dumps({"commit": SHA, "prepared": False, "why": "no GPU"}),
                                         encoding="utf-8")


SPOILED_CHUNKS = {
    "a chunk missing": (lambda w: (w / "chunk-2-of-2.json").unlink(), "no chunk 2/2"),
    "a chunk that failed": (lambda w: _chunk(w, 2, 2, passed=False, why="a RIFE test failed"),
                            "chunk 2/2 did not pass (a RIFE test failed)"),
    "a chunk that skipped one": (lambda w: _chunk(w, 2, 2, skipped=["SKIPPED [1] no RIFE"]), "chunk 2/2 skipped"),
    "a chunk at another commit": (lambda w: _chunk(w, 2, 2, commit="b" * 40), f"chunk 2/2 is for {'b' * 40}"),
    "a chunk of another environment": (lambda w: _chunk(w, 2, 2, prepared_sha256="0" * 64),
                                       "chunk 2/2 ran on another environment than this pack's prepare made"),
    "a chunk of another split": (lambda w: _chunk(w, 2, 2, chunk="2/3"), "chunk-2-of-2.json holds chunk 2/3"),
    "a test no chunk ran": (lambda w: _chunk(w, 2, 2, ran={cuda_lane.junit_key(COLLECTED[1]): ["passed", 1.0]}),
                            f"no chunk ran {COLLECTED[2]}"),
    "a test two chunks ran": (lambda w: _chunk(w, 2, 2, ran={cuda_lane.junit_key(i): ["passed", 1.0]
                                                              for i in COLLECTED}), f"{COLLECTED[0]} ran in more than one chunk"),
    "a test prepare did not collect": (lambda w: _chunk(w, 2, 2, ran={cuda_lane.junit_key(i): ["passed", 1.0]
                                                                       for i in [*COLLECTED[1:], "tests/x.py::test_y"]}),
                                       "tests.x::test_y ran but was not collected"),
    "an environment not prepared": (_not_prepared, "was not prepared: run `cuda_lane.py prepare` first"),
}


@pytest.mark.parametrize("spoiled", SPOILED_CHUNKS.values(), ids=SPOILED_CHUNKS.keys())
def test_the_verdict_refuses_chunks_that_do_not_make_one_whole_pass_and_writes_no_run_record(tmp_path, capsys, spoiled):
    spoil, why = spoiled
    where = _prepared(tmp_path, {1: COLLECTED[:1], 2: COLLECTED[1:]})
    spoil(where)
    with pytest.raises(SystemExit) as stopped:
        cuda_lane.verdict(where, 2)
    assert stopped.value.code == 1
    assert why in capsys.readouterr().err
    assert not (where / "run.json").exists()
