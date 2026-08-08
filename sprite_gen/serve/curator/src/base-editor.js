// SPDX-License-Identifier: Apache-2.0
// curator/base-editor.js — 베이스 소스 편집 (검출 격자 논리 이미지 → 줌 모달 재사용)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

// 최상단 base 참조 줄 — 아이덴티티 truth 를 생성 결과와 나란히 비교하기 위한
// 읽기 전용 표시 (선택/변형/굽기와 무관).
function renderBaseRow() {
  const wrap = document.createElement("section");
  wrap.className = "state base-row";
  wrap.innerHTML =
    `<div class="state-head"><h3>base</h3>` +
    `<span class="muted">${t("baseNote")}</span>` +
    `<button type="button" class="ghost base-edit-btn" data-tip="${t("tBaseEdit")}">✎ ${t("baseEditBtn")}</button></div>` +
    `<div class="base-stage"><img src="${escapeHtml(run.baseUrl)}" alt="base source" draggable="false" /></div>`;
  const editBtn = wrap.querySelector(".base-edit-btn");
  editBtn.addEventListener("click", async () => {
    // 격자 검출(첫 회 수 초) + 논리 이미지 빌드 동안 버튼 스피너 — 멈춘 것처럼 보이지 않게
    if (editBtn.disabled) return;
    editBtn.disabled = true;
    const label = editBtn.innerHTML;
    editBtn.innerHTML = '<span class="gen-spin" aria-label="loading"></span>';
    try {
      await openBaseEditor();
    } finally {
      editBtn.innerHTML = label;
      editBtn.disabled = false;
    }
  });
  document.getElementById("states").appendChild(wrap);
}

// 현재 베이스 편집기에 표시 중인 격자 피치 [x, y] 와 그 출처("manual"|"detected").
// 사람이 피치를 조정하면(라이브 프리뷰) 이 값이 갱신되고, "격자 저장" 이 이를
// sprite-request.json 의 fit.pitch_manual 로 확정한다.
let basePitch = null;
let basePitchSource = null;
// 저장 전 라이브 프리뷰 override 피치([x,y]). 저장/자동복귀/detected·saved 표시 중엔 null.
// 이게 set 이면 base-edit 굽기에 이 피치를 함께 보내 화면과 같은 격자로 확장한다.
let basePitchOverride = null;

