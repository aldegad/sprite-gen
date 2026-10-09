# SPDX-License-Identifier: Apache-2.0
"""`scripts/linux_lane.sh --rife-unmeasured` runs the suite where no RIFE is reachable, and the tests
that need one skip; `--rife-only` runs the tests marked `real_rife` (`pytest -m real_rife`), none
skipped. A test that skips without RIFE and is not marked is measured by neither run.

Its collection: the suite is collected twice, with nothing else changed, once with a stand-in RIFE
everywhere `rife.locate()` looks (SPRITE_GEN_RIFE, PATH, the install root) and once with none. Every
test the two collections skip, or collect, differently is decided by RIFE, and must carry the mark.

Its run: a fixture or the test body can skip it, which no collection reads, so the RIFE-less run itself
(`pytest --rife-unmeasured`, tests/conftest.py) fails each skip of a test that is not marked, unless it
is named as skipping for another reason. Each way an unmarked test can skip is run through it here."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

from sprite_gen.video import rife

ROOT = Path(__file__).resolve().parents[2]

# Loaded into each collection (-p): per collected test, whether its skip and skipif marks skip it —
# pytest's own reading, the mark tests/conftest.py adds included — and whether it is marked real_rife.
PROBE = '''
import json, os
from _pytest.skipping import evaluate_skip_marks


def pytest_collection_finish(session):
    with open(os.environ["RIFE_PROBE_OUT"], "w") as out:
        json.dump({item.nodeid: [evaluate_skip_marks(item) is not None, item.get_closest_marker("real_rife") is not None]
                   for item in session.items}, out)
'''


def _without_rife(path: str, shadow: Path) -> str:
    """PATH with each directory that holds RIFE swapped for links to everything else in it."""
    dirs = []
    for i, d in enumerate(path.split(os.pathsep)):
        found = shutil.which(rife.BINARY, path=d) if d else None
        if found:
            d = shadow / str(i)
            d.mkdir(parents=True)
            for entry in Path(found).parent.iterdir():
                if entry.name != Path(found).name:
                    (d / entry.name).symlink_to(entry)
        dirs.append(str(d))
    return os.pathsep.join(dirs)


def _stand_in(data: Path) -> Path:
    """An executable named as RIFE, with its model beside it, at the install root under `data`: what
    `rife.locate()` checks. Never run — the collections run no test."""
    binary = rife.installed_binary(data / "rife")
    (binary.parent / rife.MODEL).mkdir(parents=True)
    for name in ("flownet.param", "flownet.bin"):
        (binary.parent / rife.MODEL / name).write_bytes(b"")
    binary.write_text("#!/bin/sh\nexit 1\n")
    binary.chmod(0o755)
    return binary


def _rife_less_env(tmp_path: Path) -> dict[str, str]:
    """This environment with nothing where `rife.locate()` looks — no SPRITE_GEN_RIFE(_MODEL), no RIFE on
    PATH, an empty data directory — and no PYTEST_ADDOPTS selecting a part of a run."""
    env = {k: v for k, v in os.environ.items() if k not in ("SPRITE_GEN_RIFE", "SPRITE_GEN_RIFE_MODEL", "PYTEST_ADDOPTS")}
    env["PATH"] = _without_rife(env.get("PATH", ""), tmp_path / "path")
    env["SPRITE_GEN_DATA_DIR"] = str(tmp_path / "without")
    return env


def _collect(tmp_path: Path, name: str, env: dict[str, str]) -> dict[str, list[bool]]:
    out = tmp_path / f"{name}.json"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-qq", "-p", "rife_probe", "-p", "no:cacheprovider"],
        cwd=ROOT,
        env={**env, "RIFE_PROBE_OUT": str(out)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"collection with {name} failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    return json.loads(out.read_text())


def test_every_test_rife_decides_is_marked_real_rife(tmp_path):
    (tmp_path / "rife_probe.py").write_text(PROBE)
    # The whole suite, as the lane collects it: no PYTEST_ADDOPTS selecting a part.
    base = _rife_less_env(tmp_path)
    base["PYTHONPATH"] = os.pathsep.join(filter(None, [str(tmp_path), base.get("PYTHONPATH")]))
    stand_in = _stand_in(tmp_path / "with")
    absent = _collect(tmp_path, "no RIFE", base)
    present = _collect(tmp_path, "a stand-in RIFE", {
        **base,
        "SPRITE_GEN_DATA_DIR": str(tmp_path / "with"),
        "SPRITE_GEN_RIFE": str(stand_in),
        "PATH": os.pathsep.join([str(stand_in.parent), base["PATH"]]),
    })
    decided = (present.keys() ^ absent.keys()) | {n for n in present.keys() & absent.keys() if present[n][0] != absent[n][0]}
    unmarked = sorted(n for n in decided if not (present.get(n) or absent[n])[1])
    assert not unmarked, (
        "skipped or collected by whether RIFE is reachable, but not marked real_rife — "
        "`scripts/linux_lane.sh --rife-only` would not run them:\n" + "\n".join(unmarked))
    assert decided, "no test is decided by RIFE: the stand-in or the conftest skip no longer reaches the marked tests"


# Each way a test can skip where no RIFE is reachable: by its mark, which `--rife-only` runs, and,
# unmarked, by a skip mark, in a fixture, in its body, and with its whole file.
GUARDS = '''
import pytest

from sprite_gen.video import rife


def _found():
    try:
        rife.locate()
    except rife.RifeUnavailable:
        return False
    return True


@pytest.fixture
def interpolate():
    try:
        return rife.Rife()
    except rife.RifeUnavailable:
        pytest.skip("rife-ncnn-vulkan not installed")


@pytest.mark.real_rife
def test_marked():
    rife.Rife()


@pytest.mark.skipif(not _found(), reason="rife-ncnn-vulkan not installed")
def test_skip_mark():
    pass


def test_fixture_skip(interpolate):
    pass


def test_body_skip():
    try:
        rife.locate()
    except rife.RifeUnavailable:
        pytest.skip("rife-ncnn-vulkan not installed")
'''

GUARDED_FILE = '''
import pytest

from sprite_gen.video import rife

try:
    rife.locate()
except rife.RifeUnavailable:
    pytest.skip("rife-ncnn-vulkan not installed", allow_module_level=True)


def test_in_a_skipped_file():
    pass
'''


def test_the_rife_less_run_fails_each_unmarked_skip_by_name(tmp_path):
    probes = {"test_guards.py": GUARDS, "test_guarded_file.py": GUARDED_FILE}
    for name, text in probes.items():
        (tmp_path / name).write_text(text)
    report = tmp_path / "report.xml"
    # This repository's configuration, and tests/conftest.py as the one conftest (-p), over files outside
    # the suite; the skipped file's error does not end the run before the other tests.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--rife-unmeasured", "-c", str(ROOT / "pyproject.toml"), "--rootdir", str(tmp_path),
         "--noconftest", "-p", "conftest", "-p", "no:cacheprovider", "--continue-on-collection-errors",
         f"--junitxml={report}", *(str(tmp_path / name) for name in probes)],
        cwd=ROOT,
        env=_rife_less_env(tmp_path),
        capture_output=True,
        text=True,
    )
    output = f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    assert report.is_file(), output
    outcomes = {case.get("name"): [e.tag for e in case if e.tag in ("skipped", "failure", "error")]
                for case in ElementTree.parse(report).iter("testcase")}
    assert outcomes == {
        "test_marked": ["skipped"],  # tests/conftest.py's skip, which --rife-only runs
        "test_skip_mark": ["error"],  # skipped in setup, failed there
        "test_fixture_skip": ["error"],
        "test_body_skip": ["failure"],
        "test_guarded_file": ["error"],  # the file's collection
    }, output
    assert proc.returncode == 1, output
