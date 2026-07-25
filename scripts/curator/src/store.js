// 폐기된 호흡 사이드카 키 (파이썬 curation.RETIRED_BREATHE_KEYS 미러 — 둘이 갈리면
// 한쪽만 보존하게 되므로 같이 고친다).
const RETIRED_BREATHE_KEYS = ["splits", "amplitude", "subpixel", "hold"];

// 파이썬 curation 의 허용 범위 미러. 굽기가 요란하게 거부하는 값을 웹뷰가 조용히 깎아
// 되쓰면 사용자의 원래 숫자가 파괴되고 게이트 자체가 사라진다 — 같은 값으로 판정한다.
// 파이썬 `float(v)` 가 받는 모양인가 — 수, 또는 수로만 이루어진 문자열.
// `""`·`[]`·`{}`·`true` 는 JS `Number()` 가 조용히 0/1 로 바꾸지만 파이썬은 거부한다.
function isNumericScalar(v) {
  if (typeof v === "number") return Number.isFinite(v);
  return typeof v === "string" && /^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$/.test(v);
}

function breatheRangeProblems(raw) {
  const bounds = [["depth", 0.005, 0.20, 0.06], ["breaths", 1, 8, 1], ["lag", 0, 0.45, 0.1]];
  const bad = [];
  for (const [key, lo, hi, dflt] of bounds) {
    // 생략(undefined)은 기본값. 명시적 null 은 굽기가 `float(None)` 으로 거부하므로
    // **키와 무관하게** 여기서도 문제로 본다 — `Number(null) = 0` 이라 하한이 0 인 `lag`
    // 만 범위 검사를 통과해 빠져나갔다 (슉슉이 실측 2026-07-26: 원본이 0.1 로 덮였다).
    const given = raw[key];
    if (given === null) { bad.push(`${key}=null`); continue; }
    // `Number("")`·`Number([])` 는 **0** 이다 — 값이 있는지만 보고 `Number()` 로 넘기면
    // 하한이 0 인 `lag` 가 통과해 첫 autosave 가 사용자 값을 0 으로 덮는다(알림 0건).
    // 파이썬은 `float("")`/`float([])` 이 터져 거부하는 값들이라 여기서도 거부한다.
    if (given !== undefined && !isNumericScalar(given)) {
      bad.push(`${key}=${JSON.stringify(given)} (수가 아님)`);
      continue;
    }
    const v = given === undefined ? dflt : Number(given);
    if (!Number.isFinite(v) || v < lo || v > hi) { bad.push(`${key}=${JSON.stringify(raw[key])}`); continue; }
    // 정수여야 하는 값은 **정수인지도** 본다. `Math.round` 로 조용히 반올림하면 굽기
    // (`_exact_int` 가 거부)와 갈리고, 첫 autosave 가 사이드카를 반올림값으로 덮어써
    // loud reject 자체가 사라진다 (슉슉이 실측 2026-07-25: breaths 2.7 → 굽기 2 vs 프리뷰 3).
    if (key === "breaths" && !Number.isInteger(v)) bad.push(`${key}=${JSON.stringify(raw[key])} (정수 아님)`);
  }
  if (raw.rigid_row != null) {
    // `Number("")` 과 `Number([])` 는 **0** 이라 정수 검사만 하면 통과한다 — 그러면 첫
    // autosave 가 사용자 사이드카를 0 으로 덮고 굽기의 loud reject 근거가 사라진다
    // (슉슉이 실측 2026-07-26: 알림 0건). 파이썬은 `float("")` 이 터져 거부하는 값들이다.
    const raw_r = raw.rigid_row;
    const numeric = isNumericScalar(raw_r);
    const r = Number(raw_r);
    if (!numeric || !Number.isInteger(r)) {
      bad.push(`rigid_row=${JSON.stringify(raw_r)} (정수 아님)`);
    } else if (r < 1) {
      // `analyze` 가 `0 < rigid_row < height` 를 loud fail 한다. 상한은 해부 없이 모르므로
      // 여기서 아는 하한만 본다 — 모르는 것을 아는 척하지 않는다.
      bad.push(`rigid_row=${JSON.stringify(raw_r)} (1 이상이어야 한다)`);
    }
  }
  return bad;
}

// SPDX-License-Identifier: Apache-2.0
// curator/store.js — 런 스냅샷 + 큐레이션 인메모리 모델 (run/entries/프레임·복제 해석) — 클라이언트 상태 SSoT
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

let run = null; // /api/run snapshot

let entries = {}; // { stateName: { order: [idx], sel: Set<idx>, transforms: { idx: {..} } } }

const imageCache = new Map();

const previews = {}; // stateName -> { playing, speed, cursor } preview transport state

