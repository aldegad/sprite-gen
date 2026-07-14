// SPDX-License-Identifier: Apache-2.0
// sprite-gen curation webview — vanilla JS, no build step.
//
// Edits never touch the source frame PNGs. They mutate an in-memory model that
// mirrors curation.json and is auto-saved (debounced) via POST /api/curation.
// rotate is degrees, counter-clockwise positive (matches PIL bake). The preview
// (CSS + canvas) negates it because screen/CSS/canvas positive rotation is
// clockwise, so what you see is what compose_sprite_atlas.py will bake.

const IDENTITY = () => ({ rotate: 0, scale: 1, dx: 0, dy: 0, shx: 0, shy: 0, flipX: 0 });
const SCALE_MIN = 0.2;
const SCALE_MAX = 3;
const DRAG_THRESHOLD = 4;

// forward 2x2 matrix (Rotate · Shear · Scale · FlipX); mirrors curation.py transform_matrix
function matrixOf(t) {
  const rr = (t.rotate * Math.PI) / 180;
  const c = Math.cos(rr);
  const sn = Math.sin(rr);
  const s = t.scale;
  const shx = t.shx || 0;
  const shy = t.shy || 0;
  let m00 = s * (c + sn * shy);
  const m01 = s * (c * shx + sn);
  let m10 = s * (-sn + c * shy);
  const m11 = s * (c - sn * shx);
  // (Alex 2026-05-28) flipX = horizontal mirror (image-gen 결과가 좌우 반대로
  // 나올 때). diag(-1, 1) 을 matrix 마지막에 곱 → column-0 부호 반전.
  if (t.flipX) {
    m00 = -m00;
    m10 = -m10;
  }
  return { m00, m01, m10, m11 };
}

// --- i18n (en / ko; initial language from server --lang, toggle reloads) ----
const STR = {
  en: {
    title: "curation", compose: "Bake atlas", export: "Export PNGs", exportGif: "Export GIFs",
    groundGrid: "Ground grid", langOther: "한국어",
    ppApply: "Pixel-perfect (all)", baseNote: "identity reference — not baked",
    ppState: "Pixel-perfect",
    tPpState: "toggle pixel-perfect for THIS row only — what it displays and what compose bakes",
    pxGrid: "Pixel grid", pxGridAll: "Pixel grid (all)",
    tGridState: "grid overlay for THIS row — output pixel raster on the pixel-perfect view; on the original view the FINAL correspondence grid (green): one cell = one result pixel (display only)",
    refsLabel: "generated from", ref_anchor: "direction anchor", ref_basis: "basis row", ref_guide: "layout guide", tPxGrid: "toggle the pixel-grid overlay for ALL rows at once (display only; each row has its own checkbox)",
    tPpApply: "toggle pixel-perfect for ALL rows at once (each row has its own checkbox)",
    frames: "frames", loop: "loop", nonLoop: "non-loop", preview: "Preview",
    excluded: "✗ exclude", selected: "✓ selected", extractFail: "⚠ extraction incomplete",
    editing: "editing…", saved: "saved", saveFail: "save failed: ",
    baking: "baking…", composeDone: "atlas baked", composeFail: "bake failed: ",
    exporting: "exporting…", exportFail: "export failed: ",
    ready: "ready", loaded: "loaded existing curation", runLoadFail: "failed to load run:",
    tRotate: "rotate", tShear: "shear — horizontal = shx, vertical = shy", tReset: "reset transform", tFlipX: "flip horizontally",
    tReorder: "drag the card header to reorder; a plain click toggles sequence ⇄ pool",
    tPlay: "play", tPause: "pause", tPrev: "step back", tNext: "step forward", tSpeed: "playback speed",
    zoneSeq: "Running sequence", zonePool: "Candidate pool — drag a cut up to add it", addToSeq: "✓ add", removeFromSeq: "✗ remove",
    cellPx: "cell", tContentPx: "actual sprite pixels (transparent padding excluded)",
    tZoomOpen: "inspect large (double-click the image works too)",
    tZoomStage: "wheel/pinch = view zoom · drag = move · Shift+wheel = sprite scale",
    zoomClose: "✕", tZoomPrev: "previous frame", tZoomNext: "next frame",
    marginNote: "in margin zone",
    dirAnchorBadge: "direction anchor",
    tDirAnchorBadge: "canonical facing anchor for this direction — generated from the base; every other row of this direction derives identity from it",
    dirGroupLabel: (d) => `direction · ${d}`,
    dirMirrorLabel: (d, src) => `direction · ${d} — runtime mirror of ${src} (not generated)`,
    treeTitle: "generation structure",
    treePipeline: "pipeline",
    treeFiles: "files",
    treeBaseNote: "base — first identity truth",
    treeIdleRow: "idle row",
    treeAnchorNote: "anchor · single frame-0 crop",
    treeMirror: (d, src) => `${d} — runtime mirror of ${src} (not generated)`,
    treePending: "not generated yet",
    treeRawNote: "raw · awaiting extract",
    missingPending: "not generated",
    missingRawWait: "generated · awaiting extract",
    treeRawFolder: "generated strip originals",
    treeFramesFolder: "extracted frames",
    treeAnchorsFolder: "direction anchors — single-frame crops",
    treeAtlasNote: "final atlas",
    treeFrameCount: (n) => `${n} frames`,
    treeAnchorOrigin: (d) => `idle row · ${d} anchor source`,
    reloadBanner: "run updated — click to reload the view",
    tTreeNode: "click to scroll to this row",
    tMarginNote: "some frames exceed the safe area but fit within the margin zone — informational, not a reroll flag",
    hints: ["drag card header = reorder / move row", "drag pool→sequence to add", "wheel = scale", "top handle = rotate", "click card = sequence ⇄ pool", "saved automatically"],
    exportDone: (n) => `${n} PNGs → curated/`,
    exportGifDone: (n) => `${n} GIFs → exports/`,
  },
  ko: {
    title: "큐레이션", compose: "아틀라스 굽기", export: "PNG 내보내기", exportGif: "GIF 내보내기",
    groundGrid: "바닥 그리드", langOther: "EN",
    ppApply: "픽셀퍼펙트 전체", baseNote: "원본 베이스 (아이덴티티 참조 — 굽기와 무관)",
    ppState: "픽셀퍼펙트",
    tPpState: "이 줄만 픽셀퍼펙트 켜기/끄기 — 표시와 굽기가 같이 바뀐다",
    pxGrid: "픽셀 격자", pxGridAll: "픽셀 격자 전체",
    tGridState: "이 줄 격자 오버레이 — 픽셀퍼펙트 뷰에선 출력 픽셀 눈금, 원본 뷰에선 최종 대응 격자(초록, 칸 하나 = 결과 픽셀 하나) (표시 전용)",
    refsLabel: "생성 재료", ref_anchor: "방향 앵커", ref_basis: "basis row", ref_guide: "레이아웃 가이드", tPxGrid: "모든 줄의 격자 오버레이를 한번에 켜기/끄기 (표시 전용; 줄별 체크박스는 각 줄에)",
    tPpApply: "모든 줄의 픽셀퍼펙트를 한번에 켜기/끄기 (줄별 체크박스는 각 줄에)",
    frames: "프레임", loop: "루프", nonLoop: "비루프", preview: "프리뷰",
    excluded: "✗ 제외", selected: "✓ 선택됨", extractFail: "⚠ 추출 미완료",
    editing: "편집 중…", saved: "저장됨", saveFail: "저장 실패: ",
    baking: "굽는 중…", composeDone: "아틀라스 완료", composeFail: "굽기 실패: ",
    exporting: "내보내는 중…", exportFail: "내보내기 실패: ",
    ready: "준비됨", loaded: "기존 큐레이션 로드됨", runLoadFail: "run 로드 실패:",
    tRotate: "회전", tShear: "기울이기 — 가로=shx, 세로=shy", tReset: "보정 초기화", tFlipX: "좌우 반전",
    tReorder: "헤더를 잡고 드래그하면 순서변경, 그냥 클릭하면 시퀀스↔후보",
    tPlay: "재생", tPause: "일시정지", tPrev: "이전 프레임", tNext: "다음 프레임", tSpeed: "재생 속도",
    zoneSeq: "달리기 시퀀스", zonePool: "후보 풀 — 마음에 드는 컷을 위로 끌어 추가", addToSeq: "✓ 넣기", removeFromSeq: "✗ 빼기",
    cellPx: "셀", tContentPx: "실제 스프라이트 픽셀 (투명 여백 제외)",
    tZoomOpen: "크게 보기 (이미지 더블클릭도 됨)",
    tZoomStage: "휠/핀치 = 화면 확대 · 드래그 = 이동 · Shift+휠 = 스프라이트 크기",
    zoomClose: "✕", tZoomPrev: "이전 프레임", tZoomNext: "다음 프레임",
    marginNote: "여백 침범",
    dirAnchorBadge: "방향 앵커",
    tDirAnchorBadge: "이 방향의 canonical 앵커 — base 에서 생성되고, 이 방향의 다른 모든 행이 여기서 identity 를 가져온다",
    dirGroupLabel: (d) => `방향 · ${d}`,
    dirMirrorLabel: (d, src) => `방향 · ${d} — ${src} 런타임 미러 (생성 없음)`,
    treeTitle: "생성 구조",
    treePipeline: "파이프라인",
    treeFiles: "파일",
    treeBaseNote: "base — 최초 identity",
    treeIdleRow: "idle 행",
    treeAnchorNote: "앵커 · frame-0 크롭 1장",
    treeMirror: (d, src) => `${d} — ${src} 런타임 미러 (생성 없음)`,
    treePending: "미생성",
    treeRawNote: "raw 생성됨 · 추출 전",
    missingPending: "미생성",
    missingRawWait: "생성됨 · 추출 대기",
    treeRawFolder: "생성 스트립 원본",
    treeFramesFolder: "추출 프레임",
    treeAnchorsFolder: "방향 앵커 — 1장 크롭",
    treeAtlasNote: "최종 아틀라스",
    treeFrameCount: (n) => `${n}프레임`,
    treeAnchorOrigin: (d) => `idle 행 · ${d} 앵커 원천`,
    reloadBanner: "런이 갱신됐어 — 클릭해서 새로고침",
    tTreeNode: "클릭하면 해당 줄로 이동",
    tMarginNote: "안전영역은 넘었지만 안전마진 안에 있음 — 정보성 알림, 리롤 대상 아님",
    hints: ["카드 헤더 드래그 = 순서변경 / 행 이동", "후보→시퀀스 드래그로 추가", "휠 = 확대/축소", "상단 핸들 = 회전", "카드 클릭 = 시퀀스 ⇄ 후보", "자동 저장"],
    exportDone: (n) => `PNG ${n}장 → curated/`,
    exportGifDone: (n) => `GIF ${n}개 → exports/`,
  },
};
let lang = "en";
function t(key) {
  const v = (STR[lang] && STR[lang][key]) ?? STR.en[key];
  return v;
}

let run = null; // /api/run snapshot
let entries = {}; // { stateName: { order: [idx], sel: Set<idx>, transforms: { idx: {..} } } }
const imageCache = new Map();
const previews = {}; // stateName -> { playing, speed, cursor } preview transport state

