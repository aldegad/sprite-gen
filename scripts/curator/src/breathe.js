// SPDX-License-Identifier: Apache-2.0
// curator/breathe.js — 결정론 호흡 후처리 레이어 (사이드카) — 토글·패턴·위상 합성
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// 호흡은 프레임 선택(깜빡임)과 직교하는 변조 레이어다 (수홍 확정 2026-07-18).
// truth = entries[state].breathe = {splits, amplitude, hold, subpixel} | null.
// 저장은 curation.json 사이드카 — 서버 굽기(compose/GIF)와 같은 수학
// (sprite_gen/breathe.py breathe_pattern/phase_frame)의 JS 미러가 미리보기를 그린다.
// 테이크 굽기·재추출·적용 대기는 없다: 토글/조정 = 즉시 저장·즉시 재생 반영.

let pendingBreathe = false; // 호흡 라벨 → 줌 모달 오픈 시 호흡 모드 진입 플래그

function stateBreathe(stateName) {
  const e = entries[stateName];
  return e && e.breathe ? e.breathe : null;
}

// 서버 breathe_pattern 미러 — 1주기 위상 시퀀스 [0×hold, 1..K, K×(hold-1), K-1..1]
// (+subpixel 이면 전이 경계에 반정수 블렌드 위상 삽입)
function breathePattern(cfg) {
  const k = cfg.splits.length;
  const hold = Math.max(1, cfg.hold || 3);
  const pattern = [];
  for (let i = 0; i < hold; i++) pattern.push(0);
  for (let p = 1; p <= k; p++) pattern.push(p);
  for (let i = 1; i < hold; i++) pattern.push(k);
  for (let p = k - 1; p >= 1; p--) pattern.push(p);
  if (!cfg.subpixel) return pattern;
  const out = [];
  for (let i = 0; i < pattern.length; i++) {
    const prev = pattern[(i - 1 + pattern.length) % pattern.length];
    if (prev !== pattern[i]) out.push((prev + pattern[i]) / 2);
    out.push(pattern[i]);
  }
  return out;
}

// 서버 phase_frame 미러 — base 캔버스에 위상 하나를 적용한 새 캔버스를 돌려준다.
// 정수 위상: p<K = 밴드 시프트(+경계 행 스트레치), p=K = 최하단 선 위 전체 시프트.
// 반정수 위상: 인접 두 위상의 50% 블렌드 (서브픽셀 애니메이션 근사 — 굽기 SSoT 는 서버).
function breatheComposite(base, cfg, phase) {
  const w = base.width;
  const h = base.height;
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const ctx = out.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  if (!phase || phase <= 0) {
    ctx.drawImage(base, 0, 0);
    return out;
  }
  const lo = Math.floor(phase);
  if (phase > lo) {
    // 서브픽셀 (수홍 정정 2026-07-18): 중간색은 움직이는 경계 밴드(정수리 + 각
    // 분할선 이음새)에만 — 전체 블렌드는 몸통 안 가로 경계까지 잔상을 만든다.
    const a = breatheComposite(base, cfg, lo);
    const b = breatheComposite(base, cfg, Math.min(lo + 1, cfg.splits.length));
    ctx.drawImage(a, 0, 0);
    const bd = base.getContext("2d").getImageData(0, 0, w, h).data;
    let bTop = h, bBot = 0;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (bd[(y * w + x) * 4 + 3] >= 40) {
          if (y < bTop) bTop = y;
          if (y + 1 > bBot) bBot = y + 1;
          break;
        }
      }
    }
    if (bBot > bTop) {
      const amp = Math.max(1, cfg.amplitude || 1);
      const seams = [bTop, ...cfg.splits.map((s) => bTop + Math.floor((bBot - bTop) * s))];
      const mixed = document.createElement("canvas");
      mixed.width = w;
      mixed.height = h;
      const mctx = mixed.getContext("2d");
      mctx.imageSmoothingEnabled = false;
      mctx.drawImage(a, 0, 0);
      mctx.globalAlpha = 0.5;
      mctx.drawImage(b, 0, 0);
      mctx.globalAlpha = 1;
      for (const y of seams) {
        const r0 = Math.max(0, y - 1);
        const r1 = Math.min(h, y + amp + 1);
        if (r1 <= r0) continue;
        ctx.clearRect(0, r0, w, r1 - r0); // 교체 (합성 아님) — 서버 paste 와 동형
        ctx.drawImage(mixed, 0, r0, w, r1 - r0, 0, r0, w, r1 - r0);
      }
    }
    return out;
  }
  const data = base.getContext("2d").getImageData(0, 0, w, h).data;
  let top = h, bot = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (data[(y * w + x) * 4 + 3] >= 40) {
        if (y < top) top = y;
        if (y + 1 > bot) bot = y + 1;
        break;
      }
    }
  }
  ctx.drawImage(base, 0, 0);
  if (bot <= top) return out;
  const K = cfg.splits.length;
  const amp = Math.max(1, cfg.amplitude || 1);
  const ys = cfg.splits.map((s) => top + Math.floor((bot - top) * s));
  const yBottom = Math.max(top + 1, ys[K - 1]);
  const p = Math.min(lo, K);
  if (p === K) {
    ctx.clearRect(0, top, w, yBottom - top);
    ctx.drawImage(base, 0, top, w, yBottom - top, 0, top + amp, w, yBottom - top);
  } else {
    const yTop = ys[K - 1 - p];
    ctx.drawImage(base, 0, yTop, w, yBottom - yTop, 0, yTop + amp, w, yBottom - yTop);
    ctx.drawImage(base, 0, yTop, w, 1, 0, yTop, w, amp);
  }
  return out;
}

