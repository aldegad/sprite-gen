// SPDX-License-Identifier: Apache-2.0
// curator/tween.js — AI 중간 프레임 보간 (프레임 쌍 픽 + 테이크 생성 요청)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

// AI 중간 프레임(보간) — 이 줄의 두 프레임 사이를 RIFE 로 보간해 테이크로 기록하고
// 전체 배치를 재추출한다. 부분 추출은 서버가 제공하지 않는다 (팔레트 배치 결합 —
// docs/frame-interpolation.md). 완료되면 run 세대가 바뀌므로 뷰를 새로고침한다.
//
// 픽 모드: 팝오버가 열려 있는 동안 그 줄의 카드 클릭 = 보간 쌍 선택 (파란 테두리,
// 최대 2개 FIFO — 세 번째 클릭이 가장 오래된 픽을 대체). 기존 선택(.selected)의
// 파란 테두리는 픽 모드 동안 그 줄에서 억제해 픽만 파랗게 보인다.
let tweenOpen = null; // {stateName, pop, btn, picks, fromInput, toInput, section}

function closeTweenPick() {
  if (!tweenOpen) return;
  tweenOpen.pop.hidden = true;
  if (tweenOpen.section) {
    tweenOpen.section.classList.remove("tween-picking");
    tweenOpen.section.querySelectorAll(".card.tween-picked")
      .forEach((el) => el.classList.remove("tween-picked"));
  }
  tweenOpen = null;
}

document.addEventListener("click", (ev) => {
  if (!tweenOpen) return;
  const card = ev.target.closest(".card");
  if (!card || card.dataset.state !== tweenOpen.stateName) return;
  if (ev.target.closest(".gen-pop")) return;
  ev.preventDefault();
  ev.stopPropagation();
  const idx = Number(card.dataset.idx);
  const at = tweenOpen.picks.findIndex((p) => p.idx === idx);
  if (at >= 0) {
    tweenOpen.picks.splice(at, 1)[0].card.classList.remove("tween-picked");
  } else {
    tweenOpen.picks.push({ idx, card });
    card.classList.add("tween-picked");
    if (tweenOpen.picks.length > 2) {
      tweenOpen.picks.shift().card.classList.remove("tween-picked");
    }
  }
  const [a, b] = tweenOpen.picks;
  if (a) tweenOpen.fromInput.value = a.idx;
  if (b) tweenOpen.toInput.value = b.idx;
}, true);

function makeTweenButton(stateName) {
  const wrap = document.createElement("span");
  wrap.className = "gen-wrap";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "gif-btn";
  btn.title = t("tRowTween");
  btn.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M2 8h2.5m7 0H14M8 5.5v5M5.5 8a2.5 2.5 0 115 0 2.5 2.5 0 01-5 0z" fill="none" ' +
    'stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>' +
    `<span>${t("rowTween")}</span>`;
  const pop = document.createElement("div");
  pop.className = "gen-pop";
  pop.hidden = true;
  const field = (label, value, step, min, max) => {
    const lab = document.createElement("label");
    const input = document.createElement("input");
    input.type = "number";
    input.value = value;
    input.step = step;
    input.min = min;
    if (max !== undefined) input.max = max;
    lab.append(label, input);
    pop.appendChild(lab);
    return input;
  };
  const fromInput = field(t("tweenFrom"), 0, 1, 0);
  const toInput = field(t("tweenTo"), 1, 1, 0);
  const tInput = field(t("tweenT"), 0.5, 0.05, 0.05, 0.95);
  const providerSel = makeProviderSelect(); // 모델 선택/실행 관용구 SSoT = gen-trigger.js
  pop.appendChild(providerSel);
  const go = document.createElement("button");
  go.type = "button";
  go.className = "gif-btn";
  go.textContent = t("genGo");
  pop.appendChild(go);
  btn.addEventListener("click", () => {
    if (tweenOpen && tweenOpen.pop === pop) {
      closeTweenPick();
      return;
    }
    closeTweenPick();
    pop.hidden = false;
    const section = document.querySelector(`.state[data-state="${CSS.escape(stateName)}"]`);
    if (section) section.classList.add("tween-picking");
    tweenOpen = { stateName, pop, btn, picks: [], fromInput, toInput, section };
  });
  go.addEventListener("click", () => runServerGeneration({
    url: "/api/interpolate",
    body: {
      state: stateName,
      from: parseInt(fromInput.value, 10),
      to: parseInt(toInput.value, 10),
      t: parseFloat(tInput.value),
      provider: providerSel.value,
    },
    goBtn: go,
    buttons: [btn],
    busyMsg: t("tweenBusy"),
    doneMsg: STR[lang].tweenDone(stateName),
    failPrefix: t("tweenFail"),
  }));
  wrap.appendChild(btn);
  wrap.appendChild(pop);
  return wrap;
}
