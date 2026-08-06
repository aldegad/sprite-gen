# SPDX-License-Identifier: Apache-2.0
"""`sprite-request.json` 의 read-modify-write 는 격리 도메인 **하나** 안에서만 일어난다
(plan sprite-gen/state-revision-read-mutation).

이 파일이 소유하는 계약은 조회 무변(그건 `test_request_read_no_mutation.py` 가 갖는다)이
아니라 **쓰기끼리의 상호배제**다:

- request 를 read-modify-write 하는 모든 프로덕션 경로 — 이관 writer(`migrate_request_file`),
  리롤 테이크 기록(`reroll.record_take`), 트윈 테이크 기록(`interpolate.write_take`), 뷰의
  fps POST — 는 **같은 `publish_guard` 배타락** 안에서 락 획득 **후** fresh 재독하고 원자
  교체한다. 그래서 lost update 가 구조적으로 불가능하다.

왜 이 파일이 생겼나 (노을이 검수 note 1, 2026-08-02): 이관 writer 는 문서를 락 **밖에서**
읽고 나서 `.sprite-gen.lock` (파이프라인 단계용 단일 writer 락)을 잡았다. 두 가지가 동시에
틀렸다 — (a) read-then-lock 이라 읽은 뒤 잡기 전 사이의 편집을 stale 문서로 덮고,
(b) 편집 writer 들은 `publish_guard` 를 쓰므로 **서로 배제하지 않는 두 도메인**이었다.
밀리초 창이지만 리롤이 방금 기록한 테이크가 이관 한 번에 통째로 사라질 수 있었다.
"""

from __future__ import annotations

import ast
import json
import threading
import time
from pathlib import Path

from PIL import Image

from sprite_gen.effects import interpolate, reroll
from sprite_gen.spec import runio
from sprite_gen.spec.runio import REQUEST_FILENAME, migrate_request_file

# 이관 writer 가 문서를 읽은 **뒤** 쓰기 전까지 편집자에게 주는 시간. 배제되지 않는 두 도메인
# (수리 전 코드)이라면 이 창 안에서 편집이 통째로 완료되고, 이관이 그것을 덮어 잃는다.
# 배제되면 편집자는 이 시간 동안 락에서 대기만 하고 아무것도 잃지 않는다.
RACE_WINDOW_SECONDS = 0.3


def _write_request(run_dir: Path, fit: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / REQUEST_FILENAME).write_text(json.dumps({
        "version": 1, "kind": "sprite-gen-request", "engine": "component-row",
        "character": {"id": "racebot", "description": "", "base_image": None},
        "cell": {"shape": "square", "width": 96, "height": 96, "safe_margin_x": 8,
                 "safe_margin_y": 8, "size": 96, "safe_margin": 8},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255],
                       "selection": "fallback"},
        "states": {"walk": {"frames": 2, "fps": 8, "loop": True, "action": "x"}},
        "fit": fit,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_the_race(monkeypatch, run_dir: Path, edit) -> None:
    """이관 writer 의 read→write 사이에 편집 writer 를 밀어 넣고 둘 다 끝날 때까지 기다린다.

    이관자가 문서를 읽은 직후 `read_done` 을 올리고, 편집자가 "지금 들어간다"(`edit_attempted`)
    를 올릴 때까지 기다린 뒤 `RACE_WINDOW_SECONDS` 를 더 준다. 배제가 없으면 편집이 그 창
    안에서 완주하고 이관의 stale 쓰기에 지워진다. 배제되면 편집자는 창 내내 락 대기다."""
    read_done = threading.Event()
    edit_attempted = threading.Event()
    failures: list[BaseException] = []
    real_read = runio._read_request_document

    def hooked(target_dir):
        document = real_read(target_dir)
        if threading.current_thread().name == "migrator":
            read_done.set()
            edit_attempted.wait(5.0)
            time.sleep(RACE_WINDOW_SECONDS)
        return document

    monkeypatch.setattr(runio, "_read_request_document", hooked)

    def migrate() -> None:
        try:
            migrate_request_file(run_dir, apply=True)
        except BaseException as exc:                      # noqa: BLE001 - 스레드 밖으로 옮긴다
            failures.append(exc)
            read_done.set()

    def editor() -> None:
        try:
            read_done.wait(5.0)
            edit_attempted.set()
            edit()
        except BaseException as exc:                      # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=migrate, name="migrator"),
               threading.Thread(target=editor, name="editor")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30.0)
        assert not thread.is_alive(), f"{thread.name} 스레드가 끝나지 않았다 (락 교착 의심)"
    assert not failures, failures


def test_migration_racing_a_reroll_take_loses_neither_write(tmp_path, monkeypatch) -> None:
    """이관 vs 리롤 테이크 기록: 둘 다 살아남는다 (lost update 불가).

    수리 전에는 이관자가 락 밖에서 읽은 stale 문서를 원자 교체해, 그 사이 리롤이 기록한
    `states.walk.takes` 가 흔적 없이 사라졌다 (반대 순서면 이관이 조용히 되돌아간다)."""
    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": True, "logical_height": 48})

    _run_the_race(monkeypatch, run_dir,
                  lambda: reroll.record_take(run_dir, "walk", "reroll1", 2))

    on_disk = json.loads((run_dir / REQUEST_FILENAME).read_text(encoding="utf-8"))
    assert on_disk["fit"] == {"logical_height": 48, "pixel_unfake": True}, "이관이 사라졌다"
    assert on_disk["states"]["walk"].get("takes") == [{"label": "reroll1", "frames": 2}], (
        "리롤이 기록한 테이크가 이관의 stale 쓰기에 지워졌다 — 두 writer 가 서로 배제되지 않는다")


