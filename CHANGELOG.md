# Changelog

All notable changes to `sprite-gen` are recorded here. Versions track the `version:` field in `SKILL.md`.

## v1.9.0 — Pool arrangement persistence + sweep hardening

Adds full arrangement persistence and the hardening from a second adversarial sweep (which also caught a regression from v1.8.1).

- **Candidate pool arrangement persists.** `curation.json` now records the full display `order` (sequence then pool) alongside `selected`, so reopening the curator restores exactly how you arranged *both* rows — not just the sequence. The bake is unchanged: compose/export key off `selected`; `order` is webview-only and documented in `curation.py` (the schema SSoT).
- **Robust against corrupt / hand-edited sidecars.** Frame indices in `selected` and `order` are coerced to integers and de-duplicated on load; `curation.py` now skips non-integer / out-of-range `selected` entries instead of crashing the bake.
- **Fixed a duplicate-render regression** (introduced in v1.8.1): missing/unextracted frames now render once (they already live in `order`) — removed the redundant second render loop that doubled them and could leak duplicate indices into the atlas.
- **Label escaping.** State names, actions, and imported frame labels are HTML-escaped before display, so an imported set's `meta.json` can't inject markup; over-long labels truncate instead of breaking the card.

## v1.8.2 — Preview UX polish

Remaining low-severity items from the v1.8.0 adversarial review.

- **Preview re-anchors on edits.** Reordering or moving frames no longer jumps the preview to a different frame — it keeps the on-screen frame in view (tracked by frame index), so a paused inspection stays put while you rearrange the sequence.
- **Transport disabled when the sequence is empty.** Play/step buttons grey out (and the position reads `0/0`) when no frames are in the sequence, instead of looking active but doing nothing.

## v1.8.1 — Cross-platform hardening

Fixes from an adversarial cross-platform / blast-radius review of the v1.8.0 curator.

- **FLIP animation reliable on Safari/Firefox.** The reorder and settle animations now force a layout reflow between applying the inverted transform and enabling the transition (instead of a bare `requestAnimationFrame`), so cards slide instead of teleporting on non-Chromium engines. `.missing` cards are excluded from the FLIP.
- **Missing frames preserved.** `commitZones` and `seedEntries` keep not-yet-extracted frame slots in `order`, so a reorder during incremental extraction can't silently drop them.
- **Multi-touch guard.** The reorder grip ignores secondary pointers (`ev.isPrimary`), so a second finger can't start a parallel drag on touch devices.

## v1.8.0 — Curator: drag reorder + candidate pool

The standalone curation webview (`serve_curation.py`) gets a full frame-curation pass: reorder the play sequence by hand, scrub the preview, and reconstruct a run from several generated takes by dragging the cuts you like.

![Curator drag reorder + candidate pool](docs/curator-drag-update.gif)

- **Drag-to-reorder frames.** Grab the `⠿` grip on a frame card to change the play order. The grabbed card lifts and follows the cursor while the others slide aside (FLIP animation), and it eases into its slot on drop. The new order saves to `curation.json.selected` and is baked left-to-right by `compose_sprite_atlas.py` — no backend change, fully non-destructive.
- **Two-row curation: sequence + candidate pool.** Each state now renders a **sequence** row (the selected play order, on top) and a **candidate pool** row below it (unselected frames — e.g. a second or third generated take of the same row). Drag a cut from the pool up into the sequence to add it, or a sequence cut down to drop it; a card click sends it to the other row. This makes it easy to reconstruct one clean run loop from the best cuts across multiple takes.
- **Preview transport.** The live preview gains play/pause, frame-by-frame stepping (`⏮`/`⏭`, auto-pauses), and a 0.25×–4× speed control, plus a `cursor/total · #frame` readout. Display-only — these never touch `curation.json`, so paused inspection and stepping don't disturb the selection.
- Selection is now a flag with a separate display order, so toggling a frame no longer re-sorts the sequence. i18n (en/ko) throughout.

Curator UI: `scripts/curator/curator.js`, `scripts/curator/curator.css`.
