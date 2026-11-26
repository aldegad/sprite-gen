# SPDX-License-Identifier: Apache-2.0
"""런 폴더 단일 writer 락(`runio.acquire_run_dir_lock`)은 락 이름의 항목 그 자체다.

지키는 것 (구현이 아니라 계약):
  1. 락 이름(`.sprite-gen.lock`) 자리가 링크(닿는 곳이 오래된 파일이든 없는 이름이든 폴더든, 상대 경로든 절대
     경로든)나 폴더면, 락을 잡는 동사는 그 항목을 대며 곧바로 거절하고 링크를 따라가 읽거나 만들거나 지우지 않는다 —
     락을 회수해도 링크 너머의 파일은 지워지지 않는다. Windows 도 같은 거절이다(거기서 os.open 의 O_CREAT|O_EXCL 은
     없는 이름에 닿는 링크를 따라가 그 자리에 파일을 만들고, 폴더에는 EACCES 다) (unpack-atlas 의 배치는
     tests/frames/test_unpack_atlas_publish.py 7)
  2. 주인을 확인할 수 없는 오래된 락 파일은 회수한다
  3. 한 프로세스는 자기가 만든 바로 그 락 항목만, 아직 쥐고 있을 때만 지운다 — 명시 해제한 writer 가 끝날 때 그 뒤
     이름에 들어선 것(다음 writer 의 살아 있는 락, 그 자리에 놓인 링크)은 그대로다. 쥔 채 끝나면(해제하지 않았거나,
     해제한 뒤 다시 잡았거나) 그 락을 지운다
  4. 죽은 주인의 락을 회수하는 일과 새 락을 만드는 일은 줄을 선다 — 늦은 회수자가 옛 락을 판정한 동안 다른 writer 는
     기다리고, 회수자가 그 판정한 옛 락만 지우고 잡은 뒤에야 차례가 와 그 살아 있는 락을 대며 거절된다. 두 writer 가
     함께 쥐지 않는다
  5. 락 항목을 만들고 내용을 다 쓰지 못한 writer 는 그 만들다 만 항목을 거두고 실패한다 — 주인을 확인할 수 없는 락이
     남아 다음 writer 를 막지 않는다
  6. 락은 잡은 프로세스의 것이라 fork 자식은 부모의 락을 지우지도 쥐지도 않는다 — 맨 os.fork 자식, fork 로 띄운 풀 워커,
     파이썬 fork 훅이 돌지 않는 C fork 자식이 끝나거나 놓거나 같은 런 폴더를 잡아도 부모의 살아 있는 락은 그대로고, 같은
     런 폴더 잡기는 그 락을 대며 거절된다. 주인이 런 폴더 커널 락 안에서 죽으면 그 사이 다른 스레드가 fork 한 자식이 살아
     있어도 writer 는 기다리지 않고, 주인이 살아 있으면 그 자식이 끝나도 커널 락은 주인의 것 그대로다. 자식들이 끝난 뒤에도
     부모는 자기 락에 다시 들어오고 놓거나 끝나며 지운다
  7. fork 가 런 폴더 커널 락 fd 의 기록이 바뀌는 어느 자리에 끼어도 그 자식은 커널 락을 이어 쥐지 않는다 — 주 스레드의 시그널
     처리기가 열기→기록, 기록→잠금, 풀기→빼기, 빼기→닫기 자리에서 fork 해도(신호를 막지 않는 다른 스레드가 있든 없든, 첫 풀기를
     Ctrl-C 가 건너뛰었어도), 다른 스레드의 fork 가 runio 의 fork 훅에 들어간 뒤 그 fd 를 열 때 일어나도, 주인이 그 자리에서
     죽으면 writer 는 그 자식을 기다리지 않는다. 다른 스레드의 fork 가 돌아오지 않으면 커널 락을 잡으려는 쪽은 한도까지 기다리고
     크게 실패한다 — 잡기는 아무것도 남기지 않고 놓기는 쥔 그대로다. 그 fd 를 복사할 수 없는 fork 표시(fork 하는 스레드 자신의
     것, C fork 자식이 물려받은 것)는 기다리지 않는다
  8. 락 주인이 살았는지는 그 프로세스에 아무것도 보내지 않고 본다 — POSIX 는 이 계약 전과 같은 신호 0 이고, Windows 는
     콘솔에 Ctrl+C 를 보내지 않는다(거기서 os.kill(pid, 0) 은 CTRL_C_EVENT 다). 살아 있는 프로세스와 열 수 없는 남의
     프로세스는 살아 있고, 끝난 프로세스는 아니다
  9. 락 항목을 만든 fd 는 os.open 이 주던 fd 와 같은 성질이다 — 자식에게 상속되지 않고, 쓰기만 되고, 쓴 바이트가 그대로
     놓이고, 열린 동안 그 이름을 지울 수 있는지도 같다. Windows 에서 만든 핸들을 fd 로 넘기지 못하면 그 핸들은 닫힌다

3–8 은 실제 프로세스로 잰다 — 락은 프로세스 사이의 계약이고, 종료 처리는 프로세스가 끝날 때만 돈다. 3·6·7 은
docs/run-contract.md §7 의 세 경계 밖에서다: C fork 자식이 끝나거나 exec 할 때까지의 커널 락, 시그널 처리기에서 fork 한 뒤 끊긴
코드로 돌아가는 자식, runio 의 fork 훅을 끊는 예외.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import queue
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sprite_gen import cli
from sprite_gen.spec import runio

from conftest import symlink_or_skip

# 차례대로 락을 잡고(owner 이름) 놓고(release) 표준 입력 한 줄을 기다린다(wait). 먼저 자기 pid, 잡기 전에 trying, 한 걸음마다
# 그 이름을 찍는다.
_STEPS = r"""
import os
import sys
from pathlib import Path
from sprite_gen.spec import runio
print(os.getpid(), flush=True)
run = Path(sys.argv[1])
for step in sys.argv[2:]:
    if step == "wait":
        sys.stdin.readline()
        continue
    if step == "release":
        runio.release_run_dir_lock(run)
    else:
        print("trying", flush=True)
        runio.acquire_run_dir_lock(run, step)
    print(step, flush=True)
"""

# 옛 락의 주인이 죽었다고 판정한 바로 그 자리에서 멈추는 회수자 — runio._pid_alive 를 감싸 진짜 답을 돌려주기 전에 표준 입력
# 한 줄을 기다린다. 스케줄러가 끼어들 수 있는 자리이고, 멈춤은 그 사이를 매번 같게 만든다. 제품 코드는 그대로다.
_SLOW_RECLAIMER = r"""
import os
import sys
from pathlib import Path
from sprite_gen.spec import runio
print(os.getpid(), flush=True)
judge = runio._pid_alive
paused = []
def judged(pid):
    alive = judge(pid)
    if not alive and not paused:
        paused.append(pid)
        print("judged-stale", flush=True)
        sys.stdin.readline()
    return alive
runio._pid_alive = judged
runio.acquire_run_dir_lock(Path(sys.argv[1]), "slow-reclaimer")
print("slow-reclaimer", flush=True)
sys.stdin.readline()
"""

# 파일 크기 한도(RLIMIT_FSIZE)를 락 내용보다 작게 둔 writer — 락을 만든 뒤 쓰기가 한도에서 EFBIG 로 끊긴다(디스크가 찬
# 것과 같은 자리). SIGXFSZ 는 무시해 프로세스가 죽지 않고 쓰기가 실패하게 한다. 한도는 import 뒤에 건다.
_CUT_SHORT_WRITER = r"""
import os
import resource
import signal
import sys
from pathlib import Path
from sprite_gen.spec import runio
print(os.getpid(), flush=True)
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (16, resource.getrlimit(resource.RLIMIT_FSIZE)[1]))
try:
    runio.acquire_run_dir_lock(Path(sys.argv[1]), "cut-short-writer")
except OSError as exc:
    print("failed", exc.errno, flush=True)
else:
    print("cut-short-writer", flush=True)
"""

# 락을 쥔 부모가 자식 하나를 갈라 낸다 — 맨 os.fork(raw), fork 로 시작하는 ProcessPoolExecutor 워커(pool, 3.12 까지 리눅스의
# 기본 시작 방식), 파이썬 fork 훅이 돌지 않는 libc fork(c-fork — C 코드가 fork 하는 자리, ctypes 로). 자식은 끝나기만 하거나(end —
# raw·c-fork 는 sys.exit 로 보통 끝나 물려받은 종료 처리까지 돌고, 풀 워커는 물려받은 종료 처리 없이 끝난다: 3.12 까지는 os._exit,
# 3.13 부터는 atexit 를 비우고 시작해 자기가 단 것만 돈다) 런 폴더 락을 놓거나(release) 같은 런 폴더를 잡거나(acquire-same) 다른
# 런 폴더를 잡는다(acquire-other). 자식이 끝난 뒤 부모는 본 것을 JSON 한 줄로 찍고 놓는다.
_FORKED = r"""
import ctypes
import json
import multiprocessing
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from sprite_gen.spec import runio


