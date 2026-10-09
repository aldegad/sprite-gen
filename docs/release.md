# 릴리즈 — 태그를 push 하고, 레인이 빌드한 것으로 릴리즈 페이지를 낸다

> Owns: How a vX.Y.Z tag becomes a release page, what the lane builds and the publisher attaches, and what stays manual · Index: [docs/README.md](README.md)

릴리즈 페이지는 손으로 만들지 않는다. `vX.Y.Z` 태그를 push 한 뒤 `scripts/release_publish.py` 가
그 태그의 `CHANGELOG.md` 절을 본문으로 페이지를 만든다. 사람이 `gh release create` 의 인자를
기억할 필요가 없게 하는 것이 목적이다 — v2.5.3 이 태그·CHANGELOG·README 까지 올라간 채 릴리즈
페이지만 빠져서, 릴리즈 목록에는 v2.5.2 가 Latest 로 남아 있었던 적이 있다.

GitHub-hosted CI(GitHub Actions)는 쓰지 않는다. 테스트 게이트와 wheel·sdist 빌드는 메인테이너의
리눅스 러너에서 `scripts/linux_lane.sh` 가 돌리고, 페이지를 내는 기계는 아무것도 빌드하지 않고
레인이 만든 것을 붙인다.

레인이 통과했다는 것은 한 번의 실행이 exit 0 으로 끝났거나, 같은 커밋에서 `--rife-unmeasured "<이유>"`
실행(exit 3, RIFE 를 재지 못했다고 끝에 적는다)과 `--rife-only` 실행(exit 0)이 둘 다 끝났다는
뜻이다. RIFE 는 rife-ncnn-vulkan 을 돌릴 수 있는 Vulkan 드라이버가 있어야 잴 수 있다 — WSL1 의 Mesa
lavapipe 에서는 첫 업로드에서 죽는다 — 그래서 그런 호스트에서는 RIFE 를 떼어 내되 조용히 건너뛰지는
않는다.

실제 rife-ncnn-vulkan 을 돌리는 시험은 `real_rife` 표시를 단다(`pyproject.toml` 이 등록하고,
`tests/conftest.py` 가 RIFE 를 못 찾으면 그 표시가 붙은 시험을 건너뛴다). `--rife-unmeasured` 는
RIFE 가 닿는 곳이 하나도 없는 상태로 스위트 전체를 돌려 그 시험들이 이 이유로 건너뛰어지고,
`--rife-only` 는 `sprite-gen rife install`(그 확인 프레임)과 스위트 전체에서 `real_rife` 가 붙은
시험(`pytest -m real_rife`)을 돌린다 — 하나라도 건너뛰거나, 표시된 시험이 없으면 실패다. 그래서 새
실제-RIFE 시험은 표시만 붙이면 `--rife-only` 가 저절로 걷는다.

표시 없이 RIFE 가 없을 때 건너뛰거나 모이지 않는 시험은 두 실행 어느 쪽도 재지 않는다. 그래서
`--rife-unmeasured` 는 시험을 `pytest --rife-unmeasured` 로 돌리고, 그 실행에서 건너뛴 것마다 — skip
표시로든, fixture 나 시험 본문에서든, 파일째 import 에서든 — `tests/conftest.py` 가 `real_rife` 표시를
본다. 표시가 없고, 같은 파일의 `NOT_RIFE_SKIPS` 가 RIFE 가 아닌 이유(레인이 주지 않는 입력)로
건너뛴다고 이름 대지도 않았으면, 그 결과를 실패로 바꿔 시험의 이름을 댄다. RIFE 가 없으면 아예 모이지
않는 시험은 그 실행에 보이지 않으므로, `tests/release/test_lane_rife_collection.py` 가 스위트를 RIFE 가
있는 척하는 대역을 둔 채 한 번, 없는 채 한 번 모아, 모이는지나 skip 표시의 판정이 갈리는 시험마다
표시를 요구하고 없으면 그 이름을 대고 실패한다.