// ── 베이스 편집 = 줌 모달과 같은 컴포넌트 (수홍 지시 2026-07-17 "같은 컴포넌트를
// 쓰라" — 별도 모달 구현은 폐기). 검출 격자의 논리 해상도로 가상 상태 "__base__" 를
// 만들어 openZoom 으로 연다. 도구/단축키/마키/줌/팬 전부 프레임 편집과 단일 코드.
//
// overridePitch = [x, y] 면 그 피치로 /api/base-grid 를 라이브 프리뷰한다(저장 전,
// 디스크 미변경). 없으면 서버가 저장된 fit.pitch_manual > 자동 검출 순으로 격자를 준다.
async function openBaseEditor(overridePitch) {
  let url = "/api/base-grid";
  if (Array.isArray(overridePitch)) {
    url += `?pitchX=${encodeURIComponent(overridePitch[0])}&pitchY=${encodeURIComponent(overridePitch[1])}`;
  }
  let grid = null;
  try {
    grid = (await (await fetch(url)).json()).grid || null;
  } catch { grid = null; }
  if (!grid) {
    setStatus(t("baseEditFail") + "no confident pixel grid on the base", "err");
    return;
  }
  basePitch = Array.isArray(grid.pitch) ? grid.pitch.slice() : null;
  basePitchSource = grid.source || null;
  // 프리뷰 override 로 연 경우에만 마커를 세운다 (저장/자동복귀는 override 없이 재열림).
  basePitchOverride = Array.isArray(overridePitch) ? overridePitch.slice() : null;
  const rawUrl = run.baseUrl + (run.baseUrl.includes("?") ? "&" : "?") + "edit=" + Date.now();
  // 진짜 격자 기반 논리 이미지 (수홍 지적 2026-07-17: 균일 등분 격자는 이미지와
  // 어긋난다): 검출 절단선(xEdges/yEdges)의 블록 "중심"을 raw 에서 샘플해 논리
  // 해상도 PNG 를 만든다. 이후 모달의 표시·편집·격자·팔레트는 전부 이 균일 논리
  // 공간이라 프레임과 동일하게 정확히 떨어진다. raw 는 pp OFF 의 원본 뷰(plain
  // twin 자리)로 쓴다. 저장(논리 ops→raw 확장)은 서버가 같은 절단선으로 한다.
  const rawImg = new Image();
  rawImg.src = rawUrl;
  await new Promise((ok, err) => { rawImg.onload = ok; rawImg.onerror = err; });
  const cols = grid.xEdges.length - 1;
  const rows = grid.yEdges.length - 1;
  const probe = document.createElement("canvas");
  probe.width = rawImg.naturalWidth;
  probe.height = rawImg.naturalHeight;
  const pc = probe.getContext("2d");
  pc.drawImage(rawImg, 0, 0);
  const raw = pc.getImageData(0, 0, probe.width, probe.height).data;
  const logical = document.createElement("canvas");
  logical.width = cols;
  logical.height = rows;
  const lc = logical.getContext("2d");
  const out = lc.createImageData(cols, rows);
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const cx = Math.floor((grid.xEdges[i] + grid.xEdges[i + 1]) / 2);
      const cy = Math.floor((grid.yEdges[j] + grid.yEdges[j + 1]) / 2);
      const s = (cy * probe.width + cx) * 4;
      const d = (j * cols + i) * 4;
      out.data[d] = raw[s];
      out.data[d + 1] = raw[s + 1];
      out.data[d + 2] = raw[s + 2];
      out.data[d + 3] = 255;
    }
  }
  lc.putImageData(out, 0, 0);
  baseView = { cols, rows, xEdges: grid.xEdges, yEdges: grid.yEdges,
               url: logical.toDataURL("image/png"), rawUrl,
               // raw 픽셀 치수 — 격자 맞추기 오버레이가 raw 좌표를 화면 좌표로
               // 옮길 때 쓴다 (raw 전체가 스테이지에 선형 대응: 실측 확인).
               rawW: rawImg.naturalWidth, rawH: rawImg.naturalHeight };
  baseLogicalImg = new Image();
  baseLogicalImg.src = baseView.url;
  await new Promise((ok) => { baseLogicalImg.onload = ok; });
  if (!entries[BASE_STATE]) {
    entries[BASE_STATE] = { pixels: {}, transforms: {}, order: [0], sel: new Set([0]),
                            clones: {}, archived: [] };
  }
  openZoom(BASE_STATE, 0);
  injectBasePitchControls(BASE_STATE);
  const stage = document.querySelector("#zoom-modal .stage");
  if (stage) {
    wireGridFitDrag(stage, BASE_STATE);
    renderGridFit(stage, BASE_STATE);
  }
}

// ── 격자 맞추기 오버레이 (raw 위) ────────────────────────────────────────
// 왜 raw 위인가: 픽셀 언페이크 ON 은 이미 그 격자로 스냅된 논리 이미지라 격자가
// 언제나 균일하게 보인다 — 어긋남을 볼 수 없다. 어긋남은 raw 블록 경계와 후보
// 절단선을 나란히 놓을 때만 보이므로, 이 오버레이는 pp OFF(raw 표시)에서만 그린다.
// 좌표: 격자선은 raw 픽셀 좌표이고 raw 전체가 스테이지에 선형 대응한다(실측:
// raw 1254×1254 → stage 553×704, img/snap-canvas rect 동일).
// 격자 맞추기가 지금 보고 있는 대상. 베이스는 baseView, 프레임은 /api/frame-grid 로
// 받아 둔 후보 격자다 — 둘 다 {xEdges,yEdges,rawW,rawH} 한 모양이라 그리기·드래그
// 코드가 하나다 (수홍 확정 2026-08-08 "둘다 넣자").
let gridFitView = null;

function gridFitTarget(stateName) {
  return stateName === BASE_STATE ? baseView : gridFitView;
}

