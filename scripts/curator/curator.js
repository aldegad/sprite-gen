// SPDX-License-Identifier: Apache-2.0
// sprite-gen curation webview — vanilla JS, no build step.
//
// Edits never touch the source frame PNGs. They mutate an in-memory model that
// mirrors curation.json and is auto-saved (debounced) via POST /api/curation.
// rotate is degrees, counter-clockwise positive (matches PIL bake). The preview
// (CSS + canvas) negates it because screen/CSS/canvas positive rotation is
// clockwise, so what you see is what compose_sprite_atlas.py will bake.

const IDENTITY = () => ({ rotate: 0, scale: 1, dx: 0, dy: 0, shx: 0, shy: 0, flipX: 0 });
const SCALE_MIN = 0.2;
const SCALE_MAX = 3;
const DRAG_THRESHOLD = 4;

// forward 2x2 matrix (Rotate · Shear · Scale · FlipX); mirrors curation.py transform_matrix
function matrixOf(t) {
  const rr = (t.rotate * Math.PI) / 180;
  const c = Math.cos(rr);
  const sn = Math.sin(rr);
  const s = t.scale;
  const shx = t.shx || 0;
  const shy = t.shy || 0;
  let m00 = s * (c + sn * shy);
  const m01 = s * (c * shx + sn);
  let m10 = s * (-sn + c * shy);
  const m11 = s * (c - sn * shx);
  // (Alex 2026-05-28) flipX = horizontal mirror (image-gen 결과가 좌우 반대로
  // 나올 때). diag(-1, 1) 을 matrix 마지막에 곱 → column-0 부호 반전.
  if (t.flipX) {
    m00 = -m00;
    m10 = -m10;
  }
  return { m00, m01, m10, m11 };
}

// --- i18n (en / ko; initial language from server --lang, toggle reloads) ----
const STR = {
  en: {
    title: "curation", compose: "Download atlas", export: "Download PNGs", exportGif: "Download GIFs",
    groundGrid: "Ground grid", langOther: "한국어",
    ppApply: "Pixel-perfect (all)", baseNote: "identity reference — not baked",
    ppState: "Pixel-perfect",
    tPpState: "toggle pixel-perfect for THIS row only — what it displays and what compose bakes",
    pxGrid: "Pixel grid", pxGridAll: "Pixel grid (all)",
    tGridState: "grid overlay for THIS row — output pixel raster on the pixel-perfect view; on the original view the FINAL correspondence grid (green): one cell = one result pixel (display only)",
    refsLabel: "generated from", ref_anchor: "direction anchor", ref_basis: "basis row", ref_guide: "layout guide", tPxGrid: "toggle the pixel-grid overlay for ALL rows at once (display only; each row has its own checkbox)",
    tPpApply: "toggle pixel-perfect for ALL rows at once (each row has its own checkbox)",
    frames: "frames", loop: "loop", nonLoop: "non-loop", preview: "Preview",
    excluded: "✗ exclude", selected: "✓ selected", extractFail: "⚠ extraction incomplete",
    editing: "editing…", saved: "saved", saveFail: "save failed: ",
    saveLostBanner: "EDITS ARE NOT SAVING — the server may have restarted. Reload this page (your unsaved edits will be lost, but new edits will save again).",
    rowGif: "GIF", tRowGif: "download this row's current composed sequence as a GIF (4x nearest upscale for crisp viewing — pixel data unchanged)",
    rowBreathe: "Breathe", tRowBreathe: "deterministic idle breathing: opens the zoom view with a draggable chest line + live breathing preview — Generate bakes an exhale take (integer row-shift, zero AI) and re-extracts the full batch",
    breatheGo: "Generate", breatheAmp: "amp", breatheBusy: "baking breathe take + re-extracting the full batch…",
    breatheHint: "drag the line — everything above it sinks 1px on exhale",
    breatheDone: (s) => `${s}: breathe take added — reloading`, breatheFail: "breathe failed: ",
    rowTween: "Tween", tRowTween: "AI in-between: generate a mid frame between two frames of this row (codex/grok image gen on the server machine's CLI auth) — recorded as a take, then the FULL batch re-extracts. Click cards to pick the pair.",
    tweenFrom: "from", tweenTo: "to", tweenT: "t", tweenGo: "Generate",
    tweenBusy: "interpolating + re-extracting the full batch… (1-2 min)",
    tweenDone: (s) => `${s}: in-between added — reloading`,
    tweenFail: "interpolation failed: ",
    treeAtlas: "final atlas", atlasDoc: "manifest.json (runtime doc)",
    atlasPending: "not composed yet — created when you download the atlas",
    atlasStamp: (d) => `computed ${d}`, tTreeAtlas: "scroll to the final atlas section",
    rowGifDone: (s) => `${s}.gif downloaded`,
    baking: "computing…", composeDone: "atlas downloaded", composeFail: "download failed: ",
    exporting: "computing…", exportFail: "download failed: ",
    ready: "ready", loaded: "loaded existing curation", runLoadFail: "failed to load run:",
    tRotate: "rotate", tShear: "shear — horizontal = shx, vertical = shy", tReset: "reset transform", tFlipX: "flip horizontally",
    tReorder: "grab the title to drag — reorder, or move between sequence ⇄ pool. use 넣기/빼기 to toggle without dragging",
    tPlay: "play", tPause: "pause", tPrev: "step back", tNext: "step forward", tSpeed: "playback speed",
    zoneSeq: "Running sequence", zonePool: "Candidate pool — drag a cut up to add it", addToSeq: "add", removeFromSeq: "remove",
    tSelAdd: "add to the running sequence (move up)", tSelRemove: "remove to the candidate pool (move down)",
    tTitleCopy: "full name — select to copy (drag the title to reorder)",
    cellPx: "cell", tContentPx: "actual sprite pixels (transparent padding excluded)",
    tZoomOpen: "inspect large (double-click the image works too)",
    tZoomStage: "wheel/pinch = view zoom · drag = move · bottom-right magnifier = sprite scale",
    zoomClose: "✕", tZoomPrev: "previous frame", tZoomNext: "next frame",
    dirAnchorBadge: "direction anchor",
    tDirAnchorBadge: "canonical facing anchor for this direction — generated from the base; every other row of this direction derives identity from it",
    dirGroupLabel: (d) => `direction · ${d}`,
    dirMirrorLabel: (d, src) => `direction · ${d} — runtime mirror of ${src} (not generated)`,
    treeTitle: "generation structure",
    treePipeline: "pipeline",
    treeFiles: "files",
    treeBaseNote: "base — first identity truth",
    treeIdleRow: "idle row",
    treeAnchorNote: "anchor · single frame-0 crop",
    treeMirror: (d, src) => `${d} — runtime mirror of ${src} (not generated)`,
    treePending: "not generated yet",
    treeRawNote: "raw · awaiting extract",
    missingPending: "not generated",
    archiveChip: (n) => `archive ${n}`,
    tArchiveChip: "click to open — drop a card here to archive it",
    tArchiveBtn: "archive (remove even from the candidate pool)",
    tScaleScrub: "sprite scale — click arrows to step, drag the magnifier left/right",
    penTool: "pen", eraserTool: "eraser", pickTool: "eyedropper", undoEdit: "undo", redoEdit: "redo", clearEdits: "clear edits",
    selectTool: "select", tSelectTool: "marquee: drag to select an area (dashed box) - drag inside to MOVE it, Alt+drag to DUPLICATE, Esc to deselect",
    tUndoKeys: "undo (Cmd/Ctrl+Z)", tRedoKeys: "redo (Cmd/Ctrl+Shift+Z)",
    baseEditBtn: "edit", tBaseEdit: "pixel-edit the base image itself — saves into base-source (original backed up once as .orig); affects FUTURE generation, not already-extracted frames",
    baseEditSave: "save to base", baseEditFail: "base save failed: ",
    baseUnsaved: "editing base — press 'save to base' to bake into the file",
    baseEditSaved: (n) => `base updated (${n}px) — affects future generation`,
    tPick: "eyedropper — click a pixel to sample its color, then it switches to the pen so you can paint that exact color",
    archiveHint: "drag a card out to restore it into the sequence or pool",
    archModalTitle: (st, n) => `archive · ${st} (${n})`,
    restoreToSeq: "to sequence", restoreToPool: "to pool",
    missingRawWait: "generated · awaiting extract",
    treeRawFolder: "generated strip originals",
    treeFramesFolder: "extracted frames",
    treeAnchorsFolder: "direction anchors — single-frame crops",
    treeAtlasNote: "final atlas",
    treeFrameCount: (n) => `${n} frames`,
    treeAnchorOrigin: (d) => `idle row · ${d} anchor source`,
    reloadBanner: "run updated — click to reload the view",
    tDupBtn: "duplicate this frame — a new card with its own transform (bakes the same source image)",
    cloneBadge: (name) => `${name} copy`,
    tCloneBadge: "duplicated instance — reads the source frame's image; its transform/pixel edits are its own. The archive button removes the copy entirely.",
    curationDropped: (states, backup) =>
      `frames were regenerated — previous curation for ${states.join(", ")} no longer applies and was reset. ` +
      (backup ? `The old selections are preserved in ${backup}.` : ""),
    tTreeNode: "click to scroll to this row",
    hints: ["drag card header = reorder / move row", "drag pool→sequence to add", "hover a frame -> bottom-right magnifier = scale", "top handle = rotate", "click card = sequence ⇄ pool", "saved automatically"],
    exportDone: () => "PNGs downloaded",
    exportGifDone: () => "GIFs downloaded",
  },
  ko: {
    title: "큐레이션", compose: "아틀라스 다운로드", export: "PNG 다운로드", exportGif: "GIF 다운로드",
    groundGrid: "바닥 그리드", langOther: "EN",
    ppApply: "픽셀퍼펙트 전체", baseNote: "원본 베이스 (아이덴티티 참조 — 굽기와 무관)",
    ppState: "픽셀퍼펙트",
    tPpState: "이 줄만 픽셀퍼펙트 켜기/끄기 — 표시와 굽기가 같이 바뀐다",
    pxGrid: "픽셀 격자", pxGridAll: "픽셀 격자 전체",
    tGridState: "이 줄 격자 오버레이 — 픽셀퍼펙트 뷰에선 출력 픽셀 눈금, 원본 뷰에선 최종 대응 격자(초록, 칸 하나 = 결과 픽셀 하나) (표시 전용)",
    refsLabel: "생성 재료", ref_anchor: "방향 앵커", ref_basis: "basis row", ref_guide: "레이아웃 가이드", tPxGrid: "모든 줄의 격자 오버레이를 한번에 켜기/끄기 (표시 전용; 줄별 체크박스는 각 줄에)",
    tPpApply: "모든 줄의 픽셀퍼펙트를 한번에 켜기/끄기 (줄별 체크박스는 각 줄에)",
    frames: "프레임", loop: "루프", nonLoop: "비루프", preview: "프리뷰",
    excluded: "✗ 제외", selected: "✓ 선택됨", extractFail: "⚠ 추출 미완료",
    editing: "편집 중…", saved: "저장됨", saveFail: "저장 실패: ",
    saveLostBanner: "편집이 저장되지 않고 있습니다 — 서버가 재기동됐을 수 있어요. 이 페이지를 새로고침하세요 (미저장 편집은 유실되지만, 이후 편집은 다시 저장됩니다).",
    rowGif: "GIF", tRowGif: "이 줄의 현재 합성 시퀀스를 GIF 로 다운로드 — 선명하게 보이도록 4배 니어리스트로 굽는다 (픽셀 데이터는 그대로)",
    rowBreathe: "호흡", tRowBreathe: "결정론 숨쉬기: 줌 뷰가 열리고 드래그 가능한 가슴선 + 실시간 호흡 미리보기 — 생성을 누르면 exhale 테이크(정수 행 시프트, AI 0%)를 굽고 전체 배치 재추출",
    breatheGo: "생성", breatheAmp: "진폭", breatheBusy: "호흡 테이크 굽는 중 + 전체 배치 재추출…",
    breatheHint: "선을 드래그 — 선 위가 날숨에 1px 가라앉습니다",
    breatheDone: (s) => `${s}: 호흡 테이크 추가됨 — 새로고침합니다`, breatheFail: "호흡 생성 실패: ",
    rowTween: "보간", tRowTween: "AI 중간 프레임: 이 줄의 두 프레임 사이를 생성형(codex/grok — 서버 머신의 CLI 인증 사용)으로 그려 테이크로 기록 — 이후 전체 배치 재추출. 카드를 클릭해 쌍을 고르세요.",
    tweenFrom: "시작", tweenTo: "끝", tweenT: "t", tweenGo: "생성",
    tweenBusy: "보간 + 전체 배치 재추출 중… (1~2분)",
    tweenDone: (s) => `${s}: 중간 프레임 추가됨 — 새로고침합니다`,
    tweenFail: "보간 실패: ",
    treeAtlas: "최종 아틀라스", atlasDoc: "manifest.json (런타임 문서)",
    atlasPending: "아직 합성 전 — 아틀라스 다운로드를 누르면 계산돼 생성됩니다",
    atlasStamp: (d) => `${d} 계산본`, tTreeAtlas: "최종 아틀라스 섹션으로 스크롤",
    rowGifDone: (s) => `${s}.gif 다운로드 완료`,
    baking: "계산 중…", composeDone: "아틀라스 다운로드 완료", composeFail: "다운로드 실패: ",
    exporting: "계산 중…", exportFail: "다운로드 실패: ",
    ready: "준비됨", loaded: "기존 큐레이션 로드됨", runLoadFail: "run 로드 실패:",
    tRotate: "회전", tShear: "기울이기 — 가로=shx, 세로=shy", tReset: "보정 초기화", tFlipX: "좌우 반전",
    tReorder: "타이틀을 잡고 드래그 = 순서변경 / 시퀀스↔풀 이동. 드래그 없이 넣고 뺄 땐 넣기·빼기 버튼",
    tPlay: "재생", tPause: "일시정지", tPrev: "이전 프레임", tNext: "다음 프레임", tSpeed: "재생 속도",
    zoneSeq: "달리기 시퀀스", zonePool: "후보 풀 — 마음에 드는 컷을 위로 끌어 추가", addToSeq: "넣기", removeFromSeq: "빼기",
    tSelAdd: "시퀀스에 넣기 (위로 이동)", tSelRemove: "후보 풀로 빼기 (아래로 이동)",
    tTitleCopy: "풀네임 — 드래그해서 복사 (제목 자체를 잡으면 순서변경)",
    cellPx: "셀", tContentPx: "실제 스프라이트 픽셀 (투명 여백 제외)",
    tZoomOpen: "크게 보기 (이미지 더블클릭도 됨)",
    tZoomStage: "휠/핀치 = 화면 확대 · 드래그 = 이동 · 우하단 돋보기 = 스프라이트 크기",
    zoomClose: "✕", tZoomPrev: "이전 프레임", tZoomNext: "다음 프레임",
    dirAnchorBadge: "방향 앵커",
    tDirAnchorBadge: "이 방향의 canonical 앵커 — base 에서 생성되고, 이 방향의 다른 모든 행이 여기서 identity 를 가져온다",
    dirGroupLabel: (d) => `방향 · ${d}`,
    dirMirrorLabel: (d, src) => `방향 · ${d} — ${src} 런타임 미러 (생성 없음)`,
    treeTitle: "생성 구조",
    treePipeline: "파이프라인",
    treeFiles: "파일",
    treeBaseNote: "base — 최초 identity",
    treeIdleRow: "idle 행",
    treeAnchorNote: "앵커 · frame-0 크롭 1장",
    treeMirror: (d, src) => `${d} — ${src} 런타임 미러 (생성 없음)`,
    treePending: "미생성",
    treeRawNote: "raw 생성됨 · 추출 전",
    missingPending: "미생성",
    archiveChip: (n) => `보관함 ${n}`,
    tArchiveChip: "클릭 = 열기 · 카드를 여기로 끌어오면 보관",
    tArchiveBtn: "보관함으로 (후보 풀에서도 제외)",
    tScaleScrub: "스프라이트 크기 — 화살표 클릭 = 단계 조절, 돋보기 좌우 드래그 = 연속 조절",
    penTool: "연필", eraserTool: "지우개", pickTool: "스포이드", undoEdit: "되돌리기", redoEdit: "재실행", clearEdits: "편집 비우기",
    selectTool: "선택", tSelectTool: "영역 선택: 드래그로 점선 박스를 만들고 — 안쪽을 드래그하면 이동, Alt+드래그는 복제, Esc 는 선택 해제",
    tUndoKeys: "되돌리기 (Cmd/Ctrl+Z)", tRedoKeys: "재실행 (Cmd/Ctrl+Shift+Z)",
    baseEditBtn: "편집", tBaseEdit: "베이스 이미지 자체를 픽셀 편집 — base-source 파일에 저장 (원본은 .orig 로 1회 백업). 이미 뽑힌 프레임은 안 변하고 이후 생성에 반영됩니다",
    baseEditSave: "베이스에 저장", baseEditFail: "베이스 저장 실패: ",
    baseUnsaved: "베이스 편집 중 — '베이스에 저장' 을 눌러야 파일에 반영됩니다",
    baseEditSaved: (n) => `베이스 갱신됨 (${n}px) — 이후 생성에 반영`,
    tPick: "스포이드 — 픽셀을 클릭하면 그 색을 집어 연필로 전환, 똑같은 색으로 바로 찍을 수 있어",
    archiveHint: "카드를 끌어내 시퀀스/후보로 복구",
    archModalTitle: (st, n) => `보관함 · ${st} (${n})`,
    restoreToSeq: "시퀀스로", restoreToPool: "후보로",
    missingRawWait: "생성됨 · 추출 대기",
    treeRawFolder: "생성 스트립 원본",
    treeFramesFolder: "추출 프레임",
    treeAnchorsFolder: "방향 앵커 — 1장 크롭",
    treeAtlasNote: "최종 아틀라스",
    treeFrameCount: (n) => `${n}프레임`,
    treeAnchorOrigin: (d) => `idle 행 · ${d} 앵커 원천`,
    reloadBanner: "런이 갱신됐어 — 클릭해서 새로고침",
    tDupBtn: "이 프레임 복제 — 자기 변형을 따로 갖는 새 카드 (같은 원본 이미지를 굽는다)",
    cloneBadge: (name) => `${name} 복제`,
    tCloneBadge: "복제 인스턴스 — 원본 프레임 이미지를 읽고, 변형/픽셀편집은 이 카드 것. 보관 버튼은 복제를 완전히 제거한다.",
    curationDropped: (states, backup) =>
      `프레임이 재생성돼 ${states.join(", ")} 의 이전 큐레이션이 더 이상 맞지 않아 초기화됐어. ` +
      (backup ? `이전 선택은 ${backup} 에 백업돼 있어.` : ""),
    tTreeNode: "클릭하면 해당 줄로 이동",
    hints: ["타이틀 드래그 = 순서변경 / 시퀀스↔풀 이동", "넣기·빼기 버튼으로 토글 (클릭만으론 안 빠짐)", "제목 호버 = 풀네임 복사", "우하단 돋보기 = 크기 · 상단 핸들 = 회전", "복제 = 헤더 ⧉ 버튼", "자동 저장"],
    exportDone: () => "PNG 다운로드 완료",
    exportGifDone: () => "GIF 다운로드 완료",
  },
};
let lang = "en";
function t(key) {
  const v = (STR[lang] && STR[lang][key]) ?? STR.en[key];
  return v;
}

// --- 공통 툴팁 컴포넌트 (네이티브 title 대체) -------------------------------
// 단일 팝오버를 body 에 두고 document 위임으로 재사용한다. 대상은 `data-tip`
// 속성으로 opt-in (title= 대신). `data-tip-copy` 가 있으면 팝오버가 인터랙티브
// 해져(pointer-events auto + user-select text) 마우스를 팝오버 안으로 옮겨 텍스트를
// 드래그·복사할 수 있다 — 에이전트 협업 시 프레임 풀네임을 그대로 집어가게 하려는 것.
// 네이티브 title 은 커스텀과 겹쳐 이중 표시되므로 쓰지 않는다.
const Tooltip = (() => {
  let el = null;
  let copyEl = null;
  let anchor = null;
  let hideTimer = 0;

  function ensure() {
    if (el) return;
    el = document.createElement("div");
    el.id = "sg-tip";
    el.setAttribute("role", "tooltip");
    document.body.appendChild(el);
    // 팝오버 자체에 마우스가 들어오면 유지(복사 가능), 나가면 숨김
    el.addEventListener("pointerenter", () => clearTimeout(hideTimer));
    el.addEventListener("pointerleave", hide);
  }

  function position(target) {
    const r = target.getBoundingClientRect();
    el.style.maxWidth = Math.min(360, window.innerWidth - 16) + "px";
    // 먼저 보이게 해 크기 측정, 그 다음 위치 클램프
    el.style.visibility = "hidden";
    el.classList.add("open");
    const tr = el.getBoundingClientRect();
    let top = r.bottom + 6;
    if (top + tr.height > window.innerHeight - 6) top = Math.max(6, r.top - tr.height - 6);
    let left = r.left;
    if (left + tr.width > window.innerWidth - 6) left = Math.max(6, window.innerWidth - 6 - tr.width);
    el.style.top = Math.round(top) + "px";
    el.style.left = Math.round(left) + "px";
    el.style.visibility = "";
  }

  function show(target) {
    const text = target.getAttribute("data-tip");
    if (!text) return;
    ensure();
    clearTimeout(hideTimer);
    anchor = target;
    const copyable = target.hasAttribute("data-tip-copy");
    el.className = copyable ? "open copyable" : "open";
    if (copyable) {
      el.innerHTML = "";
      copyEl = document.createElement("span");
      copyEl.className = "sg-tip-text";
      copyEl.textContent = text;
      el.appendChild(copyEl);
    } else {
      el.textContent = text;
      copyEl = null;
    }
    position(target);
  }

  function hide() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (el) el.classList.remove("open");
      anchor = null;
    }, 80);
  }

  // 위임: 어떤 요소든 data-tip 이 있으면 자동 적용 (공통 컴포넌트)
  document.addEventListener("pointerover", (e) => {
    const target = e.target.closest?.("[data-tip]");
    if (target && target !== anchor) show(target);
  });
  document.addEventListener("pointerout", (e) => {
    const target = e.target.closest?.("[data-tip]");
    // 팝오버(복사가능)로 이동 중이면 유지 — pointerleave 가 최종 숨김을 담당
    if (target && target === anchor && !e.relatedTarget?.closest?.("#sg-tip")) hide();
  });
  // 드래그/클릭이 시작되면 즉시 숨김 (제목 드래그 = 순서변경과 충돌 방지)
  document.addEventListener("pointerdown", (e) => {
    if (!e.target.closest?.("#sg-tip")) { if (el) el.classList.remove("open"); anchor = null; }
  }, true);
  window.addEventListener("scroll", () => { if (el) el.classList.remove("open"); anchor = null; }, true);

  return { show, hide };
})();

