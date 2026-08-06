// SPDX-License-Identifier: Apache-2.0
// composer/boot.js — bootstrap. Loads /api/state, applies language, wires the
// mount / add-row / lang controls, and renders the blank or mounted view.
// Loaded last (classic scripts share globals via load order in index.html).

function applyStaticLang() {
  document.documentElement.lang = lang;
  document.getElementById("t-title").textContent = t("title");
  document.getElementById("tree-head").textContent = t("treeHead");
  document.getElementById("empty-title").textContent = t("emptyTitle");
  document.getElementById("empty-sub").textContent = t("emptySub");
  document.getElementById("empty-mount").textContent = t("emptyMount");
  document.getElementById("add-row").textContent = t("addRow");
  document.getElementById("lang-toggle").textContent = t("langLabel");
  document.getElementById("hintbar").textContent = t("emptySub");
  const mountBtn = document.getElementById("mount-btn");
  mountBtn.textContent = session.mount ? t("remount") : t("mount");
  const mountLabel = document.getElementById("mount-label");
  mountLabel.textContent = session.mount || t("noMount");
}

async function doMount() {
  const guess = session.mount || "";
  const dir = window.prompt(t("mountPrompt"), guess);
  if (!dir) return;
  try {
    const data = await apiMount(dir.trim());
    session.mount = data.mount;
    applyStaticLang();
    refreshCanvasChrome();
    await loadTree(session.mount);
    // First mount: seed one empty row so the drop target is visible immediately.
    if (session.rows.length === 0) addRow();
    renderRows();
    setStatus(t("ready"), "ok");
  } catch (e) {
    setStatus(t("mountFail", e.message), "err");
  }
}

async function boot() {
  let state = {};
  try { state = await apiGetState(); } catch (_) { /* serve blank */ }
  lang = new URLSearchParams(location.search).get("lang") || state.lang || "en";
  session.mount = state.mount || null;

  applyStaticLang();
  refreshCanvasChrome();

  document.getElementById("mount-btn").addEventListener("click", doMount);
  document.getElementById("empty-mount").addEventListener("click", doMount);
  document.getElementById("add-row").addEventListener("click", () => {
    addRow();
    renderRows();
  });
  document.getElementById("lang-toggle").addEventListener("click", () => {
    lang = lang === "en" ? "ko" : "en";
    const url = new URL(location.href);
    url.searchParams.set("lang", lang);
    history.replaceState(null, "", url);
    applyStaticLang();
    renderRows();
  });

  if (session.mount) {
    await loadTree(session.mount);
    if (session.rows.length === 0) addRow();
    renderRows();
    setStatus(t("ready"));
  }
}

boot();
