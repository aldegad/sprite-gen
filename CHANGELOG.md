# Changelog

> 버전 정책 (2026-07-11 수홍): **1.56.x 고정** — Sol(5.6) 오마주로 마이너 56 을 유지하고 패치만 올린다.
> v1.57.0/v1.58.0/v1.59.0 으로 나갔던 세 릴리스는 v1.56.7/8/9 로 소급 정정 (커밋 메시지의 옛 라벨은 히스토리 보존).

All notable changes to `sprite-gen` are recorded here. Versions track the `version:` field in `SKILL.md`.

## v1.56.12 "Sol Forge" - 생성 SSoT 통합 (`sprite_gen/gen/`, perfectpixel-studio C-gen)

생성 계층을 엔진 모듈 하나로 통합했다. 기존 `image-gen` 스킬의 독립 구현
(codex `image_gen` 세션추출 + 크로마 후처리)을 엔진으로 이식하고, grok Imagine
어댑터를 추가했다. Gemini/OpenRouter/fal/BytePlus 프로바이더는 넣지 않는다.

- **`sprite_gen/gen/` 신설 + CLI `gen` + `scripts/generate_sprite_image.py`** —
  프롬프트(+옵션 ref) → 검증된 raw PNG 한 장. `--provider codex|grok`,
  `--transparent --chroma-key magenta|green`(결정론 투명 계약 이식),
  `--report`(provider·`elapsed_seconds`·`session_id`·크로마 지표).
- **codex 어댑터** (`codex_provider.py`) — fresh `codex exec --json` (프롬프트 캐시
  분리), `thread.started.thread_id`(구버전 `session id:` 텍스트도 지원)로 rollout
  해석, 인라인 base64를 결정론 디코드(`image_generation_call`/`image_generation_end`
  둘 다), 모델 보고 경로 불신, 추출 후 세션 jsonl 청소.
- **grok 어댑터** (`grok_provider.py`) — `grok -p --sandbox workspace --always-approve`
  로 정확한 경로에 저장 지시 후 PNG magic 검증(`--effort` 미전달 — 미디어 모델 400).
  `--ref` 는 `image_edit` 경로.
- **투명 계약 이식** (`chroma.py`) — image-gen `chroma_key_transparent.py` 를 엔진
  함수로. 투명 픽셀 잔여 RGB 는 fail-loud (No Silent Fallback).
- `sprite_gen.generate_image` placeholder 는 `sprite_gen.gen` 리다이렉트 shim 으로
  교체(두 생성 surface 금지 — SSoT). `image-gen` 스킬은 엔진 셔틀로 개편, 구현은
  DEPRECATED/archive.
- **실 e2e**: 같은 row 프롬프트를 codex(39.02s)·grok(18.42s) 양쪽 생성, 속도 비교 +
  side-by-side proof 를 `docs/reports/perfectpixel-c-gen/` 에 보존(grok ~2.1× 빠름).
- 신규 테스트 `tests/test_gen.py`(추출·chroma·prompt·오케스트레이터 fake provider,
  네트워크 없음) + package surface 에 `gen` 추가. 문서 `docs/gen.md`. 기존 골든 추출
  경로 회귀 0.

## v1.56.11 "Sol Edge Runner" - 자동 inspect/score/correction loop (perfectpixel-studio B-loop)

perfectpixel-studio `inspect.go` / `score.go` 의 폐루프 구조를 sprite-gen 엔진에
이식했다. 생성 호출은 아직 C-gen 단계가 소유하므로, 이번 릴리스는 결정론 계측과
교정 힌트 생성, 그리고 provider 없는 dry-run loop 까지만 닫는다.

- **`sprite_gen.inspect` + `scripts/inspect_sprite_run.py` + CLI `inspect`** —
  `sprite-request.json` 기준 expected/found frame count, 64-bin RGB histogram,
  dHash silhouette similarity, motion presence, centroid σ, 기존
  `frames-manifest.json` 의 state별 warning/error 를 하나의 report 로 합친다.
  추출 frames 가 없으면 raw strip 을 읽어 projection 신호로 자연 포즈 수를 측정한다.
- **`sprite_gen.score` + `scripts/score_sprite_run.py` + CLI `score`** —
  inspect report 만 입력으로 받아 0-100 score, `candidate_rank`
  (`found*100-errors*10-warnings`), provider-ready correction hints 를 만든다.
  중복 힌트는 순서 보존 dedupe.
- **`sprite_gen.correction_loop` + `scripts/run_correction_loop.py` + CLI
  `correction-loop`** — 최대 3패스 inspect → score → hint 루프. dry-run 은 생성 없이
  리포트만 남기고, 실제 재생성은 `--provider-command` 가 명시된 경우만 실행한다
  (없으면 fail-loud). 작은 fixture 테스트에서 best-candidate 보존 경로를 고정.
- **founder_v7 실데이터 dry-run**:
  `docs/reports/perfectpixel-b-loop-founder-v7/` 에 `up_idle` A-runlen 경고
  (collapsed pitch/outlier) → score 91 → pixel-grid correction hint 로그를 보존.
- 신규 테스트 3개 + package surface 업데이트. 기존 골든 추출 경로는 읽기 전용으로만
  참조해 기본 extract/compose 동작을 바꾸지 않는다.

## v1.56.10 "Sol Edge Runner" - 런길이 최빈값 피치 추정기 (perfectpixel-studio 이식, 교차검증 전용)

perfectpixel-studio `internal/sprite/pixelize.go`(MIT) 의 unfake(동일색 런 길이
최빈값으로 실블록 크기 추정)를 이식. `detect_pixel_grid`(경계 히스토그램)는 정수
씨앗 ±0.75 창 안에서만 소수 피치를 정밀화하므로 씨앗 로터리가 실패하면 조용히
틀린다 — 실사고: 솔벨 주인공 컴포넌트에서 y 피치가 x 값(29.52)으로 붕괴(실측
30.56, 참값의 정밀화 점수 0.78 vs 붕괴값 0.07 — 창 밖이라 후보조차 못 됐다).
런 길이는 경계 히스토그램과 독립인 신호(경계 위치가 아니라 경계 사이 거리)라
세컨드 오피니언이 된다.

- **`estimate_pixel_grid_runlen` 신규 (추정 전용)** — 원본과 달리 축별 분리
  히스토그램(축 붕괴를 잡으려면 필수), 런 길이 가중 `hist[s]·s`(원본 동일, 짧은 런
  지배 방지), 최빈값 ±1 창의 가중 무게중심으로 소수화(참 30.56 → 30/31 런 44:56
  혼합 → 무게중심이 복원). 확신 게이트: 런 수 부족·고조파 패밀리(k·mode±k) 질량
  절반 미만·32px 미만이면 (1.0, 1.0) 으로 관측 가능하게 포기.
