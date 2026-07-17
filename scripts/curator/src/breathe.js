// SPDX-License-Identifier: Apache-2.0
// curator/breathe.js — 결정론 호흡 — 줄 체크박스 토글 + 사이클 투입/제거 + 테이크 베이크 요청
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

let pendingBreathe = false; // 호흡 라벨 → 줌 모달 오픈 시 호흡 모드 진입 플래그

// ── 호흡 = 줄 체크박스 토글 (수홍 확정 2026-07-17 "목록쪽에서 저 버튼에 체크박스") ──
// 체크 ON = 호흡 사이클이 시퀀스에 들어있는 상태. 테이크가 없으면 첫 체크가
// 기본값(실루엣 휴리스틱 선 1개, 진폭 1px)으로 굽고, 있으면 즉시 재투입한다.
// 체크 OFF = 시퀀스에서만 제거 — 테이크는 보존 (재체크 즉시 복원).

// 이 줄의 호흡 위상 프레임 인덱스들 (오름차순 = P1..PK 캐스케이드 순서)
function breathePhaseIdxs(stateName) {
  const st = run.states.find((s) => s.name === stateName);
  if (!st) return [];
  return st.frames
    .filter((f) => (f.label || "").startsWith("breathe") && f.present)
    .map((f) => f.index);
}

function breatheActive(stateName) {
  const e = entries[stateName];
  const bset = new Set(breathePhaseIdxs(stateName));
  if (!e || !bset.size) return false;
  for (const i of e.sel) {
    const src = cloneSrc(stateName, i);
    if (bset.has(src === null ? i : src)) return true;
  }
  return false;
}

// 현재 시퀀스에서 최하강 위상(PK)의 유지 길이 — 에디터 주기 select 초기값 복원용
function breatheCadenceOf(stateName) {
  const phases = breathePhaseIdxs(stateName);
  const e = entries[stateName];
  if (!phases.length || !e) return 0;
  const last = phases[phases.length - 1];
  let n = 0;
  for (const i of e.sel) {
    const src = cloneSrc(stateName, i);
    if ((src === null ? i : src) === last) n++;
  }
  return n;
}

// 호흡 프레임을 시퀀스에서 제거: 복제 인스턴스는 통째로 삭제, 원본 위상은 풀로 강등
function removeBreathe(stateName) {
  const e = entries[stateName];
  const bset = new Set(breathePhaseIdxs(stateName));
  if (!e || !bset.size) return;
  for (const [ci, src] of Object.entries(e.clones || {})) {
    if (!bset.has(src)) continue;
    const idx = Number(ci);
    e.sel.delete(idx);
    e.order = e.order.filter((i) => i !== idx);
    delete e.clones[idx];
    delete e.transforms[idx];
    delete e.pixels[idx];
  }
  for (const i of bset) e.sel.delete(i);
}

// 호흡 사이클 투입 — 시퀀스 끝에 [P1..PK(들숨 캐스케이드), PK×(주기-1)(유지),
// PK-1..P1(날숨 캐스케이드)]. 반복 등장은 복제 인스턴스로 (인덱스는 시퀀스에 1회).
function injectBreathe(stateName, cadence) {
  const e = entries[stateName];
  const st = run.states.find((s) => s.name === stateName);
  const phases = breathePhaseIdxs(stateName);
  if (!e || !st || !phases.length) return false;
  removeBreathe(stateName);
  e.clones = e.clones || {};
  const seq = e.order.filter((i) => e.sel.has(i));
  const tail = [];
  const usedOnce = new Set();
  const instance = (idx) => {
    if (!usedOnce.has(idx)) { usedOnce.add(idx); return idx; }
    const used = [...st.frames.map((f) => f.index), ...Object.keys(e.clones).map(Number),
                  ...e.order, ...e.archived, ...tail];
    const newIdx = Math.max(-1, ...used) + 1;
    e.clones[newIdx] = idx;
    return newIdx;
  };
  for (const p of phases) tail.push(instance(p));
  const deepest = phases[phases.length - 1];
  for (let k = 1; k < Math.max(1, cadence); k++) tail.push(instance(deepest));
  for (let k = phases.length - 2; k >= 0; k--) tail.push(instance(phases[k]));
  const rest = e.order.filter((i) => !e.sel.has(i) && !tail.includes(i));
  e.order = [...seq, ...tail, ...rest];
  for (const i of tail) e.sel.add(i);
  return true;
}

