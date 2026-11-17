# SPDX-License-Identifier: Apache-2.0
"""Shared curation sidecar logic for sprite-gen.

`curation.json` is an optional, non-destructive sidecar in a run directory. It
records which extracted frames a human selected (and in what order), which
frames were logically deleted, plus a per-frame affine transform. The original
`frames/<state>/frame-N.png` files are never rewritten — the atlas/GIF compose
steps read this sidecar and apply the transform at compose time, so a curation
decision is always reversible by editing `curation.json`.

This module is the single source of truth for the curation schema and for how a
transform is applied, so the webview server and the compose scripts can never
drift apart.

Schema (`curation.json`):

    {
      "version": 1,
      "kind": "sprite-gen-curation",
      "run_revision": "9f3c1a0b7e2d4c58",    # stamped at write = the frame generation this
                                              #   curation was made for. When it matches the
                                              #   current run, the whole sidecar applies
                                              #   (fast path). When it does not, each state
                                              #   is judged by its own `revision` stamp
                                              #   below — never silently applied wholesale.
      "pixel_unfake": true,                  # optional run-wide DEFAULT; false -> compose
                                              #   reads the frame-N.plain.png variant (pre-
                                              #   pixel unfake). absent/true -> the
                                              #   canonical frame-N.png. Only
                                              #   meaningful when extraction saved
                                              #   both variants (fit.pixel_unfake).
      "anchors": {                           # optional DIRECTION ANCHOR FRAME PICKS
        "down": {"state": "down_idle",        #   (maintainer 2026-07-25): which curated instance
                 "index": 2,                  #   is this direction's identity truth for
                 "revision": ["a1b2c3d4e5f6"] #   generating its other rows. Any instance of any
        }                                     #   row of that direction is allowed (a pool
      },                                      #   candidate too). Absent direction -> the anchor
                                              #   row's sequence head (explicit default).
                                              #   `revision` = the PINNED ROW's state_revision at
                                              #   pin time; when the row is later regenerated the
                                              #   gate marks the pin stale (never re-stamped, never
                                              #   silently followed to a new frame) and generation
                                              #   fails loud until it is re-picked.
                                              #   Resolution/bake SSoT = sprite_gen/curate/anchor.py.
      "recolor": {                           # optional ADOPTED COLOURWAY: which baked
        "picked": "crimson"                  #   recolor variant (sprite_gen/effects/recolor.py,
      },                                     #   `<run>/variants/`) the human picked in the
                                             #   curation view. Keyed by variant NAME — a
                                             #   colourway survives re-baking, so unlike
                                             #   `anchors` this carries no generation stamp.
                                             #   A name no longer in the bake report is
                                             #   reported as unknown by the view, never
                                             #   silently cleared. Reader = recolor_pick().
      "states": {
        "<state>": {
          "revision": ["a1b2c3d4e5f6"],      # per-state generation stamp: ordered SOURCE-
                                              #   material segment digests (state_revision).
                                              #   Valid while it is a prefix of the current
                                              #   segments — an engine-upgrade heal of the
                                              #   same raw keeps it, a raw re-roll drops it.
          "pixel_unfake": false,             # optional per-state override of the run-wide
                                              #   default above (the curator's per-row
                                              #   toggle). absent -> the run-wide value.
          "selected": [0, 1, 2, 3],          # 0-based frame indices, in play order
                                              #   (may include clone instance indices)
          "deleted": [4],                    # optional 0-based frame indices
                                               #   excluded from UI rows and bake.
          "clones": {"12": 5},               # optional duplicate instances: new index ->
                                               #   source frame index. LINKED by default
                                               #   (maintainer 2026-07-18): a linked clone
                                               #   shares the SOURCE frame's transform and
                                               #   pixel edits (one edit truth — clones are
                                               #   play slots of the same frame). Clone
                                               #   indices live outside 0..frame_count-1.
          "unlinked": [12],                    # optional clone indices explicitly detached
                                               #   ("링크 끊기"): they own their transforms/
                                               #   pixels independently (pre-2026-07-18
                                               #   clones with own edits are treated as
                                               #   unlinked by the webview on load).
          "order": [0, 1, 2, 3, 4, 5],        # optional, webview-owned; full display
                                               #   order (sequence then candidate pool).
                                               #   Restores the row arrangement on reload.
                                               #   Consumers key off `selected`; ignored here.
          "breathe": {                         # optional idle-breathing POST-PROCESS LAYER
            "depth": 0.06,                     #   (maintainer 2026-07-18: breathing is a
            "breaths": 1,                      #   modulation ORTHOGONAL to frame selection —
            "lag": 0.10,                       #   a blink frame can breathe too).
            "rigid_row": null,                 #   depth = total stretch as a fraction of the
            "anatomy": {                       #   body height. breaths = breath count PER
              "rigid_row": 58,                 #   LOOP; the loop length never changes.
              "neck_row": 49,                  #   lag = travelling-wave phase delay, which is
              "neck_source": "bottleneck",     #   what makes the head follow a beat late.
              "face": [45, 57],                #   rigid_row = manual override of the rigid
              "basis_row": 49,                 #   boundary (null = auto-detected).
              "axis_x": 35,                    #
              "torso_half": 21,                #   `anatomy` is the detection result frozen at
              "max_half": 28,                  #   curation time so the bake and the webview
              "width": 72, "height": 81,       #   read the same numbers instead of both
              "rigid_source": "face",          #   re-implementing detection. `fingerprint`
              "warnings": [],                  #   pins the source it was derived from; on a
              "fingerprint": "72x81:..."       #   mismatch the bake re-detects and refreshes
            }                                  #   it (self-heal). A manual rigid_row survives
          },                                   #   regardless — that is intent, not a cache.
                                               #
                                               #   `splits`/`amplitude`/`subpixel` are the
                                               #   retired split-line schema and are REJECTED
                                               #   loudly, never reinterpreted (2026-07-25).
                                               #   Compose/GIF bake deterministically; frames
                                               #   on disk never change, no re-extraction.
          "transforms": {                      # keyed by 0-based frame index (string)
            "0": {"rotate": 0.0, "scale": 1.0, "dx": 0, "dy": 0}
          },
          "pixels": {                          # optional per-frame pixel edits (sidecar,
            "0": {"12,34": "#1f2430",          #   originals never rewritten): cell-coord
                  "13,34": null}                #   "x,y" -> paint hex | null = erase.
          }                                     #   Applied before the transform at bake.
        }
      }
    }

Defaults when absent (explicit, not a silent fallback):
- no `curation.json`           -> every state uses all extracted frames in order, identity transform.
- mismatched `run_revision`    -> per-state salvage: each state entry whose `revision` stamp is a
                                   prefix of the current state_revision segments is KEPT; entries
                                   without a valid stamp are dropped. The drop is reported on stderr
                                   and to the webview (load_curation_report) — stale edits are never
                                   silently applied. The load itself writes nothing: `curation.json`
                                   stays on disk untouched, and the copy to
                                   `curation.stale-<hash>.json` (idempotent content-hash name) is
                                   made by the writer that actually overwrites it
                                   (`write_curation_atomic`) — never silently destroyed either.
- `anchors` missing/direction absent -> that direction's anchor is the anchor row's curated
                                   sequence head (`sprite_gen.curate.anchor.resolve_anchor`).
- pinned row regenerated       -> the pin is KEPT and marked `stale` by the load gate (reported in
                                   `report["anchors_stale"]`), so resolution fails loud
                                   (`pick-stale-generation`) instead of silently pointing the pin at
                                   a brand-new frame the human never saw, and instead of silently
                                   reverting to the sequence head. Re-pick to clear it.
- state missing from sidecar   -> same all-frames default for that state.
- `selected` missing/empty     -> all non-deleted frames in extraction order.
- `deleted` missing             -> no frames are deleted.
- `order` missing               -> webview rebuilds arrangement from `selected`; bake is unaffected (state_plan reads `selected`, never `order`).
- frame missing from transforms -> identity transform.

`rotate` is in degrees, counter-clockwise positive (PIL convention).
`scale` is a multiplier about the frame center.
`dx`/`dy` are pixel offsets inside the cell, +x right, +y down.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.spec.layout import raw_rel
from sprite_gen.spec.runio import REQUEST_FILENAME, load_request
from sprite_gen.util.resample import transform_cell

CURATION_FILENAME = "curation.json"
CURATION_KIND = "sprite-gen-curation"  # 큐레이터 persistence.js 의 저장 payload 도 이 값이다
SCHEMA_VERSION = 1


class Sidecar:
    """The keys of `curation.json` (schema above), declared once. The curator SPA writes the
    sidecar (`curator/src/persistence.js` `buildPayload`) and reads it back (`store.js`
    `seedEntries`, `boot.js`); this module, the curation server and the bake read it through
    these names. tests/curate/test_curation_sidecar_keys.py holds the SPA to them."""

    # the document
    VERSION = "version"
    KIND = "kind"
    RUN_REVISION = "run_revision"
    PIXEL_UNFAKE = "pixel_unfake"  # the run-wide default, and a state's own
    ANCHORS = "anchors"
    RECOLOR = "recolor"
    STATES = "states"
    # states.<state>
    SELECTED = "selected"
    ORDER = "order"
    DELETED = "deleted"
    CLONES = "clones"
    UNLINKED = "unlinked"
    NAMES = "names"
    TRANSFORMS = "transforms"
    PIXELS = "pixels"
    BREATHE = "breathe"
    FROZEN = "frozen"
    REVISION = "revision"  # a state's, and an anchor pick's
    # states.<state>.transforms.<index>
    ROTATE = "rotate"
    SCALE = "scale"
    DX = "dx"
    DY = "dy"
    SHX = "shx"
    SHY = "shy"
    FLIP_X = "flipX"
    # anchors.<direction>
    STATE = "state"
    INDEX = "index"
    STALE = "stale"
    STALE_REASON = "stale_reason"
    REPIN = "repin"
    # recolor
    PICKED = "picked"


IDENTITY = {Sidecar.ROTATE: 0.0, Sidecar.SCALE: 1.0, Sidecar.DX: 0, Sidecar.DY: 0,
            Sidecar.SHX: 0.0, Sidecar.SHY: 0.0, Sidecar.FLIP_X: 0}

# Generation-material chip roles for imported `_refs/<role>-<name>.png` files
# (run-contract.md §4). Single source shared by the importer (fail-loud on an
# unknown role) and the webview server (render), so neither silently invents a role.
REF_ANCHOR, REF_BASIS, REF_GUIDE = IMPORTED_REF_ROLES = ("anchor", "basis", "guide")

# 은퇴한 사이드카 키 -> 현행 키. `fit` 쪽과 같은 어휘 교체다 (runio.LEGACY_FIT_KEYS 참조):
# "pixel perfect" 는 광의 통용어의 오용이고, 이 파이프라인이 하는 일의 정확한 명칭은
# "unfake"(격자 스냅/재양자화로 가짜 픽셀아트를 되돌린다) 다 — maintainer 2026-07-25.
LEGACY_CURATION_KEYS = {"pixel_perfect": Sidecar.PIXEL_UNFAKE}


def _migrate_curation_keys(doc: dict[str, Any], where: str) -> bool:
    """은퇴 키를 현행 키로 옮긴다 (최상위 + 행별, in-place). 옮겼으면 True.

    두 키가 동시에 있으면 hard fail — 어느 쪽이 진실인지 코드가 고를 수 없다."""
    moved = False
    scopes = [("", doc)] + [(f"states.{name}.", entry)
                            for name, entry in (doc.get(Sidecar.STATES) or {}).items()
                            if isinstance(entry, dict)]
    for prefix, scope in scopes:
        for legacy, current in LEGACY_CURATION_KEYS.items():
            if legacy not in scope:
                continue
            if current in scope:
                raise SystemExit(
                    f"{where}: both `{prefix}{legacy}` (retired) and `{prefix}{current}` are "
                    f"present — two truths for one setting. Delete the `{prefix}{legacy}` line.")
            scope[current] = scope.pop(legacy)
            moved = True
    return moved


def imported_ref_role(filename: str) -> str | None:
    """Role for an imported `_refs` filename `<role>-<name>.png`, or None if the
    prefix is not a known role. Callers must handle None explicitly (the importer
    fails loud, the server skips) — never silently relabel an unknown role."""
    stem = Path(filename).stem
    prefix = stem.split("-", 1)[0] if "-" in stem else ""
    return prefix if prefix in IMPORTED_REF_ROLES else None


def curation_path(run_dir: Path) -> Path:
    return run_dir / CURATION_FILENAME


# 폐기된 분할선 스키마 키. 조용히 무시하거나 재해석하지 않고 요란하게 거부한다 —
# 제거된 결정이 필드 하나로 되살아나는 경로를 만들지 않기 위해서다 (docs/pixel-unfake.md
# 의 `conform` 사고, 2026-07-17). splits 없이는 구 정규화가 None 을 냈으므로 구 설정은
# 전부 이 게이트에 걸린다 — 조용히 다른 동작으로 바뀌는 사이드카는 없다.
# 호흡 파라미터 허용 범위 — 큐레이터 컨트롤(`breathe.js` BREATHE_*_MAX)과 같은 값이어야
# 한다. 둘이 갈리면 UI 가 만들 수 있는 값을 굽기가 거부한다.
BREATHE_DEPTH_MAX = 0.20
BREATHE_BREATHS_MAX = 8
BREATHE_LAG_MAX = 0.45
# 굽기 방식. pixel(기본, `mode` 생략)은 정수 행·열 사상이라 도트 격자를 지키고, smooth 는
# 큰 일러·영상 프레임용 — 머리를 자리·바이트 그대로 두고 가슴에서 둥글게 숨쉬게 한다
# (`sprite_gen.effects.breathe`). smooth 의 기본 진폭은 일러에 맞게 작다 — 가슴 가운데가 목 아래
# 몸통 높이의 0.85 % 오른다 (흉상 일러에서 화면 높이 약 0.5 %, 화면 크기로 봐도 숨이 세지 않다).
BREATHE_PIXEL, BREATHE_SMOOTH = BREATHE_MODES = ("pixel", "smooth")
BREATHE_SMOOTH_DEPTH = 0.0085
# smooth 의 가슴 부풂 — 가슴 가운데에서 이 비율만큼 넓어지고, 바깥으로 갈수록 무게만큼 준다.
BREATHE_SMOOTH_DEPTH_X = 0.028
# smooth 의 모양 (기본값과 허용 범위). chest_y: 가슴 가운데 높이 (목 아래 몸통 비율),
# chest_rx: 가슴 무게 가로 반지름 (몸통 반폭의 배), chest_ry: 세로 반지름 (목 아래 몸통 비율),
# shoulder: 어깨가 오르는 양 (가슴의 배), shoulder_lag: 어깨가 늦는 정도 (한 호흡 비율, 1/π 미만이라
# 시간이 되감기지 않는다). 몸이 향한 쪽 — chest_tilt: 가슴 무게를 화면에서 시계방향으로 돌린 각(도),
# chest_bias: 무게를 긴 축 따라 한쪽으로 키움 (음수 = 화면 왼쪽이 크게), chest_dir: 가슴이 가는 방향
# (바로 위에서 잰 각(도), 음수 = 왼쪽 위). 셋 다 0 이면 몸이 정면을 본다. chest_bias 는 무게의 꼭대기를
# 가슴 타원의 긴 축 따라(타원 가로 반지름 비율), chest_bias_y 는 그 축을 가로질러(세로 반지름 비율, 양수 =
# 아래) 옮긴다 — 무게는 꼭대기에서 테두리까지 반직선으로 줄어 가까운 쪽이 짧고 돌아간 쪽이 길다.
# chest_x: 가슴 중심을 몸통 축에서 옮김 (몸통 반폭 비율 — 긴 머리처럼 한쪽으로 쏠린 것이 축을 끌 때),
# chest_flat: 꼭대기 둘레에서 전량으로 오르는 몫 (꼭대기에서 테두리까지의 비율).
# inhale: 한 호흡에서 들숨이 차지하는 몫 — 들숨 꼭대기가 이 위상에 오고 나머지가 날숨이다 (기본은 들숨이 짧다).
# 바깥에서 만든 숨의 기록(breathe.json)도 이 값으로 제 곡선을 말한다.
BREATHE_SMOOTH_SHAPE = {"chest_y": 0.31, "chest_rx": 0.83, "chest_ry": 0.28, "shoulder": 0.5, "shoulder_lag": 0.08,
                        "chest_tilt": 0.0, "chest_bias": 0.0, "chest_dir": 0.0, "chest_x": 0.0, "chest_bias_y": 0.0,
                        "chest_flat": 0.0, "inhale": 0.40}
BREATHE_SMOOTH_SHAPE_RANGES = {"chest_y": (0.05, 0.95), "chest_rx": (0.2, 3.0), "chest_ry": (0.05, 1.5),
                               "shoulder": (0.0, 1.5), "shoulder_lag": (0.0, 0.3), "chest_tilt": (-60.0, 60.0),
                               "chest_bias": (-0.9, 0.9), "chest_dir": (-90.0, 90.0), "chest_x": (-1.0, 1.0),
                               "chest_bias_y": (-0.9, 0.9), "chest_flat": (0.0, 0.9), "inhale": (0.2, 0.8)}
# 꼭대기는 타원 안쪽이어야 한다 — 테두리에 닿으면 그쪽으로 무게가 줄어들 거리가 없다.
BREATHE_SMOOTH_PEAK_REACH = 0.9
# 루프의 한 토막 (smooth): 줄이 `loop_cells` 칸 루프의 숨을 `start_cell` 칸째부터 이어 받는다 — 루프 세트의 들어감·나옴
# 조각이 idle 의 숨을 위상 그대로 잇는다. 칸 i 의 위상은 루프 칸 (start_cell + i) mod loop_cells 의 위상이고 숨 박자
# (breaths·breath_peaks)는 그 루프의 것이다. 생략은 줄이 곧 루프다 (start_cell 0, loop_cells 는 줄의 칸 수).
BREATHE_STRETCH_KEYS = ("start_cell", "loop_cells")
# pixel 의 둥근 가슴과 양팔 가로 (`sprite_gen.effects.breathe` round 장): chest_round 는 들숨 꼭대기에서 가슴 테두리가
# 꼭대기 P 에서 바깥으로 나가는 양, arms_x 는 팔(해부가 몸통 밖 부속으로 읽은 열)이 옆으로 밀려 나가는 양 — 둘 다 목 아래
# 몸통 높이(정규화 기준) 비율이다. 둘 중 하나가 0 보다 크면 그 pixel 줄은 봉투 스쿼시 대신 이 장을 굽는다 (생략·0 은 꺼짐,
# 이전과 바이트 같다). 이 줄은 smooth 의 가슴 타원 키와 숨 박자 키를 읽는다 — 진행파(lag)와 봉투 진폭(depth·depth_x)은
# 쓰지 않는다.
BREATHE_ROUND_KEYS = ("chest_round", "arms_x")
BREATHE_ROUND_MAX = 0.20
# 가로·세로 최소·최대 (pixel·smooth): 세기(`depth`·`depth_x`) 대신 숨 곡선의 바닥과 꼭대기를 원래 크기 대비 몫으로 바로
# 적는다 — 최소 0 이면 숨의 그쪽 절반이 원래 크기에 머문다. 가로는 몸통이 가장 크게 숨쉬는 자리에서 넓어지는 몫(pixel: 그
# 행의 몸통 열, smooth: 가슴 꼭대기의 부풂), 세로는 목 아래 몸통 높이(정규화 기준) 대비 오르는 몫(pixel: 머리 윗선, smooth:
# 가슴)이다. 음수가 원래보다 좁거나 낮은 쪽이다. 곡선 모양과 박자(breaths·lag·inhale)는 세기 줄과 같고, 넓어질 때 낮아지는
# 짝(pixel)과 부풀 때 오르는 짝(smooth)도 그대로다. 적지 않은 경계는 0(원래 크기)이고, 세기와 같이 쓰면 거부한다.
BREATHE_RANGE_KEYS = ("width_min", "width_max", "height_min", "height_max")
BREATHE_RANGE_MAX = 0.20
BREATHE_ROUND_SHAPE = ("chest_y", "chest_rx", "chest_ry", "chest_tilt", "chest_bias", "chest_x", "chest_bias_y", "inhale")

RETIRED_BREATHE_KEYS = {
    "splits": "분할선은 봉투 경계로 대체됐다 — 경계는 자동 검출되고 `rigid_row` 로만 덮어쓴다",
    "amplitude": "정수 px 진폭은 몸통 높이 비율 `depth` 로 대체됐다 (기본 0.06)",
    "subpixel": "서브픽셀 중간색은 2상태 토글을 보정하려던 것이라 연속 위상에서 의미가 없다",
    "hold": "구 분할선 스키마의 유지 프레임 수 — 위상이 연속이라 유지 개념이 없다",
}


def _exact_int(name: str, value: Any, state: str) -> int:
    """정수만 받는다 — 2.7 이나 "3.5" 를 조용히 깎지 않는다.

    파이썬 `int()` 는 2.7 을 2 로 깎고 "3.5" 에는 예외를 던지는데, 미러의 `Math.round`
    는 3 과 4 를 낸다. 어느 쪽으로 맞추든 조용히 깎는 순간 프리뷰와 굽기가 갈리므로
    **양쪽 다 거부**하는 쪽으로 정했다 (범위 밖 loud reject 와 같은 판단)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SystemExit(f"curation: states.{state}.breathe.{name} 을 숫자로 못 읽는다: {value!r}")
    if number != int(number):
        raise SystemExit(
            f"curation: states.{state}.breathe.{name} = {value!r} 가 정수가 아니다. "
            f"조용히 깎지 않는다 — 프리뷰는 반올림하고 굽기는 버려서 서로 다른 애니메이션이 된다.")
    return int(number)


