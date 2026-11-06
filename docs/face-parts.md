# `sprite-gen video-face` and `face-bake`: a talking face layer over body motions

This document owns the face request, the face pack, the rules of the face layer, the player `sprite-gen-face-v1.mjs`, `video-face`, and `face-bake`.

A body motion and its talking face are two layers. `video-face` cuts the lower face from a talking clip into a face pack and anchors it on every frame of every body motion. The layer covers the mouth, jaw line, cheeks, and chin down to the neck line. A mouth alone would leave the body's moving jaw and cheeks visible under a closed mouth. The pack's JavaScript player picks face frames for a voice. It draws them live in a browser page, or `face-bake` runs the same player through node and writes the result ahead of time.

## Quick start

Put a reference still, a talking clip, and body clips in a face request. Clip frames are keyed `frame-*.png` files.

```bash
sprite-gen video-face --request face-request.json --out pack/
sprite-gen face-bake --pack pack/ --voice line.wav --out baked/
```

To play a body clip from its start before the line, name it with `--play`. Its own mouth and sound play first.

```bash
sprite-gen face-bake --pack pack/ --voice line.wav --out baked/ --play greet --tags "[surprised]" --video
```

| `video-face` flag | Meaning |
|---|---|
| `--request` | Face request JSON containing the reference, face, talking clip, and body clips. |
| `--out` | Pack folder to write. It must not exist. The pack is written in a temporary folder beside it and renamed when whole. |
| `--jobs` | Processes reading anchors at once. The default is one per CPU. Anchor reading takes most of a pack's time. |

| `face-bake` flag | Meaning |
|---|---|
| `--pack` | Face pack folder. |
| `--voice` | Line voice in any sound format ffmpeg reads. |
| `--out` | Output folder, new or empty. |
| `--play` | Body clip to play from its start before the line, with its own mouth and sound first. |
| `--start-cell` | Loop cell where the line is requested without `--play`. Default: `0`. |
| `--tags` | Line tags, such as `[surprised]`. |
| `--lead` | Seconds of silence before the voice. Default: `0.5`. |
| `--tail` | Seconds of silence after the voice. Default: `0.6`. |
| `--video` | Also write `baked.mp4` with the frames over a flat matte and the sound. |
| `--beside` | Also write `beside.mp4`. It puts the supplied `frame-*.png` files on the left and the bake on the right, with the bake's sound. Supply exactly as many frames as the bake writes. |
| `--video-height` | MP4 height. Default: the frames' height. |
| `--matte` | MP4 background as six hex digits. Default: `2a2430`. |

`face-bake` needs `ffmpeg` on PATH to read the voice and write MP4 files. It needs `node` on PATH to run the pack's player. Without node, it stops and says so.

## The request

The request has kind `sprite-gen-face-request` and version `1`. Paths are relative to the request file. This example uses a 480 px reference.

```json
{
  "kind": "sprite-gen-face-request", "version": 1, "fps": 24,
  "reference": "reference.png",
  "face": {
    "mouth": [240, 262],
    "eyes": [[175, 174, 215, 206], [265, 174, 305, 206]],
    "lower_face": {"top": 228, "box": [120, 228, 360, 400], "seeds": [[195, 255], [285, 255], [240, 305], [215, 280]]},
    "band_bottom": 300
  },
  "talk": {"frames": "talk", "place": [110, 90, 260]},
  "body": [
    {"name": "idle", "loop": true, "frames": "idle"},
    {"name": "greet", "frames": "greet", "mouth_timeline": "greet-mouth.tsv", "sound": "greet.wav"},
    {"name": "cheek", "frames": "cheek"},
    {"name": "reaction", "frames": "reaction", "place": [110, 90, 260], "own_mouth": [0, 3.0]}
  ]
}
```