// 실루엣 휴리스틱 (에디터 기본 가슴선과 같은 식): 상반신 최광폭 행 = 어깨 바로 아래
function silhouetteStats(data, W, H) {
  let top = H, bot = 0;
  const widths = new Array(H).fill(0);
  for (let y = 0; y < H; y++) {
    let w = 0;
    for (let x = 0; x < W; x++) if (data[(y * W + x) * 4 + 3] >= 40) w++;
    widths[y] = w;
    if (w) { top = Math.min(top, y); bot = Math.max(bot, y + 1); }
  }
  const h = Math.max(1, bot - top);
  let shoulderY = top + Math.floor(h * 0.3);
  let best = 0;
  for (let y = top; y < top + Math.floor(h * 0.55); y++) {
    if (widths[y] > best) { best = widths[y]; shoulderY = y; }
  }
  const split = Math.min(0.75, Math.max(0.3, (shoulderY + Math.floor(h * 0.1) - top) / h));
  return { top, h, split };
}

// 리로드 후 사이클 투입 예약 (테이크 굽기 → 재추출 → 리로드 → 여기서 시퀀스 반영)
const BREATHE_PENDING_KEY = "sg-breathe-inject";

function applyPendingBreatheInject() {
  let pend = null;
  try { pend = JSON.parse(localStorage.getItem(BREATHE_PENDING_KEY) || "null"); } catch { /* 무시 */ }
  if (!pend || pend.character !== run.characterId) return;
  localStorage.removeItem(BREATHE_PENDING_KEY);
  if (!injectBreathe(pend.state, pend.cadence || 3)) return;
  scheduleSave();
  rebuildState(pend.state);
  setStatus(STR[lang].breatheOn(pend.state));
}

async function bakeBreatheTake(stateName, splits, amplitude, frameIdx, cadence) {
  setStatus(t("breatheBusy"));
  const res = await fetch("/api/breathe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state: stateName, frame: frameIdx,
                           splits: splits.map((s) => Math.round(s * 100) / 100),
                           amplitude }),
  });
  const dataR = await res.json();
  if (!res.ok || !dataR.ok) {
    throw new Error(dataR.error || (dataR.stderr || "").trim().split("\n").pop() || res.status);
  }
  localStorage.setItem(BREATHE_PENDING_KEY, JSON.stringify(
    { character: run.characterId, state: stateName, cadence }));
  setStatus(STR[lang].breatheDone(stateName));
  setTimeout(() => window.location.reload(), 800);
}

function makeBreatheToggle(stateName) {
  const wrap = document.createElement("span");
  wrap.className = "pp-apply row-toggle breathe-toggle";
  wrap.title = t("tRowBreathe");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = breatheActive(stateName);
  input.addEventListener("change", async () => {
    if (!input.checked) {
      removeBreathe(stateName);
      scheduleSave();
      rebuildState(stateName);
      setStatus(STR[lang].breatheOff(stateName));
      return;
    }
    if (breathePhaseIdxs(stateName).length) {
      injectBreathe(stateName, 3);
      scheduleSave();
      rebuildState(stateName);
      setStatus(STR[lang].breatheOn(stateName));
      return;
    }
    // 테이크가 아직 없다 → 기본값으로 굽는다 (실루엣 휴리스틱 선 1개, 진폭 1px)
    input.disabled = true;
    try {
      const st = run.states.find((s) => s.name === stateName);
      const first = st && st.frames.find((f) => f.present);
      if (!first) throw new Error("no extracted frame");
      const img = new Image();
      img.src = first.url;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const cx = c.getContext("2d");
      cx.drawImage(img, 0, 0);
      const sil = silhouetteStats(cx.getImageData(0, 0, c.width, c.height).data, c.width, c.height);
      await bakeBreatheTake(stateName, [sil.split], 1, first.index, 3);
    } catch (e) {
      setStatus(t("breatheFail") + e.message, "err");
      input.checked = false;
      input.disabled = false;
    }
  });
  const lbl = document.createElement("span");
  lbl.className = "breathe-open";
  lbl.title = t("tRowBreatheEdit");
  lbl.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M2 11c2.5 0 2.5-3 5-3s2.5 3 5 3 2-2 2-2" fill="none" stroke="currentColor" ' +
    'stroke-width="1.4" stroke-linecap="round"/></svg>' +
    `<span>${t("rowBreathe")}</span>`;
  lbl.addEventListener("click", (ev) => {
    ev.preventDefault();
    const st = run.states.find((s) => s.name === stateName);
    const take = st && (st.takes || []).find((tk) => (tk.label || "") === "breathe");
    const srcIdx = take && take.breathe && Number.isInteger(take.breathe.frame)
      ? take.breathe.frame
      : ((st && st.frames.find((f) => f.present)) || { index: 0 }).index;
    pendingBreathe = true;
    openZoom(stateName, srcIdx);
  });
  wrap.appendChild(input);
  wrap.appendChild(lbl);
  return wrap;
}