def state_breathe(curation: dict[str, Any] | None, state: str) -> dict[str, Any] | None:
    """상태의 호흡 후처리 레이어 설정 (없으면 None) — 정규화·클램프 포함.

    호흡은 프레임 선택(깜빡임 등)과 직교하는 변조 레이어다 (maintainer 확정 2026-07-18).
    변형 수학은 봉투 기반 스쿼시&스트레치다 (2026-07-25 교체, `sprite_gen.effects.breathe`).
    반환: {"depth": float, "depth_x": float|None, "breaths": int, "lag": float,
           "rigid_row": int|None, "anatomy": dict|None}
    depth_x: 가로 독립 진폭 — None = depth 따름(레거시), 0 = 가로 항등.
    compose/GIF 가 재생 시퀀스 위에 결정론으로 굽는다 — 디스크 프레임 불변.

    `mode` 를 적은 사이드카만 반환에 `mode` 가 실린다 — 안 적은 사이드카(지금까지의 전부)는
    정규화 결과도 굽기도 이전과 바이트 같다. smooth 는 `lag` 가 없고(진행파는 첫 칸을 움직인다),
    `depth` 생략은 `BREATHE_SMOOTH_DEPTH`, `depth_x` 생략은 `BREATHE_SMOOTH_DEPTH_X` 이며, 모양 값
    (`BREATHE_SMOOTH_SHAPE` 의 키, 들숨 몫 `inhale` 포함)이 기본값과 함께 실린다. 루프의 한 토막(`start_cell`,
    `loop_cells` — `BREATHE_STRETCH_KEYS`)도 smooth 에 늘 실린다 (생략이면 0 과 None, 줄이 곧 루프). 모양 값과 토막은
    smooth 만 쓴다 — pixel 줄에 있으면 조용히 무시하지 않고 거부한다.

    예외는 둥근 가슴 pixel 줄이다 (`chest_round`·`arms_x` 중 하나가 0 보다 큼, `BREATHE_ROUND_KEYS`): 그 줄은 가슴 타원 키
    (`BREATHE_ROUND_SHAPE`, 들숨 몫 포함)·숨 꼭대기·토막을 smooth 와 같은 검사로 읽고, 봉투 값(`depth`·`depth_x`·`lag`)과
    smooth 만 쓰는 모양 값(어깨·chest_dir·chest_flat)은 거부한다. 반환은 {"chest_round", "arms_x", "breaths", "breath_peaks",
    가슴 타원 키, "start_cell", "loop_cells", "rigid_row", "axis_x", "torso_half", "anatomy"} 다.

    최소·최대 키(`BREATHE_RANGE_KEYS`)를 하나라도 적은 pixel·smooth 줄은 반환에 `depth`·`depth_x` 대신 네 키가 다 실린다
    (적지 않은 경계는 0). 세기만 적은 줄은 반환이 이전과 같다."""
    if not curation:
        return None
    entry = curation.get(Sidecar.STATES, {}).get(state)
    raw = entry.get(Sidecar.BREATHE) if isinstance(entry, dict) else None
    if not isinstance(raw, dict):
        return None
    retired = sorted(k for k in RETIRED_BREATHE_KEYS if k in raw)
    if retired:
        detail = "\n".join(f"  - {k}: {RETIRED_BREATHE_KEYS[k]}" for k in retired)
        raise SystemExit(
            f"curation: states.{state}.breathe 에 폐기된 분할선 스키마 키가 있다: {', '.join(retired)}\n"
            f"{detail}\n"
            f"  마이그레이션: `sprite-gen migrate-breathe <run-dir> --apply` 또는 해당 키를 지우고 "
            f"`depth`(기본 0.06)·`breaths`·`lag`(기본 0.10) 로 다시 적어라. 조용히 변환하지 않는다.")
    mode = raw.get("mode")
    if mode is not None and (not isinstance(mode, str) or mode not in BREATHE_MODES):
        raise SystemExit(
            f"curation: states.{state}.breathe.mode = {mode!r} 를 모른다 — "
            f"{' | '.join(BREATHE_MODES)} 중 하나다 (생략하면 pixel). 조용히 pixel 로 굽지 않는다.")
    smooth = mode == BREATHE_SMOOTH
    # pixel 의 둥근 가슴·양팔 가로 — 생략(None)·0 은 꺼짐. 하나라도 0 보다 크면 그 줄은 round 장을 굽는다.
    amps = {}
    for name in BREATHE_ROUND_KEYS:
        if raw.get(name) is None:
            continue
        if smooth:
            raise SystemExit(
                f"curation: states.{state}.breathe.{name} 는 pixel 의 둥근 가슴 값이다 — smooth 의 가슴은 depth·depth_x 로 "
                f"오르고 부푼다. 그 키를 지우거나 mode 를 지워라. 조용히 무시하지 않는다.")
        try:
            amps[name] = float(raw[name])
        except (TypeError, ValueError):
            raise SystemExit(f"curation: states.{state}.breathe.{name} 를 숫자로 못 읽는다: {raw[name]!r}")
        if not 0.0 <= amps[name] <= BREATHE_ROUND_MAX:
            raise SystemExit(
                f"curation: states.{state}.breathe.{name} = {raw[name]!r} 가 범위 [0.0, {BREATHE_ROUND_MAX}] 밖이다. "
                f"조용히 깎지 않는다 — 사이드카를 고쳐라.")
    rounded = any(amps.values())
    if rounded:
        envelope = sorted(k for k in ("depth", "depth_x", "lag", *BREATHE_RANGE_KEYS) if raw.get(k) is not None)
        if envelope:
            raise SystemExit(
                f"curation: states.{state}.breathe 의 {', '.join(envelope)} 는 봉투 스쿼시의 값이다 — 둥근 가슴 줄"
                f"(chest_round·arms_x)은 그 진폭으로 숨쉬고 진행파가 없다. 그 키를 지워라. 조용히 무시하지 않는다.")
        stray = sorted(k for k in BREATHE_SMOOTH_SHAPE if k in raw and k not in BREATHE_ROUND_SHAPE)
        if stray:
            raise SystemExit(
                f"curation: states.{state}.breathe 의 {', '.join(stray)} 는 smooth 만 쓰는 모양 값이다 — 둥근 가슴 줄은 "
                f"가슴 타원({', '.join(BREATHE_ROUND_SHAPE)})과 숨 박자만 읽는다. 그 키를 지워라. 조용히 무시하지 않는다.")
    stray = sorted(k for k in (*BREATHE_SMOOTH_SHAPE, "breath_peaks", *BREATHE_STRETCH_KEYS) if k in raw)
    if stray and not (smooth or rounded):
        raise SystemExit(
            f"curation: states.{state}.breathe 의 {', '.join(stray)} 는 smooth 의 모양 값이다 — "
            f"pixel 은 쓰지 않는다. `mode: \"smooth\"` 를 적거나 그 키를 지워라. 조용히 무시하지 않는다.")
    # 형변환 계약(미러와 동일해야 한다): depth/lag 는 실수(숫자 문자열 허용),
    # breaths/rigid_row 는 **정수여야 한다**. 2.7 을 조용히 2 로 깎으면 프리뷰(반올림 3)와
    # 갈리고, "요청 그대로 적용된다" 는 문서도 거짓이 된다 (validator 실측 2026-07-25).
    try:
        depth = float(raw.get("depth", BREATHE_SMOOTH_DEPTH if smooth else 0.06))
        # 가로 독립 진폭 (2026-07-30 가로/세로 분리): None = depth 따름(레거시 동일),
        # 0 = 가로 사상 항등. depth 와 달리 0 이 유효하다 — "가로만 끄기" 가 정당한 상태다.
        raw_dx = raw.get("depth_x")
        depth_x = None if raw_dx is None else float(raw_dx)
        lag = float(raw.get("lag", 0.0 if smooth else 0.10))
        peaks = raw.get("breath_peaks") if smooth or rounded else None
        breaths = _exact_int("breaths", raw.get("breaths", len(peaks) if isinstance(peaks, list) else 1), state)
    except (TypeError, ValueError) as exc:
        # 폐기 키는 요란하게 거부하면서 형식 오류만 조용히 호흡을 꺼버리면 계약이 어긋난다
        # — 둘 다 "이 사이드카는 그대로 못 쓴다" 이고, 조용한 쪽은 사용자가 못 알아챈다.
        raise SystemExit(
            f"curation: states.{state}.breathe 의 depth/breaths/lag 를 숫자로 못 읽는다: {exc}\n"
            f"  받은 값: depth={raw.get('depth')!r} breaths={raw.get('breaths')!r} lag={raw.get('lag')!r}")
    # 범위 밖은 **조용히 깎지 않는다.** 클램프는 파이썬에만 있어서 미러·배지·문서가 굽기와
    # 다른 값을 말하게 됐다 (validator 실측 2026-07-25: breaths 12 를 8 로 깎는데 프리뷰·
    # 필름스트립·WebM 은 12회 숨쉬고 배지는 "적용 12회" 라고 띄웠다). 폐기 키는 요란하게
    # 거부하면서 값 범위만 조용한 것도 계약이 어긋난다.
    ranged = [("depth", depth, 0.005, BREATHE_DEPTH_MAX),
              ("breaths", breaths, 1, BREATHE_BREATHS_MAX),
              ("lag", lag, 0.0, BREATHE_LAG_MAX)]
    if depth_x is not None:
        ranged.append(("depth_x", depth_x, 0.0, BREATHE_DEPTH_MAX))
    for name, value, lo, hi in ranged:
        if not lo <= value <= hi:
            raise SystemExit(
                f"curation: states.{state}.breathe.{name} = {value!r} 가 범위 [{lo}, {hi}] 밖이다. "
                f"조용히 깎지 않는다 — 사이드카를 고쳐라 (큐레이터 컨트롤은 이 범위 안에서만 값을 낸다).")
    bounds = _breathe_bounds(raw, state)
    rigid_row = raw.get("rigid_row")
    if rigid_row is not None:
        rigid_row = _exact_int("rigid_row", rigid_row, state)
    # 영역 조정 오버라이드 (2026-07-30, 큐레이터 영역 UI): rigid_row 와 같은 지위의
    # 사람 의도 입력 — anatomy 는 파생 캐시일 뿐이다. 범위 검증은 프레임을 아는
    # anatomy.analyze 가 한다 (여기서는 정수성만).
    axis_x = raw.get("axis_x")
    if axis_x is not None:
        axis_x = _exact_int("axis_x", axis_x, state)
    torso_half = raw.get("torso_half")
    if torso_half is not None:
        torso_half = _exact_int("torso_half", torso_half, state)
    frozen = raw.get("anatomy")
    if smooth or rounded:
        # 진행파(lag)는 윗행의 위상을 늦춰 위상 0 인 칸도 움직인다 — smooth 의 첫 칸은 원본
        # 그대로여야 루프와 묶음의 이음이 안 바뀐다. 0 아닌 lag 를 조용히 버리지 않는다 (둥근 가슴 줄은 위에서 거부했다).
        if smooth and lag != 0.0:
            raise SystemExit(
                f"curation: states.{state}.breathe.lag = {raw.get('lag')!r} — smooth 에는 진행파가 없다. "
                f"위상 0 인 칸이 원본 그대로여야 루프·묶음 이음이 안 바뀐다. lag 를 지워라.")
        if peaks is not None:
            # 들숨 꼭대기 칸 — 숨 박자를 바깥에서 받는다. 첫 칸은 쉼이어야 하므로 1 부터, 꼭대기 사이는
            # 날숨과 다음 들숨이 나눠 쓸 두 칸 이상. 루프 길이는 굽기가 알므로 끝은 거기서 본다.
            if not isinstance(peaks, list) or not peaks:
                raise SystemExit(f"curation: states.{state}.breathe.breath_peaks = {peaks!r} — 들숨 꼭대기 칸 목록이어야 한다")
            peaks = [_exact_int("breath_peaks", p, state) for p in peaks]
            if peaks[0] < 1 or any(b - a < 2 for a, b in zip(peaks, peaks[1:])):
                raise SystemExit(
                    f"curation: states.{state}.breathe.breath_peaks = {peaks} — 1 부터, 커지는 순서로, 서로 두 칸 이상 "
                    f"떨어져야 한다 (첫 칸은 쉼이고, 꼭대기 사이에 날숨과 들숨이 들어간다).")
            if "breaths" in raw and breaths != len(peaks):
                raise SystemExit(
                    f"curation: states.{state}.breathe.breaths = {breaths} 인데 breath_peaks 는 {len(peaks)}개다 — "
                    f"숨 횟수는 꼭대기 수다. breaths 를 지우거나 맞춰라.")
        # 루프의 한 토막 — 칸 수는 정수다. loop_cells 생략이면 줄의 칸 수라 start_cell 의 끝은 굽기가 본다.
        start_cell = _exact_int("start_cell", raw.get("start_cell", 0), state)
        loop_cells = raw.get("loop_cells")
        if loop_cells is not None:
            loop_cells = _exact_int("loop_cells", loop_cells, state)
        if loop_cells is not None and loop_cells < 2:
            raise SystemExit(f"curation: states.{state}.breathe.loop_cells = {loop_cells} — 숨이 놓일 루프는 두 칸 이상이다.")
        if start_cell < 0 or (loop_cells is not None and start_cell >= loop_cells):
            bound = "0 이상" if loop_cells is None else f"0 이상, 루프 칸 수 loop_cells {loop_cells} 미만"
            raise SystemExit(
                f"curation: states.{state}.breathe.start_cell = {start_cell} — 줄이 루프의 몇째 칸에서 시작하나다: {bound}"
                f"이어야 한다. 조용히 접지 않는다.")
        shape = {}
        for name, default in BREATHE_SMOOTH_SHAPE.items():
            if rounded and name not in BREATHE_ROUND_SHAPE:
                continue
            given = raw.get(name, default)
            try:
                value = float(given)
            except (TypeError, ValueError):
                raise SystemExit(f"curation: states.{state}.breathe.{name} 를 숫자로 못 읽는다: {given!r}")
            lo, hi = BREATHE_SMOOTH_SHAPE_RANGES[name]
            if not lo <= value <= hi:
                raise SystemExit(
                    f"curation: states.{state}.breathe.{name} = {given!r} 가 범위 [{lo}, {hi}] 밖이다. "
                    f"조용히 깎지 않는다 — 사이드카를 고쳐라.")
            shape[name] = value
        if math.hypot(shape["chest_bias"], shape["chest_bias_y"]) > BREATHE_SMOOTH_PEAK_REACH:
            raise SystemExit(
                f"curation: states.{state}.breathe.chest_bias {shape['chest_bias']:g}, chest_bias_y "
                f"{shape['chest_bias_y']:g} — 꼭대기가 가슴 타원 테두리에 너무 가깝다 (반지름 비율 "
                f"{BREATHE_SMOOTH_PEAK_REACH:g} 안이어야 한다).")
        if rounded:
            cfg = {"chest_round": amps.get("chest_round", 0.0), "arms_x": amps.get("arms_x", 0.0), "breaths": breaths,
                   "breath_peaks": peaks, **shape, "start_cell": start_cell, "loop_cells": loop_cells,
                   "rigid_row": rigid_row, "axis_x": axis_x, "torso_half": torso_half,
                   "anatomy": frozen if isinstance(frozen, dict) else None}
            return cfg if mode is None else {"mode": mode, **cfg}
        amplitude = bounds or {"depth": depth, "depth_x": BREATHE_SMOOTH_DEPTH_X if depth_x is None else depth_x}
        return {"mode": mode, **amplitude,
                "breaths": breaths, "breath_peaks": peaks, **shape, "start_cell": start_cell, "loop_cells": loop_cells,
                "rigid_row": rigid_row, "axis_x": axis_x,
                "torso_half": torso_half, "anatomy": frozen if isinstance(frozen, dict) else None}
    cfg = {**(bounds or {"depth": depth, "depth_x": depth_x}), "breaths": breaths, "lag": lag,
           "rigid_row": rigid_row, "axis_x": axis_x, "torso_half": torso_half,
           "anatomy": frozen if isinstance(frozen, dict) else None}
    return cfg if mode is None else {"mode": mode, **cfg}


