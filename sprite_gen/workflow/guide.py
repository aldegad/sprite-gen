# SPDX-License-Identifier: Apache-2.0
"""Read-only conversation guide. It never generates, opens a view or saves choices."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .access import probe_access
from .catalog import FIELDS, FLOWS, MOTION_METHODS, validate_choices
from .preferences import read_settings


def guide(kind: str, *, stage: str = "start", base_image: Path | None = None, result: Path | None = None,
          choices: dict | None = None, confirmed_access: tuple[str, ...] = (), confirm_api_billing: bool = False) -> dict:
    explicit = validate_choices(kind, choices or {})
    if stage not in ("start", "finish"):
        raise ValueError("stage must be start or finish")
    if base_image is not None and (kind != "sprite" or not Path(base_image).is_file()):
        raise ValueError("--base-image must name an existing sprite reference image")
    if stage == "finish" and (result is None or not Path(result).exists()):
        raise ValueError("finish requires an existing --result file or run directory, after generation and QA")
    snapshot = read_settings()
    stored = snapshot["profiles"].get(kind, {})
    selected = {**stored, **explicit}
    sources = {key: "request" if key in explicit else "saved" for key in selected}
    active_fields = [key for key in FLOWS[kind]["fields"] if key != "curation"]
    if base_image is not None:
        active_fields.remove("image_provider")
    questions, blocked, access = [], [], []
    if stage == "start":
        for field in active_fields:
            if field not in selected:
                questions.append({"field": field, **FIELDS[field]})
        # Report both candidates while choosing, then only the services actually
        # used. One probe per provider/auth route; a row run always pins codex.
        required = set()
        if "image_provider" in active_fields and selected.get("image_provider"):
            required.add((selected["image_provider"], False))
        motion = MOTION_METHODS.get(selected.get("motion_method", ""))
        if motion:
            required.add((motion["provider"], selected["motion_method"] == "grok-video"))
        candidates = required or {(provider, False) for provider in FIELDS["image_provider"]["options"]}
        for provider, is_video in sorted(candidates):
            state = probe_access(provider, video=is_video)
            access.append(state)
            if (provider, is_video) not in required:
                continue
            if state["login"] == "unavailable":
                blocked.append({"provider": provider, "reason": state["reason"]})
            elif state["billing"] == "api-credit":
                if not confirm_api_billing:
                    questions.append({"field": "confirm_api_billing", "question": "그록 영상에 별도 API 크레딧을 사용할까요?", "options": {"yes": "사용", "no": "구독 로그인으로 변경"}})
            elif provider not in confirmed_access and not any(q["field"] == f"access_{provider}" for q in questions):
                questions.append({"field": f"access_{provider}", "question": f"{FIELDS['image_provider']['options'][provider]} 계정의 구독과 이미지/영상 이용 가능 여부를 확인해 주세요.",
                                  "options": {"yes": "사용 가능", "no": "사용 불가"}})
    else:
        missing = [field for field in active_fields if field not in selected]
        if missing:
            raise ValueError("finish requires the actual generation choices: " + ", ".join(missing))
        if "curation" not in selected:
            questions.append({"field": "curation", **FIELDS["curation"]})
        elif not stored:
            questions.append({"field": "save_defaults", "question": "이번 선택을 다음 작업에도 기본으로 사용할까요?",
                              "options": {"yes": "기본 설정으로 저장", "no": "이번에만 사용"}})
    pipeline = None
    if not any(field not in selected for field in active_fields):
        motion = MOTION_METHODS.get(selected.get("motion_method", ""))
        pipeline = {"base_image": str(base_image) if base_image else None,
                    "image_provider": selected.get("image_provider") if "image_provider" in active_fields else None,
                    "generation_provider": motion["provider"] if motion else selected.get("image_provider"),
                    "steps": motion["steps"] if motion else ["gen"],
                    "doc": motion["doc"] if motion else "docs/gen.md"}
    labels = [FIELDS[key]["options"][selected[key]] for key in active_fields if key in selected]
    return {"kind": kind, "stage": stage, "status": "blocked" if blocked else "needs-input" if questions else "ready",
            "notice": " / ".join(labels), "selected": selected, "sources": sources,
            "settings_revision": snapshot["revision"], "settings_path": snapshot["path"],
            "access": access, "blocked": blocked, "questions": questions, "pipeline": pipeline,
            "curation": selected.get("curation", "ask") if stage == "finish" else "after-result",
            "save_candidate": selected if stage == "finish" and "curation" in selected else None}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kind", required=True, choices=FLOWS)
    parser.add_argument("--stage", choices=("start", "finish"), default="start")
    parser.add_argument("--base-image", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--confirmed-access", action="append", choices=FIELDS["image_provider"]["options"], default=[])
    parser.add_argument("--confirm-api-billing", action="store_true")
    for key, field in FIELDS.items():
        parser.add_argument("--" + key.replace("_", "-"), choices=field["options"])


def run(**kwargs) -> int:
    choices = {key: kwargs.pop(key) for key in FIELDS if kwargs.get(key) is not None}
    for key in FIELDS:
        kwargs.pop(key, None)
    try:
        result = guide(choices=choices, **kwargs)
    except (ValueError, OSError) as exc:
        raise SystemExit(f"workflow: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready" else 2
