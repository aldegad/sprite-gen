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
    if (p && p.total) {
      const pct = Math.min(99, Math.round((p.done / p.total) * 100));
      setStatus(STR[lang].opProgress(p, pct));
      setTopProgress(pct);
    } else {
      setTopProgress(null);
    }
  };
  opPollTimer = setInterval(tick, 800);
  tick();
}