def _breathe_bounds(raw: dict[str, Any], state: str) -> dict[str, float] | None:
    """줄의 가로·세로 최소·최대 (`BREATHE_RANGE_KEYS`, 적지 않은 경계는 0) — 하나도 안 적었으면(null 은 생략) None.

    세기(`depth`·`depth_x`)와 같이 적으면 거부한다: 둘 다 같은 곡선의 크기를 말해 어느 쪽을 따를지 조용히 고를 수 없다.
    범위 밖, 수가 아닌 값, 최소가 최대보다 큰 축도 거부한다."""
    given = {k: raw[k] for k in BREATHE_RANGE_KEYS if raw.get(k) is not None}
    if not given:
        return None
    both = sorted(k for k in ("depth", "depth_x") if raw.get(k) is not None)
    if both:
        raise SystemExit(
            f"curation: states.{state}.breathe 에 세기({', '.join(both)})와 최소·최대({', '.join(sorted(given))})가 같이 "
            f"있다 — 둘 다 숨의 크기를 말한다. 하나만 적어라 (세기 d 는 최소 -0.9009 d, 최대 +0.9009 d 인 대칭 범위다). "
            f"조용히 고르지 않는다.")
    bounds = {}
    for name in BREATHE_RANGE_KEYS:
        try:
            if isinstance(given.get(name), bool):          # float(False) 는 0 — 미러처럼 참거짓을 수로 읽지 않는다
                raise TypeError
            value = float(given.get(name, 0.0))
        except (TypeError, ValueError):
            raise SystemExit(f"curation: states.{state}.breathe.{name} 를 숫자로 못 읽는다: {given[name]!r}")
        if not -BREATHE_RANGE_MAX <= value <= BREATHE_RANGE_MAX:
            raise SystemExit(
                f"curation: states.{state}.breathe.{name} = {given[name]!r} 가 범위 [{-BREATHE_RANGE_MAX}, "
                f"{BREATHE_RANGE_MAX}] 밖이다. 조용히 깎지 않는다 — 사이드카를 고쳐라.")
        bounds[name] = value
    for axis in ("width", "height"):
        if bounds[f"{axis}_min"] > bounds[f"{axis}_max"]:
            raise SystemExit(
                f"curation: states.{state}.breathe.{axis}_min {bounds[f'{axis}_min']:g} 가 {axis}_max "
                f"{bounds[f'{axis}_max']:g} 보다 크다 — 숨의 바닥이 꼭대기 위에 있을 수 없다 (적지 않은 경계는 0).")
    return bounds


