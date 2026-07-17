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
    `<span class="meta">sprite-sheet-alpha.png · ${escapeHtml(stamp)}</span></div>` +
    `<div class="atlas-split">` +
    `<a class="atlas-sheet" href="${escapeHtml(info.url)}" target="_blank">` +
    `<img src="${escapeHtml(info.url)}" alt="final atlas" /></a>` +
    (docHtml
      ? `<div class="atlas-doc"><div class="atlas-doc-head">${t("atlasDoc")}</div>${docHtml}</div>`
      : "") +
    `</div>`;
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
async function refreshFinalAtlas() {
  const now = Math.floor(Date.now() / 1000);
  renderFinalAtlas({
    url: `/run/sprite-sheet-alpha.png?v=${now}`,
    mtime: now,
    manifestUrl: `/run/manifest.json?v=${now}`,
  });
}