// --- pixel-perfect variant (fit.pixel_perfect runs save a .plain.png twin) --
// Per-STATE toggles: each row with a twin gets its own on/off (what that row
// displays AND bakes, persisted as curation.json states.<state>.pixel_perfect).
// The header checkbox is a toggle-ALL over the same per-state truth.
let ppTwinStates = new Set();  // states that actually saved a twin

let ppStates = {};             // stateName -> bool (true = pixel-perfect variant)

// Same per-state + toggle-all shape as pixel-perfect: each grid-capable row has
// its own checkbox, the header checkbox sets all rows at once.

let gridStates = {};               // stateName -> bool (overlay shown)

let anchorStates = new Set();      // direction-anchor states (directionGroups runs)

// ── 방향 앵커 프레임 (수홍 2026-07-25) ──────────────────────────────────────
// 앵커 = 그 방향의 identity 로 생성에 붙는 **단 한 장**. 지정(curation.json anchors)이
// 있으면 그것, 없으면 앵커 행의 시퀀스 첫 프레임 (엔진 sprite_gen/anchor.py 와 같은 규칙).
// 배지는 **클라가 직접 해석**한다 — 지금 편집 중인 상태(삭제·재정렬·지정)에서 "저장하면
// 무엇이 앵커가 되는가" 를 보여야 하므로, 디스크 스냅샷(run.directionGroups[].anchorFrame)
// 을 그대로 쓰면 한 박자 늦는다. 디스크 값은 칩 라벨/오류 표시에만 쓴다.
let anchorPicks = {};              // direction -> {state, index} (사용자 지정)

let anchorDirections = [];         // 생성 방향 목록 (미러 방향 제외)

function directionOfState(stateName) {
  for (const d of anchorDirections) {
    if (stateName === d || stateName.startsWith(d + "_")) return d;
  }
  return null;
}

function anchorRowOf(direction) {
  const g = (run.directionGroups || []).find((x) => x.direction === direction && !x.mirrorOf);
  return (g && g.anchor) || null;
}

// 그 방향의 기본 앵커 인스턴스 = 앵커 행의 시퀀스 첫 프레임 (index 0 이 아니다 —
// 앞 프레임을 삭제/재정렬했으면 index 0 은 기각분이다).
function defaultAnchorFrame(direction) {
  const row = anchorRowOf(direction);
  if (!row || !entries[row]) return null;
  const play = playList(row);
  return play.length ? { state: row, index: play[0], source: "default" } : null;
}

function resolvedAnchorFrame(direction) {
  const pick = anchorPicks[direction];
  if (pick) return { ...pick, source: "picked" };
  return defaultAnchorFrame(direction);
}

function isAnchorFrame(stateName, idx) {
  const d = directionOfState(stateName);
  if (!d) return false;
  const a = resolvedAnchorFrame(d);
  return !!a && a.state === stateName && a.index === idx;
}

// 명시 지정된(고정된) 앵커인가 — 배지는 "지금 앵커인가"(isAnchorFrame), 버튼의 토글
// 방향은 "고정돼 있나"(이것)로 갈린다. 기본값 앵커도 눌러서 고정할 수 있어야 한다.
function isPinnedAnchorFrame(stateName, idx) {
  const d = directionOfState(stateName);
  const p = d && anchorPicks[d];
  return !!p && p.state === stateName && p.index === idx;
}

// 낡은 핀(재생성된 행 / 증명 없는 핀) — 그 프레임을 다시 누르면 재지정으로 풀린다.
function isStaleAnchorFrame(stateName, idx) {
  const d = directionOfState(stateName);
  const p = d && anchorPicks[d];
  return !!p && !!p.stale && p.state === stateName && p.index === idx;
}

function anchorFrameLabel(direction) {
  const a = resolvedAnchorFrame(direction);
  return a ? `${a.state}#${a.index}` : "—";
}

let anchorCacheBust = 0; // 칩(라이브 베이크) 표시 캐시 무효화 — 지정이 바뀔 때만 증가

