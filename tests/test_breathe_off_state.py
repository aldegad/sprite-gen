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


def _call_sites():
    for path in sorted(CURATOR_SRC.glob("*.js")):
        if path.name == "breathe.js":
            continue                      # 정의부 — 호출부가 아니다
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CALL.search(line):
                yield path.name, lineno, line.strip()


def test_there_are_call_sites_to_guard():
    """그물이 빈 채로 통과하지 않게 — 호출부가 사라졌으면 이 테스트를 고쳐야 한다."""
    sites = list(_call_sites())
    assert sites, "breatheComposite 호출부가 하나도 없다 — 계약이 옮겨갔는지 확인하라"


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


def _preview_call_sites():
    for path in sorted(CURATOR_SRC.glob("*.js")):
        if path.name == "breathe.js":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = PREVIEW_CALL.search(line)
            if m:
                yield path.name, lineno, line.strip(), m.group(1)


def test_there_are_preview_call_sites():
    assert list(_preview_call_sites()), "프리뷰 호출부가 사라졌다 — 계약 위치 확인"


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
FILE_PRODUCERS = re.compile(r"toDataURL\s*\(|toBlob\s*\(|/api/compare-gif|/api/row-video")
FRESH = re.compile(r"breatheAssertFresh\s*\(")

# 호흡 계약이 사는 파일 안에서 파일을 만드는 **모든** 함수가 대상이다.
# 손으로 적은 파일 화이트리스트(`FRESH_CONSUMERS = ("row-export.js",)`)로 쓰면 안 된다:
# 그 판일 때 `compare.js` 의 영상 다운로드가 호흡을 조용히 뺀 파일을 만들고 있었는데
# 그물은 아무 말도 안 했다 (슉슉이 실측 2026-07-26). 대상은 이름이 아니라 **연산**이다.
EXPORT_EXEMPT = {
    # (파일, 시작줄) : 이유
}


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
    """(파일, 시작줄, 본문) — 파일을 만드는 연산을 감싼 함수 전수 (중복 제거)."""
    out, seen = [], set()
    for name in BREATHE_FILES:
        src = _strip_comments((CURATOR_SRC / name).read_text(encoding="utf-8"))
        for m in FILE_PRODUCERS.finditer(src):
            found = _enclosing_function(src, m.start())
            assert found, f"{name}:{src[:m.start()].count(chr(10)) + 1} 를 감싼 함수를 못 찾았다"
            body, line = found
            if (name, line) in seen:
                continue
            seen.add((name, line))
            out.append((name, line, body))
    return out


def test_every_file_producing_path_checks_freshness():
    """파일을 만드는 자리는 **전부** 기준 프레임 신선도를 확인해야 한다.

    `row-export` 는 예외를 올려 파일 생성 전에 멈췄는데 `compare.js` 의 영상
    다운로드는 프리뷰 래퍼를 거쳐 거부를 삼켰다 — 같은 조건에서 두 내보내기가
    **정반대로** 행동했고, 호흡 없는 파일이 나간 뒤 초록 "완료" 알림까지 떴다."""
    sites = _file_producing_functions()
    assert sites, "파일을 만드는 자리를 하나도 못 찾았다 — 스캐너가 고장났다"
    missing = [(n, ln) for n, ln, body in sites
               if not FRESH.search(body) and (n, ln) not in EXPORT_EXEMPT]
    assert not missing, (
        "파일을 만드는데 신선도를 안 본다 — 낡은 해부로 구운 파일이 사용자에게 간다:\n"
        + "\n".join(f"  {n}:{ln}" for n, ln in missing))


# ── 호흡은 굽기가 읽는 파일로 그린다 ───────────────────────────────

# 굽기 파일에 도달하는 **인정 경로**. `bakeFrameUrl` 직접 호출이거나, 그것을 거쳐
# URL 을 내주는 store 헬퍼에서 받은 값이거나. 이름 화이트리스트가 아니라 **출처**다 —
# 헬퍼 자신은 `bakeFrame` 을 거치고, 그 선택은 node 동작 테스트가 따로 검증한다
# (`tests/test_breathe_reference_key.py::test_geometry_measures_the_reference_frame...`).
BAKE_SOURCES = ("bakeFrameUrl(", "breatheGeometryFrame(")
BAKE_URL = re.compile(r"bakeFrameUrl\s*\(")
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