// 넣기/빼기 SVG (이모지·유니코드 마크 금지 — 라인 아이콘). 시퀀스=위, 풀=아래라
// 넣기=위 화살표, 빼기=아래 화살표로 공간적으로 직관화.
const SEL_ICON = {
  add: '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M8 12.5V4.2M4.6 7.4 8 4l3.4 3.4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  remove: '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M8 3.5v8.3M4.6 8.6 8 12l3.4-3.4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

let run = null; // /api/run snapshot
let entries = {}; // { stateName: { order: [idx], sel: Set<idx>, transforms: { idx: {..} } } }
const imageCache = new Map();
const previews = {}; // stateName -> { playing, speed, cursor } preview transport state

// --- pixel-perfect variant (fit.pixel_perfect runs save a .plain.png twin) --
// Per-STATE toggles: each row with a twin gets its own on/off (what that row
// displays AND bakes, persisted as curation.json states.<state>.pixel_perfect).
// The header checkbox is a toggle-ALL over the same per-state truth.
let ppAvailable = false;       // any state has a plain (pre-pixel-perfect) twin
let ppTwinStates = new Set();  // states that actually saved a twin
let ppStates = {};             // stateName -> bool (true = pixel-perfect variant)

// --- pixel-grid overlay (display only, never persisted) ---------------------
// Same per-state + toggle-all shape as pixel-perfect: each grid-capable row has
// its own checkbox, the header checkbox sets all rows at once.
let gridCapableStates = new Set(); // states with a known/measured snap grid
let gridStates = {};               // stateName -> bool (overlay shown)
let anchorStates = new Set();      // direction-anchor states (directionGroups runs)

// --- 픽셀 편집 (사이드카 pixels — 원본 PNG 불변) -----------------------------
let pixelEdit = null; // 모달 편집 세션: {state, idx, tool: 'pen'|'eraser', color, journal: []}

function getPixelOps(stateName, idx) {
  const e = entries[stateName];
  if (!e || !e.pixels) return null;
  const ops = e.pixels[idx];
  return ops && Object.keys(ops).length ? ops : null;
}

function ppOn(stateName) {
  return ppStates[stateName] !== false;
}

function frameUrl(stateName, frame) {
  return !ppOn(stateName) && frame.plainUrl ? frame.plainUrl : frame.url;
}

// 복제 인스턴스 (entries[state].clones = {복제idx: 원본idx}) 인식 프레임 조회.
// 복제 카드는 원본의 frame 객체(이미지 URL/크기)를 빌리되 자기 인덱스로 표시된다.
// 서버/스키마 계약: 파일은 원본을 읽고, 변형/픽셀편집/순서는 복제 인덱스 소유.
// 프레임의 유니크 표시명: 테이크 라벨(blink#2) 우선, 없으면 #인덱스.
// 라벨은 원본(primary) 프레임에선 비어 있어 유니크하지 않지만, 인덱스는 줄 안에서
// 항상 유니크하므로 이 조합이 카드 표시명의 SSoT 다 (복제 배지도 이걸 가리킨다).
function frameDisplayName(stateName, idx) {
  const st = run.states.find((s) => s.name === stateName);
  const f = st && st.frames.find((x) => x.index === idx && x.clone === undefined);
  return f && f.label ? f.label : `#${idx}`;
}

function cloneSrc(stateName, idx) {
  const e = entries[stateName];
  const src = e && e.clones ? e.clones[idx] : undefined;
  return src === undefined ? null : src;
}

function frameOf(stateName, idx) {
  if (stateName === BASE_STATE) {
    if (!baseView) return null;
    return { index: 0, present: true, url: baseView.url, plainUrl: baseView.rawUrl,
             label: "base", contentSize: [baseView.cols, baseView.rows] };
  }
  const st = run.states.find((s) => s.name === stateName);
  if (!st) return null;
  const src = cloneSrc(stateName, idx);
  const f = st.frames.find((fr) => fr.index === (src === null ? idx : src));
  if (!f) return null;
  if (src === null) return f;
  return { ...f, index: idx, clone: src, label: null };
}

// fit.pixel_perfect 런에서 픽셀 변형을 표시 중인 줄의 논리 격자 스케일 (아니면 null).
// 굽기(curation.apply_transform snap_scale)와 같은 조건 — 프리뷰가 굽기를 거울처럼 따른다.
function snapScaleFor(stateName) {
  if (stateName === BASE_STATE) return 1; // 베이스 논리 공간 = 캔버스 1:1
  return run.fitPixelPerfect && run.pixelPerfect && run.pixelPerfect.scale && ppOn(stateName)
    ? run.pixelPerfect.scale
    : null;
}

function isIdentityTransform(t) {
  return !t.rotate && t.scale === 1 && !t.dx && !t.dy && !t.shx && !t.shy && !t.flipX;
}

// 변형을 셀 캔버스에 NEAREST 로 그리고, 논리 격자(snap px/논리픽셀)로 재양자화한다.
// curation.apply_transform 의 snap_scale 경로를 캔버스로 미러링 — 드래그/회전 중에도
// 스프라이트가 셀 고정 격자에 실시간으로 스냅되어 보인다 (격자는 그대로, 그림이 스냅).
function drawFrameInto(ctx, image, t, cw, ch, snap, edits) {
  ctx.imageSmoothingEnabled = false; // 항상 NEAREST — 베이스(raw→논리) 표시도 픽셀 유지
  // 사이드카 픽셀 편집은 변형 이전(소스) 공간이다 — 굽기(apply_pixel_edits →
  // apply_transform, compose_atlas.py)와 같은 순서로 편집을 먼저 소스에 합성한 뒤
  // 함께 변형한다. (실사고 2026-07-17: 변형 후에 고정 좌표로 덧그려서, 캐릭터를
  // 옮기면 편집 픽셀만 제자리에 남았다 — 수홍 발견.)
  let source = image;
  if (edits) {
    const src = drawFrameInto._src || (drawFrameInto._src = document.createElement("canvas"));
    src.width = cw;
    src.height = ch;
    const sctx = src.getContext("2d");
    sctx.imageSmoothingEnabled = false;
    sctx.clearRect(0, 0, cw, ch);
    sctx.drawImage(image, 0, 0, cw, ch);
    for (const [key, val] of Object.entries(edits)) {
      const [x, y] = key.split(",").map(Number);
      if (!(x >= 0 && x < cw && y >= 0 && y < ch)) continue;
      if (val) {
        sctx.fillStyle = val;
        sctx.fillRect(x, y, 1, 1);
      } else {
        sctx.clearRect(x, y, 1, 1);
      }
    }
    source = src;
  }
  const m = matrixOf(t);
  ctx.save();
  ctx.translate(cw / 2 + t.dx, ch / 2 + t.dy);
  ctx.transform(m.m00, m.m10, m.m01, m.m11, 0, 0);
  ctx.drawImage(source, -cw / 2, -ch / 2, cw, ch);
  ctx.restore();
  if (snap && snap > 1) {
    const lw = Math.max(1, Math.floor(cw / snap));
    const lh = Math.max(1, Math.floor(ch / snap));
    const tmp = drawFrameInto._tmp || (drawFrameInto._tmp = document.createElement("canvas"));
    tmp.width = lw;
    tmp.height = lh;
    const tctx = tmp.getContext("2d");
    tctx.imageSmoothingEnabled = false;
    tctx.clearRect(0, 0, lw, lh);
    tctx.drawImage(ctx.canvas, 0, 0, cw, ch, 0, 0, lw, lh);
    ctx.clearRect(0, 0, cw, ch);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(tmp, 0, 0, lw, lh, 0, 0, lw * snap, lh * snap);
  }
}

// 픽셀퍼펙트 격자 오버레이: 픽셀퍼펙트가 실제로 스냅한 논리 픽셀 간격을 그린다.
// run.pixelPerfect.scale = 논리 픽셀 1칸이 차지하는 셀 픽셀 수 (extract 의 pp_scale).
// 예전엔 셀 픽셀마다(scale 무시) 그어서, logical_height < cell 인 런에서 실제 스냅
// 격자보다 촘촘한 거짓 격자를 보여줬다. 픽셀퍼펙트가 아닌 런은 격자 자체가 없다.
function sizePxGrids() {
  document.querySelectorAll(".card").forEach(updateCardGrid);
}

// 줄 단위 격자: 그 줄의 격자 체크박스가 켜져 있을 때만 그린다.
// - 픽셀퍼펙트 표시 줄: 출력 격자(빨/파, 셀 픽셀 눈금) — 결과가 앉은 격자 그 자체.
//   셀에 고정이다: 이동/회전 변형은 스프라이트가 이 고정 래스터에 재양자화되는 것이지
//   래스터가 따라 움직이는 게 아니다 (수홍 확정 2026-07-14 실시간 스냅 동작).
// - 원본(plain) 표시 줄: 최종 대응 격자(초록) — 최종 픽셀 콘텐츠 bbox 를 픽셀 수만큼
//   균등 분할해 원본 위에 겹친다. 칸 하나 = 최종 픽셀 하나 (칸 수 = 픽셀 수 보장).
//   콘텐츠 기준 격자이므로 이동(dx/dy)·좌우반전은 따라간다 (수홍 지적 2026-07-15).
//   회전/기울임/배율 변형은 소스↔결과 대응이 더 이상 직사각 격자가 아니라 숨긴다 —
//   비축정렬 상태로 가짜 격자를 겹치지 않는다 (결과 픽셀은 픽셀퍼펙트 뷰가 보여준다).
//   1차 절단 뒤 48 계약 conform 축소가 칸을 합칠 수 있어 절단선(manifest input_grids)은
//   최종 대응이 아니다 — 진단 기록으로만 남긴다 (수홍 발견 2026-07-14).
// 측정/계약이 없는 줄은 오버레이를 숨긴다 — 가짜 격자 금지.
function updateCardGrid(card) {
  const overlay = card.querySelector(".pxgrid");
  const ingrid = card.querySelector(".ingrid");
  if (!overlay && !ingrid) return;
  const stage = card.querySelector(".stage");
  const cardState = card.dataset.state;
  const isBaseCard = cardState === BASE_STATE;
  const st = run.states.find((s) => s.name === cardState);
  const frame = isBaseCard ? frameOf(BASE_STATE, 0) : (st && st.frames[Number(card.dataset.idx)]);
  const on = !!gridStates[cardState];
  const plainShown = (isBaseCard || ppTwinStates.has(cardState)) && !ppOn(cardState);
  const scale = isBaseCard ? 1
    : ((st && st.pixelScale) || (run.pixelPerfect && run.pixelPerfect.scale) || null);
  const t = frame ? getTransform(card.dataset.state, frame.index) : null;
  const axisAligned = !t || (!t.rotate && t.scale === 1 && !t.shx && !t.shy);
  const useFinal = on && plainShown && frame && frame.contentBox && scale && stage && axisAligned;
  if (ingrid) {
    if (useFinal) {
      drawFinalGrid(ingrid, stage, frame.contentBox, scale, t);
      ingrid.style.display = "block";
    } else {
      ingrid.style.display = "none";
    }
  }
  const step = on && !useFinal && !plainShown ? scale : null;
  if (!overlay) return;
  if (!step || !stage) { overlay.style.display = "none"; return; }
  overlay.style.display = "block";
  const ds = (stage.clientWidth / cellDims(cardState)[0]) * step;
  overlay.style.backgroundSize = `${ds}px ${ds}px`;
}

// 최종 대응 격자: 최종 픽셀 콘텐츠 bbox(셀 좌표)를 논리 픽셀 수만큼 균등 분할해
// 원본 쌍둥이 위에 그린다 — 초록 칸 하나가 결과 픽셀 하나에 정확히 대응한다.
// 축정렬 변형(이동 dx/dy, 좌우반전)은 bbox 를 같은 규칙(CSS: 중심 기준 반전 후 이동)으로
// 옮겨 콘텐츠를 따라간다. 비축정렬 변형은 호출 전에 걸러진다 (updateCardGrid).
function drawFinalGrid(canvas, stage, box, scale, t) {
  const w = Math.max(1, Math.round(stage.clientWidth));
  const h = Math.max(1, Math.round(stage.clientHeight));
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(21, 128, 61, 0.6)";
  ctx.lineWidth = 1;
  const sx = w / run.cell.width;
  const sy = h / run.cell.height;
  const cellsX = Math.max(1, Math.round((box[2] - box[0]) / scale));
  const cellsY = Math.max(1, Math.round((box[3] - box[1]) / scale));
  let bx0 = box[0], bx1 = box[2];
  if (t && t.flipX) { const cw = run.cell.width; [bx0, bx1] = [cw - box[2], cw - box[0]]; }
  const dx = t ? t.dx : 0;
  const dy = t ? t.dy : 0;
  const x0 = (bx0 + dx) * sx, x1 = (bx1 + dx) * sx;
  const y0 = (box[1] + dy) * sy, y1 = (box[3] + dy) * sy;
  for (let k = 0; k <= cellsX; k++) {
    const px = Math.round(x0 + ((x1 - x0) * k) / cellsX) + 0.5;
    ctx.beginPath(); ctx.moveTo(px, y0); ctx.lineTo(px, y1); ctx.stroke();
  }
  for (let k = 0; k <= cellsY; k++) {
    const py = Math.round(y0 + ((y1 - y0) * k) / cellsY) + 0.5;
    ctx.beginPath(); ctx.moveTo(x0, py); ctx.lineTo(x1, py); ctx.stroke();
  }
}

function refreshVariantImages() {
  document.querySelectorAll(".card").forEach((card) => {
    if (!card.dataset.state) return;
    const idx = Number(card.dataset.idx);
    const f = frameOf(card.dataset.state, idx); // 복제 인스턴스도 원본 이미지로 해석
    const el = card.querySelector(".stage img");
    if (f && el) {
      el.src = frameUrl(card.dataset.state, f);
      // 변형 표시 모드(캔버스 스냅 ↔ CSS)도 pp 상태에 맞게 다시 결정
      if (f.present) applyCardTransform(card.querySelector(".stage"), card.dataset.state, idx);
    }
  });
  sizePxGrids();
}

// aggregate checkbox state: checked = all on, unchecked = all off, else indeterminate
function syncAggregate(checkbox, names, isOn) {
  if (!checkbox || !names.size) return;
  const vals = [...names].map(isOn);
  const allOn = vals.every(Boolean);
  checkbox.checked = allOn;
  checkbox.indeterminate = !allOn && !vals.every((v) => !v);
}

// per-state row checkboxes + the header toggle-all checkboxes reflect ppStates/gridStates
function syncPpControls() {
  document.querySelectorAll(".pp-state-check").forEach((el) => {
    el.checked = ppOn(el.dataset.state);
  });
  syncAggregate(document.getElementById("pp-apply"), ppTwinStates, ppOn);
}

function syncGridControls() {
  document.querySelectorAll(".grid-state-check").forEach((el) => {
    el.checked = !!gridStates[el.dataset.state];
  });
  syncAggregate(document.getElementById("pxgrid-check"), gridCapableStates, (n) => !!gridStates[n]);
}

// 줄별 토글 체크박스 공용 팩토리 — refs 줄과 확대 모달이 같은 클래스/핸들러를 쓰므로
// sync*Controls 가 양쪽 인스턴스를 함께 갱신한다 (per-state truth 하나, 표시 N개).
function makeStateToggle(cls, stateName, label, title, checked, onChange) {
  const el = document.createElement("label");
  el.className = "pp-apply row-toggle";
  el.title = title;
  const input = document.createElement("input");
  input.type = "checkbox";
  input.className = cls;
  input.dataset.state = stateName;
  input.checked = checked;
  input.addEventListener("change", (ev) => onChange(ev.target.checked));
  el.appendChild(input);
  el.appendChild(Object.assign(document.createElement("span"), { textContent: label }));
  return el;
}

function makeGridToggle(stateName) {
  return makeStateToggle("grid-state-check", stateName, t("pxGrid"), t("tGridState"),
    !!gridStates[stateName], (checked) => {
      gridStates[stateName] = checked;
      syncGridControls();
      sizePxGrids();
    });
}

// 줄 단위 GIF 다운로드 — 이 줄의 현재 시퀀스(선택/순서/변형/픽셀편집)를 서버가
// 그 자리에서 합성해 GIF 원파일로 내려준다 (실시간 계약: 보는 것 = 받는 것).
function makeGifButton(stateName) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "gif-btn";
  btn.title = t("tRowGif");
  btn.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M8 2v8m0 0l-3-3m3 3l3-3M3 13h10" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    `<span>${t("rowGif")}</span>`;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      await downloadArtifact(`gif?state=${encodeURIComponent(stateName)}`,
        STR[lang].rowGifDone(stateName));
    } catch (e) {
      setStatus(t("exportFail") + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  });
  return btn;
}

function makePpToggle(stateName) {
  return makeStateToggle("pp-state-check", stateName, t("ppState"), t("tPpState"),
    ppOn(stateName), (checked) => {
      ppStates[stateName] = checked;
      syncPpControls();
      refreshVariantImages();
      scheduleSave();
    });
}

// AI 중간 프레임(보간) — 이 줄의 두 프레임 사이를 RIFE 로 보간해 테이크로 기록하고
// 전체 배치를 재추출한다. 부분 추출은 서버가 제공하지 않는다 (팔레트 배치 결합 —
// docs/frame-interpolation.md). 완료되면 run 세대가 바뀌므로 뷰를 새로고침한다.
//
// 픽 모드: 팝오버가 열려 있는 동안 그 줄의 카드 클릭 = 보간 쌍 선택 (파란 테두리,
// 최대 2개 FIFO — 세 번째 클릭이 가장 오래된 픽을 대체). 기존 선택(.selected)의
// 파란 테두리는 픽 모드 동안 그 줄에서 억제해 픽만 파랗게 보인다.
let tweenOpen = null; // {stateName, pop, btn, picks, fromInput, toInput, section}

function closeTweenPick() {
  if (!tweenOpen) return;
  tweenOpen.pop.hidden = true;
  if (tweenOpen.section) {
    tweenOpen.section.classList.remove("tween-picking");
    tweenOpen.section.querySelectorAll(".card.tween-picked")
      .forEach((el) => el.classList.remove("tween-picked"));
  }
  tweenOpen = null;
}

document.addEventListener("click", (ev) => {
  if (!tweenOpen) return;
  const card = ev.target.closest(".card");
  if (!card || card.dataset.state !== tweenOpen.stateName) return;
  if (ev.target.closest(".tween-pop")) return;
  ev.preventDefault();
  ev.stopPropagation();
  const idx = Number(card.dataset.idx);
  const at = tweenOpen.picks.findIndex((p) => p.idx === idx);
  if (at >= 0) {
    tweenOpen.picks.splice(at, 1)[0].card.classList.remove("tween-picked");
  } else {
    tweenOpen.picks.push({ idx, card });
    card.classList.add("tween-picked");
    if (tweenOpen.picks.length > 2) {
      tweenOpen.picks.shift().card.classList.remove("tween-picked");
    }
  }
  const [a, b] = tweenOpen.picks;
  if (a) tweenOpen.fromInput.value = a.idx;
  if (b) tweenOpen.toInput.value = b.idx;
}, true);

let pendingBreathe = false; // 호흡 버튼 → 줌 모달 오픈 시 호흡 모드 진입 플래그

function makeBreatheButton(stateName) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "gif-btn";
  btn.title = t("tRowBreathe");
  btn.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M2 11c2.5 0 2.5-3 5-3s2.5 3 5 3 2-2 2-2" fill="none" stroke="currentColor" ' +
    'stroke-width="1.4" stroke-linecap="round"/></svg>' +
    `<span>${t("rowBreathe")}</span>`;
  btn.addEventListener("click", () => { pendingBreathe = true; openZoom(stateName, 0); });
  return btn;
}

