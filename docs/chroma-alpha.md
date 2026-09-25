# Chroma Key & Alpha Cleanup — sprite-gen reference

> Owns: Choosing the chroma key and diagnosing alpha cleanup after extraction · Index: [docs/README.md](README.md)

## Why the extractor unmixes instead of peeling (from the README, 2026-09-09)

The extractor keeps chroma cleanup deterministic: soft-alpha unmix preserves antialiased hair strands and thin outlines instead of peeling them away before coverage can be solved.

<p align="center">
  <img src="assets/chroma-fullbody-illustration-magenta.png" width="640" alt="full-body chroma comparison: illustration on magenta key" /><br />
  <em>Illustration, magenta key: source, v1.12.0 peel, v1.13.0 soft-alpha unmix.</em>
</p>

<p align="center">
  <img src="assets/chroma-fullbody-illustration-green.png" width="640" alt="full-body chroma comparison: illustration on green key" /><br />
  <em>Illustration, green key: source, v1.12.0 peel, v1.13.0 soft-alpha unmix.</em>
</p>

<p align="center">
  <img src="assets/chroma-fullbody-pixelart-magenta.png" width="640" alt="full-body chroma comparison: pixel art on magenta key" /><br />
  <em>Pixel art, magenta key: source, v1.12.0 peel, v1.13.0 binarized output.</em>
</p>

<p align="center">
  <img src="assets/chroma-fullbody-pixelart-green.png" width="640" alt="full-body chroma comparison: pixel art on green key" /><br />
  <em>Pixel art, green key: source, v1.12.0 peel, v1.13.0 binarized output.</em>
</p>

The close-up crops below show the edge detail behind the full-body comparisons.

![chroma peel before and after — illustrated hair strand](assets/chroma-peel-illustration-before-after.png)

![chroma peel before and after — pixel-art outline](assets/chroma-peel-pixelart-before-after.png)

> `SKILL.md` 허브에서 분리한 시나리오 상세. 크로마 키를 고르거나(특히 소재색이 키와 인접할 때), 추출 후 소재색 손실을 진단할 때 이 문서를 따른다. 분기표 SSoT 는 image-gen SKILL.md 최상단 게이트이고, 이 문서는 그 규칙의 sprite-gen 쪽 상세다.

## Key selection

`prepare_sprite_run.py` chooses a chroma key by sampling the base image unless the request forces one. The generated character must not use the chroma color or chroma-adjacent colors.

**Choose the chroma away from the subject's dominant hue — do not blindly default to magenta.** The hard key erase is a color-distance ball (`--key-threshold`, default 96) around the key **and** around the background colour the generator actually painted (detected from the flat borders, `detect_background_key_rgb`; since 2026-09-11 — generators paint `#00FF00` as (8, 162, 24) or darker and the pure-key ball alone missed it): a subject color inside either radius is deleted no matter where it sits, so the darker the generator painted the key, the deeper into the key's dark variants (olive under green, plum under magenta) the erase reaches. Antialias fringe is no longer erased by a separate peel; key/subject boundary blends are **unmixed into despilled RGB + partial alpha** when they are close enough to the keyed region. In-band blends are limited to key-distance `<= 2`, out-of-band blends use `--fringe-unmix-reach` (default 4), and deeper key-tinted *interior* subject colors (hot pink / purple under a magenta key) survive byte-identical. Small strongly key-tinted clusters buried inside the subject (generator spill) are despilled in place — see below. Key choice still decides quality:

- Pink / purple / magenta-family subjects (꽃, 씨앗봉투) → use **green** `#00FF00`; their edge blend under a magenta key wastes real silhouette pixels.
- Deep-red / crimson / wine hair or clothing is **magenta-adjacent** (both high R). Use **green** for red/crimson/warm subjects.
- Green/teal/olive subjects → use **magenta**.
- Blue subjects → avoid cyan/blue keys; magenta or green.
- Both pink *and* green in one subject → pick the key farther from the larger/more important material, verify both survive after extraction, and prefer `--chroma-key auto` (distance-scored).

