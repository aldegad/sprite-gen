# Workflow — the component-row run, step by step

SKILL.md owns the step index and the blocking gates; this file owns each step's exact command,
reference-attachment rules, and per-step options.

## Workflow

0. Pass the **Base Lock Gate** above. Do not start step 1 until a base idle is locked (`y`).

1. Prepare the run:

```bash
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/prepare_sprite_run.py \
  --out-dir <target>/assets/generated/sprites/<character-id> \
  --character-id <character-id> \
  --base-image /absolute/path/to/base.png \
  --description "<short identity note>" \
  --force
```

For hatch-pet-style locomotion, add the cell gate explicitly: `--cell-width 192 --cell-height 208`.

방향 있는 캐릭터(휴머노이드 4/8방향)는 방향 계약을 함께 선언한다: `--directions down,side,up --mirror left=side`.
방향 계약 런의 파일은 **택소노미**(`raw/<dir>/<pose>.png`, `frames/<dir>/<pose>/`, 가이드/프롬프트 동일)로
나뉜다 — 자세가 늘어도 flat 폴더가 비대해지지 않는다. 경로 리졸버 SSoT 는 `sprite_gen/layout.py`,
추출된 프레임의 경로는 frames-manifest `row.files` 가 SSoT 다 (run-contract §2).
base = down 정면 기본자세 하나이고, prepare 가 방향 앵커(`<dir>_idle`) 슬롯을 합성하고 생성 체인 SSoT
(`references/generation-plan.json` — 1단계 앵커는 base 기반, 2단계 행은 자기 방향 앵커 기반, 미러 방향은
생성 생략 계약)를 기록한다. 상세와 좌우 재생성 규칙: [`docs/directional-anchor-workflow.md`](docs/directional-anchor-workflow.md) "Prepare 스캐폴딩".

This writes:

```text
sprite-request.json
base-source.<ext>
references/layout-guides/<state>.png
prompts/<state>.txt
raw/
frames/
```

2. Generate one image per state with the engine's own `gen` command (generation is engine-owned; the `image-gen` skill is now a thin shuttle over this — [`docs/gen.md`](docs/gen.md)):

```bash
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/generate_sprite_image.py \
  --provider codex \
  --prompt-file <run>/prompts/<state>.txt \
  --out <run>/raw/<state>.png \
  --ref <run>/base-source.<ext> --ref <run>/references/layout-guides/<state>.png
```

