// SPDX-License-Identifier: Apache-2.0
// curator/region-transform.js — 선택한 사각 영역만 이동·회전·비틀기.
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// **왜 픽셀로 굽나** (수홍 지시 2026-07-26 "일부 영역만 비틀기 회전 이동"):
// 사이드카 `transforms[idx]` 는 셀 **전체** 표시 변형이라 영역을 표현할 수 없다.
// 새 스키마(`region_transforms`)를 넣으면 Python bake·마이그레이션·오라클이 전부
// 따라와야 한다. 대신 확정 시점에 결과를 래스터화해 기존 `pixels[idx]` 로 쓴다 —
// 스키마 0 변경, bake 경로 그대로, undo/redo 도 기존 픽셀 히스토리를 탄다.
//
// 대가: 영역 변형은 **비가역 래스터**다(전체 변형처럼 파라미터로 안 남는다).
// 확정 전엔 미리보기라 자유롭게 되돌리고, 확정 후엔 픽셀 undo 로 되돌린다.
// 도트 편집기의 자유변형이 원래 이 계약이다.

// 진행 중인 영역 변형. null 이면 미리보기 없음.
//   { state, idx, rect:{x,y,w,h}, src:[{x,y,hex}], t:{dx,dy,rotate,shx,shy}, canvas }
let regionEdit = null;

function regionActive(stateName, idx) {
  return !!regionEdit && regionEdit.state === stateName && regionEdit.idx === idx;
}

// 선택 영역의 **현재 보이는** 픽셀을 읽어 둔다 — 원본 프레임이 아니라 픽셀 편집까지
// 합성된 상태여야 한다(연필로 고친 뒤 그 영역을 옮기면 고친 게 따라가야 한다).
function regionCapture(stage, stateName, idx, rect) {
  const [cw, ch] = cellDims(stateName);
  const img = stage.querySelector("img");
  if (!img || !img.naturalWidth) return null;
  const buf = document.createElement("canvas");
  buf.width = cw;
  buf.height = ch;
  const ctx = buf.getContext("2d", { willReadFrequently: true });
  ctx.imageSmoothingEnabled = false;
  // 변형 이전 공간이다 — 픽셀 op 과 같은 좌표계(셀 픽셀). IDENTITY 로 그려야 맞는다.
  drawFrameInto(ctx, editSourceFor(stateName, img), IDENTITY(), cw, ch, null,
    getPixelOps(stateName, idx), 1);
  const src = [];
  const data = ctx.getImageData(0, 0, cw, ch).data;
  // 올가미 선택이면 `rect` 는 바운딩 박스일 뿐이고 실제 모양은 `mask` 다 —
  // 마스크 밖 픽셀을 집으면 올가미가 사각으로 퇴화한다.
  const mask = rect.mask || null;
  for (let y = rect.y; y < rect.y + rect.h; y += 1) {
    for (let x = rect.x; x < rect.x + rect.w; x += 1) {
      if (x < 0 || y < 0 || x >= cw || y >= ch) continue;
      if (mask && !mask.has(`${x},${y}`)) continue;
      const o = (y * cw + x) * 4;
      const a = data[o + 3];
      if (!a) continue;
      src.push({ x, y, hex: rgbaHex(data[o], data[o + 1], data[o + 2], a) });
    }
  }
  return src;
}

