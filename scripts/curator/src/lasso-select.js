// SPDX-License-Identifier: Apache-2.0
// curator/lasso-select.js — 올가미(자유곡선) 선택.
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// **왜 필요한가** (수홍 2026-07-26 "네모로는 선택을 못한다"): 도트 스프라이트에서 옮기고
// 싶은 부분(팔 하나, 모자, 기울이고 싶은 상반신)은 사각이 아니다. 사각으로 잡으면 이웃
// 픽셀이 딸려온다.
//
// **`sel` 에 마스크를 더한다 — rect 를 없애지 않는다.** `pixelEdit.sel` 은 지금
// `{x0,y0,x1,y1}` 이고 소비자가 여럿이다(마퀴 점선, select 툴 이동/복제, 영역 변형).
// rect 를 마스크로 교체하면 그 소비자를 전부 고쳐야 하고, 하나라도 놓치면 조용히
// 사각으로 되돌아간 것처럼 동작한다. 그래서 rect 는 **바운딩 박스로 유지**하고
// `mask`(셀키 Set)를 얹는다 — 마스크를 모르는 소비자는 지금 그대로(퇴화가 아니라 기존 동작),
// 아는 소비자만 실제 모양을 쓴다.

// 진행 중인 올가미 그리기. { pts: [[x,y]…], canvas }
let lassoDraw = null;

const LASSO_ICON =
  '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">' +
  '<path d="M8 2.2c3.1 0 5.6 1.7 5.6 3.9 0 2.1-2.5 3.8-5.6 3.8-1 0-2-.2-2.8-.5" ' +
  'fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>' +
  '<path d="M4.6 9.1C3.2 8.4 2.4 7.3 2.4 6.1c0-2.2 2.5-3.9 5.6-3.9" ' +
  'fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-dasharray="2.2 1.6"/>' +
  '<path d="M4.9 9.2c-.5 1.4-.3 2.7.5 4" fill="none" stroke="currentColor" ' +
  'stroke-width="1.2" stroke-linecap="round"/></svg>';

// 다각형 내부 판정 — even-odd 광선 교차. 폐곡선으로 닫아 센다.
function lassoContains(pts, x, y) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i, i += 1) {
    const [xi, yi] = pts[i];
    const [xj, yj] = pts[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// 자유곡선을 셀 마스크로 굽는다. 반환 = {x0,y0,x1,y1,mask} 또는 null(면적 0).
function lassoRasterize(pts, cw, ch) {
  if (pts.length < 3) return null;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [px, py] of pts) {
    if (px < x0) x0 = px;
    if (py < y0) y0 = py;
    if (px > x1) x1 = px;
    if (py > y1) y1 = py;
  }
  x0 = Math.max(0, Math.floor(x0));
  y0 = Math.max(0, Math.floor(y0));
  x1 = Math.min(cw, Math.ceil(x1));
  y1 = Math.min(ch, Math.ceil(y1));
  const mask = new Set();
  for (let y = y0; y < y1; y += 1) {
    for (let x = x0; x < x1; x += 1) {
      // 셀 중심으로 판정한다 — 모서리로 재면 경계 셀이 들쭉날쭉해진다.
      if (lassoContains(pts, x + 0.5, y + 0.5)) mask.add(`${x},${y}`);
    }
  }
  if (!mask.size) return null;
  return { x0, y0, x1, y1, mask };
}

// 그리는 동안의 윤곽. 확정된 선택의 표시는 `lassoRenderOutline` 이 따로 맡는다.
function lassoRenderPath(stage, stateName, pts, closed) {
  const [cw, ch] = cellDims(stateName);
  let cv = lassoDraw && lassoDraw.canvas;
  if (!cv) {
    cv = document.createElement("canvas");
    cv.className = "lasso-path";
    stage.appendChild(cv);
    if (lassoDraw) lassoDraw.canvas = cv;
  }
  const ss = 4; // 윤곽은 셀보다 곱게 그려야 곡선이 계단으로 안 보인다
  cv.width = cw * ss;
  cv.height = ch * ss;
  const ctx = cv.getContext("2d");
  // 버퍼를 정한 자리에서 판정까지 한다 — 바깥 스윕에 맡기면 canvas 기본값을 보고 답한다
  // (curator 픽셀 스케일링 SSoT 계약).
  applyPixelScaling(cv);
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (pts.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(pts[0][0] * ss, pts[0][1] * ss);
  for (let i = 1; i < pts.length; i += 1) ctx.lineTo(pts[i][0] * ss, pts[i][1] * ss);
  if (closed) ctx.closePath();
  ctx.lineWidth = 1.5;
  ctx.setLineDash([5, 4]);
  ctx.strokeStyle = "#000";
  ctx.stroke();
  ctx.setLineDash([5, 4]);
  ctx.lineDashOffset = 5;
  ctx.strokeStyle = "#fff";
  ctx.stroke();
}

function lassoClearPath() {
  if (lassoDraw && lassoDraw.canvas && lassoDraw.canvas.parentNode) {
    lassoDraw.canvas.parentNode.removeChild(lassoDraw.canvas);
  }
  lassoDraw = null;
}

// 확정된 마스크의 윤곽 — 마스크 경계 셀의 바깥변만 그린다. bbox 점선으로는
// "어디를 잡았는지" 가 안 보인다(올가미의 존재 이유가 그것이다).
function lassoRenderMask(stage, stateName, sel) {
  const old = stage.querySelector(".lasso-mask");
  if (old) old.parentNode.removeChild(old);
  if (!sel || !sel.mask) return;
  const [cw, ch] = cellDims(stateName);
  const cv = document.createElement("canvas");
  cv.className = "lasso-mask";
  cv.width = cw;
  cv.height = ch;
  const ctx = cv.getContext("2d");
  ctx.fillStyle = "rgba(90,160,255,0.28)";
  for (const key of sel.mask) {
    const [x, y] = key.split(",").map(Number);
    ctx.fillRect(x, y, 1, 1);
  }
  stage.appendChild(cv);
  applyPixelScaling(cv);
}

// 스테이지에 올가미 제스처를 건다. 올가미 툴이 활성일 때만 먹는다.
function wireLasso(stage, stateName, ctx) {
  const { isLassoTool, srcXY, onSelection } = ctx;
  stage.addEventListener("pointerdown", (ev) => {
    if (!isLassoTool()) return;
    if (ev.button || !ev.isPrimary) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    try { stage.setPointerCapture(ev.pointerId); } catch { /* 합성 포인터 */ }
    lassoDraw = { pts: [srcXY(ev)], canvas: null };
    lassoRenderPath(stage, stateName, lassoDraw.pts, false);

    const onMove = (e) => {
      if (!lassoDraw) return;
      const p = srcXY(e);
      const last = lassoDraw.pts[lassoDraw.pts.length - 1];
      // 점을 너무 촘촘히 쌓지 않는다 — 판정 비용이 점 수에 선형이다.
      if (Math.abs(p[0] - last[0]) < 0.4 && Math.abs(p[1] - last[1]) < 0.4) return;
      lassoDraw.pts.push(p);
      lassoRenderPath(stage, stateName, lassoDraw.pts, false);
    };
    const onUp = () => {
      try { stage.releasePointerCapture(ev.pointerId); } catch { /* no-op */ }
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("pointerup", onUp);
      const pts = lassoDraw ? lassoDraw.pts : [];
      const [cw, ch] = cellDims(stateName);
      const sel = lassoRasterize(pts, cw, ch);
      lassoClearPath();
      onSelection(sel);
    };
    stage.addEventListener("pointermove", onMove);
    stage.addEventListener("pointerup", onUp);
  });
}
