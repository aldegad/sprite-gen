// SPDX-License-Identifier: Apache-2.0
// sprite-gen curation webview — vanilla JS, no build step.
//
// Edits never touch the source frame PNGs. They mutate an in-memory model that
// mirrors curation.json and is auto-saved (debounced) via POST /api/curation.
// rotate is degrees, counter-clockwise positive (matches PIL bake). The preview
// (CSS + canvas) negates it because screen/CSS/canvas positive rotation is
// clockwise, so what you see is what compose_sprite_atlas.py will bake.

const IDENTITY = () => ({ rotate: 0, scale: 1, dx: 0, dy: 0, shx: 0, shy: 0 });
const SCALE_MIN = 0.2;
const SCALE_MAX = 3;
const DRAG_THRESHOLD = 4;

// forward 2x2 matrix (Rotate · Shear · Scale); mirrors curation.py transform_matrix
function matrixOf(t) {
  const rr = (t.rotate * Math.PI) / 180;
  const c = Math.cos(rr);
  const sn = Math.sin(rr);
  const s = t.scale;
  const shx = t.shx || 0;
  const shy = t.shy || 0;
  return {
    m00: s * (c + sn * shy),
    m01: s * (c * shx + sn),
    m10: s * (-sn + c * shy),
    m11: s * (c - sn * shx),
  };
}

// --- i18n (en / ko; initial language from server --lang, toggle reloads) ----
const STR = {
  en: {
    title: "curation", compose: "Bake atlas", export: "Export PNGs",
    groundGrid: "Ground grid", langOther: "한국어",
    frames: "frames", loop: "loop", nonLoop: "non-loop", preview: "Preview",
    excluded: "✗ exclude", selected: "✓ selected", extractFail: "⚠ extraction incomplete",
    editing: "editing…", saved: "saved", saveFail: "save failed: ",
    baking: "baking…", composeDone: "atlas baked", composeFail: "bake failed: ",
    exporting: "exporting…", exportFail: "export failed: ",
    ready: "ready", loaded: "loaded existing curation", runLoadFail: "failed to load run:",
    tRotate: "rotate", tShear: "shear — horizontal = shx, vertical = shy", tReset: "reset transform",
    hints: ["drag = move", "wheel = scale", "top handle = rotate", "bottom-left = shear", "click card = select/deselect", "saved automatically"],
    exportDone: (n) => `${n} PNGs → curated/`,
  },
  ko: {
    title: "큐레이션", compose: "아틀라스 굽기", export: "PNG 내보내기",
    groundGrid: "바닥 그리드", langOther: "EN",
    frames: "프레임", loop: "루프", nonLoop: "비루프", preview: "프리뷰",
    excluded: "✗ 제외", selected: "✓ 선택됨", extractFail: "⚠ 추출 미완료",
    editing: "편집 중…", saved: "저장됨", saveFail: "저장 실패: ",
    baking: "굽는 중…", composeDone: "아틀라스 완료", composeFail: "굽기 실패: ",
    exporting: "내보내는 중…", exportFail: "내보내기 실패: ",
    ready: "준비됨", loaded: "기존 큐레이션 로드됨", runLoadFail: "run 로드 실패:",
    tRotate: "회전", tShear: "기울이기 — 가로=shx, 세로=shy", tReset: "보정 초기화",
    hints: ["드래그 = 이동", "휠 = 확대/축소", "상단 핸들 = 회전", "좌하단 = 기울이기", "카드 클릭 = 선택/해제", "자동 저장"],
    exportDone: (n) => `PNG ${n}장 → curated/`,
  },
};
let lang = "en";
function t(key) {
  const v = (STR[lang] && STR[lang][key]) ?? STR.en[key];
  return v;
}

let run = null; // /api/run snapshot
let entries = {}; // { stateName: { selected: [idx], transforms: { idx: {..} } } }
const imageCache = new Map();

const statusEl = document.getElementById("status");
let saveTimer = null;

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = "status" + (kind ? " " + kind : "");
}