def act(mode, run, other):
    if mode == "release":
        runio.release_run_dir_lock(run)
        return "released"
    if mode.startswith("acquire"):
        try:
            return f"held {runio.acquire_run_dir_lock(run if mode == 'acquire-same' else other, 'forked-child')}"
        except SystemExit as exc:
            return f"refused: {str(exc).splitlines()[0]}"
    return "ended"


def another_writer(run):
    got = subprocess.run([sys.executable, "-c", "import sys; from pathlib import Path; from sprite_gen.spec import runio; "
                          "runio.acquire_run_dir_lock(Path(sys.argv[1]), 'another-writer')", str(run)],
                         capture_output=True, encoding="utf-8", timeout=30)
    return got.returncode, got.stderr


run, other, kind, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
lock = runio.acquire_run_dir_lock(run, "live-parent")
held = lock.read_bytes()
if kind == "pool":
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("fork")) as pool:
        said = pool.submit(act, mode, run, other).result(timeout=30)
    ended = 0  # 풀이 닫혔다 — 워커는 끝났다
else:
    read_end, write_end = os.pipe()
    child = os.fork() if kind == "raw" else ctypes.PyDLL(None).fork()
    if child == 0:
        os.close(read_end)
        os.write(write_end, act(mode, run, other).encode())
        os.close(write_end)
        if mode == "end":
            sys.exit(0)
        os._exit(0)
    os.close(write_end)
    with os.fdopen(read_end, "rb") as pipe:
        said = pipe.read().decode()
    ended = os.waitstatus_to_exitcode(os.waitpid(child, 0)[1])
try:
    kept = lock.read_bytes() == held
except FileNotFoundError:
    kept = False
rc, err = another_writer(run)
seen = {"said": said, "child_exit": ended, "parent_pid": os.getpid(), "parent_lock_kept": kept,
        "another_writer_rc": rc, "another_writer_stderr": err}
if mode == "acquire-other":
    seen["other_lock_left"] = os.path.lexists(other / runio.LOCK_FILENAME)
    seen["other_next_writer_rc"] = another_writer(other)[0]
print(json.dumps(seen), flush=True)
runio.release_run_dir_lock(run)
"""

# 죽은 주인의 락을 판정하는 동안 — 런 폴더 커널 락(`_entry_guard`) 안에서 — SIGKILL 로 죽는 주인. 판정은 한 스레드가 하고,
# fork 면 그 사이 주 스레드가 자식을 갈라 낸다. 자식은 표시 파일 `go` 가 생기거나 6 s 가 지날 때까지 산다.
_KILLED_IN_THE_GUARD = r"""
import os
import signal
import sys
import threading
import time
from pathlib import Path
from sprite_gen.spec import runio
run, marks, dead, mode = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
judge = runio._pid_alive
def judging(pid):
    if pid == dead:
        (marks / "judging").touch()
        time.sleep(60)
    return judge(pid)
runio._pid_alive = judging
threading.Thread(target=runio.acquire_run_dir_lock, args=(run, "killed-holder"), daemon=True).start()
deadline = time.monotonic() + 10
while not (marks / "judging").exists():
    if time.monotonic() > deadline:
        sys.exit("the holder never reached its judgment")
    time.sleep(0.01)
if mode == "fork":
    child = os.fork()
    if child == 0:
        deadline = time.monotonic() + 6
        while not (marks / "go").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os._exit(0)
    (marks / "child").write_text(str(child))
os.kill(os.getpid(), signal.SIGKILL)
"""

# 락을 잡는 데 걸린 시간(초)을 찍는 writer.
_TIMED_WRITER = r"""
import sys
import time
from pathlib import Path
from sprite_gen.spec import runio
started = time.monotonic()
runio.acquire_run_dir_lock(Path(sys.argv[1]), "writer")
print(f"{time.monotonic() - started:.2f}", flush=True)
"""

# 한 스레드가 죽은 주인의 락을 판정하는 동안 — 런 폴더 커널 락 안에서 — 주 스레드가 자식을 갈라 내고 그 자식이 끝나기를
# 기다린 뒤(forked), 표준 입력 한 줄을 받으면 판정을 이어 락을 잡고(judging-parent) 다시 한 줄을 기다린다.
_JUDGING_PARENT = r"""
import os
import sys
import threading
from pathlib import Path
from sprite_gen.spec import runio
print(os.getpid(), flush=True)
run, dead = Path(sys.argv[1]), int(sys.argv[2])
judge = runio._pid_alive
judging, go_on = threading.Event(), threading.Event()
def judged(pid):
    if pid == dead:
        judging.set()
        go_on.wait()
    return judge(pid)
runio._pid_alive = judged
taker = threading.Thread(target=runio.acquire_run_dir_lock, args=(run, "judging-parent"))
taker.start()
judging.wait()
child = os.fork()
if child == 0:
    os._exit(0)
os.waitpid(child, 0)
print("forked", flush=True)
sys.stdin.readline()
go_on.set()
taker.join()
print("judging-parent", flush=True)
sys.stdin.readline()
"""

# 락을 쥔 부모가 맨 os.fork 자식, libc fork 자식, fork 풀 워커를 차례로 띄우고 끝나기를 기다린 뒤(자식은 아무것도 하지 않고
# os._exit 로 끝난다) 같은 런 폴더를 다시 잡고(재진입) 놓거나(release) 쥔 채 끝난다(exit).
_PARENT_AFTER_CHILDREN = r"""
import ctypes
import json
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from sprite_gen.spec import runio
run, finish = Path(sys.argv[1]), sys.argv[2]
lock = runio.acquire_run_dir_lock(run, "parent")
for fork in (os.fork, ctypes.PyDLL(None).fork):
    child = fork()
    if child == 0:
        os._exit(0)
    os.waitpid(child, 0)
with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("fork")) as pool:
    pool.submit(os.getpid).result(timeout=30)
print(json.dumps({"reentered": runio.acquire_run_dir_lock(run, "parent") == lock, "kept": lock.is_file()}), flush=True)
if finish == "release":
    runio.release_run_dir_lock(run)
    print(json.dumps({"left_after_release": os.path.lexists(lock)}), flush=True)
"""

# 주 스레드의 SIGUSR1 처리기가 런 폴더 커널 락(`_entry_guard`)의 fd 기록이 바뀌는 자리에서 fork 한다 — sys.settrace 는 신호를 넣을
# 자리만 고르고 진짜 처리기가 os.fork 한다(종단 반려 netti-20261023-232602). 자리는 줄 번호가 아니라 실제로 실행되는 줄이다: 표시 줄
# 이 돌기 직전(at)이나 그 줄이 돈 바로 다음 줄(after). 열기 쪽 표시 줄(기록하는 `_GUARD_FDS.add(fd)`, 잠그는 `_lock_fd(fd,
# exclusive=True)`)은 첫 줄, 닫기 쪽 표시 줄(푸는 `_unlock_fd(fd)`, 기록에서 빼는 `_GUARD_FDS.discard(fd)`)은 마지막 줄이다. 열기
# 쪽은 주인이 커널 락을 잡은 뒤 그 안에서 멈추고, 닫기 쪽은 첫 풀기를 KeyboardInterrupt 가 건너뛰고(Ctrl-C 가 `_unlock_fd` 가 시작될
# 때 떨어진 자리) 부모가 fork 한 그 자리에서 멈춘다 — 둘 다 시험이 그 주인을 SIGKILL 한다. 자식은
# 표시 파일 `go` 가 생기거나 6 s 가 지날 때까지 산다. helper-thread 면 신호를 막지 않는 빈 스레드가 하나 더 있다 — 신호는 그
# 스레드가 받을 수 있고 파이썬 처리기는 늘 주 스레드에서, 다음 바이트코드 경계에서 돈다(파이썬이든 네이티브든 스레드가 둘인
# 프로세스). 신호를 넣은 trace 는 처리기가 돌 때까지 기다린다.
_HANDLER_FORK = r"""
import inspect
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from sprite_gen.spec import runio
run, marks, mark, when, helper = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
guard = runio._entry_guard.__wrapped__
source, first = inspect.getsourcelines(guard)
marked = [first + i for i, text in enumerate(source) if text.strip() == mark]
closing = mark in ("_unlock_fd(fd)", "_GUARD_FDS.discard(fd)")
anchor = marked[-1] if closing else marked[0]
if closing:
    unlock, calls = runio._unlock_fd, []
    def unlock_unless_interrupted(fd):
        calls.append(fd)
        if len(calls) == 1:
            raise KeyboardInterrupt("Ctrl-C as _unlock_fd starts, before LOCK_UN")
        unlock(fd)
    runio._unlock_fd = unlock_unless_interrupted
