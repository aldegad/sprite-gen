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

// 현재 베이스 편집기에 표시 중인 격자 피치 [x, y] 와 그 출처("manual"|"detected").
// 사람이 피치를 조정하면(라이브 프리뷰) 이 값이 갱신되고, "격자 저장" 이 이를
// sprite-request.json 의 fit.pitch_manual 로 확정한다.
let basePitch = null;
let basePitchSource = null;
// 저장 전 라이브 프리뷰 override 피치([x,y]). 저장/자동복귀/detected·saved 표시 중엔 null.
// 이게 set 이면 base-edit 굽기에 이 피치를 함께 보내 화면과 같은 격자로 확장한다.
let basePitchOverride = null;

// ── 베이스 편집 = 줌 모달과 같은 컴포넌트 (수홍 지시 2026-07-17 "같은 컴포넌트를
// 쓰라" — 별도 모달 구현은 폐기). 검출 격자의 논리 해상도로 가상 상태 "__base__" 를
// 만들어 openZoom 으로 연다. 도구/단축키/마키/줌/팬 전부 프레임 편집과 단일 코드.
//
// overridePitch = [x, y] 면 그 피치로 /api/base-grid 를 라이브 프리뷰한다(저장 전,
// 디스크 미변경). 없으면 서버가 저장된 fit.pitch_manual > 자동 검출 순으로 격자를 준다.
async function openBaseEditor(overridePitch) {
  let url = "/api/base-grid";
  if (Array.isArray(overridePitch)) {
    url += `?pitchX=${encodeURIComponent(overridePitch[0])}&pitchY=${encodeURIComponent(overridePitch[1])}`;
  }
  let grid = null;
  try {
    grid = (await (await fetch(url)).json()).grid || null;
  } catch { grid = null; }
  if (!grid) {
    setStatus(t("baseEditFail") + "no confident pixel grid on the base", "err");
    return;
  }
  basePitch = Array.isArray(grid.pitch) ? grid.pitch.slice() : null;
  basePitchSource = grid.source || null;
  // 프리뷰 override 로 연 경우에만 마커를 세운다 (저장/자동복귀는 override 없이 재열림).
  basePitchOverride = Array.isArray(overridePitch) ? overridePitch.slice() : null;
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
  injectBasePitchControls();
}

// 베이스 줌 모달 툴바에 픽셀 격자 피치 조정 위젯을 붙인다. 값을 바꾸면 그 피치로
// 격자를 라이브 재렌더(디스크 미변경)하고, "격자 저장" 이 fit.pitch_manual 로 확정한다.
function injectBasePitchControls() {
  const toolbar = document.querySelector("#zoom-modal .edit-toolbar");
  if (!toolbar || !basePitch) return;
  const fmt = (v) => (Math.round(v * 100) / 100);
  const wrap = document.createElement("span");
  wrap.className = "et-pitch";
  wrap.setAttribute("data-tip", t("tBasePitch"));
  wrap.innerHTML =
    `<span class="et-pitch-label">${t("basePitchLabel")}</span>` +
    `<button type="button" class="ghost et-pitch-dec" data-axis="x" aria-label="pitch x -">−</button>` +
    `<input type="number" class="et-pitch-x" step="0.5" min="2" value="${fmt(basePitch[0])}" />` +
    `<button type="button" class="ghost et-pitch-inc" data-axis="x" aria-label="pitch x +">+</button>` +
    `<span class="et-pitch-x-glyph">×</span>` +
    `<button type="button" class="ghost et-pitch-dec" data-axis="y" aria-label="pitch y -">−</button>` +
    `<input type="number" class="et-pitch-y" step="0.5" min="2" value="${fmt(basePitch[1])}" />` +
    `<button type="button" class="ghost et-pitch-inc" data-axis="y" aria-label="pitch y +">+</button>` +
    `<button type="button" class="et-pitch-save" data-tip="${t("tBasePitchSave")}">${t("basePitchSave")}</button>` +
    `<button type="button" class="ghost et-pitch-auto" data-tip="${t("tBasePitchAuto")}">${t("basePitchAuto")}</button>`;
  const xInput = wrap.querySelector(".et-pitch-x");
  const yInput = wrap.querySelector(".et-pitch-y");
  if (basePitchSource === "manual") wrap.classList.add("is-manual");
  const readPair = () => [parseFloat(xInput.value), parseFloat(yInput.value)];
  const valid = ([x, y]) => Number.isFinite(x) && Number.isFinite(y) && x >= 2 && y >= 2;
  // 라이브 프리뷰: 새 피치로 격자를 다시 그린다. 논리 해상도(cols×rows)가 바뀌면 예전
  // 논리좌표 픽셀 편집은 무효가 되므로 초기화한다(굽기 전이라 파일 미변경).
  const preview = async (pair) => {
    if (!valid(pair)) return;
    delete entries[BASE_STATE];
    await openBaseEditor(pair);
  };
  wrap.querySelectorAll(".et-pitch-inc, .et-pitch-dec").forEach((btn) => {
    btn.addEventListener("click", () => {
      const delta = btn.classList.contains("et-pitch-inc") ? 1 : -1;
      const input = btn.dataset.axis === "x" ? xInput : yInput;
      const next = Math.max(2, (parseFloat(input.value) || 2) + delta);
      input.value = Math.round(next * 100) / 100;
      preview(readPair());
    });
  });
  xInput.addEventListener("change", () => preview(readPair()));
  yInput.addEventListener("change", () => preview(readPair()));
  wrap.querySelector(".et-pitch-save").addEventListener("click", async (ev) => {
    const pair = readPair();
    if (!valid(pair)) { setStatus(t("basePitchFail") + "pitch must be >= 2px", "err"); return; }
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      const res = await fetch("/api/base-pitch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pitchX: pair[0], pitchY: pair[1] }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      basePitchSource = "manual";
      basePitchOverride = null;  // 이제 fit.pitch_manual 로 저장됨 — 굽기가 그걸 읽는다
      wrap.classList.add("is-manual");
      setStatus(t("basePitchSaved"), "ok");
    } catch (e) {
      setStatus(t("basePitchFail") + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  });
  wrap.querySelector(".et-pitch-auto").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      const res = await fetch("/api/base-pitch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),  // 비움 = fit.pitch_manual 제거 → 자동 검출 복귀
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.status);
      setStatus(t("basePitchAutoDone"), "ok");
      delete entries[BASE_STATE];
      await openBaseEditor();  // override 없이 재열기 → 서버가 자동 검출 격자를 준다
    } catch (e) {
      setStatus(t("basePitchFail") + e.message, "err");
      btn.disabled = false;
    }
  });
  toolbar.appendChild(wrap);
}