- **`crosscheck_pitch_runlen` 신규 + 픽셀퍼펙트 합의 직후 훅 (기본 on, 경고 전용)**
  — 불일치는 report `warnings` + stderr `[pitch-crosscheck]` 로만 표면화. 스냅은
  계속 `detect_pixel_grid` 합의만 쓴다 (자동 교체 금지, No Silent Fallback — 어느
  쪽을 쓸지는 사람이/상위 게이트가 판단). runlen 의 오차 모델(AA 가 런 양끝을
  갉아 픽셀 단위 하향 바이어스)이 규칙 모양을 정한다:
  - 약수 오검출(runlen ≫ grid, 슬랙 2px): 참 29.5 를 14.73 으로 잡는 모드.
  - 배수/고조파 오검출(grid ≫ runlen, 슬랙 12%+3px).
  - 축비(y/x) 불일치 > max(2%, 0.7/피치): 축 붕괴 모드 — 축차 3.5% 는 축별 규칙
    밑에 숨지만 AA 공통 바이어스가 비율에서 상쇄돼 잡힌다. 0.7/피치 하한은 축별
    바이어스 편차(서브픽셀)의 소피치 확대를 흡수 — founder_v7 8~15px 대역의 판별
    불가 드리프트(3~9%)는 침묵, 히어로 스케일(30px) 실붕괴 신호(2.8%)는 발화.
- **실측 (founder_v7 22 state, 읽기 전용)**: 건강한 20 state 경고 0, 경고는 정확히
  실고장 2건 — down_carry_run grid (4.00, 4.00) vs runlen (6.91, 7.96) (플랜 기록
  참값 ~9, 약수 붕괴), side_carry_idle grid x=6.00 vs runlen 10.99.
- 110 tests OK (신규 10: 정수/소수 축별 정답 · 노이즈/소형 확신-없음 · 정상합의
  침묵 2종 · 약수 픽스처(20x36@29.5/30.6) · y축 붕괴 픽스처(28x60@29/30.3, 실사고
  메커니즘 결정론 재현) · 확신 게이트 · 파이프라인 통합: 경고 표면화 + runlen 중화
  시 프레임 비트 동일 = 스냅 영향 0 고정). 기존 100 테스트 회귀 0.
- NOTICE 에 MIT 출처 표기 (perfectpixel-studio internal/sprite/pixelize.go).

## v1.56.9 "Sol Edge Runner" - chroma.mode: ycbcr (perfectpixel-studio 이식, 옵트인)

perfectpixel-studio `internal/sprite/chroma.go`(MIT) 의 색차(CbCr) 평면 매팅을 이식.
현행 RGB 경로는 키와의 RGB 거리로 분류하므로 배경 쉐이딩·그라디언트·JPEG 4:2:0
크로마 노이즈가 erase 반경(96)을 벗어나면 잔존한다 — 이 경로는 루마를 통째로 무시하고
CbCr 평면에서만 분리해 명암 변화에 강건하다.

- **`chroma.mode: "ycbcr"` 신규 값 (옵트인, 기본값 "rgb" 불변)** — CLI `--chroma-mode
  {rgb,ycbcr}` 는 request 를 덮는 명시 override, effective 값은 request 에 되써진다.
  기본 경로는 비트 동일 (골든 회귀 0).
- 파이프라인: 배경키 = 코너 패치+얇은 테두리의 **CbCr 히스토그램 최빈값**(평균 금지,
  그라디언트에 안 밀림; 선언 키 계열이 테두리 샘플 12%+ 면 그 클러스터 우선) →
  Hermite smoothstep 소프트 매팅(24→72) → **키 방향 성분만 빼는 despill**(직교 색
  보존) → 테두리 4-연결 flood fill(내부 고립 키계열 픽셀 보존) → 고립 점 제거·핀홀
  메움 → **자가진단 폴백**: 불투명율 스파이크(피사체 오삭제) 또는 선언키 잔존
  스파이크(배경 미제거) 시 순수 선언 키로 재매팅, 더 나은 쪽 채택 — 폴백은 추출
  warnings 로 관측 가능 (No Silent Fallback).
- **실측 (그린키)**: 열화 소스 합성 픽스처(루마만 낮춘 쉐이딩 그린 밴드 — RGB 거리
  115 로 erase 반경 밖, unmix 도달 깊이 밖) 그린 틴트 잔존 **1728 → 0**, 밴드 불투명
  잔존 **1920 → 0**. 반면 founder_v7 클린 플랫키 raw 22 state 합계: fringe(RGB≤150)
  0→0 · CbCr 잔존 0→0 로 동률, 그린 틴트 엣지 잔존은 rgb 9 vs ycbcr 2779 (0.92 고정
  스케일 despill 은 현행 exact-solve unmix 와 달리 틴트를 완전히 못 뺌, 피사체 손실
  아님 — 불투명 커버리지 차 -1~-2.3% 는 엣지 수렴 차이). **결론: 클린 소스는 기본
  rgb 유지, ycbcr 는 열화 소스(쉐이딩/그라디언트/JPEG) 전용 옵트인** —
  `docs/chroma-alpha.md` 에 명시.
- 100 tests OK (신규 7: 쉐이딩 배경 rgb/ycbcr 대비 · 최빈값 키 검출 2분기 · 키방향
  despill/직교 보존 · flood 내부 보존 · 자가진단 폴백 관측 · CLI e2e · 기본 rgb 고정).
- NOTICE: MIT 출처 표기.

## v1.56.8 "Sol Edge Runner" - segmentation: projection (perfectpixel-studio 이식, 옵트인)

perfectpixel-studio `internal/sprite/segment.go`(MIT) 의 projection-profile + DP 최적 컷
프레임 분리를 `sprite_gen/segment.py` 로 이식. connected-components 는 팔·소품이 이웃
프레임과 닿으면 붙은 포즈를 한 덩어리로 합쳐 추출이 실패한다 — 세로 알파 프로젝션
P[x]=Σα 의 골(gutter)로 자연 포즈 수를 세고, 골이 사라지면 DP 로 `Σ P[cut] +
λ·(width−ideal)²` 최소 컷을 찾아 정확히 기대 프레임 수로 분리한다.