else:
    lock = runio._lock_fd
    def lock_and_stay(fd, **kwargs):
        lock(fd, **kwargs)
        (marks / "inside").touch()
        time.sleep(60)
    runio._lock_fd = lock_and_stay
def fork_here(signum, frame):
    while frame is not None and frame.f_code is not guard.__code__:
        frame = frame.f_back
    fd = frame.f_locals.get("fd") if frame is not None else None
    (marks / "handler").write_text(json.dumps({"fd": fd, "recorded": fd in runio._GUARD_FDS,
                                               "thread": threading.current_thread().name}))
    if os.fork() == 0:
        (marks / "child.tmp").write_text(str(os.getpid()))
        os.replace(marks / "child.tmp", marks / "child")
        deadline = time.monotonic() + 6
        while not (marks / "go").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os._exit(0)
    if closing:
        (marks / "inside").touch()
        time.sleep(60)
signal.signal(signal.SIGUSR1, fork_here)
if helper == "helper-thread":
    threading.Thread(target=time.sleep, args=(60,), daemon=True, name="helper").start()
sent = passed = False
def trace(frame, event, arg):
    global sent, passed
    if sent or event != "line" or frame.f_code is not guard.__code__:
        return trace
    if when == "at" and frame.f_lineno == anchor or when == "after" and passed:
        sent = True
        (marks / "injected").write_text(f"{frame.f_lineno}: {source[frame.f_lineno - first].strip()}")
        os.kill(os.getpid(), signal.SIGUSR1)
        deadline = time.monotonic() + 5
        while not (marks / "handler").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
    elif frame.f_lineno == anchor:
        passed = True
    return trace
sys.settrace(trace)
runio.acquire_run_dir_lock(run, "signal-holder")
"""

# 다른 스레드(forker)가 fork 하는 동안 주 스레드가 런 폴더 커널 락을 잡으러 들어간다. forker 는 fork 시스템 호출 전에 GIL 을 놓고
# 멈춘다 — past-the-hook 은 runio 의 before 훅을 다 지난 뒤(그 뒤에 도는 before 훅 — logging 의 모듈 락 — 이나 임포트 락이 막힌
# 자리. 멈추는 훅은 runio 를 들이기 전에 등록해 등록의 역순으로 runio 의 훅 뒤에 돈다), inside-the-hook 은 runio 의 before 훅이
# 진행 중 표시(`_FORKING.append(`)와 횟수(`_FORKS += 1`) 가운데 먼저 바꾸는 줄을 마친 자리(그다음 실제로 실행되는 줄, 없으면 훅이
# 돌아갈 때). past-the-hook 은 주 스레드가 fork 횟수를 읽는 줄
# (`forks = _FORKS` — 그런 줄이 없는 코드면 커널 락 헬퍼의 첫 줄)에 닿은 뒤에야 forker 를 띄우고, inside-the-hook 은 forker 가 멈춘
# 뒤에 주 스레드가 들어간다. 주 스레드가 그 fd 를 열면 기록하기 직전에 fork 가 일어나게 하고(열지 않고 기다리면 fork 는 1.5 s 뒤에
# 일어난다), 주인은 커널 락을 잡은 뒤 그 안에서 멈춘다 — 시험이 SIGKILL 한다. 자식은 표시 파일 `go` 가 생기거나 6 s 가 지날 때까지
# 산다.
_FORK_FROM_ANOTHER_THREAD = r"""
import inspect
import json
import os
import sys
import threading
import time
from pathlib import Path
run, marks, where = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
stalled, opened, forked = threading.Event(), threading.Event(), threading.Event()
def stall():
    if not stalled.is_set():
        stalled.set()
        opened.wait(1.5)
def after_runios_hook():
    if where == "past-the-hook" and threading.current_thread().name == "forker":
        stall()
os.register_at_fork(before=after_runios_hook)
from sprite_gen.spec import runio
lock = runio._lock_fd
def lock_and_stay(fd, **kwargs):
    lock(fd, **kwargs)
    (marks / "inside").touch()
    time.sleep(60)
runio._lock_fd = lock_and_stay
hook = runio._before_fork.__code__
hook_source, hook_first = inspect.getsourcelines(runio._before_fork)
changes = {hook_first + i for i, said in enumerate(hook_source) if "_FORKING.append(" in said or "_FORKS += 1" in said}
def inside_runios_hook(frame, event, arg):
    if frame.f_code is not hook:
        return None
    changed = []
    def step(frame, event, arg):
        if changed or event == "return":
            stall()
        elif event == "line" and frame.f_lineno in changes:
            changed.append(frame.f_lineno)
        return step
    return step
def fork():
    if where == "inside-the-hook":
        sys.settrace(inside_runios_hook)
    if os.fork() == 0:
        (marks / "child.tmp").write_text(str(os.getpid()))
        os.replace(marks / "child.tmp", marks / "child")
        deadline = time.monotonic() + 6
        while not (marks / "go").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os._exit(0)
    forked.set()
forker = threading.Thread(target=fork, name="forker", daemon=True)
def start_the_fork():
    forker.start()
    if not stalled.wait(10):
        sys.exit("the forker never stalled")
guard = runio._entry_guard.__wrapped__
source, first = inspect.getsourcelines(guard)
def line_of(text):
    return next((first + i for i, said in enumerate(source) if said.strip() == text), None)
record, count = line_of("_GUARD_FDS.add(fd)"), line_of("forks = _FORKS")
def trace(frame, event, arg):
    if event != "line" or frame.f_code is not guard.__code__:
        return trace
    if not stalled.is_set() and (count is None or frame.f_lineno == count):
        start_the_fork()
    elif frame.f_lineno == record and not opened.is_set():
        (marks / "opened").write_text(json.dumps({"fork_returned_before_the_open": forked.is_set()}))
        opened.set()
        forked.wait(5)
    return trace
if where == "inside-the-hook":
    start_the_fork()
sys.settrace(trace)
runio.acquire_run_dir_lock(run, "opening-holder")
"""

# 다른 스레드(forker)의 fork 가 runio 의 before 훅을 지난 뒤 돌아오지 않는다 — 그 뒤에 도는 before 훅이 8 s 동안 붙잡는다(runio 를
# 들이기 전에 등록). 주 스레드는 진행 중 fork 를 기다리는 한도를 0.5 s 로 줄이고, fork 전에 잡아 둔 런 폴더를 놓으려 하고 다른 런
# 폴더를 잡으려 해, 받은 것과 걸린 시간, 그때 커널 락 fd 기록과 다른 런 폴더의 락 항목, 놓지 못한 락을 아직 쥐었는지(재진입)를
# JSON 으로 찍는다. 그 뒤 fork 를 놓아 주고(자식은 곧 끝난다) 다른 런 폴더를 잡고, 놓지 못했던 락을 다시 놓거나(release) 놓지 않은
# 채 끝난다(exit — 종료 처리가 남아 있어야 지운다).
_FORK_THAT_DOES_NOT_RETURN = r"""
import json
import os
import sys
import threading
import time
from pathlib import Path
run, other, finish = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
stalled, release = threading.Event(), threading.Event()
def hold_the_fork():
    if threading.current_thread().name == "forker":
        stalled.set()
        release.wait(8)
os.register_at_fork(before=hold_the_fork)
from sprite_gen.spec import runio
runio._FORK_WAIT_SECONDS = 0.5
lock = runio.acquire_run_dir_lock(run, "holder")
def fork():
    if os.fork() == 0:
        os._exit(0)
forker = threading.Thread(target=fork, name="forker")
forker.start()
if not stalled.wait(10):
    sys.exit("the forker never stalled")
def attempt(step, *args):
    started = time.monotonic()
    try:
        step(*args)
        said = "done"
    except SystemExit as exc:
        said = "refused: " + str(exc).splitlines()[0]
    return {"said": said, "seconds": round(time.monotonic() - started, 2)}
seen = {"release": attempt(runio.release_run_dir_lock, run),
        "acquire": attempt(runio.acquire_run_dir_lock, other, "other-writer")}