// 실루엣 휴리스틱 (기본 가슴선): 상반신 최광폭 행 = 어깨 바로 아래
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

// 첫 활성화 기본값: 첫 프레임 이미지에서 휴리스틱 가슴선 1개
async function defaultBreatheConfig(stateName) {
  const st = run.states.find((s) => s.name === stateName);
  const first = st && st.frames.find((f) => f.present);
  if (!first) throw new Error("no extracted frame");
  const image = new Image();
  image.src = first.url;
  await image.decode();
  const c = document.createElement("canvas");
  c.width = image.naturalWidth;
  c.height = image.naturalHeight;
  const cx = c.getContext("2d");
  cx.drawImage(image, 0, 0);
  const sil = silhouetteStats(cx.getImageData(0, 0, c.width, c.height).data, c.width, c.height);
  return { splits: [Math.round(sil.split * 100) / 100], amplitude: 1, hold: 3, subpixel: false };
}

// 레거시 자가 이전 (self-heal): 구 테이크 방식이 시퀀스에 끼워둔 breathe 위상
// 프레임들을 시퀀스에서 걷어내고, 테이크에 기록된 파라미터를 레이어 설정으로 옮긴다.
// (테이크 원본/추출 프레임은 그대로 — 풀에서는 숨겨진다. 재추출 불필요.)
function migrateLegacyBreathe(stateName) {
  const e = entries[stateName];
  const st = run.states.find((s) => s.name === stateName);
  if (!e || !st || e.breathe) return false;
  const legacy = st.frames.filter((f) => (f.label || "").startsWith("breathe")).map((f) => f.index);
  const legacySet = new Set(legacy);
  const inSeq = [...e.sel].some((i) => {
    const src = cloneSrc(stateName, i);
    return legacySet.has(src === null ? i : src);
  });
  if (!inSeq) return false;
  for (const [ci, src] of Object.entries(e.clones || {})) {
    if (!legacySet.has(src)) continue;
    const idx = Number(ci);
    e.sel.delete(idx);
    e.order = e.order.filter((i) => i !== idx);
    delete e.clones[idx];
    delete e.transforms[idx];
    delete e.pixels[idx];
  }
  for (const i of legacySet) e.sel.delete(i);
  const take = (st.takes || []).find((tk) => (tk.label || "") === "breathe");
  const saved = take && take.breathe;
  e.breathe = saved && Array.isArray(saved.splits) && saved.splits.length
    ? { splits: saved.splits.map(Number), amplitude: saved.amplitude || 1, hold: 3, subpixel: false }
    : { splits: [0.55], amplitude: 1, hold: 3, subpixel: false };
  return true;
}

function makeBreatheToggle(stateName) {
  const wrap = document.createElement("span");
  wrap.className = "pp-apply row-toggle breathe-toggle";
  wrap.title = t("tRowBreathe");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = !!stateBreathe(stateName);
  input.addEventListener("change", async () => {
    const e = entries[stateName];
    if (!input.checked) {
      if (e.breathe) e.lastBreathe = e.breathe; // 재체크 시 마지막 설정 복원
      e.breathe = null;
      scheduleSave();
      rebuildState(stateName);
      setStatus(STR[lang].breatheOff(stateName));
      return;
    }
    input.disabled = true;
    try {
      e.breathe = e.lastBreathe || await defaultBreatheConfig(stateName);
      scheduleSave();
      rebuildState(stateName);
      setStatus(STR[lang].breatheOn(stateName));
    } catch (err) {
      setStatus(t("breatheFail") + err.message, "err");
      input.checked = false;
    }
    input.disabled = false;
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
    const srcIdx = ((st && st.frames.find((f) => f.present)) || { index: 0 }).index;
    pendingBreathe = true;
    openZoom(stateName, srcIdx);
  });
  wrap.appendChild(input);
  wrap.appendChild(lbl);
  return wrap;
}