| Field | Meaning |
|---|---|
| `fps` | Pack frame rate. |
| `reference` | Rest pose and still from which the clips were made. Its pixels define the `face` coordinates. |
| `face.mouth` | Centre of the closed mouth. The mouth reading, open-area zone, and step box sit around it. |
| `face.eyes` | Two boxes in `x0, y0, x1, y1` order. They are excluded from the layer's weight and anchor band. |
| `face.lower_face.top` | Top edge across the cheeks, in reference y coordinates. Weight is zero above it. |
| `face.lower_face.box` | Bounds within which a lower face can be. |
| `face.lower_face.seeds` | Skin points on the cheeks and chin from which the lower face grows. They must sit on reference skin. |
| `face.band_bottom` | Y coordinate below which the anchor band reads nothing. The neck and choker below it belong to the body. A hand counts only above it. |
| `talk.frames` | Folder of keyed, square `frame-*.png` talking frames. It needs six or more frames and can use `video-frames` output. |
| `talk.place` | `[x, y, side]` showing where the talking clip's square frame lies on the reference. Default: `[0, 0, reference width]`. |
| `body[]` | Body motions. Exactly one has `loop: true`; it is the rest motion to which the others return and talks only through the face layer. |
| `body[].name` | Letters, digits, `-`, and `_`. |
| `body[].place` | Placement in the same form as `talk.place`. |
| `body[].sound` | The clip's own sound, copied into the pack. |
| `body[].own_mouth` | `[0, end seconds]`, the initial span during which the clip uses its own mouth. |
| `body[].mouth_timeline` | Tab-separated file with `frame`, `state`, and `sound` columns, with one row per frame from `0`. Use this or `own_mouth`, never both. |

For a mouth timeline, the own-mouth span ends at the first `closed` frame after the last `voiced` frame. A mouth that closes while sound is still voiced is inside a word. `video-face` refuses a timeline if the mouth never closes after its last sound or if its rows do not match the clip's frames.

## What `video-face` does

### 1. Anchors

Each frame of each clip is laid on the reference with the engine's cell resampler and its `place`. An anchor reads the similarity that maps the reference face to that frame: place, size, and tilt.

The anchor band runs from the feather of the reference's own weight to 30 px beyond it. It stays above `band_bottom` and excludes the eyes. It excludes the jaw because the jaw moves during speech.

The first place match uses masked normalised cross-correlation within 48 px. If the best match sits on the search rim, the search extends to 96 px. Size is tried from 0.96 to 1.04 in steps of 0.02, and tilt from -4 to 4 degrees in steps of 1. Each pair is laid through that place and matched again within 3 px. The least cost wins. A parabola through its neighbours refines size and tilt.

A best match on the grid rim is read again on a grid twice as wide: size 0.92 to 1.08 and tilt -8 to 8 degrees. The place required by a size and tilt can be more than 3 px from the first reading. A 3 by 3 grid is therefore read again around the place, size, and tilt it found until the place stays, for at most 4 passes.

Each anchor number is checked against the median of 5 surrounding frames, wrapping around a loop. A reading farther than three scaled MADs and farther than 0.5 px in place, 0.01 in size, or 0.3 degrees in tilt is replaced by the window medians. Anchors depend only on the reference and the clip's own frames. Naming other clips in the request does not change them. `--jobs` runs anchor reading in processes.

The band lies round the reference's own lower face: hair edge, headset, cheeks, and nose. A head that turns moves its hair, headset, and cheeks further than its mouth, so the band's place carries the layer off the mouth. A body frame's place is therefore read again on a ring of skin round the mouth. This applies to body clips only. The talking clip keeps its band anchors, because its jaw moves as it talks and the layer is cut from it.

The ring is the reference's own lower face, its weight, between two ellipses centred on `face.mouth`, with inner half axes 30 by 14 px and outer half axes 60 by 42 px, where the reference is at least 200/255 opaque. The mouth inside the inner ellipse is left out, because a body's closed mouth can smile more or less than the reference's. A body frame is read on the ring when its own mouth, read through its band anchor, is closed. The mouth reading is the one used for talking frames: a mouth 5 px tall or less is closed. Size and tilt stay the band's. Only the place is read again. The frame laid through its band anchor is matched to the ring with masked normalised cross-correlation within 16 px. If the best match sits on the search rim, the search extends to 32 px.