seen["recorded_fds"] = sorted(runio._GUARD_FDS)
seen["other_entry_left"] = os.path.lexists(other / runio.LOCK_FILENAME)
seen["re_entry"] = attempt(runio.acquire_run_dir_lock, run, "re-entry")
seen["entry_owner"] = json.loads(lock.read_text(encoding="utf-8"))["owner"]
release.set()
forker.join(10)
taken = runio.acquire_run_dir_lock(other, "after-the-fork")
seen["other_owner_after_the_fork_returned"] = json.loads(taken.read_text(encoding="utf-8"))["owner"]
if finish == "release":
    runio.release_run_dir_lock(run)
    seen["entry_left_after_release"] = os.path.lexists(lock)
print(json.dumps(seen), flush=True)
"""

# 다른 스레드의 진행 중 fork 표시가 있는 동안 런 폴더 락을 잡고 놓는다 — 그 표시가 막지 않아야 하는 두 자리, 진행 중 fork 를 기다리는
# 한도는 0.5 s. forking-thread: fork 하는 스레드 자신이 runio 의 before 훅을 지난 뒤(그 뒤에 도는 before 훅 — runio 를 들이기 전에
# 등록 — 안에서. 그 스레드의 시그널 처리기가 fork 의 훅 사이에서 락을 잡는 자리) 잡고 놓는다. c-fork-child: 다른 스레드의 파이썬 fork 가
# runio 의 훅을 지나 멈춘 동안 주 스레드가 파이썬 fork 훅을 거치지 않는 libc fork 로 띄운 자식이(부모의 표시를 물려받는다) 잡고 놓는다 —
# 그 자식은 20 s 안에 끝난다(SIGALRM). 받은 것과 걸린 시간을 JSON 으로 찍는다.
_LOCK_TAKEN_DURING_A_FORK = r"""
import ctypes
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
run, mode = Path(sys.argv[1]), sys.argv[2]
stalled, release = threading.Event(), threading.Event()
seen = {}
def take_and_let_go():
    started = time.monotonic()
    try:
        runio.acquire_run_dir_lock(run, mode)
        runio.release_run_dir_lock(run)
        said = "held and released"
    except SystemExit as exc:
        said = "refused: " + str(exc).splitlines()[0]
    return {"said": said, "seconds": round(time.monotonic() - started, 2)}
def after_runios_hook():
    if threading.current_thread().name != "forker":
        return
    if mode == "forking-thread":
        seen.update(take_and_let_go())
    else:
        stalled.set()
        release.wait(8)
os.register_at_fork(before=after_runios_hook)
from sprite_gen.spec import runio
runio._FORK_WAIT_SECONDS = 0.5
def fork():
    if os.fork() == 0:
        os._exit(0)
forker = threading.Thread(target=fork, name="forker")
forker.start()
if mode == "c-fork-child":
    if not stalled.wait(10):
        sys.exit("the forker never stalled")
    read_end, write_end = os.pipe()
    child = ctypes.PyDLL(None).fork()
    if child == 0:
        signal.alarm(20)
        os.write(write_end, json.dumps(take_and_let_go()).encode())
        os._exit(0)
    os.close(write_end)
    with os.fdopen(read_end, "rb") as pipe:
        said = pipe.read().decode()
    os.waitpid(child, 0)
    seen.update(json.loads(said) if said else {"said": "the child said nothing"})
    release.set()
forker.join(10)
print(json.dumps(seen), flush=True)
"""

# 콘솔 하나를 나눠 쓰는 두 프로세스 — 이 탐침과 그가 띄운 대상 — 가 받은 Ctrl+C 를 센다(물려받았을 수 있는 'Ctrl+C 무시'는
# 둘 다 끈다). 탐침은 runio._pid_alive 로 대상·자신·끝난 프로세스·System(pid 4)·0 을 차례로 묻고, 물을 때마다 콘솔 신호가
# 닿을 틈을 둔 뒤 그 사이 둘이 받은 Ctrl+C 를 적는다. Windows 만.
_CONSOLE_PROBE = r"""
import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
import time
from sprite_gen.spec import runio
TARGET = '''
import ctypes, os, signal, sys, threading, time
ctypes.WinDLL("kernel32").SetConsoleCtrlHandler(None, False)
signal.signal(signal.SIGINT, lambda *_: print("ctrl-c", flush=True))
print(os.getpid(), flush=True)
threading.Thread(target=lambda: (sys.stdin.read(), os._exit(0)), daemon=True).start()
while True:
    time.sleep(0.05)
'''
ctypes.WinDLL("kernel32").SetConsoleCtrlHandler(None, False)
here, there = [], []
signal.signal(signal.SIGINT, lambda *_: here.append("ctrl-c"))
target = subprocess.Popen([sys.executable, "-c", TARGET], stdin=subprocess.PIPE, stdout=subprocess.PIPE, encoding="utf-8")
target_pid = int(target.stdout.readline())
def pump():
    for line in target.stdout:
        there.append(line.strip())
threading.Thread(target=pump, daemon=True).start()
ended = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, encoding="utf-8",
                       check=True)
seen = {}
for name, pid in (("live", target_pid), ("self", os.getpid()), ("ended", int(ended.stdout)), ("system", 4), ("zero", 0)):
    before = len(here), there.count("ctrl-c")
    try:
        alive = runio._pid_alive(pid)
    except Exception as exc:
        alive = repr(exc)
    time.sleep(0.5)  # 콘솔 신호는 따로 도착한다 — 그 처리기가 돌 틈
    seen[name] = {"pid": pid, "alive": alive, "ctrl_c_here": len(here) - before[0],
                  "ctrl_c_target": there.count("ctrl-c") - before[1]}