function img(url) {
  if (!imageCache.has(url)) {
    const i = new Image();
    i.src = url;
    imageCache.set(url, i);
  }
  return imageCache.get(url);
}

function getTransform(stateName, idx) {
  const t = entries[stateName].transforms;
  if (!t[idx]) t[idx] = IDENTITY();
  return t[idx];
}

function isSelected(stateName, idx) {
  return entries[stateName].selected.includes(idx);
}

function toggleSelect(stateName, idx) {
  const sel = entries[stateName].selected;
  const at = sel.indexOf(idx);
  if (at >= 0) sel.splice(at, 1);
  else {
    sel.push(idx);
    sel.sort((a, b) => a - b);
  }
}

// --- persistence -----------------------------------------------------------

function buildPayload() {
  const states = {};
  for (const [name, entry] of Object.entries(entries)) {
    const transforms = {};
    for (const [idx, t] of Object.entries(entry.transforms)) {
      if (t.rotate || t.scale !== 1 || t.dx || t.dy || t.shx || t.shy) transforms[idx] = t;
    }
    states[name] = { selected: entry.selected, transforms };
  }
  return { version: run.schemaVersion || 1, kind: "sprite-gen-curation", states };
}

function scheduleSave() {
  setStatus(t("editing"));
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 250);
}

async function save() {
  try {
    const res = await fetch("/api/curation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload()),
    });
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    setStatus(t("saved"), "ok");
  } catch (e) {
    setStatus(t("saveFail") + e.message, "err");
  }
}

// --- transform application -------------------------------------------------

function applyCardTransform(stage, stateName, idx) {
  const t = getTransform(stateName, idx);
  const el = stage.querySelector("img");
  if (!el) return;
  // dx/dy are stored in cell pixels; CSS needs rendered pixels.
  const ds = stage.clientWidth / run.cell.width;
  const m = matrixOf(t);
  // CSS matrix(a,b,c,d,e,f): a=m00 b=m10 c=m01 d=m11; translate applied after, about center.
  el.style.transform =
    `translate(${t.dx * ds}px, ${t.dy * ds}px) matrix(${m.m00}, ${m.m10}, ${m.m01}, ${m.m11}, 0, 0)`;
  const sh = t.shx || t.shy ? ` sh${(t.shx || 0).toFixed(2)},${(t.shy || 0).toFixed(2)}` : "";
  stage.closest(".card").querySelector(".tvals").textContent =
    `r${t.rotate.toFixed(0)}° ×${t.scale.toFixed(2)} ${t.dx >= 0 ? "+" : ""}${t.dx.toFixed(0)},${t.dy >= 0 ? "+" : ""}${t.dy.toFixed(0)}${sh}`;
}

// --- interactions ----------------------------------------------------------