// 지정/해제. 앵커 칩은 서버가 **디스크의 curation.json** 에서 굽기 때문에, 저장을
// 먼저 밀어낸 뒤에 다시 그린다 (디바운스 250ms 를 기다리면 한 박자 옛 앵커가 보인다).
async function setAnchorFrame(stateName, idx) {
  const direction = directionOfState(stateName);
  if (!direction) return;
  // 토글 축은 **명시 지정 여부**다 (기본값으로 앵커인가가 아니다): 지정된 그 프레임을
  // 다시 누르면 해제, 그 외에는(기본값으로 이미 앵커인 프레임 포함) 명시 지정. 기본값
  // 앵커를 눌러도 무반응이면 "지금 이 프레임을 고정해서 재정렬·삭제에도 안 움직이게"
  // 를 뷰에서 할 방법이 없다 (젯비 검증 2026-07-25 범위 밖 note 1 — CLI 로만 가능했다).
  const pinned = anchorPicks[direction];
  if (pinned && pinned.state === stateName && pinned.index === idx) {
    if (pinned.stale) {
      // 낡은(증명 안 되는) 핀을 그 프레임에서 다시 누른 것 = **명시 재지정**. 지문을 새로
      // 받아야 오류가 풀린다 — 저장 경로가 조용히 유효화하지 않는 게 계약이라(fail-safe),
      // 푸는 길은 이 명시 의도뿐이다.
      anchorPicks[direction] = { state: stateName, index: idx, repin: true };
    } else {
      delete anchorPicks[direction];
    }
  } else {
    anchorPicks[direction] = { state: stateName, index: idx };
  }
  scheduleSave();
  await flushSave();  // 저장이 카운터를 올리고 칩을 다시 받아온다 (persistence.save)
  const group = (run.directionGroups || []).find((g) => g.direction === direction && !g.mirrorOf);
  for (const name of (group ? group.states : [stateName])) {
    if (entries[name]) rebuildState(name);
  }
  if (typeof renderPipelineTree === "function") renderPipelineTree();  // 트리 앵커 노드도 같은 진실
  const label = anchorFrameLabel(direction);
  setStatus(anchorPicks[direction] ? STR[lang].anchorPinned(direction, label)
                                   : STR[lang].anchorUnpinned(direction, label), "ok");
}

let pixelEdit = null; // 모달 편집 세션: {state, idx, tool: 'pen'|'eraser', color, journal: []}

// 편집 truth 인덱스 (수홍 확정 2026-07-18): 링크된 복제의 편집 SSoT 는 원본
// 프레임 하나다 — 복제는 재생 슬롯일 뿐. '링크 끊기' 한 복제(unlinked)만 자기
// 편집을 소유한다. 모든 편집 읽기/쓰기는 이 리졸버를 거친다.
function editIndex(stateName, idx) {
  const e = entries[stateName];
  const src = e && e.clones ? e.clones[idx] : undefined;
  if (src === undefined) return idx;
  return e.unlinked && e.unlinked.has(idx) ? idx : src;
}

function isLinkedClone(stateName, idx) {
  const e = entries[stateName];
  return !!(e && e.clones && e.clones[idx] !== undefined && !(e.unlinked && e.unlinked.has(idx)));
}

function getPixelOps(stateName, idx) {
  const e = entries[stateName];
  if (!e || !e.pixels) return null;
  const ops = e.pixels[editIndex(stateName, idx)];
  return ops && Object.keys(ops).length ? ops : null;
}

function ppOn(stateName) {
  return ppStates[stateName] !== false;
}

// 소스 선택은 엔진이 구운 파일 사이에서만 갈린다 (트윈 = 추출이 만든 진실).
// 트윈 없는 프레임의 퍼펙은 파일 변형이 아니라 표시 렌더러의 양자화 모드다
// (snapScaleFor 의 측정 k) — 구 서버 추정 스냅 프리뷰 레거시는
// 표시 격자와 다른 검출기로 스냅해 "격자 기준 퍼펙" 을 깨뜨려서 폐기했다
// (수홍 2026-07-24, plan curator-single-display-pipeline).
function frameUrl(stateName, frame) {
  if (frame.plainUrl) return !ppOn(stateName) ? frame.plainUrl : frame.url;
  return frame.url;
}

