// SPDX-License-Identifier: Apache-2.0
// curator/recolor.js — 컬러웨이(recolor variant) 비교·픽 섹션
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

// 색 변형은 **같은 자리에서 갈아끼워야** 보인다. 두 시트를 나란히 놓고 눈을 좌우로
// 옮기면 도트 한 칸짜리 색차는 그냥 안 보인다 — 그래서 이 섹션은 큰 스테이지 하나에
// 모든 시트를 겹쳐 쌓아두고(전부 미리 로드) 목록 위를 지나갈 때 즉시 교체한다.
// 깜빡임 비교가 기본이고, 목록은 그 자체로 나란히 비교(썸네일)도 겸한다.
//
// 채택(픽)은 큐레이션 사이드카의 `recolor.picked` 하나다 — 이름으로만 기록한다
// (curation.py `recolor_pick` 계약: 컬러웨이는 프레임 세대가 아니라 색 결정이라
// 재베이크로 낡지 않는다). 베이크 리포트에 없는 이름은 조용히 지우지 않고
// 서버가 `pickedKnown: false` 로 알려주며, 여기서 경고로 보인다.

const RECOLOR_BASE_KEY = "__base__";
const RECOLOR_SWATCH_PX = 64;

// 썸네일은 시트가 아니라 **프레임 한 칸**을 보여준다 — 1280px 짜리 띠를 68px 로 줄이면
// 색이 사라진다. 상자는 서버가 manifest `frame_layout` 에서 읽어 준 것이고 (swatch),
// 여기선 그 상자가 칸을 채우도록 `<img>` 를 키우고 밀어 넣는다. transform 이 아니라
// width/margin 인 이유: 표시 샘플링 판정(display.js applyPixelScaling)이 clientWidth 를
// 재기 때문에, 변환으로 키우면 판정이 확대를 못 보고 니어리스트가 안 걸린다.
function recolorSwatchStyle(swatch) {
  if (!swatch || !swatch.w || !swatch.h) return "";
  const scale = RECOLOR_SWATCH_PX / Math.max(swatch.w, swatch.h);
  return ` style="width:${swatch.sheetWidth * scale}px;max-width:none;` +
    `margin-left:${-swatch.x * scale}px;margin-top:${-swatch.y * scale}px"`;
}

let recolorPicked = null;   // 채택된 variant 이름 (없으면 null) — 저장 페이로드의 진실
let recolorPreview = null;  // 지금 스테이지에 보이는 것 (RECOLOR_BASE_KEY | variant 이름)

function seedRecolorPick(runState) {
  recolorPicked = (runState.recolor && runState.recolor.picked) || null;
  recolorPreview = recolorPicked || RECOLOR_BASE_KEY;
}

