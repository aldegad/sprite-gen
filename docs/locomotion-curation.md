# Locomotion Curation & Clean GIF Export — sprite-gen reference

> `SKILL.md` 에서 분리한 시나리오 상세. motion-phase 실험·수동 selected-cycle·클린 GIF export 가 필요할 때 따른다. 내용은 손실 없이 `SKILL.md` 본문에서 그대로 옮겨졌다.

### Motion Phase Guide Experiment

For 8-frame run rows, `prepare_sprite_run.py --motion-phase-guides` adds simple stick-pose hints to the layout guide:

```text
contact -> down -> passing -> up -> opposite contact -> down -> passing -> up
```

Use this only for explicit locomotion experiments. The guide is not final art and must not appear in the generated row. Its purpose is to nudge foot contact, body height, and leg phase. It can improve leg alternation, but it is not a guarantee of a natural run loop; visual motion QA remains blocking.

### Manual Selected Cycle

When a generated locomotion row contains usable frames but the full row fails motion QA, do not pretend the full row passed and do not ask image generation to redraw locked peak frames. Preserve the generated frame truth and make a separate selected-cycle artifact:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/compose_selected_cycle.py \
  --run-dir <target>/assets/generated/sprites/<character-id> \
  --state running-right \
  --frames 2,3,4,5 \
  --name running-right-selected-2-3-4-5 \
  --delay-ticks 19 \
  --note "human QA selected current best right-run loop"
```

This writes:

```text
qa/<name>.gif
qa/<name>-contact.png
qa/<name>.json
```

`qa/<name>.json` is the selected-cycle SSoT: it records the source state, exact 1-based selected frame numbers, runtime zero-based frame indices, delay/duration, and SHA-256 for every source frame. Runtime integrations may consume the original atlas with the selected zero-based frame order, or export a derived atlas explicitly from that manifest.

Use this path for user-approved manual locomotion curation. It is not a silent fallback: report that full-row locomotion failed and that the selected subset is the accepted usable loop.

For precise humanoid running today, the most reliable path is candidate generation plus human frame picking. Generate a few candidate rows, keep the best extracted frames, and let the user choose or reorder the 1-based frame sequence for `compose_selected_cycle.py`. Do not promise automatic frame-order selection yet; if the row only works after manual picking, record that as the current limitation in `qa-notes.md`.

### Clean GIF Export

Use `compose_sprite_gif.py` whenever you need a shareable transparent GIF from extracted frames or from a human-picked frame order. Do not hand-roll GIFs with ad-hoc `magick` commands unless you are debugging.

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/compose_sprite_gif.py \
  --frame-dir <target>/assets/generated/sprites/<character-id>/frames/running-right \
  --frame-order 2,1,5,3 \
  --delay-ticks 14 \
  --output <target>/assets/generated/sprites/<character-id>/qa/running-right-picked.gif \
  --contact-output <target>/assets/generated/sprites/<character-id>/qa/running-right-picked-contact.png \
  --manifest-output <target>/assets/generated/sprites/<character-id>/qa/running-right-picked-gif.json
```

Delay ticks are GIF/ImageMagick ticks: `14` means about 140 ms per frame, `20` means about 200 ms, `30` means about 300 ms. State-specific timing is allowed, but it must be explicit in the output manifest or final delivery note. A typical preview profile can be:

```text
base/idle/rest/working/talking/success = 20 ticks
running = 14 ticks
sleep = 30 ticks
```

Clean GIF invariants:

- Source PNG frames remain the truth. The GIF owns only frame order and timing.
- Save as transparent GIF with a dedicated transparent palette index.
- Use disposal method 2 so each frame clears before the next frame draws. This prevents "previous frame showing through" ghosts in Discord/browser previews.
- Use infinite loop (`loop-count 0`) for looped states.
- Keep contact sheets separate from GIFs. Contact sheets may use checker backgrounds for inspection; runtime/shareable GIFs should preserve transparency.
- If a loop only works after dropping or reordering frames, record the selected 1-based frame order. Do not hide that as an automatic pass.

