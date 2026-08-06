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
  document.getElementById("empty-mount-img").textContent = t("openImage");
  document.getElementById("mount-img-btn").textContent = t("openImage");
  document.getElementById("add-row").textContent = t("addRow");
  document.getElementById("build-btn").textContent = t("build");
  document.getElementById("open-cur-btn").textContent = t("openCuration");
  document.getElementById("lang-toggle").textContent = t("langLabel");
  document.getElementById("hintbar").textContent = t("emptySub");
  const mountBtn = document.getElementById("mount-btn");
  mountBtn.textContent = session.mount ? t("remount") : t("mount");
  const mountLabel = document.getElementById("mount-label");
  mountLabel.textContent = session.mount || t("noMount");
}

async function resolveFolder(kind) {
  // Prefer the native OS chooser; fall back to a path prompt only where the native
  // dialog is unavailable (non-macOS) — an explicit, observable path. For kind
  // "image" the server returns the picked image's parent folder as `dir`.
  try {
    const picked = await apiPick(kind);
    if (picked.cancelled) return null;
    return picked.dir;
  } catch (e) {
    if (e.code === "unsupported-platform") {
      const typed = window.prompt(t("mountPrompt"), session.mount || "");
      return typed ? typed.trim() : null;
    }
    throw e;
  }
}

async function doMount(kind = "folder") {
  let dir;
  try {
    dir = await resolveFolder(kind);
  } catch (e) {
    setStatus(t("mountFail", e.message), "err");
    return;
  }
  if (!dir) return;
  try {
    const data = await apiMount(dir);
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

function suggestOutDir() {
  // A sibling of the opened folder, named after it — easy to find, does not
  // write inside the read-only library.
  const m = (session.mount || "").replace(/\/+$/, "");
  return m ? `${m}-sprite` : "";
}

async function doBuild() {
  const hasFrames = session.rows.some((r) => r.cells.length > 0);
  if (!hasFrames) {
    setStatus(t("needRows"), "err");
    return;
  }
  const outDir = window.prompt(t("buildPrompt"), suggestOutDir());
  if (!outDir) return;
  const result = document.getElementById("build-result");
  const buildBtn = document.getElementById("build-btn");
  const openBtn = document.getElementById("open-cur-btn");
  buildBtn.disabled = true;
  result.className = "build-result";
  result.textContent = t("building");
  try {
    const data = await apiBuild(outDir.trim());
    result.className = "build-result ok";
    result.textContent = t("buildDone", data.states.length, data.frames) + " · " + data.runDir;
    openBtn.hidden = false;
    openBtn.onclick = () => doOpenCuration(data.runDir);
    setStatus(t("ready"), "ok");
  } catch (e) {
    result.className = "build-result err";
    result.textContent = t("buildFail", e.message);
    setStatus(t("buildFail", e.message), "err");
  } finally {
    buildBtn.disabled = false;
  }
}

async function doOpenCuration(runDir) {
  const openBtn = document.getElementById("open-cur-btn");
  openBtn.disabled = true;
  setStatus(t("opening"));
  try {
    const data = await apiOpenCuration(runDir);
    window.open(data.url, "_blank");
    setStatus(t("ready"), "ok");
  } catch (e) {
    setStatus(t("openFail", e.message), "err");
  } finally {
    openBtn.disabled = false;
  }
}

async function boot() {
  let state = {};
  try { state = await apiGetState(); } catch (_) { /* serve blank */ }
  lang = new URLSearchParams(location.search).get("lang") || state.lang || "en";
  session.mount = state.mount || null;

  applyStaticLang();
  refreshCanvasChrome();

  document.getElementById("mount-btn").addEventListener("click", () => doMount("folder"));
  document.getElementById("empty-mount").addEventListener("click", () => doMount("folder"));
  document.getElementById("mount-img-btn").addEventListener("click", () => doMount("image"));
  document.getElementById("empty-mount-img").addEventListener("click", () => doMount("image"));
  document.getElementById("add-row").addEventListener("click", () => {
    addRow();
    renderRows();
  });
  document.getElementById("build-btn").addEventListener("click", doBuild);
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
