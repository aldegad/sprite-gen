# SPDX-License-Identifier: Apache-2.0
"""Safe run-dir IO shared by the pipeline scripts.

Two concerns live together here because they answer the same question — "what
happens when two sprite-gen processes touch the same run dir at once?" (for
example Claude Code and the Codex app driving the skill in parallel):

- `acquire_run_dir_lock()` — single-writer lock per run dir. SKILL.md forbids
  two workers writing one character folder; this makes the rule enforced
  instead of documentation-only. Writers (extract / compose / export / unpack,
  and the webview's compose/export subprocesses through them) fail loudly with
  the holder's pid instead of silently interleaving output files.
- `atomic_write_text()` / `atomic_save_image()` — temp file in the target dir
  + `os.replace`, so a concurrent reader never observes a half-written
  atlas/manifest/frame.

`curation.json` is intentionally NOT under the lock: the curation surface writes
it with the same atomic replace, and the compose scripts read one consistent
snapshot of it. Concurrent curation edits on one run dir remain last-write-wins
by design; the lock guards pipeline outputs, not human edit sessions.
"""

from __future__ import annotations

import atexit
import json
import os
import tempfile
import time
from pathlib import Path, PurePath

from PIL import Image

LOCK_FILENAME = ".sprite-gen.lock"
# reclaim threshold for locks whose holder pid cannot be verified
# (unreadable lock file, or a writer on another host of a shared volume)
STALE_LOCK_SECONDS = 15 * 60

# Lock paths this process already owns. Re-entry from the same process (for
# example a long-lived MCP server invoking prepare -> extract -> compose against
# one run dir) must succeed; the single-writer rule is against other processes.
_HELD_LOCKS: set[Path] = set()


def relative_posix(path: PurePath, start: PurePath) -> str:
    """Return a manifest-safe relative path with POSIX separators."""

    return path.relative_to(start).as_posix()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_run_dir_lock(run_dir: Path, owner: str) -> Path:
    """Take the single-writer lock for `run_dir`, released automatically at exit.

    Create-exclusive lock file (`.sprite-gen.lock`) holding owner + pid. When
    another live process holds it, exit loudly instead of interleaving writes.
    A lock whose pid is dead — or unreadable and older than STALE_LOCK_SECONDS —
    is reclaimed, so a killed run never wedges the run dir.

    Re-entry from the same process is allowed: the MCP server / freeze binary
    runs many pipeline steps in one interpreter against the same run dir, and a
    writer must never block itself.

    Release runs via atexit (normal return, SystemExit, KeyboardInterrupt).
    A SIGKILL'd holder is covered by the dead-pid reclaim above.
    """
    lock_path = (run_dir / LOCK_FILENAME).resolve()
    if lock_path in _HELD_LOCKS:
        return lock_path
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            holder: dict = {}
            try:
                holder = json.loads(lock_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            pid = holder.get("pid")
            if isinstance(pid, int) and _pid_alive(pid):
                raise SystemExit(
                    f"run dir is locked by {holder.get('owner', 'unknown')} (pid {pid}): {run_dir}\n"
                    f"  another sprite-gen process is writing this run dir; wait for it to finish,\n"
                    f"  or delete {lock_path} if you are sure that process is gone"
                )
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                continue  # holder released it between our checks; retry the create
            if isinstance(pid, int) or age > STALE_LOCK_SECONDS:
                # dead pid, or unverifiable and old: reclaim, then retry the
                # exclusive create (one winner if two reclaimers race)
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            raise SystemExit(
                f"run dir has a lock whose holder cannot be verified ({age:.0f}s old): {lock_path}\n"
                f"  delete the lock file if no sprite-gen process is running"
            )

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"owner": owner, "pid": os.getpid(), "started": time.time()}, handle)

    _HELD_LOCKS.add(lock_path)

    def _release() -> None:
        try:
            lock_path.unlink()
        except OSError:
            pass
        _HELD_LOCKS.discard(lock_path)

    atexit.register(_release)
    return lock_path


def _atomic_replace(target: Path, write_payload) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        write_payload(fd, tmp_name)
        os.replace(tmp_name, target)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def atomic_write_text(target: Path, text: str) -> None:
    """Write text via temp file + os.replace so readers never see a torn file."""

    def payload(fd: int, _tmp_name: str) -> None:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)

    _atomic_replace(target, payload)


def atomic_save_image(image: Image.Image, target: Path) -> None:
    """Save a PIL image via temp file + os.replace (format from target suffix)."""
    fmt = (target.suffix.lstrip(".") or "png").upper()
    fmt = {"JPG": "JPEG"}.get(fmt, fmt)

    def payload(fd: int, tmp_name: str) -> None:
        os.close(fd)
        image.save(tmp_name, format=fmt)

    _atomic_replace(target, payload)
