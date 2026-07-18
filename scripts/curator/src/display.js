// SPDX-License-Identifier: Apache-2.0
// curator/display.js — 프레임 그리기 + 픽셀퍼펙트/격자 표시 변형 동기화
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

// ── 프레임 공간 변환 SSoT (수홍 지시 2026-07-19 "코드 좀 체계적으로") ──
// 소스↔표시 좌표 수학과 "화면에 보이는 색" 샘플링은 여기 한 곳만 소유한다.
// 다른 파일은 이 함수들을 쓰기만 한다 — 툴별 복붙 좌표 수학 금지.
// 순변환: T(p) = M(p−c) + c + d  (drawFrameInto 의 렌더 수학과 동일)

function frameFwdXY(stateName, idx, x, y) {
  const [cw, ch] = cellDims(stateName);
  const tr = getTransform(stateName, idx);
  const m = matrixOf(tr);
  return [m.m00 * (x - cw / 2) + m.m01 * (y - ch / 2) + cw / 2 + tr.dx,
          m.m10 * (x - cw / 2) + m.m11 * (y - ch / 2) + ch / 2 + tr.dy];
}

function frameInvXY(stateName, idx, x, y) {
  const [cw, ch] = cellDims(stateName);
  const tr = getTransform(stateName, idx);
  const m = matrixOf(tr);
  const det = m.m00 * m.m11 - m.m01 * m.m10 || 1;
  const ux = x - cw / 2 - tr.dx;
  const uy = y - ch / 2 - tr.dy;
  return [(m.m11 * ux - m.m01 * uy) / det + cw / 2,
          (-m.m10 * ux + m.m00 * uy) / det + ch / 2];
}

// 포인터 이벤트 → 소스(저장) 공간 좌표. 표시가 어떤 모드든 저장은 항상 소스 공간.
function pointerSrcXY(stage, stateName, idx, e2) {
  const [cw, ch] = cellDims(stateName);
  const r = stage.getBoundingClientRect();
  const dx0 = ((e2.clientX - r.left) / r.width) * cw;
  const dy0 = ((e2.clientY - r.top) / r.height) * ch;
  return frameInvXY(stateName, idx, dx0, dy0);
}

// 스포이드 진실 = "화면에 보이는 그 색". 표시 파이프라인이 양자화 캔버스든
// (비트맵 = 표시 공간) CSS 변형 캔버스든 (비트맵 = 소스 공간), 실제 렌더된
// 비트맵에서 집는다 — 소스 픽셀을 따로 재계산하면 픽셀퍼펙트 재양자화와
// 어긋난 색이 잡힌다 (실사고 2026-07-19 수홍 "스포이드가 다른 색을 잡음").
function sampleDisplayedColor(stage, stateName, idx, e2) {
  const canvas = stage.querySelector(".snap-canvas");
  if (!(canvas && canvas.style.display === "block" && canvas.width)) return null;
  const r = stage.getBoundingClientRect();
  let x = ((e2.clientX - r.left) / r.width) * canvas.width;
  let y = ((e2.clientY - r.top) / r.height) * canvas.height;
  if (canvas.style.transform) [x, y] = frameInvXY(stateName, idx, x, y);
  x = Math.floor(x);
  y = Math.floor(y);
  if (!(x >= 0 && x < canvas.width && y >= 0 && y < canvas.height)) return null;
  const d = canvas.getContext("2d").getImageData(x, y, 1, 1).data;
  if (d[3] < 40) return null; // 투명 픽셀은 집을 색이 없다
  return "#" + [d[0], d[1], d[2]].map((v) => v.toString(16).padStart(2, "0")).join("");
}

