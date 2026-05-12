---
name: sprite-gen
description: Generate fast 2D game sprite strips for Kuma live demos by combining image-gen/imagegen with hatch-pet identity and animation discipline. Use for Animal Crossing-like billboard characters, NPC sprites, short idle/run/jump/talk animation rows, chroma-keyed transparent assets, and 5-minute dispatch demos where each worker owns one character folder.
---

# Sprite Gen

Fast-path sprite generation for the Hermes/Kuma live demo. This is not a second truth for pet packaging: it reuses the image generation layer and the visual rules from `hatch-pet`, but narrows the output to game-ready billboard sprite strips.

Use this skill when speed matters more than producing a full Codex pet. The final game asset may be copied into one folder. For known Kuma Studio members, first resolve the existing member image and use it as the canonical identity reference; do not regenerate it. The reliable path is row-by-row strips, while the Hermes 5-minute fast path may use one-shot master-sheet generation with the built-in skeleton.

The built-in layout SSoT for this skill is `assets/sprite-gen-assets.json`. It defines the 256x256 square-cell guides, row frame counts, and master skeleton sheet. Do not regenerate guide boxes during normal demo work; copy or attach the built-in assets.

For Kuma Studio members, the identity SSoT is **not** a new image-generation prompt. It is `packages/shared/team.json` and the image path declared on each member. Resolve the image path against `$KUMA_STUDIO_ROOT/packages/studio-web/public/` before creating any new `base.png`.

If a dispatch task or plan explicitly provides a base image path, that image is the
canonical identity input for this character. Copy it into the character folder as
`base-source.<ext>` or `base.png`; do not reinterpret it, redraw it, or spend an
image-generation call on a replacement base. Known-image fast mode is exactly one
image-generation call per character: base image + master skeleton in, one
`sprite-sheet.png` out, then deterministic alpha cleanup to `sprite-sheet-alpha.png`.

Known Hermes/Kuma member source images:

```text
kuma/sukuma -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/sukuma.png
howl -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/howl.jpg
tookdaki -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/ttukddak.jpg
darami -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/darami.jpg
saemi -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/saemi.jpg
koon -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/koon.jpg
bamdori -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/bamdori.jpg
kongkongi -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/kongkong.jpg
buri -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/buri.jpg
lumi -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/lumi.jpg
moongchi -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/mungchi.jpg
shuksshuki -> $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/shuksshuki.jpg
```

If `team.json` has an image for the character, copy that file into the character output folder as `base.png` or `base-source.<ext>` and use it as the identity reference. Do **not** spend a first image-generation call making a new base. Generate a new base only when the character is not in `team.json`, the declared image file is missing, or Alex explicitly asks to replace the member image.

## Output Contract

One worker owns exactly one character folder:

```text
<target-worktree>/assets/generated/sprites/<character-id>/
  manifest.json
  base.png or base-source.<ext>
  references/layout-guides/
    idle.png
    run.png
    jump.png
    talk.png
    layout-guides.json
  idle-strip.png
  run-strip.png
  jump-strip.png
  talk-strip.png
  normalized/
    idle-strip.png
    run-strip.png
    jump-strip.png
    talk-strip.png
  contact-sheet.png
  qa-notes.md
```

Do not let multiple workers write the same character folder. If a folder exists from a previous run, create a timestamped sibling instead of overwriting it.

For Hermes live demos, do not write new sprite outputs under `~/.kuma/assets`. The target worktree is the asset SSoT. `~/.kuma/assets/hermes-sprites` is legacy scratch/probe storage only and must not appear in new worker write scopes unless Alex explicitly asks for a global asset library.

## Hermes Live Game Contract

For Hermes-style live demos, the game consumes assets from the target worktree, not from the worker scratch folder:

```text
<target-worktree>/assets/generated/sprites/<character-id>/
  base.png                 # copied identity source from team.json, or raw generated source only when missing
  sprite-sheet.png         # raw one-shot or assembled sheet, may still be magenta
  sprite-sheet-alpha.png   # game-ready animated sheet, transparent background
  manifest.json
  qa-notes.md
```

