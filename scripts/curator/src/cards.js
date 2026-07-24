// SPDX-License-Identifier: Apache-2.0
// curator/cards.js — 상태 줄/카드/미리보기 렌더링
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

// 넣기/빼기 SVG (이모지·유니코드 마크 금지 — 라인 아이콘). 시퀀스=위, 풀=아래라
// 넣기=위 화살표, 빼기=아래 화살표로 공간적으로 직관화.
const SEL_ICON = {
  add: '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M8 12.5V4.2M4.6 7.4 8 4l3.4 3.4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  remove: '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M8 3.5v8.3M4.6 8.6 8 12l3.4-3.4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

function renderState(state, replaceEl) {
  const wrap = document.createElement("section");
  wrap.className = "state";
  wrap.dataset.state = state.name;

  const head = document.createElement("div");
  head.className = "state-head";
  head.innerHTML =
    `<span class="name">${escapeHtml(state.name)}</span>` +
    `<span class="meta">${state.requestFrames} ${t("frames")} · ${state.fps}fps · ${state.loop ? t("loop") : t("nonLoop")} · ${t("cellPx")} ${run.cell.width}x${run.cell.height}px</span>` +
    (state.action ? `<span class="action">${escapeHtml(state.action)}</span>` : "") +
    (state.extractOk ? "" : `<span class="state-warn">${t("extractFail")}</span>`) +
    (anchorStates.has(state.name) ? `<span class="anchor-badge" data-tip="${t("tDirAnchorBadge")}">${t("dirAnchorBadge")}</span>` : "");
  wrap.appendChild(head);

  // 이 줄을 "무엇으로 생성했는가" — run dir 실재 파일 기준 ref 체인 (앵커/basis/가이드).
  // 같은 줄 우측 = 줄별 표시/굽기 컨트롤(픽셀 격자 · 픽셀퍼펙트 체크박스) — 이미지 바로 위.
  const hasRefs = state.refs && state.refs.length;
  const showGridToggle = gridCapableStates.has(state.name);
  const showPpToggle = ppTwinStates.has(state.name) || ppPreviewStates.has(state.name);
  const showGifBtn = state.frames && state.frames.some((f) => f.present);
  if (hasRefs || showGridToggle || showPpToggle || showGifBtn) {
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
    controls.appendChild(makeFpsStepper(state.name));
    if (showGridToggle) controls.appendChild(makeGridToggle(state.name));
    if (showPpToggle) controls.appendChild(makePpToggle(state.name));
    controls.appendChild(makeTweenButton(state.name));
    controls.appendChild(makeRerollButton(state.name));
    controls.appendChild(makeBreatheToggle(state.name));
    // 저장은 맨 우측 (수홍 2026-07-19)
    if (showGifBtn) controls.appendChild(makeGifButton(state.name));
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
  // frameOf 는 복제 인스턴스(order 의 물리 범위 밖 인덱스)도 원본 frame 객체를
  // 빌려 해석한다 — 카드 하나 = 인스턴스 하나.
  for (const idx of e.order) {
    if (!e.sel.has(idx)) continue;
    const frame = frameOf(state.name, idx);
    if (frame) seqFrames.appendChild(renderCard(state, frame));
  }
  // pool = everything not in the sequence. `order` already contains every
  // index (present + missing), so this single loop covers missing frames too
  // — do NOT also iterate state.frames here or missing cards render twice.
  for (const idx of e.order) {
    if (e.sel.has(idx)) continue;
    const frame = frameOf(state.name, idx);
    if (!frame) continue;
    // 호흡 위상 프레임은 큐레이션 후보가 아니라 파생 사이클 재료다 (수홍 지적
    // 2026-07-17 "토글할 때 후보 풀이 달라진다") — 토글 OFF 상태에선 풀에
    // 노출하지 않는다. 토글 체크박스가 유일한 표면이고, 풀 구성은 호흡
    // on/off 와 무관하게 불변이다.
    if ((frame.label || "").startsWith("breathe")) continue;
    poolFrames.appendChild(renderCard(state, frame));
  }

  // 보관함: 후보 풀에서도 완전히 뺀 프레임. 접힌 칩 → 클릭하면 팝오버(쇽),
  // 카드를 칩에 끌어다 놓으면 보관, 팝오버의 미니카드를 끌어내면 복구.
  zones.appendChild(renderArchive(state));

  body.appendChild(zones);
  body.appendChild(renderPreview(state));
  wrap.appendChild(body);

  if (replaceEl) replaceEl.replaceWith(wrap);
  else document.getElementById("states").appendChild(wrap);

  // wire stages + reorder grips after they are in the DOM (need clientWidth).
  // order 를 돌아야 복제 인스턴스 카드도 와이어링된다 (물리 프레임 목록엔 없다).
  for (const idx of e.order) {
    const frame = frameOf(state.name, idx);
    if (!frame || !frame.present) continue;
    const card = wrap.querySelector(`.card[data-idx="${idx}"]`);
    if (!card) continue; // 보관된 프레임은 행에 카드가 없다
    const stage = card.querySelector(".stage");
    wireStage(stage, state.name, idx);
    applyCardTransform(stage, state.name, idx);
    if (run.iso) drawGroundGrid(stage);
    // the whole header strip is the drag handle (grip + label + ✗/✓ button),
    // not just the ⠿ glyph — see wireReorder.
    const cardTop = card.querySelector(".card-top");
    if (cardTop) wireReorder(cardTop, card, wrap, state.name);
  }
  renderSelectionState(state.name);
  startPreview(state);
  // 새로 만든 표시면은 판정을 받아야 한다. load 위임 훅은 <img> 전용이라
  // 여기서 만든 캔버스(미리보기·스냅)를 못 덮고, rebuildState 경로 11곳
  // (보관·복구·복제·삭제·호흡 적용·줌 닫기…)이 전부 이 함수를 지난다.
  // 빠뜨리면 리사이즈가 올 때까지 뭉갠 채로 남는다 (콩콩이 R2, 2026-07-24).
  syncPixelScaling(wrap);
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
      `<div class="rotate-handle" data-tip="${t("tRotate")}"></div>` +
      `<div class="shear-handle" data-tip="${t("tShear")}"></div>`
    : `<div class="missing-label">${state.rawPresent ? t("missingRawWait") : t("missingPending")}</div>`;

  const isClone = frame.clone !== undefined;
  const srcName = isClone ? frameDisplayName(state.name, frame.clone) : null;
  const customName = (entries[state.name].names || {})[frame.index];
  const shortLabel = customName || (isClone ? STR[lang].cloneBadge(srcName) : (frame.label ? frame.label : `#${frame.index}`));
  // 풀네임(복사 대상) — 에이전트가 그대로 집어가도록 런-상대 파일 경로 + 라벨.
  const relPath = (frame.url || "").replace(/^\/run\//, "");
  const fullName = (customName ? `${customName} · ` : "") + (isClone ? `${srcName} 복제 · ${relPath}` : [frame.label, relPath].filter(Boolean).join(" · ") || shortLabel) + " · " + t("tRenameHint");
  const titleCls = isClone ? "idx clone-badge" : "idx";
  const linkBtn = !isClone ? "" : (isLinkedClone(state.name, frame.index)
    ? `<button type="button" class="ghost link-state-btn unlink-btn" data-tip="${t("tUnlink")}" aria-label="unlink">` +
      '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">' +
      '<path d="M6.5 9.5 3.8 12.2a2.2 2.2 0 0 0 3.1 3.1l2.7-2.7M9.5 6.5l2.7-2.7a2.2 2.2 0 0 0-3.1-3.1L6.4 3.4M2.5 2.5l2 2M13.5 13.5l-2-2" transform="translate(0,-1)" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>'
    : `<button type="button" class="ghost link-state-btn relink-btn" data-tip="${t("tRelink")}" aria-label="relink">` +
      '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">' +
      '<path d="M6.8 9.2 4 12a2.3 2.3 0 0 0 3.2 3.2l2.8-2.8M9.2 6.8 12 4a2.3 2.3 0 0 1 3.2 3.2l-2.8 2.8M6.2 9.8l3.6-3.6" transform="translate(-1.5,-1.5)" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>');
  const title = `<span class="${titleCls}" data-tip="${escapeHtml(fullName)}" data-tip-copy>${escapeHtml(shortLabel)}</span>${linkBtn}`;
  // 아이콘 SVG — 이모지 대신 라인 아이콘 (플랫폼별 렌더 편차·저품질 방지)
  const dupIcon =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<rect x="5.5" y="5.5" width="8.2" height="8.2" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.3"/>' +
    '<path d="M3.4 10.4H2.9A1 1 0 0 1 1.9 9.4V3A1.1 1.1 0 0 1 3 1.9h6.4a1 1 0 0 1 1 1v0.5" ' +
    'fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>';
  const zoomIcon =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<circle cx="6.8" cy="6.8" r="4.3" fill="none" stroke="currentColor" stroke-width="1.3"/>' +
    '<path d="M10 10 14 14M5 6.8h3.6M6.8 5v3.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>';
  const archIcon =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M1.8 3.2h12.4v3H1.8zM2.9 6.2v6.4A1 1 0 0 0 3.9 13.6h8.2a1 1 0 0 0 1-1V6.2M6.2 8.6h3.6" ' +
    'fill="none" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round"/></svg>';
  const psize = frame.present && frame.contentSize
    ? `<span class="psize" data-tip="${t("tContentPx")}">${frame.contentSize[0]}×${frame.contentSize[1]}</span>` : "";
  card.innerHTML =
    // 헤더: 복제 | 타이틀(드래그 핸들, 호버 시 풀네임 복사) | 확대 —
    // 복제를 좌측으로 분리 (수홍 지시 2026-07-17: 확대 옆에 붙어 있어 오클릭).
    `<div class="card-top"${frame.present ? ` data-tip="${t("tReorder")}"` : ""}>` +
    `<span class="ct-left">` +
    (frame.present
      ? `<button type="button" class="ghost dup-btn" data-tip="${t("tDupBtn")}" aria-label="duplicate">${dupIcon}</button>`
      : "") +
    `${title}</span>` +
    (frame.present
      ? `<span class="ct-right">` +
        `<button type="button" class="ghost zoom-btn" data-tip="${t("tZoomOpen")}" aria-label="zoom">${zoomIcon}</button>` +
        `</span>`
      : "") +
    `</div>` +
    `<div class="stage">${stageInner}</div>` +
    // 푸터 2층: (1) 정보 — 크기·변형값 / (2) 버튼 — 반전·초기화 · 넣기빼기·보관
    (frame.present
      ? `<div class="card-info">${psize}<span class="tvals"></span></div>` +
        `<div class="card-controls">` +
        `<button type="button" class="ghost flip-btn" data-tip="${t("tFlipX")}" aria-label="flip-x">↔</button>` +
        `<button type="button" class="ghost reset-btn" data-tip="${t("tReset")}" aria-label="reset">↺</button>` +
        `<span class="ctrl-group">` +
        `<button type="button" class="sel-btn"></button>` +
        `<button type="button" class="ghost arch-btn" data-tip="${t("tArchiveBtn")}" aria-label="archive">${archIcon}</button>` +
        `</span>` +
        `</div>`
      : "") +
    "";

  if (frame.present) {
    // 표시 샘플링 판정은 display.js 가 소유한다 — 로드는 위임 훅
    // (installPixelScalingLoadHook), 기하 변화는 sizePxGrids/resize 가 재평가한다.
    // 여기서 따로 부르지 않는다 (판정 재확산 금지).
    const idxEl = card.querySelector(".card-top .idx");
    if (idxEl) idxEl.addEventListener("dblclick", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const e2 = entries[state.name];
      const cur = (e2.names || {})[frame.index] || "";
      const next = window.prompt(t("renamePrompt"), cur);
      if (next === null) return;
      e2.names = e2.names || {};
      const trimmed = next.trim().slice(0, 24);
      if (trimmed) e2.names[frame.index] = trimmed;
      else delete e2.names[frame.index];
      scheduleSave();
      rebuildState(state.name);
    });
    const relinkBtn = card.querySelector(".relink-btn");
    if (relinkBtn) relinkBtn.addEventListener("click", () => {
      // 재링크: 자기 편집을 버리고 원본 truth 재채택 (명시적 사용자 액션)
      const e2 = entries[state.name];
      delete e2.transforms[frame.index];
      delete e2.pixels[frame.index];
      if (e2.unlinked) e2.unlinked.delete(frame.index);
      scheduleSave();
      rebuildState(state.name);
      setStatus(STR[lang].relinked(frameDisplayName(state.name, e2.clones[frame.index])));
    });
    const unlinkBtn = card.querySelector(".unlink-btn");
    if (unlinkBtn) unlinkBtn.addEventListener("click", () => {
      // 링크 끊기: 현재 원본 편집을 복사해 독립 프레임으로 (이후 편집은 각자)
      const e2 = entries[state.name];
      const srcIdx = e2.clones[frame.index];
      if (e2.transforms[srcIdx]) e2.transforms[frame.index] = { ...e2.transforms[srcIdx] };
      if (e2.pixels[srcIdx]) e2.pixels[frame.index] = JSON.parse(JSON.stringify(e2.pixels[srcIdx]));
      e2.unlinked = e2.unlinked || new Set();
      e2.unlinked.add(frame.index);
      scheduleSave();
      rebuildState(state.name);
      setStatus(STR[lang].unlinked(frameDisplayName(state.name, srcIdx)));
    });
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
    const dupBtn = card.querySelector(".dup-btn");
    if (dupBtn) {
      dupBtn.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      dupBtn.addEventListener("click", () => duplicateFrame(state.name, frame.index));
    }
    const cloneBadge = card.querySelector(".clone-badge");
    if (cloneBadge && frame.clone !== undefined) {
      // 복제 배지 클릭 = 원본 카드로 이동 + 반짝 — "어떤 프레임의 복제인지" 시각 연결
      cloneBadge.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      cloneBadge.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const srcCard = document.querySelector(
          `#states .card[data-state="${cssEscape(state.name)}"][data-idx="${frame.clone}"]`);
        if (!srcCard) return;
        srcCard.scrollIntoView({ behavior: "smooth", block: "center" });
        srcCard.classList.remove("flash-target");
        void srcCard.offsetWidth;
        srcCard.classList.add("flash-target");
        srcCard.addEventListener("animationend", () => srcCard.classList.remove("flash-target"), { once: true });
      });
    }
    const archBtn = card.querySelector(".arch-btn");
    archBtn.addEventListener("pointerdown", (ev) => ev.stopPropagation());
    archBtn.addEventListener("click", () => archiveFrame(state.name, frame.index));
    // 넣기/빼기 = 시퀀스⇄풀 토글의 유일한 버튼 (드래그 외). 푸터에 있어 헤더 드래그와
    // 무관하지만, 실수 드래그 시작을 막으려 pointerdown 전파를 끊는다.
    const selBtn = card.querySelector(".sel-btn");
    selBtn.addEventListener("pointerdown", (ev) => ev.stopPropagation());
    selBtn.addEventListener("click", () => moveCardToOtherZone(card, state.name));
    card.querySelector(".stage").appendChild(makeScaleScrub(state.name, frame.index));
  }
  return card;
}

