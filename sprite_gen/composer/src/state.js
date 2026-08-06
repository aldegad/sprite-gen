// SPDX-License-Identifier: Apache-2.0
// composer/state.js — the virtual composition session.
//
// The session is the "unsaved buffer": it holds the mounted library root and the
// rows -> file references. Nothing here is written to disk and no original file is
// copied — a row cell is just a reference {path, name} into the mounted folder.
// Materializing this into a real run dir (the "build" seam) is a later slice.

const session = {
  mount: null, // absolute path of the mounted library root (or null)
  rows: [],    // [{ id, name, cells: [{ path, name }] }]
};

let _rowSeq = 0;
function newRowId() {
  _rowSeq += 1;
  return `row-${_rowSeq}`;
}

function addRow(name) {
  const row = { id: newRowId(), name: name || t("newRowName"), cells: [] };
  session.rows.push(row);
  return row;
}

function deleteRow(id) {
  session.rows = session.rows.filter((r) => r.id !== id);
}

function rowById(id) {
  return session.rows.find((r) => r.id === id) || null;
}

// A file may sit in more than one row (same source referenced twice), but not
// twice in the SAME row — a duplicate drop on one row is a no-op.
function addCell(rowId, file) {
  const row = rowById(rowId);
  if (!row) return false;
  if (row.cells.some((c) => c.path === file.path)) return false;
  row.cells.push({ path: file.path, name: file.name });
  return true;
}

function removeCell(rowId, path) {
  const row = rowById(rowId);
  if (!row) return;
  row.cells = row.cells.filter((c) => c.path !== path);
}

// ── server ────────────────────────────────────────────────────────
async function apiGetState() {
  const res = await fetch("/api/state");
  return res.json();
}

async function apiMount(dir) {
  const res = await fetch("/api/mount", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dir }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data; // { mount, dir, entries }
}

// Pop the native OS chooser (server-side). kind "folder" -> { dir }; kind "image"
// -> { dir: <parent>, files } (the image's folder). { cancelled: true } if
// dismissed; throws with code "unsupported-platform" (501) off macOS so the UI can
// fall back to a path prompt.
async function apiPick(kind) {
  const res = await fetch(`/api/pick?kind=${encodeURIComponent(kind)}`, { method: "POST" });
  const data = await res.json();
  if (res.status === 501) {
    const err = new Error(data.error || "unsupported");
    err.code = "unsupported-platform";
    throw err;
  }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data; // { dir } | { dir, files } | { cancelled: true }
}

async function apiBrowse(dir) {
  const url = dir ? `/api/browse?dir=${encodeURIComponent(dir)}` : "/api/browse";
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data; // { dir, entries }
}

function imgUrl(path) {
  return `/api/browse-img?path=${encodeURIComponent(path)}`;
}

async function apiBuild(outDir) {
  const rows = session.rows
    .filter((r) => r.cells.length)
    .map((r) => ({ name: r.name, cells: r.cells.map((c) => ({ path: c.path, name: c.name })) }));
  const res = await fetch("/api/build", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ outDir, rows }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data; // { runDir, states, frames, cell }
}

async function apiOpenCuration(runDir) {
  const res = await fetch("/api/open-curation", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ runDir }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data; // { url }
}

function setStatus(msg, kind) {
  const el = document.getElementById("status");
  el.textContent = msg || "";
  el.className = "status" + (kind ? " " + kind : "");
}