`sprite-sheet-alpha.png` is the preferred game-ready output. For Hermes timed
sprints, do not create `base-alpha.png`; it is not part of the fast sprite
pipeline. If animation generation fails, set `manifest.json.game_input` to the
copied canonical member image (`base.png` or `base-source.<ext>`) and mark
`degraded_static_fallback: true`. A visible magenta square/plane in the browser
is a failed integration, even if `base.png` exists. Raw magenta images are
source material only.

For a full animated character, `sprite-sheet-alpha.png` is the destination file.
The game must consume this file through `manifest.json.game_input` and animation
metadata. `sprite-sheet.png` is an intermediate raw source; it is not a browser
render input. A file named `sprite-sheet-alpha.png` is valid only after
`scripts/validate_sprite_sheet.py` writes a passing `sprite-sheet-alpha.report.json`.

Do not create or keep `sprite-sheet.png` / `sprite-sheet-alpha.png` for a static fallback. Those filenames mean an actual animated sheet. Copying `base.png`, a static cutout, or the original full illustration/card into repeated grid cells is a failed sprite-gen result because downstream game workers treat the file as a frame grid. If animation generation times out, omit the sheet files, set their manifest entries to `null`, set `game_input` to the copied canonical member image, and mark `degraded_static_fallback: true`.

### Grid is not guaranteed — `frame_layout` is the runtime SSoT

One-shot master-mode generation (gpt-image) **does not honour the exact 256px
uniform 6x4 grid**. Rows come back with uneven frame counts (e.g. idle=4 / run=6,
run=9, run=10), and individual frames drift off the cell lattice. If the game
slices the sheet at a fixed `cellWidth=256`, the next frame bleeds into the cut and
billboards split / show outlines. So:

- After generating a one-shot master sheet (and the chroma-key / repair steps),
  run `scripts/extract_frame_layout.py` to recover the **real** per-row frame
  rectangles from the alpha channel and write them into `manifest.json` as
  `frame_layout` (schema below). This is a **mandatory** pipeline step for
  one-shot master mode, not optional.
- The game must consume `manifest.json.frame_layout` and crop each frame at its
  exact `{x, y, w, h}` — it must not fall back to uniform `cellWidth` slicing when
  a `frame_layout` is present. The starter already does this:
  `three-game-starter/src/sprite-runtime.js` `normalizeSpriteManifest` lifts
  `manifest.frame_layout` onto `sprite.frameLayout`, and `getSpriteFrame` prefers
  `sprite.frameLayout?.rows?.[stateName]` (`[{x,y,w,h}, ...]`) over the uniform
  `frameIndex * frameWidth` path. The manifest key name **must** be `frame_layout`
  with `rows.<state>` arrays of `{x, y, w, h}` to match that code.
- Row-by-row strip mode (`normalize_strip_to_grid.py`) produces an exact grid by
  construction, so `frame_layout` may be omitted there — the uniform `cellWidth`
  path is correct for normalized strips. One-shot master mode is the only mode
  that requires `frame_layout`.

Run `extract_frame_layout.py` after chroma-key / repair:

```bash
# single sheet (writes frame_layout into the sibling manifest.json)
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/extract_frame_layout.py \
  --image <target-worktree>/assets/generated/sprites/<character-id>/sprite-sheet-alpha.png \
  --manifest <target-worktree>/assets/generated/sprites/<character-id>/manifest.json \
  --write

# whole batch directory
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/extract_frame_layout.py \
  --root <target-worktree>/assets/generated/sprites --write
```

Re-running is idempotent: it overwrites `frame_layout` with the same result.

### Validation

`scripts/validate_sprite_sheet.py` no longer hard-fails on 256px cell alignment
(that grid is not guaranteed). It rejects sheets that only tile the original image
or fuse sprites into one blob. A passing report must show all of:

- the sheet is exactly 1536x1024 (the contract canvas);
- `extract_frame_layout` cleanly separates at least `idle>=3, run>=4, jump>=3,
  talk>=3` frames per row — i.e. sprites are not fused into one alpha blob and the
  sheet is not a single tiled illustration;
- each row's frames look like character cutouts (per-frame alpha density inside a
  sane band, no over-wide fused frames), not full square source cards or
  room/background images;