function wireStage(stage, stateName, idx) {
  const ds = () => stage.clientWidth / run.cell.width;

  // translate by dragging, toggle select on a click that did not drag
  stage.addEventListener("pointerdown", (ev) => {
    if (ev.target.classList.contains("rotate-handle")) return;
    ev.preventDefault();
    stage.setPointerCapture(ev.pointerId);
    const t = getTransform(stateName, idx);
    const start = { x: ev.clientX, y: ev.clientY, dx: t.dx, dy: t.dy };
    let moved = false;

    const onMove = (e) => {
      const ddx = e.clientX - start.x;
      const ddy = e.clientY - start.y;
      if (Math.abs(ddx) > DRAG_THRESHOLD || Math.abs(ddy) > DRAG_THRESHOLD) moved = true;
      t.dx = start.dx + ddx / ds();
      t.dy = start.dy + ddy / ds();
      applyCardTransform(stage, stateName, idx);
    };
    const onUp = (e) => {
      stage.releasePointerCapture(ev.pointerId);
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("pointerup", onUp);
      if (!moved) {
        toggleSelect(stateName, idx);
        renderSelectionState(stateName);
      }
      scheduleSave();
    };
    stage.addEventListener("pointermove", onMove);
    stage.addEventListener("pointerup", onUp);
  });

  // scale with the wheel
  stage.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      const t = getTransform(stateName, idx);
      const factor = ev.deltaY < 0 ? 1.05 : 1 / 1.05;
      t.scale = Math.min(SCALE_MAX, Math.max(SCALE_MIN, t.scale * factor));
      applyCardTransform(stage, stateName, idx);
      scheduleSave();
    },
    { passive: false }
  );

  // rotate via the top handle
  const handle = stage.querySelector(".rotate-handle");
  handle.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    handle.setPointerCapture(ev.pointerId);
    const rect = stage.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const t = getTransform(stateName, idx);
    const startScreen = Math.atan2(ev.clientY - cy, ev.clientX - cx);
    const origRotate = t.rotate;

    const onMove = (e) => {
      const now = Math.atan2(e.clientY - cy, e.clientX - cx);
      // screen angle grows clockwise; schema is CCW positive -> subtract.
      const deltaDeg = ((now - startScreen) * 180) / Math.PI;
      t.rotate = origRotate - deltaDeg;
      applyCardTransform(stage, stateName, idx);
    };
    const onUp = () => {
      handle.releasePointerCapture(ev.pointerId);
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      scheduleSave();
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
  });

  // shear via the bottom-left handle: horizontal drag = shx, vertical = shy
  const shear = stage.querySelector(".shear-handle");
  shear.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    shear.setPointerCapture(ev.pointerId);
    const t = getTransform(stateName, idx);
    const start = { x: ev.clientX, y: ev.clientY, shx: t.shx || 0, shy: t.shy || 0 };
    const onMove = (e) => {
      // full-width drag ≈ 1.0 slope; small moves give fine control
      t.shx = start.shx + (e.clientX - start.x) / stage.clientWidth;
      t.shy = start.shy + (e.clientY - start.y) / stage.clientHeight;
      applyCardTransform(stage, stateName, idx);
    };
    const onUp = () => {
      shear.releasePointerCapture(ev.pointerId);
      shear.removeEventListener("pointermove", onMove);
      shear.removeEventListener("pointerup", onUp);
      scheduleSave();
    };
    shear.addEventListener("pointermove", onMove);
    shear.addEventListener("pointerup", onUp);
  });
}

function resetTransform(stateName, idx, stage) {
  entries[stateName].transforms[idx] = IDENTITY();
  applyCardTransform(stage, stateName, idx);
  scheduleSave();
}

// --- rendering -------------------------------------------------------------

function renderSelectionState(stateName) {
  document.querySelectorAll(`.card[data-state="${cssEscape(stateName)}"]`).forEach((card) => {
    const idx = Number(card.dataset.idx);
    const sel = isSelected(stateName, idx);
    card.classList.toggle("selected", sel);
    card.classList.toggle("rejected", !sel);
    const btn = card.querySelector(".sel-btn");
    if (btn) btn.textContent = sel ? t("selected") : t("excluded");
  });
  const state = run.states.find((s) => s.name === stateName);
  const countEl = document.querySelector(`.preview[data-state="${cssEscape(stateName)}"] .count`);
  if (countEl) countEl.textContent = `${entries[stateName].selected.length}/${state.requestFrames} ${t("frames")}`;
}