Each read frame's move, ring place less band place, is checked against the moves read among the 5 frames round it, wrapping on a loop, when three or more of them were read. A move farther from their median than three scaled MADs and farther than 0.5 px is a misreading and takes their median. A frame whose mouth is not read closed takes the move of the read frames round it, because an open mouth moves the jaw and the skin round it: linear between the nearest read frame on each side, wrapping on a loop, and before the first or after the last read frame, the nearest one's move. A body clip with no closed frame keeps the band's places.

### 2. Weight

Lower faces are read from the reference and every clip frame, then brought into reference coordinates through their anchors and joined. A lower face is opaque skin lighter than luma 175, grown from the seeds inside the box below the top edge. Its outline and mouth lines stop that growth.

A frame adds no more than 12 px beyond the reference's own lower face. This includes a dropped jaw without growing into a hand or sleeve. Holes from open mouths and lines are filled.

The weight is 1 on the union grown by 6 px. It falls to 0 over the next 10 px with a cosine-squared curve. It is 0 above the top edge and rises to 1 over 14 px. It is 0 on the eye boxes, grown by 6 px and feathered over 5 px.

### 3. Talking frames

Each talking frame is read in reference coordinates through its anchor. The mouth is the dark line or opening nearest `face.mouth`, at least 18 luma below the box's skin and away from headset grey. Its corners are its leftmost and rightmost pixels.

Lift is how far the mouth's top edge in its middle third sits below the chord between the corners, in px. Positive lift means a smile with raised corners. The reading also records height and open area. Open area counts pixels around the mouth more than 40 luma from the reference's closed mouth.

A mouth 5 px tall or less is closed. A closed mouth with lift of at least 0.35 px smiles; below that it is neutral. A closed mouth is one line, not a gap. Omitting one between two frames of the same expression would split that run into two turns.

An open run takes the set of the closed frames on both sides. A run between a smiling and a neutral closed frame turns between expressions and belongs to neither set. The step between any two talking frames is read on the face around the mouth with the engine's loop analysis features, in units of the talking clip's median step.

### 4. Sprites

A talking frame in the smile or neutral set becomes a sprite at the talking clip's own resolution. Its alpha starts with the weight and removes what must remain the body's where the face meets it. This includes dark grey and black reaching inward from the boundary, such as a headset boom or choker, and lines drawn in the feather. The removal prevents a second, faint line. Median colour over the feather moves the sprite's colour onto the reference's colour.

A sprite leaves out the weight's outermost 2 px, where the weight is under 0.1. Mapping a sprite to the talking clip's pixels and back spreads its edge by a pixel, and nothing may change outside the weight. Where a sprite does not cover a pixel, the lay leaves that pixel exactly as it was, including the colour a keyed cell keeps under no coverage.

### 5. Body frames

Each body cell is copied with its anchor and a keep image for anything that must stay above the face layer. The keep contains the body's own dark grey and black reaching inward from the boundary, its lines in the feather, and a hand. For both the inward dark grey and black and the hand, the body's own mouth line or opening is read like a talking frame's mouth, filled and grown 2 px; the dark grey and black exclude it, and the hand uses it to separate mouth skin from hand skin. A dark outline can join the mouth's rim to the outside through a finger's outline, a palm, and a sleeve when a finger touches the mouth's corner. The mouth belongs to the layer, and nothing of it stays on top except where a hand meets it.

A hand differs from the reference face by more than 48 in any channel and reaches inward from outside the layer. The frame's light, warm skin is divided into pieces using drawn lines as walls. Teeth and white streaks do not count as skin. The piece that overlaps the reference's lower face most is the face. Other skin pieces touched by the outside piece belong to the hand, with their surrounding lines. A skin piece that lies half or more inside the body's own mouth belongs to the mouth, even when a finger at the corner joins it to the hand, and the layer covers it. A hand counts where at least 400 px lie on the weight's flat part above `band_bottom`.

