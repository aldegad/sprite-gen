// SPDX-License-Identifier: Apache-2.0
// composer/boot.js — bootstrap only. Loads server state, sets language, renders
// the controls (controls.js) and the initial view. Loaded last; classic scripts
// share globals via the load order declared in index.html.

// Static text nodes (headings, empty-state copy, mount label). Interactive
// controls own their own labels via controls.renderControls().
function applyText() {
  document.documentElement.lang = lang;
  document.getElementById("t-title").textContent = t("title");
  document.getElementById("tree-head").textContent = t("treeHead");
  document.getElementById("empty-title").textContent = t("emptyTitle");
  document.getElementById("empty-sub").textContent = t("emptySub");
  document.getElementById("hintbar").textContent = t("emptySub");
  document.getElementById("mount-label").textContent = session.mount || t("noMount");
}

async function boot() {
  let state = {};
  try { state = await apiGetState(); } catch (_) { /* serve blank */ }
  lang = new URLSearchParams(location.search).get("lang") || state.lang || "en";
  session.mount = state.mount || null;

  applyText();
  renderControls();
  refreshCanvasChrome();

  if (session.mount) {
    await loadTree(session.mount);
    if (session.rows.length === 0) addRow();
    renderRows();
    setStatus(t("ready"));
  }
}

boot();
