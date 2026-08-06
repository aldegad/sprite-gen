// SPDX-License-Identifier: Apache-2.0
// composer/i18n.js — bilingual strings (en/ko). Classic script, shared globals.

const STR = {
  en: {
    title: "compose",
    mount: "Mount folder",
    remount: "Change folder",
    treeHead: "Library",
    emptyTitle: "Compose a sprite from a folder",
    emptySub: "Mount a folder of images, then drag files onto rows to build each animation state. Originals are never copied or changed.",
    emptyMount: "Mount a folder",
    addRow: "+ Add row",
    newRowName: "new-state",
    rowEmptyHint: "Drag image files here",
    frames: (n) => `${n} frame${n === 1 ? "" : "s"}`,
    deleteRow: "Delete row",
    mountPrompt: "Absolute path of the folder to mount:",
    mounted: (p) => p,
    mountFail: (m) => `Mount failed: ${m}`,
    browseFail: (m) => `Browse failed: ${m}`,
    ready: "Ready",
    dropAdded: (name, row) => `Added ${name} to ${row}`,
    noMount: "No folder mounted yet",
    langLabel: "한국어",
  },
  ko: {
    title: "조립",
    mount: "폴더 물기",
    remount: "폴더 바꾸기",
    treeHead: "라이브러리",
    emptyTitle: "폴더에서 스프라이트를 조립한다",
    emptySub: "이미지 폴더를 물고, 파일을 줄로 드래그해서 각 애니메이션 상태를 짠다. 원본은 복사도 변경도 되지 않는다.",
    emptyMount: "폴더 물기",
    addRow: "+ 줄 추가",
    newRowName: "새-상태",
    rowEmptyHint: "여기로 이미지 파일을 드래그",
    frames: (n) => `${n}장`,
    deleteRow: "줄 삭제",
    mountPrompt: "물릴 폴더의 절대 경로:",
    mounted: (p) => p,
    mountFail: (m) => `폴더 물기 실패: ${m}`,
    browseFail: (m) => `탐색 실패: ${m}`,
    ready: "준비됨",
    dropAdded: (name, row) => `${name} 을(를) ${row} 에 추가`,
    noMount: "아직 물린 폴더 없음",
    langLabel: "English",
  },
};

let lang = "en";
function t(key, ...args) {
  const v = STR[lang][key];
  return typeof v === "function" ? v(...args) : v;
}
