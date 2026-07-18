// SPDX-License-Identifier: Apache-2.0
// curator/row-controls.js — 줄 헤더 컨트롤 팩토리 (픽셀 격자/픽셀퍼펙트 토글, GIF 버튼)
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

// 줄 단위 GIF 다운로드 — 이 줄의 현재 시퀀스(선택/순서/변형/픽셀편집)를 서버가
// 그 자리에서 합성해 GIF 원파일로 내려준다 (실시간 계약: 보는 것 = 받는 것).
function makeGifButton(stateName) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "gif-btn";
  btn.title = t("tRowGif");
  btn.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M8 2v8m0 0l-3-3m3 3l3-3M3 13h10" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    `<span>${t("rowGif")}</span>`;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      await downloadArtifact(`gif?state=${encodeURIComponent(stateName)}`,
        STR[lang].rowGifDone(stateName));
    } catch (e) {
      setStatus(t("exportFail") + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  });
  return btn;
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
