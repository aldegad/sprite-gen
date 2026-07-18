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
  const edits = getPixelOps(stateName, idx);
  const editingThis = pixelEdit && pixelEdit.state === stateName && pixelEdit.idx === idx
    && stage.closest("#zoom-modal");
  const basePp = stateName === BASE_STATE && ppOn(stateName); // 베이스 pp ON = 논리 양자화 뷰
  if (canvas && (edits || editingThis || basePp || (snap && !isIdentityTransform(t)))) {
    el.style.transform = "";
    el.style.visibility = "hidden";
    canvas.style.display = "block";
    // 두 캔버스 모드 (실사고 2026-07-17: 편집 유무에 따라 이동 거동이 갈라져
    // 한쪽만 지글거렸다 — 수홍 발견):
    // - 양자화 미리보기 (픽셀퍼펙트 뷰 + 변형): 변형을 격자 재양자화로 굽기와
    //   동일하게 미리 본다. 재양자화 특성상 이동이 격자 단위로 스냅된다 (의도).
    // - 소스 표시 (plain 뷰의 편집 프레임): identity 로 한 번 그리고,
    //   이동은 img 와 똑같은 CSS 변형으로 — 편집 없는 프레임과 거동이 같아진다.
    // 편집 세션도 같은 두 모드를 그대로 쓴다 (WYSIWYG, 수홍 지시 2026-07-19:
    // 지우개/스포이드를 눌러도 스케일 조정해 둔 모습 그대로 보면서 편집).
    // 입력 좌표는 줌 에디터가 소스 공간으로 역변환한다 — 저장 공간은 불변.
    const quantize = (!!snap || basePp);
    const render = () => {
      canvas.width = cw;
      canvas.height = ch;
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const tt = quantize ? getTransform(stateName, idx) : IDENTITY();
      drawFrameInto(ctx, editSourceFor(stateName, el), tt, canvas.width, canvas.height, snap,
        getPixelOps(stateName, idx));
    };
    canvas.style.transform = quantize
      ? ""
      : `translate(${t.dx * ds}px, ${t.dy * ds}px) matrix(${m.m00}, ${m.m10}, ${m.m01}, ${m.m11}, 0, 0)`;
    if (el.complete && el.naturalWidth) render();
    else el.addEventListener("load", render, { once: true });
  } else {
    el.style.visibility = "";
    if (canvas) {
      canvas.style.display = "none";
      canvas.style.transform = "";
    }
    // CSS matrix(a,b,c,d,e,f): a=m00 b=m10 c=m01 d=m11; translate applied after, about center.
    el.style.transform =
      `translate(${t.dx * ds}px, ${t.dy * ds}px) matrix(${m.m00}, ${m.m10}, ${m.m01}, ${m.m11}, 0, 0)`;
  }
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
  const flipBtn = card.querySelector(".flip-btn");
  if (flipBtn) flipBtn.classList.toggle("active", !!t.flipX);
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
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      stage.releasePointerCapture(ev.pointerId);
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("pointerup", onUp);
      // 스테이지 클릭은 더 이상 시퀀스⇄풀 토글이 아니다 (수홍 2026-07-15): 실수로
      // 카드를 눌러 빠지는 걸 막는다. 이동은 넣기/빼기 버튼 또는 드래그로만.
      if (moved) scheduleSave();
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
      applyFrameTransformAll(stateName, idx);
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

//
// Each state renders two `.frames` rows: the top is the play SEQUENCE (selected
// frames, in order) and the bottom is the candidate POOL (everything else,
// e.g. an extra generated take). Dragging the ⠿ grip reorders within a row OR
// moves a card between rows; which row a card lands in *is* its selection. The
// grip lives in `.card-top`, outside `.stage`, so it never collides with the
// stage's move/scale/rotate/shear drags.

// 확대 스크러버 (‹🔍› 형태, 이모지 아님 — SVG): 화살표 클릭 = 스텝, 돋보기 드래그 = 연속.
// 맥 터치패드에서 휠-스케일이 불편해 추가 (휠도 계속 동작).
function makeScaleScrub(stateName, idx) {
  const wrap = document.createElement("span");
  wrap.className = "scale-scrub";
  wrap.setAttribute("data-tip", t("tScaleScrub"));
  wrap.innerHTML =
    '<button type="button" class="ghost ss-step" data-dir="-1" aria-label="smaller">' +
    '<svg viewBox="0 0 10 10" width="9" height="9"><path d="M2 5h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>' +
    '<span class="ss-grab" aria-label="drag to scale">' +
    '<svg viewBox="0 0 16 16" width="13" height="13">' +
    '<circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.4"/>' +
    '<path d="M7 5.2v3.6M5.2 7h3.6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>' +
    '<path d="M10.4 10.4 14 14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></span>' +
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
      const tr = getTransform(stateName, idx);
      tr.scale = clamp(tr.scale * (btn.dataset.dir === "1" ? 1.05 : 1 / 1.05));
      applyFrameTransformAll(stateName, idx);
      scheduleSave();
    });
  });
  const grab = wrap.querySelector(".ss-grab");
  grab.addEventListener("pointerdown", (ev) => {
    if (ev.button || !ev.isPrimary) return;
    ev.preventDefault();
    ev.stopPropagation();
    grab.setPointerCapture(ev.pointerId);
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
      scheduleSave();
    };
    grab.addEventListener("pointermove", onMove);
    grab.addEventListener("pointerup", onUp);
  });
  return wrap;
}

function resetTransform(stateName, idx) {
  entries[stateName].transforms[editIndex(stateName, idx)] = IDENTITY();
  applyFrameTransformAll(stateName, idx);
  scheduleSave();
}

function toggleFlipX(stateName, idx) {
  const entry = entries[stateName];
  if (!entry) return;
  const key = editIndex(stateName, idx);
  if (!entry.transforms[key]) entry.transforms[key] = IDENTITY();
  entry.transforms[key].flipX = entry.transforms[key].flipX ? 0 : 1;
  // 모든 스테이지에 거울 반전을 렌더하고 flip 버튼을 강조한다.
  applyFrameTransformAll(stateName, idx);
  scheduleSave();
}
