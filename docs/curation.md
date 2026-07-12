# Curation — webview, standalone view, finished-sheet editing, `curation.json` — sprite-gen reference

> `SKILL.md` 허브에서 분리한 시나리오 상세. 큐레이션뷰를 띄우거나(파이프라인 스텝 3.5 / 클로징 스텝 5), 임의 이미지 후보군을 비교·선택하거나, 완성된 시트를 다시 편집하거나, `curation.json` 스키마를 다뤄야 할 때 이 문서를 따른다.

## Standalone Curation View (이미지 후보 큐레이션 — 스프라이트 아님)

"큐레이션(뷰) 해줘 / 이미지 후보 보여줘 / 나란히 비교 / 골라볼게" 로 진입했고 대상이 **애니메이션 프레임이
아니라 임의 이미지 후보군**(아이콘 시안, 로고, 생성 초안)이면, 파이프라인 없이 이 단독 경로만 쓴다.
에이전트 채팅 surface 는 이미지를 못 보여주는 경우가 많다 — 이 웹뷰가 그 표시 수단이다.

```bash
SG=${ALEX_EXTENSIONS_DIR:-$HOME/Documents/workspace/personal/agent-extensions}/sprite-gen
STAGE=$(mktemp -d); mkdir -p "$STAGE/pngs"
cp <후보들> "$STAGE/pngs/"   # 의미 있는 이름으로: 1-hub-cube.png, 2-hook-plug.png ... (timestamp/uuid 파일명 금지)
# 성격이 다른 이미지를 한 통에 붓지 마라 — 하위폴더 = 큐레이터 줄(state) 하나.
# 예: pngs/reference/ (베이스/원본 레퍼런스 칸) + pngs/portraits/ (표정 세트 줄).
# 셀 크기는 전 그룹 공유 최대치이므로, 거대한 레퍼런스는 미리 다른 후보 크기대로 축소해 넣는다.
python3 "$SG/scripts/unpack_atlas_run.py" --pngs-dir "$STAGE/pngs" --out-dir "$STAGE/run" --force
nohup python3 "$SG/scripts/serve_curation.py" --run-dir "$STAGE/run" --lang ko > "$STAGE/server.log" 2>&1 &
sleep 2
PORT=$(lsof -nP -a -p $! -iTCP -sTCP:LISTEN | awk 'END{sub(".*:","",$9); print $9}')   # stdout 버퍼링 때문에 log 대신 lsof 로 포트 확보
curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/"   # 200 = positive proof, 그 후 URL 보고
```

- **캐릭터/스프라이트 세트 표시 기본지침 = "실제 사용하는 컷"** (수홍 확정 2026-07-12 — "대표컷"
  아님). 줄(state)은 `base`, `anchor-idle-down`, `idle`, `walk` … 처럼 개념 단위로 만들고, 각 줄의
  내용은 그 개념의 **실물**이다: base·방향 앵커처럼 실물이 1장이면 그 1장, 런타임 상태면 매니페스트가
  실제 재생하는 프레임들을 재생 순서대로 **전부**. 임의 대표컷 발췌(예: `frame-0` 한 장만) 금지 —
  게임이 행 전체를 돌리는데 한 장만 보여주면 검수 대상이 왜곡된다. 아틀라스 시트 통짜·raw
  멀티프레임 row 이미지·비교용 합성 시트를 그대로 붓는 것도 금지 (셀 크기 폭발 + 선택 단위 흐림).
  파이프라인 산출물 검수는 이 standalone 경로가 아니라 run dir 를 직접 서빙하는 파이프라인
  큐레이션(아래 스텝 3.5)이 우선이다.
- 사용자 로컬이면 브라우저 자동 오픈이 기본, headless/원격이면 `--no-open` + URL 전달.
- 선택 회수는 `"$STAGE/run/curation.json"` 의 `selected` 인덱스를 파일명으로 역매핑. 비어 있으면 다시 묻는다 — 추측 진행 금지.
- 결정 후 서버 kill + `$STAGE` 정리. 후보가 1장이면 큐레이션이 아니다 — 경로만 보고하고 끝.

## Curation Webview (파이프라인 스텝 3.5) — 캐릭터 검수의 정식 뷰

**캐릭터/스프라이트 검수의 정식 뷰 = run dir 를 직접 서빙하는 이 파이프라인 뷰다** (수홍 채택 2026-07-12).
이 뷰의 네 표시 요소(베이스 참조 줄 · 생성 재료 ref 칩 · 픽셀 격자 · 원본 화질 토글)와 그것을
채우는 `/api/run` payload 는 [`run-contract.md`](run-contract.md) §3 이 계약으로 소유한다 — 어떤
에이전트가 뷰를 차려도 같은 경험이 나오도록 스크립트가 강제한다. 아래는 그 뷰의 상호작용(선택/재배열/
변형)과 시나리오다.

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/serve_curation.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

This launches a standalone local webview (no Studio dependency — usable from Claude Code Desktop, the Codex app, or any host with the skill installed). It shows every state's frames side by side so you can compare them in parallel, toggle which frames are selected, drag the ⠿ grip on a card to reorder the play sequence or move it between the two rows — a **sequence** row (the selected play order, saved to `curation.json.selected` and baked left-to-right by compose) and a **candidate pool** row below it (unselected frames, e.g. an extra generated take of the same row); drag a cut from the pool up into the sequence to add it (or a sequence cut down to drop it), and apply a per-frame transform (drag = move, wheel = scale, top handle = rotate, bottom-left handle = shear) when a frame's angle or position is slightly off. A live preview animates the selected frames at the state fps, with play/pause, frame-by-frame stepping, and a 0.25×–4× speed control.