// 프레임의 후보 격자를 받아 둔다. 베이스와 대칭이되 한 가지가 다르다: 프레임의
// 초록 격자(`inputGrid`)는 **지난 추출이 실제로 자른 선의 기록**이라, 여기서 피치를
// 바꿔도 재추출 전에는 그 기록이 안 바뀐다. 그래서 이건 "이 피치로 자르면 이렇게
// 된다" 를 겹쳐 보여주는 후보이고, 확정은 저장 + 재추출이다.
async function loadFrameGrid(stateName, index, overridePitch) {
  let url = `/api/frame-grid?state=${encodeURIComponent(stateName)}&index=${index}`;
  if (Array.isArray(overridePitch)) {
    url += `&pitchX=${encodeURIComponent(overridePitch[0])}&pitchY=${encodeURIComponent(overridePitch[1])}`;
  }
  let grid = null;
  try {
    grid = (await (await fetch(url)).json()).grid || null;
  } catch { grid = null; }
  if (!grid) { gridFitView = null; return null; }
  gridFitView = {
    xEdges: grid.xEdges, yEdges: grid.yEdges,
    rawW: grid.imageSize[0], rawH: grid.imageSize[1],
    pitch: grid.pitch, source: grid.source,
  };
  basePitch = grid.pitch.slice();
  basePitchSource = grid.source;
  return gridFitView;
}

function renderGridFit(stage, stateName) {
  const state = stateName || (zoomView && zoomView.stateName) || BASE_STATE;
  const view = gridFitTarget(state);
  const canvas = stage && stage.querySelector(".gridfit");
  if (!canvas || !view || !view.rawW) return;
  const show = !!gridStates[state] && !unfakeOn(state);
  if (!show) { canvas.style.display = "none"; return; }
  const w = Math.max(1, Math.round(stage.clientWidth));
  const h = Math.max(1, Math.round(stage.clientHeight));
  canvas.width = w;
  canvas.height = h;
  canvas.style.display = "block";
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  const edges = gridFitDrag && gridFitDrag.preview ? gridFitDrag.preview : view;
  const sx = w / view.rawW;
  const sy = h / view.rawH;
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(21, 200, 90, 0.95)";
  const y0 = edges.yEdges[0] * sy;
  const y1 = edges.yEdges[edges.yEdges.length - 1] * sy;
  const x0 = edges.xEdges[0] * sx;
  const x1 = edges.xEdges[edges.xEdges.length - 1] * sx;
  edges.xEdges.forEach((e, i) => {
    const x = Math.round(e * sx) + 0.5;
    // 끝선(첫/마지막)은 굵게 — 정렬 기준선이 어디인지 눈에 띄게
    ctx.lineWidth = (i === 0 || i === edges.xEdges.length - 1) ? 2 : 1;
    ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1); ctx.stroke();
  });
  edges.yEdges.forEach((e, i) => {
    const y = Math.round(e * sy) + 0.5;
    ctx.lineWidth = (i === 0 || i === edges.yEdges.length - 1) ? 2 : 1;
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
  });
}

// 드래그 상태: {axis:"x"|"y", index, origin, preview:{xEdges,yEdges}}
let gridFitDrag = null;

// 균일 격자 미리보기 — 서버의 `_grid_edges` 를 흉내낸 **드래그 중 임시 표시**다.
// 확정 값은 pointerup 에서 서버에 다시 물어 받는다 (서버가 절단선 SSoT).
function previewEdges(origin, pitch, end) {
  const out = [];
  for (let i = 0; ; i++) {
    const v = origin + i * pitch;
    if (v > end + pitch * 0.5) break;
    out.push(Math.round(v));
    if (out.length > 4096) break;
  }
  return out.length > 1 ? out : [origin, end];
}

