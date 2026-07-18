// SPDX-License-Identifier: Apache-2.0
// curator/zoom-editor.js — 확대 편집 모달 — 픽셀 편집·팔레트·마키·팬·실행취소 + 호흡 포커스 모드
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

// 같은 entries/transform truth 를 쓰므로 모달 편집이 그리드 카드에 실시간 반영된다.
// 휠/핀치 = 화면 배율(뷰 확대), 드래그/핸들/Shift+휠 = 기존 스프라이트 편집 그대로.
let zoomView = null; // { stateName, idx, width }

function closeZoom() {
  if (zoomView && zoomView.cleanupBreathe) zoomView.cleanupBreathe();
  pixelEdit = null;
  const modal = document.getElementById("zoom-modal");
  if (modal) modal.remove();
  zoomView = null;
  document.removeEventListener("keydown", onZoomKey);
}

// ── 공용 툴 아이콘 (SVG 라인 아이콘 — 이모지 금지, 양 편집기 공용) ──
const TOOL_ICONS = {
  pen: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="m2 14 .8-3.2L11 2.6l2.4 2.4L5.2 13.2 2 14z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  eraser: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M9.5 2.5 2.8 9.2a1 1 0 0 0 0 1.4l2.6 2.6h4.1l4-4a1 1 0 0 0 0-1.4L9.5 2.5zM5.5 13.2 9.9 8.8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  pick: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M10.6 2a1.9 1.9 0 0 1 2.7 2.7l-1 1 .5.5-1.1 1.1-.5-.5-4.3 4.3-2.4.6.6-2.4 4.3-4.3-.5-.5L10 6.4l-.5-.5 1.1-1.1.5.5 1-1z" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linejoin="round"/></svg>',
  select: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.2" stroke-dasharray="2.4 1.7"/></svg>',
};

// ── 뷰 패닝 — Space+드래그 / 휠버튼(중클릭) 드래그로 화면을 끈다 (수홍 지시
// 2026-07-17: 스프라이트 이동과 별개인 캔버스 시야 이동). 스크롤 컨테이너를
// 당기는 방식이라 페인트/마키보다 먼저(capture) 가로챈다.
let panSpaceHeld = false;

document.addEventListener("keydown", (ev) => {
  if (ev.code !== "Space") return;
  if (!document.getElementById("zoom-modal")) return;
  if (ev.target && ev.target.closest && ev.target.closest("input, textarea, select, button")) return;
  panSpaceHeld = true;
  ev.preventDefault();
});

document.addEventListener("keyup", (ev) => { if (ev.code === "Space") panSpaceHeld = false; });

