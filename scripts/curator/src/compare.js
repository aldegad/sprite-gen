// SPDX-License-Identifier: Apache-2.0
// curator/compare.js — 완성본 비교 캔버스 v2 (수홍 2026-07-18)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// - 정렬 모드(디폴트 배치): 수평(바닥 공유) / 수직(가로 중심 공유) / 겹침(오니언스킨)
// - 좌측 목록: 모든 상태의 완성본을 체크박스로 켜고 끔 (많아져도 스크롤)
// - 스프라이트를 직접 잡고 드래그해 자유 배치 (오프셋은 세션 로컬)
// - 가이드선: 캔버스 클릭 = 가로선, Shift+클릭 = 세로선. 클릭 = 포커스,
//   드래그 = 이동, Delete/Backspace = 포커스된 선 삭제.
// - 배율: 스테이지 위에서 휠.

function openCompare() {
  document.getElementById("compare-modal")?.remove();
  const all = [];
  for (const st of run.states) {
    const f = st.frames.find((fr) => fr.present && fr.contentBox);
    if (f) all.push({ state: st.name, frame: f });
  }
  if (!all.length) {
    setStatus(t("cmpNone"), "err");
    return;
  }
  const state = {
    mode: "h",                 // 'h' | 'v' | 'overlay'
    zoom: 8,
    guides: [],                // {axis: 'h'|'v', pos}  (논리 px, 캔버스 좌표)
    focus: -1,
    include: new Set(all.filter((tg) => /_idle$/.test(tg.state)).map((tg) => tg.state)),
    offsets: {},               // state -> {dx, dy} (자유 배치, 논리 px)
  };
  if (!state.include.size) state.include.add(all[0].state);
  // 조작 히스토리 (Cmd/Ctrl+Z ↔ Cmd/Ctrl+Shift+Z): 가이드 추가/이동/삭제, 스프라이트 이동, 정렬 리셋
  const hist = { list: [], pos: -1 };
  const snapshot = () => JSON.parse(JSON.stringify({ guides: state.guides, offsets: state.offsets, focus: state.focus }));
  const pushHist = () => {
    hist.list = hist.list.slice(0, hist.pos + 1);
    hist.list.push(snapshot());
    hist.pos = hist.list.length - 1;
  };
  const restoreHist = (pos) => {
    if (pos < 0 || pos >= hist.list.length || pos === hist.pos) return;
    hist.pos = pos;
    const s = JSON.parse(JSON.stringify(hist.list[pos]));
    state.guides = s.guides;
    state.offsets = s.offsets;
    state.focus = s.focus;
    render();
  };

  const anim = { playing: false, cursors: {}, last: {} };
  const modal = document.createElement("div");
  modal.id = "compare-modal";
  modal.innerHTML =
    `<div class="zoom-backdrop"></div>` +
    `<div class="card cmp-card">` +
    `<div class="zoom-head"><span class="zoom-title">${t("cmpTitle")}</span>` +
    `<span class="cmp-controls">` +
    `<label class="pp-apply"><input type="radio" name="cmp-mode" value="h" checked /><span>${t("cmpH")}</span></label>` +
    `<label class="pp-apply"><input type="radio" name="cmp-mode" value="v" /><span>${t("cmpV")}</span></label>` +
    `<label class="pp-apply"><input type="radio" name="cmp-mode" value="overlay" /><span>${t("cmpOverlay")}</span></label>` +
    `<button type="button" class="ghost cmp-play" data-tip="${t("tCmpPlay")}">▶</button>` +
    `<span class="cmp-zoom-label">×8</span>` +
    `</span>` +
    `<button type="button" class="ghost zoom-close">${t("zoomClose")}</button></div>` +
    `<div class="cmp-hint">${t("cmpHint")}</div>` +
    `<div class="cmp-body">` +
    `<div class="cmp-list"></div>` +
    `<div class="cmp-scroll"><div class="cmp-stage"><canvas class="cmp-canvas"></canvas><div class="cmp-guides"></div></div></div>` +
    `</div>` +
    `<div class="cmp-legend"></div>` +
    `</div>`;
  document.body.appendChild(modal);
  const canvas = modal.querySelector(".cmp-canvas");
  const guidesEl = modal.querySelector(".cmp-guides");
  const listEl = modal.querySelector(".cmp-list");
  const legend = modal.querySelector(".cmp-legend");
  const zoomLabel = modal.querySelector(".cmp-zoom-label");

  const imgs = new Map();
  for (const tg of all) {
    const im = new Image();
    im.src = tg.frame.url;
    im.onload = () => render();
    imgs.set(tg.state, im);
  }

  // 큐레이션 완성 상태 합성 (수홍 2026-07-18: 편집·조정된 모습이 비교본):
  // 시퀀스의 cursor 번째 인스턴스를 캐노니컬+변형+픽셀편집+호흡 위상으로 굽는다.
  const compositeFor = (name, cursor) => {
    const play = playList(name);
    const st = run.states.find((s) => s.name === name);
    let idx;
    let phase = 0;
    if (play.length) {
      const cur = ((cursor % play.length) + play.length) % play.length;
      idx = play[cur];
      const bcfg = stateBreathe(name);
      if (bcfg) phase = breathePattern(bcfg, play.length)[cur] || 0;
    } else {
      const f0 = st && st.frames.find((fr) => fr.present);
      if (!f0) return null;
      idx = f0.index;
    }
    const f = frameOf(name, idx);
    const image = f ? img(f.url) : null;
    if (!(image && image.complete && image.naturalWidth)) return null;
    const base = document.createElement("canvas");
    base.width = run.cell.width;
    base.height = run.cell.height;
    const bx = base.getContext("2d");
    bx.imageSmoothingEnabled = false;
    drawFrameInto(bx, image, getTransform(name, idx), base.width, base.height,
      snapScaleFor(name), getPixelOps(name, idx));
    const bcfg = stateBreathe(name);
    const out = phase && bcfg ? breatheComposite(base, bcfg, phase) : base;
    // 콘텐츠 bbox (합성 결과 기준 — 변형으로 옮겨진 위치 반영)
    const d = out.getContext("2d").getImageData(0, 0, out.width, out.height).data;
    let x0 = out.width, y0 = out.height, x1 = 0, y1 = 0;
    for (let y = 0; y < out.height; y++) {
      for (let x = 0; x < out.width; x++) {
        if (d[(y * out.width + x) * 4 + 3] >= 40) {
          if (x < x0) x0 = x;
          if (y < y0) y0 = y;
          if (x + 1 > x1) x1 = x + 1;
          if (y + 1 > y1) y1 = y + 1;
        }
      }
    }
    if (x1 <= x0 || y1 <= y0) return null;
    return { canvas: out, box: [x0, y0, x1, y1] };
  };

  // 좌측 목록 (토글)
  const buildList = () => {
    listEl.innerHTML = "";
    for (const tg of all) {
      const label = document.createElement("label");
      label.className = "cmp-item";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = state.include.has(tg.state);
      cb.addEventListener("change", () => {
        if (cb.checked) state.include.add(tg.state);
        else state.include.delete(tg.state);
        render();
      });
      const thumb = document.createElement("img");
      thumb.src = tg.frame.url;
      label.appendChild(cb);
      label.appendChild(thumb);
      label.appendChild(Object.assign(document.createElement("span"), { textContent: tg.state }));
      listEl.appendChild(label);
    }
  };
  buildList();

  // 배치 계산: 디폴트 정렬 + 사용자 오프셋
  const layout = () => {
    const z = state.zoom;
    const cells = [];
    for (const tg of all) {
      if (!state.include.has(tg.state)) continue;
      const comp = compositeFor(tg.state, anim.playing ? (anim.cursors[tg.state] || 0) : 0);
      if (!comp) continue;
      const [x0, y0, x1, y1] = comp.box;
      cells.push({ tg, comp, box: comp.box, w: x1 - x0, h: y1 - y0 });
    }
    const gap = 6;
    const pad = 12;
    const maxW = Math.max(1, ...cells.map((c) => c.w));
    const maxH = Math.max(1, ...cells.map((c) => c.h));
    let W;
    let H;
    if (state.mode === "h") {
      W = pad * 2 + cells.reduce((a, c) => a + c.w, 0) + gap * Math.max(0, cells.length - 1);
      H = pad * 2 + maxH;
      let x = pad;
      for (const c of cells) {
        c.bx = x;
        c.by = (H - pad) - c.h; // 공유 바닥
        x += c.w + gap;
      }
    } else if (state.mode === "v") {
      W = pad * 2 + maxW;
      H = pad * 2 + cells.reduce((a, c) => a + c.h, 0) + gap * Math.max(0, cells.length - 1);
      let y = pad;
      for (const c of cells) {
        c.bx = Math.round((W - c.w) / 2); // 공유 가로 중심
        c.by = y;
        y += c.h + gap;
      }
    } else {
      W = pad * 2 + maxW;
      H = pad * 2 + maxH;
      for (const c of cells) {
        c.bx = Math.round((W - c.w) / 2);
        c.by = (H - pad) - c.h;
      }
    }
    // 자유 배치 오프셋
    for (const c of cells) {
      const off = state.offsets[c.tg.state] || { dx: 0, dy: 0 };
      c.dx = c.bx + off.dx;
      c.dy = c.by + off.dy;
    }
    return { cells, W, H, z, pad };
  };

  const render = () => {
    const { cells, W, H, z, pad } = layout();
    zoomLabel.textContent = `×${state.zoom}`;
    canvas.width = W * z;
    canvas.height = H * z;
    canvas.style.width = `${W * z}px`;
    canvas.style.height = `${H * z}px`;
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    cells.forEach((c, i) => {
      ctx.globalAlpha = state.mode === "overlay" && i > 0 ? 0.45 : 1;
      const [x0, y0] = c.box;
      ctx.drawImage(c.comp.canvas, x0, y0, c.w, c.h, c.dx * z, c.dy * z, c.w * z, c.h * z);
    });
    ctx.globalAlpha = 1;
    // 디폴트 정렬 기준선 (희미하게): 수평 = 바닥, 수직 = 가로 중심
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(37, 99, 235, 0.55)";
    if (state.mode !== "v") {
      const gy = (H - pad) * z + 0.5;
      ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(canvas.width, gy); ctx.stroke();
    } else {
      const gx = Math.round(W / 2) * z + 0.5;
      ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, canvas.height); ctx.stroke();
    }
    // 가이드선 DOM
    guidesEl.innerHTML = "";
    guidesEl.style.width = `${canvas.width}px`;
    guidesEl.style.height = `${canvas.height}px`;
    state.guides.forEach((g, gi) => {
      const ln = document.createElement("div");
      ln.className = `cmp-guide ${g.axis === "h" ? "gh" : "gv"}${gi === state.focus ? " focused" : ""}`;
      if (g.axis === "h") ln.style.top = `${g.pos * z}px`;
      else ln.style.left = `${g.pos * z}px`;
      const tag = document.createElement("span");
      tag.textContent = `${Math.round(g.pos)}px`;
      ln.appendChild(tag);
      ln.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        state.focus = gi;
        ln.setPointerCapture(ev.pointerId);
        let moved = false;
        const onMove = (e2) => {
          const r = canvas.getBoundingClientRect();
          moved = true;
          g.pos = g.axis === "h"
            ? Math.max(0, Math.min(H, (e2.clientY - r.top) / z))
            : Math.max(0, Math.min(W, (e2.clientX - r.left) / z));
          render();
        };
        const onUp = () => {
          ln.removeEventListener("pointermove", onMove);
          ln.removeEventListener("pointerup", onUp);
          if (moved) pushHist();
          else render(); // 포커스 표시 갱신
        };
        ln.addEventListener("pointermove", onMove);
        ln.addEventListener("pointerup", onUp);
      });
      guidesEl.appendChild(ln);
    });
    legend.innerHTML = cells.map((c) =>
      `<span class="cmp-key"><span class="cmp-name">${escapeHtml(c.tg.state)}</span> ${c.w}×${c.h}px</span>`
    ).join("");
  };

  // 스프라이트 드래그 / 빈 곳 클릭 = 가이드 추가 (Shift = 세로선)
  canvas.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    const r = canvas.getBoundingClientRect();
    const { cells, z } = layout();
    const lx = (ev.clientX - r.left) / z;
    const ly = (ev.clientY - r.top) / z;
    const hit = [...cells].reverse().find((c) =>
      lx >= c.dx && lx < c.dx + c.w && ly >= c.dy && ly < c.dy + c.h);
    if (hit) {
      canvas.setPointerCapture(ev.pointerId);
      const off = state.offsets[hit.tg.state] || (state.offsets[hit.tg.state] = { dx: 0, dy: 0 });
      const sx = lx - off.dx;
      const sy = ly - off.dy;
      let moved = false;
      const onMove = (e2) => {
        moved = true;
        off.dx = Math.round((e2.clientX - r.left) / z - sx);
        off.dy = Math.round((e2.clientY - r.top) / z - sy);
        render();
      };
      const onUp = () => {
        canvas.removeEventListener("pointermove", onMove);
        canvas.removeEventListener("pointerup", onUp);
        if (moved) pushHist();
      };
      canvas.addEventListener("pointermove", onMove);
      canvas.addEventListener("pointerup", onUp);
      return;
    }
    // 빈 곳: 가이드 추가 + 포커스
    state.guides.push(ev.shiftKey ? { axis: "v", pos: lx } : { axis: "h", pos: ly });
    state.focus = state.guides.length - 1;
    render();
    pushHist();
  });

  // 휠 = 배율
  modal.querySelector(".cmp-stage").addEventListener("wheel", (ev) => {
    ev.preventDefault();
    state.zoom = Math.max(2, Math.min(16, state.zoom + (ev.deltaY < 0 ? 1 : -1)));
    render();
  }, { passive: false });

  const onKey = (ev) => {
    if (ev.key === "Escape") { close(); return; }
    if (ev.key.toLowerCase() === "z" && (ev.metaKey || ev.ctrlKey)) {
      ev.preventDefault();
      ev.stopImmediatePropagation(); // 픽셀 편집 전역 라우터보다 비교 모달이 우선
      restoreHist(ev.shiftKey ? hist.pos + 1 : hist.pos - 1);
      return;
    }
    if ((ev.key === "Delete" || ev.key === "Backspace") && state.focus >= 0 && state.focus < state.guides.length) {
      ev.preventDefault();
      state.guides.splice(state.focus, 1);
      state.focus = -1;
      render();
      pushHist();
    }
  };
  const close = () => {
    modal.remove();
    document.removeEventListener("keydown", onKey, true);
  };
  modal.querySelectorAll('input[name="cmp-mode"]').forEach((el) =>
    el.addEventListener("change", () => {
      state.mode = el.value;
      state.offsets = {}; // 정렬 버튼 = 디폴트 배치로 리셋 (수홍 2026-07-18)
      render();
      pushHist();
    }));
  modal.querySelector(".zoom-close").addEventListener("click", close);
  modal.querySelector(".zoom-backdrop").addEventListener("click", close);
  const playBtn = modal.querySelector(".cmp-play");
  const animFrame = (ts) => {
    if (!document.body.contains(modal)) return;
    if (anim.playing) {
      let dirty = false;
      for (const name of state.include) {
        const st = run.states.find((s) => s.name === name);
        const fps = (st && st.fps) || 6;
        if (!anim.last[name]) anim.last[name] = ts;
        if (ts - anim.last[name] >= 1000 / Math.max(0.1, fps)) {
          anim.last[name] = ts;
          anim.cursors[name] = (anim.cursors[name] || 0) + 1;
          dirty = true;
        }
      }
      if (dirty) render();
    }
    requestAnimationFrame(animFrame);
  };
  playBtn.addEventListener("click", () => {
    anim.playing = !anim.playing;
    playBtn.textContent = anim.playing ? "⏸" : "▶";
    render();
  });
  requestAnimationFrame(animFrame);
  document.addEventListener("keydown", onKey, true); // capture — 전역 undo 라우터보다 먼저
  render();
  pushHist(); // 히스토리 바닥 = 초기 배치
}