Use `prompts/<state>.txt` as the prompt; save the selected image as `raw/<state>.png`. `--provider` is optional — the default is **codex** (`SPRITE_GEN_DEFAULT_PROVIDER` env overrides it; an observable grok fallback kicks in only if codex is unavailable). Pass `--provider grok` explicitly for the faster backend; codex adheres tighter to negative constraints. Default policy: [`docs/gen.md`](docs/gen.md#default-provider-selection). Keep the request chroma key on the background (extraction removes it). Reference attachment rules:

**생성 동시성 (maintainer 확정 2026-07-19)**: 여러 행을 뽑는 배치는 **4동시**로 돌린다 —
`sprite-gen gen` 호출을 최대 4개 병렬 (codex 실측 4병렬까지 스로틀 없음; grok 도 4,
사용자 관측상 6까지 가능하나 기본은 4). 1개씩 직렬은 멀티-행 배치에서 안티패턴.
run-dir 쓰기는 `runio.py` 락이 지키므로 생성(각자 다른 `raw/<state>.png` 출력)은
안전하게 병렬화된다. 이 규칙은 지침이다 — 오케스트레이션 스크립트를 짤 때
`ThreadPoolExecutor(max_workers=4)` 급으로 반영하라.

Generation providers are **engine backends**, not user-facing agents. Selecting
`grok` launches a headless `grok -p` process owned by `GrokProvider`; it does not
require or route through a separate user-facing skill/task. Spawning a visible
worker/agent is the caller's orchestrator concern — out of this engine's scope.
Command chain: [`docs/gen.md`](docs/gen.md#provider-topology).

- Simple/default states (before direction-anchor mode exists): attach exactly two references — `base-source.<ext>` (canonical identity) + `references/layout-guides/<state>.png` (layout only).
- Direction-anchor mode: do **not** attach `base-source.<ext>` to action rows. Attach the accepted target-direction anchor (**a single-pose single image — never a multi-frame idle row**) + the state layout guide; for a paired row also attach the basis row as timing/scale/motion reference only. **Never choose the anchor crop by hand** — ask the pipeline, right before each generation:

```bash
$SPRITE_GEN_ROOT/.venv/bin/python -m sprite_gen.cli anchor \
  --run-dir <run> --for-state <state>   # prints the identity ref path (bakes it)
```

  It returns `references/anchors/<dir>-anchor-x8.png` for an action row (the curated anchor frame — pixel edits, transforms, deletions and reordering all baked, upscaled ×8 NEAREST) and `base-source.<ext>` for an anchor row or a non-direction run. The file is a derived cache, so re-run it every time; which frame is the anchor is the human's call (`--pick <state>#<index>`, or the pin button in the curation view) and defaults to the anchor row's curated sequence head. Chain details: [`docs/directional-anchor-workflow.md`](docs/directional-anchor-workflow.md).
- Hatch-pet-style locomotion may attach additional references only when they are part of the row plan, recorded in `qa-notes.md`: original sheet / canonical base (identity support only), a previous gait row such as `raw/running-right.png` (motion rhythm only), or an accepted motion-QA artifact (gait readability support only).

3. Extract frames:

```bash
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/extract_sprite_row_frames.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

This removes the request chroma key, finds connected sprite components, fits each pose into a fresh transparent request-sized cell, and writes `frames/<state>/frame-N.png` plus `frames/frames-manifest.json`.

3.5. (Optional) Curate frames in the webview:

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen curation \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

Standalone local webview: side-by-side frame compare, select/reject, drag-to-reorder play sequence, non-destructive per-frame transform saved to `curation.json` (originals never rewritten; no sidecar = all frames in order, an explicit default). Usage detail, finished-sheet editing via `unpack_atlas_run.py`, and the standalone image-candidate curation path: [`docs/curation.md`](docs/curation.md).

4. Compose the runtime atlas:

```bash
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/compose_sprite_atlas.py \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

This writes:

```text
sprite-sheet-alpha.png
sprite-sheet-alpha.report.json
manifest.json
```

`manifest.json.frame_layout` is the runtime SSoT. Game code must consume rectangles from the manifest and must not recover frame rectangles from alpha content at runtime.

4.5. (Optional) Bake palette-swap colourways of the finished atlas:

```bash
# draft the opaque colours of the base sheet (edit into a recolor spec)
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen recolor-palette \
  --base <run>/sprite-sheet-alpha.png --out <run>/palette.draft.json

# bake N variants from a recolor spec (kind "sprite-gen-recolor") into <run>/variants/
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen recolor \
  --run-dir <run> --spec <run>/recolor.spec.json
```

Exact RGB match by default (dot art); opt-in `match: "tolerance"` for soft edges. Same input → same output bytes. The report names every unused map source and every unmapped passthrough colour — nothing outside the map vanishes quietly. Spec schema, report fields, and curation-view adopt flow: [`docs/recolor.md`](docs/recolor.md).

4.6. (Optional, rig runs only) Bake the declared composite stacks:

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen compose-layers \
  --run-dir <target>/assets/generated/sprites/<character-id>
```

Only for a run whose `sprite-request.json` declares `rig` / `states.<state>.track` / `layers` — every other run is untouched by this feature and this step is skipped. It stacks the **curated** rows (integer pivot translation + alpha masks, no resampling) into `<run>/layers/<name>.png` + `<name>.manifest.json` + `layers.report.json`, so the same run bakes the same bytes every time. `--names a,b` bakes a subset and leaves the rest of `layers/` alone. Declaration schema, landmark rules, track kinds, and what `prepare` carries: [`docs/layer-tracks.md`](docs/layer-tracks.md).

5. Launch the curation webview automatically (default closing step):

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen curation \
  --run-dir <target>/assets/generated/sprites/<character-id> &
```

After the atlas composes (and QA previews exist), launch the webview in the background and report the printed URL — finishing a run means handing the human the open webview, not just file paths. Multi-agent launch rules (per-launch free port, one webview per run dir, `.sprite-gen.lock`, `--no-open` for headless): [`docs/curation.md`](docs/curation.md). Skip the auto-launch only for an explicitly unattended batch run.