function makeTweenButton(stateName) {
  const wrap = document.createElement("span");
  wrap.className = "tween-wrap";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "gif-btn";
  btn.title = t("tRowTween");
  btn.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M2 8h2.5m7 0H14M8 5.5v5M5.5 8a2.5 2.5 0 115 0 2.5 2.5 0 01-5 0z" fill="none" ' +
    'stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>' +
    `<span>${t("rowTween")}</span>`;
  const pop = document.createElement("div");
  pop.className = "tween-pop";
  pop.hidden = true;
  const field = (label, value, step, min, max) => {
    const lab = document.createElement("label");
    const input = document.createElement("input");
    input.type = "number";
    input.value = value;
    input.step = step;
    input.min = min;
    if (max !== undefined) input.max = max;
    lab.append(label, input);
    pop.appendChild(lab);
    return input;
  };
  const fromInput = field(t("tweenFrom"), 0, 1, 0);
  const toInput = field(t("tweenTo"), 1, 1, 0);
  const tInput = field(t("tweenT"), 0.5, 0.05, 0.05, 0.95);
  const providerSel = document.createElement("select");
  for (const name of ["codex", "grok"]) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name === "codex" ? "GPT" : "Grok";
    providerSel.appendChild(opt);
  }
  pop.appendChild(providerSel);
  const go = document.createElement("button");
  go.type = "button";
  go.className = "gif-btn";
  go.textContent = t("tweenGo");
  pop.appendChild(go);
  btn.addEventListener("click", () => {
    if (tweenOpen && tweenOpen.pop === pop) {
      closeTweenPick();
      return;
    }
    closeTweenPick();
    pop.hidden = false;
    const section = document.querySelector(`.state[data-state="${CSS.escape(stateName)}"]`);
    if (section) section.classList.add("tween-picking");
    tweenOpen = { stateName, pop, btn, picks: [], fromInput, toInput, section };
  });
  go.addEventListener("click", async () => {
    go.disabled = btn.disabled = true;
    const goLabel = go.textContent;
    go.innerHTML = '<span class="tween-spin" aria-label="generating"></span>';
    setStatus(t("tweenBusy"));
    try {
      const res = await fetch("/api/interpolate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          state: stateName,
          from: parseInt(fromInput.value, 10),
          to: parseInt(toInput.value, 10),
          t: parseFloat(tInput.value),
          provider: providerSel.value,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || (data.stderr || "").trim().split("\n").pop() || res.status);
      }
      setStatus(STR[lang].tweenDone(stateName));
      setTimeout(() => window.location.reload(), 800);
    } catch (e) {
      setStatus(t("tweenFail") + e.message, "err");
      go.textContent = goLabel;
      go.disabled = btn.disabled = false;
    }
  });
  wrap.appendChild(btn);
  wrap.appendChild(pop);
  return wrap;
}

const statusEl = document.getElementById("status");
let saveTimer = null;

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = "status" + (kind ? " " + kind : "");
}

function img(url) {
  if (!imageCache.has(url)) {
    const i = new Image();
    i.src = url;
    imageCache.set(url, i);
  }
  return imageCache.get(url);
}

function getTransform(stateName, idx) {
  const t = entries[stateName].transforms;
  if (!t[idx]) t[idx] = IDENTITY();
  return t[idx];
}

// selected := the frame is in the sequence row (top). Moving a card between the
// sequence and pool rows (drag or click) is what flips this; see moveCardToOtherZone.
function isSelected(stateName, idx) {
  return entries[stateName].sel.has(idx);
}

// play sequence = display order filtered to selected frames.
// This is exactly what gets persisted as curation.json `selected`, which
// compose_sprite_atlas.py lays out left-to-right in this order.
function playList(stateName) {
  const e = entries[stateName];
  return e.order.filter((idx) => e.sel.has(idx));
}

// --- persistence -----------------------------------------------------------

function buildPayload() {
  const states = {};
  for (const [name, entry] of Object.entries(entries)) {
    if (name === BASE_STATE) continue; // 가상 상태 — 큐레이션 사이드카에 절대 저장 금지
    const transforms = {};
    for (const [idx, t] of Object.entries(entry.transforms)) {
      if (t.rotate || t.scale !== 1 || t.dx || t.dy || t.shx || t.shy || t.flipX) transforms[idx] = t;
    }
    // `selected` is the play order (what compose bakes). `order` is the full
    // display order (sequence then pool) so the webview can restore the exact
    // row arrangement on reload — compose/curation.py ignore it.
    states[name] = {
      selected: entry.order.filter((idx) => entry.sel.has(idx)),
      order: entry.order.slice(),
      transforms,
    };
    // 보관함 = 스키마의 deleted (UI 행/굽기 기본값에서 제외 — state_plan SSoT)
    if (entry.archived && entry.archived.length) states[name].deleted = entry.archived.slice();
    // 복제 인스턴스 맵 — order 에 남아 있는 복제만 저장 (제거된 복제는 흔적 없이 정리)
    const liveClones = {};
    for (const [ci, src] of Object.entries(entry.clones || {})) {
      if (entry.order.includes(Number(ci))) liveClones[ci] = src;
    }
    if (Object.keys(liveClones).length) states[name].clones = liveClones;
    // 픽셀 편집 사이드카 (빈 프레임 엔트리는 정리)
    const px = {};
    for (const [i, ops] of Object.entries(entry.pixels || {})) {
      if (ops && Object.keys(ops).length) px[i] = ops;
    }
    if (Object.keys(px).length) states[name].pixels = px;
    // per-state pixel-perfect (the row's own toggle) — only for rows with a twin
    if (ppTwinStates.has(name)) states[name].pixel_perfect = ppOn(name);
  }
  const payload = { version: run.schemaVersion || 1, kind: "sprite-gen-curation", states };
  // echo the run generation this view was loaded with; the server rejects the autosave
  // (409) if the run was re-imported/re-extracted under this session so stale selections
  // never land on new frames.
  if (run.runRevision) payload.runRevision = run.runRevision;
  // run-wide default field: written only when every twin row agrees (uniform),
  // so a consumer without per-state awareness still bakes the right variant.
  // Mixed rows -> omitted; the per-state values above are the truth.
  if (ppAvailable) {
    const vals = [...ppTwinStates].map((n) => ppOn(n));
    if (vals.every((v) => v === vals[0])) payload.pixel_perfect = vals[0];
  }
  return payload;
}

let lastEditAt = 0;

function scheduleSave() {
  if (zoomView && zoomView.stateName === BASE_STATE) {
    // 베이스 편집은 파일 굽기 대상 — "저장됨" 오해 방지, 명시 버튼으로만 반영
    setStatus(t("baseUnsaved"));
    return;
  }
  lastEditAt = Date.now();
  setStatus(t("editing"));
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 250);
}

async function save() {
  try {
    const res = await fetch("/api/curation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload()),
    });
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    setStatus(t("saved"), "ok");
    hideSaveLostBanner();
  } catch (e) {
    setStatus(t("saveFail") + e.message, "err");
    // 작은 상태줄만으론 그림 그리는 중에 못 본다 (실사고 2026-07-17: 서버 재기동으로
    // 죽은 탭에서 픽셀 편집이 조용히 유실, 수홍 발견). 저장이 실패하면 무시할 수
    // 없는 배너로 알린다 — 편집은 계속 가능하되 "지금 저장 안 되고 있음" 이 보인다.
    showSaveLostBanner(e.message);
  }
}

let saveLostBanner = null;

function showSaveLostBanner(detail) {
  if (!saveLostBanner) {
    saveLostBanner = document.createElement("div");
    saveLostBanner.className = "save-lost-banner";
    document.body.appendChild(saveLostBanner);
  }
  saveLostBanner.textContent = `${t("saveLostBanner")} (${detail})`;
  saveLostBanner.hidden = false;
}

function hideSaveLostBanner() {
  if (saveLostBanner) saveLostBanner.hidden = true;
}

// --- transform application -------------------------------------------------

function applyCardTransform(stage, stateName, idx) {
  const t = getTransform(stateName, idx);
  const el = stage.querySelector("img");
  if (!el) return;
  const [cw, ch] = cellDims(stateName); // 베이스(가상 상태)는 검출 격자 논리 치수
  // dx/dy are stored in cell pixels; CSS needs rendered pixels.
  const ds = stage.clientWidth / cw;
  const m = matrixOf(t);
  const snap = snapScaleFor(stateName);
  const canvas = stage.querySelector(".snap-canvas");
  const edits = getPixelOps(stateName, idx);
  const editingThis = pixelEdit && pixelEdit.state === stateName && pixelEdit.idx === idx
    && stage.closest("#zoom-modal");
  const basePp = stateName === BASE_STATE && ppOn(stateName); // 베이스 pp ON = 논리 양자화 뷰
  if (canvas && (edits || editingThis || basePp || (snap && !isIdentityTransform(t)))) {
    el.style.transform = "";
    el.style.visibility = "hidden";
    canvas.style.display = "block";
    // 두 캔버스 모드 (실사고 2026-07-17: 편집 유무에 따라 이동 거동이 갈라져
    // 한쪽만 지글거렸다 — 수홍 발견):
    // - 양자화 미리보기 (픽셀퍼펙트 뷰 + 변형): 변형을 격자 재양자화로 굽기와
    //   동일하게 미리 본다. 재양자화 특성상 이동이 격자 단위로 스냅된다 (의도).
    // - 소스 표시 (plain 뷰의 편집 프레임 / 편집 세션): identity 로 한 번 그리고,
    //   이동은 img 와 똑같은 CSS 변형으로 — 편집 없는 프레임과 거동이 같아진다.
    const quantize = (!!snap || basePp) && !editingThis;
    const render = () => {
      canvas.width = cw;
      canvas.height = ch;
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const tt = quantize ? getTransform(stateName, idx) : IDENTITY();
      drawFrameInto(ctx, editSourceFor(stateName, el), tt, canvas.width, canvas.height, snap,
        getPixelOps(stateName, idx));
    };
    canvas.style.transform = (quantize || editingThis)
      ? ""
      : `translate(${t.dx * ds}px, ${t.dy * ds}px) matrix(${m.m00}, ${m.m10}, ${m.m01}, ${m.m11}, 0, 0)`;
    if (el.complete && el.naturalWidth) render();
    else el.addEventListener("load", render, { once: true });
  } else {
    el.style.visibility = "";
    if (canvas) {
      canvas.style.display = "none";
      canvas.style.transform = "";
    }
    // CSS matrix(a,b,c,d,e,f): a=m00 b=m10 c=m01 d=m11; translate applied after, about center.
    el.style.transform =
      `translate(${t.dx * ds}px, ${t.dy * ds}px) matrix(${m.m00}, ${m.m10}, ${m.m01}, ${m.m11}, 0, 0)`;
  }
  const sh = t.shx || t.shy ? ` sh${(t.shx || 0).toFixed(2)},${(t.shy || 0).toFixed(2)}` : "";
  const flip = t.flipX ? " ↔" : "";
  const card = stage.closest(".card");
  const tvalsEl = card.querySelector(".tvals");
  // 항등 변형이면 값 줄을 비운다 (r0° ×1.00 +0,+0 상시 표시 = 잡음). 조정이 있을 때만 노출.
  if (tvalsEl) {
    tvalsEl.textContent = isIdentityTransform(t)
      ? ""
      : `r${t.rotate.toFixed(0)}° ×${t.scale.toFixed(2)} ${t.dx >= 0 ? "+" : ""}${t.dx.toFixed(0)},${t.dy >= 0 ? "+" : ""}${t.dy.toFixed(0)}${sh}${flip}`;
  }
  const flipBtn = card.querySelector(".flip-btn");
  if (flipBtn) flipBtn.classList.toggle("active", !!t.flipX);
  // 대응 격자(초록)는 콘텐츠 기준 — 변형이 바뀔 때마다 이 카드 것만 다시 그린다
  // (이동은 따라오고, 비축정렬이 되면 숨는 판정도 여기서 갱신된다).
  updateCardGrid(card);
}

// 같은 프레임을 보여주는 모든 스테이지(그리드 카드 + 확대 모달)를 함께 갱신 —
// 어느 쪽에서 편집해도 두 화면이 실시간 동기화된다.
function applyFrameTransformAll(stateName, idx) {
  document
    .querySelectorAll(`.card[data-state="${cssEscape(stateName)}"][data-idx="${idx}"] .stage`)
    .forEach((s) => applyCardTransform(s, stateName, idx));
}

// --- interactions ----------------------------------------------------------

function wireStage(stage, stateName, idx) {
  const ds = () => stage.clientWidth / run.cell.width;

  // translate by dragging, toggle select on a click that did not drag
  stage.addEventListener("pointerdown", (ev) => {
    if (ev.target.classList.contains("rotate-handle")) return;
    ev.preventDefault();
    stage.setPointerCapture(ev.pointerId);
    const t = getTransform(stateName, idx);
    const start = { x: ev.clientX, y: ev.clientY, dx: t.dx, dy: t.dy };
    let moved = false;

    const onMove = (e) => {
      const ddx = e.clientX - start.x;
      const ddy = e.clientY - start.y;
      if (Math.abs(ddx) > DRAG_THRESHOLD || Math.abs(ddy) > DRAG_THRESHOLD) moved = true;
      t.dx = start.dx + ddx / ds();
      t.dy = start.dy + ddy / ds();
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      stage.releasePointerCapture(ev.pointerId);
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("pointerup", onUp);
      // 스테이지 클릭은 더 이상 시퀀스⇄풀 토글이 아니다 (수홍 2026-07-15): 실수로
      // 카드를 눌러 빠지는 걸 막는다. 이동은 넣기/빼기 버튼 또는 드래그로만.
      if (moved) scheduleSave();
    };
    stage.addEventListener("pointermove", onMove);
    stage.addEventListener("pointerup", onUp);
  });

  // (휠 스케일 제거 — 맥 터치패드 오작동. 크기 조절은 우하단 돋보기 스크러버로.)

  // rotate via the top handle
  const handle = stage.querySelector(".rotate-handle");
  handle.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    handle.setPointerCapture(ev.pointerId);
    const rect = stage.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const t = getTransform(stateName, idx);
    const startScreen = Math.atan2(ev.clientY - cy, ev.clientX - cx);
    const origRotate = t.rotate;

    const onMove = (e) => {
      const now = Math.atan2(e.clientY - cy, e.clientX - cx);
      // screen angle grows clockwise; schema is CCW positive -> subtract.
      const deltaDeg = ((now - startScreen) * 180) / Math.PI;
      t.rotate = origRotate - deltaDeg;
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      handle.releasePointerCapture(ev.pointerId);
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      scheduleSave();
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
  });

  // shear via the bottom-left handle: horizontal drag = shx, vertical = shy
  const shear = stage.querySelector(".shear-handle");
  shear.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    shear.setPointerCapture(ev.pointerId);
    const t = getTransform(stateName, idx);
    const start = { x: ev.clientX, y: ev.clientY, shx: t.shx || 0, shy: t.shy || 0 };
    const onMove = (e) => {
      // full-width drag ≈ 1.0 slope; small moves give fine control
      t.shx = start.shx + (e.clientX - start.x) / stage.clientWidth;
      t.shy = start.shy + (e.clientY - start.y) / stage.clientHeight;
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      shear.releasePointerCapture(ev.pointerId);
      shear.removeEventListener("pointermove", onMove);
      shear.removeEventListener("pointerup", onUp);
      scheduleSave();
    };
    shear.addEventListener("pointermove", onMove);
    shear.addEventListener("pointerup", onUp);
  });
}

// --- frame reorder + two-zone curation (sequence row / candidate pool) ------
//
// Each state renders two `.frames` rows: the top is the play SEQUENCE (selected
// frames, in order) and the bottom is the candidate POOL (everything else,
// e.g. an extra generated take). Dragging the ⠿ grip reorders within a row OR
// moves a card between rows; which row a card lands in *is* its selection. The
// grip lives in `.card-top`, outside `.stage`, so it never collides with the
// stage's move/scale/rotate/shear drags.

function presentCards(container) {
  return [...container.querySelectorAll(".card:not(.missing)")];
}

function zoneFrames(wrap) {
  return { seq: wrap.querySelector(".seq-frames"), pool: wrap.querySelector(".pool-frames") };
}

// selection := membership of the sequence row. order := seq cards then pool
// cards, so playList() (order ∩ sel) is exactly the sequence row, left to right.
function commitZones(wrap, stateName) {
  const { seq, pool } = zoneFrames(wrap);
  const seqIdx = presentCards(seq).map((c) => Number(c.dataset.idx));
  const poolIdx = presentCards(pool).map((c) => Number(c.dataset.idx));
  // keep not-yet-extracted (missing) frames in order so their slot survives a
  // reorder — if extraction later fills them in, they aren't silently dropped.
  const state = run.states.find((s) => s.name === stateName);
  const archivedSet = new Set(entries[stateName].archived || []);
  const missingIdx = state ? state.frames.filter((f) => !f.present && !archivedSet.has(f.index)).map((f) => f.index) : [];
  entries[stateName].sel = new Set(seqIdx);
  entries[stateName].order = [...seqIdx, ...poolIdx, ...missingIdx];
}

// the present card the dragged card should be inserted *before*, by pointer x
// within one row. null -> after them all.
function reorderRefBefore(container, dragCard, x) {
  let ref = null;
  let closest = -Infinity;
  for (const card of presentCards(container)) {
    if (card === dragCard) continue;
    const box = card.getBoundingClientRect();
    const offset = x - (box.left + box.width / 2);
    if (offset < 0 && offset > closest) {
      closest = offset;
      ref = card;
    }
  }
  return ref;
}

// pick the row (seq above, pool below) whose band the cursor y falls into.
function pickZone(seq, pool, y) {
  const s = seq.getBoundingClientRect();
  const p = pool.getBoundingClientRect();
  return y < (s.bottom + p.top) / 2 ? seq : pool;
}

// FLIP across both rows: measure (First), reorder DOM (mutate), then invert +
// Play in 2D so cards slide — including vertically when they cross rows —
// since flexbox reflow can't be animated by CSS transitions alone.
function flipReorder(containers, mutate) {
  // exclude .missing (inert, not interactive) so unextracted slots don't animate
  const cards = containers.flatMap((c) => [...c.querySelectorAll(".card:not(.dragging):not(.missing)")]);
  const first = cards.map((c) => {
    const b = c.getBoundingClientRect();
    return { l: b.left, t: b.top };
  });
  mutate();
  // pass 1: apply the inverted transform with no transition
  const moved = [];
  cards.forEach((c, i) => {
    const b = c.getBoundingClientRect();
    const dl = first[i].l - b.left;
    const dt = first[i].t - b.top;
    if (Math.abs(dl) < 0.5 && Math.abs(dt) < 0.5) return;
    c.style.transition = "none";
    c.style.transform = `translate(${dl}px, ${dt}px)`;
    moved.push(c);
  });
  if (!moved.length) return;
  // single forced reflow commits the inverted positions across all moved cards;
  // a bare requestAnimationFrame is not reliable on Safari/Firefox (the inverted
  // frame may not paint before the transition is enabled, so cards teleport).
  void moved[0].offsetWidth;
  // pass 2: enable the transition and release to home -> they slide
  for (const c of moved) {
    c.style.transition = "transform 0.18s ease";
    c.style.transform = "";
  }
}

// click affordance: send a card to the other row (sequence <-> pool), animated.
function moveCardToOtherZone(card, stateName) {
  const wrap = card.closest(".state");
  const { seq, pool } = zoneFrames(wrap);
  const dest = card.closest(".frames") === seq ? pool : seq;
  flipReorder([seq, pool], () => dest.appendChild(card));
  commitZones(wrap, stateName);
  renderSelectionState(stateName);
  if (previews[stateName] && previews[stateName].refresh) previews[stateName].refresh();
  scheduleSave();
}

// The card header (`.card-top` = the title strip) is the drag handle: grab the
// title anywhere and move past DRAG_THRESHOLD to reorder within a row or move it
// between the sequence/pool rows. A press that never moves is a plain click and
// does NOTHING (수홍 2026-07-15): toggling sequence ⇄ pool is only the 넣기/빼기
// button or a drop, so a stray click can't silently add/remove a frame.
function wireReorder(handle, card, wrap, stateName) {
  handle.addEventListener("pointerdown", (ev) => {
    if (ev.button || !ev.isPrimary) return; // primary button + primary pointer only (no multi-touch parallel drag)
    ev.preventDefault();
    const { seq, pool } = zoneFrames(wrap);
    const startX = ev.clientX;
    const startY = ev.clientY;
    let lifted = false;
    let ph = null;
    let grabDX = 0;
    let grabDY = 0;

    const moveCard = (x, y) => {
      card.style.left = `${x - grabDX}px`;
      card.style.top = `${y - grabDY}px`;
    };

    // lift the card out of flow so it floats under the cursor; a placeholder of
    // the same size holds the slot it will drop into (in its current row). Only
    // happens once the press crosses DRAG_THRESHOLD, so a plain click never lifts.
    const lift = () => {
      const rect = card.getBoundingClientRect();
      grabDX = startX - rect.left;
      grabDY = startY - rect.top;
      ph = document.createElement("div");
      ph.className = "card-placeholder";
      ph.style.width = `${rect.width}px`;
      ph.style.height = `${rect.height}px`;
      card.parentNode.insertBefore(ph, card);
      card.classList.add("dragging");
      card.style.width = `${rect.width}px`;
      card.style.height = `${rect.height}px`;
      card.style.position = "fixed";
      card.style.zIndex = "1000";
      card.style.pointerEvents = "none";
      lifted = true;
    };

    // listeners on window (not the handle): once lifted the card is fixed/detached
    // from flow, so a handle-scoped pointerup could be missed — window catches the
    // release anywhere.
    const onMove = (e) => {
      if (!lifted) {
        if (Math.abs(e.clientX - startX) <= DRAG_THRESHOLD && Math.abs(e.clientY - startY) <= DRAG_THRESHOLD) return;
        lift();
      }
      moveCard(e.clientX, e.clientY);
      const zone = pickZone(seq, pool, e.clientY);
      const firstMissing = zone.querySelector(".card.missing");
      const refNode = reorderRefBefore(zone, card, e.clientX) || firstMissing;
      if (ph.parentNode === zone && (ph.nextElementSibling === refNode || refNode === ph)) return;
      flipReorder([seq, pool], () => zone.insertBefore(ph, refNode));
    };
    const end = (evUp) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      if (!lifted) {
        // 임계값을 넘지 않은 순수 클릭 — 아무 것도 하지 않는다. 시퀀스⇄풀 이동은
        // 넣기/빼기 버튼(sel-btn)이나 드롭으로만 (수홍 2026-07-15).
        return;
      }
      // 보관함 칩 위에서 놓으면 보관 (풀에서도 제외)
      const chip = wrap.querySelector(".archive-chip");
      if (chip && evUp && typeof evUp.clientX === "number") {
        const r = chip.getBoundingClientRect();
        if (evUp.clientX >= r.left && evUp.clientX <= r.right && evUp.clientY >= r.top && evUp.clientY <= r.bottom) {
          ph.remove();
          card.remove();
          archiveFrame(stateName, Number(card.dataset.idx));
          return;
        }
      }
      const fromRect = card.getBoundingClientRect();
      card.classList.remove("dragging");
      card.style.position = card.style.left = card.style.top = "";
      card.style.width = card.style.height = card.style.zIndex = card.style.pointerEvents = "";
      ph.parentNode.insertBefore(card, ph);
      ph.remove();
      // settle: slide the dropped card from the release point into its slot.
      const toRect = card.getBoundingClientRect();
      const dx = fromRect.left - toRect.left;
      const dy = fromRect.top - toRect.top;
      if (dx || dy) {
        card.style.transition = "none";
        card.style.transform = `translate(${dx}px, ${dy}px)`;
        void card.offsetWidth; // commit before enabling transition (Safari/Firefox safe)
        card.style.transition = "transform 0.16s ease";
        card.style.transform = "";
      }
      commitZones(wrap, stateName);
      renderSelectionState(stateName); // refresh selection classes + count
      if (previews[stateName] && previews[stateName].refresh) previews[stateName].refresh();
      scheduleSave();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  });
}

