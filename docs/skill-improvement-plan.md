# sprite-gen — SKILL.md / 스킬 운영 개선 계획

> Status: DRAFT v0 (2026-05-28, howl)
> Scope: 본 OSS skill repo (`sprite-gen`, Apache-2.0) 의 `SKILL.md` (~500
> lines) 와 `scripts/` 운영의 개선 후보를 "좁은 직접 패치" 와 "큰 변경
> (별 plan 필요)" 로 분리해 추적한다.
> 본 문서는 결정문이 아니라 **다음 plan 들의 후보 명세**.
> SaaS / hosted / 사이트 관련 전략은 본 repo 의 책임이 아니라 별 repo
> `personal/spritegen-studio` 에서 다룬다. 본 문서는 skill 단독 진화만
> 다룬다.

---

## 0. 작업 정책

- **좁은 직접 패치 (이번 dispatch 안에서 가능)**: SKILL.md 한두 단락
  추가/명확화. 행동 변경 없음, 문서 명확성만.
- **중간 변경 (다음 plan 1 개)**: 새 script 추가, frontmatter 변경, 새
  reference 분리 — 영향 범위가 1~2개 file.
- **큰 변경 (별 plan / 별 PR)**: CLI 패키지화, provider 추상화, 테스트
  하니스, SaaS 연계 — repo 구조에 손이 감.

## 1. Quick wins (좁은 직접 패치 — 이번 PR 에 즉시 가능)

### 1.1 [P0] `SKILL.md` 끝에 "Related Docs" 포인터 추가
- 현재 SKILL.md 는 본 improvement plan 의 존재를 모름. 다음 사이클에서
  agent 가 이 skill 을 어떻게 진화시킬지 모름.
- **변경**: QA 섹션 다음에 짧은 "Related Docs" 추가, 본 plan 으로 링크.
- **이번 dispatch 에서 적용**: ✅ 적용됨.

### 1.2 [P1] Workflow step 0 의 base lock gate Korean copy 추가
- 현재 한국어 사용자는 README.ko 를 보지만, SKILL.md 의 BLOCKING gate
  설명은 영문만. 한국어 agent / 사용자에게 동일 무게로 전달되지 않음.
- **변경**: Stage 0 gate 영문 5줄 옆에 한국어 한 줄 요약.
- **범위**: 좁음. 이번 dispatch 에서 적용 가능하나, 톤/표현 검토가 더 필요해
  다음 dispatch 로 미룸.

### 1.3 [P1] `extract_sprite_row_frames.py` 의 `--allow-slot-fallback` 경고 강화
- 현재 SKILL.md: "explicit debugging only" 만 표시. 실제 출력 행동은 미명시.
- **변경**: 스크립트 stderr 에 `WARNING: --allow-slot-fallback active —
  this is not a sprite-gen pass` 같은 명시 출력 강제. SKILL.md 의 텍스트와
  실제 코드 행동 일치.
- **범위**: 1 file 1 print. 작지만 코드 변경 — 다음 dispatch 로 분리.

## 2. 중간 변경 (별 plan 1 개씩, ~1 dispatch 단위)

### 2.1 [P0] SKILL.md "What this skill is NOT" 섹션 신설
- 현재: simple MVP scope / advanced workflow / experimental 의 boundary 가
  여러 곳에 흩어져 있어 agent 가 종종 "humanoid run 한 번에 8 frames" 같은
  걸 default 로 시도.
- 제안: `## Out-of-Scope (Read First)` 를 frontmatter 직후에 추가:
  - 게임-ready humanoid locomotion 자동 보장 X
  - one-shot master sheet / fixed-grid cutting / 로컬 드로잉 fallback X
  - 사용자 base 이미지 없이 "프롬프트만으로" 캐릭터 생성 X
  - chroma-key 가 캐릭터 색에 인접한 경우 자동 보호 X (선택 게이트 필수)
  - 9+ frame default 일반화 X
- 효과: agent 가 진입 단계에서 결정 부담을 덜고, 잘못된 시도를 줄임.

