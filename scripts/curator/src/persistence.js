// SPDX-License-Identifier: Apache-2.0
// curator/persistence.js — curation.json 저장 (디바운스 autosave + 유실 배너) / 산출물 다운로드
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

let saveTimer = null;

function buildPayload() {
  const states = {};
  for (const [name, entry] of Object.entries(entries)) {
    if (name === BASE_STATE) continue; // 가상 상태 — 큐레이션 사이드카에 절대 저장 금지
    const transforms = {};
    for (const [idx, t] of Object.entries(entry.transforms)) {
      if (t.rotate || t.scale !== 1 || t.dx || t.dy || t.shx || t.shy || t.flipX) transforms[idx] = t;
    }
    // `selected` is the play order (what compose bakes). `order` is the full
    // display order (sequence then pool) so the webview can restore the exact
    // row arrangement on reload — compose/curation.py ignore it.
    states[name] = {
      selected: entry.order.filter((idx) => entry.sel.has(idx)),
      order: entry.order.slice(),
      transforms,
    };
    // 보관함 = 스키마의 deleted (UI 행/굽기 기본값에서 제외 — state_plan SSoT)
    if (entry.archived && entry.archived.length) states[name].deleted = entry.archived.slice();
    // 복제 인스턴스 맵 — order 에 남아 있는 복제만 저장 (제거된 복제는 흔적 없이 정리)
    const liveClones = {};
    for (const [ci, src] of Object.entries(entry.clones || {})) {
      if (entry.order.includes(Number(ci))) liveClones[ci] = src;
    }
    if (Object.keys(liveClones).length) states[name].clones = liveClones;
    // 픽셀 편집 사이드카 (빈 프레임 엔트리는 정리)
    const px = {};
    for (const [i, ops] of Object.entries(entry.pixels || {})) {
      if (ops && Object.keys(ops).length) px[i] = ops;
    }
    if (Object.keys(px).length) states[name].pixels = px;
    // per-state pixel-perfect (the row's own toggle) — only for rows with a twin
    if (ppTwinStates.has(name)) states[name].pixel_perfect = ppOn(name);
    // 호흡 후처리 레이어 (수홍 2026-07-18) — 켠 상태만 기록 (없음 = off)
    if (entry.breathe) states[name].breathe = entry.breathe;
  }
  const payload = { version: run.schemaVersion || 1, kind: "sprite-gen-curation", states };
  // echo the run generation this view was loaded with; the server rejects the autosave
  // (409) if the run was re-imported/re-extracted under this session so stale selections
  // never land on new frames.
  if (run.runRevision) payload.runRevision = run.runRevision;
  // run-wide default field: written only when every twin row agrees (uniform),
  // so a consumer without per-state awareness still bakes the right variant.
  // Mixed rows -> omitted; the per-state values above are the truth.
  if (ppAvailable) {
    const vals = [...ppTwinStates].map((n) => ppOn(n));
    if (vals.every((v) => v === vals[0])) payload.pixel_perfect = vals[0];
  }
  return payload;
}

let lastEditAt = 0;

function scheduleSave() {
  if (zoomView && zoomView.stateName === BASE_STATE) {
    // 베이스 편집은 파일 굽기 대상 — "저장됨" 오해 방지, 명시 버튼으로만 반영
    setStatus(t("baseUnsaved"));
    return;
  }
  lastEditAt = Date.now();
  setStatus(t("editing"));
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 250);
}

async function save() {
  try {
    const res = await fetch("/api/curation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload()),
    });
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    setStatus(t("saved"), "ok");
    hideSaveLostBanner();
  } catch (e) {
    setStatus(t("saveFail") + e.message, "err");
    // 작은 상태줄만으론 그림 그리는 중에 못 본다 (실사고 2026-07-17: 서버 재기동으로
    // 죽은 탭에서 픽셀 편집이 조용히 유실, 수홍 발견). 저장이 실패하면 무시할 수
    // 없는 배너로 알린다 — 편집은 계속 가능하되 "지금 저장 안 되고 있음" 이 보인다.
    showSaveLostBanner(e.message);
  }
}

let saveLostBanner = null;

function showSaveLostBanner(detail) {
  if (!saveLostBanner) {
    saveLostBanner = document.createElement("div");
    saveLostBanner.className = "save-lost-banner";
    document.body.appendChild(saveLostBanner);
  }
  saveLostBanner.textContent = `${t("saveLostBanner")} (${detail})`;
  saveLostBanner.hidden = false;
}

function hideSaveLostBanner() {
  if (saveLostBanner) saveLostBanner.hidden = true;
}

async function downloadArtifact(kind, doneMsg) {
  clearTimeout(saveTimer);
  await save();
  setStatus(t("baking"));
  const res = await fetch(`/download/${kind}`);
  if (!res.ok) {
    let msg = "download failed";
    try {
      const data = await res.json();
      msg = (data.stderr || data.error || msg).trim();
    } catch { /* 비 JSON 에러 응답 — 기본 메시지 유지 */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = res.headers.get("X-Filename") || `${kind}.zip`;
  link.click();
  URL.revokeObjectURL(link.href);
  if (kind === "atlas") await refreshFinalAtlas();  // 방금 재계산된 시트/문서 반영
  setStatus(doneMsg, "ok");
}

for (const [id, kind, done] of [
  ["compose", "atlas", () => t("composeDone")],
  ["export", "pngs", () => STR[lang].exportDone()],
  ["export-gif", "gifs", () => STR[lang].exportGifDone()],
]) {
  document.getElementById(id).addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      await downloadArtifact(kind, done());
    } catch (e) {
      setStatus(t(id === "compose" ? "composeFail" : "exportFail") + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  });
}
