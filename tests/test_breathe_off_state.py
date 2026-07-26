# SPDX-License-Identifier: Apache-2.0
"""호흡을 **끈** 상태에서 큐레이터가 워프를 그리지 않는지 (소스 계약).

봉투에서 위상 0 은 항등이 아니다 — 진행파 지연 때문에 `t=0` 도 변형된 프레임이다.
그래서 "꺼짐"을 "위상 0" 으로 표현하면 안 된다: 굽기는 off 면 `state_breathe` 가 None
이라 원본을 굽는데, 프리뷰는 워프된 정지화면을 보여준다 (실측 348~800B 차이,
새미 검증 2026-07-25).

`breatheComposite` 호출부는 전부 "호흡이 켜져 있는가"로 게이트돼야 한다. 소스 수준
계약 테스트인 이유: 이 경로는 캔버스 렌더 루프 안이라 단위 실행이 어렵고, 되살아나는
방식이 항상 "가드를 빼먹는" 형태라 호출부 형태를 고정하는 게 정확히 맞는 그물이다.
(`tests/test_curator_pixel_scaling_ssot.py` 가 같은 방식의 선례다.)
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CURATOR_SRC = ROOT / "scripts" / "curator" / "src"
CALL = re.compile(r"breatheComposite\s*\(|breatheComposeForPreview\s*\(")
# 파이썬 굽기 소비자 — 위상 0 을 건너뛰면 그 슬롯만 원본이 구워진다.
PY_CONSUMERS = ("sprite_gen/compose_atlas.py", "sprite_gen/compose_gif.py")
PY_CALL = re.compile(r"phase_frame\s*\(|bake_breathe_sequence\s*\(")

# 게이트는 **호출부 같은 줄**에 있어야 한다. 감싸는 블록까지 인정하면 그물이 무의미해진다
# — 실측: 25줄 창으로 넓혔더니 무관한 `bm.enabled ?`(위상 계산 줄)가 걸려서, 정작 워프
# 호출의 가드를 빼도 통과했다. 그래서 계약을 "호출부에서 삼항으로" 로 통일했다.
# (`stateBreathe()` 가 꺼진 줄에 null 을 주므로 `bcfg ?` 도 유효한 켜짐 게이트다.)
GUARDS = ("bm.enabled ?", "bcfg ?")


def _strip_comments(src: str) -> str:
    """주석을 공백으로 (줄 수·오프셋 보존). 주석 속 `/api/compare-gif` 언급이 파일 생성
    연산으로 잡히면 그물이 엉뚱한 자리를 지목한다."""
    out, i, n = [], 0, len(src)
    while i < n:
        two = src[i:i + 2]
        if two == "//":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif two == "/*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(c if c == "\n" else " " for c in src[i:j])); i = j
        else:
            out.append(src[i]); i += 1
    return "".join(out)


# ── 문장 리더 — 그물 넷이 **같은 것**을 읽는다 ──────────────────────
#
# 앞선 판은 스캐너 넷이 전부 **줄 단위**로 읽었다. 그래서 잎 판정을 아무리 좁혀도 `\n`
# 하나가 그 좁힘 전체를 우회했다 (노을이 탈출 J/J2/K 2026-07-26):
#
#     const canonical = (canonImg && canonImg.complete)     // <- 여기까지만 읽고 참
#       ? canonImg
#       : image;                                            // <- 폴백이 안 보인다
#
# prettier 식 줄바꿈만 입히면 되므로 적대적 트릭이 필요 없다. 문장(깊이 0 의 `;` 까지)을
# 단위로 읽으면 이 축이 통째로 사라진다.
def _neutralize_literals(src: str, strip_comments: bool = False) -> str:
    """문자열·템플릿·정규식 리터럴의 **내용만** `_` 로 덮는다 (구조·길이·줄수 보존).

    `strip_comments=True` 면 주석도 **같은 패스**에서 지운다. 두 패스로 나누면 서로를
    오인한다 — 주석 제거를 먼저 하면 정규식 `/^\\/run\\//` 안의 `//` 를 줄주석으로 읽어
    그 줄 뒤를 통째로 날리고, 리터럴을 먼저 하면 주석 안의 따옴표에 걸린다.

    내용을 남기면 템플릿 리터럴의 `${...}` 중괄호가 문장을 끊고, 정규식의 `/`·`{` 도
    같은 짓을 한다 — 실제로 `cards.js` 의 `` `#${frame.index}` `` 한 줄이 문장 리더를
    무너뜨려 그 파일의 사이트가 통째로 어긋났다. 통째로 공백으로 지우면 `""`(빈 값 잎)
    같은 의미 있는 리터럴이 사라지므로, 따옴표는 남기고 안만 덮는다."""
    out, i, n, prev = [], 0, len(src), ""
    while i < n:
        ch = src[i]
        if strip_comments and src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
            continue
        if strip_comments and src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(c if c == "\n" else " " for c in src[i:j]))
            i = j
            continue
        if ch in "'\"":
            j = i + 1
            while j < n and src[j] != ch:
                j += 2 if src[j] == "\\" else 1
            out.append(ch + "".join("\n" if c == "\n" else "_" for c in src[i + 1:j]) + ch)
            i = j + 1
            prev = "x"
            continue
        if ch == "`":
            j, tdepth = i + 1, 0
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "$" and j + 1 < n and src[j + 1] == "{":
                    tdepth += 1
                    j += 2
                    continue
                if src[j] == "}" and tdepth:
                    tdepth -= 1
                    j += 1
                    continue
                if src[j] == "`" and not tdepth:
                    break
                j += 1
            out.append("`" + "".join("\n" if c == "\n" else "_" for c in src[i + 1:j]) + "`")
            i = j + 1
            prev = "x"
            continue
        if ch == "/" and (prev == "" or prev in "(,=:[!&|?{;+-*%~^<>"):
            j = i + 1
            while j < n and src[j] not in "/\n":
                j += 2 if src[j] == "\\" else 1
            if j < n and src[j] == "/":
                out.append("/" + "_" * (j - i - 1) + "/")
                i = j + 1
                prev = "x"
                continue
        out.append(ch)
        if not ch.isspace():
            prev = ch
        i += 1
    return "".join(out)


def _statements(name: str):
    """[(시작줄, 문장 텍스트)] — 주석·리터럴 중립화 후 깊이 0 의 `;`/`{`/`}` 로 끊는다."""
    src = _neutralize_literals((CURATOR_SRC / name).read_text(encoding="utf-8"),
                               strip_comments=True)
    out, buf, start, line = [], "", 1, 1
    stack, prev = [], ""          # stack: 열린 괄호 종류 ("(" "[" "{obj")
    for ch in src:
        if ch == "\n":
            line += 1
            buf += " "
            continue
        if ch in "([":
            stack.append(ch)
        elif ch in ")]":
            if stack:
                stack.pop()
        elif ch == "{":
            # **블록이냐 리터럴이냐** — 구조분해/객체 리터럴의 `{}` 를 문장 구분자로 쓰면
            # `const { url: refUrl } = ...` 같은 선언이 토막난다(실제로 났다). 앞의 유의미
            # 문자로 가른다: `=`·`(`·`,`·`:`·`?`·`return` 뒤면 값이고, 그 외는 블록이다.
            # `const {` / `let {` / `var {` / `return {` 뒤도 값(구조분해·객체)이다 —
            # 선언 키워드 뒤를 블록으로 읽으면 `const { url: refUrl } = ...` 가 토막나서
            # 그 이름의 바인딩을 영영 못 찾고 "증명 불가 = 거짓" 으로 정상 코드를 오진한다.
            tail = buf.rstrip().rsplit(" ", 1)[-1] if buf.strip() else ""
            if prev in "=(,:?[&|" or tail in ("const", "let", "var", "return"):
                stack.append("{obj")
            elif not stack:
                text = buf.strip()
                if text:
                    out.append((start, text))
                buf, start = "", line
                prev = ch
                continue
            else:
                stack.append("{blk")
        elif ch == "}":
            kind = stack.pop() if stack else None
            if kind is None:
                text = buf.strip()
                if text:
                    out.append((start, text))
                buf, start = "", line
                prev = ch
                continue
        if not stack and ch == ";":
            text = buf.strip()
            if text:
                out.append((start, text))
            buf, start = "", line
            prev = ch
            continue
        if not buf.strip():
            start = line
        buf += ch
        if not ch.isspace():
            prev = ch
    if buf.strip():
        out.append((start, buf.strip()))
    return out


def _js_files():
    return [p.name for p in sorted(CURATOR_SRC.glob("*.js")) if p.name != "breathe.js"]


def _inventory(sites):
    got = {}
    for name, *_ in sites:
        got[name] = got.get(name, 0) + 1
    return got


def _assert_inventory(kind, got, expected):
    """사이트 인벤토리는 **파일당 개수**까지 못박는다.

    존재만 보면 사이트가 여럿인 파일의 상실을 못 보고, 전역 "비어있지 않다" 만 보면
    파일 하나가 통째로 사라져도 통과한다. 둘 다 이 플랜에서 실제로 뚫린 형태다."""
    assert got == expected, (
        f"{kind} 인벤토리가 달라졌다.\n  기대: {expected}\n  실제: {got}\n"
        "  줄었으면 사이트가 조용히 증발한 것이고(줄바꿈·이름 변경), 늘었으면 새 자리가"
        " 계약에 들어온 것이다 — 어느 쪽이든 표를 같이 고쳐라.")


EXPECTED_CALL_SITES = {"cards.js": 1, "compare.js": 1, "row-export.js": 1, "zoom-editor.js": 2}


def _call_sites():
    for name in _js_files():              # breathe.js 는 정의부 — 호출부가 아니다
        for lineno, text in _statements(name):
            if CALL.search(text):
                yield name, lineno, text


def test_there_are_call_sites_to_guard():
    """호출부 **인벤토리**가 그대로여야 한다.

    "비어있지 않다" 만 보면 한 자리가 줄바꿈 한 번으로 증발해도 통과한다 — 신호는
    "테스트가 하나 줄었다" 뿐이고, 그건 이 플랜이 두 번 1급 계약으로 올린 실패 모드다."""
    _assert_inventory("breatheComposite 호출부", _inventory(_call_sites()), EXPECTED_CALL_SITES)


@pytest.mark.parametrize("site", list(_call_sites()), ids=lambda s: f"{s[0]}:{s[1]}")
def test_every_breathe_composite_call_is_gated_on_enabled(site):
    name, lineno, line = site
    assert any(guard in line for guard in GUARDS), (
        f"{name}:{lineno} 가 켜짐 여부로 게이트되지 않았다 — 꺼진 줄이 워프된다.\n"
        f"  {line}\n"
        f"  허용 형태: {', '.join(GUARDS)}")


# ── 파이썬 굽기 소비자 ──────────────────────────────────────────────

def _py_call_sites():
    for rel in PY_CONSUMERS:
        path = ROOT / rel
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, 1):
            if PY_CALL.search(line) and not line.lstrip().startswith("#"):
                # 이 호출을 감싸는 가장 가까운 `if` 조건 (들여쓰기가 더 얕은 첫 if)
                indent = len(line) - len(line.lstrip())
                guard = ""
                for prev in range(lineno - 2, -1, -1):
                    cand = lines[prev]
                    if not cand.strip():
                        continue
                    cind = len(cand) - len(cand.lstrip())
                    if cind < indent and cand.lstrip().startswith("if "):
                        guard = cand.strip()
                        break
                    if cind < indent:
                        break
                yield rel, lineno, line.strip(), guard


def test_there_are_python_bake_call_sites():
    assert list(_py_call_sites()), "굽기 소비자에서 호흡 호출부가 사라졌다 — 계약 위치 확인"


@pytest.mark.parametrize("site", list(_py_call_sites()), ids=lambda s: f"{s[0].split('/')[-1]}:{s[1]}")
def test_python_bake_never_skips_a_phase(site):
    """굽기가 **위상 값으로** 워프 여부를 가르면 안 된다.

    `if breathe_cfg and breathe_phase:` 는 위상 0 슬롯을 건너뛰어 그 칸만 원본이 되고,
    같은 런의 GIF 굽기와 그림이 갈린다 (round-1 reject 1, 실측 353px). 게이트는
    "호흡이 켜져 있는가"(`breathe_cfg`)만 봐야 한다.

    이 그물이 필요한 이유: 부리가 변이 테스트로 확인했다 — `compose_atlas` 의 가드를
    옛 형태로 되돌려도 `pytest -k "atlas or breathe"` 40개가 **전부 통과**했다.
    JS 쪽엔 그물이 있었고 파이썬 쪽만 비어 있었다 (2026-07-25)."""
    rel, lineno, line, guard = site
    assert "phase" not in guard.replace("breathe_cfg", ""), (
        f"{rel}:{lineno} 의 가드가 위상 값을 본다 — 위상 0 이 안 구워진다.\n"
        f"  가드: {guard}\n  호출: {line}")


# ── 줄 단위 신선도 검사가 실제로 걸리는가 ──────────────────────────

PREVIEW_CALL = re.compile(r"breatheComposeForPreview\s*\(([^;]*)\)")
FRESH = re.compile(r"breatheAssertFresh\s*\(")


EXPECTED_PREVIEW_SITES = {"cards.js": 1, "compare.js": 1, "zoom-editor.js": 2}


def _preview_call_sites():
    for name in _js_files():
        for lineno, text in _statements(name):
            m = PREVIEW_CALL.search(text)
            if m:
                yield name, lineno, text, m.group(1)


def test_there_are_preview_call_sites():
    _assert_inventory("프리뷰 호출부",
                      _inventory(_preview_call_sites()), EXPECTED_PREVIEW_SITES)


@pytest.mark.parametrize("site", list(_preview_call_sites()), ids=lambda s: f"{s[0]}:{s[1]}")
def test_every_preview_call_passes_a_freshness_reference(site):
    """프리뷰 호출부는 **전부** 기준 프레임을 넘겨야 한다.

    round-7 은 `reference` 를 선택 인자로 뒀고 5곳 중 2곳만 넘겼다. 나머지 3곳은
    `if (reference)` 를 통째로 건너뛰어, 같은 웹뷰의 두 화면이 정반대로 행동했다 —
    줄 카드는 거부하는데 호흡 편집 모달은 낡은 숫자로 조용히 그렸다 (슉슉이 실측
    2026-07-26: 12/12 위상 갈림, 알림 0건). round-2 가 켜짐 게이트를 호출부 전수
    파라미터화로 고정한 것과 같은 형태의 그물이다."""
    name, lineno, line, args = site
    # `breatheComposeForPreview(a, b, c, ref)` — 인자 4개여야 한다
    depth = 0
    parts, cur = [], ""
    for ch in args:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip()); cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    assert len(parts) >= 4 and parts[3], (
        f"{name}:{lineno} 가 기준 프레임을 안 넘긴다 — 신선도 검사가 건너뛰어진다.\n  {line}")


# ── 파일을 만드는 자리는 전부 신선도를 본다 ────────────────────────

# 사용자 손에 **파일**이 떨어지는 연산. 프리뷰 래퍼는 거부를 잡아 원본을 돌려주므로
# (화면에선 옳다) 이 자리에 신선도 검사가 없으면 호흡이 빠진 파일이 조용히 나간다.
# 파일을 만드는 연산. **두 축**이다:
#  - 캔버스 축(`toDataURL`/`toBlob`) — 코드에 그대로 있다.
#  - 엔드포인트 축(`/api/...`) — **문자열 리터럴 안에만** 있다.
# 리터럴 중립화한 소스에서 후자를 찾으면 영원히 0건이다(라운드 9 가 중립화를 통일하면서
# 그 축을 조용히 죽였다 — 현행 두 경로가 우연히 `toDataURL` 도 써서 초록이었을 뿐이다).
# 그래서 엔드포인트 축은 **원문**에서 찾는다 (노을이 M2 실측 2026-07-26).
FILE_PRODUCERS = re.compile(r"toDataURL\s*\(|toBlob\s*\(")
FILE_PRODUCER_URLS = re.compile(r"/api/compare-gif|/api/row-video")
FRESH = re.compile(r"breatheAssertFresh\s*\(")

# 호흡 계약이 사는 파일 안에서 파일을 만드는 **모든** 함수가 대상이다.
# 손으로 적은 파일 화이트리스트(`FRESH_CONSUMERS = ("row-export.js",)`)로 쓰면 안 된다:
# 그 판일 때 `compare.js` 의 영상 다운로드가 호흡을 조용히 뺀 파일을 만들고 있었는데
# 그물은 아무 말도 안 했다 (슉슉이 실측 2026-07-26). 대상은 이름이 아니라 **연산**이다.
# 파일을 만들지만 **호흡 경로가 아닌** 자리 — 면제는 파일 단위로, 이유와 함께.
NON_BREATHE_PRODUCERS = {
    "base-editor.js": "베이스 이미지 편집 업로드 — 호흡 레이어를 안 거친다 (/api/base-edit)",
}


HEADER = re.compile(r"(function\s*\w*\s*\([^)]*\)|\([^()]*\)\s*=>|\w+\s*=>)\s*$")


def _enclosing_function(src: str, pos: int) -> tuple[str, int] | None:
    """`pos` 를 감싸는 **가장 안쪽 함수 본문**과 그 시작줄.

    줄 기준 휴리스틱으로 자르면 `const gcd = (a, b) => ...` 같은 한 줄 화살표 함수가
    다음 함수 시작으로 잡혀 뒤따르는 본문을 통째로 삼킨다 — 그러면 검사가 있는데도
    없다고 보고한다. 중괄호를 세어 진짜 블록을 찾는다."""
    stack = []
    for i, ch in enumerate(src[:pos]):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            stack.pop()
    for open_at in reversed(stack):
        if not HEADER.search(src[max(0, open_at - 200):open_at]):
            continue
        depth = 0
        for j in range(open_at, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    return src[open_at:j + 1], src[:open_at].count("\n") + 1
        return src[open_at:], src[:open_at].count("\n") + 1
    return None


def _file_producing_functions():
    """(파일, 시작줄, 본문) — 파일을 만드는 연산을 감싼 함수 전수 (중복 제거).

    **큐레이터 전 파일**을 훑는다. `BREATHE_FILES` 로 한정하면 내보내기 플러밍을 공용
    헬퍼로 빼는 흔한 DRY 리팩토링만으로 그 함수가 수집에서 통째로 증발한다 — 그리고
    같은 커밋에서 신선도 게이트를 지워도 540 passed, 기준선과 개수까지 동일이었다
    (노을이 탈출 L 실측 2026-07-26). 이 그물이 잡으려고 존재하는 **역사적 버그 그 자체**가
    원문 그대로 되살아나도 신호가 없었다.

    리터럴·주석은 문장 리더와 **같은 중립화**를 쓴다 — 두 리더가 다른 것을 보면 한쪽에만
    보이는 자리가 생긴다."""
    out, seen = [], set()
    for name in (p.name for p in sorted(CURATOR_SRC.glob("*.js"))):
        if name in NON_BREATHE_PRODUCERS:
            continue
        raw = (CURATOR_SRC / name).read_text(encoding="utf-8")
        src = _neutralize_literals(raw, strip_comments=True)
        stripped = _strip_comments(raw)
        # 캔버스 축은 중립화 소스에서, 엔드포인트 축은 **원문**(주석만 제거)에서 찾는다.
        spots = {m.start() for m in FILE_PRODUCERS.finditer(src)}
        spots |= {m.start() for m in FILE_PRODUCER_URLS.finditer(stripped)}
        for start in sorted(spots):
            found = _enclosing_function(src, start)
            assert found, f"{name}:{src[:start].count(chr(10)) + 1} 를 감싼 함수를 못 찾았다"
            body, line = found
            if (name, line) in seen:
                continue
            seen.add((name, line))
            out.append((name, line, body))
    return out


EXPECTED_FILE_PRODUCERS = {"compare.js": 1, "row-export.js": 1}


def test_every_file_producing_path_checks_freshness():
    """파일을 만드는 자리는 **전부** 기준 프레임 신선도를 확인해야 한다.

    `row-export` 는 예외를 올려 파일 생성 전에 멈췄는데 `compare.js` 의 영상
    다운로드는 프리뷰 래퍼를 거쳐 거부를 삼켰다 — 같은 조건에서 두 내보내기가
    **정반대로** 행동했고, 호흡 없는 파일이 나간 뒤 초록 "완료" 알림까지 떴다."""
    sites = _file_producing_functions()
    _assert_inventory("파일 생성 경로", _inventory(sites), EXPECTED_FILE_PRODUCERS)
    missing = [(n, ln) for n, ln, body in sites if not FRESH.search(body)]
    assert not missing, (
        "파일을 만드는데 신선도를 안 본다 — 낡은 해부로 구운 파일이 사용자에게 간다:\n"
        + "\n".join(f"  {n}:{ln}" for n, ln in missing))


# ── 호흡은 굽기가 읽는 파일로 그린다 ───────────────────────────────

# 굽기 파일에 도달하는 **인정 경로**. `bakeFrameUrl` 직접 호출이거나, 그것을 거쳐
# URL 을 내주는 store 헬퍼에서 받은 값이거나. 이름 화이트리스트가 아니라 **출처**다 —
# 헬퍼 자신은 `bakeFrame` 을 거치고, 그 선택은 node 동작 테스트가 따로 검증한다
# (`tests/test_breathe_reference_key.py::test_geometry_measures_the_reference_frame...`).
BAKE_SOURCES = ("bakeFrameUrl(", "breatheGeometryFrame(")
BAKE_HEAD = re.compile(r"(?:bakeFrameUrl|breatheGeometryFrame)\s*\(")


def _is_bake_call(expr: str) -> bool:
    """식 **전체**가 정확히 굽기 소스 호출인가 (뒤따르는 프로퍼티 접근만 허용).

    정규식 `fullmatch` + 탐욕 `.*` 로 판정하면 `bakeFrameUrl(a) ?? frameUrl(b)` 도,
    `bakeFrameUrl(a) , frameUrl(b)` 도 참이 된다 — 탈출 G 에서 없앤 "부분문자열 관대함" 이
    정규식 형태로 되돌아온 것이다. 인자 괄호를 **세어** 호출이 어디서 끝나는지 보고,
    그 뒤에 뭐가 더 있으면 거짓이다. 이러면 같은 축의 다음 철자(새 연산자)도 안 샌다."""
    if not BAKE_HEAD.match(expr):
        return False
    depth = 0
    for i, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                rest = expr[i + 1:].strip()
                return rest == "" or bool(re.fullmatch(r"(?:\.\w+)+", rest))
    return False
IMG_CALL = re.compile(r"\bimg\s*\(")
# 호흡 계약이 사는 파일 — 이 안의 **모든** 프레임 이미지 소스가 굽기 파일이어야 한다.
#
# 변수명 화이트리스트로 쓰면 안 된다: round-9 판은 `zoom-editor.js: ("image",)` 였고,
# 그래서 `const canonImg = img(fr.url)` 로 지오메트리를 재던 자리가 **스캔 대상에조차
# 없었다** (슉슉이 실측 2026-07-26: 경계가 8px 어긋났는데 그물은 조용했다). 이름이
# 아니라 **파일 전체**를 훑고, 호흡과 무관한 줄만 명시적으로 면제한다.
BREATHE_FILES = ("cards.js", "compare.js", "zoom-editor.js", "row-export.js")
# 호흡 경로가 아닌 이미지 소스 (표시·썸네일 등) — 면제는 **이유와 함께** 명시한다.
NON_BREATHE_IMG = {
    ("cards.js", "image"): "카드 표시면 — 호흡 합성 전 원본 표시 (frameUrl 이 맞다)",
}


EMPTY_LEAF = re.compile(r"^(null|undefined|false|0|\"\"|\'\')$")


# 자르면 안 되는 2글자 토큰 — `?` 로 시작하지만 삼항이 아니다.
# `??` 는 **분기**라 따로 자르고(아래), `?.`·`=>` 는 통째로 지나가야 한다.
ATOMIC = ("??", "?.", "=>", "===", "!==", "==", "!=", ">=", "<=")


def _split_top(expr: str, seps: tuple[str, ...]) -> list[str]:
    """괄호 깊이 0 에서만 자른다 (`?:` 는 `?` 와 `:` 를 짝으로 다룬다).

    `??`·`?.` 같은 원자 토큰은 통째로 지나간다 — 안 그러면 `a ?? b` 를 삼항으로 잘못
    쪼개고, 쪼개다 실패하면 잎으로 떨어져 **분기 전수 판정이 통째로 우회된다**
    (노을이 탈출 M 2026-07-26)."""
    out, cur, depth, i = [], "", 0, 0
    while i < len(expr):
        ch = expr[i]
        if depth == 0:
            atom = next((a for a in ATOMIC if expr.startswith(a, i) and a not in seps), None)
            if atom:
                cur += atom
                i += len(atom)
                continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if depth == 0:
            for sep in seps:
                if expr.startswith(sep, i):
                    out.append(cur); cur = ""; i += len(sep); break
            else:
                cur += ch; i += 1
            continue
        cur += ch; i += 1
    out.append(cur)
    return [x.strip() for x in out]


def _neutral_src(name: str) -> str:
    return _neutralize_literals((CURATOR_SRC / name).read_text(encoding="utf-8"),
                                strip_comments=True)


def _line_start(src: str, lineno: int) -> int:
    """1-기반 줄 번호의 시작 오프셋."""
    pos, line = 0, 1
    while line < lineno:
        nxt = src.find("\n", pos)
        if nxt < 0:
            return len(src)
        pos, line = nxt + 1, line + 1
    return pos


def _enclosing_spans(src: str, pos: int):
    """`pos` 를 감싸는 함수 본문들 — [(여는{ 오프셋, 닫는} 오프셋, 헤더텍스트)], 안쪽부터."""
    stack = []
    for i, ch in enumerate(src[:pos]):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            stack.pop()
    out = []
    for open_at in reversed(stack):
        header = src[max(0, open_at - 200):open_at]
        if not HEADER.search(header):
            continue
        depth, close = 0, len(src)
        for j in range(open_at, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    close = j
                    break
        out.append((open_at, close, header))
    return out


PARAMS = re.compile(r"\(([^()]*)\)\s*(?:=>)?\s*$")


def _param_names(header: str) -> set:
    m = PARAMS.search(header.rstrip())
    if not m:
        return set()
    return {t.strip().split(":")[0].strip() for t in m.group(1).split(",") if t.strip()}


def _reassigned(name: str, symbol: str) -> bool:
    """`symbol` 이 선언 밖에서 다시 대입되는가.

    `_bake_only` 는 이름을 만나면 **선언만** 읽는다. 그래서 `const` 를 `let` 으로 바꾸고
    가드 본문에서 되돌리면 그물이 침묵했다:

        let canonical = canonSrc ? img(canonSrc) : null;
        if (!(canonical && canonical.complete)) canonical = image;   // <- 무사통과

    선언 RHS 가 굽기 소스라 참을 주고 그 뒤 흐름을 안 봤다 — 그물이 자기 규칙("증명 못
    하는 이름은 거짓")을 자기가 어긴 것이다. 재대입된 이름은 증명된 이름이 아니다."""
    sym = re.escape(symbol)
    decl = re.compile(rf"\b(const|let|var)\s+({sym}\b|\{{[^}}]*\b{sym}\b[^}}]*\}})\s*=")
    assign = re.compile(rf"(^|[^\w.]){sym}\s*(=(?![=>])|\+=|\|\|=|\?\?=)")
    return any(assign.search(t) and not decl.search(t) for _, t in _statements(name))


def _binding_rhs(name: str, lineno: int, symbol: str):
    """`symbol` 의 선언(위쪽)과 그 시작줄. 못 찾거나 **재대입되면** None.

    **문장 단위**로 읽는다. 줄 단위(`prev.split("=", 1)[1]`)로 읽던 판은 RHS 가 다음
    줄로 넘어가면 뒷부분이 통째로 안 보였고, 잘린 머리가 참을 줬다 — prettier 식
    줄바꿈만으로 분기 전수 판정이 통째로 우회됐다 (노을이 탈출 J 2026-07-26).

    구조분해(`const { url: refUrl } = breatheGeometryFrame(...)`)도 읽는다."""
    if _reassigned(name, symbol):
        return None                      # 흐름에 따라 값이 바뀐다 = 증명 불가
    # **스코프**를 본다. 예전엔 파일 전체에서 "그 줄 앞의 마지막 선언" 을 찾아, 그리는
    # 자리가 **함수 파라미터**를 받으면 같은 파일 위쪽의 무관한 `const image = ...` 가
    # 그 파라미터를 증명해버렸다 — 실제 인자는 호출부에 있고 호출부는 수집되지도 않는다.
    # 두 블록이 거의 같아 그리기를 헬퍼로 빼는 **평범한 DRY 리팩토링**만으로 도달했고,
    # 540 passed(기준선과 개수까지 동일)였다 (노을이 탈출 N 2026-07-26).
    src = _neutral_src(name)
    spans = _enclosing_spans(src, _line_start(src, lineno))
    if spans and symbol in _param_names(spans[0][2]):
        return None                      # 파라미터 = 값이 호출부에 있다 = 증명 불가
    sym = re.escape(symbol)
    direct = re.compile(rf"\b(const|let|var)\s+{sym}\b\s*=")
    destructured = re.compile(rf"\b(const|let|var)\s*\{{[^}}]*\b{sym}\b[^}}]*\}}\s*=")
    best = None
    for start, text in _statements(name):
        if start > lineno:
            break
        if direct.search(text) or destructured.search(text):
            # 선언이 사이트의 **스코프 체인 안**에 있어야 한다. 형제 스코프의 동명 바인딩은
            # 이 자리를 증명하지 못한다 (자기 계약: 증명 못 하면 거짓).
            at = _line_start(src, start)
            if spans and not any(o < at < c for o, c, _ in spans):
                continue
            best = (text.split("=", 1)[1].strip(), start)
    return best


def _bake_only(name: str, lineno: int, expr: str, depth: int = 8) -> bool:
    """`expr` 이 **모든 분기에서** 굽기 소스이거나 빈 값인가.

    "굽기 소스에 닿는가" 로는 부족하다 — 앞선 판은 도달만 보고 True 를 줬고, 그래서
    폐기된 폴백을 한 줄 변수 추출로 원문 그대로 되살려도 537 전부 통과했다:

        const canonImg = canonSrc ? img(canonSrc) : null;
        const canonical = (canonImg && canonImg.complete) ? canonImg : image;   // <- 무사통과

    워커가 `canonImg` 를 따라 `bakeFrameUrl` 에 닿으면 참을 돌려주고, **같은 바인딩의
    `: image` 가지를 안 봤다.** 그물을 강화하려고 넣은 전이 추적이 정확히 그 탈출구를 열었다
    (노을이 실측 2026-07-26 R1). 그래서 도달이 아니라 **분기 전수**로 판정한다.

    증명 못 하면 거짓이다 — 못 찾은 이름(파라미터·상위 스코프)을 통과시키면 같은 구멍이 난다.

    예산(`depth`)은 **바인딩을 따라갈 때만** 깎는다. 삼항·`&&`·`||` 분해는 같은 식을 쪼개는
    것이라 홉이 아닌데, 거기서도 깎으면 정상 체인이 예산 고갈로 거짓이 되어 오진한다."""
    if depth <= 0:
        return False
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")") and len(_split_top(expr[1:-1], (",",))) == 1:
        expr = expr[1:-1].strip()
    if not expr:
        return False
    # 삼항: 두 가지가 **모두** 성립해야 한다 (조건절은 값이 아니다)
    parts = _split_top(expr, ("?",))
    if len(parts) == 2:
        branches = _split_top(parts[1], (":",))
        if len(branches) == 2:
            return all(_bake_only(name, lineno, b, depth) for b in branches)
    # `||`·`??` 는 분기다 — 모든 피연산자가 성립해야 한다.
    # `??` 를 빠뜨렸더니 "굽기 URL 이 없으면 표시 URL" 을 `??` 로 적는 것만으로 그물 둘이
    # 동시에 침묵했다. 그건 적대적 트릭이 아니라 **린터가 권하는 표현**이고, `bakeFrameUrl`
    # 은 plain+트윈없음에서 실제로 `null` 을 돌려주므로 그 폴백은 진짜로 발화한다.
    for op in ("||", "??"):
        parts = _split_top(expr, (op,))
        if len(parts) > 1:
            return all(_bake_only(name, lineno, o, depth) for o in parts)
    # 콤마 연산자도 분기다 (값은 마지막이지만, 앞 항이 굽기 소스라고 통과시키면 안 된다)
    commas = _split_top(expr, (",",))
    if len(commas) > 1:
        return all(_bake_only(name, lineno, c, depth) for c in commas)
    # `&&` 는 가드 + 값 — 마지막 피연산자만 값이다
    ands = _split_top(expr, ("&&",))
    if len(ands) > 1:
        return _bake_only(name, lineno, ands[-1], depth)
    # 잎 — 여기서 참을 주는 형태를 **좁게 열거**한다. 넓게 열면 매 라운드 새 우회가 생겼다.
    if EMPTY_LEAF.match(expr):
        return True                       # 값 없음 = 워프 안 함 (폴백이 아니다)
    # 굽기 소스는 "그 식이 **그 호출이다**" 여야 한다. `src in expr` 부분문자열 판정은
    # `imgOrDisplay(bakeFrameUrl(...), image)` 처럼 굽기 소스를 **인자로 품은** 임의의
    # 호출에 참을 준다 — 폴백을 두 번째 인자로 숨기면 그만이다 (노을이 탈출 G).
    if _is_bake_call(expr):
        return True
    # 호출 투명성은 `img(` **하나에만** 준다. `[\w.]+\(x\)` 로 열어두면 폐기된 폴백을
    # 한 인자 헬퍼에 넣는 것만으로 통과한다 — 그물이 인자만 보고 헬퍼가 그걸로 무엇을
    # 하는지 한 번도 안 본다 (노을이 탈출 F 실측 2026-07-26: 538 passed, 기준선과 동일):
    #
    #     const readyOr = (c) => (c && c.complete) ? c : image;
    #     const canonical = readyOr(canonImg);          // <- 무사통과였다
    #
    # 자기 계약("증명 못 하는 이름은 거짓")과 맞추려면 아는 함수만 투명해야 한다.
    call = re.fullmatch(r"img\(\s*([\w.]+)\s*\)", expr)
    if call:
        return _bake_only(name, lineno, call.group(1), depth - 1)
    if re.fullmatch(r"[\w.]+", expr):
        found = _binding_rhs(name, lineno, expr.split(".")[0])
        if not found:
            return False                  # 증명 불가 = 거짓
        rhs, at = found
        return _bake_only(name, at, rhs, depth - 1)
    return False