// 상태 섹션 in-place 재구성 (보관/복구 후) — 위치 보존
function rebuildState(stateName) {
  const st = run.states.find((s) => s.name === stateName);
  const old = document.querySelector(`.state[data-state="${cssEscape(stateName)}"]`);
  if (!st || !old) return;
  renderState(st, old); // old 자리에 교체 렌더
}

function archiveFrame(stateName, idx) {
  const e = entries[stateName];
  e.sel.delete(idx);
  e.order = e.order.filter((i) => i !== idx);
  if (cloneSrc(stateName, idx) !== null) {
    // 복제 인스턴스는 보관함에 넣지 않고 완전히 제거한다 — 원본이 살아 있으니
    // 언제든 다시 복제하면 되고, 보관함에 사본이 쌓이는 건 혼란만 준다.
    delete e.clones[idx];
    delete e.transforms[idx];
    delete e.pixels[idx];
  } else if (!e.archived.includes(idx)) {
    e.archived.push(idx);
  }
  scheduleSave();
  rebuildState(stateName);
}

// 프레임 복제: 원본 카드 바로 뒤에, 같은 존(시퀀스/풀)으로, 현재 변형·픽셀편집을
// 복사한 새 인스턴스를 만든다. 복제의 복제도 원본 프레임으로 평탄화해 기록한다.
function duplicateFrame(stateName, idx) {
  const e = entries[stateName];
  const st = run.states.find((s) => s.name === stateName);
  if (!st) return;
  e.clones = e.clones || {};
  const used = [
    ...st.frames.map((f) => f.index),
    ...Object.keys(e.clones).map(Number),
    ...e.order,
    ...e.archived,
  ];
  const newIdx = Math.max(-1, ...used) + 1;
  const src = cloneSrc(stateName, idx) ?? idx;
  e.clones[newIdx] = src;
  if (e.transforms[idx]) e.transforms[newIdx] = { ...e.transforms[idx] };
  if (e.pixels[idx]) e.pixels[newIdx] = JSON.parse(JSON.stringify(e.pixels[idx]));
  const pos = e.order.indexOf(idx);
  e.order.splice(pos < 0 ? e.order.length : pos + 1, 0, newIdx);
  if (e.sel.has(idx)) e.sel.add(newIdx);
  scheduleSave();
  rebuildState(stateName);
}

function restoreFrame(stateName, idx, toSequence) {
  const e = entries[stateName];
  e.archived = e.archived.filter((i) => i !== idx);
  if (!e.order.includes(idx)) e.order.push(idx);
  if (toSequence) e.sel.add(idx);
  else e.sel.delete(idx);
  scheduleSave();
  rebuildState(stateName);
}

// 확대 스크러버 (‹🔍› 형태, 이모지 아님 — SVG): 화살표 클릭 = 스텝, 돋보기 드래그 = 연속.
// 맥 터치패드에서 휠-스케일이 불편해 추가 (휠도 계속 동작).
function makeScaleScrub(stateName, idx) {
  const wrap = document.createElement("span");
  wrap.className = "scale-scrub";
  wrap.setAttribute("data-tip", t("tScaleScrub"));
  wrap.innerHTML =
    '<button type="button" class="ghost ss-step" data-dir="-1" aria-label="smaller">' +
    '<svg viewBox="0 0 10 10" width="9" height="9"><path d="M2 5h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>' +
    '<span class="ss-grab" aria-label="drag to scale">' +
    '<svg viewBox="0 0 16 16" width="13" height="13">' +
    '<circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.4"/>' +
    '<path d="M7 5.2v3.6M5.2 7h3.6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>' +
    '<path d="M10.4 10.4 14 14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></span>' +
    '<button type="button" class="ghost ss-step" data-dir="1" aria-label="bigger">' +
    '<svg viewBox="0 0 10 10" width="9" height="9"><path d="M2 5h6M5 2v6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>';
  const clamp = (v) => Math.min(SCALE_MAX, Math.max(SCALE_MIN, v));
  // ± 연타가 스테이지 더블클릭(확대 모달 열기)으로 새지 않게 스크러버 안의
  // 포인터/클릭 계열 이벤트는 전부 여기서 끊는다 (수홍 지적 2026-07-16).
  for (const type of ["pointerdown", "click", "dblclick"]) {
    wrap.addEventListener(type, (ev) => ev.stopPropagation());
  }
  wrap.querySelectorAll(".ss-step").forEach((btn) => {
    btn.addEventListener("pointerdown", (ev) => ev.stopPropagation());
    btn.addEventListener("click", () => {
      const tr = getTransform(stateName, idx);
      tr.scale = clamp(tr.scale * (btn.dataset.dir === "1" ? 1.05 : 1 / 1.05));
      applyFrameTransformAll(stateName, idx);
      scheduleSave();
    });
  });
  const grab = wrap.querySelector(".ss-grab");
  grab.addEventListener("pointerdown", (ev) => {
    if (ev.button || !ev.isPrimary) return;
    ev.preventDefault();
    ev.stopPropagation();
    grab.setPointerCapture(ev.pointerId);
    const tr = getTransform(stateName, idx);
    const startX = ev.clientX;
    const startScale = tr.scale;
    const onMove = (e2) => {
      tr.scale = clamp(startScale * Math.exp((e2.clientX - startX) / 140));
      applyFrameTransformAll(stateName, idx);
    };
    const onUp = () => {
      grab.releasePointerCapture(ev.pointerId);
      grab.removeEventListener("pointermove", onMove);
      grab.removeEventListener("pointerup", onUp);
      scheduleSave();
    };
    grab.addEventListener("pointermove", onMove);
    grab.addEventListener("pointerup", onUp);
  });
  return wrap;
}

function resetTransform(stateName, idx) {
  entries[stateName].transforms[idx] = IDENTITY();
  applyFrameTransformAll(stateName, idx);
  scheduleSave();
}

// --- rendering -------------------------------------------------------------

function renderSelectionState(stateName) {
  document.querySelectorAll(`.card[data-state="${cssEscape(stateName)}"]`).forEach((card) => {
    if (card.classList.contains("missing")) return;
    const idx = Number(card.dataset.idx);
    const inSeq = isSelected(stateName, idx);
    card.classList.toggle("selected", inSeq);
    const btn = card.querySelector(".sel-btn");
    if (btn) {
      // 아이콘(방향) + 라벨. 색상 강조 없이 방향 화살표로 넣기/빼기를 구분.
      btn.innerHTML = (inSeq ? SEL_ICON.remove : SEL_ICON.add) +
        `<span>${inSeq ? t("removeFromSeq") : t("addToSeq")}</span>`;
      btn.setAttribute("data-tip", inSeq ? t("tSelRemove") : t("tSelAdd"));
    }
  });
  const state = run.states.find((s) => s.name === stateName);
  const countEl = document.querySelector(`.preview[data-state="${cssEscape(stateName)}"] .count`);
  if (countEl) countEl.textContent = `${entries[stateName].sel.size}/${state.requestFrames} ${t("frames")}`;
}

function cssEscape(s) {
  return s.replace(/"/g, '\\"');
}

// escape text that comes from run data (state name/action, frame labels from a
// manifest / meta.json) before it goes into innerHTML, so an imported set can't
// inject markup into the webview.
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderState(state, replaceEl) {
  const wrap = document.createElement("section");
  wrap.className = "state";
  wrap.dataset.state = state.name;

  const head = document.createElement("div");
  head.className = "state-head";
  head.innerHTML =
    `<span class="name">${escapeHtml(state.name)}</span>` +
    `<span class="meta">${state.requestFrames} ${t("frames")} · ${state.fps}fps · ${state.loop ? t("loop") : t("nonLoop")} · ${t("cellPx")} ${run.cell.width}x${run.cell.height}px</span>` +
    (state.action ? `<span class="action">${escapeHtml(state.action)}</span>` : "") +
    (state.extractOk ? "" : `<span class="state-warn">${t("extractFail")}</span>`) +
    (anchorStates.has(state.name) ? `<span class="anchor-badge" data-tip="${t("tDirAnchorBadge")}">${t("dirAnchorBadge")}</span>` : "");
  wrap.appendChild(head);

  // 이 줄을 "무엇으로 생성했는가" — run dir 실재 파일 기준 ref 체인 (앵커/basis/가이드).
  // 같은 줄 우측 = 줄별 표시/굽기 컨트롤(픽셀 격자 · 픽셀퍼펙트 체크박스) — 이미지 바로 위.
  const hasRefs = state.refs && state.refs.length;
  const showGridToggle = gridCapableStates.has(state.name);
  const showPpToggle = ppTwinStates.has(state.name);
  const showGifBtn = state.frames && state.frames.some((f) => f.present);
  if (hasRefs || showGridToggle || showPpToggle || showGifBtn) {
    const refs = document.createElement("div");
    refs.className = "state-refs";
    refs.innerHTML = hasRefs
      ? `<span class="refs-label">${t("refsLabel")}</span>` +
        state.refs
          .map(
            (r) =>
              `<a class="ref-chip" href="${escapeHtml(r.url)}" target="_blank" title="${escapeHtml(r.name)}">` +
              `<img src="${escapeHtml(r.url)}" alt="${escapeHtml(r.role)}" loading="lazy" />` +
              `<span>${t("ref_" + r.role)}</span></a>`
          )
          .join("")
      : "";
    const controls = document.createElement("span");
    controls.className = "row-controls";
    if (showGridToggle) controls.appendChild(makeGridToggle(state.name));
    if (showPpToggle) controls.appendChild(makePpToggle(state.name));
    if (showGifBtn) controls.appendChild(makeGifButton(state.name));
    controls.appendChild(makeTweenButton(state.name));
    controls.appendChild(makeBreatheButton(state.name));
    refs.appendChild(controls);
    wrap.appendChild(refs);
  }

  const body = document.createElement("div");
  body.className = "state-body";

  // two rows: sequence (selected, in play order) on top, candidate pool below.
  const zones = document.createElement("div");
  zones.className = "zones";
  zones.innerHTML =
    `<div class="zone zone-seq"><div class="zone-label">${t("zoneSeq")}</div>` +
    `<div class="frames seq-frames"></div></div>` +
    `<div class="zone zone-pool"><div class="zone-label">${t("zonePool")}</div>` +
    `<div class="frames pool-frames"></div></div>`;
  const seqFrames = zones.querySelector(".seq-frames");
  const poolFrames = zones.querySelector(".pool-frames");

  const e = entries[state.name];
  // frameOf 는 복제 인스턴스(order 의 물리 범위 밖 인덱스)도 원본 frame 객체를
  // 빌려 해석한다 — 카드 하나 = 인스턴스 하나.
  for (const idx of e.order) {
    if (!e.sel.has(idx)) continue;
    const frame = frameOf(state.name, idx);
    if (frame) seqFrames.appendChild(renderCard(state, frame));
  }
  // pool = everything not in the sequence. `order` already contains every
  // index (present + missing), so this single loop covers missing frames too
  // — do NOT also iterate state.frames here or missing cards render twice.
  for (const idx of e.order) {
    if (e.sel.has(idx)) continue;
    const frame = frameOf(state.name, idx);
    if (frame) poolFrames.appendChild(renderCard(state, frame));
  }

  // 보관함: 후보 풀에서도 완전히 뺀 프레임. 접힌 칩 → 클릭하면 팝오버(쇽),
  // 카드를 칩에 끌어다 놓으면 보관, 팝오버의 미니카드를 끌어내면 복구.
  zones.appendChild(renderArchive(state));

  body.appendChild(zones);
  body.appendChild(renderPreview(state));
  wrap.appendChild(body);

  if (replaceEl) replaceEl.replaceWith(wrap);
  else document.getElementById("states").appendChild(wrap);

  // wire stages + reorder grips after they are in the DOM (need clientWidth).
  // order 를 돌아야 복제 인스턴스 카드도 와이어링된다 (물리 프레임 목록엔 없다).
  for (const idx of e.order) {
    const frame = frameOf(state.name, idx);
    if (!frame || !frame.present) continue;
    const card = wrap.querySelector(`.card[data-idx="${idx}"]`);
    if (!card) continue; // 보관된 프레임은 행에 카드가 없다
    const stage = card.querySelector(".stage");
    wireStage(stage, state.name, idx);
    applyCardTransform(stage, state.name, idx);
    if (run.iso) drawGroundGrid(stage);
    // the whole header strip is the drag handle (grip + label + ✗/✓ button),
    // not just the ⠿ glyph — see wireReorder.
    const cardTop = card.querySelector(".card-top");
    if (cardTop) wireReorder(cardTop, card, wrap, state.name);
  }
  renderSelectionState(state.name);
  startPreview(state);
}

// --- 생성 구조 트리 (파이프라인 개요) ----------------------------------------
// base → 방향별 idle 행 → 앵커(frame-0 크롭 1장) → 각 행. 방향 계약 없는 런은
// base → 행 2단. 미생성 노드는 점선(진행 현황판 겸용), 클릭 = 해당 줄로 스크롤.
function treeNode(label, note, thumbUrl, targetState, extra) {
  const rawOnly = thumbUrl && typeof thumbUrl === "object" && thumbUrl.raw;
  const node = document.createElement("span");
  node.className = "tree-node" + (thumbUrl === false ? " pending" : "") + (rawOnly ? " raw-only" : "") + (extra ? " " + extra : "");
  if (rawOnly) {
    const img = document.createElement("img");
    img.src = thumbUrl.raw;
    img.alt = label;
    node.appendChild(img);
  } else if (thumbUrl) {
    const img = document.createElement("img");
    img.src = thumbUrl;
    img.alt = label;
    node.appendChild(img);
  } else if (thumbUrl === false) {
    node.appendChild(Object.assign(document.createElement("span"), { className: "thumb-missing" }));
  }
  node.appendChild(Object.assign(document.createElement("span"), { className: "tn-label", textContent: label }));
  if (note) node.appendChild(Object.assign(document.createElement("span"), { className: "tn-note", textContent: note }));
  if (targetState) {
    node.setAttribute("data-tip", t("tTreeNode"));
    node.classList.add("clickable");
    node.addEventListener("click", () => {
      const section = targetState === "__base__"
        ? document.querySelector(".base-row")
        : targetState === "__atlas__"
          ? document.getElementById("final-atlas")
          : document.querySelector(`.state[data-state="${cssEscape(targetState)}"]`);
      if (!section) return;
      section.scrollIntoView({ behavior: "smooth", block: "start" });
      flashSection(section);
    });
  }
  return node;
}

// --- 최종 아틀라스 섹션 (페이지 맨 아래): 아틀라스 시트 | manifest.json 좌우 ------
// 아틀라스는 다운로드/합성 시점의 산출물이라 계산 시각을 함께 표시한다 — 큐레이션을
// 더 만졌으면 다음 다운로드가 다시 계산한다 (버튼 = 라이브 상태의 다운로드 계약).
async function renderFinalAtlas(info) {
  let section = document.getElementById("final-atlas");
  if (!section) {
    section = document.createElement("div");
    section.id = "final-atlas";
    section.className = "state final-atlas";
    document.getElementById("states").appendChild(section);
  }
  if (!info) {
    section.innerHTML =
      `<div class="state-head"><span class="name">${t("treeAtlas")}</span></div>` +
      `<div class="atlas-pending">${t("atlasPending")}</div>`;
    return;
  }
  const stamp = STR[lang].atlasStamp(new Date(info.mtime * 1000).toLocaleString());
  let docHtml = "";
  if (info.manifestUrl) {
    try {
      const res = await fetch(info.manifestUrl);
      const doc = await res.json();
      docHtml = `<pre class="atlas-doc-json">${escapeHtml(JSON.stringify(doc, null, 2))}</pre>`;
    } catch {
      docHtml = `<pre class="atlas-doc-json">manifest.json 읽기 실패</pre>`;
    }
  }
  section.innerHTML =
    `<div class="state-head"><span class="name">${t("treeAtlas")}</span>` +
    `<span class="meta">sprite-sheet-alpha.png · ${escapeHtml(stamp)}</span></div>` +
    `<div class="atlas-split">` +
    `<a class="atlas-sheet" href="${escapeHtml(info.url)}" target="_blank">` +
    `<img src="${escapeHtml(info.url)}" alt="final atlas" /></a>` +
    (docHtml
      ? `<div class="atlas-doc"><div class="atlas-doc-head">${t("atlasDoc")}</div>${docHtml}</div>`
      : "") +
    `</div>`;
  syncAtlasDocHeight();
}

// JSON 패널 높이 = 아틀라스 시트 높이 (수홍 지시 2026-07-17: 기준은 아틀라스 —
// stretch 는 JSON 이 길면 반대로 JSON 이 기준이 돼 버린다). 이미지 로드 후 실측으로
// 고정하고, 창 리사이즈 때 다시 잰다. JSON 이 더 길면 패널 안에서 스크롤.
function syncAtlasDocHeight() {
  const section = document.getElementById("final-atlas");
  if (!section) return;
  const sheetImg = section.querySelector(".atlas-sheet img");
  const doc = section.querySelector(".atlas-doc");
  if (!sheetImg || !doc) return;
  const apply = () => {
    const h = section.querySelector(".atlas-sheet").offsetHeight;
    if (h > 0) doc.style.height = h + "px";
  };
  if (sheetImg.complete && sheetImg.naturalWidth) apply();
  else sheetImg.addEventListener("load", apply, { once: true });
  if (!syncAtlasDocHeight._wired) {
    syncAtlasDocHeight._wired = true;
    window.addEventListener("resize", () => {
      clearTimeout(syncAtlasDocHeight._t);
      syncAtlasDocHeight._t = setTimeout(syncAtlasDocHeight, 120);
    });
  }
}

// 다운로드가 아틀라스/manifest 를 다시 계산했을 수 있으니 섹션을 최신 파일로 갱신
async function refreshFinalAtlas() {
  const now = Math.floor(Date.now() / 1000);
  renderFinalAtlas({
    url: `/run/sprite-sheet-alpha.png?v=${now}`,
    mtime: now,
    manifestUrl: `/run/manifest.json?v=${now}`,
  });
}

// 이동한 대상 패널 하이라이트: 스크롤이 끝난 뒤 따닥 두 번 깜빡이고 사라진다.
// scrollend 지원 브라우저는 도착 즉시, 아니면 짧은 타임아웃 폴백.
function flashSection(el) {
  let fired = false;
  const fire = () => {
    if (fired) return;
    fired = true;
    window.removeEventListener("scrollend", fire);
    el.classList.remove("flash-target");
    void el.offsetWidth; // 연속 클릭 시 애니메이션 재시작
    el.classList.add("flash-target");
    el.addEventListener("animationend", () => el.classList.remove("flash-target"), { once: true });
  };
  window.addEventListener("scrollend", fire, { once: true });
  setTimeout(fire, 750); // 스크롤이 필요 없거나 scrollend 미지원일 때
}

// 생성 진행 스냅샷 (트리 실시간 갱신): stateName -> {raw, frames}. 초기값은 /api/run,
// 이후 /api/progress 3초 폴링이 갱신한다.
let treeProgress = new Map();
let treeRevision = null;

async function seedTreeProgress() {
  // 초기값도 /api/progress 로 — 경로(rawUrl/frame0Url/relRaw)는 서버 리졸버가 SSoT
  // (택소노미/flat 레이아웃을 클라이언트가 패턴 조립하지 않는다).
  try {
    const res = await fetch("/api/progress");
    const next = await res.json();
    if (next.states) {
      treeProgress = new Map(next.states.map((p) => [p.name, p]));
      treeRevision = next.runRevision;
      return;
    }
  } catch { /* 아래 폴백 */ }
  treeProgress = new Map(run.states.map((s) => [s.name, {
    raw: !!s.rawPresent,
    frames: s.frames.filter((f) => f.present).length,
  }]));
  treeRevision = run.runRevision;
}

function renderPipelineTree() {
  const frameThumb = (name) => {
    const p = treeProgress.get(name);
    if (!(p && p.frames > 0)) return false;
    return `${p.frame0Url || `/frames/${encodeURIComponent(name)}/frame-0.png`}?v=${treeRevision || 0}`;
  };
  const rawThumb = (name) => {
    const p = treeProgress.get(name);
    if (!(p && p.raw)) return false;
    return `${p.rawUrl || `/run/raw/${encodeURIComponent(name)}.png`}?v=${treeRevision || 0}`;
  };
  const frameCount = (name) => {
    const p = treeProgress.get(name);
    return p ? p.frames : 0;
  };
  // 생성 진행을 반영한 대표 썸네일: 추출 프레임 > raw 스트립 > 미생성
  const bestThumb = (name) => {
    const f = frameThumb(name);
    if (f) return f;
    const r = rawThumb(name);
    return r ? { raw: r } : false;
  };
  const anchorFileThumb = (direction) => {
    const f = (run.anchorFiles || []).find((a) => a.name === `${direction}.png`);
    return f ? `${f.url}?v=${treeRevision || 0}` : null;
  };
  const chipList = () => {
    const ul = document.createElement("ul");
    ul.className = "tree-rows";
    return ul;
  };
  const chipItem = (ul, node) => {
    const el = document.createElement("li");
    el.appendChild(node);
    ul.appendChild(el);
  };
  const liWith = (parentUl, ...nodes) => {
    const el = document.createElement("li");
    for (const n of nodes) if (n) el.appendChild(n);
    parentUl.appendChild(el);
    return el;
  };
  // 접을 수 있는 블록 (파이프라인 / 파일) — folderNode 의 접힘 상태 공유
  const block = (label, ul, kind) => {
    const div = document.createElement("div");
    div.className = "tree-block" + (kind ? " " + kind : "");
    div.appendChild(folderNode(label, null, kind === "pipeline" ? "flow" : "folder"));
    div.appendChild(ul);
    if (collapsedFolders.has(label)) div.classList.add("folder-collapsed");
    return div;
  };
  const stateChip = (name, extraNote, extraCls) => {
    const n = frameCount(name);
    const note = [n > 0 ? STR[lang].treeFrameCount(n) : t("treePending"), extraNote].filter(Boolean).join(" · ");
    return treeNode(name, note, bestThumb(name), name, extraCls);
  };

  // ── 파이프라인 블록: base → <dir>_idle 행 → 방향 앵커 → rows 체인 ──────────
  const chainUl = document.createElement("ul");
  let chainHost = chainUl;
  if (run.baseUrl) {
    const baseLi = liWith(chainUl, treeNode("base", t("treeBaseNote"), run.baseUrl, "__base__", "tree-root"));
    chainHost = document.createElement("ul");
    baseLi.appendChild(chainHost);
  }
  if (run.directionGroups && run.directionGroups.length) {
    for (const group of run.directionGroups) {
      if (group.mirrorOf) {
        liWith(chainHost, treeNode(STR[lang].treeMirror(group.direction, group.mirrorOf), null, undefined, null, "mirror"));
        continue;
      }
      if (group.anchor) {
        const idleLi = liWith(chainHost, stateChip(group.anchor, t("treeIdleRow")));
        const anchorUl = document.createElement("ul");
        idleLi.appendChild(anchorUl);
        const anchorLi = liWith(anchorUl, treeNode(
          `${group.direction} ${t("dirAnchorBadge")}`, t("treeAnchorNote"),
          anchorFileThumb(group.direction) || bestThumb(group.anchor), group.anchor, "anchor"));
        const rows = chipList();
        for (const name of group.states.filter((n) => n !== group.anchor)) chipItem(rows, stateChip(name));
        anchorLi.appendChild(rows);
      } else {
        const rows = chipList();
        for (const name of group.states) chipItem(rows, stateChip(name));
        liWith(chainHost, treeNode(group.direction, null, undefined, null)).appendChild(rows);
      }
    }
  } else {
    const rows = chipList();
    for (const st of run.states) chipItem(rows, stateChip(st.name));
    const holder = liWith(chainHost);
    holder.appendChild(rows);
  }
  // 체인의 종착지 = 최종 아틀라스 (클릭 → 맨 아래 섹션으로 스크롤)
  liWith(chainUl, treeNode(t("treeAtlas"), run.atlas ? null : t("treePending"),
    run.atlas ? run.atlas.url : false, "__atlas__", "atlas-node"));

  // ── 파일 블록: 폴더 뼈대 (어디에 저장되는가) ──────────────────────────────
  const fileUl = document.createElement("ul");
  if (run.baseUrl) liWith(fileUl, treeNode("base-source", null, run.baseUrl, "__base__"));
  // 택소노미 중첩: rel 경로가 <root>/<dir>/<leaf> 면 방향 하위 폴더로 묶는다 (legacy flat 은 그대로)
  const groupedFolder = (rootLabel, rootNote, relKey, chipFor) => {
    const rootLi = liWith(fileUl, folderNode(rootLabel, rootNote));
    const dirs = new Map(); // "" = flat
    for (const st of run.states) {
      const rel = (treeProgress.get(st.name) || {})[relKey] || "";
      const segs = rel.split("/");
      const dir = segs.length >= 3 ? segs[1] : "";
      const leaf = segs.length >= 3 ? segs.slice(2).join("/") : segs.slice(1).join("/");
      if (!dirs.has(dir)) dirs.set(dir, []);
      dirs.get(dir).push({ state: st, leaf: leaf || st.name });
    }
    const host = document.createElement("ul");
    for (const [dir, items] of dirs) {
      if (dir) {
        const dli = document.createElement("li");
        dli.appendChild(folderNode(`${dir}/`, null));
        const ul = chipList();
        for (const it of items) chipItem(ul, chipFor(it));
        dli.appendChild(ul);
        host.appendChild(dli);
      } else {
        for (const it of items) {
          const li = document.createElement("li");
          li.appendChild(chipFor(it));
          host.appendChild(li);
        }
      }
    }
    rootLi.appendChild(host);
  };
  groupedFolder("raw/", t("treeRawFolder"), "relRaw", (it) => {
    const thumb = rawThumb(it.state.name);
    const note = thumb && frameCount(it.state.name) === 0 ? t("treeRawNote") : null;
    return treeNode(it.leaf, note, thumb ? { raw: thumb } : false, it.state.name);
  });
  groupedFolder("frames/", t("treeFramesFolder"), "relFrames", (it) => {
    const n = frameCount(it.state.name);
    return treeNode(`${it.leaf}/`, n > 0 ? STR[lang].treeFrameCount(n) : t("treePending"), frameThumb(it.state.name), it.state.name);
  });
  if (run.anchorFiles && run.anchorFiles.length) {
    const aLi = liWith(fileUl, folderNode("references/anchors/", t("treeAnchorsFolder")));
    const aUl = chipList();
    for (const a of run.anchorFiles) {
      chipItem(aUl, treeNode(a.name, t("treeAnchorNote"), `${a.url}?v=${treeRevision || 0}`, null, "anchor"));
    }
    aLi.appendChild(aUl);
  }
  if (run.hasAtlas) {
    liWith(fileUl, treeNode("sprite-sheet-alpha.png", t("treeAtlasNote"), `/run/sprite-sheet-alpha.png?v=${treeRevision || 0}`, null));
  }

  const wrap = document.createElement("section");
  wrap.className = "state pipeline-tree";
  wrap.innerHTML =
    `<div class="state-head"><span class="name">${t("treeTitle")}</span>` +
    `<span class="meta tree-path" title="${escapeHtml(run.runDir)}">${escapeHtml(run.runDir)}</span></div>`;
  // 파이프라인 가지 곡선: CSS border 는 대시 애니메이션이 못 타므로 SVG 패스로.
  // rail(정적 옅은 액센트) 위로 dash 가 곡선을 따라 흘러내린다.
  const SVG_NS = "http://www.w3.org/2000/svg";
  const attachBranch = (li) => {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "branch");
    svg.setAttribute("viewBox", "0 0 15 19");
    svg.setAttribute("width", "15");
    svg.setAttribute("height", "19");
    const d = "M0.5 0 V11.5 Q0.5 18.5 7.5 18.5 H15";
    for (const cls of ["rail", "dash"]) {
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", d);
      path.setAttribute("class", cls);
      svg.appendChild(path);
    }
    li.insertBefore(svg, li.firstChild);
  };
  for (const li of chainUl.querySelectorAll("li")) attachBranch(li);

  const root = document.createElement("div");
  root.className = "tree";
  root.appendChild(block(t("treePipeline"), chainUl, "pipeline"));
  root.appendChild(block(t("treeFiles"), fileUl));
  wrap.appendChild(root);
  const existing = document.querySelector(".pipeline-tree");
  if (existing) existing.replaceWith(wrap);
  else document.getElementById("sidebar").appendChild(wrap);
}