def edit_index(curation: dict[str, Any] | None, state: str, index: int) -> int:
    """인스턴스의 편집(변형/픽셀) truth 인덱스 — 링크된 복제는 원본을 가리킨다.

    복제는 기본 링크 (maintainer 확정 2026-07-18): 같은 프레임의 재생 슬롯이므로 편집
    SSoT 는 원본 하나다. `unlinked` 에 명시된 복제만 자기 편집을 소유한다.
    베이크(compose/GIF/export)와 웹뷰가 같은 규칙을 쓴다 (드리프트 금지)."""
    if not curation:
        return index
    entry = curation.get(Sidecar.STATES, {}).get(state)
    if not isinstance(entry, dict):
        return index
    clones = entry.get(Sidecar.CLONES)
    if not isinstance(clones, dict):
        return index
    src = clones.get(str(index), clones.get(index))
    if src is None:
        return index
    unlinked = entry.get(Sidecar.UNLINKED)
    if isinstance(unlinked, list) and index in {int(u) for u in unlinked if str(u).lstrip("-").isdigit()}:
        return index
    # 레거시 자가판정 (웹뷰와 동일 규칙): 링크 개념(2026-07-18) 이전 사이드카가 복제
    # 인덱스에 직접 넣어둔 변형/픽셀 편집은 독립 의도다 — 조용한 편집 소실 금지.
    own_transform = (entry.get(Sidecar.TRANSFORMS) or {}).get(str(index))
    own_pixels = (entry.get(Sidecar.PIXELS) or {}).get(str(index))
    if own_transform or (own_pixels and len(own_pixels)):
        return index
    try:
        return int(src)
    except (TypeError, ValueError):
        return index