def _bound_from_bake_source(name: str, lineno: int, symbol: str) -> bool:
    """호환 진입점 — 판정은 `_bake_only` 하나가 소유한다."""
    return _bake_only(name, lineno, symbol)


def _breathe_image_sites():
    for name in BREATHE_FILES:
        for lineno, text in _statements(name):
            m = re.match(r"(?:const|let|var)\s+(\w+)\s*=", text)
            if not (m and IMG_CALL.search(text)):
                continue
            if (name, m.group(1)) in NON_BREATHE_IMG:
                continue
            yield name, lineno, text


EXPECTED_IMAGE_SITES = {"cards.js": 1, "compare.js": 1, "zoom-editor.js": 4, "row-export.js": 1}


def test_there_are_breathe_image_sites():
    sites = list(_breathe_image_sites())
    assert sites, "호흡 이미지 소스 지점이 사라졌다 — 계약 위치 확인"
    got = {}
    for name, _, _ in sites:
        got[name] = got.get(name, 0) + 1
    assert got == EXPECTED_IMAGE_SITES, (
        f"호흡 이미지 사이트 인벤토리가 달라졌다.\n  기대: {EXPECTED_IMAGE_SITES}\n"
        f"  실제: {got}\n"
        "  선언 키워드·대입 모양을 바꾸면 사이트가 조용히 증발한다 — 파일 존재만 보면 "
        "여러 사이트를 가진 파일의 상실을 못 본다(워프 base 스캐너가 그렇게 뚫렸다).")


