// SPDX-License-Identifier: Apache-2.0
// curator/base-editor.js — 베이스 소스 편집 (검출 격자 논리 이미지 → 줌 모달 재사용)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

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
    editBtn.innerHTML = '<span class="gen-spin" aria-label="loading"></span>';
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