// --- pixel-perfect variant (fit.pixel_perfect runs save a .plain.png twin) --
// Per-STATE toggles: each row with a twin gets its own on/off (what that row
// displays AND bakes, persisted as curation.json states.<state>.pixel_perfect).
// The header checkbox is a toggle-ALL over the same per-state truth.
let ppAvailable = false;       // any state has a plain (pre-pixel-perfect) twin
let ppTwinStates = new Set();  // states that actually saved a twin
let ppStates = {};             // stateName -> bool (true = pixel-perfect variant)

// --- pixel-grid overlay (display only, never persisted) ---------------------
// Same per-state + toggle-all shape as pixel-perfect: each grid-capable row has
// its own checkbox, the header checkbox sets all rows at once.
let gridCapableStates = new Set(); // states with a known/measured snap grid
let gridStates = {};               // stateName -> bool (overlay shown)
let anchorStates = new Set();      // direction-anchor states (directionGroups runs)

function ppOn(stateName) {
  return ppStates[stateName] !== false;
}

function frameUrl(stateName, frame) {
  return !ppOn(stateName) && frame.plainUrl ? frame.plainUrl : frame.url;
}

// fit.pixel_perfect 런에서 픽셀 변형을 표시 중인 줄의 논리 격자 스케일 (아니면 null).
// 굽기(curation.apply_transform snap_scale)와 같은 조건 — 프리뷰가 굽기를 거울처럼 따른다.
function snapScaleFor(stateName) {
  return run.fitPixelPerfect && run.pixelPerfect && run.pixelPerfect.scale && ppOn(stateName)
    ? run.pixelPerfect.scale
    : null;
}

function isIdentityTransform(t) {
  return !t.rotate && t.scale === 1 && !t.dx && !t.dy && !t.shx && !t.shy && !t.flipX;
}

// 변형을 셀 캔버스에 NEAREST 로 그리고, 논리 격자(snap px/논리픽셀)로 재양자화한다.
// curation.apply_transform 의 snap_scale 경로를 캔버스로 미러링 — 드래그/회전 중에도
// 스프라이트가 셀 고정 격자에 실시간으로 스냅되어 보인다 (격자는 그대로, 그림이 스냅).
function drawFrameInto(ctx, image, t, cw, ch, snap) {
  if (snap) ctx.imageSmoothingEnabled = false;
  const m = matrixOf(t);
  ctx.save();
  ctx.translate(cw / 2 + t.dx, ch / 2 + t.dy);
  ctx.transform(m.m00, m.m10, m.m01, m.m11, 0, 0);
  ctx.drawImage(image, -cw / 2, -ch / 2, cw, ch);
  ctx.restore();
  if (snap && snap > 1) {
    const lw = Math.max(1, Math.floor(cw / snap));
    const lh = Math.max(1, Math.floor(ch / snap));
    const tmp = drawFrameInto._tmp || (drawFrameInto._tmp = document.createElement("canvas"));
    tmp.width = lw;
    tmp.height = lh;
    const tctx = tmp.getContext("2d");
    tctx.imageSmoothingEnabled = false;
    tctx.clearRect(0, 0, lw, lh);
    tctx.drawImage(ctx.canvas, 0, 0, cw, ch, 0, 0, lw, lh);
    ctx.clearRect(0, 0, cw, ch);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(tmp, 0, 0, lw, lh, 0, 0, lw * snap, lh * snap);
  }
}

// 픽셀퍼펙트 격자 오버레이: 픽셀퍼펙트가 실제로 스냅한 논리 픽셀 간격을 그린다.
// run.pixelPerfect.scale = 논리 픽셀 1칸이 차지하는 셀 픽셀 수 (extract 의 pp_scale).
// 예전엔 셀 픽셀마다(scale 무시) 그어서, logical_height < cell 인 런에서 실제 스냅
// 격자보다 촘촘한 거짓 격자를 보여줬다. 픽셀퍼펙트가 아닌 런은 격자 자체가 없다.
function sizePxGrids() {
  // 줄 단위 격자: 그 줄의 격자 체크박스가 켜져 있을 때만 그린다.
  // - 픽셀퍼펙트 표시 줄: 출력 격자(빨/파, 셀 픽셀 눈금) — 결과가 앉은 격자 그 자체.
  // - 원본(plain) 표시 줄: 최종 대응 격자(초록) — 최종 픽셀 콘텐츠 bbox 를 픽셀 수만큼
  //   균등 분할해 원본 위에 겹친다. 칸 하나 = 최종 픽셀 하나 (칸 수 = 픽셀 수 보장).
  //   1차 절단 뒤 48 계약 conform 축소가 칸을 합칠 수 있어 절단선(manifest input_grids)은
  //   최종 대응이 아니다 — 진단 기록으로만 남긴다 (수홍 발견 2026-07-14).
  // 측정/계약이 없는 줄은 오버레이를 숨긴다 — 가짜 격자 금지.
  document.querySelectorAll(".card").forEach((card) => {
    const overlay = card.querySelector(".pxgrid");
    const ingrid = card.querySelector(".ingrid");
    if (!overlay && !ingrid) return;
    const stage = card.querySelector(".stage");
    const st = run.states.find((s) => s.name === card.dataset.state);
    const frame = st && st.frames[Number(card.dataset.idx)];
    const on = !!gridStates[card.dataset.state];
    const plainShown = ppTwinStates.has(card.dataset.state) && !ppOn(card.dataset.state);
    const scale = (st && st.pixelScale) || (run.pixelPerfect && run.pixelPerfect.scale) || null;
    const useFinal = on && plainShown && frame && frame.contentBox && scale && stage;
    if (ingrid) {
      if (useFinal) {
        drawFinalGrid(ingrid, stage, frame.contentBox, scale);
        ingrid.style.display = "block";
      } else {
        ingrid.style.display = "none";
      }
    }
    const step = on && !useFinal ? scale : null;
    if (!overlay) return;
    if (!step || !stage) { overlay.style.display = "none"; return; }
    overlay.style.display = "block";
    const ds = (stage.clientWidth / run.cell.width) * step;
    overlay.style.backgroundSize = `${ds}px ${ds}px`;
  });
}

// 최종 대응 격자: 최종 픽셀 콘텐츠 bbox(셀 좌표)를 논리 픽셀 수만큼 균등 분할해
// 원본 쌍둥이 위에 그린다 — 초록 칸 하나가 결과 픽셀 하나에 정확히 대응한다.
function drawFinalGrid(canvas, stage, box, scale) {
  const w = Math.max(1, Math.round(stage.clientWidth));
  const h = Math.max(1, Math.round(stage.clientHeight));
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(21, 128, 61, 0.6)";
  ctx.lineWidth = 1;
  const sx = w / run.cell.width;
  const sy = h / run.cell.height;
  const cellsX = Math.max(1, Math.round((box[2] - box[0]) / scale));
  const cellsY = Math.max(1, Math.round((box[3] - box[1]) / scale));
  const x0 = box[0] * sx, x1 = box[2] * sx;
  const y0 = box[1] * sy, y1 = box[3] * sy;
  for (let k = 0; k <= cellsX; k++) {
    const px = Math.round(x0 + ((x1 - x0) * k) / cellsX) + 0.5;
    ctx.beginPath(); ctx.moveTo(px, y0); ctx.lineTo(px, y1); ctx.stroke();
  }
  for (let k = 0; k <= cellsY; k++) {
    const py = Math.round(y0 + ((y1 - y0) * k) / cellsY) + 0.5;
    ctx.beginPath(); ctx.moveTo(x0, py); ctx.lineTo(x1, py); ctx.stroke();
  }
}

function refreshVariantImages() {
  document.querySelectorAll(".card").forEach((card) => {
    const st = run.states.find((s) => s.name === card.dataset.state);
    const f = st && st.frames[Number(card.dataset.idx)];
    const el = card.querySelector(".stage img");
    if (f && el) {
      el.src = frameUrl(card.dataset.state, f);
      // 변형 표시 모드(캔버스 스냅 ↔ CSS)도 pp 상태에 맞게 다시 결정
      if (f.present) applyCardTransform(card.querySelector(".stage"), card.dataset.state, f.index);
    }
  });
  sizePxGrids();
}

// aggregate checkbox state: checked = all on, unchecked = all off, else indeterminate
function syncAggregate(checkbox, names, isOn) {
  if (!checkbox || !names.size) return;
  const vals = [...names].map(isOn);
  const allOn = vals.every(Boolean);
  checkbox.checked = allOn;
  checkbox.indeterminate = !allOn && !vals.every((v) => !v);
}

// per-state row checkboxes + the header toggle-all checkboxes reflect ppStates/gridStates
function syncPpControls() {
  document.querySelectorAll(".pp-state-check").forEach((el) => {
    el.checked = ppOn(el.dataset.state);
  });
  syncAggregate(document.getElementById("pp-apply"), ppTwinStates, ppOn);
}

function syncGridControls() {
  document.querySelectorAll(".grid-state-check").forEach((el) => {
    el.checked = !!gridStates[el.dataset.state];
  });
  syncAggregate(document.getElementById("pxgrid-check"), gridCapableStates, (n) => !!gridStates[n]);
}

// 줄별 토글 체크박스 공용 팩토리 — refs 줄과 확대 모달이 같은 클래스/핸들러를 쓰므로
// sync*Controls 가 양쪽 인스턴스를 함께 갱신한다 (per-state truth 하나, 표시 N개).
function makeStateToggle(cls, stateName, label, title, checked, onChange) {
  const el = document.createElement("label");
  el.className = "pp-apply row-toggle";
  el.title = title;
  const input = document.createElement("input");
  input.type = "checkbox";
  input.className = cls;
  input.dataset.state = stateName;
  input.checked = checked;
  input.addEventListener("change", (ev) => onChange(ev.target.checked));
  el.appendChild(input);
  el.appendChild(Object.assign(document.createElement("span"), { textContent: label }));
  return el;
}

function makeGridToggle(stateName) {
  return makeStateToggle("grid-state-check", stateName, t("pxGrid"), t("tGridState"),
    !!gridStates[stateName], (checked) => {
      gridStates[stateName] = checked;
      syncGridControls();
      sizePxGrids();
    });
}

function makePpToggle(stateName) {
  return makeStateToggle("pp-state-check", stateName, t("ppState"), t("tPpState"),
    ppOn(stateName), (checked) => {
      ppStates[stateName] = checked;
      syncPpControls();
      refreshVariantImages();
      scheduleSave();
    });
}

const statusEl = document.getElementById("status");
let saveTimer = null;

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = "status" + (kind ? " " + kind : "");
}

function img(url) {
  if (!imageCache.has(url)) {
    const i = new Image();
    i.src = url;
    imageCache.set(url, i);
  }
  return imageCache.get(url);
}

function getTransform(stateName, idx) {
  const t = entries[stateName].transforms;
  if (!t[idx]) t[idx] = IDENTITY();
  return t[idx];
}

// selected := the frame is in the sequence row (top). Moving a card between the
// sequence and pool rows (drag or click) is what flips this; see moveCardToOtherZone.
function isSelected(stateName, idx) {
  return entries[stateName].sel.has(idx);
}

