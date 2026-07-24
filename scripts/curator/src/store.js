// SPDX-License-Identifier: Apache-2.0
// curator/store.js — 런 스냅샷 + 큐레이션 인메모리 모델 (run/entries/프레임·복제 해석) — 클라이언트 상태 SSoT
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

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

// 트윈 없는 줄의 온디맨드 스냅 프리뷰 (서버 .pixel-preview 캐시) — 임포트 세트나
// 비 pp 런에서도 픽셀퍼펙트 토글이 동작한다 (표시 전용, 굽기/저장과 무관).
let ppPreviewStates = new Set();

let ppStates = {};             // stateName -> bool (true = pixel-perfect variant)

// Same per-state + toggle-all shape as pixel-perfect: each grid-capable row has
// its own checkbox, the header checkbox sets all rows at once.
let gridCapableStates = new Set(); // 전 상태 (격자는 항상 정의됨) — 일괄 토글 대상 집합

let gridStates = {};               // stateName -> bool (overlay shown)

let anchorStates = new Set();      // direction-anchor states (directionGroups runs)

let pixelEdit = null; // 모달 편집 세션: {state, idx, tool: 'pen'|'eraser', color, journal: []}

// 편집 truth 인덱스 (수홍 확정 2026-07-18): 링크된 복제의 편집 SSoT 는 원본
// 프레임 하나다 — 복제는 재생 슬롯일 뿐. '링크 끊기' 한 복제(unlinked)만 자기
// 편집을 소유한다. 모든 편집 읽기/쓰기는 이 리졸버를 거친다.
function editIndex(stateName, idx) {
  const e = entries[stateName];
  const src = e && e.clones ? e.clones[idx] : undefined;
  if (src === undefined) return idx;
  return e.unlinked && e.unlinked.has(idx) ? idx : src;
}

function isLinkedClone(stateName, idx) {
  const e = entries[stateName];
  return !!(e && e.clones && e.clones[idx] !== undefined && !(e.unlinked && e.unlinked.has(idx)));
}

function getPixelOps(stateName, idx) {
  const e = entries[stateName];
  if (!e || !e.pixels) return null;
  const ops = e.pixels[editIndex(stateName, idx)];
  return ops && Object.keys(ops).length ? ops : null;
}

function ppOn(stateName) {
  return ppStates[stateName] !== false;
}

function frameUrl(stateName, frame) {
  if (frame.plainUrl) return !ppOn(stateName) ? frame.plainUrl : frame.url;
  // 트윈 없는 프레임: pp ON = 온디맨드 스냅 프리뷰 (없으면 원본 그대로)
  return ppOn(stateName) && frame.pixelPreviewUrl ? frame.pixelPreviewUrl : frame.url;
}

// 복제 인스턴스 (entries[state].clones = {복제idx: 원본idx}) 인식 프레임 조회.
// 복제 카드는 원본의 frame 객체(이미지 URL/크기)를 빌리되 자기 인덱스로 표시된다.
// 서버/스키마 계약: 파일은 원본을 읽고, 변형/픽셀편집/순서는 복제 인덱스 소유.
// 프레임의 유니크 표시명: 테이크 라벨(blink#2) 우선, 없으면 #인덱스.
// 라벨은 원본(primary) 프레임에선 비어 있어 유니크하지 않지만, 인덱스는 줄 안에서
// 항상 유니크하므로 이 조합이 카드 표시명의 SSoT 다 (복제 배지도 이걸 가리킨다).
function frameDisplayName(stateName, idx) {
  const st = run.states.find((s) => s.name === stateName);
  const f = st && st.frames.find((x) => x.index === idx && x.clone === undefined);
  return f && f.label ? f.label : `#${idx}`;
}

function cloneSrc(stateName, idx) {
  const e = entries[stateName];
  const src = e && e.clones ? e.clones[idx] : undefined;
  return src === undefined ? null : src;
}

function frameOf(stateName, idx) {
  if (stateName === BASE_STATE) {
    if (!baseView) return null;
    return { index: 0, present: true, url: baseView.url, plainUrl: baseView.rawUrl,
             label: "base", contentSize: [baseView.cols, baseView.rows] };
  }
  const st = run.states.find((s) => s.name === stateName);
  if (!st) return null;
  const src = cloneSrc(stateName, idx);
  const f = st.frames.find((fr) => fr.index === (src === null ? idx : src));
  if (!f) return null;
  if (src === null) return f;
  return { ...f, index: idx, clone: src, label: null };
}

// fit.pixel_perfect 런에서 픽셀 변형을 표시 중인 줄의 논리 격자 스케일 (아니면 null).
// 굽기(curation.apply_transform snap_scale)와 같은 조건 — 프리뷰가 굽기를 거울처럼 따른다.
function snapScaleFor(stateName) {
  if (stateName === BASE_STATE) return 1; // 베이스 논리 공간 = 캔버스 1:1
  return run.fitPixelPerfect && run.pixelPerfect && run.pixelPerfect.scale && ppOn(stateName)
    ? run.pixelPerfect.scale
    : null;
}