### 2.2 [P1] `prepare_sprite_run.py` 의 `--describe-only` dry-run 모드
- 현재: prepare 가 파일을 즉시 씀. agent 가 "이 request 가 어떻게 풀릴지"
  미리 보고 싶을 때 직접 코드를 읽어야 함.
- 제안: `--describe-only` 옵션 — `sprite-request.json` 만 stdout 에 print
  하고 file write 안 함. agent 가 dry-run 으로 cell/state/prompt 검증.
- 효과: SaaS API 의 `POST /v1/runs?dry-run=true` 와 1:1 대응 — 향후 hosted
  에 그대로 mapping 됨.

### 2.3 [P1] `qa-notes.md` 의 schema 화
- 현재: 자유 markdown. 사람이 적기 좋지만 agent 가 "어느 state 가 pass /
  experimental 인지" 정형 파싱 못함. dashboard 화도 불가.
- 제안: `qa-notes.json` 추가 (markdown 와 병행). state 별 `verdict:
  pass | best-effort | experimental | fail`, `motion_qa: pass | fail`,
  `notes: string`.
- 효과: SaaS 의 자동 대시보드, motion QA 자동화 (vision LLM second
  opinion) 의 입력으로 바로 쓰임.

### 2.4 [P2] curator webview 의 "lock anchor" 액션
- 현재: idle anchor 결정이 사람의 외부 판단. webview 가 그 결정을 모름.
- 제안: 큐레이터에 "이 프레임을 idle anchor 로 lock" 버튼 추가 →
  `curation.json.anchors[<direction>] = frame_path`. `prepare_sprite_run.py
  --from-anchor` 가 그 anchor 를 다음 row 의 base 로 자동 채택.
- 효과: SKILL.md 의 Idle Anchor Architecture 가 GUI 까지 자연스럽게
  closure. agent 가 더 빨리 advanced workflow 진입.

## 3. 큰 변경 (별 plan, 큰 PR — 향후 4~12 주)

### 3.1 [P0] CLI 패키지화 — `sprite-gen` 단일 entrypoint
- 현재: 12개 `python3 scripts/X.py --...` 명령. agent / 사용자 모두 path
  관리 부담. `$ALEX_EXTENSIONS_DIR` env 의존이 OSS 배포에 부적합.
- 제안: `pyproject.toml` + `console_scripts` entrypoint:
  ```
  sprite-gen prepare ...
  sprite-gen extract ...
  sprite-gen curate ...
  sprite-gen compose ...
  sprite-gen unpack ...
  sprite-gen export ...
  sprite-gen quickstart ...   # 새 wrapper: prepare → image-gen → extract → compose 한 번
  sprite-gen login            # 호스팅 funnel
  ```
- 영향 범위: 모든 scripts 의 `if __name__ == "__main__"` → entrypoint
  함수. SKILL.md 의 모든 명령 예시 갱신. README 갱신. install path 갱신.
- **별 plan 필수**.

### 3.2 [P0] Provider 추상화 (`providers/` plug)
- 현재: `kuma:image-gen` skill 에 hard-wire (Codex `image_gen` only).
- 제안: `providers/` 디렉터리에 `codex_image_gen.py`, `openai_api.py`,
  `anthropic_api.py`, `replicate.py` 인터페이스 통일:
  ```python
  class Provider(Protocol):
      def generate_row(self, prompt: str, refs: list[Path], out_path: Path) -> ProviderResult: ...
  ```
- agent skill 은 `--provider` flag 또는 `SPRITE_GEN_PROVIDER` env 로 선택.
- **별 plan 필수.** SaaS 전략의 BYO 모델과 정확히 연결.

### 3.3 [P1] Golden run regression suite
- 현재: 테스트 없음. 변경 시 회귀 발견이 사람의 눈.
- 제안: `tests/golden/` 에 base 이미지 + sprite-request + 기대 manifest
  hash. CI 에서 `pytest tests/golden` 으로 결정론 단계 (extract/compose/
  unpack/export) 회귀 자동 감지. 이미지 생성 단계는 fixture stub.
- **별 plan**.