target.stdin.close()
target.wait(10)
print(json.dumps(seen), flush=True)
"""

_NO_FORK = pytest.mark.skipif(not hasattr(os, "fork"), reason="os.fork and os.register_at_fork are Unix only (Availability: "
                                                            "Unix, not Emscripten, not WASI) — Windows makes no fork child")


class _Child:
    """실제 프로세스 하나 — 표준 출력을 한 줄씩 받아, 한 줄을 얼마나 기다릴지 정할 수 있다. `pid` 는 자식이 처음 찍는 자기
    pid 다(Windows venv 의 python.exe 는 진짜 인터프리터를 띄우는 런처라 Popen.pid 와 다르다)."""

    def __init__(self, code: str, *args: object) -> None:
        self.process = subprocess.Popen([sys.executable, "-c", code, *map(str, args)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._err: list[str] = []
        self._out_pump = threading.Thread(target=self._pump_out, daemon=True)
        self._err_pump = threading.Thread(target=self._err.extend, args=(self.process.stderr,), daemon=True)
        self._out_pump.start()
        self._err_pump.start()
        said = self.line()
        assert said is not None and said.isdigit(), f"child did not start: {said}\n{self.stderr()}"
        self.pid = int(said)

    def _pump_out(self) -> None:
        for line in self.process.stdout:
            self._lines.put(line.strip())
        self._lines.put(None)

    def line(self, timeout: float = 10.0) -> str | None:
        """다음 줄 — 끝났으면 None, `timeout` 초 안에 없으면 "no answer"."""
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return "no answer"

    def go_on(self) -> None:
        """기다리는 한 걸음을 넘긴다."""
        with contextlib.suppress(OSError, ValueError):  # 이미 끝났거나 표준 입력을 닫았다
            self.process.stdin.write("\n")
            self.process.stdin.flush()

    def finish(self, timeout: float = 10.0) -> int:
        """남은 기다림을 모두 넘기고 끝날 때까지 기다린다 — 종료 코드."""
        with contextlib.suppress(OSError, ValueError):
            self.process.stdin.write("\n" * 8)
            self.process.stdin.close()
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait()

    def stderr(self) -> str:
        if self.process.poll() is not None:
            self._err_pump.join(5)
        return "".join(self._err)

    def close(self) -> None:
        """끝까지 기다리고 출력 파이프를 닫는다."""
        self.finish()
        for pump, pipe in ((self._out_pump, self.process.stdout), (self._err_pump, self.process.stderr)):
            pump.join(5)
            pipe.close()


@pytest.fixture
def start():
    """`start(code, *args)` 로 띄운 프로세스는 시험이 어떻게 끝나든 끝까지 기다린다."""
    started: list[_Child] = []

    def start(code: str, *args: object) -> _Child:
        started.append(_Child(code, *args))
        return started[-1]

    yield start
    for child in started:
        child.close()


def _dead_pid() -> int:
    """방금 끝난 프로세스의 pid — 죽은 주인의 자리."""
    gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                          encoding="utf-8", check=True)
    pid = int(gone.stdout)
    assert not runio._pid_alive(pid), f"pid {pid} is in use again"
    return pid


def _tree(folder: Path) -> dict[str, object]:
    """폴더 안 모든 것 — 상대 경로 → 파일 바이트 · "dir" · 링크 대상. 링크 안으로는 들어가지 않는다."""
    got: dict[str, object] = {}
    for root, dirs, files in os.walk(folder):
        for name in dirs + files:
            path = Path(root) / name
            got[path.relative_to(folder).as_posix()] = (f"link:{os.readlink(path)}" if path.is_symlink()
                                                        else "dir" if path.is_dir() else path.read_bytes())
    return got


def _aged(path: Path) -> None:
    """한 시간 전에 바뀐 것 — 락으로 읽히면 주인을 확인할 수 없는 오래된 락이라 회수 대상이다."""
    old = time.time() - 3600
    os.utime(path, (old, old))


def _seen(code: str, *args: object) -> list[dict]:
    """실제 프로세스 하나를 끝까지 돌려 그가 찍은 JSON 줄들."""
    got = subprocess.run([sys.executable, "-c", code, *map(str, args)], capture_output=True, encoding="utf-8", timeout=60)
    assert got.returncode == 0, got.stderr
    return [json.loads(line) for line in got.stdout.splitlines() if line.startswith("{")]


def _ended(pid: int) -> bool:
    """고아 `pid` 가 끝났나. 끝난 고아는 새 부모가 거둘 때까지 좀비로 남아 신호 0 에 찾힌다 — 컨테이너의 첫 프로세스처럼 아무도
    거두지 않는 부모 밑에서는 끝까지(GitHub 호스트 러너의 레인 컨테이너에서 이 기다림이 시험마다 한도 10 s 를 다 채웠다). 그래서
    리눅스에서는 그 상태(/proc/<pid>/stat 의 Z)도 끝난 것으로 읽는다. 거두는 부모가 있는 곳(macOS 의 launchd)에서는 신호 0 만으로
    곧 끝난 것이 보인다."""
    if not runio._pid_alive(pid):
        return True
    try:
        line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return line.rpartition(")")[2].split()[:1] == ["Z"]


def _wait_gone(pid: int | None, timeout: float = 10.0) -> None:
    """시험이 띄운 프로세스의 자식(이제 고아)이 끝날 때까지 — 길어야 `timeout` 초."""
    deadline = time.monotonic() + timeout
    while pid is not None and not _ended(pid) and time.monotonic() < deadline:
        time.sleep(0.05)


@pytest.mark.timeout(10)  # 거절은 곧바로다 — 회수를 되풀이하며 멈추면 이름 붙은 실패가 된다
@pytest.mark.parametrize("entry", ["link-to-an-old-file", "link-to-no-file", "absolute-link-to-no-file",
                                   "link-to-a-folder", "old-folder"])
def test_a_writer_refuses_a_lock_name_that_is_not_a_lock_file_and_never_follows_it(tmp_path, entry) -> None:
    run, outside = tmp_path / "run", tmp_path / "outside"
    run.mkdir()
    outside.mkdir()
    lock = run / runio.LOCK_FILENAME
    if entry == "old-folder":
        lock.mkdir()
        _aged(lock)
    elif entry == "absolute-link-to-no-file":  # 운영체제가 읽는 대상 — Windows 의 os.open 은 이 링크를 따라가 그 자리에 만든다
        lock.symlink_to(outside / "keep.txt")
    elif entry == "link-to-a-folder":
        lock.symlink_to(outside, target_is_directory=True)
    else:
        if entry == "link-to-an-old-file":
            (outside / "keep.txt").write_bytes(b"not a lock\n")
            _aged(outside / "keep.txt")
        lock.symlink_to("../outside/keep.txt")
    before = _tree(tmp_path)
    with pytest.raises(SystemExit, match=r"run[/\\]\.sprite-gen\.lock is not a lock file"):
        cli.main(["export-pngs", "--run-dir", str(run)])
    assert _tree(tmp_path) == before, "락 이름 자리를 따라가 무엇을 지우거나 만들었다"


def _descriptor(fd: int, path: Path) -> dict[str, object]:
    """`path` 에 열린 쓰기 fd 의 성질 — 상속되는지, 읽으면 어떻게 끝나는지, 쓴 바이트가 그대로 놓이는지, 열린 동안 그 이름을
    지울 수 있는지. fd 는 닫는다."""
    got: dict[str, object] = {"inheritable": os.get_inheritable(fd)}
    try:
        got["read"] = os.read(fd, 1)
    except OSError as exc:
        got["read"] = errno.errorcode.get(exc.errno, exc.errno)
    os.write(fd, b"a\nb\r\n")
    got["bytes"] = path.read_bytes()
    try:
        os.unlink(path)
        got["unlink-while-open"] = "removed"
    except PermissionError as exc:
        got["unlink-while-open"] = getattr(exc, "winerror", None) or exc.errno
    finally:
        os.close(fd)
    return got


def test_the_lock_entry_is_made_on_a_descriptor_like_the_one_os_open_gives(tmp_path) -> None:
    """락 항목을 만드는 fd(Windows 는 이름 자체를 여는 CreateFileW 의 핸들을 넘긴 fd)는 os.open(O_CREAT|O_EXCL|O_WRONLY) 이
    주던 fd 와 같은 성질이고, 이름에 새로 만든 그 일반 파일이다."""
    made_path, plain_path = tmp_path / "made", tmp_path / "plain"
    made = runio._make_entry(made_path)
    entry, seen = os.fstat(made), os.lstat(made_path)
    assert stat.S_ISREG(seen.st_mode) and (entry.st_dev, entry.st_ino) == (seen.st_dev, seen.st_ino)
    got = _descriptor(made, made_path)
    plain = _descriptor(os.open(plain_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)), plain_path)
    assert got == plain, f"made {got} / os.open {plain}"
    assert got["inheritable"] is False and got["bytes"] == b"a\nb\r\n", got


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 의 CreateFileW 핸들 — POSIX 는 os.open 이 바로 fd 다")
def test_a_lock_entry_handle_that_cannot_become_a_descriptor_is_closed_on_windows(tmp_path, monkeypatch) -> None:
    """Windows 에서 락 항목의 핸들을 fd 로 넘기지 못하면(fd 표가 찼다) 그 오류로 끝나고 핸들은 닫힌다 — 열린 핸들은 그 이름을
    지우지 못하게 막는데(공유 삭제 없음), 실패 뒤에는 지워지고 다시 만들어진다. 만든 빈 항목은 os.fstat 이 실패할 때처럼
    남는다(주인을 확인할 수 없는 락)."""
    lock = tmp_path / runio.LOCK_FILENAME
    held = runio._make_entry(lock)
    with pytest.raises(PermissionError):  # 열린 핸들은 지우기를 막는다 — 아래의 지우기가 닫힘을 잰다
        os.unlink(lock)
    os.close(held)
    os.unlink(lock)

    def full(handle: int, flags: int) -> int:
        raise OSError(errno.EMFILE, os.strerror(errno.EMFILE))

    monkeypatch.setattr(runio.msvcrt, "open_osfhandle", full)
    with pytest.raises(OSError) as failed:
        runio._make_entry(lock)
    monkeypatch.undo()
    assert failed.value.errno == errno.EMFILE
    assert lock.read_bytes() == b""
    os.unlink(lock)  # 핸들이 남았으면 PermissionError(WinError 32)
    os.close(runio._make_entry(lock))


def test_an_old_lock_file_whose_holder_cannot_be_verified_is_reclaimed(tmp_path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    lock = run / runio.LOCK_FILENAME
    lock.write_text(json.dumps({"owner": "a writer long gone"}), encoding="utf-8")  # pid 가 없다 — 주인을 확인할 수 없다
    _aged(lock)
    try:
        runio.acquire_run_dir_lock(run, "reclaimer")
        assert json.loads(lock.read_text(encoding="utf-8"))["owner"] == "reclaimer", "오래된 락을 회수하지 않았다"
    finally:
        runio.release_run_dir_lock(run)
    assert not lock.exists()


@pytest.mark.parametrize("after", ["live-writer-lock", "link"])
def test_a_writer_that_released_its_lock_leaves_what_took_the_name_after_it_when_it_exits(tmp_path, start, after) -> None:
    """명시 해제한 writer 가 끝날 때 그 뒤 이름에 들어선 것 — 다음 writer 가 잡은 살아 있는 락, 그 자리에 놓인 링크 — 은
    그대로다. 다음 writer 가 쥔 동안 셋째 writer 는 그 락을 대며 거절된다 (종단 반려 netti-20261023-132314 의 반례 둘)."""
    run, outside = tmp_path / "run", tmp_path / "outside"
    run.mkdir()
    outside.mkdir()
    lock = run / runio.LOCK_FILENAME
    first = start(_STEPS, run, "first-writer", "release", "wait")
    assert [first.line(), first.line(), first.line()] == ["trying", "first-writer", "release"], first.stderr()
    assert not os.path.lexists(lock)
    if after == "live-writer-lock":
        successor = start(_STEPS, run, "successor-writer", "wait")
        assert [successor.line(), successor.line()] == ["trying", "successor-writer"], successor.stderr()
    else:
        (outside / "keep.txt").write_bytes(b"not a lock\n")
        symlink_or_skip(lock, Path("../outside/keep.txt"))
    before = _tree(tmp_path)
    assert first.finish() == 0, first.stderr()
    assert _tree(tmp_path) == before, "락을 놓은 writer 가 끝나면서 그 뒤 이름에 들어선 것을 지웠다"
    if after == "live-writer-lock":
        third = start(_STEPS, run, "third-writer")
        assert third.finish() == 1, "다음 writer 가 쥔 런 폴더를 셋째 writer 가 잡았다"
        assert f"run dir is locked by successor-writer (pid {successor.pid})" in third.stderr()


@pytest.mark.parametrize("steps", [["writer"], ["first-take", "release", "second-take"]],
                         ids=["never-released", "released-and-taken-again"])
def test_a_writer_that_still_holds_its_lock_removes_it_when_it_exits(tmp_path, start, steps) -> None:
    """쥔 채 끝나는 writer 는 자기 락을 지운다 — 해제하지 않았을 때도, 해제한 뒤 같은 프로세스에서 다시 잡았을 때도(그 새
    락). 해제가 종료 처리를 거두어도 다시 잡은 락의 종료 처리는 남는다."""
    run = tmp_path / "run"
    run.mkdir()
    lock = run / runio.LOCK_FILENAME
    writer = start(_STEPS, run, *steps, "wait")
    said = [word for step in steps for word in (["release"] if step == "release" else ["trying", step])]
    assert [writer.line() for _ in said] == said, writer.stderr()
    holder = json.loads(lock.read_text(encoding="utf-8"))
    assert (holder["owner"], holder["pid"]) == (steps[-1], writer.pid)
    assert writer.finish() == 0, writer.stderr()
    assert not os.path.lexists(lock), "쥔 락을 남기고 끝났다"


@pytest.mark.slow
def test_a_late_reclaimer_and_another_writer_queue_so_only_one_of_them_holds_the_run_dir(tmp_path, start) -> None:
    """죽은 주인의 락을 회수하는 일과 새 락을 만드는 일은 줄을 선다 (회수 경합, 설계 판정 eve-20261023-135225).

    늦은 회수자가 옛 락의 주인이 죽었다고 판정한 자리에서 멈춘 동안, 다른 writer 는 락을 만들지 못하고 기다리며 옛 락은
    그대로다. 회수자가 판정한 그 옛 락을 지우고 잡은 뒤에야 writer 의 차례가 오고, writer 는 살아 있는 회수자의 락을 대며
    거절된다. 두 writer 가 함께 쥐는 일은 없다."""
    run = tmp_path / "run"
    run.mkdir()
    lock = run / runio.LOCK_FILENAME
    lock.write_text(json.dumps({"owner": "a writer that died", "pid": _dead_pid(), "started": 0}), encoding="utf-8")
    stale, stale_bytes = os.lstat(lock), lock.read_bytes()
    slow = start(_SLOW_RECLAIMER, run)
    assert slow.line() == "judged-stale", slow.stderr()
    writer = start(_STEPS, run, "writer", "wait")
    assert writer.line() == "trying", writer.stderr()
    during_pause = writer.line(timeout=3.0)
    try:
        now, now_bytes = os.lstat(lock), lock.read_bytes()
    except FileNotFoundError:  # 멈추지 않은 writer 가 그 사이 지웠다
        now = now_bytes = None
    stale_kept = (now is not None and (now.st_dev, now.st_ino) == (stale.st_dev, stale.st_ino)
                  and now_bytes == stale_bytes)
    slow.go_on()
    said = {"slow-reclaimer": slow.line(), "writer": during_pause if during_pause != "no answer" else writer.line()}
    holders = sorted(name for name, line in said.items() if line == name)
    assert len(holders) == 1, f"두 writer 가 함께 런 폴더를 쥐었다: {holders} (회수자가 판정한 동안 writer: {during_pause})"
    assert during_pause == "no answer" and stale_kept, "회수자가 옛 락을 판정한 동안 다른 writer 가 락을 만들거나 옛 락을 건드렸다"
    assert holders == ["slow-reclaimer"], said
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == slow.pid
    assert writer.finish() == 1, writer.stderr()
    assert f"run dir is locked by slow-reclaimer (pid {slow.pid})" in writer.stderr()
    assert slow.finish() == 0, slow.stderr()
    assert not os.path.lexists(lock)


def _signal_zero(pid: int) -> bool:
    """이 계약 전의 runio._pid_alive 그대로(main 7404145) — POSIX 판정은 이것과 같은 답이다."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 의 신호 0 판정 — Windows 는 콘솔 Ctrl+C 시험")
