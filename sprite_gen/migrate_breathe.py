# SPDX-License-Identifier: Apache-2.0
"""폐기된 분할선 호흡 설정을 봉투 스키마로 옮긴다 (일회성).

`states.<state>.breathe` 의 `splits`/`amplitude`/`subpixel` 은 2026-07-25 에 봉투
경계로 교체되면서 폐기됐고, `curation.state_breathe` 가 요란하게 거부한다. 이 도구는
그 거부를 뚫는 우회로가 **아니다** — 사용자가 명시적으로 실행하는 변환이다.

의도적으로 하지 않는 것: 옛 값을 새 값으로 자동 환산하지 않는다.

  · `splits` 는 "이 선 위가 움직인다" 였고 새 `rigid_row` 는 "이 위는 안 움직인다" 다.
    반대 개념이라 자리 이동으로 옮길 수 없다. 새 경계는 실루엣에서 자동 검출된다.
  · `amplitude` 는 정수 px 행 이동이었고 새 `depth` 는 몸통 높이 비율이다. 단위가
    비교 불가라 숫자를 옮기면 그럴듯하지만 틀린 값이 된다.

그래서 `breaths` 만 보존하고 나머지는 기본값으로 두며, 무엇을 버렸는지 전부 출력한다.
호흡 세기는 큐레이터에서 다시 잡는 게 맞다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sprite_gen.curation import (BREATHE_BREATHS_MAX, BREATHE_DEPTH_MAX, BREATHE_LAG_MAX,
                                 CURATION_FILENAME, RETIRED_BREATHE_KEYS)
from sprite_gen.breathe import DEFAULT_DEPTH, DEFAULT_LAG
from sprite_gen.runio import atomic_write_text


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("run_dir", type=Path, help="run directory holding curation.json")
    parser.add_argument("--apply", action="store_true",
                        help="write the migrated sidecar (default is a dry run)")


def migrate_entry(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """한 상태의 breathe 설정 -> (새 설정, 버린 것 설명)."""
    dropped = [f"{key}={raw[key]!r} ({reason})"
               for key, reason in RETIRED_BREATHE_KEYS.items() if key in raw]
    fresh: dict[str, Any] = {"depth": DEFAULT_DEPTH, "lag": DEFAULT_LAG}
    try:
        want = int(raw.get("breaths", 1))
    except (TypeError, ValueError):
        want = 1
        dropped.append(f"breaths={raw.get('breaths')!r} (정수가 아니라 1 로 되돌림)")
    fresh["breaths"] = max(1, min(BREATHE_BREATHS_MAX, want))
    if fresh["breaths"] != want:
        # 깎았으면 **말한다.** 조용히 깎으면 모듈 계약("무엇을 버렸는지 전부 출력한다")이
        # 깨지고, round-5 가 파이썬에서 없앤 조용한 클램프가 여기 남는다.
        dropped.append(f"breaths={want!r} (범위 1~{BREATHE_BREATHS_MAX} 밖이라 {fresh['breaths']} 로 조정)")
    # 이월하는 값도 **범위를 확인한다.** 안 하면 마이그레이션 산출물이 굽기에서 거부당한다
    # (슉슉이 실측 2026-07-25: depth 0.5 를 그대로 이월해 결과 사이드카가 안 구워졌다).
    for key, lo, hi in (("depth", 0.005, BREATHE_DEPTH_MAX), ("lag", 0.0, BREATHE_LAG_MAX)):
        if key not in raw:
            continue
        try:
            value = float(raw[key])
        except (TypeError, ValueError):
            dropped.append(f"{key}={raw[key]!r} (숫자가 아니라 기본값 {fresh[key]} 사용)")
            continue
        if lo <= value <= hi:
            fresh[key] = value
        else:
            dropped.append(f"{key}={value!r} (범위 [{lo}, {hi}] 밖이라 기본값 {fresh[key]} 사용)")
    for key in ("rigid_row", "anatomy"):
        if key in raw:
            fresh[key] = raw[key]
    return fresh, dropped


def run(**kwargs: object) -> int:
    run_dir = Path(str(kwargs["run_dir"]))
    apply_changes = bool(kwargs.get("apply"))
    path = run_dir / CURATION_FILENAME
    if not path.is_file():
        print(f"migrate-breathe: {path} 가 없다")
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    states = data.get("states")
    if not isinstance(states, dict):
        print(f"migrate-breathe: {path} 에 states 가 없다 — 옮길 것이 없다")
        return 0

    touched = 0
    for state, entry in states.items():
        raw = entry.get("breathe") if isinstance(entry, dict) else None
        if not isinstance(raw, dict):
            continue
        if not any(key in raw for key in RETIRED_BREATHE_KEYS):
            print(f"  {state}: 이미 봉투 스키마 — 그대로 둔다")
            continue
        fresh, dropped = migrate_entry(raw)
        touched += 1
        print(f"  {state}: breaths {fresh['breaths']} · depth {fresh['depth']} · lag {fresh['lag']}")
        for line in dropped:
            print(f"      버림 {line}")
        entry["breathe"] = fresh

    if not touched:
        print("migrate-breathe: 옮길 분할선 설정이 없다")
        return 0
    if not apply_changes:
        print(f"migrate-breathe: dry-run ({touched} 개 상태). 실제로 쓰려면 --apply")
        return 0
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"migrate-breathe: {path} 갱신 ({touched} 개 상태). 호흡 세기는 큐레이터에서 다시 잡아라.")
    return 0