- **`fit.segmentation: "projection"` 신규 값 (옵트인, 기본값 components 불변)** — CLI
  `--segmentation {components,projection}` 은 request 를 덮는 명시 override. 활성 시
  크로마 제거 직후 스트립을 컷 경계에서 갈라 투명 거터를 넣어 재조립하는 pre-pass 라
  하류 connected-components·위성 병합·pixel-perfect 경로는 무변경으로 그대로 동작한다.
  분리 실패는 스트립을 건드리지 않고 stderr 로 보고 — 하류가 기존 에러로 관측 가능하게
  실패한다 (No Silent Fallback).
- **융착 픽스처 골든** (`tests/fixtures/run-fused/`) — 팔이 닿아 한 덩어리가 된 3포즈
  스트립: components 는 실패(에러 기록), projection 은 3/3 분리 (골든 매니페스트 고정).
- **분리된 스트립에는 완전 투명** — 기존 골든 런에 projection 을 켜도 매니페스트가
  비트 동일함을 테스트로 고정.
- founder_v7 실측: carry/action 8개 state 회귀 0 (natural 골짜기로 기대 수 그대로).
  down_carry_walk 프레임을 16/24/32px 겹쳐 재조립한 실제 융착 스트립에서 components 는
  3→1 덩어리로 붕괴(추출 실패), projection 은 전부 6/6 분리.
- 93 tests OK (신규 10: 융착 골든·옵트인 부재 실패·CLI 활성/무효화·비트동일·순수함수).

## v1.56.7 "Sol Edge Runner" - align_x: alpha-centroid (perfectpixel-studio 이식, 옵트인)

perfectpixel-studio `internal/sprite/extract.go`(MIT) 의 알파 가중 무게중심 정렬을 fit 에 이식.
bbox 중심은 팔/무기가 뻗은 프레임에서 몸통을 반대로 밀어 재생 시 좌우 지터를 만들고,
cx=Σ(x·α)/Σα 는 면적이 큰 몸통이 지배해 축이 안정된다 (상류 실측 σ 27.2px→0.2px).

- **`fit.align_x: "alpha-centroid"` 신규 값 (옵트인, 기본값 foot-centroid 불변)** —
  소프트 매팅 프린지(α ≤ 10)를 무게에서 제외하는 알파 가중 무게중심을 셀 중앙에 정렬.
  `fit_to_cell` / `fit_pixel_perfect` / `row_placement` 세 경로 모두 지원, 출처 주석 표기.
- **픽셀퍼펙트 행 경로에서는 프레임별 배치** — 기존 모드들은 행 union 공동 left 하나를
  전 프레임에 쓰므로 `register_row_frames` 의 정합 잔차가 align_x 선택과 무관하게 그대로
  지터로 남는다(실측: bbox-center 와 foot-centroid 의 σ 가 동일). alpha-centroid 만
  프레임마다 무게중심을 셀 중앙에 앉힌다(논리 격자 스냅으로 flip 대칭 보존). 점프 아크는
  기존 `ground_frames: false` 가 담당(상류 baseline 오프셋과 동일 역할).
- **`scripts/measure_align_sigma.py`** — align_x 변형별 프레임 무게중심 X 의 σ 를 리포트
  (원본 run 은 읽기 전용, 스크래치 복사 후 재추출). founder_v7 실측: down_run σ
  0.53px→0.17px (−68%), down_walk 은 0.03px 로 이미 포화(동일).
- 83 tests OK (신규 5: 프린지 불감·옵트인 보증·per-frame 지터 상쇄·격자 스냅/클램프).

## v1.56.6 "Sol Edge Runner" - 붕괴 프레임이 행 합의 피치를 오염시키던 문제 + span 중복 합산

솔벨 `down_carry_run` 6 프레임 중 3개가 피치 3.00 으로 무너졌고(참값 8.56), 중앙값 합의가
**5.00** 으로 오염돼 행 전체가 잘못 스냅됐다. v1.56.5 의 축 불일치 가드는 두 축이 **함께**
무너지면(3.00/3.00) 잡지 못한다.

- **행 합의 피치에서 붕괴 프레임을 버린다**: 행 안에서 참 피치는 거의 같으므로, 프레임별 검출값
  중 최대값의 60% 미만은 붕괴로 보고 제외한 뒤 중앙값을 낸다. 몇 개를 버렸는지 warning 으로
  남긴다(조용히 고치지 않는다). 실측: 합의 5.00 → 8.56 복구.
- **`_axis_refine` 의 span 이 bins 를 넘을 수 있었다** — 순환 윈도우가 같은 bin 을 두 번 세어
  `frac > 1.0` 이 되고, 작은 피치일수록 점수가 부풀었다. `span = min(bins, ...)` 로 클램프.
- 78 tests OK.

## v1.56.5 "Sol Edge Runner" - 축 불일치 가드: 한 축이 약수로 무너지면 신뢰 축을 쓴다

솔벨 `down_carry_walk` 행에서 한 프레임의 축별 피치가 **가로 9 / 세로 3** 으로 나왔다 (참값 9,
독립 측정 = 엣지 간격 최빈). 스냅 결과가 가로로 짓눌려 부서졌다. 팔을 머리 위로 든 포즈는 세로로
균일한 막대가 화면을 채워 **세로 엣지가 고갈**되고, 그 축에서 참 피치의 약수(3 = 9/3)가 이긴다.

- **가드**: 축별 피치가 1.5배 넘게 벌어지면 엣지 총량이 많은 축의 피치를 양쪽에 쓴다. 비균등
  리스케일로 생기는 실제 축 차이는 2% 수준이므로(솔벨 chibi 베이스 30.38 / 30.92), 3배 격차는
  물리적으로 불가능하고 한 축의 검출 실패다.
- 약수 후보(`s/2`, `s/3`)는 그대로 둔다 — 실측상 정수 씨앗이 참 피치의 2·3·5배 배음을 자주 집는다
  (같은 행에서 씨앗 19 / 28 / 29 / 48, 참값 9). 약수를 빼면 배음을 못 내려온다.
- **재현 한계**: 사고 당시의 raw 는 폐기되어 그 프레임을 재현하지 못했다. 합성(무작위 도트 +
  가우시안 블러)으로는 붕괴가 나오지 않는다 — p=3 의 점수 상한은 0.25, p=9 는 0.75 다. 그래서
  이 릴리스는 **원인을 좁힌 것이 아니라 관측된 실패 패턴을 막는 가드**다. 축 불일치라는 관측
  가능한 신호에만 반응하므로 정상 그림에는 영향이 없다.