// play sequence = display order filtered to selected frames.
// This is exactly what gets persisted as curation.json `selected`, which
// compose_sprite_atlas.py lays out left-to-right in this order.
function playList(stateName) {
  const e = entries[stateName];
  return e.order.filter((idx) => e.sel.has(idx));
}

// --- persistence -----------------------------------------------------------

function buildPayload() {
  const states = {};
  for (const [name, entry] of Object.entries(entries)) {
    const transforms = {};
    for (const [idx, t] of Object.entries(entry.transforms)) {
      if (t.rotate || t.scale !== 1 || t.dx || t.dy || t.shx || t.shy || t.flipX) transforms[idx] = t;
    }
    // `selected` is the play order (what compose bakes). `order` is the full
    // display order (sequence then pool) so the webview can restore the exact
    // row arrangement on reload — compose/curation.py ignore it.
    states[name] = {
      selected: entry.order.filter((idx) => entry.sel.has(idx)),
      order: entry.order.slice(),
      transforms,
    };
    // per-state pixel-perfect (the row's own toggle) — only for rows with a twin
    if (ppTwinStates.has(name)) states[name].pixel_perfect = ppOn(name);
  }
  const payload = { version: run.schemaVersion || 1, kind: "sprite-gen-curation", states };
  // echo the run generation this view was loaded with; the server rejects the autosave
  // (409) if the run was re-imported/re-extracted under this session so stale selections
  // never land on new frames.
  if (run.runRevision) payload.runRevision = run.runRevision;
  // run-wide default field: written only when every twin row agrees (uniform),
  // so a consumer without per-state awareness still bakes the right variant.
  // Mixed rows -> omitted; the per-state values above are the truth.
  if (ppAvailable) {
    const vals = [...ppTwinStates].map((n) => ppOn(n));
    if (vals.every((v) => v === vals[0])) payload.pixel_perfect = vals[0];
  }
  return payload;
}

let lastEditAt = 0;

function scheduleSave() {
  lastEditAt = Date.now();
  setStatus(t("editing"));
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 250);
}

async function save() {
  try {
    const res = await fetch("/api/curation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload()),
    });
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    setStatus(t("saved"), "ok");
  } catch (e) {
    setStatus(t("saveFail") + e.message, "err");
  }
}

// --- transform application -------------------------------------------------

function applyCardTransform(stage, stateName, idx) {
  const t = getTransform(stateName, idx);
  const el = stage.querySelector("img");
  if (!el) return;
  // dx/dy are stored in cell pixels; CSS needs rendered pixels.
  const ds = stage.clientWidth / run.cell.width;
  const m = matrixOf(t);
  const snap = snapScaleFor(stateName);
  const canvas = stage.querySelector(".snap-canvas");
  if (snap && canvas && !isIdentityTransform(t)) {
    // 픽셀퍼펙트 줄의 변형은 CSS(서브픽셀, 부드럽게)가 아니라 격자 재양자화로
    // 미리 본다 — 굽기(snap_scale bake)와 같은 결과. 격자 오버레이는 셀 고정.
    el.style.transform = "";
    el.style.visibility = "hidden";
    canvas.style.display = "block";
    const render = () => {
      canvas.width = run.cell.width;
      canvas.height = run.cell.height;
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      drawFrameInto(ctx, el, getTransform(stateName, idx), canvas.width, canvas.height, snap);
    };
    if (el.complete && el.naturalWidth) render();
    else el.addEventListener("load", render, { once: true });
  } else {
    el.style.visibility = "";
    if (canvas) canvas.style.display = "none";
    // CSS matrix(a,b,c,d,e,f): a=m00 b=m10 c=m01 d=m11; translate applied after, about center.
    el.style.transform =
      `translate(${t.dx * ds}px, ${t.dy * ds}px) matrix(${m.m00}, ${m.m10}, ${m.m01}, ${m.m11}, 0, 0)`;
  }
  const sh = t.shx || t.shy ? ` sh${(t.shx || 0).toFixed(2)},${(t.shy || 0).toFixed(2)}` : "";
  const flip = t.flipX ? " ↔" : "";
  const card = stage.closest(".card");
  card.querySelector(".tvals").textContent =
    `r${t.rotate.toFixed(0)}° ×${t.scale.toFixed(2)} ${t.dx >= 0 ? "+" : ""}${t.dx.toFixed(0)},${t.dy >= 0 ? "+" : ""}${t.dy.toFixed(0)}${sh}${flip}`;
  const flipBtn = card.querySelector(".flip-btn");
  if (flipBtn) flipBtn.classList.toggle("active", !!t.flipX);
}

// 같은 프레임을 보여주는 모든 스테이지(그리드 카드 + 확대 모달)를 함께 갱신 —
// 어느 쪽에서 편집해도 두 화면이 실시간 동기화된다.
function applyFrameTransformAll(stateName, idx) {
  document
    .querySelectorAll(`.card[data-state="${cssEscape(stateName)}"][data-idx="${idx}"] .stage`)
    .forEach((s) => applyCardTransform(s, stateName, idx));
}

// --- interactions ----------------------------------------------------------

function wireStage(stage, stateName, idx) {
  const ds = () => stage.clientWidth / run.cell.width;

  // translate by dragging, toggle select on a click that did not drag
  stage.addEventListener("pointerdown", (ev) => {
    if (ev.target.classList.contains("rotate-handle")) return;
    ev.preventDefault();
    stage.setPointerCapture(ev.pointerId);
    const t = getTransform(stateName, idx);
    const start = { x: ev.clientX, y: ev.clientY, dx: t.dx, dy: t.dy };
    let moved = false;

    const onMove = (e) => {
      const ddx = e.clientX - start.x;
      const ddy = e.clientY - start.y;
      if (Math.abs(ddx) > DRAG_THRESHOLD || Math.abs(ddy) > DRAG_THRESHOLD) moved = true;
      t.dx = start.dx + ddx / ds();
      t.dy = start.dy + ddy / ds();
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      stage.releasePointerCapture(ev.pointerId);
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("pointerup", onUp);
      if (!moved) {
        // a click (not a drag) sends the frame to the other row.
        // 확대 모달의 스테이지는 줄(.state) 밖이므로 이동 없음 — 편집 전용.
        const owner = stage.closest(".card");
        if (owner && owner.closest(".state")) moveCardToOtherZone(owner, stateName);
      } else {
        scheduleSave();
      }
    };
    stage.addEventListener("pointermove", onMove);
    stage.addEventListener("pointerup", onUp);
  });

  // scale with the wheel
  stage.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      const t = getTransform(stateName, idx);
      const factor = ev.deltaY < 0 ? 1.05 : 1 / 1.05;
      t.scale = Math.min(SCALE_MAX, Math.max(SCALE_MIN, t.scale * factor));
      applyFrameTransformAll(stateName, idx);
      scheduleSave();
    },
    { passive: false }
  );

  // rotate via the top handle
  const handle = stage.querySelector(".rotate-handle");
  handle.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    handle.setPointerCapture(ev.pointerId);
    const rect = stage.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const t = getTransform(stateName, idx);
    const startScreen = Math.atan2(ev.clientY - cy, ev.clientX - cx);
    const origRotate = t.rotate;

    const onMove = (e) => {
      const now = Math.atan2(e.clientY - cy, e.clientX - cx);
      // screen angle grows clockwise; schema is CCW positive -> subtract.
      const deltaDeg = ((now - startScreen) * 180) / Math.PI;
      t.rotate = origRotate - deltaDeg;
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      handle.releasePointerCapture(ev.pointerId);
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      scheduleSave();
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
  });

  // shear via the bottom-left handle: horizontal drag = shx, vertical = shy
  const shear = stage.querySelector(".shear-handle");
  shear.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    shear.setPointerCapture(ev.pointerId);
    const t = getTransform(stateName, idx);
    const start = { x: ev.clientX, y: ev.clientY, shx: t.shx || 0, shy: t.shy || 0 };
    const onMove = (e) => {
      // full-width drag ≈ 1.0 slope; small moves give fine control
      t.shx = start.shx + (e.clientX - start.x) / stage.clientWidth;
      t.shy = start.shy + (e.clientY - start.y) / stage.clientHeight;
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      shear.releasePointerCapture(ev.pointerId);
      shear.removeEventListener("pointermove", onMove);
      shear.removeEventListener("pointerup", onUp);
      scheduleSave();
    };
    shear.addEventListener("pointermove", onMove);
    shear.addEventListener("pointerup", onUp);
  });
}

// --- frame reorder + two-zone curation (sequence row / candidate pool) ------
//
// Each state renders two `.frames` rows: the top is the play SEQUENCE (selected
// frames, in order) and the bottom is the candidate POOL (everything else,
// e.g. an extra generated take). Dragging the ⠿ grip reorders within a row OR
// moves a card between rows; which row a card lands in *is* its selection. The
// grip lives in `.card-top`, outside `.stage`, so it never collides with the
// stage's move/scale/rotate/shear drags.

function presentCards(container) {
  return [...container.querySelectorAll(".card:not(.missing)")];
}

function zoneFrames(wrap) {
  return { seq: wrap.querySelector(".seq-frames"), pool: wrap.querySelector(".pool-frames") };
}

// selection := membership of the sequence row. order := seq cards then pool
// cards, so playList() (order ∩ sel) is exactly the sequence row, left to right.
function commitZones(wrap, stateName) {
  const { seq, pool } = zoneFrames(wrap);
  const seqIdx = presentCards(seq).map((c) => Number(c.dataset.idx));
  const poolIdx = presentCards(pool).map((c) => Number(c.dataset.idx));
  // keep not-yet-extracted (missing) frames in order so their slot survives a
  // reorder — if extraction later fills them in, they aren't silently dropped.
  const state = run.states.find((s) => s.name === stateName);
  const missingIdx = state ? state.frames.filter((f) => !f.present).map((f) => f.index) : [];
  entries[stateName].sel = new Set(seqIdx);
  entries[stateName].order = [...seqIdx, ...poolIdx, ...missingIdx];
}

// the present card the dragged card should be inserted *before*, by pointer x
// within one row. null -> after them all.
function reorderRefBefore(container, dragCard, x) {
  let ref = null;
  let closest = -Infinity;
  for (const card of presentCards(container)) {
    if (card === dragCard) continue;
    const box = card.getBoundingClientRect();
    const offset = x - (box.left + box.width / 2);
    if (offset < 0 && offset > closest) {
      closest = offset;
      ref = card;
    }
  }
  return ref;
}

// pick the row (seq above, pool below) whose band the cursor y falls into.
function pickZone(seq, pool, y) {
  const s = seq.getBoundingClientRect();
  const p = pool.getBoundingClientRect();
  return y < (s.bottom + p.top) / 2 ? seq : pool;
}

