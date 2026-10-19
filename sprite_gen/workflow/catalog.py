# SPDX-License-Identifier: Apache-2.0
# Modified: label the explicit-only Azure provider without adding a guided billing route.
"""The two user journeys and their selectable fields, declared once."""
from sprite_gen.gen import CODEX_UNAVAILABLE_CHOICES, CODEX_UNAVAILABLE_SETTING, PROVIDERS

MOTION_METHODS = {
    "gpt-rows": {"label": "지피티 이미지 스프라이트", "provider": "codex", "doc": "docs/atlas-workflow.md",
                 "steps": ["prepare", "gen-set", "extract", "compose-atlas", "compose-gif"]},
    "grok-video": {"label": "그록 영상", "provider": "grok", "doc": "docs/video-pipeline.md",
                   "steps": ["video-set"]},
}
# One label per registered provider. A provider with no label here is a bug, not
# a row to drop quietly: the mapping is checked rather than zipped positionally,
# so registering a backend in `gen.PROVIDERS` without naming it fails at import.
PROVIDER_LABELS = {
    "azure": "Azure OpenAI / Foundry (per-call API billing)",
    "codex": "지피티",
    "grok": "그록",
    "openai": "지피티 API (서버·SaaS 용, 호출당 과금)",
}
# Subscription-first (수홍 2026-09-20): the guided flow offers only the routes
# that run on a subscription the user already pays for as the image provider.
# `openai` spends metered API credit per call, so it is never offered there; the
# one place it is a choice is the answer for a codex that cannot run (below).
GUIDED_PROVIDERS = ("codex", "grok")
_unlabelled = [provider for provider in PROVIDERS if provider not in PROVIDER_LABELS]
if _unlabelled:
    raise RuntimeError(f"workflow catalog: no label for provider(s) {', '.join(_unlabelled)}")
_unregistered = [provider for provider in GUIDED_PROVIDERS if provider not in PROVIDERS]
if _unregistered:
    raise RuntimeError(f"workflow catalog: guided provider(s) not registered: {', '.join(_unregistered)}")

FIELDS = {
    "image_provider": {"options": {provider: PROVIDER_LABELS[provider] for provider in GUIDED_PROVIDERS},
                       "question": "이미지를 지피티로 만들까요, 그록으로 만들까요?"},
    "motion_method": {"options": {k: v["label"] for k, v in MOTION_METHODS.items()},
                      "question": "동작을 그록 영상으로 만들까요, 지피티 이미지 스프라이트로 만들까요?"},
    "curation": {"options": {"open": "열기", "skip": "열지 않기"},
                 "question": "큐레이션뷰에서 결과를 확인하고 골라볼까요?"},
}
# Settings every profile shares, asked once and saved beside the profiles (`preferences`, `general`). The one so
# far: what a call that names no provider does when codex cannot run here (not signed in, or its usage used up) —
# `gen.codex_unavailable_choice` reads it, docs/gen.md "When codex cannot run" owns the rule.
GENERAL_FIELDS = {
    CODEX_UNAVAILABLE_SETTING: {
        "options": {"ask": "멈추고 묻기 (기본)", "grok": "그록으로 만들기", "openai": "OpenAI API 로 만들기 (호출당 과금)"},
        "question": "지피티(codex)를 쓸 수 없을 때 — 로그인이 없거나 사용량을 다 썼을 때 — 어떻게 할까요?",
    },
}
if list(GENERAL_FIELDS[CODEX_UNAVAILABLE_SETTING]["options"]) != list(CODEX_UNAVAILABLE_CHOICES):
    raise RuntimeError("workflow catalog: the codex-unavailable options must be gen.CODEX_UNAVAILABLE_CHOICES")

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


def validate_general(values: dict) -> dict:
    if not isinstance(values, dict):
        raise ValueError("general settings must be an object")
    for key, value in values.items():
        if key not in GENERAL_FIELDS or not isinstance(value, str) or value not in GENERAL_FIELDS[key]["options"]:
            raise ValueError(f"invalid general setting: {key}")
    return dict(values)