function renderRecolorVariants(info) {
  const host = document.getElementById("states");
  let section = document.getElementById("recolor-variants");
  if (!info) {
    // 베이크 산출물이 없는 런엔 섹션 자체가 없다 (빈 껍데기를 남기지 않는다).
    if (section) section.remove();
    return;
  }
  if (!section) {
    section = document.createElement("div");
    section.id = "recolor-variants";
    section.className = "state recolor-variants";
    host.appendChild(section);
  }
  const items = [
    {
      key: RECOLOR_BASE_KEY,
      label: t("rcBase"),
      url: info.base.url,
      present: info.base.present,
      note: info.base.name,
      pickable: false,
    },
    ...info.variants.map((v) => ({
      key: v.name,
      label: v.name,
      url: v.url,
      present: v.present,
      note: STR[lang].rcVariantNote(v.substitutedPixels, v.passthroughPixels),
      warn: (v.unusedSources && v.unusedSources.length)
        ? STR[lang].rcUnusedSources(v.unusedSources)
        : null,
      manifestUrl: v.manifestUrl,
      pickable: true,
    })),
  ];
  if (!items.some((it) => it.key === recolorPreview)) recolorPreview = RECOLOR_BASE_KEY;

  const meta = STR[lang].rcMeta(info.variants.length, info.match, info.tolerance);
  section.innerHTML =
    `<div class="state-head"><span class="name">${t("rcTitle")}</span>` +
    `<span class="meta" data-tip="${t("tRcSection")}">${escapeHtml(meta)}</span></div>` +
    (info.picked && !info.pickedKnown
      ? `<div class="rc-unknown">${escapeHtml(STR[lang].rcPickUnknown(info.picked))}</div>`
      : "") +
    `<div class="rc-split">` +
    `<div class="rc-stage">` +
    items.map((it) => (it.present
      ? `<img data-key="${escapeHtml(it.key)}" class="${it.key === recolorPreview ? "active" : ""}"` +
        ` src="${escapeHtml(it.url)}" alt="${escapeHtml(it.label)}" />`
      : "")).join("") +
    `<div class="rc-stage-label"></div></div>` +
    `<div class="rc-list">` +
    items.map((it) =>
      `<div class="rc-thumb${it.key === recolorPreview ? " previewing" : ""}` +
      `${it.key === recolorPicked ? " picked" : ""}${it.present ? "" : " missing"}"` +
      ` data-key="${escapeHtml(it.key)}" tabindex="0" data-tip="${t("tRcThumb")}">` +
      (it.present
        ? `<span class="rc-swatch${info.swatch ? " cropped" : ""}">` +
          `<img src="${escapeHtml(it.url)}" alt="${escapeHtml(it.label)}"${recolorSwatchStyle(info.swatch)} />` +
          `</span>`
        : `<span class="rc-missing">${t("rcMissing")}</span>`) +
      `<div class="rc-thumb-body">` +
      `<span class="rc-name">${escapeHtml(it.label)}</span>` +
      `<span class="rc-note">${escapeHtml(it.note || "")}</span>` +
      (it.warn ? `<span class="rc-warn">${escapeHtml(it.warn)}</span>` : "") +
      (it.manifestUrl
        ? `<a class="rc-manifest" href="${escapeHtml(it.manifestUrl)}" target="_blank">manifest.json</a>`
        : "") +
      `</div>` +
      (it.pickable
        ? `<button type="button" class="rc-pick ghost" data-key="${escapeHtml(it.key)}"` +
          ` data-tip="${t("tRcPick")}">` +
          `${it.key === recolorPicked ? t("rcPicked") : t("rcPick")}</button>`
        : "") +
      `</div>`).join("") +
    `</div></div>`;

  const stageLabel = section.querySelector(".rc-stage-label");
  const showPreview = (key) => {
    recolorPreview = key;
    for (const img of section.querySelectorAll(".rc-stage img")) {
      img.classList.toggle("active", img.dataset.key === key);
    }
    for (const card of section.querySelectorAll(".rc-thumb")) {
      card.classList.toggle("previewing", card.dataset.key === key);
    }
    const item = items.find((it) => it.key === key);
    stageLabel.textContent = item ? item.label : "";
  };
  showPreview(recolorPreview);

  for (const card of section.querySelectorAll(".rc-thumb")) {
    const key = card.dataset.key;
    // 지나가기만 해도 즉시 교체 = 깜빡임 비교. 클릭은 고정(포커스)이라 마우스를 떼도 남는다.
    card.addEventListener("mouseenter", () => showPreview(key));
    card.addEventListener("focus", () => showPreview(key));
    card.addEventListener("click", (ev) => {
      if (ev.target.closest(".rc-pick") || ev.target.closest(".rc-manifest")) return;
      showPreview(key);
    });
    card.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); showPreview(key); }
    });
  }
  for (const btn of section.querySelectorAll(".rc-pick")) {
    btn.addEventListener("click", () => pickRecolorVariant(btn.dataset.key));
  }
  syncPixelScaling(section);
}

// 픽은 토글이다 — 채택한 것을 다시 누르면 해제된다 (해제도 의도라 빈 객체로 저장된다).
function pickRecolorVariant(name) {
  recolorPicked = recolorPicked === name ? null : name;
  recolorPreview = recolorPicked || recolorPreview;
  setStatus(recolorPicked ? STR[lang].rcPickSaved(recolorPicked) : t("rcPickCleared"), "ok");
  scheduleSave();
  // 서버 스냅샷을 다시 받지 않고 로컬 진실로 다시 그린다 — 저장은 디바운스라
  // 지금 /api/run 을 읽으면 방금 누른 픽이 아직 디스크에 없다.
  renderRecolorVariants({ ...run.recolor, picked: recolorPicked, pickedKnown: recolorPicked !== null });
}
