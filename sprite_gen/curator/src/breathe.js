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
function breatheFnv1a(text) {
  // 파이썬은 `key.encode("utf-8")` 을 해시한다 — 여기도 **UTF-8 바이트**여야 한다.
  // `charCodeAt` 로 하면 상태 이름에 비-ASCII 가 한 글자만 들어와도 갈린다.
  const data = new TextEncoder().encode(text);
  let h = 2166136261;
  for (let i = 0; i < data.length; i++) h = Math.imul(h ^ data[i], 16777619) >>> 0;
  return h;
}

// 파이썬 `breathe._num` 미러. `toFixed(6)` 은 파이썬 `f"{v:.6f}"` 와 경계에서 갈리고
// (JS 는 이진값, 파이썬은 십진 표현 기준 반올림) 지수 표기도 다르다. 곱·합·floor 만 쓰면
// 양쪽 다 IEEE754 double 이라 같은 입력에 반드시 같은 결과가 나온다.
function breatheNum(v) {
  if (v === null || v === undefined) return "~";
  if (typeof v === "boolean") return v ? "1" : "0";
  if (typeof v === "number") return String(Math.floor(v * 1000000.0 + 0.5));
  return String(v);
}

function breatheCanon(v) {
  if (Array.isArray(v)) return "[" + v.map(breatheCanon).join(",") + "]";
  if (v && typeof v === "object") {
    // 파이썬은 `sorted(key=str)`, JS 는 UTF-16 코드유닛 정렬 — 실제 키(`"x,y"`, 변형
    // 필드명)는 전부 ASCII 라 같은 순서다. 비-ASCII 키가 생기면 이 가정이 깨진다.
    return "{" + Object.keys(v).sort().map((k) => `${k}=${breatheCanon(v[k])}`).join(",") + "}";
  }
  return breatheNum(v);
}

// 파이썬 `breathe.reference_key` 미러 — **픽셀을 안 읽는다.**
// 굽기는 BICUBIC(`apply_transform`), 웹뷰 캔버스는 NEAREST 라 같은 원본·같은 변형에도
// 두 쪽이 만드는 그림이 다르다. 결과 픽셀을 해시하던 옛 지문이 회전·확대가 걸린 줄에서
// 영구 불일치였던 이유다 (슉슉이 실측 2026-07-26).
// 파이썬 `curation.normalize_transform` 미러 — 항상 7키 전체.
function breatheNormalizeTransform(raw) {
  const t = raw && typeof raw === "object" ? raw : {};
  const num = (v, d) => (v === undefined || v === null ? d : Number(v));
  return { rotate: num(t.rotate, 0), scale: num(t.scale, 1), dx: num(t.dx, 0),
           dy: num(t.dy, 0), shx: num(t.shx, 0), shy: num(t.shy, 0), flipX: t.flipX ? 1 : 0 };
}

function breatheReferenceKeyOf(p) {
  return ["breathe-ref-v1", String(p.state), String(p.variant), String(p.requestStamp),
          String(p.sourceIndex), String(p.sourceStamp),
          breatheCanon(p.pixelOps || {}),
          breatheCanon(breatheNormalizeTransform(p.transform))].join("|");
}

