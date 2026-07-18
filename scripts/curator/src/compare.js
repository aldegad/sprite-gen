// SPDX-License-Identifier: Apache-2.0
// curator/compare.js — 방향 앵커 비교 캔버스 (나란히/겹침 + 가이드 선 + 오니언스킨)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// 수홍 요청 2026-07-18: 정면/옆/뒤 idle 을 같은 수평·수직에 놓고 비교하고, 줄도
// 치고, 겹쳐도 보고 싶다. 발끝(바닥) 정렬이 기본 — 도트 캐릭터의 공유 기준선.
// 수평 가이드는 클릭으로 추가, 드래그로 이동, 더블클릭으로 제거.

function openCompare() {
  document.getElementById("compare-modal")?.remove();
  // 비교 대상: 각 방향 앵커(있는 것만) 의 캐노니컬 frame-0
  const targets = [];
  for (const st of run.states) {
    if (!/_idle$/.test(st.name)) continue;
    const f = st.frames.find((fr) => fr.present);
    if (f && f.contentBox) targets.push({ state: st.name, frame: f });
  }
  if (!targets.length) {
    setStatus(t("cmpNone"), "err");
    return;
  }
  const modal = document.createElement("div");
  modal.id = "compare-modal";
  modal.innerHTML =
    `<div class="zoom-backdrop"></div>` +
    `<div class="card cmp-card">` +
    `<div class="zoom-head"><span class="zoom-title">${t("cmpTitle")}</span>` +
    `<span class="cmp-controls">` +
    `<label class="pp-apply"><input type="radio" name="cmp-mode" value="side" checked /><span>${t("cmpSide")}</span></label>` +
    `<label class="pp-apply"><input type="radio" name="cmp-mode" value="overlay" /><span>${t("cmpOverlay")}</span></label>` +
    `<select class="cmp-zoom"><option value="6">×6</option><option value="8" selected>×8</option><option value="10">×10</option></select>` +
    `</span>` +
    `<button type="button" class="ghost zoom-close">${t("zoomClose")}</button></div>` +
    `<div class="cmp-hint">${t("cmpHint")}</div>` +
    `<div class="cmp-stage"><canvas class="cmp-canvas"></canvas><div class="cmp-guides"></div></div>` +
    `<div class="cmp-legend"></div>` +
    `</div>`;
  document.body.appendChild(modal);
  const canvas = modal.querySelector(".cmp-canvas");
  const guidesEl = modal.querySelector(".cmp-guides");
  const legend = modal.querySelector(".cmp-legend");
  const stageEl = modal.querySelector(".cmp-stage");
  const state = { mode: "side", zoom: 8, guides: [] }; // guides: 캔버스 y(논리px)

  const imgs = new Map();
  let loaded = 0;
  for (const tg of targets) {
    const im = new Image();
    im.src = tg.frame.url;
    im.onload = () => { loaded += 1; render(); };
    imgs.set(tg.state, im);
  }

  // 발끝(바닥) 정렬 배치 계산 — 나란히: 가로로 나열, 겹침: 같은 중심
  const layout = () => {
    const z = state.zoom;
    const cells = targets.map((tg) => {
      const [x0, y0, x1, y1] = tg.frame.contentBox;
      return { tg, w: x1 - x0, h: y1 - y0, box: tg.frame.contentBox };
    });
    const maxH = Math.max(...cells.map((c) => c.h));
    const gap = 6;
    const pad = 10;
    let W;
    if (state.mode === "side") {
      W = pad * 2 + cells.reduce((a, c) => a + c.w, 0) + gap * (cells.length - 1);
    } else {
      W = pad * 2 + Math.max(...cells.map((c) => c.w));
    }
    const H = pad * 2 + maxH;
    const ground = H - pad; // 공유 바닥선 (발끝 정렬)
    let x = pad;
    for (const c of cells) {
      if (state.mode === "side") {
        c.dx = x;
        x += c.w + gap;
      } else {
        c.dx = Math.round((W - c.w) / 2);
      }
      c.dy = ground - c.h; // 바닥 정렬
    }
    return { cells, W, H, ground, z };
  };

  const render = () => {
    const { cells, W, H, ground, z } = layout();
    canvas.width = W * z;
    canvas.height = H * z;
    canvas.style.width = `${W * z}px`;
    canvas.style.height = `${H * z}px`;
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    // 겹침 모드: 뒤에서부터 반투명 (오니언스킨)
    cells.forEach((c, i) => {
      const im = imgs.get(c.tg.state);
      if (!(im && im.complete && im.naturalWidth)) return;
      ctx.globalAlpha = state.mode === "overlay" ? (i === 0 ? 1 : 0.45) : 1;
      const [x0, y0] = c.box;
      ctx.drawImage(im, x0, y0, c.w, c.h, c.dx * z, c.dy * z, c.w * z, c.h * z);
    });
    ctx.globalAlpha = 1;
    // 공유 바닥선 + 각 정수리선 (희미하게)
    ctx.strokeStyle = "rgba(37, 99, 235, 0.8)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(0, ground * z + 1); ctx.lineTo(canvas.width, ground * z + 1); ctx.stroke();
    ctx.strokeStyle = "rgba(148, 163, 184, 0.65)";
    ctx.lineWidth = 1;
    for (const c of cells) {
      ctx.beginPath(); ctx.moveTo(0, c.dy * z + 0.5); ctx.lineTo(canvas.width, c.dy * z + 0.5); ctx.stroke();
    }
    // 사용자 가이드 (드래그 가능한 빨간 수평선)
    guidesEl.innerHTML = "";
    guidesEl.style.width = `${canvas.width}px`;
    guidesEl.style.height = `${canvas.height}px`;
    for (let gi = 0; gi < state.guides.length; gi++) {
      const gy = state.guides[gi];
      const ln = document.createElement("div");
      ln.className = "cmp-guide";
      ln.style.top = `${gy * z}px`;
      const tag = document.createElement("span");
      tag.textContent = `${Math.round(ground - gy)}px`; // 바닥에서의 높이
      ln.appendChild(tag);
      ln.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        ln.setPointerCapture(ev.pointerId);
        const onMove = (e2) => {
          const r = canvas.getBoundingClientRect();
          state.guides[gi] = Math.max(0, Math.min(H, (e2.clientY - r.top) / z));
          render();
        };
        const onUp = () => {
          ln.removeEventListener("pointermove", onMove);
          ln.removeEventListener("pointerup", onUp);
        };
        ln.addEventListener("pointermove", onMove);
        ln.addEventListener("pointerup", onUp);
      });
      ln.addEventListener("dblclick", () => {
        state.guides.splice(gi, 1);
        render();
      });
      guidesEl.appendChild(ln);
    }
    // 범례: 이름 + 콘텐츠 크기 (+ 바닥선 기준 키 비교)
    legend.innerHTML = cells.map((c) =>
      `<span class="cmp-key"><span class="cmp-name">${escapeHtml(c.tg.state)}</span> ${c.w}×${c.h}px</span>`
    ).join("");
  };

  // 캔버스 클릭 = 그 자리에 수평 가이드 추가
  canvas.addEventListener("click", (ev) => {
    const r = canvas.getBoundingClientRect();
    const { z } = layout();
    state.guides.push((ev.clientY - r.top) / z);
    render();
  });
  modal.querySelectorAll('input[name="cmp-mode"]').forEach((el) =>
    el.addEventListener("change", () => { state.mode = el.value; render(); }));
  modal.querySelector(".cmp-zoom").addEventListener("change", (ev) => {
    state.zoom = parseInt(ev.target.value, 10) || 8;
    render();
  });
  const close = () => {
    modal.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (ev) => { if (ev.key === "Escape") close(); };
  modal.querySelector(".zoom-close").addEventListener("click", close);
  modal.querySelector(".zoom-backdrop").addEventListener("click", close);
  document.addEventListener("keydown", onKey);
  render();
}