def test_migration_racing_an_interpolate_take_loses_neither_write(tmp_path, monkeypatch) -> None:
    """이관 vs 트윈 테이크 기록: 같은 계약, 같은 락 (`interpolate.write_take`)."""
    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": True, "logical_height": 48})
    request = json.loads((run_dir / REQUEST_FILENAME).read_text(encoding="utf-8"))
    image = Image.new("RGBA", (8, 8), (255, 0, 255, 255))

    _run_the_race(monkeypatch, run_dir,
                  lambda: interpolate.write_take(run_dir, request, "walk", "tween1", image))

    on_disk = json.loads((run_dir / REQUEST_FILENAME).read_text(encoding="utf-8"))
    assert on_disk["fit"] == {"logical_height": 48, "pixel_unfake": True}, "이관이 사라졌다"
    assert on_disk["states"]["walk"].get("takes") == [{"label": "tween1", "frames": 1}], (
        "트윈이 기록한 테이크가 이관의 stale 쓰기에 지워졌다")


def test_migration_does_not_take_the_pipeline_writer_lock(tmp_path) -> None:
    """이관은 `.sprite-gen.lock` 도메인을 쓰지 않는다 — 그 락은 request 를 지키지 않는다.

    파이프라인 락이 지키는 것은 프레임·아틀라스 같은 단계 산출물이고, 추출·굽기는
    `sprite-request.json` 을 쓰지 않는다. 안 만지는 자원의 락을 잡으면 보호받는다는 착시만
    남는다 — 실제 경쟁자(리롤·트윈·fps 편집)는 그 락을 안 잡으므로 그대로 통과했다.
    한 자원에 격리 도메인은 하나이고, request 의 도메인은 `publish_guard` 다."""
    from sprite_gen.spec.runio import LOCK_FILENAME

    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": True})
    # 다른 살아있는 홀더의 최소 모형 (pid 1 = launchd/init) — 이관은 이걸 보지 않는다
    (run_dir / LOCK_FILENAME).write_text(
        json.dumps({"owner": "extract", "pid": 1, "started": 0}), encoding="utf-8")

    assert migrate_request_file(run_dir, apply=True) is True
    assert json.loads((run_dir / REQUEST_FILENAME).read_text(
        encoding="utf-8"))["fit"] == {"pixel_unfake": True}
    # 남의 락을 소비하지도 않는다 (파일은 그대로)
    assert json.loads((run_dir / LOCK_FILENAME).read_text(encoding="utf-8"))["owner"] == "extract"


def test_migration_leaves_no_lock_residue_in_the_run_dir(tmp_path) -> None:
    """이관이 끝나면 런 디렉터리에 새로 남는 파일이 없다 — request 하나만 바뀐다.

    publish rwlock 사이드카는 런 디렉터리 **밖**(`.<name>.sg-rwlock`)에 산다. 안에 남으면
    이관 한 번이 그 런의 파일 목록·세대 판정을 건드린다."""
    from sprite_gen.spec.runio import LOCK_FILENAME

    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": True})
    before = {p.name for p in run_dir.iterdir()}

    migrate_request_file(run_dir, apply=True)

    assert {p.name for p in run_dir.iterdir()} == before
    assert not (run_dir / LOCK_FILENAME).exists()


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _calls_inside_publish_guard(tree: ast.AST) -> set[int]:
    """`with publish_guard(...)` 블록 안에 있는 Call 노드 id 집합."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        if not any(isinstance(item.context_expr, ast.Call)
                   and _callee_name(item.context_expr.func) == "publish_guard"
                   for item in node.items):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                guarded.add(id(child))
    return guarded


def test_every_request_write_is_inside_the_publish_guard() -> None:
    """구조 단정: request 를 되쓰는 프로덕션 호출은 전부 `publish_guard` 블록 안에 있다.

    런타임 경쟁 테스트만 두면 새 편집 진입점(다음 POST 핸들러)이 락 밖에서 `write_request`
    를 부르는 회귀를 못 잡는다 — 그 경로를 밟는 fixture 가 없으면 초록으로 지나간다. 그래서
    "락 안에 있다" 를 AST 로 본다.

    범위: 되쓰기 게이트(`write_request`) 호출 전부 + 이관 writer 의 원자 교체. 새 런을
    **만드는** 경로(`prepare`, `unpack_atlas` 의 스테이징)는 제외한다 — 기존 문서를 읽어
    고치는 것이 아니라 처음 발행하는 것이고, unpack 의 발행 스왑 자체는 `_publish_run` 의
    `publish_guard` 안에서 일어난다."""
    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in sorted((root / "sprite_gen").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded = _calls_inside_publish_guard(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _callee_name(node.func) != "write_request":
                continue
            if id(node) not in guarded:
                offenders.append(f"{path.relative_to(root)}:{node.lineno} write_request()")

    runio_path = root / "sprite_gen" / "spec" / "runio.py"
    runio_tree = ast.parse(runio_path.read_text(encoding="utf-8"))
    runio_guarded = _calls_inside_publish_guard(runio_tree)
    for node in ast.walk(runio_tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "migrate_request_file":
            continue
        for child in ast.walk(node):
            if (isinstance(child, ast.Call) and _callee_name(child.func) == "atomic_write_text"
                    and id(child) not in runio_guarded):
                offenders.append(f"sprite_gen/spec/runio.py:{child.lineno} "
                                 f"atomic_write_text() in migrate_request_file()")

    assert not offenders, (
        "publish_guard 밖에서 request 를 되쓰는 경로:\n  " + "\n  ".join(offenders)
        + "\n→ 락 획득 후 fresh 재독 → 원자 교체. request 의 격리 도메인은 publish_guard 하나다")