// FLIP across both rows: measure (First), reorder DOM (mutate), then invert +
// Play in 2D so cards slide — including vertically when they cross rows —
// since flexbox reflow can't be animated by CSS transitions alone.
function flipReorder(containers, mutate) {
  // exclude .missing (inert, not interactive) so unextracted slots don't animate
  const cards = containers.flatMap((c) => [...c.querySelectorAll(".card:not(.dragging):not(.missing)")]);
  const first = cards.map((c) => {
    const b = c.getBoundingClientRect();
    return { l: b.left, t: b.top };
  });
  mutate();
  // pass 1: apply the inverted transform with no transition
  const moved = [];
  cards.forEach((c, i) => {
    const b = c.getBoundingClientRect();
    const dl = first[i].l - b.left;
    const dt = first[i].t - b.top;
    if (Math.abs(dl) < 0.5 && Math.abs(dt) < 0.5) return;
    c.style.transition = "none";
    c.style.transform = `translate(${dl}px, ${dt}px)`;
    moved.push(c);
  });
  if (!moved.length) return;
  // single forced reflow commits the inverted positions across all moved cards;
  // a bare requestAnimationFrame is not reliable on Safari/Firefox (the inverted
  // frame may not paint before the transition is enabled, so cards teleport).
  void moved[0].offsetWidth;
  // pass 2: enable the transition and release to home -> they slide
  for (const c of moved) {
    c.style.transition = "transform 0.18s ease";
    c.style.transform = "";
  }
}

// click affordance: send a card to the other row (sequence <-> pool), animated.
function moveCardToOtherZone(card, stateName) {
  const wrap = card.closest(".state");
  const { seq, pool } = zoneFrames(wrap);
  const dest = card.closest(".frames") === seq ? pool : seq;
  flipReorder([seq, pool], () => dest.appendChild(card));
  commitZones(wrap, stateName);
  renderSelectionState(stateName);
  if (previews[stateName] && previews[stateName].refresh) previews[stateName].refresh();
  scheduleSave();
}

// The card header (`.card-top`) is the drag handle. A press that moves past
// DRAG_THRESHOLD lifts the card and reorders/moves it between rows; a press that
// never moves is a click that toggles the card's row (sequence ⇄ pool), the same
// affordance as clicking the stage. This is why the ✗/✓ button needs no separate
// click handler, and why a drag *started on that button* still drags the card
// instead of instantly excluding the frame (Alex 2026-06-23: grabbing the header,
// including the ✗ button, must drag — only a clean click toggles).
function wireReorder(handle, card, wrap, stateName) {
  handle.addEventListener("pointerdown", (ev) => {
    if (ev.button || !ev.isPrimary) return; // primary button + primary pointer only (no multi-touch parallel drag)
    ev.preventDefault();
    const { seq, pool } = zoneFrames(wrap);
    const startX = ev.clientX;
    const startY = ev.clientY;
    let lifted = false;
    let ph = null;
    let grabDX = 0;
    let grabDY = 0;

    const moveCard = (x, y) => {
      card.style.left = `${x - grabDX}px`;
      card.style.top = `${y - grabDY}px`;
    };

    // lift the card out of flow so it floats under the cursor; a placeholder of
    // the same size holds the slot it will drop into (in its current row). Only
    // happens once the press crosses DRAG_THRESHOLD, so a plain click never lifts.
    const lift = () => {
      const rect = card.getBoundingClientRect();
      grabDX = startX - rect.left;
      grabDY = startY - rect.top;
      ph = document.createElement("div");
      ph.className = "card-placeholder";
      ph.style.width = `${rect.width}px`;
      ph.style.height = `${rect.height}px`;
      card.parentNode.insertBefore(ph, card);
      card.classList.add("dragging");
      card.style.width = `${rect.width}px`;
      card.style.height = `${rect.height}px`;
      card.style.position = "fixed";
      card.style.zIndex = "1000";
      card.style.pointerEvents = "none";
      lifted = true;
    };

    // listeners on window (not the handle): once lifted the card is fixed/detached
    // from flow, so a handle-scoped pointerup could be missed — window catches the
    // release anywhere.
    const onMove = (e) => {
      if (!lifted) {
        if (Math.abs(e.clientX - startX) <= DRAG_THRESHOLD && Math.abs(e.clientY - startY) <= DRAG_THRESHOLD) return;
        lift();
      }
      moveCard(e.clientX, e.clientY);
      const zone = pickZone(seq, pool, e.clientY);
      const firstMissing = zone.querySelector(".card.missing");
      const refNode = reorderRefBefore(zone, card, e.clientX) || firstMissing;
      if (ph.parentNode === zone && (ph.nextElementSibling === refNode || refNode === ph)) return;
      flipReorder([seq, pool], () => zone.insertBefore(ph, refNode));
    };
    const end = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      if (!lifted) {
        // a press that never crossed the drag threshold is a click: toggle the
        // card's row (sequence ⇄ pool). This is the ✗ 빼기 / ✓ 넣기 action.
        moveCardToOtherZone(card, stateName);
        return;
      }
      const fromRect = card.getBoundingClientRect();
      card.classList.remove("dragging");
      card.style.position = card.style.left = card.style.top = "";
      card.style.width = card.style.height = card.style.zIndex = card.style.pointerEvents = "";
      ph.parentNode.insertBefore(card, ph);
      ph.remove();
      // settle: slide the dropped card from the release point into its slot.
      const toRect = card.getBoundingClientRect();
      const dx = fromRect.left - toRect.left;
      const dy = fromRect.top - toRect.top;
      if (dx || dy) {
        card.style.transition = "none";
        card.style.transform = `translate(${dx}px, ${dy}px)`;
        void card.offsetWidth; // commit before enabling transition (Safari/Firefox safe)
        card.style.transition = "transform 0.16s ease";
        card.style.transform = "";
      }
      commitZones(wrap, stateName);
      renderSelectionState(stateName); // refresh selection classes + count
      if (previews[stateName] && previews[stateName].refresh) previews[stateName].refresh();
      scheduleSave();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  });
}

function resetTransform(stateName, idx) {
  entries[stateName].transforms[idx] = IDENTITY();
  applyFrameTransformAll(stateName, idx);
  scheduleSave();
}

// --- rendering -------------------------------------------------------------

function renderSelectionState(stateName) {
  document.querySelectorAll(`.card[data-state="${cssEscape(stateName)}"]`).forEach((card) => {
    if (card.classList.contains("missing")) return;
    const idx = Number(card.dataset.idx);
    const inSeq = isSelected(stateName, idx);
    card.classList.toggle("selected", inSeq);
    const btn = card.querySelector(".sel-btn");
    if (btn) btn.textContent = inSeq ? t("removeFromSeq") : t("addToSeq");
  });
  const state = run.states.find((s) => s.name === stateName);
  const countEl = document.querySelector(`.preview[data-state="${cssEscape(stateName)}"] .count`);
  if (countEl) countEl.textContent = `${entries[stateName].sel.size}/${state.requestFrames} ${t("frames")}`;
}