def frame_variant(curation: dict[str, Any] | None, state: str | None = None) -> str:
    """Which extracted frame variant consumers read: 'pixel' or 'plain'.

    Resolution order (single source for every consumer):
    1. the state's own `states.<state>.pixel_unfake` (the curator's per-row toggle),
    2. the run-wide `pixel_unfake` default (the curator's toggle-all),
    3. absent sidecar / absent fields -> the canonical pixel unfakeed frames.

    Called without `state` it resolves the run-wide default only (legacy callers,
    single-state tools that pass their state explicitly elsewhere)."""
    if not curation:
        return "pixel"
    if state is not None:
        entry = curation.get(Sidecar.STATES, {}).get(state)
        if isinstance(entry, dict) and isinstance(entry.get(Sidecar.PIXEL_UNFAKE), bool):
            return "pixel" if entry[Sidecar.PIXEL_UNFAKE] else "plain"
    if curation.get(Sidecar.PIXEL_UNFAKE) is False:
        return "plain"
    return "pixel"


def frame_filename(index: int, variant: str = "pixel") -> str:
    """Frame file name for a variant. 'pixel' = canonical frame-N.png; 'plain'
    = the pre-unfake twin saved by extraction when fit.pixel_unfake."""
    if variant == "plain":
        return f"frame-{index}.plain.png"
    return f"frame-{index}.png"


