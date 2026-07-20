// SPDX-License-Identifier: Apache-2.0
// curator/zoom-editor.js — 확대 편집 모달 — 픽셀 편집·팔레트·마키·팬·실행취소 + 호흡 포커스 모드
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

// 같은 entries/transform truth 를 쓰므로 모달 편집이 그리드 카드에 실시간 반영된다.
// 휠/핀치 = 화면 배율(뷰 확대), 드래그/핸들/Shift+휠 = 기존 스프라이트 편집 그대로.
let zoomView = null; // { stateName, idx, width }

// 마지막 사용 펜 색 — 프레임/모달을 넘어가도 유지 (수홍 2026-07-18 "스포이드로
// 찍은 색으로 다음 프레임에 찍고 싶다"). 리로드에도 남게 localStorage 백업.
let lastPenColor = null;
try { lastPenColor = localStorage.getItem("sg-pen-color"); } catch { /* 무시 */ }
function rememberPenColor(hex) {
  if (!hex) return;
  lastPenColor = hex;
  try { localStorage.setItem("sg-pen-color", hex); } catch { /* 무시 */ }
}

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
  hand: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M5.3 8V3.8a.95.95 0 0 1 1.9 0V7m0-3.9v-.6a.95.95 0 0 1 1.9 0V7m0-3.3a.95.95 0 0 1 1.9 0V7.8m0-2.3a.95.95 0 0 1 1.9 0v3.7c0 2.9-1.8 4.7-4.5 4.7-2.1 0-3.1-.9-4.2-2.5L3 9.9c-.5-.8-.3-1.6.4-2 .6-.4 1.3-.2 1.7.4z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round"/></svg>',
};

// 뷰 패닝/커서 앵커 줌/돋보기 위젯 = view-nav.js 공용 (wirePan, keepViewAnchor,
// centerView, makeViewNavWidget — 확대편집·호흡 포커스·비교뷰 세 디테일뷰 공유).

function clearMarquee() {
  if (pixelEdit) pixelEdit.sel = null;
  document.querySelectorAll(".marquee").forEach((m) => { m.hidden = true; });
}