@pytest.mark.parametrize("site", list(_breathe_image_sites()), ids=lambda s: f"{s[0]}:{s[1]}")
def test_breathe_draws_from_the_file_the_bake_reads(site):
    """워프 base 도 신선도 기준도 **굽기가 읽는 파일**에서 와야 한다.

    `frameUrl` 은 표시용이라 pp OFF 에서 `orig/` 고해상 트윈을 준다 — 굽기(`row_frame_rel`)
    는 그 파일을 절대 안 읽는다. 기준이 굽기가 안 읽는 파일이면 지문이 **영구 불일치**라
    해부를 갱신해도 안 풀리고 그 줄의 영상 내보내기가 영구 차단된다. 캐노니컬(`f.url`)로
    그리면 pp OFF 줄에서 굽기와 다른 그림이 나간다 (슉슉이 2026-07-26: 같은 캔버스를
    다른 해부로 워프해 4위상에서 최대 114바이트).

    이 그물이 없던 동안 `frameUrl` → `f.url` 로 되돌려도 111개 테스트가 전부 통과했다."""
    name, lineno, line = site
    # 판정은 **`_bake_only` 하나**가 소유한다 — 형제 스캐너(워프 base)와 같은 판정기다.
    #
    # 앞선 판은 여기서만 옛 관대함을 들고 있었다: (a) 줄에서 첫 `img(` 인자 **한 심볼**만
    # 뽑아 봐서 같은 선언의 다른 분기가 안 보였고(탈출 C 의 분기 사각이 축만 바꿔 재발),
    # (b) 줄 **부분문자열** 지름길(`bakeFrameUrl(` 이 어딘가 있으면 통과)이 탈출 G 에서 잎에서는 없앤 그
    # 관대함을 그대로 남겨뒀다. 그래서 `img(refUrl || frameUrl(...))` 같은 폴백이 540 passed
    # 로 통과했다 (노을이 탈출 I 실측 2026-07-26).
    #
    # 판정기를 좁히면 **모든 호출부가 같이 좁아져야** 한다. 한 곳만 고치면 다음 우회가
    # 안 고친 쪽으로 간다 — 이 플랜에서 여섯 라운드 반복된 형태다.
    rhs = line.split("=", 1)[1].strip().rstrip(";") if "=" in line else line
    assert _bake_only(name, lineno, rhs), (
        f"{name}:{lineno} 의 어떤 분기가 굽기 파일이 아니다.\n  {line}\n"
        f"  `bakeFrameUrl(state, frame)` 또는 그걸 거치는 store 헬퍼를 써라 "
        f"(frameUrl 은 표시용, f.url 은 캐노니컬).")


