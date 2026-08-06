# SPDX-License-Identifier: Apache-2.0
"""Reroll a state's row as a NEW take — candidate pool, never a replacement.

리롤 = 같은 행을 한 번 더 생성해 **테이크로 병기**한다 (수홍 2026-07-19 "리롤버튼
눌러서 후보군에 추가"). primary raw 는 건드리지 않고 `raw/<...>.takes/rerollN.png`
+ request `states.<state>.takes` 로 기록되므로, 추출 후 큐레이션 뷰에는 기존 프레임
뒤에 `rerollN#i` 라벨 후보가 이어 붙는다. 픽/기각은 사람 몫.

생성 refs 는 행 생성 계약(directional-anchor-workflow)을 따른다 — 결정은 `anchor.py`
하나가 소유한다 (`identity_ref`): 방향 런의 액션 행이면 그 방향 앵커를 큐레이션 진실에서
매번 다시 구워 붙이고, 앵커 행 자체/단순 런이면 `base-source.*` 를 붙인다. 여기에
레이아웃 가이드를 더한 두 장이 리롤 refs 다.

AI 개입은 raw 생성 한 곳 — 최종 프레임은 언제나 결정론 추출이 굽는다. 부분 추출은
없다 (공유 팔레트가 추출 배치 구성에 결합 — interpolate 와 같은 계약).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from sprite_gen.curate.anchor import identity_ref
from sprite_gen.spec.runio import load_request, write_request
from sprite_gen.gen import PROVIDERS, generate_image
from sprite_gen.spec.layout import guide_rel, prompt_rel, take_raw_rel


def next_reroll_label(request: dict[str, Any], state: str) -> str:
    takes = request["states"][state].get("takes") or []
    used = set()
    for take in takes:
        m = re.fullmatch(r"reroll(\d+)", str(take.get("label") or ""))
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"reroll{n}"


def record_take(run_dir: Path, state: str, label: str, frames: int) -> None:
    """request 의 takes 선언 갱신 (멱등) — write_take(interpolate)와 같은 기록 계약.

    Isolation: 생성이 수 분 걸리므로 시작 시점에 읽은 request 로 쓰면 그 사이의
    다른 변경(다른 행 리롤·fps 수정)을 덮어쓴다 (lost update). publish_guard
    배타락 안에서 **fresh 재독 → 갱신 → 원자 쓰기** 로만 기록한다."""
    from sprite_gen.spec.runio import publish_guard

    with publish_guard(run_dir):
        # 락 안 fresh 재독도 게이트를 통과한다 — 이관 판정·두 키 hard fail 이 한 곳에만 있어야
        # 한다. 게이트는 읽기만 하므로 락 안에서도 안전하다.
        fresh = load_request(run_dir)
        takes = fresh["states"][state].setdefault("takes", [])
        entry = next((t for t in takes if t.get("label") == label), None)
        if entry is None:
            takes.append({"label": label, "frames": frames})
        else:
            entry["frames"] = frames
        # 쓰기도 게이트 경유 (`write_request`): 테이크 기록은 스키마와 무관한 편집이라
        # 디스크의 키 형태를 그대로 둔다 (스키마 이관은 `sprite-gen migrate-request` 만).
        write_request(run_dir, fresh)


def reroll_state(run_dir: Path | str, state: str, provider: str = "codex",
                 label: str | None = None) -> Path:
    """행 리롤 한 번: 테이크 raw 생성 + request takes 기록. 반환 = 테이크 raw 경로."""
    run_dir = Path(run_dir)
    # 읽기는 **게이트만** 쓴다 (`load_request`): 여기서 raw 로 읽으면 아직 이관되지 않은
    # 레거시 런에서 아래 `fit.pixel_unfake` 검사가 항상 False 가 되어, 언페이크가 켜진 런의
    # 리롤을 "켜져 있지 않다" 며 거부한다 (젯비 검증 2026-07-26 R1 재현). 리롤은 뷰가 **별도
    # 서브프로세스**로 띄우므로 뷰 프로세스의 in-memory 이관도 여기로 넘어오지 않는다.
    request = load_request(run_dir)
    if state not in request.get("states", {}):
        raise SystemExit(f"unknown state: {state}")
    label = label or next_reroll_label(request, state)
    if "/" in label or label.startswith("."):
        raise SystemExit(f"take label must be filesystem-safe: {label!r}")
    # 테이크는 pixel_unfake 추출 계약 위에서만 병합된다 — 생성비를 쓰기 전에 막는다
    # (실측 2026-07-19: 비-pp 런에서 생성까지 간 뒤 추출이 "takes require
    # fit.pixel_unfake" 로 죽었다 — fail-loud 는 맞지만 늦다).
    if not (request.get("fit") or {}).get("pixel_unfake"):
        raise SystemExit("reroll: takes require fit.pixel_unfake on this run "
                         "(candidate pool rides the take pipeline)")
    prompt_path = run_dir / prompt_rel(request, state)
    if not prompt_path.is_file():
        raise SystemExit(f"reroll: prompt file missing: {prompt_path}")
    refs = [identity_ref(run_dir, state, request=request)]
    guide_path = run_dir / guide_rel(request, state)
    if guide_path.is_file():
        refs.append(guide_path)
    target = run_dir / take_raw_rel(request, state, label)
    target.parent.mkdir(parents=True, exist_ok=True)
    generate_image(provider, prompt_path.read_text(encoding="utf-8"), target, refs=refs)
    frames = int(request["states"][state]["frames"])
    record_take(run_dir, state, label, frames)
    print(f"[reroll] take written: {target.relative_to(run_dir)} "
          f"(state={state}, frames={frames}, provider={provider})")
    return target


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--provider", choices=PROVIDERS, default="codex",
                        help="생성 백엔드 (기본 codex)")
    parser.add_argument("--label", default=None, help="테이크 라벨 (기본 rerollN 자동 증가)")
    parser.add_argument("--extract", action="store_true",
                        help="기록 후 전체 배치 재추출까지 수행 (부분 추출은 팔레트 배치 결합 때문에 지원하지 않음)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    reroll_state(args.run_dir, args.state, provider=args.provider, label=args.label)
    if args.extract:
        from sprite_gen.frames import extract as extract_module
        code = extract_module.run(run_dir=Path(args.run_dir))
        if code != 0:
            return code
        print("[reroll] full-batch re-extraction done")
    else:
        # 인터프리터는 sys.executable 로 찍는다 — 이 힌트는 붙여넣어 바로 실행되는
        # 명령이고, 전역 `python3` 는 이 패키지의 의존(NumPy/Pillow)이 없는 다른
        # 인터프리터일 수 있다 (SKILL.md "실행 인터프리터" 게이트).
        print("[reroll] next: re-extract the FULL batch so the shared palette stays "
              f"consistent -> {sys.executable} -m sprite_gen.frames.extract --run-dir {args.run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