// **굽기가 읽는 파일** — 파이썬 `frame_variant` + `row_frame_rel` 미러.
// `frameUrl` 은 표시용이라 pp OFF 에서 `orig/` 고해상본(굽기가 안 읽는 파일)을 줄 수
// 있다. 호흡은 굽기와 같은 그림을 내야 하므로 워프 base 도 신선도 기준도 이걸 쓴다 —
// 안 그러면 지문이 영구 불일치라 영상 내보내기가 영구 차단된다 (슉슉이 2026-07-26).
// 퍼펙 토글 시드 — 첫 렌더 전에 해소돼야 한다 (`frameUrl` 이 읽는다).
// boot 에 인라인돼 있던 것을 여기로 옮겼다: 이 시드가 `bakeVariant` 의 입력이라,
// 그물이 `ppStates` 를 손으로 먹이면 "웹뷰가 서버와 같은 변종을 **스스로** 골라내는가"
// 를 영영 못 묻는다 — 그 공백으로 변종 불일치가 한 라운드를 살아남았다
// (슉슉이 실측 2026-07-26).
//
// 퍼펙 토글은 **모든 줄**이 가진다 — 트윈 줄은 소스 전환(canonical↔orig), 트윈 없는
// 줄은 측정 k 양자화 렌즈(snapScaleFor). "가능한 줄" 게이팅은 격자 게이팅과 같은
// 병이었다 (수홍 2026-07-24: 확대화면에 퍼펙 버튼이 없다 — 조건 분기 = 버그).
function seedPixelPerfect(snapshot) {
  ppTwinStates = new Set(
    snapshot.states.filter((s) => s.frames.some((f) => f.plainUrl)).map((s) => s.name));
  const ppDefault = !(snapshot.curation && snapshot.curation.pixel_perfect === false);
  ppStates = {};
  for (const s of snapshot.states) {
    const c = snapshot.curation && snapshot.curation.states && snapshot.curation.states[s.name];
    // 트윈 없는 줄 기본 OFF (원본 먼저 — 양자화 렌즈는 사용자가 눌러서 본다).
    // 이건 **표시 렌즈 기본값**이고 굽기 변종이 아니다 (아래 `bakeVariant`).
    const fallback = ppTwinStates.has(s.name) ? ppDefault : false;
    ppStates[s.name] = c && typeof c.pixel_perfect === "boolean" ? c.pixel_perfect : fallback;
  }
}

// ── 굽기 변종 (표시 렌즈와 **다른 개념**) ───────────────────────────
//
// `ppOn` 은 **표시 렌즈**다: 트윈 없는 줄은 boot 시드가 기본 OFF 로 놓는다(원본 먼저,
// 양자화 렌즈는 눌러서 본다). 그런데 그 값은 사이드카에 **저장되지 않으므로**
// (persistence: 트윈 줄만 기록) 서버 `frame_variant` 는 그 줄을 `pixel` 로 본다.
//
// 이 둘을 같은 값으로 쓰면 기본 런(= `fit.pixel_perfect` 없는 런, `orig/`·`.plain.png`
// 가 하나도 없다) 전부에서 웹뷰는 `plain`, 서버는 `pixel` 로 키를 지어 지문이 **영구
// 불일치**가 된다 — 프리뷰가 영원히 원본으로 떨어지고 영상 내보내기가 영구 차단되며,
// 안내대로 해부를 갱신해도 라우트가 또 `pixel` 로 같은 키를 만들어 안 풀린다
// (슉슉이 실측 2026-07-26).
//
// 그래서 굽기 변종은 **저장되는 값**으로만 해소한다 — 파이썬 `curation.frame_variant`
// 미러(per-state bool > 런 전역 > pixel).

// 이 줄의 `pixel_perfect` 로 사이드카에 실제로 기록되는 값 (기록 안 하면 undefined).
// persistence 도 이걸 쓴다 — 저장 규칙과 굽기 해소가 두 벌이면 반드시 갈린다.
function savedPixelPerfect(stateName) {
  return ppTwinStates.has(stateName) ? ppOn(stateName) : undefined;
}

// 사이드카의 런 전역 `pixel_perfect`. 뷰는 **트윈 줄이 전부 같을 때만** 이 필드를
// authoring 한다(혼합/트윈 없음이면 생략). 생략은 삭제가 아니라 "안 건드림" 이고
// 서버가 기존 값을 이월하므로, 여기서도 로드된 값이 그대로 진실이다 — 그러지 않으면
// 트윈 없는 줄에서 서버는 `plain`, 웹뷰는 `pixel` 로 갈린다 (해소표 전수에서 발견).
function savedRunPixelPerfect() {
  if (ppTwinStates.size) {
    const vals = [...ppTwinStates].map((n) => ppOn(n));
    if (vals.every((v) => v === vals[0])) return vals[0];
  }
  const loaded = run && run.curation ? run.curation.pixel_perfect : undefined;
  return typeof loaded === "boolean" ? loaded : undefined;
}

// 굽기가 읽는 변종 — 파이썬 `curation.frame_variant` 미러.
function bakeVariant(stateName) {
  const own = savedPixelPerfect(stateName);
  if (typeof own === "boolean") return own ? "pixel" : "plain";
  const wide = savedRunPixelPerfect();
  if (typeof wide === "boolean") return wide ? "pixel" : "plain";
  return "pixel";
}

// URL 과 파일 스탬프를 **한 자리에서** 고른다. 둘을 따로 고르면 "어느 변종을 골랐나" 가
// 갈릴 수 있고, 그러면 호흡 기준 프레임 키가 굽기와 다른 파일을 가리킨다.
function bakeFrame(stateName, frame) {
  if (bakeVariant(stateName) === "plain") {
    // 변종이 plain 인데 트윈 파일이 없으면 **없는 파일을 가리키는 것**이다. 캐노니컬로
    // 조용히 떨어지면 굽기(그 파일을 못 찾아 실패)와 웹뷰가 다른 진실을 갖는다.
    return { url: frame.plainBakeUrl || null, stamp: frame.plainBakeStamp || null };
  }
  return { url: frame.url, stamp: frame.stamp || null };
}