// 폴더 노드 — SVG 폴더 아이콘 + 경로 라벨 (이모지 금지 규칙).
// 클릭 = 접기/펴기. 트리는 진행 폴링으로 재렌더되므로 접힘 상태를 라벨 키로 유지한다.
const collapsedFolders = new Set();

const FOLDER_ICON =
  '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
  '<path d="M1.5 4A1.5 1.5 0 0 1 3 2.5h2.6a1 1 0 0 1 .8.4l.9 1.1H13A1.5 1.5 0 0 1 14.5 5.5v6A1.5 1.5 0 0 1 13 13H3a1.5 1.5 0 0 1-1.5-1.5V4z" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';
// 파이프라인(흐름) 아이콘 — 위 노드에서 아래 노드로 흘러 내려가는 모양 (폴더와 구분)
const FLOW_ICON =
  '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
  '<circle cx="8" cy="3" r="1.7" fill="none" stroke="currentColor" stroke-width="1.2"/>' +
  '<path d="M8 4.7v4.1M5.6 8.9 8 11.3l2.4-2.4" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>' +
  '<circle cx="8" cy="13" r="1.7" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';

function folderNode(label, note, icon) {
  const node = document.createElement("span");
  node.className = "tree-node folder clickable";
  node.innerHTML =
    '<svg class="caret" viewBox="0 0 16 16" width="10" height="10" aria-hidden="true">' +
    '<path d="M5 3.5 10.5 8 5 12.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    (icon === "flow" ? FLOW_ICON : FOLDER_ICON);
  node.appendChild(Object.assign(document.createElement("span"), { className: "tn-label", textContent: label }));
  if (note) node.appendChild(Object.assign(document.createElement("span"), { className: "tn-note", textContent: note }));
  node.addEventListener("click", () => {
    const li = node.parentElement;
    const collapsed = !li.classList.contains("folder-collapsed");
    li.classList.toggle("folder-collapsed", collapsed);
    if (collapsed) collapsedFolders.add(label);
    else {
      collapsedFolders.delete(label);
      for (const ul of li.querySelectorAll(":scope > ul")) {
        ul.animate(
          [{ opacity: 0, transform: "translateY(-5px)" }, { opacity: 1, transform: "none" }],
          { duration: 190, easing: "ease" });
      }
    }
  });
  return node;
}

// 3초 폴링: 생성/추출 진행을 트리에 실시간 반영. 프레임 세대(runRevision)가 바뀌면
// 아래 상태 줄들은 구세대라 새로고침 배너를 띄운다 (편집 중 강제 리로드는 하지 않는다).
async function pollTreeProgress() {
  try {
    const res = await fetch("/api/progress");
    if (!res.ok) return;
    const next = await res.json();
    if (!next.states) return;
    const sig = JSON.stringify(next.states.map((p) => [p.name, p.raw, p.frames]));
    const prev = JSON.stringify([...treeProgress.entries()].map(([n, p]) => [n, p.raw, p.frames]));
    const revChanged = next.runRevision !== treeRevision;
    if (sig !== prev || revChanged) {
      treeProgress = new Map(next.states.map((p) => [p.name, p]));
      treeRevision = next.runRevision;
      renderPipelineTree();
      // 우측 상태 패널은 로드 시점 스냅샷이라 새 raw/프레임을 모른다 — 생성을
      // 지켜보는 중(최근 편집 없음 + 모달 안 열림)이면 통째로 새로고침해 동기화.
      // 편집 중이면 강제 리로드 대신 배너만 (자동저장이 보존하지만 흐름을 끊지 않게).
      const editing = Date.now() - lastEditAt < 15000 || document.getElementById("zoom-modal");
      if (!editing) {
        location.reload();
        return;
      }
    }
    if (next.runRevision !== run.runRevision) showReloadBanner();
  } catch {
    /* 서버 일시 중단은 조용히 재시도 */
  }
}

function showReloadBanner() {
  if (document.getElementById("reload-banner")) return;
  const banner = document.createElement("button");
  banner.id = "reload-banner";
  banner.type = "button";
  banner.textContent = t("reloadBanner");
  banner.addEventListener("click", () => location.reload());
  document.body.appendChild(banner);
}

function renderArchive(state) {
  const e = entries[state.name];
  const wrap = document.createElement("div");
  wrap.className = "archive-wrap";
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "ghost archive-chip";
  chip.setAttribute("data-tip", t("tArchiveChip"));
  chip.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M1.5 3h13v3h-13zM2.5 6v6.5A1 1 0 0 0 3.5 13.5h9a1 1 0 0 0 1-1V6M6 8.5h4" ' +
    'fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>' +
    `<span>${STR[lang].archiveChip(e.archived.length)}</span>`;
  chip.addEventListener("click", () => {
    if (e.archived.length) openArchiveModal(state.name);
  });
  wrap.appendChild(chip);
  if (e.archived.length === 0) wrap.classList.add("empty");
  return wrap;
}

// 보관함 풀 모달 — 일반 카드 크기로 크게 보고 버튼으로 복구 (팝오버 대체, UX)
function openArchiveModal(stateName) {
  document.getElementById("archive-modal")?.remove();
  const state = run.states.find((s) => s.name === stateName);
  const e = entries[stateName];
  if (!state || !e.archived.length) return;
  const modal = document.createElement("div");
  modal.id = "archive-modal";
  modal.innerHTML =
    `<div class="zoom-backdrop"></div>` +
    `<div class="card zoom-card arch-modal-card">` +
    `<div class="zoom-head"><span class="zoom-title">${STR[lang].archModalTitle(escapeHtml(stateName), e.archived.length)}</span>` +
    `<button type="button" class="ghost zoom-close">${t("zoomClose")}</button></div>` +
    `<div class="arch-grid"></div></div>`;
  const grid = modal.querySelector(".arch-grid");
  const frameByIdx = new Map(state.frames.map((f) => [f.index, f]));
  for (const idx of e.archived) {
    const f = frameByIdx.get(idx);
    if (!f) continue;
    const cardEl = document.createElement("div");
    cardEl.className = "card arch-card";
    cardEl.style.setProperty("--cell-aspect", run.cell.width / run.cell.height);
    cardEl.innerHTML =
      `<div class="card-top"><span class="idx">${f.label ? escapeHtml(f.label) : `#${idx}`}</span></div>` +
      `<div class="stage">` +
      (f.present ? `<img src="${escapeHtml(frameUrl(stateName, f))}" class="px-upscale" draggable="false" />` : `<div class="missing-label">${t("missingPending")}</div>`) +
      `</div>` +
      `<div class="card-controls">` +
      `<button type="button" class="ghost ar-seq">${t("restoreToSeq")}</button>` +
      `<button type="button" class="ghost ar-pool">${t("restoreToPool")}</button>` +
      `</div>`;
    const restore = (toSeq) => {
      restoreFrame(stateName, idx, toSeq);
      if (entries[stateName].archived.length) openArchiveModal(stateName);
      else close();
    };
    cardEl.querySelector(".ar-seq").addEventListener("click", () => restore(true));
    cardEl.querySelector(".ar-pool").addEventListener("click", () => restore(false));
    grid.appendChild(cardEl);
  }
  const close = () => {
    modal.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (ev) => { if (ev.key === "Escape") close(); };
  modal.querySelector(".zoom-close").addEventListener("click", close);
  modal.querySelector(".zoom-backdrop").addEventListener("click", close);
  document.addEventListener("keydown", onKey);
  document.body.appendChild(modal);
}

// 팝오버 미니카드를 끌어내// 팝오버 미니카드를 끌어내 시퀀스/후보 존에 떨어뜨리면 복구
function wireArchiveRestoreDrag(mini, stateName, idx) {
  mini.addEventListener("pointerdown", (ev) => {
    if (ev.button || !ev.isPrimary) return;
    ev.preventDefault();
    const startX = ev.clientX;
    const startY = ev.clientY;
    let ghost = null;
    const onMove = (e2) => {
      if (!ghost) {
        if (Math.abs(e2.clientX - startX) <= DRAG_THRESHOLD && Math.abs(e2.clientY - startY) <= DRAG_THRESHOLD) return;
        ghost = mini.cloneNode(true);
        ghost.classList.add("ap-ghost");
        document.body.appendChild(ghost);
      }
      ghost.style.left = `${e2.clientX + 8}px`;
      ghost.style.top = `${e2.clientY + 8}px`;
    };
    const end = (e2) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      if (!ghost) return; // 클릭만 한 것 — 아무 일 없음
      ghost.remove();
      const sectionSel = `.state[data-state="${cssEscape(stateName)}"]`;
      const seq = document.querySelector(`${sectionSel} .seq-frames`);
      const pool = document.querySelector(`${sectionSel} .pool-frames`);
      const over = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return e2.clientX >= r.left && e2.clientX <= r.right && e2.clientY >= r.top && e2.clientY <= r.bottom;
      };
      if (over(seq)) restoreFrame(stateName, idx, true);
      else if (over(pool)) restoreFrame(stateName, idx, false);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  });
}

// 최상단 base 참조 줄 — 아이덴티티 truth 를 생성 결과와 나란히 비교하기 위한
// 읽기 전용 표시 (선택/변형/굽기와 무관).
function renderBaseRow() {
  const wrap = document.createElement("section");
  wrap.className = "state base-row";
  wrap.innerHTML =
    `<div class="state-head"><h3>base</h3>` +
    `<span class="muted">${t("baseNote")}</span>` +
    `<button type="button" class="ghost base-edit-btn" data-tip="${t("tBaseEdit")}">✎ ${t("baseEditBtn")}</button></div>` +
    `<div class="base-stage"><img src="${escapeHtml(run.baseUrl)}" alt="base source" draggable="false" /></div>`;
  const editBtn = wrap.querySelector(".base-edit-btn");
  editBtn.addEventListener("click", async () => {
    // 격자 검출(첫 회 수 초) + 논리 이미지 빌드 동안 버튼 스피너 — 멈춘 것처럼 보이지 않게
    if (editBtn.disabled) return;
    editBtn.disabled = true;
    const label = editBtn.innerHTML;
    editBtn.innerHTML = '<span class="tween-spin" aria-label="loading"></span>';
    try {
      await openBaseEditor();
    } finally {
      editBtn.innerHTML = label;
      editBtn.disabled = false;
    }
  });
  document.getElementById("states").appendChild(wrap);
}

// ── 베이스 편집 = 줌 모달과 같은 컴포넌트 (수홍 지시 2026-07-17 "같은 컴포넌트를
// 쓰라" — 별도 모달 구현은 폐기). 검출 격자의 논리 해상도로 가상 상태 "__base__" 를
// 만들어 openZoom 으로 연다. 도구/단축키/마키/줌/팬 전부 프레임 편집과 단일 코드.
async function openBaseEditor() {
  let grid = null;
  try {
    grid = (await (await fetch("/api/base-grid")).json()).grid || null;
  } catch { grid = null; }
  if (!grid) {
    setStatus(t("baseEditFail") + "no confident pixel grid on the base", "err");
    return;
  }
  const rawUrl = run.baseUrl + (run.baseUrl.includes("?") ? "&" : "?") + "edit=" + Date.now();
  // 진짜 격자 기반 논리 이미지 (수홍 지적 2026-07-17: 균일 등분 격자는 이미지와
  // 어긋난다): 검출 절단선(xEdges/yEdges)의 블록 "중심"을 raw 에서 샘플해 논리
  // 해상도 PNG 를 만든다. 이후 모달의 표시·편집·격자·팔레트는 전부 이 균일 논리
  // 공간이라 프레임과 동일하게 정확히 떨어진다. raw 는 pp OFF 의 원본 뷰(plain
  // twin 자리)로 쓴다. 저장(논리 ops→raw 확장)은 서버가 같은 절단선으로 한다.
  const rawImg = new Image();
  rawImg.src = rawUrl;
  await new Promise((ok, err) => { rawImg.onload = ok; rawImg.onerror = err; });
  const cols = grid.xEdges.length - 1;
  const rows = grid.yEdges.length - 1;
  const probe = document.createElement("canvas");
  probe.width = rawImg.naturalWidth;
  probe.height = rawImg.naturalHeight;
  const pc = probe.getContext("2d");
  pc.drawImage(rawImg, 0, 0);
  const raw = pc.getImageData(0, 0, probe.width, probe.height).data;
  const logical = document.createElement("canvas");
  logical.width = cols;
  logical.height = rows;
  const lc = logical.getContext("2d");
  const out = lc.createImageData(cols, rows);
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const cx = Math.floor((grid.xEdges[i] + grid.xEdges[i + 1]) / 2);
      const cy = Math.floor((grid.yEdges[j] + grid.yEdges[j + 1]) / 2);
      const s = (cy * probe.width + cx) * 4;
      const d = (j * cols + i) * 4;
      out.data[d] = raw[s];
      out.data[d + 1] = raw[s + 1];
      out.data[d + 2] = raw[s + 2];
      out.data[d + 3] = 255;
    }
  }
  lc.putImageData(out, 0, 0);
  baseView = { cols, rows, xEdges: grid.xEdges, yEdges: grid.yEdges,
               url: logical.toDataURL("image/png"), rawUrl };
  baseLogicalImg = new Image();
  baseLogicalImg.src = baseView.url;
  await new Promise((ok) => { baseLogicalImg.onload = ok; });
  if (!entries[BASE_STATE]) {
    entries[BASE_STATE] = { pixels: {}, transforms: {}, order: [0], sel: new Set([0]),
                            clones: {}, archived: [] };
  }
  openZoom(BASE_STATE, 0);
}