// 툴 단축키 (포토샵 표준, 수홍 지시 2026-07-18): B/P=펜, E=지우개, I=스포이드, M=선택(마키).
// 열려 있는 편집 툴바(줌/베이스 모달)에만 적용 — 입력 필드 포커스 중엔 무시.
// 같은 툴 키 재입력 = 툴 끔 (버튼 재클릭과 동일 거동).
// ev.code (물리 키) 기준 — 한글 IME/타 레이아웃에서도 동작 (ev.key 는 한글 모드에서
// "ㅠ" 같은 조합 문자가 와 매칭이 죽는다 — 실사고 2026-07-19 수홍).
const TOOL_SHORTCUTS = { KeyB: ".et-pen", KeyP: ".et-pen", KeyE: ".et-eraser", KeyI: ".et-pick", KeyM: ".et-select", KeyH: ".et-hand" };
document.addEventListener("keydown", (ev) => {
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const sel = TOOL_SHORTCUTS[ev.code];
  if (!sel) return;
  const a = document.activeElement;
  if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable)) return;
  const bars = [...document.querySelectorAll(".edit-toolbar")].filter((el) => el.offsetParent !== null);
  const btn = bars.length ? bars[bars.length - 1].querySelector(sel) : null;
  if (btn) { ev.preventDefault(); btn.click(); }
});

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
  // ev.code 기준 — 한글 IME 에서도 Cmd/Ctrl+Z 동작 (key 는 "ㅋ" 이 온다)
  if (ev.code !== "KeyZ" || !(ev.metaKey || ev.ctrlKey)) return;
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
  // 오프너(줄 라벨/버튼)에 남은 포커스 해제 — Space 홀드 팬이 버튼 가드에 걸리지 않게
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur();

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
  // 좌우반전 = 도구 조작 (연필/지우개/마키 이동과 같은 취급, 수홍 2026-07-19):
  // 활성/비활성 상태가 아니라 누를 때마다 반전. 마키 선택이 있으면 그 영역 픽셀만
  // 반전(픽셀 저널 스트로크 — Cmd+Z 로 되돌림), 없으면 프레임 전체(변형 flipX).
  card.querySelector(".flip-btn").addEventListener("click", () => {
    if (pixelEdit && pixelEdit.sel && pixelEdit.state === stateName && pixelEdit.idx === idx) {
      flipSelectionX();
      return;
    }
    toggleFlipX(stateName, idx);
  });
  stage.appendChild(makeScaleScrub(stateName, idx));
  card.querySelector(".zoom-close").addEventListener("click", closeZoom);
  modal.querySelector(".zoom-backdrop").addEventListener("click", closeZoom);
  // Space 홀드/휠버튼 드래그 + 손 툴(토글) = 뷰 패닝 (프레임·베이스 공통) —
  // 팬/줌 배선은 뷰포트 구성 뒤에 (아래 stage-viewport 블록).
  let panTool = false;

  // ── 픽셀 편집 툴바: 연필/지우개 + 프레임 팔레트 + 컬러피커 + 되돌리기/비우기 ──
  const toolbar = document.createElement("div");
  toolbar.className = "edit-toolbar";
  toolbar.innerHTML =
    `<button type="button" class="ghost et-pen" data-tip="${t("penTool")} (B)">` +
    `${TOOL_ICONS.pen}</button>` +
    `<button type="button" class="ghost et-eraser" data-tip="${t("eraserTool")} (E)">${TOOL_ICONS.eraser}</button>` +
    `<button type="button" class="ghost et-pick" data-tip="${t("tPick")} (I)">${TOOL_ICONS.pick}</button>` +
    `<button type="button" class="ghost et-select" data-tip="${t("tSelectTool")} (M)">${TOOL_ICONS.select}</button>` +
    `<button type="button" class="ghost et-hand" data-tip="${t("tHandTool")} (H)">${TOOL_ICONS.hand}</button>` +
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
  colorInput.value = lastPenColor || "#1f2430"; // 마지막 사용 색 승계
  colorInput.title = "color";
  colorInput.className = "et-color";
  dock.appendChild(swatchBox);
  dock.appendChild(colorInput);
  stageRow.appendChild(dock);

  // ── 3계층 뷰 (수홍 지시 2026-07-20): 모달(고정 최대) ⊃ 뷰포트(화면 배율·팬) ⊃
  // 스테이지(캐릭터). 스테이지 사방에 큰 패딩을 둬 어느 구석이든 화면 중앙으로
  // 끌어올 수 있다 (자유 팬). 스크롤 좌표계라 기존 %-오버레이/포인터 수학 무변.
  const viewwrap = document.createElement("div");
  viewwrap.className = "stage-viewwrap";
  const viewport = document.createElement("div");
  viewport.className = "stage-viewport";
  const pad = document.createElement("div");
  pad.className = "stage-pad";
  pad.appendChild(stage);
  viewport.appendChild(pad);
  viewwrap.appendChild(viewport);
  stageRow.appendChild(viewwrap);

  // 화면 배율: 최소 120px, 최대 셀폭×64 (기존 0.9×창폭 캡 폐지 — 수홍 "확대 더 되게")
  const VIEW_MIN_W = 120;
  const viewMaxW = Math.max(8000, cellW * 64);
  const syncViewLabels = () => {
    const pct = `${Math.round((zoomView.width / cellW) * 100)}%`;
    modal.querySelectorAll(".view-nav .vn-label").forEach((el) => { el.textContent = pct; });
  };
  const setViewWidth = (w, anchorX, anchorY) => {
    w = Math.round(Math.min(viewMaxW, Math.max(VIEW_MIN_W, w)));
    if (!zoomView || w === zoomView.width) return;
    keepViewAnchor(viewport, stage, anchorX, anchorY, () => {
      zoomView.width = w;
      stage.style.width = `${w}px`;
      applyCardTransform(stage, stateName, idx);
      sizePxGrids();
    });
    syncViewLabels();
  };
  const fitViewWidth = () => {
    const vr = viewport.getBoundingClientRect();
    return Math.max(VIEW_MIN_W, Math.min(Math.floor(vr.width - 48), Math.floor((vr.height - 48) / aspect)));
  };
  const viewHandlers = {
    zoomOut: () => setViewWidth(zoomView.width / 1.25),
    zoomIn: () => setViewWidth(zoomView.width * 1.25),
    fit: () => { setViewWidth(fitViewWidth()); centerView(viewport, stage); },
  };
  // 돋보기 위젯 ×2 (수홍 지시 2026-07-20): 툴바 + 뷰포트 우하단 코너
  toolbar.appendChild(makeViewNavWidget(viewHandlers));
  viewwrap.appendChild(makeViewNavWidget(viewHandlers, { corner: true }));
  // 뷰 확대: 휠/핀치 — 커서 아래 지점이 고정되는 앵커 줌. 뷰포트 레벨이라
  // 스테이지 밖 여백 위에서도 동작한다.
  viewport.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
    setViewWidth(zoomView.width * factor, ev.clientX, ev.clientY);
  }, { passive: false });
  wirePan(viewport, viewport, () => panTool);
  // 오픈 = 뷰포트에 맞춤 + 중앙 정렬 (프레임 넘김은 배율 유지 — keepWidth)
  if (!keepWidth) {
    zoomView.width = fitViewWidth();
    stage.style.width = `${zoomView.width}px`;
  }
  centerView(viewport, stage);
  syncViewLabels();

  const handBtn = toolbar.querySelector(".et-hand");
  handBtn.addEventListener("click", () => {
    panTool = !panTool;
    handBtn.classList.toggle("active", panTool);
    viewport.classList.toggle("pan-tool", panTool);
  });
  const selBox = document.createElement("div");
  selBox.className = "marquee";
  selBox.hidden = true;
  stage.appendChild(selBox);
  const syncMarqueeBox = () => {
    const sel = pixelEdit && pixelEdit.sel;
    selBox.hidden = !sel;
    if (!sel) return;
    // sel 은 소스 공간 — 표시가 변형(WYSIWYG)이라 점선도 순변환해서 그린다
    // (회전/기울임은 축정렬 bbox 근사; 공간 수학 SSoT = display.js frameFwdXY).
    const fwd = (x, y) => frameFwdXY(stateName, idx, x, y);
    const cs = [fwd(sel.x0, sel.y0), fwd(sel.x1, sel.y0), fwd(sel.x0, sel.y1), fwd(sel.x1, sel.y1)];
    const x0 = Math.min(...cs.map((p) => p[0]));
    const x1 = Math.max(...cs.map((p) => p[0]));
    const y0 = Math.min(...cs.map((p) => p[1]));
    const y1 = Math.max(...cs.map((p) => p[1]));
    selBox.style.left = `${(x0 / cellW) * 100}%`;
    selBox.style.top = `${(y0 / cellH) * 100}%`;
    selBox.style.width = `${((x1 - x0) / cellW) * 100}%`;
    selBox.style.height = `${((y1 - y0) / cellH) * 100}%`;
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
    applyFrameTransformAll(stateName, idx); // 편집 모드도 변형 유지(WYSIWYG) — 재표시만
  };
  penBtn.addEventListener("click", () => setTool("pen"));
  eraserBtn.addEventListener("click", () => setTool("eraser"));
  pickBtn.addEventListener("click", () => setTool("pick"));
  selectBtn.addEventListener("click", () => setTool("select"));
  colorInput.addEventListener("input", () => {
    rememberPenColor(colorInput.value);
    if (pixelEdit) pixelEdit.color = colorInput.value;
  });

  // 스포이드 = display.js sampleDisplayedColor ("화면에 보이는 그 색" SSoT).
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
        rememberPenColor(hex);
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

  // 마키 영역 좌우반전 — 현재 보이는 픽셀(합성)을 영역 안에서 거울로 뒤집어 픽셀
  // 저널 스트로크로 기록한다 (수홍 2026-07-19 "영역선택하고 좌우반전하면 거기만 툭툭").
  // 달라지는 픽셀만 ops 로 남기므로 다시 누르면 정확히 원복되고 Cmd+Z 도 스트로크
  // 단위로 먹는다. 투명은 null(지움) — 베이스의 크로마 배경은 균일색이라 자연 무변.
  function flipSelectionX() {
    const sel = pixelEdit && pixelEdit.sel;
    if (!sel || sel.x1 <= sel.x0 || sel.y1 <= sel.y0) return;
    const cx = compositeCell();
    const w = sel.x1 - sel.x0;
    const h = sel.y1 - sel.y0;
    const data = cx.getImageData(sel.x0, sel.y0, w, h).data;
    const hexAt = (i) => "#" + [data[i], data[i + 1], data[i + 2]]
      .map((v) => v.toString(16).padStart(2, "0")).join("");
    const e = entries[stateName];
    const ops = e.pixels[idx] || (e.pixels[idx] = {});
    const sets = [];
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const cur = (y * w + x) * 4;
        const mir = (y * w + (w - 1 - x)) * 4;
        const curVal = data[cur + 3] >= 128 ? hexAt(cur) : null;
        const mirVal = data[mir + 3] >= 128 ? hexAt(mir) : null;
        if (curVal === mirVal) continue; // 화면 무변 — op 잡음 남기지 않는다
        const key = `${sel.x0 + x},${sel.y0 + y}`;
        sets.push({ key, had: key in ops, prev: ops[key], value: mirVal });
        ops[key] = mirVal;
      }
    }
    if (!sets.length) return;
    pixelEdit.journal.push({ sets });
    pixelEdit.redo.length = 0;
    applyFrameTransformAll(stateName, idx);
    scheduleSave();
    buildPalette();
  }

  buildPalette();

  // 페인트: 편집 툴 활성 시 스테이지 드래그는 그리기 (다른 핸들러보다 먼저 등록해 가로챔)
  stage.addEventListener("pointerdown", (ev) => {
    if (!pixelEdit || pixelEdit.state !== stateName || pixelEdit.idx !== idx) return;
    if (ev.button || !ev.isPrimary) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    // 표시는 변형(WYSIWYG) 그대로, 저장은 소스 공간 — 공간 수학 SSoT 는 display.js.
    const cellX = (e2) => Math.floor(pointerSrcXY(stage, stateName, idx, e2)[0]);
    const cellY = (e2) => Math.floor(pointerSrcXY(stage, stateName, idx, e2)[1]);
    // 스포이드: 색만 집고 바로 연필로 전환 (드래그 페인트 아님). 투명 픽셀은 무시.
    // 집는 색 = 화면에 보이는 그 색 (표시 비트맵 샘플, display.js SSoT).
    if (pixelEdit.tool === "pick") {
      const hex = sampleDisplayedColor(stage, stateName, idx, ev);
      if (hex) {
        colorInput.value = hex;
        rememberPenColor(hex);
        pixelEdit.color = hex;
        pixelEdit.tool = "pen"; // 집은 색으로 즉시 그리게
        syncToolbar();
        setStatus(`${t("pickTool")}: ${hex}`, "ok");
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
        // 라이브 프리뷰: 드래그 중에도 픽셀이 점선과 함께 실제로 따라온다.
        // 밑층(img/snap-canvas)을 잠깐 숨기고 전체 합성본을 프리뷰 캔버스에 직접 그린다 —
        // 원본 자리는 비우고(이동) 목적지에 그대로 칠해 드롭 결과와 동일하게 보인다.
        const fullC = compositeCell().canvas;
        let chromaFill = null;
        if (isBase) {
          const d0 = fullC.getContext("2d").getImageData(0, 0, 1, 1).data;
          chromaFill = `rgb(${d0[0]},${d0[1]},${d0[2]})`;
        }
        const prev = document.createElement("canvas");
        prev.width = cellW; prev.height = cellH;
        Object.assign(prev.style, { position: "absolute", inset: "0", width: "100%",
                                    height: "100%", imageRendering: "pixelated", pointerEvents: "none" });
        const hidden = [stage.querySelector("img"), stage.querySelector(".snap-canvas")].filter(Boolean);
        const work = document.createElement("canvas");
        work.width = cellW; work.height = cellH;
        const drawPreview = (ddx, ddy, dup) => {
          // 소스 공간에서 이동을 합성한 뒤, 표시 변형을 통과시켜 그린다 (WYSIWYG 일치)
          const wctx = work.getContext("2d");
          wctx.imageSmoothingEnabled = false;
          wctx.clearRect(0, 0, cellW, cellH);
          wctx.drawImage(fullC, 0, 0);
          if (!dup) for (const p of grab) {
            const x = from.x0 + p.dx, y = from.y0 + p.dy;
            if (chromaFill) { wctx.fillStyle = chromaFill; wctx.fillRect(x, y, 1, 1); }
            else wctx.clearRect(x, y, 1, 1);
          }
          for (const p of grab) {
            const x = from.x0 + p.dx + ddx, y = from.y0 + p.dy + ddy;
            if (x >= 0 && x < cellW && y >= 0 && y < cellH) {
              wctx.fillStyle = p.hex;
              wctx.fillRect(x, y, 1, 1);
            }
          }
          const pcx = prev.getContext("2d");
          pcx.clearRect(0, 0, cellW, cellH);
          drawFrameInto(pcx, work, getTransform(stateName, idx), cellW, cellH, snapScaleFor(stateName));
        };
        hidden.forEach((el2) => { el2.style.visibility = "hidden"; });
        stage.insertBefore(prev, selBox);
        drawPreview(0, 0, ev.altKey);
        const onMove = (e2) => {
          const step = s;
          delta = [
            Math.round((cellX(e2) - sx) / step) * step,
            Math.round((cellY(e2) - sy) / step) * step,
          ];
          pixelEdit.sel = { x0: from.x0 + delta[0], y0: from.y0 + delta[1],
                            x1: from.x1 + delta[0], y1: from.y1 + delta[1] };
          syncMarqueeBox();
          drawPreview(delta[0], delta[1], e2.altKey);
        };
        const onUp = (e2) => {
          finish(onMove, onUp);
          prev.remove();
          hidden.forEach((el2) => { el2.style.visibility = ""; });
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
        // Space(또는 Cmd/Ctrl) 홀드 중엔 앵커가 커서와 함께 움직여 영역 전체가 이동한다
        // (포토샵 마키 표준). 떼면 그 자리에서 다시 크기 조절로 복귀.
        let ax = sx, ay = sy;
        let lx = sx, ly = sy;
        let spaceHeld = false;
        const onKey = (ke) => {
          if (ke.code === "Space") { spaceHeld = ke.type === "keydown"; ke.preventDefault(); }
        };
        window.addEventListener("keydown", onKey, true);
        window.addEventListener("keyup", onKey, true);
        pixelEdit.sel = norm(ax, ay, sx, sy);
        syncMarqueeBox();
        const onMove = (e2) => {
          const cx2 = cellX(e2), cy2 = cellY(e2);
          if (spaceHeld || e2.metaKey || e2.ctrlKey) { ax += cx2 - lx; ay += cy2 - ly; }
          lx = cx2; ly = cy2;
          pixelEdit.sel = norm(ax, ay, cx2, cy2);
          syncMarqueeBox();
        };
        const onUp = () => {
          window.removeEventListener("keydown", onKey, true);
          window.removeEventListener("keyup", onKey, true);
          finish(onMove, onUp);
        };
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
      const x = cellX(e2);
      const y = cellY(e2);
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

  // 뷰 확대 휠 = 뷰포트 레벨 앵커 줌 (위 stage-viewport 블록이 소유)

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
    const e0 = entries[stateName];
    const beforeCfg = e0.breathe ? JSON.parse(JSON.stringify(e0.breathe)) : null; // Esc 복원
    // enabled = 호흡 레이어 on/off (수홍 2026-07-19: 이 뷰가 유일한 확대 재생 뷰라
    // 꺼진 상태로 열거나 여기서 꺼도 무호흡 애니메이션이 그대로 재생돼야 한다).
    // 열 때 줄 체크박스 truth 를 승계 — 꺼진 행을 열어도 강제로 켜지 않는다.
    const bm = { cfg: null, enabled: !!e0.breathe, geomReady: false, hist: [], histPos: -1, cancelled: false, tick: 0 };
    const clone = (o) => JSON.parse(JSON.stringify(o));
    const commit = () => { // 조정 = 즉시 truth 반영 + 디바운스 저장 (수홍: 적용 대기 금지)
      if (bm.cancelled) return;
      e0.breathe = bm.enabled ? clone(bm.cfg) : null;
      scheduleSave();
      if (typeof scheduleStrip === "function") scheduleStrip();
    };
    const pushHist = () => {
      bm.hist = bm.hist.slice(0, bm.histPos + 1);
      bm.hist.push({ enabled: bm.enabled, cfg: clone(bm.cfg) });
      bm.histPos = bm.hist.length - 1;
    };
    const restoreHist = (pos) => {
      if (pos < 0 || pos >= bm.hist.length || pos === bm.histPos) return;
      bm.histPos = pos;
      const h = bm.hist[pos];
      bm.cfg = clone(h.cfg);
      bm.enabled = h.enabled !== false;
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
        // 지오메트리 확정 전(임시 위치 점프 방지)과 호흡 꺼짐 상태에선 숨김
        lineEls[i].style.visibility = bm.geomReady && bm.enabled ? "" : "hidden";
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
      const phase = bm.enabled ? (pattern[cursor] || 0) : 0; // off = 무호흡 재생
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
      // 지오메트리 truth = 굽기와 같은 합성 결과의 bbox — 캐노니컬 픽셀 + 변형 +
      // 픽셀 편집을 적용한 뒤 잰다 (실사고 2026-07-18 수홍: 캐릭터를 축소하니
      // 선이 축소 전 위치를 기준으로 잡힘 — 굽기는 변형 후 bbox 를 쓴다).
      const fr = frameOf(stateName, idx);
      const canonImg = fr ? img(fr.url) : null;
      if (canonImg && canonImg.complete && canonImg.naturalWidth) {
        const cvs = document.createElement("canvas");
        cvs.width = cellW;
        cvs.height = cellH;
        const cctx = cvs.getContext("2d");
        cctx.imageSmoothingEnabled = false;
        drawFrameInto(cctx, canonImg, getTransform(stateName, idx), cellW, cellH,
          snapScaleFor(stateName), getPixelOps(stateName, idx));
        const dd = cctx.getImageData(0, 0, cellW, cellH).data;
        let ttop = cellH, tbot = 0;
        for (let y = 0; y < cellH; y++) {
          for (let x = 0; x < cellW; x++) {
            if (dd[(y * cellW + x) * 4 + 3] >= 40) {
              if (y < ttop) ttop = y;
              if (y + 1 > tbot) tbot = y + 1;
              break;
            }
          }
        }
        if (tbot <= ttop) return false; // 아직 빈 합성 — 재시도 경로가 처리
        btop = ttop;
        bh = Math.max(1, tbot - ttop);
        if (!e0.breathe && !beforeCfg && bm.histPos <= 0) {
          // 기본 split 사슬 = defaultBreatheConfig 와 동일: 형제 행 상속 → 허리 → 가슴
          const sib = run.states
            .map((s) => s.name !== stateName && entries[s.name] && entries[s.name].breathe)
            .find((b) => b && Array.isArray(b.splits) && b.splits.length);
          const cxh = compositeCell();
          const dd2 = cxh.getImageData(0, 0, cellW, cellH).data;
          const silh = silhouetteStats(dd2, cellW, cellH);
          if (sib) bm.cfg.splits = [sib.splits[0]];
          else if (silh.top < cellH) {
            const waist = waistSplitFrom(dd2, cellW, cellH);
            bm.cfg.splits = [Math.round((waist !== null ? waist : silh.split) * 100) / 100];
          }
          if (bm.histPos === 0) bm.hist[0] = { enabled: bm.enabled, cfg: clone(bm.cfg) };
        }
        bm.geomReady = true;
        syncLines();
        renderTick();
        commit(); // 오픈 = truth 동기화 — enabled 승계, 꺼진 행을 강제로 켜지 않는다 (Esc 복원 가능)
        rebuildStrip();
        return true;
      }
      // 캐노니컬 이미지 미로드 = 아직 못 잰다 — 재시도 경로가 처리한다.
      // (폴백 금지: 표시용 쌍둥이(compositeCell)는 변형 미적용 + 풋프린트가 달라
      //  선이 엉뚱한 위치에 놓였다 — 실사고 2026-07-19 수홍 "어떨 때는 이상한 위치".)
      return false;
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
      const pattern = bm.enabled ? breathePattern(bm.cfg, play.length) : new Array(play.length).fill(0);
      strip.innerHTML = "";
      if (!play.length) return;
      const total = play.length; // 루프 불변 — 시퀀스 그대로 (수홍 확정)
      const actualBreaths = breatheFitCount(bm.cfg, play.length);
      const cap = document.createElement("div");
      cap.className = "bs-caption";
      cap.textContent = bm.enabled
        ? STR[lang].breatheStrip(play.length, actualBreaths, bm.cfg.breaths || 1)
        : STR[lang].breatheStripOff(play.length);
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
    // 호흡 on/off 토글 — 줄 헤더 체크박스와 같은 truth (수홍 2026-07-19: 확대 재생
    // 뷰에서도 끄고 켤 수 있게 — 끄면 선/조정 컨트롤이 쉬고 무호흡 재생만 남는다)
    const onWrap = document.createElement("label");
    onWrap.className = "pp-apply breathe-enable";
    onWrap.title = t("tRowBreathe");
    const onCheck = document.createElement("input");
    onCheck.type = "checkbox";
    onCheck.checked = bm.enabled;
    onCheck.addEventListener("change", () => {
      bm.enabled = onCheck.checked;
      syncBreatheControls();
      syncLines();
      commit();
      pushHist();
      setStatus(bm.enabled ? STR[lang].breatheOn(stateName) : STR[lang].breatheOff(stateName));
    });
    onWrap.appendChild(onCheck);
    onWrap.appendChild(Object.assign(document.createElement("span"), { textContent: t("rowBreathe") }));
    bar.appendChild(onWrap);
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
    function syncBreatheControls() {
      onCheck.checked = bm.enabled;
      const off = !bm.enabled;
      for (const el of [addBtn, delBtn, ampSel, minusBtn, breathInput, plusBtn, subCheck]) el.disabled = off;
      ampSel.value = String(bm.cfg.amplitude);
      breathInput.value = String(bm.cfg.breaths || 1);
      subCheck.checked = !!bm.cfg.subpixel;
      syncFitBadge();
    }
    syncBreatheControls();

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
      const fr0 = frameOf(stateName, idx);
      const canon0 = fr0 ? img(fr0.url) : null; // 지오메트리 truth 는 캐노니컬 — 그 로드가 진짜 신호
      if (canon0 && !canon0.complete) canon0.addEventListener("load", () => initSilhouette(), { once: true });
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
