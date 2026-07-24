// SPDX-License-Identifier: Apache-2.0
// curator/transforms.js — 카드 변형 (회전/스케일/시어/플립) 적용 + 스테이지 조작 배선
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

function applyCardTransform(stage, stateName, idx) {
  const t = getTransform(stateName, idx);
  const el = stage.querySelector("img");
  if (!el) return;
  const [cw, ch] = cellDims(stateName); // 베이스(가상 상태)는 검출 격자 논리 치수
  // dx/dy are stored in cell pixels; CSS needs rendered pixels.
  const ds = stage.clientWidth / cw;
  const m = matrixOf(t);
  const snap = snapScaleFor(stateName);
  const canvas = stage.querySelector(".snap-canvas");
  if (!canvas) return; // 프레임 없는 스테이지(missing 라벨)는 그릴 것이 없다
  // ── 표시면은 캔버스 하나다 (수홍 2026-07-24 "구현체가 몇 종류라 노이즈"). ──
  // img 는 숨김 로더(natural size·load 이벤트·편집 소스)일 뿐 절대 표시면이 아니다.
  // 예전엔 편집/변형/양자화 프레임만 캔버스로, 나머지는 img 로 갈라 그렸고 —
  // 그 fork 의 한쪽만 보다가 표시 결함 5건(v1.56.85~89)이 전부 태어났다.
  el.style.transform = "";
  el.style.visibility = "hidden";
  canvas.style.display = "block";
  // 한 렌더러의 두 모드 (fork 아님 — drawFrameInto 하나가 렌더한다):
  // - 양자화 (pp 뷰: 계약 scale 또는 측정 k): 변형을 격자 재양자화로 굽기와 동일하게
  //   미리 본다. 이동이 격자 단위로 스냅된다 (의도). 격자 오버레이와 같은 k — 격자 기준 퍼펙.
  // - 소스 (plain 뷰): 소스 해상도(ss)로 identity 렌더 + 이동은 CSS 변형.
  // 편집 세션도 같은 두 모드를 그대로 쓴다 (WYSIWYG, 수홍 2026-07-19).
  const quantize = !!snap; // 베이스 pp ON 은 snapScaleFor 가 1 을 돌려 여기 포함된다
  const render = () => {
    // 소스 모드는 소스 해상도로 그린다 — 셀 크기 캔버스에 고해상 원본 트윈을
    // 그리면 64px 로 파괴된다 (수홍 2026-07-24). 양자화 모드는 결과 격자가
    // 목적이라 셀 크기 그대로다.
    // 소스 모드의 **표시** 소스는 el(frameUrl 이 고른 파일 — 베이스 pp OFF 면 raw)이다.
    // editSourceFor 는 편집 좌표 계약(베이스 = 항상 논리 공간)이라 양자화 모드에서만
    // 소스로 쓴다 — 표시까지 논리로 강제하면 베이스의 원본 뷰가 사라진다 (콩콩이 기각).
    const source = quantize ? editSourceFor(stateName, el) : el;
    const ss = quantize ? 1 : superSampleFor(source, cw);
    canvas.width = cw * ss;
    canvas.height = ch * ss;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const tt = quantize ? getTransform(stateName, idx) : IDENTITY();
    drawFrameInto(ctx, source, tt, cw, ch, snap, getPixelOps(stateName, idx), ss);
    // 버퍼 해상도가 여기서 정해진다 — 사실을 만든 자리에서 소비자(판정)를 갱신한다
    // (콩콩이 R1, 2026-07-24: 바깥 스윕만 두면 canvas 기본값 300 을 보고 답한다).
    applyPixelScaling(canvas);
  };
  canvas.style.transform = quantize
    ? ""
    : `translate(${t.dx * ds}px, ${t.dy * ds}px) matrix(${m.m00}, ${m.m10}, ${m.m01}, ${m.m11}, 0, 0)`;
  if (el.complete && el.naturalWidth) render();
  else el.addEventListener("load", render, { once: true });
  const sh = t.shx || t.shy ? ` sh${(t.shx || 0).toFixed(2)},${(t.shy || 0).toFixed(2)}` : "";
  const flip = t.flipX ? " ↔" : "";
  const card = stage.closest(".card");
  const tvalsEl = card.querySelector(".tvals");
  // 항등 변형이면 값 줄을 비운다 (r0° ×1.00 +0,+0 상시 표시 = 잡음). 조정이 있을 때만 노출.
  if (tvalsEl) {
    tvalsEl.textContent = isIdentityTransform(t)
      ? ""
      : `r${t.rotate.toFixed(0)}° ×${t.scale.toFixed(2)} ${t.dx >= 0 ? "+" : ""}${t.dx.toFixed(0)},${t.dy >= 0 ? "+" : ""}${t.dy.toFixed(0)}${sh}${flip}`;
  }
  // flip 버튼은 도구(누를 때마다 반전)다 — 활성/비활성 상태 강조 없음 (수홍 2026-07-19).
  // 반전 상태 자체는 tvals 의 ↔ 로만 표시한다.
  // 대응 격자(초록)는 콘텐츠 기준 — 변형이 바뀔 때마다 이 카드 것만 다시 그린다
  // (이동은 따라오고, 비축정렬이 되면 숨는 판정도 여기서 갱신된다).
  updateCardGrid(card);
}