function renderCard(state, frame) {
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.state = state.name;
  card.dataset.idx = frame.index;
  if (!frame.present) card.classList.add("missing");
  card.style.setProperty("--cell-aspect", run.cell.width / run.cell.height);

  const stageInner = frame.present
    ? (run.iso ? `<canvas class="grid-overlay"></canvas>` : "") +
      `<div class="pxgrid"></div>` +
      `<canvas class="ingrid"></canvas>` +
      `<img src="${escapeHtml(frameUrl(state.name, frame))}" alt="frame ${frame.index}" draggable="false" />` +
      `<canvas class="snap-canvas"></canvas>` +
      `<div class="rotate-handle" data-tip="${t("tRotate")}"></div>` +
      `<div class="shear-handle" data-tip="${t("tShear")}"></div>`
    : `<div class="missing-label">${state.rawPresent ? t("missingRawWait") : t("missingPending")}</div>`;

  const isClone = frame.clone !== undefined;
  const srcName = isClone ? frameDisplayName(state.name, frame.clone) : null;
  const shortLabel = isClone ? STR[lang].cloneBadge(srcName) : (frame.label ? frame.label : `#${frame.index}`);
  // 풀네임(복사 대상) — 에이전트가 그대로 집어가도록 런-상대 파일 경로 + 라벨.
  const relPath = (frame.url || "").replace(/^\/run\//, "");
  const fullName = isClone ? `${srcName} 복제 · ${relPath}` : [frame.label, relPath].filter(Boolean).join(" · ") || shortLabel;
  const titleCls = isClone ? "idx clone-badge" : "idx";
  const title = `<span class="${titleCls}" data-tip="${escapeHtml(fullName)}" data-tip-copy>${escapeHtml(shortLabel)}</span>`;
  // 아이콘 SVG — 이모지 대신 라인 아이콘 (플랫폼별 렌더 편차·저품질 방지)
  const dupIcon =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<rect x="5.5" y="5.5" width="8.2" height="8.2" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.3"/>' +
    '<path d="M3.4 10.4H2.9A1 1 0 0 1 1.9 9.4V3A1.1 1.1 0 0 1 3 1.9h6.4a1 1 0 0 1 1 1v0.5" ' +
    'fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>';
  const zoomIcon =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<circle cx="6.8" cy="6.8" r="4.3" fill="none" stroke="currentColor" stroke-width="1.3"/>' +
    '<path d="M10 10 14 14M5 6.8h3.6M6.8 5v3.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>';
  const archIcon =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M1.8 3.2h12.4v3H1.8zM2.9 6.2v6.4A1 1 0 0 0 3.9 13.6h8.2a1 1 0 0 0 1-1V6.2M6.2 8.6h3.6" ' +
    'fill="none" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round"/></svg>';
  const psize = frame.present && frame.contentSize
    ? `<span class="psize" data-tip="${t("tContentPx")}">${frame.contentSize[0]}×${frame.contentSize[1]}</span>` : "";
  card.innerHTML =
    // 헤더: 복제 | 타이틀(드래그 핸들, 호버 시 풀네임 복사) | 확대 —
    // 복제를 좌측으로 분리 (수홍 지시 2026-07-17: 확대 옆에 붙어 있어 오클릭).
    `<div class="card-top"${frame.present ? ` data-tip="${t("tReorder")}"` : ""}>` +
    `<span class="ct-left">` +
    (frame.present
      ? `<button type="button" class="ghost dup-btn" data-tip="${t("tDupBtn")}" aria-label="duplicate">${dupIcon}</button>`
      : "") +
    `${title}</span>` +
    (frame.present
      ? `<span class="ct-right">` +
        `<button type="button" class="ghost zoom-btn" data-tip="${t("tZoomOpen")}" aria-label="zoom">${zoomIcon}</button>` +
        `</span>`
      : "") +
    `</div>` +
    `<div class="stage">${stageInner}</div>` +
    // 푸터 2층: (1) 정보 — 크기·변형값 / (2) 버튼 — 반전·초기화 · 넣기빼기·보관
    (frame.present
      ? `<div class="card-info">${psize}<span class="tvals"></span></div>` +
        `<div class="card-controls">` +
        `<button type="button" class="ghost flip-btn" data-tip="${t("tFlipX")}" aria-label="flip-x">↔</button>` +
        `<button type="button" class="ghost reset-btn" data-tip="${t("tReset")}" aria-label="reset">↺</button>` +
        `<span class="ctrl-group">` +
        `<button type="button" class="sel-btn"></button>` +
        `<button type="button" class="ghost arch-btn" data-tip="${t("tArchiveBtn")}" aria-label="archive">${archIcon}</button>` +
        `</span>` +
        `</div>`
      : "") +
    "";

  if (frame.present) {
    // 픽셀아트 확대 표시: 프레임 원본보다 크게 그려질 때만 pixelated (다운스케일 회화체는 부드럽게 유지)
    const imgEl = card.querySelector(".stage img");
    if (imgEl) {
      const markPx = () =>
        requestAnimationFrame(() => {
          if (imgEl.naturalWidth && imgEl.clientWidth > imgEl.naturalWidth) imgEl.classList.add("px-upscale");
        });
      if (imgEl.complete) markPx();
      else imgEl.addEventListener("load", markPx, { once: true });
    }
    card.querySelector(".reset-btn").addEventListener("click", () =>
      resetTransform(state.name, frame.index)
    );
    card.querySelector(".flip-btn").addEventListener("click", () =>
      toggleFlipX(state.name, frame.index)
    );
    // 확대 모달 진입: 헤더의 ⛶ 버튼 (pointerdown 전파를 끊어 헤더 드래그/클릭 토글과
    // 충돌하지 않게) + 스테이지 더블클릭.
    const zoomBtn = card.querySelector(".zoom-btn");
    if (zoomBtn) {
      zoomBtn.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      zoomBtn.addEventListener("click", () => openZoom(state.name, frame.index));
    }
    card.querySelector(".stage").addEventListener("dblclick", () => openZoom(state.name, frame.index));
    const dupBtn = card.querySelector(".dup-btn");
    if (dupBtn) {
      dupBtn.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      dupBtn.addEventListener("click", () => duplicateFrame(state.name, frame.index));
    }
    const cloneBadge = card.querySelector(".clone-badge");
    if (cloneBadge && frame.clone !== undefined) {
      // 복제 배지 클릭 = 원본 카드로 이동 + 반짝 — "어떤 프레임의 복제인지" 시각 연결
      cloneBadge.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      cloneBadge.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const srcCard = document.querySelector(
          `#states .card[data-state="${cssEscape(state.name)}"][data-idx="${frame.clone}"]`);
        if (!srcCard) return;
        srcCard.scrollIntoView({ behavior: "smooth", block: "center" });
        srcCard.classList.remove("flash-target");
        void srcCard.offsetWidth;
        srcCard.classList.add("flash-target");
        srcCard.addEventListener("animationend", () => srcCard.classList.remove("flash-target"), { once: true });
      });
    }
    const archBtn = card.querySelector(".arch-btn");
    archBtn.addEventListener("pointerdown", (ev) => ev.stopPropagation());
    archBtn.addEventListener("click", () => archiveFrame(state.name, frame.index));
    // 넣기/빼기 = 시퀀스⇄풀 토글의 유일한 버튼 (드래그 외). 푸터에 있어 헤더 드래그와
    // 무관하지만, 실수 드래그 시작을 막으려 pointerdown 전파를 끊는다.
    const selBtn = card.querySelector(".sel-btn");
    selBtn.addEventListener("pointerdown", (ev) => ev.stopPropagation());
    selBtn.addEventListener("click", () => moveCardToOtherZone(card, state.name));
    card.querySelector(".stage").appendChild(makeScaleScrub(state.name, frame.index));
  }
  return card;
}

/** Toggle horizontal flip for a single frame (Alex 2026-05-28). */
function toggleFlipX(stateName, idx) {
  const entry = entries[stateName];
  if (!entry) return;
  if (!entry.transforms[idx]) entry.transforms[idx] = IDENTITY();
  entry.transforms[idx].flipX = entry.transforms[idx].flipX ? 0 : 1;
  // 모든 스테이지에 거울 반전을 렌더하고 flip 버튼을 강조한다.
  applyFrameTransformAll(stateName, idx);
  scheduleSave();
}

function renderPreview(state) {
  const box = document.createElement("div");
  box.className = "preview";
  box.dataset.state = state.name;
  const aspect = run.cell.height / run.cell.width;
  const speedOpts = [0.25, 0.5, 1, 2, 4]
    .map((v) => `<option value="${v}"${v === 1 ? " selected" : ""}>×${v}</option>`)
    .join("");
  // 위치 표시(pv-pos)를 캔버스·프레임수 바로 밑(컨트롤 위)으로 — 재생 컨트롤 아래
  // 뚝 떨어져 있어 캔버스와 멀고 헷갈렸다 (수홍 2026-07-15, 쿠마피커 pv-pos 지정).
  box.innerHTML =
    `<h4>${t("preview")}</h4>` +
    `<canvas${run.cell.width < 160 ? ' class="px-upscale"' : ""} width="${run.cell.width}" height="${run.cell.height}" style="height:${(160 * aspect).toFixed(0)}px"></canvas>` +
    `<div class="count"></div>` +
    `<div class="pv-pos"></div>` +
    `<div class="pv-controls">` +
    `<button type="button" class="ghost pv-prev" data-tip="${t("tPrev")}">⏮</button>` +
    `<button type="button" class="ghost pv-play" data-tip="${t("tPause")}">⏸</button>` +
    `<button type="button" class="ghost pv-next" data-tip="${t("tNext")}">⏭</button>` +
    `<select class="pv-speed" name="speed-${escapeHtml(state.name)}" aria-label="${t("tSpeed")}" data-tip="${t("tSpeed")}">${speedOpts}</select>` +
    `</div>`;
  return box;
}

function startPreview(state) {
  const root = document.querySelector(`.preview[data-state="${cssEscape(state.name)}"]`);
  const canvas = root.querySelector("canvas");
  const ctx = canvas.getContext("2d");
  const cw = run.cell.width;
  const ch = run.cell.height;
  const playBtn = root.querySelector(".pv-play");
  const posEl = root.querySelector(".pv-pos");
  const pv = (previews[state.name] = { playing: true, speed: 1, cursor: 0, shown: -1 });
  let last = 0;

  const syncPlayBtn = () => {
    playBtn.textContent = pv.playing ? "⏸" : "▶";
    playBtn.setAttribute("data-tip", pv.playing ? t("tPause") : t("tPlay"));
  };

  // draw the frame at the current cursor; runs every rAF so live transform
  // edits show even while paused. The matrix matches CSS + the compose bake.
  const draw = () => {
    const play = playList(state.name);
    ctx.clearRect(0, 0, cw, ch);
    if (!play.length) {
      posEl.textContent = "0/0";
      return;
    }
    pv.cursor = ((pv.cursor % play.length) + play.length) % play.length;
    const idx = play[pv.cursor];
    pv.shown = idx; // remember which frame is on screen (for reanchoring on edits)
    const f = frameOf(state.name, idx); // 복제 인스턴스 → 원본 이미지
    const image = f ? img(frameUrl(state.name, f)) : null;
    if (image && image.complete && image.naturalWidth) {
      const tr = getTransform(state.name, idx);
      // 픽셀퍼펙트 줄은 카드와 동일하게 격자 재양자화로 그린다 (프리뷰 = 굽기)
      drawFrameInto(ctx, image, tr, cw, ch, snapScaleFor(state.name), getPixelOps(state.name, idx));
    }
    posEl.textContent = `${pv.cursor + 1}/${play.length} · #${idx}`;
  };

  const step = (delta) => {
    pv.playing = false;
    syncPlayBtn();
    const play = playList(state.name);
    if (play.length) pv.cursor = (pv.cursor + delta + play.length) % play.length;
    draw();
  };
  root.querySelector(".pv-prev").addEventListener("click", () => step(-1));
  root.querySelector(".pv-next").addEventListener("click", () => step(1));
  playBtn.addEventListener("click", () => {
    pv.playing = !pv.playing;
    syncPlayBtn();
  });
  root.querySelector(".pv-speed").addEventListener("change", (e) => {
    pv.speed = parseFloat(e.target.value) || 1;
  });

  // Called after the selection/order changes (move between rows, reorder). Keeps
  // the on-screen frame in view instead of jumping (re-anchor by frame index),
  // and disables the transport when the sequence is empty (nothing to play).
  const prevBtn = root.querySelector(".pv-prev");
  const nextBtn = root.querySelector(".pv-next");
  pv.refresh = () => {
    const play = playList(state.name);
    if (!play.length) {
      pv.cursor = 0;
    } else {
      const p = play.indexOf(pv.shown);
      pv.cursor = p >= 0 ? p : ((pv.cursor % play.length) + play.length) % play.length;
    }
    const empty = play.length === 0;
    prevBtn.disabled = empty;
    nextBtn.disabled = empty;
    playBtn.disabled = empty;
    draw();
  };
  pv.refresh();

  function frame(ts) {
    if (!root.isConnected) return; // 섹션이 교체/제거되면 이 루프는 은퇴
    const play = playList(state.name);
    if (pv.playing && play.length) {
      const interval = 1000 / Math.max(0.1, state.fps * pv.speed);
      if (ts - last >= interval) {
        last = ts;
        pv.cursor = (pv.cursor + 1) % play.length;
      }
    }
    draw();
    requestAnimationFrame(frame);
  }
  syncPlayBtn();
  requestAnimationFrame(frame);
}

// --- 확대 편집 모달 — 한 프레임을 크게 띄워 격자/픽셀퍼펙트를 켜가며 조정 -----
// 같은 entries/transform truth 를 쓰므로 모달 편집이 그리드 카드에 실시간 반영된다.
// 휠/핀치 = 화면 배율(뷰 확대), 드래그/핸들/Shift+휠 = 기존 스프라이트 편집 그대로.
let zoomView = null; // { stateName, idx, width }

function closeZoom() {
  if (zoomView && zoomView.cleanupBreathe) zoomView.cleanupBreathe();
  pixelEdit = null;
  const modal = document.getElementById("zoom-modal");
  if (modal) modal.remove();
  zoomView = null;
  document.removeEventListener("keydown", onZoomKey);
}

// ── 베이스 = 줌 모달과 같은 컴포넌트 (수홍 지시 2026-07-17 "같은 컴포넌트를 쓰라") ──
// 가상 상태 "__base__" 로 줌 모달을 연다. 편집 공간 = 검출 격자의 논리 해상도
// (예: 28×48) — 프레임과 동일한 조작감. 저장은 사이드카가 아니라 명시 버튼으로
// /api/base-edit (space:"logical") 에 굽고, 서버가 논리→raw 블록 확장을 맡는다.
const BASE_STATE = "__base__";
let baseView = null; // {cols, rows, xEdges, yEdges, url(논리 PNG), rawUrl} — 베이스 뷰 상태
let baseLogicalImg = null; // 논리 이미지 엘리먼트 — 편집/합성/샘플의 단일 소스

// 편집·합성 소스: 베이스는 항상 논리 이미지 (pp OFF 로 raw 를 "보고" 있어도
// 편집 공간은 논리 격자 — 균일 좌표가 이미지와 어긋나는 문제의 원천 차단).
function editSourceFor(stateName, imgEl) {
  return stateName === BASE_STATE && baseLogicalImg ? baseLogicalImg : imgEl;
}

function cellDims(stateName) {
  if (stateName === BASE_STATE && baseView) return [baseView.cols, baseView.rows];
  return [run.cell.width, run.cell.height];
}

// ── 공용 툴 아이콘 (SVG 라인 아이콘 — 이모지 금지, 양 편집기 공용) ──
const TOOL_ICONS = {
  pen: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="m2 14 .8-3.2L11 2.6l2.4 2.4L5.2 13.2 2 14z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  eraser: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M9.5 2.5 2.8 9.2a1 1 0 0 0 0 1.4l2.6 2.6h4.1l4-4a1 1 0 0 0 0-1.4L9.5 2.5zM5.5 13.2 9.9 8.8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  pick: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M10.6 2a1.9 1.9 0 0 1 2.7 2.7l-1 1 .5.5-1.1 1.1-.5-.5-4.3 4.3-2.4.6.6-2.4 4.3-4.3-.5-.5L10 6.4l-.5-.5 1.1-1.1.5.5 1-1z" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linejoin="round"/></svg>',
  select: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.2" stroke-dasharray="2.4 1.7"/></svg>',
};

// ── 뷰 패닝 — Space+드래그 / 휠버튼(중클릭) 드래그로 화면을 끈다 (수홍 지시
// 2026-07-17: 스프라이트 이동과 별개인 캔버스 시야 이동). 스크롤 컨테이너를
// 당기는 방식이라 페인트/마키보다 먼저(capture) 가로챈다.
let panSpaceHeld = false;
document.addEventListener("keydown", (ev) => {
  if (ev.code !== "Space") return;
  if (!document.getElementById("zoom-modal")) return;
  if (ev.target && ev.target.closest && ev.target.closest("input, textarea, select, button")) return;
  panSpaceHeld = true;
  ev.preventDefault();
});
document.addEventListener("keyup", (ev) => { if (ev.code === "Space") panSpaceHeld = false; });

