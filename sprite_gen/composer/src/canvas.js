// SPDX-License-Identifier: Apache-2.0
// composer/canvas.js — the row composition canvas (right main).
//
// Each row maps 1:1 to a run-dir state. Files dropped from the tree become
// reference cells (state.js). Rendering is straightforward re-draw from the
// session; nothing here writes to disk.

function delSvg() {
  return '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">'
    + '<path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" '
    + 'stroke-width="1.8" stroke-linecap="round"/></svg>';
}

function makeCell(row, cell) {
  const el = document.createElement("div");
  el.className = "cell";
  const img = document.createElement("img");
  img.className = "frame";
  img.src = imgUrl(cell.path);
  img.alt = cell.name;
  const cap = document.createElement("div");
  cap.className = "cap";
  cap.textContent = cell.name;
  cap.title = cell.path;
  const del = document.createElement("button");
  del.className = "cell-del";
  del.type = "button";
  del.innerHTML = delSvg();
  del.addEventListener("click", () => {
    removeCell(row.id, cell.path);
    renderRows();
  });
  el.appendChild(img);
  el.appendChild(cap);
  el.appendChild(del);
  return el;
}

function makeRow(row) {
  const el = document.createElement("div");
  el.className = "row";
  el.dataset.rowId = row.id;

  const head = document.createElement("div");
  head.className = "row-head";
  const nameInput = document.createElement("input");
  nameInput.className = "row-name";
  nameInput.value = row.name;
  nameInput.spellcheck = false;
  nameInput.addEventListener("change", () => {
    row.name = nameInput.value.trim() || row.name;
    nameInput.value = row.name;
  });
  const count = document.createElement("span");
  count.className = "count";
  count.textContent = t("frames", row.cells.length);
  const spacer = document.createElement("span");
  spacer.className = "spacer";
  const del = document.createElement("button");
  del.className = "row-del";
  del.type = "button";
  del.title = t("deleteRow");
  del.innerHTML = delSvg();
  del.addEventListener("click", () => {
    deleteRow(row.id);
    renderRows();
  });
  head.appendChild(nameInput);
  head.appendChild(count);
  head.appendChild(spacer);
  head.appendChild(del);

  const strip = document.createElement("div");
  strip.className = "row-strip";
  if (row.cells.length === 0) {
    const hint = document.createElement("span");
    hint.className = "row-empty-hint";
    hint.textContent = t("rowEmptyHint");
    strip.appendChild(hint);
  } else {
    for (const cell of row.cells) strip.appendChild(makeCell(row, cell));
  }

  // drop target
  strip.addEventListener("dragover", (ev) => {
    if (ev.dataTransfer.types.includes("application/x-sprite-file")) {
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "copy";
      strip.classList.add("drop-hot");
    }
  });
  strip.addEventListener("dragleave", () => strip.classList.remove("drop-hot"));
  strip.addEventListener("drop", (ev) => {
    ev.preventDefault();
    strip.classList.remove("drop-hot");
    const raw = ev.dataTransfer.getData("application/x-sprite-file");
    if (!raw) return;
    let file;
    try { file = JSON.parse(raw); } catch { return; }
    if (addCell(row.id, file)) {
      setStatus(t("dropAdded", file.name, row.name), "ok");
      renderRows();
    }
  });

  el.appendChild(head);
  el.appendChild(strip);
  return el;
}

function renderRows() {
  const rowsEl = document.getElementById("rows");
  rowsEl.innerHTML = "";
  for (const row of session.rows) rowsEl.appendChild(makeRow(row));
  updateBuildBar();
}

// The build bar appears once at least one row holds a file — a build with no
// frames is not a build. renderRows() runs on every composition mutation, so
// hiding the "open curation" button here retires a stale build: after an edit
// the last run dir is superseded, and doBuild() re-shows the button on success.
function updateBuildBar() {
  const hasFrames = session.rows.some((r) => r.cells.length > 0);
  document.getElementById("build-bar").hidden = !(session.mount && hasFrames);
  document.getElementById("open-cur-btn").hidden = true;
  document.getElementById("build-result").textContent = "";
}

function refreshCanvasChrome() {
  const mounted = !!session.mount;
  document.getElementById("empty").hidden = mounted;
  document.getElementById("add-row").hidden = !mounted;
  document.getElementById("rows").hidden = !mounted;
}
