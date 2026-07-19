// SPDX-License-Identifier: Apache-2.0
// curator/row-export.js — 줄 단위 내보내기 (저장 팝오버: GIF 서버 굽기 / WebM·MP4 클라 샘플)
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)
//
// row-controls.js(표시 토글/스텝퍼 팩토리)에서 분리 — 내보내기는 별개 관심사 (SRP,
// 2026-07-19 텍소노미 정리). 서버측 생성 트리거는 gen-trigger.js, 여기는 다운로드만.

// 줄 단위 저장 — 공용 저장 팝오버 (수홍 2026-07-19 "row 저장버튼도 공용저장버튼으로"):
// GIF = 서버 굽기(실시간 계약: 보는 것 = 받는 것, 4x 니어리스트), WebM(투명)/MP4(흰
// 배경) = 비교뷰와 같은 클라 결정론 샘플 → /api/compare-gif 서버 조립.
function makeGifButton(stateName) {
  const wrap = document.createElement("span");
  wrap.className = "dl-wrap row-dl-wrap";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "gif-btn";
  btn.title = t("tRowDl");
  btn.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
    '<path d="M8 2v8m0 0l-3-3m3 3l3-3M3 13h10" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    `<span>${t("cmpDl")} ▾</span>`;
  const menu = document.createElement("div");
  menu.className = "dl-menu";
  menu.hidden = true;
  menu.innerHTML =
    `<button type="button" data-fmt="gif">GIF</button>` +
    `<button type="button" data-fmt="webm">WebM · ${t("cmpDlAlpha")}</button>` +
    `<button type="button" data-fmt="mp4">MP4 · ${t("cmpDlWhite")}</button>`;
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    menu.hidden = !menu.hidden;
  });
  document.addEventListener("click", () => { menu.hidden = true; });
  const download = async (fmt) => {
    btn.disabled = true;
    try {
      if (fmt === "gif") {
        await downloadArtifact(`gif?state=${encodeURIComponent(stateName)}`,
          STR[lang].rowGifDone(stateName));
      } else {
        await downloadRowVideo(stateName, fmt);
        setStatus(STR[lang].rowDlDone(stateName, fmt), "ok");
      }
    } catch (e) {
      setStatus(t("exportFail") + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  };
  menu.querySelectorAll("button[data-fmt]").forEach((b) =>
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      menu.hidden = true;
      download(b.dataset.fmt);
    }));
  wrap.appendChild(btn);
  wrap.appendChild(menu);
  return wrap;
}

// WebM/MP4 = 줄 재생 루프 1회를 클라에서 결정론 합성(캐노니컬+변형+픽셀편집+호흡,
// 4x 니어리스트)해 서버(ffmpeg)가 조립한다 — 비교뷰 다운로드와 같은 경로.
async function downloadRowVideo(stateName, fmt) {
  const play = playList(stateName);
  if (!play.length) throw new Error("empty sequence");
  const st = run.states.find((s) => s.name === stateName);
  const [cellW, cellH] = cellDims(stateName);
  const bcfg = stateBreathe(stateName);
  const pattern = bcfg ? breathePattern(bcfg, play.length) : [];
  const scale = 4;
  const frames = [];
  for (let i = 0; i < play.length; i++) {
    const f = frameOf(stateName, play[i]);
    const image = f ? img(f.url) : null;
    if (!(image && image.complete && image.naturalWidth)) throw new Error("frame images still loading");
    const base = document.createElement("canvas");
    base.width = cellW;
    base.height = cellH;
    const bx = base.getContext("2d");
    bx.imageSmoothingEnabled = false;
    drawFrameInto(bx, image, getTransform(stateName, play[i]), cellW, cellH,
      snapScaleFor(stateName), getPixelOps(stateName, play[i]));
    const composed = bcfg ? breatheComposite(base, bcfg, pattern[i] || 0) : base;
    const out = document.createElement("canvas");
    out.width = cellW * scale;
    out.height = cellH * scale;
    const ox = out.getContext("2d");
    ox.imageSmoothingEnabled = false;
    ox.drawImage(composed, 0, 0, out.width, out.height);
    frames.push(out.toDataURL("image/png"));
  }
  const res = await fetch("/api/compare-gif", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      frames,
      duration_ms: Math.round(1000 / Math.max(1, (st && st.fps) || 6)),
      format: fmt,
    }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || res.status);
  }
  const blob = await res.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${run.characterId || "row"}-${stateName}.${fmt}`;
  link.click();
  URL.revokeObjectURL(link.href);
}
