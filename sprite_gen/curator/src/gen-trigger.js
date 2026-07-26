// SPDX-License-Identifier: Apache-2.0
// curator/gen-trigger.js — 서버측 생성 트리거의 단일 관용구 (표면 계약 SSoT)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// 모든 "서버에서 생성이 도는 버튼"(보간/리롤, 이후 추가되는 것 포함)은 같은 계약을 탄다:
//   버튼 클릭 = 파라미터 팝오버(.gen-pop) 토글 → 팝오버 안 공용 모델 select → 실행 버튼
//   실행 = 스피너(.gen-spin) + 진행도 워치 + POST + 에러 언랩 + 완료 시 뷰 새로고침
// 모델 표기(GPT/Grok ↔ codex/grok)와 실행 시퀀스는 이 파일만 소유한다. 트리거마다
// 다른 제스처(Alt클릭 모델 선택 등)를 만들지 않는다 — 실사고 2026-07-19 수홍
// "보간은 클릭하면 모델선택이고 리롤은 alt클릭이고 다 지멋대로".

const GEN_PROVIDERS = [
  { value: "codex", label: "GPT" },
  { value: "grok", label: "Grok" },
];

// 공용 모델 선택 위젯 — 표기/순서/기본값(codex)의 유일한 자리
function makeProviderSelect() {
  const sel = document.createElement("select");
  for (const p of GEN_PROVIDERS) {
    const opt = document.createElement("option");
    opt.value = p.value;
    opt.textContent = p.label;
    sel.appendChild(opt);
  }
  return sel;
}

// 공용 실행 시퀀스 — 성공 시 run 세대가 바뀌므로 항상 뷰를 새로고침한다.
// 실패 시 버튼/라벨을 복구하고 진행도 워치를 멈춘다 (조용한 부분 성공 없음).
async function runServerGeneration({ url, body, goBtn, buttons, busyMsg, doneMsg, failPrefix }) {
  const goLabel = goBtn.textContent;
  const all = [goBtn, ...(buttons || [])];
  for (const b of all) b.disabled = true;
  goBtn.innerHTML = '<span class="gen-spin" aria-label="generating"></span>';
  setStatus(busyMsg);
  try {
    startOpProgressWatch(); // 생성 후 전체 배치 재추출 — 진행도 퍼센트 표시
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || (data.stderr || "").trim().split("\n").pop() || res.status);
    }
    setStatus(doneMsg, "ok");
    setTimeout(() => window.location.reload(), 800);
  } catch (e) {
    stopOpProgressWatch();
    setStatus(failPrefix + e.message, "err");
    goBtn.textContent = goLabel;
    for (const b of all) b.disabled = false;
  }
}
