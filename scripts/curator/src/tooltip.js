// SPDX-License-Identifier: Apache-2.0
// curator/tooltip.js — 공통 툴팁 컴포넌트 (네이티브 title 대체)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

// 단일 팝오버를 body 에 두고 document 위임으로 재사용한다. 대상은 `data-tip`
// 속성으로 opt-in (title= 대신). `data-tip-copy` 가 있으면 팝오버가 인터랙티브
// 해져(pointer-events auto + user-select text) 마우스를 팝오버 안으로 옮겨 텍스트를
// 드래그·복사할 수 있다 — 에이전트 협업 시 프레임 풀네임을 그대로 집어가게 하려는 것.
// 네이티브 title 은 커스텀과 겹쳐 이중 표시되므로 쓰지 않는다.
const Tooltip = (() => {
  let el = null;
  let copyEl = null;
  let anchor = null;
  let hideTimer = 0;

  function ensure() {
    if (el) return;
    el = document.createElement("div");
    el.id = "sg-tip";
    el.setAttribute("role", "tooltip");
    document.body.appendChild(el);
    // 팝오버 자체에 마우스가 들어오면 유지(복사 가능), 나가면 숨김
    el.addEventListener("pointerenter", () => clearTimeout(hideTimer));
    el.addEventListener("pointerleave", hide);
  }

  function position(target) {
    const r = target.getBoundingClientRect();
    el.style.maxWidth = Math.min(360, window.innerWidth - 16) + "px";
    // 먼저 보이게 해 크기 측정, 그 다음 위치 클램프
    el.style.visibility = "hidden";
    el.classList.add("open");
    const tr = el.getBoundingClientRect();
    let top = r.bottom + 6;
    if (top + tr.height > window.innerHeight - 6) top = Math.max(6, r.top - tr.height - 6);
    let left = r.left;
    if (left + tr.width > window.innerWidth - 6) left = Math.max(6, window.innerWidth - 6 - tr.width);
    el.style.top = Math.round(top) + "px";
    el.style.left = Math.round(left) + "px";
    el.style.visibility = "";
  }

  function show(target) {
    const text = target.getAttribute("data-tip");
    if (!text) return;
    ensure();
    clearTimeout(hideTimer);
    anchor = target;
    const copyable = target.hasAttribute("data-tip-copy");
    el.className = copyable ? "open copyable" : "open";
    if (copyable) {
      el.innerHTML = "";
      copyEl = document.createElement("span");
      copyEl.className = "sg-tip-text";
      copyEl.textContent = text;
      el.appendChild(copyEl);
    } else {
      el.textContent = text;
      copyEl = null;
    }
    position(target);
  }

  function hide() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (el) el.classList.remove("open");
      anchor = null;
    }, 80);
  }

  // 위임: 어떤 요소든 data-tip 이 있으면 자동 적용 (공통 컴포넌트)
  document.addEventListener("pointerover", (e) => {
    const target = e.target.closest?.("[data-tip]");
    if (target && target !== anchor) show(target);
  });
  document.addEventListener("pointerout", (e) => {
    const target = e.target.closest?.("[data-tip]");
    // 팝오버(복사가능)로 이동 중이면 유지 — pointerleave 가 최종 숨김을 담당
    if (target && target === anchor && !e.relatedTarget?.closest?.("#sg-tip")) hide();
  });
  // 드래그/클릭이 시작되면 즉시 숨김 (제목 드래그 = 순서변경과 충돌 방지)
  document.addEventListener("pointerdown", (e) => {
    if (!e.target.closest?.("#sg-tip")) { if (el) el.classList.remove("open"); anchor = null; }
  }, true);
  window.addEventListener("scroll", () => { if (el) el.classList.remove("open"); anchor = null; }, true);

  return { show, hide };
})();
