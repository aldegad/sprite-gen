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

// 서버 fit_breathe_pattern v2 미러 (수홍 정정 2026-07-18): 요청 횟수 그대로 —
// 등분 제약 없음. 사이클 길이가 안 나눠떨어지면 나머지를 앞 사이클 쉼에 배분.
// 유일한 보정 = 물리 클램프 (사이클당 최소 2K 프레임).
function breathePattern(cfg, seqLen) {
  const k = cfg.splits.length;
  if (!seqLen || seqLen <= 0) return [];
  const want = Math.max(1, cfg.breaths || 1);
  const minCycle = Math.max(2, 2 * k);
  const fit = Math.min(want, Math.floor(seqLen / minCycle));
  if (fit < 1) return new Array(seqLen).fill(0);
  const baseLen = Math.floor(seqLen / fit);
  const remainder = seqLen - baseLen * fit;
  const down = [];
  for (let ph = 1; ph <= k; ph++) down.push(ph);
  const up = [];
  for (let ph = k - 1; ph >= 1; ph--) up.push(ph);
  let pattern = [];
  for (let i = 0; i < fit; i++) {
    const length = baseLen + (i < remainder ? 1 : 0);
    const free = length - down.length - up.length;
    const deep = Math.floor(free / 2);
    const rest = free - deep;
    pattern = pattern.concat(new Array(rest).fill(0), down, new Array(deep).fill(k), up);
  }
  if (cfg.subpixel) {
    const out = [...pattern];
    const n = pattern.length;
    for (let i = 0; i < n; i++) {
      const prev = pattern[(i - 1 + n) % n];
      if (pattern[i] === prev) continue;
      let run = 1;
      while (run < n && pattern[(i + run) % n] === pattern[i]) run += 1;
      if (run >= 2) out[i] = (prev + pattern[i]) / 2; // 길이 보존: 런 첫 슬롯 치환
    }
    pattern = out;
  }
  return pattern;
}

// 실제 적용 호흡 횟수 — v2 에선 물리 클램프(사이클당 최소 2K 프레임) 경우에만 요청과 달라진다
function breatheFitCount(cfg, seqLen) {
  if (!seqLen || seqLen <= 0) return 0;
  const want = Math.max(1, cfg.breaths || 1);
  return Math.min(want, Math.floor(seqLen / Math.max(2, 2 * cfg.splits.length)));
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
    // 서브픽셀 = 도트 장인 규칙의 알고리즘화 (수홍 2026-07-18, 서버 phase_frame 미러):
    // 중간색은 프레임 팔레트에 있는 색으로만(램프 톤 스냅), 실루엣/투명 경계는
    // 통픽셀 유지(반투명 금지), 움직이는 이음새 밴드에만.
    const a = breatheComposite(base, cfg, lo);
    const b = breatheComposite(base, cfg, Math.min(lo + 1, cfg.splits.length));
    ctx.drawImage(a, 0, 0);
    const ad = a.getContext("2d").getImageData(0, 0, w, h);
    const bd2 = b.getContext("2d").getImageData(0, 0, w, h).data;
    const src = ad.data;
    let bTop = h, bBot = 0;
    const baseData = base.getContext("2d").getImageData(0, 0, w, h).data;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (baseData[(y * w + x) * 4 + 3] >= 40) {
          if (y < bTop) bTop = y;
          if (y + 1 > bBot) bBot = y + 1;
          break;
        }
      }
    }
    if (bBot <= bTop) return out;
    const palette = [];
    const seen = new Set();
    for (let i = 0; i < src.length; i += 4) {
      if (src[i + 3] >= 128) {
        const key = (src[i] << 16) | (src[i + 1] << 8) | src[i + 2];
        if (!seen.has(key)) { seen.add(key); palette.push([src[i], src[i + 1], src[i + 2]]); }
      }
    }
    if (!palette.length) return out;
    const amp = Math.max(1, cfg.amplitude || 1);
    const seams = [bTop, ...cfg.splits.map((s) => bTop + Math.floor((bBot - bTop) * s))];
    const outData = ctx.getImageData(0, 0, w, h);
    const od = outData.data;
    for (const sy of seams) {
      const r0 = Math.max(0, sy - 1);
      const r1 = Math.min(h, sy + amp + 1);
      for (let y = r0; y < r1; y++) {
        for (let x = 0; x < w; x++) {
          const k4 = (y * w + x) * 4;
          if (src[k4 + 3] < 128 || bd2[k4 + 3] < 128) continue; // 실루엣 통픽셀
          if (src[k4] === bd2[k4] && src[k4 + 1] === bd2[k4 + 1] && src[k4 + 2] === bd2[k4 + 2]) continue;
          const mr = (src[k4] + bd2[k4]) >> 1;
          const mg = (src[k4 + 1] + bd2[k4 + 1]) >> 1;
          const mb = (src[k4 + 2] + bd2[k4 + 2]) >> 1;
          let best = palette[0];
          let bestD = Infinity;
          for (const c of palette) {
            const d = (c[0] - mr) ** 2 + (c[1] - mg) ** 2 + (c[2] - mb) ** 2;
            if (d < bestD) { bestD = d; best = c; }
          }
          od[k4] = best[0]; od[k4 + 1] = best[1]; od[k4 + 2] = best[2]; od[k4 + 3] = 255;
        }
      }
    }
    ctx.putImageData(outData, 0, 0);
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

