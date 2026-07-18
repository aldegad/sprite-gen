// SPDX-License-Identifier: Apache-2.0
// curator/atlas.js — 최종 아틀라스 섹션 (시트 + manifest 뷰)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

// 아틀라스는 다운로드/합성 시점의 산출물이라 계산 시각을 함께 표시한다 — 큐레이션을
// 더 만졌으면 다음 다운로드가 다시 계산한다 (버튼 = 라이브 상태의 다운로드 계약).
async function renderFinalAtlas(info) {
  let section = document.getElementById("final-atlas");
  if (!section) {
    section = document.createElement("div");
    section.id = "final-atlas";
    section.className = "state final-atlas";
    document.getElementById("states").appendChild(section);
  }
  if (!info) {
    section.innerHTML =
      `<div class="state-head"><span class="name">${t("treeAtlas")}</span></div>` +
      `<div class="atlas-pending">${t("atlasPending")}</div>`;
    return;
  }
  const stamp = STR[lang].atlasStamp(new Date(info.mtime * 1000).toLocaleString());
  let docHtml = "";
  if (info.manifestUrl) {
    try {
      const res = await fetch(info.manifestUrl);
      const doc = await res.json();
      docHtml = `<pre class="atlas-doc-json">${escapeHtml(JSON.stringify(doc, null, 2))}</pre>`;
    } catch {
      docHtml = `<pre class="atlas-doc-json">manifest.json 읽기 실패</pre>`;
    }
  }
  section.innerHTML =
    `<div class="state-head"><span class="name">${t("treeAtlas")}</span>` +
    `<span class="meta">sprite-sheet-alpha.png · ${escapeHtml(stamp)}</span>` +
    `<button type="button" class="ghost atlas-bake-btn" data-tip="${t("tAtlasBake")}">${t("atlasBake")}</button></div>` +
    `<div class="atlas-split">` +
    `<a class="atlas-sheet" href="${escapeHtml(info.url)}" target="_blank">` +
    `<img src="${escapeHtml(info.url)}" alt="final atlas" /></a>` +
    (docHtml
      ? `<div class="atlas-doc"><div class="atlas-doc-head">${t("atlasDoc")}</div>${docHtml}</div>`
      : "") +
    `</div>`;
  const bakeBtn = section.querySelector(".atlas-bake-btn");
  // 스테일 관측성 (수홍 2026-07-19 "아틀라스가 내가 편집한 게 아닌"): 시트는
  // 마지막 굽기 시점의 산출물이다 — 그 뒤 편집이 있으면 버튼에 배지로 알린다.
  if (typeof lastEditAt !== "undefined" && lastEditAt > info.mtime * 1000) markAtlasStale();
  if (bakeBtn) bakeBtn.addEventListener("click", async () => {
    bakeBtn.disabled = true;
    bakeBtn.textContent = t("atlasBaking");
    try {
      await flushSave(); // 서버는 디스크 truth 를 굽는다 — 대기 중 편집 저장부터
      const res = await fetch("/api/compose", { method: "POST" });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      await refreshFinalAtlas();
      setStatus(t("atlasBaked"), "ok");
    } catch (e) {
      setStatus(t("composeFail") + e.message, "err");
      bakeBtn.disabled = false;
      bakeBtn.textContent = t("atlasBake");
    }
  });
  syncAtlasDocHeight();
}

// JSON 패널 높이 = 아틀라스 시트 높이 (수홍 지시 2026-07-17: 기준은 아틀라스 —
// stretch 는 JSON 이 길면 반대로 JSON 이 기준이 돼 버린다). 이미지 로드 후 실측으로
// 고정하고, 창 리사이즈 때 다시 잰다. JSON 이 더 길면 패널 안에서 스크롤.
function syncAtlasDocHeight() {
  const section = document.getElementById("final-atlas");
  if (!section) return;
  const sheetImg = section.querySelector(".atlas-sheet img");
  const doc = section.querySelector(".atlas-doc");
  if (!sheetImg || !doc) return;
  const apply = () => {
    const h = section.querySelector(".atlas-sheet").offsetHeight;
    if (h > 0) doc.style.height = h + "px";
  };
  if (sheetImg.complete && sheetImg.naturalWidth) apply();
  else sheetImg.addEventListener("load", apply, { once: true });
  if (!syncAtlasDocHeight._wired) {
    syncAtlasDocHeight._wired = true;
    window.addEventListener("resize", () => {
      clearTimeout(syncAtlasDocHeight._t);
      syncAtlasDocHeight._t = setTimeout(syncAtlasDocHeight, 120);
    });
  }
}

// 다운로드가 아틀라스/manifest 를 다시 계산했을 수 있으니 섹션을 최신 파일로 갱신
// ── 자동 굽기 (수홍 2026-07-19 "아틀라스 실시간 반영"): 사이드카 저장이 디스크에
// 앉을 때마다 디바운스로 compose 를 재실행해 섹션을 갱신한다. compose 실측 ~80ms 라
// 실시간으로 돌려도 부담 없음. 굽는 중 새 편집이 오면 끝나고 한 번 더 (코얼레싱).
// 수동 "지금 상태로 굽기" 버튼은 즉시 실행용으로 유지.
let autoBakeTimer = null;
let autoBakeRunning = false;
let autoBakeDirty = false;
async function autoBakeAtlas() {
  if (autoBakeRunning) { autoBakeDirty = true; return; }
  autoBakeRunning = true;
  try {
    const res = await fetch("/api/compose", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.ok) await refreshFinalAtlas();
    else autoBakeDirty = true; // 서버 바쁨(503)/일시 실패 — 다음 스케줄에 재시도
  } catch {
    autoBakeDirty = true;
  }
  autoBakeRunning = false;
  if (autoBakeDirty) { autoBakeDirty = false; scheduleAutoBake(); }
}
function scheduleAutoBake() {
  clearTimeout(autoBakeTimer);
  autoBakeTimer = setTimeout(autoBakeAtlas, 800);
}

// 편집 직후 호출 (scheduleSave) — "편집 이후 안 구움" 을 버튼 배지로 보이게 한다.
// (자동 굽기가 곧 갱신하므로 배지는 굽기 완료까지의 과도 상태 표시가 된다.)
function markAtlasStale() {
  const btn = document.querySelector(".atlas-bake-btn");
  if (btn && !btn.disabled && !btn.classList.contains("stale")) {
    btn.classList.add("stale");
    btn.textContent = `${t("atlasBake")} · ${t("atlasStale")}`;
  }
}

async function refreshFinalAtlas() {
  const now = Math.floor(Date.now() / 1000);
  renderFinalAtlas({
    url: `/run/sprite-sheet-alpha.png?v=${now}`,
    mtime: now,
    manifestUrl: `/run/manifest.json?v=${now}`,
  });
}