The body's own open mouth is kept only where it meets a hand. The face's own skin is never counted as hand skin.

### 6. Entry

The clip's own mouth is read at the end of `own_mouth`, or at the first frame if there is no such span. The same 0.35 px lift line selects smile or neutral.

The rest is a closed talking frame in that set from which the line opens and to which it closes. The nearest lift wins. Among closed frames within 0.5 px of that nearest lift, the one with the smallest switch wins.

### 7. Switch

For every body frame and every rest used by the pack, the switch measures how far laying that rest moves the face. It divides by the larger of the body's median step and the talking clip's median step. A body that only breathes can step much less than a talking face. The layer comes on or goes off only when the switch is within the jump line, 1.4.

### 8. Rules

The pack records the flap's numbers, the pick's costs, the open area wanted for each mouth level, the jump line of 1.4, and the enter line. Level `L` of 6 wants `L/6` of the talking clip's widest open area. The enter line is the talking clip's largest step divided by its median step.

## The pack

The pack has kind `sprite-gen-face-pack` and version `1`. `face-bake` checks every listed file against its SHA-256 digest. It refuses a pack changed after creation.

| Path | What |
|---|---|
| `face-pack.json` | FPS, size, rules, requested face geometry, and every talking frame's set, open area, mouth reading, sprite file, and map to the reference. It also contains the two sets and their closed frames, the step matrix, body cells, anchors as maps from the reference, each body row's `ring`, every frame's move in px as x and y, and `ring_read`, the frames read on the ring, keeps, `own_mouth`, entry and switch tables, own sounds, player, and every file's SHA-256 digest. |
| `sprite-gen-face-v1.mjs` | Player copied from the package. |
| `face/frame-NNNNN.png` | Sprites indexed by talking frame. |
| `body/<name>/frame-NNNNN.png` | Body cells. |
| `body/<name>/keep-NNNNN.png` | Parts of a body cell that stay on top, where present. |
| `body/<name>/sound.wav` | A clip's own sound. |
| `weight.npy` | Layer weight in reference coordinates. |
| `report.json` | Per clip anchor ranges; band residual median, p90, and maximum with anchor, place only, and no alignment; misread frames; frames using the wide grid or wide search; per body clip, `ring_read`, how many frames were read on the ring, and `ring_move`, the median, p90, and maximum length of the move; and hand frames. It also records expression sets, each body's entry, and how many frames can switch on for each rest. |

## The player

`sprite-gen-face-v1.mjs` is the one place where the line, pick, and scene rules run. It exports these functions for browser and node.

| Export | Result |
|---|---|
| `mouthTrack(samples, sampleRate, rules, { fps, tags })` | Voice mouth levels from 0 to 6, one per frame. |
| `pickFaces(pack, levels, set, rest)` | A talking frame in one set or its rest for each output frame. |
| `planScene(pack, { play, start, levels, layer })` | Body, cell, face, and layer state for each output frame. It also returns `own_mouth`, the own-mouth end frame; `line_start`, the frame the voice and layer start; and `wait`, the frames waited after the own mouth. |
| `loadFacePack(url)` | Loads a pack. |
| `createFacePlayer(canvas, pack, { audioContext })` | Live player with `start()`, `stop()`, `play(name)`, and `say(voice, { tags })`. |

### The line

The voice is folded to mono. Loudness is RMS over 1/48 s windows sampled every 1/96 s, smoothed over three windows, and divided by the line's loudest value.

A syllable is a peak above 0.12 that stands 0.05 above the lower of its neighbouring dips, searched 10 steps to either side. If two syllables are within 1/12 s, the louder remains. A syllable opens to level 2 through 5 according to its loudness within the line's range. With `[surprised]` or `[gasps]`, a syllable louder than 0.85 opens to level 6.

