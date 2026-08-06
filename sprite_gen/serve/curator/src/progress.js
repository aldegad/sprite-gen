// SPDX-License-Identifier: Apache-2.0
// curator/progress.js — 오래 걸리는 파이프라인(추출 등)의 퍼센트 진행도 표시
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// 엔진이 .sprite-gen.progress.json 에 상태×단계 단위로 기록한 진행을
// /api/op-progress 로 폴링해 상단 바 + 상태줄 퍼센트로 보여준다
// (수홍 요청 2026-07-18 "잘게잘게 퍼센트로"). 표시 전용 — truth 는 엔진 파일.

let opPollTimer = null;

function topProgressBar() {
  let bar = document.getElementById("op-progress");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "op-progress";
    bar.innerHTML = '<div class="op-progress-fill"></div>';
    document.body.appendChild(bar);
  }
  return bar;
}

function setTopProgress(pct) {
  const bar = topProgressBar();
  const fill = bar.querySelector(".op-progress-fill");
  if (pct === null) { // busy 인데 진행 미기록 — 불확정 표시
    bar.hidden = false;
    fill.classList.add("indeterminate");
    fill.style.width = "100%";
    return;
  }
  fill.classList.remove("indeterminate");
  if (pct <= 0 || pct >= 100) {
    bar.hidden = true;
    fill.style.width = "0%";
    return;
  }
  bar.hidden = false;
  fill.style.width = `${pct}%`;
}

function stopOpProgressWatch() {
  if (opPollTimer) {
    clearInterval(opPollTimer);
    opPollTimer = null;
  }
  setTopProgress(0);
}

// onIdle: busy 가 풀렸을 때 1회 호출 (예: 리로드) — 폴링은 자동 중단
function startOpProgressWatch(onIdle) {
  if (opPollTimer) return;
  const tick = async () => {
    let data = null;
    try {
      const res = await fetch("/api/op-progress");
      data = await res.json();
    } catch {
      return; // 서버 재시작 등 일시 오류 — 다음 틱에 재시도
    }
    if (!data || !data.busy) {
      stopOpProgressWatch();
      if (onIdle) onIdle();
      return;
    }
    const p = data.progress;
    const live = document.getElementById("op-loading-live");
    const panelFill = document.querySelector(".op-loading-fill");
    if (p && p.total) {
      const pct = Math.min(99, Math.round((p.done / p.total) * 100));
      setStatus(STR[lang].opProgress(p, pct));
      setTopProgress(pct);
      if (live) live.textContent = STR[lang].opProgress(p, pct);
      if (panelFill) {
        panelFill.classList.remove("indeterminate");
        panelFill.style.width = `${Math.max(2, pct)}%`;
      }
    } else {
      setTopProgress(null);
      if (live) live.textContent = t("healWaiting");
      if (panelFill) panelFill.classList.add("indeterminate");
    }
  };
  opPollTimer = setInterval(tick, 800);
  tick();
}

// 장기 heal/추출 중 첫 로드용 정식 로딩 패널 — fatal 에러 박스가 아니라
// 진행 바·퍼센트·현재 행·안내 문구를 보여준다 (plan long-op-loading-ux:
// 엔진 갱신 후 첫 탭이 빈 화면으로 수십 분 멈추던 실사고의 클라 반쪽).
function renderLoadingPanel() {
  const host = document.getElementById("states");
  host.innerHTML =
    '<div class="op-loading">' +
    `<div class="op-loading-title">${t("healTitle")}</div>` +
    '<div class="op-loading-bar"><div class="op-loading-fill indeterminate" style="width:100%"></div></div>' +
    `<div id="op-loading-live" class="op-loading-live">${t("healWaiting")}</div>` +
    `<div class="op-loading-note">${t("healNote")}</div>` +
    "</div>";
}