def _bound_from_bake_source(name: str, lineno: int, symbol: str, _depth: int = 3) -> bool:
    """`symbol` 이 이 파일에서 굽기 경로로 바인딩됐는가 (선언 위쪽 40줄 안).

    **전이적으로** 따라간다: `const canonSrc = bakeFrameUrl(...)` → `const canonical =
    canonSrc ? img(canonSrc) : null` 처럼 한 단계 건너 오는 게 정상 형태이고, 한 단계만
    보면 선택을 헬퍼/중간변수로 모으는 리팩토링이 곧 그물 실패가 된다."""
    if _depth <= 0:
        return False
    root = symbol.split(".")[0]
    lines = _strip_comments((CURATOR_SRC / name).read_text(encoding="utf-8")).splitlines()
    for offset, prev in enumerate(lines[max(0, lineno - 41):lineno - 1]):
        if not re.search(rf"\b(const|let|var)\b[^=]*\b{re.escape(root)}\b[^=]*=", prev):
            continue
        if any(src in prev for src in BAKE_SOURCES):
            return True
        # 이 바인딩이 참조하는 다른 지역 이름을 따라간다
        rhs = prev.split("=", 1)[1] if "=" in prev else ""
        prev_line = max(0, lineno - 41) + offset + 1
        for ref in set(re.findall(r"\b[a-zA-Z_]\w*\b", rhs)) - {root}:
            if _bound_from_bake_source(name, prev_line, ref, _depth - 1):
                return True
    return False


def _breathe_image_sites():
    for name in BREATHE_FILES:
        path = CURATOR_SRC / name
        if not path.is_file():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"\s*const (\w+) = .*", line)
            if not (m and IMG_CALL.search(line)):
                continue
            if (name, m.group(1)) in NON_BREATHE_IMG:
                continue
            yield name, lineno, line.strip()


def test_there_are_breathe_image_sites():
    assert list(_breathe_image_sites()), "호흡 이미지 소스 지점이 사라졌다 — 계약 위치 확인"


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
    # 한 줄 안의 직접 호출이거나, 같은 파일 위쪽에서 굽기 경로로 바인딩된 값이거나.
    # 후자를 인정하지 않으면 선택을 헬퍼로 모으는 리팩토링이 곧 그물 실패가 되어
    # 중복을 남기는 쪽이 유리해진다 — 그물이 나쁜 설계를 강요하면 안 된다.
    arg = re.search(r"\bimg\s*\(\s*([\w.]+)", line)
    ok = BAKE_URL.search(line) or (arg and _bound_from_bake_source(name, lineno, arg.group(1)))
    assert ok, (
        f"{name}:{lineno} 가 굽기 파일이 아닌 소스로 호흡을 그린다.\n  {line}\n"
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

WARP_BASE = re.compile(r"drawFrameInto\(\s*(\w*[Bb]aseCtx)\s*,\s*([^,]+),")


def test_the_warp_base_never_falls_back_to_a_display_file():
    """호흡 워프의 base 는 **굽기가 읽는 파일 하나**다 — 폴백 삼항을 두지 않는다.

    `cards.js` 는 `(canonical && canonical.complete) ? canonical : image` 였다. 굽기 파일
    이미지가 아직 로드 중이면 **표시 파일**(pp OFF 트윈 줄은 `orig/` 고해상본)을 워프
    base 로 썼고 알림은 0건이었다 (슉슉이 note 2026-07-26). 로드되면 자가 교정되는
    일시적 현상이라 산출물은 안 깨지지만, 조용히 **틀린 그림**을 그리는 건 조용히
    **안 그리는** 것과 다르다 — 아직 준비 안 됐으면 워프하지 않는다(`zoom-editor` 처럼).

    `test_breathe_draws_from_the_file_the_bake_reads` 는 `img(...)` 바인딩만 봐서 이
    폴백 가지를 못 본다."""
    found = 0
    for name in BREATHE_FILES:
        src = _strip_comments((CURATOR_SRC / name).read_text(encoding="utf-8"))
        for m in WARP_BASE.finditer(src):
            found += 1
            arg = m.group(2).strip()
            line = src[:m.start()].count("\n") + 1
            assert re.fullmatch(r"\w+", arg), (
                f"{name}:{line} 워프 base 가 폴백을 가진다: {arg!r}\n"
                "  준비 안 된 소스는 폴백이 아니라 **안 그리는 것**으로 다뤄라.")
            assert _bound_from_bake_source(name, line, arg), (
                f"{name}:{line} 워프 base {arg!r} 가 굽기 파일에서 온 값이 아니다")
    assert found, "워프 base 자리를 하나도 못 찾았다 — 스캐너가 고장났다"


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
