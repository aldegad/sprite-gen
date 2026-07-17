// SPDX-License-Identifier: Apache-2.0
// curator/chrome.js — 페이지 크롬 — 사이드바 접기, 상단 토글 버튼
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

const sidebarToggle = document.getElementById("sidebar-toggle");
// topbar 실측 높이 → 사이드바 sticky 오프셋/전체높이 계산에 주입.
// 라벨/폰트가 늦게 차면서 높이가 변하므로 ResizeObserver 로 추적한다 (일회 측정 금지).
{
  const topbar = document.querySelector(".topbar");
  if (topbar) {
    const sync = () =>
      document.documentElement.style.setProperty("--topbar-h", `${topbar.offsetHeight}px`);
    sync();
    new ResizeObserver(sync).observe(topbar);
  }
}

function applySidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  try { localStorage.setItem("curator-sidebar-collapsed", collapsed ? "1" : ""); } catch { /* private mode */ }
}
if (sidebarToggle) {
  sidebarToggle.addEventListener("click", () =>
    applySidebarCollapsed(!document.body.classList.contains("sidebar-collapsed")));
  let saved = "";
  try { saved = localStorage.getItem("curator-sidebar-collapsed") || ""; } catch { /* private mode */ }
  applySidebarCollapsed(saved === "1");
}

const gridToggle = document.getElementById("grid-toggle");

const langToggle = document.getElementById("lang-toggle");
gridToggle.addEventListener("click", () => {
  const on = document.body.classList.toggle("show-grid");
  gridToggle.textContent = `${t("groundGrid")} ${on ? "▣" : "▢"}`;
  if (on) document.querySelectorAll(".stage").forEach(drawGroundGrid);
});

// language toggle reloads with ?lang= so preview rAF loops are not duplicated
langToggle.addEventListener("click", () => {
  const next = lang === "en" ? "ko" : "en";
  const u = new URL(location.href);
  u.searchParams.set("lang", next);
  location.href = u.toString();
});
