"""호흡 계약 변이 배터리 — 그물을 손대면 **전부** 다시 돌린다.

    python scripts/breathe_mutation_battery.py

각 항목은 "이 계약이 깨지면 어떤 코드가 되는가" 를 소스에 잠깐 심고, 그물이 실제로
무는지(테스트가 FAIL 하는지) 확인한 뒤 되돌린다. 전부 물어야 exit 0 이다.

**왜 스크립트인가.** 검증 라운드 두 번이 같은 형태로 기각됐다: 그물을 고치면서 "새
변이를 무는가" 만 보고 "원래 잡던 걸 여전히 잡는가" 를 안 물었다. 두 번 다 그물 자신의
개선(전이 추적 / 선언 추적)이 새 탈출구를 열었다. 배터리로 묶어 그 선택지를 없앤다 —
그물을 바꾸면 이걸 돌리고, 새 계약을 세우면 여기에 줄을 추가한다.
"""
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
PY = os.environ.get("PYTEST_PYTHON", sys.executable)

# 소스를 잠깐 망가뜨렸다 되돌린다 — 남의 변경 위에서 돌리면 복구가 그 변경을 덮는다.
dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                       capture_output=True, text=True).stdout.strip()
if dirty and not os.environ.get("BATTERY_ALLOW_DIRTY"):
    print("작업트리가 clean 이 아니다 — 변이 복구가 그 변경을 덮을 수 있다.\n"
          "커밋하거나 stash 한 뒤 다시 돌려라 (강행: BATTERY_ALLOW_DIRTY=1).")
    print(dirty)
    sys.exit(2)
os.chdir(REPO)

# 각 항목: (설명, [(파일, 옛 문자열, 새 문자열), ...])
# 편집을 **묶을 수 있어야 한다** — "이름을 바꾸고 폴백을 되살린다" 처럼 두 곳을 동시에
# 건드리는 회귀가 실제 탈출구였다 (노을이 탈출 E, 2026-07-26).
MUTS = [
    ("N1 isfinite 게이트 제거", "sprite_gen/migrate_breathe.py",
     "integral = math.isfinite(raw_breaths) and raw_breaths == int(raw_breaths)",
     "integral = raw_breaths == int(raw_breaths)"),
    ("N2 변형 생략", "scripts/serve_curation.py",
     "return apply_transform(source, transforms.get(edit_idx), cell, snap_scale=snap), key",
     "return source, key"),
    ("N3 캐노니컬 폴백", "scripts/curator/src/store.js",
     "return { url: frame.plainBakeUrl || null, stamp: frame.plainBakeStamp || null };",
     "return { url: frame.plainBakeUrl || frame.url, stamp: frame.plainBakeStamp || frame.stamp || null };"),
    ("N4 키없음 통과", "scripts/curator/src/breathe.js",
     '    throw new BreatheRefused("기준 프레임 키를 만들 수 없다 — 신선도 확인 불가.");',
     "    return;"),
    ("N5 직접 폴백", "scripts/curator/src/cards.js",
     "          drawFrameInto(baseCtx, canonical,",
     "          drawFrameInto(baseCtx, (canonical && canonical.complete) ? canonical : image,"),
    ("N5 표시 URL 출처", "scripts/curator/src/cards.js",
     "        const canonSrc = f && bakeFrameUrl(state.name, f);",
     "        const canonSrc = f && frameUrl(state.name, f);"),
    ("N5 탈출 C: 변수 추출", "scripts/curator/src/cards.js",
     "        const canonical = canonSrc ? img(canonSrc) : null;\n        if (!(canonical && canonical.complete && canonical.naturalWidth)) {",
     "        const canonImg = canonSrc ? img(canonSrc) : null;\n        const canonical = (canonImg && canonImg.complete && canonImg.naturalWidth) ? canonImg : image;\n        if (false) {"),
    ("N5 탈출 D: let + 재대입", "scripts/curator/src/cards.js",
     "        const canonical = canonSrc ? img(canonSrc) : null;\n        if (!(canonical && canonical.complete && canonical.naturalWidth)) {",
     "        let canonical = canonSrc ? img(canonSrc) : null;\n        if (!(canonical && canonical.complete && canonical.naturalWidth)) canonical = image;\n        if (false) {"),
    ("N5 탈출 E: ctx 리네임 + 폴백 복원", [
        ("scripts/curator/src/cards.js",
         '        const baseCtx = base.getContext("2d");',
         '        const warpCtx = base.getContext("2d");'),
        ("scripts/curator/src/cards.js",
         "        baseCtx.imageSmoothingEnabled = false;",
         "        warpCtx.imageSmoothingEnabled = false;"),
        ("scripts/curator/src/cards.js",
         "          drawFrameInto(baseCtx, canonical,",
         "          drawFrameInto(warpCtx, (canonical && canonical.complete && canonical.naturalWidth) ? canonical : image,"),
    ]),
    ("N6 폐기 문구 복원", "CHANGELOG.md",
     "is frozen into the sidecar as a cache",
     "is frozen into the sidecar with a fingerprint of the\n  frame it came from; a mismatch re-detects and says so. cache"),
]

TARGET = "tests"
failed_to_bite = []
for label, *rest in MUTS:
    edits = rest[0] if len(rest) == 1 else [(rest[0], rest[1], rest[2])]
    backups, stale = {}, False
    for rel, old, new in edits:
        path = pathlib.Path(rel)
        text = backups.setdefault(rel, path.read_text(encoding="utf-8"))
        current = path.read_text(encoding="utf-8")
        if old not in current:
            print(f"  !! {label}: 변이 대상 문자열을 못 찾았다 ({rel}) — 배터리가 낡았다")
            stale = True
            break
        path.write_text(current.replace(old, new, 1), encoding="utf-8")
    if stale:
        for rel, text in backups.items():
            pathlib.Path(rel).write_text(text, encoding="utf-8")
        failed_to_bite.append(label + " (대상 없음)")
        continue
    try:
        r = subprocess.run([PY, "-m", "pytest", TARGET, "-q", "--no-header", "-x",
                            "-k", "breathe or migrate"],
                           capture_output=True, text=True)
        bit = r.returncode != 0
        print(f"  {'물었다' if bit else '!! 안 물었다'}  {label}")
        if not bit:
            failed_to_bite.append(label)
    finally:
        for rel, text in backups.items():
            pathlib.Path(rel).write_text(text, encoding="utf-8")

print()
if failed_to_bite:
    print("무방비:", ", ".join(failed_to_bite))
    sys.exit(1)
print(f"변이 {len(MUTS)}건 전부 그물에 걸림")
