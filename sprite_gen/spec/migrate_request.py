# SPDX-License-Identifier: Apache-2.0
"""은퇴한 `fit` 키를 런의 `sprite-request.json` 에서 현행 키로 옮긴다 (일회성, 명시 실행).

`fit.pixel_perfect` 는 2026-07-25 에 `fit.pixel_unfake` 로 교체됐다. 로더
(`runio.load_request`) 는 은퇴 키를 **메모리에서만** 현행 키로 읽어 기존 런이 무손실로
계속 돌게 하고, 디스크 스키마를 실제로 바꾸는 일은 이 명령에서만 한다.

왜 나뉘어 있나: 예전엔 로더가 정규화한 문서를 곧바로 다시 썼고, 그래서 승인된 런에
`state_revision()` 을 부른 것만으로 그 런의 `sprite-request.json` 이 재기록됐다. 조회가
canonical 런의 바이트를 바꾸면 `run_revision`(request 바이트를 해싱한다)이 함께 움직이고,
그 런의 큐레이션 사이드카가 stale 로 떨어진다. 읽는 쪽은 읽기만 하고, 쓰는 일은 사용자가
부른 명령 안에서만 일어난다 (plan sprite-gen/state-revision-read-mutation).

이 명령이 바꾸는 것은 키 이름 하나뿐이고 값·의미는 그대로다. 다만 파일 바이트가 바뀌므로
`run_revision` 은 바뀐다 — 큐레이션 사이드카는 행별 `revision` 스탬프로 구제되고
(`curation.load_curation_report`), 스탬프가 없는 행은 평소 규칙대로 드롭된다. 그 결과를
쓰기 전에 미리 말한다: 조용히 잃는 것이 없어야 사용자가 실행 시점을 고를 수 있다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sprite_gen.curate.curation import CURATION_FILENAME, run_revision
from sprite_gen.spec.runio import LEGACY_FIT_KEYS, REQUEST_FILENAME, migrate_request_file


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("run_dir", type=Path, help="run directory holding sprite-request.json")
    parser.add_argument("--apply", action="store_true",
                        help="write the migrated request (default is a dry run)")


def _curation_impact(run_dir: Path) -> list[str]:
    """이관이 큐레이션 사이드카에 무엇을 할지 미리 읽어 보고한다 (읽기 전용).

    사이드카가 없거나 이미 세대가 어긋나 있으면 이관이 더 잃게 만드는 것이 없다. 지금
    세대가 맞는 사이드카만 영향을 받고, 그중 행별 스탬프가 없는 행이 드롭 대상이다."""
    path = run_dir / CURATION_FILENAME
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"{CURATION_FILENAME} 을 읽을 수 없다 — 이관 후 상태를 미리 볼 수 없다"]
    if doc.get("run_revision") != run_revision(run_dir):
        return []
    unstamped = [name for name, entry in (doc.get("states") or {}).items()
                 if not (isinstance(entry, dict) and entry.get("revision"))]
    if not unstamped:
        return [f"{CURATION_FILENAME}: 행별 스탬프로 전부 구제된다"]
    return [f"{CURATION_FILENAME}: 행별 스탬프가 없어 드롭될 행 — {', '.join(sorted(unstamped))}"]


def run(**kwargs: object) -> int:
    run_dir = Path(str(kwargs["run_dir"]))
    apply_changes = bool(kwargs.get("apply"))
    path = run_dir / REQUEST_FILENAME
    if not path.is_file():
        print(f"migrate-request: {path} 가 없다")
        return 1

    moved = ", ".join(f"{legacy} -> {current}" for legacy, current in LEGACY_FIT_KEYS.items())
    if not migrate_request_file(run_dir):
        print(f"migrate-request: {path} 는 이미 현행 키뿐이다 — 옮길 것이 없다")
        return 0

    print(f"migrate-request: {path} 의 은퇴 fit 키 이관 ({moved})")
    for line in _curation_impact(run_dir):
        print(f"  {line}")
    if not apply_changes:
        print("migrate-request: dry-run. 실제로 쓰려면 --apply")
        return 0
    migrate_request_file(run_dir, apply=True)
    print(f"migrate-request: {path} 갱신. 값·의미는 그대로고 키 이름만 바뀐다 "
          f"(파일 바이트가 바뀌므로 run_revision 은 움직인다).")
    return 0