통과는 커밋 하나의 것이다. 어떤 커밋에서 잰 레인 — 한 번의 exit 0 이든, `--rife-only` 의 exit 0 이든 —
은 다른 커밋의 통과가 아니다. 이전 릴리즈나 부모 커밋의 통과를 다음 커밋으로 옮겨 적지 않고, 머지할
커밋과 태그 커밋을 각각 그 SHA 의 깨끗한 클론에서 잰다. `--rife-unmeasured` 로 잰 커밋은 같은 SHA 의
`--rife-only` 가 exit 0 으로 끝나기 전까지 레인을 통과하지 않은 것이다. 패키지도 그 SHA 의 것이다:
`SOURCE_SHA` 가 태그 커밋이 아닌 디렉터리는 그 릴리즈에 쓰지 못한다(아래 5).

## 한 묶음

1. **릴리즈 커밋** — `CHANGELOG.md` 의 `## Unreleased (vX.Y.Z)` 절을 `## vX.Y.Z - <제목>` 으로
   바꾸고, `pyproject.toml` 의 `version` 과 `SKILL.md` 의 `version:` 을 그 버전으로 맞춘다
   (둘의 일치는 `tests/packaging/test_version_ssot.py` 가 강제한다).
2. **PR 로 `main` 머지** — `main` 은 보호 브랜치라 직접 push 가 거부된다. 머지할 커밋에서 리눅스
   레인이 통과해야 한다(위 정의) — 러너에서 그 커밋의 깨끗한 클론으로 `scripts/linux_lane.sh`.
3. **태그 push** — 코드네임 접두사 없는 `vX.Y.Z` 로.

   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

4. **러너에서 태그 커밋을 패키징한다** — 태그가 가리키는 커밋의 깨끗한 클론에서, 체크아웃 밖의 빈
   디렉터리로.

   ```bash
   scripts/linux_lane.sh --artifact <빈 디렉터리>
   ```

   게이트를 통과한 뒤에만 wheel·sdist·`SHA256SUMS`·`SOURCE_SHA`(그 커밋) 네 파일을 쓴다
   (`--rife-unmeasured` 와 함께 쓰면 같은 커밋의 `--rife-only` 도 통과해야 레인이 통과한 것이다).
   그 디렉터리를 페이지를 낼 기계로 가져온다.
5. **페이지를 낸다** — 태그가 있는 체크아웃에서.

   ```bash
   .venv/bin/python scripts/release_publish.py --tag vX.Y.Z --prebuilt <가져온 디렉터리>
   ```

   `SOURCE_SHA` 가 태그 커밋이고 `SHA256SUMS` 가 두 아카이브와 맞을 때만 붙인다. 그 버전의
   CHANGELOG 절을 본문으로, 절이 이름 댄 GIF 를 자산으로 함께 붙여 릴리즈를 생성한다.
6. **확인은 `gh release view`** — 태그를 push 한 것으로 끝이 아니다. 이게 성공해야 릴리즈가 끝난 것이다.

   ```bash
   gh release view vX.Y.Z --repo aldegad/sprite-gen
   ```

## 이미 있는 릴리즈는 건드리지 않는다

이미 페이지가 있는 태그로 스크립트를 다시 돌리면 **아무것도 만들지 않고 아무것도 고치지 않고**
통과한다(`… already has a release page …; body and assets left untouched.`). 실패한 실행을 다시
돌려도 공개된 페이지를 덮어쓰지 않는다는 뜻이고, 반대로 **이미 공개된 페이지의 본문·자산을 이
스크립트로 고칠 수는 없다** — 고칠 일이 생기면 `gh release edit` / `gh release upload` 로 직접 한다.

## 쇼케이스 GIF 는 레포에 커밋된 것만 붙는다

스크립트가 자산으로 첨부하는 GIF 는 **두 조건을 모두 만족한 것뿐**이다:

