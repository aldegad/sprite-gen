# SPDX-License-Identifier: Apache-2.0
"""은퇴한 `pixel_perfect` 키 -> 현행 `pixel_unfake` 이관 (plan sprite-gen/pixel-unfake-rename).

용어 근거 (vault `domains/tools/spritefusion-pixel-snapper.md` 각주): "픽셀 퍼펙트" 는 광의
통용어(UI 정합·충돌판정)의 오용이고, 이 파이프라인이 하는 일의 정확한 명칭은 격자 스냅 /
재양자화 — 커뮤니티 속칭 **unfake** (unfake.js 유래). 어휘를 코드·스키마까지 통일하되, 기존
런(solvell 94개)은 일회성 이관으로 무손실 통과해야 한다.

이관 계약: 읽기는 구키를 신키로 **메모리에서만** 정규화하고 파일은 건드리지 않는다 · 디스크
스키마 이관은 명시 writer (`migrate_request_file` / `sprite-gen migrate-request --apply`)
에서만 원자적으로 · 두 키 동시 = hard fail · 신키만 있으면 무동작 (멱등).

읽기/쓰기 분리의 근거와 조회 바이트 불변 계약은 `test_request_read_no_mutation.py` 가, 이관
writer 와 편집 writer 사이의 격리(같은 `publish_guard`, 락 획득 후 fresh 재독)는
`test_request_write_isolation.py` 가 소유한다 (plan sprite-gen/state-revision-read-mutation).
"""

from __future__ import annotations

import json

import pytest

from sprite_gen.curate.curation import (CURATION_FILENAME, frame_variant, load_curation_report,
                                 run_revision)
from sprite_gen.spec.runio import load_request, migrate_request_file


