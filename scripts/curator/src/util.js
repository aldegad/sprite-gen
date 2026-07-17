// SPDX-License-Identifier: Apache-2.0
// curator/util.js — 기하/문자열 순수 유틸 — 상태 없음
// 로드 순서 SSoT = index.html (classic script 전역 어휘 공유; 빌드 스텝 없음)

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
