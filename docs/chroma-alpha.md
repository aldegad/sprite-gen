# Chroma Key & Alpha Cleanup — sprite-gen reference

> `SKILL.md` 허브에서 분리한 시나리오 상세. 크로마 키를 고르거나(특히 소재색이 키와 인접할 때), 추출 후 소재색 손실을 진단할 때 이 문서를 따른다. 분기표 SSoT 는 image-gen SKILL.md 최상단 게이트이고, 이 문서는 그 규칙의 sprite-gen 쪽 상세다.

## Key selection

`prepare_sprite_run.py` chooses a chroma key by sampling the base image unless the request forces one. The generated character must not use the chroma color or chroma-adjacent colors.

**Choose the chroma away from the subject's dominant hue — do not blindly default to magenta.** The hard key erase is a color-distance ball around the key (`--key-threshold`, default 96): a subject color inside that radius is deleted no matter where it sits. Antialias fringe is no longer erased by a separate peel; key/subject boundary blends are **unmixed into despilled RGB + partial alpha** when they are close enough to the keyed region. In-band blends are limited to key-distance `<= 2`, out-of-band blends use `--fringe-unmix-reach` (default 4), and deeper key-tinted *interior* subject colors (hot pink / purple under a magenta key) survive byte-identical. Small strongly key-tinted clusters buried inside the subject (generator spill) are despilled in place — see below. Key choice still decides quality:

- Pink / purple / magenta-family subjects (꽃, 씨앗봉투) → use **green** `#00FF00`; their edge blend under a magenta key wastes real silhouette pixels.
- Deep-red / crimson / wine hair or clothing is **magenta-adjacent** (both high R). Use **green** for red/crimson/warm subjects.
- Green/teal/olive subjects → use **magenta**.
- Blue subjects → avoid cyan/blue keys; magenta or green.
- Both pink *and* green in one subject → pick the key farther from the larger/more important material, verify both survive after extraction, and prefer `--chroma-key auto` (distance-scored).

## `--chroma-key auto`

When unsure, let `--chroma-key auto` sample the base. It excludes the detected flat opaque chroma background from subject scoring, then scores candidates by distance from the remaining subject pixels. `auto` refuses a key whose nearest subject pixel falls inside the erase radius when a safer candidate exists, records candidate `score`, `min_subject_distance`, `clears_erase_radius`, and `background` metadata in the request, and warns (stderr) when no candidate clears the subject — so a small but critical feature (eyes, a gem, an ear lamp) under 1% of the pixels is not silently deleted. Only force a key when you know the subject hue is safely far from it. Verify after extraction that the dominant subject color survived — a black-where-it-should-be-colored frame means the key was adjacent to the subject.

## Extraction-side alpha cleanup

`extract_sprite_row_frames.py` owns alpha cleanup for sprite rows. Three passes, in order:

1. **Hard key cut** — pixels within `--key-threshold` of the key (and already-transparent input) are erased; alpha=0 pixels get their RGB cleared to `(0,0,0)` (no halo).
2. **Soft-alpha unmix** — key-tinted blends near the keyed region are separated into despilled RGB + **partial alpha**: the blend model `observed = (1-k)·subject + k·key` is solved from the key-tint score, so antialiased silhouettes keep their coverage ramp instead of collapsing into a binary staircase. In-band blends (inside `--fringe-key-threshold`) are eligible only at key-distance `<= 2`; out-of-band blends use `--fringe-unmix-reach` (default 4). This preserves deeper key-tinted material while fixing trapped blend pockets between hair strands.
3. **Trapped-spill despill** — a small connected cluster of key-tinted pixels (≤ `--spill-max-fraction` of the subject, default 0.005) containing at least one strongly tinted pixel is generator spill, wherever it sits: its color is corrected in place with alpha kept (no pinholes). Large key-tinted regions are intentional material and are never touched; marginally warm subject colors (skin) never qualify.

The unmix/spill tunables live in the run's `sprite-request.json` under `chroma` (`unmix_reach`, `spill_max_fraction`) — the extractor reads them from there, CLI flags override, and the effective values are written back to the request so every run records what produced it. Then it extracts connected components and writes fresh transparent cells. The pixel-perfect path is unaffected: it binarizes alpha downstream (α ≥ 128 → opaque), so soft-alpha input degrades gracefully. This is intentionally closer to hatch-pet than to simple `magick -transparent`.

If component extraction cannot find the declared frame count, the row is blocked. `--allow-slot-fallback` exists for explicit debugging only; it must be reported as `slots-explicit` and is not the default path.

## Related

- [`../SKILL.md`](../SKILL.md) — 필수 게이트 (크로마 키 소재색 분기 + 변환 후 소재색 보존 검증)
- [`architecture.md`](architecture.md) — `remove_chroma_background` 추출 내부 단계
