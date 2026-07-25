# SPDX-License-Identifier: Apache-2.0
"""호흡을 **끈** 상태에서 큐레이터가 워프를 그리지 않는지 (소스 계약).

봉투에서 위상 0 은 항등이 아니다 — 진행파 지연 때문에 `t=0` 도 변형된 프레임이다.
그래서 "꺼짐"을 "위상 0" 으로 표현하면 안 된다: 굽기는 off 면 `state_breathe` 가 None
이라 원본을 굽는데, 프리뷰는 워프된 정지화면을 보여준다 (실측 348~800B 차이,
새미 검증 2026-07-25).

`breatheComposite` 호출부는 전부 "호흡이 켜져 있는가"로 게이트돼야 한다. 소스 수준
계약 테스트인 이유: 이 경로는 캔버스 렌더 루프 안이라 단위 실행이 어렵고, 되살아나는
방식이 항상 "가드를 빼먹는" 형태라 호출부 형태를 고정하는 게 정확히 맞는 그물이다.
(`tests/test_curator_pixel_scaling_ssot.py` 가 같은 방식의 선례다.)
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CURATOR_SRC = ROOT / "scripts" / "curator" / "src"
CALL = re.compile(r"breatheComposite\s*\(|breatheComposeForPreview\s*\(")
# 파이썬 굽기 소비자 — 위상 0 을 건너뛰면 그 슬롯만 원본이 구워진다.
PY_CONSUMERS = ("sprite_gen/compose_atlas.py", "sprite_gen/compose_gif.py")
PY_CALL = re.compile(r"phase_frame\s*\(|bake_breathe_sequence\s*\(")

# 게이트는 **호출부 같은 줄**에 있어야 한다. 감싸는 블록까지 인정하면 그물이 무의미해진다
# — 실측: 25줄 창으로 넓혔더니 무관한 `bm.enabled ?`(위상 계산 줄)가 걸려서, 정작 워프
# 호출의 가드를 빼도 통과했다. 그래서 계약을 "호출부에서 삼항으로" 로 통일했다.
# (`stateBreathe()` 가 꺼진 줄에 null 을 주므로 `bcfg ?` 도 유효한 켜짐 게이트다.)
GUARDS = ("bm.enabled ?", "bcfg ?")


def _call_sites():
    for path in sorted(CURATOR_SRC.glob("*.js")):
        if path.name == "breathe.js":
            continue                      # 정의부 — 호출부가 아니다
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CALL.search(line):
                yield path.name, lineno, line.strip()


def test_there_are_call_sites_to_guard():
    """그물이 빈 채로 통과하지 않게 — 호출부가 사라졌으면 이 테스트를 고쳐야 한다."""
    sites = list(_call_sites())
    assert sites, "breatheComposite 호출부가 하나도 없다 — 계약이 옮겨갔는지 확인하라"


@pytest.mark.parametrize("site", list(_call_sites()), ids=lambda s: f"{s[0]}:{s[1]}")
def test_every_breathe_composite_call_is_gated_on_enabled(site):
    name, lineno, line = site
    assert any(guard in line for guard in GUARDS), (
        f"{name}:{lineno} 가 켜짐 여부로 게이트되지 않았다 — 꺼진 줄이 워프된다.\n"
        f"  {line}\n"
        f"  허용 형태: {', '.join(GUARDS)}")


# ── 파이썬 굽기 소비자 ──────────────────────────────────────────────

def _py_call_sites():
    for rel in PY_CONSUMERS:
        path = ROOT / rel
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, 1):
            if PY_CALL.search(line) and not line.lstrip().startswith("#"):
                # 이 호출을 감싸는 가장 가까운 `if` 조건 (들여쓰기가 더 얕은 첫 if)
                indent = len(line) - len(line.lstrip())
                guard = ""
                for prev in range(lineno - 2, -1, -1):
                    cand = lines[prev]
                    if not cand.strip():
                        continue
                    cind = len(cand) - len(cand.lstrip())
                    if cind < indent and cand.lstrip().startswith("if "):
                        guard = cand.strip()
                        break
                    if cind < indent:
                        break
                yield rel, lineno, line.strip(), guard


def test_there_are_python_bake_call_sites():
    assert list(_py_call_sites()), "굽기 소비자에서 호흡 호출부가 사라졌다 — 계약 위치 확인"


@pytest.mark.parametrize("site", list(_py_call_sites()), ids=lambda s: f"{s[0].split('/')[-1]}:{s[1]}")
def test_python_bake_never_skips_a_phase(site):
    """굽기가 **위상 값으로** 워프 여부를 가르면 안 된다.

    `if breathe_cfg and breathe_phase:` 는 위상 0 슬롯을 건너뛰어 그 칸만 원본이 되고,
    같은 런의 GIF 굽기와 그림이 갈린다 (round-1 reject 1, 실측 353px). 게이트는
    "호흡이 켜져 있는가"(`breathe_cfg`)만 봐야 한다.

    이 그물이 필요한 이유: 부리가 변이 테스트로 확인했다 — `compose_atlas` 의 가드를
    옛 형태로 되돌려도 `pytest -k "atlas or breathe"` 40개가 **전부 통과**했다.
    JS 쪽엔 그물이 있었고 파이썬 쪽만 비어 있었다 (2026-07-25)."""
    rel, lineno, line, guard = site
    assert "phase" not in guard.replace("breathe_cfg", ""), (
        f"{rel}:{lineno} 의 가드가 위상 값을 본다 — 위상 0 이 안 구워진다.\n"
        f"  가드: {guard}\n  호출: {line}")


# ── 줄 단위 신선도 검사가 실제로 걸리는가 ──────────────────────────

FRESH = re.compile(r"breatheAssertFresh\s*\(")
# 굽기와 같은 그림을 내보내는 경로 — 여기서 신선도를 안 보면 낡은 해부로 굽는다.
FRESH_CONSUMERS = ("row-export.js",)


@pytest.mark.parametrize("name", FRESH_CONSUMERS)
def test_export_paths_check_anatomy_freshness(name):
    """클라에서 굽는 경로는 기준 프레임 신선도를 확인해야 한다.

    `row-export` 는 서버를 안 거치고 WebM/MP4 를 만든다. 얼린 해부가 지금의 기준
    프레임에서 나온 게 아니면 GIF(서버 굽기)와 다른 애니메이션이 파일로 나간다
    (슉슉이 실측 2026-07-25: 픽셀 편집 후 최대 617바이트)."""
    src = (CURATOR_SRC / name).read_text(encoding="utf-8")
    assert FRESH.search(src), (
        f"{name} 이 breatheAssertFresh 를 안 부른다 — 낡은 해부로 구운 파일이 사용자에게 간다")