- each animation row has visible frame-to-frame motion, with the run row changing
  across multiple adjacent frames;
- the report echoes the recovered `frame_layout`;
- `manifest.json` points to `sprite-sheet-alpha.png` only when the report `ok` is
  true, and carries the matching `frame_layout`.

Run this before reporting sprite success (validate first, then extract the layout —
or pass `--manifest` to do both):

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/validate_sprite_sheet.py \
  --image <target-worktree>/assets/generated/sprites/<character-id>/sprite-sheet-alpha.png \
  --report <target-worktree>/assets/generated/sprites/<character-id>/sprite-sheet-alpha.report.json \
  --manifest <target-worktree>/assets/generated/sprites/<character-id>/manifest.json
```

For presentation-grade live demos, static billboards are not enough. The minimum pass contract is:

- at least the main player plus 3 named NPCs have `sprite-sheet-alpha.png`;
- the game uses those sheets for visible idle/run/talk frame changes;
- any character without a validated sheet is marked `degraded_static_fallback: true` in `manifest.json`;
- a degraded static fallback can keep the demo running, but it is not a sprite-animation pass.
- visual QA is blocking: visible full source cards, room/background illustrations, oval crops, procedural smiley/initial placeholders, and repeated static source images in grid cells are failures, even if file existence checks pass.

Do not report a 12-character static fallback roster as a successful sprite-gen
batch. If a worker cannot produce at least one validated `sprite-sheet-alpha.png`
for its assigned batch, the worker result is `sprite-animation-blocked`, even
when the copied canonical member images are present and usable as survival
billboards.

For 2-character or 4-character live batches, report the result per character. A batch
is not `qa-passed` just because one shard succeeded. If one assigned character fails
validation, the worker result must include `partial` or `sprite-animation-blocked`
with the failed character id and the exact validator reason. This is what lets the
orchestrator find `moongchi`/`shuksshuki`-style failures quickly instead of hunting
through screenshots.

If the dispatch task names hard animated targets, those specific characters own
the pass/fail line. For example, if a live game says the player `sukuma` must be
animated, generating four other NPC sheets is not a substitute. Leave the static
baseline in place for runtime survival, but report `sprite-animation-blocked`
until each hard target has a validated `sprite-sheet-alpha.png` and manifest
metadata that points the game to that sheet.

Static fallback still means the actual character image is rendered. If a `manifest.json`
has `game_input`, the game worker must load that file as the visible texture. Drawing a
new procedural oval/smiley/initials placeholder with Canvas/SVG/Three.js is only allowed
when the character asset is missing and must be reported as `runtime_placeholder: true`;
it is not a sprite-gen pass and must not be counted as using the generated skin.

For known Kuma Studio members, the copied canonical member image is the static fallback.
Do not run image generation or deterministic background removal just to create a fallback
cutout. Image generation is for animation rows or master sheets only. Record
`source_image_origin` in the manifest so the runtime can report the actual image it used.

If the copied canonical member image is itself a full scene/card illustration rather than
a clean character cutout, it is still only survival fallback. It may keep the game
runnable, but it cannot satisfy presentation visual pass. The worker must keep trying to
produce a validated transparent animated sheet for the hard target instead of declaring
done.

When a dispatch plan assigns a specific visual-generation model/provider, obey that plan.
Do not silently switch a sprite worker to a faster text/coding model if the live run is
being judged by browser screenshots. Faster close-time is not a substitute for a
presentation-grade sprite visual.

## Runtime Consumption Contract

A generated sprite sheet is only half of the sprite-gen result. The integration worker must consume it as an animated atlas. Rendering the whole sheet on one plane, rendering only frame 0 forever, or replacing it with a static billboard is a failed animation integration even when `sprite-sheet-alpha.png` exists.

Every `manifest.json` for a full sheet must include enough runtime metadata for a game worker to animate without guessing:

```json
{
  "characterId": "<character-id>",
  "game_input": "sprite-sheet-alpha.png",
  "degraded_static_fallback": false,
  "animation": {
    "cellWidth": 256,
    "cellHeight": 256,
    "columns": 6,
    "rows": {
      "idle": { "row": 0, "frames": 4, "fps": 4, "loop": true },
      "run": { "row": 1, "frames": 6, "fps": 10, "loop": true },
      "jump": { "row": 2, "frames": 4, "fps": 8, "loop": false },
      "talk": { "row": 3, "frames": 4, "fps": 6, "loop": true }
    }
  },
  "frame_layout": {
    "sheetWidth": 1536,
    "sheetHeight": 1024,
    "rows": {
      "idle": [ { "x": 64, "y": 64, "w": 128, "h": 160 }, "..." ],
      "run":  [ { "x": 0,  "y": 320, "w": 150, "h": 170 }, "..." ],
      "jump": [ "..." ],
      "talk": [ "..." ]
    }
  }
}
```

For one-shot master sheets the `animation.rows.<state>.frames` count is a hint
only; the authoritative per-frame rectangles are `frame_layout.rows.<state>`
(produced by `scripts/extract_frame_layout.py`). When `frame_layout` is present
the runtime must use it and ignore uniform `cellWidth` slicing. `frame_layout` may
be omitted only for grid-exact row-by-row strip output normalized by
`normalize_strip_to_grid.py`.

The game worker must implement a `SpriteAnimator` or equivalent that:

- chooses `idle`, `run`, `jump`, or `talk` from the character state;
- advances `frameIndex` from elapsed time and each row's `fps`;
- samples only the active cell, not the full atlas;
- resets or clamps non-loop rows such as `jump`;
- reports the current `state`, `row`, `frameIndex`, and `source` in debug state or QA notes.

The game worker must also report the actual texture source in debug state, for example
`sprites.<id>.source = "assets/generated/sprites/<id>/sprite-sheet-alpha.png"` or
the copied canonical member image. If debug state shows animated frame ticks but the texture source is a
procedural placeholder, the runtime integration failed.

For timed Hermes-style billboard games, do not spend extra image-generation time producing
separate left-facing rows unless Alex explicitly asks. The runtime must keep `facingX` or an
equivalent direction state and mirror the selected atlas cell when the character moves left.
Visible pass requires WASD right then left to flip the same generated skin; if the player
runs left while still facing right, the sprite runtime integration is incomplete.

For Canvas 2D with a grid-exact (normalized-strip) sheet, sample frames with `drawImage(sheet, column * cellWidth, row * cellHeight, cellWidth, cellHeight, ...)`. For a one-shot master sheet, sample each frame at its `frame_layout.rows.<state>[i]` `{x, y, w, h}` instead — the cells are not on the 256px lattice. For Three.js atlas textures, set the plane to that frame rectangle using UV repeat/offset or render the selected cell to a canvas texture first. Do not put a 1536x1024 full sheet on a billboard and call it animated.

Hermes runtime note: generated sheets can contain guide-line or edge pixels at the
outer frame boundary. For grid-exact sheets, sample each 256x256 cell with a small
inward padding (currently 3px). For one-shot master sheets, the `frame_layout`
rectangles are already alpha-tight from `extract_frame_layout.py`, so use them
as-is. Either way, keep one absolute rectangle per frame and do **not** recompute
billboard scale from each frame's or each animation state's live alpha bounds at
runtime — per-frame auto-fit makes characters shrink/grow, drift left/right, or clip
differently between idle/run/talk. The fitting decision belongs to the sheet /
`frame_layout`, not to runtime auto-fit.

Visible pass criteria:

- player run input changes state to `run` and advances at least 2 different frame indexes within one second;
- player run input to the right and then left changes visible facing direction, either by atlas rows or runtime mirroring;
- an idle NPC advances idle frames even while standing still;
- at least one NPC or prop interaction changes the character state to `talk` and advances talk frames;
- if NPC walking is part of the demo contract, the NPC world position must also change while its state is `run`;
- static fallback characters must stay out of animation pass counts and must be reported as `degraded_static_fallback: true`.

If generation is slow, the accepted fallback is:

1. first assigned character: `sprite-sheet-alpha.png` if possible;
2. remaining characters: existing team image static billboard through the copied canonical member image;
3. every delayed sheet must be marked in `manifest.json` and `qa-notes.md`.

The phrase "`sprite-sheet-alpha.png` if possible" means it passes `scripts/validate_sprite_sheet.py`. A 1254x1254 single character alpha image is not a sprite sheet, even if it is named `sprite-sheet-alpha.png`.

Never hand the game a raw `base.png` or raw `sprite-sheet.png` as the visible texture. If only raw magenta exists, run `scripts/chroma_key_magenta.py` first or explicitly report that the character is not game-ready.

The game/integration worker has the same rule: use `*-alpha.png` first. If a late worker only produced magenta sources, the integration worker must either convert them with `chroma_key_magenta.py` or use a runtime chroma-key texture loader. It must never render a visible magenta background as an acceptable fallback.

For browser smoke tests, take a Playwright screenshot and fail the run if visible key-magenta remains:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/check_visible_magenta.py \
  --image /absolute/path/to/playwright-screenshot.png
```