def _write_request(run_dir, fit: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "sprite-request.json").write_text(json.dumps({
        "version": 1, "kind": "sprite-gen-request", "engine": "component-row",
        "character": {"id": "migbot", "description": "", "base_image": None},
        "cell": {"shape": "square", "width": 96, "height": 96, "safe_margin_x": 8,
                 "safe_margin_y": 8, "size": 96, "safe_margin": 8},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255],
                       "selection": "fallback"},
        "states": {"walk": {"frames": 2, "fps": 8, "loop": True, "action": "x"}},
        "fit": fit,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_explicit_migration_rewrites_the_request(tmp_path, capsys) -> None:
    """구키 런은 **명시 이관**에서만 신키로 재기록된다 — 읽기는 메모리 정규화까지다.

    쓰기가 사용자가 부른 명령 안에서만 일어나야 조회가 canonical 런을 오염시키지 않는다
    (plan state-revision-read-mutation). 이관 자체는 값·의미를 보존하고 키 이름만 옮긴다."""
    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": True, "logical_height": 48})

    request = load_request(run_dir)
    assert request["fit"]["pixel_unfake"] is True
    assert "pixel_perfect" not in request["fit"]
    # 읽기는 안내만 낸다 (한 프로세스에서 런당 한 번) — 파일은 그대로다
    assert "migrate-request" in capsys.readouterr().err
    assert "pixel_perfect" in json.loads(
        (run_dir / "sprite-request.json").read_text(encoding="utf-8"))["fit"]

    # dry run 은 옮길 것이 있다고만 답한다
    assert migrate_request_file(run_dir) is True
    assert "pixel_perfect" in json.loads(
        (run_dir / "sprite-request.json").read_text(encoding="utf-8"))["fit"]

    assert migrate_request_file(run_dir, apply=True) is True
    on_disk = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert on_disk["fit"] == {"pixel_unfake": True, "logical_height": 48}
    # 멱등: 두 번째 이관은 옮길 것이 없다
    assert migrate_request_file(run_dir, apply=True) is False


def test_both_request_keys_present_fails_loud(tmp_path) -> None:
    """어느 쪽이 진실인지 코드가 고를 수 없다 — 고르는 순간 그게 조용한 폴백이다."""
    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": False, "pixel_unfake": True})
    with pytest.raises(SystemExit) as excinfo:
        load_request(run_dir)
    assert "two truths" in str(excinfo.value)


def test_reading_is_unaffected_by_a_pipeline_writer_lock(tmp_path) -> None:
    """락이 있든 없든 읽기 결과는 같다 — 읽기가 쓰지 않으니 락을 볼 이유가 없다.

    예전엔 로더가 락 유무로 재기록 여부를 갈랐고, 그래서 "락이 없을 때만 파일이 바뀐다" 는
    상태 의존 부작용이 조회 경로에 있었다."""
    from sprite_gen.spec.runio import LOCK_FILENAME

    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": True})
    unlocked = load_request(run_dir)

    (run_dir / LOCK_FILENAME).write_text("held by another writer", encoding="utf-8")
    assert load_request(run_dir) == unlocked
    assert unlocked["fit"]["pixel_unfake"] is True
    assert "pixel_perfect" in json.loads(
        (run_dir / "sprite-request.json").read_text(encoding="utf-8"))["fit"]


def _write_curation(run_dir, doc: dict) -> None:
    """현재 세대 도장을 찍어 둔다 — 스탬프 없는 사이드카는 세대 게이트가 (정상적으로) 드롭해서
    이관 여부를 볼 수 없다. 여기서 검증하려는 건 이관이고 세대 판정이 아니다."""
    run_dir.mkdir(parents=True, exist_ok=True)
    doc = {**doc, "run_revision": run_revision(run_dir)}
    (run_dir / CURATION_FILENAME).write_text(json.dumps(doc), encoding="utf-8")


def test_legacy_sidecar_keys_are_migrated_on_load(tmp_path) -> None:
    """사이드카도 같은 계약 — 최상위 + 행별 둘 다."""
    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_unfake": True})
    _write_curation(run_dir, {"version": 1, "kind": "sprite-gen-curation",
                              "pixel_perfect": False,
                              "states": {"walk": {"pixel_perfect": True, "selected": [0]}}})
    doc = load_curation_report(run_dir)[0]
    assert doc["pixel_unfake"] is False and "pixel_perfect" not in doc
    assert doc["states"]["walk"]["pixel_unfake"] is True
    # 해석 SSoT 도 현행 키를 본다
    assert frame_variant(doc, "walk") == "pixel"
    assert frame_variant(doc) == "plain"


def test_both_sidecar_keys_present_fails_loud(tmp_path) -> None:
    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_unfake": True})
    _write_curation(run_dir, {"version": 1, "kind": "sprite-gen-curation",
                              "pixel_perfect": True, "pixel_unfake": False, "states": {}})
    with pytest.raises(SystemExit) as excinfo:
        load_curation_report(run_dir)
    assert "two truths" in str(excinfo.value)


def test_unrelated_edits_do_not_migrate_the_schema(tmp_path) -> None:
    """스키마와 무관한 편집(테이크 기록·fps)은 디스크의 키 형태를 그대로 둔다.

    이 경로들은 로더가 정규화해 준 문서를 통째로 되쓴다. 그대로 쓰면 fps 한 번 바꾼 것이
    스키마까지 옮겨, 이관이 다시 "아무 명령이나 하는 김에 하는 일" 이 된다 — 명시 writer
    하나만 스키마를 바꾼다는 계약이 그 자리에서 깨진다."""
    from sprite_gen.spec.runio import write_request

    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": True, "logical_height": 48})

    request = load_request(run_dir)
    assert request["fit"]["pixel_unfake"] is True          # 메모리는 현행 키
    request["states"]["walk"]["fps"] = 12
    write_request(run_dir, request)

    on_disk = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert on_disk["states"]["walk"]["fps"] == 12          # 편집은 반영되고
    assert on_disk["fit"] == {"pixel_perfect": True, "logical_height": 48}   # 스키마는 그대로
    assert request["fit"]["pixel_unfake"] is True          # 호출부 dict 는 안 건드린다

    # 이미 현행 키인 런은 되접을 것이 없다 (되접기가 신키 런을 구키로 되돌리면 안 된다)
    fresh_run = tmp_path / "fresh"
    _write_request(fresh_run, {"pixel_unfake": False})
    doc = load_request(fresh_run)
    doc["states"]["walk"]["fps"] = 9
    write_request(fresh_run, doc)
    assert json.loads((fresh_run / "sprite-request.json").read_text(
        encoding="utf-8"))["fit"] == {"pixel_unfake": False}


def test_migrate_request_command_is_dry_run_by_default(tmp_path, capsys) -> None:
    """공개 진입점도 같은 계약이다 — `--apply` 없이는 아무것도 쓰지 않고, 무엇이 바뀔지만
    말한다 (`migrate-breathe` 와 같은 모양)."""
    from sprite_gen.cli import main

    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": True, "logical_height": 48})

    assert main(["migrate-request", str(run_dir)]) == 0
    assert "dry-run" in capsys.readouterr().out
    assert "pixel_perfect" in json.loads(
        (run_dir / "sprite-request.json").read_text(encoding="utf-8"))["fit"]

    assert main(["migrate-request", str(run_dir), "--apply"]) == 0
    assert json.loads((run_dir / "sprite-request.json").read_text(
        encoding="utf-8"))["fit"] == {"pixel_unfake": True, "logical_height": 48}

    # 멱등: 이미 현행 키뿐이면 옮길 것이 없다고 답하고 끝난다
    assert main(["migrate-request", str(run_dir), "--apply"]) == 0
    assert "옮길 것이 없다" in capsys.readouterr().out


def test_migrate_request_command_reports_the_curation_cost_before_writing(tmp_path, capsys) -> None:
    """이관은 request 바이트를 바꾸므로 `run_revision` 이 움직인다 — 행별 스탬프가 없는
    큐레이션 행은 다음 로드에서 드롭된다. 쓰기 **전에** 그걸 말한다 (조용히 잃지 않는다)."""
    from sprite_gen.cli import main

    run_dir = tmp_path / "run"
    _write_request(run_dir, {"pixel_perfect": True})
    _write_curation(run_dir, {"version": 1, "kind": "sprite-gen-curation",
                              "states": {"walk": {"selected": [0]}}})   # 행 스탬프 없음

    assert main(["migrate-request", str(run_dir)]) == 0
    assert "행별 스탬프가 없어 드롭될 행 — walk" in capsys.readouterr().out


def test_retired_cli_flag_reports_the_new_name(tmp_path) -> None:
    """구 플래그는 조용한 별칭이 아니라 안내와 함께 죽는다 (두 이름 공존 금지)."""
    from sprite_gen.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["prepare", "--out-dir", str(tmp_path / "o"), "--character-id", "x",
              "--fit-pixel-perfect"])
    message = str(excinfo.value)
    assert "--fit-pixel-unfake" in message and "retired" in message