// 같은 프레임을 보여주는 모든 스테이지(그리드 카드 + 확대 모달)를 함께 갱신 —
// 어느 쪽에서 편집해도 두 화면이 실시간 동기화된다.
function applyFrameTransformAll(stateName, idx) {
  // 링크 동기화 (수홍 2026-07-18): 편집 truth 를 공유하는 모든 인스턴스 카드
  // (원본 + 링크된 복제들)를 같이 다시 그린다 — truth 는 하나, 표시는 전부.
  const truth = editIndex(stateName, idx);
  const e = entries[stateName];
  const targets = new Set([truth]);
  for (const i of e.order) {
    if (editIndex(stateName, i) === truth) targets.add(i);
  }
  for (const i of targets) {
    document
      .querySelectorAll(`.card[data-state="${cssEscape(stateName)}"][data-idx="${i}"] .stage`)
      .forEach((s) => applyCardTransform(s, stateName, i));
  }
}

// ── 변형 조작 히스토리 (수홍 2026-07-20 "커맨드제트 안 먹혀, 캐릭터 이동한 담에"):
// 제스처(드래그 1회·클릭 1회) 단위 before/after 스냅샷. 이동/회전/시어/스케일/
// 플립/리셋 전부 — 그리드 카드와 확대 모달 공통 (변형 truth 가 하나이므로 하나의
// 전역 스택). Cmd+Z 라우팅 우선순위는 zoom-editor.js 소유: 호흡 > 픽셀 편집 > 변형.
const transformHist = { list: [], pos: -1 };

function tfSnapshot(stateName, idx) {
  return { state: stateName, idx, tr: { ...getTransform(stateName, idx) } };
}

function pushTransformHist(before) {
  const after = tfSnapshot(before.state, before.idx);
  if (JSON.stringify(before.tr) === JSON.stringify(after.tr)) return; // 무변 제스처 = 잡음 금지
  transformHist.list = transformHist.list.slice(0, transformHist.pos + 1);
  transformHist.list.push({ before, after });
  transformHist.pos = transformHist.list.length - 1;
}

function applyTfSnapshot(s) {
  entries[s.state].transforms[editIndex(s.state, s.idx)] = { ...s.tr };
  applyFrameTransformAll(s.state, s.idx);
  scheduleSave();
}

function undoTransform() {
  if (transformHist.pos < 0) return false;
  applyTfSnapshot(transformHist.list[transformHist.pos].before);
  transformHist.pos -= 1;
  return true;
}

function redoTransform() {
  if (transformHist.pos + 1 >= transformHist.list.length) return false;
  transformHist.pos += 1;
  applyTfSnapshot(transformHist.list[transformHist.pos].after);
  return true;
}

function wireStage(stage, stateName, idx) {
  const ds = () => stage.clientWidth / run.cell.width;

  // translate by dragging, toggle select on a click that did not drag
  stage.addEventListener("pointerdown", (ev) => {
    if (ev.target.classList.contains("rotate-handle")) return;
    ev.preventDefault();
    stage.setPointerCapture(ev.pointerId);
    const before = tfSnapshot(stateName, idx);
    const t = getTransform(stateName, idx);
    const start = { x: ev.clientX, y: ev.clientY, dx: t.dx, dy: t.dy };
    let moved = false;

    const onMove = (e) => {
      const ddx = e.clientX - start.x;
      const ddy = e.clientY - start.y;
      if (Math.abs(ddx) > DRAG_THRESHOLD || Math.abs(ddy) > DRAG_THRESHOLD) moved = true;
      t.dx = start.dx + ddx / ds();
      t.dy = start.dy + ddy / ds();
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      stage.releasePointerCapture(ev.pointerId);
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("pointerup", onUp);
      // 스테이지 클릭은 더 이상 시퀀스⇄풀 토글이 아니다 (수홍 2026-07-15): 실수로
      // 카드를 눌러 빠지는 걸 막는다. 이동은 넣기/빼기 버튼 또는 드래그로만.
      if (moved) {
        pushTransformHist(before);
        scheduleSave();
      }
    };
    stage.addEventListener("pointermove", onMove);
    stage.addEventListener("pointerup", onUp);
  });

  // (휠 스케일 제거 — 맥 터치패드 오작동. 크기 조절은 우하단 돋보기 스크러버로.)

  // rotate via the top handle
  const handle = stage.querySelector(".rotate-handle");
  handle.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    handle.setPointerCapture(ev.pointerId);
    const before = tfSnapshot(stateName, idx);
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
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      handle.releasePointerCapture(ev.pointerId);
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      pushTransformHist(before);
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
    const before = tfSnapshot(stateName, idx);
    const t = getTransform(stateName, idx);
    const start = { x: ev.clientX, y: ev.clientY, shx: t.shx || 0, shy: t.shy || 0 };
    const onMove = (e) => {
      // full-width drag ≈ 1.0 slope; small moves give fine control
      t.shx = start.shx + (e.clientX - start.x) / stage.clientWidth;
      t.shy = start.shy + (e.clientY - start.y) / stage.clientHeight;
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      shear.releasePointerCapture(ev.pointerId);
      shear.removeEventListener("pointermove", onMove);
      shear.removeEventListener("pointerup", onUp);
      pushTransformHist(before);
      scheduleSave();
    };
    shear.addEventListener("pointermove", onMove);
    shear.addEventListener("pointerup", onUp);
  });
}