// 변형을 셀 캔버스에 NEAREST 로 그리고, 논리 격자(snap px/논리픽셀)로 재양자화한다.
// curation.apply_transform 의 snap_scale 경로를 캔버스로 미러링 — 드래그/회전 중에도
// 스프라이트가 셀 고정 격자에 실시간으로 스냅되어 보인다 (격자는 그대로, 그림이 스냅).
function drawFrameInto(ctx, image, t, cw, ch, snap, edits) {
  ctx.imageSmoothingEnabled = false; // 항상 NEAREST — 베이스(raw→논리) 표시도 픽셀 유지
  // 사이드카 픽셀 편집은 변형 이전(소스) 공간이다 — 굽기(apply_pixel_edits →
  // apply_transform, compose_atlas.py)와 같은 순서로 편집을 먼저 소스에 합성한 뒤
  // 함께 변형한다. (실사고 2026-07-17: 변형 후에 고정 좌표로 덧그려서, 캐릭터를
  // 옮기면 편집 픽셀만 제자리에 남았다 — 수홍 발견.)
  let source = image;
  if (edits) {
    const src = drawFrameInto._src || (drawFrameInto._src = document.createElement("canvas"));
    src.width = cw;
    src.height = ch;
    const sctx = src.getContext("2d");
    sctx.imageSmoothingEnabled = false;
    sctx.clearRect(0, 0, cw, ch);
    sctx.drawImage(image, 0, 0, cw, ch);
    for (const [key, val] of Object.entries(edits)) {
      const [x, y] = key.split(",").map(Number);
      if (!(x >= 0 && x < cw && y >= 0 && y < ch)) continue;
      if (val) {
        sctx.fillStyle = val;
        sctx.fillRect(x, y, 1, 1);
      } else {
        sctx.clearRect(x, y, 1, 1);
      }
    }
    source = src;
  }
  const m = matrixOf(t);
  ctx.save();
  ctx.translate(cw / 2 + t.dx, ch / 2 + t.dy);
  ctx.transform(m.m00, m.m10, m.m01, m.m11, 0, 0);
  ctx.drawImage(source, -cw / 2, -ch / 2, cw, ch);
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
  document.querySelectorAll(".card").forEach(updateCardGrid);
}

// 줄 단위 격자: 그 줄의 격자 체크박스가 켜져 있을 때만 그린다.
// - 픽셀퍼펙트 표시 줄: 출력 격자(빨/파, 셀 픽셀 눈금) — 결과가 앉은 격자 그 자체.
//   셀에 고정이다: 이동/회전 변형은 스프라이트가 이 고정 래스터에 재양자화되는 것이지
//   래스터가 따라 움직이는 게 아니다 (수홍 확정 2026-07-14 실시간 스냅 동작).
// - 원본(plain) 표시 줄: 최종 대응 격자(초록) — 최종 픽셀 콘텐츠 bbox 를 픽셀 수만큼
//   균등 분할해 원본 위에 겹친다. 칸 하나 = 최종 픽셀 하나 (칸 수 = 픽셀 수 보장).
//   콘텐츠 기준 격자이므로 이동(dx/dy)·좌우반전은 따라간다 (수홍 지적 2026-07-15).
//   회전/기울임/배율 변형은 소스↔결과 대응이 더 이상 직사각 격자가 아니라 숨긴다 —
//   비축정렬 상태로 가짜 격자를 겹치지 않는다 (결과 픽셀은 픽셀퍼펙트 뷰가 보여준다).
//   1차 절단 뒤 48 계약 conform 축소가 칸을 합칠 수 있어 절단선(manifest input_grids)은
//   최종 대응이 아니다 — 진단 기록으로만 남긴다 (수홍 발견 2026-07-14).
// 측정/계약이 없는 줄은 오버레이를 숨긴다 — 가짜 격자 금지.
function updateCardGrid(card) {
  const overlay = card.querySelector(".pxgrid");
  const ingrid = card.querySelector(".ingrid");
  if (!overlay && !ingrid) return;
  const stage = card.querySelector(".stage");
  const cardState = card.dataset.state;
  const isBaseCard = cardState === BASE_STATE;
  const st = run.states.find((s) => s.name === cardState);
  const frame = isBaseCard ? frameOf(BASE_STATE, 0) : (st && st.frames[Number(card.dataset.idx)]);
  const on = !!gridStates[cardState];
  const plainShown = (isBaseCard || ppTwinStates.has(cardState)) && !ppOn(cardState);
  const scale = isBaseCard ? 1
    : ((st && st.pixelScale) || (run.pixelPerfect && run.pixelPerfect.scale) || null);
  const t = frame ? getTransform(card.dataset.state, frame.index) : null;
  const axisAligned = !t || (!t.rotate && t.scale === 1 && !t.shx && !t.shy);
  const useFinal = on && plainShown && frame && frame.contentBox && scale && stage && axisAligned;
  if (ingrid) {
    if (useFinal) {
      drawFinalGrid(ingrid, stage, frame.contentBox, scale, t, frame.inputGrid || null);
      ingrid.style.display = "block";
    } else {
      ingrid.style.display = "none";
    }
  }
  const step = on && !useFinal && !plainShown ? scale : null;
  if (!overlay) return;
  if (!step || !stage) { overlay.style.display = "none"; return; }
  overlay.style.display = "block";
  const ds = (stage.clientWidth / cellDims(cardState)[0]) * step;
  overlay.style.backgroundSize = `${ds}px ${ds}px`;
}