- 회귀 테스트 2개: 세로 엣지를 고갈시킨 합성 프레임, 작은 블록(9px) 왕복. 78 tests OK.

## v1.56.4 "Sol Edge Runner" - 피치를 축별로 잰다 (가로/세로 블록 크기가 다를 수 있다)

`detect_pixel_grid` 가 한 피치를 두 축에 강제했다. 비균등 비율로 리스케일된 생성물은 가로 블록과
세로 블록의 크기가 어긋난다 — 솔벨 주인공 chibi 베이스는 가로 30.38px / 세로 30.92px 다.
한 피치(30.92)를 두 축에 쓰면 세로는 맞고 **가로만 통째로 미끄러진다**: 실제 블록 경계가 격자선
위에 얹힌 비율 가로 11.7% / 세로 92.6%. 그 결과 스냅한 얼굴이 부서졌다.

- `detect_pixel_grid` 반환형이 `((pitch_x, pitch_y), (phase_x, phase_y))` 로 바뀐다.
- `grid_snap_downscale(pitch=...)` 는 스칼라와 (가로, 세로) 쌍을 모두 받는다 (기존 호출 호환).
- 씨앗은 **축별 씨앗 + 두 축 합산 씨앗** 을 모두 후보로 둔다. 축별만 쓰면 한 축의 정수 검출이
  노이즈에 흔들려 약수로 빠지고(참 17.24 -> 씨앗 9), 합산만 쓰면 축마다 블록이 다른 그림에서
  한 축의 참값이 ±0.75 정밀화 창 밖에 놓인다. 둘 다 두면 두 실패가 모두 막힌다.
- 정수 스코어러를 `_axis_int_score` / `_axis_int_seed` 로 분리해 `detect_pixel_pitch` 와 공유한다.
- **측정 (솔벨 chibi 베이스)**: 가로 정렬률 11.7% -> 75.7%. 세로 92.6% 유지.
- **회귀 테스트**: 가로 24 / 세로 30 으로 비균등 업스케일한 도트에서 축별 피치를 각각 잡고,
  스냅하면 원본 논리 도트가 픽셀 단위로 복원된다. v1.56.3 은 이 테스트에서 실패한다.
- 76 tests OK.

## v1.56.3 "Sol Edge Runner" - hotfix: 등분 격자가 bbox 자투리에 늘어나던 회귀

v1.56.2 가 넣은 `_grid_edges` 의 "length 를 셀 개수로 등분" 이 스프라이트 bbox 가 블록의
정수배가 아닐 때 격자를 늘렸다. 솔벨 주인공 chibi 베이스에서 발견 — bbox 849px = 27.46 블록
(AA 프린지), 27 등분하면 셀 폭 31.44px 로 참 블록 30.92px 와 칸마다 0.52px 어긋나고 오른쪽
끝에서 반 블록이 밀려 스냅 결과의 얼굴이 부서졌다(눈 하나 소실, 아웃라인 파편화).

- **등분은 body 가 피치의 정수배에 가까울 때만**(잔차 <= 블록의 1/4) 쓴다. 이때는 피치 측정의
  미세오차(16.00 을 15.96 으로 재는 것)를 흡수해 격자가 딱 떨어진다.
- 정수배가 아니면 격자선을 `lead + i*pitch` 로 직접 놓고 남는 자투리는 **마지막 셀 하나가 흡수**한다.
  어느 쪽이든 피치를 누적 덧셈하지 않아 부동소수 오차는 쌓이지 않는다.
- **회귀 테스트**: bbox 오른쪽에 블록의 정수배가 아닌 자투리(1/7/14/20px)를 붙여도 마지막 셀을
  뺀 모든 셀 폭이 참 피치 ±1px 여야 한다. v1.56.2 는 이 테스트에서 실패한다.
- 기존 소수 배율 왕복 테스트 전부 유지. 74 tests OK.

## v1.56.2 "Sol Edge Runner" - pixel-grid detection: fractional pitch, phase, divisor collapse

Patch release in the Sol Edge Runner line. Three real bugs in `detect_pixel_pitch` / grid snapping, all found while rebuilding the Sol Valley protagonist base and all pinned by synthetic ground-truth tests (`tests/test_pitch_ground_truth.py`).

- **참 피치가 자기 약수에게 졌다.** 창 폭이 `w = 1 if p >= 8 else 0` 이라, 창이 열린 참 피치(p>=8)는 우연 기대치가 3/p 로 부풀고 창이 닫힌 약수(p<8)는 1/p 만 물었다. 엣지가 격자에 100% 얹혀도 p=4(0.75) 가 p=8(0.625) 을 이겨서 k=8,10,12,14 가 정확히 k/2 로 붕괴했다. 창 폭을 모든 p 에 동일하게 고정하고 잉여류를 집합으로 세어(중복 합산 제거) 정수 검출 정확도 7/11 -> 11/11.
- **피치를 소수로 잰다.** AI 가 그린 도트는 블록 폭이 정수로 떨어지지 않는다 (솔벨 주인공 base = 17.22px). 정수로 반올림하면 칸마다 오차가 쌓여 23칸 뒤에는 5.5px, 블록의 1/3 이 밀린다 — 셀 경계가 블록 한가운데를 지나 작은 디테일이 평균에 먹혔다. 새 `detect_pixel_grid()` 가 (소수 피치, 소수 위상) 을 내고, `_grid_edges()` 는 피치를 누적하는 대신 길이를 셀 개수로 등분한다: **측정은 소수, 결과는 항상 정수 격자.** 씨앗의 약수(2,3)도 후보로 재서 참 16.5 에서 배음 33 을 집던 문제를 막고, 정밀 탐색은 씨앗값 자체를 포함하며(예전엔 15.99/16.01 만 봐서 정확히 정수인 격자를 놓쳤다), 위상은 창의 기하 중심이 아니라 창 안 엣지의 가중 무게중심으로 잡는다(중심이면 완전 정렬 격자에서 반창만큼 밀렸다).
- **큐레이션뷰 격자가 가짜였다.** 오버레이가 셀 픽셀마다 선을 그었지만 픽셀퍼펙트는 논리 픽셀 단위로 스냅한다 (`pp_scale = cell_height // logical_height`). founder_v4 는 64 셀에 logical 30 -> 실제 격자보다 정확히 2배 촘촘한 선을 보여줬다. 서버가 `pixelPerfect{logicalHeight, scale}` 를 payload 에 싣고 큐레이터가 그 간격으로 긋는다. 픽셀퍼펙트가 아닌 런은 스냅 격자가 없으므로 토글을 감춘다.
- **측정 결과** (솔벨 주인공 base, 실제 블록 경계가 격자선 위에 얹힌 비율): 가로 30% -> 58%, 세로 5% -> 55%. 평균 오차 가로 2.10px -> 1.74px, 세로 5.04px -> 1.59px.
- **회귀 테스트**: 소수 배율(12.0/14.35/16.0/16.2/17.24/20.0/23.7) 왕복 — 늘렸다 줄이면 원본 논리 도트가 돌아온다. 크기 복원 3/8 -> 8/8, 픽셀 완전복원 3/8 -> 6/8. (16.5 처럼 정확히 반픽셀 배율은 블록 경계가 화면 픽셀 한가운데에 걸려 잔차가 남는 원리적 한계.) 기존 62 테스트 전부 통과 — 정수 격자로 뽑은 기존 에셋은 출력이 바뀌지 않으므로 재추출 불필요.