def test_a_lock_holders_pid_is_judged_on_posix_by_signal_zero_as_before(tmp_path, start, monkeypatch) -> None:
    """POSIX 의 락 주인 판정은 이 계약 전과 같다 — 살아 있는 자식·자신·끝난 프로세스·남의 프로세스(pid 1)에 같은 답이고,
    그 사이 부르는 것은 os.kill(pid, 0) 하나다."""
    live = start(_STEPS, tmp_path, "wait")
    pids = {"live": live.pid, "self": os.getpid(), "ended": _dead_pid(), "another user's": 1}
    before = {name: _signal_zero(pid) for name, pid in pids.items()}
    sent: list[tuple[int, int]] = []
    real_kill = os.kill

    def kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        real_kill(pid, sig)

    monkeypatch.setattr(os, "kill", kill)
    now = {name: runio._pid_alive(pid) for name, pid in pids.items()}
    monkeypatch.undo()
    assert now == before == {"live": True, "self": True, "ended": False, "another user's": True}
    assert sent == [(pid, 0) for pid in pids.values()]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 의 콘솔 Ctrl+C — POSIX 는 신호 0 시험")
def test_a_lock_holders_pid_is_judged_on_windows_without_a_ctrl_c_to_its_console() -> None:
    """Windows 의 락 주인 판정은 콘솔에 Ctrl+C 를 보내지 않는다 — 거기서 os.kill(pid, 0) 은 CTRL_C_EVENT 라 같은 콘솔의
    프로세스들에 Ctrl+C 를 보낸다. 살아 있는 대상·자신·System(pid 4, 열 수 없는 남의 프로세스)은 살아 있고, 끝난 프로세스와 0
    은 아니다. 그 사이 같은 콘솔의 탐침도 대상도 Ctrl+C 를 받지 않는다."""
    hidden = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=0)  # 새 콘솔 창을 띄우지 않는다
    got = subprocess.run([sys.executable, "-c", _CONSOLE_PROBE], capture_output=True, encoding="utf-8", timeout=60,
                         creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=hidden)
    assert got.returncode == 0, got.stderr
    seen = json.loads(got.stdout.splitlines()[-1])
    assert {name: case["alive"] for name, case in seen.items()} == {
        "live": True, "self": True, "ended": False, "system": True, "zero": False}, seen
    assert all(case["ctrl_c_here"] == case["ctrl_c_target"] == 0 for case in seen.values()), seen


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_FSIZE and SIGXFSZ are POSIX")
def test_a_writer_that_could_not_write_its_lock_takes_back_what_it_made(tmp_path, start) -> None:
    """락 항목을 만들고 내용을 쓰다 끊긴 writer(파일 크기 한도에서 EFBIG — 디스크가 찬 것과 같은 자리)는 그 만들다 만 항목을
    거두고 실패한다. 주인을 확인할 수 없는 그 항목이 남으면 다음 writer 가 15분 동안 거절된다."""
    run = tmp_path / "run"
    run.mkdir()
    lock = run / runio.LOCK_FILENAME
    writer = start(_CUT_SHORT_WRITER, run)
    assert writer.line() == f"failed {errno.EFBIG}", writer.stderr()
    assert writer.finish() == 0, writer.stderr()
    assert not os.path.lexists(lock), f"다 쓰지 못한 락 항목이 남았다: {lock.read_bytes()!r}"
    following = start(_STEPS, run, "following-writer")
    assert following.finish() == 0, following.stderr()


