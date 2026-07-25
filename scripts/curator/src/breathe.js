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
const BREATHE_MAX_ROW_STRAIN = 0.25;
// 사이드카 허용 범위 — 파이썬 curation.BREATHE_*_MAX 미러. UI 가 이 밖의 값을 만들면
// 굽기가 loud reject 하므로 컨트롤이 여기서 막아야 한다.
const BREATHE_DEPTH_MAX = 0.20;
const BREATHE_BREATHS_MAX = 8;
const BREATHE_LAG_MAX = 0.45;

// 굽기가 거부하는 프레임은 미리보기도 만들어 주지 않는다.
// 파이썬은 셀 밖으로 나간 불투명 픽셀을 세어 SystemExit 으로 멈추고 행당 변형 상한도
// 강제한다. 미러가 그걸 안 지키면 (a) 프리뷰는 멀쩡한데 굽기가 죽고 (b) row-export 의
// WebM/MP4 는 서버를 안 거치므로 **잘린 영상이 그대로 사용자 손에 들어간다**
// (슉슉이 실측 2026-07-25: 여백 0 셀에서 불투명 73px 소실, 오류 0건).
class BreatheRefused extends Error {}

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

// 파이썬 `breathe._fnv1a` / `anatomy_fingerprint` 미러. 미러가 지문을 **직접 계산**해야
// 자기가 그리는 프레임이 얼린 해부와 맞는지 확인할 수 있다. 못 하면 굽기만 자가 복구하고
// 프리뷰는 낡은 숫자로 계속 그린다 (슉슉이 실측 2026-07-25: 픽셀 편집 후 최대 617바이트,
// 불투명 픽셀 수까지 불일치). SHA-256 을 안 쓰는 이유는 브라우저에서 동기로 못 구해서다.
function breatheFnv1a(data) {
  let h = 2166136261;
  for (let i = 0; i < data.length; i++) h = Math.imul(h ^ data[i], 16777619) >>> 0;
  return h;
}