This check is for game/browser screenshots, not raw generated sprite sources. Pink land tiles are acceptable only if they are not chroma-key magenta; `#FF00FF` blocks around sprites are failed integration.

## Built-In Assets

Use these built-in references before creating anything new:

```text
$ALEX_EXTENSIONS_DIR/sprite-gen/assets/sprite-gen-assets.json
$ALEX_EXTENSIONS_DIR/sprite-gen/assets/guides/square-256/idle.png
$ALEX_EXTENSIONS_DIR/sprite-gen/assets/guides/square-256/run.png
$ALEX_EXTENSIONS_DIR/sprite-gen/assets/guides/square-256/jump.png
$ALEX_EXTENSIONS_DIR/sprite-gen/assets/guides/square-256/talk.png
$ALEX_EXTENSIONS_DIR/sprite-gen/assets/skeletons/sprite-gen-master-256.png
$ALEX_EXTENSIONS_DIR/sprite-gen/scripts/validate_sprite_sheet.py
$ALEX_EXTENSIONS_DIR/sprite-gen/scripts/extract_frame_layout.py
```

The master skeleton is a 1536x1024 sheet: 6 columns x 4 rows, 256x256 cells. Rows are `idle`, `run`, `jump`, and `talk`. `idle`, `jump`, and `talk` use columns 0-3 and leave columns 4-5 unused; `run` uses columns 0-5.