def pixel_snap_scale(request: dict[str, Any]) -> int | None:
    """Logical-grid scale (cell px per logical px) for a `fit.pixel_unfake` run, or None
    for a legacy run. Mirrors extract's pp_scale so a curation transform baked onto the
    canonical pixel frames re-snaps to the SAME grid the extraction snapped to. Single
    source for compose/GIF/PNG-export/cycle, the extraction itself, and the webview
    preview — every consumer calls this instead of re-deriving the formula (hand copies
    drifted: the webview omitted the usable-height clamp branch entirely)."""
    fit = request.get("fit") or {}
    if not fit.get("pixel_unfake"):
        return None
    cell = request.get("cell", {})
    cell_height = int(cell.get("height", cell.get("size", 0)))
    margin_y = int(cell.get("safe_margin_y", cell.get("safe_margin", 0)))
    usable_height = max(1, cell_height - margin_y * 2)
    logical_height = int(fit.get("logical_height", cell_height))
    scale = max(1, cell_height // max(1, logical_height))
    if logical_height * scale > cell_height:
        scale = max(1, usable_height // max(1, logical_height))
    return scale


def effective_logical_height(request: dict[str, Any]) -> int | None:
    """엔진이 **실제로** 쓰는 논리 높이 = 셀 높이 / 파생 배율. 선언값이 아니다.

    격자 배율은 정수라, 셀 높이의 약수가 아닌 `fit.logical_height` 는 선언대로 적용될
    수 없다 — 셀 64 에 48 을 선언하면 배율은 `64//48 = 1` 이 되어 논리 높이가 64 로
    되돌아간다 (선언은 무효, 회귀 hero synthetic fixtures: conform 눌림이 제거된 뒤로
    48 은 아무 픽셀도 바꾸지 않으면서 웹뷰 라벨만 "48px" 로 거짓 표기했다, 2026-07-25).
    소비자는 선언값 대신 이 값을 보고, 추출은 선언이 무효화되면 경고로 관측시킨다."""
    scale = pixel_snap_scale(request)
    if scale is None:
        return None
    cell = request.get("cell", {})
    cell_height = int(cell.get("height", cell.get("size", 0)))
    return max(1, cell_height // scale)


def run_revision(run_dir: Path) -> str:
    """Frame-content fingerprint of the run's current generation: the request + frames
    manifest bytes plus each canonical frame file's name/size/mtime. It changes whenever
    the frames are (re)written (`--force` re-import, re-extract), so a curation sidecar
    stamped for a prior generation is detected as stale. Single source of run identity for
    the server, compose, export, and the webview."""
    h = hashlib.sha256()
    for name in (REQUEST_FILENAME, "frames/frames-manifest.json"):
        try:
            h.update((run_dir / name).read_bytes())
        except OSError:
            h.update(b"\0")
    frames_root = run_dir / "frames"
    if frames_root.is_dir():
        # 재귀 걷기 — 택소노미(frames/<dir>/<pose>/)와 flat 레거시 둘 다 커버.
        # orig/ 표시 쌍둥이는 세대 정체성에 불포함 (레거시 스탬프와 동일 규칙).
        for frame in sorted(frames_root.rglob("frame-*.png")):
            if frame.name.endswith(".plain.png") or frame.parent.name == "orig":
                continue
            try:
                st = frame.stat()
                rel = frame.relative_to(frames_root).as_posix()
                h.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}".encode())
            except OSError:
                pass
    return h.hexdigest()[:16]


# raw/프레임 파일 내용 다이제스트 캐시 — (path, size, mtime_ns) 가 같으면 재해시하지
# 않는다. state_revision 이 서버 요청마다 불리므로 MB 급 raw 재해시를 피한다.
_CONTENT_DIGEST_CACHE: dict[tuple[str, int, int], str] = {}


def _file_content_digest(path: Path) -> str | None:
    """파일 내용 sha256 12자리 (mtime/size 키 메모이즈). 없으면 None."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_size, st.st_mtime_ns)
    cached = _CONTENT_DIGEST_CACHE.get(key)
    if cached is None:
        try:
            cached = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        except OSError:
            return None
        _CONTENT_DIGEST_CACHE[key] = cached
    return cached


def state_revision(run_dir: Path, state: str, request: dict[str, Any] | None = None,
                   row: dict[str, Any] | None = None) -> list[str] | None:
    """행(state) 단위 세대 지문 — 순서 있는 원료(source-material) 세그먼트 다이제스트 리스트.

    세그먼트 = 그 행의 프레임 인덱스 공간을 만드는 원료 단위: primary raw, 그리고 선언
    순서의 take raw (manifest row `takes` 가 SSoT). raw 가 아예 없는 임포트 행은 프레임
    파일 내용 자체가 원료다. 다이제스트 입력은 원료의 **내용**(sha256)·세그먼트 프레임
    수·셀/픽셀 언페이크 기하이고, frames/ 캐시의 mtime 이나 엔진 리비전은 넣지 않는다 —
    엔진 업그레이드 heal 이 같은 raw 를 재유도해도 지문이 유지돼 큐레이션이 살아남고,
    raw 리롤·테이크 교체·셀 변경은 지문을 바꾼다.

    유효성 규칙 (load_curation_report): 저장 리스트가 현재 리스트의 접두(prefix)면 유효.
    테이크가 끝에 추가돼도 기존 프레임 인덱스 공간이 밀리지 않으므로 선택이 유지된다.
    manifest row 가 없으면 None (검증 불가 — 그 행 큐레이션은 살릴 수 없다)."""
    try:
        if request is None:
            request = load_request(run_dir)
    except (OSError, json.JSONDecodeError):
        return None
    if state not in (request.get("states") or {}):
        return None
    if row is None:
        try:
            manifest = json.loads(
                (run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        row = next((r for r in manifest.get("rows", []) if r.get("state") == state), None)
    if not isinstance(row, dict):
        return None
    cell = request.get("cell", {})
    fit = request.get("fit") or {}
    # 기하 세그먼트는 **파생된 격자 배율**을 넣는다 — 선언값(`fit.logical_height`)이 아니다.
    # 선언값을 넣으면 추출 출력이 한 픽셀도 안 바뀌는 선언 편집이 전 행 큐레이션을 무효화
    # 한다 (회귀 hero synthetic_fixture_b 2026-07-25: 셀 64 에서 무효값 48 을 지웠을 뿐인데 배율은
    # 1 그대로, 프레임은 바이트 동일이었는데도 14행이 통째로 드롭됐다). 지문은 "엔진이
    # 실제로 만들 격자"만 보면 되고, 그게 `pixel_snap_scale` 이다.
    # 주의: 이 basis 문자열이 바뀌면서 배율 표기 방식이 한 번 바뀐다 (v1.57.1). run_revision
    # fast-path 가 맞는 사이드카는 영향 없고, 이미 세대가 어긋난 사이드카만 첫 로드에서
    # 평소대로 드롭 + stale 백업된다 (조용히 재해석하지 않는다 — 원칙 6).
    basis = (f"{cell.get('width', cell.get('size'))}x{cell.get('height', cell.get('size'))}"
             f":pp={1 if fit.get('pixel_unfake') else 0}:scale={pixel_snap_scale(request)}")
    segments = row.get("takes")
    if not segments:
        segments = [{
            "label": None, "start": 0,
            "frames": row.get("frames", len(row.get("files") or [])),
            "raw": raw_rel(request, state),
        }]
    digests: list[str] = []
    for seg in segments:
        raw_path = run_dir / str(seg.get("raw", ""))
        content = _file_content_digest(raw_path) if seg.get("raw") else None
        if content is None:
            # 임포트 행 (raw 없음): 세그먼트가 낳은 프레임 파일 내용이 곧 원료.
            start = int(seg.get("start", 0))
            count = int(seg.get("frames", 0))
            parts = []
            for rel in (row.get("files") or [])[start:start + count]:
                parts.append(_file_content_digest(run_dir / rel) or "missing")
            content = hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
        h = hashlib.sha256(
            f"{seg.get('label') or ''}:{seg.get('raw') or ''}:{content}"
            f":{int(seg.get('frames', 0))}:{basis}".encode())
        digests.append(h.hexdigest()[:12])
    return digests


def backup_stale_curation(run_dir: Path, raw_text: str) -> str:
    """덮여 사라질 큐레이션 원문을 `curation.stale-<hash>.json` 으로 보존 — **writer 전용**.

    파일명이 내용 해시라 멱등 — 같은 원문은 한 번만 남고, 정상 편집 흐름에서는 절대
    생기지 않는다. 사람이 나중에 열어 selected/transforms 를 수동 복원할 수 있는
    관측 가능한 안전망이다 (백업 없는 원자 덮어쓰기 금지).

    **조회 경로는 이걸 부르지 않는다.** 예전엔 `load_curation_report` 가 세대 불일치로 행을
    드롭할 때 여기서 백업을 썼다 — 읽기만 해도 런 디렉터리에 새 파일이 생겼다는 뜻이고,
    그건 이 플랜이 request 로더에서 잡은 것과 같은 계열의 "조회가 런에 쓴다" 였다. 드롭은
    메모리 판정일 뿐 그 시점에 사라지는 것은 없다 (`curation.json` 은 디스크에 그대로다).
    실제로 원문이 사라지는 순간은 `write_curation_atomic` 의 덮어쓰기 하나뿐이고, 백업은
    그 writer 한 곳에서만 일어난다."""
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:8]
    name = f"curation.stale-{digest}.json"
    path = run_dir / name
    if not path.exists():
        path.write_text(raw_text, encoding="utf-8")
    return name


def _validated_anchor_pins(run_dir: Path, data: dict[str, Any], request: dict[str, Any] | None,
                           report: dict[str, Any]) -> dict[str, Any]:
    """앵커 지정을 검증해 `stale` 표식을 붙여 돌려준다 (드롭하지 않는다).

    드롭하면 지정이 조용히 사라져 기본값(시퀀스 헤드)으로 복귀하고, 그냥 이월하면 지정이 새
    세대의 전혀 다른 프레임에 조용히 옮겨 붙는다 — 둘 다 금지다. 표식을 달아두면
    `sprite_gen.curate.anchor.resolve_anchor` 가 fail-loud 하고 뷰가 이유를 보여준다 (validator 4차 기각:
    핀한 행을 리롤하면 사용자가 본 적 없는 프레임이 '당신이 핀한 앵커'로 표시됐다)."""
    anchors_out: dict[str, Any] = {}
    for direction, pick in anchor_choices(data).items():
        stored = pick.get(Sidecar.REVISION)
        current = state_revision(run_dir, pick[Sidecar.STATE], request=request) if request else None
        fresh = (isinstance(stored, list) and stored and current
                 and stored == current[:len(stored)])
        entry = {Sidecar.STATE: pick[Sidecar.STATE], Sidecar.INDEX: pick[Sidecar.INDEX]}
        if stored is not None:
            entry[Sidecar.REVISION] = stored
        if not fresh:
            entry[Sidecar.STALE] = True
            # 왜 낡았나를 구분한다: 재생성됐다(지문 불일치) vs 애초에 증명이 없다(지문 없음).
            # 후자에 "재생성됐다" 라고 말하면 사실과 다른 오류가 된다 (validator 5차 기각 2).
            entry[Sidecar.STALE_REASON] = "regenerated" if stored is not None else "unverifiable"
            report["anchors_stale"].append(direction)
        anchors_out[direction] = entry
    return anchors_out


def _report_stale_anchors(run_dir: Path, report: dict[str, Any]) -> None:
    if not report["anchors_stale"]:
        return
    print(f"[curation] anchor pin(s) can no longer be proven against the current frames: "
          f"{', '.join(report['anchors_stale'])} — generation fails loud until they are re-picked "
          f"(they are NOT silently reverted to the sequence head, and NOT silently re-stamped): "
          f"{run_dir}", file=sys.stderr)


def load_curation_report(run_dir: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """사이드카 로드 + 세대 검증 보고 — **읽기만 한다** (run dir 바이트 불변). Returns (doc|None, report).

    report = {"dropped": [state...], "anchors_stale": [direction...]}. 규칙:
    - run_revision 이 현재와 일치 → 문서 전체 유효 (fast path, dropped 없음).
    - 불일치 → 행 단위 구제: `revision` 스탬프가 현재 state_revision 의 접두인 행만
      유지, 나머지는 드롭. 드롭이 하나라도 있으면 stderr 로 보고한다. 전 행이 드롭되면
      doc 은 None (전량 기본값).
    스탬프 없는 행(레거시/수동 편집)은 불일치 세대에서 검증 불가 → 드롭 (No Silent
    Fallback — 증명 없는 선택을 새 프레임에 적용하지 않는다).

    드롭은 **이 로드가 무엇을 적용하지 않을지**에 대한 판정이지 파일 삭제가 아니다:
    `curation.json` 은 그대로 남아 있고, 실제로 덮이는 순간 `write_curation_atomic` 이
    `curation.stale-<hash>.json` 백업을 남긴다. 그래서 조회는 백업을 쓰지 않는다 — 조회가
    런 디렉터리에 파일을 만들면 그것도 "조회가 런에 쓴다" 다
    (plan sprite-gen/state-revision-read-mutation)."""
    path = curation_path(run_dir)
    report: dict[str, Any] = {"dropped": [], "anchors_stale": []}
    if not path.is_file():
        return None, report
    raw_text = path.read_text(encoding="utf-8")
    data = json.loads(raw_text)
    if data.get(Sidecar.KIND) != CURATION_KIND:
        raise SystemExit(f"{path} is not a sprite-gen-curation file")
    # 은퇴 키 이관 (메모리) — 다음 저장이 현행 키로 파일을 갱신한다. 두 키 동시 = hard fail.
    _migrate_curation_keys(data, str(path))
    try:
        request = load_request(run_dir)
    except (OSError, json.JSONDecodeError):
        request = None
    # 앵커 지정 검증은 **fast path 와 무관하게 항상** 돈다. run_revision 은 "이 문서를 쓴
    # 시점 = 지금 프레임 세대" 를 뜻할 뿐, 그 문서 안 지정의 스탬프가 그 세대라는 증명이
    # 아니다: 낡은 핀을 안은 채 다른 편집을 저장하면 문서 지문만 새것이 되고, fast path 는
    # 그 문서를 통째로 유효하다고 통과시켜 낡은 핀이 살아난다 (validator 5차 실측 — provenance
    # 이월만으로는 못 막았다. 게이트가 건너뛰면 이월된 증거를 아무도 안 본다).
    anchors_out = _validated_anchor_pins(run_dir, data, request, report)
    if data.get(Sidecar.RUN_REVISION) == run_revision(run_dir) and not report["anchors_stale"]:
        return ({**data, **({Sidecar.ANCHORS: anchors_out} if anchors_out else {})}, report)
    if data.get(Sidecar.RUN_REVISION) == run_revision(run_dir):
        # 프레임 세대는 현재지만 지정 하나가 낡았다 — states 는 전부 유효하다.
        _report_stale_anchors(run_dir, report)
        return ({**data, Sidecar.ANCHORS: anchors_out}, report)
    kept: dict[str, Any] = {}
    for name, entry in (data.get(Sidecar.STATES) or {}).items():
        entry_rev = entry.get(Sidecar.REVISION) if isinstance(entry, dict) else None
        current = state_revision(run_dir, name, request=request) if request else None
        if (isinstance(entry_rev, list) and entry_rev and current
                and entry_rev == current[:len(entry_rev)]):
            kept[name] = entry
        else:
            report["dropped"].append(name)
    if not report["dropped"] and not report["anchors_stale"]:
        # 런 세대 지문은 바뀌었지만 (예: request 메타 편집) 전 행이 개별 검증을 통과.
        return {**data, Sidecar.STATES: kept, **({Sidecar.ANCHORS: anchors_out} if anchors_out else {})}, report
    if report["dropped"]:
        print(f"[curation] frames regenerated under {CURATION_FILENAME}: dropped "
              f"{', '.join(report['dropped'])} (kept {len(kept)}); the file itself is unchanged "
              f"until the next save, which backs it up to curation.stale-<hash>.json: {run_dir}",
              file=sys.stderr)
    _report_stale_anchors(run_dir, report)
    if not kept and not anchors_out:
        return None, report
    # 전 행이 드롭돼도 지정은 남긴다 — 남아 있어야 오류를 낼 수 있다.
    return {**data, Sidecar.STATES: kept, **({Sidecar.ANCHORS: anchors_out} if anchors_out else {})}, report


def load_curation(run_dir: Path) -> dict[str, Any] | None:
    """The single gate every consumer (server, compose, export, GIF) passes through.
    Thin wrapper over load_curation_report — see it for the per-state salvage +
    backup semantics."""
    return load_curation_report(run_dir)[0]


def stamp_curation(run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """쓰기 직전 세대 도장. run_revision(런 전체 fast-path 지문) + 행별 `revision`
    (state_revision 세그먼트 지문) 을 payload 사본에 찍어 반환한다. `runRevision`
    (transport echo) 은 제거. 행 스탬프가 계산 불가(행 미생성)면 스탬프를 지운다 —
    거짓 증명을 남기지 않는다."""
    payload = {k: v for k, v in payload.items() if k != "runRevision"}
    payload[Sidecar.RUN_REVISION] = run_revision(run_dir)
    try:
        request = load_request(run_dir)
    except (OSError, json.JSONDecodeError):
        request = None
    anchors = payload.get(Sidecar.ANCHORS)
    if isinstance(anchors, dict):
        for direction, pick in list(anchors.items()):
            if not isinstance(pick, dict) or not isinstance(pick.get(Sidecar.STATE), str):
                continue
            for derived in (Sidecar.STALE, Sidecar.STALE_REASON, Sidecar.REPIN):
                pick.pop(derived, None)  # 파생/의도 표식은 저장하지 않는다 (게이트가 재판정)
            # **없을 때만** 찍는다: 이월되는 기존 지정에 현재 지문을 다시 찍으면 낡은 지정이
            # 새 세대에서 유효해져 버린다 (states 부활과 같은 병). 새 지정(revision 없음)만
            # 지금 세대로 도장받는다. 계산 불가(그 행 미추출)면 스탬프를 남기지 않는다 —
            # states 와 같은 규칙(거짓 증명 금지)이고, 게이트가 그걸 unverifiable 로 읽는다.
            if pick.get(Sidecar.REVISION) is None:
                rev = state_revision(run_dir, pick[Sidecar.STATE], request=request) if request else None
                if rev:
                    pick[Sidecar.REVISION] = rev
                else:
                    pick.pop(Sidecar.REVISION, None)
    states = payload.get(Sidecar.STATES)
    if isinstance(states, dict):
        for name, entry in states.items():
            if not isinstance(entry, dict):
                continue
            rev = state_revision(run_dir, name, request=request) if request else None
            if rev:
                entry[Sidecar.REVISION] = rev
            else:
                entry.pop(Sidecar.REVISION, None)
    return payload


def normalize_transform(raw: Any) -> dict[str, float]:
    """Coerce a stored transform into a full {rotate, scale, dx, dy, shx, shy, flipX} dict."""
    if not isinstance(raw, dict):
        return dict(IDENTITY)
    return {
        Sidecar.ROTATE: float(raw.get(Sidecar.ROTATE, 0.0)),
        Sidecar.SCALE: float(raw.get(Sidecar.SCALE, 1.0)),
        Sidecar.DX: float(raw.get(Sidecar.DX, 0)),
        Sidecar.DY: float(raw.get(Sidecar.DY, 0)),
        Sidecar.SHX: float(raw.get(Sidecar.SHX, 0.0)),
        Sidecar.SHY: float(raw.get(Sidecar.SHY, 0.0)),
        # (maintainer 2026-05-28) flipX: 0 | 1 — horizontal mirror. Image-gen 결과가 좌우
        # 반대로 나올 때 frame 별로 거울 반전. matrix 마지막에 diag(-1, 1) 곱.
        Sidecar.FLIP_X: 1 if raw.get(Sidecar.FLIP_X) else 0,
    }


def is_identity(transform: dict[str, float]) -> bool:
    return (
        abs(transform[Sidecar.ROTATE]) < 1e-6
        and abs(transform[Sidecar.SCALE] - 1.0) < 1e-6
        and abs(transform[Sidecar.DX]) < 1e-6
        and abs(transform[Sidecar.DY]) < 1e-6
        and abs(transform.get(Sidecar.SHX, 0.0)) < 1e-6
        and abs(transform.get(Sidecar.SHY, 0.0)) < 1e-6
        and not transform.get(Sidecar.FLIP_X, 0)
    )


def normalize_frame_indices(raw: Any, default_count: int,
                            extra_valid: set[int] | None = None) -> list[int]:
    """Return unique 0-based frame indices that are valid for this state.
    `extra_valid` admits clone instance indices (outside the physical range)."""
    if not isinstance(raw, list):
        return []
    indices: list[int] = []
    seen: set[int] = set()
    extra = extra_valid or set()
    for value in raw:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if (0 <= index < default_count or index in extra) and index not in seen:
            indices.append(index)
            seen.add(index)
    return indices


def state_clones(curation: dict[str, Any] | None, state: str, default_count: int) -> dict[int, int]:
    """행의 복제 인스턴스 맵 {복제 인덱스: 원본 프레임 인덱스}.

    복제 인덱스는 물리 프레임 범위(0..default_count-1) 밖의 정수, 원본은 범위 안이어야
    한다. 복제는 자기만의 order 슬롯/변형/픽셀편집을 갖는 정식 인스턴스이고, 굽기 때
    원본 프레임 파일을 읽는다 (frames/ 는 파생 캐시라 복제 파일을 만들지 않는다 —
    복제 의도는 사이드카가 소유). 손상 항목은 스킵."""
    entry = ((curation or {}).get(Sidecar.STATES) or {}).get(state)
    raw = entry.get(Sidecar.CLONES) if isinstance(entry, dict) else None
    clones: dict[int, int] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                clone_idx, src = int(key), int(value)
            except (TypeError, ValueError):
                continue
            if clone_idx >= default_count and 0 <= src < default_count:
                clones[clone_idx] = src
    return clones


def source_frame_index(curation: dict[str, Any] | None, state: str,
                       index: int, default_count: int) -> int:
    """인스턴스 인덱스 → 실제 프레임 파일 인덱스 (복제면 원본, 아니면 자기 자신).
    소비자는 파일을 열 때만 이 리졸버를 쓰고, transforms/pixels 는 인스턴스
    인덱스로 그대로 조회한다 — 복제마다 다른 변형이 가능해야 하므로."""
    return state_clones(curation, state, default_count).get(index, index)


def state_instances(curation: dict[str, Any] | None, state: str, default_count: int) -> list[int]:
    """행의 **살아있는 인스턴스** 전체 (물리 프레임 + 복제, 보관분 제외).

    시퀀스(state_plan)와 다르다: 후보 풀에 있는(선택 안 된) 프레임도 포함한다.
    "선택되진 않았지만 존재하는 인스턴스" 를 물어야 하는 소비자 — 앵커 프레임 지정
    검증(sprite_gen.curate.anchor) — 의 SSoT."""
    clones = state_clones(curation, state, default_count)
    entry = ((curation or {}).get(Sidecar.STATES) or {}).get(state)
    deleted: set[int] = set()
    if isinstance(entry, dict):
        deleted = set(normalize_frame_indices(entry.get(Sidecar.DELETED), default_count, set(clones)))
    return [index for index in [*range(default_count), *sorted(clones)] if index not in deleted]


def anchor_choices(curation: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """방향 앵커 프레임 지정 — {direction: {"state": str, "index": int}}.

    사용자가 큐레이션 뷰에서 "이 프레임을 앵커로" 라고 지정한 것 (maintainer 2026-07-25).
    지정이 없는 방향은 여기 없다 = 앵커 행의 시퀀스 첫 인스턴스가 앵커라는 뜻.
    손상 항목은 스킵 (손으로 편집된 사이드카가 크래시를 내지 않는다) — 지정이 가리키는
    인스턴스의 **실재 검증은 해석 시점**(sprite_gen.curate.anchor.resolve_anchor)에 fail-loud."""
    raw = (curation or {}).get(Sidecar.ANCHORS)
    picks: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for direction, value in raw.items():
            if not isinstance(value, dict):
                continue
            state = value.get(Sidecar.STATE)
            try:
                index = int(value.get(Sidecar.INDEX))
            except (TypeError, ValueError):
                continue
            if isinstance(state, str) and state:
                pick = {Sidecar.STATE: state, Sidecar.INDEX: index}
                # 세대 지문 (쓰기 시 stamp_curation 이 찍는다) + 로드 게이트가 붙인 stale 표식.
                # stale 은 파생값이라 파일에 쓰지 않는다 — 게이트가 매번 다시 판정한다.
                if value.get(Sidecar.REVISION) is not None:
                    pick[Sidecar.REVISION] = value[Sidecar.REVISION]
                if value.get(Sidecar.STALE):
                    pick[Sidecar.STALE] = True
                    pick[Sidecar.STALE_REASON] = str(value.get(Sidecar.STALE_REASON) or "regenerated")
                if value.get(Sidecar.REPIN):
                    pick[Sidecar.REPIN] = True  # 명시 재지정 의도 (쓰기 경로에서 소비·제거)
                picks[str(direction)] = pick
    return picks


def recolor_pick(curation: dict[str, Any] | None) -> str | None:
    """The adopted recolor variant name, or None when none is picked.

    Keyed by VARIANT NAME, not by a frame generation: a colourway ("crimson") is a
    decision about colour, and re-baking the same spec against re-curated frames
    produces the same colourway. So this pick is deliberately NOT stamped with a
    run/state revision the way anchor pins are — there is nothing about new frames
    that could make "crimson" the wrong answer to "which colourway".

    What CAN go wrong is the name disappearing from the spec. That is not resolved
    here: the pick is returned as stored, and the reader (the curation view via
    `serve_curation`) reports it as unknown against the current bake report instead
    of silently clearing it (No Silent Fallback)."""
    raw = (curation or {}).get(Sidecar.RECOLOR)
    if not isinstance(raw, dict):
        return None
    picked = raw.get(Sidecar.PICKED)
    return picked if isinstance(picked, str) and picked else None


def _carry_anchor_provenance(run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """앵커 지정의 세대 지문(`revision`)은 **엔진 소유**다 — 쓰는 쪽이 들고 오지 않는다.

    이미 파일에 있는 같은 지정(direction+state+index 동일)은 저장된 지문을 그대로 이월한다.
    **stale 이어도 이월한다** — 그게 fail-safe 다. 지문을 왕복시키지 않는 writer(뷰 autosave 는
    `{state, index}` 만 싣는다)가 있으면, 지문 없는 지정이 stamp 단계에서 현재 세대 도장을
    받아 **낡은 핀이 유효해지고 오류 배너가 사라진다** — 즉 무관한 편집 한 번이 4차에서 세운
    fail-loud 계약을 세탁한다 (validator 5차 기각 실측: 리롤 후 side_walk 프레임 하나 삭제·저장에
    down 핀이 새 세대 프레임으로 조용히 옮겨 붙었다).

    낡은 핀을 푸는 유일한 길은 **명시적 재지정**이다: payload 의 그 지정에 `repin: true` 가
    실려 있어야 새 지문을 받는다 (뷰의 핀 버튼이 stale 상태에서 그걸 싣는다). 잊은 writer 는
    지정을 낡은 상태로 남기므로 오류가 유지된다 — 조용히 유효해지는 쪽으로 실패하지 않는다.
    """
    anchors = payload.get(Sidecar.ANCHORS)
    if not isinstance(anchors, dict) or not anchors:
        return payload
    try:
        old = json.loads((run_dir / CURATION_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return payload
    stored = anchor_choices(old)
    payload = {**payload, Sidecar.ANCHORS: {k: dict(v) if isinstance(v, dict) else v
                                      for k, v in anchors.items()}}
    for direction, pick in payload[Sidecar.ANCHORS].items():
        if not isinstance(pick, dict):
            continue
        prior = stored.get(direction)
        if pick.pop(Sidecar.REPIN, None):
            pick.pop(Sidecar.REVISION, None)  # 명시 재지정 = 지금 세대로 새로 도장받는다
            continue
        if (prior and prior[Sidecar.STATE] == pick.get(Sidecar.STATE)
                and prior[Sidecar.INDEX] == pick.get(Sidecar.INDEX) and prior.get(Sidecar.REVISION) is not None):
            pick[Sidecar.REVISION] = prior[Sidecar.REVISION]
    return payload


def write_curation_atomic(run_dir: Path, payload: dict[str, Any]) -> str | None:
    """Atomically replace curation.json (temp file in the same dir + os.replace). Stamps the
    sidecar with the current run generation (`run_revision`) AND per-state `revision`
    segment fingerprints (stamp_curation), so a later regeneration invalidates only the
    rows it actually touched. Before replacing, any state entry in the existing file that
    this write would lose (missing from the payload, or stamped for an incompatible
    generation) triggers a `curation.stale-<hash>.json` backup of the old file — an
    autosave can never permanently destroy selections without an observable copy.
    `runRevision` is a transport-only echo field and is not stored.

    Returns the backup filename when one was written, else None — this is the **single
    place** a stale backup is created, so it is also the single place that can report one.
    The load gate deliberately does not back up: a generation-mismatched read drops rows
    from *this load*, but nothing is lost until an overwrite, and a read that creates a
    file in the run dir is itself a read-that-writes
    (plan sprite-gen/state-revision-read-mutation).

    Sidecar write semantics live with the sidecar schema (this module) so every writer —
    the webview POST and the `sprite-gen anchor --pick` CLI — stamps and backs up
    identically. Callers own the run-dir lock (publish_guard)."""
    import os
    import tempfile

    backup: str | None = None

    if payload.get(Sidecar.KIND) != CURATION_KIND:
        raise ValueError("payload is not a sprite-gen-curation document")
    _migrate_curation_keys(payload, str(run_dir / CURATION_FILENAME))
    payload = _carry_anchor_provenance(run_dir, payload)
    payload = stamp_curation(run_dir, payload)
    if isinstance(payload.get(Sidecar.ANCHORS), dict) and not payload[Sidecar.ANCHORS]:
        payload.pop(Sidecar.ANCHORS)  # 빈 지정은 기록하지 않는다 (없음 = 기본값, 같은 뜻)
    target = run_dir / CURATION_FILENAME
    if target.is_file():
        old_text = target.read_text(encoding="utf-8")
        try:
            old = json.loads(old_text)
        except json.JSONDecodeError:
            old = None
        if isinstance(old, dict):
            # 앵커 지정의 **소실**도 states 와 같은 무게로 다룬다 (백업 대칭): 뷰가 열린 채
            # CLI `--pick` 으로 심은 지정은 뷰의 다음 autosave 가 통째로 덮을 수 있고
            # (뷰가 authoritative 인 건 다른 필드와 같은 semantics 지만), 관측 가능한 사본이
            # 없으면 사용자가 사라진 지정을 되찾을 방법이 없다. **사라짐(direction 이 새
            # 문서에 아예 없음)만** 백업한다 — 지정을 다른 프레임으로 옮기는 건 소실이
            # 아니므로 정상 편집마다 백업 파일이 쌓이지 않는다.
            new_anchors = anchor_choices(payload)
            if any(d not in new_anchors for d in anchor_choices(old)):
                backup = backup_stale_curation(run_dir, old_text)
            new_states = payload.get(Sidecar.STATES) or {}
            same_generation = old.get(Sidecar.RUN_REVISION) == payload.get(Sidecar.RUN_REVISION)
            for name, old_entry in (old.get(Sidecar.STATES) or {}).items():
                new_entry = new_states.get(name)
                if not isinstance(old_entry, dict):
                    continue
                if not isinstance(new_entry, dict):
                    lost = True
                else:
                    old_rev, new_rev = old_entry.get(Sidecar.REVISION), new_entry.get(Sidecar.REVISION)
                    if isinstance(old_rev, list) and isinstance(new_rev, list):
                        lost = old_rev != new_rev[:len(old_rev)]
                    else:
                        # 레거시 스탬프 없는 항목: 같은 런 세대의 정상 편집이면 호환
                        lost = not same_generation
                if lost:
                    backup = backup_stale_curation(run_dir, old_text)
                    break
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(run_dir), prefix=".curation-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    if backup:
        # 원문이 실제로 덮인 순간에만 나는 줄이다 — 백업이 조용히 쌓이지 않게 관측 가능히.
        print(f"[curation] this save overwrote selections {CURATION_FILENAME} still held; "
              f"the previous file is preserved as {backup}: {run_dir}", file=sys.stderr)
    return backup


def transform_matrix(t: dict[str, float]) -> tuple[float, float, float, float]:
    """Forward 2x2 linear matrix (M00, M01, M10, M11) = Rotate · Shear · Scale · FlipX.

    Screen y-down. Positive `rotate` is counter-clockwise. This exact matrix is
    mirrored in the webview (CSS `matrix()` + canvas), so what the user aligns to
    the ground grid is what bakes — no preview/bake drift. flipX (when set)
    multiplies the right-most diag(-1, 1) so column-0 의 부호가 반전된다.
    """
    rr = math.radians(t[Sidecar.ROTATE])
    c, sn = math.cos(rr), math.sin(rr)
    s, shx, shy = t[Sidecar.SCALE], t.get(Sidecar.SHX, 0.0), t.get(Sidecar.SHY, 0.0)
    m00 = s * (c + sn * shy)
    m01 = s * (c * shx + sn)
    m10 = s * (-sn + c * shy)
    m11 = s * (c - sn * shx)
    if t.get(Sidecar.FLIP_X):
        m00, m10 = -m00, -m10
    return m00, m01, m10, m11


def state_pixel_ops(curation: dict[str, Any] | None, state: str) -> dict[int, dict[str, Any]]:
    """프레임별 픽셀 편집 ops — {frame_index: {"x,y": "#rrggbb"|None}}. 손상 항목은 스킵."""
    entry = ((curation or {}).get(Sidecar.STATES) or {}).get(state)
    raw = entry.get(Sidecar.PIXELS) if isinstance(entry, dict) else None
    ops: dict[int, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and value:
                ops[index] = value
    return ops


def apply_pixel_edits(frame: Image.Image, ops: dict[str, Any] | None) -> Image.Image:
    """사이드카 픽셀 편집을 프레임 사본에 합성 (원본 불변). 좌표는 셀 픽셀 공간,
    변형(apply_transform) 이전에 적용한다 — 웹뷰 오버레이와 같은 순서."""
    if not ops:
        return frame
    edited = frame.convert("RGBA").copy()
    px = edited.load()
    for key, value in ops.items():
        try:
            x_str, y_str = str(key).split(",", 1)
            x, y = int(x_str), int(y_str)
        except (TypeError, ValueError):
            continue
        if not (0 <= x < edited.width and 0 <= y < edited.height):
            continue
        if value is None:
            px[x, y] = (0, 0, 0, 0)
        elif isinstance(value, str) and value.startswith("#") and len(value) in (7, 9):
            r, g, b = int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
            a = int(value[7:9], 16) if len(value) == 9 else 255
            px[x, y] = (r, g, b, a)
    return edited


def state_plan(
    curation: dict[str, Any] | None,
    state: str,
    default_count: int,
) -> tuple[list[int], dict[int, dict[str, float]]]:
    """Resolve the ordered frame indices and per-frame transforms for a state.

    Returns (ordered_zero_based_indices, {frame_index: transform}).
    """
    default_order = list(range(default_count))
    if not curation:
        return default_order, {}
    entry = curation.get(Sidecar.STATES, {}).get(state)
    if not isinstance(entry, dict):
        return default_order, {}

    # 복제 인스턴스 인덱스도 selected/deleted 의 유효 인덱스다 (파일은 원본을 읽음
    # — source_frame_index). 기본값(선택 없음)은 물리 프레임만: 복제는 명시 선택으로만 굽는다.
    clone_ids = set(state_clones(curation, state, default_count))
    deleted = set(normalize_frame_indices(entry.get(Sidecar.DELETED), default_count, clone_ids))
    default_visible = [index for index in default_order if index not in deleted]
    selected = entry.get(Sidecar.SELECTED)
    if isinstance(selected, list) and selected:
        # tolerate a hand-edited / corrupt sidecar: skip non-integer,
        # out-of-range, duplicate, or deleted entries instead of crashing.
        ordered = [
            index
            for index in normalize_frame_indices(selected, default_count, clone_ids)
            if index not in deleted
        ]
        if not ordered:
            ordered = default_visible
    else:
        ordered = default_visible

    transforms_raw = entry.get(Sidecar.TRANSFORMS, {})
    transforms: dict[int, dict[str, float]] = {}
    if isinstance(transforms_raw, dict):
        for key, value in transforms_raw.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            transform = normalize_transform(value)
            if not is_identity(transform):
                transforms[index] = transform
    return ordered, transforms


def apply_transform(
    frame: Image.Image,
    transform: dict[str, float] | None,
    cell_size: tuple[int, int],
    snap_scale: int | None = None,
) -> Image.Image:
    """Apply scale/shear/rotate (about center) + translate, into a fresh cell.

    Rendered with one inverse-affine `Image.transform` into the cell, so cell
    size is preserved and the atlas layout never changes. Non-destructive: the
    source frame is not modified. The forward matrix matches `transform_matrix`,
    which the webview uses for its preview, so alignment to the ground grid is
    faithful to the bake.

    `snap_scale` (a `fit.pixel_unfake` run baking the canonical pixel variant,
    from `pixel_snap_scale`): the transform is sampled NEAREST and the result is
    re-quantized to the fixed logical grid (cell-anchored, `snap_scale` px per
    logical px), so a curated move/scale/rotate cannot smear the pixel grid —
    the sprite lands back on the same grid the extraction snapped to. The webview
    mirrors this quantization live while editing (curator src/display.js drawFrameInto).
    """
    transform = normalize_transform(transform) if transform else dict(IDENTITY)
    if is_identity(transform) and frame.size == cell_size:
        return frame.convert("RGBA")

    src = frame.convert("RGBA")
    cw, ch = cell_size
    m00, m01, m10, m11 = transform_matrix(transform)
    det = m00 * m11 - m01 * m10
    if abs(det) < 1e-6:
        det = 1e-6 if det >= 0 else -1e-6
    # inverse 2x2 (output -> input)
    ia, ib = m11 / det, -m01 / det
    id_, ie = -m10 / det, m00 / det
    cin_x, cin_y = src.width / 2, src.height / 2
    cout_x, cout_y = cw / 2 + transform[Sidecar.DX], ch / 2 + transform[Sidecar.DY]
    c = -(ia * cout_x + ib * cout_y) + cin_x
    f = -(id_ * cout_x + ie * cout_y) + cin_y
    if not snap_scale:
        # Coverage and colour mapped apart: BICUBIC over the keyed frame drew a light rim and a
        # key tint around the outline (docs/video-pipeline.md "Cells").
        return transform_cell(src, (cw, ch), (ia, ib, c, id_, ie, f))
    out = src.transform((cw, ch), Image.AFFINE, (ia, ib, c, id_, ie, f), resample=Image.NEAREST)
    if snap_scale > 1:
        logical_w, logical_h = max(1, cw // snap_scale), max(1, ch // snap_scale)
        out = out.resize((logical_w, logical_h), Image.Resampling.NEAREST)
        out = out.resize((logical_w * snap_scale, logical_h * snap_scale), Image.Resampling.NEAREST)
        if out.size != (cw, ch):  # cell not divisible by scale: pad back to exact cell
            padded = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            padded.alpha_composite(out, (0, 0))
            out = padded
    return out


def empty_curation() -> dict[str, Any]:
    return {Sidecar.VERSION: SCHEMA_VERSION, Sidecar.KIND: CURATION_KIND, Sidecar.STATES: {}}