// 격자선을 잡아 블록 경계로 끌면 피치가 재계산된다. i 번째 선을 raw 좌표 r 로
// 옮기면 pitch = (r - origin) / i — 즉 "먼 선을 실제 블록 경계에 맞추면 간격이
// 그에 맞게 정해진다". 원점(첫 선)은 콘텐츠 bbox 라 고정한다(위상 조정은 추출
// 백엔드가 프레임별 실측 위상을 쓰므로 별도 작업 — 여기서 열면 화면과 추출이
// 갈린다, No Silent Fallback).
function wireGridFitDrag(stage, stateName) {
  const canvas = stage && stage.querySelector(".gridfit");
  if (!canvas) return;
  const state = stateName || BASE_STATE;
  const viewOf = () => gridFitTarget(state);
  const near = (ev) => {
    const view = viewOf();
    if (!view || !view.rawW) return null;
    const r = stage.getBoundingClientRect();
    const rx = ((ev.clientX - r.left) / r.width) * view.rawW;
    const ry = ((ev.clientY - r.top) / r.height) * view.rawH;
    const tolX = (view.rawW / r.width) * 7;   // 화면 7px 를 raw 로 환산
    const tolY = (view.rawH / r.height) * 7;
    let best = null;
    view.xEdges.forEach((e, i) => {
      if (i === 0) return;                    // 원점선은 잡지 않는다
      const d = Math.abs(e - rx);
      if (d <= tolX && (!best || d < best.d)) best = { axis: "x", index: i, d };
    });
    view.yEdges.forEach((e, i) => {
      if (i === 0) return;
      const d = Math.abs(e - ry);
      if (d <= tolY && (!best || d < best.d)) best = { axis: "y", index: i, d };
    });
    return best;
  };
  // 커서로 "잡을 수 있다" 를 알린다 — 잡을 게 없으면 편집 도구가 그대로 쓰인다
  canvas.addEventListener("pointermove", (ev) => {
    if (gridFitDrag) return;
    canvas.style.cursor = near(ev) ? (near(ev).axis === "x" ? "ew-resize" : "ns-resize") : "";
    canvas.style.pointerEvents = "auto";
  });
  canvas.addEventListener("pointerdown", (ev) => {
    const hit = near(ev);
    if (!hit) return;                             // 격자선이 아니면 아래 도구로 흘린다
    ev.preventDefault();
    ev.stopImmediatePropagation();
    try { canvas.setPointerCapture(ev.pointerId); } catch { /* no-op */ }
    const view = viewOf();
    const axis = hit.axis;
    const origin = axis === "x" ? view.xEdges[0] : view.yEdges[0];
    const end = axis === "x" ? view.xEdges[view.xEdges.length - 1]
                             : view.yEdges[view.yEdges.length - 1];
    const other = axis === "x" ? view.yEdges : view.xEdges;
    gridFitDrag = { axis, index: hit.index, origin, end, other, pitch: null };
    const onMove = (e2) => {
      const r = stage.getBoundingClientRect();
      const raw = axis === "x"
        ? ((e2.clientX - r.left) / r.width) * view.rawW
        : ((e2.clientY - r.top) / r.height) * view.rawH;
      const pitch = Math.max(2, (raw - origin) / gridFitDrag.index);
      gridFitDrag.pitch = pitch;
      const line = previewEdges(origin, pitch, end);
      gridFitDrag.preview = axis === "x" ? { xEdges: line, yEdges: other }
                                         : { xEdges: other, yEdges: line };
      // 숫자 입력도 같이 움직인다 — 두 컨트롤이 한 값의 두 표시다
      const input = document.querySelector(axis === "x" ? "#zoom-modal .et-pitch-x" : "#zoom-modal .et-pitch-y");
      if (input) input.value = Math.round(pitch * 100) / 100;
      renderGridFit(stage, state);
    };
    const onUp = async () => {
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      try { canvas.releasePointerCapture(ev.pointerId); } catch { /* no-op */ }
      const pitch = gridFitDrag && gridFitDrag.pitch;
      gridFitDrag = null;
      if (!pitch) { renderGridFit(stage, state); return; }
      // 확정은 서버에 다시 물어 받는다 — 절단선 SSoT 는 서버 격자 응답이다.
      const xIn = document.querySelector("#zoom-modal .et-pitch-x");
      const yIn = document.querySelector("#zoom-modal .et-pitch-y");
      const pair = [parseFloat(xIn && xIn.value), parseFloat(yIn && yIn.value)];
      if (!pair.every((v) => Number.isFinite(v) && v >= 2)) return;
      if (state === BASE_STATE) {
        delete entries[BASE_STATE];
        await openBaseEditor(pair);
      } else {
        await loadFrameGrid(state, zoomView ? zoomView.idx : 0, pair);
        renderGridFit(stage, state);
      }
    };
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
  });
}