# ── 고칠 수 없는 차이도 관측 가능해야 한다 ──────────────────────────

RESAMPLE_NOTICE = re.compile(r"breatheResamplesDifferently\s*\(")


def test_file_producing_paths_report_the_resampler_gap():
    """회전·확대가 걸린 줄은 프리뷰와 굽기의 **픽셀이 다르다** — 말은 해야 한다.

    굽기는 `apply_transform` 에서 BICUBIC(`snap_scale` 없는 런 = 기본 런 전부),
    웹뷰는 `imageSmoothingEnabled=false` 캔버스라 NEAREST 다. 실측 (슉슉이 2026-07-26,
    96px 픽스처, 신선도는 통과시킨 상태): rotate 3° 에서 12/12 위상 최대 1803px 상이,
    scale 1.03 에서 최대 1699px. 원인은 표시 파이프라인이라 호흡이 고칠 수 있는 게
    아니지만, `row-export` 와 비교뷰 다운로드는 **이 캔버스로 파일을 굽는다** —
    조용히 두면 사용자는 GIF 와 다른 파일을 받고도 모른다 (원칙 6).

    "고칠 수 없으니 말하지 않는다" 는 이 플랜에서 금지된 형태다."""
    for name, line, body in _file_producing_functions():
        assert RESAMPLE_NOTICE.search(body), (
            f"{name}:{line} 이 파일을 만들면서 리샘플러 차이를 안 알린다 — "
            "사용자는 GIF 와 다른 파일을 받고도 모른다")


