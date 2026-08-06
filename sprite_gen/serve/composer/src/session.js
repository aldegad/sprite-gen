// SPDX-License-Identifier: Apache-2.0
// composer/session.js — the virtual composition session (pure model).
//
// The "unsaved buffer": the mounted library root and rows -> file references.
// Nothing here touches the network or the DOM. A row cell is a reference
// { path, name } into the mounted folder; no original file is copied. The build
// seam materializes this into a run dir.

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

// A file may sit in more than one row, but not twice in the SAME row — a duplicate
// drop on one row is a no-op.
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

function sessionHasFrames() {
  return session.rows.some((r) => r.cells.length > 0);
}

// The build payload: rows with at least one cell, as { name, cells:[{path,name}] }.
function sessionBuildRows() {
  return session.rows
    .filter((r) => r.cells.length)
    .map((r) => ({ name: r.name, cells: r.cells.map((c) => ({ path: c.path, name: c.name })) }));
}