// 베이스 줌 모달 툴바에 픽셀 격자 피치 조정 위젯을 붙인다. 값을 바꾸면 그 피치로
// 격자를 라이브 재렌더(디스크 미변경)하고, "격자 저장" 이 fit.pitch_manual 로 확정한다.
function injectBasePitchControls(stateName) {
  const toolbar = document.querySelector("#zoom-modal .edit-toolbar");
  if (!toolbar || !basePitch) return;
  if (toolbar.querySelector(".et-pitch")) return;   // 중복 주입 방지
  const pitchState = stateName || BASE_STATE;
  const fmt = (v) => (Math.round(v * 100) / 100);
  const wrap = document.createElement("span");
  wrap.className = "et-pitch";
  wrap.setAttribute("data-tip", t("tBasePitch"));
  wrap.innerHTML =
    `<span class="et-pitch-label">${t("basePitchLabel")}</span>` +
    `<button type="button" class="ghost et-pitch-dec" data-axis="x" aria-label="pitch x -">−</button>` +
    `<input type="number" class="et-pitch-x" step="0.5" min="2" value="${fmt(basePitch[0])}" />` +
    `<button type="button" class="ghost et-pitch-inc" data-axis="x" aria-label="pitch x +">+</button>` +
    `<span class="et-pitch-x-glyph">×</span>` +
    `<button type="button" class="ghost et-pitch-dec" data-axis="y" aria-label="pitch y -">−</button>` +
    `<input type="number" class="et-pitch-y" step="0.5" min="2" value="${fmt(basePitch[1])}" />` +
    `<button type="button" class="ghost et-pitch-inc" data-axis="y" aria-label="pitch y +">+</button>` +
    `<button type="button" class="et-pitch-save" data-tip="${t("tBasePitchSave")}">${t("basePitchSave")}</button>` +
    `<button type="button" class="ghost et-pitch-auto" data-tip="${t("tBasePitchAuto")}">${t("basePitchAuto")}</button>`;
  const xInput = wrap.querySelector(".et-pitch-x");
  const yInput = wrap.querySelector(".et-pitch-y");
  if (basePitchSource === "manual") wrap.classList.add("is-manual");
  const readPair = () => [parseFloat(xInput.value), parseFloat(yInput.value)];
  const valid = ([x, y]) => Number.isFinite(x) && Number.isFinite(y) && x >= 2 && y >= 2;
  // 라이브 프리뷰: 새 피치로 격자를 다시 그린다. 베이스는 논리 해상도(cols×rows)가
  // 바뀌므로 편집기를 다시 열고(예전 논리좌표 픽셀 편집은 무효 — 굽기 전이라 파일
  // 미변경), 프레임은 후보 격자만 다시 받아 오버레이를 갱신한다.
  const preview = async (pair) => {
    if (!valid(pair)) return;
    if (pitchState === BASE_STATE) {
      delete entries[BASE_STATE];
      await openBaseEditor(pair);
      return;
    }
    await loadFrameGrid(pitchState, zoomView ? zoomView.idx : 0, pair);
    const stage = document.querySelector("#zoom-modal .stage");
    if (stage) renderGridFit(stage, pitchState);
  };
  wrap.querySelectorAll(".et-pitch-inc, .et-pitch-dec").forEach((btn) => {
    btn.addEventListener("click", () => {
      const delta = btn.classList.contains("et-pitch-inc") ? 1 : -1;
      const input = btn.dataset.axis === "x" ? xInput : yInput;
      const next = Math.max(2, (parseFloat(input.value) || 2) + delta);
      input.value = Math.round(next * 100) / 100;
      preview(readPair());
    });
  });
  xInput.addEventListener("change", () => preview(readPair()));
  yInput.addEventListener("change", () => preview(readPair()));
  wrap.querySelector(".et-pitch-save").addEventListener("click", async (ev) => {
    const pair = readPair();
    if (!valid(pair)) { setStatus(t("basePitchFail") + "pitch must be >= 2px", "err"); return; }
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      const res = await fetch("/api/base-pitch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pitchX: pair[0], pitchY: pair[1] }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      basePitchSource = "manual";
      basePitchOverride = null;  // 이제 fit.pitch_manual 로 저장됨 — 굽기가 그걸 읽는다
      wrap.classList.add("is-manual");
      setStatus(t("basePitchSaved"), "ok");
    } catch (e) {
      setStatus(t("basePitchFail") + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  });
  wrap.querySelector(".et-pitch-auto").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      const res = await fetch("/api/base-pitch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),  // 비움 = fit.pitch_manual 제거 → 자동 검출 복귀
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      setStatus(t("basePitchAutoDone"), "ok");
      if (pitchState === BASE_STATE) {
        delete entries[BASE_STATE];
        await openBaseEditor();  // override 없이 재열기 → 서버가 자동 검출 격자를 준다
      } else {
        await loadFrameGrid(pitchState, zoomView ? zoomView.idx : 0);
        const stage = document.querySelector("#zoom-modal .stage");
        if (stage) renderGridFit(stage, pitchState);
        wrap.classList.remove("is-manual");
        btn.disabled = false;
      }
    } catch (e) {
      setStatus(t("basePitchFail") + e.message, "err");
      btn.disabled = false;
    }
  });
  toolbar.appendChild(wrap);
}
