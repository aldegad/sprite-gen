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
# 예: pngs/portraits/ (표정 세트 줄) + pngs/idle/ (idle 줄).
# 셀 크기는 전 그룹 공유 최대치이므로, 거대한 레퍼런스는 미리 다른 후보 크기대로 축소해 넣는다.
# 소스 1급 수용(run-contract.md §4): pngs/_base/<img> → 베이스 참조 줄, pngs/<group>/_refs/<role>-<name>.png
#   (role=anchor|basis|guide) → 그 줄의 생성 재료 칩. _base·_refs 는 예약 폴더라 큐레이터 줄이 되지 않는다.
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

This launches a standalone local webview (no orchestrator dependency — usable from Claude Code Desktop, the Codex app, or any host with the skill installed). It shows every state's frames side by side so you can compare them in parallel. Two rows per state: a **sequence** row (the selected play order, saved to `curation.json.selected`, baked left-to-right by compose) and a **candidate pool** row below it (unselected frames, e.g. an extra generated take). A per-frame transform (drag the stage = move, bottom-right magnifier = scale, top handle = rotate, side handle = shear) corrects an off angle/position. A live preview animates the selected frames at the state fps, with play/pause, frame stepping, and a 0.25×–4× speed control.

**Card interaction model** (수홍 2026-07-15 — a stray click must never add/remove a frame):

- **Move between rows / reorder = grab the card TITLE and drag** (the whole header strip is the handle; there is no separate ⠿ grip). A plain click on the card does nothing.
- **넣기/빼기 (add/remove) button** in the card footer is the only click-toggle between sequence ⇄ pool (up arrow = add to sequence, down arrow = remove to pool). No color emphasis — direction icon only.
- **Duplicate = the ⧉ button in the card header.** A clone is a full instance (own transform/pixels/order slot) that bakes the source frame's image; its footer archive button deletes the clone. See the `clones` field below.
- **Card layout**: header = title (drag handle) + duplicate/zoom icons; footer tier 1 = size + transform readout (info); footer tier 2 = flip/reset + 넣기·빼기/archive. Hovering the title shows the full frame name in a **custom `data-tip` tooltip** (not native `title=`) whose text is selectable/copyable — so a collaborating agent can lift the exact frame path.

The webview UI is bilingual (English / Korean). Pass `--lang en|ko` to match the user's language (it is also toggleable in the app); default is `en`. For isometric sets imported with `--pngs-dir`, a sibling `meta.json` tile/anchor adds a ground-grid overlay for aligning furniture with the shear handle.

All edits are **non-destructive**: they are saved to `curation.json` in the run dir, and the original `frames/<state>/frame-N.png` files are never rewritten. The compose step bakes `curation.json` deterministically, so any curation decision is reversible by editing or deleting that file.

**실시간 계약 (수홍 확정 2026-07-14/15)** — 뷰에는 '재추출' 개념이 없다. 뷰가 보여주는 것은
항상 (raw + request + 현재 엔진 + 큐레이션)의 실시간 결과다: `/api/run`·`/api/progress` 가
요청마다 stale 프레임 캐시를 자가치유(heal)하고, 엔진이 바뀌면 열려 있는 페이지도 다음 폴에서
자동 재계산·리로드된다 (자세한 캐시 키 계약은 run-contract.md §2 frames-manifest 절).
상단 버튼 3종(**아틀라스/PNG/GIF 다운로드**, `GET /download/{atlas,pngs,gifs}`)은 '게임에 적용'이
아니라 지금 보이는 라이브 상태를 그 자리에서 계산해 zip 으로 내려주는 **다운로드**다 — 계산
산출물은 런 폴더에도 남는다 (런 폴더 = 작업장, 다운로드 = 핸드오프).

This step is optional. When there is no `curation.json`, every state uses all extracted frames in order with identity transform — an explicit default, not a silent fallback.

### 생성 트리거 관용구 (SSoT = `src/gen-trigger.js`)

