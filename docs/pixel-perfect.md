# Pixel-Perfect Fit (`fit` / `pixel_perfect`) — sprite-gen reference

> `SKILL.md` 허브에서 분리한 시나리오 상세. 픽셀아트 타깃, 지터 없는 locomotion, 게임-레디 청키 픽셀 출력이 필요할 때 이 문서를 따른다. 구현 내부(피치 검출·grid-snap·팔레트 단계별 코드 동작)는 [`architecture.md`](architecture.md) §6 참조.

## `fit` object

Optional `fit` object (opt-in; absent means legacy behavior). For pixel-art targets and jitter-free locomotion use:

```json
"fit": { "resample": "kcentroid", "align_x": "foot-centroid", "align_y": "bottom" }
```

- `resample` — `lanczos` (default) | `nearest` | `kcentroid`. `kcentroid` (Astropulse-style dominant-cluster downscale) keeps 1px dark outlines readable when the generated art's implied pixel grid does not match the target cell; `nearest` is crisp but drops off-grid outline pixels; `lanczos` blurs pixel art.
- `align_x` — `bbox-center` | `centroid` | `foot-centroid` (default) | `alpha-centroid`. Bbox-centering shifts the body left/right whenever a pose's content bbox width changes (extended arm/leg), which reads as per-frame horizontal jitter. `centroid` aligns the whole-alpha centroid; `foot-centroid` aligns the bottom-20% alpha (the legs), so trailing hair/capes do not pull the body off the cell axis — use it when the runtime mirrors the cell for left/right facing (flip pivots on the leg axis instead of teleporting the body). `alpha-centroid` (opt-in, perfectpixel-studio port — github.com/gykim80/perfectpixel-studio `internal/sprite/extract.go`, MIT) aligns the alpha-weighted centroid ignoring soft-matte fringe (α ≤ 10); crucially, in the `pixel_perfect` row path it is applied **per frame** instead of once per row union, so residual registration jitter from `register_row_frames` is cancelled (upstream measured σ 27.2px → 0.2px; jump arcs stay preserved via `ground_frames: false`).
- `align_y` — `center` (default) | `bottom`. Bottom pins feet to a shared baseline (`cell_height - safe_margin_y`).

`prepare_sprite_run.py` exposes these as `--fit-resample`, `--fit-align-x`, `--fit-align-y`, plus the `pixel_perfect` family below as `--fit-pixel-perfect`, `--fit-logical-height`, `--fit-palette-size`, `--fit-detail-bias`, `--fit-outline {on,off,STRENGTH}`, `--fit-pitch-hint`. CLI flags override the same keys in `--request` JSON; either way the merged result is recorded in the run's `sprite-request.json` `fit` object (SSoT).

## `pixel_perfect` mode

For true pixel-perfect output (game-ready chunky pixel art with intact 1px outlines), use the `pixel_perfect` mode instead of `resample` — it removes ALL non-integer resampling:

```json
"fit": { "pixel_perfect": true, "logical_height": 64, "palette_size": 24, "align_x": "foot-centroid", "align_y": "bottom" }
```

`logical_height` 를 **생략하면 셀 높이와 동일(1:1)** 이 기본이다 — 생성 프롬프트가 "TRUE `<셀>`x`<셀>` pixel grid" 를 명시하는 현행 레시피에서 원본 그리드 해상도를 그대로 따라간다(권장). 더 청키한 저해상 룩을 원할 때만 작은 값을 명시한다: 셀 64 + 로지컬 32 → 2× 청키 픽셀.

Pipeline (unfake.js/pixeldetector-style): 포즈 컴포넌트를 먼저 분리한 뒤 **프레임별** 처리 — 엣지-정렬 스코어링 피치 검출(그리드선 ±w 에 색 경계가 모이는 비율 − 우연 기대치 |잉여류|/p 의 argmax; 창 폭 w 는 모든 p 에 동일하고 잉여류는 집합으로 세어 중복 합산하지 않는다 — w 를 p>=8 에서만 열면 참 피치가 자기 약수에게 져서 k=8,10,12,14 가 k/2 로 붕괴한다, `tests/test_pitch_ground_truth.py`) → 피치는 **소수**로 잰다 (AI 도트의 블록 폭은 정수로 안 떨어진다 — 예: 17.24px; 정수로 반올림하면 그 오차가 폭 전체에 누적돼 셀 경계가 블록 한가운데를 지난다). 격자선은 `_grid_edges` 가 길이를 셀 개수로 등분해 정수 픽셀로 확정하므로 **결과는 항상 정수 격자**다. 프레임별 검출의 **중앙값을 합의 피치**로(배수 낚임 방지, 아웃라이어는 warning), 검출 확신 미달이면 `fit.pitch_hint`(보통 베이스 검출값) → **위상은 프레임마다** 다시 잡아 grid-snap → conform to `logical_height` (kCentroid) → run-wide shared median-cut palette (`palette_size`, kills frame-to-frame color flicker) → alpha binarization → integer NEAREST upscale into the cell. `detail_bias` (default true) prefers a near-black minority cluster (share ≥ 0.40, luma < 70/255) so eyes and outlines survive dominant voting. The final display scale is `cell_height // logical_height` — e.g. cell 64 + logical 32 → crisp 2× chunky pixels. (폐기된 대안과 그 이유는 `CHANGELOG.md` v1.10.0.)

## Stage ownership (불변)