function wirePan(surface, container) {
  surface.addEventListener("pointerdown", (ev) => {
    if (!(ev.button === 1 || (ev.button === 0 && panSpaceHeld))) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const sx = ev.clientX, sy = ev.clientY;
    const sl = container.scrollLeft, st = container.scrollTop;
    const prevCursor = surface.style.cursor;
    surface.style.cursor = "grabbing";
    const onMove = (e2) => {
      container.scrollLeft = sl - (e2.clientX - sx);
      container.scrollTop = st - (e2.clientY - sy);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      surface.style.cursor = prevCursor;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, true);
}

function clearMarquee() {
  if (pixelEdit) pixelEdit.sel = null;
  document.querySelectorAll(".marquee").forEach((m) => { m.hidden = true; });
}

function onZoomKey(ev) {
  if (ev.key === "Escape") {
    if (pixelEdit && pixelEdit.sel) { clearMarquee(); return; } // 선택 해제가 우선
    closeZoom();
  }
  else if (ev.key === "ArrowLeft") stepZoomFrame(-1);
  else if (ev.key === "ArrowRight") stepZoomFrame(1);
}

// 실행취소/재실행 단축키 — Cmd/Ctrl+Z, Cmd/Ctrl+Shift+Z (수홍 지시 2026-07-17).
// 활성 편집 컨텍스트로 라우팅: 베이스 에디터 모달이 열려 있으면 그쪽, 아니면
// 줌 모달의 픽셀 편집 세션. 입력 필드에선 브라우저 기본 동작 유지.
document.addEventListener("keydown", (ev) => {
  if (ev.key.toLowerCase() !== "z" || !(ev.metaKey || ev.ctrlKey)) return;
  if (ev.target && ev.target.closest && ev.target.closest("input, textarea, select")) return;
  // 베이스도 줌 모달(pixelEdit)로 통일 — 별도 에디터 분기는 폐기됨 (v1.56.24)
  const target = pixelEdit && pixelEdit.undoFn
    ? { undo: pixelEdit.undoFn, redo: pixelEdit.redoFn } : null;
  if (!target) return;
  ev.preventDefault();
  ev.stopImmediatePropagation();
  if (ev.shiftKey) { if (target.redo) target.redo(); }
  else if (target.undo) target.undo();
}, true);

function stepZoomFrame(delta) {
  if (!zoomView || zoomView.stateName === BASE_STATE) return; // 베이스 = 단일 뷰
  // 표시 순서(order)를 따라 넘긴다 — 복제 인스턴스 카드도 순회에 포함
  const e = entries[zoomView.stateName];
  const present = e.order.filter((i) => {
    const f = frameOf(zoomView.stateName, i);
    return f && f.present;
  });
  if (!present.length) return;
  const pos = present.indexOf(zoomView.idx);
  openZoom(zoomView.stateName, present[(pos + delta + present.length) % present.length], zoomView.width);
}

function openZoom(stateName, idx, keepWidth) {
  closeZoom();
  const frame = frameOf(stateName, idx); // 복제 인스턴스 → 원본 이미지, 자기 변형
  if (!frame || !frame.present) return;
  const isBase = stateName === BASE_STATE; // 베이스도 같은 컴포넌트로 연다 (수홍 지시 2026-07-17)
  const [cellW, cellH] = cellDims(stateName);
  const aspect = cellH / cellW;
  const width = keepWidth
    || Math.min(Math.floor(window.innerWidth * 0.8), Math.floor((window.innerHeight * 0.72) / aspect));
  zoomView = { stateName, idx, width };

  const modal = document.createElement("div");
  modal.id = "zoom-modal";
  const label = frame.clone !== undefined
    ? `⧉ ${escapeHtml(STR[lang].cloneBadge(frame.clone))}`
    : (frame.label ? escapeHtml(frame.label) : `#${idx}`);
  modal.innerHTML =
    `<div class="zoom-backdrop"></div>` +
    `<div class="card zoom-card" data-state="${escapeHtml(stateName)}" data-idx="${idx}">` +
    `<div class="zoom-head">` +
    `<span class="zoom-title">${escapeHtml(isBase ? "base" : stateName)}${isBase ? ` · ${cellW}×${cellH}` : ` · ${label}`}</span>` +
    `<span class="row-controls"></span>` +
    (isBase ? "" :
      `<button type="button" class="ghost zoom-prev" data-tip="${t("tZoomPrev")}">⏮</button>` +
      `<button type="button" class="ghost zoom-next" data-tip="${t("tZoomNext")}">⏭</button>`) +
    `<button type="button" class="ghost zoom-close">${t("zoomClose")}</button>` +
    `</div>` +
    `<div class="stage" data-tip="${t("tZoomStage")}">` +
    `<div class="pxgrid"></div>` +
    `<canvas class="ingrid"></canvas>` +
    `<img src="${escapeHtml(frameUrl(stateName, frame))}" alt="frame ${idx}" draggable="false" class="px-upscale" />` +
    `<canvas class="snap-canvas"></canvas>` +
    `<div class="rotate-handle" data-tip="${t("tRotate")}"></div>` +
    `<div class="shear-handle" data-tip="${t("tShear")}"></div>` +
    `</div>` +
    `<div class="card-controls">` +
    `<span class="psize" data-tip="${t("tContentPx")}">${frame.contentSize ? `${frame.contentSize[0]}x${frame.contentSize[1]}px` : ""}</span>` +
    `<span class="tvals"></span>` +
    `<button type="button" class="ghost flip-btn" data-tip="${t("tFlipX")}" aria-label="flip-x">↔</button>` +
    `<button type="button" class="ghost reset-btn" data-tip="${t("tReset")}">↺</button>` +
    `</div>` +
    `</div>`;
  document.body.appendChild(modal);

  const card = modal.querySelector(".zoom-card");
  card.style.setProperty("--cell-aspect", cellW / cellH);
  const stage = card.querySelector(".stage");
  stage.style.width = `${width}px`;

  // 컨트롤: 줄별 토글과 같은 클래스 → sync*Controls 가 카드/모달을 함께 갱신
  const controls = card.querySelector(".row-controls");
  // 완전 동일 계약 (수홍 지시 2026-07-17 "다 똑같이"): 픽셀퍼펙트·격자·변형은
  // 베이스에도 전부. 프레임 전용은 GIF/이전·다음(다중 프레임 개념)뿐.
  if (isBase || gridCapableStates.has(stateName)) controls.appendChild(makeGridToggle(stateName));
  if (isBase || ppTwinStates.has(stateName)) controls.appendChild(makePpToggle(stateName));
  if (!isBase) {
    controls.appendChild(makeGifButton(stateName));
    // 보간 버튼은 줄 헤더 전용 — 확대뷰에선 카드 픽이 불가능해 쓸 수 없다 (수홍 2026-07-17).
    card.querySelector(".zoom-prev").addEventListener("click", () => stepZoomFrame(-1));
    card.querySelector(".zoom-next").addEventListener("click", () => stepZoomFrame(1));
  }
  card.querySelector(".reset-btn").addEventListener("click", () => resetTransform(stateName, idx));
  card.querySelector(".flip-btn").addEventListener("click", () => toggleFlipX(stateName, idx));
  stage.appendChild(makeScaleScrub(stateName, idx));
  card.querySelector(".zoom-close").addEventListener("click", closeZoom);
  modal.querySelector(".zoom-backdrop").addEventListener("click", closeZoom);
  wirePan(stage, card); // Space+드래그/휠버튼 드래그 = 뷰 패닝 (프레임·베이스 공통)

  // ── 픽셀 편집 툴바: 연필/지우개 + 프레임 팔레트 + 컬러피커 + 되돌리기/비우기 ──
  const toolbar = document.createElement("div");
  toolbar.className = "edit-toolbar";
  toolbar.innerHTML =
    `<button type="button" class="ghost et-pen" data-tip="${t("penTool")}">` +
    `${TOOL_ICONS.pen}</button>` +
    `<button type="button" class="ghost et-eraser" data-tip="${t("eraserTool")}">${TOOL_ICONS.eraser}</button>` +
    `<button type="button" class="ghost et-pick" data-tip="${t("tPick")}">${TOOL_ICONS.pick}</button>` +
    `<button type="button" class="ghost et-select" data-tip="${t("tSelectTool")}">${TOOL_ICONS.select}</button>` +
    `<button type="button" class="ghost et-undo" data-tip="${t("tUndoKeys")}">${t("undoEdit")}</button>` +
    `<button type="button" class="ghost et-redo" data-tip="${t("tRedoKeys")}">${t("redoEdit")}</button>` +
    `<button type="button" class="ghost et-clear">${t("clearEdits")}</button>`;
  card.insertBefore(toolbar, stage);
  if (isBase) {
    // 베이스는 사이드카가 아니라 파일에 굽는다 — 명시 저장 (원본 .orig 1회 백업)
    const saveBtn = document.createElement("button");
    saveBtn.className = "et-basesave";
    saveBtn.setAttribute("data-tip", t("tBaseEdit"));
    saveBtn.textContent = t("baseEditSave");
    saveBtn.addEventListener("click", async () => {
      const ops = (entries[BASE_STATE].pixels && entries[BASE_STATE].pixels[0]) || {};
      const tr = entries[BASE_STATE].transforms[0];
      const trDirty = tr && (tr.rotate || tr.scale !== 1 || tr.dx || tr.dy || tr.shx || tr.shy || tr.flipX);
      if (!Object.keys(ops).length && !trDirty) { closeZoom(); return; }
      saveBtn.disabled = true;
      try {
        const res = await fetch("/api/base-edit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ops, space: "logical", transform: trDirty ? tr : null }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || res.status);
        entries[BASE_STATE].pixels = {};
        entries[BASE_STATE].transforms = {}; // 변형은 파일에 구워졌다 — 표시 변형 초기화
        pixelEdit = null;
        setStatus(STR[lang].baseEditSaved(data.applied), "ok");
        closeZoom();
        const baseImg = document.querySelector(".base-row .base-stage img");
        if (baseImg) baseImg.src = run.baseUrl + (run.baseUrl.includes("?") ? "&" : "?") + "v=" + Date.now();
      } catch (e) {
        setStatus(t("baseEditFail") + e.message, "err");
        saveBtn.disabled = false;
      }
    });
    toolbar.appendChild(saveBtn);
  }
  const penBtn = toolbar.querySelector(".et-pen");
  const eraserBtn = toolbar.querySelector(".et-eraser");
  const pickBtn = toolbar.querySelector(".et-pick");
  const selectBtn = toolbar.querySelector(".et-select");
  // ── 팔레트 도크 (Aseprite 식, 수홍 지시 2026-07-17): 스테이지 왼쪽 세로 팔레트 —
  // 위 = 현재 쓰인 색 전부 (같은 색 1개, 빈도순), 아래 = 자유 색상 피커.
  const stageRow = document.createElement("div");
  stageRow.className = "stage-row";
  stage.parentNode.insertBefore(stageRow, stage);
  const dock = document.createElement("div");
  dock.className = "palette-dock";
  const swatchBox = document.createElement("div");
  swatchBox.className = "palette-swatches";
  const colorInput = document.createElement("input");
  colorInput.type = "color";
  colorInput.value = "#1f2430";
  colorInput.title = "color";
  colorInput.className = "et-color";
  dock.appendChild(swatchBox);
  dock.appendChild(colorInput);
  stageRow.appendChild(dock);
  stageRow.appendChild(stage);
  const selBox = document.createElement("div");
  selBox.className = "marquee";
  selBox.hidden = true;
  stage.appendChild(selBox);
  const syncMarqueeBox = () => {
    const sel = pixelEdit && pixelEdit.sel;
    selBox.hidden = !sel;
    if (!sel) return;
    selBox.style.left = `${(sel.x0 / cellW) * 100}%`;
    selBox.style.top = `${(sel.y0 / cellH) * 100}%`;
    selBox.style.width = `${((sel.x1 - sel.x0) / cellW) * 100}%`;
    selBox.style.height = `${((sel.y1 - sel.y0) / cellH) * 100}%`;
  };
  const syncToolbar = () => {
    penBtn.classList.toggle("active", !!pixelEdit && pixelEdit.tool === "pen");
    eraserBtn.classList.toggle("active", !!pixelEdit && pixelEdit.tool === "eraser");
    pickBtn.classList.toggle("active", !!pixelEdit && pixelEdit.tool === "pick");
    selectBtn.classList.toggle("active", !!pixelEdit && pixelEdit.tool === "select");
    stage.classList.toggle("pixel-editing", !!pixelEdit);
    stage.classList.toggle("picking", !!pixelEdit && pixelEdit.tool === "pick");
    stage.classList.toggle("selecting", !!pixelEdit && pixelEdit.tool === "select");
    syncMarqueeBox();
  };
  const setTool = (tool) => {
    if (pixelEdit && pixelEdit.tool === tool) pixelEdit = null; // 같은 툴 재클릭 = 끔
    else pixelEdit = { state: stateName, idx, tool, color: colorInput.value,
                       journal: (pixelEdit && pixelEdit.journal) || [],
                       redo: (pixelEdit && pixelEdit.redo) || [],
                       sel: tool === "select" ? (pixelEdit && pixelEdit.sel) || null : null,
                       undoFn: () => undoPixel(), redoFn: () => redoPixel() };
    syncToolbar();
    applyFrameTransformAll(stateName, idx); // 편집 모드 = identity 표시 전환
  };
  penBtn.addEventListener("click", () => setTool("pen"));
  eraserBtn.addEventListener("click", () => setTool("eraser"));
  pickBtn.addEventListener("click", () => setTool("pick"));
  selectBtn.addEventListener("click", () => setTool("select"));
  colorInput.addEventListener("input", () => { if (pixelEdit) pixelEdit.color = colorInput.value; });

  // 스포이드 표본: 현재 표시 픽셀(베이스 이미지 + 이미 적용한 편집)의 색을 (x,y)에서 읽는다.
  // 편집(ops)이 우선 — 방금 찍은 색도 다시 집을 수 있게. 투명/지운 픽셀은 null.
  const sampleColor = (x, y) => {
    const ops = entries[stateName].pixels[idx];
    const key = `${x},${y}`;
    if (ops && key in ops) {
      const v = ops[key];
      return typeof v === "string" && v.startsWith("#") ? v.slice(0, 7) : null;
    }
    const imgEl = editSourceFor(stateName, stage.querySelector("img"));
    if (!(imgEl && imgEl.complete && imgEl.naturalWidth)) return null;
    const tmp = sampleColor._c || (sampleColor._c = document.createElement("canvas"));
    tmp.width = cellW; tmp.height = cellH;
    const c2 = tmp.getContext("2d");
    c2.imageSmoothingEnabled = false;
    c2.clearRect(0, 0, tmp.width, tmp.height);
    c2.drawImage(imgEl, 0, 0, tmp.width, tmp.height);
    const d = c2.getImageData(x, y, 1, 1).data;
    if (d[3] < 40) return null; // 투명 픽셀은 집을 색이 없다
    return "#" + [d[0], d[1], d[2]].map((v) => v.toString(16).padStart(2, "0")).join("");
  };
  // 저널은 스트로크(액션) 단위 {sets: [{key, had, prev, value}]} — undo 는 통째로
  // 되돌리고 redo 스택에 쌓는다. 새 액션이 생기면 redo 는 비운다 (표준 편집기 계약).
  const undoPixel = () => {
    if (!pixelEdit || !pixelEdit.journal.length) return;
    const j = pixelEdit.journal.pop();
    const e = entries[stateName];
    const ops = e.pixels[idx] || (e.pixels[idx] = {});
    if (j.full) { j.after = { ...(e.pixels[idx] || {}) }; e.pixels[idx] = j.full; }
    else if (j.sets) {
      for (let i = j.sets.length - 1; i >= 0; i--) {
        const s = j.sets[i];
        if (s.had) ops[s.key] = s.prev;
        else delete ops[s.key];
      }
    }
    pixelEdit.redo.push(j);
    applyFrameTransformAll(stateName, idx);
    scheduleSave();
    buildPalette();
  };
  const redoPixel = () => {
    if (!pixelEdit || !pixelEdit.redo.length) return;
    const j = pixelEdit.redo.pop();
    const e = entries[stateName];
    if (j.full) e.pixels[idx] = j.after || {};
    else if (j.sets) {
      const ops = e.pixels[idx] || (e.pixels[idx] = {});
      for (const s of j.sets) ops[s.key] = s.value;
    }
    pixelEdit.journal.push(j);
    applyFrameTransformAll(stateName, idx);
    scheduleSave();
    buildPalette();
  };
  toolbar.querySelector(".et-undo").addEventListener("click", undoPixel);
  toolbar.querySelector(".et-redo").addEventListener("click", redoPixel);
  toolbar.querySelector(".et-clear").addEventListener("click", () => {
    const e = entries[stateName];
    const ops = e.pixels[idx];
    if (!ops || !Object.keys(ops).length) return;
    if (pixelEdit) { pixelEdit.journal.push({ full: { ...ops } }); pixelEdit.redo.length = 0; }
    e.pixels[idx] = {};
    applyFrameTransformAll(stateName, idx);
    scheduleSave();
  });
  // 프레임 고유색 팔레트 (빈도순 상위 12)
  const buildPalette = () => {
    const imgEl = editSourceFor(stateName, stage.querySelector("img"));
    if (!(imgEl && imgEl.complete && imgEl.naturalWidth)) {
      if (imgEl) imgEl.addEventListener("load", buildPalette, { once: true });
      return;
    }
    // 합성(원본+편집) 기준 — 방금 찍은 색도 팔레트에 나타난다. 같은 색 1개, 빈도순.
    const counts = new Map();
    {
      const cx = compositeCell();
      const data = cx.getImageData(0, 0, cellW, cellH).data;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] < 200) continue;
        const hex = "#" + [data[i], data[i + 1], data[i + 2]].map((v) => v.toString(16).padStart(2, "0")).join("");
        counts.set(hex, (counts.get(hex) || 0) + 1);
      }
    }
    if (isBase) {
      // 근접색 병합 — raw 유래 미세 변종(0,0,0 vs 1,1,1)을 대표색 1개로 (tol 24 실측:
      // 426→18색). 크로마 배경색은 제외 (지우개가 그 역할).
      const cornerHex = (() => {
        const cx2 = compositeCell();
        const d = cx2.getImageData(0, 0, 1, 1).data;
        return [d[0], d[1], d[2]];
      })();
      const reps = [];
      for (const [hex, n] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b2 = parseInt(hex.slice(5, 7), 16);
        if (Math.abs(r - cornerHex[0]) + Math.abs(g - cornerHex[1]) + Math.abs(b2 - cornerHex[2]) <= 60) continue;
        const hit = reps.find((q) => Math.abs(q.r - r) + Math.abs(q.g - g) + Math.abs(q.b - b2) <= 24);
        if (hit) hit.n += n;
        else reps.push({ hex, r, g, b: b2, n });
      }
      counts.clear();
      for (const q of reps) counts.set(q.hex, q.n);
    }
    swatchBox.innerHTML = "";
    for (const [hex] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "swatch";
      b.style.background = hex;
      b.setAttribute("data-tip", hex);
      b.classList.toggle("current", hex === colorInput.value);
      b.addEventListener("click", () => {
        colorInput.value = hex;
        swatchBox.querySelectorAll(".swatch.current").forEach((s) => s.classList.remove("current"));
        b.classList.add("current");
        if (pixelEdit) { pixelEdit.color = hex; if (pixelEdit.tool !== "pen") setTool("pen"); }
        else setTool("pen"), (pixelEdit.color = hex);
      });
      swatchBox.appendChild(b);
    }
  };

  // 마키(영역 선택) 헬퍼 — 캡처는 "현재 보이는 픽셀"(원본 + 편집 합성) 기준
  const compositeCell = () => {
    const c = document.createElement("canvas");
    c.width = cellW;
    c.height = cellH;
    const cx = c.getContext("2d");
    cx.imageSmoothingEnabled = false;
    const imgEl = editSourceFor(stateName, stage.querySelector("img"));
    if (imgEl && imgEl.complete && imgEl.naturalWidth) cx.drawImage(imgEl, 0, 0, c.width, c.height);
    const ops0 = entries[stateName].pixels[idx] || {};
    for (const [key, val] of Object.entries(ops0)) {
      const [x, y] = key.split(",").map(Number);
      if (val) { cx.fillStyle = val; cx.fillRect(x, y, 1, 1); }
      else cx.clearRect(x, y, 1, 1);
    }
    return cx;
  };
  const captureRegion = (sel) => {
    const cx = compositeCell();
    let chromaRef = null;
    if (isBase) {
      const d0 = cx.getImageData(0, 0, 1, 1).data; // 베이스 배경(크로마)은 내용이 아니다
      chromaRef = [d0[0], d0[1], d0[2]];
    }
    const data = cx.getImageData(sel.x0, sel.y0, sel.x1 - sel.x0, sel.y1 - sel.y0).data;
    const pixels = [];
    const w = sel.x1 - sel.x0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] < 40) continue; // 투명은 옮길 내용 없음
      if (chromaRef && Math.abs(data[i] - chromaRef[0]) + Math.abs(data[i + 1] - chromaRef[1]) + Math.abs(data[i + 2] - chromaRef[2]) <= 60) continue;
      pixels.push({
        dx: (i / 4) % w,
        dy: Math.floor((i / 4) / w),
        hex: "#" + [data[i], data[i + 1], data[i + 2]].map((v) => v.toString(16).padStart(2, "0")).join(""),
      });
    }
    return pixels;
  };
  const commitRegionMove = (fromSel, pixels, ddx, ddy, duplicate) => {
    if (!pixels.length || (ddx === 0 && ddy === 0)) return;
    const staged = new Map(); // key -> value (이동: 원본 지움 먼저, 목적지 색이 덮음)
    if (!duplicate) {
      for (const p of pixels) staged.set(`${fromSel.x0 + p.dx},${fromSel.y0 + p.dy}`, null);
    }
    for (const p of pixels) {
      const x = fromSel.x0 + p.dx + ddx;
      const y = fromSel.y0 + p.dy + ddy;
      if (x >= 0 && x < cellW && y >= 0 && y < cellH) staged.set(`${x},${y}`, p.hex);
    }
    const e = entries[stateName];
    const ops = e.pixels[idx] || (e.pixels[idx] = {});
    const sets = [];
    for (const [key, value] of staged) {
      if (key in ops && ops[key] === value) continue;
      sets.push({ key, had: key in ops, prev: ops[key], value });
      ops[key] = value;
    }
    if (!sets.length) return;
    pixelEdit.journal.push({ sets });
    pixelEdit.redo.length = 0;
    applyFrameTransformAll(stateName, idx);
    scheduleSave();
    buildPalette();
  };

  buildPalette();

  // 페인트: 편집 툴 활성 시 스테이지 드래그는 그리기 (다른 핸들러보다 먼저 등록해 가로챔)
  stage.addEventListener("pointerdown", (ev) => {
    if (!pixelEdit || pixelEdit.state !== stateName || pixelEdit.idx !== idx) return;
    if (ev.button || !ev.isPrimary) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const cellX = (e2) => Math.floor(((e2.clientX - stage.getBoundingClientRect().left) / stage.getBoundingClientRect().width) * cellW);
    const cellY = (e2) => Math.floor(((e2.clientY - stage.getBoundingClientRect().top) / stage.getBoundingClientRect().height) * cellH);
    // 스포이드: 색만 집고 바로 연필로 전환 (드래그 페인트 아님). 투명 픽셀은 무시.
    if (pixelEdit.tool === "pick") {
      const x = cellX(ev), y = cellY(ev);
      if (x >= 0 && x < cellW && y >= 0 && y < cellH) {
        const hex = sampleColor(x, y);
        if (hex) {
          colorInput.value = hex;
          pixelEdit.color = hex;
          pixelEdit.tool = "pen"; // 집은 색으로 즉시 그리게
          syncToolbar();
          setStatus(`${t("pickTool")}: ${hex}`, "ok");
        }
      }
      return;
    }
    try { stage.setPointerCapture(ev.pointerId); } catch { /* 일부 펜/합성 포인터 */ }
    const s = snapScaleFor(stateName) || 1;
    const finish = (onMove, onUp) => {
      try { stage.releasePointerCapture(ev.pointerId); } catch { /* no-op */ }
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("pointerup", onUp);
    };

    // ── 선택(마키): 점선 드래그로 영역 선택 → 안쪽 드래그 = 이동, Alt+드래그 = 복제 ──
    if (pixelEdit.tool === "select") {
      const sx = cellX(ev), sy = cellY(ev);
      const sel = pixelEdit.sel;
      const inside = sel && sx >= sel.x0 && sx < sel.x1 && sy >= sel.y0 && sy < sel.y1;
      if (inside) {
        const grab = captureRegion(sel);
        const from = { ...sel };
        let delta = [0, 0];
        const onMove = (e2) => {
          const step = s;
          delta = [
            Math.round((cellX(e2) - sx) / step) * step,
            Math.round((cellY(e2) - sy) / step) * step,
          ];
          pixelEdit.sel = { x0: from.x0 + delta[0], y0: from.y0 + delta[1],
                            x1: from.x1 + delta[0], y1: from.y1 + delta[1] };
          syncMarqueeBox();
        };
        const onUp = (e2) => {
          finish(onMove, onUp);
          commitRegionMove(from, grab, delta[0], delta[1], e2.altKey);
          syncMarqueeBox();
        };
        stage.addEventListener("pointermove", onMove);
        stage.addEventListener("pointerup", onUp);
      } else {
        const norm = (ax, ay, bx, by) => {
          const clampX = (v) => Math.max(0, Math.min(cellW, v));
          const clampY = (v) => Math.max(0, Math.min(cellH, v));
          const x0 = Math.floor(Math.min(ax, bx) / s) * s;
          const y0 = Math.floor(Math.min(ay, by) / s) * s;
          const x1 = Math.ceil((Math.max(ax, bx) + 1) / s) * s;
          const y1 = Math.ceil((Math.max(ay, by) + 1) / s) * s;
          return { x0: clampX(x0), y0: clampY(y0), x1: clampX(x1), y1: clampY(y1) };
        };
        pixelEdit.sel = norm(sx, sy, sx, sy);
        syncMarqueeBox();
        const onMove = (e2) => { pixelEdit.sel = norm(sx, sy, cellX(e2), cellY(e2)); syncMarqueeBox(); };
        const onUp = () => finish(onMove, onUp);
        stage.addEventListener("pointermove", onMove);
        stage.addEventListener("pointerup", onUp);
      }
      return;
    }

    const e = entries[stateName];
    if (!e.pixels[idx]) e.pixels[idx] = {};
    const ops = e.pixels[idx];
    const stroke = []; // 스트로크(드래그 1회) 단위 액션 — undo/redo 가 통째로 다룬다
    const paint = (e2) => {
      const r = stage.getBoundingClientRect();
      const x = Math.floor(((e2.clientX - r.left) / r.width) * cellW);
      const y = Math.floor(((e2.clientY - r.top) / r.height) * cellH);
      if (!(x >= 0 && x < cellW && y >= 0 && y < cellH)) return;
      const bx = Math.floor(x / s) * s;
      const by = Math.floor(y / s) * s;
      const value = pixelEdit.tool === "eraser" ? null : pixelEdit.color;
      let changed = false;
      for (let dy = 0; dy < s; dy++) {
        for (let dx = 0; dx < s; dx++) {
          const key = `${bx + dx},${by + dy}`;
          if (ops[key] === value && key in ops) continue;
          stroke.push({ key, had: key in ops, prev: ops[key], value });
          ops[key] = value;
          changed = true;
        }
      }
      if (changed) applyFrameTransformAll(stateName, idx);
    };
    paint(ev);
    const onMove = (e2) => paint(e2);
    const onUp = () => {
      finish(onMove, onUp);
      if (stroke.length) {
        pixelEdit.journal.push({ sets: stroke });
        pixelEdit.redo.length = 0;
        buildPalette();
      }
      scheduleSave();
    };
    stage.addEventListener("pointermove", onMove);
    stage.addEventListener("pointerup", onUp);
  });

  // 뷰 확대: 휠/핀치(ctrl+휠). wireStage 의 휠(스프라이트 스케일)보다 먼저 등록해
  // 가로채고, Shift+휠만 스프라이트 스케일로 통과시킨다.
  stage.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
    zoomView.width = Math.min(Math.floor(window.innerWidth * 0.9),
      Math.max(120, Math.round(zoomView.width * factor)));
    stage.style.width = `${zoomView.width}px`;
    applyCardTransform(stage, stateName, idx);
    sizePxGrids();
  }, { passive: false });

  // ── 호흡 모드 (수홍 제안 2026-07-17): 드래그 가슴선 + 라이브 호흡 미리보기 ──
  // 기본 가슴선 = 실루엣 휴리스틱(상반신 최광폭 행 = 어깨의 바로 아래) — AI 불필요,
  // SD/8등신 모두 자동 적응. 선을 끌면 미리보기가 실시간으로 숨쉰다.
  if (!isBase && pendingBreathe) {
    pendingBreathe = false;
    // 포커스 모드 (수홍 2026-07-17): 호흡 조정에 필요한 것만 남긴다 — 가슴선 +
    // 진폭 + 생성. 다른 편집 도구/팔레트/변형은 숨기고 스테이지 입력도 차단.
    card.classList.add("breathe-focus");
    stage.addEventListener("pointerdown", (ev) => {
      if (!ev.target.closest(".breathe-line")) {
        ev.stopImmediatePropagation();
        ev.preventDefault();
      }
    }, true);
    const bm = { amplitude: 1, phase: 0, split: 0.55 };
    const cx0 = compositeCell();
    const bdata = cx0.getImageData(0, 0, cellW, cellH).data;
    let btop = cellH, bbot = 0;
    const widths = new Array(cellH).fill(0);
    for (let y = 0; y < cellH; y++) {
      let w = 0;
      for (let x = 0; x < cellW; x++) if (bdata[(y * cellW + x) * 4 + 3] >= 40) w++;
      widths[y] = w;
      if (w) { btop = Math.min(btop, y); bbot = Math.max(bbot, y + 1); }
    }
    const bh = Math.max(1, bbot - btop);
    let shoulderY = btop + Math.floor(bh * 0.3);
    let bestW = 0;
    for (let y = btop; y < btop + Math.floor(bh * 0.55); y++) {
      if (widths[y] > bestW) { bestW = widths[y]; shoulderY = y; }
    }
    bm.split = Math.min(0.75, Math.max(0.3, (shoulderY + Math.floor(bh * 0.1) - btop) / bh));

    const line = document.createElement("div");
    line.className = "breathe-line";
    line.setAttribute("data-tip", t("breatheHint"));
    stage.appendChild(line);
    const syncLine = () => {
      line.style.top = `${((btop + bm.split * bh) / cellH) * 100}%`;
    };
    syncLine();
    line.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      line.setPointerCapture(ev.pointerId);
      const onMove = (e2) => {
        const r = stage.getBoundingClientRect();
        const yCell = ((e2.clientY - r.top) / r.height) * cellH;
        bm.split = Math.min(0.9, Math.max(0.1, (yCell - btop) / bh));
        syncLine();
      };
      const onUp = () => {
        line.removeEventListener("pointermove", onMove);
        line.removeEventListener("pointerup", onUp);
      };
      line.addEventListener("pointermove", onMove);
      line.addEventListener("pointerup", onUp);
    });

    const bcanvas = stage.querySelector(".snap-canvas");
    const bImg = stage.querySelector("img");
    const renderPhase = () => {
      const srcEl = editSourceFor(stateName, bImg);
      if (!srcEl || !bcanvas) return;
      bImg.style.visibility = "hidden";
      bcanvas.style.display = "block";
      bcanvas.width = cellW;
      bcanvas.height = cellH;
      const bctx = bcanvas.getContext("2d");
      bctx.imageSmoothingEnabled = false;
      bctx.clearRect(0, 0, cellW, cellH);
      drawFrameInto(bctx, srcEl, IDENTITY(), cellW, cellH, snapScaleFor(stateName),
        getPixelOps(stateName, idx));
      if (bm.phase) { // exhale 미리보기: breathe_frames.shift_above 와 같은 행 시프트
        const splitY = Math.round(btop + bm.split * bh);
        const region = bctx.getImageData(0, btop, cellW, Math.max(1, splitY - btop));
        bctx.clearRect(0, btop, cellW, splitY - btop);
        bctx.putImageData(region, 0, btop + bm.amplitude);
      }
    };
    bm.timer = setInterval(() => { bm.phase = 1 - bm.phase; renderPhase(); }, 500);
    renderPhase();

    const bar = document.createElement("div");
    bar.className = "breathe-bar";
    const ampSel = document.createElement("select");
    for (const v of [1, 2]) {
      const o = document.createElement("option");
      o.value = String(v);
      o.textContent = `${t("breatheAmp")} ${v}px`;
      ampSel.appendChild(o);
    }
    ampSel.addEventListener("change", () => { bm.amplitude = parseInt(ampSel.value, 10) || 1; });
    const goBtn = document.createElement("button");
    goBtn.textContent = t("breatheGo");
    goBtn.addEventListener("click", async () => {
      goBtn.disabled = true;
      goBtn.innerHTML = '<span class="tween-spin" aria-label="generating"></span>';
      setStatus(t("breatheBusy"));
      try {
        const res = await fetch("/api/breathe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state: stateName, frame: idx,
                                 split: Math.round(bm.split * 100) / 100,
                                 amplitude: bm.amplitude }),
        });
        const dataR = await res.json();
        if (!res.ok || !dataR.ok) {
          throw new Error(dataR.error || (dataR.stderr || "").trim().split("\n").pop() || res.status);
        }
        setStatus(STR[lang].breatheDone(stateName));
        setTimeout(() => window.location.reload(), 800);
      } catch (e) {
        setStatus(t("breatheFail") + e.message, "err");
        goBtn.textContent = t("breatheGo");
        goBtn.disabled = false;
      }
    });
    bar.appendChild(ampSel);
    bar.appendChild(goBtn);
    toolbar.appendChild(bar);
    zoomView.cleanupBreathe = () => clearInterval(bm.timer);
  }

  wireStage(stage, stateName, idx); // 베이스도 변형 동일 — 저장 시 파일에 굽는다 (수홍 지시)
  applyCardTransform(stage, stateName, idx);
  syncPpControls();
  syncGridControls();
  sizePxGrids();
  document.addEventListener("keydown", onZoomKey);
}