Normal safe mode is row-by-row: attach the resolved team image (`base.png` / `base-source.<ext>`) plus the matching row guide.

Experimental fast mode is one-shot sheet generation: attach the resolved team image plus `assets/skeletons/sprite-gen-master-256.png`, then ask image generation to replace the skeleton figure with the character while preserving the exact grid, row order, frame count, and pose rhythm. This reduces known-character sprite generation to 1 sheet job. For text-only or missing-image characters only, count one extra call to create `base.png` first. Always hard-key alpha-clean and validate or reject the result before game use.

## Generation Layer

- Claude workers: read and use `~/.claude/skills/image-gen/SKILL.md`.
- Codex workers: read and use `${CODEX_HOME:-$HOME/.codex}/skills/.system/imagegen/SKILL.md`.
- If full pet packaging is required, use `${CODEX_HOME:-$HOME/.codex}/skills/hatch-pet/SKILL.md` scripts as the deterministic layer. For the 5-minute Hermes demo, prefer this skill's four-strip contract.

No silent fallback: if image generation is unavailable, report the exact missing precondition and stop for that character.

## Hatch-Pet Boundary

`hatch-pet` owns the full Codex pet contract: one final atlas with 8 columns, 9 rows, and 192x208 cells. Its native rows are:

```text
idle=6
running-right=8
running-left=8
waving=4
jumping=5
failed=8
waiting=6
running=6
review=6
```

`sprite-gen` borrows the identity lock, chroma-key cleanup discipline, row-strip prompting, and visual QA from `hatch-pet`; it does not duplicate pet packaging truth. For a 5-minute game demo, generate only the reduced set below. If the user asks for a Codex pet after the demo, switch to `hatch-pet` and its scripts instead of extending this skill ad hoc.

Important structure:

- Final full pets become one atlas image, but the normal reliable generation path is one base image plus one generated image per animation row.
- A row strip is one horizontal image containing multiple frames for one state.
- Every row job must attach a layout guide image, the same way `hatch-pet` attaches `references/layout-guides/<state>.png`.
- `running-left` may be mirrored from `running-right` only when the design is symmetric and the mirror decision is explicit.
- `talk` is not a native `hatch-pet` row; model it as a reduced `waving`/`review`-style gesture strip with mouth or hand motion only.

## 5-Minute Character Set

For each assigned character, generate:

1. `base.png` or `base-source.<ext>` — canonical identity reference copied from `team.json` for Kuma members.
2. `idle-strip.png` — 4 frames, breathing/blinking only.
3. `run-strip.png` — 6 frames, side-facing or 3/4 run cycle.
4. `jump-strip.png` — 4 frames, vertical body motion only.
5. `talk-strip.png` — 4 frames, mouth/face/gesture only.

All outputs must use a solid flat magenta `#FF00FF` background unless the prompt has a stronger reason to choose green. Ask for an exact RGB 255,0,255 single-color export background with no vignette, no studio lighting, no backdrop gradient, and no texture. Keep the same chibi/pixel-adjacent identity across every strip.

Before row generation, copy the canonical member image and built-in layout guides into the character folder:

```bash
mkdir -p <target-worktree>/assets/generated/sprites/<character-id>/references/layout-guides
cp $KUMA_STUDIO_ROOT/packages/studio-web/public/characters/<team-image-file> \
  <target-worktree>/assets/generated/sprites/<character-id>/base-source.<ext>
cp $ALEX_EXTENSIONS_DIR/sprite-gen/assets/guides/square-256/*.png \
  <target-worktree>/assets/generated/sprites/<character-id>/references/layout-guides/
cp $ALEX_EXTENSIONS_DIR/sprite-gen/assets/sprite-gen-assets.json \
  <target-worktree>/assets/generated/sprites/<character-id>/references/layout-guides/
```

Default `sprite-gen` cells are square 256x256 because the live game consumes billboard textures. `scripts/make_layout_guides.py` and `scripts/make_master_assets.py` exist for skill maintenance, not for normal demo execution. For hatch-pet-compatible rows, use the full `hatch-pet` package flow instead of guessing later.

After generation, normalize selected strips into exact cells before handing them to the game:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/normalize_strip_to_grid.py \
  --input /absolute/path/to/generated-idle-strip.png \
  --out <target-worktree>/assets/generated/sprites/<character-id>/normalized/idle-strip.png \
  --frames 4 \
  --report <target-worktree>/assets/generated/sprites/<character-id>/normalized/idle-strip.report.json
```

This deterministic step is required. Image generation may ignore exact output aspect ratio even when a guide is attached; the game consumes the normalized grid, not the raw generation.

If alpha PNGs are needed without grid normalization, run this skill's hard-key helper on the selected magenta source files:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/chroma_key_magenta.py \
  --input /absolute/path/to/source-strip.png \
  --out /absolute/path/to/final-alpha-strip.png
```

Use the hard-key helper for `sprite-gen` demo assets because generated magenta backgrounds often contain slight vignette or lighting variation. Use the system `imagegen` chroma helper only when a soft matte is actually desired; if it visibly desaturates or partially erases the sprite, reject that cleanup and report it.

For the Hermes live game contract, write alpha outputs only for generated sprite sheets:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/chroma_key_magenta.py \
  --input /absolute/path/to/sprite-sheet.png \
  --out /absolute/path/to/sprite-sheet-alpha.png
