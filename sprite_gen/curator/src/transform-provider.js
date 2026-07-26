// SPDX-License-Identifier: Apache-2.0
// curator/transform-provider.js — 변형 제스처의 공용 프로바이더.
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// **왜 따로 있나**: 변형 제스처(이동·회전·비틀기)가 두 대상에 붙는다.
//   1. 셀 전체 — 사이드카 `transforms[idx]` 파라미터를 고친다 (`transforms.js`)
//   2. 선택 영역 — 픽셀을 실제로 옮겨 `pixels[idx]` 로 굽는다 (`region-transform.js`)
// 제스처의 **수학과 손맛**(드래그 임계·회전 각도·시어 기울기 환산)은 둘이 같아야 한다.
// 두 벌로 두면 한쪽만 고쳐져 "전체는 되는데 영역은 반대로 돈다" 같은 게 태어난다.
// 그래서 여기에 한 벌만 둔다. 대상별 차이는 콜백(onDelta)이 흡수한다.

// 드래그로 볼 최소 이동(px). 클릭과 드래그를 가르는 값 — `transforms.js` 가 쓰던 것과 같다.
// `DRAG_THRESHOLD` 는 util.js 전역 — index.html 로드 순서가 그것을 먼저 보장한다.
const TP_DRAG_THRESHOLD = DRAG_THRESHOLD;

// 화면 각도는 시계방향이 +, 스키마(PIL)는 반시계가 + 다. 부호를 여기서 한 번만 뒤집는다 —
// 두 소비자가 각자 뒤집으면 한쪽이 반대로 돈다.
function tpScreenDeltaToRotate(startAngle, nowAngle) {
  return -(((nowAngle - startAngle) * 180) / Math.PI);
}

// 기울기 정규화 — 전폭 드래그가 대략 1.0 기울기. 셀 전체·영역 두 경로가 같은 감도를 쓴다.
function tpShearDelta(ddx, ddy, spanW, spanH) {
  return { shx: ddx / Math.max(1, spanW), shy: ddy / Math.max(1, spanH) };
}

function tpAngleAt(cx, cy, ev) {
  return Math.atan2(ev.clientY - cy, ev.clientX - cx);
}

// 포인터 제스처 한 벌을 건다. `onDelta({dx, dy, rotate, shx, shy}, ev)` 가 매 이동마다
// **시작 시점 대비 누적 델타**를 받는다 — 소비자는 자기 기준값에 더하기만 하면 된다.
//
// 모디파이어로 축을 가른다 (핸들 DOM 을 안 늘린다 — 영역은 크기가 제각각이라
// 핸들을 붙이면 작은 선택에서 핸들이 영역을 덮는다):
//   기본        = 이동
//   Shift       = 회전 (영역/셀 중심 기준)
//   Alt(Option) = 비틀기 (가로 드래그 = shx, 세로 = shy)
function tpWireGesture(el, opts) {
  const {
    pixelsPerUnit,      // () => 화면px / 셀px 배율
    centerOf,           // () => [clientX, clientY] 회전 중심
    spanOf,             // () => [width, height] 비틀기 정규화 기준(화면px)
    onStart,            // () => void — 스냅샷 찍을 자리
    onDelta,            // (delta, ev) => void
    onEnd,              // (moved) => void
    enabled,            // () => boolean
  } = opts;

  el.addEventListener("pointerdown", (ev) => {
    if (enabled && !enabled(ev)) return;
    if (ev.button || !ev.isPrimary) return;
    ev.preventDefault();
    // **stopPropagation 이 아니라 stopImmediatePropagation 이다.** 같은 엘리먼트(stage)에
    // 셀 전체 이동 핸들러(`transforms.js` wireStage)가 같이 붙어 있어서, 버블링만 막으면
    // 그놈이 그대로 실행돼 영역과 셀이 **같이** 움직인다(노을이 재현 2026-07-26:
    // regionDelta dx=6 인데 wholeCellDx=6 도 같이 발생). 바로 옆 페인트 핸들러가
    // 같은 이유로 이미 stopImmediatePropagation 을 쓴다.
    ev.stopImmediatePropagation();
    el.setPointerCapture(ev.pointerId);
    if (onStart) onStart(ev);

    const start = { x: ev.clientX, y: ev.clientY };
    const [cx, cy] = centerOf ? centerOf() : [0, 0];
    const startAngle = tpAngleAt(cx, cy, ev);
    let moved = false;

    const onMove = (e) => {
      const ddx = e.clientX - start.x;
      const ddy = e.clientY - start.y;
      if (Math.abs(ddx) > TP_DRAG_THRESHOLD || Math.abs(ddy) > TP_DRAG_THRESHOLD) moved = true;
      const delta = { dx: 0, dy: 0, rotate: 0, shx: 0, shy: 0 };
      // 축 고정 — 핸들에서 온 제스처는 모디파이어와 무관하게 자기 축만 움직인다.
      // (스테이지 드래그는 axis 없이 걸어 모디파이어로 가른다.)
      const axis = opts.axis;
      if (axis === "rotate" || (!axis && e.shiftKey)) {
        delta.rotate = tpScreenDeltaToRotate(startAngle, tpAngleAt(cx, cy, e));
      } else if (axis === "shear" || (!axis && e.altKey)) {
        const [sw, sh] = spanOf ? spanOf() : [el.clientWidth, el.clientHeight];
        const sh2 = tpShearDelta(ddx, ddy, sw, sh);
        delta.shx = sh2.shx;
        delta.shy = sh2.shy;
      } else {
        const ppu = pixelsPerUnit ? pixelsPerUnit() : 1;
        delta.dx = ddx / ppu;
        delta.dy = ddy / ppu;
      }
      onDelta(delta, e);
    };
    const onUp = () => {
      el.releasePointerCapture(ev.pointerId);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      if (onEnd) onEnd(moved);
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
  });
}

// 영역 변형이 픽셀을 옮길 때 쓰는 역사상(destination → source). 정변환으로 소스를 훑으면
// 회전·비틀기에서 목적지에 구멍이 뚫린다 — 목적지를 훑고 소스를 역으로 찾아야 빈틈이 없다.
// `t` = {rotate(도, CCW+), shx, shy, dx, dy}, 중심 = (ox, oy) 셀 좌표.
function tpInverseSample(t, ox, oy) {
  const rad = ((t.rotate || 0) * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  // 정변환 M = Shear · Rot (transforms.js matrixOf 와 같은 합성 순서)
  const m00 = cos + (t.shx || 0) * sin;
  const m01 = -sin + (t.shx || 0) * cos;
  const m10 = (t.shy || 0) * cos + sin;
  const m11 = -(t.shy || 0) * sin + cos;
  const det = m00 * m11 - m01 * m10;
  if (Math.abs(det) < 1e-9) return null;
  const i00 = m11 / det;
  const i01 = -m01 / det;
  const i10 = -m10 / det;
  const i11 = m00 / det;
  return (dx, dy) => {
    const px = dx - ox - (t.dx || 0);
    const py = dy - oy - (t.dy || 0);
    return [i00 * px + i01 * py + ox, i10 * px + i11 * py + oy];
  };
}
