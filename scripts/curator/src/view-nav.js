// SPDX-License-Identifier: Apache-2.0
// curator/view-nav.js — 디테일뷰 공용 뷰 내비게이션 (수홍 지시 2026-07-20)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// 3계층 계약: 모달(고정 최대 크기) ⊃ 뷰포트(화면 배율·팬) ⊃ 콘텐츠(스프라이트 —
// 자체 변형/스케일은 transforms.js 소유). 이 모듈은 "뷰포트" 계층만 소유한다:
// - Space 홀드 / 휠버튼(중클릭) / 손 툴 = 뷰 팬 (wirePan)
// - 커서 앵커 줌 헬퍼 (keepViewAnchor) + 콘텐츠 중앙 정렬 (centerView)
// - 돋보기 −/+/맞춤 위젯 팩토리 (makeViewNavWidget — 뷰포트 우하단 + 툴바 공용)
// 확대편집(줌 모달)·호흡 포커스·비교 캔버스 세 디테일뷰가 전부 이 모듈을 쓴다.

// ── 뷰 내비 아이콘 (SVG 라인 아이콘 — 이모지 금지) ──
// 돋보기 = "화면 배율" 전용 어휘. 스프라이트 크기 조절(transforms.js scale-scrub)은
// 대각 리사이즈 아이콘을 쓴다 — 돋보기와 혼동 금지 (수홍 지시 2026-07-20).
const VIEW_ICONS = {
  zoomIn: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">' +
    '<circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.4"/>' +
    '<path d="M7 5.2v3.6M5.2 7h3.6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>' +
    '<path d="M10.4 10.4 14 14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  zoomOut: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">' +
    '<circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.4"/>' +
    '<path d="M5.2 7h3.6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>' +
    '<path d="M10.4 10.4 14 14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  fit: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">' +
    '<path d="M5.5 2.5H2.5v3M10.5 2.5h3v3M5.5 13.5h-3v-3M10.5 13.5h3v-3" ' +
    'fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

// ── 뷰 패닝 — Space+드래그 / 휠버튼(중클릭) 드래그 / 손 툴로 화면을 끈다 (수홍 지시
// 2026-07-17: 스프라이트 이동과 별개인 캔버스 시야 이동. 2026-07-19: 비교뷰에도 +
// 손 툴 버튼 — Space 는 홀드형(누르는 동안만), 버튼은 토글형). 스크롤 컨테이너를
// 당기는 방식이라 페인트/마키보다 먼저(capture) 가로챈다.
let panSpaceHeld = false;

document.addEventListener("keydown", (ev) => {
  if (ev.code !== "Space") return;
  if (!document.getElementById("zoom-modal") && !document.getElementById("compare-modal")) return;
  // 버튼은 가드에서 제외 — 마지막 클릭한 버튼에 포커스가 남아도 Space=팬이 이긴다
  // (포토샵 계약; 버튼 재발동 사고도 같이 막힘). 타이핑 필드만 존중.
  if (ev.target && ev.target.closest && ev.target.closest("input, textarea, select")) return;
  panSpaceHeld = true;
  document.body.classList.add("pan-space");
  ev.preventDefault();
});

document.addEventListener("keyup", (ev) => {
  if (ev.code !== "Space") return;
  panSpaceHeld = false;
  document.body.classList.remove("pan-space");
});

function wirePan(surface, container, isToolActive) {
  surface.addEventListener("pointerdown", (ev) => {
    const toolOn = isToolActive && isToolActive();
    if (!(ev.button === 1 || (ev.button === 0 && (panSpaceHeld || toolOn)))) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const sx = ev.clientX, sy = ev.clientY;
    const sl = container.scrollLeft, st = container.scrollTop;
    const prevCursor = surface.style.cursor;
    surface.style.cursor = "grabbing";
    const onMove = (e2) => {
      container.scrollLeft = sl - (e2.clientX - sx);
      container.scrollTop = st - (e2.clientY - sy);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      surface.style.cursor = prevCursor;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, true);
}

// 콘텐츠 중앙을 뷰포트 중앙으로 (모달 오픈 직후 / 맞춤 버튼)
function centerView(viewport, content) {
  const vr = viewport.getBoundingClientRect();
  const cr = content.getBoundingClientRect();
  viewport.scrollLeft += (cr.left + cr.width / 2) - (vr.left + vr.width / 2);
  viewport.scrollTop += (cr.top + cr.height / 2) - (vr.top + vr.height / 2);
}

// 커서 앵커 줌: apply() 가 콘텐츠 크기를 바꾼 뒤에도 (clientX, clientY) 아래에
// 있던 콘텐츠 지점이 그대로 그 자리에 남게 뷰포트 스크롤을 보정한다.
// 좌표 인자가 없으면 뷰포트 중앙 기준 (버튼 줌 경로).
function keepViewAnchor(viewport, content, clientX, clientY, apply) {
  const vr = viewport.getBoundingClientRect();
  const ax = clientX === null || clientX === undefined ? vr.left + vr.width / 2 : clientX;
  const ay = clientY === null || clientY === undefined ? vr.top + vr.height / 2 : clientY;
  const cr = content.getBoundingClientRect();
  const fx = cr.width ? (ax - cr.left) / cr.width : 0.5;
  const fy = cr.height ? (ay - cr.top) / cr.height : 0.5;
  apply();
  const cr2 = content.getBoundingClientRect();
  viewport.scrollLeft += (cr2.left + fx * cr2.width) - ax;
  viewport.scrollTop += (cr2.top + fy * cr2.height) - ay;
}

// 돋보기 −/+/맞춤 위젯 — 뷰포트 우하단 오버레이와 툴바가 같은 팩토리를 쓴다.
// handlers: { zoomOut(), zoomIn(), fit() } — 라벨 갱신은 반환 요소의 .vn-label 로.
function makeViewNavWidget(handlers, opts) {
  const wrap = document.createElement("span");
  wrap.className = "view-nav" + (opts && opts.corner ? " vn-corner" : "");
  const mkBtn = (cls, icon, tip, fn) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `ghost ${cls}`;
    b.innerHTML = icon;
    b.setAttribute("data-tip", tip);
    b.addEventListener("click", fn);
    // 더블클릭/포인터 계열이 스테이지(모달 열기·페인트)로 새지 않게 차단
    for (const type of ["pointerdown", "dblclick"]) b.addEventListener(type, (ev) => ev.stopPropagation());
    return b;
  };
  wrap.appendChild(mkBtn("vn-out", VIEW_ICONS.zoomOut, t("tViewZoomOut"), handlers.zoomOut));
  const label = document.createElement("span");
  label.className = "vn-label";
  label.textContent = "100%";
  wrap.appendChild(label);
  wrap.appendChild(mkBtn("vn-in", VIEW_ICONS.zoomIn, t("tViewZoomIn"), handlers.zoomIn));
  if (handlers.fit) wrap.appendChild(mkBtn("vn-fit", VIEW_ICONS.fit, t("tViewZoomFit"), handlers.fit));
  return wrap;
}