```

Then inspect the result. Transparent corners must be alpha-zero. If the magenta box remains visible in a browser/canvas preview, reject and report the character as delayed.

For one-shot master outputs, acceptance requires:

- exact 1536x1024 dimensions.
- the master skeleton intends 6 columns x 4 rows of 256x256 cells, but gpt-image
  does **not** reproduce that lattice exactly — frame counts per row vary and
  frames drift off-cell. Do not reject on cell misalignment; instead require that
  `scripts/extract_frame_layout.py` can separate at least `idle>=3, run>=4,
  jump>=3, talk>=3` frames per row.
- row 0 idle, row 1 run, row 2 jump, row 3 talk are present and ordered top-to-bottom.
- run is the longest row; idle/jump/talk are shorter.
- no visible skeleton, guide boxes, red joints, X marks, labels, or guide lines.

Validate the final alpha sheet, then write the recovered layout into the manifest,
before declaring it game-ready:

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/validate_sprite_sheet.py \
  --image /absolute/path/to/sprite-sheet-alpha.png \
  --report /absolute/path/to/sprite-sheet-alpha.report.json \
  --manifest /absolute/path/to/manifest.json
# (the --manifest flag writes frame_layout into the manifest on a passing report;
#  or run scripts/extract_frame_layout.py --image ... --manifest ... --write separately)
```

If this command fails, do not report the character as full animation. Either repair
the sheet immediately or downgrade to the copied canonical member image with
`degraded_static_fallback: true` and no `sprite-sheet-alpha.png` file. Invalid debug
outputs may be kept under a `raw/` or `rejected/` folder, but `manifest.json.game_input`
must not point at an invalid sheet and the worker must not name it as the final
`sprite-sheet-alpha.png`.

## Batch Rule

Use at most 4 image-generation sessions at once. The happy path is:

1. Resolve the character image from `team.json` and copy it into the character folder. Generate `base.png` only if that source is missing.
2. Copy `references/layout-guides/idle.png`, `run.png`, `jump.png`, and `talk.png` from this skill.
3. Start a batch of four row jobs in parallel: idle, run, jump, talk.
4. For every row job, attach both the resolved team image as identity reference and the matching layout guide as spacing-only reference.
5. Record the selected original generated PNG path for each row in `manifest.json`.
6. Normalize each accepted raw strip into `normalized/<state>-strip.png` with exact cell dimensions.
7. Copy final selected files into the character folder and write `qa-notes.md`.

If the batch is too slow, finish `idle` and `run` first. Mark `jump` and `talk` as delayed in `qa-notes.md`; do not fabricate them locally. Do not waste the first batch round regenerating base images for existing Kuma members.

For Hermes one-shot batches, the same fallback rule is stricter: never satisfy "deliver `sprite-sheet-alpha.png`" by duplicating a static image. Static fallback is represented by `manifest.json.game_input` pointing at the copied canonical member image.

When a generated sheet is close but fails only because cells are too large, clipped, or
off-center, run the cell-padding repair/normalization step before giving up. The repaired
file still must pass `validate_sprite_sheet.py`. Do not use this repair to hide a sheet
whose frames are identical static cards, room backgrounds, or non-character illustrations;
those remain blocked/degraded.

```bash
python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/repair_cell_padding.py \
  --image /absolute/path/to/rejected/sprite-sheet-alpha.png \
  --output /absolute/path/to/sprite-sheet-alpha.png \
  --padding 14

python3 $ALEX_EXTENSIONS_DIR/sprite-gen/scripts/validate_sprite_sheet.py \
  --image /absolute/path/to/sprite-sheet-alpha.png \
  --report /absolute/path/to/sprite-sheet-alpha.report.json
```

## Live Demo Improvement Loop

After every Hermes/Kuma live-demo attempt, compare the actual browser artifact against the demo contract before calling the skill "done":

- intro/title flow is separate from sprite generation, but sprite work must still state whether the game can animate the produced files;
- `qa-passed` requires the intended consumer path under `<target-worktree>/assets/generated/sprites/<id>/`;
- static-only output is a degraded asset pack, not a completed animation pack;
- if a worker could not use the image generation layer or only produced placeholder/static assets, the result must say `degraded` or `blocked`, not “full sheet”;
- feed recurring misses back into this skill or the active plan before the next 5-minute rerun.

## Timing Probe

Before betting a live demo on a worker mix, run one timing probe for one simple character:

1. Record the start time.
2. Resolve and copy the member image from `team.json`.
3. Record source-copy duration and inspect whether the identity is usable. If the source is missing, stop or explicitly count the extra base-generation call.
4. Copy layout guides.
5. Generate `idle-strip.png`, `run-strip.png`, `jump-strip.png`, and `talk-strip.png` as four independent row jobs, each with the team image plus matching guide attached.
6. Normalize each row into exact cells.
7. Record total duration, per-row duration if available, and the first-pass accept/reject result.