def test_the_warn_notice_has_a_visible_style():
    """`warn` 알림이 스타일 없이 나가면 '알렸다' 가 사실상 거짓이 된다."""
    css = (CURATOR_SRC.parent / "curator.css").read_text(encoding="utf-8")
    assert ".status.warn" in css, "warn 알림에 스타일이 없다 — 오류·성공과 구분이 안 된다"


# ── 워프 base 는 폴백하지 않는다 ────────────────────────────────────

# 워프 base 사이트는 **이름이 아니라 의미**로 모은다.
#
# 앞선 판은 `drawFrameInto\(\s*(\w*[Bb]aseCtx)` 로 ctx **이름**을 훑었다. 그 패턴에 걸리는
# 자리는 레포 전체에 2곳뿐이었고, `baseCtx` 를 `warpCtx` 로 바꾸는 것만으로 cards.js 가
# 통째로 스캐너에서 사라지면서 폴백을 원문 그대로 되살려도 537 passed 였다 — 개수까지
# 기준선과 같아서 테스트가 사라진 신호조차 없었다 (노을이 실측 2026-07-26).
#
# 그래서 사이트를 **호흡 워프에 실제로 들어가는 캔버스**에서 역산한다:
#   breatheComposite(X, ...) / breatheComposeForPreview(X, ...) -> 캔버스 X
#   const <ctx> = X.getContext(...)                             -> 그 캔버스의 ctx
#   drawFrameInto(<ctx>, ARG, ...)                              -> ARG 가 워프 base 다
# 이름을 바꿔도 역산이 따라가므로 리네임으로 빠져나갈 수 없다. 덤으로 `bx` 처럼 이름이
# 다른 자리 3곳(compare·row-export·zoom-editor 두 번째)도 이제 계약에 들어온다 —
# row-export 는 프리뷰가 아니라 **WebM 파일을 굽는** 경로다.
COMPOSITE_CALL = re.compile(r"breathe(?:Composite|ComposeForPreview)\(\s*(\w+)")
CTX_BIND = re.compile(r"\b(?:const|let|var)\s+(\w+)\s*=\s*(\w+)\.getContext\(")
DRAW_INTO = re.compile(r"drawFrameInto\(\s*(\w+)\s*,\s*([^,]+),")
# 워프 base **인벤토리** — 파일마다 몇 곳인지까지 못박는다.
#
# "파일에 하나라도 있나" 만 보면 사이트가 2곳인 파일(zoom-editor)은 하나가 사라져도
# 못이 안 운다. 그 자리에 조용한 표시파일 폴백을 되살리면 538 passed, **기준선과 개수까지
# 동일**이었다 (노을이 탈출 H 실측 2026-07-26). ctx 바인딩을 헬퍼로 묶는 자연스러운 DRY
# 리팩토링만으로 도달한다 — 적대적 트릭이 필요 없다.
EXPECTED_WARP_BASES = {"cards.js": 1, "compare.js": 1, "zoom-editor.js": 2, "row-export.js": 1}