function isIdentityTransform(t) {
  return !t.rotate && t.scale === 1 && !t.dx && !t.dy && !t.shx && !t.shy && !t.flipX;
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
  const key = editIndex(stateName, idx); // 링크 복제 = 원본 truth 공유 (쓰기도 이 객체로)
  if (!t[key]) t[key] = IDENTITY();
  return t[key];
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

// ── 베이스 = 줌 모달과 같은 컴포넌트 (수홍 지시 2026-07-17 "같은 컴포넌트를 쓰라") ──
// 가상 상태 "__base__" 로 줌 모달을 연다. 편집 공간 = 검출 격자의 논리 해상도
// (예: 28×48) — 프레임과 동일한 조작감. 저장은 사이드카가 아니라 명시 버튼으로
// /api/base-edit (space:"logical") 에 굽고, 서버가 논리→raw 블록 확장을 맡는다.
const BASE_STATE = "__base__";

let baseView = null; // {cols, rows, xEdges, yEdges, url(논리 PNG), rawUrl} — 베이스 뷰 상태

let baseLogicalImg = null; // 논리 이미지 엘리먼트 — 편집/합성/샘플의 단일 소스

// 편집·합성 소스: 베이스는 항상 논리 이미지 (pp OFF 로 raw 를 "보고" 있어도
// 편집 공간은 논리 격자 — 균일 좌표가 이미지와 어긋나는 문제의 원천 차단).
function editSourceFor(stateName, imgEl) {
  return stateName === BASE_STATE && baseLogicalImg ? baseLogicalImg : imgEl;
}

function cellDims(stateName) {
  if (stateName === BASE_STATE && baseView) return [baseView.cols, baseView.rows];
  return [run.cell.width, run.cell.height];
}

function seedEntries() {
  entries = {};
  const curated = (run.curation && run.curation.states) || {};
  for (const state of run.states) {
    const physPresent = state.frames.filter((f) => f.present).map((f) => f.index);
    const c = curated[state.name];
    // 복제 인스턴스: {복제idx: 원본idx}. 복제idx 는 물리 범위 밖 정수, 원본은 물리
    // 프레임이어야 한다 (손상 항목 스킵). 원본이 present 면 복제도 present 취급.
    const physIdxSet = new Set(state.frames.map((f) => f.index));
    const clones = {};
    if (c && c.clones && typeof c.clones === "object") {
      for (const [k, v] of Object.entries(c.clones)) {
        const ci = Number(k);
        const src = Number(v);
        if (Number.isInteger(ci) && Number.isInteger(src) && !physIdxSet.has(ci) && physIdxSet.has(src)) clones[ci] = src;
      }
    }
    const cloneIdx = Object.keys(clones).map(Number);
    const present = [...physPresent, ...cloneIdx.filter((ci) => physPresent.includes(clones[ci]))];
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
    const archived = c && Array.isArray(c.deleted) ? coerce(c.deleted, allIdx) : [];
    const archivedSet = new Set(archived);
    const savedSel = (c && Array.isArray(c.selected) ? coerce(c.selected, present) : []).filter((i) => !archivedSet.has(i));
    const savedOrder = (c && Array.isArray(c.order) ? coerce(c.order, allIdx) : []).filter((i) => !archivedSet.has(i));
    let order;
    if (savedOrder.length) {
      // restore the exact saved arrangement (incl. pool order); append any
      // newly-extracted frames that weren't in the saved order.
      const seen = new Set([...savedOrder, ...archived]);
      order = [...savedOrder, ...allIdx.filter((i) => !seen.has(i))];
    } else if (savedSel.length) {
      // older sidecar without `order`: selected leads, the rest trail.
      const inSel = new Set(savedSel);
      order = [...savedSel, ...present.filter((i) => !inSel.has(i) && !archivedSet.has(i)),
               ...missing.filter((i) => !archivedSet.has(i))];
    } else {
      order = allIdx.filter((i) => !archivedSet.has(i));
    }
    const sel = savedSel.length ? new Set(savedSel) : new Set(present.filter((i) => !archivedSet.has(i)));
    const transforms = {};
    if (c && c.transforms) {
      for (const [idx, t] of Object.entries(c.transforms)) {
        transforms[idx] = { ...IDENTITY(), ...t };
      }
    }
    const pixels = {};
    if (c && c.pixels && typeof c.pixels === "object") {
      for (const [k, v] of Object.entries(c.pixels)) {
        const i = Number(k);
        if (Number.isInteger(i) && v && typeof v === "object" && Object.keys(v).length) pixels[i] = { ...v };
      }
    }
    const unlinked = new Set();
    if (c && Array.isArray(c.unlinked)) {
      for (const raw of c.unlinked) {
        const ci = Number(raw);
        if (clones[ci] !== undefined) unlinked.add(ci);
      }
    }
    // 레거시 자가판정: 링크 개념 도입(2026-07-18) 이전에 복제에 직접 넣은 변형/픽셀
    // 편집은 독립 의도였다 — 자동 unlinked 로 보존 (조용한 편집 소실 금지)
    for (const ci of Object.keys(clones).map(Number)) {
      if (unlinked.has(ci)) continue;
      const hasOwn = (c && c.transforms && c.transforms[ci]) || (c && c.pixels && c.pixels[ci] && Object.keys(c.pixels[ci]).length);
      if (hasOwn) unlinked.add(ci);
    }
    const names = {};
    if (c && c.names && typeof c.names === "object") {
      for (const [k, v] of Object.entries(c.names)) {
        const i = Number(k);
        if (Number.isInteger(i) && typeof v === "string" && v.trim()) names[i] = v.trim().slice(0, 24);
      }
    }
    entries[state.name] = { order, sel, transforms, archived, pixels, clones, unlinked, names,
      // 호흡 후처리 레이어 (사이드카 breathe — curation.state_breathe 와 같은 형태)
      breathe: c && c.breathe && Array.isArray(c.breathe.splits) && c.breathe.splits.length
        ? { splits: c.breathe.splits.map(Number).sort((a, b) => a - b).slice(0, 3),
            amplitude: Math.max(1, Math.min(4, Number(c.breathe.amplitude) || 1)),
            breaths: Math.max(1, Math.min(8, Number(c.breathe.breaths) || 1)),
            subpixel: !!c.breathe.subpixel }
        : null };
  }
}
