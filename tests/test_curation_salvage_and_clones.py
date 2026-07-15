# SPDX-License-Identifier: Apache-2.0
"""행 단위 큐레이션 보존(salvage) + stale 백업 + 프레임 복제(clones) 계약.

배경 실사고 (2026-07-15, 솔밸리 direction-pipeline-demo): 엔진 커밋마다 heal 이 전
행을 재유도해 frames mtime 이 바뀌고, 런 전체 run_revision 지문이 어긋나 사용자의
선택/보관함이 통째로 무시된 뒤 다음 autosave 가 기본값으로 영구 덮어썼다 ("계속
부활"). 계약:

- state_revision = 원료(raw/테이크, 임포트 행은 프레임 내용) 기반 세그먼트 지문 —
  frames 캐시 mtime 과 엔진 리비전은 지문에 들어가지 않는다.
- 같은 raw 의 재추출(엔진 업그레이드 heal)은 행 큐레이션을 유지한다.
- raw 리롤은 그 행만 드롭한다. 드롭 전 원문 전체가 curation.stale-<hash>.json 으로
  백업되고 report 에 관측된다.
- 스탬프 없는 레거시 사이드카는 세대가 어긋나면 전량 드롭 + 백업 (증명 없는 선택을
  새 프레임에 적용하지 않는다).
- clones: 복제 인스턴스는 사이드카 소유(파생 캐시에 파일을 만들지 않음), 자기
  변형을 갖고, 굽기 때 원본 프레임 파일을 읽는다.
"""

import json
from pathlib import Path

from PIL import Image

from conftest import run_script
from sprite_gen.curation import (
    load_curation,
    load_curation_report,
    run_revision,
    source_frame_index,
    stamp_curation,
    state_plan,
    state_revision,
)


def _extract(run: Path) -> None:
    result = run_script("extract_sprite_row_frames.py", "--run-dir", str(run))
    assert result.returncode == 0, result.stdout + result.stderr