/** Toggle horizontal flip for a single frame (Alex 2026-05-28). */

function renderPreview(state) {
  const box = document.createElement("div");
  box.className = "preview";
  box.dataset.state = state.name;
  const aspect = run.cell.height / run.cell.width;
  const speedOpts = [0.25, 0.5, 1, 2, 4]
    .map((v) => `<option value="${v}"${v === 1 ? " selected" : ""}>×${v}</option>`)
    .join("");
  // 위치 표시(pv-pos)를 캔버스·프레임수 바로 밑(컨트롤 위)으로 — 재생 컨트롤 아래
  // 뚝 떨어져 있어 캔버스와 멀고 헷갈렸다 (수홍 2026-07-15, 쿠마피커 pv-pos 지정).
  box.innerHTML =
    `<h4>${t("preview")}</h4>` +
    `<canvas width="${run.cell.width}" height="${run.cell.height}" style="height:${(160 * aspect).toFixed(0)}px"></canvas>` +
    `<div class="count"></div>` +
    `<div class="pv-pos"></div>` +
    `<div class="pv-controls">` +
    `<button type="button" class="ghost pv-prev" data-tip="${t("tPrev")}">⏮</button>` +
    `<button type="button" class="ghost pv-play" data-tip="${t("tPause")}">⏸</button>` +
    `<button type="button" class="ghost pv-next" data-tip="${t("tNext")}">⏭</button>` +
    `<select class="pv-speed" name="speed-${escapeHtml(state.name)}" aria-label="${t("tSpeed")}" data-tip="${t("tSpeed")}">${speedOpts}</select>` +
    `</div>`;
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
  const pv = (previews[state.name] = { playing: true, speed: 1, cursor: 0, shown: -1, tick: 0 });
  let last = 0;

  const syncPlayBtn = () => {
    playBtn.textContent = pv.playing ? "⏸" : "▶";
    playBtn.setAttribute("data-tip", pv.playing ? t("tPause") : t("tPlay"));
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
    const f = frameOf(state.name, idx); // 복제 인스턴스 → 원본 이미지
    const image = f ? img(frameUrl(state.name, f)) : null;
    if (image && image.complete && image.naturalWidth) {
      const tr = getTransform(state.name, idx);
      const bcfg = stateBreathe(state.name);
      if (bcfg) {
        // 호흡 합성은 캐노니컬 픽셀 기준 (표시 변형은 풋프린트 상이 — 이음새 흔들림)
        // 호흡 후처리 레이어 (수홍 2026-07-18): 프레임 선택과 직교하는 변조 —
        // 재생 틱 기준 위상을 프레임 위에 얹는다 (깜빡임 프레임도 그대로 숨쉰다).
        const base = document.createElement("canvas");
        base.width = cw;
        base.height = ch;
        const baseCtx = base.getContext("2d");
        baseCtx.imageSmoothingEnabled = false;
        const canonical = f && f.url ? img(f.url) : image;
        drawFrameInto(baseCtx, (canonical && canonical.complete && canonical.naturalWidth) ? canonical : image,
          tr, cw, ch, snapScaleFor(state.name), getPixelOps(state.name, idx));
        const pattern = breathePattern(bcfg, play.length);
        ctx.drawImage(breatheComposite(base, bcfg, pattern[pv.cursor] || 0), 0, 0);
      } else {
        // 픽셀퍼펙트 줄은 카드와 동일하게 격자 재양자화로 그린다 (프리뷰 = 굽기)
        drawFrameInto(ctx, image, tr, cw, ch, snapScaleFor(state.name), getPixelOps(state.name, idx));
      }
    }
    posEl.textContent = `${pv.cursor + 1}/${play.length} · #${idx}`;
  };

  const step = (delta) => {
    pv.playing = false;
    syncPlayBtn();
    const play = playList(state.name);
    if (play.length) pv.cursor = (pv.cursor + delta + play.length) % play.length;
    pv.tick += 1;
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
    if (!root.isConnected) return; // 섹션이 교체/제거되면 이 루프는 은퇴
    const play = playList(state.name);
    if (pv.playing && play.length) {
      const interval = 1000 / Math.max(0.1, state.fps * pv.speed);
      if (ts - last >= interval) {
        last = ts;
        pv.cursor = (pv.cursor + 1) % play.length;
        pv.tick += 1; // 호흡 위상 진행 (프레임 주기와 독립 — LCM 으로 재정합)
      }
    }
    draw();
    requestAnimationFrame(frame);
  }
  syncPlayBtn();
  requestAnimationFrame(frame);
}