function rgbaHex(r, g, b, a) {
  const h = (n) => n.toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}${a < 255 ? h(a) : ""}`;
}

// 변형 결과를 셀 좌표 맵으로 계산한다. 역사상으로 목적지를 훑어 구멍을 막는다.
function regionResolve(reg) {
  const { rect, t } = reg;
  const ox = rect.x + rect.w / 2;
  const oy = rect.y + rect.h / 2;
  const inv = tpInverseSample(t, ox, oy);
  if (!inv) return new Map();
  const byXY = new Map();
  for (const p of reg.src) byXY.set(`${p.x},${p.y}`, p.hex);
  // 목적지 탐색 범위 = 원 사각을 변형한 것의 여유 있는 바운딩 박스.
  const pad = Math.ceil(Math.max(rect.w, rect.h) * 0.75) + 2;
  const out = new Map();
  const x0 = Math.floor(rect.x + (t.dx || 0)) - pad;
  const y0 = Math.floor(rect.y + (t.dy || 0)) - pad;
  const x1 = Math.ceil(rect.x + rect.w + (t.dx || 0)) + pad;
  const y1 = Math.ceil(rect.y + rect.h + (t.dy || 0)) + pad;
  for (let dy = y0; dy < y1; dy += 1) {
    for (let dx = x0; dx < x1; dx += 1) {
      const [sx, sy] = inv(dx + 0.5, dy + 0.5);
      const hex = byXY.get(`${Math.floor(sx)},${Math.floor(sy)}`);
      if (hex) out.set(`${dx},${dy}`, hex);
    }
  }
  return out;
}

// 미리보기 — 스테이지 위 오버레이 캔버스에 결과만 그린다. 사이드카는 안 건드린다.
function regionRenderPreview(stage, stateName) {
  if (!regionEdit) return;
  const [cw, ch] = cellDims(stateName);
  let cv = regionEdit.canvas;
  if (!cv) {
    cv = document.createElement("canvas");
    cv.className = "region-preview";
    stage.appendChild(cv);
    regionEdit.canvas = cv;
  }
  cv.width = cw;
  cv.height = ch;
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cw, ch);
  // 원본 자리는 비워 보여야 "옮겼다" 가 읽힌다.
  ctx.fillStyle = "rgba(0,0,0,0)";
  for (const [key, hex] of regionResolve(regionEdit)) {
    const [x, y] = key.split(",").map(Number);
    ctx.fillStyle = hex.length === 9
      ? `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${parseInt(hex.slice(7, 9), 16) / 255})`
      : hex;
    ctx.fillRect(x, y, 1, 1);
  }
  applyPixelScaling(cv);
}

// 확정 — 원본 영역을 지우고(null) 옮겨간 자리에 색을 찍어 픽셀 op 으로 쓴다.
// 지우기를 먼저, 찍기를 나중에 해야 겹치는 자리가 살아남는다.
function regionCommit(stateName, idx) {
  if (!regionEdit) return false;
  const moved = regionResolve(regionEdit);
  const identity = !regionEdit.t.dx && !regionEdit.t.dy
    && !regionEdit.t.rotate && !regionEdit.t.shx && !regionEdit.t.shy;
  if (identity) { regionCancel(); return false; }
  const e = entries[stateName];
  const ops = e.pixels[idx] || (e.pixels[idx] = {});
  const before = { ...ops };
  for (const p of regionEdit.src) ops[`${p.x},${p.y}`] = null;
  for (const [key, hex] of moved) ops[key] = hex;
  if (pixelEdit) {
    pixelEdit.journal.push({ full: before, after: { ...ops } });
    pixelEdit.redo.length = 0;
  }
  regionCancel();
  return true;
}

function regionCancel() {
  if (regionEdit && regionEdit.canvas && regionEdit.canvas.parentNode) {
    regionEdit.canvas.parentNode.removeChild(regionEdit.canvas);
  }
  regionEdit = null;
}

// 선택이 있으면 회전·비틀기 핸들을 **선택 영역으로 옮긴다** (수홍 지시 2026-07-26
// "로테이트랑 비틀기가 올가미 영역 위주로 위치를 이동했으면 좋겠어").
//
// 왜 중요한가: 핸들은 스테이지 고정 위치(위 가운데 / 왼아래)라 선택과 무관해 보이고,
// 실제로도 셀 전체를 돌린다. 올가미로 팔 하나를 잡아 놓고 그 핸들을 잡으면 캐릭터가
// 통째로 돈다 — 수홍이 실사용에서 바로 밟았다. 핸들이 선택 위로 따라오면 "이건 이 영역을
// 돌린다" 가 위치로 읽히고, 아래 `wireRegionHandles` 가 실제 대상도 영역으로 바꾼다.
function regionPlaceHandles(stage, stateName, sel) {
  const rot = stage.querySelector(".rotate-handle");
  const shr = stage.querySelector(".shear-handle");
  if (!rot || !shr) return;
  if (!sel) {
    // 선택 해제 = CSS 기본 자리로 되돌린다 (인라인 제거).
    for (const el of [rot, shr]) {
      el.style.left = ""; el.style.top = ""; el.style.bottom = ""; el.style.marginLeft = "";
    }
    return;
  }
  const [cw, ch] = cellDims(stateName);
  const pct = (v, span) => `${(v / span) * 100}%`;
  rot.style.left = pct((sel.x0 + sel.x1) / 2, cw);
  rot.style.top = `calc(${pct(sel.y0, ch)} - 18px)`;
  rot.style.marginLeft = "-8px";
  shr.style.left = `calc(${pct(sel.x0, cw)} - 16px)`;
  shr.style.top = `calc(${pct(sel.y1, ch)} - 0px)`;
  shr.style.bottom = "auto";
}


// 핸들 드래그의 **대상**을 영역으로 바꾼다. 선택이 없으면 아무것도 안 하고
// `wireStage` 의 셀 전체 핸들러가 그대로 가져간다.
// `wireStage`(zoom-editor.js:1291)보다 **먼저** 등록돼야 stopImmediatePropagation 이 먹는다.
function wireRegionHandles(stage, stateName, idx, ctx) {
  const { isTransformTool, hasSelection, selectionRect, afterCommit } = ctx;
  const common = (axis) => ({
    axis,
    enabled: () => isTransformTool() && hasSelection(),
    pixelsPerUnit: () => stage.clientWidth / cellDims(stateName)[0],
    centerOf: () => {
      const r = stage.getBoundingClientRect();
      const [cw, ch] = cellDims(stateName);
      const rect = selectionRect();
      return [
        r.left + ((rect.x + rect.w / 2) / cw) * r.width,
        r.top + ((rect.y + rect.h / 2) / ch) * r.height,
      ];
    },
    spanOf: () => [stage.clientWidth, stage.clientHeight],
    onStart: () => {
      const rect = selectionRect();
      const src = regionCapture(stage, stateName, idx, rect);
      regionEdit = { state: stateName, idx, rect, src: src || [],
                     t: { dx: 0, dy: 0, rotate: 0, shx: 0, shy: 0 }, canvas: null };
    },
    onDelta: (d) => {
      if (!regionEdit) return;
      regionEdit.t = { dx: Math.round(d.dx), dy: Math.round(d.dy),
                       rotate: d.rotate, shx: d.shx, shy: d.shy };
      regionRenderPreview(stage, stateName);
    },
    onEnd: (moved) => {
      if (!moved) { regionCancel(); return; }
      if (regionCommit(stateName, idx) && afterCommit) afterCommit();
    },
  });
  const rot = stage.querySelector(".rotate-handle");
  const shr = stage.querySelector(".shear-handle");
  if (rot) tpWireGesture(rot, common("rotate"));
  if (shr) tpWireGesture(shr, common("shear"));
}


// 스테이지에 영역 변형 제스처를 건다. 마퀴 선택이 있고 변형 툴이 활성일 때만 먹는다.
function wireRegionTransform(stage, stateName, idx, ctx) {
  const { hasSelection, selectionRect, isTransformTool, afterCommit } = ctx;
  tpWireGesture(stage, {
    enabled: () => isTransformTool() && hasSelection(),
    pixelsPerUnit: () => stage.clientWidth / cellDims(stateName)[0],
    centerOf: () => {
      const r = stage.getBoundingClientRect();
      const [cw, ch] = cellDims(stateName);
      const rect = selectionRect();
      return [
        r.left + ((rect.x + rect.w / 2) / cw) * r.width,
        r.top + ((rect.y + rect.h / 2) / ch) * r.height,
      ];
    },
    spanOf: () => [stage.clientWidth, stage.clientHeight],
    onStart: () => {
      const rect = selectionRect();
      const src = regionCapture(stage, stateName, idx, rect);
      regionEdit = { state: stateName, idx, rect, src: src || [],
                     t: { dx: 0, dy: 0, rotate: 0, shx: 0, shy: 0 }, canvas: null };
    },
    onDelta: (d) => {
      if (!regionEdit) return;
      // 이동은 정수 셀로 스냅한다 — 도트라 반 픽셀 이동은 결과가 뭉갠다.
      regionEdit.t = { dx: Math.round(d.dx), dy: Math.round(d.dy),
                       rotate: d.rotate, shx: d.shx, shy: d.shy };
      regionRenderPreview(stage, stateName);
    },
    onEnd: (moved) => {
      if (!moved) { regionCancel(); return; }
      if (regionCommit(stateName, idx) && afterCommit) afterCommit();
    },
  });
}