### 3.4 [—] hosted/cloud 변형 skill
**본 repo 책임 아님**. 별 repo `personal/spritegen-studio` 가 hosted /
cloud surface 를 다룬다. 거기서 cloud 변형 skill (또는 본 skill 의 cloud
mode adapter) 가 필요하다고 결정되면 spritegen-studio 의 plan 으로 가야 함.
본 plan 에는 *언급만* 남기고 추적 안 함.

### 3.5 [P2] 다국어 SKILL.md (현재 영어만)
- 현재: README 는 en/ko 분기, SKILL.md 는 영어 + 부분 한글 (Advanced
  Workflows 단락만 한글). 한국어 agent / 작업자가 SKILL.md 정독 시 비효율.
- 제안: `SKILL.md` (en, canonical) + `SKILL.ko.md` (mirror). frontmatter
  의 `description` 은 영어 유지 (agent 라우팅용), 본문만 분기.
- 영향: 유지보수 비용 ↑. drift 방지 hook 필요. *문서 inflation 가능성 주의*.

### 3.6 [P2] `unpack_atlas_run.py` 자동검출의 confidence 노출
- 현재: SKILL.md 는 auto-detect 가 캐릭터 내부 transparency 를 "survive"
  한다고 명시. 실제 동작은 그렇게 알려져 있음.
- 제안: 그래도 자동검출 결과의 confidence 를 stderr / `unpack-source.json`
  에 명시적으로 노출 (예: row 검출의 gap 크기, 클러스터 면적 분산). 사용자가
  의심스러울 때 즉시 `--manifest` / `--grid` 로 전환 결정 가능.
- 영향: 1 script + 로깅 추가.
- *확인 필요*: 실제로 자동검출이 어느 패턴에서 빗나가는지 reproducible
  케이스를 모은 뒤에 우선순위 재평가.

## 4. 거절한 후보 (의식적 No)

- ❌ **runtime atlas 를 JSON 외 yaml/toml 로도 export**: SSoT 분기. manifest
  하나만 truth.
- ❌ **자동 motion repair (frame interpolation)**: SKILL.md 가 명시적으로
  금지하는 행위 (No Silent Fallback). "regenerate the row" 가 정답.
- ❌ **GUI desktop 앱 (Electron)**: skill 책임 아님 (hosted/Electron 은
  spritegen-studio 영역). webview 가 cross-platform 으로 충분.
- ❌ **base 이미지 자동 background removal**: chroma key 가 명시적 input.
  자동화하면 "왜 내 색이 사라졌는지" 디버깅 불가.

## 5. 이번 dispatch 에서 적용된 직접 패치 목록

- ✅ `SKILL.md` 끝에 "Related Docs" 짧은 섹션 추가 (1.1)
- ✅ 이 문서 (`docs/skill-improvement-plan.md`) 신설
- ⚠️ SaaS 전략 doc (`saas-open-source-strategy.md`) 은 처음 본 repo 의
  `docs/` 에 함께 만들었다가 **별 repo `personal/spritegen-studio` 로
  이동·삭제**. 본 repo 는 skill 단독 책임만 진다.

직접 패치는 **최소한**으로 고의 제한. 나머지는 별 plan 으로 분리해
Alex / 쿠마가 우선순위 잡고 dispatch 분할 결정.

## 6. 우선순위 추천 (다음 dispatch 후보)

가장 ROI 큰 순서 (skill 단독 책임 안에서):
1. **2.1** "What this skill is NOT" 섹션 — 30 분 작업, agent 혼동 큰 폭 감소.
2. **3.1** CLI 패키지화 — 1~2 일 작업, OSS 배포 + skill 호출 표준화.
3. **2.2** `--describe-only` dry-run — 반 일 작업, agent 의 안전한 검증 경로.
4. **3.2** Provider 추상화 — 2~3 일 작업, BYO 전략의 전제. spritegen-studio
   가 본 skill 을 host 할 때도 그대로 재사용.
5. **3.3** Golden suite — 1 일 작업, 위의 변경들이 안전하게 들어가기 위한 전제.