function bakeFrameUrl(stateName, frame) {
  return bakeFrame(stateName, frame).url;
}

// 호흡 경계선(rigid_row)을 재고 그릴 프레임 — **굽기가 해부를 확정하는 기준 프레임**.
//
// `rigid_row` 는 그 프레임의 콘텐츠 행 인덱스라, 모달이 열린 프레임을 재면 선의 화면
// 위치와 저장되는 행 번호가 굽기와 어긋난다 (골든 픽스처에서 8px = 50px 캐릭터의 16%,
// 슉슉이 실측 2026-07-26). 선택을 **한 함수로** 모아 둔다: 재는 자리와 그 재시도가
// 각자 고르면 한쪽만 고쳐도 조용히 갈린다 — round-10 이 정확히 그 모양이었다.
function breatheGeometryFrame(stateName, fallbackIdx) {
  const play0 = playList(stateName)[0];
  const index = play0 === undefined ? fallbackIdx : play0;
  const frame = frameOf(stateName, index);
  return { index, frame, url: frame ? bakeFrameUrl(stateName, frame) : null };
}

// 이 줄의 기준 프레임에 **정수 이동이 아닌** 변형이 걸려 있나.
//
// 굽기는 `apply_transform` 에서 BICUBIC 으로 리샘플하고(`snap_scale` 없는 런 = 기본 런
// 전부) 이 웹뷰는 `imageSmoothingEnabled=false` 캔버스, 즉 NEAREST 다. 회전·확대가
// 걸리면 같은 입력에도 두 쪽 그림이 다르다 (실측: rotate 3° 에서 12/12 위상 최대
// 1803px 상이). 원인은 표시 파이프라인이라 호흡이 고칠 수 있는 게 아니지만, `row-export`
// 와 비교뷰는 **이 캔버스로 파일을 굽는다** — 조용히 두면 사용자는 GIF 와 다른 파일을
// 받고도 모른다 (원칙 6). 고치지 못하는 차이라도 말은 해야 한다.
function breatheResamplesDifferently(stateName) {
  const play = playList(stateName);
  if (!play.length) return false;
  const t = (entries[stateName].transforms || {})[editIndex(stateName, play[0])];
  if (!t) return false;
  return Number(t.rotate || 0) !== 0 || Number(t.scale || 1) !== 1
    || Number(t.shx || 0) !== 0 || Number(t.shy || 0) !== 0;
}

// 파이썬 `breathe.reference_key` 의 재료를 사이드카에서 모은다 — **줄의 기준 프레임**
// (`playList[0]`, 굽기의 `images[0]`) 하나에 대해서만.
//
// 픽셀을 안 읽는다: 굽기는 BICUBIC 으로 리샘플하고 캔버스는 NEAREST 라 결과 픽셀은
// 구조적으로 다르다. 여기 들어가는 값은 전부 사이드카(사람의 의도)와 서버가 준 스탬프다.
// 재료가 하나라도 없으면 **null 을 돌려 거부시킨다** — 없는 값을 기본값으로 메우면
// 서로 다른 프레임이 같은 키를 갖는다.
function breatheReferenceKey(stateName) {
  const e = entries[stateName];
  if (!e || !run || !run.requestStamp) return null;
  const play = playList(stateName);
  if (!play.length) return null;
  const idx = play[0];
  const frame = frameOf(stateName, idx);
  if (!frame) return null;
  const stamp = bakeFrame(stateName, frame).stamp;
  if (!stamp) return null;
  const src = cloneSrc(stateName, idx);
  const editIdx = editIndex(stateName, idx);
  const t = e.transforms ? e.transforms[editIdx] : null;
  return breatheReferenceKeyOf({
    state: stateName,
    variant: bakeVariant(stateName),
    requestStamp: run.requestStamp,
    sourceIndex: src === null ? idx : src,
    sourceStamp: stamp,
    pixelOps: getPixelOps(stateName, idx),
    transform: t,
  });
}