def _warp_base_sites():
    for name in BREATHE_FILES:
        stmts = _statements(name)
        joined = " ".join(t for _, t in stmts)
        canvases = set(COMPOSITE_CALL.findall(joined))
        ctxs = {ctx for ctx, canvas in CTX_BIND.findall(joined) if canvas in canvases}
        for lineno, text in stmts:
            m = DRAW_INTO.search(text)
            if m and m.group(1) in ctxs:
                yield name, lineno, m.group(2).strip()


def test_the_warp_base_inventory_is_intact():
    """워프 base 사이트가 **파일마다 몇 곳인지까지** 그대로여야 한다.

    존재만 보는 못은 사이트가 여럿인 파일에서 상실을 못 본다 — 하나가 사라져도 나머지가
    파일을 커버한다. 조용한 커버리지 상실은 이 플랜이 이미 두 번 1급 계약으로 올린
    실패 모드이고, 이건 그 다음 granularity 다.

    개수가 바뀌면 실패하는 게 **의도다**: 워프 base 를 늘리거나 줄이는 변경은 사람이
    한 번 보고 이 표를 같이 고쳐야 한다."""
    got = {}
    for name, _, _ in _warp_base_sites():
        got[name] = got.get(name, 0) + 1
    assert got == EXPECTED_WARP_BASES, (
        f"워프 base 인벤토리가 달라졌다.\n  기대: {EXPECTED_WARP_BASES}\n  실제: {got}\n"
        "  줄었으면 사이트가 조용히 증발한 것이고(캔버스·ctx·호출 이름 변경), "
        "늘었으면 새 워프 base 가 계약에 들어온 것이다 — 어느 쪽이든 표를 같이 고쳐라.")