Do not count local copying, manifest writing, or chroma-key cleanup as image-generation time. If the first-pass strip quality is weak, report the failure mode directly: identity drift, bad frame separation, cropped body, non-flat background, forbidden effects, or too-illustrative style.

## Prompt Shape

Base prompt:

```text
Small chibi pixel-art-adjacent game sprite of <character>, transparent-ready, thick dark 1-2 px outline, compact readable silhouette, flat cel shading, cozy Animal Crossing-like mini game NPC, full body, centered, no props unless named, exact RGB 255,0,255 solid flat MAGENTA #FF00FF single-color export background, no vignette, no studio lighting, no backdrop gradient, no texture, no text, no UI, no scenery.
```

Row prompt:

```text
Using the attached base character as the canonical identity, create a single horizontal sprite strip for <state>.
Use the attached layout guide only for frame count, equal-width slot spacing, centering, and safe padding.
Do not reproduce the guide itself: no visible boxes, guide lines, center marks, labels, guide colors, or guide background may appear in the output.
Frames: <4 or 6>. Show the same character repeated once per frame with consistent spacing and safe padding.
Same character, same face, same markings, same palette, same outline weight, same proportions.
Exact RGB 255,0,255 solid flat MAGENTA #FF00FF single-color export background, no vignette, no studio lighting, no backdrop gradient, no texture. No text, no guide marks, no frame numbers, no shadows, no glow, no detached effects, no speed lines, no dust, no scenery.
```

One-shot master prompt:

```text
Using the attached base character as the canonical identity and the attached master skeleton sheet as the pose/grid reference, create one complete sprite sheet.
Replace every skeleton figure with the same character. Preserve the exact 6 columns x 4 rows grid, 256x256 cell rhythm, row order, frame count, pose timing, centering, and safe padding.
Rows: row 0 idle uses columns 0-3; row 1 run uses columns 0-5; row 2 jump uses columns 0-3; row 3 talk uses columns 0-3. Leave unused cells empty/transparent-ready magenta.
Do not reproduce the skeleton, guide boxes, guide lines, center marks, labels, blue safe boxes, gray skeleton lines, red joints, X marks, or guide background.
Same character, same face, same markings, same palette, same outline weight, same proportions across all frames.
Exact RGB 255,0,255 solid flat MAGENTA #FF00FF single-color export background, no vignette, no studio lighting, no backdrop gradient, no texture. No text, no frame numbers, no shadows, no glow, no detached effects, no speed lines, no dust, no scenery.
```

State notes:

- `idle`: breathing/blinking; no waving marks.
- `run`: limb/body motion only; no speed lines or dust.
- `jump`: body position changes only; no landing marks or shadows.
- `talk`: mouth/face/hand gesture only; no speech bubbles or text.

## QA

Before reporting done, visually check:

- base identity matches the assigned character brief.
- all four strips keep the same character identity.
- frames are separated and not cropped.
- frame count, slot width, and pose centering match the matching `references/layout-guides/<state>.png`.
- normalized outputs have exact dimensions: `idle/jump/talk = 1024x256`, `run = 1536x256` for default 256 square cells.
- background is clean chroma color.
- no forbidden detached effects, text, UI, shadows, or scenery.
- `manifest.json` includes source image paths and generation notes.
- if alpha conversion was run, transparent corners are actually alpha-zero and sprite colors are not desaturated.
- for one-shot master sheets, `manifest.json` has a `frame_layout` block written by `scripts/extract_frame_layout.py` and `scripts/validate_sprite_sheet.py` `ok` is true; the game reads `frame_layout`, not uniform `cellWidth` slicing.
- for Hermes live demos, the browser/game-visible file is `manifest.json.game_input`; visible magenta squares are not accepted.
- for Hermes browser smoke, `scripts/check_visible_magenta.py --image <screenshot>` passes.

Report in this exact shape:

```text
sprite_gen_done=<character-id>
folder=<absolute folder path>
files=base,idle,run,jump,talk,normalized
qa_note=<one sentence>
```
