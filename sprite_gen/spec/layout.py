# SPDX-License-Identifier: Apache-2.0
"""Run-dir file taxonomy — 경로 구성의 단일 SSoT.

기본 계약(택소노미, 수홍 확정 2026-07-14): 상태 ID 는 `<direction>_<pose>` 그대로
두고 **파일 경로만** 방향/자세 폴더로 나눈다. 자세가 늘어나도 flat 폴더가
비대해지지 않는다:

    raw/<direction>/<pose>.png                  # 생성 스트립 원본
    frames/<direction>/<pose>/frame-N.png       # 추출 프레임 (+ .plain / orig/)
    references/layout-guides/<direction>/<pose>.png
    prompts/<direction>/<pose>.txt

방향 계약(directions)이 없는 런(이펙트 등)은 자세 1단(`raw/<state>.png`)이 곧
택소노미다. `layout` 필드가 없는 기존 런은 flat 레거시로 계속 동작한다.

프로젝트별 정비 방식이 지침으로 주어지면 그 지침이 우선하고, 이 모듈의 기본
계약은 지침이 없을 때의 표준이다.

경로 소비 원칙: 이미 추출된 프레임을 읽는 소비자(compose/export/GIF/cycle/뷰)는
frames-manifest 의 `files` 경로를 따른다(`row_frame_rel`) — 패턴 조립 금지.
경로를 새로 만드는 쪽(prepare/extract)만 리졸버로 조립한다.
"""

from __future__ import annotations

from typing import Any

TAXONOMY = "taxonomy/v1"


def is_taxonomy(request: dict[str, Any]) -> bool:
    """방향 폴더 분리가 활성인 런인가 — layout 계약 + directions 계약 둘 다 필요."""
    return request.get("layout") == TAXONOMY and bool((request.get("directions") or {}).get("set"))


def split_state(request: dict[str, Any], state: str) -> tuple[str | None, str]:
    """상태 ID -> (direction, pose). 택소노미가 아니거나 방향 접두사가 없으면 (None, state)."""
    if not is_taxonomy(request):
        return None, state
    for direction in request["directions"]["set"]:
        if state.startswith(direction + "_"):
            return direction, state[len(direction) + 1:]
    return None, state


def _joined(prefix: str, request: dict[str, Any], state: str, suffix: str) -> str:
    direction, pose = split_state(request, state)
    if direction:
        return f"{prefix}/{direction}/{pose}{suffix}"
    return f"{prefix}/{state}{suffix}"


def raw_rel(request: dict[str, Any], state: str) -> str:
    return _joined("raw", request, state, ".png")


def state_frame_total(request: dict[str, Any], state: str) -> int:
    """상태의 행 프레임 풀 총량 = primary `frames` + 선언된 테이크들의 `frames` 합.

    소비자(합성/뷰/검사)가 행 크기를 request 에서 셀 때는 반드시 이 값을 쓴다 —
    primary 만 세면 takes 가 있는 행이 '초과 프레임'으로 오판된다."""
    spec = request["states"][state]
    total = int(spec.get("frames", 0))
    total += sum(int(take.get("frames", 0)) for take in (spec.get("takes") or []))
    return total


def take_raw_rel(request: dict[str, Any], state: str, label: str) -> str:
    """추가 테이크(같은 상태의 후보/보강 생성 스트립) 원본 경로.

    상태의 primary raw 옆 `.takes/` 폴더가 소유한다 — 예: `raw/down/idle.takes/blink.png`
    (택소노미) / `raw/wave.takes/alt.png` (flat). 테이크는 request
    `states.<state>.takes: [{label, frames}]` 로 선언되고, 추출이 primary 뒤에
    이어붙여 한 행의 프레임 풀을 만든다."""
    base = raw_rel(request, state)
    return f"{base[: -len('.png')]}.takes/{label}.png"


def frames_dir_rel(request: dict[str, Any], state: str) -> str:
    return _joined("frames", request, state, "")


def guide_rel(request: dict[str, Any], state: str) -> str:
    return _joined("references/layout-guides", request, state, ".png")


def prompt_rel(request: dict[str, Any], state: str) -> str:
    return _joined("prompts", request, state, ".txt")


def row_frame_rel(row: dict[str, Any], index: int, variant: str = "pixel") -> str:
    """추출된 프레임의 실제 경로 — manifest row 의 files 가 위치의 SSoT.

    'plain' 은 같은 자리의 .plain.png 쌍둥이. 인덱스가 파일 목록을 벗어나면
    fail-loud (조용한 패턴 폴백 금지)."""
    files = row.get("files") or []
    if index >= len(files):
        raise SystemExit(
            f"frame index {index} out of range for state '{row.get('state')}' "
            f"({len(files)} extracted frames) — stale selection or incomplete generation")
    rel = files[index]
    if variant == "plain":
        return rel[: -len(".png")] + ".plain.png"
    return rel


def row_orig_rel(row: dict[str, Any], index: int) -> str:
    """원본 화질(orig/) 쌍둥이 경로 — 프레임 파일과 같은 폴더의 orig/ 하위."""
    rel = row_frame_rel(row, index)
    head, _, name = rel.rpartition("/")
    return f"{head}/orig/{name}"