function wirePan(surface, container) {
  surface.addEventListener("pointerdown", (ev) => {
    if (!(ev.button === 1 || (ev.button === 0 && panSpaceHeld))) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const sx = ev.clientX, sy = ev.clientY;
    const sl = container.scrollLeft, st = container.scrollTop;
    const prevCursor = surface.style.cursor;
    surface.style.cursor = "grabbing";
    const onMove = (e2) => {
      container.scrollLeft = sl - (e2.clientX - sx);
      container.scrollTop = st - (e2.clientY - sy);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      surface.style.cursor = prevCursor;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, true);
}

function clearMarquee() {
  if (pixelEdit) pixelEdit.sel = null;
  document.querySelectorAll(".marquee").forEach((m) => { m.hidden = true; });
}

function onZoomKey(ev) {
  if (ev.key === "Escape") {
    if (pixelEdit && pixelEdit.sel) { clearMarquee(); return; } // 선택 해제가 우선
    if (zoomView && zoomView.breatheCancel) zoomView.breatheCancel(); // Esc = 호흡 적용 취소
    closeZoom();
  }
  else if (ev.key === "ArrowLeft") stepZoomFrame(-1);
  else if (ev.key === "ArrowRight") stepZoomFrame(1);
}

// 실행취소/재실행 단축키 — Cmd/Ctrl+Z, Cmd/Ctrl+Shift+Z (수홍 지시 2026-07-17).
// 활성 편집 컨텍스트로 라우팅: 베이스 에디터 모달이 열려 있으면 그쪽, 아니면
// 줌 모달의 픽셀 편집 세션. 입력 필드에선 브라우저 기본 동작 유지.
document.addEventListener("keydown", (ev) => {
  if (ev.key.toLowerCase() !== "z" || !(ev.metaKey || ev.ctrlKey)) return;
  if (ev.target && ev.target.closest && ev.target.closest("input, textarea, select")) return;
  // 베이스도 줌 모달(pixelEdit)로 통일 — 별도 에디터 분기는 폐기됨 (v1.56.24)
  // 호흡 포커스 모드가 열려 있으면 선/진폭/주기 조정 히스토리가 우선이다 (수홍 2026-07-17)
  const target = zoomView && zoomView.breatheUndo
    ? { undo: zoomView.breatheUndo, redo: zoomView.breatheRedo }
    : (pixelEdit && pixelEdit.undoFn
      ? { undo: pixelEdit.undoFn, redo: pixelEdit.redoFn } : null);
  if (!target) return;
  ev.preventDefault();
  ev.stopImmediatePropagation();
  if (ev.shiftKey) { if (target.redo) target.redo(); }
  else if (target.undo) target.undo();
}, true);

function stepZoomFrame(delta) {
  if (!zoomView || zoomView.stateName === BASE_STATE) return; // 베이스 = 단일 뷰
  if (zoomView.breatheMode) return; // 호흡 포커스 모드: 프레임 이동 없음
  // 표시 순서(order)를 따라 넘긴다 — 복제 인스턴스 카드도 순회에 포함
  const e = entries[zoomView.stateName];
  const present = e.order.filter((i) => {
    const f = frameOf(zoomView.stateName, i);
    return f && f.present;
  });
  if (!present.length) return;
  const pos = present.indexOf(zoomView.idx);
  openZoom(zoomView.stateName, present[(pos + delta + present.length) % present.length], zoomView.width);
}

function openZoom(stateName, idx, keepWidth) {
  closeZoom();
  const frame = frameOf(stateName, idx); // 복제 인스턴스 → 원본 이미지, 자기 변형
  if (!frame || !frame.present) return;
  const isBase = stateName === BASE_STATE; // 베이스도 같은 컴포넌트로 연다 (수홍 지시 2026-07-17)
  // 링크된 복제는 원본 프레임의 편집 세션을 연다 (편집 truth = 원본 하나, 수홍 2026-07-18)
  if (!isBase) idx = editIndex(stateName, idx);
  const [cellW, cellH] = cellDims(stateName);
  const aspect = cellH / cellW;
  const width = keepWidth
    || Math.min(Math.floor(window.innerWidth * 0.8), Math.floor((window.innerHeight * 0.72) / aspect));
  zoomView = { stateName, idx, width };

  const modal = document.createElement("div");
  modal.id = "zoom-modal";
  const label = frame.clone !== undefined
    ? `⧉ ${escapeHtml(STR[lang].cloneBadge(frame.clone))}`
    : (frame.label ? escapeHtml(frame.label) : `#${idx}`);
  modal.innerHTML =
    `<div class="zoom-backdrop"></div>` +
    `<div class="card zoom-card" data-state="${escapeHtml(stateName)}" data-idx="${idx}">` +
    `<div class="zoom-head">` +
    `<span class="zoom-title">${escapeHtml(isBase ? "base" : stateName)}${isBase ? ` · ${cellW}×${cellH}` : ` · ${label}`}</span>` +
    `<span class="row-controls"></span>` +
    (isBase ? "" :
      `<button type="button" class="ghost zoom-prev" data-tip="${t("tZoomPrev")}">⏮</button>` +
      `<button type="button" class="ghost zoom-next" data-tip="${t("tZoomNext")}">⏭</button>`) +
    `<button type="button" class="ghost zoom-close">${t("zoomClose")}</button>` +
    `</div>` +
    `<div class="stage" data-tip="${t("tZoomStage")}">` +
    `<div class="pxgrid"></div>` +
    `<canvas class="ingrid"></canvas>` +
    `<img src="${escapeHtml(frameUrl(stateName, frame))}" alt="frame ${idx}" draggable="false" class="px-upscale" />` +
    `<canvas class="snap-canvas"></canvas>` +
    `<div class="rotate-handle" data-tip="${t("tRotate")}"></div>` +
    `<div class="shear-handle" data-tip="${t("tShear")}"></div>` +
    `</div>` +
    `<div class="card-controls">` +
    `<span class="psize" data-tip="${t("tContentPx")}">${frame.contentSize ? `${frame.contentSize[0]}x${frame.contentSize[1]}px` : ""}</span>` +
    `<span class="tvals"></span>` +
    `<button type="button" class="ghost flip-btn" data-tip="${t("tFlipX")}" aria-label="flip-x">↔</button>` +
    `<button type="button" class="ghost reset-btn" data-tip="${t("tReset")}">↺</button>` +
    `</div>` +
    `</div>`;
  document.body.appendChild(modal);

  const card = modal.querySelector(".zoom-card");
  card.style.setProperty("--cell-aspect", cellW / cellH);
  const stage = card.querySelector(".stage");
  stage.style.width = `${width}px`;

  // 컨트롤: 줄별 토글과 같은 클래스 → sync*Controls 가 카드/모달을 함께 갱신
  const controls = card.querySelector(".row-controls");
  // 완전 동일 계약 (수홍 지시 2026-07-17 "다 똑같이"): 픽셀퍼펙트·격자·변형은
  // 베이스에도 전부. 프레임 전용은 GIF/이전·다음(다중 프레임 개념)뿐.
  if (isBase || gridCapableStates.has(stateName)) controls.appendChild(makeGridToggle(stateName));
  if (isBase || ppTwinStates.has(stateName)) controls.appendChild(makePpToggle(stateName));
  if (!isBase) {
    controls.appendChild(makeGifButton(stateName));
    // 보간 버튼은 줄 헤더 전용 — 확대뷰에선 카드 픽이 불가능해 쓸 수 없다 (수홍 2026-07-17).
    card.querySelector(".zoom-prev").addEventListener("click", () => stepZoomFrame(-1));
    card.querySelector(".zoom-next").addEventListener("click", () => stepZoomFrame(1));
  }
  card.querySelector(".reset-btn").addEventListener("click", () => resetTransform(stateName, idx));
  card.querySelector(".flip-btn").addEventListener("click", () => toggleFlipX(stateName, idx));
  stage.appendChild(makeScaleScrub(stateName, idx));
  card.querySelector(".zoom-close").addEventListener("click", closeZoom);
  modal.querySelector(".zoom-backdrop").addEventListener("click", closeZoom);
  wirePan(stage, card); // Space+드래그/휠버튼 드래그 = 뷰 패닝 (프레임·베이스 공통)

  // ── 픽셀 편집 툴바: 연필/지우개 + 프레임 팔레트 + 컬러피커 + 되돌리기/비우기 ──
  const toolbar = document.createElement("div");
  toolbar.className = "edit-toolbar";
  toolbar.innerHTML =
    `<button type="button" class="ghost et-pen" data-tip="${t("penTool")}">` +
    `${TOOL_ICONS.pen}</button>` +
    `<button type="button" class="ghost et-eraser" data-tip="${t("eraserTool")}">${TOOL_ICONS.eraser}</button>` +
    `<button type="button" class="ghost et-pick" data-tip="${t("tPick")}">${TOOL_ICONS.pick}</button>` +
    `<button type="button" class="ghost et-select" data-tip="${t("tSelectTool")}">${TOOL_ICONS.select}</button>` +
    `<button type="button" class="ghost et-undo" data-tip="${t("tUndoKeys")}">${t("undoEdit")}</button>` +
    `<button type="button" class="ghost et-redo" data-tip="${t("tRedoKeys")}">${t("redoEdit")}</button>` +
    `<button type="button" class="ghost et-clear">${t("clearEdits")}</button>`;
  card.insertBefore(toolbar, stage);
  if (isBase) {
    // 베이스는 사이드카가 아니라 파일에 굽는다 — 명시 저장 (원본 .orig 1회 백업)
    const saveBtn = document.createElement("button");
    saveBtn.className = "et-basesave";
    saveBtn.setAttribute("data-tip", t("tBaseEdit"));
    saveBtn.textContent = t("baseEditSave");
    saveBtn.addEventListener("click", async () => {
      const ops = (entries[BASE_STATE].pixels && entries[BASE_STATE].pixels[0]) || {};
      const tr = entries[BASE_STATE].transforms[0];
      const trDirty = tr && (tr.rotate || tr.scale !== 1 || tr.dx || tr.dy || tr.shx || tr.shy || tr.flipX);
      if (!Object.keys(ops).length && !trDirty) { closeZoom(); return; }
      saveBtn.disabled = true;
      try {
        const res = await fetch("/api/base-edit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ops, space: "logical", transform: trDirty ? tr : null }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || res.status);
        entries[BASE_STATE].pixels = {};
        entries[BASE_STATE].transforms = {}; // 변형은 파일에 구워졌다 — 표시 변형 초기화
        pixelEdit = null;
        setStatus(STR[lang].baseEditSaved(data.applied), "ok");
        closeZoom();
        const baseImg = document.querySelector(".base-row .base-stage img");
        if (baseImg) baseImg.src = run.baseUrl + (run.baseUrl.includes("?") ? "&" : "?") + "v=" + Date.now();
      } catch (e) {
        setStatus(t("baseEditFail") + e.message, "err");
        saveBtn.disabled = false;
      }
    });
    toolbar.appendChild(saveBtn);
  }
  const penBtn = toolbar.querySelector(".et-pen");
  const eraserBtn = toolbar.querySelector(".et-eraser");
  const pickBtn = toolbar.querySelector(".et-pick");
  const selectBtn = toolbar.querySelector(".et-select");
  // ── 팔레트 도크 (Aseprite 식, 수홍 지시 2026-07-17): 스테이지 왼쪽 세로 팔레트 —
  // 위 = 현재 쓰인 색 전부 (같은 색 1개, 빈도순), 아래 = 자유 색상 피커.
  const stageRow = document.createElement("div");
  stageRow.className = "stage-row";
  stage.parentNode.insertBefore(stageRow, stage);
  const dock = document.createElement("div");
  dock.className = "palette-dock";
  const swatchBox = document.createElement("div");
  swatchBox.className = "palette-swatches";
  const colorInput = document.createElement("input");
  colorInput.type = "color";
  colorInput.value = "#1f2430";
  colorInput.title = "color";
  colorInput.className = "et-color";
  dock.appendChild(swatchBox);
  dock.appendChild(colorInput);
  stageRow.appendChild(dock);
  stageRow.appendChild(stage);
  const selBox = document.createElement("div");
  selBox.className = "marquee";
  selBox.hidden = true;
  stage.appendChild(selBox);
  const syncMarqueeBox = () => {
    const sel = pixelEdit && pixelEdit.sel;
    selBox.hidden = !sel;
    if (!sel) return;
    selBox.style.left = `${(sel.x0 / cellW) * 100}%`;
    selBox.style.top = `${(sel.y0 / cellH) * 100}%`;
    selBox.style.width = `${((sel.x1 - sel.x0) / cellW) * 100}%`;
    selBox.style.height = `${((sel.y1 - sel.y0) / cellH) * 100}%`;
  };
  const syncToolbar = () => {
    penBtn.classList.toggle("active", !!pixelEdit && pixelEdit.tool === "pen");
    eraserBtn.classList.toggle("active", !!pixelEdit && pixelEdit.tool === "eraser");
    pickBtn.classList.toggle("active", !!pixelEdit && pixelEdit.tool === "pick");
    selectBtn.classList.toggle("active", !!pixelEdit && pixelEdit.tool === "select");
    stage.classList.toggle("pixel-editing", !!pixelEdit);
    stage.classList.toggle("picking", !!pixelEdit && pixelEdit.tool === "pick");
    stage.classList.toggle("selecting", !!pixelEdit && pixelEdit.tool === "select");
    syncMarqueeBox();
  };
  const setTool = (tool) => {
    if (pixelEdit && pixelEdit.tool === tool) pixelEdit = null; // 같은 툴 재클릭 = 끔
    else pixelEdit = { state: stateName, idx, tool, color: colorInput.value,
                       journal: (pixelEdit && pixelEdit.journal) || [],
                       redo: (pixelEdit && pixelEdit.redo) || [],
                       sel: tool === "select" ? (pixelEdit && pixelEdit.sel) || null : null,
                       undoFn: () => undoPixel(), redoFn: () => redoPixel() };
    syncToolbar();
    applyFrameTransformAll(stateName, idx); // 편집 모드 = identity 표시 전환
  };
  penBtn.addEventListener("click", () => setTool("pen"));
  eraserBtn.addEventListener("click", () => setTool("eraser"));
  pickBtn.addEventListener("click", () => setTool("pick"));
  selectBtn.addEventListener("click", () => setTool("select"));
  colorInput.addEventListener("input", () => { if (pixelEdit) pixelEdit.color = colorInput.value; });

  // 스포이드 표본: 현재 표시 픽셀(베이스 이미지 + 이미 적용한 편집)의 색을 (x,y)에서 읽는다.
  // 편집(ops)이 우선 — 방금 찍은 색도 다시 집을 수 있게. 투명/지운 픽셀은 null.
  const sampleColor = (x, y) => {
    const ops = entries[stateName].pixels[idx];
    const key = `${x},${y}`;
    if (ops && key in ops) {
      const v = ops[key];
      return typeof v === "string" && v.startsWith("#") ? v.slice(0, 7) : null;
    }
    const imgEl = editSourceFor(stateName, stage.querySelector("img"));
    if (!(imgEl && imgEl.complete && imgEl.naturalWidth)) return null;
    const tmp = sampleColor._c || (sampleColor._c = document.createElement("canvas"));
    tmp.width = cellW; tmp.height = cellH;
    const c2 = tmp.getContext("2d");
    c2.imageSmoothingEnabled = false;
    c2.clearRect(0, 0, tmp.width, tmp.height);
    c2.drawImage(imgEl, 0, 0, tmp.width, tmp.height);
    const d = c2.getImageData(x, y, 1, 1).data;
    if (d[3] < 40) return null; // 투명 픽셀은 집을 색이 없다
    return "#" + [d[0], d[1], d[2]].map((v) => v.toString(16).padStart(2, "0")).join("");
  };
  // 저널은 스트로크(액션) 단위 {sets: [{key, had, prev, value}]} — undo 는 통째로
  // 되돌리고 redo 스택에 쌓는다. 새 액션이 생기면 redo 는 비운다 (표준 편집기 계약).
  const undoPixel = () => {
    if (!pixelEdit || !pixelEdit.journal.length) return;
    const j = pixelEdit.journal.pop();
    const e = entries[stateName];
    const ops = e.pixels[idx] || (e.pixels[idx] = {});
    if (j.full) { j.after = { ...(e.pixels[idx] || {}) }; e.pixels[idx] = j.full; }
    else if (j.sets) {
      for (let i = j.sets.length - 1; i >= 0; i--) {
        const s = j.sets[i];
        if (s.had) ops[s.key] = s.prev;
        else delete ops[s.key];
      }
    }
    pixelEdit.redo.push(j);
    applyFrameTransformAll(stateName, idx);
    scheduleSave();
    buildPalette();
  };
  const redoPixel = () => {
    if (!pixelEdit || !pixelEdit.redo.length) return;
    const j = pixelEdit.redo.pop();
    const e = entries[stateName];
    if (j.full) e.pixels[idx] = j.after || {};
    else if (j.sets) {
      const ops = e.pixels[idx] || (e.pixels[idx] = {});
      for (const s of j.sets) ops[s.key] = s.value;
    }
    pixelEdit.journal.push(j);
    applyFrameTransformAll(stateName, idx);
    scheduleSave();
    buildPalette();
  };
  toolbar.querySelector(".et-undo").addEventListener("click", undoPixel);
  toolbar.querySelector(".et-redo").addEventListener("click", redoPixel);
  toolbar.querySelector(".et-clear").addEventListener("click", () => {
    const e = entries[stateName];
    const ops = e.pixels[idx];
    if (!ops || !Object.keys(ops).length) return;
    if (pixelEdit) { pixelEdit.journal.push({ full: { ...ops } }); pixelEdit.redo.length = 0; }
    e.pixels[idx] = {};
    applyFrameTransformAll(stateName, idx);
    scheduleSave();
  });
  // 프레임 고유색 팔레트 (빈도순 상위 12)
  const buildPalette = () => {
    const imgEl = editSourceFor(stateName, stage.querySelector("img"));
    if (!(imgEl && imgEl.complete && imgEl.naturalWidth)) {
      if (imgEl) imgEl.addEventListener("load", buildPalette, { once: true });
      return;
    }
    // 합성(원본+편집) 기준 — 방금 찍은 색도 팔레트에 나타난다. 같은 색 1개, 빈도순.
    const counts = new Map();
    {
      const cx = compositeCell();
      const data = cx.getImageData(0, 0, cellW, cellH).data;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] < 200) continue;
        const hex = "#" + [data[i], data[i + 1], data[i + 2]].map((v) => v.toString(16).padStart(2, "0")).join("");
        counts.set(hex, (counts.get(hex) || 0) + 1);
      }
    }
    if (isBase) {
      // 근접색 병합 — raw 유래 미세 변종(0,0,0 vs 1,1,1)을 대표색 1개로 (tol 24 실측:
      // 426→18색). 크로마 배경색은 제외 (지우개가 그 역할).
      const cornerHex = (() => {
        const cx2 = compositeCell();
        const d = cx2.getImageData(0, 0, 1, 1).data;
        return [d[0], d[1], d[2]];
      })();
      const reps = [];
      for (const [hex, n] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b2 = parseInt(hex.slice(5, 7), 16);
        if (Math.abs(r - cornerHex[0]) + Math.abs(g - cornerHex[1]) + Math.abs(b2 - cornerHex[2]) <= 60) continue;
        const hit = reps.find((q) => Math.abs(q.r - r) + Math.abs(q.g - g) + Math.abs(q.b - b2) <= 24);
        if (hit) hit.n += n;
        else reps.push({ hex, r, g, b: b2, n });
      }
      counts.clear();
      for (const q of reps) counts.set(q.hex, q.n);
    }
    swatchBox.innerHTML = "";
    for (const [hex] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "swatch";
      b.style.background = hex;
      b.setAttribute("data-tip", hex);
      b.classList.toggle("current", hex === colorInput.value);
      b.addEventListener("click", () => {
        colorInput.value = hex;
        swatchBox.querySelectorAll(".swatch.current").forEach((s) => s.classList.remove("current"));
        b.classList.add("current");
        if (pixelEdit) { pixelEdit.color = hex; if (pixelEdit.tool !== "pen") setTool("pen"); }
        else setTool("pen"), (pixelEdit.color = hex);
      });
      swatchBox.appendChild(b);
    }
  };

  // 마키(영역 선택) 헬퍼 — 캡처는 "현재 보이는 픽셀"(원본 + 편집 합성) 기준
  const compositeCell = () => {
    const c = document.createElement("canvas");
    c.width = cellW;
    c.height = cellH;
    const cx = c.getContext("2d");
    cx.imageSmoothingEnabled = false;
    const imgEl = editSourceFor(stateName, stage.querySelector("img"));
    if (imgEl && imgEl.complete && imgEl.naturalWidth) cx.drawImage(imgEl, 0, 0, c.width, c.height);
    const ops0 = entries[stateName].pixels[idx] || {};
    for (const [key, val] of Object.entries(ops0)) {
      const [x, y] = key.split(",").map(Number);
      if (val) { cx.fillStyle = val; cx.fillRect(x, y, 1, 1); }
      else cx.clearRect(x, y, 1, 1);
    }
    return cx;
  };
  const captureRegion = (sel) => {
    const cx = compositeCell();
    let chromaRef = null;
    if (isBase) {
      const d0 = cx.getImageData(0, 0, 1, 1).data; // 베이스 배경(크로마)은 내용이 아니다
      chromaRef = [d0[0], d0[1], d0[2]];
    }
    const data = cx.getImageData(sel.x0, sel.y0, sel.x1 - sel.x0, sel.y1 - sel.y0).data;
    const pixels = [];
    const w = sel.x1 - sel.x0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] < 40) continue; // 투명은 옮길 내용 없음
      if (chromaRef && Math.abs(data[i] - chromaRef[0]) + Math.abs(data[i + 1] - chromaRef[1]) + Math.abs(data[i + 2] - chromaRef[2]) <= 60) continue;
      pixels.push({
        dx: (i / 4) % w,
        dy: Math.floor((i / 4) / w),
        hex: "#" + [data[i], data[i + 1], data[i + 2]].map((v) => v.toString(16).padStart(2, "0")).join(""),
      });
    }
    return pixels;
  };
  const commitRegionMove = (fromSel, pixels, ddx, ddy, duplicate) => {
    if (!pixels.length || (ddx === 0 && ddy === 0)) return;
    const staged = new Map(); // key -> value (이동: 원본 지움 먼저, 목적지 색이 덮음)
    if (!duplicate) {
      for (const p of pixels) staged.set(`${fromSel.x0 + p.dx},${fromSel.y0 + p.dy}`, null);
    }
    for (const p of pixels) {
      const x = fromSel.x0 + p.dx + ddx;
      const y = fromSel.y0 + p.dy + ddy;
      if (x >= 0 && x < cellW && y >= 0 && y < cellH) staged.set(`${x},${y}`, p.hex);
    }
    const e = entries[stateName];
    const ops = e.pixels[idx] || (e.pixels[idx] = {});
    const sets = [];
    for (const [key, value] of staged) {
      if (key in ops && ops[key] === value) continue;
      sets.push({ key, had: key in ops, prev: ops[key], value });
      ops[key] = value;
    }
    if (!sets.length) return;
    pixelEdit.journal.push({ sets });
    pixelEdit.redo.length = 0;
    applyFrameTransformAll(stateName, idx);
    scheduleSave();
    buildPalette();
  };

  buildPalette();

  // 페인트: 편집 툴 활성 시 스테이지 드래그는 그리기 (다른 핸들러보다 먼저 등록해 가로챔)
  stage.addEventListener("pointerdown", (ev) => {
    if (!pixelEdit || pixelEdit.state !== stateName || pixelEdit.idx !== idx) return;
    if (ev.button || !ev.isPrimary) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const cellX = (e2) => Math.floor(((e2.clientX - stage.getBoundingClientRect().left) / stage.getBoundingClientRect().width) * cellW);
    const cellY = (e2) => Math.floor(((e2.clientY - stage.getBoundingClientRect().top) / stage.getBoundingClientRect().height) * cellH);
    // 스포이드: 색만 집고 바로 연필로 전환 (드래그 페인트 아님). 투명 픽셀은 무시.
    if (pixelEdit.tool === "pick") {
      const x = cellX(ev), y = cellY(ev);
      if (x >= 0 && x < cellW && y >= 0 && y < cellH) {
        const hex = sampleColor(x, y);
        if (hex) {
          colorInput.value = hex;
          pixelEdit.color = hex;
          pixelEdit.tool = "pen"; // 집은 색으로 즉시 그리게
          syncToolbar();
          setStatus(`${t("pickTool")}: ${hex}`, "ok");
        }
      }
      return;
    }
    try { stage.setPointerCapture(ev.pointerId); } catch { /* 일부 펜/합성 포인터 */ }
    const s = snapScaleFor(stateName) || 1;
    const finish = (onMove, onUp) => {
      try { stage.releasePointerCapture(ev.pointerId); } catch { /* no-op */ }
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("pointerup", onUp);
    };

    // ── 선택(마키): 점선 드래그로 영역 선택 → 안쪽 드래그 = 이동, Alt+드래그 = 복제 ──
    if (pixelEdit.tool === "select") {
      const sx = cellX(ev), sy = cellY(ev);
      const sel = pixelEdit.sel;
      const inside = sel && sx >= sel.x0 && sx < sel.x1 && sy >= sel.y0 && sy < sel.y1;
      if (inside) {
        const grab = captureRegion(sel);
        const from = { ...sel };
        let delta = [0, 0];
        const onMove = (e2) => {
          const step = s;
          delta = [
            Math.round((cellX(e2) - sx) / step) * step,
            Math.round((cellY(e2) - sy) / step) * step,
          ];
          pixelEdit.sel = { x0: from.x0 + delta[0], y0: from.y0 + delta[1],
                            x1: from.x1 + delta[0], y1: from.y1 + delta[1] };
          syncMarqueeBox();
        };
        const onUp = (e2) => {
          finish(onMove, onUp);
          commitRegionMove(from, grab, delta[0], delta[1], e2.altKey);
          syncMarqueeBox();
        };
        stage.addEventListener("pointermove", onMove);
        stage.addEventListener("pointerup", onUp);
      } else {
        const norm = (ax, ay, bx, by) => {
          const clampX = (v) => Math.max(0, Math.min(cellW, v));
          const clampY = (v) => Math.max(0, Math.min(cellH, v));
          const x0 = Math.floor(Math.min(ax, bx) / s) * s;
          const y0 = Math.floor(Math.min(ay, by) / s) * s;
          const x1 = Math.ceil((Math.max(ax, bx) + 1) / s) * s;
          const y1 = Math.ceil((Math.max(ay, by) + 1) / s) * s;
          return { x0: clampX(x0), y0: clampY(y0), x1: clampX(x1), y1: clampY(y1) };
        };
        pixelEdit.sel = norm(sx, sy, sx, sy);
        syncMarqueeBox();
        const onMove = (e2) => { pixelEdit.sel = norm(sx, sy, cellX(e2), cellY(e2)); syncMarqueeBox(); };
        const onUp = () => finish(onMove, onUp);
        stage.addEventListener("pointermove", onMove);
        stage.addEventListener("pointerup", onUp);
      }
      return;
    }

    const e = entries[stateName];
    if (!e.pixels[idx]) e.pixels[idx] = {};
    const ops = e.pixels[idx];
    const stroke = []; // 스트로크(드래그 1회) 단위 액션 — undo/redo 가 통째로 다룬다
    const paint = (e2) => {
      const r = stage.getBoundingClientRect();
      const x = Math.floor(((e2.clientX - r.left) / r.width) * cellW);
      const y = Math.floor(((e2.clientY - r.top) / r.height) * cellH);
      if (!(x >= 0 && x < cellW && y >= 0 && y < cellH)) return;
      const bx = Math.floor(x / s) * s;
      const by = Math.floor(y / s) * s;
      const value = pixelEdit.tool === "eraser" ? null : pixelEdit.color;
      let changed = false;
      for (let dy = 0; dy < s; dy++) {
        for (let dx = 0; dx < s; dx++) {
          const key = `${bx + dx},${by + dy}`;
          if (ops[key] === value && key in ops) continue;
          stroke.push({ key, had: key in ops, prev: ops[key], value });
          ops[key] = value;
          changed = true;
        }
      }
      if (changed) applyFrameTransformAll(stateName, idx);
    };
    paint(ev);
    const onMove = (e2) => paint(e2);
    const onUp = () => {
      finish(onMove, onUp);
      if (stroke.length) {
        pixelEdit.journal.push({ sets: stroke });
        pixelEdit.redo.length = 0;
        buildPalette();
      }
      scheduleSave();
    };
    stage.addEventListener("pointermove", onMove);
    stage.addEventListener("pointerup", onUp);
  });

  // 뷰 확대: 휠/핀치(ctrl+휠). wireStage 의 휠(스프라이트 스케일)보다 먼저 등록해
  // 가로채고, Shift+휠만 스프라이트 스케일로 통과시킨다.
  stage.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
    zoomView.width = Math.min(Math.floor(window.innerWidth * 0.9),
      Math.max(120, Math.round(zoomView.width * factor)));
    stage.style.width = `${zoomView.width}px`;
    applyCardTransform(stage, stateName, idx);
    sizePxGrids();
  }, { passive: false });

  // ── 호흡 모드 (레이어, 수홍 확정 2026-07-18): 실제 시퀀스가 재생되는 위에
  // 분할선·진폭·주기·서브픽셀을 조정하면 즉시 truth(사이드카)에 반영된다.
  // 깜빡임 프레임도 그대로 숨쉰다 (직교 레이어). 굽기/재추출/적용 대기 없음.
  if (!isBase && pendingBreathe) {
    pendingBreathe = false;
    card.classList.add("breathe-focus");
    zoomView.breatheMode = true;
    stage.addEventListener("pointerdown", (ev) => {
      if (!ev.target.closest(".breathe-line")) {
        ev.stopImmediatePropagation();
        ev.preventDefault();
      }
    }, true);
    const st0 = run.states.find((s) => s.name === stateName);
    const e0 = entries[stateName];
    const beforeCfg = e0.breathe ? JSON.parse(JSON.stringify(e0.breathe)) : null; // Esc 복원
    const bm = { cfg: null, geomReady: false, hist: [], histPos: -1, cancelled: false, tick: 0 };
    const clone = (o) => JSON.parse(JSON.stringify(o));
    const commit = () => { // 조정 = 즉시 truth 반영 + 디바운스 저장 (수홍: 적용 대기 금지)
      if (bm.cancelled) return;
      e0.breathe = clone(bm.cfg);
      scheduleSave();
      if (typeof scheduleStrip === "function") scheduleStrip();
    };
    const pushHist = () => {
      bm.hist = bm.hist.slice(0, bm.histPos + 1);
      bm.hist.push(clone(bm.cfg));
      bm.histPos = bm.hist.length - 1;
    };
    const restoreHist = (pos) => {
      if (pos < 0 || pos >= bm.hist.length || pos === bm.histPos) return;
      bm.histPos = pos;
      bm.cfg = clone(bm.hist[pos]);
      syncBreatheControls();
      syncLines();
      commit();
    };
    bm.cfg = e0.breathe ? clone(e0.breathe) : { splits: [0.55], amplitude: 1, breaths: 1, subpixel: false };

    // 실루엣 지오메트리 (선 표시 기준): 이미지 로드 전 빈 합성이면 재시도
    // (실사고 2026-07-17 로드 레이스 — 선이 바닥에 붙고 드래그 죽음)
    let btop = 0;
    let bh = 1;

    // 분할선들 — 드래그 = 즉시 반영, 놓으면 히스토리 스냅샷
    const lineEls = [];
    function wireLine(ln) {
      ln.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        if (!bm.geomReady) return;
        ln.setPointerCapture(ev.pointerId);
        const li = lineEls.indexOf(ln);
        const onMove = (e2) => {
          const r = stage.getBoundingClientRect();
          const yCell = ((e2.clientY - r.top) / r.height) * cellH;
          bm.cfg.splits[li] = Math.min(0.9, Math.max(0.1, Math.round(((yCell - btop) / bh) * 100) / 100));
          syncLines();
          commit();
        };
        const onUp = () => {
          ln.removeEventListener("pointermove", onMove);
          ln.removeEventListener("pointerup", onUp);
          bm.cfg.splits.sort((a, b) => a - b); // 선 순서 불변식: 위→아래 오름차순
          syncLines();
          commit();
          pushHist();
        };
        ln.addEventListener("pointermove", onMove);
        ln.addEventListener("pointerup", onUp);
      });
    }
    const syncLines = () => {
      while (lineEls.length > bm.cfg.splits.length) lineEls.pop().remove();
      while (lineEls.length < bm.cfg.splits.length) {
        const ln = document.createElement("div");
        ln.className = "breathe-line";
        ln.setAttribute("data-tip", t("breatheHint"));
        wireLine(ln);
        stage.appendChild(ln);
        lineEls.push(ln);
      }
      bm.cfg.splits.forEach((s, i) => {
        lineEls[i].style.top = `${((btop + s * bh) / cellH) * 100}%`;
      });
    };

    // 시퀀스 재생 미리보기 — 실제 재생 순서(playList: 깜빡임 포함) 위에 위상을 얹는다.
    const bcanvas = stage.querySelector(".snap-canvas");
    const bImg = stage.querySelector("img");
    const renderTick = () => {
      if (!bcanvas) return;
      const play = playList(stateName);
      const pattern = breathePattern(bm.cfg, play.length || 1);
      const cursor = play.length ? bm.tick % play.length : 0;
      const frameIdx = play.length ? play[cursor] : idx;
      const phase = pattern[cursor] || 0;
      const f = frameOf(stateName, frameIdx);
      // 호흡 프리뷰 = 굽기 결과 미리보기 — 항상 캐노니컬 프레임(f.url)으로 그린다.
      // 표시 변형(원본 쌍둥이)은 풋프린트가 달라 이음새가 프레임마다 다른 줄에 생겨
      // 선이 두 개처럼 흔들렸다 (실사고 2026-07-18 수홍).
      const image = f ? img(f.url) : null;
      bImg.style.visibility = "hidden";
      bcanvas.style.display = "block";
      bcanvas.width = cellW;
      bcanvas.height = cellH;
      const bctx = bcanvas.getContext("2d");
      bctx.imageSmoothingEnabled = false;
      bctx.clearRect(0, 0, cellW, cellH);
      if (!(image && image.complete && image.naturalWidth)) return;
      const base = document.createElement("canvas");
      base.width = cellW;
      base.height = cellH;
      const baseCtx = base.getContext("2d");
      baseCtx.imageSmoothingEnabled = false;
      drawFrameInto(baseCtx, image, getTransform(stateName, frameIdx), cellW, cellH,
        snapScaleFor(stateName), getPixelOps(stateName, frameIdx));
      bctx.drawImage(breatheComposite(base, bm.cfg, phase), 0, 0);
    };
    // 재생 타이밍 = 줄 프리뷰와 동일 계약: 현재 fps × 줄 배속(pv.speed)
    // (수홍 2026-07-18 "배속 해둔 거 확대해서도 배속으로"; fps 는 줄 스텝퍼로 실시간 변경)
    const tickLoop = () => {
      bm.tick += 1;
      renderTick();
      const live = run.states.find((s) => s.name === stateName);
      const fps = (live && live.fps) || 6;
      const speed = (previews[stateName] && previews[stateName].speed) || 1;
      bm.timer = setTimeout(tickLoop, Math.max(40, 1000 / Math.max(0.1, fps * speed)));
    };
    tickLoop();

    const initSilhouette = () => {
      if (bm.geomReady) return true;
      // 지오메트리 truth = 캐노니컬 프레임 contentBox (굽기가 쓰는 그 좌표계).
      // 표시 변형(원본 쌍둥이)은 실루엣이 달라 선이 "최종선이 아닌" 곳에 그려졌다
      // (실사고 2026-07-18 수홍). contentBox 는 /api/run 이 즉시 주므로 레이스도 없다.
      const fr = frameOf(stateName, idx);
      if (fr && fr.contentBox) {
        btop = fr.contentBox[1];
        bh = Math.max(1, fr.contentBox[3] - fr.contentBox[1]);
        if (!e0.breathe && !beforeCfg && bm.histPos <= 0) {
          const cxh = compositeCell();
          const silh = silhouetteStats(cxh.getImageData(0, 0, cellW, cellH).data, cellW, cellH);
          if (silh.top < cellH) bm.cfg.splits = [Math.round(silh.split * 100) / 100];
          if (bm.histPos === 0) bm.hist[0] = clone(bm.cfg);
        }
        bm.geomReady = true;
        syncLines();
        renderTick();
        commit();
        rebuildStrip();
        return true;
      }
      const cx0 = compositeCell();
      const sil = silhouetteStats(cx0.getImageData(0, 0, cellW, cellH).data, cellW, cellH);
      if (sil.top >= cellH) return false; // 불투명 픽셀 0 — 아직 빈 합성
      btop = sil.top;
      bh = sil.h;
      if (!e0.breathe && !beforeCfg && bm.histPos <= 0) {
        bm.cfg.splits = [Math.round(sil.split * 100) / 100]; // 휴리스틱 가슴선
        if (bm.histPos === 0) bm.hist[0] = clone(bm.cfg);
      }
      bm.geomReady = true;
      syncLines();
      renderTick();
      commit(); // 에디터 오픈 = 레이어 활성 (Esc 로 이전 상태 복원 가능)
      rebuildStrip();
      return true;
    };
    // 최종 굽기 필름스트립 (수홍 요청 2026-07-18 "최종 프레임들을 나열해서"):
    // 시퀀스×호흡주기 LCM 전개를 그대로 보여준다 — GIF/아틀라스에 구워질 순서.
    const strip = document.createElement("div");
    strip.className = "breathe-strip";
    // 스테이지 플렉스 행(.stage-row) 밖, 그 아래에 — 행 안에 넣으면 스테이지가 눌린다
    (stage.closest(".stage-row") || stage).insertAdjacentElement("afterend", strip);
    let stripTimer = null;
    const rebuildStrip = () => {
      const play = playList(stateName);
      const pattern = breathePattern(bm.cfg, play.length);
      strip.innerHTML = "";
      if (!play.length) return;
      const total = play.length; // 루프 불변 — 시퀀스 그대로 (수홍 확정)
      const actualBreaths = breatheFitCount(bm.cfg, play.length);
      const cap = document.createElement("div");
      cap.className = "bs-caption";
      cap.textContent = STR[lang].breatheStrip(play.length, actualBreaths, bm.cfg.breaths || 1);
      strip.appendChild(cap);
      const rowEl = document.createElement("div");
      rowEl.className = "bs-frames";
      for (let i = 0; i < total; i++) {
        const frameIdx = play[i];
        const phase = pattern[i] || 0;
        const f = frameOf(stateName, frameIdx);
        const image = f ? img(frameUrl(stateName, f)) : null;
        const cellEl = document.createElement("div");
        cellEl.className = "bs-cell";
        const cv = document.createElement("canvas");
        cv.width = cellW;
        cv.height = cellH;
        cv.className = "px-upscale";
        const cctx = cv.getContext("2d");
        cctx.imageSmoothingEnabled = false;
        if (image && image.complete && image.naturalWidth) {
          const b = document.createElement("canvas");
          b.width = cellW;
          b.height = cellH;
          const bx = b.getContext("2d");
          bx.imageSmoothingEnabled = false;
          drawFrameInto(bx, image, getTransform(stateName, frameIdx), cellW, cellH,
            snapScaleFor(stateName), getPixelOps(stateName, frameIdx));
          cctx.drawImage(breatheComposite(b, bm.cfg, phase), 0, 0);
        }
        cellEl.appendChild(cv);
        const capEl = document.createElement("span");
        capEl.className = "bs-cap";
        const phaseTxt = Number.isInteger(phase) ? String(phase) : phase.toFixed(1);
        capEl.textContent = `${frameDisplayName(stateName, frameIdx)} · P${phaseTxt}`;
        cellEl.appendChild(capEl);
        rowEl.appendChild(cellEl);
      }
      strip.appendChild(rowEl);
    };
    const scheduleStrip = () => {
      clearTimeout(stripTimer);
      stripTimer = setTimeout(rebuildStrip, 220);
    };

    const bar = document.createElement("div");
    bar.className = "breathe-bar";
    const mkSel = (values, current, fmt, onchange) => {
      const sel = document.createElement("select");
      for (const v of values) {
        const o = document.createElement("option");
        o.value = String(v);
        o.textContent = fmt(v);
        if (v === current) o.selected = true;
        sel.appendChild(o);
      }
      sel.addEventListener("change", () => onchange(parseInt(sel.value, 10)));
      return sel;
    };
    const lineBtns = document.createElement("span");
    lineBtns.className = "breathe-linectl";
    lineBtns.title = t("tBreatheLines");
    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.textContent = t("breatheLineAdd");
    addBtn.addEventListener("click", () => {
      if (bm.cfg.splits.length >= 3) return;
      bm.cfg.splits.unshift(Math.max(0.1, bm.cfg.splits[0] - 0.18)); // 새 선은 최상단 위로
      bm.cfg.splits.sort((a, b) => a - b);
      syncLines();
      commit();
      pushHist();
    });
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.textContent = t("breatheLineDel");
    delBtn.addEventListener("click", () => {
      if (bm.cfg.splits.length <= 1) return;
      bm.cfg.splits.shift(); // 맨 위 선부터 제거 — 최하단(기본 가슴선) 유지
      syncLines();
      commit();
      pushHist();
    });
    lineBtns.appendChild(addBtn);
    lineBtns.appendChild(delBtn);
    bar.appendChild(lineBtns);
    const ampSel = mkSel([1, 2], bm.cfg.amplitude,
      (v) => `${t("breatheAmp")} ${v}px`,
      (v) => { bm.cfg.amplitude = v || 1; commit(); pushHist(); });
    // 호흡 횟수: 숫자 입력 + −/+ 스텝퍼 (수홍 2026-07-18 — 약수 셀렉트 대신 자유 입력;
    // 나눠떨어지지 않으면 fit_breathe_pattern 이 보정하고 스트립 캡션이 알려준다)
    const breathWrap = document.createElement("span");
    breathWrap.className = "breathe-count";
    breathWrap.title = STR[lang].tBreatheCount;
    const breathLabel = document.createElement("span");
    breathLabel.textContent = t("breatheCountLabel");
    const minusBtn = document.createElement("button");
    minusBtn.type = "button";
    minusBtn.textContent = "−";
    const breathInput = document.createElement("input");
    breathInput.type = "number";
    breathInput.min = "1";
    breathInput.step = "1";
    breathInput.value = String(bm.cfg.breaths || 1);
    const plusBtn = document.createElement("button");
    plusBtn.type = "button";
    plusBtn.textContent = "+";
    const setBreaths = (v) => {
      const seqLen = playList(stateName).length || 1;
      const clamped = Math.max(1, Math.min(Math.max(1, seqLen), Math.round(Number(v)) || 1));
      bm.cfg.breaths = clamped;
      breathInput.value = String(clamped);
      commit();
      pushHist();
      syncFitBadge();
    };
    minusBtn.addEventListener("click", () => setBreaths((bm.cfg.breaths || 1) - 1));
    plusBtn.addEventListener("click", () => setBreaths((bm.cfg.breaths || 1) + 1));
    breathInput.addEventListener("change", () => setBreaths(breathInput.value));
    const fitBadge = document.createElement("span");
    fitBadge.className = "breathe-fit-badge";
    const syncFitBadge = () => {
      const seqLen = playList(stateName).length || 1;
      const fitted = breatheFitCount(bm.cfg, seqLen);
      const want = bm.cfg.breaths || 1;
      // 요청 횟수가 루프에 안 나눠떨어지면 실제 적용 횟수를 그 자리에서 보여준다
      fitBadge.textContent = fitted === want ? "" : STR[lang].breatheFitted(fitted);
      fitBadge.title = fitted === want ? "" : STR[lang].tBreatheFitted(seqLen);
    };
    breathWrap.appendChild(breathLabel);
    breathWrap.appendChild(minusBtn);
    breathWrap.appendChild(breathInput);
    breathWrap.appendChild(plusBtn);
    breathWrap.appendChild(fitBadge);
    bar.appendChild(ampSel);
    bar.appendChild(breathWrap);
    const subWrap = document.createElement("label");
    subWrap.className = "breathe-subpixel";
    subWrap.title = t("tBreatheSub");
    const subCheck = document.createElement("input");
    subCheck.type = "checkbox";
    subCheck.checked = !!bm.cfg.subpixel;
    subCheck.addEventListener("change", () => {
      bm.cfg.subpixel = subCheck.checked;
      commit();
      pushHist();
    });
    subWrap.appendChild(subCheck);
    subWrap.appendChild(Object.assign(document.createElement("span"), { textContent: t("breatheSub") }));
    bar.appendChild(subWrap);
    toolbar.appendChild(bar);
    syncFitBadge();
    function syncBreatheControls() {
      ampSel.value = String(bm.cfg.amplitude);
      breathInput.value = String(bm.cfg.breaths || 1);
      subCheck.checked = !!bm.cfg.subpixel;
      syncFitBadge();
    }

    // Esc = 취소 (열기 전 설정으로 복원 — 없었으면 레이어 해제)
    // 지오메트리 초기화는 스트립/컨트롤 선언 뒤에 — commit→scheduleStrip TDZ 방지
    syncLines();
    if (!initSilhouette()) {
      let geomTries = 0;
      const retryGeom = () => {
        if (bm.cancelled || !document.body.contains(stage)) return;
        if (!initSilhouette() && ++geomTries < 50) setTimeout(retryGeom, 120);
      };
      if (bImg) bImg.addEventListener("load", () => initSilhouette(), { once: true });
      setTimeout(retryGeom, 120);
    }

    zoomView.breatheCancel = () => {
      bm.cancelled = true;
      e0.breathe = beforeCfg;
      scheduleSave();
      rebuildState(stateName);
    };
    zoomView.breatheUndo = () => restoreHist(bm.histPos - 1);
    zoomView.breatheRedo = () => restoreHist(bm.histPos + 1);
    pushHist(); // 히스토리 바닥 = 오픈 시점 설정
    zoomView.cleanupBreathe = () => {
      clearTimeout(bm.timer);
      clearTimeout(stripTimer);
      if (!bm.cancelled) rebuildState(stateName); // 줄 미리보기·토글 상태 동기화
    };
  }

  wireStage(stage, stateName, idx); // 베이스도 변형 동일 — 저장 시 파일에 굽는다 (수홍 지시)
  applyCardTransform(stage, stateName, idx);
  syncPpControls();
  syncGridControls();
  sizePxGrids();
  document.addEventListener("keydown", onZoomKey);
}