def test_the_warp_base_never_falls_back_to_a_display_file():
    """호흡 워프의 base 는 **굽기가 읽는 파일 하나**다 — 폴백을 두지 않는다.

    `cards.js` 는 `(canonical && canonical.complete) ? canonical : image` 였다. 굽기 파일
    이미지가 아직 로드 중이면 **표시 파일**(pp OFF 트윈 줄은 `orig/` 고해상본)을 워프
    base 로 썼고 알림은 0건이었다. 로드되면 자가 교정되는 일시적 현상이라 산출물은 안
    깨지지만, 조용히 **틀린 그림**을 그리는 건 조용히 **안 그리는** 것과 다르다 —
    준비 안 됐으면 워프하지 않는다(`zoom-editor`·`compare`·`row-export` 가 쓰는 형태).

    판정은 도달도 선언도 아닌 **분기 전수 + 재대입 금지**다 (`_bake_only`): 세 번의 탈출이
    각각 분기·흐름·사이트 수집을 노렸다."""
    sites = list(_warp_base_sites())
    assert sites, "워프 base 자리를 하나도 못 찾았다 — 스캐너가 고장났다"
    for name, line, arg in sites:
        assert _bake_only(name, line, arg), (
            f"{name}:{line} 워프 base {arg!r} 의 어떤 분기가 굽기 파일이 아니다.\n"
            "  준비 안 된 소스는 폴백이 아니라 **안 그리는 것**으로 다뤄라.")