function cssEscape(s) {
  return s.replace(/"/g, '\\"');
}

// escape text that comes from run data (state name/action, frame labels from a
// manifest / meta.json) before it goes into innerHTML, so an imported set can't
// inject markup into the webview.
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderState(state) {
  const wrap = document.createElement("section");
  wrap.className = "state";
  wrap.dataset.state = state.name;

  const head = document.createElement("div");
  head.className = "state-head";
  // 여백 침범 알림 (정보성): 안전영역(사방 여백 준수 상한)은 넘었지만 물리캡 이내.
  // 리롤 대상 아님 — 순한 톤으로만 표시 (수홍 확정 2026-07-14).
  const safeW = run.cell.width - (run.cell.safeMarginX || 0) * 2;
  const safeH = run.cell.height - (run.cell.safeMarginY || 0) * 2;
  const inMarginZone = state.frames.some(
    (f) => f.present && f.contentSize && (f.contentSize[0] > safeW || f.contentSize[1] > safeH)
  );
  head.innerHTML =
    `<span class="name">${escapeHtml(state.name)}</span>` +
    `<span class="meta">${state.requestFrames} ${t("frames")} · ${state.fps}fps · ${state.loop ? t("loop") : t("nonLoop")} · ${t("cellPx")} ${run.cell.width}x${run.cell.height}px</span>` +
    (state.action ? `<span class="action">${escapeHtml(state.action)}</span>` : "") +
    (state.extractOk ? "" : `<span class="state-warn">${t("extractFail")}</span>`) +
    (inMarginZone ? `<span class="state-note" title="${t("tMarginNote")}">${t("marginNote")}</span>` : "") +
    (anchorStates.has(state.name) ? `<span class="anchor-badge" title="${t("tDirAnchorBadge")}">${t("dirAnchorBadge")}</span>` : "");
  wrap.appendChild(head);

  // 이 줄을 "무엇으로 생성했는가" — run dir 실재 파일 기준 ref 체인 (앵커/basis/가이드).
  // 같은 줄 우측 = 줄별 표시/굽기 컨트롤(픽셀 격자 · 픽셀퍼펙트 체크박스) — 이미지 바로 위.
  const hasRefs = state.refs && state.refs.length;
  const showGridToggle = gridCapableStates.has(state.name);
  const showPpToggle = ppTwinStates.has(state.name);
  if (hasRefs || showGridToggle || showPpToggle) {
    const refs = document.createElement("div");
    refs.className = "state-refs";
    refs.innerHTML = hasRefs
      ? `<span class="refs-label">${t("refsLabel")}</span>` +
        state.refs
          .map(
            (r) =>
              `<a class="ref-chip" href="${escapeHtml(r.url)}" target="_blank" title="${escapeHtml(r.name)}">` +
              `<img src="${escapeHtml(r.url)}" alt="${escapeHtml(r.role)}" loading="lazy" />` +
              `<span>${t("ref_" + r.role)}</span></a>`
          )
          .join("")
      : "";
    const controls = document.createElement("span");
    controls.className = "row-controls";
    if (showGridToggle) controls.appendChild(makeGridToggle(state.name));
    if (showPpToggle) controls.appendChild(makePpToggle(state.name));
    refs.appendChild(controls);
    wrap.appendChild(refs);
  }

  const body = document.createElement("div");
  body.className = "state-body";

  // two rows: sequence (selected, in play order) on top, candidate pool below.
  const zones = document.createElement("div");
  zones.className = "zones";
  zones.innerHTML =
    `<div class="zone zone-seq"><div class="zone-label">${t("zoneSeq")}</div>` +
    `<div class="frames seq-frames"></div></div>` +
    `<div class="zone zone-pool"><div class="zone-label">${t("zonePool")}</div>` +
    `<div class="frames pool-frames"></div></div>`;
  const seqFrames = zones.querySelector(".seq-frames");
  const poolFrames = zones.querySelector(".pool-frames");

  const e = entries[state.name];
  const frameByIdx = new Map(state.frames.map((f) => [f.index, f]));
  for (const idx of e.order) {
    if (!e.sel.has(idx)) continue;
    const frame = frameByIdx.get(idx);
    if (frame) seqFrames.appendChild(renderCard(state, frame));
  }
  // pool = everything not in the sequence. `order` already contains every
  // index (present + missing), so this single loop covers missing frames too
  // — do NOT also iterate state.frames here or missing cards render twice.
  for (const idx of e.order) {
    if (e.sel.has(idx)) continue;
    const frame = frameByIdx.get(idx);
    if (frame) poolFrames.appendChild(renderCard(state, frame));
  }

  body.appendChild(zones);
  body.appendChild(renderPreview(state));
  wrap.appendChild(body);

  document.getElementById("states").appendChild(wrap);

  // wire stages + reorder grips after they are in the DOM (need clientWidth)
  for (const frame of state.frames) {
    if (!frame.present) continue;
    const card = wrap.querySelector(`.card[data-idx="${frame.index}"]`);
    const stage = card.querySelector(".stage");
    wireStage(stage, state.name, frame.index);
    applyCardTransform(stage, state.name, frame.index);
    if (run.iso) drawGroundGrid(stage);
    // the whole header strip is the drag handle (grip + label + ✗/✓ button),
    // not just the ⠿ glyph — see wireReorder.
    const cardTop = card.querySelector(".card-top");
    if (cardTop) wireReorder(cardTop, card, wrap, state.name);
  }
  renderSelectionState(state.name);
  startPreview(state);
}

// --- 생성 구조 트리 (파이프라인 개요) ----------------------------------------
// base → 방향별 idle 행 → 앵커(frame-0 크롭 1장) → 각 행. 방향 계약 없는 런은
// base → 행 2단. 미생성 노드는 점선(진행 현황판 겸용), 클릭 = 해당 줄로 스크롤.
function treeNode(label, note, thumbUrl, targetState, extra) {
  const rawOnly = thumbUrl && typeof thumbUrl === "object" && thumbUrl.raw;
  const node = document.createElement("span");
  node.className = "tree-node" + (thumbUrl === false ? " pending" : "") + (rawOnly ? " raw-only" : "") + (extra ? " " + extra : "");
  if (rawOnly) {
    const img = document.createElement("img");
    img.src = thumbUrl.raw;
    img.alt = label;
    node.appendChild(img);
  } else if (thumbUrl) {
    const img = document.createElement("img");
    img.src = thumbUrl;
    img.alt = label;
    node.appendChild(img);
  } else if (thumbUrl === false) {
    node.appendChild(Object.assign(document.createElement("span"), { className: "thumb-missing" }));
  }
  node.appendChild(Object.assign(document.createElement("span"), { className: "tn-label", textContent: label }));
  if (note) node.appendChild(Object.assign(document.createElement("span"), { className: "tn-note", textContent: note }));
  if (targetState) {
    node.title = t("tTreeNode");
    node.classList.add("clickable");
    node.addEventListener("click", () => {
      const section = targetState === "__base__"
        ? document.querySelector(".base-row")
        : document.querySelector(`.state[data-state="${cssEscape(targetState)}"]`);
      if (!section) return;
      section.scrollIntoView({ behavior: "smooth", block: "start" });
      flashSection(section);
    });
  }
  return node;
}

// 이동한 대상 패널 하이라이트: 스크롤이 끝난 뒤 따닥 두 번 깜빡이고 사라진다.
// scrollend 지원 브라우저는 도착 즉시, 아니면 짧은 타임아웃 폴백.
function flashSection(el) {
  let fired = false;
  const fire = () => {
    if (fired) return;
    fired = true;
    window.removeEventListener("scrollend", fire);
    el.classList.remove("flash-target");
    void el.offsetWidth; // 연속 클릭 시 애니메이션 재시작
    el.classList.add("flash-target");
    el.addEventListener("animationend", () => el.classList.remove("flash-target"), { once: true });
  };
  window.addEventListener("scrollend", fire, { once: true });
  setTimeout(fire, 750); // 스크롤이 필요 없거나 scrollend 미지원일 때
}

// 생성 진행 스냅샷 (트리 실시간 갱신): stateName -> {raw, frames}. 초기값은 /api/run,
// 이후 /api/progress 3초 폴링이 갱신한다.
let treeProgress = new Map();
let treeRevision = null;

async function seedTreeProgress() {
  // 초기값도 /api/progress 로 — 경로(rawUrl/frame0Url/relRaw)는 서버 리졸버가 SSoT
  // (택소노미/flat 레이아웃을 클라이언트가 패턴 조립하지 않는다).
  try {
    const res = await fetch("/api/progress");
    const next = await res.json();
    if (next.states) {
      treeProgress = new Map(next.states.map((p) => [p.name, p]));
      treeRevision = next.runRevision;
      return;
    }
  } catch { /* 아래 폴백 */ }
  treeProgress = new Map(run.states.map((s) => [s.name, {
    raw: !!s.rawPresent,
    frames: s.frames.filter((f) => f.present).length,
  }]));
  treeRevision = run.runRevision;
}

function renderPipelineTree() {
  const frameThumb = (name) => {
    const p = treeProgress.get(name);
    if (!(p && p.frames > 0)) return false;
    return `${p.frame0Url || `/frames/${encodeURIComponent(name)}/frame-0.png`}?v=${treeRevision || 0}`;
  };
  const rawThumb = (name) => {
    const p = treeProgress.get(name);
    if (!(p && p.raw)) return false;
    return `${p.rawUrl || `/run/raw/${encodeURIComponent(name)}.png`}?v=${treeRevision || 0}`;
  };
  const frameCount = (name) => {
    const p = treeProgress.get(name);
    return p ? p.frames : 0;
  };
  // 생성 진행을 반영한 대표 썸네일: 추출 프레임 > raw 스트립 > 미생성
  const bestThumb = (name) => {
    const f = frameThumb(name);
    if (f) return f;
    const r = rawThumb(name);
    return r ? { raw: r } : false;
  };
  const anchorFileThumb = (direction) => {
    const f = (run.anchorFiles || []).find((a) => a.name === `${direction}.png`);
    return f ? `${f.url}?v=${treeRevision || 0}` : null;
  };
  const chipList = () => {
    const ul = document.createElement("ul");
    ul.className = "tree-rows";
    return ul;
  };
  const chipItem = (ul, node) => {
    const el = document.createElement("li");
    el.appendChild(node);
    ul.appendChild(el);
  };
  const liWith = (parentUl, ...nodes) => {
    const el = document.createElement("li");
    for (const n of nodes) if (n) el.appendChild(n);
    parentUl.appendChild(el);
    return el;
  };
  // 접을 수 있는 블록 (파이프라인 / 파일) — folderNode 의 접힘 상태 공유
  const block = (label, ul, kind) => {
    const div = document.createElement("div");
    div.className = "tree-block" + (kind ? " " + kind : "");
    div.appendChild(folderNode(label, null, kind === "pipeline" ? "flow" : "folder"));
    div.appendChild(ul);
    if (collapsedFolders.has(label)) div.classList.add("folder-collapsed");
    return div;
  };
  const stateChip = (name, extraNote, extraCls) => {
    const n = frameCount(name);
    const note = [n > 0 ? STR[lang].treeFrameCount(n) : t("treePending"), extraNote].filter(Boolean).join(" · ");
    return treeNode(name, note, bestThumb(name), name, extraCls);
  };

  // ── 파이프라인 블록: base → <dir>_idle 행 → 방향 앵커 → rows 체인 ──────────
  const chainUl = document.createElement("ul");
  let chainHost = chainUl;
  if (run.baseUrl) {
    const baseLi = liWith(chainUl, treeNode("base", t("treeBaseNote"), run.baseUrl, "__base__", "tree-root"));
    chainHost = document.createElement("ul");
    baseLi.appendChild(chainHost);
  }
  if (run.directionGroups && run.directionGroups.length) {
    for (const group of run.directionGroups) {
      if (group.mirrorOf) {
        liWith(chainHost, treeNode(STR[lang].treeMirror(group.direction, group.mirrorOf), null, undefined, null, "mirror"));
        continue;
      }
      if (group.anchor) {
        const idleLi = liWith(chainHost, stateChip(group.anchor, t("treeIdleRow")));
        const anchorUl = document.createElement("ul");
        idleLi.appendChild(anchorUl);
        const anchorLi = liWith(anchorUl, treeNode(
          `${group.direction} ${t("dirAnchorBadge")}`, t("treeAnchorNote"),
          anchorFileThumb(group.direction) || bestThumb(group.anchor), group.anchor, "anchor"));
        const rows = chipList();
        for (const name of group.states.filter((n) => n !== group.anchor)) chipItem(rows, stateChip(name));
        anchorLi.appendChild(rows);
      } else {
        const rows = chipList();
        for (const name of group.states) chipItem(rows, stateChip(name));
        liWith(chainHost, treeNode(group.direction, null, undefined, null)).appendChild(rows);
      }
    }
  } else {
    const rows = chipList();
    for (const st of run.states) chipItem(rows, stateChip(st.name));
    const holder = liWith(chainHost);
    holder.appendChild(rows);
  }

  // ── 파일 블록: 폴더 뼈대 (어디에 저장되는가) ──────────────────────────────
  const fileUl = document.createElement("ul");
  if (run.baseUrl) liWith(fileUl, treeNode("base-source", null, run.baseUrl, "__base__"));
  const rawLi = liWith(fileUl, folderNode("raw/", t("treeRawFolder")));
  const rawUl = chipList();
  for (const st of run.states) {
    const thumb = rawThumb(st.name);
    const note = thumb && frameCount(st.name) === 0 ? t("treeRawNote") : null;
    const rel = (treeProgress.get(st.name) || {}).relRaw;
    const label = rel ? rel.replace(/^raw\//, "") : `${st.name}.png`;
    chipItem(rawUl, treeNode(label, note, thumb ? { raw: thumb } : false, st.name));
  }
  rawLi.appendChild(rawUl);
  const framesLi = liWith(fileUl, folderNode("frames/", t("treeFramesFolder")));
  const framesUl = chipList();
  for (const st of run.states) {
    const n = frameCount(st.name);
    const rel = (treeProgress.get(st.name) || {}).relFrames;
    const label = rel ? rel.replace(/^frames\//, "") + "/" : `${st.name}/`;
    chipItem(framesUl, treeNode(label, n > 0 ? STR[lang].treeFrameCount(n) : t("treePending"), frameThumb(st.name), st.name));
  }
  framesLi.appendChild(framesUl);
  if (run.anchorFiles && run.anchorFiles.length) {
    const aLi = liWith(fileUl, folderNode("references/anchors/", t("treeAnchorsFolder")));
    const aUl = chipList();
    for (const a of run.anchorFiles) {
      chipItem(aUl, treeNode(a.name, t("treeAnchorNote"), `${a.url}?v=${treeRevision || 0}`, null, "anchor"));
    }
    aLi.appendChild(aUl);
  }
  if (run.hasAtlas) {
    liWith(fileUl, treeNode("sprite-sheet-alpha.png", t("treeAtlasNote"), `/run/sprite-sheet-alpha.png?v=${treeRevision || 0}`, null));
  }

  const wrap = document.createElement("section");
  wrap.className = "state pipeline-tree";
  wrap.innerHTML =
    `<div class="state-head"><span class="name">${t("treeTitle")}</span>` +
    `<span class="meta tree-path" title="${escapeHtml(run.runDir)}">${escapeHtml(run.runDir)}</span></div>`;
  // 파이프라인 가지 곡선: CSS border 는 대시 애니메이션이 못 타므로 SVG 패스로.
  // rail(정적 옅은 액센트) 위로 dash 가 곡선을 따라 흘러내린다.
  const SVG_NS = "http://www.w3.org/2000/svg";
  const attachBranch = (li) => {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "branch");
    svg.setAttribute("viewBox", "0 0 15 19");
    svg.setAttribute("width", "15");
    svg.setAttribute("height", "19");
    const d = "M0.5 0 V11.5 Q0.5 18.5 7.5 18.5 H15";
    for (const cls of ["rail", "dash"]) {
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", d);
      path.setAttribute("class", cls);
      svg.appendChild(path);
    }
    li.insertBefore(svg, li.firstChild);
  };
  for (const li of chainUl.querySelectorAll("li")) attachBranch(li);

  const root = document.createElement("div");
  root.className = "tree";
  root.appendChild(block(t("treePipeline"), chainUl, "pipeline"));
  root.appendChild(block(t("treeFiles"), fileUl));
  wrap.appendChild(root);
  const existing = document.querySelector(".pipeline-tree");
  if (existing) existing.replaceWith(wrap);
  else document.getElementById("sidebar").appendChild(wrap);
}

// 폴더 노드 — SVG 폴더 아이콘 + 경로 라벨 (이모지 금지 규칙).
// 클릭 = 접기/펴기. 트리는 진행 폴링으로 재렌더되므로 접힘 상태를 라벨 키로 유지한다.
const collapsedFolders = new Set();

const FOLDER_ICON =
  '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
  '<path d="M1.5 4A1.5 1.5 0 0 1 3 2.5h2.6a1 1 0 0 1 .8.4l.9 1.1H13A1.5 1.5 0 0 1 14.5 5.5v6A1.5 1.5 0 0 1 13 13H3a1.5 1.5 0 0 1-1.5-1.5V4z" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';
// 파이프라인(흐름) 아이콘 — 위 노드에서 아래 노드로 흘러 내려가는 모양 (폴더와 구분)
const FLOW_ICON =
  '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
  '<circle cx="8" cy="3" r="1.7" fill="none" stroke="currentColor" stroke-width="1.2"/>' +
  '<path d="M8 4.7v4.1M5.6 8.9 8 11.3l2.4-2.4" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>' +
  '<circle cx="8" cy="13" r="1.7" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';

function folderNode(label, note, icon) {
  const node = document.createElement("span");
  node.className = "tree-node folder clickable";
  node.innerHTML =
    '<svg class="caret" viewBox="0 0 16 16" width="10" height="10" aria-hidden="true">' +
    '<path d="M5 3.5 10.5 8 5 12.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    (icon === "flow" ? FLOW_ICON : FOLDER_ICON);
  node.appendChild(Object.assign(document.createElement("span"), { className: "tn-label", textContent: label }));
  if (note) node.appendChild(Object.assign(document.createElement("span"), { className: "tn-note", textContent: note }));
  node.addEventListener("click", () => {
    const li = node.parentElement;
    const collapsed = !li.classList.contains("folder-collapsed");
    li.classList.toggle("folder-collapsed", collapsed);
    if (collapsed) collapsedFolders.add(label);
    else {
      collapsedFolders.delete(label);
      for (const ul of li.querySelectorAll(":scope > ul")) {
        ul.animate(
          [{ opacity: 0, transform: "translateY(-5px)" }, { opacity: 1, transform: "none" }],
          { duration: 190, easing: "ease" });
      }
    }
  });
  return node;
}

// 3초 폴링: 생성/추출 진행을 트리에 실시간 반영. 프레임 세대(runRevision)가 바뀌면
// 아래 상태 줄들은 구세대라 새로고침 배너를 띄운다 (편집 중 강제 리로드는 하지 않는다).
async function pollTreeProgress() {
  try {
    const res = await fetch("/api/progress");
    if (!res.ok) return;
    const next = await res.json();
    if (!next.states) return;
    const sig = JSON.stringify(next.states.map((p) => [p.name, p.raw, p.frames]));
    const prev = JSON.stringify([...treeProgress.entries()].map(([n, p]) => [n, p.raw, p.frames]));
    const revChanged = next.runRevision !== treeRevision;
    if (sig !== prev || revChanged) {
      treeProgress = new Map(next.states.map((p) => [p.name, p]));
      treeRevision = next.runRevision;
      renderPipelineTree();
      // 우측 상태 패널은 로드 시점 스냅샷이라 새 raw/프레임을 모른다 — 생성을
      // 지켜보는 중(최근 편집 없음 + 모달 안 열림)이면 통째로 새로고침해 동기화.
      // 편집 중이면 강제 리로드 대신 배너만 (자동저장이 보존하지만 흐름을 끊지 않게).
      const editing = Date.now() - lastEditAt < 15000 || document.getElementById("zoom-modal");
      if (!editing) {
        location.reload();
        return;
      }
    }
    if (next.runRevision !== run.runRevision) showReloadBanner();
  } catch {
    /* 서버 일시 중단은 조용히 재시도 */
  }
}

function showReloadBanner() {
  if (document.getElementById("reload-banner")) return;
  const banner = document.createElement("button");
  banner.id = "reload-banner";
  banner.type = "button";
  banner.textContent = t("reloadBanner");
  banner.addEventListener("click", () => location.reload());
  document.body.appendChild(banner);
}

// 최상단 base 참조 줄 — 아이덴티티 truth 를 생성 결과와 나란히 비교하기 위한
// 읽기 전용 표시 (선택/변형/굽기와 무관).
function renderBaseRow() {
  const wrap = document.createElement("section");
  wrap.className = "state base-row";
  wrap.innerHTML =
    `<div class="state-head"><h3>base</h3>` +
    `<span class="muted">${t("baseNote")}</span></div>` +
    `<div class="base-stage"><img src="${escapeHtml(run.baseUrl)}" alt="base source" draggable="false" /></div>`;
  document.getElementById("states").appendChild(wrap);
}

function renderCard(state, frame) {
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.state = state.name;
  card.dataset.idx = frame.index;
  if (!frame.present) card.classList.add("missing");
  card.style.setProperty("--cell-aspect", run.cell.width / run.cell.height);

  const stageInner = frame.present
    ? (run.iso ? `<canvas class="grid-overlay"></canvas>` : "") +
      `<div class="pxgrid"></div>` +
      `<canvas class="ingrid"></canvas>` +
      `<img src="${escapeHtml(frameUrl(state.name, frame))}" alt="frame ${frame.index}" draggable="false" />` +
      `<canvas class="snap-canvas"></canvas>` +
      `<div class="rotate-handle" title="${t("tRotate")}"></div>` +
      `<div class="shear-handle" title="${t("tShear")}"></div>`
    : `<div class="missing-label">${state.rawPresent ? t("missingRawWait") : t("missingPending")}</div>`;

  const label = frame.label ? escapeHtml(frame.label) : `#${frame.index}`;
  card.innerHTML =
    `<div class="card-top">` +
    `<span class="ct-left">` +
    (frame.present ? `<span class="grip" title="${t("tReorder")}" aria-label="reorder">⠿</span>` : "") +
    `<span class="idx" title="frame ${frame.index}">${label}</span>` +
    `</span>` +
    `<span class="ct-right">` +
    (frame.present ? `<button type="button" class="ghost zoom-btn" title="${t("tZoomOpen")}">⛶</button>` : "") +
    `<button type="button" class="ghost sel-btn">${t("excluded")}</button>` +
    `</span>` +
    `</div>` +
    `<div class="stage">${stageInner}</div>` +
    `<div class="card-controls">` +
    `<span class="psize" title="${t("tContentPx")}">${frame.present && frame.contentSize ? `${frame.contentSize[0]}x${frame.contentSize[1]}px` : ""}</span>` +
    `<span class="tvals"></span>` +
    `<button type="button" class="ghost flip-btn" title="${t("tFlipX")}" aria-label="flip-x">↔</button>` +
    `<button type="button" class="ghost reset-btn" title="${t("tReset")}">↺</button>` +
    `</div>`;

  // No separate ✗/✓ click handler: the header strip (.card-top) owns the press —
  // move past threshold = drag, clean click = toggle row — via wireReorder, so a
  // click on the button toggles there. A handler here would double-fire the toggle.
  if (frame.present) {
    // 픽셀아트 확대 표시: 프레임 원본보다 크게 그려질 때만 pixelated (다운스케일 회화체는 부드럽게 유지)
    const imgEl = card.querySelector(".stage img");
    if (imgEl) {
      const markPx = () =>
        requestAnimationFrame(() => {
          if (imgEl.naturalWidth && imgEl.clientWidth > imgEl.naturalWidth) imgEl.classList.add("px-upscale");
        });
      if (imgEl.complete) markPx();
      else imgEl.addEventListener("load", markPx, { once: true });
    }
    card.querySelector(".reset-btn").addEventListener("click", () =>
      resetTransform(state.name, frame.index)
    );
    card.querySelector(".flip-btn").addEventListener("click", () =>
      toggleFlipX(state.name, frame.index)
    );
    // 확대 모달 진입: 헤더의 ⛶ 버튼 (pointerdown 전파를 끊어 헤더 드래그/클릭 토글과
    // 충돌하지 않게) + 스테이지 더블클릭.
    const zoomBtn = card.querySelector(".zoom-btn");
    if (zoomBtn) {
      zoomBtn.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      zoomBtn.addEventListener("click", () => openZoom(state.name, frame.index));
    }
    card.querySelector(".stage").addEventListener("dblclick", () => openZoom(state.name, frame.index));
  }
  return card;
}

/** Toggle horizontal flip for a single frame (Alex 2026-05-28). */
function toggleFlipX(stateName, idx) {
  const entry = entries[stateName];
  if (!entry) return;
  if (!entry.transforms[idx]) entry.transforms[idx] = IDENTITY();
  entry.transforms[idx].flipX = entry.transforms[idx].flipX ? 0 : 1;
  // 모든 스테이지에 거울 반전을 렌더하고 flip 버튼을 강조한다.
  applyFrameTransformAll(stateName, idx);
  scheduleSave();
}

function renderPreview(state) {
  const box = document.createElement("div");
  box.className = "preview";
  box.dataset.state = state.name;
  const aspect = run.cell.height / run.cell.width;
  const speedOpts = [0.25, 0.5, 1, 2, 4]
    .map((v) => `<option value="${v}"${v === 1 ? " selected" : ""}>×${v}</option>`)
    .join("");
  box.innerHTML =
    `<h4>${t("preview")}</h4>` +
    `<canvas${run.cell.width < 160 ? ' class="px-upscale"' : ""} width="${run.cell.width}" height="${run.cell.height}" style="height:${(160 * aspect).toFixed(0)}px"></canvas>` +
    `<div class="count"></div>` +
    `<div class="pv-controls">` +
    `<button type="button" class="ghost pv-prev" title="${t("tPrev")}">⏮</button>` +
    `<button type="button" class="ghost pv-play" title="${t("tPause")}">⏸</button>` +
    `<button type="button" class="ghost pv-next" title="${t("tNext")}">⏭</button>` +
    `<select class="pv-speed" name="speed-${escapeHtml(state.name)}" aria-label="${t("tSpeed")}" title="${t("tSpeed")}">${speedOpts}</select>` +
    `</div>` +
    `<div class="pv-pos"></div>`;
  return box;
}

function startPreview(state) {
  const root = document.querySelector(`.preview[data-state="${cssEscape(state.name)}"]`);
  const canvas = root.querySelector("canvas");
  const ctx = canvas.getContext("2d");
  const cw = run.cell.width;
  const ch = run.cell.height;
  const playBtn = root.querySelector(".pv-play");
  const posEl = root.querySelector(".pv-pos");
  const pv = (previews[state.name] = { playing: true, speed: 1, cursor: 0, shown: -1 });
  let last = 0;

  const syncPlayBtn = () => {
    playBtn.textContent = pv.playing ? "⏸" : "▶";
    playBtn.title = pv.playing ? t("tPause") : t("tPlay");
  };

  // draw the frame at the current cursor; runs every rAF so live transform
  // edits show even while paused. The matrix matches CSS + the compose bake.
  const draw = () => {
    const play = playList(state.name);
    ctx.clearRect(0, 0, cw, ch);
    if (!play.length) {
      posEl.textContent = "0/0";
      return;
    }
    pv.cursor = ((pv.cursor % play.length) + play.length) % play.length;
    const idx = play[pv.cursor];
    pv.shown = idx; // remember which frame is on screen (for reanchoring on edits)
    const f = state.frames[idx];
    const image = f ? img(frameUrl(state.name, f)) : null;
    if (image && image.complete && image.naturalWidth) {
      const tr = getTransform(state.name, idx);
      // 픽셀퍼펙트 줄은 카드와 동일하게 격자 재양자화로 그린다 (프리뷰 = 굽기)
      drawFrameInto(ctx, image, tr, cw, ch, snapScaleFor(state.name));
    }
    posEl.textContent = `${pv.cursor + 1}/${play.length} · #${idx}`;
  };

  const step = (delta) => {
    pv.playing = false;
    syncPlayBtn();
    const play = playList(state.name);
    if (play.length) pv.cursor = (pv.cursor + delta + play.length) % play.length;
    draw();
  };
  root.querySelector(".pv-prev").addEventListener("click", () => step(-1));
  root.querySelector(".pv-next").addEventListener("click", () => step(1));
  playBtn.addEventListener("click", () => {
    pv.playing = !pv.playing;
    syncPlayBtn();
  });
  root.querySelector(".pv-speed").addEventListener("change", (e) => {
    pv.speed = parseFloat(e.target.value) || 1;
  });

  // Called after the selection/order changes (move between rows, reorder). Keeps
  // the on-screen frame in view instead of jumping (re-anchor by frame index),
  // and disables the transport when the sequence is empty (nothing to play).
  const prevBtn = root.querySelector(".pv-prev");
  const nextBtn = root.querySelector(".pv-next");
  pv.refresh = () => {
    const play = playList(state.name);
    if (!play.length) {
      pv.cursor = 0;
    } else {
      const p = play.indexOf(pv.shown);
      pv.cursor = p >= 0 ? p : ((pv.cursor % play.length) + play.length) % play.length;
    }
    const empty = play.length === 0;
    prevBtn.disabled = empty;
    nextBtn.disabled = empty;
    playBtn.disabled = empty;
    draw();
  };
  pv.refresh();

  function frame(ts) {
    const play = playList(state.name);
    if (pv.playing && play.length) {
      const interval = 1000 / Math.max(0.1, state.fps * pv.speed);
      if (ts - last >= interval) {
        last = ts;
        pv.cursor = (pv.cursor + 1) % play.length;
      }
    }
    draw();
    requestAnimationFrame(frame);
  }
  syncPlayBtn();
  requestAnimationFrame(frame);
}

// --- 확대 편집 모달 — 한 프레임을 크게 띄워 격자/픽셀퍼펙트를 켜가며 조정 -----
// 같은 entries/transform truth 를 쓰므로 모달 편집이 그리드 카드에 실시간 반영된다.
// 휠/핀치 = 화면 배율(뷰 확대), 드래그/핸들/Shift+휠 = 기존 스프라이트 편집 그대로.
let zoomView = null; // { stateName, idx, width }

function closeZoom() {
  const modal = document.getElementById("zoom-modal");
  if (modal) modal.remove();
  zoomView = null;
  document.removeEventListener("keydown", onZoomKey);
}

function onZoomKey(ev) {
  if (ev.key === "Escape") closeZoom();
  else if (ev.key === "ArrowLeft") stepZoomFrame(-1);
  else if (ev.key === "ArrowRight") stepZoomFrame(1);
}

function stepZoomFrame(delta) {
  if (!zoomView) return;
  const st = run.states.find((s) => s.name === zoomView.stateName);
  const present = st.frames.filter((f) => f.present).map((f) => f.index);
  if (!present.length) return;
  const pos = present.indexOf(zoomView.idx);
  openZoom(zoomView.stateName, present[(pos + delta + present.length) % present.length], zoomView.width);
}

function openZoom(stateName, idx, keepWidth) {
  closeZoom();
  const st = run.states.find((s) => s.name === stateName);
  const frame = st && st.frames.find((f) => f.index === idx);
  if (!frame || !frame.present) return;
  const aspect = run.cell.height / run.cell.width;
  const width = keepWidth
    || Math.min(Math.floor(window.innerWidth * 0.8), Math.floor((window.innerHeight * 0.72) / aspect));
  zoomView = { stateName, idx, width };

  const modal = document.createElement("div");
  modal.id = "zoom-modal";
  const label = frame.label ? escapeHtml(frame.label) : `#${idx}`;
  modal.innerHTML =
    `<div class="zoom-backdrop"></div>` +
    `<div class="card zoom-card" data-state="${escapeHtml(stateName)}" data-idx="${idx}">` +
    `<div class="zoom-head">` +
    `<span class="zoom-title">${escapeHtml(stateName)} · ${label}</span>` +
    `<span class="row-controls"></span>` +
    `<button type="button" class="ghost zoom-prev" title="${t("tZoomPrev")}">⏮</button>` +
    `<button type="button" class="ghost zoom-next" title="${t("tZoomNext")}">⏭</button>` +
    `<button type="button" class="ghost zoom-close">${t("zoomClose")}</button>` +
    `</div>` +
    `<div class="stage" title="${t("tZoomStage")}">` +
    `<div class="pxgrid"></div>` +
    `<canvas class="ingrid"></canvas>` +
    `<img src="${escapeHtml(frameUrl(stateName, frame))}" alt="frame ${idx}" draggable="false" class="px-upscale" />` +
    `<canvas class="snap-canvas"></canvas>` +
    `<div class="rotate-handle" title="${t("tRotate")}"></div>` +
    `<div class="shear-handle" title="${t("tShear")}"></div>` +
    `</div>` +
    `<div class="card-controls">` +
    `<span class="psize" title="${t("tContentPx")}">${frame.contentSize ? `${frame.contentSize[0]}x${frame.contentSize[1]}px` : ""}</span>` +
    `<span class="tvals"></span>` +
    `<button type="button" class="ghost flip-btn" title="${t("tFlipX")}" aria-label="flip-x">↔</button>` +
    `<button type="button" class="ghost reset-btn" title="${t("tReset")}">↺</button>` +
    `</div>` +
    `</div>`;
  document.body.appendChild(modal);

  const card = modal.querySelector(".zoom-card");
  card.style.setProperty("--cell-aspect", run.cell.width / run.cell.height);
  const stage = card.querySelector(".stage");
  stage.style.width = `${width}px`;

  // 컨트롤: 줄별 토글과 같은 클래스 → sync*Controls 가 카드/모달을 함께 갱신
  const controls = card.querySelector(".row-controls");
  if (gridCapableStates.has(stateName)) controls.appendChild(makeGridToggle(stateName));
  if (ppTwinStates.has(stateName)) controls.appendChild(makePpToggle(stateName));
  card.querySelector(".zoom-prev").addEventListener("click", () => stepZoomFrame(-1));
  card.querySelector(".zoom-next").addEventListener("click", () => stepZoomFrame(1));
  card.querySelector(".zoom-close").addEventListener("click", closeZoom);
  modal.querySelector(".zoom-backdrop").addEventListener("click", closeZoom);
  card.querySelector(".reset-btn").addEventListener("click", () => resetTransform(stateName, idx));
  card.querySelector(".flip-btn").addEventListener("click", () => toggleFlipX(stateName, idx));

  // 뷰 확대: 휠/핀치(ctrl+휠). wireStage 의 휠(스프라이트 스케일)보다 먼저 등록해
  // 가로채고, Shift+휠만 스프라이트 스케일로 통과시킨다.
  stage.addEventListener("wheel", (ev) => {
    if (ev.shiftKey) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
    zoomView.width = Math.min(Math.floor(window.innerWidth * 0.9),
      Math.max(120, Math.round(zoomView.width * factor)));
    stage.style.width = `${zoomView.width}px`;
    applyCardTransform(stage, stateName, idx);
    sizePxGrids();
  }, { passive: false });

  wireStage(stage, stateName, idx);
  applyCardTransform(stage, stateName, idx);
  syncPpControls();
  syncGridControls();
  sizePxGrids();
  document.addEventListener("keydown", onZoomKey);
}

// --- iso ground grid overlay -----------------------------------------------

function drawGroundGrid(stage) {
  const canvas = stage.querySelector(".grid-overlay");
  if (!canvas || !run.iso) return;
  const rect = stage.getBoundingClientRect();
  const W = Math.round(rect.width);
  const H = Math.round(rect.height);
  if (!W || !H) return;
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, W, H);

  // cell pixels -> displayed pixels
  const ds = W / run.cell.width;
  const tw = run.iso.tile.width * ds;   // diamond full width (2:1 -> width = 2*height)
  const th = run.iso.tile.height * ds;  // diamond full height
  const [ax, ay] = run.iso.anchor_pixel;
  const ox = ax * ds; // anchor in displayed px
  const oy = ay * ds;

  // grid-(gx,gy) center on screen, 2:1 dimetric, anchored at the meta anchor
  const center = (gx, gy) => [ox + (gx - gy) * (tw / 2), oy + (gx + gy) * (th / 2)];
  const diamond = (cx, cy) => {
    ctx.beginPath();
    ctx.moveTo(cx, cy - th / 2);
    ctx.lineTo(cx + tw / 2, cy);
    ctx.lineTo(cx, cy + th / 2);
    ctx.lineTo(cx - tw / 2, cy);
    ctx.closePath();
  };

  const R = 4;
  ctx.lineWidth = 1;
  for (let gx = -R; gx <= R; gx++) {
    for (let gy = -R; gy <= R; gy++) {
      const [cx, cy] = center(gx, gy);
      diamond(cx, cy);
      const anchorTile = gx === 0 && gy === 0;
      ctx.strokeStyle = anchorTile ? "rgba(37,99,235,0.9)" : "rgba(37,99,235,0.25)";
      ctx.stroke();
    }
  }
  // axis guide lines through the anchor (the true 2:1 slopes)
  ctx.strokeStyle = "rgba(217,119,6,0.9)";
  ctx.lineWidth = 1.5;
  for (const [sx, sy] of [[1, 1], [1, -1]]) {
    ctx.beginPath();
    ctx.moveTo(ox - sx * tw * 3, oy - sy * th * 3);
    ctx.lineTo(ox + sx * tw * 3, oy + sy * th * 3);
    ctx.stroke();
  }
}

// --- 사이드바 접기/펴기 (쿠마피커 도크 스타일, 상태는 localStorage 유지) ------
const sidebarToggle = document.getElementById("sidebar-toggle");
// topbar 실측 높이 → 사이드바 sticky 오프셋/전체높이 계산에 주입.
// 라벨/폰트가 늦게 차면서 높이가 변하므로 ResizeObserver 로 추적한다 (일회 측정 금지).
{
  const topbar = document.querySelector(".topbar");
  if (topbar) {
    const sync = () =>
      document.documentElement.style.setProperty("--topbar-h", `${topbar.offsetHeight}px`);
    sync();
    new ResizeObserver(sync).observe(topbar);
  }
}
function applySidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  try { localStorage.setItem("curator-sidebar-collapsed", collapsed ? "1" : ""); } catch { /* private mode */ }
}
if (sidebarToggle) {
  sidebarToggle.addEventListener("click", () =>
    applySidebarCollapsed(!document.body.classList.contains("sidebar-collapsed")));
  let saved = "";
  try { saved = localStorage.getItem("curator-sidebar-collapsed") || ""; } catch { /* private mode */ }
  applySidebarCollapsed(saved === "1");
}

