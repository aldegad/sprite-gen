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

// 줄 단위 저장 — 공용 저장 팝오버 (수홍 2026-07-19 "row 저장버튼도 공용저장버튼으로"):
// GIF = 서버 굽기(실시간 계약: 보는 것 = 받는 것, 4x 니어리스트), WebM(투명)/MP4(흰
// 배경) = 비교뷰와 같은 클라 결정론 샘플 → /api/compare-gif 서버 조립.
function makeGifButton(stateName) {
  const wrap = document.createElement("span");
  wrap.className = "dl-wrap row-dl-wrap";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "gif-btn";
  btn.title = t("tRowDl");
  btn.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M8 2v8m0 0l-3-3m3 3l3-3M3 13h10" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    `<span>${t("cmpDl")} ▾</span>`;
  const menu = document.createElement("div");
  menu.className = "dl-menu";
  menu.hidden = true;
  menu.innerHTML =
    `<button type="button" data-fmt="gif">GIF</button>` +
    `<button type="button" data-fmt="webm">WebM · ${t("cmpDlAlpha")}</button>` +
    `<button type="button" data-fmt="mp4">MP4 · ${t("cmpDlWhite")}</button>`;
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    menu.hidden = !menu.hidden;
  });
  document.addEventListener("click", () => { menu.hidden = true; });
  const download = async (fmt) => {
    btn.disabled = true;
    try {
      if (fmt === "gif") {
        await downloadArtifact(`gif?state=${encodeURIComponent(stateName)}`,
          STR[lang].rowGifDone(stateName));
      } else {
        await downloadRowVideo(stateName, fmt);
        setStatus(STR[lang].rowDlDone(stateName, fmt), "ok");
      }
    } catch (e) {
      setStatus(t("exportFail") + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  };
  menu.querySelectorAll("button[data-fmt]").forEach((b) =>
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      menu.hidden = true;
      download(b.dataset.fmt);
    }));
  wrap.appendChild(btn);
  wrap.appendChild(menu);
  return wrap;
}

// WebM/MP4 = 줄 재생 루프 1회를 클라에서 결정론 합성(캐노니컬+변형+픽셀편집+호흡,
// 4x 니어리스트)해 서버(ffmpeg)가 조립한다 — 비교뷰 다운로드와 같은 경로.
async function downloadRowVideo(stateName, fmt) {
  const play = playList(stateName);
  if (!play.length) throw new Error("empty sequence");
  const st = run.states.find((s) => s.name === stateName);
  const [cellW, cellH] = cellDims(stateName);
  const bcfg = stateBreathe(stateName);
  const pattern = bcfg ? breathePattern(bcfg, play.length) : [];
  const scale = 4;
  const frames = [];
  for (let i = 0; i < play.length; i++) {
    const f = frameOf(stateName, play[i]);
    const image = f ? img(f.url) : null;
    if (!(image && image.complete && image.naturalWidth)) throw new Error("frame images still loading");
    const base = document.createElement("canvas");
    base.width = cellW;
    base.height = cellH;
    const bx = base.getContext("2d");
    bx.imageSmoothingEnabled = false;
    drawFrameInto(bx, image, getTransform(stateName, play[i]), cellW, cellH,
      snapScaleFor(stateName), getPixelOps(stateName, play[i]));
    const composed = bcfg ? breatheComposite(base, bcfg, pattern[i] || 0) : base;
    const out = document.createElement("canvas");
    out.width = cellW * scale;
    out.height = cellH * scale;
    const ox = out.getContext("2d");
    ox.imageSmoothingEnabled = false;
    ox.drawImage(composed, 0, 0, out.width, out.height);
    frames.push(out.toDataURL("image/png"));
  }
  const res = await fetch("/api/compare-gif", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      frames,
      duration_ms: Math.round(1000 / Math.max(1, (st && st.fps) || 6)),
      format: fmt,
    }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || res.status);
  }
  const blob = await res.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${run.characterId || "row"}-${stateName}.${fmt}`;
  link.click();
  URL.revokeObjectURL(link.href);
}

// 줄 리롤 버튼 — 같은 행을 한 번 더 생성해 **후보군에 병기** (수홍 2026-07-19).
// primary 를 덮지 않는다: 서버가 rerollN 테이크로 기록하고 전체 배치를 재추출한다.
// 클릭 = codex, Alt+클릭 = grok. 완료되면 run 세대가 바뀌므로 뷰를 새로고침한다.
function makeRerollButton(stateName) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "gif-btn reroll-btn";
  btn.title = t("tRowReroll");
  const idle =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 1.8v2.7h-2.7" fill="none" ' +
    'stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    `<span>${t("rowReroll")}</span>`;
  btn.innerHTML = idle;
  btn.addEventListener("click", async (ev) => {
    btn.disabled = true;
    btn.innerHTML = '<span class="tween-spin" aria-label="generating"></span>';
    setStatus(STR[lang].rerollBusy(stateName));
    try {
      startOpProgressWatch(); // 생성 후 전체 재추출 — 진행도 퍼센트 표시
      const res = await fetch("/api/reroll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: stateName, provider: ev.altKey ? "grok" : "codex" }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || (data.stderr || "").trim().split("\n").pop() || res.status);
      }
      setStatus(STR[lang].rerollDone(stateName));
      setTimeout(() => window.location.reload(), 800);
    } catch (e) {
      stopOpProgressWatch();
      setStatus(t("rerollFail") + e.message, "err");
      btn.innerHTML = idle;
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
