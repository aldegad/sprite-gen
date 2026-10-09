# SPDX-License-Identifier: Apache-2.0
"""`scripts/linux_lane.sh --rife-unmeasured` runs the suite where no RIFE is reachable, and the tests
that need one skip; `--rife-only` runs the tests marked `real_rife` (`pytest -m real_rife`), none
skipped. A test that skips without RIFE and is not marked is measured by neither run.

The suite is collected twice, with nothing else changed: once with a stand-in RIFE everywhere
`rife.locate()` looks (SPRITE_GEN_RIFE, PATH, the install root) and once with none. Every test the
two collections skip, or collect, differently is decided by RIFE, and must carry the mark."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

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
    base = {k: v for k, v in os.environ.items() if k not in ("SPRITE_GEN_RIFE", "SPRITE_GEN_RIFE_MODEL", "PYTEST_ADDOPTS")}
    base["PYTHONPATH"] = os.pathsep.join(filter(None, [str(tmp_path), base.get("PYTHONPATH")]))
    base["PATH"] = _without_rife(base.get("PATH", ""), tmp_path / "path")
    stand_in = _stand_in(tmp_path / "with")
    absent = _collect(tmp_path, "no RIFE", {**base, "SPRITE_GEN_DATA_DIR": str(tmp_path / "without")})
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
