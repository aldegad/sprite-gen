// SPDX-License-Identifier: Apache-2.0
// curator/gen-trigger.js — 서버측 생성 트리거의 단일 관용구 (표면 계약 SSoT)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// 모든 "서버에서 생성이 도는 버튼"(보간/리롤, 이후 추가되는 것 포함)은 같은 계약을 탄다:
//   버튼 클릭 = 파라미터 팝오버(.gen-pop) 토글 → 팝오버 안 공용 모델 select → 실행 버튼
//   실행 = 스피너(.gen-spin) + 진행도 워치 + POST + 에러 언랩 + 완료 시 뷰 새로고침
// 모델 표기(GPT/Grok ↔ codex/grok)와 실행 시퀀스는 이 파일만 소유한다. 트리거마다
// 다른 제스처(Alt클릭 모델 선택 등)를 만들지 않는다 — 회귀 2026-07-19 maintainer
// "보간은 클릭하면 모델선택이고 리롤은 alt클릭이고 다 지멋대로".

// 서버의 sprite_gen.gen.PROVIDERS 와 같은 집합 (최종 검증은 언제나 서버).
// `row: false` = 다중 포즈 행 한 장을 통째로 만들지 못하는 provider — 서버의
// ROW_PROVIDERS 와 같은 구분이다. agy 는 4포즈 프롬프트가 자체 5분 타임아웃을
// 미완료로 넘기고 ~240k 토큰을 쓰고 파일을 남기지 않았다 (2026-09-12 실측), 반면
// 단일 피사체는 ~27~65초에 끝난다. 그래서 보간(프레임 1장)에는 뜨고 리롤(행 1줄)에는
// 뜨지 않는다. 목록 마지막인 이유는 셋 중 가장 느려서다.
const GEN_PROVIDERS = [
  { value: "codex", label: "GPT", row: true },
  { value: "grok", label: "Grok", row: true },
  { value: "agy", label: "Antigravity", row: false },
];

// 공용 모델 선택 위젯 — 표기/순서/기본값(codex)의 유일한 자리.
// `{ rowCapableOnly: true }` 를 주면 행 생성 트리거(리롤)용으로 좁힌다: 눌러봐야
// 서버가 400 으로 거절할 선택지를 애초에 보여주지 않는다.
function makeProviderSelect(options) {
  const rowCapableOnly = Boolean(options && options.rowCapableOnly);
  const sel = document.createElement("select");
  for (const p of GEN_PROVIDERS) {
    if (rowCapableOnly && !p.row) continue;
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