function cssEscape(s) {
  return s.replace(/"/g, '\\"');
}

function renderState(state) {
  const wrap = document.createElement("section");
  wrap.className = "state";

  const head = document.createElement("div");
  head.className = "state-head";
  head.innerHTML =
    `<span class="name">${state.name}</span>` +
    `<span class="meta">${state.requestFrames} ${t("frames")} · ${state.fps}fps · ${state.loop ? t("loop") : t("nonLoop")}</span>` +
    (state.action ? `<span class="action">${state.action}</span>` : "") +
    (state.extractOk ? "" : `<span class="state-warn">${t("extractFail")}</span>`);
  wrap.appendChild(head);

  const body = document.createElement("div");
  body.className = "state-body";

  const framesEl = document.createElement("div");
  framesEl.className = "frames";
  for (const frame of state.frames) {
    framesEl.appendChild(renderCard(state, frame));
  }
  body.appendChild(framesEl);
  body.appendChild(renderPreview(state));
  wrap.appendChild(body);

  document.getElementById("states").appendChild(wrap);

  // wire stages after they are in the DOM (need clientWidth)
  for (const frame of state.frames) {
    if (!frame.present) continue;
    const stage = wrap.querySelector(`.card[data-idx="${frame.index}"] .stage`);
    wireStage(stage, state.name, frame.index);
    applyCardTransform(stage, state.name, frame.index);
    if (run.iso) drawGroundGrid(stage);
  }
  renderSelectionState(state.name);
  startPreview(state);
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
      `<img src="${frame.url}" alt="frame ${frame.index}" draggable="false" />` +
      `<div class="rotate-handle" title="${t("tRotate")}"></div>` +
      `<div class="shear-handle" title="${t("tShear")}"></div>`
    : `<div class="missing-label">missing</div>`;

  const label = frame.label ? `${frame.label}` : `#${frame.index}`;
  card.innerHTML =
    `<div class="card-top">` +
    `<span class="idx" title="frame ${frame.index}">${label}</span>` +
    `<button type="button" class="ghost sel-btn">${t("excluded")}</button>` +
    `</div>` +
    `<div class="stage">${stageInner}</div>` +
    `<div class="card-controls">` +
    `<span class="tvals"></span>` +
    `<button type="button" class="ghost reset-btn" title="${t("tReset")}">↺</button>` +
    `</div>`;

  card.querySelector(".sel-btn").addEventListener("click", () => {
    toggleSelect(state.name, frame.index);
    renderSelectionState(state.name);
    scheduleSave();
  });
  if (frame.present) {
    card.querySelector(".reset-btn").addEventListener("click", () =>
      resetTransform(state.name, frame.index, card.querySelector(".stage"))
    );
  }
  return card;
}

function renderPreview(state) {
  const box = document.createElement("div");
  box.className = "preview";
  box.dataset.state = state.name;
  const aspect = run.cell.height / run.cell.width;
  box.innerHTML =
    `<h4>${t("preview")}</h4>` +
    `<canvas width="${run.cell.width}" height="${run.cell.height}" style="height:${(160 * aspect).toFixed(0)}px"></canvas>` +
    `<div class="count"></div>`;
  return box;
}

function startPreview(state) {
  const canvas = document.querySelector(`.preview[data-state="${cssEscape(state.name)}"] canvas`);
  const ctx = canvas.getContext("2d");
  const cw = run.cell.width;
  const ch = run.cell.height;
  let cursor = 0;
  let last = 0;

  function frame(ts) {
    const sel = entries[state.name].selected;
    const interval = 1000 / Math.max(1, state.fps);
    if (ts - last >= interval) {
      last = ts;
      cursor = sel.length ? (cursor + 1) % sel.length : 0;
    }
    ctx.clearRect(0, 0, cw, ch);
    if (sel.length) {
      const idx = sel[cursor % sel.length];
      const url = state.frames[idx] ? state.frames[idx].url : null;
      const image = url ? img(url) : null;
      if (image && image.complete && image.naturalWidth) {
        const t = getTransform(state.name, idx);
        const m = matrixOf(t);
        ctx.save();
        ctx.translate(cw / 2 + t.dx, ch / 2 + t.dy);
        ctx.transform(m.m00, m.m10, m.m01, m.m11, 0, 0); // same matrix as CSS + bake
        ctx.drawImage(image, -cw / 2, -ch / 2, cw, ch);
        ctx.restore();
      }
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

// --- iso ground grid overlay -----------------------------------------------

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
      ctx.strokeStyle = anchorTile ? "rgba(93,176,255,0.95)" : "rgba(93,176,255,0.28)";
      ctx.stroke();
    }
  }
  // axis guide lines through the anchor (the true 2:1 slopes)
  ctx.strokeStyle = "rgba(255,180,80,0.85)";
  ctx.lineWidth = 1.5;
  for (const [sx, sy] of [[1, 1], [1, -1]]) {
    ctx.beginPath();
    ctx.moveTo(ox - sx * tw * 3, oy - sy * th * 3);
    ctx.lineTo(ox + sx * tw * 3, oy + sy * th * 3);
    ctx.stroke();
  }
}

const gridToggle = document.getElementById("grid-toggle");
const langToggle = document.getElementById("lang-toggle");
gridToggle.addEventListener("click", () => {
  const on = document.body.classList.toggle("show-grid");
  gridToggle.textContent = `${t("groundGrid")} ${on ? "▣" : "▢"}`;
  if (on) document.querySelectorAll(".stage").forEach(drawGroundGrid);
});

// language toggle reloads with ?lang= so preview rAF loops are not duplicated
langToggle.addEventListener("click", () => {
  const next = lang === "en" ? "ko" : "en";
  const u = new URL(location.href);
  u.searchParams.set("lang", next);
  location.href = u.toString();
});

function applyStaticLang() {
  document.getElementById("t-title").textContent = t("title");
  document.getElementById("compose").textContent = t("compose");
  document.getElementById("export").textContent = t("export");
  gridToggle.textContent = `${t("groundGrid")} ${document.body.classList.contains("show-grid") ? "▣" : "▢"}`;
  langToggle.textContent = t("langOther");
  document.getElementById("hintbar").innerHTML = t("hints").map((h) => `<span>${h}</span>`).join("");
}

// --- compose ---------------------------------------------------------------

document.getElementById("compose").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  clearTimeout(saveTimer);
  await save();
  setStatus(t("baking"));
  try {
    const res = await fetch("/api/compose", { method: "POST" });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error((data.stderr || data.error || "compose failed").trim());
    setStatus(t("composeDone"), "ok");
  } catch (e) {
    setStatus(t("composeFail") + e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("export").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  clearTimeout(saveTimer);
  await save();
  setStatus(t("exporting"));
  try {
    const res = await fetch("/api/export", { method: "POST" });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error((data.stderr || data.error || "export failed").trim());
    const out = data.export || {};
    setStatus(STR[lang].exportDone(out.count || 0), "ok");
    console.log("exported to:", out.out_dir, out.files);
  } catch (e) {
    setStatus(t("exportFail") + e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

// --- bootstrap -------------------------------------------------------------

function seedEntries() {
  entries = {};
  const curated = (run.curation && run.curation.states) || {};
  for (const state of run.states) {
    const present = state.frames.filter((f) => f.present).map((f) => f.index);
    const c = curated[state.name];
    let selected;
    if (c && Array.isArray(c.selected) && c.selected.length) {
      selected = c.selected.filter((i) => present.includes(i));
    } else {
      selected = present.slice();
    }
    const transforms = {};
    if (c && c.transforms) {
      for (const [idx, t] of Object.entries(c.transforms)) {
        transforms[idx] = { ...IDENTITY(), ...t };
      }
    }
    entries[state.name] = { selected, transforms };
  }
}

async function boot() {
  try {
    const res = await fetch("/api/run");
    run = await res.json();
    if (run.error) throw new Error(run.error);
  } catch (e) {
    document.getElementById("states").innerHTML =
      `<div class="fatal">${t("runLoadFail")}\n${e.message}</div>`;
    return;
  }
  // initial language: ?lang= (set by the toggle) overrides the server --lang
  lang = new URLSearchParams(location.search).get("lang") || run.lang || "en";
  document.documentElement.lang = lang;
  applyStaticLang();
  document.getElementById("character").textContent = `${run.characterId} · ${run.cell.width}×${run.cell.height}`;
  if (run.iso) gridToggle.hidden = false;
  seedEntries();
  for (const state of run.states) renderState(state);
  setStatus(run.curation && Object.keys(run.curation.states || {}).length ? t("loaded") : t("ready"));
}

boot();
