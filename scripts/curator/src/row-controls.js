// SPDX-License-Identifier: Apache-2.0
// curator/row-controls.js — 줄 헤더 표시 컨트롤 팩토리 (토글/fps 스텝퍼/리롤 트리거)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

// 줄별 토글 체크박스 공용 팩토리 — refs 줄과 확대 모달이 같은 클래스/핸들러를 쓰므로
// sync*Controls 가 양쪽 인스턴스를 함께 갱신한다 (per-state truth 하나, 표시 N개).
function makeStateToggle(cls, stateName, label, title, checked, onChange) {
  const el = document.createElement("label");
  el.className = "pp-apply row-toggle";
  el.title = title;
  const input = document.createElement("input");
  input.type = "checkbox";
  input.className = cls;
  input.dataset.state = stateName;
  input.checked = checked;
  input.addEventListener("change", (ev) => onChange(ev.target.checked));
  el.appendChild(input);
  el.appendChild(Object.assign(document.createElement("span"), { textContent: label }));
  return el;
}

function makeGridToggle(stateName) {
  return makeStateToggle("grid-state-check", stateName, t("pxGrid"), t("tGridState"),
    !!gridStates[stateName], (checked) => {
      gridStates[stateName] = checked;
      syncGridControls();
      sizePxGrids();
    });
}


// 줄 리롤 버튼 — 같은 행을 한 번 더 생성해 **후보군에 병기** (수홍 2026-07-19).
// primary 를 덮지 않는다: 서버가 rerollN 테이크로 기록하고 전체 배치를 재추출한다.
// UI 는 보간과 같은 생성 트리거 관용구(gen-trigger.js): 클릭 = 팝오버(모델 select
// + 실행). Alt클릭 같은 트리거별 제스처는 두지 않는다.
function makeRerollButton(stateName) {
  const wrap = document.createElement("span");
  wrap.className = "gen-wrap";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "gif-btn reroll-btn";
  btn.title = t("tRowReroll");
  btn.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 1.8v2.7h-2.7" fill="none" ' +
    'stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    `<span>${t("rowReroll")}</span>`;
  const pop = document.createElement("div");
  pop.className = "gen-pop";
  pop.hidden = true;
  const providerSel = makeProviderSelect();
  pop.appendChild(providerSel);
  const go = document.createElement("button");
  go.type = "button";
  go.className = "gif-btn";
  go.textContent = t("genGo");
  pop.appendChild(go);
  btn.addEventListener("click", () => { pop.hidden = !pop.hidden; });
  go.addEventListener("click", () => runServerGeneration({
    url: "/api/reroll",
    body: { state: stateName, provider: providerSel.value },
    goBtn: go,
    buttons: [btn],
    busyMsg: STR[lang].rerollBusy(stateName),
    doneMsg: STR[lang].rerollDone(stateName),
    failPrefix: t("rerollFail"),
  }));
  wrap.appendChild(btn);
  wrap.appendChild(pop);
  return wrap;
}

function makePpToggle(stateName) {
  return makeStateToggle("pp-state-check", stateName, t("ppState"), t("tPpState"),
    ppOn(stateName), (checked) => {
      ppStates[stateName] = checked;
      syncPpControls();
      refreshVariantImages();
      scheduleSave();
    });
}


// 줄 전체 재생 속도 (fps) 스텝퍼 — SSoT 는 sprite-request.json (서버가 원자 수정).
// 프레임별 지속시간은 프레임 복제(아틀라스 셀 공유 = 부하 0)가 담당한다 (수홍 확정
// 2026-07-18 — per-frame duration 방식 폐기). 변경은 프리뷰/호흡 편집기에 즉시 반영.
function makeFpsStepper(stateName) {
  const st = run.states.find((s) => s.name === stateName);
  const wrap = document.createElement("span");
  wrap.className = "fps-stepper";
  wrap.title = t("tFpsStepper");
  const minus = document.createElement("button");
  minus.type = "button";
  minus.textContent = "−";
  const label = document.createElement("span");
  label.className = "fps-value";
  label.textContent = `${(st && st.fps) || 6}fps`;
  const plus = document.createElement("button");
  plus.type = "button";
  plus.textContent = "+";
  const apply = async (next) => {
    const clamped = Math.max(1, Math.min(30, next));
    if (!st || clamped === st.fps) return;
    minus.disabled = plus.disabled = true;
    try {
      const res = await fetch("/api/state-fps", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: stateName, fps: clamped }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      st.fps = clamped; // 모델 즉시 반영 — 프리뷰/에디터 틱이 이 값을 읽는다
      label.textContent = `${clamped}fps`;
      const meta = document.querySelector(`.state[data-state="${cssEscape(stateName)}"] .state-head .meta`);
      if (meta) meta.textContent = meta.textContent.replace(/\d+fps/, `${clamped}fps`);
      setStatus(STR[lang].fpsSet(stateName, clamped));
    } catch (e) {
      setStatus(t("fpsFail") + e.message, "err");
    }
    minus.disabled = plus.disabled = false;
  };
  minus.addEventListener("click", () => apply(((st && st.fps) || 6) - 1));
  plus.addEventListener("click", () => apply(((st && st.fps) || 6) + 1));
  wrap.appendChild(minus);
  wrap.appendChild(label);
  wrap.appendChild(plus);
  return wrap;
}