// 복제 인스턴스 (entries[state].clones = {복제idx: 원본idx}) 인식 프레임 조회.
// 복제 카드는 원본의 frame 객체(이미지 URL/크기)를 빌리되 자기 인덱스로 표시된다.
// 서버/스키마 계약: 파일은 원본을 읽고, 변형/픽셀편집/순서는 복제 인덱스 소유.
// 프레임의 유니크 표시명: 테이크 라벨(blink#2) 우선, 없으면 #인덱스.
// 라벨은 원본(primary) 프레임에선 비어 있어 유니크하지 않지만, 인덱스는 줄 안에서
// 항상 유니크하므로 이 조합이 카드 표시명의 SSoT 다 (복제 배지도 이걸 가리킨다).
function frameDisplayName(stateName, idx) {
  const st = run.states.find((s) => s.name === stateName);
  const f = st && st.frames.find((x) => x.index === idx && x.clone === undefined);
  return f && f.label ? f.label : `#${idx}`;
}

function cloneSrc(stateName, idx) {
  const e = entries[stateName];
  const src = e && e.clones ? e.clones[idx] : undefined;
  return src === undefined ? null : src;
}

function frameOf(stateName, idx) {
  if (stateName === BASE_STATE) {
    if (!baseView) return null;
    return { index: 0, present: true, url: baseView.url, plainUrl: baseView.rawUrl,
             label: "base", contentSize: [baseView.cols, baseView.rows] };
  }
  const st = run.states.find((s) => s.name === stateName);
  if (!st) return null;
  const src = cloneSrc(stateName, idx);
  const f = st.frames.find((fr) => fr.index === (src === null ? idx : src));
  if (!f) return null;
  if (src === null) return f;
  return { ...f, index: idx, clone: src, label: null };
}

// 퍼펙 표시 중인 줄의 양자화 격자 스케일 (퍼펙 OFF 면 null). 격자 진실은 하나다:
// - pp 런: 계약 scale — 굽기(curation.pixel_snap_scale)와 거울. 프리뷰가 굽기를 따른다.
// - 그 외(임포트 등): **측정 k(`st.pixelScale`)** — 격자 오버레이가 그리는 바로 그 값.
//   격자가 쳐지면 그 격자 기준으로 퍼펙이 된다 (수홍 2026-07-24). k=1 은 항등.
//   이건 표시 렌즈다 — 임포트 런 굽기는 양자화가 없으므로 사이드카에 저장하지
//   않는다 (persistence 의 트윈-줄 한정 저장이 그 계약이다).
function snapScaleFor(stateName) {
  // ppOn 검사가 **모든 분기보다 먼저**다 — 베이스를 먼저 항등 처리하면 pp OFF 의
  // 원본(raw) 뷰가 죽는다 (콩콩이 기각 2026-07-24: 퍼펙 체크박스가 아무것도 안
  // 바꾸는 "거짓말하는 컨트롤"이 됐다). 베이스 pp OFF = 소스 모드로 raw 를 그린다.
  if (!ppOn(stateName)) return null;
  if (stateName === BASE_STATE) return 1; // 베이스 pp ON = 논리 양자화 뷰 (캔버스 1:1)
  if (run.fitPixelPerfect && run.pixelPerfect && run.pixelPerfect.scale) return run.pixelPerfect.scale;
  const st = run.states.find((s) => s.name === stateName);
  return (st && st.pixelScale) || 1;
}

function isIdentityTransform(t) {
  return !t.rotate && t.scale === 1 && !t.dx && !t.dy && !t.shx && !t.shy && !t.flipX;
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
  const key = editIndex(stateName, idx); // 링크 복제 = 원본 truth 공유 (쓰기도 이 객체로)
  if (!t[key]) t[key] = IDENTITY();
  return t[key];
}

// selected := the frame is in the sequence row (top). Moving a card between the
// sequence and pool rows (drag or click) is what flips this; see moveCardToOtherZone.
function isSelected(stateName, idx) {
  return entries[stateName].sel.has(idx);
}

// play sequence = display order filtered to selected frames.
// This is exactly what gets persisted as curation.json `selected`, which
// compose_sprite_atlas.py lays out left-to-right in this order.
function playList(stateName) {
  const e = entries[stateName];
  return e.order.filter((idx) => e.sel.has(idx));
}

// ── 베이스 = 줌 모달과 같은 컴포넌트 (수홍 지시 2026-07-17 "같은 컴포넌트를 쓰라") ──
// 가상 상태 "__base__" 로 줌 모달을 연다. 편집 공간 = 검출 격자의 논리 해상도
// (예: 28×48) — 프레임과 동일한 조작감. 저장은 사이드카가 아니라 명시 버튼으로
// /api/base-edit (space:"logical") 에 굽고, 서버가 논리→raw 블록 확장을 맡는다.
const BASE_STATE = "__base__";

let baseView = null; // {cols, rows, xEdges, yEdges, url(논리 PNG), rawUrl} — 베이스 뷰 상태

