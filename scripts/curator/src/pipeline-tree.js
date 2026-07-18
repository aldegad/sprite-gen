// SPDX-License-Identifier: Apache-2.0
// curator/pipeline-tree.js — 생성 구조 트리 (파이프라인 진행 현황판, 폴링)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

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
    // 파일 트리의 아틀라스 노드도 클릭 = 최종 아틀라스 섹션 스크롤 (수홍 2026-07-18 —
    // 파이프라인 트리의 아틀라스 노드와 같은 목적지; "__atlas__" 라우팅 공유)
    liWith(fileUl, treeNode("sprite-sheet-alpha.png", t("treeAtlasNote"), `/run/sprite-sheet-alpha.png?v=${treeRevision || 0}`, "__atlas__"));
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