const gridToggle = document.getElementById("grid-toggle");
const langToggle = document.getElementById("lang-toggle");
gridToggle.addEventListener("click", () => {
  const on = document.body.classList.toggle("show-grid");
  gridToggle.textContent = `${t("groundGrid")} ${on ? "▣" : "▢"}`;
  if (on) document.querySelectorAll(".stage").forEach(drawGroundGrid);
});

// language toggle reloads with ?lang= so preview rAF loops are not duplicated
langToggle.addEventListener("click", () => {
  const next = lang === "en" ? "ko" : "en";
  const u = new URL(location.href);
  u.searchParams.set("lang", next);
  location.href = u.toString();
});

function applyStaticLang() {
  document.getElementById("t-title").textContent = t("title");
  document.getElementById("compose").textContent = t("compose");
  document.getElementById("export").textContent = t("export");
  document.getElementById("export-gif").textContent = t("exportGif");
  gridToggle.textContent = `${t("groundGrid")} ${document.body.classList.contains("show-grid") ? "▣" : "▢"}`;
  langToggle.textContent = t("langOther");
  const ppLabel = document.getElementById("pp-label");
  if (ppLabel) ppLabel.textContent = t("ppApply");
  const pxLabel = document.getElementById("pxgrid-label");
  if (pxLabel) pxLabel.textContent = t("pxGridAll") + (run.pixelPerfect && run.pixelPerfect.label ? " \u00b7 " + run.pixelPerfect.label : "");
  const pxWrap = document.getElementById("pxgrid-wrap");
  if (pxWrap) pxWrap.title = t("tPxGrid");
  const ppWrap = document.getElementById("pp-wrap");
  if (ppWrap) ppWrap.title = t("tPpApply");
  document.getElementById("hintbar").innerHTML = t("hints").map((h) => `<span>${h}</span>`).join("");
}

