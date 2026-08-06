// SPDX-License-Identifier: Apache-2.0
// composer/controls.js — the app's action controls, rendered from single sources.
//
// Every button here goes through ui.button (one shape, one size). The open actions
// are defined ONCE and rendered into both the toolbar and the empty state, so the
// two placements cannot drift apart — the structural reason two "Open" buttons can
// no longer differ in height.

const OPEN_ACTIONS = [
  { kind: "folder", labelKey: "mount", remountKey: "remount" },
  { kind: "image", labelKey: "openImage" },
];

function openActionButtons() {
  return OPEN_ACTIONS.map((a) => {
    const label = a.kind === "folder" && session.mount ? t(a.remountKey) : t(a.labelKey);
    return button({ label, onClick: () => doMount(a.kind) });
  });
}

function renderToolbar() {
  const bar = document.getElementById("toolbar");
  const status = document.getElementById("status"); // keep the live status node
  bar.innerHTML = "";
  bar.appendChild(status);
  for (const b of openActionButtons()) bar.appendChild(b);
  bar.appendChild(button({ label: t("langLabel"), variant: "ghost", onClick: toggleLang }));
}

function renderEmptyActions() {
  const c = document.getElementById("empty-actions");
  c.innerHTML = "";
  for (const b of openActionButtons()) c.appendChild(b);
}

function renderCanvasActions() {
  const c = document.getElementById("canvas-actions");
  c.innerHTML = "";
  c.appendChild(button({
    id: "add-row", label: t("addRow"), variant: "dashed",
    hidden: !session.mount,
    onClick: () => { addRow(); renderRows(); },
  }));
}

function renderBuildBar() {
  const bar = document.getElementById("build-bar");
  bar.innerHTML = "";
  bar.appendChild(button({ id: "build-btn", label: t("build"), variant: "accent", onClick: doBuild }));
  bar.appendChild(button({ id: "open-cur-btn", label: t("openCuration"), hidden: true }));
  const res = document.createElement("span");
  res.id = "build-result";
  res.className = "build-result";
  bar.appendChild(res);
}

function renderControls() {
  renderToolbar();
  renderEmptyActions();
  renderCanvasActions();
  renderBuildBar();
}

function toggleLang() {
  lang = lang === "en" ? "ko" : "en";
  const url = new URL(location.href);
  url.searchParams.set("lang", lang);
  history.replaceState(null, "", url);
  applyText();
  renderControls();
  renderRows();
}

// ── open / mount ──────────────────────────────────────────────────
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
    applyText();
    renderToolbar();       // folder button flips to "change folder"
    renderCanvasActions(); // add-row becomes visible
    refreshCanvasChrome();
    await loadTree(session.mount);
    if (session.rows.length === 0) addRow(); // seed one row so the drop target shows
    renderRows();
    setStatus(t("ready"), "ok");
  } catch (e) {
    setStatus(t("mountFail", e.message), "err");
  }
}

// ── build / handoff ───────────────────────────────────────────────
function suggestOutDir() {
  const m = (session.mount || "").replace(/\/+$/, "");
  return m ? `${m}-sprite` : "";
}

async function doBuild() {
  if (!sessionHasFrames()) {
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
    const data = await apiBuild(outDir.trim(), sessionBuildRows());
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
