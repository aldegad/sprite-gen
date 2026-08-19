# 실행 인터프리터 — 근거와 레지스터별 세칙

SKILL.md 가 규칙을 지고, 이 문서가 왜 그런지와 형식별 세칙을 진다.

## 실행 인터프리터 — 전역 `python3` 는 이 스킬의 인터프리터가 아니다 (BLOCKING)

이 스킬의 모든 명령은 **레포 루트의 venv 인터프리터**로 실행한다:

```bash
export SPRITE_GEN_ROOT=/path/to/sprite-gen
$SPRITE_GEN_ROOT/.venv/bin/python <script.py> ...
```

- **부트스트랩은 README quickstart·CI 와 같은 한 줄이다** — `python3 -m venv .venv && .venv/bin/pip install -e .`.
  `.venv` 가 없으면 만든 뒤 실행한다. 다른 경로에 만들었다면 그 인터프리터의 절대경로로 바꿔 쓴다 —
  바뀌면 안 되는 것은 경로가 아니라 **"전역 `python3` 를 쓰지 않는다"** 는 규칙이다.
- **이유**: 의존(Pillow, NumPy)의 SSoT 는 `pyproject.toml` 이고, 그것을 실물로 만드는 곳은 이 venv 하나다.
  전역 `python3` 는 `$PATH` 가 그날 가리키는 아무 인터프리터이고(macOS 에서는 보통 homebrew CPython,
  PEP 668 `EXTERNALLY-MANAGED`), 거기 든 패키지는 손으로 넣은 것이라 선언과 실물이 갈린다. 실제로
  그렇게 갈렸다: homebrew python3 에는 Pillow 만 있고 NumPy 가 없어서, **한 개가 깔려 있다는 이유로
  다 깔린 것처럼 보이는** 상태였다.
- **폴백 금지**: "`.venv` 있으면 그거, 없으면 `python3`" 같은 해석은 두지 않는다 (원칙 6).
  없으면 만들거나 요란하게 실패한다 — 조용히 다른 인터프리터로 도는 경로는 없다.
- **NumPy 가 없는 인터프리터에서는 아무것도 시작하지 않는다**: 진입점은 패키지 import 시점에
  멈추고, 실행한 인터프리터 경로와 위 부트스트랩 명령을 그대로 찍는다. 추출 경로는 바이트 동일
  계약을 지고 있어서 **순수 파이썬 폴백은 없다** — 느리게라도 도는 두 번째 구현을 두면 같은 질문에
  답이 둘이 된다. (게이트 `sprite_gen/_deps.py`, 잠금 `tests/test_numpy_dependency_gate.py`)
- **자식 프로세스는 상속한다**: `heal_run` 과 큐레이션 서버는 자식을 `sys.executable` 로 띄운다.
  즉 부모를 옳은 인터프리터로 띄우면 그 아래는 자동으로 옳고, 반대로 큐레이션 서버를 전역 `python3` 로
  띄우면 그 서버가 부르는 재추출·compose 가 전부 같이 틀린다. 고칠 곳은 **띄우는 순간 한 곳**이다.
- **`sprite-gen <tool>` 은 실재하는 콘솔 스크립트다** (`anchor`, `cutout`, `curation`,
  `recolor`, `recolor-palette`, `migrate-breathe`, `migrate-request` …). `pip install` 이 venv 의 `bin/` 에 써 넣고 그 shebang 이 **바로 그 venv 의
  인터프리터**를 가리키므로, 이 형식은 인터프리터를 고르는 문제 자체가 없다:

  ```bash
  $SPRITE_GEN_ROOT/.venv/bin/sprite-gen <tool> ...
  ```

  - **여기서도 절대경로다** — 맨 `sprite-gen` 이 PATH 에 있는 건 venv 를 활성화했거나 그 환경에
    설치한 셸 안에서뿐이다. `SKILL.md`·`docs/*.md` 는 활성화 없는 셸에서 읽히므로 맨 `python3` 와
    같은 이유로 맨 `sprite-gen` 도 쓰지 않는다 (README quickstart 는 활성화가 앞에 있어 예외).
  - **이 변경 이전에 만든 `.venv` 에는 없다** — `[project.scripts]` 가 없던 시절 설치본이라
    `bin/sprite-gen` 이 안 만들어졌다. `pip install -e .` 를 한 번 다시 돌리면 생긴다.
  - `$SPRITE_GEN_ROOT/.venv/bin/python -m sprite_gen.cli <tool> ...` 는 같은
    `cli:main` 을 부르는 동치 형식이다 — 콘솔 스크립트가 아직 없는 venv 에서 쓴다.
- **레지스터는 파일로 갈린다**: 상대경로 `python3 scripts/...` 형식이 같은 인터프리터를 가리키는 건
  `source .venv/bin/activate` 가 **바로 앞에 적혀 있는** README quickstart 안에서만이다. `SKILL.md` 와
  `docs/*.md` 는 활성화 단계가 없는 셸에서 읽히므로, 절대경로든 상대경로든 **여기서는 상대형을 쓰지
  않는다** — 위 venv 절대경로 형식 하나만 쓴다. (`tests/test_entrypoint_interpreter.py` 가 이 두 파일군에
  대해 잠근다.)

