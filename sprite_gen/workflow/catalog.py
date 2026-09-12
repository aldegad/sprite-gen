# SPDX-License-Identifier: Apache-2.0
"""The two user journeys and their selectable fields, declared once."""
from sprite_gen.gen import PROVIDERS

MOTION_METHODS = {
    "gpt-rows": {"label": "지피티 이미지 스프라이트", "provider": "codex", "doc": "docs/atlas-workflow.md",
                 "steps": ["prepare", "gen-set", "extract", "compose-atlas", "compose-gif"]},
    "grok-video": {"label": "그록 영상", "provider": "grok", "doc": "docs/video-pipeline.md",
                   "steps": ["video-set"]},
}
# One label per provider, keyed by name — never zipped against PROVIDERS positionally.
# `zip` truncates to the shorter side, so a newly registered provider with no label here
# would have been dropped from the question silently; a dict lookup fails loudly instead.
PROVIDER_LABELS = {"codex": "지피티", "grok": "그록", "agy": "안티그래비티"}
FIELDS = {
    "image_provider": {"options": {name: PROVIDER_LABELS[name] for name in PROVIDERS},
                       "question": "이미지를 지피티, 그록, 안티그래비티 중 무엇으로 만들까요?"},
    "motion_method": {"options": {k: v["label"] for k, v in MOTION_METHODS.items()},
                      "question": "동작을 그록 영상으로 만들까요, 지피티 이미지 스프라이트로 만들까요?"},
    "curation": {"options": {"open": "열기", "skip": "열지 않기"},
                 "question": "큐레이션뷰에서 결과를 확인하고 골라볼까요?"},
}
FLOWS = {
    "sprite": {"label": "스프라이트 만들기", "fields": ("image_provider", "motion_method", "curation")},
    "image": {"label": "이미지 만들기", "fields": ("image_provider", "curation")},
}


def validate_choices(kind: str, choices: dict) -> dict:
    if kind not in FLOWS:
        raise ValueError(f"unknown workflow: {kind}")
    if not isinstance(choices, dict):
        raise ValueError("workflow choices must be an object")
    for key, value in choices.items():
        if key not in FLOWS[kind]["fields"] or not isinstance(value, str) or value not in FIELDS[key]["options"]:
            raise ValueError(f"invalid {kind} choice: {key}")
    return dict(choices)