let baseLogicalImg = null; // 논리 이미지 엘리먼트 — 편집/합성/샘플의 단일 소스

// 편집·합성 소스: 베이스는 항상 논리 이미지 (pp OFF 로 raw 를 "보고" 있어도
// 편집 공간은 논리 격자 — 균일 좌표가 이미지와 어긋나는 문제의 원천 차단).
function editSourceFor(stateName, imgEl) {
  return stateName === BASE_STATE && baseLogicalImg ? baseLogicalImg : imgEl;
}

function cellDims(stateName) {
  if (stateName === BASE_STATE && baseView) return [baseView.cols, baseView.rows];
  return [run.cell.width, run.cell.height];
}

function seedEntries() {
  entries = {};
  const curated = (run.curation && run.curation.states) || {};
  for (const state of run.states) {
    const physPresent = state.frames.filter((f) => f.present).map((f) => f.index);
    const c = curated[state.name];
    // 복제 인스턴스: {복제idx: 원본idx}. 복제idx 는 물리 범위 밖 정수, 원본은 물리
    // 프레임이어야 한다 (손상 항목 스킵). 원본이 present 면 복제도 present 취급.
    const physIdxSet = new Set(state.frames.map((f) => f.index));
    const clones = {};
    if (c && c.clones && typeof c.clones === "object") {
      for (const [k, v] of Object.entries(c.clones)) {
        const ci = Number(k);
        const src = Number(v);
        if (Number.isInteger(ci) && Number.isInteger(src) && !physIdxSet.has(ci) && physIdxSet.has(src)) clones[ci] = src;
      }
    }
    const cloneIdx = Object.keys(clones).map(Number);
    const present = [...physPresent, ...cloneIdx.filter((ci) => physPresent.includes(clones[ci]))];
    // order = full display arrangement (sequence then pool); sel = which are on.
    // Coerce to integers and de-dupe so a hand-edited / corrupt sidecar (string
    // indices, duplicates) can't produce a duplicated or dropped frame.
    const missing = state.frames.filter((f) => !f.present).map((f) => f.index);
    const allIdx = [...present, ...missing];
    const coerce = (arr, valid) => {
      const seen = new Set();
      const out = [];
      for (const raw of Array.isArray(arr) ? arr : []) {
        const i = Number(raw);
        if (Number.isInteger(i) && valid.includes(i) && !seen.has(i)) {
          seen.add(i);
          out.push(i);
        }
      }
      return out;
    };
    const archived = c && Array.isArray(c.deleted) ? coerce(c.deleted, allIdx) : [];
    const archivedSet = new Set(archived);
    const savedSel = (c && Array.isArray(c.selected) ? coerce(c.selected, present) : []).filter((i) => !archivedSet.has(i));
    const savedOrder = (c && Array.isArray(c.order) ? coerce(c.order, allIdx) : []).filter((i) => !archivedSet.has(i));
    let order;
    if (savedOrder.length) {
      // restore the exact saved arrangement (incl. pool order); append any
      // newly-extracted frames that weren't in the saved order.
      const seen = new Set([...savedOrder, ...archived]);
      order = [...savedOrder, ...allIdx.filter((i) => !seen.has(i))];
    } else if (savedSel.length) {
      // older sidecar without `order`: selected leads, the rest trail.
      const inSel = new Set(savedSel);
      order = [...savedSel, ...present.filter((i) => !inSel.has(i) && !archivedSet.has(i)),
               ...missing.filter((i) => !archivedSet.has(i))];
    } else {
      order = allIdx.filter((i) => !archivedSet.has(i));
    }
    const sel = savedSel.length ? new Set(savedSel) : new Set(present.filter((i) => !archivedSet.has(i)));
    const transforms = {};
    if (c && c.transforms) {
      for (const [idx, t] of Object.entries(c.transforms)) {
        transforms[idx] = { ...IDENTITY(), ...t };
      }
    }
    const pixels = {};
    if (c && c.pixels && typeof c.pixels === "object") {
      for (const [k, v] of Object.entries(c.pixels)) {
        const i = Number(k);
        if (Number.isInteger(i) && v && typeof v === "object" && Object.keys(v).length) pixels[i] = { ...v };
      }
    }
    const unlinked = new Set();
    if (c && Array.isArray(c.unlinked)) {
      for (const raw of c.unlinked) {
        const ci = Number(raw);
        if (clones[ci] !== undefined) unlinked.add(ci);
      }
    }
    // 레거시 자가판정: 링크 개념 도입(2026-07-18) 이전에 복제에 직접 넣은 변형/픽셀
    // 편집은 독립 의도였다 — 자동 unlinked 로 보존 (조용한 편집 소실 금지)
    for (const ci of Object.keys(clones).map(Number)) {
      if (unlinked.has(ci)) continue;
      const hasOwn = (c && c.transforms && c.transforms[ci]) || (c && c.pixels && c.pixels[ci] && Object.keys(c.pixels[ci]).length);
      if (hasOwn) unlinked.add(ci);
    }
    const names = {};
    if (c && c.names && typeof c.names === "object") {
      for (const [k, v] of Object.entries(c.names)) {
        const i = Number(k);
        if (Number.isInteger(i) && typeof v === "string" && v.trim()) names[i] = v.trim().slice(0, 24);
      }
    }
    // 폐기된 분할선 스키마(splits/amplitude/subpixel/hold)를 가진 사이드카는 **건드리지
    // 않고 그대로 들고 있는다.** 파싱 실패로 null 처리하면 그 줄이 "호흡 꺼짐"으로 보이고
    // 첫 autosave 가 폐기 키를 사이드카에서 지워버려, 굽기의 loud reject 도 migrate-breathe
    // 도 근거를 잃는다 (새미·부리 독립 실측 2026-07-25 — 구 런을 한 번 여는 것만으로 소실).
    const rawBreathe = c && c.breathe;
    const retired = rawBreathe
      ? RETIRED_BREATHE_KEYS.filter((k) => k in rawBreathe) : [];
    // 범위 밖 값도 **조용히 깎지 않는다.** 굽기는 loud reject 하는데 웹뷰만 깎아서 되쓰면
    // 사용자의 원래 숫자가 파괴되고 loud reject 자체가 사라진다 (슉슉이 실측 2026-07-25).
    // 폐기 키와 같은 취급: 원본 보존 + 화면 통지.
    const outOfRange = rawBreathe && !retired.length
      ? breatheRangeProblems(rawBreathe) : [];
    entries[state.name] = { order, sel, transforms, archived, pixels, clones, unlinked, names,
      // 원본 보존 — persistence 가 이걸 그대로 되쓴다 (정규화·삭제 금지)
      breatheRetired: (retired.length || outOfRange.length)
        ? { keys: retired.length ? retired : outOfRange, raw: rawBreathe } : null,
      // 호흡 후처리 레이어 (사이드카 breathe — curation.state_breathe 와 같은 형태).
      // 클램프하지 않는다: 범위 검증은 위에서 하고, 여기서는 굽기와 같은 형변환만 한다
      // (파이썬 `float()`/`int()` 처럼 숫자 문자열도 받는다).
      // `depth` 생략은 굽기에서 기본값 0.06 이다 — 웹뷰만 "호흡 없음" 으로 읽으면
      // 켜짐/꺼짐이 반대가 되고 첫 autosave 가 설정을 지운다 (슉슉이 note 2026-07-26).
      // 명시적 `null` 은 키와 무관하게 위 범위 검사에 걸려 여기 안 온다.
      breathe: rawBreathe && !retired.length && !outOfRange.length
        // `depth` 생략은 굽기에서 0.06 이다. `Number(undefined)` = NaN 이고
        // `JSON.stringify(NaN)` 은 `null` 이라, 기본값 분기가 없으면 첫 autosave 가
        // 사이드카에 `depth: null` 을 박아 **굽기가 죽는다** (슉슉이 실측 2026-07-26).
        ? { depth: rawBreathe.depth == null ? 0.06 : Number(rawBreathe.depth),
            // 위에서 정수 검증을 통과했으므로 반올림이 필요 없다 — 반올림하면 굽기와 갈린다
            breaths: Number(rawBreathe.breaths == null ? 1 : rawBreathe.breaths),
            lag: Number(rawBreathe.lag == null ? 0.1 : rawBreathe.lag),
            rigid_row: rawBreathe.rigid_row == null ? null : Number(rawBreathe.rigid_row),
            anatomy: rawBreathe.anatomy || null }
        : null };
  }
  // 폐기 스키마가 하나라도 있으면 조용히 넘어가지 않는다 — 사용자가 마이그레이션을
  // 실행할 수 있게 알린다 (굽기 쪽 loud reject 와 같은 계약을 UI 에서도 지킨다).
  const stale = run.states.filter((s2) => entries[s2.name] && entries[s2.name].breatheRetired);
  if (stale.length) {
    const names = stale.map((s2) => s2.name).join(", ");
    const why = stale.map((s2) => entries[s2.name].breatheRetired.keys.join("/")).join(" · ");
    setStatus(`읽을 수 없는 호흡 사이드카: ${names} (${why}) — 원본은 그대로 두었다. `
      + `\`sprite-gen migrate-breathe <run-dir> --apply\` 로 옮기거나 값을 범위 안으로 고쳐라.`, "err");
  }
}
