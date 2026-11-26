# `sprite-gen logo` — typography from a mood image

> Owns: `sprite-gen logo`: a title logo, wordmark or title lettering drawn by the model inside the picture it belongs to, then lifted out of it as a transparent PNG — the three steps, the files and the report, the text check and its refusals, the key choice, the logo matte and its known limit · Index: [docs/README.md](README.md)

Lettering asked for on its own comes back as a font with an effect laid over it. Asked
for inside the picture it belongs to — a game's title screen, a poster — the model draws it
as part of that world: its letterforms, materials and light come from the scene. So a logo
is made in three steps:

1. **mood**: a title screen (16:9) or poster (2:3) in the brief's style with the title
   lettered in it. The text check reads it back (a misreading is read once more); a title
   the model dropped or misspelt is refused here, before the next image is bought. The
   check also returns the box round the title, on which `--chroma-key auto` picks the key
   ([Which key](#which-key)); a title with both keys' hues among its colours is refused
   there, before the lettering is bought.
2. **lettering**: that picture attached as the reference of an image edit that asks for
   only its lettering, with the same letterforms, materials, colours, outlines, glows and
   effects, on a flat chroma key.
3. **matte**: the key removed by the logo matte (soft glows and partial alpha kept, no key
   hue left), trimmed to the lettering with a 2 px margin. A logo see-through inside its
   letters, past the reach of its own glow, is refused here, before its text is read
   ([See-through inside the letters](#see-through-inside-the-letters)). The logo's text is
   read again on a plain backdrop; letters that differ from the text asked are refused.

Nothing in this path draws a font. A step that cannot be done fails and says why; it is
never replaced by rendered text or by another provider.

```bash
sprite-gen logo --provider codex --text "STARFALL" --style "painted fantasy" --out-dir logo/
sprite-gen logo --provider codex --text "Iron Lantern" --subtitle "Chapter One" --style "16-bit pixel art" --out-dir logo/
sprite-gen logo --provider codex --text "별빛 기사" --language Korean --style "painted fantasy" --out-dir logo/
sprite-gen logo --provider codex --text "STARFALL" --mood title-screen.png --out-dir logo/   # start from a picture you have
sprite-gen logo --provider codex --text "STARFALL" --lettering logo/lettering.png --out-dir logo/  # cut again, nothing generated
```

| Option | Meaning |
|---|---|
| `--text` | the title the logo spells; empty is refused before anything is bought |
| `--subtitle` | an optional short line, lettered smaller with the title and checked with it |
| `--style`, `--scene` | style words, and what the mood image shows besides the title |
| `--composition` | `title-screen` (16:9, default) or `poster` (2:3); `--aspect-ratio` overrides the ratio (codex ignores ratios: the prompt says it) |
| `--language` | the script the lettering is in (`Korean`, `Japanese`, …) |
| `--mood PATH` | start from a picture with the title already in it (a title screen you have, a candidate you picked): step 1 is not generated, its text is still read |
| `--lettering PATH` | a lettering image already on a key is cut again (after a matte change, or for a lettering drawn elsewhere): nothing is generated; the logo's text is still read |
| `--chroma-key` | `auto` (default), `magenta` or `green` — [below](#which-key) |
| `--provider`, `--model`, `--quality`, `--resolution` | as `gen`, for both images ([gen](gen.md)); the provider also reads both text checks, on its own reading model ([below](#the-text-check)) |
| `--out-dir`, `--report` | where the files go; the report defaults to `<out-dir>/logo.report.json` |

## Files

| File | What it is |
|---|---|
| `mood.png` | step 1: the picture with the title in it (or a copy of `--mood`) |
| `lettering.png` | step 2: the lettering alone on the key, as the model drew it |
| `logo.png` | step 3: the transparent logo, trimmed; written only when its text reads as asked (or could not be checked, below) |
| `logo.rejected.png` | the cut of a logo whose text did not read as asked, or that is see-through inside its letters, kept aside for a look |
| `logo.preview.png` | the logo on a dark and on a light backdrop, side by side: where a halo or a hard edge would show |
| `logo.report.json` | the report |

A run still removes an earlier run's `lettering.png` and `logo*.png` from its folder
first, so a file there is always this run's. A `--lettering` given as the folder's own
`lettering.png` (cut again in place) is kept, and `--mood` may be the folder's own
`mood.png`. An input given as any other file the run removes or writes, or as the report,
is refused before anything is made
([run-contract.md §2-b-3](run-contract.md#2-b-3-a-run-never-writes-over-moves-or-deletes-its-inputs)).
The run's working folder is a new one of its own (`.logo-work.<pid>-<random>`); a
`.logo-work` folder already there is never touched.

## The report

`kind: sprite-gen-logo-report`, with `status`: `ok`; `unverified` (the logo was made but
its text could not be checked); `refused` (with `refused_at`: `mood-check`, `key`, `matte`
or `logo-check`, and `reason`); `failed` (a step failed: a provider refusal, a timeout).
Then:

- `steps.mood` (`source`: `generated`, `given` or `none`; the prompt sent), `steps.key`
  (`key`, `source`, `region` and both keys' `scores`: [Which key](#which-key)),
  `steps.lettering` (the prompt, the reference, `raw_alpha`), `steps.matte` (the matte's own
  numbers, below) and `steps.trim` (`bbox` in the lettering image, sizes, `pad_px`);
- `logo`: `size`, `alpha` (`opaque_pct`, `partial_pct`, `empty_pct` of the trimmed logo and
  `visible_opaque_share`), `key_hue_left` (visible pixels whose colour still carries the
  key's hue; 0 is the aim) and `see_through_inside` (`px`, `pct_of_visible`,
  `glow_reach_px`, `max_pct` and `wide`:
  [See-through inside the letters](#see-through-inside-the-letters));
- `text_check.mood` and `text_check.logo` (below);
- `calls`: every call in order — `mood`, `mood-check`, `lettering`, `logo-check`, a check's
  call once for each reading it took, with its `reading` (1, 2) and the `effort` it was read
  at (`null` where the provider names none) — with its provider, model, `auth_source`,
  `transport`, `elapsed_seconds` for the images, and `route`:
  `subscription` (a codex ChatGPT login, a Grok login) or `per-call` (a codex API-key login,
  `XAI_API_KEY`, `OPENAI_API_KEY`; each said on stderr before its request leaves, as every
  metered call in sprite-gen is);
- `warnings`.

## The text check

The provider that drew the images reads them back through the same vision call the facing
check uses (`inspect_image`; codex `exec` read-only and ephemeral, or the xAI or OpenAI
Responses API on the same credential). It is asked to return the lines of text as spelled,
without being told what they should say. Both checks compare by one rule
(`logo.normalize`), what the text spells:

- **kept** — every letter and digit, and every mark written on one: an accent, a vowel
  sign, a virama, a nukta, a tone mark, a voiced mark. `किला` read as `कला` (the vowel sign
  `ि` gone) is a mismatch, and so is `Café` read as `CAFE`. An accent counts on a capital
  too: for lettering in capitals that go without their accents (as Greek capitals do), give
  the title that way. Hangul is compared by its syllables, so `별` written as jamo is `별`;
- **folded away** — case, compatibility forms (full-width letters, ligatures, `Ⅳ` and
  `IV`), the characters drawn as nothing (joiners, variation selectors), enclosing marks (a
  keycap), and spaces, punctuation and symbols, with any mark written on them. So `Hero's
  Quest` matches `HERO’S QUEST!` and `I O N` matches `ION`.

The subtitle may be read before or after the title. The verdict is one of:

| Verdict | At the mood image | At the logo |
|---|---|---|
| `match` | go on to the lettering | publish `logo.png`, `status: ok` |
| `mismatch`, `no-text` | refused: the lettering is not bought | refused: the cut is kept as `logo.rejected.png` |
| `unverified` (the call failed, or its answer was not the JSON asked for) | a warning; go on | `logo.png` is published with `status: unverified` and a warning: look at the preview |

A model that reads stylised lettering can misread it, and the same picture read twice can
come back two ways. So a reading that would refuse (`mismatch`, `no-text`) is read once
more, up to `logo.MAX_READS` (2) readings: one that spells the text passes, and only when
every reading differs is the image refused. Both checks decide by this one rule
(`logo.read_text`). A call that fails is not asked again: with nothing read before it the
check is `unverified`, and a misreading before it stands. Each check in the report keeps
the deciding reading's verdict and `read`, `max_reads`, and `readings`: every reading in
order with its number, verdict and what it read (or why it could not). A refusal names
every reading, so a person can judge, and `--mood` or `--lettering` starts again from the
files already made.

A second reading can only pass a picture whose letters are wrong if the reader writes a
stroke that is not drawn: it is never told what the text should say. Measured on a
synthetic pixel title drawn one stroke short (쿠 drawn as 구, the middle bar of ㅋ left
out), the codex reader below read that syllable as 구 in 10 readings of 10 and never as
쿠, so neither one reading nor a check of two (0 of 5) could pass it.

On codex the text checks read on `gpt-6-sol` at `high` reasoning effort, named on each call
over the login's own `config.toml` (`facing_vision.CODEX_READ_MODEL` and
`CODEX_READ_EFFORT`). A check is a gate, and its verdict should not change with the machine
it runs on: left to each login's config, the same picture could be read on another model
and effort on each machine, and the report could not say which. These are the model and
effort the measurement above ran on. The report names the model and effort of every
reading. `--model` names the images' model only (on codex, the driver of the drawing,
[gen](gen.md#the-codex-driver-model)); the facing check reads on the same model and effort.
The xAI and OpenAI checks read on their own fixed models, as the facing check does.

## Which key

The lettering keeps the mood image's colours, and the matte needs a key no letter is made
of. The lettering image is drawn on a flat chroma key, magenta or green, and a letter colour
in the key's own hue is, by its pixels, a mix with the key: the matte cuts it see-through (a
mint fabric patch in a letter on the green key comes out a pale, half-transparent patch).
The mood check also returns the box round the title, and `--chroma-key auto` (the default)
measures inside it (`logo_matte.choose_key`, the whole image when no box came back) the
share of pixels in each key's hue. "In a key's hue" is `logo_matte.key_hued`: for magenta
min(R, B) - G, for green G - max(R, B), above 16 levels, or a pale colour whose small cast
is mostly the key's.

A key whose hue is 5 % or more of the box (`logo_matte.CLASH_HUED_PCT`) is one of the
title's colours and is not chosen. When the style or scene (`--style`, `--scene`) names a
colour in a key's hue, 1 % of the box (`logo_matte.NAMED_CLASH_HUED_PCT`) is already
enough. The words are `logo.KEY_COLOUR_WORDS`: green, mint, lime, emerald, jade and
chartreuse for green; magenta, fuchsia, pink, purple, violet, plum and orchid for magenta. A
plural or an -ish or -y ending counts ("pinkish"). Teal, cyan and lavender are not in the
list: their usual values are in neither key's hue. A word alone never rules a key out: the
box holds scenery too, so the pixels decide and a word only lowers the share they need.

Between the keys left, the share of pixels in a key's hue (a pink letter on magenta) counts
hard against it, and every other colour scores how long its mix with the key keeps the
key's hue, which is how long a glow of that colour keeps its fade
([the known limit](#known-limit)). A cyan or yellow glow keeps green's hue to about 85 %
coverage and magenta's to about half; an orange one the other way; a white or cream glow
keeps either. Magenta wins a tie.

When both keys are colours of the title there is no third key: the logo matte and the key
prompts know magenta and green only. The run is refused at the key step (`status: refused`,
`refused_at: key`) before the lettering image is bought. The message gives each key's share
and the brief's words for it, and says: restyle the title without one of the two hues, or
name the key to lose with `--chroma-key`; `--mood <mood.png>` skips the mood image on the
next run.

An explicit `--chroma-key magenta` or `green` is kept as before; when that key is one of the
title's colours, a warning says so: its letters in that hue come out see-through. With
`--lettering`, `auto` reads the key off the image's border, and no colour check is made
there. Name the key yourself when you know the lettering better.

`steps.key` records `key` (`null` when refused), `source` (`auto`, `explicit`, or `border`
for `--lettering`), `region` (`lettering-box` or `image`), and for each key under `scores`:
`hued_pct`, `hold`, `score`, `named` (the brief's words in that key's hue), `clash_at_pct`
(5 or 1) and `clashes`.

## The logo matte

`sprite_gen.frames.logo_matte`. The sprite matte behind `cutout` and `extract` ramps
coverage over a short chroma distance, which suits a sprite's one-pixel edge: on a white
glow it is opaque half way to white, keeping the pink mix of glow and key (a halo), and cuts
the last fifth of the fade to nothing (a hard edge). The logo matte instead explains each
pixel as `alpha × colour + (1 − alpha) × background` and writes the colour with the key's
share taken out:

- the **background** is the key as painted, a quadratic surface fitted to the key's pixels,
  so a vignette stays background; its noise is measured there. The border band must be at
  least half key, or the image is refused (`the lettering image's border is not the … key`);
- a pixel far from the key in chroma and without the key's hue is the lettering's own
  material: opaque, as drawn — except a rim pixel that is a mix of the key with the colour
  just inside it, which is a letter's antialiased edge;
- every other pixel is a mix. Next to a letter, a pixel that lies on the line from the key
  to one of the letters' own colours (learned where a letter meets the key) — a letter's
  antialiased edge — takes its coverage by projection, in the letter's colour; any other
  mix — a glow — takes the least coverage any colour could have, which is exact for the
  bright, saturated colours glows are and never leaves the key's hue in the colour;
- a glow's last trace, under the noise of one pixel, is measured on the mean of its faint
  neighbours, so it fades out instead of ending in an edge;
- coverage under 3/255, isolated faint specks, and faint coverage on the picture's outer
  4 px (a generated picture's edge often carries a seam a few levels off the key) are
  cleared, and transparent pixels carry no colour.

`steps.matte` reports the background (`mean`, `spread`, `sigma`, `border_key_pct`), and the
pixel counts: `solid_px`, `edge_px` (where the palette was learned), `blend_px` (explained by
a letter colour), `unmix_px` (glow), `faint_px`, `partial_px`, `specks_cleared_px`,
`frame_seam_cleared_px`, `palette_size`. A lettering image that came back with its own
transparent background (codex sometimes answers an edit with alpha) is kept on that alpha
(`method: native-alpha`); a key painted as a transparency checkerboard is refused.

### See-through inside the letters

A letter colour in the key's hue is cut as a mix with the key and comes out see-through
inside the letter, where no glow reaches; a glow fades out beside the letters and pools in a
counter no farther than it reaches beside them. After the matte and trim,
`logo_matte.see_through_inside` measures the logo on that difference: the see-through pixels
(coverage under 240 of 255, `INSIDE_ALPHA8`) that are walled in by opaque lettering (no way
out to the picture's edge except across opaque pixels) and lie farther from the opaque
lettering than the logo's own glow reaches beside it. The way out starts from the
transparent pixels on the edge of the trimmed logo and runs through transparent pixels and
see-through ones; a see-through gap of 2 px or narrower does not count as a way, a
transparent gap of any width does. Only what is joined to the picture's edge is outside,
however narrowly: a hole joined to the outside, even through one pixel, is outside. A
transparent hole walled in by the letters is no way out: an O's counter, or a letter colour
cut all the way to nothing, such as a square of the key's colour painted inside a
see-through patch; the see-through patch round such a hole is therefore still counted
inside. Transparent pixels are never counted, only see-through ones: a letter's own counter,
transparent and walled in by the letter, adds nothing, so big hollow letters with no glow
give 0. The same way out decides both things: which see-through pixels are inside, and the
glow's reach. The glow's reach (`glow_reach_px`) is the 99th percentile of how far the
see-through pixels that have a way out to the picture's edge lie from the opaque lettering,
and at least 2 px (a letter's antialiased rim); a see-through pixel whose only way out would
be a hole inside the letters does not count toward the reach.

When that share is 1 % (`INSIDE_MAX_PCT`) of the visible lettering or more, the logo is
refused at the matte step (`refused_at: matte`) before its text is read; no logo text check
call is made. The cut is kept aside as `logo.rejected.png` and `logo.preview.png` is
written. The message names the share and the key, and says: generate the lettering again on
the other key (`--chroma-key <the other key>`, with `--mood <mood.png>` when there is a mood
image), or restyle the title without that colour. There is no option that accepts it; the
rejected cut is there to look at. Only a cut made by the logo matte (`steps.matte.method:
logo-unmix`) is refused; a lettering image that came back with its own alpha
(`native-alpha`) is measured and reported, not refused.

`logo.see_through_inside` reports `px`, `pct_of_visible`, `glow_reach_px`, `max_pct` (1.0)
and `wide` (true when refused on it).

In the tests, patchwork letters (square pink, butter yellow, mint and sky-blue patches in a
plum outline, one letter with a counter) on green have their mint patches cut see-through,
and 9.5 % of the visible lettering is found inside; the same letters with no colour in
magenta's hue, on magenta, give 0 %. A white glow, dark-outlined gold letters with a cyan
glow, a cyan glow pooled in an O's counter on green and on magenta, an orange glow on green,
and pixel lettering with no glow give 0 % each. A navy letter block on green, in flat
colours, with a mint patch walled in by it, has the mint cut see-through and 17.1 % of the
visible lettering found inside; with one 9x9 square of the key's colour in the middle of the
patch, cut to nothing, 16.9 %; with four such squares, two of them glued to the patch's
wall, 16.6 %. The glow's reach stays 2 px in all three (there is no glow). Run through
`sprite-gen logo` with `--lettering`, the letter with one square is refused at `matte`
before its text is read, as the one without is. With the mint patch filling the letter but
for a 4 px outline and a hole glued to the letter's edge, one pixel of the letter left
between the hole and the key round the letter, the hole is sealed and 88.6 % of the visible
lettering is found inside; when that one pixel is the key's colour, the hole is joined to
the outside, the patch has a way out through it, and nothing is found. Big hollow O and A
letters, whose counters are transparent and walled in by the letters, find nothing with no
glow (0 px). With a glow pooled round a counter whose middle stays transparent, a white
glow on magenta gives 0 %, dark-outlined gold letters with a cyan glow on green 0 %, and an
orange glow on green 0.02 %; a sweep of white glows from 3 to 16 px on both keys finds at
most 0.04 %.

### Known limit

The dense part of a glow, past the coverage where its mix with the key still shows the
key's hue, cannot be told from a letter of that mixed colour by its pixels, and is kept
opaque in it: a cyan glow pooled in an O's counter on magenta (past about half coverage), or
the ring round a small gold sparkle's white core on green (past about three quarters: a
faint olive cast on a light backdrop). On the key whose hue the glow shares more it keeps
its fade (`tests/frames/test_logo_matte.py` holds both sides): that is what
`--chroma-key auto` weighs, after the letters' own colours, which count more because a
letter in the key's hue comes out see-through. When `logo.preview.png` shows such a ring
where a glow is dense, cut the same lettering on the other key's terms by generating it
again with `--chroma-key` (or accept the ring: at full size it sits inside the glow's
brightest part).

The see-through check has limits of its own. A letter colour cut all the way to nothing
everywhere leaves holes that look like counters, and transparent pixels are not counted, so
it is not found; keeping such a key off the lettering is the key choice's job. A see-through
patch round such a hole is found. See-through within the glow's reach of the opaque
lettering is taken for glow, so in a glowing title a key-hued patch narrower than about
twice that reach is not found. A see-through patch with a way out to the picture's edge is
not inside: one on a letter's outer edge with no outline walling it in, or one that a hole
joins to the outside, even through one pixel.

## Cost

Four to six calls a logo on the provider named: two images and two text checks, each read
once, or twice when its first reading would refuse. On a subscription (codex ChatGPT login,
Grok login) nothing is billed per call; `calls[].route` says which route each ran on.
`--mood` saves the first image, `--lettering` both. A refusal at the key step costs the mood
image (unless given with `--mood`) and its text check; the lettering is not bought. A
refusal at the matte step costs both images, and no logo text check. Beyond a check's
second reading, nothing is sent again on its own: a call that fails is not asked again, and
a refusal is said and the run ends.

## Related

- [gen](gen.md) — the image calls, providers and their credentials
- [sheet-slicing](sheet-slicing.md) — `cutout`, the uniform-background cut for sprites
- [curation](curation.md) — a view to pick between logo candidates (`unpack-atlas --pngs-dir`)