//
// Each state renders two `.frames` rows: the top is the play SEQUENCE (selected
// frames, in order) and the bottom is the candidate POOL (everything else,
// e.g. an extra generated take). Dragging the ⠿ grip reorders within a row OR
// moves a card between rows; which row a card lands in *is* its selection. The
// grip lives in `.card-top`, outside `.stage`, so it never collides with the
// stage's move/scale/rotate/shear drags.

// 스프라이트 크기 스크러버 (‹⤢› 형태, 이모지 아님 — SVG): 화살표 클릭 = 스텝,
// 가운데 아이콘 드래그 = 연속. 맥 터치패드에서 휠-스케일이 불편해 추가.
// 아이콘 = 대각 리사이즈 — 돋보기는 "화면 배율"(view-nav.js) 전용 어휘라 여기 금지
// (수홍 지시 2026-07-20: 돋보기로 하니까 확대축소 보기로 오해).
function makeScaleScrub(stateName, idx) {
  const wrap = document.createElement("span");
  wrap.className = "scale-scrub";
  wrap.setAttribute("data-tip", t("tScaleScrub"));
  wrap.innerHTML =
    '<button type="button" class="ghost ss-step" data-dir="-1" aria-label="smaller">' +
    '<svg viewBox="0 0 10 10" width="9" height="9"><path d="M2 5h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>' +
    '<span class="ss-grab" aria-label="drag to scale">' +
    '<svg viewBox="0 0 16 16" width="13" height="13">' +
    '<path d="M9.8 3h3.2v3.2M6.2 13H3v-3.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>' +
    '<path d="M12.6 3.4 9.4 6.6M3.4 12.6l3.2-3.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></span>' +
    '<button type="button" class="ghost ss-step" data-dir="1" aria-label="bigger">' +
    '<svg viewBox="0 0 10 10" width="9" height="9"><path d="M2 5h6M5 2v6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>';
  const clamp = (v) => Math.min(SCALE_MAX, Math.max(SCALE_MIN, v));
  // ± 연타가 스테이지 더블클릭(확대 모달 열기)으로 새지 않게 스크러버 안의
  // 포인터/클릭 계열 이벤트는 전부 여기서 끊는다 (수홍 지적 2026-07-16).
  for (const type of ["pointerdown", "click", "dblclick"]) {
    wrap.addEventListener(type, (ev) => ev.stopPropagation());
  }
  wrap.querySelectorAll(".ss-step").forEach((btn) => {
    btn.addEventListener("pointerdown", (ev) => ev.stopPropagation());
    btn.addEventListener("click", () => {
      const before = tfSnapshot(stateName, idx);
      const tr = getTransform(stateName, idx);
      tr.scale = clamp(tr.scale * (btn.dataset.dir === "1" ? 1.05 : 1 / 1.05));
      applyFrameTransformAll(stateName, idx);
      pushTransformHist(before);
      scheduleSave();
    });
  });
  const grab = wrap.querySelector(".ss-grab");
  grab.addEventListener("pointerdown", (ev) => {
    if (ev.button || !ev.isPrimary) return;
    ev.preventDefault();
    ev.stopPropagation();
    grab.setPointerCapture(ev.pointerId);
    const before = tfSnapshot(stateName, idx);
    const tr = getTransform(stateName, idx);
    const startX = ev.clientX;
    const startScale = tr.scale;
    const onMove = (e2) => {
      tr.scale = clamp(startScale * Math.exp((e2.clientX - startX) / 140));
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      grab.releasePointerCapture(ev.pointerId);
      grab.removeEventListener("pointermove", onMove);
      grab.removeEventListener("pointerup", onUp);
      pushTransformHist(before);
      scheduleSave();
    };
    grab.addEventListener("pointermove", onMove);
    grab.addEventListener("pointerup", onUp);
  });
  return wrap;
}

function resetTransform(stateName, idx) {
  const before = tfSnapshot(stateName, idx);
  entries[stateName].transforms[editIndex(stateName, idx)] = IDENTITY();
  applyFrameTransformAll(stateName, idx);
  pushTransformHist(before);
  scheduleSave();
}

function toggleFlipX(stateName, idx) {
  const entry = entries[stateName];
  if (!entry) return;
  const before = tfSnapshot(stateName, idx);
  const key = editIndex(stateName, idx);
  if (!entry.transforms[key]) entry.transforms[key] = IDENTITY();
  entry.transforms[key].flipX = entry.transforms[key].flipX ? 0 : 1;
  // 모든 스테이지에 거울 반전을 렌더하고 flip 버튼을 강조한다.
  applyFrameTransformAll(stateName, idx);
  pushTransformHist(before);
  scheduleSave();
}
