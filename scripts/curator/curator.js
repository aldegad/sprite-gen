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
    ppApply: "Pixel-perfect", baseNote: "identity reference — not baked",
    pxGrid: "Pixel-perfect grid", tPxGrid: "overlay the logical pixel grid the pixel-perfect stage snapped to (display only)",
    tPpApply: "bake and show the pixel-perfected frames (off = the pre-pixel-perfect originals)",
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
    hints: ["drag card header = reorder / move row", "drag pool→sequence to add", "wheel = scale", "top handle = rotate", "click card = sequence ⇄ pool", "saved automatically"],
    exportDone: (n) => `${n} PNGs → curated/`,
    exportGifDone: (n) => `${n} GIFs → exports/`,
  },
  ko: {
    title: "큐레이션", compose: "아틀라스 굽기", export: "PNG 내보내기", exportGif: "GIF 내보내기",
    groundGrid: "바닥 그리드", langOther: "EN",
    ppApply: "픽셀퍼펙트 적용", baseNote: "원본 베이스 (아이덴티티 참조 — 굽기와 무관)",
    pxGrid: "픽셀퍼펙트 격자", tPxGrid: "픽셀퍼펙트가 실제로 스냅한 논리 픽셀 격자 오버레이 (표시 전용)",
    tPpApply: "체크 = 픽셀퍼펙트 프레임 표시·굽기, 해제 = 적용 전 원본 표시·굽기",
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
let ppAvailable = false; // any frame has a plain (pre-pixel-perfect) twin
let ppApply = true;      // curation.json `pixel_perfect` — what compose bakes
let ppView = "pixel";    // which variant the cards/preview display right now

function frameUrl(frame) {
  return ppView === "plain" && frame.plainUrl ? frame.plainUrl : frame.url;
}

// 픽셀퍼펙트 격자 오버레이: 픽셀퍼펙트가 실제로 스냅한 논리 픽셀 간격을 그린다.
// run.pixelPerfect.scale = 논리 픽셀 1칸이 차지하는 셀 픽셀 수 (extract 의 pp_scale).
// 예전엔 셀 픽셀마다(scale 무시) 그어서, logical_height < cell 인 런에서 실제 스냅
// 격자보다 촘촘한 거짓 격자를 보여줬다. 픽셀퍼펙트가 아닌 런은 격자 자체가 없다.
function sizePxGrids() {
  const step = run.pixelPerfect ? run.pixelPerfect.scale : 1;
  document.querySelectorAll(".stage").forEach((stage) => {
    const overlay = stage.querySelector(".pxgrid");
    if (!overlay) return;
    const ds = (stage.clientWidth / run.cell.width) * step;
    overlay.style.backgroundSize = `${ds}px ${ds}px`;
  });
}

function refreshVariantImages() {
  document.querySelectorAll(".card").forEach((card) => {
    const st = run.states.find((s) => s.name === card.dataset.state);
    const f = st && st.frames[Number(card.dataset.idx)];
    const el = card.querySelector(".stage img");
    if (f && el) el.src = frameUrl(f);
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
  }
  const payload = { version: run.schemaVersion || 1, kind: "sprite-gen-curation", states };
  // only meaningful when the run saved plain twins — the top-right checkbox
  if (ppAvailable) payload.pixel_perfect = ppApply;
  return payload;
}

function scheduleSave() {
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
  // CSS matrix(a,b,c,d,e,f): a=m00 b=m10 c=m01 d=m11; translate applied after, about center.
  el.style.transform =
    `translate(${t.dx * ds}px, ${t.dy * ds}px) matrix(${m.m00}, ${m.m10}, ${m.m01}, ${m.m11}, 0, 0)`;
  const sh = t.shx || t.shy ? ` sh${(t.shx || 0).toFixed(2)},${(t.shy || 0).toFixed(2)}` : "";
  const flip = t.flipX ? " ↔" : "";
  const card = stage.closest(".card");
  card.querySelector(".tvals").textContent =
    `r${t.rotate.toFixed(0)}° ×${t.scale.toFixed(2)} ${t.dx >= 0 ? "+" : ""}${t.dx.toFixed(0)},${t.dy >= 0 ? "+" : ""}${t.dy.toFixed(0)}${sh}${flip}`;
  const flipBtn = card.querySelector(".flip-btn");
  if (flipBtn) flipBtn.classList.toggle("active", !!t.flipX);
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
      applyCardTransform(stage, stateName, idx);
    };
    const onUp = () => {
      stage.releasePointerCapture(ev.pointerId);
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("pointerup", onUp);
      if (!moved) {
        // a click (not a drag) sends the frame to the other row
        moveCardToOtherZone(stage.closest(".card"), stateName);
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
      applyCardTransform(stage, stateName, idx);
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
      applyCardTransform(stage, stateName, idx);
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
      applyCardTransform(stage, stateName, idx);
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

function resetTransform(stateName, idx, stage) {
  entries[stateName].transforms[idx] = IDENTITY();
  applyCardTransform(stage, stateName, idx);
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

  const head = document.createElement("div");
  head.className = "state-head";
  head.innerHTML =
    `<span class="name">${escapeHtml(state.name)}</span>` +
    `<span class="meta">${state.requestFrames} ${t("frames")} · ${state.fps}fps · ${state.loop ? t("loop") : t("nonLoop")} · ${t("cellPx")} ${run.cell.width}x${run.cell.height}px</span>` +
    (state.action ? `<span class="action">${escapeHtml(state.action)}</span>` : "") +
    (state.extractOk ? "" : `<span class="state-warn">${t("extractFail")}</span>`);
  wrap.appendChild(head);

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

// 최상단 base 참조 줄 — 아이덴티티 truth 를 생성 결과와 나란히 비교하기 위한
// 읽기 전용 표시 (선택/변형/굽기와 무관).
function renderBaseRow() {
  const wrap = document.createElement("section");
  wrap.className = "state base-row";
  wrap.innerHTML =
    `<div class="state-head"><h3>base</h3>` +
    `<span class="muted">${t("baseNote")}</span></div>` +
    `<div class="base-stage"><img src="${run.baseUrl}" alt="base source" draggable="false" /></div>`;
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
      `<img src="${frameUrl(frame)}" alt="frame ${frame.index}" draggable="false" />` +
      `<div class="rotate-handle" title="${t("tRotate")}"></div>` +
      `<div class="shear-handle" title="${t("tShear")}"></div>`
    : `<div class="missing-label">missing</div>`;

  const label = frame.label ? escapeHtml(frame.label) : `#${frame.index}`;
  card.innerHTML =
    `<div class="card-top">` +
    `<span class="ct-left">` +
    (frame.present ? `<span class="grip" title="${t("tReorder")}" aria-label="reorder">⠿</span>` : "") +
    `<span class="idx" title="frame ${frame.index}">${label}</span>` +
    `</span>` +
    `<button type="button" class="ghost sel-btn">${t("excluded")}</button>` +
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
      resetTransform(state.name, frame.index, card.querySelector(".stage"))
    );
    card.querySelector(".flip-btn").addEventListener("click", () =>
      toggleFlipX(state.name, frame.index, card.querySelector(".stage"))
    );
  }
  return card;
}

/** Toggle horizontal flip for a single frame (Alex 2026-05-28). */
function toggleFlipX(stateName, idx, stage) {
  const entry = entries[stateName];
  if (!entry) return;
  if (!entry.transforms[idx]) entry.transforms[idx] = IDENTITY();
  entry.transforms[idx].flipX = entry.transforms[idx].flipX ? 0 : 1;
  // applyCardTransform renders the mirror and highlights the flip button.
  applyCardTransform(stage, stateName, idx);
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
    `<select class="pv-speed" name="speed-${state.name}" aria-label="${t("tSpeed")}" title="${t("tSpeed")}">${speedOpts}</select>` +
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
    const image = f ? img(frameUrl(f)) : null;
    if (image && image.complete && image.naturalWidth) {
      const tr = getTransform(state.name, idx);
      const m = matrixOf(tr);
      ctx.save();
      ctx.translate(cw / 2 + tr.dx, ch / 2 + tr.dy);
      ctx.transform(m.m00, m.m10, m.m01, m.m11, 0, 0);
      ctx.drawImage(image, -cw / 2, -ch / 2, cw, ch);
      ctx.restore();
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
  if (pxLabel) pxLabel.textContent = t("pxGrid");
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
  // pixel-perfect twin state must resolve BEFORE first render (frameUrl reads it)
  ppAvailable = run.states.some((s) => s.frames.some((f) => f.plainUrl));
  ppApply = !(run.curation && run.curation.pixel_perfect === false);
  ppView = ppApply ? "pixel" : "plain";
  applyStaticLang();
  document.getElementById("character").textContent = `${run.characterId} · ${run.cell.width}×${run.cell.height}`;
  if (run.iso) gridToggle.hidden = false;
  if (ppAvailable) {
    const ppWrap = document.getElementById("pp-wrap");
    const ppCheck = document.getElementById("pp-apply");
    ppWrap.hidden = false;
    ppCheck.checked = ppApply;
    // 체크박스 하나가 표시와 굽기를 함께 결정한다 (별도 보기 토글 없음).
    ppCheck.addEventListener("change", () => {
      ppApply = ppCheck.checked;
      ppView = ppApply ? "pixel" : "plain";
      refreshVariantImages();
      scheduleSave();
    });
  }
  // 픽셀퍼펙트 격자 체크박스 — 표시 전용 오버레이 (굽기와 무관).
  // 픽셀퍼펙트가 아닌 런에는 스냅 격자가 없다 → 토글을 감춘다 (가짜 격자를 보여주지 않는다).
  const pxWrap = document.getElementById("pxgrid-wrap");
  const pxCheck = document.getElementById("pxgrid-check");
  pxWrap.hidden = !run.pixelPerfect;
  pxCheck.addEventListener("change", () => {
    document.body.classList.toggle("show-pxgrid", pxCheck.checked);
    if (pxCheck.checked) sizePxGrids();
  });
  seedEntries();
  if (run.baseUrl) renderBaseRow();
  for (const state of run.states) renderState(state);
  refreshVariantImages();
  sizePxGrids();
  setStatus(run.curation && Object.keys(run.curation.states || {}).length ? t("loaded") : t("ready"));
}

boot();