## v1.56.1 "Sol Edge Runner" - slice-sheet: variant grid sheets to per-cell standing cuts

Patch release in the Sol Edge Runner line. Adds the `slice-sheet` tool, distilled from the Sol Valley dialogue cut-in overhaul (2026-07-09): one generated image holding a COLSxROWS grid of the same character's expressions becomes per-cell 512x768 RGBA cuts with a shared feet baseline and normalized body height.

- **New module `sprite_gen.slice_sheet`** + wrapper `scripts/slice_sheet_cells.py` + CLI subcommand `sprite_gen.cli slice-sheet`. Alpha truth stays `remove_chroma_background` (v1.13 4-pass); the module owns only cell geometry.
- **Geometry rules encode the field failures**: centroid component-to-cell assignment (grid cropping imported neighbour fragments), merged-figure split with in-cell re-label (a kunai-fused neighbour's hair shipped as a floating clothes fragment without it), border-touching debris drop at `--debris-fraction` 0.30 (in-cell effects like hearts survive), per-cell height normalization (a sheet shipped rows at visibly different body sizes under sheet-wide max scaling), feet pinned to `--baseline-y`, fail-loud empty cells.
- **Manifest path separators are POSIX-stable on Windows**: frames manifests, selected-cycle manifests, and unpack manifests now serialize run-relative frame paths with `/` separators instead of host separators, reported and first fixed by @bokjk in #2.
- **Docs**: new leaf `docs/sheet-slicing.md` (usage, geometry rules with their field accidents, sheet prompting guidance including the cream-background flood-fill ban); SKILL.md Script Map + Docs Topology entries.
- **Tests**: `tests/test_slice_sheet.py` synthetic-sheet coverage — height/baseline normalization, neighbour-overhang drop vs effect survival, zero chroma residue; `slice_sheet` registered in the package-surface run() guard.

## v1.56.0 "Sol Edge Runner" - Importable package SSoT, behavior unchanged

This is a behavior-preserving structural release. The public pipeline scripts remain backwards-compatible CLI entrypoints, while the algorithm implementation now has one importable `sprite_gen` package SSoT. The release is minor because `sprite_gen` is a new public import surface for downstream apps and MCP hosts.

- **`sprite_gen` package added.** The current script bodies were modularized into package modules instead of copying older desktop-v2 algorithm bodies, so v1.13.0 chroma unmix, despill, auto-key, fit, and pixel/plain frame behavior remains the algorithm truth.
- **`scripts/*.py` are compatibility wrappers.** Existing script commands delegate to package modules and preserve the previous CLI surface; wrapper `--help` output diffed 0 against the pre-wrapper baseline.
- **`runio` reentrant lock adopted from v2.** Same-process package calls can re-enter the run-dir lock without self-deadlocking, while atomic write/replace behavior stays unchanged.
- **Curation schema merged both sides.** The main `frame_variant` / `frame_filename` selection and v2 `deleted` state now coexist, so plain-vs-pixel frame choice and permanent deletion both survive.
- **Unified CLI added.** `sprite_gen.cli` exposes 8 subcommands: `prepare`, `extract`, `compose-atlas`, `preview`, `compose-cycle`, `compose-gif`, `unpack-atlas`, and `export-pngs`.
- **Packaging activated.** `pyproject.toml` now includes the `sprite_gen` package for editable installs and keeps the package metadata version synchronized with `SKILL.md`.
- **Behavior identity evidence.** The refactor regenerated the SSoT baseline commands and produced 118/118 identical output sha256 hashes against the pre-refactor baseline.

## v1.13.0 — Chroma peel removal, key-depth unmix, safer auto key sampling

This release removes the legacy fringe erase peel that became harmful after v1.12.0's soft-alpha unmix. The peel ran before unmix, deleted the original 1.1-1.3 px antialias band wholesale, turned silhouettes into binary stair-steps, and could erode thin outlines or hair strands by 1-2 px. Soft-alpha unmix now owns chroma boundary cleanup.

- **Fringe erase peel removed completely.** `--fringe-reach` and the `remove_chroma_background(..., fringe_reach=...)` parameter are gone; old CLI use fails loudly with `unrecognized arguments` instead of silently re-enabling a destructive path.
- **In-band unmix guard changed from subject-neighbor to key-depth.** In-band key blends are unmixed only when their distance from the keyed region is `<= 2`; out-of-band blends still use `--fringe-unmix-reach` (default 4). This fixes blend pockets whose inner pixels had no untinted `_SUBJECT` 8-neighbor and previously survived as `alpha=255` warm grey/brown crust.
- **`--fringe-unmix-reach 0` now disables all chroma boundary cleanup beyond the hard key cut.** Before the peel removal, `unmix_reach=0` still allowed the independent fringe erase peel to remove boundary fringe; in v1.13.0, the peel is gone, so `0` means no soft-alpha unmix and no peel cleanup. On the downscaled fixture `tests/fixtures/moe/moe_heart.png`, measured mid-alpha is `204` at reach 4, `196` at reach 2, and `0` at reach 0.
- **Optional unmix tunables are keyword-only.** `unmix_reach` and `spill_max_fraction` can no longer be passed as ambiguous positional arguments, preventing old six-argument calls from silently changing meaning after `fringe_reach` was removed.
- **Package metadata version synchronized.** `pyproject.toml` now mirrors `SKILL.md` at `1.13.0`; future releases must update both fields in the same release commit because editable installs and CI consume `pyproject.toml`, while the changelog treats `SKILL.md` as the release SSoT.
- **`--chroma-key auto` no longer counts opaque chroma background as subject** (commit `83b269b`). Candidate scoring excludes the detected flat background, records candidate `score` / `min_subject_distance` / `clears_erase_radius` / `background` metadata, and keeps the nearest-subject erase-radius guard.
- **Measured cleanup impact.** Sources are labelled per line. The full-resolution raw inputs live under `assets/chroma-repro/` (local-only, gitignored, not shipped); the downscaled fixtures under `tests/fixtures/` ship with the repo:
  - moe-heart raw, magenta key: mid-alpha `5,391 -> 11,621`; `6,274` former peel-band pixels, `alpha=255` residue `0`.
  - moe-mirror raw, green key: mid-alpha `1,971 -> 6,835`; `4,877` former peel-band pixels, `alpha=255` residue `0`.
  - accident fixtures: opaque subject pixels improve from herb `4,254 -> 4,415`, seed `4,501 -> 4,513`.
  - pixel-art after `binarize_alpha`: pixel-heart silhouette `+115 px`, pixel-mirror `+1,781 px`, discarded pixels `0`; recovered pixels are key-distant black outline colors on average `(35,26,19)` / `(7,9,11)`.
- **Tests and fixtures**: `tests/fixtures/moe/moe_heart.png` and `tests/fixtures/moe/moe_mirror.png` pin the green/magenta mirror cases; `tests/test_chroma_extraction.py` pins former peel-band soft alpha, heart material byte-identity, accident opaque baselines, and keyword-only fail-loud behavior; `tests/test_chroma_key_auto.py` covers the auto-key background exclusion.

## v1.12.0 — Soft-alpha chroma edges, trapped-spill despill, fit CLI parity

Non-pixel-target extraction produced binary alpha (0 or 255) — antialiasing died at the hard key cut, so every silhouette read as a staircase, and key-colored blend pixels trapped between hair strands survived as opaque magenta/green residue (351/339 px on the two repro runs). Extraction alpha cleanup in `extract_sprite_row_frames.py` is now a four-pass chain; the v1.10.1 guarantee (key-tinted subject interiors like hot-pink packets and purple crystals survive byte-identical) is regression-tested and unchanged.

- **Pass 1 — hard key cut** (unchanged): pixels within `--key-threshold` of the key erased, alpha=0 RGB cleared to `(0,0,0)`.
- **Pass 2 — fringe erase peel** (unchanged v1.10.1 semantics): in-band key-tinted pixels chain-adjacent to the keyed region erased, at most `--fringe-reach` layers. Erase set is byte-identical to v1.10.1, so the accident-fixture protections hold.
- **Pass 3 — soft-alpha unmix** (new): key-tinted blends the erase pass cannot represent — out-of-band boundary blends, and in-band specks touching untinted subject — within `--fringe-unmix-reach` (default 4) of the keyed region are solved against the blend model `observed = (1-k)·subject + k·key` and rewritten as despilled RGB + **partial alpha**. Silhouettes keep their antialiased coverage ramp; blend pockets inside interior holes (between hair strands) stop surviving as opaque residue. Measured on the repro runs: mid-alpha 0 → 2,092/2,263 px.
- **Pass 4 — trapped-spill despill** (new): small connected key-tinted clusters buried deep inside the subject (generator spill drawn into the hair, unreachable by any bounded peel) are detected by cluster size (≤ `--spill-max-fraction` of the subject, default 0.005, floor 32 px) plus one strongly tinted pixel (tint > 40), and color-corrected in place with alpha kept — no pinholes. Large key-tinted regions (real material, 7–20× above the threshold on the accident fixtures) and marginally warm skin tones never qualify. Key-tint residue on the repro runs: 351/339 → **0**.
- **Request JSON SSoT**: the extractor reads `chroma.unmix_reach` / `chroma.spill_max_fraction` from `sprite-request.json`, CLI flags override, and effective values are written back. Pixel-perfect path unaffected (downstream α≥128 binarize).
- **`prepare_sprite_run.py` fit CLI parity** (docs promised these since v1.10.0): `--fit-resample` gains `kcentroid`, `--fit-align-x` gains `foot-centroid`, and the `pixel_perfect` family is exposed — `--fit-pixel-perfect`, `--fit-logical-height`, `--fit-palette-size`, `--fit-detail-bias`, `--fit-outline {on,off,STRENGTH}`, `--fit-pitch-hint`. CLI overrides merge over `--request` JSON and the merged `fit` object is recorded in the run's `sprite-request.json`.
- **Tests**: new `tests/test_chroma_soft_alpha.py` on 1/8-NEAREST repro fixtures (`tests/fixtures/moe/`) pins boundary partial alpha, zero key-tint residue, deep-interior byte-identity, and the trapped-spill pass; `tests/test_pipeline_smoke.py` pins fit-CLI-to-request recording. The extraction golden manifest needed no update: the synthetic fixture strips contain no key-blend pixels, so passes 3–4 are no-ops there and the manifest matches bit-for-bit.

## v1.11.0 — SKILL.md becomes a thin hub; scenario detail moves to a `docs/` leaf network

Docs-only topology split (no script changes). A 589-line SKILL.md front-loaded every scenario's detail into every session; it is now a 299-line hub — BLOCKING gates, workflow commands, contract summaries, and one-click links into leaf docs read only when their scenario comes up. The split is lossless: every rule, number, and prohibition lives in exactly one place, hub or leaf.

- **SKILL.md hub (589 → 299 lines, `version: 1.11.0`).** Keeps verbatim the mandatory raw→deterministic gate, the Base Lock Gate criteria, the Motion Continuity BLOCKING declaration, the prompt/output/runtime contracts, and the step 0–5 command blocks. Adds a `## Docs Topology` section listing each leaf with a one-line "read when" trigger. The only deletion is the License And Attribution section (duplicated in README).
- **Five new leaf docs** carved out of the hub: `docs/pixel-perfect.md` (the `fit` object, `pixel_perfect` mode, stage ownership, role contract), `docs/states-and-frames.md` (MVP state scope, quick-path request, frame-count guidance), `docs/curation.md` (standalone curation-view recipe, webview usage, finished-sheet editing, multi-agent launch rules, `curation.json` schema), `docs/chroma-alpha.md` (key-selection branching, `auto` scoring, extraction internals, slot fallback), `docs/qa-motion.md` (full motion-continuity judgment criteria).
- **`reference/` folder retired**: `directional-anchor-workflow.md` and `locomotion-curation.md` moved under `docs/` (content unchanged, internal links updated).
- **`docs/architecture.md` refreshed to v1.10.x reality**: absorbs the base-frame ownership ASCII flow from the hub, documents the pixel-perfect fit path against the actual `extract_sprite_row_frames.py` call order, and replaces the retired HTML/PNG diagrams with embedded mermaid.
- **Retired files deleted**: `docs/architecture-diagram{,.ko}.{html,png}` (4 files, ~1.6 MB of hand-maintained diagrams superseded by mermaid) and `docs/skill-improvement-plan.md` (stale 2026-06-02 draft, absorbed by v1.10.x).
- **README** swaps the PNG diagram embed for a GitHub-native mermaid pipeline block plus a `docs/architecture.md` link; all five translated READMEs (ko/ja/es/fr/zh-Hans) regenerated from the English source.

## v1.10.2 — Dependency source declared for `image-gen`

Docs-only. A fresh installer could not resolve the `kuma:image-gen` dependency from its name alone.

- **`SKILL.md` `depends_on.required_skills`** now declares the public source alongside the name: `name: kuma:image-gen`, `source: github:aldegad/image-gen`.
- **README `## Install`** gains a "Required skill dependency" subsection with the `install-skill-from-github.py --repo aldegad/image-gen` command; all five translated READMEs (ko/ja/es/fr/zh-Hans) regenerated from the English source.

## v1.10.1 — Boundary-limited fringe cut (key-tinted subjects survive) + mandatory raw→deterministic gate

Fixes the third way extraction destroyed real subject colors, and hardens the skill contract that a prior worker shortcut violated.

- **The fringe tint-gate no longer erases key-tinted subject material.** `remove_chroma_background` cut every pixel with `distance <= 180` and `key_tint_score >= 18` *anywhere in the image*, so hot pink (~129 from magenta) and purple (~153) subjects fell inside the band and were bleached wholesale — real accidents: solvell `seed_flower_pink` (hot-pink seed packet rendered three times as a white flower) and `herb_plant_star_bloom` (purple star bloom turned white), both on a magenta key, 2026-07-07. Fringe is boundary antialiasing by definition, so the cut is now limited to pixels spatially adjacent to the keyed-out background, peeled at most `--fringe-reach` layers (default 2). Measured on the accident raws: 98.7% / 98.9% of the key-tinted subject pixels survive (previously 0%), while a green-subject control (`herb_plant_wind_leaf`) removes the exact same 2,582 fringe pixels as before — no quality regression.
- **Regression tests** in `tests/test_chroma_extraction.py`: boundary fringe still removed, isolated fringe-band colors treated as subject, hot-pink/purple interiors survive, and 1/8-size NEAREST copies of both accident raws (`tests/fixtures/accident/`) must keep >= 90% of their fringe-band subject pixels.
- **SKILL.md leads with a BLOCKING gate**: AI touches raw generation only; the final asset must go through the deterministic extraction transform — a plain `PIL.resize()` downscale shortcut is a failed result (the shortcut a worker actually took on 2026-07-07, degrading edges). Chroma key selection by subject color (pink/purple → green, green plants → magenta) is part of the gate; the branch-table SSoT lives in image-gen's SKILL.md top gate.
- **History moved out of the SKILL.md body** (per skill-hook-authoring): dated redesign narratives now live here. For the record — the pixel-pitch detector replaced a run-length-mode approach that antialiased 2px runs dominated; per-frame phase snapping replaced a strip-global grid that always let some frames slide (inter-frame phase drift); the `logical_height` default changed from half the usable height (which mushed a protagonist to ~logical 30) to cell-height 1:1 (2026-07-05); the style-contract prose ("compact chibi / chunky / thick outline") that kept polluting a slim base was removed in favor of reference-image style SSoT; a 64px-locked anchor re-input erased eyes while the raw anchor preserved them (double-degradation proof, 2026-07-05).

## v1.10.0 — Pixel-perfect row pipeline (`fit`), auto-outline, light-theme curator

Game-ready pixel-art output for animation rows, built while shipping the Sol Valley protagonist (Godot 4, 64px cells). The headline: extraction no longer treats each frame as an independent image — pixel-perfect is a *row* methodology.

- **New `fit` object in `sprite-request.json`** (opt-in; absent = legacy behavior), exposed via `prepare_sprite_run.py --fit-*`:
  - `resample`: `lanczos` (default) | `nearest` | `kcentroid` — kCentroid (dominant-cluster) downscale keeps 1px outlines readable.
  - `align_x`: `bbox-center` (default) | `centroid` | `foot-centroid` — bbox-centering shifts the body whenever a pose's content width changes (per-frame horizontal jitter); `foot-centroid` anchors the leg axis so trailing hair/capes don't pull the body off the runtime flip pivot.
  - `align_y`: `center` (default) | `bottom` — shared foot baseline.
  - `pixel_perfect` mode (with `logical_height`, `palette_size`, `detail_bias`, `outline`): runs-based pixel-pitch detection → **strip-shared grid phase** snap downscale → row-uniform conform scale → **inter-frame registration** (upper-body alpha overlap; measured jitter <=1 logical px) → union-bbox crop (no more cell-bottom clipping) → run-wide shared median-cut palette (kills frame-to-frame color flicker) → alpha binarization → `enforce_outline` (uniform 1px silhouette outline; `detail_bias` keeps eyes/outlines that dominant voting erases) → **integer NEAREST upscale** with row-constant placement. No non-integer resampling anywhere.
- **Curation webview restyled to a clean light theme** (white base, neutral greys, single blue accent); dark mode removed.
- Lessons baked in from the field: don't rely on engine negative-rect flipping (Godot 4 renders negative dest rects displaced and negative src rects empty past the first cell — pre-mirror rows into the atlas instead), and never anchor locomotion frames on per-frame foot centroids (feet are the moving part; register on the stable upper body).

## v1.9.2 — Chroma extraction no longer eats subject colors

Extraction fixes for subjects whose colors share a channel with the chroma key (e.g. a red/orange body under magenta, or any subject with small green/teal features).

- **Despill no longer destroys colors far from the key.** `remove_chroma_background` ran its "neutralize key tint" pass on every pixel whose channels leaned toward the key's, with *no distance gate* — so a saturated red/orange/blue subject was clamped toward olive/grey under a magenta key even at color-distance 200+. The destructive pass is removed (it only ever fired on pixels the fringe stage had already decided to keep); near-key antialias fringe is still removed as before. `neutralize_key_tint` is dropped.
- **`--chroma-key auto` stops silently deleting small features.** Candidate scoring ranked by the 1st-percentile distance to subject pixels, which discards sub-1% features (eyes, gems, ear lamps): a key could look "safe" while its nearest subject pixel was still inside the erase radius. `auto` now prefers candidates that clear *every* subject pixel, records `min_subject_distance`, and warns on stderr when none do.
- Regression coverage in `tests/test_chroma_extraction.py`; the golden extraction manifest is unchanged.

## v1.9.1 — Docs sync & polish

Catch the docs and repo up to the v1.8–v1.9 curator (from an evaluator-grade consistency audit).

- Documented the `order` field and the `flipX` / shear transforms in the `curation.json` schema (`SKILL.md` + `curation.py`), and the two-row sequence/candidate-pool, grip reorder, flip, and preview transport in the README.
- Removed a stray `console.log` and a hardcoded `/tmp` path in the curation-view snippet.
- Backfilled changelog entries for v1.5–v1.7.0.

## v1.9.0 — Pool arrangement persistence + sweep hardening

Adds full arrangement persistence and the hardening from a second adversarial sweep (which also caught a regression from v1.8.1).

- **Candidate pool arrangement persists.** `curation.json` now records the full display `order` (sequence then pool) alongside `selected`, so reopening the curator restores exactly how you arranged *both* rows — not just the sequence. The bake is unchanged: compose/export key off `selected`; `order` is webview-only and documented in `curation.py` (the schema SSoT).
- **Robust against corrupt / hand-edited sidecars.** Frame indices in `selected` and `order` are coerced to integers and de-duplicated on load; `curation.py` now skips non-integer / out-of-range `selected` entries instead of crashing the bake.
- **Fixed a duplicate-render regression** (introduced in v1.8.1): missing/unextracted frames now render once (they already live in `order`) — removed the redundant second render loop that doubled them and could leak duplicate indices into the atlas.
- **Label escaping.** State names, actions, and imported frame labels are HTML-escaped before display, so an imported set's `meta.json` can't inject markup; over-long labels truncate instead of breaking the card.

## v1.8.2 — Preview UX polish

Remaining low-severity items from the v1.8.0 adversarial review.

- **Preview re-anchors on edits.** Reordering or moving frames no longer jumps the preview to a different frame — it keeps the on-screen frame in view (tracked by frame index), so a paused inspection stays put while you rearrange the sequence.
- **Transport disabled when the sequence is empty.** Play/step buttons grey out (and the position reads `0/0`) when no frames are in the sequence, instead of looking active but doing nothing.

## v1.8.1 — Cross-platform hardening

Fixes from an adversarial cross-platform / blast-radius review of the v1.8.0 curator.

- **FLIP animation reliable on Safari/Firefox.** The reorder and settle animations now force a layout reflow between applying the inverted transform and enabling the transition (instead of a bare `requestAnimationFrame`), so cards slide instead of teleporting on non-Chromium engines. `.missing` cards are excluded from the FLIP.
- **Missing frames preserved.** `commitZones` and `seedEntries` keep not-yet-extracted frame slots in `order`, so a reorder during incremental extraction can't silently drop them.
- **Multi-touch guard.** The reorder grip ignores secondary pointers (`ev.isPrimary`), so a second finger can't start a parallel drag on touch devices.

## v1.8.0 — Curator: drag reorder + candidate pool

The standalone curation webview (`serve_curation.py`) gets a full frame-curation pass: reorder the play sequence by hand, scrub the preview, and reconstruct a run from several generated takes by dragging the cuts you like.

![Curator drag reorder + candidate pool](docs/assets/curator-drag-update.gif)

- **Drag-to-reorder frames.** Grab the `⠿` grip on a frame card to change the play order. The grabbed card lifts and follows the cursor while the others slide aside (FLIP animation), and it eases into its slot on drop. The new order saves to `curation.json.selected` and is baked left-to-right by `compose_sprite_atlas.py` — no backend change, fully non-destructive.
- **Two-row curation: sequence + candidate pool.** Each state now renders a **sequence** row (the selected play order, on top) and a **candidate pool** row below it (unselected frames — e.g. a second or third generated take of the same row). Drag a cut from the pool up into the sequence to add it, or a sequence cut down to drop it; a card click sends it to the other row. This makes it easy to reconstruct one clean run loop from the best cuts across multiple takes.
- **Preview transport.** The live preview gains play/pause, frame-by-frame stepping (`⏮`/`⏭`, auto-pauses), and a 0.25×–4× speed control, plus a `cursor/total · #frame` readout. Display-only — these never touch `curation.json`, so paused inspection and stepping don't disturb the selection.
- Selection is now a flag with a separate display order, so toggling a frame no longer re-sorts the sequence. i18n (en/ko) throughout.

Curator UI: `scripts/curator/curator.js`, `scripts/curator/curator.css`.

## v1.7.0 — README rewrite + standalone curation view

- README rewritten for humans (problem hook, what-you-get, honest labels) with an EN/KO architecture diagram.
- `SKILL.md`: standalone curation-view triggers (큐레이션 / curation keywords) and a generic image-candidate path, so the webview serves any PNG set (icons, logos, drafts), not just sprites.
- Version SSoT unified across `SKILL.md` and `pyproject.toml`.

## v1.6 — Concurrency-safe pipeline

Hardening so the pipeline is safe to run from multiple agents at once (e.g. Claude Code and Codex side by side).

- **Run-dir single-writer lock** (`runio.py`): extract/compose/export/unpack take a `.sprite-gen.lock` per run dir — a second writer on the same character folder fails loudly with the holder's pid instead of interleaving output; a dead holder's lock is reclaimed automatically.
- **Atomic outputs**: frames, atlas, manifests, and reports write via temp file + `os.replace`, so a concurrent reader never sees a half-written file.
- Curator flip (↔) `ReferenceError` fix; path-traversal guard on all curator-server routes; auto-launch the curation webview as the closing workflow step.
- Japanese, Simplified Chinese, Spanish & French READMEs.

## v1.5 — Curation webview

- Standalone local **curation webview** (`serve_curation.py`): compare a state's frames side by side, select/reject, non-destructive per-frame move/scale/rotate/shear, saved to a `curation.json` sidecar; bilingual UI (`--lang en|ko`).
- `unpack_atlas_run.py` — rebuild frames from a finished sheet (`--grid` › `--manifest` › alpha auto-detect), or import a loose PNG set (`--pngs-dir`).
- `export_curated_pngs.py` — bake corrections back to named PNGs.
- Isometric ground-grid overlay (from `meta.json` tile/anchor) + shear handle for aligning furniture to the floor.

Releases before v1.5 (v0.1.0–v1.4) predate this changelog; see the [GitHub releases](https://github.com/aldegad/sprite-gen/releases).
