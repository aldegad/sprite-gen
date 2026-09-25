# Changelog

All notable public changes to `sprite-gen` are recorded here. Versions track the `version:` field in `SKILL.md` and `pyproject.toml`.

## Unreleased

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