**The border and the interior answer different questions** (2026-09-12). `detect_background_key_rgb` classifies the border samples with `is_border_key_candidate` — keyed channels lit, unkeyed channels dark (< 64) and under 35 % of the brightest keyed channel, no balance between the keyed channels — because a flat border is already background evidence: Grok paints `#FF00FF` as (216, 46, 147), blue/red 0.68, which the interior rule `is_key_family` (balance ≥ 0.8) rejected, so the detector fell back to the declared key and the whole background survived. The interior rule keeps the balance so hot pink (250, 77, 150) and purple (213, 112, 246) inside the subject stay byte-identical; they fail the border rule too, on the lit green channel. The painted key's erase ball only reaches pixels that carry the signature or are 8-connected to them through the ball (the antialiased rim) — an isolated look-alike patch inside the subject is not erased. `cutout --key auto`, `video-canvas --key` and the `video-frames` residual split use the same border function.

## `--chroma-key auto`

When unsure, let `--chroma-key auto` sample the base. It excludes the detected flat opaque chroma background from subject scoring, then scores candidates by distance from the remaining subject pixels. `auto` refuses a key whose nearest subject pixel falls inside the erase radius when a safer candidate exists, records candidate `score`, `min_subject_distance`, `clears_erase_radius`, and `background` metadata in the request, and warns (stderr) when no candidate clears the subject — so a small but critical feature (eyes, a gem, an ear lamp) under 1% of the pixels is not silently deleted. Only force a key when you know the subject hue is safely far from it. Verify after extraction that the dominant subject color survived — a black-where-it-should-be-colored frame means the key was adjacent to the subject.

## Extraction-side alpha cleanup

`extract_sprite_row_frames.py` owns alpha cleanup for sprite rows. Three passes, in order:

1. **Hard key cut** — pixels within `--key-threshold` of the key *or* of the painted background colour detected on the borders (`detect_background_key_rgb`; `background_key=` pins it explicitly) — and already-transparent input — are erased; alpha=0 pixels get their RGB cleared to `(0,0,0)` (no halo).
2. **Soft-alpha unmix** — key-tinted blends near the keyed region are separated into despilled RGB + **partial alpha**: the blend model `observed = (1-k)·subject + k·key` is solved from the key-tint score, so antialiased silhouettes keep their coverage ramp instead of collapsing into a binary staircase. In-band blends (inside `--fringe-key-threshold`) are eligible only at key-distance `<= 2`; out-of-band blends use `--fringe-unmix-reach` (default 4). This preserves deeper key-tinted material while fixing trapped blend pockets between hair strands.
3. **Trapped-spill despill** — a small connected cluster of key-tinted pixels (≤ `--spill-max-fraction` of the subject, default 0.005) containing at least one strongly tinted pixel is generator spill, wherever it sits: its color is corrected in place with alpha kept (no pinholes). Large key-tinted regions are intentional material and are never touched; marginally warm subject colors (skin) never qualify.

A fourth pass, edge decontamination (`decontam`, below; opt-in for sprite rows through `chroma.decontam`), then re-explains the edge with the subject's own colours.

The unmix/spill tunables live in the run's `sprite-request.json` under `chroma` (`unmix_reach`, `spill_max_fraction`) — the extractor reads them from there, CLI flags override, and the effective values are written back to the request so every run records what produced it. Then it extracts connected components and writes fresh transparent cells. The pixel-unfake path is unaffected: it binarizes alpha downstream (α ≥ 128 → opaque), so soft-alpha input degrades gracefully. This is intentionally closer to hatch-pet than to simple `magick -transparent`.

If component extraction cannot find the declared frame count, the row is blocked. `--allow-slot-fallback` exists for explicit debugging only; it must be reported as `slots-explicit` and is not the default path.

## `chroma.mode: "ycbcr"` — chrominance-plane matting (opt-in)

