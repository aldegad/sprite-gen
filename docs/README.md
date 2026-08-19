# sprite-gen docs — topology and concept ownership

Leaf docs are one link deep from this hub. The tree groups them by the concern
you are in — walk down the branch that matches your task, don't scan the flat
list. Each doc owns its tables; SKILL.md and the others point rather than restate.

```text
sprite-gen (this SKILL.md = behavior contract + hub)
│
├─ CONTRACT & STRUCTURE ── "what files exist and what each stage promises"
│   ├─ docs/run-contract.md      # pipeline stage I/O table · canonical run-dir folder tree ·
│   │                            #   curation-view display contract · run_revision/HTTP-409 ·
│   │                            #   per-state salvage + stale backup · --pngs-dir import rule
│   └─ docs/architecture.md      # how scripts realize the contract: stages · cell geometry ·
│                                #   idle-anchor ownership flow · extraction internals (SKILL wins on conflict)
│
├─ REQUEST AUTHORING ── "fill sprite-request.json before generating"
│   ├─ docs/states-and-frames.md # which states · frame counts (4/5/6/8/9/12) · Quick Path JSON
│   ├─ docs/subject-profiles.md  # "subject": character|effect · sparse-floor 프로필 ·
│   │                            #   이펙트 베스트/워스트 프랙티스 (실측 배터리 근거)
│   ├─ docs/pixel-unfake.md     # fit / pixel_unfake params · plain-twin curator toggle · density refs
│   └─ docs/chroma-alpha.md      # chroma key branch table · --chroma-key auto · alpha cleanup
│
├─ GENERATION ── "raw/<state>.png from prompts (the one AI step)"
│   ├─ docs/gen.md               # sprite-gen gen provider CLI · verified PNG/report · image-gen shuttle
│   ├─ docs/frame-interpolation.md  # generative in-between (codex/grok) → take raw · auth prereqs · RIFE retire rationale
│   └─ docs/seamless-video-loop.md  # non-looping AI video clip → seamless loop: flow-matched cut + RIFE seam bridge
│
├─ CURATION ── "human/agent picks, edits, and downloads via the webview"
│   └─ docs/curation.md          # webview · curation.json schema (selected/order/transforms/
│                                #   deleted/clones/revision/recolor.picked) · per-state salvage ·
│                                #   frame CLONES · standalone image-candidate path · finished-sheet
│                                #   re-edit (unpack)
│
├─ COLOURWAYS ── "bake N palette-swapped sheets from one base atlas"
│   └─ docs/recolor.md           # recolor / recolor-palette CLI · spec + report schema · exact vs
│                                #   tolerance match · variants/ layout · curation blink-compare + adopt
│
├─ LAYER TRACKS ── "compose rows onto each other instead of generating every combination"
│   └─ docs/layer-tracks.md      # rig profiles + integer landmarks · track kinds (base /
│                                #   action_overlay / prop_effect / full_body_override) ·
│                                #   composite stack · manifest rig block · layers/ artifact tree ·
│                                #   compose-layers CLI + prepare 의 레이어 키 반입/드롭 고지
│
├─ ENGINE EXPORT ── "adapt one composed atlas to existing game-engine loaders"
│   └─ docs/engine-export.md      # Aseprite JSON mapping · Phaser tags · Flame per-state hash
│
├─ SPECIALIZED INPUTS ── "not the plain animation-row path"
│   ├─ docs/directional-anchor-workflow.md  # directional / 45° anchor chains · hatch-pet locomotion
│   └─ docs/sheet-slicing.md     # multi-figure variant sheet → per-cell standing cuts (立ち絵, not rows)
│
├─ QA ── "verify motion as motion before reporting done"
│   ├─ docs/qa-motion.md         # Motion Continuity verdict criteria (BLOCKING)
│   └─ docs/locomotion-curation.md  # motion-phase guides · manual selected cycles · clean GIF export
│
└─ TROUBLESHOOTING ── "조용히 이상할 때 먼저 볼 표"
    └─ docs/troubleshooting.md   # 사이드카 스테일 가드/도장 경로 · 두-작성자 충돌 ·
                                 #   provider 무출력 행(env 위생) · 세로 스트립 전멸 · ffmpeg 500
```

Concept taxonomy (which doc owns each term, so agents don't guess):

- `sprite-request.json`, cell, states, takes → run-contract.md §2 · states-and-frames.md
- `run_revision`, `state_revision`, per-state salvage, `curation.stale-*.json` → curation.py + curation.md
- `curation.json` fields (`selected`/`order`/`deleted`/`transforms`/`pixels`/`clones`/`pixel_unfake`/`revision`/`recolor.picked`) → curation.md
- frame **clones** (duplicate instances, `source_frame_index`) → curation.md + compose consumers
- `frame_layout`, `manifest.json` runtime contract → run-contract.md + this SKILL.md "Runtime Contract"
- Aseprite-compatible Phaser / Flame JSON export → [`docs/engine-export.md`](engine-export.md)
- pixel-unfake `fit`, `.plain.png`/`orig/` twins → pixel-unfake.md
- recolor spec / report / `variants/` bake + colourway adopt → recolor.md
- `rig` profiles / landmarks, row `track` kinds, composite `layers` stack + `layers/` bake (`sprite-gen compose-layers`) → [`docs/layer-tracks.md`](layer-tracks.md) (`sprite_gen/compose/layers.py` validates the declaration, `sprite_gen/compose/compose_layers.py` bakes it)
- webview interactions (title-drag reorder, 넣기/빼기 toggle, 2-tier card, custom `data-tip` tooltip, recolor blink-compare) → `sprite_gen/curator/` (도메인 분할 `src/*.js` — 로드 순서 SSoT 는 index.html — + curator.css), described in curation.md + recolor.md