// --- compose ---------------------------------------------------------------

document.getElementById("compose").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  clearTimeout(saveTimer);
  await save();
  setStatus(t("baking"));
  try {
    const res = await fetch("/api/compose", { method: "POST" });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error((data.stderr || data.error || "compose failed").trim());
    setStatus(t("composeDone"), "ok");
  } catch (e) {
    setStatus(t("composeFail") + e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("export").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  clearTimeout(saveTimer);
  await save();
  setStatus(t("exporting"));
  try {
    const res = await fetch("/api/export", { method: "POST" });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error((data.stderr || data.error || "export failed").trim());
    const out = data.export || {};
    setStatus(STR[lang].exportDone(out.count || 0), "ok");
  } catch (e) {
    setStatus(t("exportFail") + e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("export-gif").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  clearTimeout(saveTimer);
  await save();
  setStatus(t("exporting"));
  try {
    const res = await fetch("/api/export-gif", { method: "POST" });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error((data.stderr || data.error || "gif export failed").trim());
    const gif = data.gif || {};
    setStatus(STR[lang].exportGifDone((gif.exports || []).length), "ok");
  } catch (e) {
    setStatus(t("exportFail") + e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

// --- bootstrap -------------------------------------------------------------

function seedEntries() {
  entries = {};
  const curated = (run.curation && run.curation.states) || {};
  for (const state of run.states) {
    const present = state.frames.filter((f) => f.present).map((f) => f.index);
    const c = curated[state.name];
    // order = full display arrangement (sequence then pool); sel = which are on.
    // Coerce to integers and de-dupe so a hand-edited / corrupt sidecar (string
    // indices, duplicates) can't produce a duplicated or dropped frame.
    const missing = state.frames.filter((f) => !f.present).map((f) => f.index);
    const allIdx = [...present, ...missing];
    const coerce = (arr, valid) => {
      const seen = new Set();
      const out = [];
      for (const raw of Array.isArray(arr) ? arr : []) {
        const i = Number(raw);
        if (Number.isInteger(i) && valid.includes(i) && !seen.has(i)) {
          seen.add(i);
          out.push(i);
        }
      }
      return out;
    };
    const savedSel = c && Array.isArray(c.selected) ? coerce(c.selected, present) : [];
    const savedOrder = c && Array.isArray(c.order) ? coerce(c.order, allIdx) : [];
    let order;
    if (savedOrder.length) {
      // restore the exact saved arrangement (incl. pool order); append any
      // newly-extracted frames that weren't in the saved order.
      const seen = new Set(savedOrder);
      order = [...savedOrder, ...allIdx.filter((i) => !seen.has(i))];
    } else if (savedSel.length) {
      // older sidecar without `order`: selected leads, the rest trail.
      const inSel = new Set(savedSel);
      order = [...savedSel, ...present.filter((i) => !inSel.has(i)), ...missing];
    } else {
      order = allIdx;
    }
    const sel = savedSel.length ? new Set(savedSel) : new Set(present);
    const transforms = {};
    if (c && c.transforms) {
      for (const [idx, t] of Object.entries(c.transforms)) {
        transforms[idx] = { ...IDENTITY(), ...t };
      }
    }
    entries[state.name] = { order, sel, transforms };
  }
}

async function boot() {
  try {
    const res = await fetch("/api/run");
    run = await res.json();
    if (run.error) throw new Error(run.error);
  } catch (e) {
    document.getElementById("states").innerHTML =
      `<div class="fatal">${t("runLoadFail")}\n${e.message}</div>`;
    return;
  }
  // initial language: ?lang= (set by the toggle) overrides the server --lang
  lang = new URLSearchParams(location.search).get("lang") || run.lang || "en";
  document.documentElement.lang = lang;
  // pixel-perfect twin state must resolve BEFORE first render (frameUrl reads it):
  // per-state truth = states.<state>.pixel_perfect override > run-wide default > on.
  ppTwinStates = new Set(run.states.filter((s) => s.frames.some((f) => f.plainUrl)).map((s) => s.name));
  ppAvailable = ppTwinStates.size > 0;
  const ppDefault = !(run.curation && run.curation.pixel_perfect === false);
  ppStates = {};
  for (const s of run.states) {
    const c = run.curation && run.curation.states && run.curation.states[s.name];
    ppStates[s.name] = c && typeof c.pixel_perfect === "boolean" ? c.pixel_perfect : ppDefault;
  }
  // 격자 오버레이 가능 줄(계약 scale 또는 줄별 측정 피치) — 표시 전용, 저장 안 함, 기본 off
  const contractScale = run.pixelPerfect && run.pixelPerfect.scale;
  gridCapableStates = new Set(run.states.filter((s) => s.pixelScale || contractScale).map((s) => s.name));
  gridStates = {};
  applyStaticLang();
  document.getElementById("character").textContent = `${run.characterId} · ${run.cell.width}×${run.cell.height}`;
  if (run.iso) gridToggle.hidden = false;
  if (ppAvailable) {
    const ppWrap = document.getElementById("pp-wrap");
    const ppCheck = document.getElementById("pp-apply");
    ppWrap.hidden = false;
    // 전체 토글: 클릭 = 쌍둥이 있는 모든 줄을 새 값으로 일괄 설정. 줄들이 섞여
    // 있으면(일부 on/일부 off) indeterminate 로 표시한다 (syncPpControls).
    ppCheck.addEventListener("change", () => {
      const on = ppCheck.checked;
      for (const n of ppTwinStates) ppStates[n] = on;
      syncPpControls();
      refreshVariantImages();
      scheduleSave();
    });
  }
  // 픽셀 격자 전체 토글 — 표시 전용 오버레이 (굽기와 무관), 줄별 체크박스와 같은 truth.
  // 격자를 알 수 있는 줄이 하나도 없으면 감춘다 (가짜 격자를 보여주지 않는다).
  const pxWrap = document.getElementById("pxgrid-wrap");
  const pxCheck = document.getElementById("pxgrid-check");
  pxWrap.hidden = gridCapableStates.size === 0;
  pxCheck.addEventListener("change", () => {
    const on = pxCheck.checked;
    for (const n of gridCapableStates) gridStates[n] = on;
    syncGridControls();
    sizePxGrids();
  });
  seedEntries();
  if (run.directionGroups && run.directionGroups.length) {
    anchorStates = new Set(run.directionGroups.map((g) => g.anchor).filter(Boolean));
  }
  await seedTreeProgress();
  renderPipelineTree();
  setInterval(pollTreeProgress, 3000);
  if (run.baseUrl) renderBaseRow();
  // 방향 계약 런: 방향별 그룹(앵커 우선) + 미러 방향(생성 생략) 스트립으로 렌더.
  // 계약 없는 런은 기존 flat 순서 그대로.
  if (run.directionGroups && run.directionGroups.length) {
    const byName = new Map(run.states.map((s) => [s.name, s]));
    const rendered = new Set();
    for (const group of run.directionGroups) {
      const headEl = document.createElement("div");
      headEl.className = "dir-head" + (group.mirrorOf ? " dir-mirror" : "");
      headEl.textContent = group.mirrorOf
        ? STR[lang].dirMirrorLabel(group.direction, group.mirrorOf)
        : STR[lang].dirGroupLabel(group.direction);
      document.getElementById("states").appendChild(headEl);
      for (const name of group.states) {
        const st = byName.get(name);
        if (st) { renderState(st); rendered.add(name); }
      }
    }
    // 방향 접두사에 안 걸린 잔여 상태는 끝에 그대로 (숨기지 않는다)
    for (const state of run.states) if (!rendered.has(state.name)) renderState(state);
  } else {
    for (const state of run.states) renderState(state);
  }
  syncPpControls();
  syncGridControls();
  refreshVariantImages();
  // 힌트바를 우측 본문 컬럼 끝으로 이동 — 좌측 스플릿이 페이지 바닥까지 유지되게
  document.getElementById("states").appendChild(document.getElementById("hintbar"));
  setStatus(run.curation && Object.keys(run.curation.states || {}).length ? t("loaded") : t("ready"));
}

boot();
