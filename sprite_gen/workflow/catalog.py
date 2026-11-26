# SPDX-License-Identifier: Apache-2.0
"""The two user journeys and their selectable fields, declared once."""
from sprite_gen.gen import CODEX_UNAVAILABLE_ASK, CODEX_UNAVAILABLE_CHOICES, CODEX_UNAVAILABLE_SETTING, PROVIDERS
from sprite_gen.gen.base import PROVIDER_CODEX, PROVIDER_GROK, PROVIDER_OPENAI
from sprite_gen.gen.codex_provider import (DRIVER_EFFORT, DRIVER_EFFORT_SETTING, DRIVER_EFFORTS, DRIVER_MODEL,
                                           DRIVER_MODEL_PATTERN, DRIVER_MODEL_SETTING)

MOTION_METHODS = {
    "gpt-rows": {"label": "지피티 이미지 스프라이트", "provider": PROVIDER_CODEX, "doc": "docs/atlas-workflow.md",
                 "steps": ["prepare", "gen-set", "extract", "compose-atlas", "compose-gif"]},
    "grok-video": {"label": "그록 영상", "provider": PROVIDER_GROK, "doc": "docs/video-pipeline.md",
                   "steps": ["video-set"]},
}
# One label per registered provider. A provider with no label here is a bug, not
# a row to drop quietly: the mapping is checked rather than zipped positionally,
# so registering a backend in `gen.PROVIDERS` without naming it fails at import.
PROVIDER_LABELS = {
    PROVIDER_CODEX: "지피티",
    PROVIDER_GROK: "그록",
    PROVIDER_OPENAI: "지피티 API (서버·SaaS 용, 호출당 과금)",
}
# Subscription-first (수홍 2026-09-20): the guided flow offers only the routes
# that run on a subscription the user already pays for as the image provider.
# `openai` spends metered API credit per call, so it is never offered there; the
# one place it is a choice is the answer for a codex that cannot run (below).
GUIDED_PROVIDERS = (PROVIDER_CODEX, PROVIDER_GROK)
_unlabelled = [provider for provider in PROVIDERS if provider not in PROVIDER_LABELS]
if _unlabelled:
    raise RuntimeError(f"workflow catalog: no label for provider(s) {', '.join(_unlabelled)}")
_unregistered = [provider for provider in GUIDED_PROVIDERS if provider not in PROVIDERS]
if _unregistered:
    raise RuntimeError(f"workflow catalog: guided provider(s) not registered: {', '.join(_unregistered)}")

# The journeys' and fields' names, each written once: the keys of FLOWS and FIELDS, which `guide` reads by them.
SPRITE, IMAGE = "sprite", "image"
IMAGE_PROVIDER, MOTION_METHOD, CURATION = "image_provider", "motion_method", "curation"
FIELDS = {
    IMAGE_PROVIDER: {"options": {provider: PROVIDER_LABELS[provider] for provider in GUIDED_PROVIDERS},
                     "question": "이미지를 지피티로 만들까요, 그록으로 만들까요?"},
    MOTION_METHOD: {"options": {k: v["label"] for k, v in MOTION_METHODS.items()},
                    "question": "동작을 그록 영상으로 만들까요, 지피티 이미지 스프라이트로 만들까요?"},
    CURATION: {"options": {"open": "열기", "skip": "열지 않기"},
               "question": "큐레이션뷰에서 결과를 확인하고 골라볼까요?"},
}
# Settings every profile shares, saved beside the profiles (`preferences`, `general`). What a call that names no
# provider does when codex cannot run here (not signed in, or its usage used up) — `gen.codex_unavailable_choice`
# reads it, docs/gen.md "When codex cannot run" owns the rule; the guide asks it once. And the model and effort a
# codex generation runs on (`codex_provider.resolve_driver`, docs/gen.md "The codex driver model"), saved only when
# asked for. A field whose `options` is None takes any value its `pattern` matches.
GENERAL_FIELDS = {
    CODEX_UNAVAILABLE_SETTING: {
        "options": {CODEX_UNAVAILABLE_ASK: "멈추고 묻기 (기본)", PROVIDER_GROK: "그록으로 만들기",
                    PROVIDER_OPENAI: "OpenAI API 로 만들기 (호출당 과금)"},
        "question": "지피티(codex)를 쓸 수 없을 때 — 로그인이 없거나 사용량을 다 썼을 때 — 어떻게 할까요?",
    },
    DRIVER_MODEL_SETTING: {
        "options": None, "pattern": DRIVER_MODEL_PATTERN, "metavar": "MODEL",
        "help": f"the model a codex generation runs on (built in: {DRIVER_MODEL}); --model still wins for one call",
    },
    DRIVER_EFFORT_SETTING: {
        "options": {effort: effort for effort in DRIVER_EFFORTS},
        "help": f"the reasoning effort a codex generation runs at (built in: {DRIVER_EFFORT})",
    },
}
if list(GENERAL_FIELDS[CODEX_UNAVAILABLE_SETTING]["options"]) != list(CODEX_UNAVAILABLE_CHOICES):
    raise RuntimeError("workflow catalog: the codex-unavailable options must be gen.CODEX_UNAVAILABLE_CHOICES")

FLOWS = {
    SPRITE: {"label": "스프라이트 만들기", "fields": (IMAGE_PROVIDER, MOTION_METHOD, CURATION)},
    IMAGE: {"label": "이미지 만들기", "fields": (IMAGE_PROVIDER, CURATION)},
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
        field = GENERAL_FIELDS.get(key)
        if field is None or not isinstance(value, str):
            raise ValueError(f"invalid general setting: {key}")
        if field["options"] is None:
            if not field["pattern"].fullmatch(value):
                raise ValueError(f"invalid general setting: {key}")
        elif value not in field["options"]:
            raise ValueError(f"invalid general setting: {key}")
    return dict(values)
