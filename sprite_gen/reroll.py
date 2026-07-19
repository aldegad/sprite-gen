# SPDX-License-Identifier: Apache-2.0
"""Reroll a state's row as a NEW take — candidate pool, never a replacement.

리롤 = 같은 행을 한 번 더 생성해 **테이크로 병기**한다 (수홍 2026-07-19 "리롤버튼
눌러서 후보군에 추가"). primary raw 는 건드리지 않고 `raw/<...>.takes/rerollN.png`
+ request `states.<state>.takes` 로 기록되므로, 추출 후 큐레이션 뷰에는 기존 프레임
뒤에 `rerollN#i` 라벨 후보가 이어 붙는다. 픽/기각은 사람 몫.

생성 refs 는 행 생성 계약(directional-anchor-workflow)을 따른다:
- 방향(택소노미) 런의 액션 행 = 대상 방향 idle 앵커 + 레이아웃 가이드.
  앵커 ref 는 실재하는 것 중 우선순위로 고른다:
  ① `references/anchors/<dir>-idle-x8.png` (런이 준비한 확대 앵커 ref)
  ② `curated/<dir>_idle/frame-0.png` 또는 `curated/<dir>_idle-frame-0.png` (curated export 진실)
  ③ `frames/<dir>/idle/frame-0.png` (추출 캐노니컬 폴백)
- 방향 런의 idle 앵커 행 자체 / 단순 런 = base-source + 가이드 (pre-anchor 체인).

AI 개입은 raw 생성 한 곳 — 최종 프레임은 언제나 결정론 추출이 굽는다. 부분 추출은
없다 (공유 팔레트가 추출 배치 구성에 결합 — interpolate 와 같은 계약).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .gen import PROVIDERS, generate_image
from .layout import guide_rel, prompt_rel, split_state, take_raw_rel


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


def _find_base_source(run_dir: Path) -> Path | None:
    for candidate in sorted(run_dir.glob("base-source.*")):
        if candidate.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return candidate
    return None


def resolve_identity_ref(run_dir: Path, request: dict[str, Any], state: str) -> Path:
    """행 정체성 ref — 방향 액션 행은 대상 방향 idle 앵커, 그 외는 base-source."""
    direction, pose = split_state(request, state)
    if direction is not None and pose != "idle":
        candidates = [
            run_dir / "references" / "anchors" / f"{direction}-idle-x8.png",
            run_dir / "curated" / f"{direction}_idle" / "frame-0.png",
            run_dir / "curated" / f"{direction}_idle-frame-0.png",
            run_dir / "frames" / direction / "idle" / "frame-0.png",
        ]
        for c in candidates:
            if c.is_file():
                return c
        raise SystemExit(
            f"reroll: no identity anchor found for direction {direction!r} "
            f"(looked for anchors/x8, curated export, extracted canonical)")
    base = _find_base_source(run_dir)
    if base is None:
        raise SystemExit(f"reroll: no base-source image in run dir: {run_dir}")
    return base


def record_take(run_dir: Path, request: dict[str, Any], state: str, label: str,
                frames: int) -> None:
    """request 의 takes 선언 갱신 (멱등) — write_take(interpolate)와 같은 기록 계약."""
    takes = request["states"][state].setdefault("takes", [])
    entry = next((t for t in takes if t.get("label") == label), None)
    if entry is None:
        takes.append({"label": label, "frames": frames})
    else:
        entry["frames"] = frames
    (run_dir / "sprite-request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reroll_state(run_dir: Path | str, state: str, provider: str = "codex",
                 label: str | None = None) -> Path:
    """행 리롤 한 번: 테이크 raw 생성 + request takes 기록. 반환 = 테이크 raw 경로."""
    run_dir = Path(run_dir)
    request_path = run_dir / "sprite-request.json"
    if not request_path.is_file():
        raise SystemExit(f"not a sprite-gen run dir (no sprite-request.json): {run_dir}")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if state not in request.get("states", {}):
        raise SystemExit(f"unknown state: {state}")
    label = label or next_reroll_label(request, state)
    if "/" in label or label.startswith("."):
        raise SystemExit(f"take label must be filesystem-safe: {label!r}")
    # 테이크는 pixel_perfect 추출 계약 위에서만 병합된다 — 생성비를 쓰기 전에 막는다
    # (실측 2026-07-19: 비-pp 런에서 생성까지 간 뒤 추출이 "takes require
    # fit.pixel_perfect" 로 죽었다 — fail-loud 는 맞지만 늦다).
    if not (request.get("fit") or {}).get("pixel_perfect"):
        raise SystemExit("reroll: takes require fit.pixel_perfect on this run "
                         "(candidate pool rides the take pipeline)")
    prompt_path = run_dir / prompt_rel(request, state)
    if not prompt_path.is_file():
        raise SystemExit(f"reroll: prompt file missing: {prompt_path}")
    refs = [resolve_identity_ref(run_dir, request, state)]
    guide_path = run_dir / guide_rel(request, state)
    if guide_path.is_file():
        refs.append(guide_path)
    target = run_dir / take_raw_rel(request, state, label)
    target.parent.mkdir(parents=True, exist_ok=True)
    generate_image(provider, prompt_path.read_text(encoding="utf-8"), target, refs=refs)
    frames = int(request["states"][state]["frames"])
    record_take(run_dir, request, state, label, frames)
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
        from . import extract as extract_module
        code = extract_module.run(run_dir=Path(args.run_dir))
        if code != 0:
            return code
        print("[reroll] full-batch re-extraction done")
    else:
        print("[reroll] next: re-extract the FULL batch so the shared palette stays "
              f"consistent -> python3 -m sprite_gen.extract --run-dir {args.run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