function breatheFingerprint(canvas, data, box) {
  const hex = breatheFnv1a(data).toString(16).padStart(8, "0");
  const b = box || [0, 0, 0, 0];
  return `${canvas.width}x${canvas.height}:${b[0]},${b[1]},${b[2]},${b[3]}:${hex}`;
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

// 줄 단위 신선도 검사 — **기준 프레임 하나**에만 건다.
//
// 굽기는 줄의 첫 프레임으로 해부를 확정하고(`bake_breathe_sequence` 의 `images[0]`) 그
// 한 벌로 모든 프레임을 굽는다. 그래서 프레임마다 지문을 보면 안 된다 — 깜빡임처럼
// 정상적으로 다른 프레임까지 거부해 프리뷰가 통째로 죽는다. 봐야 하는 건 "얼린 해부가
// **지금의 기준 프레임**에서 나온 것인가" 하나다.
//
// 이게 없으면 큐레이터 픽셀 편집기로 도트를 찍기만 해도(호흡을 건드릴 필요조차 없다)
// 굽기는 자가 복구하고 프리뷰는 낡은 숫자로 계속 그린다 — 실측 최대 617바이트, 불투명
// 픽셀 수까지 불일치 (슉슉이 2026-07-25).
function breatheAssertFresh(referenceCanvas, cfg) {
  const anat = cfg && cfg.anatomy;
  if (!anat || !anat.fingerprint) return;          // 해부가 없으면 굽기가 매번 재검출한다
  const w = referenceCanvas.width, h = referenceCanvas.height;
  const data = referenceCanvas.getContext("2d").getImageData(0, 0, w, h).data;
  const now = breatheFingerprint(referenceCanvas, data, breatheSolidBox(data, w, h));
  if (now !== anat.fingerprint) {
    throw new BreatheRefused(
      `해부가 지금의 기준 프레임에서 나온 게 아니다 — 얼린 지문 ${anat.fingerprint} vs `
      + `현재 ${now}. 굽기는 다시 재서 굽는다. 해부를 갱신해야 프리뷰가 같아진다.`);
  }
}

// 프리뷰 전용 래퍼 — 굽기가 거부하는 설정이면 **원본을 그리고 loud 하게 알린다.**
// 내보내기(row-export)는 이 래퍼를 쓰지 않는다: 거기서는 예외가 그대로 올라가 파일이
// 만들어지기 전에 중단돼야 한다. 프리뷰는 타이머 루프라 예외가 올라가면 재생이 죽으므로
// 잡되, **조용히 워프된 그림을 보여주지는 않는다** — 못 굽는 설정이면 못 굽는 대로 보인다.
let _breatheWarned = "";
function breatheComposeForPreview(base, cfg, phase, reference) {
  try {
    if (reference) breatheAssertFresh(reference, cfg);
    return breatheComposite(base, cfg, phase);
  } catch (err) {
    if (!(err instanceof BreatheRefused)) throw err;
    if (_breatheWarned !== err.message) {
      _breatheWarned = err.message;
      setStatus(`호흡 미리보기 중단 — 이 설정은 굽기에서도 거부된다: ${err.message}`, "err");
    }
    return base;
  }
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
  // `rigid_row` 는 사람의 의도(입력)이고 `anatomy` 는 거기서 파생된 캐시다. 굽기는 둘이
  // 어긋나면 재검출해 의도를 따르는데(`resolve_anatomy` 의 stale_override), 미러는 검출을
  // 못 하므로 **낡은 캐시로 그리면 거짓말이 된다.** 그래서 거부한다 — 프리뷰는 원본을
  // 보여주고 사용자는 해부를 갱신하라는 말을 듣는다 (슉슉이 실측 2026-07-25: override 31
  // 을 굽기는 따르고 미러는 23 으로 그려 12위상 전부, 최대 164바이트 갈렸다).
  if (anat && cfg.rigid_row != null && Number(cfg.rigid_row) !== anat.rigid_row) {
    throw new BreatheRefused(
      `강체 경계가 어긋난다 — 사이드카 rigid_row ${cfg.rigid_row} vs 해부 ${anat.rigid_row}. `
      + `굽기는 ${cfg.rigid_row} 로 다시 재서 굽는다. 해부를 갱신해야 프리뷰가 같아진다.`);
  }
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
  let peak = 0;
  for (let j = 0; j < anat.height; j++) peak = Math.max(peak, env(j / Math.max(1, anat.height - 1)));
  const strain = depth * norm * peak;
  if (strain > BREATHE_MAX_ROW_STRAIN) {
    throw new BreatheRefused(
      `행당 변형 ${strain.toFixed(3)} > 상한 ${BREATHE_MAX_ROW_STRAIN} — 변형 구간이 너무 좁다 `
      + `(강체 경계 ${anat.rigid_row}/${anat.height}). depth 를 낮추거나 경계를 올려라.`);
  }
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
  let clipped = 0;
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
      for (const [ox, si] of rowMap) {
        const s4 = ((by0 + j) * w + (bx0 + si)) * 4;
        if (!src[s4 + 3]) continue;
        if (yy < 0 || yy >= h || ox < 0 || ox >= w) { clipped += 1; continue; }
        const d4 = (yy * w + ox) * 4;
        od[d4] = src[s4];
        od[d4 + 1] = src[s4 + 1];
        od[d4 + 2] = src[s4 + 2];
        od[d4 + 3] = src[s4 + 3];
      }
    }
    yCursor += reps;
  }
  if (clipped) {
    throw new BreatheRefused(
      `늘어난 프레임이 셀 밖으로 나가 불투명 픽셀 ${clipped}개가 잘린다 `
      + `(셀 ${w}x${h}). 셀 여백을 늘리거나 depth 를 낮춰라.`);
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