서버에서 생성이 도는 모든 버튼(보간 `tween.js`, 리롤 `row-controls.js`, 이후 추가되는 것)은
한 가지 표면 계약만 쓴다 — **클릭 = 파라미터 팝오버(`.gen-pop`) 토글 → 공용 모델
select(GPT=codex / Grok=grok, `makeProviderSelect`) → 생성 버튼 → `runServerGeneration`**
(스피너 → 진행도 워치 → POST → 에러 언랩 → 성공 시 뷰 새로고침). 모델 표기·실행 시퀀스의
유일한 소유자는 `gen-trigger.js` 다. 트리거마다 다른 제스처(Alt클릭 모델 선택 등)를 만들지
않는다 (수홍 2026-07-19 통일 확정 — 실사고: 보간=팝오버 select, 리롤=Alt클릭으로 갈라져
있었다). 줄 단위 다운로드(저장 팝오버)는 생성이 아니므로 별도 파일 `row-export.js` 소유.

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
  "run_revision": "9f3c1a0b7e2d4c58",
  "pixel_perfect": true,
  "states": {
    "idle": {
      "revision": ["a1b2c3d4e5f6"],
      "pixel_perfect": false,
      "selected": [0, 2, 3, 7],
      "order": [0, 2, 3, 7, 1],
      "clones": { "7": 0 },
      "transforms": {
        "0": { "rotate": 15, "scale": 1.2, "dx": 10, "dy": -8, "flipX": 0 }
      }
    }
  }
}
```

- `run_revision` — top-level, **required**; stamped at write: the frame generation (request +
  frames-manifest + each frame's name/size/mtime) this curation was made for. When it matches
  the current run, the whole sidecar applies (fast path). The `POST /api/curation` autosave
  echoes it as `runRevision` and the server rejects a mismatched write (`HTTP 409`). See
  [`run-contract.md`](run-contract.md) §4.
- `states.<state>.revision` — 행 단위 세대 스탬프 (`curation.state_revision`): 그 행의 프레임
  인덱스 공간을 만드는 **원료** 세그먼트(primary raw + 선언 순서의 take raw; raw 없는 임포트
  행은 프레임 파일 내용) 다이제스트의 순서 리스트. frames/ 캐시의 mtime 과 엔진 리비전은
  입력이 아니다. top-level `run_revision` 이 어긋나면(재추출·heal·재임포트) 행별 구제로
  넘어간다: 저장된 리스트가 현재 리스트의 **접두(prefix)** 인 행만 유지(테이크 append 는
  인덱스 공간을 밀지 않으므로 유효), 나머지 행과 스탬프 없는 레거시 행은 드롭. **드롭이
  생기면 원문 전체가 `curation.stale-<hash>.json` 으로 먼저 백업**되고(내용 해시 파일명,
  멱등) stderr + 웹뷰 배너(`/api/run` 의 `curationDropped`/`curationBackup`)로 보고된다 —
  같은 raw 의 엔진 업그레이드 재유도에는 선택이 살아남고, raw 리롤은 그 행만 리셋되며,
  무엇도 조용히 소실되지 않는다.
- `clones` — 프레임 복제 인스턴스 맵 `{복제 인덱스: 원본 프레임 인덱스}` (웹뷰 카드의 ⧉
  버튼). 복제 인덱스는 물리 범위(0..N-1) 밖의 정수이고 `selected`/`order`/`transforms`/
  `pixels` 에 자기 인덱스로 참여하는 정식 인스턴스다 — 자기만의 변형/픽셀편집/순서를 갖고,
  compose/export/GIF 는 파일만 원본 프레임(`source_frame_index`)에서 읽는다. `frames/` 는
  파생 캐시이므로 복제 파일을 만들지 않는다(복제 의도는 사이드카 소유). 웹뷰에서 복제
  카드의 보관 버튼은 보관함이 아니라 인스턴스 삭제다.
- `pixel_perfect` — **두 층위**. top-level = 런 전체 기본값(웹뷰 우측 상단 "전체 토글" — 모든 줄을 한번에 설정; 줄별 값이 섞이면 웹뷰는 이 필드를 생략한다). `states.<state>.pixel_perfect` = 줄별 override(각 줄 헤더의 토글). 해석 순서는 `curation.frame_variant(curation, state)` 가 SSoT: 줄별 값 > top-level > 기본 `true`. `false` 인 줄은 compose/export/GIF 가 적용 전 쌍둥이(`frame-N.plain.png`)를 굽고, 없거나 `true` 인 줄은 canonical `frame-N.png`. plain 쌍둥이가 없는 런에서는 무의미(비 pixel_perfect fit). manifest 는 줄별 `animation.rows.<state>.frame_variant` 와 top-level 요약(`pixel`/`plain`/`mixed`)을 기록한다. 상세는 [`pixel-perfect.md`](pixel-perfect.md).
- `selected` — 0-based frame indices in play order. Absent/empty → all extracted frames in order.
- `order` — optional, webview-owned: the full display order (sequence row then candidate-pool row) so reopening the curator restores the exact arrangement of both rows. `compose` / `state_plan` ignore it and key off `selected`.
- `transforms` — keyed by 0-based frame index. `rotate` degrees (counter-clockwise positive, PIL convention), `scale` multiplier about center, `dx`/`dy` pixel offsets in the cell (+x right, +y down), `shx`/`shy` shear, `flipX` (0|1) horizontal mirror. Absent → identity. On a `fit.pixel_perfect` run, rows baking the pixel variant re-snap the transformed result onto the fixed logical grid (`apply_transform(snap_scale=…)`, mirrored live by the webview) — see [`pixel-perfect.md`](pixel-perfect.md).
- A state missing from the sidecar uses the all-frames identity default.
- The transform is applied at compose time inside the request-sized cell, so atlas geometry never changes. `manifest.json.animation.rows.<state>.frames` reflects the curated frame count, and `manifest.json.curation_applied` records whether a sidecar was used.

`curation.py` owns this schema and the transform math so the server and the compose scripts cannot drift. If a folder exists from a previous run, create a timestamped sibling unless the user explicitly says to replace it.

## Related

- [`../SKILL.md`](../SKILL.md) — canonical behavior contract (Workflow 스텝 3.5/5)
- [`architecture.md`](architecture.md) — 큐레이션 사이드카가 파이프라인에서 소비되는 위치
- [`locomotion-curation.md`](locomotion-curation.md) — 수동 selected-cycle, 클린 GIF export
- [`pixel-perfect.md`](pixel-perfect.md) — 전/후 쌍둥이 토글과 `pixel_perfect` 굽기 결정


## Base editing (same component as frames)

The base reference row's edit button opens the **same zoom modal** used for frame
editing (Soohong 2026-07-17: no parallel editor). What differs is only the target:

- The edit space is a **logical image built from the detected base grid** — each
  detected block's center pixel sampled from the raw (`/api/base-grid` cut lines).
  Display quantization, painting, marquee, eyedropper, palette, and the grid
  overlay all live in this uniform logical space; a uniform grid over the raw
  would drift (non-uniform fractional pitch + origin offset), so the raw view
  (pixel-perfect toggle OFF) shows no uniform grid — same rule as frames' plain view.
- Edits/transforms accumulate client-side and bake into `base-source` only via the
  explicit **save-to-base** button (`POST /api/base-edit`, `space: "logical"`;
  pixel ops expand to raw blocks and the transform bakes after edits with
  `curation.apply_transform` math, re-composited onto the chroma background).
  The original is backed up once as `.orig`. Nothing about the base ever enters
  `curation.json` (the `__base__` virtual state is excluded from the payload).
- Base edits affect FUTURE generation (anchors/rerolls) — already-extracted frames
  derive from raw strips and do not change.
- The palette dock merges near-identical raw shades (Manhattan tol 24) and excludes
  the chroma background; frames show their exact quantized colors unmerged.
