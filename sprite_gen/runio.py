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

`curation.json` is intentionally NOT under this pipeline write lock: the curation
surface writes it with the same atomic replace, and the compose scripts read one
consistent snapshot of it, so a curation edit never blocks on a running
compose/extract. Concurrent curation edits on one run dir remain last-write-wins
by design; the lock guards pipeline outputs, not human edit sessions. The curation
*write* IS serialized against a `--force` re-import publish through the separate
publish rwlock (`read_guard`/`publish_guard`), and the server rejects a curation
POST whose echoed run generation (`runRevision`) no longer matches — so a stale edit
can't apply old selections/transforms to a freshly re-imported run's frames.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import tempfile
import time
from pathlib import Path, PurePath

from PIL import Image

try:  # Unix advisory locks; on a platform without fcntl the guards no-op (best-effort).
    import fcntl
except ImportError:  # pragma: no cover - non-Unix
    fcntl = None

LOCK_FILENAME = ".sprite-gen.lock"
# Sidecar (beside the run dir) reader/writer coordination lock for the publish swap.
# It lives outside the run dir so it survives content swaps and is never itself
# published; a run dir named `foo` uses `.foo.sg-rwlock` in the parent.
RWLOCK_SUFFIX = ".sg-rwlock"
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


def release_run_dir_lock(run_dir: Path) -> None:
    """이 프로세스가 쥔 run-dir 락을 작업 완료 시점에 명시 해제한다.

    atexit 해제만 있으면 장수 프로세스(테스트 러너·MCP 서버·큐레이션 서버)가
    한 번의 in-process 추출 뒤 락을 영구 보유해, heal_run 의 서브프로세스
    재추출이 산 pid 락에 막힌다. 락은 세션 리스가 아니라 작업 단위 writer-writer
    상호배제고, 원자성은 스테이징 통짜 스왑이 보장하므로 작업이 끝나면 놓는 게
    맞다. atexit 핸들러는 남아 있어도 멱등이다 (unlink 실패 무시 + discard)."""
    lock_path = (Path(run_dir).resolve() / LOCK_FILENAME).resolve()
    if lock_path not in _HELD_LOCKS:
        return
    try:
        lock_path.unlink()
    except OSError:
        pass
    _HELD_LOCKS.discard(lock_path)


def _rwlock_path(run_dir: Path) -> Path:
    run_dir = Path(run_dir).resolve()
    return run_dir.parent / f".{run_dir.name}{RWLOCK_SUFFIX}"


class RWLockUnavailable(RuntimeError):
    """Publish reader/writer isolation could not be established on this platform.

    Raised (fail-loud) instead of degrading the guard to a no-op. A no-op guard would be a
    *failover that changes canonical truth*: a reader could observe a half-published run
    (old/new file mix or a missing file) mid-swap. The isolation contract permits an
    availability failover only when it stays observable AND does not change canonical truth
    — a silent old-or-new-abandoning no-op fails the second half, so we refuse to proceed
    without isolation rather than serve a partial run.
    """


def _rwlock_unavailable(run_dir: Path, why: str) -> "RWLockUnavailable":
    return RWLockUnavailable(
        f"publish reader/writer isolation unavailable ({why}) for {run_dir}: cannot "
        f"guarantee a reader the complete old-or-new snapshot across a --force re-import / "
        f"re-extract swap. Refusing to proceed rather than expose a partial run (a failover "
        f"must not change canonical truth). Run on a platform with fcntl advisory locks "
        f"(macOS/Linux) and ensure the run dir's parent is writable for the "
        f".<name>{RWLOCK_SUFFIX} sidecar."
    )


@contextlib.contextmanager
def read_guard(run_dir: Path):
    """Shared (reader) lock on the run dir's publish rwlock. While a publish holds the
    exclusive lock for its content swap, a reader inside this guard blocks — so it never
    observes a half-published run (no old/new file mix, no missing file). Advisory
    cross-process flock. If the platform has no fcntl or the sidecar can't be created, the
    guard **fails loud** (`RWLockUnavailable`) rather than degrading to a no-op: a no-op
    would let canonical truth change inside the read transaction, which the isolation
    contract forbids."""
    if fcntl is None:
        raise _rwlock_unavailable(run_dir, "fcntl unavailable")
    try:
        fd = os.open(_rwlock_path(run_dir), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise _rwlock_unavailable(run_dir, f"cannot create rwlock sidecar: {exc}") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextlib.contextmanager
def publish_guard(run_dir: Path):
    """Exclusive (writer) lock on the run dir's publish rwlock — held only around the
    content swap so concurrent readers block briefly and never see a partial publish.
    Same sidecar file as read_guard. **Fails loud** (`RWLockUnavailable`, see read_guard)
    if fcntl is unavailable or the sidecar can't be created — never a no-op, because a
    no-op publish would swap canonical frames while readers are unguarded."""
    if fcntl is None:
        raise _rwlock_unavailable(run_dir, "fcntl unavailable")
    try:
        fd = os.open(_rwlock_path(run_dir), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise _rwlock_unavailable(run_dir, f"cannot create rwlock sidecar: {exc}") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


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