픽셀퍼펙트는 **row 추출 단계에서만** 적용한다. 베이스/앵커 생성 단계는 타깃 스타일(픽셀 룩 vs 2D 일러스트 vs 3D/실사풍)을 프롬프트·레퍼런스로 잠글 뿐, 픽셀퍼펙트 후처리를 하지 않는다 — 베이스는 row 의 identity truth 라 가공 없이 원본으로 쓴다. 생성 프롬프트가 "TRUE NxN pixel grid" 를 명시해도 모델이 완벽한 균일 그리드로 그리지는 않으므로 정렬 강제는 여전히 추출 단계 몫이다.

## 스타일의 SSoT 는 첨부된 베이스/앵커 이미지다

프롬프트 텍스트로 체형·등신·볼살·아웃라인 굵기·디테일 밀도를 재기술하지 마라 — 텍스트가 레퍼런스와 경쟁해 identity 를 되돌린다. 행 프롬프트에는 "첨부 레퍼런스를 정확히 따라라(밀도·비율·아웃라인·팔레트)" + 모션 서술 + 레이아웃/크로마 규칙만 남긴다. `STYLE_DEFAULT` 도 이 원칙으로 고정돼 있다 — 강한 스타일 지시가 필요하면 베이스를 다시 뽑아 확정하는 게 정도다.

## 픽셀 밀도는 프롬프트가 아니라 레퍼런스가 지배한다

image_gen 은 출력 크기가 ~1024px 급 고정이라 "작게 생성"은 불가능하고, "TRUE NxN grid" 문구만으로는 밀도를 못 잠근다. 모델이 실제로 따라가는 것은 **첨부된 스타일 레퍼런스의 픽셀 블록 굵기**다: 진짜 저해상 도트(예: 24~64px 스프라이트를 NEAREST 확대한 것, 실게임 스크린샷)를 붙이면 그 굵기로 그리고, 고해상 가짜-도트(1024px+ 생성물)를 붙이면 그 고밀도를 따라가 로지컬 축소에서 뭉개진다. **규칙: 픽셀 타깃 런의 스타일 레퍼런스는 반드시 타깃 로지컬 해상도급의 진짜 저해상 도트로 준비한다.** 가짜-도트 밖에 없으면 한 번 픽셀퍼펙트로 잠근 결과물을 레퍼런스로 재사용한다. 단, **베이스 raw 가 이미 그리드-인식 생성물이면 그 raw 가 최상의 앵커다** — 픽셀퍼펙트로 잠근 판을 앵커로 재투입하면 이중 열화로 얼굴/디테일이 뭉개진다.

## 역할 계약

AI 개입은 **raw 생성 한 곳뿐**이다 (`SKILL.md` 필수 게이트). 픽셀퍼펙트(피치 검출→그리드 스냅→kCentroid→팔레트→아웃라인)는 모델 호출이 없는 **완전 결정론 코드**라 같은 입력이면 항상 같은 출력이다. 에셋 제작의 기본 프로세스 = 변환 후 **큐레이션뷰 자동 런치**, **픽셀퍼펙트 적용 여부는 인간이 체크박스로 결정**. 사용자가 "뷰 생략하고 알아서 픽셀 이미지로" 라고 명시했을 때만 무인 처리한다.

## 큐레이터 표시 규칙

확대 표시는 항상 nearest(`image-rendering: pixelated`, 패시브) — 안티앨리어싱 확대가 실픽셀 품질을 뭉개 보이게 하는 착시를 막는다. 헤더의 **"픽셀퍼펙트 격자" 체크박스**는 논리 픽셀 격자를 카드 위에 오버레이한다(표시 전용, 굽기 무관). `fit.pixel_perfect` 런은 요청 scale(간격 = `cell_height // logical_height`)로 그리고, 임포트/plain 런은 줄별로 측정한 블록 피치(label `auto`)로 그린다 — 격자를 알 수 있거나 측정 가능한 줄이면 토글이 뜬다. 피치를 측정할 수 없는 줄만 격자를 그리지 않는다(가짜 격자 금지). 표시 계약 SSoT 는 `docs/run-contract.md` §3(Pixel grid 행).

## 전/후 쌍둥이 + 큐레이터 선택

`fit.pixel_perfect` 런에서 추출은 픽셀퍼펙트 결과(`frame-N.png`, canonical)와 함께 **적용 전 쌍둥이 두 개**를 저장한다: **셀 크기 `frame-N.plain.png`**(굽기용 — 아틀라스 슬롯이 셀 크기라 compose 가 이걸 읽는다)와 **고해상 `orig/frame-N.png`**(표시 전용 — 같은 fit 을 S×셀에 앉혀, pp 해제 표시가 셀 확대 흐림 없이 원본 화질). 실패 시 warning 으로 관측, 해당 쌍둥이만 빠진다. 큐레이션 웹뷰 우측 상단에 **"보기: 적용 후/전" 토글**(표시만 전환 — 해제 시 `orig/` 고해상본 우선, 없으면 `.plain.png`)과 **"픽셀퍼펙트 적용" 체크박스**(굽기 결정 → `curation.json.pixel_perfect`)가 뜬다. 체크 해제 시 compose·GIF·PNG export 전부 셀 크기 `.plain.png` 변형을 굽고(`frame_variant`/`frame_filename` 리졸버 — curation.py SSoT), plain 파일이 없으면 조용한 폴백 없이 에러다. report/manifest 에 `frame_variant` 가 기록된다. 표시 계약 SSoT 는 [`run-contract.md`](run-contract.md) §3.

## Related

- [`../SKILL.md`](../SKILL.md) — canonical behavior contract (필수 게이트, SSoT 요청 스키마)
- [`architecture.md`](architecture.md) — 추출 내부 구현 (피치 검출·grid-snap·팔레트 코드 동작)
- [`curation.md`](curation.md) — 큐레이션뷰 사용법, `curation.json.pixel_perfect` 플래그