The webview UI is bilingual (English / Korean). Pass `--lang en|ko` to match the user's language (it is also toggleable in the app); default is `en`. For isometric sets imported with `--pngs-dir`, a sibling `meta.json` tile/anchor adds a ground-grid overlay for aligning furniture with the shear handle.

All edits are **non-destructive**: they are saved to `curation.json` in the run dir, and the original `frames/<state>/frame-N.png` files are never rewritten. The compose step bakes `curation.json` deterministically, so any curation decision is reversible by editing or deleting that file. The "Compose 굽기" button re-runs `compose_sprite_atlas.py`.

This step is optional. When there is no `curation.json`, every state uses all extracted frames in order with identity transform — an explicit default, not a silent fallback.

## Editing a finished sprite sheet (no `frames/` source)

When only the combined sheet survives (a deployed asset whose run dir is gone), rebuild a curator-ready run dir with the inverse step before curating:

```bash
# default: auto-detect the grid by reading the atlas alpha
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/unpack_atlas_run.py \
  --atlas <sheet>.png --out-dir <run-dir> --force

# when a manifest carries exact rectangles (position-faithful)
python3 .../unpack_atlas_run.py --manifest <manifest>.json [--direction <dir>] --out-dir <run-dir>

# when a human states the grid, e.g. "8x9"
python3 .../unpack_atlas_run.py --atlas <sheet>.png --grid 8x9 --out-dir <run-dir>
```

The chosen layout source is always reported (`manifest` / `grid-explicit` / `auto-detect`) and stored in `unpack-source.json` for a later writeback. Then point `serve_curation.py` at the new run dir. Auto-detect is the no-instruction default; `--grid` and `--manifest` are position-faithful (they crop full cells), while auto-detect crops each blob's content bbox and centers it in the cell.

## Multi-agent rules for the auto-launch (클로징 스텝 5)

- The server picks a free port per launch (`--port 0` default) and serves exactly one run dir, so several agents curating different characters can each keep a webview open with no port or state conflicts.
- One curator webview per run dir. Two webviews on the same run dir are last-write-wins on `curation.json`; if one is already serving that run dir, reuse its URL instead of launching another.
- Pipeline writes are guarded by a run-dir lock (`.sprite-gen.lock`): extract/compose/export/unpack fail loudly when another sprite-gen process is writing the same run dir. Treat that error as "wait or pick another run dir", not as a retry-until-success loop.
- In a headless/remote session add `--no-open` and give the user the URL; on the user's own machine the default auto-opens their browser.
- Skip the auto-launch only when the user explicitly asked for an unattended batch run.

## Curation Sidecar (`curation.json`)

`curation.json` is an optional, non-destructive sidecar written by the curation webview (`serve_curation.py`) and consumed by `compose_sprite_atlas.py` and `compose_selected_cycle.py`. It records a human selection plus a per-frame affine transform; the original frame PNGs are never modified.

```json
{
  "version": 1,
  "kind": "sprite-gen-curation",
  "pixel_perfect": true,
  "states": {
    "idle": {
      "selected": [0, 2, 3],
      "order": [0, 2, 3, 1],
      "transforms": {
        "0": { "rotate": 15, "scale": 1.2, "dx": 10, "dy": -8, "flipX": 0 }
      }
    }
  }
}
```

- `pixel_perfect` — top-level, 웹뷰 우측 상단 체크박스. `false` → compose/export 가 적용 전 쌍둥이(`frame-N.plain.png`)를 굽는다. 없거나 `true` → canonical `frame-N.png`. plain 쌍둥이가 없는 런에서는 무의미(비 pixel_perfect fit). 상세는 [`pixel-perfect.md`](pixel-perfect.md).
- `selected` — 0-based frame indices in play order. Absent/empty → all extracted frames in order.
- `order` — optional, webview-owned: the full display order (sequence row then candidate-pool row) so reopening the curator restores the exact arrangement of both rows. `compose` / `state_plan` ignore it and key off `selected`.
- `transforms` — keyed by 0-based frame index. `rotate` degrees (counter-clockwise positive, PIL convention), `scale` multiplier about center, `dx`/`dy` pixel offsets in the cell (+x right, +y down), `shx`/`shy` shear, `flipX` (0|1) horizontal mirror. Absent → identity.
- A state missing from the sidecar uses the all-frames identity default.
- The transform is applied at compose time inside the request-sized cell, so atlas geometry never changes. `manifest.json.animation.rows.<state>.frames` reflects the curated frame count, and `manifest.json.curation_applied` records whether a sidecar was used.

`curation.py` owns this schema and the transform math so the server and the compose scripts cannot drift. If a folder exists from a previous run, create a timestamped sibling unless the user explicitly says to replace it.

## Related

- [`../SKILL.md`](../SKILL.md) — canonical behavior contract (Workflow 스텝 3.5/5)
- [`architecture.md`](architecture.md) — 큐레이션 사이드카가 파이프라인에서 소비되는 위치
- [`locomotion-curation.md`](locomotion-curation.md) — 수동 selected-cycle, 클린 GIF export
- [`pixel-perfect.md`](pixel-perfect.md) — 전/후 쌍둥이 토글과 `pixel_perfect` 굽기 결정