def test_reroll_reads_through_the_gate_not_raw(tmp_path) -> None:
    """레거시 런의 리롤이 **거짓 거부**되지 않는다 (젯비 2026-07-26 R1 재현 고정).

    리롤은 `fit.pixel_unfake` 계약 위에서만 테이크를 병합한다. 그 검사 앞의 request 읽기가
    게이트를 우회해 raw 로 읽으면, 아직 이관되지 않은 런(디스크에 은퇴 키만)에서 검사가 항상
    False 가 되어 "언페이크가 켜진 런"의 리롤을 켜져 있지 않다며 막는다. 리롤은 뷰가 **별도
    서브프로세스**로 띄우므로 뷰 프로세스의 in-memory 이관도 넘어오지 않는다 — 그래서 읽기
    자체가 게이트 뒤에 있어야 한다 (키 이름만 바꾸는 것으로는 부족하다)."""
    import random

    from PIL import Image

    from sprite_gen.effects import reroll

    run_dir = tmp_path / "run"
    (run_dir / "raw").mkdir(parents=True)
    rng = random.Random(7)
    art = Image.new("RGB", (20, 36), (255, 0, 255))
    for y in range(36):
        for x in range(20):
            if rng.random() < 0.55:
                art.putpixel((x, y), (rng.randrange(30, 220), 90, 200))
    frame = art.resize((160, 288), Image.Resampling.NEAREST)
    strip = Image.new("RGB", (frame.width * 2 + 120, frame.height + 80), (255, 0, 255))
    strip.paste(frame, (40, 40))
    strip.paste(frame, (frame.width + 80, 40))
    strip.save(run_dir / "raw" / "walk.png")
    _write_request(run_dir, {"pixel_perfect": True, "logical_height": 48})   # 은퇴 키만

    with pytest.raises(SystemExit) as excinfo:
        reroll.reroll_state(run_dir, "walk")
    # pp 계약 검사를 통과해 **다음** 단계(프롬프트 부재)에서 멈춰야 한다
    assert "require fit.pixel_unfake" not in str(excinfo.value), (
        "레거시 런이 언페이크 계약 검사에서 거짓 거부됐다 — 읽기가 게이트를 우회한다")
    assert "prompt file missing" in str(excinfo.value)
    # 게이트를 지났지만 파일은 그대로다 — 읽기 호환은 디스크를 바꾸지 않고 얻는다
    assert "pixel_perfect" in json.loads(
        (run_dir / "sprite-request.json").read_text(encoding="utf-8"))["fit"]