@_NO_FORK
@pytest.mark.parametrize(("kind", "mode"), [(kind, mode) for kind in ("raw", "pool", "c-fork")
                                            for mode in ("end", "release", "acquire-same")] + [("pool", "acquire-other")])
def test_a_forked_child_neither_removes_nor_holds_its_parents_lock(tmp_path, kind, mode) -> None:
    """락은 잡은 프로세스의 것이다 — 락을 쥔 부모가 fork 한 자식이 끝나거나 놓거나 같은 런 폴더를 잡아도 부모의 살아 있는 락은
    그대로고, 자식의 같은 런 폴더 잡기와 다른 writer 는 그 락을 대며 거절된다 (종단 반려 netti-20261023-210333 의 반례 둘이
    raw 의 end·release, 나머지는 설계 판정 eve-20261023-211033 의 fork 갈래). 풀 워커가 잡은 다른 런 폴더의 락은 워커의 것이다
    — 3.12 까지는 워커가 os._exit 로 끝나 남고 죽은 pid 의 락이라 다음 writer 가 회수하며, 3.13 부터는 워커가 끝나며 지운다.
    어느 쪽이든 다음 writer 는 그 런 폴더를 잡는다."""
    run, other = tmp_path / "run", tmp_path / "other"
    run.mkdir()
    other.mkdir()
    [seen] = _seen(_FORKED, run, other, kind, mode)
    refused = f"run dir is locked by live-parent (pid {seen['parent_pid']})"
    assert seen["child_exit"] == 0, seen
    if mode == "acquire-same":
        assert seen["said"] == f"refused: {refused}: {run}", ("fork 자식이 부모가 쥔 런 폴더를 잡았다", seen)
    assert seen["parent_lock_kept"], ("fork 자식이 부모의 살아 있는 락을 지웠다", seen)
    assert seen["another_writer_rc"] == 1 and refused in seen["another_writer_stderr"], seen
    if mode == "acquire-other":
        assert seen["said"] == f"held {other.resolve() / runio.LOCK_FILENAME}", seen
        assert seen["other_next_writer_rc"] == 0, seen


@_NO_FORK
@pytest.mark.parametrize("mode", ["no-child", "fork"])
def test_a_holder_killed_inside_the_run_dirs_kernel_lock_leaves_it_to_no_child(tmp_path, mode) -> None:
    """주인이 런 폴더 커널 락 안에서(죽은 주인의 락을 판정하는 동안) 죽으면 커널이 그 락을 놓는다 — 그 사이 다른 스레드가 fork 한
    자식이 살아 있어도 자식은 물려받은 커널 락을 쥐지 않아, writer 는 자식이 끝나기를 기다리지 않고 죽은 주인의 락을 회수해
    잡는다 (설계 판정 eve-20261023-211033)."""
    run, marks = tmp_path / "run", tmp_path / "marks"
    run.mkdir()
    marks.mkdir()
    dead = _dead_pid()
    (run / runio.LOCK_FILENAME).write_text(json.dumps({"owner": "a writer that died", "pid": dead, "started": 0}),
                                           encoding="utf-8")
    # 주인의 출력은 파이프가 아니라 파일로 — 자식이 물려받은 파이프면 subprocess.run 이 자식이 끝날 때까지 기다린다
    with open(marks / "holder.txt", "w", encoding="utf-8") as said:
        holder = subprocess.run([sys.executable, "-c", _KILLED_IN_THE_GUARD, *map(str, (run, marks, dead, mode))],
                                stdout=said, stderr=said, timeout=60)
    assert holder.returncode == -signal.SIGKILL, (marks / "holder.txt").read_text(encoding="utf-8")
    child = int((marks / "child").read_text()) if mode == "fork" else None
    try:
        assert child is None or runio._pid_alive(child), "주인이 fork 한 자식이 writer 보다 먼저 끝났다"
        writer = subprocess.run([sys.executable, "-c", _TIMED_WRITER, str(run)], capture_output=True, encoding="utf-8",
                                timeout=60)
    finally:
        (marks / "go").touch()
        _wait_gone(child)
    assert writer.returncode == 0, writer.stderr
    assert float(writer.stdout) < 2.0, f"죽은 주인의 fork 자식이 쥔 커널 락을 writer 가 {writer.stdout.strip()} s 기다렸다"


@_NO_FORK
@pytest.mark.slow
def test_a_fork_child_that_ends_leaves_its_parent_inside_the_run_dirs_kernel_lock(tmp_path, start) -> None:
    """fork 자식은 물려받은 커널 락 사본을 닫기만 하고 풀지 않는다 — 부모의 한 스레드가 런 폴더 커널 락 안에서 판정하는 동안
    다른 스레드가 fork 한 자식이 끝나도 writer 는 기다리고, 부모가 판정한 옛 락을 회수해 잡은 뒤에야 그 락을 대며 거절된다."""
    run = tmp_path / "run"
    run.mkdir()
    dead = _dead_pid()
    (run / runio.LOCK_FILENAME).write_text(json.dumps({"owner": "a writer that died", "pid": dead, "started": 0}),
                                           encoding="utf-8")
    parent = start(_JUDGING_PARENT, run, dead)
    assert parent.line() == "forked", parent.stderr()
    writer = start(_STEPS, run, "writer")
    assert writer.line() == "trying", writer.stderr()
    during = writer.line(timeout=3.0)
    parent.go_on()
    assert parent.line() == "judging-parent", parent.stderr()
    assert during == "no answer", f"부모가 커널 락 안에 있는데 fork 자식이 끝나자 writer 가 들어왔다: {during}"
    assert writer.finish() == 1, writer.stderr()
    assert f"run dir is locked by judging-parent (pid {parent.pid})" in writer.stderr()
    assert parent.finish() == 0, parent.stderr()


@_NO_FORK
@pytest.mark.parametrize("finish", ["release", "exit"])
def test_a_parent_whose_forked_children_ended_still_takes_and_removes_its_own_lock(tmp_path, finish) -> None:
    """자식들(맨 os.fork, libc fork, fork 풀 워커)이 돌고 끝난 뒤에도 부모는 자기 락에 다시 들어오고(재진입), 놓거나 쥔 채
    끝나며 그 락을 지운다 — fork 가 자식의 몫을 지우면서 부모의 보유까지 지우지 않는다."""
    run = tmp_path / "run"
    run.mkdir()
    lock = run / runio.LOCK_FILENAME
    seen = _seen(_PARENT_AFTER_CHILDREN, run, finish)
    assert seen[0] == {"reentered": True, "kept": True}, seen
    if finish == "release":
        assert seen[1] == {"left_after_release": False}, "자식들이 끝난 뒤 부모가 놓았는데 자기 락이 남았다"
    assert not os.path.lexists(lock), "자식들이 끝난 뒤 부모가 끝나면서 자기 락을 남겼다"


def _writer_after_the_holder_is_killed_with_a_child_alive(run: Path, marks: Path, code: str, *args: object):
    """`code` 를 주인으로 띄워 그가 표시 파일 `inside`(멈출 자리에 닿음)와 `child`(fork 한 자식의 pid — 자식이 다른 이름에 써서
    옮겨 놓아 쓰다 만 것을 읽지 않는다)를 남기면 SIGKILL 하고, 그 자식이 살아 있는 동안 락을 잡는 writer 를 끝까지 돌린다 —
    (writer 결과, 주인이 남긴 표시 파일 내용)."""
    # 주인의 출력은 파이프가 아니라 파일로 — 자식이 물려받은 파이프면 읽는 쪽이 자식이 끝날 때까지 기다린다
    with open(marks / "holder.txt", "w", encoding="utf-8") as said:
        holder = subprocess.Popen([sys.executable, "-c", code, *map(str, (run, marks, *args))], stdout=said, stderr=said)
    child = None
    try:
        deadline = time.monotonic() + 20
        while not ((marks / "inside").exists() and (marks / "child").exists()):
            assert holder.poll() is None and time.monotonic() < deadline, (marks / "holder.txt").read_text(encoding="utf-8")
            time.sleep(0.01)
        child = int((marks / "child").read_text())
        holder.kill()
        holder.wait(timeout=10)
        assert runio._pid_alive(child), "주인이 fork 한 자식이 writer 보다 먼저 끝났다"
        writer = subprocess.run([sys.executable, "-c", _TIMED_WRITER, str(run)], capture_output=True, encoding="utf-8",
                                timeout=60)
    finally:
        (marks / "go").touch()
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)
        _wait_gone(child)
    left = {path.name: path.read_text(encoding="utf-8") for path in sorted(marks.iterdir())
            if path.name in ("injected", "handler", "opened")}
    return writer, left