Port of perfectpixel-studio's `chroma.go` (MIT, see NOTICE). The default RGB path above classifies pixels by RGB distance to the key, so a *shaded or gradient* key background (dark green under a green key) or JPEG 4:2:0 chroma noise can drift past the erase radius and survive. The ycbcr path ignores luma entirely and separates on the CbCr plane, where shading does not move the key cluster:

1. **Key detection** — CbCr histogram *mode* of corner patches + thin borders (never a mean — a mean drifts on gradients); when enough border samples sit in the declared key's chroma family, that cluster wins outright.
2. **Soft matte** — Hermite smoothstep alpha ramp over CbCr distance (24 → 72).
3. **Despill** — subtracts only the key-direction chroma component inside the despill band; colors orthogonal to the key keep their saturation.
4. **Border flood fill** — 4-connected from the image borders through lenient key-chroma pixels; interior key-family pixels (gems, highlights) never connect and survive.
5. **Self-diagnostic rematte** — an opaque-fraction spike (subject erased) or declared-key residue spike (background survived) triggers a rematte with the declared pure key; the better result wins and the fallback is reported via extraction warnings (never silent).

Select with request `chroma.mode: "ycbcr"` or `--chroma-mode ycbcr`. **The default stays `"rgb"`**: on clean flat-key raws the RGB path's exact-solve unmix already removes key tint completely, while ycbcr's fixed-scale despill leaves a small tinted soft-edge halo — ycbcr is for degraded sources (shaded/gradient backgrounds, JPEG chroma noise), not a general upgrade.

## `decontam` — give the edge the subject's own colour back

The unmix above separates a blend by its key-tint score, and that assumes the subject's own colour carries no tint (grey, white). A saturated subject breaks the assumption. A crimson hair strand blended a third of the way into green scores as barely tinted, so it stays opaque and olive; where the full spill pass does reach it, the `1/(1-k)` gain of `despill_color` turns it orange. Video adds a second loss: H.264 4:2:0 has already averaged the key into the chroma of every strand one or two pixels wide. Its luma survives at full resolution.

`decontam: "palette"` re-explains the edge after the matte (either matte). It learns a 32-colour palette from the subject's confident interior (opaque, deeper than 6 px) and fits every subject pixel within 6 px of the keyed background, and every keyed pixel within 3 px of the subject, as

    observed = alpha · P + (1 − alpha) · B

B is the key background measured locally (mean of keyed pixels within 12 px). P is the palette colour whose line through B passes closest to the observation, and alpha is the projection onto that line. The colour written is P's chroma at the observation's luma: the hue comes from the subject, the light and shade from the pixel. When the subject owns no key-hued material (≤ 0.5 % of its interior carries the key hue, the same test `video-frames --spill auto` runs on its reference still), the palette is key-free, so the output cannot carry the key's hue and no channel is amplified.

| The pixel | Outcome |
|---|---|
| no palette colour explains it (a colour the interior never shows), or, in the still fit, the explanation misses its luma (outline ink darker than any blend) | engine bytes |
| closer to a palette colour as observed than the blend explains it (margin 10, or 3 noise sigmas of the background) | engine bytes; with a key-free palette, a leftover key hue is capped (keyed channel ≤ largest other channel + 8), which never raises a channel |
| pushed toward the key clearly more than the fit misses by, but not proven partial coverage | colour only |
| a blend within the unmix reach (`chroma.unmix_reach`, default 4) | alpha and colour |
| a blend deeper in the 6 px band | colour only, so a key reflection on a solid surface is recoloured rather than made see-through |
| a keyed pixel within 3 px of the subject that fits a blend above the background's noise floor and chains to the subject | coverage recovered: the faint sides of a strand the hard cut erased |

Interior pixels are never touched, `off` returns the engine's output byte for byte, and everything is integer-exact or a fixed sequence of float64 operations (no random seed, no iteration-order dependence). The palette is a histogram k-means with a greedy weighted farthest-point seed and a fixed iteration count. There is no new dependency.

