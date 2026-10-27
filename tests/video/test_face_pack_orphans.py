# SPDX-License-Identifier: Apache-2.0
"""A face pack's reading workers leave when the process that made them is killed outright.

`face_pack.build` reads anchors on one process per core. On Windows a parent killed without warning (a test
timeout, a crashed test worker) does not take spawned workers with it: each went on holding its memory."""
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[2])

HOLDER = textwrap.dedent("""
    import os, sys, time
    from pathlib import Path

    def hold(out):
        Path(out, f"{os.getpid()}.pid").write_text("")
        time.sleep(60)

    if __name__ == "__main__":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from sprite_gen.video import face_pack
        face_pack._pool_map(hold, [sys.argv[1]] * 4, {}, 2)  # two chunks of two: one for each worker
""")


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_workers_leave_when_their_parent_is_killed(tmp_path):
    holder = tmp_path / "holder.py"
    holder.write_text(HOLDER, encoding="utf-8")
    pids = tmp_path / "pids"
    pids.mkdir()
    # The base interpreter, not a venv launcher: killing a launcher would leave the real parent alive.
    python = getattr(sys, "_base_executable", sys.executable)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([ROOT, *sys.path])}
    parent = subprocess.Popen([python, str(holder), str(pids)], env=env)
    try:
        deadline = time.monotonic() + 60
        while len(list(pids.iterdir())) < 2:
            assert parent.poll() is None, f"the parent exited {parent.returncode} before its workers started"
            assert time.monotonic() < deadline, "the workers did not start"
            time.sleep(0.2)
        workers = [int(p.stem) for p in pids.iterdir()]
    finally:
        parent.kill()
        parent.wait()
    deadline = time.monotonic() + 15
    while any(_alive(pid) for pid in workers) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not [pid for pid in workers if _alive(pid)], "workers outlived their killed parent"