// --- iso ground grid overlay -----------------------------------------------

function drawGroundGrid(stage) {
  const canvas = stage.querySelector(".grid-overlay");
  if (!canvas || !run.iso) return;
  const rect = stage.getBoundingClientRect();
  const W = Math.round(rect.width);
  const H = Math.round(rect.height);
  if (!W || !H) return;
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, W, H);

  // cell pixels -> displayed pixels
  const ds = W / run.cell.width;
  const tw = run.iso.tile.width * ds;   // diamond full width (2:1 -> width = 2*height)
  const th = run.iso.tile.height * ds;  // diamond full height
  const [ax, ay] = run.iso.anchor_pixel;
  const ox = ax * ds; // anchor in displayed px
  const oy = ay * ds;

  // grid-(gx,gy) center on screen, 2:1 dimetric, anchored at the meta anchor
  const center = (gx, gy) => [ox + (gx - gy) * (tw / 2), oy + (gx + gy) * (th / 2)];
  const diamond = (cx, cy) => {
    ctx.beginPath();
    ctx.moveTo(cx, cy - th / 2);
    ctx.lineTo(cx + tw / 2, cy);
    ctx.lineTo(cx, cy + th / 2);
    ctx.lineTo(cx - tw / 2, cy);
    ctx.closePath();
  };

  const R = 4;
  ctx.lineWidth = 1;
  for (let gx = -R; gx <= R; gx++) {
    for (let gy = -R; gy <= R; gy++) {
      const [cx, cy] = center(gx, gy);
      diamond(cx, cy);
      const anchorTile = gx === 0 && gy === 0;
      ctx.strokeStyle = anchorTile ? "rgba(37,99,235,0.9)" : "rgba(37,99,235,0.25)";
      ctx.stroke();
    }
  }
  // axis guide lines through the anchor (the true 2:1 slopes)
  ctx.strokeStyle = "rgba(217,119,6,0.9)";
  ctx.lineWidth = 1.5;
  for (const [sx, sy] of [[1, 1], [1, -1]]) {
    ctx.beginPath();
    ctx.moveTo(ox - sx * tw * 3, oy - sy * th * 3);
    ctx.lineTo(ox + sx * tw * 3, oy + sy * th * 3);
    ctx.stroke();
  }
}

// --- 사이드바 접기/펴기 (쿠마피커 도크 스타일, 상태는 localStorage 유지) ------
const sidebarToggle = document.getElementById("sidebar-toggle");
// topbar 실측 높이 → 사이드바 sticky 오프셋/전체높이 계산에 주입.
// 라벨/폰트가 늦게 차면서 높이가 변하므로 ResizeObserver 로 추적한다 (일회 측정 금지).
{
  const topbar = document.querySelector(".topbar");
  if (topbar) {
    const sync = () =>
      document.documentElement.style.setProperty("--topbar-h", `${topbar.offsetHeight}px`);
    sync();
    new ResizeObserver(sync).observe(topbar);
  }
}
function applySidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  try { localStorage.setItem("curator-sidebar-collapsed", collapsed ? "1" : ""); } catch { /* private mode */ }
}
if (sidebarToggle) {
  sidebarToggle.addEventListener("click", () =>
    applySidebarCollapsed(!document.body.classList.contains("sidebar-collapsed")));
  let saved = "";
  try { saved = localStorage.getItem("curator-sidebar-collapsed") || ""; } catch { /* private mode */ }
  applySidebarCollapsed(saved === "1");
}

const gridToggle = document.getElementById("grid-toggle");
const langToggle = document.getElementById("lang-toggle");
gridToggle.addEventListener("click", () => {
  const on = document.body.classList.toggle("show-grid");
  gridToggle.textContent = `${t("groundGrid")} ${on ? "▣" : "▢"}`;
  if (on) document.querySelectorAll(".stage").forEach(drawGroundGrid);
});

// language toggle reloads with ?lang= so preview rAF loops are not duplicated
langToggle.addEventListener("click", () => {
  const next = lang === "en" ? "ko" : "en";
  const u = new URL(location.href);
  u.searchParams.set("lang", next);
  location.href = u.toString();
});

function applyStaticLang() {
  document.getElementById("t-title").textContent = t("title");
  document.getElementById("compose").textContent = t("compose");
  document.getElementById("export").textContent = t("export");
  document.getElementById("export-gif").textContent = t("exportGif");
  gridToggle.textContent = `${t("groundGrid")} ${document.body.classList.contains("show-grid") ? "▣" : "▢"}`;
  langToggle.textContent = t("langOther");
  const ppLabel = document.getElementById("pp-label");
  if (ppLabel) ppLabel.textContent = t("ppApply");
  const pxLabel = document.getElementById("pxgrid-label");
  if (pxLabel) pxLabel.textContent = t("pxGridAll") + (run.pixelPerfect && run.pixelPerfect.label ? " \u00b7 " + run.pixelPerfect.label : "");
  const pxWrap = document.getElementById("pxgrid-wrap");
  if (pxWrap) pxWrap.title = t("tPxGrid");
  const ppWrap = document.getElementById("pp-wrap");
  if (ppWrap) ppWrap.title = t("tPpApply");
  document.getElementById("hintbar").innerHTML = t("hints").map((h) => `<span>${h}</span>`).join("");
}

// --- 다운로드 3종 ------------------------------------------------------------
// 실시간 계약 (수홍 확정 2026-07-14): 버튼은 '게임에 적용'이 아니다 — 지금
// 보이는 라이브 상태(프레임 캐시 + 큐레이션)를 서버가 그 자리에서 계산해
// 파일(zip)로 내려주는 다운로드다. 서버는 계산 전에 캐시를 자가치유한다.

async function downloadArtifact(kind, doneMsg) {
  clearTimeout(saveTimer);
  await save();
  setStatus(t("baking"));
  const res = await fetch(`/download/${kind}`);
  if (!res.ok) {
    let msg = "download failed";
    try {
      const data = await res.json();
      msg = (data.stderr || data.error || msg).trim();
    } catch { /* 비 JSON 에러 응답 — 기본 메시지 유지 */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = res.headers.get("X-Filename") || `${kind}.zip`;
  link.click();
  URL.revokeObjectURL(link.href);
  if (kind === "atlas") await refreshFinalAtlas();  // 방금 재계산된 시트/문서 반영
  setStatus(doneMsg, "ok");
}

for (const [id, kind, done] of [
  ["compose", "atlas", () => t("composeDone")],
  ["export", "pngs", () => STR[lang].exportDone()],
  ["export-gif", "gifs", () => STR[lang].exportGifDone()],
]) {
  document.getElementById(id).addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      await downloadArtifact(kind, done());
    } catch (e) {
      setStatus(t(id === "compose" ? "composeFail" : "exportFail") + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  });
}

// --- bootstrap -------------------------------------------------------------

function seedEntries() {
  entries = {};
  const curated = (run.curation && run.curation.states) || {};
  for (const state of run.states) {
    const physPresent = state.frames.filter((f) => f.present).map((f) => f.index);
    const c = curated[state.name];
    // 복제 인스턴스: {복제idx: 원본idx}. 복제idx 는 물리 범위 밖 정수, 원본은 물리
    // 프레임이어야 한다 (손상 항목 스킵). 원본이 present 면 복제도 present 취급.
    const physIdxSet = new Set(state.frames.map((f) => f.index));
    const clones = {};
    if (c && c.clones && typeof c.clones === "object") {
      for (const [k, v] of Object.entries(c.clones)) {
        const ci = Number(k);
        const src = Number(v);
        if (Number.isInteger(ci) && Number.isInteger(src) && !physIdxSet.has(ci) && physIdxSet.has(src)) clones[ci] = src;
      }
    }
    const cloneIdx = Object.keys(clones).map(Number);
    const present = [...physPresent, ...cloneIdx.filter((ci) => physPresent.includes(clones[ci]))];
    // order = full display arrangement (sequence then pool); sel = which are on.
    // Coerce to integers and de-dupe so a hand-edited / corrupt sidecar (string
    // indices, duplicates) can't produce a duplicated or dropped frame.
    const missing = state.frames.filter((f) => !f.present).map((f) => f.index);
    const allIdx = [...present, ...missing];
    const coerce = (arr, valid) => {
      const seen = new Set();
      const out = [];
      for (const raw of Array.isArray(arr) ? arr : []) {
        const i = Number(raw);
        if (Number.isInteger(i) && valid.includes(i) && !seen.has(i)) {
          seen.add(i);
          out.push(i);
        }
      }
      return out;
    };
    const archived = c && Array.isArray(c.deleted) ? coerce(c.deleted, allIdx) : [];
    const archivedSet = new Set(archived);
    const savedSel = (c && Array.isArray(c.selected) ? coerce(c.selected, present) : []).filter((i) => !archivedSet.has(i));
    const savedOrder = (c && Array.isArray(c.order) ? coerce(c.order, allIdx) : []).filter((i) => !archivedSet.has(i));
    let order;
    if (savedOrder.length) {
      // restore the exact saved arrangement (incl. pool order); append any
      // newly-extracted frames that weren't in the saved order.
      const seen = new Set([...savedOrder, ...archived]);
      order = [...savedOrder, ...allIdx.filter((i) => !seen.has(i))];
    } else if (savedSel.length) {
      // older sidecar without `order`: selected leads, the rest trail.
      const inSel = new Set(savedSel);
      order = [...savedSel, ...present.filter((i) => !inSel.has(i) && !archivedSet.has(i)),
               ...missing.filter((i) => !archivedSet.has(i))];
    } else {
      order = allIdx.filter((i) => !archivedSet.has(i));
    }
    const sel = savedSel.length ? new Set(savedSel) : new Set(present.filter((i) => !archivedSet.has(i)));
    const transforms = {};
    if (c && c.transforms) {
      for (const [idx, t] of Object.entries(c.transforms)) {
        transforms[idx] = { ...IDENTITY(), ...t };
      }
    }
    const pixels = {};
    if (c && c.pixels && typeof c.pixels === "object") {
      for (const [k, v] of Object.entries(c.pixels)) {
        const i = Number(k);
        if (Number.isInteger(i) && v && typeof v === "object" && Object.keys(v).length) pixels[i] = { ...v };
      }
    }
    entries[state.name] = { order, sel, transforms, archived, pixels, clones };
  }
}

async function boot() {
  try {
    const res = await fetch("/api/run");
    run = await res.json();
    if (run.error) throw new Error(run.error);
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
  // pixel-perfect twin state must resolve BEFORE first render (frameUrl reads it):
  // per-state truth = states.<state>.pixel_perfect override > run-wide default > on.
  ppTwinStates = new Set(run.states.filter((s) => s.frames.some((f) => f.plainUrl)).map((s) => s.name));
  ppAvailable = ppTwinStates.size > 0;
  const ppDefault = !(run.curation && run.curation.pixel_perfect === false);
  ppStates = {};
  for (const s of run.states) {
    const c = run.curation && run.curation.states && run.curation.states[s.name];
    ppStates[s.name] = c && typeof c.pixel_perfect === "boolean" ? c.pixel_perfect : ppDefault;
  }
  // 격자 오버레이 가능 줄(계약 scale 또는 줄별 측정 피치) — 표시 전용, 저장 안 함, 기본 off
  const contractScale = run.pixelPerfect && run.pixelPerfect.scale;
  gridCapableStates = new Set(run.states.filter((s) => s.pixelScale || contractScale).map((s) => s.name));
  gridStates = {};
  applyStaticLang();
  document.getElementById("character").textContent = `${run.characterId} · ${run.cell.width}×${run.cell.height}`;
  if (run.iso) gridToggle.hidden = false;
  if (ppAvailable) {
    const ppWrap = document.getElementById("pp-wrap");
    const ppCheck = document.getElementById("pp-apply");
    ppWrap.hidden = false;
    // 전체 토글: 클릭 = 쌍둥이 있는 모든 줄을 새 값으로 일괄 설정. 줄들이 섞여
    // 있으면(일부 on/일부 off) indeterminate 로 표시한다 (syncPpControls).
    ppCheck.addEventListener("change", () => {
      const on = ppCheck.checked;
      for (const n of ppTwinStates) ppStates[n] = on;
      syncPpControls();
      refreshVariantImages();
      scheduleSave();
    });
  }
  // 픽셀 격자 전체 토글 — 표시 전용 오버레이 (굽기와 무관), 줄별 체크박스와 같은 truth.
  // 격자를 알 수 있는 줄이 하나도 없으면 감춘다 (가짜 격자를 보여주지 않는다).
  const pxWrap = document.getElementById("pxgrid-wrap");
  const pxCheck = document.getElementById("pxgrid-check");
  pxWrap.hidden = gridCapableStates.size === 0;
  pxCheck.addEventListener("change", () => {
    const on = pxCheck.checked;
    for (const n of gridCapableStates) gridStates[n] = on;
    syncGridControls();
    sizePxGrids();
  });
  seedEntries();
  if (run.directionGroups && run.directionGroups.length) {
    anchorStates = new Set(run.directionGroups.map((g) => g.anchor).filter(Boolean));
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
  syncPpControls();
  syncGridControls();
  refreshVariantImages();
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
  if (healParts.length) {
    setStatus(healParts.join(" · "), run.heal.failed && run.heal.failed.length ? "err" : "ok");
  } else {
    setStatus(run.curation && Object.keys(run.curation.states || {}).length ? t("loaded") : t("ready"));
  }
}

boot();
