# SPDX-License-Identifier: Apache-2.0
"""The GPU lane (`scripts/cuda_lane.py`, docs/release.md) runs every RIFE test on the maintainer's NVIDIA host: the
binary on its Vulkan, the torch backend on CUDA, which the Linux lane (`scripts/linux_lane.sh`) on GPU-less runners
leaves to it. It installs the Python the Linux lane does and the PyTorch the torch extra was measured on.

The host installs nothing safedeps has not passed at that very version: `pack` stops at the first check that is not a
pass, a check that did not finish among them, before it writes anything, and `run` refuses a pack that holds no passing
check of every package before it makes anything. On Windows the run's directory must leave room under the virtual
environment for the longest name a wheel writes. safedeps is stood in by a script that answers as safedeps does."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tarfile
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
def test_run_refuses_a_pack_without_a_passing_check_of_every_package_before_it_makes_anything(checkout, tmp_path,
                                                                                               capsys, spoiled):
    spoil, why = spoiled
    _pack(checkout, tmp_path, {})
    out = tmp_path / "pack"
    spoil(out)
    capsys.readouterr()
    with pytest.raises(SystemExit) as stopped:
        cuda_lane.run(out, None, None)
    assert stopped.value.code == 1
    assert why in capsys.readouterr().err
    assert sorted(p.name for p in out.iterdir()) == sorted(cuda_lane.PACK_FILES), "run made nothing"


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
