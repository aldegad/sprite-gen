// SPDX-License-Identifier: Apache-2.0
// composer/library.js — the read-only library file tree (left sidebar).
//
// Lazy: a folder's children are fetched only when first expanded. Image rows are
// drag sources; the payload is the file's absolute path + name, which a canvas row
// consumes as a reference. The library never mutates the folder — it only reads.

function makeDirNode(entry) {
  const node = document.createElement("div");
  node.className = "tree-node";
  const row = document.createElement("div");
  row.className = "tree-row dir";
  row.innerHTML = icon("twist") + `<span class="name"></span>`;
  row.querySelector(".name").textContent = entry.name;
  const children = document.createElement("div");
  children.className = "tree-children";
  children.hidden = true;
  let loaded = false;

  row.addEventListener("click", async () => {
    const open = children.hidden;
    if (open && !loaded) {
      try {
        const data = await apiBrowse(entry.path);
        renderEntries(children, data.entries);
        loaded = true;
      } catch (e) {
        setStatus(t("browseFail", e.message), "err");
        return;
      }
    }
    children.hidden = !open;
    row.classList.toggle("open", open);
  });

  node.appendChild(row);
  node.appendChild(children);
  return node;
}

function makeImageNode(entry) {
  const node = document.createElement("div");
  node.className = "tree-node";
  const row = document.createElement("div");
  row.className = "tree-row image";
  row.draggable = true;
  const thumb = document.createElement("img");
  thumb.className = "thumb";
  thumb.src = imgUrl(entry.path);
  thumb.loading = "lazy";
  thumb.alt = "";
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = entry.name;
  row.appendChild(thumb);
  row.appendChild(name);

  row.addEventListener("dragstart", (ev) => {
    ev.dataTransfer.setData(
      "application/x-sprite-file",
      JSON.stringify({ path: entry.path, name: entry.name })
    );
    ev.dataTransfer.effectAllowed = "copy";
    row.classList.add("dragging");
  });
  row.addEventListener("dragend", () => row.classList.remove("dragging"));

  node.appendChild(row);
  return node;
}

function renderEntries(container, entries) {
  container.innerHTML = "";
  for (const entry of entries) {
    container.appendChild(entry.type === "dir" ? makeDirNode(entry) : makeImageNode(entry));
  }
}

async function loadTree(mountDir) {
  const treeEl = document.getElementById("tree");
  treeEl.innerHTML = "";
  try {
    const data = await apiBrowse(mountDir);
    renderEntries(treeEl, data.entries);
  } catch (e) {
    setStatus(t("browseFail", e.message), "err");
  }
}
