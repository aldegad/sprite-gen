// SPDX-License-Identifier: Apache-2.0
// curator/breathe.js — 결정론 호흡 후처리 레이어 (사이드카) — 토글·위상·봉투 워프 미러
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// 호흡은 프레임 선택(깜빡임)과 직교하는 변조 레이어다 (수홍 확정 2026-07-18).
// truth = entries[state].breathe = {depth, breaths, lag, rigid_row, anatomy} | null.
//
// **검출은 여기서 하지 않는다** (수홍 결정 2026-07-25 b안). 목 병목·대칭 눈쌍·부속
// 판정은 서버(`sprite_gen/anatomy.py`)가 GET /api/breathe-anatomy 로 한 번 돌려
// 사이드카에 숫자로 얼려두고, 이 파일은 그 숫자로 **워프만** 미러링한다. 검출까지
// JS 가 재구현하면 굽기와 미리보기의 진실이 둘로 갈라진다.
//
// 미러 대상은 sprite_gen/breathe.py 의 fit_breathe_pattern / phase_frame 이다.
// 같은 봉투·같은 정수 연산이라 프리뷰와 굽기가 픽셀 동일해야 한다.

// 서버 상수 미러 (sprite_gen/breathe.py)
const BREATHE_TAPER = 0.055;
const BREATHE_FOOT = 0.28;

let pendingBreathe = false; // 호흡 라벨 → 줌 모달 오픈 시 호흡 모드 진입 플래그

function stateBreathe(stateName) {
  const e = entries[stateName];
  return e && e.breathe ? e.breathe : null;
}

// 서버 fit_breathe_pattern 미러 — 위상은 [0,1) 연속값이다 (구 정수 분할선 단계 아님).
// breaths 회가 시퀀스 안에서 정확히 반복되므로 루프 이음매도 등분 보정도 없다.
function breathePattern(cfg, seqLen) {
  if (!seqLen || seqLen <= 0) return [];
  const breaths = Math.max(1, cfg.breaths || 1);
  // 분자를 정수 나머지로 **먼저** 접는다 — 파이썬 fit_breathe_pattern 과 같은 식이어야
  // 같은 double 이 나온다. `(i*breaths/seqLen) % 1` 로 쓰면 수학적으로 같은 위상이 서로
  // 다른 double 이 되어 프리뷰가 굽기와 갈린다 (부리 실측 2026-07-25: seq=30 breaths=7
  // 에서 slot 24 py=0.6 vs js=0.5999999999999996 → 4바이트 차이).
  return Array.from({ length: seqLen }, (_, i) => ((i * breaths) % seqLen) / seqLen);
}

// 연속 위상이라 요청 횟수가 그대로 성립한다 (물리 클램프 없음).
function breatheFitCount(cfg, seqLen) {
  if (!seqLen || seqLen <= 0) return 0;
  return Math.max(1, cfg.breaths || 1);
}

function breatheWave(t) {
  return 0.86 * Math.sin(2 * Math.PI * t) + 0.14 * Math.sin(4 * Math.PI * t);
}

function breatheSmoothstep(a, b, x) {
  if (b <= a) return x >= b ? 1 : 0;
  const u = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return u * u * (3 - 2 * u);
}

// 변형 강도 봉투 + 진폭 정규화 계수 (서버 envelope() 미러).
function breatheEnvelope(anat) {
  const height = anat.height;
  const ru = 1 - anat.rigid_row / Math.max(1, height - 1);
  const band = Math.max(1.5, BREATHE_TAPER * height) / Math.max(1, height);
  const footTop = BREATHE_FOOT * ru;
  const env = (u) =>
    breatheSmoothstep(0, footTop, u) * (1 - breatheSmoothstep(ru - band, ru + band, u));
  let total = 0;
  for (let j = 0; j < height; j++) total += env(j / Math.max(1, height - 1));
  const basisRows = Math.max(1, height - anat.basis_row);
  return { env, ru, norm: total > 1e-6 ? basisRows / total : 0 };
}

// 부속 보호 가중 — 1 이면 그 열은 가로로 안 늘어난다 (밀리기만 한다).
function breatheProtect(anat) {
  const hasAppendage = anat.max_half >= 1.3 * anat.torso_half;
  if (!hasAppendage) return () => 0;
  const t0 = anat.torso_half * 1.15;
  const t1 = Math.max(t0 + 1, anat.max_half * 0.95);
  return (x) => breatheSmoothstep(t0, t1, Math.abs(x - anat.axis_x));
}

function breatheSolidBox(data, w, h) {
  let x0 = w, y0 = h, x1 = 0, y1 = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (data[(y * w + x) * 4 + 3] >= 128) {
        if (x < x0) x0 = x;
        if (y < y0) y0 = y;
        if (x + 1 > x1) x1 = x + 1;
        if (y + 1 > y1) y1 = y + 1;
      }
    }
  }
  return x1 > x0 && y1 > y0 ? [x0, y0, x1, y1] : null;
}

