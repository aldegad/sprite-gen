# SPDX-License-Identifier: Apache-2.0
"""방향 앵커 = 사람이 승인한 **단 한 장** — 해석·후처리 베이크·materialize 의 SSoT.

앵커는 그 방향의 identity 다 (directional-anchor-workflow.md). 그래서 앵커 재료는
raw 생성물이 아니라 **사람이 화면에서 승인한 모습**이어야 한다 — 픽셀 편집·변형·
삭제·재정렬이 반영된 프레임. 이 모듈이 그 한 장을 결정하고 굽는다:

    지정(curation.json `anchors.<direction>`)  >  그 방향 앵커 행의 시퀀스 첫 인스턴스

두 번째가 기본값이다 (명시 기본값 — 폴백이 아니다). index 0 이 아니라 **시퀀스 첫
인스턴스**인 이유: 사용자가 앞 프레임을 삭제/재정렬했으면 index 0 은 기각분이다
(실사고 2026-07-19 수홍 — side_idle 이 0·1·2 삭제 후 3부터라 index 0 베이크가
삭제된 미편집 프레임을 앵커로 만들었다).

지정은 **어느 행의 어느 인스턴스든** 될 수 있다 (수홍 2026-07-25 "내가 앵커를 정할
수 있게, 어떤 프레임을 앵커로 할지 큐레이션 뷰에서"): 같은 방향이면 walk 의 3번
프레임도, 시퀀스에 없는 후보 풀 프레임도 앵커가 될 수 있다. 사라진 인스턴스를
가리키는 지정은 fail-loud 다 — 조용히 기본값으로 되돌리면 "지정했는데 왜 안 먹지"를
사용자가 영원히 못 본다 (No Silent Fallback).

`references/anchors/<direction>-anchor-x<scale>.png` 는 **파생 캐시**다: 생성 직전마다
큐레이션 진실에서 다시 굽고 그 자리를 덮어쓴다. 정적 스냅샷을 재사용하면 사용자가
뷰에서 앵커를 더 편집한 순간 소리 없이 낡고, 이후 생성 행 전부가 옛 정체성/치수를
물려받는다 (실사고 2026-07-19 수홍 "다운앵커가 왜 내가 편집해둔 아틀라스가 아니야").
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .curation import (anchor_choices, apply_pixel_edits, apply_transform, edit_index,
                       frame_variant, load_curation, pixel_snap_scale, source_frame_index,
                       state_instances, state_pixel_ops, state_plan)
from .layout import row_frame_rel, state_frame_total

ANCHOR_SCALE = 8
CONTENT_ALPHA_FLOOR = 40  # 콘텐츠 crop 기준 (프린지 알파를 콘텐츠로 세지 않는다)


# --- 방향/앵커 상태 어휘 ------------------------------------------------------
# 방향 접두사 판정은 `directions.set` 만 본다 (layout 계약과 독립) — 택소노미
# 이전 flat 방향 런도 같은 앵커 계약을 받아야 한다. 파일 경로는 그대로 layout
# 리졸버(raw_rel/row_frame_rel)가 소유한다.

def anchor_suffix(request: dict[str, Any]) -> str:
    return str((request.get("directions") or {}).get("anchor_suffix", "idle"))


def directions(request: dict[str, Any]) -> list[str]:
    return [str(d) for d in ((request.get("directions") or {}).get("set") or [])]


def anchor_state(request: dict[str, Any], direction: str) -> str:
    """그 방향의 앵커 행 이름 (`<direction>_<anchor_suffix>`)."""
    return f"{direction}_{anchor_suffix(request)}"


def state_direction(request: dict[str, Any], state: str) -> str | None:
    """상태가 속한 생성 방향 (없으면 None — 비방향 런/미러 방향)."""
    for direction in directions(request):
        if state == direction or state.startswith(direction + "_"):
            return direction
    return None


def anchor_ref_rel(direction: str, scale: int = ANCHOR_SCALE) -> str:
    """생성에 첨부되는 앵커 ref 의 런-상대 경로 (파생 캐시)."""
    return f"references/anchors/{direction}-anchor-x{scale}.png"


def _legacy_ref_rel(direction: str, scale: int) -> str:
    """앵커가 `<dir>_idle` 시퀀스 헤드로만 정해졌던 시절의 이름. 지금은 앵커 프레임이
    다른 행에서 올 수 있어 `-idle-` 이 거짓이 된다 — materialize 가 지운다."""
    return f"references/anchors/{direction}-idle-x{scale}.png"


# --- 해석 -------------------------------------------------------------------

def resolve_anchor(request: dict[str, Any], curation: dict[str, Any] | None,
                   direction: str) -> dict[str, Any]:
    """이 방향의 앵커 인스턴스 = {"direction", "state", "index", "source"}.

    source: "picked"(사용자 지정) | "default"(앵커 행 시퀀스 첫 인스턴스).
    해석 불가는 SystemExit — 앵커 없이 방향 행을 생성하지 않는다."""
    if direction not in directions(request):
        raise SystemExit(f"anchor: '{direction}' is not a generated direction "
                         f"({', '.join(directions(request)) or 'run has no directions block'})")
    pick = anchor_choices(curation).get(direction)
    if pick is not None:
        state, index = pick["state"], pick["index"]
        if state not in request.get("states", {}):
            raise SystemExit(f"anchor: picked anchor state '{state}' is not in this run "
                             f"(direction {direction}) — re-pick the anchor frame in the curation view")
        owner = state_direction(request, state)
        if owner != direction:
            raise SystemExit(f"anchor: picked frame {state}#{index} belongs to direction "
                             f"'{owner}', not '{direction}' — an anchor owns its own facing")
        live = state_instances(curation, state, state_frame_total(request, state))
        if index not in live:
            raise SystemExit(f"anchor: picked anchor frame {state}#{index} no longer exists "
                             f"(archived, or the row was regenerated) — re-pick the anchor "
                             f"frame in the curation view")
        return {"direction": direction, "state": state, "index": index, "source": "picked"}
    state = anchor_state(request, direction)
    if state not in request.get("states", {}):
        raise SystemExit(f"anchor: direction '{direction}' has no anchor row '{state}' and no "
                         f"picked anchor frame — generate the anchor row first")
    ordered, _ = state_plan(curation, state, state_frame_total(request, state))
    if not ordered:
        raise SystemExit(f"anchor: '{state}' has an empty curated sequence — nothing to use as "
                         f"the direction anchor")
    return {"direction": direction, "state": state, "index": ordered[0], "source": "default"}


def anchor_status(run_dir: Path, request: dict[str, Any], curation: dict[str, Any] | None,
                  direction: str) -> dict[str, Any]:
    """뷰용 비-예외 상태: 해석 + 재료 파일 실재까지 확인하고 실패는 `error` 로 돌려준다.

    표시는 죽지 않아야 하지만 **이유는 보여야** 한다 (조용한 빈칸 금지). 생성 경로는
    언제나 `resolve_anchor`/`anchor_image` 의 fail-loud 를 쓴다."""
    try:
        resolved = resolve_anchor(request, curation, direction)
        path = frame_source_path(run_dir, request, curation, resolved["state"], resolved["index"])
        if not path.is_file():
            raise SystemExit(f"anchor: frame file missing for {resolved['state']}"
                             f"#{resolved['index']}: {path}")
        return {**resolved, "error": None}
    except (SystemExit, Exception) as exc:
        return {"direction": direction, "state": None, "index": None,
                "source": None, "error": str(exc)}


# --- 후처리 베이크 ------------------------------------------------------------

def frame_source_path(run_dir: Path, request: dict[str, Any], curation: dict[str, Any] | None,
                      state: str, index: int) -> Path:
    """이 인스턴스가 읽는 실제 프레임 파일 (복제 해소 + 표시/굽기 variant 반영).

    위치 SSoT 는 frames-manifest 의 `files` 다 (패턴 조립 금지 — layout.row_frame_rel)."""
    from .extract import require_frames_manifest

    manifest = require_frames_manifest(run_dir)
    row = next((r for r in manifest.get("rows", []) if r.get("state") == state), None)
    if row is None:
        raise SystemExit(f"anchor: no extracted row for {state}")
    src_index = source_frame_index(curation, state, index, state_frame_total(request, state))
    return run_dir / row_frame_rel(row, src_index, frame_variant(curation, state))


def bake_frame(run_dir: Path, request: dict[str, Any], curation: dict[str, Any] | None,
               state: str, index: int) -> "Any":
    """인스턴스 하나를 **뷰에 보이는 그대로** 셀 캔버스에 굽는다 (RGBA).

    export/compose 와 같은 프리미티브·같은 순서 (클론 해소 → 픽셀 편집 → 변형 →
    pp 격자 재양자화) — 앵커만 다른 수학으로 구우면 승인한 모습과 어긋난다."""
    from PIL import Image

    cell = request["cell"]
    cell_size = (int(cell.get("width", cell.get("size", 0))),
                 int(cell.get("height", cell.get("size", 0))))
    default_count = state_frame_total(request, state)
    _ordered, transforms = state_plan(curation, state, default_count)
    variant = frame_variant(curation, state)
    src_path = frame_source_path(run_dir, request, curation, state, index)
    if not src_path.is_file():
        raise SystemExit(f"anchor: frame file missing for {state}#{index}: {src_path}")
    edit_idx = edit_index(curation, state, index)
    with Image.open(src_path) as opened:
        return apply_transform(
            apply_pixel_edits(opened.convert("RGBA"),
                              state_pixel_ops(curation, state).get(edit_idx)),
            transforms.get(edit_idx), cell_size,
            snap_scale=pixel_snap_scale(request) if variant == "pixel" else None)


def anchor_image(run_dir: Path, request: dict[str, Any], curation: dict[str, Any] | None,
                 direction: str, scale: int = ANCHOR_SCALE) -> tuple["Any", dict[str, Any]]:
    """생성에 붙일 앵커 ref 이미지 + 해석 결과.

    콘텐츠 bbox 로 crop 한 뒤 ×scale 니어리스트 확대 — 픽셀 데이터는 그대로이고,
    작은 셀 스프라이트를 image-gen 이 읽을 수 있게 키우기만 한다."""
    from PIL import Image

    resolved = resolve_anchor(request, curation, direction)
    img = bake_frame(run_dir, request, curation, resolved["state"], resolved["index"])
    box = img.split()[3].point(lambda a: 255 if a >= CONTENT_ALPHA_FLOOR else 0).getbbox()
    if box is None:
        raise SystemExit(f"anchor: baked anchor frame {resolved['state']}#{resolved['index']} "
                         f"is empty (no visible pixels)")
    content = img.crop(box)
    upscaled = content.resize((content.width * scale, content.height * scale),
                              Image.Resampling.NEAREST)
    return upscaled, {**resolved, "content_size": [content.width, content.height]}


def materialize(run_dir: Path, direction: str, scale: int = ANCHOR_SCALE,
                request: dict[str, Any] | None = None,
                curation: dict[str, Any] | None = None, quiet: bool = False) -> Path:
    """앵커 ref 를 큐레이션 진실에서 방금 구워 파일 자리를 덮어쓴다 (self-heal 캐시)."""
    run_dir = Path(run_dir)
    if request is None:
        request = load_request(run_dir)
    if curation is None:
        curation = load_curation(run_dir)
    image, resolved = anchor_image(run_dir, request, curation, direction, scale)
    out = run_dir / anchor_ref_rel(direction, scale)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    legacy = run_dir / _legacy_ref_rel(direction, scale)
    if legacy.is_file() and legacy != out:
        legacy.unlink()
        if not quiet:
            print(f"[anchor] removed legacy snapshot {legacy.relative_to(run_dir)} "
                  f"(anchor frame is no longer pinned to the idle row)")
    if not quiet:
        print(f"[anchor] {direction}: baked {resolved['state']}#{resolved['index']} "
              f"({resolved['source']}) -> {out.relative_to(run_dir)} "
              f"({resolved['content_size'][0]}x{resolved['content_size'][1]} content)")
    return out


# --- 행 생성용 identity ref ---------------------------------------------------

def load_request(run_dir: Path) -> dict[str, Any]:
    path = Path(run_dir) / "sprite-request.json"
    if not path.is_file():
        raise SystemExit(f"not a sprite-gen run dir (no sprite-request.json): {run_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def base_source(run_dir: Path) -> Path:
    for candidate in sorted(Path(run_dir).glob("base-source.*")):
        if candidate.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return candidate
    raise SystemExit(f"no base-source image in run dir: {run_dir}")


def identity_ref(run_dir: Path, state: str, request: dict[str, Any] | None = None,
                 curation: dict[str, Any] | None = None, scale: int = ANCHOR_SCALE,
                 quiet: bool = False) -> Path:
    """이 행을 생성할 때 identity 로 붙이는 파일 (refs[0]).

    - 방향 런의 **액션 행** = 그 방향 앵커 (호출마다 큐레이션 진실에서 재베이크).
    - 방향 앵커 행 자체 / 비방향 런 = `base-source.*` (앵커 이전 체인).
    """
    run_dir = Path(run_dir)
    if request is None:
        request = load_request(run_dir)
    direction = state_direction(request, state)
    if direction is not None and state != anchor_state(request, direction):
        return materialize(run_dir, direction, scale, request=request,
                           curation=curation, quiet=quiet)
    return base_source(run_dir)


# --- 지정 쓰기 (CLI) ----------------------------------------------------------

def _write_anchor_pick(run_dir: Path, request: dict[str, Any], state: str | None,
                       index: int | None, direction: str | None = None) -> dict[str, Any]:
    """`curation.json` 의 `anchors.<direction>` 갱신 (state=None → 지정 해제).

    Isolation: 사이드카는 뷰 autosave 와 공유 자원이라 배타락(publish_guard) 안에서
    **fresh 재독 → 갱신 → 원자 쓰기** 한다 (lost update 금지)."""
    from .curation import CURATION_FILENAME, empty_curation, write_curation_atomic
    from .runio import publish_guard

    if state is not None:
        owner = state_direction(request, state)
        if owner is None:
            raise SystemExit(f"anchor: '{state}' does not belong to a generated direction — "
                             f"only direction rows can own a direction anchor")
        direction = owner
    if direction is None:
        raise SystemExit("anchor: --clear needs a direction (--direction <dir>)")
    with publish_guard(run_dir):
        path = run_dir / CURATION_FILENAME
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
        else:
            doc = empty_curation()
        anchors = {k: dict(v) for k, v in anchor_choices(doc).items()}
        if state is None:
            anchors.pop(direction, None)
        else:
            anchors[direction] = {"state": state, "index": int(index)}
        doc["anchors"] = anchors
        write_curation_atomic(run_dir, doc)
    return {"direction": direction, "state": state, "index": index}


def _parse_pick(value: str) -> tuple[str, int]:
    state, sep, raw_index = str(value).rpartition("#")
    if not sep or not raw_index.strip().lstrip("-").isdigit():
        raise SystemExit(f"anchor: --pick expects <state>#<index> (e.g. down_idle#2), got: {value!r}")
    return state, int(raw_index)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--direction", help="materialize this direction's anchor ref")
    parser.add_argument("--all", dest="all_directions", action="store_true",
                        help="materialize every generated direction's anchor ref")
    parser.add_argument("--for-state",
                        help="print the identity ref to attach when generating this state "
                             "(direction action row -> its anchor, anchor row/simple run -> base-source)")
    parser.add_argument("--pick", metavar="STATE#INDEX",
                        help="pin this instance as its direction's anchor frame (curation.json anchors)")
    parser.add_argument("--clear", action="store_true",
                        help="drop the pinned anchor frame (back to the anchor row's sequence head)")
    parser.add_argument("--scale", type=int, default=ANCHOR_SCALE,
                        help=f"nearest-neighbour upscale of the ref image (default {ANCHOR_SCALE})")


def run(run_dir: Path, direction: str | None = None, all_directions: bool = False,
        for_state: str | None = None, pick: str | None = None, clear: bool = False,
        scale: int = ANCHOR_SCALE) -> int:
    run_dir = Path(run_dir).expanduser().resolve()
    request = load_request(run_dir)
    if pick and clear:
        raise SystemExit("anchor: --pick and --clear are mutually exclusive")
    if pick:
        state, index = _parse_pick(pick)
        written = _write_anchor_pick(run_dir, request, state, index)
        print(f"[anchor] {written['direction']} anchor frame pinned: {state}#{index}")
        direction = direction or written["direction"]
    elif clear:
        written = _write_anchor_pick(run_dir, request, None, None, direction=direction)
        print(f"[anchor] {written['direction']} anchor frame unpinned "
              f"(back to the {anchor_state(request, written['direction'])} sequence head)")
        direction = direction or written["direction"]
    if for_state:
        if for_state not in request.get("states", {}):
            raise SystemExit(f"anchor: unknown state: {for_state}")
        print(identity_ref(run_dir, for_state, request=request, quiet=True)
              .relative_to(run_dir).as_posix())
        return 0
    targets = directions(request) if all_directions else ([direction] if direction else [])
    if not targets:
        raise SystemExit("anchor: pass --direction <dir>, --all, or --for-state <state>")
    for target in targets:
        materialize(run_dir, target, scale, request=request)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_arguments(parser)
    args = parser.parse_args(argv)
    return run(**vars(args))


if __name__ == "__main__":
    raise SystemExit(main())