The peak level lands on the frame one ahead of the syllable. The preceding frame gets half that level, rounded up. The following frame gets two fifths, rounded down. Other frames are closed. No frame changes by more than 3 levels; a neighbour is raised instead of cutting an opening. Rounding follows Python's rule: halves go to the even neighbour.

### The pick

The pick uses Viterbi over each frame in one expression set and the rest, for every output frame. It starts and ends on the rest. Frames outside the set are never shown, so the expression entered for a line remains its expression.

A frame normally advances to the talking clip's next frame. Holding costs 0.6. Jumping to a frame within 1.4 median steps of that next frame costs 1.5 plus the step. Entering from rest or exiting to it costs 1.0 and is allowed only within the enter line.

A frame's cost is the distance between its open area and the area wanted by the level, measured in one level's area. Opening less than wanted costs three times as much as opening more. The rest costs the wanted area.

### The scene

A line requested during a body clip's `own_mouth` span waits until that span ends. The clip's own mouth and sound play through it.

The body at the end of that span names the line's set and rest. The voice and layer start together at the first frame whose switch to that rest is within the jump line, 1.4. Until then the clip shows as it is, with the layer off and no voice playing. A voice never plays on a frame whose mouth cannot move. If the layer is already on, the voice and layer start at once in its current set and rest. A clip that plays once returns to the loop from the loop's first cell. If no suitable frame occurs within the body clip and one round of the loop, `planScene` refuses the line and the player throws.

### Live

The audio context supplies one frame clock. `play(name)` starts a body clip and its own sound. `say(voice)` decodes the voice, computes levels, and plays it at the plan's `line_start` with the layer. The player keeps one queue of every `play` and `say` request, in the order the calls are made. As soon as a request is made, its sound starts to load and decode while the request waits: the clip's own sound for `play`, the voice for `say`. A request whose sound arrives late keeps its place in the queue.

A request starts on the frame after the request before it has ended. A line ends when all of its frames have shown, and its voice has ended by then. So two voices never overlap, and a line never replaces another line's plan. There is one exception. A line placed right after a clip with its own mouth is planned together with that clip, the way `planScene` plans a clip and a line. This is the same plan that `face-bake --play` bakes. The line's voice and layer start at the plan's `line_start`, after the own-mouth span. The clip's scene then ends with the line. The exception needs the line's voice to be decoded before the clip's own-mouth span ends. It also applies when the line was requested just after `play`, before the clip started. If the voice is decoded only after the span has ended, the line waits for the clip to end, like any other request. Only the first line after such a clip joins it. A line requested after that waits until the first line has ended.

Each call returns a promise that resolves when its scene ends. When a line joins a clip, both promises resolve together. The queue has no length limit and drops nothing. A request whose sound does not load or decode, or whose line `planScene` refuses, rejects its promise when its turn comes. The next request then goes on.

After a line, the layer stays at its rest until a loop frame can switch off within the jump line. Body cells load when first drawn. Each frame draws the body cell, then the sprite using the cell anchor times the sprite map as a canvas transform, then the keep image. These are plain source-over draws. Sprite colour is already matched in the pack, so live drawing does no colour step.

## `face-bake`

`face-bake` loads the pack and checks its digests. It decodes the voice to mono 48 kHz with ffmpeg. It adds `--lead` and `--tail` silence after the mono fold, so a stereo voice cannot delay one channel alone. Through the package's `face_plan.mjs`, node runs the pack's own `mouthTrack` and `planScene`. The bake lays each frame as the live page does.

With `--play`, the sound track contains the clip's own sound for its `own_mouth` span, then silence while the line waits, then the padded voice.

| File | What |
|---|---|
| `frames/frame-NNNNN.png` | RGBA frames at the pack's size. |
| `voice-baked.wav` | Whole sound track, mono 48 kHz, as long as the frames. |
| `voice-padded.wav` | Voice with lead and tail silence. |
| `bake.json` | Per frame body, cell, face frame, layer state, level, pixels changed outside the weight, and composite, body, and layer steps. It also records set, rest, `own_mouth_frames`, `line_start`, `wait_frames`, levels, `over_line`, `over_sum`, `held`, and verdict. |
| `baked.mp4` | Frames over the matte with sound, when `--video` is set. |
| `beside.mp4` | Supplied frames on the left and bake on the right, with bake sound, when `--beside` is set. |