// 서버 phase_frame 미러 — 캔버스 크기 불변, 발바닥 고정.
// 세로는 행 국소 배율 누적, 가로는 행 안 밀도 적분(단조 → 접힘 없음). 전부 정수 연산.
function breatheComposite(base, cfg, phase) {
  const w = base.width;
  const h = base.height;
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const ctx = out.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  const anat = cfg && cfg.anatomy;
  if (!anat) {
    // 해부 숫자가 아직 없다 — 서버가 채우기 전까지는 원본을 그대로 보여준다.
    // 여기서 대충 추정해 그리면 굽기와 다른 그림을 보여주게 된다 (조용한 폴백 금지).
    ctx.drawImage(base, 0, 0);
    return out;
  }
  const srcData = base.getContext("2d").getImageData(0, 0, w, h);
  const src = srcData.data;
  const box = breatheSolidBox(src, w, h);
  if (!box) {
    ctx.drawImage(base, 0, 0);
    return out;
  }
  const [bx0, by0, bx1, by1] = box;
  const width = bx1 - bx0;
  const height = by1 - by0;
  const anchorX = bx0 + anat.axis_x;
  const baseline = by1;
  const { env, ru, norm } = breatheEnvelope(anat);
  const pOf = breatheProtect(anat);
  const depth = cfg.depth || 0.06;
  const lag = cfg.lag == null ? 0.1 : cfg.lag;
  const gain = (u) => {
    const e = env(u);
    if (e <= 0) return 0;
    return depth * norm * breatheWave(phase - lag * Math.min(1, u / Math.max(1e-6, ru))) * e;
  };

  const heights = [];
  let acc = 0;
  for (let j = 0; j < height; j++) {
    const g = gain(1 - j / Math.max(1, height - 1));
    acc += g === 0 ? 1 : 1 / (1 + g);
    heights.push(acc);
  }
  const total = Math.max(1, Math.round(acc));

  const outImg = ctx.createImageData(w, h);
  const od = outImg.data;
  let yCursor = baseline - total;
  let prev = 0;
  for (let j = 0; j < height; j++) {
    const u = 1 - j / Math.max(1, height - 1);
    const cur = Math.round(heights[j]);
    const reps = Math.max(0, cur - prev);
    prev = cur;
    if (reps === 0) continue;
    const g = gain(u);
    let rowMap;
    if (g === 0) {
      // 변형 없음 = 원본 위치 그대로 (축 고정점 사상의 g->0 극한과 동일)
      rowMap = Array.from({ length: width }, (_, i) => [bx0 + i, i]);
    } else {
      const edge = [0];
      for (let i = 0; i < width; i++) edge.push(edge[i] + Math.max(0.05, 1 + g * (1 - pOf(i))));
      const origin = edge[anat.axis_x];    // 축이 고정점 — 여기가 anchorX 에 박힌다
      const lo = Math.round(edge[0] - origin);
      const hi = Math.round(edge[width] - origin);
      rowMap = [];
      let i = 0;
      for (let ox = lo; ox < hi; ox++) {
        while (i < width - 1 && edge[i + 1] - origin <= ox) i += 1;
        rowMap.push([anchorX + ox, i]);
      }
    }
    for (let r = 0; r < reps; r++) {
      const yy = yCursor + r;
      if (yy < 0 || yy >= h) continue;
      for (const [ox, si] of rowMap) {
        if (ox < 0 || ox >= w) continue;
        const s4 = ((by0 + j) * w + (bx0 + si)) * 4;
        if (!src[s4 + 3]) continue;
        const d4 = (yy * w + ox) * 4;
        od[d4] = src[s4];
        od[d4 + 1] = src[s4 + 1];
        od[d4 + 2] = src[s4 + 2];
        od[d4 + 3] = src[s4 + 3];
      }
    }
    yCursor += reps;
  }
  ctx.putImageData(outImg, 0, 0);
  return out;
}

// 첫 활성화 기본값: 서버에 해부를 물어본다 (검출 SSoT = 서버).
// 예전엔 여기서 JS 가 어깨/허리선을 직접 추정했지만, 그 추정이 굽기 쪽 검출과 달라
// 미리보기와 결과가 갈라졌다. 이제 숫자는 한 곳에서만 나온다.
async function fetchBreatheAnatomy(stateName, rigidRow) {
  const q = new URLSearchParams({ state: stateName });
  if (rigidRow != null) q.set("rigid_row", String(rigidRow));
  const res = await fetch(`/api/breathe-anatomy?${q}`);
  const body = await res.json();
  if (!res.ok || body.error) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

async function defaultBreatheConfig(stateName) {
  // 같은 런에서 사람이 이미 튜닝한 세기를 물려받는다 (사람 판단 > 기본값).
  const sibling = run.states
    .map((s) => (s.name !== stateName && entries[s.name] ? entries[s.name].breathe : null))
    .find((b) => b && typeof b.depth === "number");
  const { anatomy, defaults } = await fetchBreatheAnatomy(stateName);
  return {
    depth: sibling ? sibling.depth : defaults.depth,
    breaths: sibling ? sibling.breaths : defaults.breaths,
    lag: sibling ? sibling.lag : defaults.lag,
    rigid_row: null,
    anatomy,
  };
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
  // 구 테이크의 분할선 파라미터는 봉투 경계로 옮길 수 없다 (반대 개념 — 서버
  // migrate_breathe.py 와 같은 판단). 시퀀스에서 걷어내는 일만 하고 설정은 기본값으로
  // 두며, 해부 숫자는 다음 토글/저장에서 서버가 채운다.
  e.breathe = { depth: 0.06, breaths: 1, lag: 0.1, rigid_row: null, anatomy: null };
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