function breatheFingerprint(key) {
  const parts = key.split("|");
  const variant = parts.length > 5 ? parts[2] : "?";
  const index = parts.length > 5 ? parts[4] : "?";
  return `${variant}:${index}:${breatheFnv1a(key).toString(16).padStart(8, "0")}`;
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
function breatheAssertFresh(referenceKey, cfg) {
  const anat = cfg && cfg.anatomy;
  if (!anat) return;                               // 해부가 아예 없으면 굽기가 매번 재검출한다
  if (!anat.fingerprint) {
    // 해부는 있는데 지문이 없다 = **확인할 수 없다.** 조용히 통과시키면 낡은 숫자로
    // 그리게 된다 — `refreshAnatomy` 가 실패해 지문만 무효화한 상태가 정확히 이 모양이고,
    // 그때 미러가 통과해 굽기와 164바이트 갈렸다 (슉슉이 실측 2026-07-26).
    throw new BreatheRefused(
      "해부에 지문이 없다 — 이 프레임에서 나온 값인지 확인할 수 없다. 해부를 갱신해라.");
  }
  if (!referenceKey) {
    // 키를 못 만든다 = 기준 프레임의 정체를 모른다 (스탬프 없는 프레임, 빈 재생목록).
    throw new BreatheRefused("기준 프레임 키를 만들 수 없다 — 신선도 확인 불가.");
  }
  const now = breatheFingerprint(referenceKey);
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
function breatheComposeForPreview(base, cfg, phase, referenceKey) {
  // `reference` 는 **필수 인자**다. 선택으로 두면 호출부가 빠뜨렸을 때 신선도 검사가
  // 조용히 건너뛰어져, 같은 웹뷰의 두 화면이 정반대로 행동한다 — 줄 카드는 거부하는데
  // 호흡 편집 모달은 낡은 숫자로 그렸다 (슉슉이 실측 2026-07-26: 12/12 위상 갈림, 알림 0건).
  //
  // 인자를 **안 넘긴 것**(프로그래머 실수)과 넘겼는데 **빈 값**(상태가 아직 안 실림)은
  // 다르다: 전자는 하드 에러, 후자는 "확인할 수 없으니 워프하지 않는다" 로 부드럽게 거부.
  if (arguments.length < 4) {
    throw new Error("breatheComposeForPreview: referenceKey 인자가 필요하다 (신선도 검사)");
  }
  try {
    breatheAssertFresh(referenceKey, cfg);
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
  // axis_x / torso_half 도 rigid_row 와 같은 지위의 사람 의도 입력이다 (영역 UI 2026-07-30).
  // 캐시가 의도와 어긋나면 낡은 숫자로 그리지 않는다 — 굽기는 의도로 다시 재서 굽는다.
  if (anat && cfg.axis_x != null && Number(cfg.axis_x) !== anat.axis_x) {
    throw new BreatheRefused(
      `몸통 축이 어긋난다 — 사이드카 axis_x ${cfg.axis_x} vs 해부 ${anat.axis_x}. `
      + `해부를 갱신해야 프리뷰가 같아진다.`);
  }
  if (anat && cfg.torso_half != null && Number(cfg.torso_half) !== anat.torso_half) {
    throw new BreatheRefused(
      `몸통 반폭이 어긋난다 — 사이드카 torso_half ${cfg.torso_half} vs 해부 ${anat.torso_half}. `
      + `해부를 갱신해야 프리뷰가 같아진다.`);
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
  let deformed = false;   // 이 위상에서 변형이 실제로 일어났나 (다듬기 게이트, 파이썬 미러)
  for (let j = 0; j < height; j++) {
    const u = 1 - j / Math.max(1, height - 1);
    const cur = Math.round(heights[j]);
    const reps = Math.max(0, cur - prev);
    prev = cur;
    if (reps === 0) continue;
    const g = gain(u);
    if (reps !== 1) deformed = true;         // 행 복제/삭제 = 세로 변형이 실재
    let rowMap;
    if (g === 0) {
      // 변형 없음 = 원본 위치 그대로 (축 고정점 사상의 g->0 극한과 동일)
      rowMap = Array.from({ length: width }, (_, i) => [bx0 + i, i]);
    } else {
      deformed = true;                       // 가로 사상 변형이 실재
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
      // 외곽선 보존 (파이썬 _warp 미러) — 가로 축소 위상에서 실루엣 양끝 열이 떨어져
      // 1px 외곽선이 사라지는 것을 막는다. 이 행의 최말단 불투명 소스 열(= 외곽선)을
      // 출력 양끝 불투명 픽셀에 그대로 실어 항상 1px 외곽선을 남긴다.
      const rowOpaque = (si) => src[((by0 + j) * w + (bx0 + si)) * 4 + 3];
      let opLo = -1, opHi = -1;
      for (let k = 0; k < width; k++) if (rowOpaque(k)) { if (opLo < 0) opLo = k; opHi = k; }
      if (opHi >= 0 && rowMap.length) {
        for (let k = rowMap.length - 1; k >= 0; k--) {
          if (rowOpaque(rowMap[k][1])) { rowMap[k] = [rowMap[k][0], opHi]; break; }
        }
        for (let k = 0; k < rowMap.length; k++) {
          if (rowOpaque(rowMap[k][1])) { rowMap[k] = [rowMap[k][0], opLo]; break; }
        }
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
  if (deformed) breatheThinOutline(od, w, h);
  ctx.putImageData(outImg, 0, 0);
  return out;
}

// 워프된 실루엣 외곽선 2px 를 **안쪽점 기준** 1px 로 정규화 — 파이썬 `_thin_outline_1px`
// 의 바이트 동일 미러 (고정점 반복: 한 패스의 제거가 새 돌출점을 만들 수 있다).
// 패턴1: 끝 '어두움-어두움-내부색' → 바깥 픽셀 제거(알파 0). 패턴2: 어두운 끝점이
// 위아래 행 끝보다 정확히 1px 바깥으로 튄 단독 돌출점 → 제거하고 안쪽 자리에 그 색을
// 그린다(가로 한정 — 세로 1px 계단은 작가 의도와 구분 불가). 안전 필터: 제거로 내부색
// 노출·어두운 선 중간 구멍이 생기는 후보는 제외, 고정점까지 재평가.
function breatheThinOutline(od, w, h) {
  while (breatheThinOutlinePass(od, w, h) > 0) { /* 불투명 픽셀 단조 감소 — 종료 보장 */ }
}

function breatheThinOutlinePass(od, w, h) {
  const snap = Uint8ClampedArray.from(od);
  const op = (x, y) => x >= 0 && x < w && y >= 0 && y < h && snap[(y * w + x) * 4 + 3] !== 0;
  const dk = (x, y) => {
    if (!op(x, y)) return false;
    const i = (y * w + x) * 4;
    return (0.299 * snap[i] + 0.587 * snap[i + 1] + 0.114 * snap[i + 2]) < 60;
  };
  // 후보: "x,y" -> 'v'|'h' (같은 픽셀이 양축 후보면 먼저 등록된 세로가 이긴다 — 파이썬 setdefault)
  const cand = new Map();
  const put = (x, y, ax) => { const k = x + "," + y; if (!cand.has(k)) cand.set(k, ax); };
  for (let x = 0; x < w; x++) {
    let t0 = -1, b0 = -1;
    for (let y = 0; y < h; y++) if (op(x, y)) { if (t0 < 0) t0 = y; b0 = y; }
    if (t0 < 0) continue;
    if (t0 + 2 <= b0 && dk(x, t0) && dk(x, t0 + 1) && op(x, t0 + 2) && !dk(x, t0 + 2)) put(x, t0, "v");
    if (b0 - 2 >= t0 && dk(x, b0) && dk(x, b0 - 1) && op(x, b0 - 2) && !dk(x, b0 - 2)) put(x, b0, "v");
  }
  const rowLo = new Map();
  const rowHi = new Map();
  for (let y = 0; y < h; y++) {
    let l0 = -1, r0 = -1;
    for (let x = 0; x < w; x++) if (op(x, y)) { if (l0 < 0) l0 = x; r0 = x; }
    if (l0 < 0) continue;
    rowLo.set(y, l0); rowHi.set(y, r0);
    if (l0 + 2 <= r0 && dk(l0, y) && dk(l0 + 1, y) && op(l0 + 2, y) && !dk(l0 + 2, y)) put(l0, y, "h");
    if (r0 - 2 >= l0 && dk(r0, y) && dk(r0 - 1, y) && op(r0 - 2, y) && !dk(r0 - 2, y)) put(r0, y, "h");
  }
  const moves = new Map();   // "x,y"(안쪽) -> 스냅샷 색 인덱스
  for (const y of [...rowLo.keys()].sort((a, b) => a - b)) {
    const m = rowLo.get(y);
    const upLo = rowLo.get(y - 1), dnLo = rowLo.get(y + 1);
    if (upLo !== undefined && dnLo !== undefined && Math.min(upLo, dnLo) - m === 1
        && dk(m, y) && op(m + 1, y)) {
      put(m, y, "h");
      if (!dk(m + 1, y)) moves.set((m + 1) + "," + y, (y * w + m) * 4);
    }
    const r = rowHi.get(y);
    const upHi = rowHi.get(y - 1), dnHi = rowHi.get(y + 1);
    if (upHi !== undefined && dnHi !== undefined && r - Math.max(upHi, dnHi) === 1
        && dk(r, y) && op(r - 1, y)) {
      put(r, y, "h");
      if (!dk(r - 1, y)) moves.set((r - 1) + "," + y, (y * w + r) * 4);
    }
  }
  // 안전 필터 고정점 (파이썬과 동일한 정렬 순회·재시작)
  const drop = new Set(cand.keys());
  const keyCmp = (a, b) => {
    const [ax, ay] = a.split(",").map(Number);
    const [bx, by] = b.split(",").map(Number);
    return ax !== bx ? ax - bx : ay - by;
  };
  let changed = true;
  while (changed) {
    changed = false;
    for (const k of [...drop].sort(keyCmp)) {
      const [x, y] = k.split(",").map(Number);
      const neigh = cand.get(k) === "h" ? [[x, y - 1], [x, y + 1]] : [[x - 1, y], [x + 1, y]];
      let keptDark = 0;
      let ok = true;
      for (const [nx, ny] of neigh) {
        if (!op(nx, ny) || drop.has(nx + "," + ny)) continue;
        if (dk(nx, ny)) keptDark += 1;
        else { ok = false; break; }
      }
      if (!ok || keptDark >= 2) { drop.delete(k); changed = true; break; }
    }
  }
  for (const k of drop) {
    const [x, y] = k.split(",").map(Number);
    const d = (y * w + x) * 4;
    od[d] = 0; od[d + 1] = 0; od[d + 2] = 0; od[d + 3] = 0;
  }
  let applied = drop.size;
  for (const [k, s] of moves) {
    if (drop.has(k)) continue;
    const [x, y] = k.split(",").map(Number);
    const d = (y * w + x) * 4;
    od[d] = snap[s]; od[d + 1] = snap[s + 1]; od[d + 2] = snap[s + 2]; od[d + 3] = snap[s + 3];
    applied += 1;
  }
  return applied;
}

// 첫 활성화 기본값: 서버에 해부를 물어본다 (검출 SSoT = 서버).
// 예전엔 여기서 JS 가 어깨/허리선을 직접 추정했지만, 그 추정이 굽기 쪽 검출과 달라
// 미리보기와 결과가 갈라졌다. 이제 숫자는 한 곳에서만 나온다.
async function fetchBreatheAnatomy(stateName, rigidRow, axisX, torsoHalf) {
  const q = new URLSearchParams({ state: stateName });
  if (rigidRow != null) q.set("rigid_row", String(rigidRow));
  if (axisX != null) q.set("axis_x", String(axisX));
  if (torsoHalf != null) q.set("torso_half", String(torsoHalf));
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

// 사이드카에서 breathe 가 켜진 채 로드됐지만 anatomy 캐시가 비어 있으면(뷰 없이
// 에이전트/CLI 가 breathe 만 쓴 런, 레거시 이전 직후) 미리보기 미러는 해부를 로컬로 못
// 재 조용히 정지 그림을 그린다 (breatheComposite: !anat → base 원본). 굽기는 서버가
// 매번 재서 숨쉬는데 미리보기만 죽어 "체크됐는데 안 쉰다" 가 된다. 로드 시 서버에 한 번
// 물어 캐시를 채운다 — 토글 ON(defaultBreatheConfig) 과 같은 검출 경로다(SSoT = 서버).
async function ensureBreatheAnatomy(stateName) {
  const e = entries[stateName];
  const cfg = e && e.breathe;
  if (!cfg || cfg.anatomy) return false;
  const { anatomy } = await fetchBreatheAnatomy(stateName, cfg.rigid_row, cfg.axis_x, cfg.torso_half);
  // 재진입/경쟁 사이 다른 경로가 이미 채웠으면 덮지 않는다.
  if (e.breathe && !e.breathe.anatomy) {
    e.breathe.anatomy = anatomy;
    return true;
  }
  return false;
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
  wrap.className = "unfake-apply row-toggle breathe-toggle";
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