def test_no_production_path_reads_the_request_outside_the_gate() -> None:
    """프로덕션 코드가 `sprite-request.json` 을 게이트 밖에서 읽지 않는다 (구조 단정).

    이게 이 플랜의 핵심 계약이다. 키 이름만 바꾸고 읽기를 게이트 뒤로 옮기지 않으면, 아직
    이관되지 않은 런에서 그 판독부만 조용히 틀린 답을 본다 — 실제로 `reroll.py` 에서 리롤이
    거짓 거부되는 회귀가 났고(젯비 R1), 같은 클래스가 `interpolate.py`·`preview.py` 에도 있었다.

    **AST 로 본다.** 첫 버전은 한 줄 정규식이었고, 실제 회귀 형태(경로를 변수에 담고 다음 줄에서
    `json.loads(path.read_text())`)를 못 잡았다 — 젯비가 mutant 로 증명했다(interpolate 를 옛
    형태로 되돌려도 548 passed). 그래서 판정 단위를 **함수**로 올린다: 한 함수 안에서 request
    파일을 가리키면서(`REQUEST_FILENAME` 또는 리터럴 파일명) `json.load(s)` 를 호출하면 그것이
    게이트 우회다. 쓰기 경로는 `json.dumps` 를 쓰므로 걸리지 않는다.

    테스트 코드는 예외다: 테스트는 "디스크에 실제로 무엇이 쓰였는가" 를 검사해야 한다."""
    import ast
    from pathlib import Path as _Path

    REQUEST_MARKERS = {"REQUEST_FILENAME", "sprite-request.json"}

    def _mentions_request(node: ast.AST) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in REQUEST_MARKERS:
                return True
            if isinstance(child, ast.Constant) and child.value in REQUEST_MARKERS:
                return True
        return False

    root = _Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in sorted(list((root / "sprite_gen").glob("*.py")) + list((root / "scripts").glob("*.py"))):
        if path.name == "runio.py":            # 게이트 자신이 사는 곳
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # ① 이 함수 안에서 request 경로를 담은 변수 이름들
            request_vars = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Assign) and _mentions_request(child.value):
                    for target in child.targets:
                        if isinstance(target, ast.Name):
                            request_vars.add(target.id)
            # ② 그 변수(또는 리터럴/상수)를 읽는 json.load(s) 호출만 우회다.
            #    같은 함수가 curation.json 을 읽는 건 무관하다 (오탐 방지 — 판정 단위를
            #    함수 전체로 두면 do_POST/heal_run 이 걸린다).
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                target = child.func
                if not (isinstance(target, ast.Attribute) and target.attr in {"load", "loads"}
                        and isinstance(target.value, ast.Name) and target.value.id == "json"):
                    continue
                arg_names = {n.id for n in ast.walk(child) if isinstance(n, ast.Name)}
                if (arg_names & request_vars) or _mentions_request(child):
                    offenders.append(f"{path.relative_to(root)}:{child.lineno} in {node.name}()")
    assert not offenders, (
        "게이트를 우회해 request 를 직접 읽는 프로덕션 경로:\n  " + "\n  ".join(offenders)
        + "\n→ `runio.load_request` 를 쓰라 (이관·두 키 hard fail 판정이 그 안에만 있다)")