Two fits. `still` chooses P per pixel. `video` chooses P on a 3×3 mean of the observation, whose chroma is blurred anyway, and averages the projection alpha with a luma-only alpha where P and B differ in luma. `video-frames` uses the video fit and learns one palette per clip on the first frame, so the colours an edge may take cannot change from frame to frame.

Three modes: `palette` runs the pass and fails loud where it cannot (no key hue, or no subject pixel deeper than 6 px to learn from); `auto` runs it wherever it applies and records why not elsewhere; `off` leaves the matte as it is.

| Entry point | Default | Flag |
|---|---|---|
| `sprite-gen cutout IMAGE` | `auto` (chroma routes; the white matte records that it has no key colour) | `--decontam off\|auto\|palette` |
| `sprite-gen gen … --transparent` | `auto` (chroma strategy; native alpha is left alone) | `--decontam off\|auto\|palette`; `palette` is refused before the provider is called when it cannot apply |
| `sprite-gen video-frames`, `video-set` | `off` | `--decontam off\|auto\|palette` |
| row extractor (`extract`, `inspect`) | `off` | request `chroma.decontam`, or `--decontam` to override; written back to the request once in play |

Reports record what the pass did under `decontam`: mode, whether it applied (and why not), fit, the palette, and changed / refit / tint / recovered / unexplained pixel counts.

Measured on a procedural ground-truth set (red strands 0.5–3 px wide, outline and interior ink, highlights, translucent wisps, white cloth and a gold accent on a painted green key; a still, and an H.264 4:2:0 round trip at CRF 16). Key contamination counts edge pixels covered in both output and truth whose hue turned 12° or more toward the key (green residue and the despill's warm drift both turn that way), or, on near-grey truth, whose a*/b* moved toward the key by 8. Halo is the mean CIEDE2000 against the true composite on #0A0A0D and on white:

| Source | Path | Key contamination | Halo dark / white | Strand recall |
|---|---|---|---|---|
| still | RGB engine (default) | 22.5 % | 6.65 / 7.16 | 84.3 % |
| still | RGB engine + `decontam` (still fit) | 0.07 % | 1.14 / 1.14 | 99.0 % |
| still | YCbCr matte (`gen`) | 19.1 % | 9.77 / 9.36 | 77.3 % |
| still | YCbCr matte + `decontam` | 0.16 % | 1.15 / 1.14 | 99.0 % |
| H.264 frames | RGB engine + `--spill full` | 33.2 % | 12.79 / 12.43 | 82.1 % |
| H.264 frames | + `decontam` (video fit) | 10.8 % | 9.38 / 8.73 | 91.4 % |

**Defaults.** Stills cut by `cutout` and `gen --transparent` default to `auto`: on stills the pass removes nearly all key contamination with a smaller halo and more strand coverage, and `off` gives the previous output back byte for byte. The row extractor stays `off`: its frames are a derived cache that `heal` re-derives from raw, so a changed default would silently rewrite every existing run, and pixel-art rows binarize alpha downstream anyway. `video-frames` / `video-set` stay `off` (see the video numbers above).

Limitations:

- An edge takes one of the subject's interior colours at its observed brightness. A thin feature whose colour appears nowhere inside the subject keeps the engine's pixels.
- In video a thin strand's chroma is gone before keying, so its colour is chosen from blurred chroma; strands come back in the hair's hue, sometimes a shade darker or paler than drawn.
- A subject that owns key-coloured material keeps it in its palette, and its edges are repainted with whatever explains them best, key colour included. Keep the default when that material matters.
- The pixel-unfake path binarizes alpha downstream, so on pixel-art rows only edge colours change.

## Related

- [`../SKILL.md`](../SKILL.md) — 필수 게이트 (크로마 키 소재색 분기 + 변환 후 소재색 보존 검증)
- [`architecture.md`](architecture.md) — `remove_chroma_background` 추출 내부 단계