# ── 문서가 폐기된 설계를 말하지 않는다 ──────────────────────────────

REPO_ROOT = CURATOR_SRC.parent.parent.parent
# round-11 이 폐기한 설계의 문구. 문서가 이걸 말하면 다음 사람이 폐기된 모델로 고친다.
RETIRED_DOC_CLAIMS = [
    ("fingerprint of the\n  frame it came from", "지문은 프레임이 아니라 **입력 키**의 지문이다"),
    ("a mismatch re-detects", "굽기는 지문을 안 보고 **매번** 재검출한다"),
]
DOC_FILES = ["CHANGELOG.md", "SKILL.md", "README.md",
             "docs/static-pose-recipe.md", "docs/run-contract.md", "docs/curation.md"]


def test_no_doc_still_describes_the_retired_fingerprint_design():
    """문서는 지금 설계를 말해야 한다 (SSoT 드리프트 금지).

    round-11 이 지문을 *변형 결과 픽셀* 에서 *입력 키* 로 옮겼고 굽기가 캐시를 아예 안
    믿게 됐는데, CHANGELOG 한 곳만 옛 모델을 계속 말하고 있었다 (슉슉이 note 2026-07-26).
    같은 규칙을 말하는 진입점이 여럿이면 한쪽만 고쳐진다 — 그래서 전 진입점을 훑는다."""
    stale = []
    for rel in DOC_FILES:
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for claim, why in RETIRED_DOC_CLAIMS:
            if claim in text:
                stale.append(f"{rel}: {claim!r} — {why}")
    assert not stale, "문서가 폐기된 지문 설계를 말한다:\n  " + "\n  ".join(stale)