- 그 버전의 CHANGELOG 절이 `docs/assets/<이름>.gif` 로 이름을 댄다 (본문의 맨 `<이름>.gif` 도
  `docs/assets/` 안에 그 파일이 있으면 같은 것으로 본다), **그리고**
- 그 파일이 레포에 커밋돼 있다.

즉 **레포 밖에서 만든 GIF 는 게이트가 붙이지 못한다.** v2.5.4 처럼 쇼케이스 클립을 레포에 커밋하지
않고 릴리즈 자산으로만 올리는 경우, 그 GIF 는 릴리즈가 생성된 뒤 손으로 올린다:

```bash
gh release upload vX.Y.Z <clip>.gif --repo aldegad/sprite-gen
```

본문에서 그 클립을 보여주려면 릴리즈 다운로드 URL
(`https://github.com/aldegad/sprite-gen/releases/download/vX.Y.Z/<clip>.gif`)로 임베드하고,
공개 후 그 URL 이 200 인지 확인한다. 반대로 CHANGELOG 절이 `docs/assets/…` 경로를 이름 댔는데 그
파일이 체크아웃에 없으면, 죽은 이미지가 달린 페이지를 내보내는 대신 **스크립트가 멈춘다.**

## 태그 없이 미리 돌려보기

`--dry-run` 으로 같은 판정을 돌린다. 태그도, 릴리즈도, 업로드도 없이 제목·본문·첨부 목록만 찍는다.
`--checkout` 의 `CHANGELOG.md` 를 읽으므로, 릴리즈 커밋을 준비하는 브랜치에서 본문을 미리 읽어 볼 수
있다. `--prebuilt` 없이 돌리면 그 자리에서 빌드하므로, 빌드하지 않는 기계에서는 `--skip-build`(GIF
만 붙는 목록)로 판정만 본다.

## 스크립트가 멈추는 경우

전부 "조용히 이상한 페이지" 대신 실패를 고른 지점이다.

- 태그에 해당하는 `## vX.Y.Z` 절이 `CHANGELOG.md` 에 없다 (있는 절 목록을 같이 찍는다).
- 그 절이 비어 있다.
- 절이 이름 댄 `docs/assets/…gif` 가 체크아웃에 없다.
- `--prebuilt` 디렉터리가 wheel 하나·sdist 하나·`SHA256SUMS`·`SOURCE_SHA` 말고 다른 것을 담고
  있거나, 아카이브 버전이 태그와 다르거나, `SOURCE_SHA` 가 태그 커밋이 아니거나, `SHA256SUMS` 가
  아카이브와 맞지 않는다 — 다른 커밋의 빌드나 낡은 아카이브가 이번 릴리즈 자산으로 섞이는 것을 막는다.
- 그 자리에서 빌드할 때 빌드 디렉터리가 비어 있지 않다 — 같은 이유다.
- `gh` 가 "릴리즈가 있다/없다" 를 답하지 못했다 (토큰 없음, 네트워크 끊김, 5xx). **모르는 답은
  '없음' 으로 읽지 않는다** — 그대로 멈춘다.

## `release not found` 는 "레포 없음" 이기도 하다

`gh release view` 는 **없는 레포·권한 없는 레포**에 대고 물어도 똑같이 `release not found` 로 답한다.
"릴리즈가 아직 없다" 와 "레포 이름을 잘못 썼다" 가 같은 문장인 셈이다. 스크립트의 기본 `--repo` 는
`aldegad/sprite-gen` 이라 보통은 이 모호함에 도달하지 않고, 도달하더라도 뒤따르는 생성이 크게
실패한다. `--repo` 를 손으로 줄 때만 주의하면 된다 — 오타가 "아직 릴리즈가 없네" 로 읽힌다. `gh` 의
입자도 한계이지 스크립트가 고칠 수 있는 것이 아니다.

## Related

- [docs/README.md](README.md) — documentation index
- `scripts/linux_lane.sh` — the test gates and the release build, on the maintainer's Linux runner
