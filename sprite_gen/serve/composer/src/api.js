// SPDX-License-Identifier: Apache-2.0
// composer/api.js — the server boundary. Every call to the compose server lives
// here; no other module builds a fetch. Domain: transport, not model or view.

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

// Native OS chooser (server-side). kind "folder" -> { dir }; kind "image" ->
// { dir: <parent>, files }. { cancelled: true } if dismissed; throws with code
// "unsupported-platform" (501) off macOS so the caller can fall back to a prompt.
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

async function apiBuild(outDir, rows) {
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
