// SPDX-License-Identifier: Apache-2.0
// composer/ui.js — shared UI primitives. The single source for icons and for
// button creation, so two controls can never diverge in size or shape.

// Inline SVG icons — one definition each (previously duplicated as delSvg/twistSvg).
const ICONS = {
  close:
    '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">'
    + '<path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" '
    + 'stroke-width="1.8" stroke-linecap="round"/></svg>',
  twist:
    '<svg class="twist" viewBox="0 0 16 16" aria-hidden="true">'
    + '<path d="M6 4l5 4-5 4" fill="none" stroke="currentColor" stroke-width="1.8" '
    + 'stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

function icon(name) {
  return ICONS[name] || "";
}

// The ONE place a text/action button is created. `variant` changes colour only —
// padding and height live on `.btn`, so no two buttons can differ in size. This is
// the structural fix for "Open image is a different height from Open folder".
// variant: "default" | "ghost" | "accent" | "dashed".
function button({ label, variant = "default", onClick, id, title, hidden = false } = {}) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = "btn" + (variant && variant !== "default" ? ` btn--${variant}` : "");
  if (id) el.id = id;
  if (title) el.title = title;
  el.hidden = hidden;
  if (label != null) el.textContent = label;
  if (onClick) el.addEventListener("click", onClick);
  return el;
}

// A small icon-only button (card actions: delete a cell/row). Its own component,
// consistent among its instances; shares the icon SSoT above.
function iconButton({ name, cls, onClick, title }) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = cls;
  if (title) el.title = title;
  el.innerHTML = icon(name);
  if (onClick) el.addEventListener("click", onClick);
  return el;
}

function setStatus(msg, kind) {
  const el = document.getElementById("status");
  el.textContent = msg || "";
  el.className = "status" + (kind ? " " + kind : "");
}
