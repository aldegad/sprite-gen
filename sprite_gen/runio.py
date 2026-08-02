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
  atlas/manifest/frame. `atomic_write_set()` extends the same mechanism to a
  set of files that only makes sense published together (a sheet, its manifest,
  and the report naming it).

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
import sys
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


def atomic_write_set(payloads: "dict[Path, bytes | str]") -> None:
    """Publish a whole set of files: stage every payload first, then rename them in.

    The single-file writers above make each artifact appear whole, which is all a
    stage that publishes one file needs. A stage that publishes a *set* — a sheet,
    its manifest, and the report that says which sheets are current — needs the
    set to appear together: a mid-loop `ENOSPC` on the second sheet would
    otherwise leave the first one published with no report naming it.

    Phase 1 writes each payload to a temp file beside its target and fsyncs it.
    That is the phase that allocates, so any failure there has published nothing
    and removes its own temps. Phase 2 is one `os.replace` per target — a rename
    inside a directory whose blocks are already allocated.

    This is not a filesystem transaction (POSIX has no multi-file commit): a
    power loss between two renames can still land a subset. What it removes is
    the failure this code can actually observe and act on — a write error partway
    through a publish reporting success over a half-published set.

    Parent directories must already exist; the caller owns the layout.
    """
    staged: list[tuple[str, Path]] = []
    try:
        for target, payload in payloads.items():
            data = payload.encode("utf-8") if isinstance(payload, str) else payload
            fd, tmp_name = tempfile.mkstemp(
                dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
            # Tracked before it is written: a failure mid-write must still clean
            # up this temp, not just the ones that completed.
            staged.append((tmp_name, target))
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
    except BaseException:
        for tmp_name, _target in staged:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
        raise
    for index, (tmp_name, target) in enumerate(staged):
        try:
            os.replace(tmp_name, target)
        except BaseException:
            for pending, _target in staged[index:]:
                with contextlib.suppress(OSError):
                    os.unlink(pending)
            raise


# --- sprite-request.json 단일 로드 게이트 ------------------------------------
# 18곳이 각자 `json.loads((run_dir / "sprite-request.json").read_text())` 하던 것을 하나로
# 모은다 (`load_curation` 이 큐레이션 사이드카에 대해 그렇게 하는 것과 같은 이유). 게이트가
# 하나면 스키마 이관을 걸 자리도 하나다 — 열여덟 곳에 같은 이관 코드를 흩뿌리면 그중 하나가
# 빠진 날 조용히 두 진실이 생긴다.
REQUEST_FILENAME = "sprite-request.json"

# 은퇴한 키 -> 현행 키. "pixel perfect" 는 광의 통용어(UI 정합·충돌판정)의 오용이었고, 이
# 파이프라인이 실제로 하는 일의 정확한 명칭은 커뮤니티 속칭 "unfake"(unfake.js 유래)다
# — 격자 스냅/재양자화로 가짜 픽셀아트를 되돌린다 (수홍 2026-07-25, vault
# domains/tools/spritefusion-pixel-snapper.md 용어 각주).
LEGACY_FIT_KEYS = {"pixel_perfect": "pixel_unfake"}


def normalize_retired_fit_keys(request: dict, where: str) -> bool:
    """은퇴 키를 현행 키로 옮긴다 (**메모리 in-place**, 디스크는 손대지 않는다). 옮겼으면 True.

    두 키가 동시에 있으면 **hard fail** — 어느 쪽이 진실인지 코드가 고를 수 없고, 고르는
    순간 그게 조용한 폴백이다. 이건 읽기 정규화이지 이관이 아니다: 디스크 스키마를 바꾸는
    쪽은 `migrate_request_file` 하나뿐이고, 이 함수는 그 writer 도 같이 쓴다 (키 매핑이
    두 벌 생기지 않게)."""
    fit = request.get("fit")
    if not isinstance(fit, dict):
        return False
    moved = False
    for legacy, current in LEGACY_FIT_KEYS.items():
        if legacy not in fit:
            continue
        if current in fit:
            raise SystemExit(
                f"{where}: both `fit.{legacy}` (retired) and "
                f"`fit.{current}` are present — two truths for one setting. Delete the "
                f"`fit.{legacy}` line (its value was {fit[legacy]!r}); the current key is "
                f"`fit.{current}` = {fit[current]!r}.")
        fit[current] = fit.pop(legacy)
        moved = True
    return moved


# 은퇴 키를 안은 런을 한 프로세스에서 처음 읽을 때만 이관 안내를 낸다. `state_revision`
# 이 뷰 요청마다 로더를 부르므로 매번 찍으면 로그가 안내가 아니라 소음이 된다. 로그
# 중복 억제이지 진실 저장소가 아니다 — 여기 무엇이 들어 있든 판독 결과는 같다.
_RETIRED_KEY_NOTICED: set[str] = set()


def _read_request_document(run_dir: Path) -> tuple[Path, dict, bool]:
    """`sprite-request.json` 을 읽어 (경로, 정규화된 문서, 은퇴 키가 있었나) 를 돌려준다.

    request 파일을 실제로 여는 곳은 여기 하나다 — 읽기 게이트와 이관 writer 가 같은 판독
    (두 키 hard fail 포함)을 쓰게 하려면 파싱이 한 벌이어야 한다."""
    path = Path(run_dir) / REQUEST_FILENAME
    if not path.is_file():
        raise SystemExit(f"not a sprite-gen run dir (no {REQUEST_FILENAME}): {run_dir}")
    request = json.loads(path.read_text(encoding="utf-8"))
    return path, request, normalize_retired_fit_keys(request, str(path))


def load_request(run_dir: Path) -> dict:
    """런의 numeric SSoT (`sprite-request.json`) 를 읽는 **유일한 게이트**. 읽기만 한다.

    은퇴한 키(`fit.pixel_perfect`)는 현행 키(`fit.pixel_unfake`)로 **메모리에서만** 정규화
    되고 파일은 바이트 그대로 남는다. 두 키가 동시에 있으면 hard fail.

    예전엔 이 함수가 정규화한 문서를 곧바로 디스크에 다시 썼다. 그건 SRP 위반이었고 실제로
    터졌다: founder v8 승계 작업 중 승인된 founder_v7 런에 `state_revision()` 을 부른 것만
    으로 그 런의 `sprite-request.json` 이 재기록됐다. `run_revision` 이 request 바이트를
    해싱하므로 조회 한 번이 세대 지문까지 움직여 큐레이션 사이드카를 stale 로 떨어뜨렸다
    (plan sprite-gen/state-revision-read-mutation).

    디스크 스키마 이관은 사용자가 명시적으로 부르는 단일 writer 에서만 원자적으로 한다:
    `migrate_request_file()` / `sprite-gen migrate-request <run-dir> --apply`."""
    path, request, had_retired = _read_request_document(run_dir)
    if had_retired and str(path) not in _RETIRED_KEY_NOTICED:
        _RETIRED_KEY_NOTICED.add(str(path))
        print(f"[request] retired fit key(s) "
              f"{', '.join(f'{k} -> {v}' for k, v in LEGACY_FIT_KEYS.items())} read as the "
              f"current key(s); the file is unchanged. Run `sprite-gen migrate-request "
              f"{run_dir} --apply` to move the schema on disk: {path}", file=sys.stderr)
    return request


def migrate_request_file(run_dir: Path, *, apply: bool = False) -> bool:
    """은퇴한 `fit` 키를 디스크에서 현행 키로 옮긴다 — request 스키마의 **유일한 이관 writer**.

    Returns True 면 옮길 것이 있었다는 뜻이다 (`apply=False` 인 dry run 에서도 True).
    이미 현행 키뿐이면 False (멱등 — 두 번째 실행은 아무것도 쓰지 않는다).

    writer 라서 writer 규율을 따른다: run-dir 단일 writer 락을 잡고(다른 파이프라인
    프로세스가 쓰는 중이면 조용히 미루는 게 아니라 요란하게 거부한다), 원자 교체로 쓰고,
    작업이 끝나면 락을 놓는다. 조회 경로가 락 유무에 따라 쓰거나 안 쓰던 예전 구조와의
    차이가 여기다 — 쓰기는 이제 사용자가 부른 명령 안에서만 일어나므로 거부해도 될 자리다.
    """
    path, request, had_retired = _read_request_document(run_dir)
    if not had_retired:
        return False
    if not apply:
        return True
    acquire_run_dir_lock(Path(run_dir), "migrate-request")
    try:
        atomic_write_text(path, json.dumps(request, ensure_ascii=False, indent=2) + "\n")
    finally:
        release_run_dir_lock(Path(run_dir))
    _RETIRED_KEY_NOTICED.discard(str(path))
    return True


def write_request(run_dir: Path, request: dict) -> None:
    """편집한 request 를 원자적으로 다시 쓴다 — **디스크의 키 형태를 보존한다**.

    take 기록·fps 편집처럼 스키마와 무관한 편집도 로더가 정규화해 준 문서를 통째로 되쓴다.
    그대로 쓰면 그 편집이 은퇴 키를 조용히 현행 키로 바꿔, 스키마 이관이 다시 "아무 명령이나
    하는 김에 하는 일" 이 된다. 로더가 메모리에서 편 것을 쓰기 직전에 되접으면, 디스크
    스키마를 바꾸는 곳은 `migrate_request_file` 하나로 남는다.

    호출부의 dict 는 건드리지 않는다 (쓴 뒤에도 현행 키로 계속 쓰인다)."""
    path = Path(run_dir) / REQUEST_FILENAME
    on_disk_fit: dict = {}
    try:
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        on_disk_fit = on_disk.get("fit") if isinstance(on_disk.get("fit"), dict) else {}
    except (OSError, ValueError):
        on_disk_fit = {}   # 첫 발행이거나 읽을 수 없다 — 되접을 형태가 없으니 그대로 쓴다
    fit = request.get("fit")
    if isinstance(fit, dict):
        restore = {current: legacy for legacy, current in LEGACY_FIT_KEYS.items()
                   if legacy in on_disk_fit and current in fit}
        if restore:
            # 키 순서를 유지한 채 이름만 되돌린다 — 편집 diff 가 편집한 줄만 보이게.
            request = {**request, "fit": {restore.get(key, key): value
                                          for key, value in fit.items()}}
    atomic_write_text(path, json.dumps(request, ensure_ascii=False, indent=2) + "\n")
