# SPDX-License-Identifier: Apache-2.0
"""Compose component-row frames into a game atlas and runtime manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.breathe import anatomy_report, fit_breathe_pattern, phase_frame, resolve_anatomy
from sprite_gen.curation import apply_pixel_edits, apply_transform, edit_index, frame_variant, load_curation, pixel_snap_scale, source_frame_index, state_breathe, state_pixel_ops, state_plan
from sprite_gen.layout import row_frame_rel, state_frame_total
from sprite_gen.extract import heal_run, require_frames_manifest
from sprite_gen.layers import (has_layer_contract, landmark_map, require_valid_layer_request,
                               state_track)
from sprite_gen.runio import acquire_run_dir_lock, atomic_save_image, atomic_write_text, load_request


def alpha_nonzero_count(image: Image.Image) -> int:
    return sum(image.getchannel("A").histogram()[1:])


def cell_geometry(cell: dict[str, Any]) -> tuple[int, int]:
    width = int(cell.get("width", cell.get("size", 0)))
    height = int(cell.get("height", cell.get("size", 0)))
    if width <= 0 or height <= 0:
        raise SystemExit("cell width/height must be positive in sprite-request.json")
    return width, height


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--atlas", default="sprite-sheet-alpha.png")
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--report", default="sprite-sheet-alpha.report.json")
    parser.add_argument("--min-used-pixels", type=int, default=400)
    return parser


def _namespace_from_kwargs(**kwargs: object) -> argparse.Namespace:
    parser = _build_parser()
    values: dict[str, object] = {}
    remaining = dict(kwargs)
    for action in parser._actions:
        if action.dest == "help":
            continue
        default = [] if action.nargs == "*" else action.default
        if default is argparse.SUPPRESS:
            default = None
        value = remaining.pop(action.dest, default)
        if getattr(action, "required", False) and value is None:
            raise TypeError(f"missing required argument: {action.dest}")
        values[action.dest] = value
    if remaining:
        names = ", ".join(sorted(remaining))
        raise TypeError(f"unexpected keyword argument(s): {names}")
    return argparse.Namespace(**values)
def _run(args: argparse.Namespace):

    run_dir = args.run_dir.expanduser().resolve()
    # 소비 전 파생 캐시 자가치유 (실시간 계약): 합성이 굽는 것 = 항상 현재 엔진의
    # 프레임. heal 은 서브프로세스 추출로 락을 따로 잡으므로 compose 락 획득 전에.
    heal_report = heal_run(run_dir)
    for note in heal_report["notes"]:
        print(f"[heal] {note}", file=sys.stderr)
    if heal_report["healed"]:
        print(f"[heal] re-derived stale rows: {', '.join(heal_report['healed'])}", file=sys.stderr)
    acquire_run_dir_lock(run_dir, "compose_sprite_atlas")
    request = load_request(run_dir)
    # A run that declares no rig / track / layers is not a layer run and this is a
    # no-op (docs/layer-tracks.md §1). One that does is checked before any bake
    # work, so an invalid rig never reaches the manifest as half-declared pivots.
    require_valid_layer_request(request)
    frames_manifest = require_frames_manifest(run_dir)  # fail loud if absent/corrupt/not-ok
    rows_by_state = {row["state"]: row for row in frames_manifest.get("rows", [])}

    states = list(request["states"])
    cell_width, cell_height = cell_geometry(request["cell"])
    cell_size = (cell_width, cell_height)

    # curation.json is an optional non-destructive sidecar. When absent, every
    # state uses all extracted frames in order with identity transform.
    curation = load_curation(run_dir)
    plans = {
        state: state_plan(curation, state, state_frame_total(request, state))
        for state in states
    }
    # 'plain' = pre-unfake twin (curator toggle off). Resolved per state
    # (per-row curator toggle > run-wide default). Fail loud when the twin is
    # missing — a plain bake must never silently fall back to pixel.
    variants = {state: frame_variant(curation, state) for state in states}
    # pixel-variant rows of a fit.pixel_unfake run re-snap curated transforms to the
    # logical grid (plain rows keep the smooth BICUBIC bake — they are not grid art).
    snap_scale = pixel_snap_scale(request)

    # 아틀라스 셀 재사용 (수홍 승인 2026-07-16): 같은 (원본 프레임, 변형, 픽셀편집)으로
    # 구워지는 인스턴스는 칸 하나를 공유한다 — 프레임 복제·루프딜레이가 텍스처를
    # 늘리지 않는다. frame_layout.rows 의 rect 목록은 인스턴스(재생) 순서 그대로이고
    # 재사용 시 같은 rect 가 반복된다 (Aseprite JSON 과 동형 패턴 — 소비자는 지금처럼
    # 프레임 인덱스 → rect 샘플링만 하면 된다).
    def _instance_key(state: str, frame_index: int, phase: float = 0.0) -> tuple:
        source_index = source_frame_index(curation, state, frame_index, state_frame_total(request, state))
        edit_idx = edit_index(curation, state, frame_index)  # 링크 복제 = 원본 편집 truth (셀 공유)
        transform_key = json.dumps(plans[state][1].get(edit_idx), sort_keys=True)
        ops_key = json.dumps(state_pixel_ops(curation, state).get(edit_idx), sort_keys=True)
        return (source_index, transform_key, ops_key, phase)

    # 호흡 후처리 레이어 (사이드카, 수홍 확정 2026-07-18 루프-맞춤): 재생 위치 =
    # (프레임, 위상), 길이 = 기존 시퀀스 그대로 (루프 불변 — fit_breathe_pattern 이
    # breaths 회를 시퀀스에 딱 떨어지게 배분). 텍스처 칸은 유니크 (프레임×위상)만.
    #
    # 위상은 연속값이지만 **반복된다**: breaths 회가 시퀀스에 딱 떨어지므로 슬롯 i 와
    # i + seq_len/breaths 는 수학적으로 같은 위상이고, `fit_breathe_pattern` 이 정수
    # 나머지를 먼저 취해 그 둘을 비트 동일한 double 로 만든다. 그래서 칸 재사용이
    # 살아 있다 — 18슬롯 3호흡이면 유니크 칸 6개다. (분자를 정수로 접지 않으면 표현
    # 노이즈로 14칸이 되어 바이트 동일한 칸이 8개 중복 구워졌다, 새미 검증 2026-07-25.)
    def _positions(state: str) -> list[tuple[int, float]]:
        ordered = plans[state][0]
        cfg = state_breathe(curation, state)
        if not cfg or not ordered:
            return [(i, 0.0) for i in ordered]
        pattern = fit_breathe_pattern(len(ordered), cfg)
        return [(ordered[i], pattern[i]) for i in range(len(ordered))]

    positions_by_state = {state: _positions(state) for state in states}
    breathe_by_state = {state: state_breathe(curation, state) for state in states}

    columns = max(
        max(1, len({_instance_key(state, i, ph) for i, ph in positions_by_state[state]}))
        for state in states
    )
    atlas = Image.new("RGBA", (columns * cell_width, len(states) * cell_height), (0, 0, 0, 0))
    frame_layout: dict[str, Any] = {
        "sheetWidth": atlas.width,
        "sheetHeight": atlas.height,
        "cellWidth": cell_width,
        "cellHeight": cell_height,
        "rows": {},
    }
    animation: dict[str, Any] = {
        "cellWidth": cell_width,
        "cellHeight": cell_height,
        "columns": columns,
        "rows": {},
    }
    errors: list[str] = []
    cells: list[dict[str, Any]] = []

    for row_index, state in enumerate(states):
        entry = request["states"][state]
        ordered, transforms = plans[state]
        positions = positions_by_state[state]
        breathe_cfg = breathe_by_state[state]
        variant = variants[state]
        frames = []
        row_source_frames: list[Image.Image] = []   # 호흡 적용 **직전** 프레임 (관측용)
        row_anatomy = None                          # 줄 전체가 공유하는 한 벌 (첫 프레임에서 확정)
        baked: dict[tuple, dict[str, Any]] = {}  # instance key -> shared rect
        for frame_index, breathe_phase in positions:
            key = _instance_key(state, frame_index, breathe_phase)
            reused = baked.get(key)
            if reused is not None:
                frames.append(reused)
                cells.append({"state": state, "frame": frame_index, "reuses": True, **reused})
                continue
            # 파일 위치의 SSoT 는 manifest row 의 files — 패턴 조립 금지 (택소노미/flat 공용).
            # 복제 인스턴스는 원본 프레임 파일을 읽는다 (변형/픽셀편집은 인스턴스 인덱스 소유).
            source_index = source_frame_index(curation, state, frame_index, state_frame_total(request, state))
            frame_path = run_dir / row_frame_rel(rows_by_state[state], source_index, variant)
            if not frame_path.is_file():
                errors.append(f"missing frame ({variant} variant): {frame_path}")
                continue
            edit_idx = edit_index(curation, state, frame_index)
            with Image.open(frame_path) as opened:
                source = apply_pixel_edits(opened.convert("RGBA"), state_pixel_ops(curation, state).get(edit_idx))
            if source.size != cell_size:
                errors.append(f"{frame_path} is {source.width}x{source.height}; expected {cell_width}x{cell_height}")
            # apply the human curation transform (identity when uncurated)
            frame = apply_transform(source, transforms.get(edit_idx), cell_size,
                                    snap_scale=snap_scale if variant == "pixel" else None)
            if breathe_cfg:
                row_source_frames.append(frame)
                if row_anatomy is None:
                    # 해부는 캐릭터 속성이라 줄 전체가 한 벌을 쓴다 — 프레임마다 다시 재면
                    # 경계가 흔들려 강체 구간이 프레임 간 같은 구간이 아니게 되고,
                    # 사이드카 한 벌로 그리는 프리뷰와도 갈린다 (슉슉이 실측 2026-07-25).
                    row_anatomy = resolve_anatomy(frame, breathe_cfg)
                # 호흡 위상은 최종 셀 픽셀 위 결정론 봉투 워프 — 팔레트·격자 불변.
                # **위상 0 도 굽는다.** 구 분할선 방식에선 위상 0 이 항등이라 건너뛰어도
                # 됐지만 봉투에선 아니다: 진행파 지연(lag) 때문에 t=0 에서도 윗행은
                # wave(-lag*u) 만큼 변형된다. 건너뛰면 그 칸만 원본이 되어 아틀라스가
                # 매 루프 시작에서 튀고 GIF 굽기와 그림이 갈린다 (새미 검증 2026-07-25).
                frame = phase_frame(frame, breathe_cfg, breathe_phase, row_anatomy)
            nontransparent = alpha_nonzero_count(frame)
            if nontransparent < args.min_used_pixels:
                errors.append(f"{state} frame {frame_index} is too sparse ({nontransparent})")
            left = len(baked) * cell_width
            top = row_index * cell_height
            atlas.alpha_composite(frame, (left, top))
            rect = {"x": left, "y": top, "w": cell_width, "h": cell_height}
            baked[key] = rect
            frames.append(rect)
            cells.append({"state": state, "frame": frame_index, "nontransparent_pixels": nontransparent, **rect})

        frame_layout["rows"][state] = frames
        # durations_ms: 프레임별 표시 시간 계약 (지금은 fps 등간격 — 프레임별 편집
        # UI 가 생기면 여기만 비등간격으로 채워진다). 소비자는 이 배열이 있으면
        # fps 대신 이것을 따른다.
        duration_ms = max(1, round(1000.0 / (float(entry.get("fps", 6)) or 6.0)))
        animation["rows"][state] = {
            "row": row_index,
            "frames": len(positions),
            "fps": int(entry.get("fps", 6)),
            # 프레임별 지속시간은 프레임 복제가 담당한다 (수홍 확정 2026-07-18 —
            # 복제는 아틀라스 셀 공유라 부하 0; per-frame duration 이중 진실 금지).
            # 전체 속도는 state fps 하나로 조정한다.
            "durations_ms": [duration_ms] * len(positions),
            "loop": bool(entry.get("loop", True)),
            "frame_variant": variant,
        }
        if breathe_cfg:
            # 자가 복구가 돌았는지, 프레임마다 경계가 달라졌는지 관측 (원칙 6).
            animation["rows"][state]["breathe"] = anatomy_report(row_source_frames, breathe_cfg) \
                if row_source_frames else None

    # 레이어 런 확장 (docs/layer-tracks.md §5): rig 를 선언한 런만 매니페스트에
    # `rig` 블록과 행별 `track` 이 붙는다. 랜드마크는 **아틀라스 절대 정수 좌표**이고
    # **재생 위치**로 색인된다 — frame_layout.rows.<state> 와 길이·순서가 같아서
    # 소비자가 rect 와 1:1 로 zip 할 수 있다 (칸 재사용으로 rect 는 반복될 수 있다).
    # 런타임이 트랙을 실시간으로 합치기 위한 입력이고, 굽힌 조합의 소비가 아니다.
    rig_block: dict[str, Any] | None = None
    if has_layer_contract(request):
        for state in states:
            animation["rows"][state]["track"] = state_track(request, state)
        rig = request.get("rig")
        if isinstance(rig, dict):
            rig_landmarks: dict[str, Any] = {}
            for state in states:
                if not ((rig.get("landmarks") or {}).get(state)):
                    continue
                entries = []
                for (instance, _phase), rect in zip(positions_by_state[state],
                                                    frame_layout["rows"][state]):
                    points = landmark_map(request, state, instance)
                    if points is None:
                        # 큐레이션 복제 인스턴스는 풀 밖 인덱스라 선언이 없다. 원본
                        # 프레임의 피벗을 빌려오면 복제 자신의 변형만큼 어긋난 좌표를
                        # 런타임에 내보내게 된다 — 추정하지 않고 실패한다.
                        errors.append(
                            f"{state}: instance {instance} declares no rig landmarks "
                            f"(a curated clone carries its own transform, so its pivots "
                            f"cannot be borrowed from the frame it copies)")
                        entries = None
                        break
                    entries.append({name: [rect["x"] + point[0], rect["y"] + point[1]]
                                    for name, point in points.items()})
                if entries is not None:
                    rig_landmarks[state] = entries
            rig_block = {"profile": rig.get("profile"), "landmarks": rig_landmarks}

    # top-level summary: uniform value when every row agrees, else 'mixed'
    # (per-row truth lives in animation.rows.<state>.frame_variant).
    unique_variants = set(variants.values())
    variant_summary = unique_variants.pop() if len(unique_variants) == 1 else "mixed"

    report = {
        "ok": not errors,
        "engine": "component-row",
        "curation_applied": curation is not None,
        "frame_variant": variant_summary,
        "errors": errors,
        "atlas": args.atlas,
        "manifest": args.manifest,
        "cell": request["cell"],
        "states": states,
        "cells": cells,
        "frame_layout": frame_layout,
    }

    report_path = run_dir / args.report
    atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if errors:
        print(json.dumps({k: v for k, v in report.items() if k != "cells"}, ensure_ascii=False, indent=2))
        return 1

    atlas_path = run_dir / args.atlas
    atomic_save_image(atlas, atlas_path)
    manifest = {
        "characterId": request["character"]["id"],
        "engine": "component-row",
        "game_input": args.atlas,
        "degraded_static_fallback": False,
        "curation_applied": curation is not None,
        "frame_variant": variant_summary,
        "sprite_sheet_alpha": args.atlas,
        "sprite_sheet_alpha_report": args.report,
        "base_image": request["character"].get("base_image"),
        "cell": request["cell"],
        "chroma_key": request["chroma_key"],
        "animation": animation,
        "frame_layout": frame_layout,
    }
    if rig_block is not None:
        manifest["rig"] = rig_block
    atomic_write_text(run_dir / args.manifest, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"ok": True, "atlas": str(atlas_path), "manifest": str(run_dir / args.manifest)}, ensure_ascii=False, indent=2))
    return 0



def run(**kwargs: object):
    return _run(_namespace_from_kwargs(**kwargs))

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
