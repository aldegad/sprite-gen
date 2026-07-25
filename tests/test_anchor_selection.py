# SPDX-License-Identifier: Apache-2.0
"""방향 앵커 = 승인된 프레임 (수홍 2026-07-25).

- 기본 앵커 = 그 방향 앵커 행의 **큐레이션 시퀀스 첫 인스턴스** (index 0 이 아니다) 이고,
  베이크는 픽셀 편집·변형까지 태운 결과다 — raw 크롭이 아니다.
- 사용자가 큐레이션 뷰/CLI 로 **어떤 프레임이 앵커인지 지정**할 수 있다 (같은 방향의
  다른 행, 시퀀스에 없는 후보 풀 프레임도 가능).
- 사라진 인스턴스를 가리키는 지정은 fail-loud (조용한 기본값 복귀 금지).
- 리롤/생성 플랜/뷰 칩이 같은 앵커 진실을 쓴다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sprite_gen import anchor as anchor_mod  # noqa: E402
from sprite_gen import extract as extract_module  # noqa: E402
from sprite_gen.curation import (anchor_choices, load_curation, state_instances,  # noqa: E402
                                 write_curation_atomic)

MAGENTA = (255, 0, 255)
PITCH = 8
LOGICAL = (20, 36)
GAP = 40


def _frame_art(seed: int) -> Image.Image:
    """결정론 합성 프레임 — 논리 픽셀 노이즈(격자 검출이 가능한 밀도)를 PITCH 배 확대.

    단색 블록은 내부 경계가 없어 피치 검출이 실패한다 (실측: "empty or too sparse")
    — test_takes_heal 과 같은 노이즈 방식을 쓴다."""
    import random

    rng = random.Random(seed)
    art = Image.new("RGB", LOGICAL, MAGENTA)
    for y in range(LOGICAL[1]):
        for x in range(LOGICAL[0]):
            if rng.random() < 0.55:
                art.putpixel((x, y), (rng.randrange(30, 220), rng.randrange(30, 220),
                                      rng.randrange(30, 220)))
    return art.resize((LOGICAL[0] * PITCH, LOGICAL[1] * PITCH), Image.Resampling.NEAREST)


def _strip(frames: int, seed0: int) -> Image.Image:
    frame = _frame_art(seed0)  # 한 행의 프레임들은 같은 그림 (앵커 판정은 인덱스로 한다)
    strip = Image.new("RGB", (frame.width * frames + GAP * (frames + 1),
                              frame.height + GAP * 2), MAGENTA)
    for i in range(frames):
        strip.paste(frame, (GAP + i * (frame.width + GAP), GAP))
    return strip


def _build_direction_run(root: Path, extract_states: str | None = "all") -> Path:
    """down/side 방향 계약 + pp 추출 런 (방향별 idle 앵커 행 4프레임 + walk 행 3프레임).

    `extract_states` 로 생성 진행 구간을 고른다: `"all"`(완전 추출) ·
    `"down_idle,side_idle"`(stage-1: 앵커 행만) · `None`(추출 전)."""
    run_dir = root / "run"
    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": "anchorbot", "description": "anchor fixture", "base_image": None},
        "cell": {"shape": "square", "width": 96, "height": 96, "safe_margin_x": 8,
                 "safe_margin_y": 8, "size": 96, "safe_margin": 8},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255],
                       "selection": "fallback"},
        "layout": "taxonomy/v1",
        "directions": {"set": ["down", "side"], "mirror": {}, "anchor_suffix": "idle"},
        "states": {
            "down_idle": {"frames": 4, "fps": 4, "loop": True, "action": "anchor row"},
            "side_idle": {"frames": 4, "fps": 4, "loop": True, "action": "anchor row"},
            "down_walk": {"frames": 3, "fps": 8, "loop": True, "action": "action row"},
            "side_walk": {"frames": 3, "fps": 8, "loop": True, "action": "action row"},
        },
        "fit": {"pixel_perfect": True, "logical_height": 48},
    }
    (run_dir / "raw" / "down").mkdir(parents=True)
    (run_dir / "raw" / "side").mkdir(parents=True)
    _strip(4, seed0=1).save(run_dir / "raw" / "down" / "idle.png")
    _strip(4, seed0=60).save(run_dir / "raw" / "side" / "idle.png")
    _strip(3, seed0=20).save(run_dir / "raw" / "down" / "walk.png")
    _strip(3, seed0=40).save(run_dir / "raw" / "side" / "walk.png")
    Image.new("RGB", (64, 64), (10, 200, 10)).save(run_dir / "base-source.png")
    (run_dir / "sprite-request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if extract_states is not None:
        assert extract_module.run(run_dir=run_dir, states=extract_states) == 0
    return run_dir


def _save_curation(run_dir: Path, states: dict, anchors: dict | None = None) -> None:
    payload = {"version": 1, "kind": "sprite-gen-curation", "states": states}
    if anchors is not None:
        payload["anchors"] = anchors
    write_curation_atomic(run_dir, payload)


def _pins(doc: dict | None) -> dict:
    """지정의 **의미만** 비교 (state/index) — `revision` 세대 스탬프는 구현 세부다."""
    return {d: {"state": p["state"], "index": p["index"]} for d, p in anchor_choices(doc).items()}


def _request(run_dir: Path) -> dict:
    return json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))


@pytest.fixture
def direction_run(tmp_path: Path) -> Path:
    return _build_direction_run(tmp_path)


@pytest.fixture
def stage1_run(tmp_path: Path) -> Path:
    """**워크플로 stage-1 그대로**: 방향 앵커 행만 생성·추출됐고 액션 행은 아직 없다.

    앵커 소비자는 정의상 이 구간에서 돈다 (stage-2 행을 생성하려면 그 방향 앵커 ref 가
    먼저 필요하다). 플랜 초기 픽스처가 전부 '완전 추출된 런'이라 이 구간을 한 건도
    덮지 않았고, 그래서 `require_frames_manifest`(완성 소비자 게이트) 오사용이
    테스트를 통과했다 — 젯비 검증 2026-07-25 reject 의 근본 원인."""
    return _build_direction_run(tmp_path, extract_states="down_idle,side_idle")


@pytest.fixture
def ungenerated_run(tmp_path: Path) -> Path:
    """방향 계약만 선언되고 추출이 아예 없는 런 (생성 이전 구간)."""
    return _build_direction_run(tmp_path, extract_states=None)


# --- 기본값: 시퀀스 첫 인스턴스 + 후처리 반영 ---------------------------------

def test_default_anchor_is_the_curated_sequence_head_not_index_zero(direction_run: Path) -> None:
    run_dir = direction_run
    request = _request(run_dir)
    # 사용자가 앞 두 프레임을 기각(보관)하고 3,2 순서로 재정렬
    _save_curation(run_dir, {"down_idle": {"deleted": [0, 1], "selected": [3, 2]}})
    resolved = anchor_mod.resolve_anchor(request, load_curation(run_dir), "down")
    assert resolved == {"direction": "down", "state": "down_idle", "index": 3, "source": "default"}


def test_anchor_bake_carries_pixel_edits_and_transform(direction_run: Path) -> None:
    """앵커 재료 = 승인된 모습. 픽셀 편집이 앵커 ref 에 실제로 들어있어야 한다."""
    run_dir = direction_run
    request = _request(run_dir)
    _save_curation(run_dir, {"down_idle": {
        "selected": [0, 1, 2, 3],
        # 셀 좌표 픽셀 편집 (한 논리 블록 전체를 덮는 8x8 은 아니어도 색은 나타난다)
        "pixels": {"0": {f"{x},{y}": "#ff0000" for x in range(40, 48) for y in range(40, 48)}},
    }})
    image, resolved = anchor_mod.anchor_image(run_dir, request, load_curation(run_dir), "down")
    assert resolved["state"] == "down_idle" and resolved["index"] == 0
    colors = {px[:3] for px in image.convert("RGBA").getdata() if px[3] > 0}
    assert (255, 0, 0) in colors, "픽셀 편집이 앵커 ref 에 반영되지 않았다 (raw 를 구웠다)"


def test_materialize_writes_ref_and_removes_legacy_snapshot(direction_run: Path) -> None:
    run_dir = direction_run
    legacy = run_dir / "references" / "anchors" / "down-idle-x8.png"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"stale")
    out = anchor_mod.materialize(run_dir, "down")
    assert out == run_dir / "references" / "anchors" / "down-anchor-x8.png"
    assert out.is_file()
    assert not legacy.exists(), "앵커가 idle 행에 묶여 있던 시절의 파일이 남아 두 진실이 됐다"
    with Image.open(out) as im:
        assert im.width % 8 == 0 and im.height % 8 == 0  # x8 니어리스트 확대


# --- 생성 도중 구간 (앵커 소비자가 실제로 도는 시점) ---------------------------
# 회귀 고정: 앵커 진입점이 `require_frames_manifest`(완성된 생성물 소비자 게이트)를
# 물면 request 의 *모든* 행에 매니페스트 행을 요구해서, "행 생성 직전에 매번 돌려라"
# 라는 계약이 **항상** corrupt-manifest 로 죽는다 (젯비 검증 2026-07-25 실측).

def test_stage1_run_serves_the_anchor_ref(stage1_run: Path, capsys) -> None:
    """앵커 행만 추출된 시점에 액션 행의 identity ref 가 나와야 한다 (그게 유일한 용도다)."""
    run_dir = stage1_run
    assert anchor_mod.run(run_dir=run_dir, for_state="side_walk") == 0
    printed = capsys.readouterr().out.strip()
    assert printed == "references/anchors/side-anchor-x8.png"
    assert (run_dir / printed).is_file()
    assert anchor_mod.run(run_dir=run_dir, direction="down") == 0
    assert anchor_mod.run(run_dir=run_dir, all_directions=True) == 0
    for direction in ("down", "side"):
        assert (run_dir / f"references/anchors/{direction}-anchor-x8.png").is_file()


def test_stage1_run_pick_persists_and_bakes(stage1_run: Path) -> None:
    run_dir = stage1_run
    assert anchor_mod.run(run_dir=run_dir, pick="down_idle#2") == 0
    assert _pins(load_curation(run_dir))["down"] == {"state": "down_idle", "index": 2}
    assert (run_dir / "references" / "anchors" / "down-anchor-x8.png").is_file()


def test_ungenerated_run_pick_is_refused_not_half_saved(ungenerated_run: Path) -> None:
    """추출 전 런에서는 지정 자체가 거부된다 (본 적 없는 프레임 + 지문 계산 불가).

    옛 계약은 "지정은 저장하고 ref 만 나중에" 였는데, 그렇게 저장된 스탬프 없는 핀이 다음
    로드에서 '재생성됐다' 로 오분류돼 그 방향 생성을 막았다 (젯비 5차 기각 2)."""
    with pytest.raises(anchor_mod.AnchorUnavailable) as excinfo:
        anchor_mod.run(run_dir=ungenerated_run, pick="down_idle#1")
    assert excinfo.value.kind == "row-not-extracted"
    assert _pins(load_curation(ungenerated_run)) == {}
    assert not (ungenerated_run / "references" / "anchors").exists()


def test_ungenerated_run_for_state_fails_loud_with_the_real_reason(ungenerated_run: Path) -> None:
    """ref 를 **요구**하는 진입점은 조용히 넘어가지 않고, 원인을 정확히 말해야 한다."""
    with pytest.raises(anchor_mod.AnchorUnavailable) as excinfo:
        anchor_mod.run(run_dir=ungenerated_run, for_state="down_walk")
    assert excinfo.value.kind in anchor_mod.AnchorUnavailable.PENDING_KINDS
    assert excinfo.value.pending is True
    assert "corrupt" not in str(excinfo.value)


# --- 실패 메시지가 **프로세스 stderr 까지** 도달하는가 -------------------------
# 회귀 고정: 분류 슬러그를 `SystemExit.code` 에 얹으면 인터프리터가 그걸 stderr 에 찍어
# 안내문이 통째로 사라진다 (실측: `anchor --for-state` 추출 전 호출의 stderr = "no-frames").
# 예외 객체의 `str()` 만 보는 테스트는 이 사각을 못 본다 — CLI 를 **서브프로세스로** 돈다.

def _cli(run_dir: Path, *args: str):
    import subprocess

    return subprocess.run([sys.executable, "-m", "sprite_gen.cli", "anchor",
                           "--run-dir", str(run_dir), *args],
                          capture_output=True, text=True, cwd=str(ROOT))


@pytest.mark.parametrize("args,needle", [
    (("--for-state", "down_walk"), "generate and extract"),
    (("--direction", "down"), "generate and extract"),
])
def test_cli_pending_failure_prints_the_guidance(ungenerated_run: Path, args, needle) -> None:
    proc = _cli(ungenerated_run, *args)
    assert proc.returncode != 0
    assert needle in proc.stderr, proc.stderr
    assert proc.stderr.strip() not in anchor_mod.AnchorUnavailable.PENDING_KINDS


def test_cli_broken_pin_failure_prints_the_guidance(direction_run: Path) -> None:
    _save_curation(direction_run, {"down_idle": {"deleted": [3], "selected": [0, 1, 2]}},
                   anchors={"down": {"state": "down_idle", "index": 3}})
    proc = _cli(direction_run, "--for-state", "down_walk")
    assert proc.returncode != 0
    assert "no longer exists" in proc.stderr, proc.stderr
    assert "re-pick the anchor frame" in proc.stderr
    assert proc.stderr.strip() != "pick-missing"


def test_all_bakes_every_bakeable_direction_before_failing(tmp_path: Path) -> None:
    """벌크(`--all`)는 끝까지 돌고 한 번에 보고한다 — 중간에 죽으면 재실행 지점이 모호하다."""
    run_dir = _build_direction_run(tmp_path, extract_states="side_idle")
    with pytest.raises(anchor_mod.AnchorUnavailable) as excinfo:
        anchor_mod.run(run_dir=run_dir, all_directions=True)
    assert "1 of 2 direction(s)" in str(excinfo.value)
    assert (run_dir / "references" / "anchors" / "side-anchor-x8.png").is_file()
    assert not (run_dir / "references" / "anchors" / "down-anchor-x8.png").exists()


def test_cli_pick_direction_mismatch_is_reported(direction_run: Path) -> None:
    """지정 방향과 명시 `--direction` 이 어긋나면 조용히 다른 방향을 굽지 않는다."""
    proc = _cli(direction_run, "--pick", "side_idle#0", "--direction", "down")
    assert proc.returncode == 0, proc.stderr
    assert "does not own 'side_idle'" in proc.stdout
    assert _pins(load_curation(direction_run)) == {"side": {"state": "side_idle", "index": 0}}
    for d in ("down", "side"):
        assert (direction_run / f"references/anchors/{d}-anchor-x8.png").is_file()


def test_invalid_pick_writes_nothing(direction_run: Path) -> None:
    """커밋 전 검증 (Atomicity): 잘못된 지정은 사이드카에 잔류하지 않는다."""
    run_dir = direction_run
    with pytest.raises(anchor_mod.AnchorUnavailable) as excinfo:
        anchor_mod.run(run_dir=run_dir, pick="down_idle#99")
    assert excinfo.value.kind == "pick-missing"
    assert _pins(load_curation(run_dir)) == {}


def test_stage1_view_reports_no_anchor_error(stage1_run: Path) -> None:
    """작업 중간 런은 멀쩡한 상태다 — 뷰에 오류가 뜨면 안 된다."""
    from serve_curation import build_run_state

    snapshot = build_run_state(stage1_run)
    for group in snapshot["directionGroups"]:
        if group.get("mirrorOf"):
            continue
        assert group["anchorError"] is None, group
        assert group["anchorPending"] is False
        assert group["anchorFrame"]["state"] == f"{group['direction']}_idle"
    walk = next(s for s in snapshot["states"] if s["name"] == "side_walk")
    assert [r for r in walk["refs"] if r["role"] == "anchor"], "앵커 칩이 사라졌다"


def test_pending_and_broken_are_different_states(ungenerated_run: Path) -> None:
    """`pending`(아직 안 뽑음)과 `broken`(사람이 고쳐야 함)은 뷰에서 다르게 취급된다."""
    from serve_curation import build_run_state

    run_dir = ungenerated_run
    pending = next(g for g in build_run_state(run_dir)["directionGroups"]
                   if g["direction"] == "down")
    assert pending["anchorPending"] is True
    assert pending["anchorErrorCode"] in anchor_mod.AnchorUnavailable.PENDING_KINDS
    assert pending["anchorUrl"] is None

    _save_curation(run_dir, {}, anchors={"down": {"state": "side_walk", "index": 0}})
    broken = next(g for g in build_run_state(run_dir)["directionGroups"]
                  if g["direction"] == "down")
    assert broken["anchorPending"] is False
    assert broken["anchorErrorCode"] == "pick-wrong-direction"


# --- 세대 경계 (행이 재생성된 뒤) ----------------------------------------------
# 회귀 고정: 사이드카 writer 가 세대 게이트(`load_curation_report`)를 우회해 raw 로 읽으면,
# `stamp_curation` 이 모든 행에 현재 세대 지문을 다시 찍어 **드롭된 낡은 큐레이션이 부활**
# 한다 — 그 부활한 selected 가 새 아트의 방향 앵커를 고른다 (젯비 3차 기각 실측).

def _regenerate_row(run_dir: Path, direction: str, pose: str, seed: int) -> None:
    """raw 를 갈아끼우고 그 행만 재추출 = 리롤/재생성 (새 세대)."""
    _strip(4, seed0=seed).save(run_dir / "raw" / direction / f"{pose}.png")
    assert extract_module.run(run_dir=run_dir, states=f"{direction}_{pose}") == 0


def test_cli_pick_does_not_resurrect_dropped_curation(direction_run: Path) -> None:
    from sprite_gen.curation import load_curation_report

    run_dir = direction_run
    _save_curation(run_dir, {"down_idle": {"deleted": [0, 1], "selected": [2, 3],
                                           "pixels": {"2": {"10,10": "#ff0000"}}}})
    _regenerate_row(run_dir, "down", "idle", seed=999)
    dropped = load_curation_report(run_dir)[1]["dropped"]
    assert "down_idle" in dropped, "세대 게이트가 낡은 행을 드롭하지 않았다 (전제 실패)"

    # 다른 방향 지정 한 번 — 이것만으로 down_idle 이 되살아나면 안 된다
    assert anchor_mod.run(run_dir=run_dir, pick="side_idle#0") == 0
    doc = load_curation_report(run_dir)[0] or {}
    assert "down_idle" not in (doc.get("states") or {}), "드롭된 큐레이션이 부활했다"
    assert _pins(doc) == {"side": {"state": "side_idle", "index": 0}}
    # 그래서 down 앵커는 새 세대의 시퀀스 헤드다 (죽은 selected 의 index 2 가 아니다)
    resolved = anchor_mod.resolve_anchor(_request(run_dir), doc, "down")
    assert (resolved["index"], resolved["source"]) == (0, "default")


def test_pin_on_a_regenerated_row_fails_loud_instead_of_following(direction_run: Path) -> None:
    """핀한 **그 행**이 리롤되면 같은 인덱스는 다른 그림이다 — 조용히 따라가지 않는다.

    따라가면 사용자가 본 적 없는 새 세대 프레임이 방향 identity 가 되어 액션 행 전부에
    번진다 (이 플랜이 존재하는 2026-07-19 사고와 같은 형태). 조용히 기본값으로 복귀해서도
    안 된다 — 문서 3곳이 "hard error" 로 못박은 계약이다."""
    from sprite_gen.curation import load_curation_report

    run_dir = direction_run
    _save_curation(run_dir, {"down_idle": {"selected": [0, 1, 2, 3],
                                           "pixels": {"2": {"10,10": "#ff0000"}}},
                             "side_idle": {"selected": [0, 1, 2, 3]}},
                   anchors={"down": {"state": "down_idle", "index": 2}})
    before = anchor_mod.materialize(run_dir, "down", quiet=True).read_bytes()
    _regenerate_row(run_dir, "down", "idle", seed=31337)

    doc, report = load_curation_report(run_dir)
    assert report["anchors_stale"] == ["down"]
    assert (doc.get("anchors") or {}).get("down"), "지정이 조용히 사라졌다 (기본값 복귀)"
    with pytest.raises(anchor_mod.AnchorUnavailable) as excinfo:
        anchor_mod.resolve_anchor(_request(run_dir), doc, "down")
    assert excinfo.value.kind == "pick-stale-generation"
    assert excinfo.value.pending is False  # 뷰가 오류로 표시해야 한다
    # 다시 찍으면 풀린다 (그리고 그때 비로소 새 세대 프레임을 굽는다)
    assert anchor_mod.run(run_dir=run_dir, pick="down_idle#2") == 0
    after = (run_dir / anchor_mod.anchor_ref_rel("down")).read_bytes()
    assert after != before, "재지정 후에도 옛 세대 그림이 남았다"


def _post_curation(run_dir: Path, body: dict) -> int:
    """뷰의 저장 경로(HTTP POST /api/curation)를 그대로 태운다 — 서버 writer 검증용."""
    import json as _json
    import threading
    import urllib.request
    from functools import partial
    from http.server import ThreadingHTTPServer

    from serve_curation import CurationHandler

    CurationHandler.run_dir = run_dir
    CurationHandler.lang = "en"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(CurationHandler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        snapshot = _json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/run").read())
        payload = {"version": 1, "kind": "sprite-gen-curation",
                   "runRevision": snapshot["runRevision"], **body}
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/curation",
                                     data=_json.dumps(payload).encode(), method="POST")
        with urllib.request.urlopen(req) as res:
            return res.status
    finally:
        srv.shutdown()


def test_view_save_cannot_launder_a_stale_pin(direction_run: Path) -> None:
    """뷰 저장(무관한 편집)이 낡은 핀을 현재 세대로 **세탁하지 못한다**.

    지문(`revision`)은 엔진 소유다: 뷰 payload 는 `{state, index}` 만 싣고, 저장 시 writer 가
    저장된 지문을 이월한다. 이월이 없으면 stamp 단계가 지문 없는 핀에 현재 세대 도장을 찍어
    낡은 핀이 유효해지고 오류 배너가 사라진다 — 4차에서 세운 fail-loud 계약이 뷰 저장 한 번에
    무효화됐다 (젯비 5차 기각 실측)."""
    from sprite_gen.curation import load_curation_report

    run_dir = direction_run
    _save_curation(run_dir, {"down_idle": {"selected": [0, 1, 2, 3]},
                             "side_idle": {"selected": [0, 1, 2, 3]}},
                   anchors={"down": {"state": "down_idle", "index": 2}})
    _regenerate_row(run_dir, "down", "idle", seed=8888)
    assert load_curation_report(run_dir)[1]["anchors_stale"] == ["down"]

    # 사용자가 재지정은 안 하고 무관한 편집만 저장한다 (뷰는 {state,index} 만 싣는다)
    assert _post_curation(run_dir, {
        "anchors": {"down": {"state": "down_idle", "index": 2}},
        "states": {"side_idle": {"selected": [0, 1]}}}) == 200

    doc, report = load_curation_report(run_dir)
    assert report["anchors_stale"] == ["down"], "무관한 저장이 낡은 핀을 세탁했다"
    with pytest.raises(anchor_mod.AnchorUnavailable) as excinfo:
        anchor_mod.resolve_anchor(_request(run_dir), doc, "down")
    assert excinfo.value.kind == "pick-stale-generation"


def test_explicit_repin_clears_the_stale_pin(direction_run: Path) -> None:
    """푸는 길은 **명시 재지정**뿐이다 (뷰의 stale 핀 버튼이 `repin: true` 를 싣는다)."""
    from sprite_gen.curation import load_curation_report

    run_dir = direction_run
    _save_curation(run_dir, {"down_idle": {"selected": [0, 1, 2, 3]}},
                   anchors={"down": {"state": "down_idle", "index": 2}})
    _regenerate_row(run_dir, "down", "idle", seed=5150)
    assert load_curation_report(run_dir)[1]["anchors_stale"] == ["down"]

    assert _post_curation(run_dir, {
        "anchors": {"down": {"state": "down_idle", "index": 2, "repin": True}},
        "states": {}}) == 200

    doc, report = load_curation_report(run_dir)
    assert report["anchors_stale"] == []
    resolved = anchor_mod.resolve_anchor(_request(run_dir), doc, "down")
    assert (resolved["index"], resolved["source"]) == (2, "picked")
    stored = json.loads((run_dir / "curation.json").read_text(encoding="utf-8"))
    assert "repin" not in stored["anchors"]["down"]  # 의도 표식은 저장하지 않는다


def test_cannot_pin_a_row_that_is_not_extracted(stage1_run: Path) -> None:
    """추출 전 행은 핀할 수 없다 — 본 적 없는 프레임이고, 지문도 계산되지 않아 나중에
    "재생성됐다" 로 오분류돼 그 방향 생성을 막았다 (젯비 5차 기각 2)."""
    from sprite_gen.curation import load_curation

    with pytest.raises(anchor_mod.AnchorUnavailable) as excinfo:
        anchor_mod.run(run_dir=stage1_run, pick="down_walk#1")
    assert excinfo.value.kind == "row-not-extracted"
    assert "not extracted yet" in str(excinfo.value)
    assert _pins(load_curation(stage1_run)) == {}, "거부된 지정이 디스크에 남았다"


def test_unstamped_pin_reports_unverifiable_not_regenerated(direction_run: Path) -> None:
    """손으로 쓴(스탬프 없는) 핀은 "재생성됨" 이 아니라 "증명 불가" 로 말해야 한다."""
    from sprite_gen.curation import load_curation_report

    run_dir = direction_run
    _save_curation(run_dir, {"down_idle": {"selected": [0, 1, 2, 3]}})
    doc = json.loads((run_dir / "curation.json").read_text(encoding="utf-8"))
    doc["anchors"] = {"down": {"state": "down_idle", "index": 2}}       # 지문 없이 직접 기입
    doc["run_revision"] = "0" * 16                                      # fast-path 우회
    (run_dir / "curation.json").write_text(json.dumps(doc), encoding="utf-8")

    loaded, report = load_curation_report(run_dir)
    assert report["anchors_stale"] == ["down"]
    with pytest.raises(anchor_mod.AnchorUnavailable) as excinfo:
        anchor_mod.resolve_anchor(_request(run_dir), loaded, "down")
    assert excinfo.value.kind == "pick-unverifiable"
    assert "no generation stamp" in str(excinfo.value)


def test_pin_survives_when_every_curated_row_is_dropped(direction_run: Path) -> None:
    """전 행이 드롭돼도 지정은 남아야 한다 — 남아 있어야 오류를 낼 수 있다."""
    from sprite_gen.curation import load_curation_report

    run_dir = direction_run
    _save_curation(run_dir, {"down_idle": {"selected": [0, 1, 2, 3]}},
                   anchors={"down": {"state": "down_idle", "index": 1}})
    _regenerate_row(run_dir, "down", "idle", seed=4242)
    doc, report = load_curation_report(run_dir)
    assert report["dropped"] == ["down_idle"]
    assert doc is not None, "지정까지 통째로 사라져 조용히 기본값으로 돌아갔다"
    assert anchor_choices(doc)["down"]["stale"] is True


def test_cli_clear_does_not_resurrect_dropped_curation(direction_run: Path) -> None:
    from sprite_gen.curation import load_curation_report

    run_dir = direction_run
    _save_curation(run_dir, {"down_idle": {"deleted": [0], "selected": [1, 2, 3]}},
                   anchors={"side": {"state": "side_idle", "index": 1}})
    _regenerate_row(run_dir, "down", "idle", seed=777)
    assert "down_idle" in load_curation_report(run_dir)[1]["dropped"]
    assert anchor_mod.run(run_dir=run_dir, clear=True, direction="side") == 0
    doc = load_curation_report(run_dir)[0] or {}
    assert "down_idle" not in (doc.get("states") or {})
    assert _pins(doc) == {}


# --- 사용자 지정 ---------------------------------------------------------------

def test_picked_frame_overrides_the_default_including_another_row(direction_run: Path) -> None:
    run_dir = direction_run
    request = _request(run_dir)
    _save_curation(run_dir, {"down_idle": {"selected": [0, 1, 2, 3]}},
                   anchors={"down": {"state": "down_walk", "index": 2}})
    resolved = anchor_mod.resolve_anchor(request, load_curation(run_dir), "down")
    assert resolved == {"direction": "down", "state": "down_walk", "index": 2, "source": "picked"}


def test_picked_frame_may_be_a_pool_candidate(direction_run: Path) -> None:
    """시퀀스에 없는(선택 안 된) 후보 프레임도 앵커가 될 수 있다 — 포즈가 제일 좋을 수 있다."""
    run_dir = direction_run
    request = _request(run_dir)
    _save_curation(run_dir, {"down_idle": {"selected": [0, 1]}},
                   anchors={"down": {"state": "down_idle", "index": 3}})
    curation = load_curation(run_dir)
    assert 3 in state_instances(curation, "down_idle", 4)
    resolved = anchor_mod.resolve_anchor(request, curation, "down")
    assert (resolved["index"], resolved["source"]) == (3, "picked")


def test_picked_frame_that_vanished_fails_loud(direction_run: Path) -> None:
    run_dir = direction_run
    request = _request(run_dir)
    _save_curation(run_dir, {"down_idle": {"deleted": [3], "selected": [0, 1, 2]}},
                   anchors={"down": {"state": "down_idle", "index": 3}})
    with pytest.raises(SystemExit) as excinfo:
        anchor_mod.resolve_anchor(request, load_curation(run_dir), "down")
    assert "no longer exists" in str(excinfo.value)
    # 뷰는 죽지 않고 이유를 보여준다
    status = anchor_mod.anchor_status(run_dir, request, load_curation(run_dir), "down")
    assert status["state"] is None and "no longer exists" in status["error"]


def test_pick_from_another_direction_is_rejected(direction_run: Path) -> None:
    run_dir = direction_run
    request = _request(run_dir)
    _save_curation(run_dir, {}, anchors={"down": {"state": "side_walk", "index": 0}})
    with pytest.raises(SystemExit) as excinfo:
        anchor_mod.resolve_anchor(request, load_curation(run_dir), "down")
    assert "belongs to direction" in str(excinfo.value)


# --- CLI ---------------------------------------------------------------------

def test_cli_pick_then_clear_roundtrip(direction_run: Path) -> None:
    run_dir = direction_run
    assert anchor_mod.run(run_dir=run_dir, pick="down_idle#2") == 0
    assert _pins(load_curation(run_dir)) == {"down": {"state": "down_idle", "index": 2}}
    # 지정만으로 ref 도 갱신된다 (--direction 이 pick 에서 파생)
    assert (run_dir / "references" / "anchors" / "down-anchor-x8.png").is_file()
    assert anchor_mod.run(run_dir=run_dir, clear=True, direction="down") == 0
    assert _pins(load_curation(run_dir)) == {}


def test_cli_for_state_prints_the_identity_ref(direction_run: Path, capsys) -> None:
    run_dir = direction_run
    # 액션 행 = 방향 앵커 (실제로 구워진 파일)
    assert anchor_mod.run(run_dir=run_dir, for_state="down_walk") == 0
    printed = capsys.readouterr().out.strip()
    assert printed == "references/anchors/down-anchor-x8.png"
    assert (run_dir / printed).is_file()
    # 앵커 행 자체 = base-source (앵커 이전 체인)
    assert anchor_mod.run(run_dir=run_dir, for_state="down_idle") == 0
    assert capsys.readouterr().out.strip() == "base-source.png"


def test_cli_pick_preserves_existing_curation_rows(direction_run: Path) -> None:
    """지정 쓰기가 사용자의 선택/편집을 덮지 않는다 (fresh 재독 → 갱신)."""
    run_dir = direction_run
    _save_curation(run_dir, {"down_idle": {"selected": [1, 2], "transforms": {"1": {"dx": 3}}}})
    assert anchor_mod.run(run_dir=run_dir, pick="down_idle#1") == 0
    curation = load_curation(run_dir)
    assert curation["states"]["down_idle"]["selected"] == [1, 2]
    assert curation["states"]["down_idle"]["transforms"]["1"]["dx"] == 3
    assert _pins(curation)["down"] == {"state": "down_idle", "index": 1}


# --- 소비자 정합 --------------------------------------------------------------

def test_reroll_identity_ref_uses_the_picked_anchor(direction_run: Path) -> None:
    """리롤이 붙이는 identity ref = 같은 앵커 진실 (reroll 안에 두 번째 규칙이 없다)."""
    from sprite_gen import reroll

    run_dir = direction_run
    request = _request(run_dir)
    _save_curation(run_dir, {}, anchors={"down": {"state": "down_walk", "index": 1}})
    ref = reroll.identity_ref(run_dir, "down_walk", request=request, quiet=True)
    assert ref == run_dir / "references" / "anchors" / "down-anchor-x8.png"
    baked = anchor_mod.anchor_image(run_dir, request, load_curation(run_dir), "down")[0]
    with Image.open(ref) as written:
        assert list(written.convert("RGBA").getdata()) == list(baked.convert("RGBA").getdata())


def test_anchor_suffix_other_than_idle_is_honored(direction_run: Path) -> None:
    """앵커 행 이름은 `anchor_suffix` 계약이 정한다 — `idle` 하드코딩 금지."""
    run_dir = direction_run
    request = _request(run_dir)
    request["directions"]["anchor_suffix"] = "walk"
    assert anchor_mod.anchor_state(request, "down") == "down_walk"
    resolved = anchor_mod.resolve_anchor(request, load_curation(run_dir), "down")
    assert resolved["state"] == "down_walk"
    # 이제 앵커 행이 된 down_walk 는 base-source 로 생성된다 (자기 자신을 앵커로 붙이지 않는다)
    assert anchor_mod.identity_ref(run_dir, "down_walk", request=request,
                                   quiet=True).name == "base-source.png"


def test_view_chip_is_a_live_bake_endpoint(direction_run: Path) -> None:
    """앵커 칩 = `/api/anchor` 라이브 베이크 (파일 스냅샷이면 편집 직후 낡는다)."""
    from serve_curation import build_run_state

    run_dir = direction_run
    _save_curation(run_dir, {"down_idle": {"deleted": [0], "selected": [1, 2, 3]}})
    snapshot = build_run_state(run_dir)
    walk = next(s for s in snapshot["states"] if s["name"] == "down_walk")
    anchor_chip = next(r for r in walk["refs"] if r["role"] == "anchor")
    assert anchor_chip["url"].startswith("/api/anchor?direction=down")
    assert anchor_chip["name"] == "down_idle#1"
    group = next(g for g in snapshot["directionGroups"] if g["direction"] == "down")
    assert group["anchorFrame"] == {"state": "down_idle", "index": 1, "source": "default"}
    assert group["anchorError"] is None
    # 앵커 행 자체에는 앵커 칩이 붙지 않는다 (자기 자신이 앵커다)
    idle = next(s for s in snapshot["states"] if s["name"] == "down_idle")
    assert not [r for r in idle["refs"] if r["role"] == "anchor"]


def test_api_anchor_serves_the_baked_png(direction_run: Path) -> None:
    import threading
    import urllib.request
    from functools import partial
    from http.server import ThreadingHTTPServer

    from serve_curation import CurationHandler

    run_dir = direction_run
    _save_curation(run_dir, {}, anchors={"down": {"state": "down_idle", "index": 2}})
    CurationHandler.run_dir = run_dir
    CurationHandler.lang = "en"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(CurationHandler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{srv.server_address[1]}/api/anchor?direction=down") as res:
            assert res.status == 200
            assert res.headers["Content-Type"] == "image/png"
            assert res.headers["X-Anchor-Frame"] == "down_idle#2"
            assert res.headers["X-Anchor-Source"] == "picked"
            assert res.read()[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        srv.shutdown()


def test_curation_post_without_anchors_key_keeps_the_pick(direction_run: Path) -> None:
    """엔진(CLI)이 심은 지정은 그 키를 모르는 저장에 조용히 사라지지 않는다."""
    import json as _json
    import threading
    import urllib.request
    from functools import partial
    from http.server import ThreadingHTTPServer

    from serve_curation import CurationHandler

    run_dir = direction_run
    assert anchor_mod.run(run_dir=run_dir, pick="down_idle#2") == 0
    CurationHandler.run_dir = run_dir
    CurationHandler.lang = "en"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(CurationHandler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def post(body: dict) -> int:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/curation",
                                     data=_json.dumps(body).encode(), method="POST")
        with urllib.request.urlopen(req) as res:
            return res.status

    try:
        revision = _json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/run").read())["runRevision"]
        # anchors 키가 없는 저장 → 이월
        assert post({"version": 1, "kind": "sprite-gen-curation", "runRevision": revision,
                     "states": {"down_idle": {"selected": [0, 1]}}}) == 200
        assert _pins(load_curation(run_dir))["down"] == {"state": "down_idle", "index": 2}
        # 빈 anchors 를 명시로 실으면 해제 (뷰의 해제 동작)
        revision = _json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/run").read())["runRevision"]
        assert post({"version": 1, "kind": "sprite-gen-curation", "runRevision": revision,
                     "anchors": {}, "states": {"down_idle": {"selected": [0, 1]}}}) == 200
        assert _pins(load_curation(run_dir)) == {}
        assert "anchors" not in _json.loads(
            (run_dir / "curation.json").read_text(encoding="utf-8"))
    finally:
        srv.shutdown()
