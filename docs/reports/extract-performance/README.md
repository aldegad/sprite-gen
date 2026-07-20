# 추출 엔진 성능 — 프로파일 & 결정론 보존 최적화

plan `sprite-gen/extract-performance` 산출물. founder_v7 실런 1개 상태(up_idle,
4 프레임, 1280×720 raw)를 재추출하며 프로파일 → 결정론(바이트 동일 출력) 보존
최적화 → 전후 실측.

- 대상 상태: `docs/reports/perfectpixel-b-loop-e2e-founder-v7/candidate-2` (up_idle)
- 하드웨어: M4 Max, Python 3.13.9, Pillow 12
- 재현 하네스: `isolated_bench.py` (editable 설치가 main 을 가리켜 워크트리 코드를
  덮는 것을 막기 위해 setuptools editable finder 를 meta_path 에서 제거하고
  대상 루트를 sys.path 최상단에 둔다 — `bench` / `golden` / `wall` 모드)

```
python docs/reports/extract-performance/isolated_bench.py <repo-root> bench   <tmp> 9
python docs/reports/extract-performance/isolated_bench.py <repo-root> golden  <tmp>
python docs/reports/extract-performance/isolated_bench.py <repo-root> wall    <tmp>
```

## 결과 요약

| 지표 | before | after | 배수 |
|---|---|---|---|
| 상태당 추출 (min, 12 runs interleaved) | 4.79s | 2.26s | **2.12×** |
| 상태당 추출 (trimmed-mean) | 4.83s | 2.29s | **2.11×** |

출력은 **바이트 동일**: 프레임 PNG 12개 SHA-256 전부 일치 + 추출 경고 문자열
(피치 불일치 uniformity 점수 `.1f` 포함) 일치. 골든은 `isolated_bench.py golden`
로 대조.

## 프로파일 방법론과 함정

`cProfile` 은 **호출 많은 함수를 과대평가**한다 (제너레이터/`sum` 다발이 실제보다
크게 잡힘). 그래서 cProfile 로 상위 후보를 잡되, **핵심 함수를 시계벽시간으로
래핑한 실측**(`wall` 모드)으로 재확정했다.

### cProfile 상위 (self, 11.7s 프로파일 런) — 후보 발굴용

```
2.406s builtins.sum          (6.0M calls)   ← 제너레이터 축약
1.352s extract.py:1144 genexpr (16M)
1.065s _grid_uniformity      (66 calls)
0.721s opaque (cleanup)      (7.4M)
0.672s _cleanup_alpha_ycc
0.464s _flood_clear_background_ycc
```

### 실측 시계벽시간 (before, 4.8s 런) — 최적화 대상 확정

| 함수 | 시간 | 점유 | 성격 |
|---|---|---|---|
| `remove_chroma_background_ycbcr` | 1.87s | 38% | 매 상태·전 픽셀 (1280×720) |
| ├ `_cleanup_alpha_ycc` | 0.85s | 17% | 정수 8이웃 스텐실 |
| ├ `_matte_ycc` (flood 포함) | 0.84s | 17% | float elementwise + BFS |
| └ `_flood_clear_background_ycc` | 0.52s | 11% | 경계 flood BFS |
| `_best_phase` / `_grid_uniformity` | 1.79s | 37% | 8×8 위상 스캔 (피치 불일치 상태만) |
| `_dominant_block_color` | 0.52s | 11% | 블록 k-means (snap 다운스케일) |

> 플랜이 추정한 `_edge_histograms`·`detect_pixel_grid` 는 실측 5~9% 로 상위가
> 아니었다 (프로파일 먼저의 근거).

## 결정론 계약 — float 축약은 CPython `sum()` 에 고정

**핵심 발견**: CPython 3.12+ 의 `sum()` 은 float 에 대해 **보정합(Neumaier)** 을
쓴다. 순진한 `acc += x` 좌결합 합과 마지막 ULP 가 갈린다 — 음수 아닌 3항조차 약
9%, 긴 합은 약 73% 에서 값이 다르다 (실측). 따라서 **바이트 동일을 지키려면 모든
float 축약은 원본 `sum()` 제너레이터 표현을 글자 그대로 유지**해야 한다.

이 계약이 numpy 벡터화를 배제한다: (1) `np.hypot ≠ math.hypot` 로 매트 경계 픽셀이
1 ULP 뒤집혀 알파가 갈리고, (2) numpy reduction 은 pairwise 라 Neumaier 와도
다르다. 그래서 numpy 는 **도입하지 않았다** (플랜의 "PIL 우선" + 신규 의존성 게이트
준수). 정수 연산(스텐실·k-means)만 순서 무관이라 자유롭게 재작성 가능하다.

이 계약은 `tests/test_extract_perf_equivalence.py` 가 순진한 참조 구현과 대조해
구조적으로 고정한다 (골든 런이 우연히 안 밟는 위상까지 커버 — 실제로 이 테스트가
grid dev 합의 1 ULP 회귀를 잡아냈다).

## 최적화 (전부 바이트 동일)

1. **`_best_phase` 위상 메모이즈 + flat 로드 hoist** — `_grid_edges` 의 정수 스냅
   때문에 8×8=64 위상이 소수의 고유 정수 경계로 붕괴한다. `(xs, ys)` 로 채점을
   메모이즈하고 프레임 픽셀을 한 번만 실어 재로딩을 없앤다. **1.79s → 0.62s.**
   (float dev 합은 `sum()` 그대로 유지 — 계약.)
2. **`_cleanup_alpha_ycc` → PIL 네이티브 스텐실** — 8이웃 불투명 카운트를 zero-pad
   시프트-합(`ImageChops.add`)으로, clear/fill 마스크를 단일 LUT 로. 정수 논리라
   바이트 동일. **0.85s → 0.007s.**
3. **`_matte_ycc` 색-값 메모이즈** — 매트 출력은 픽셀 `(r,g,b)` 의 순수 함수(키
   고정)라 색값으로 캐시. `getdata`/`putdata` 로 PixelAccess 왕복 제거. 산술 불변.
4. **`_flood_clear_background_ycc` — 방문=클리어 배치화 + 색 판정 메모이즈** —
   클리어 대상이 방문 집합과 정확히 같으므로 BFS 는 방문 집합만 확정하고 마지막에
   PIL 마스크로 알파를 한 번에 0 으로. ~90만 PixelAccess 왕복 제거. **매트+flood
   0.84s → 0.55s.**
5. **`_dominant_block_color` 명시 정수 누적** — 제곱거리·정수합//count 는 순서
   무관이라 제너레이터/`sum` 오버헤드만 제거. **0.52s → 0.25s.**

## 남은 바닥 (바이트 동일 한계)

`_matte_ycc` 의 elementwise float(rgb→ycc·hypot·smoothstep)과 grid 의 보정합
축약은 pure-Python 바닥이다. 더 줄이려면 알고리즘 의미 변경(범위 밖) 또는 numpy
(계약 위반) 뿐이라 여기서 멈췄다. 목표 ≥3× 는 결정론 절대 보존과 상충 — 계약을
지키는 최댓값이 **2.1×**.