_FD_RECORD_POINTS = {  # fd 기록이 바뀌는 자리 → (표시 줄, 그 줄이 돌기 직전 · 돈 바로 다음 줄)
    "open-record": ("_GUARD_FDS.add(fd)", "at"),  # 열었고 아직 기록하지 않았다
    "record-lock": ("_lock_fd(fd, exclusive=True)", "at"),  # 잠글 그 fd 를 기록했고 아직 잠그지 않았다
    "locked-unlock": ("_unlock_fd(fd)", "at"),  # 첫 풀기를 건너뛰어 잠긴 채 기록돼 있고, 마지막 풀기 직전이다
    "unlock-unrecord": ("_GUARD_FDS.discard(fd)", "at"),  # 마지막 풀기 뒤, 기록에서 빼기 직전
    "unrecord-close": ("_GUARD_FDS.discard(fd)", "after"),  # 기록에서 뺐고 아직 닫지 않았다
}


@_NO_FORK
@pytest.mark.parametrize("helper", ["no-helper", "helper-thread"])
@pytest.mark.parametrize("point", list(_FD_RECORD_POINTS))
def test_a_handler_fork_where_the_kernel_lock_fd_enters_or_leaves_the_record_leaves_that_lock_to_no_child(
        tmp_path, point, helper) -> None:
    """주 스레드의 시그널 처리기가 런 폴더 커널 락 fd 의 기록이 바뀌는 자리 — 열기→기록, 기록→잠금, 풀기(잠긴 채 마지막 풀기 직전과
    그 뒤)→빼기, 빼기→닫기 — 에서 fork 하고 주인이 그 자리에서 SIGKILL 로 죽어도, 살아 있는 그 자식은 커널 락을 쥐지 않아 writer 가
    기다리지 않는다. 닫기 쪽은 첫 풀기를 Ctrl-C 가 건너뛰었을 때다. 신호를 막지 않는 다른 스레드가 있어도 같다 (종단 반려
    netti-20261023-232602, 설계 판정 kongkongi-20261023-233626 의 A1·A2)."""
    run, marks = tmp_path / "run", tmp_path / "marks"
    run.mkdir()
    marks.mkdir()
    writer, left = _writer_after_the_holder_is_killed_with_a_child_alive(run, marks, _HANDLER_FORK,
                                                                         *_FD_RECORD_POINTS[point], helper)
    print(json.dumps({"point": point, "helper": helper, **left, "writer_seconds": writer.stdout.strip()}), flush=True)
    assert writer.returncode == 0, writer.stderr
    assert float(writer.stdout) < 2.0, (f"주인이 {point} 자리에서 fork 한 자식이 쥔 커널 락을 writer 가 {writer.stdout.strip()} s "
                                        f"기다렸다: {left}")


@_NO_FORK
@pytest.mark.parametrize("where", ["past-the-hook", "inside-the-hook"])
@pytest.mark.slow
def test_a_fork_from_another_thread_that_runs_runios_fork_hook_leaves_the_kernel_lock_to_no_child(tmp_path, where) -> None:
    """다른 스레드의 fork 는 runio 의 before 훅에 들어간 뒤 fork 시스템 호출 전에 GIL 을 놓고 기다릴 수 있다 — 훅 안의 두 문장 사이,
    또는 훅을 지나 뒤에 도는 logging 의 before 훅·임포트 락에서. 그동안 런 폴더 커널 락을 잡으러 들어간 스레드가 fd 를 열고
    기록하기 전에 그 fork 가 일어나면 자식은 기록 밖 사본을 갖는다 — 그 설명을 잠그면 주인이 그 안에서 죽었을 때 writer 가 자식을
    기다린다. fork 횟수만으로는 열기 전에 셈해진 fork 가 보이지 않는다. 주인은 그 fork 가 돌아올 때까지 열지 않고, writer 는
    기다리지 않는다. 두 자리가 순서 둘을 잰다: 훅은 진행 중 표시를 넣고 나서 횟수를 올리고(inside-the-hook), 커널 락 헬퍼는
    횟수를 읽고 나서 표시를 본다(past-the-hook — 횟수를 읽는 줄에 닿은 뒤 fork 가 시작한다)."""
    run, marks = tmp_path / "run", tmp_path / "marks"
    run.mkdir()
    marks.mkdir()
    writer, left = _writer_after_the_holder_is_killed_with_a_child_alive(run, marks, _FORK_FROM_ANOTHER_THREAD, where)
    print(json.dumps({"where": where, **left, "writer_seconds": writer.stdout.strip()}), flush=True)
    assert writer.returncode == 0, writer.stderr
    assert float(writer.stdout) < 2.0, (f"다른 스레드의 fork({where}) 자식이 쥔 커널 락을 writer 가 {writer.stdout.strip()} s "
                                        f"기다렸다: {left}")


@_NO_FORK
@pytest.mark.parametrize("finish", ["release", "exit"])
@pytest.mark.slow
def test_a_fork_from_another_thread_that_does_not_return_makes_the_writer_fail_loudly_and_leave_nothing(tmp_path,
                                                                                                         finish) -> None:
    """런 폴더 커널 락 헬퍼는 다른 스레드의 진행 중 fork 를 끝없이 기다리지 않는다 — 한도(여기서는 0.5 s) 안에 그 fork 가 돌아오지
    않으면 커널 락 fd 를 열지 않은 채 그 까닭을 대며 크게 실패한다. 잡기는 아무것도 남기지 않고(기록도 락 항목도), 놓기는 쥔 그대로라
    같은 프로세스가 다시 들어오고, 그 fork 가 돌아온 뒤 다시 놓거나 놓지 않은 채 끝나면(종료 처리) 자기 락을 지운다 — 놓다 실패한
    writer 가 제 pid 의 락에 스스로 막히지 않는다. 그 fork 가 돌아온 뒤에는 다른 런 폴더도 잡는다."""
    run, other = tmp_path / "run", tmp_path / "other"
    run.mkdir()
    other.mkdir()
    [seen] = _seen(_FORK_THAT_DOES_NOT_RETURN, run, other, finish)
    print(json.dumps({"finish": finish, **seen}), flush=True)
    for step, folder in (("release", run), ("acquire", other)):
        assert seen[step]["said"] == (
            f"refused: run dir lock in {folder.resolve()}: another thread's fork is still marked in progress after 0.5s, and "
            f"sprite-gen will not open its kernel lock on the run dir while a fork may copy it"), seen
        assert 0.5 <= seen[step]["seconds"] < 3.0, seen
    assert seen["recorded_fds"] == [] and not seen["other_entry_left"], seen
    assert seen["re_entry"]["said"] == "done" and seen["entry_owner"] == "holder", ("놓다 실패한 락을 쥐지 않게 됐다", seen)
    assert seen["other_owner_after_the_fork_returned"] == "after-the-fork", seen
    if finish == "release":
        assert not seen["entry_left_after_release"], ("fork 가 돌아온 뒤 놓았는데 자기 락이 남았다", seen)
    assert not os.path.lexists(run / runio.LOCK_FILENAME), ("놓다 실패한 락을 다시 놓거나 끝나며 지우지 않았다", finish)


@_NO_FORK
@pytest.mark.parametrize("mode", ["forking-thread", "c-fork-child"])
def test_a_fork_mark_that_cannot_copy_the_kernel_lock_fd_does_not_hold_up_a_writer(tmp_path, mode) -> None:
    """런 폴더 커널 락 헬퍼가 다른 스레드의 진행 중 fork 를 기다리는 것은 그 fork 가 지금 여는 fd 를 복사할 수 있어서다 — 그럴 수 없는
    표시에는 기다리지 않는다. fork 하는 스레드 자신의 표시(그 스레드가 락 헬퍼를 도는 동안 그 fork 는 일어나지 않는다)와, 훅을 거치지
    않은 C fork 자식이 물려받은 부모 다른 스레드의 표시(그 fork 는 부모에서 일어난다)에는 곧바로 잡고 놓는다."""
    run = tmp_path / "run"
    run.mkdir()
    [seen] = _seen(_LOCK_TAKEN_DURING_A_FORK, run, mode)
    print(json.dumps({"mode": mode, **seen}), flush=True)
    assert seen["said"] == "held and released", seen