def _write_stamped(run: Path, states: dict) -> dict:
    payload = stamp_curation(run, {"version": 1, "kind": "sprite-gen-curation", "states": states})
    (run / "curation.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _reroll_raw(run: Path, state: str) -> None:
    """리롤 시뮬레이션 — raw 내용을 실제로 바꾼다 (지문은 내용 기반)."""
    path = run / "raw" / f"{state}.png"
    with Image.open(path) as im:
        raw = im.convert("RGBA")
    raw.putpixel((0, 0), (1, 2, 3, 255))
    raw.save(path)


def test_reextract_same_raw_keeps_curation(fixture_run_dir: Path) -> None:
    """엔진 업그레이드 heal 시나리오: 같은 raw 재추출 → 선택/보관함 유지."""
    run = fixture_run_dir
    _extract(run)
    stamped = _write_stamped(run, {
        "idle": {"selected": [2, 0], "deleted": [3], "transforms": {"0": {"dx": 4}}},
        "walk": {"selected": [1]},
    })
    assert stamped["states"]["idle"]["revision"], "행 스탬프가 찍혀야 한다"

    old_run_rev = run_revision(run)
    _extract(run)  # frames 재작성 (mtime 변경) — raw 는 그대로
    assert run_revision(run) != old_run_rev, "런 전체 지문은 어긋나야 salvage 경로를 탄다"

    doc, report = load_curation_report(run)
    assert report["dropped"] == []
    assert report["backup"] is None
    assert doc is not None
    assert doc["states"]["idle"]["selected"] == [2, 0]
    assert doc["states"]["idle"]["deleted"] == [3]
    assert doc["states"]["walk"]["selected"] == [1]


def test_reroll_drops_only_that_state_with_backup(fixture_run_dir: Path) -> None:
    run = fixture_run_dir
    _extract(run)
    _write_stamped(run, {
        "idle": {"selected": [2, 0]},
        "walk": {"selected": [1]},
    })
    original_text = (run / "curation.json").read_text(encoding="utf-8")

    _reroll_raw(run, "walk")
    _extract(run)

    doc, report = load_curation_report(run)
    assert report["dropped"] == ["walk"]
    assert report["backup"] and (run / report["backup"]).is_file()
    assert (run / report["backup"]).read_text(encoding="utf-8") == original_text
    assert doc is not None and "walk" not in doc["states"]
    assert doc["states"]["idle"]["selected"] == [2, 0]
    # load_curation 은 같은 게이트의 thin wrapper
    assert "walk" not in (load_curation(run) or {}).get("states", {})


def test_legacy_unstamped_sidecar_drops_all_with_backup(fixture_run_dir: Path) -> None:
    run = fixture_run_dir
    _extract(run)
    legacy = {"version": 1, "kind": "sprite-gen-curation",
              "run_revision": run_revision(run),
              "states": {"idle": {"selected": [1]}}}
    (run / "curation.json").write_text(json.dumps(legacy), encoding="utf-8")
    # 같은 세대인 동안은 fast path 로 그대로 적용된다
    doc, report = load_curation_report(run)
    assert doc is not None and report["dropped"] == []

    _extract(run)  # 세대가 어긋나면 스탬프 없는 행은 검증 불가 → 드롭 + 백업
    doc, report = load_curation_report(run)
    assert doc is None
    assert report["dropped"] == ["idle"]
    assert report["backup"] and (run / report["backup"]).is_file()


def test_take_append_prefix_rule(tmp_path: Path) -> None:
    """테이크가 뒤에 추가돼도(현재 세그먼트의 접두) 기존 행 큐레이션은 유효하다.
    takes 는 pixel_perfect 계약이므로 pp 런을 test_takes_heal 과 같은 방식으로 만든다."""
    from test_takes_heal import _build_run, _strip

    run = _build_run(tmp_path)
    _extract(run)
    stamped = _write_stamped(run, {"walk": {"selected": [0]}})
    stored = stamped["states"]["walk"]["revision"]
    assert len(stored) == 1

    # 테이크 추가: request 선언 + 테이크 raw 배치 후 재추출
    request = json.loads((run / "sprite-request.json").read_text(encoding="utf-8"))
    request["states"]["walk"]["takes"] = [{"label": "alt", "frames": 2}]
    (run / "sprite-request.json").write_text(json.dumps(request), encoding="utf-8")
    takes_dir = run / "raw" / "walk.takes"
    takes_dir.mkdir()
    _strip(2, seed=11).save(takes_dir / "alt.png")
    _extract(run)

    current = state_revision(run, "walk")
    assert current is not None and len(current) == 2
    assert current[:len(stored)] == stored, "기존 세그먼트 지문은 접두로 보존"

    doc, report = load_curation_report(run)
    assert report["dropped"] == []
    assert doc is not None and doc["states"]["walk"]["selected"] == [0]


def test_clone_instances_plan_and_bake(fixture_run_dir: Path) -> None:
    run = fixture_run_dir
    _extract(run)
    # idle 물리 4프레임(0..3) + 복제 7→0 (자기만의 flipX 변형), 시퀀스 = [0, 7, 1]
    _write_stamped(run, {
        "idle": {
            "selected": [0, 7, 1],
            "clones": {"7": 0},
            "transforms": {"7": {"flipX": 1}},
        },
    })

    curation = load_curation(run)
    ordered, transforms = state_plan(curation, "idle", 4)
    assert ordered == [0, 7, 1]
    assert transforms[7]["flipX"] == 1
    assert source_frame_index(curation, "idle", 7, 4) == 0
    assert source_frame_index(curation, "idle", 1, 4) == 1
    # 원본 범위와 겹치거나 소스가 물리 범위 밖인 손상 항목은 무시된다
    corrupt = {"states": {"idle": {"selected": [0, 2], "clones": {"2": 0, "9": 5}}}}
    assert state_plan(corrupt, "idle", 4)[0] == [0, 2]
    assert source_frame_index(corrupt, "idle", 2, 4) == 2

    compose = run_script("compose_sprite_atlas.py", "--run-dir", str(run))
    assert compose.returncode == 0, compose.stdout + compose.stderr
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["frame_layout"]["rows"]["idle"]) == 3
    assert manifest["animation"]["rows"]["idle"]["frames"] == 3

    # 복제 셀(2번째)은 원본 셀(1번째)의 좌우 반전과 일치해야 한다 — 같은 원본을
    # 읽고 자기 변형만 적용했다는 증명
    cell = json.loads((run / "sprite-request.json").read_text(encoding="utf-8"))["cell"]
    cw, ch = int(cell["width"]), int(cell["height"])
    row_y = 0  # idle 은 첫 행
    with Image.open(run / "sprite-sheet-alpha.png") as atlas:
        rgba = atlas.convert("RGBA")
        source_cell = rgba.crop((0, row_y, cw, row_y + ch))
        clone_cell = rgba.crop((cw, row_y, cw * 2, row_y + ch))
    from PIL import ImageOps
    assert list(clone_cell.getdata()) == list(ImageOps.mirror(source_cell).getdata())