// 최종 대응 격자: 최종 픽셀 콘텐츠 bbox(셀 좌표)를 논리 픽셀 수만큼 균등 분할해
// 원본 쌍둥이 위에 그린다 — 초록 칸 하나가 결과 픽셀 하나에 정확히 대응한다.
// 축정렬 변형(이동 dx/dy, 좌우반전)은 bbox 를 같은 규칙(CSS: 중심 기준 반전 후 이동)으로
// 옮겨 콘텐츠를 따라간다. 비축정렬 변형은 호출 전에 걸러진다 (updateCardGrid).
function drawFinalGrid(canvas, stage, box, scale, t, inputGrid) {
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
  const dx = t ? t.dx : 0;
  const dy = t ? t.dy : 0;
  const cw = run.cell.width;
  // 추출이 검출한 실제 절단선(input_grids, 쌍둥이 셀 좌표)이 있으면 그것을 그린다 —
  // 균등 분할은 끝점만 맞고 중간이 어긋난다 (수홍 발견 2026-07-18 down_lie:
  // "머리끝발끝은 맞는데 중간 픽셀이 하나도 안 맞아"). conform 축소 폐지(v1.56.22)로
  // 1차 절단선이 곧 최종 대응이 됐다. 없는 프레임(구세대 캐시·테이크)만 균등 근사.
  // 기록 격자 신뢰 게이트 (실사고 2026-07-18 side_idle: 최종 31칸인데 기록은 45칸 —
  // 반피치 하모닉 오검출): 칸수가 최종 픽셀 수와 ±1 이상 어긋나면 그 기록은 버린다.
  // 균등 격자는 "칸 수 = 최종 픽셀 수" 를 보장한다 (가짜 격자 금지).
  if (inputGrid && Array.isArray(inputGrid.x) && Array.isArray(inputGrid.y)) {
    const expCols = Math.max(1, Math.round((box[2] - box[0]) / scale));
    const expRows = Math.max(1, Math.round((box[3] - box[1]) / scale));
    if (Math.abs((inputGrid.x.length - 1) - expCols) > 1 || Math.abs((inputGrid.y.length - 1) - expRows) > 1) {
      inputGrid = null;
    }
  }
  if (inputGrid && Array.isArray(inputGrid.x) && Array.isArray(inputGrid.y) && inputGrid.x.length > 1 && inputGrid.y.length > 1) {
    // 검출 절단선의 매핑 비율 오차가 중간에서 누적된다 (실사고 2026-07-18 down_lie:
    // 28칸이 27.2px 로 눌려 끝만 맞고 중간 전부 어긋남) — 끝점을 최종 콘텐츠
    // 박스에 앵커하고 검출 비례만 유지하도록 정규화한다.
    const norm = (edges, lo, hi) => {
      const e0 = edges[0];
      const e1 = edges[edges.length - 1];
      if (e1 - e0 <= 0) return edges;
      return edges.map((e) => lo + ((e - e0) * (hi - lo)) / (e1 - e0));
    };
    inputGrid = { x: norm(inputGrid.x, box[0], box[2]), y: norm(inputGrid.y, box[1], box[3]) };
    const xs = t && t.flipX ? inputGrid.x.map((e) => cw - e).reverse() : inputGrid.x;
    const yTop = (inputGrid.y[0] + dy) * sy;
    const yBot = (inputGrid.y[inputGrid.y.length - 1] + dy) * sy;
    const xLeft = (xs[0] + dx) * sx;
    const xRight = (xs[xs.length - 1] + dx) * sx;
    for (const e of xs) {
      const px = Math.round((e + dx) * sx) + 0.5;
      ctx.beginPath(); ctx.moveTo(px, yTop); ctx.lineTo(px, yBot); ctx.stroke();
    }
    for (const e of inputGrid.y) {
      const py = Math.round((e + dy) * sy) + 0.5;
      ctx.beginPath(); ctx.moveTo(xLeft, py); ctx.lineTo(xRight, py); ctx.stroke();
    }
    return;
  }
  const cellsX = Math.max(1, Math.round((box[2] - box[0]) / scale));
  const cellsY = Math.max(1, Math.round((box[3] - box[1]) / scale));
  let bx0 = box[0], bx1 = box[2];
  if (t && t.flipX) { [bx0, bx1] = [cw - box[2], cw - box[0]]; }
  const x0 = (bx0 + dx) * sx, x1 = (bx1 + dx) * sx;
  const y0 = (box[1] + dy) * sy, y1 = (box[3] + dy) * sy;
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
    if (!card.dataset.state) return;
    const idx = Number(card.dataset.idx);
    const f = frameOf(card.dataset.state, idx); // 복제 인스턴스도 원본 이미지로 해석
    const el = card.querySelector(".stage img");
    if (f && el) {
      el.src = frameUrl(card.dataset.state, f);
      // 변형 표시 모드(캔버스 스냅 ↔ CSS)도 pp 상태에 맞게 다시 결정
      if (f.present) applyCardTransform(card.querySelector(".stage"), card.dataset.state, idx);
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
