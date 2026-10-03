# Changelog

All notable public changes to `sprite-gen` are recorded here. Versions track the `version:` field in `SKILL.md` and `pyproject.toml`.

## v2.18.0 - A walk's jumping frames are remade by RIFE, its jolt is measured, and a direction set shares one cycle

- **Jump frames are repaired** (`video-loop --repair auto`, the default for walk and run): a step whose coverage change is 1.4x the loop's median, over the whole body or the hair behind it (`--facing left` mirrors that box), is a jump, and the frame that broke it is replaced by the RIFE frame half way between its two neighbours — at most three, worst first, never next to a frame already made; every other frame stays the video's own. A single stray frame, which breaks the step into it and out of it, is the one remade. The report's `jump_repair` names the frames, the scores before and after and the interpolator; with a frame replaced the seam gate reads the rendered cells. `--repair off` cuts as filmed. On the 2026-10-03 eight-direction set it replaces the frames the experiment did in 8 of 10 views (the other two differ by one frame).
- **RIFE** is `rife-ncnn-vulkan` 20221029 with `rife-v4.6`, found by `SPRITE_GEN_RIFE` or `PATH` and located only when a frame is to be made; a loop that needs it and finds none fails naming `--repair off`. Colour is interpolated premultiplied and coverage separately. Measured: 0.33-0.41 s a call on an M4 Max GPU; 2.8-3.0 s in a Modal Linux container through Mesa llvmpipe, with the same frame; the binary's own CPU path (`-g -1`) is wrong and never passed. Code and weights are MIT. CI installs it on llvmpipe.
- **The jolt index is reported on every walk and run** (`jolt` in the loop report), read after the repair: how far each step strays from its neighbours' mean over the median step, the hair's, and the head's frame-to-frame moves sideways and up and down in % of the body height (largest, median, largest over median, range). Beyond the reference bounds (index 0.43, head sideways 0.75 %) it is a warning line and the loop is kept; `--jolt-max` / `--head-step-max` turn a bound into a gate (`video-loop: loop jolts — …`). As a default gate the bounds would have refilmed 16 of 30 Lite takes on two refusals' evidence, and the index does not separate an alternating side walk from a kept Pro walk (0.414), so they stay a reference.
- **One cycle per direction set**: new `sprite-gen video-cycle-align --loop-dir … --loop-dir …` resamples every loop to the set's median length at times k·L/L* — a time on a source frame keeps that frame, only times between frames are made by RIFE — turns each to start on a foot strike, and rebuilds its cycle, strip, GIF and WebP in place (`cycle_align` in `strip.json`). The cut as filmed is kept in `cycle.source/`, so a second run never resamples a resampled loop. `video-set` runs it for every walk or run filmed in two or more directions (`--align-cycles auto`, default). Strip metadata now records `cell_height_cap`.
- **Measured walk defaults**: `build_prompt(model=...)` calms a Lite walk (`LITE_WALK_TEXT`; Lite read "walks naturally" as a run) and holds a Lite back-diagonal walk's head (`LITE_HEAD_TEXT`; sideways sway 2.7-3.8 % fell to 1.7-3.1 %, Pro 0.88 %); Pro prompts are unchanged. A front or back walk films from its base still redrawn mid-step (`WALK_START_TEXT`, `walk_start_prompt`, `starts_mid_step`; 1 of 14 front clips walked straight from a standing still, 14 of 16 from a mid-step one); `video-set --walk-start redraw` (default) draws it with one `sprite-gen gen --ref` call.
- Method, measurements and the regression on the eight-direction set: [docs/loop-repair.md](docs/loop-repair.md).
- **`video-frames --decontam palette` adds no edge contact**: within the edge band the gate reads (4 px of the top, left and right edges), a pixel the matte left transparent stays transparent (`decontaminate(edge_band=...)`, `cutout(decontam_edge_band=...)`, `edge_band` in the report's `decontam`). A decoded frame's blur runs the subject on into the key as a smooth tail that the video fit reads as a faint blend, so a head a few pixels under the frame edge got faint flank coverage on the edge band and a clip that passes with decontam off was refused with it on, as "the key survived the matte". No floor tells that tail from a strand's side, so the rule is positional; the flank keeps what it recovers everywhere else, and the still fit is unchanged. [docs/chroma-alpha.md](docs/chroma-alpha.md#decontam--give-the-edge-the-subjects-own-colour-back).

## v2.17.0 - Two three-quarter views: a character walks down and up to the right as in an isometric game

- `front_diagonal` and `back_diagonal` join `side`, `front` and `back` (`VIEW_TEXT`): three-quarter views turned to the right, the two an isometric game adds between the side and the front or back. Turned over (`video-loop` outputs mirrored), they give the left-hand two, so five views make eight directions.
- A walk or run in a diagonal names its heading on the screen (`VIEW_MOTION_TEXT`): "heading diagonally away from the viewer toward the upper right, like a character walking up and to the right in an isometric game … It keeps the exact three-quarter back angle of the image the whole time … It never turns into a side view." It is filmed pinned to its first frame with the return sentence (`PINNED_GAIT_VIEWS`, `pins_last_frame`, used by `video-set` too) and cut as any walk. A caller's own gait in a diagonal is held to the angle (`GAIT_HOLD_TEXT`).
- A still drawn at a diagonal from another picture has its own view sentence (`STILL_VIEW_TEXT`, `still_view_text(direction, facing)`): the whole body and head turned about 45 degrees, the face hidden for the back one, the feet pointing the same way. The clip's view sentence points at the image instead ("turned exactly as in the image").
- Measured on the API, 480p 3 s, pinned, five humanoid characters twice each: the front and the back diagonal walk kept their angle in 10 of 10 each; the sentence that only said to walk "toward the way its body faces in the image" kept a back diagonal in 0 of 2. Idle and attack keep a diagonal angle (4 of 4). A diagonal run kept it in 2 of 4. Method: [docs/video-pipeline.md](docs/video-pipeline.md).

## v2.16.0 - Every walk and run moves naturally in place, and a caller's own gait is held there

- Every walk and run, from every view, now says only that it moves naturally, which way it faces and that it stays in place (`MOTION_TEXT`, `VIEW_MOTION_TEXT`): the side walk "walks naturally in place, as if on a treadmill, without moving across the screen", the side run "runs naturally in place …", and the front and back runs "runs naturally in place, facing (away from) the viewer, as if on a treadmill …". The side sentences said "moves in place on a treadmill" as a place (the form that sometimes drew a treadmill under the feet) and the side walk asked for "an even left-right or front-back rhythm" (the wording that made front walks step sideways before 2.13); the front run spelled its strides out (the form that made front walks march before 2.15) and said "toward the viewer" and "without coming any closer" in one clause. On one character, 480p 3 s on the API: the front run kept facing in 2 of 2, the side run ran in 2 of 2, the back run kept facing away in the one clip whose frames passed, and the side walk walked in 2 of 2.
- A caller's own walk or run paragraph (`build_prompt(motion=...)`) is followed by `GAIT_HOLD_TEXT` for its view: it stays in place as if on a treadmill and keeps facing the viewer, away from the viewer or `{facing}` the whole time. A request interpreter can now ask for a sneak, a march or a lazy stroll and the engine keeps it on the spot and turned the way the image faces, as it already held an attack's grip (`HOLD_TEXT`). On one character, one clip each: a march lifted the knees high and stayed in place, a stroll from behind kept facing away, a tiptoe sneak kept facing the viewer but read only as a slightly hunched walk.
- `build_prompt(..., pinned=True)` gives any state the pinned template (`PINNED_LOOP_TEXT`, the return to the first pose) instead of the evenly paced repeat; left out, only `PINNED_LOOP_STATES` get it, as before. A caller that pins a walk or run (`video --last-frame`) says so; two pinned front walks closed on their first frame. Method: [docs/video-pipeline.md](docs/video-pipeline.md).

## v2.15.0 - A front walk walks naturally, and a slow or drifting walk still finds its loop

- A walk seen from the front or from behind now says only that it walks naturally, which way it faces and that it stays in place (`VIEW_MOTION_TEXT`): "walks naturally in place, facing the viewer, as if on a treadmill, without coming any closer". The 2.13 sentence spelled out the steps, and some takes marched in place with the knees lifted to the waist and the arms held stiff. On one character, two clips per sentence on the API: the 2.13 sentence marched in one of two; "walks naturally" swung the arms, kept the knees low and kept facing the viewer in both. How a character walks is the caller's to say, so the default stays generic. The front run keeps its sentence. [docs/video-pipeline.md](docs/video-pipeline.md).
- When `video-loop --anchor motion-auto` finds no walk or run cycle, it now looks once more (`sprite_gen/video/gait_fallback.py`). A front walk asked to stay in place sometimes comes toward the camera, and the body grows through the clip, so the same pose never matches itself in size: when a straight-line fit of the subject's height changes by 3 % or more over the clip, every frame is scaled back to the first frame's height about its foot point, and the search, the cells and the seam gate all read the scaled-back frames. A calm walk in long clothing can also take longer than half the clip per cycle, the most the first search confirms: the second search allows up to 60 % of the clip and at most 2 s (43 frames of a 3 s clip at 24 fps, where the first stops at 36).
- Only a failed search reaches this, so a clip that looped before is cut exactly as before, and every gate, the seam limit included, is unchanged. An explicit `--max-len` stays the ceiling. The report records what was tried in `gait_fallback`; when the second search fails too, the error still starts with `video-loop: no periodic cycle found` and says what was tried.
- Measured on six stored 3 s front walks that had failed with `no periodic cycle found`: four loop now. Three takes of a kimono that hides the legs walked a 27 to 39 frame cycle (one clip grew 3.9 %, two grew 11 % and 16 %), and a front horse walk grew 6.4 % with a 42 frame cycle. One of the other two still shows no repeat; the other finds a 38 frame candidate that the seam gate refuses. On the walk clips we have kept, the median height change is 0.5 % and nine in ten stay under 2.7 %. Method: [docs/loop-review.md](docs/loop-review.md).

## v2.14.0 - A single still can be drawn to a layout guide with crown and floor lines

- `gen --layout-guide` attaches the one-slot form of the row layout guide after any `--ref`: the frame, the inner safe box at the row guide's 9.4 % margin, the centre line, and an orange crown line and a teal floor line one margin inside the safe box (about 19 % and 81 % of the frame height). The prompt says what each line means: the anatomical top of the skull on the orange line, ignoring hair, hats, raised limbs and props; the supporting soles on the teal line, which is a ground-contact line and not a frame edge; nothing of the guide in the output. A still drawn from words tends to fill its frame top to bottom, and an in-place motion then has no room above the head. The lines sit inside the box because on its edges a crown line leaves hair and hats to fill the room above it and a floor line reads as a border the feet float above.
- The guide is an attached image: openai sends the call to `/images/edits` and bills the guide as image input, and `--transparent` steps down to chroma keying as with any `--ref`. Models differ in how closely they follow it; measure the one you use. The report records the guide's cell in `extra.layout_guide`. Usage: [docs/gen.md](docs/gen.md).

## v2.13.0 - A front or back walk walks straight, and a walk loop is cut in seconds

- A walk seen from the front or from behind, and a run seen from the front, now have their own sentence (`VIEW_MOTION_TEXT` in `sprite_gen/video/batch.py`): the steps lift and land straight forward and back under the body, toward the viewer or away from it "as if on a treadmill", and the body never turns to the side, steps sideways or crosses its feet. A walk also asks for a calm, natural cycle with small, even steps that never kick a leg out to the side. The walk sentence every view shared asked for "an even left-right or front-back rhythm", and a character facing the viewer read it as stepping sideways: crossed feet, side kicks, a body turned to three-quarters. A side view and a run seen from behind keep the state's sentence.
- Measured on Grok (subscription), 480p 3 s, nine humanoid characters twice each, judged on frame sheets with the variant hidden: a front walk kept its facing and walked in 7 of 18 clips with the old sentence and 16 of 18 with the new one (12 to 13 of 18 without the calm-steps clause, which still crossed its feet or kicked sideways in five); a back walk in 8 of 10 and 10 of 10; a front run in 11 of 18 and 15 of 18. "on a treadmill" said as a place was sometimes drawn under the feet; "as if on a treadmill" was not. A walk drawn at a three-quarter angle still turns to a side view in about half the clips with every sentence tried. Method: [docs/video-pipeline.md](docs/video-pipeline.md).
- `video-loop --anchor motion-auto` finds its two tracking regions with FFT correlations instead of copying the masked patch out once per offset. On three 3 s walk clips at 544 px the region search took 1.0 to 1.4 s of CPU instead of 22.6 to 37.4 s, and full loops from the same frames gave the same cycle and byte-identical strips. Costs agree with the direct form to about 1e-6, so two offsets that tie within that may swap. [docs/loop-review.md](docs/loop-review.md).

## v2.12.1 - The error body is masked as the provider sent it, before it is put on one line

- The error body is now masked string by string, as the provider sent it, before sprite-gen puts it on one line. v2.12.0 masked the finished line, where JSON, `repr` and the line join had already written a line break, a tab or another control or invisible character as `\n`, `\t`, `\x..` or `\u....` (and the video verbs' JSON fallback any non-ASCII character as `\u....`). A key, a `Bearer` value or a URL query right after one of those kept a letter or digit in front of it and was printed as sent, so v2.12.0's "only a letter or digit in front makes `sk-`/`xai-`/`bearer` part of a word" was wrong there. It holds now, and the letter or digit is the provider's own: `task-…` and `xbearer …` are still words, and so is a key after the two characters `\n` or `%0A` the provider wrote itself. A `Bearer` value after a tab or a line break, and the credential the request carried when it holds a quote or a non-ASCII character, are masked too. An API key or login token of the usual shape that the request carried was masked in every case in v2.12.0 too, and still is (a number in the body equal to it included).
- A URL query is masked wherever the URL starts; after `_`, a letter or a non-ASCII letter it was printed as sent.
- Nothing else in the line changes: a body with nothing to mask prints byte for byte what v2.12.0 printed. When two field names of one error object mask to the same text, the line keeps the later one. A body nested some 500 levels deep can exceed Python 3.11's recursion limit while it is masked; the line then turns into a traceback (with no body text in it) instead of a refusal line. Provider error bodies are a few levels deep.

## v2.12.0 - A refusal names the provider's code, and says when it was the content policy

- A provider refusal line now carries the provider's own code after the status: `openai-gen: image request failed (HTTP 400) code=invalid_value; no retry or provider fallback`. The code is `error.code` (or, for xAI's synchronous errors, a top-level `code`) and is printed only when it matches `^[A-Za-z0-9_.:-]{1,64}$`. The video verbs read the code out of a failed poll's `error` object too.
- The image paths now print the provider's error body too, as received, at the end of the line: `openai-gen: image request failed (HTTP 400) code=invalid_value; no retry or provider fallback: {"error": {...}}` (JSON on one line, other text with its line breaks as `\n`, nothing for an empty body; this includes 401/403). Before, the body was dropped and a failure was hard to diagnose. Two things are masked with `[redacted]`: strings shaped like an API key (`sk-…`, `xai-…`, a `Bearer` value, the credential the request carried; a key the provider already masked is left alone) and URL query strings, where signatures live. The video verbs, which already printed what xAI said, now go through the same mask, applied before their 300-character cut so a key the cut would split is still masked. A key or a `Bearer` value right after `_` or `-` (`api_key_sk-…`, `x_Bearer …`) is masked too; only a letter or digit in front makes `sk-`/`xai-`/`bearer` part of a word. The body can repeat the prompt: a product that shows sprite-gen's stderr to its own users decides what to keep. Everything in front of the body is unchanged.
- A code the provider documents as a content-policy block turns the line into `<verb>: refused by the provider's content policy (HTTP <n>) code=<code>; no retry or provider fallback`, so a caller can tell "change the request" from an auth, quota or parameter error by one fixed phrase. OpenAI's `moderation_blocked` is the only such code (the image guide calls `error.code` the stable discriminator); its `moderation_details.moderation_stage` follows as `moderation_stage=input|output|unknown`. xAI's `invalid_argument` is not one: it covers moderation and parameter errors alike.
- An xAI clip that finishes with `video.respect_moderation: false` is now `<verb>: generation <id> refused by the provider's content policy (HTTP 200) respect_moderation=false; nothing was written` instead of "done but reported no video url". The xAI REST image answer has no moderation field, so a grok image refusal carries its code only.
- `(HTTP <n>)` is unchanged, and the body comes after it and everything else, so anything that finds the status with `\(HTTP (\d{3})\)` reads these lines as before, whatever the body says. The policy table is `sprite_gen/gen/refusal.py` `POLICY_CODES`. Line shape: [docs/gen.md](docs/gen.md).

## v2.11.0 - An attack is one strike, and its whole clip is the loop

- `MOTION_TEXT["attack"]` asks for one attack (windup, strike, held impact pose, recovery to the exact starting stance) instead of the same attack twice, and `build_prompt(motion=...)` no longer adds a repeat sentence after a caller's paragraph (`REPEAT_TEXT` is empty; `HOLD_TEXT` still follows it once). The clip is pinned to the canvas as before, so it runs stance -> strike -> stance, and `video-set` cuts it whole with `video-loop --cycle pinned`, as it already did an idle (`PINNED_LOOP_STATES` = idle, attack). The attack keeps its own template (`ACTION_TEXT_STATES`). Before, the one-shot or period search cut a two-strike clip into loops that started mid-strike, held both strikes, or kept only the held pose, and some of those passed the seam gate.
- `video-set` asks attack clips for 2 s (`STATE_DURATION_SECONDS`), down from 4. One strike at the stated timing is about 1.6 s; asked for 3 or 4 s, the clip still struck once and held the impact pose for about half of it.
- Measured on Grok (subscription), 13 characters pinned to their canvas: 13 of 13 loops at 2 s in 480p and in 720p, 13 of 13 at 3 s, 12 of 13 at 4 s (one clip touched the frame edge); every loop is the whole clip and starts on the ready stance. A 2 s clip is half the video seconds of the 4 s one.

## v2.10.3 - An attack keeps what the other hand holds where it is drawn

- What stays still in an attack is one sentence, `HOLD_TEXT["attack"]`: every grip stays the one the image shows (one hand stays one hand, both hands stay both hands, nothing let go or switched to the other hand), a hand the strike does not use stays where it is drawn with whatever it holds, and the body keeps facing the same direction. The built-in attack sentence ends with it instead of naming the grip twice, and `build_prompt(motion=...)` now adds it once after a caller's own motion paragraph, before the repeat count. A request interpreter writes its motion before the still exists, so it cannot know where a shield or a lantern is drawn; asked to keep "the shield raised in front", a clip brought a shield drawn behind the body to the front and the strike read as a shield bash. The caller writes the choreography, the engine says what stays put.
- Measured on Grok (subscription), 4 s 480p pinned to the canvas: a sword-and-shield knight whose shield is drawn behind the body kept it there in 4 of 4 clips with the built-in sentence and 6 of 7 with a caller's choreography followed by the hold sentence. Characters holding a shield and a hand axe, a spear and a shield, and a lantern and a dagger kept the second item where it was drawn, and a two-handed axe stayed two-handed. Method: [docs/video-pipeline.md](docs/video-pipeline.md).

## v2.10.2 - The spill judgment keys only the subject, and decontam keeps gold

- `video-frames --spill auto` (and `video-set`, which judges each item's `canvas.png`) keys only the subject window of the reference still instead of the whole still. A canvas padded for motion room is mostly key, and keying all of it cost about 0.14 GiB more per megapixel of canvas, past 4 GiB for a 30-megapixel canvas, while keying the frames themselves stays near 1 GiB. The window is the box of pixels the hard cut cannot erase plus one keyed pixel all round, and the painted key is read on the whole still, so it gives exactly the whole still's counts: the decision and the frames do not change. A window over 6 megapixels is judged on every n-th pixel instead; the report records `reference_size`, `reference_window` and `reference_stride`. Background-key detection now reads an image a band of rows at a time. On synthetic padded canvases the judgment peaks at 0.42 to 0.56 GiB from 8 to 39 megapixels, and a synthetic 1080p clip judged against a 39-megapixel canvas runs `video-frames --decontam palette` in 1.1 GiB.
- `--decontam` keeps gold and yellow on stills. A gold a little darker or yellower than the palette's own read as a warmer palette gold with some green mixed in, so gold edges and small gold drops came out orange and partly see-through. The still fit now counts a palette colour at the pixel's own luma as the subject's colour within the subject's own spread (how far its interior sits from the palette); a pixel deeper than a still's antialiased edge (2 px) without the key's hue, or an edge pixel with the colour of the material right behind it, is the subject's own colour; and where the matte changed such a pixel it gets its source colour and coverage back. That last part matters for `cutout`, whose RGB matte scores key tint on the channel average: that calls yellow green, so the matte unmixed gold by itself. On a synthetic gold scene every decontam path now matches the clean YCbCr matte, where `cutout --decontam palette` left 39 % of the solid gold edge see-through. On the procedural ground-truth set key contamination falls from 0.07 % to 0.03 % (RGB matte) and from 0.16 % to 0.12 % (YCbCr matte), halos shrink and recall stays the same. The video fit is unchanged. Reports gain `material_spread` and `restored_px`. Method: [docs/chroma-alpha.md](docs/chroma-alpha.md#decontam--give-the-edge-the-subjects-own-colour-back).

### Gold showcase

A synthetic gold scene (the fixture of `tests/frames/test_decontam_gold.py`) through `cutout --decontam palette` (RGB matte) and `gen`'s YCbCr matte with decontam, v2.10.1 then v2.10.2, on dark and on white.

![Gold drops and strokes before and after](https://github.com/aldegad/sprite-gen/releases/download/v2.10.2/decontam-gold.gif)

## v2.10.1 - Attacks keep the grip the image shows

- `MOTION_TEXT["attack"]` no longer names one hand. What the subject holds is kept exactly as gripped in the image — one hand stays one hand, both hands stay both hands — and is never let go, switched to the other hand or taken in an extra hand. Naming the hand nearest the viewer for a weapon the still draws in both hands made the clip let go of it on the windup and grab it again for the strike.

## v2.10.0 - Edge decontamination gives thin strands their own colour back

- Add opt-in edge decontamination, `--decontam auto|palette` (request `chroma.decontam` for the row extractor and `inspect`). After the matte, each edge pixel is explained as a blend of the locally measured key background with one colour from a palette learned on the subject's own interior. The colour written is that palette colour at the pixel's observed luma, so thin hair strands and outlines come back in the subject's hue: not green, and not the orange that despill's channel gain produces. `palette` demands the pass and fails where it cannot run; `auto` runs it wherever it applies and records why it did not elsewhere. Available on `cutout`, `gen --transparent` (chroma keying), `video-frames` / `video-set` (video fit, one palette per clip) and the row extractor. Every default stays `off`, which returns existing output byte for byte. On a procedural ground-truth set, key contamination at the edge falls from 22.5 % to 0.07 % on stills and from 33 % to 11 % on H.264 frames; halo against the true composite shrinks on dark and white backgrounds, and strand recall rises from 84 % to 99 % (stills) and from 82 % to 91 % (frames). Method, guards and limits: [docs/chroma-alpha.md](docs/chroma-alpha.md).

## v2.9.0 - Idle stands still and closes on its first frame

- `MOTION_TEXT["idle"]` keeps both feet planted flat for the whole clip, limits the motion to breathing, a settle of the arms, hair and loose cloth and one blink, and names walking and marching in place as what not to do. A side-view full body asked for "a subtle weight sway" and an evenly paced loop tended to step in place.
- Idle is pinned to end on its first frame (`PIN_LAST_FRAME_STATES`) and its template (`PINNED_LOOP_TEXT`) asks for that return instead of an evenly paced repeating motion.
- `video-loop --cycle pinned` keeps a pinned clip whole as the loop (every frame but the last, which re-renders the first) and gates it on the pin: the last frame has to land within `max(seam_max x the mean step, PIN_NOISE_MAX)` of the first. A near-still idle moves so little per frame that the seam ratio read its re-render noise as a jump. A clip that ends in another pose fails with its own message. `video-set` cuts idle this way (`PINNED_LOOP_STATES`).

### Idle showcase

Nine side-view idles before and after this release, each strip played at its own speed (`idle-nine.mp4` is the same comparison as video).

![Nine idle loops before and after](https://github.com/aldegad/sprite-gen/releases/download/v2.9.0/idle-nine.gif)

## v2.8.1 - Tight clips pass their own edge and corner checks

- `video-frames --allow-subject-edge-contact` fails only on a frame where the key alone reaches the edge. A frame where the subject touches the edge carries key-tinted pixels beside it (its antialiased fringe, or the key reflected on metal) and is accepted with them, the same reading a mixed contact already had in the refusal message. Before, a few such pixels failed a tight clip as leftover background.
- `video-loop` keeps its 8 transparent columns on both sides of every cell even when the subject reaches the frame's side edge, so no cell corner is the subject and the GIF/WebP corner check holds. A crop that stays inside the frame is unchanged.

## v2.8.0 - Tight framing for a fixed body height

- `video-canvas --fit tight` frames a still with no room for the motion: it drops the empty rows above and below the subject (keeping headroom of 4 % of the subject's height), keeps the still's width, and pads to the nearest framing the video model returns (9:16, 1:1, 16:9). The subject is never scaled or cut. A long, low subject in a square still becomes a 16:9 frame it fills, so a `--body-height` target is reached at a low clip resolution by scaling down rather than up. A motion that leaves the frame is clipped. The default `--fit state` is unchanged, and the report now says which `fit` framed it.
- `video-frames --allow-subject-edge-contact` accepts the subject at the frame edge and still refuses leftover key background there. The report's `edge_policy` names the rule that applied.
- `video-set --fit tight` frames every item that way and lets its subject reach the edge. It refuses `--shape`, and a cached canvas framed with another fit is not reused.
- `--body-height` measures the standing height on the clip's first frame, the base still's pose that every clip starts from. The tallest grounded frame, used before, is an attack's windup with the weapon overhead, and scaling that to the target made the character smaller in its attack than in its walk. The strip sidecar records `body_ref`, `body_src_h` (the standing height as filmed) and `scale`, so a strip whose cells were upscaled says so. Without a target nothing changes.

## v2.7.0 - Timed attack clips pinned to their start

- Attack clips are timed strikes pinned to their start. `MOTION_TEXT["attack"]` asks for the same attack twice, each a windup, one strike in front, a held impact pose and a recovery to the exact starting stance with approximate times, keeps what the subject holds in the hand nearest the viewer, and forbids turning. The attack template drops the "evenly paced" line, keeps what the subject holds inside the frame and asks for crisp frames. `video-set` asks attack clips for 4 s by default (other states keep 3 s; `--duration` still sets one length for all) and passes the canvas as `--last-frame`, so the clip ends where it began. The item report records the clip's `duration` and `last_frame`.
- `build_prompt(..., motion=...)` accepts a caller's own motion paragraph in place of the built-in state sentence, keeping the engine's frame, camera, background and design rules.
- `video-set --body-height N` passes one standing-height target to every state's loop (`video-loop --body-height`), so a character comes out the same size in every state even though states use differently shaped canvases.

## v2.6.0 - Automatic local gait selection and XY motion correction

- In `motion-auto`, an uncertain fine XY search-boundary match can emit the selected frames unchanged only after the existing rendered-cell seam gate and animation checks pass. Reports and the CLI expose the rejected measurement and unapplied correction. Other failures and explicit `motion` registration remain strict.

- Add explicit `video-loop --anchor motion` for reviewed fixed cuts with head and torso regions. Masked XY registration and boundary velocity determine one integer translation ramp; padded cycle PNGs retain the original keyed pixels. Reports record the inputs and shifts, and the unchanged seam threshold measures the rendered output. Automatic cycle selection and existing anchor defaults are unchanged; batch generation rejects this review-only mode before provider work.

- Add opt-in `--anchor motion-auto` for walk/run in `video-loop` and `video-set`: discover stable texture regions, separate drift during local repeat analysis, and apply the same XY boundary-velocity ramp without manual cut/region inputs. Keep source motion and pixels in padded cycle frames; gate the rendered cells. Reports expose automatic tracking, candidate selection, and correction. Existing defaults stay unchanged.

### Gait showcase

Human and animal loops produced by `video-loop --anchor motion-auto`, shown at their original playback speed.

| Humans | Animals |
|---|---|
| ![Nine human walk loops](https://github.com/aldegad/sprite-gen/releases/download/v2.6.0/human-nine.gif) | ![Nine animal walk loops](https://github.com/aldegad/sprite-gen/releases/download/v2.6.0/animal-nine.gif) |

## v2.5.6 - Walk and run loops close their wrap with one ramp

- Walk and run loops close their wrap with one ramp. `video-loop --anchor body` (now the default for `walk` and `run`) measures how far the last frame's head and torso sit from the first's, once, by whole-pixel registration over the top of the first frame's box, and shifts frame k by that offset times k/L. It never fits frame by frame: per-frame fitting turns a head bob into a full-body shiver, and the feet anchor pins the planted foot, which a quadruped moves every step. A cycle whose wrap already lands (most bipeds) is left byte-identical; the strip sidecar records `wrap_dx_px` and `seam_ratio_after_anchor`. `--anchor feet` and `--anchor none` are unchanged, and other states keep `none`.

## v2.5.5 - A lone step under the gait floor is refused, not looped

- Gait loop selection no longer keeps a lone step. When the deepest repeat sits under the gait floor and no repeat near twice it is about as good, `video-loop` refuses with `no periodic cycle found` (the same words as a flat profile) instead of looping half a stride; the failure report carries the guard's verdict and the minima it weighed. A quadruped whose near and far legs read alike produced exactly this on a walk clip. The doubled period is now searched among the profile's own minima within three frames of twice the step, since a real stride rarely lands on exactly 2x. Clips whose full gait repeats are selected as before.

## v2.5.4 - Room behind the attack canvas

- Attack canvases keep 20 % of their width empty behind the subject (`--trail`, a new wide-canvas margin next to `--lead`). A long weapon drawn back before the strike reached the back edge of the old layout and the frames gate refused the clip; the samurai and katana samples that needed hand-padded stills in v2.5.3 now pass from the raw still. Headroom, lead and every other profile are unchanged, and `--trail 0` restores the previous placement.
- `video-set --base` refuses a direction the prompt table does not know (`back`, `front`, `side`) instead of accepting `left=` and animating it with the default facing.
- The facing test suite names its orchestrator scrub sample neutrally.

## v2.5.3 - Reference facing controls and observed action returns

- Reference edits can opt into `gen --facing right|left` to request orientation in the generation prompt. The default `preserve` leaves existing generation callers unchanged.
- Facing inspection is **record-only by default**: `gen`, `video` and `video-set` use `--facing-fix none`. Explicit `mirror` corrects an observed opposite in the output copy; `gen --facing-fix regen` regenerates once, rechecks and mirrors a remaining observed opposite.
- `video --direction side` and batch side inputs inspect the still before animation. Batch uses one shared observation per side still; the requested facing drives canvas placement and motion prompts independently of the observation. Original inputs stay intact; cached canvases and prompts must match the requested policy.
- Direction reports include model, requested direction, model-reported confidence and correction evidence. `final_direction_source` distinguishes observations from values derived by mirroring; none is independent verification. Failed or uncertain calls record `unknown` with a reason and continue without correction.
- Known limitation: direction detectors can misclassify a correctly oriented still even at high confidence. Prompts request direction but cannot guarantee it; inspect the still before opting into correction. Front-facing observations are unchanged, and mirroring does not preserve accessory handedness.
- Synthetic regressions cover incorrect observations with byte-preserving defaults, explicit mirror pixel symmetry, regeneration rechecks, prompt/canvas agreement and existing callers.
- Attack loop selection searches longer action periods with observed repeat context and raises the periodicity requirement when less than a full repeat is available. The seam limit remains 2.0; walk and run selection are unchanged.
- One-shot selection requires an observed departure and return with resting frames on both sides. Endpoint-relative motion can identify a return after a held strike, while truncated actions, stationary clips and incoherent jitter are refused. These pixel measurements do not establish anatomical correctness.
- Loop selection and seam failures write structured reports with the rejected candidate, search window and repeat/return evidence. Successful reports explicitly record `status: passed`; undefined ratios are represented as JSON `null`.

- Attack canvases reserve overhead room as well as forward room for raised weapon swings. Wide padding now honors headroom while preserving 16:9 and the still pixels; idle, walk, run and jump defaults are unchanged.
- The attack motion prompt now tells the video model to strike with the weapon the character already holds and to keep its gear and outfit as drawn, so an armed sprite does not fall back to punching or grow new equipment mid-clip.
- Attack showcase: `docs/assets/attack-slime.gif`, `attack-fox-hood.gif`, `attack-paladin.gif`, `attack-claudecy-samurai.gif` (two-handed cut) and `attack-claudecy-samurai-onehand.gif` were produced by this release's pipeline (still, canvas, Grok Imagine clip, frames gate, loop selection) with no manual cut points. On a ten-character side-view sample every attack closed its loop; two clips needed the built-in single regeneration because the model framed the raised weapon above the top edge.

## v2.5.2 - Cleaner video spill correction

- Automatic spill selection distinguishes the key hue from yellow/cyan or red/blue material and discounts dark contamination in the reference matte edge band. Bright key-coloured details still count as material.
- Full correction removes faint key casts while recovering brightness without amplifying residual colour differences into secondary casts. The declared key defines channel groups even when the painted background is dim or asymmetric.
- Alpha, conservative `small` correction and gait selection logic are unchanged. Existing source colours without the key hue remain; this does not reconstruct the original material palette.
- Synthetic regressions cover colour preservation, bright edge details, secondary-cast amplification and imperfect painted keys.

## v2.5.1 - More consistent automatic walk and run loops

- Automatic gait cuts now score whether neighbouring poses repeat one cycle later as well as the last-to-first transition. This reduces selection of irregular motion that happens to have a plausible seam, without preferring an earlier or later part of the clip.
- Loop reports expose the selected neighbourhood, normalized repeat error and combined score in `cycle.selection`. Existing period detection, manual cuts, non-gait states and source playback speed are preserved.
- Synthetic regressions cover steady and irregular motion in both time directions. The repeat metric measures temporal consistency; it does not identify anatomical left/right limbs or repair malformed source motion.

## v2.5.0 - API image generation and smoother loop cuts

- New `openai` image provider: `sprite-gen gen --provider openai` calls the OpenAI Images REST API with nothing but `OPENAI_API_KEY` — the credential a headless container (a Modal worker, CI, a SaaS backend) can have, where the `codex` route's interactive ChatGPT login cannot exist. New images go to `/v1/images/generations`, `--ref` switches to `/v1/images/edits` as multipart with the references as repeated `image[]` parts in order (up to 16), and gpt-image's inline base64 is decoded and published as a verified PNG without resizing. Default model `gpt-image-2.5-flare`. `--transparent` asks for `background: transparent` with `output_format: png` — the same measured `native` strategy as codex, and a live run came back 84 % alpha-0 with the subject at alpha 251–254. `--aspect-ratio` maps to one of the gpt-image `size` values that satisfy the API's constraints (both sides divisible by 16, ratio within 1:3..3:1, 655,360–8,294,400 pixels); a ratio with no exact size is refused rather than rounded to a nearby one you would be billed for. A missing or empty key, a rejected key and a failed request are all terminal: this provider never falls back to codex, to another credential, or to a retry.
- sprite-gen stays subscription-first. `codex` and `grok` run on a subscription you already pay for; `openai` bills per call, so it runs **only** when `--provider openai` names it. `SPRITE_GEN_DEFAULT_PROVIDER=openai` is refused rather than honoured, the guided `workflow` flow neither offers it nor saves it as a preference, no availability fallback targets it (a codex outage still reaches grok, never metered credit), and having `OPENAI_API_KEY` in the environment changes no route by itself. Every call that does spend API credit — image or video — prints one stderr line naming the charge before the request leaves.
- `gen --quality low|medium|high|xhigh|max|auto` and `gen --resolution 1k|1.5k|2k`: the two knobs an image is billed on, as one shared vocabulary with a per-provider subset. grok Imagine carries `auto|low|medium` and all three resolution tiers and prices an image on the pair; openai carries the whole quality range and takes its size from `--aspect-ratio`; codex `image_gen` exposes neither dial. A knob a provider cannot honour fails loudly instead of being dropped from a request body you are about to pay for. Omitting both keeps every existing call byte-identical. The resolution names are tiers, not pixel counts — `1.5k` rendered 1408x1408 at 1:1 (2026-09-20 measurement) — and the grok subsets were read off the server rather than the prose: `quality` deserializes the wider shared enum and then refuses per model (`high` → HTTP 400 "This model only supports the following quality value(s): low, medium, auto."), `resolution: 1.5k` renders although the capability guide lists only 1k and 2k, and an unknown field is not refused at all, which is why these names are checked locally. Grok image reports carry `extra.quality` and `extra.resolution` when the request sent them.
- `video`, `video-extend` and `video-edit` announce the per-call charge on stderr when they run on `XAI_API_KEY` instead of the Grok subscription login — one line per submitted job, naming the duration and resolution it is priced on. The subscription route stays silent and no other video behaviour changed.
- Registering a provider now fails loudly when the workflow catalog has no label for it. The guided flow built its provider choices by zipping labels onto the provider tuple positionally, so a third provider was dropped without a word; labels are a checked mapping (a missing one fails at import) and the flow offers the subscription routes only. `workflow` access probing knows `openai` — ready when the key is set, with `billing: api-credit` and the billing confirmation that goes with it.

- `video-loop` automatic cuts now score the transition actually played from the last displayed frame back to the first. The chooser prefers a step comparable to the cycle's ordinary motion rather than the smallest distance, which could repeat a pose and pause at the wrap. Reports retain `next_frame_distance` as a separate diagnostic.
- `video-frames --spill full` now lowers the tint threshold as well as lifting the cluster-size limit, so faint key-colour reflections are corrected. `--spill auto` examines the reference at the same threshold before selecting full correction, preserving the character's own key-coloured material. The conservative `small` mode and the default 3-second video-set duration are unchanged.

- Gait auto-detection now considers an ambiguous double-period candidate even when the short candidate exceeds the duration floor. It preserves both phase occurrences at source speed when the longer local minimum is similarly credible and passes the periodicity gate; near-exact short repeats, worse doubles, non-gait states and caller window bounds are preserved. Reports identify this conservative choice with `half_period_guard.reason = "ambiguous-harmonic"` and `cycle.review_recommended`, rather than claiming anatomical stride certainty.
- Added a [loop review guide](docs/loop-review.md) defining what numeric gates establish, when sequential visual review is needed, and how to record explicit cut/alignment choices without presenting them as automatic decisions.

## v2.4.1 - A visible hop is no longer refused as "never leaves its rest pose"

- `video-loop` one-shot detection no longer refuses a visible hop because the rest pose is not one pose. The excursion is admitted by either the peak's height in MADs of the rest noise (as before) or the fraction of the subject's pixel mass the peak moves (new, ≥ 0.4); the report records `excursion_moved` and `excursion_rule`. A body that walked a few steps, hopped 42 px and froze scored 1.8 MADs because the walking preamble and the frozen tail inflate the "rest noise"; by moved mass it scores 1.07. A jittering stand still fails both rules, and clips the MAD rule already accepted are cut exactly as before.

## v2.4.0 - Three-second clips and gait windows in seconds

- `video-set --duration` defaults to 3 seconds (was 6). A repeating motion holds two or more cycles at 3 s (walk strides measured at 0.6–1.3 s across seven bodies), a single action is one hop or swing that the one-shot cut already handles, and 6 s bought only a longer wait — and, for jump, more idle standing between hops. `--duration 6` still asks for a longer clip.
- `video-loop` walk and run take their period window in seconds (walk 0.5–1.6 s, run 0.3–1.2 s, capped at half the clip) instead of a fraction of the clip length. At 3 s the old 6–31 % window topped out at 0.96 s and put a 1.0–1.3 s stride out of reach: on four walks generated at 3 s it refused one outright and cut another at a half step; with the bounds in seconds all four pass with equal or better seams, and the 6 s results are unchanged. Idle, jump and attack keep their windows.
- The strip sidecar (`<name>.strip.json`) records `kind` (`periodic` / `one-shot` / `fixed`) and `loop` (`false` for a one-shot), and the spec asset loader reads `loop` from it, so a jump or attack cut as a single action plays once on a trigger instead of repeating. A sidecar without the key is a loop, as before.
- The GIF/WebP never ask for more frames than the strip has cells. A cycle longer than the 64-cell cap is subsampled into the strip; the GIF/WebP are cut from those cells, so requesting the cycle length repeated cells back to back, the writer merged the identical neighbours, and verification refused its own output ("64 frames, expected 68" on a 2.8 s jump cycle).

## v2.3.1 - Key reflections removed from video sprites

- `video-frames --spill small|full|auto` and `video-set --spill` (default `auto`): remove the key colour a video model paints into the subject — a reflection across metal, a tint on a pale surface — which the chroma engine's trapped-spill pass leaves alone once the patch is larger than its small-cluster cap. `auto` keys the still the clip was made from and measures its own key-coloured material; none worth the name means every key tint in the clip is spill (`full`), otherwise the still-pipeline rule stays (`small`). Colour only — alpha is unchanged — and colours without key tint are untouched. `video-frames` defaults to `small`, byte-identical to before; the frames report records the decision under `spill`.
- Known limitation: on pixels where the reflection is removed, colour can lean warm. The correction solves observed = (1-k)·subject + k·key for the subject and so scales the unkeyed channels by 1/(1-k), which amplifies any warm light already mixed in (a pale armour piece can come out slightly reddish instead of neutral silver). A correction that keeps the source still's palette is open for contributions.

## v2.3.0 - Grok video modes: first-last, reference, extend, edit

- `sprite-gen video` gains the other Grok Imagine generation modes: `--last-frame` pins the closing frame (alone or with `--image`), and `--reference` (repeatable, up to 7) guides the clip with reference images named `<IMAGE_0>`, `<IMAGE_1>`, … in the prompt. `--image` is no longer required on its own. The image-only request body is unchanged and pinned by a snapshot test; the video → loop pipeline (`video-canvas`, `video-frames`, `video-loop`, `video-set`) is untouched.
- New `video-extend` (`POST /v1/videos/extensions`) continues a 2–15 s clip by 2–10 s and new `video-edit` (`POST /v1/videos/edits`) changes a clip of at most 8.7 s with a prompt. Both force the classic `grok-imagine-video` model (the 1.5 model refuses both endpoints), take no resolution / aspect / model flags because the output inherits the input's, check the input with ffprobe before uploading, and `video-extend` refuses a result shorter than its input.
- Local pre-checks refuse, before any call: no input at all, more than 7 references, `<IMAGE_n>` tags outside the given references (or with none), 1080p with references, and `last_frame` / `image`+`reference` on the classic model.
- The `sprite-gen-video-report` carries `mode` (`image-to-video`, `first-last`, `last-frame`, `reference`, `extend`, `edit`), `inputs` (local paths by role) and, for extend / edit, `input_duration`; every existing field stays.
- Removed the retired `sprite_gen/gen/generate_image.py` shim. It shared its name with the `generate_image()` function that `sprite_gen.gen` exports, so `import sprite_gen.gen.generate_image` replaced the function with the shim module for the rest of the process and the next `sprite_gen.gen.run()` died with `TypeError: 'module' object is not callable` — at runtime, not only under pytest. Nothing routed to the shim (no CLI verb, no `-m` step, no MCP entry), so the fix is deletion: the old module path now fails with `ModuleNotFoundError`, and the only image-generation entry point remains `sprite_gen.gen` / `sprite-gen gen`. `sprite_gen._modules.MODULE_DOMAIN` and the packaging import-surface list drop the row; the `import_probe` fixture stays as a general isolation device.
- Test isolation: the packaging import-surface probe now restores the parent package attribute and `sys.modules` entry it touched, so importing the retired `sprite_gen.gen.generate_image` shim no longer replaces the `generate_image()` function for later `tests/gen` runs. `pytest tests/packaging tests/gen` is green in any order.
- Test isolation: `tests/curate` imports its shared helper from `tests/frames` through the `tests/` root instead of by bare module name, so `pytest tests/curate` passes on its own instead of depending on `tests/frames` having been collected first.
- The expired-login prescription for Grok video and images no longer names a bare prompt (`grok -p ok`): on the grok CLI that starts the coding agent in the current directory, which can read, write and spend on its own. The prescription is now a non-agent round-trip (`grok models`) run from an empty directory, with `grok login` as the fallback, and the message says so. A test pins the prescription to a non-agent command.

## v2.2.1

- `video-loop` gait half-period guard: a `walk`/`run` period shorter than the state's floor (`LoopProfile.min_seconds`: 0.6 s / 0.35 s) is treated as one step of a two-step gait and doubled when the doubled period repeats within 25 % of it — the case where a costume hides the legs and the half period dips as deep as the full one, which the depth rule alone returned as a one-legged loop. The report carries `cycle.half_period_guard` (`applied`, `from`, `to`, depth ratio, or `below_floor` + why it was left alone). `--min-len` still overrides.
- `video-loop` one-shot detection no longer refuses an excursion shorter than the periodic window's lower edge; only a cut under 4 frames or longer than the clip is refused. Short performed actions (a set-down, a nod) come back as the frames they are.
- `video-loop --anchor feet` (also `video-set --anchor`): remove the slow in-canvas drift of a subject the model walked across an "in-place" canvas, so the strip stops sliding once per cycle. A straight line fitted to the body's centre across the cycle carries the drift and not the step; only that line is removed and every cell stands on the mean foot line. Strip meta gains `foot_anchor`, `drift_px` (drift removed), `foot_sway_px` (the planted foot's in-gait movement, kept and reported) and `foot_x`, and declares the spec loader's `anchor` as `[foot_x, h]` so scenes stand the sprite on its feet; the video-set table row carries `drift_px`. Default `none` keeps existing output byte-identical. The first implementation pinned each frame's own foot line instead; in an in-place walk that line jumps to the planted foot every step, so a body that stood still lurched back and forth by the stride. That version never shipped.
- `video-canvas` / `video-set`: `cheer`, `wave` and `celebrate` now take the wide canvas (raised limbs leave a 1:1 frame at the top corners), `video-set --shape` forces one shape for a whole batch (wide costumes), and `video-set` carries `cheer` / `wave` motion templates that name no limbs.
- `gen --trim-alpha` (with `--transparent`): crop the published PNG to its opaque bbox so the bottom edge is the foot line; the report records `extra.trim_alpha` (`bbox`, `before`, `after`, `margin_px`). The `.raw.png` is untouched.

## v2.2.0 - Independent asset tools and optional scenes

- Added standalone `background-tile`, `shadow` and `inspect-motion` commands. They accept existing artwork and report tile joins, anchor-preserving shadow projections, repeated poses and explicit foot-contact measurements without changing source animation.
- Added optional `scene-render` and `scene-inspect` with asset references, planes, placement, playback rate, camera, lighting, PNG/MP4/GIF output and layer/placement export. Scene applies only verified stride for the matching asset and an explicitly chosen direction.
- Added one read-only adapter for PNGs, external frame descriptors, existing loop strips and runtime atlases, preserving native timing and anchors. Source fingerprints bind measurements to loaded content; output publication uses shared locking and atomic writes.
- Split the command catalog into ordered sprite pipelines, independent tool groups and optional workflows. Added background recipes and asset/scene contracts to the skill and documentation, plus a 20 fps, 256-colour village GIF below the existing README showcase.
- Raised the Pillow minimum to 12.3.0 for its upstream security fixes; existing environments must update separately to satisfy the new requirement.

- Grok images now call xAI Imagine directly for generation and one-to-five-reference editing, without spawning Grok Build. The default image model is `grok-imagine-image-2.0`; `--model` now selects the image API model. Responses are decoded and published as verified PNGs with existing chroma cleanup preserved.
- Images and videos share one credential reader: the user's Grok subscription login first (`GROK_HOME` supported), even with `XAI_API_KEY` configured. Only an absent login file permits API-credit use; invalid, expired or rejected logins never fall back to it. Image reports expose the auth source and API endpoint. The workflow guide checks the same source and explicitly confirms API-credit billing for either media type. Expired login and request failures stop without agent fallback or automatic image request retries.
- Standalone image-to-video requests are routed by the sprite-gen skill itself; no separate video skill is needed.

- Flat-border key detection accepts imbalanced chroma channels using the shared `is_border_key_candidate` rule in cutout, extraction and video preparation. Interior subject-colour classification remains unchanged.
- Painted-key removal is limited to the key family and its connected antialiased rim, preserving isolated look-alike colours inside the subject. The declared-key removal radius remains unchanged.

## v2.1.1 - Chroma keying follows the painted background

- Chroma keying measures the key distance from the background colour the generator actually painted (detected from the flat borders, `detect_background_key_rgb`) as well as from the pure key, in `cutout`, `extract`, `slice-sheet` and `video-frames`. Image models paint `#00FF00` as (8, 162, 24) and darker; that colour sat on the 96 radius from pure green and keyed half-and-half pixel by pixel. The radius is unchanged. Reports carry `chroma_key_painted`. Documented cost: key-family dark subject colours are erased on darker painted backgrounds — choose the key away from the subject's hues.
- `video-canvas` normalizes a green/magenta still's flat background to the exact declared key and pads with that key (`--key auto|green|magenta|white`; the repaint mask is the `cutout` matte's own alpha-0 set, the subject is byte-identical). The report records `key`, `key_painted`, `normalized_px`.
- `video-frames` tells leftover key background at the frame edge (`residual`, points at `video-canvas`) from a subject that was framed too tight (`subject`, points at a taller/wider canvas) instead of reporting both as "framed too tight".

## v2.1.0 - Guided requests and saved defaults

- Added two user journeys: sprites (base-image provider, then GPT rows or Grok video) and ordinary images (provider). The read-only `workflow` command checks credentials, resolves request choices over saved defaults and returns the next questions and existing engine route.
- Added `defaults show|save|clear` with separate sprite/image preferences. Saves require the observed revision, use the existing cross-platform file lock and publish atomically. A stale concurrent writer is rejected; one-off requests and reads never change defaults.
- Results are delivered before optional curation. The first completed selection can be saved for future requests; saved curation choices skip repeat questions. Subscription and quota remain unknown when not independently verified, and configured video API-credit billing requires its own explicit choice.
- Simplified the skill entry point, separated conversation and row execution contracts, refreshed all six README entry pages, and removed the retired PR-specific subject-profile proof script. Existing pipeline and utility commands remain available.
- Removed stick-pose motion guides and obsolete rendering-style bans from generated prompts. Fixed GIF inspection to read each frame's metadata before advancing the decoder.

## v2.0.3 - One standing height

- `video-loop` checks that `img2webp` actually supports `-exact` (libwebp >= 1.5; Ubuntu 24.04 ships 1.3.x) and fails by name otherwise instead of writing WebP with rewritten RGB under alpha; CI installs `ffmpeg` and the official libwebp 1.6.0 binaries so the video tests run there, and those tests skip cleanly where support is absent.
- `video-loop` `body_h` is the standing height (tallest floor-contact frame) instead of the cycle's median bbox height, which under-measured jumps and over-scaled them by ~22 %; `--body-height N` scales every state to the same standing height directly in the pipeline (`--strip-height` remains the cap). README heroes regenerated at one standing height.

## v2.0.2 - Heroes from the pipeline

- `video-loop` GIF/WebP default rate is now 24 fps (the source rate): every cycle frame is kept, so fast actions never read slow; `--gif-fps 12` restores the lighter output.
- `video-loop --cycle fixed --start N --length L` cuts an explicitly named cycle without detection (reported as `kind = "fixed"`, seam gate still applied) — for clips with too few repeats for the periodicity gate.
- `video-loop --strip-height` caps the cell/strip/GIF height so an output can be produced at its display size directly from the pipeline.
- README hero GIFs are `video-loop` outputs verbatim; the jump `<img>` height matches its taller file.

## v2.0.1 - Jump loops play at the same rate

- `video-loop` GIF/WebP frame count follows the cycle length at a fixed playback rate (`--gif-fps`, default 12) instead of a per-state fixed count: a 2.5 s jump now gets ~30 frames at ~84 ms instead of 12 frames at 210 ms, so every state plays at the same density. `--n-out` still overrides; the report records `gif_fps`.

## v2.0.0 - Four pipelines, one taxonomy

### Highlights

- **Video → sprite loops.** One still becomes a whole motion set: `video-canvas` pads it into the canvas the state needs, `video` animates it in place through Grok Imagine, `video-frames` keys every frame, `video-loop` finds the true period (or the one performed action) and emits a strip, a transparent GIF and a WebP, and `video-set` runs directions × states with per-item reports. Every stage is measured and fails by name.
- **Parallel row generation is a command.** `gen-set` generates every state row of a prepared run N at a time with the run's own identity ref, one report per row, a table and a non-zero exit on any failure — what the skill used to describe in prose.
- **One taxonomy.** `sprite-gen --help` opens with the four named pipelines (A atlas rows · B video → loop · C utilities · D post-processing) and groups every verb by domain; the grouping, the `scripts/` map and the pipeline list derive from `sprite_gen/_modules.py` (`MODULE_DOMAIN`, `PIPELINES`), and the docs classification is catalogued in `docs/README.md` itself, checked by tests against the file set and the pipeline catalog.
- **Documentation you can navigate.** `docs/README.md` indexes every doc once under its branch with a one-line owner; `docs/architecture.md` opens with a domain diagram and a four-pipeline diagram; the largest docs carry a table of contents; the repo README shrinks to an entry page and the sections it carried live in the docs that own them.

### Breaking

- New required binaries for pipeline B: `ffmpeg` (frame extraction) and `img2webp` from libwebp (WebP with exact alpha). Pipelines A, C and D do not need them.
- Removed the `scripts/` aliases that pointed at library modules or verb-less modules. Use the verb or module instead:

  | Removed | Use |
  |---|---|
  | `scripts/extract.py` | `sprite-gen extract` (`scripts/extract_sprite_row_frames.py`) |
  | `scripts/gif_utils.py` | `from sprite_gen.util import gif_utils` |
  | `scripts/runio.py` | `from sprite_gen.spec import runio` |
  | `scripts/reroll_state_row.py` | `sprite_gen.effects.reroll` (module; `interpolate_frames.py` covers the take workflow) |

- Maintainer experiments moved to `scripts/dev/` (`breathe_mutation_battery.py`, `measure_align_sigma.py`, `validate_pr6_subject_profile.py`, `check_visible_magenta.py`); they are not verbs and the skill no longer requires them.
- `docs/static-pose-recipe.md` merged into `docs/breathing.md` (one contract, one owner); links to the old file are gone.

### Added

- `sprite_gen/video/` domain and the verbs `video-canvas`, `video-frames`, `video-loop`, `video-set` (contract: `docs/video-pipeline.md`). `video-loop --cycle auto|periodic|one-shot`: action states (`jump`, `attack`, unknown) whose clip performs the action once get a recorded one-shot cut (rest → excursion → rest) instead of a hard failure; the report keeps the rejected periodic attempt and `table.md` gains a `kind` column. `walk`, `run`, `idle` still fail loud without a period.
- `sprite-gen gen-set` (`sprite_gen/gen/gen_set.py`, `scripts/gen_set.py`): 6 rows at a time by default (the lead-verified batch width), anchors before rows on direction runs, reuse unless `--force`, `reports/gen-set/table.md` + `set.report.json`, `--provider` honoured verbatim with `gen`'s own default resolution and its recorded codex → grok availability failover per row.
- `sprite_gen._modules.DOMAINS` / `domain_of()`: display order and one-line meaning per domain; `cli.command_domains()` derives the help groups. A verb whose module is not in the table fails loudly.
- `docs/README.md` (documentation index) with a test that pins it to the file set and resolves every relative markdown link; tables of contents in `run-contract.md`, `layer-tracks.md`, `curation.md`, `directional-anchor-workflow.md`, `architecture.md`; `scripts/dev/README.md`.

### Changed

- `SKILL.md` is a route-first hub (still / atlas / video-to-loop / utilities) under the 24 KB skill budget; the interpreter rationale, the rename gate and the breathing contract moved verbatim to `docs/interpreter.md`, `docs/rename-gate.md`, `docs/breathing.md`.
- `video-loop` strips are capped by pixel width (32 000 px) as well as by cell count: a 650 px-wide cell now yields 49 cells instead of a 41 664 px image Chrome refuses; the strip meta records `cell_cap` and `subsampled`.
- Walk detection window floor 10 % → 6 % of the clip so a legless body's fast bounce resolves; the 15 % depth rule keeps rejecting the one-step half period (verified on a biped and a quadruped). `video-set` motion templates no longer assume a biped.
- `README.md` is an entry page: what it is, the four pipelines, one quickstart each, install. Breathe, chroma-alpha quality, Backbone Lattice and the curation webview tour moved verbatim to `docs/breathing.md`, `docs/chroma-alpha.md`, `docs/pixel-unfake.md`, `docs/curation.md`.

### Removed

- See **Breaking** for the removed `scripts/` aliases and the merged doc.

## v1.61.0 - Image to Video

- Added `sprite-gen video`: one still + prompt → a verified mp4 through Grok Imagine (`POST /v1/videos/generations`), with duration 1–15 s, 480p/720p/1080p, optional aspect ratio and audio flag, and a `sprite-gen-video-report` JSON.
- Credentials are the user's own and never part of the repo: `XAI_API_KEY` when set, otherwise the `grok` CLI login file (`~/.grok/auth.json`, `GROK_HOME` honoured). The report records `auth_source`; tokens and download URLs are never printed or written.
- An expired grok login fails before any upload with the exact refresh command; a set-but-empty `XAI_API_KEY`, a missing credential, a refused request, a failed/expired generation, a poll timeout, or a non-mp4 download each fail by name and write nothing.
- New wrapper `scripts/generate_sprite_video.py` and docs at `docs/video.md`.

## v1.60.0 - Native Alpha

- `sprite-gen gen --transparent` now follows a per-provider transparency strategy declared once on each adapter (`Provider.transparency`). `codex` asks `image_gen` for a genuinely transparent background and publishes the measured alpha (`native`, first choice); `grok` keeps deterministic chroma keying because Grok Imagine returns JPEG only.
- Added `--alpha-mode auto|native|chroma`. `auto` reads the provider's strategy, `chroma` forces keying on codex for prompts that already carry a key background, and `native` on a chroma-only provider fails before any model call. With `--ref` attached, `auto` keys instead of asking codex for native alpha (measured 1/6 real alpha with references vs 6/6 chroma); the report records why under `alpha.strategy_source`.
- Native output is verified before publishing: no alpha channel or 0% transparent pixels refuses the run (a drawn checkerboard is never keyed silently), RGB under alpha 0 is scrubbed, and partial alpha is reported untouched.
- Reports carry an `alpha` block (`strategy` plus stats) next to the existing `chroma` stats, and codex's own `transparentBackground` claim under `extra.transparent_background_reported`.
- Sprite-row generation is unchanged: rows still carry the request chroma key and are keyed at extraction.

## v1.59.0 - Contributor Collection

This release incorporates accepted work from eight community pull requests. Thanks to [@devswha](https://github.com/devswha) for chroma color preservation, [@bokjk](https://github.com/bokjk) for portable manifest paths, [@Dongkyu-ES](https://github.com/Dongkyu-ES) for deterministic CLI tests, engine export, and subject-aware sparse-frame handling, [@napkn34](https://github.com/napkn34) for the Windows provider and publish-lock fixes, and [@monibu1548](https://github.com/monibu1548) for pixel-unfake vertical centering and grounding controls.

- Added `sprite-gen export-aseprite` for Phaser-compatible Aseprite JSON and Flame-compatible hash files split by state. Curated frame geometry and timing remain canonical, and exports are confined to the run's `exports/` directory.
- Added a Windows `LockFileEx` backend that preserves shared readers and exclusive publishers across processes without weakening the fail-loud isolation contract.
- Fixed provider CLI resolution and UTF-8 subprocess I/O on Windows, including npm `.cmd` shims and non-UTF-8 console code pages.
- Made Python 3.14 CLI option tests deterministic under colored shell output.
- Added `character` and `effect` subject profiles. Their sparse-frame floors scale with cell resolution: `ceil(sqrt(width * height))` for characters and half that value for effects. Explicit `--min-used-pixels` still wins.

## v1.58.0 - Compose canvas and domain package layout

- Added the human-facing `sprite-gen compose` assembly canvas and handoff to the curation view.
- Reorganized the Python package and tests into domain subpackages. CLI and script entrypoints remain stable; Python imports intentionally use `sprite_gen.<domain>.<module>` paths derived from `sprite_gen._modules`.
- Split request loading from schema migration so reads no longer mutate run state.

## v1.57.0 - First Pixel Breath

- Added deterministic breathing, pixel-grid measurement, curation editing, and run repair contracts.
- Added deterministic palette-swap recolor baking (`sprite-gen recolor` / `recolor-palette`) and curation-side colourway selection.
- Added package entrypoints, declared runtime dependencies, and install smoke coverage.

Earlier public milestones are summarized above. Historical tags remain published only where their contents pass the current public-data policy.
