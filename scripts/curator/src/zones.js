// SPDX-License-Identifier: Apache-2.0
// curator/zones.js — 2존 큐레이션 (시퀀스/후보 풀) — 순서·이동·복제·보관함
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

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
  const archivedSet = new Set(entries[stateName].archived || []);
  const missingIdx = state ? state.frames.filter((f) => !f.present && !archivedSet.has(f.index)).map((f) => f.index) : [];
  // 카드 없이 존재하는 프레임(예: 토글 OFF 로 풀에서 숨긴 호흡 위상)도 order 에
  // 보존한다 — DOM 은 표시의 부분집합이지 인스턴스 진실이 아니다.
  const seen = new Set([...seqIdx, ...poolIdx]);
  const hiddenIdx = state
    ? state.frames.filter((f) => f.present && !seen.has(f.index) && !archivedSet.has(f.index)).map((f) => f.index)
    : [];
  entries[stateName].sel = new Set(seqIdx);
  entries[stateName].order = [...seqIdx, ...poolIdx, ...hiddenIdx, ...missingIdx];
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

// The card header (`.card-top` = the title strip) is the drag handle: grab the
// title anywhere and move past DRAG_THRESHOLD to reorder within a row or move it
// between the sequence/pool rows. A press that never moves is a plain click and
// does NOTHING (수홍 2026-07-15): toggling sequence ⇄ pool is only the 넣기/빼기
// button or a drop, so a stray click can't silently add/remove a frame.
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
    const end = (evUp) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      if (!lifted) {
        // 임계값을 넘지 않은 순수 클릭 — 아무 것도 하지 않는다. 시퀀스⇄풀 이동은
        // 넣기/빼기 버튼(sel-btn)이나 드롭으로만 (수홍 2026-07-15).
        return;
      }
      // 보관함 칩 위에서 놓으면 보관 (풀에서도 제외)
      const chip = wrap.querySelector(".archive-chip");
      if (chip && evUp && typeof evUp.clientX === "number") {
        const r = chip.getBoundingClientRect();
        if (evUp.clientX >= r.left && evUp.clientX <= r.right && evUp.clientY >= r.top && evUp.clientY <= r.bottom) {
          ph.remove();
          card.remove();
          archiveFrame(stateName, Number(card.dataset.idx));
          return;
        }
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

// 상태 섹션 in-place 재구성 (보관/복구 후) — 위치 보존
function rebuildState(stateName) {
  const st = run.states.find((s) => s.name === stateName);
  const old = document.querySelector(`.state[data-state="${cssEscape(stateName)}"]`);
  if (!st || !old) return;
  renderState(st, old); // old 자리에 교체 렌더
}

function archiveFrame(stateName, idx) {
  const e = entries[stateName];
  e.sel.delete(idx);
  e.order = e.order.filter((i) => i !== idx);
  if (cloneSrc(stateName, idx) !== null) {
    // 복제 인스턴스는 보관함에 넣지 않고 완전히 제거한다 — 원본이 살아 있으니
    // 언제든 다시 복제하면 되고, 보관함에 사본이 쌓이는 건 혼란만 준다.
    delete e.clones[idx];
    delete e.transforms[idx];
    delete e.pixels[idx];
    if (e.unlinked) e.unlinked.delete(idx);
  } else if (!e.archived.includes(idx)) {
    e.archived.push(idx);
  }
  scheduleSave();
  rebuildState(stateName);
}

// 프레임 복제: 원본 카드 바로 뒤에, 같은 존(시퀀스/풀)으로, 현재 변형·픽셀편집을
// 복사한 새 인스턴스를 만든다. 복제의 복제도 원본 프레임으로 평탄화해 기록한다.
function duplicateFrame(stateName, idx) {
  const e = entries[stateName];
  const st = run.states.find((s) => s.name === stateName);
  if (!st) return;
  e.clones = e.clones || {};
  const used = [
    ...st.frames.map((f) => f.index),
    ...Object.keys(e.clones).map(Number),
    ...e.order,
    ...e.archived,
  ];
  const newIdx = Math.max(-1, ...used) + 1;
  const src = cloneSrc(stateName, idx) ?? idx;
  e.clones[newIdx] = src;
  // 링크 기본 (수홍 확정 2026-07-18): 복제는 원본 편집 truth 를 공유한다 —
  // 변형/픽셀을 복사하지 않는다. 독립은 '링크 끊기' 로만.
  const pos = e.order.indexOf(idx);
  e.order.splice(pos < 0 ? e.order.length : pos + 1, 0, newIdx);
  if (e.sel.has(idx)) e.sel.add(newIdx);
  scheduleSave();
  rebuildState(stateName);
}

function restoreFrame(stateName, idx, toSequence) {
  const e = entries[stateName];
  e.archived = e.archived.filter((i) => i !== idx);
  if (!e.order.includes(idx)) e.order.push(idx);
  if (toSequence) e.sel.add(idx);
  else e.sel.delete(idx);
  scheduleSave();
  rebuildState(stateName);
}

function renderSelectionState(stateName) {
  document.querySelectorAll(`.card[data-state="${cssEscape(stateName)}"]`).forEach((card) => {
    if (card.classList.contains("missing")) return;
    const idx = Number(card.dataset.idx);
    const inSeq = isSelected(stateName, idx);
    card.classList.toggle("selected", inSeq);
    const btn = card.querySelector(".sel-btn");
    if (btn) {
      // 아이콘(방향) + 라벨. 색상 강조 없이 방향 화살표로 넣기/빼기를 구분.
      btn.innerHTML = (inSeq ? SEL_ICON.remove : SEL_ICON.add) +
        `<span>${inSeq ? t("removeFromSeq") : t("addToSeq")}</span>`;
      btn.setAttribute("data-tip", inSeq ? t("tSelRemove") : t("tSelAdd"));
    }
  });
  const state = run.states.find((s) => s.name === stateName);
  const countEl = document.querySelector(`.preview[data-state="${cssEscape(stateName)}"] .count`);
  if (countEl) countEl.textContent = STR[lang].seqCount(entries[stateName].sel.size, state.requestFrames);
}

function renderArchive(state) {
  const e = entries[state.name];
  const wrap = document.createElement("div");
  wrap.className = "archive-wrap";
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "ghost archive-chip";
  chip.setAttribute("data-tip", t("tArchiveChip"));
  chip.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M1.5 3h13v3h-13zM2.5 6v6.5A1 1 0 0 0 3.5 13.5h9a1 1 0 0 0 1-1V6M6 8.5h4" ' +
    'fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>' +
    `<span>${STR[lang].archiveChip(e.archived.length)}</span>`;
  chip.addEventListener("click", () => {
    if (e.archived.length) openArchiveModal(state.name);
  });
  wrap.appendChild(chip);
  if (e.archived.length === 0) wrap.classList.add("empty");
  return wrap;
}

// 보관함 풀 모달 — 일반 카드 크기로 크게 보고 버튼으로 복구 (팝오버 대체, UX)
function openArchiveModal(stateName) {
  document.getElementById("archive-modal")?.remove();
  const state = run.states.find((s) => s.name === stateName);
  const e = entries[stateName];
  if (!state || !e.archived.length) return;
  const modal = document.createElement("div");
  modal.id = "archive-modal";
  modal.innerHTML =
    `<div class="zoom-backdrop"></div>` +
    `<div class="card arch-modal-card">` +
    `<div class="zoom-head"><span class="zoom-title">${STR[lang].archModalTitle(escapeHtml(stateName), e.archived.length)}</span>` +
    `<button type="button" class="ghost zoom-close">${t("zoomClose")}</button></div>` +
    `<div class="arch-grid"></div></div>`;
  const grid = modal.querySelector(".arch-grid");
  const frameByIdx = new Map(state.frames.map((f) => [f.index, f]));
  for (const idx of e.archived) {
    const f = frameByIdx.get(idx);
    if (!f) continue;
    const cardEl = document.createElement("div");
    cardEl.className = "card arch-card";
    cardEl.style.setProperty("--cell-aspect", run.cell.width / run.cell.height);
    cardEl.innerHTML =
      `<div class="card-top"><span class="idx">${f.label ? escapeHtml(f.label) : `#${idx}`}</span></div>` +
      `<div class="stage">` +
      (f.present ? `<img src="${escapeHtml(frameUrl(stateName, f))}" class="px-upscale" draggable="false" />` : `<div class="missing-label">${t("missingPending")}</div>`) +
      `</div>` +
      `<div class="card-controls">` +
      `<button type="button" class="ghost ar-seq">${t("restoreToSeq")}</button>` +
      `<button type="button" class="ghost ar-pool">${t("restoreToPool")}</button>` +
      `</div>`;
    const restore = (toSeq) => {
      restoreFrame(stateName, idx, toSeq);
      if (entries[stateName].archived.length) openArchiveModal(stateName);
      else close();
    };
    cardEl.querySelector(".ar-seq").addEventListener("click", () => restore(true));
    cardEl.querySelector(".ar-pool").addEventListener("click", () => restore(false));
    grid.appendChild(cardEl);
  }
  const close = () => {
    modal.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (ev) => { if (ev.key === "Escape") close(); };
  modal.querySelector(".zoom-close").addEventListener("click", close);
  modal.querySelector(".zoom-backdrop").addEventListener("click", close);
  document.addEventListener("keydown", onKey);
  document.body.appendChild(modal);
}

// 팝오버 미니카드를 끌어내// 팝오버 미니카드를 끌어내 시퀀스/후보 존에 떨어뜨리면 복구
function wireArchiveRestoreDrag(mini, stateName, idx) {
  mini.addEventListener("pointerdown", (ev) => {
    if (ev.button || !ev.isPrimary) return;
    ev.preventDefault();
    const startX = ev.clientX;
    const startY = ev.clientY;
    let ghost = null;
    const onMove = (e2) => {
      if (!ghost) {
        if (Math.abs(e2.clientX - startX) <= DRAG_THRESHOLD && Math.abs(e2.clientY - startY) <= DRAG_THRESHOLD) return;
        ghost = mini.cloneNode(true);
        ghost.classList.add("ap-ghost");
        document.body.appendChild(ghost);
      }
      ghost.style.left = `${e2.clientX + 8}px`;
      ghost.style.top = `${e2.clientY + 8}px`;
    };
    const end = (e2) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      if (!ghost) return; // 클릭만 한 것 — 아무 일 없음
      ghost.remove();
      const sectionSel = `.state[data-state="${cssEscape(stateName)}"]`;
      const seq = document.querySelector(`${sectionSel} .seq-frames`);
      const pool = document.querySelector(`${sectionSel} .pool-frames`);
      const over = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return e2.clientX >= r.left && e2.clientX <= r.right && e2.clientY >= r.top && e2.clientY <= r.bottom;
      };
      if (over(seq)) restoreFrame(stateName, idx, true);
      else if (over(pool)) restoreFrame(stateName, idx, false);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  });
}