Steps are read on the face around the mouth in reference coordinates. A composite step over 1.4 times the largest of the body's own step, the layer's own step, and the body's median step is `over_line`. Body and layer motion in one frame can add up even when each is within the line. A frame that is `over_line` and also exceeds the sum of the body and layer steps is `over_sum`.

The body is also read for a picture shown again: a frame whose body cell shows the picture of the frame before, so the body stands still for a frame mid-motion. Two cells are one picture by `held_fill`'s rule ([loop repair](loop-repair.md) section 7), read on the cells as the pack has them. `bake.json` records it as `held`: `frames`, `poses`, the thresholds, and `held_runs` (`start`, `length`, and `body`, the clip and cell of each frame). A run a clip means is recorded with a `why` and a `kind` and not warned of: one longer than three frames (`motion-hold`), the opening or closing pose of a clip played once (cells of one clip that is not the loop, from its first cell or to its last), or cells a body row of the pack lists as `holds` (`from`, `to`, both in, `kind`, `reason`, as a strip's `holds`). Any other run, within a clip, across the loop's wrap, or where a clip comes back to the loop, prints `face-bake: warning: frames A-B show one body picture (…)` with the cells and the poses over the frames. The bake makes no frame: the pack's cells are baked as they are, and the verdict does not change. Make the pack from frames whose held cells are made again (`video-loop --fill-held auto`).

If the planner finds no suitable frame within the body clip and one round of the loop, `face-bake` stops and names the reason. The bake is refused with exit code 1 and verdict `refused` if any frame is `over_sum` or if any pixel outside the layer's weight changes. The frames are still written for inspection.

## What it takes from sprite-gen

| Part | Taken from | Why / owner |
|---|---|---|
| Keyed clip frames | `sprite-gen video-frames` | The request uses its `keyed/` folders. |
| Clip frame laid on the reference at another size | `util.resample.resize_cell` | Cell resampling owned by `docs/video-pipeline.md`, "Cells". |
| Sprite mapped onto a body frame | `util.resample.transform_cell`, after one `resize_cell` when laid smaller than 0.9 | Cell transform owned by `docs/video-pipeline.md`, "Cells". |
| Anchor place match | `video.auto_motion._match_costs` and `_best` on `video.motion_anchor._features` | Same masked normalised cross-correlation and nearest-offset tie rule as the `video-loop` motion anchor. |
| Frame steps | `video.loop._small_features` | Loop analysis features. |
| Jump line | `video.stop.SEAM_MAX` | Stop and loop jump line, 1.4. |
| Mask growth and pieces | `video.parts._dilate`, `_erode`, and `_pieces` | Tail mask morphology. |
| Packaged JS player and digests | New code here | One rule in JS, copied into the pack. |
| JS run from Python | node, as in the breathe JS mirror test | Runs the pack's player for a bake. |
| Similarity anchor with size and tilt | New code here | The engine has no size and tilt reading. |
| Mouth reading and expression sets | New code here | The engine has no mouth reading. |
| Hand keep | New code here | The engine has no hand over face reading. |
| Flap and pick | New player rules here | Moves the rules into the player. |

## Limits

A three-quarter head turn is not a similarity transform. The anchor reads it as well as a similarity can. `report.json` gives the band residual; a large residual means visible seams.

A hand is kept only where the frame draws its outline. A finger without a line separating it from the cheek becomes part of the face skin and is covered by the layer.

There is no transition yet from a smile back to neutral. After a line in the smile set, the layer rests smiling until a loop frame is within the jump line of that rest. A neutral loop may never reach that condition.

Body cells are carried at the reference size.