// 허리선 검출 (수홍 확정 2026-07-19 "벨트 있으면 딱 그 위, 허리에 걸리게"):
// 상체만 숨쉬고 하체(치마/다리)는 고정되는 이상적 분할선 = 벨트 바로 위.
// ① 벨트 액센트 밴드(콘텐츠 45~80% 구간의 고채도 warm 색 행) 최상단 → 실측 0.2px 일치
// ② 행간 색분포 급변점(히스토그램 L1, 같은 구간) → 실측 1~3px
// 실패 시 null — 호출자가 가슴 휴리스틱(silhouetteStats)으로 폴백.
function waistSplitFrom(data, W, H) {
  let top = H, bot = 0;
  const warm = new Array(H).fill(0);
  const hist = [];
  for (let y = 0; y < H; y++) {
    const c = new Map();
    let n = 0;
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      if (data[i + 3] < 40) continue;
      n++;
      const r = data[i], g = data[i + 1], b = data[i + 2];
      if (r > 200 && g > 140 && b < 110 && r > b + 100) warm[y]++;
      const k = ((r >> 5) << 6) | ((g >> 5) << 3) | (b >> 5);
      c.set(k, (c.get(k) || 0) + 1);
    }
    hist.push({ c, n });
    if (n) { if (y < top) top = y; bot = y + 1; }
  }
  const h = bot - top;
  if (h < 8) return null;
  const y0 = top + Math.floor(h * 0.45);
  const y1 = top + Math.floor(h * 0.8);
  const clamp = (y) => Math.min(0.75, Math.max(0.3, (y - top) / h));
  for (let y = y0; y < y1; y++) if (warm[y] >= 1) return clamp(y); // ① 벨트 위
  let best = 0.35, besty = null; // 급변점 최소 문턱 — 밋밋한 행에서 오검출 방지
  for (let y = y0; y < y1; y++) {
    const a = hist[y - 1], b2 = hist[y];
    if (!a.n || !b2.n) continue;
    let d = 0;
    const keys = new Set([...a.c.keys(), ...b2.c.keys()]);
    for (const k of keys) d += Math.abs((a.c.get(k) || 0) / a.n - (b2.c.get(k) || 0) / b2.n);
    if (d > best) { best = d; besty = y; }
  }
  return besty === null ? null : clamp(besty); // ② 급변점
}

// 첫 활성화 기본값 사슬 (수홍 확정 2026-07-19): ⓪ 같은 런에서 사람이 이미 튜닝한
// 다른 행의 split 상속(사람 판단 > 휴리스틱 — 바리에이션 행들이 허리선을 물려받는다)
// → ① / ② waistSplitFrom → ③ 가슴 휴리스틱.
async function defaultBreatheConfig(stateName) {
  const sibling = run.states
    .map((s) => s.name !== stateName && entries[s.name] && entries[s.name].breathe)
    .find((b) => b && Array.isArray(b.splits) && b.splits.length);
  if (sibling) {
    return { splits: [sibling.splits[0]], amplitude: sibling.amplitude || 1,
             breaths: sibling.breaths || 1, subpixel: false };
  }
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
  const data = cx.getImageData(0, 0, c.width, c.height).data;
  const waist = waistSplitFrom(data, c.width, c.height);
  const split = waist !== null ? waist : silhouetteStats(data, c.width, c.height).split;
  return { splits: [Math.round(split * 100) / 100], amplitude: 1, breaths: 1, subpixel: false };
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
    ? { splits: saved.splits.map(Number), amplitude: saved.amplitude || 1, breaths: 1, subpixel: false }
    : { splits: [0.55], amplitude: 1, breaths: 1, subpixel: false };
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
