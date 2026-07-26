// SPDX-License-Identifier: Apache-2.0
// curator/boot.js — 부트스트랩 — 런 로드 → 전 도메인 초기 렌더 (항상 마지막 로드)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

async function boot() {
  try {
    const res = await fetch("/api/run");
    run = await res.json();
    if (run.error) {
      if (run.busy) {
        // 재추출 진행 중 — 정식 로딩 패널(진행 바·현재 행)을 띄우고 끝나면 자동 재시도.
        // busy 응답에는 run.lang 이 실려 온다 (첫 탭도 서버 --lang 로 렌더).
        lang = new URLSearchParams(location.search).get("lang") || run.lang || lang || "en";
        document.documentElement.lang = lang;
        renderLoadingPanel();
        startOpProgressWatch(() => window.location.reload());
        return;
      }
      throw new Error(run.error);
    }
  } catch (e) {
    document.getElementById("states").innerHTML =
      `<div class="fatal">${t("runLoadFail")}\n${e.message}</div>`;
    return;
  }
  // initial language: ?lang= (set by the toggle) overrides the server --lang
  lang = new URLSearchParams(location.search).get("lang") || run.lang || "en";
  document.documentElement.lang = lang;
  // 자가치유 보고 (실시간 계약): 서버가 이번 로드에서 stale 프레임을 재계산했으면
  // 조용히 알려만 준다 — '재추출' 버튼/개념은 없다. raw 가 없어 못 고친 행도 관측.
  // (표시는 boot 끝의 최종 setStatus 자리에서 — 중간 상태 메시지에 덮이지 않게)
  const healParts = [];
  if (run.heal) {
    if (run.heal.healed && run.heal.healed.length) healParts.push(`엔진 갱신 반영: ${run.heal.healed.join(", ")}`);
    if (run.heal.kept_stale && run.heal.kept_stale.length) healParts.push(`원본 없음(구엔진 유지): ${run.heal.kept_stale.join(", ")}`);
    if (run.heal.failed && run.heal.failed.length) healParts.push(`재계산 실패(이전 세대 유지): ${run.heal.failed.join(", ")}`);
  }
  // pixel-unfake twin state must resolve BEFORE first render (frameUrl reads it):
  // per-state truth = states.<state>.pixel_unfake override > run-wide default > on.
  seedUnfakeState(run);
  // 격자 오버레이는 모든 줄이 가진다 — "격자 가능 줄" 이라는 집합 자체를 두지 않는다.
  // 집합이 존재하면 언젠가 필터가 다시 붙는다 (콩콩이 R3 실증: 5줄 mutant 로 병 복원).
  // 스위치가 없으면 되살릴 knob 도 없다.
  gridStates = {};
  applyStaticLang();
  const cmpBtn = document.getElementById("compare-open");
  if (cmpBtn) {
    cmpBtn.textContent = t("cmpOpen");
    cmpBtn.title = t("tCmpOpen");
    cmpBtn.addEventListener("click", openCompare);
  }
  document.getElementById("character").textContent = `${run.characterId} · ${run.cell.width}×${run.cell.height}`;
  if (run.iso) gridToggle.hidden = false;
  {
    // 퍼펙 전체 토글 — 항상 보인다 (조건 게이트 = 컨트롤 숨김 버그 클래스).
    // 줄들이 섞이면(일부 on/off) indeterminate 로 표시한다 (syncUnfakeControls).
    const unfakeWrap = document.getElementById("unfake-wrap");
    const unfakeCheck = document.getElementById("unfake-apply");
    unfakeWrap.hidden = false;
    unfakeCheck.addEventListener("change", () => {
      const on = unfakeCheck.checked;
      for (const s of run.states) unfakeStates[s.name] = on;
      syncUnfakeControls();
      refreshVariantImages();
      scheduleSave();
    });
  }
  // 픽셀 격자 전체 토글 — 표시 전용 오버레이 (굽기와 무관), 줄별 체크박스와 같은 truth.
  // 항상 보인다: 격자는 스크립트가 결정론으로 재는 것이라 "모름"이 없다.
  const pxWrap = document.getElementById("pxgrid-wrap");
  const pxCheck = document.getElementById("pxgrid-check");
  pxWrap.hidden = false;
  pxCheck.addEventListener("change", () => {
    const on = pxCheck.checked;
    for (const s of run.states) gridStates[s.name] = on;
    syncGridControls();
    sizePxGrids();
  });
  seedEntries();
  if (run.directionGroups && run.directionGroups.length) {
    anchorStates = new Set(run.directionGroups.map((g) => g.anchor).filter(Boolean));
    // 생성 방향(미러 제외) + 사용자 앵커 프레임 지정 시드 — 앵커 배지/버튼의 입력
    anchorDirections = run.directionGroups.filter((g) => !g.mirrorOf).map((g) => g.direction);
    anchorPicks = {};
    for (const [direction, pick] of Object.entries((run.curation && run.curation.anchors) || {})) {
      if (pick && typeof pick.state === "string" && Number.isInteger(Number(pick.index))) {
        // `stale` 을 **반드시 왕복**시킨다: 이걸 버리면 무관한 편집의 autosave 가 낡은 핀을
        // 현재 세대로 세탁해 오류 배너가 사라진다 (젯비 5차 기각 실측). 서버 writer 도
        // fail-safe 로 막지만, 뷰가 진실을 들고 있어야 배지/버튼 상태가 맞는다.
        anchorPicks[direction] = { state: pick.state, index: Number(pick.index) };
        if (pick.stale) {
          anchorPicks[direction].stale = true;
          anchorPicks[direction].stale_reason = pick.stale_reason || "regenerated";
        }
      }
    }
    // 해석 실패(지정이 사라진 프레임을 가리킴 등)는 조용히 넘기지 않는다 — 이 상태로
    // 생성하면 엔진이 fail-loud 하므로 사용자가 지금 알아야 한다. 단 `anchorPending`
    // (그 방향 앵커 행을 아직 안 뽑았다)은 **정상 작업 구간**이라 오류로 칠하지 않는다 —
    // 그러면 생성 중인 모든 런에 빨간 배너가 뜬다 (젯비 검증 2026-07-25 파생 증상 2).
    const broken = run.directionGroups.filter((g) => g.anchorError && !g.anchorPending);
    if (broken.length) {
      healParts.push(...broken.map((g) => STR[lang].anchorError(g.direction, g.anchorError)));
    }
  }
  await seedTreeProgress();
  renderPipelineTree();
  setInterval(pollTreeProgress, 3000);
  if (run.baseUrl) renderBaseRow();
  // 방향 계약 런: 방향별 그룹(앵커 우선) + 미러 방향(생성 생략) 스트립으로 렌더.
  // 계약 없는 런은 기존 flat 순서 그대로.
  if (run.directionGroups && run.directionGroups.length) {
    const byName = new Map(run.states.map((s) => [s.name, s]));
    const rendered = new Set();
    for (const group of run.directionGroups) {
      const headEl = document.createElement("div");
      headEl.className = "dir-head" + (group.mirrorOf ? " dir-mirror" : "");
      headEl.textContent = group.mirrorOf
        ? STR[lang].dirMirrorLabel(group.direction, group.mirrorOf)
        : STR[lang].dirGroupLabel(group.direction);
      document.getElementById("states").appendChild(headEl);
      for (const name of group.states) {
        const st = byName.get(name);
        if (st) { renderState(st); rendered.add(name); }
      }
    }
    // 방향 접두사에 안 걸린 잔여 상태는 끝에 그대로 (숨기지 않는다)
    for (const state of run.states) if (!rendered.has(state.name)) renderState(state);
  } else {
    for (const state of run.states) renderState(state);
  }
  syncUnfakeControls();
  syncGridControls();
  refreshVariantImages();
  // 레거시 테이크 방식 호흡의 자가 이전 (시퀀스 위상 프레임 → 사이드카 레이어)
  {
    const migrated = run.states.filter((s) => migrateLegacyBreathe(s.name));
    if (migrated.length) {
      for (const s of migrated) rebuildState(s.name);
      scheduleSave();
      setStatus(`호흡 레이어로 자동 이전: ${migrated.map((s) => s.name).join(", ")}`);
    }
  }
  // 세대 불일치로 서버가 이번 로드에서 무효화한 행 알림 — 조용한 소실 금지.
  // 백업 파일명을 함께 보여줘 수동 복원 경로를 남긴다 (load_curation_report 계약).
  if (run.curationDropped && run.curationDropped.length) {
    const note = document.createElement("div");
    note.id = "curation-dropped-note";
    note.textContent = STR[lang].curationDropped(run.curationDropped, run.curationBackup);
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "ghost";
    dismiss.textContent = "✕";
    dismiss.addEventListener("click", () => note.remove());
    note.appendChild(dismiss);
    document.body.prepend(note);
  }
  await renderFinalAtlas(run.atlas);
  // 힌트바를 우측 본문 컬럼 끝으로 이동 — 좌측 스플릿이 페이지 바닥까지 유지되게
  document.getElementById("states").appendChild(document.getElementById("hintbar"));
  // 표시 샘플링 판정은 기하가 바뀔 때마다 다시 답해야 한다 (창 크기·패널 폭이
  // 바뀌면 같은 이미지가 확대에서 축소로 넘어간다) — sizePxGrids 가 그 갱신 지점.
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(sizePxGrids, 120);
  });
  installPixelScalingLoadHook();
  syncPixelScaling();
  if (healParts.length) {
    const hadFailure = (run.heal && run.heal.failed && run.heal.failed.length)
      || (run.directionGroups || []).some((g) => g.anchorError && !g.anchorPending);
    setStatus(healParts.join(" · "), hadFailure ? "err" : "ok");
  } else {
    setStatus(run.curation && Object.keys(run.curation.states || {}).length ? t("loaded") : t("ready"));
  }
}

boot();
