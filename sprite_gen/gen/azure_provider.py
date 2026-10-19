# SPDX-License-Identifier: Apache-2.0
"""Explicit-only Azure OpenAI / Foundry Images provider.

Shares the OpenAI-compatible image codec, not its endpoint or credential.
Azure CLI / Entra is the default authentication route; API keys require an
explicit AZURE_OPENAI_AUTH=api-key. No automatic retry, redirect, or fallback.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from . import facing_vision, openai_provider as images, refusal
from .base import (
    GEN_TIMEOUT_SECONDS, GenRequest, ProviderRun, TRANSPARENCY_NATIVE,
    announce_api_billing, env_api_key, provider_binary, provider_subprocess_env,
)
from .xai import NO_ANSWER, error_body, read_whole, time_left

ENDPOINT_ENV = "AZURE_OPENAI_ENDPOINT"
DEPLOYMENT_ENV = "AZURE_OPENAI_DEPLOYMENT"
AUTH_ENV = "AZURE_OPENAI_AUTH"
KEY_ENV = "AZURE_OPENAI_API_KEY"
VISION_ENV = "AZURE_OPENAI_VISION_DEPLOYMENT"
SUBSCRIPTION_ENV = "AZURE_SUBSCRIPTION_ID"
TENANT_ENV = "AZURE_TENANT_ID"
AUTH_CLI = "azure-cli"
AUTH_KEY = "api-key"
API_VERSION = "preview"
TOKEN_RESOURCE = "https://cognitiveservices.azure.com"
CLI_TIMEOUT_SECONDS = 30
TRANSPORT_FAILED = "azure-gen: request failed or timed out; no automatic retry (the server may have accepted it)"


def _setting(env: Mapping[str, str], name: str, *, required: bool = False) -> str | None:
    value = env.get(name)
    if value is not None:
        if not value.strip():
            raise SystemExit(f"azure-gen: {name} is set but empty; give it a value or unset it")
        return value.strip()
    if required:
        raise SystemExit(f"azure-gen: {name} is required")
    return None


def _identifier(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise SystemExit(f"azure-gen: {name} must be a subscription/directory UUID") from None


def normalize_endpoint(value: str) -> str:
    """Accept a resource origin or its v1 base, never a project/operation URL."""
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        raise SystemExit(f"azure-gen: invalid {ENDPOINT_ENV}") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or
            parsed.password is not None or parsed.query or parsed.fragment or
            "\\" in value or any(char.isspace() for char in value)):
        raise SystemExit(f"azure-gen: {ENDPOINT_ENV} must be HTTPS without credentials, query, or fragment")
    if parsed.path.rstrip("/") not in ("", "/openai/v1"):
        raise SystemExit(f"azure-gen: {ENDPOINT_ENV} must be a resource origin or /openai/v1 base, not a project or operation URL")
    if port == 0:
        raise SystemExit(f"azure-gen: invalid port in {ENDPOINT_ENV}")
    return urllib.parse.urlunsplit(("https", parsed.netloc, "/openai/v1", "", ""))


@dataclass(frozen=True)
class Config:
    endpoint: str
    auth: str
    deployment: str | None
    vision_deployment: str | None
    subscription: str | None
    tenant: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        endpoint = normalize_endpoint(_setting(env, ENDPOINT_ENV, required=True))
        auth = _setting(env, AUTH_ENV) or AUTH_CLI
        if auth not in (AUTH_CLI, AUTH_KEY):
            raise SystemExit(f"azure-gen: {AUTH_ENV} must be {AUTH_CLI} or {AUTH_KEY}; no auth fallback")
        return cls(endpoint, auth, _setting(env, DEPLOYMENT_ENV), _setting(env, VISION_ENV),
                   _identifier(_setting(env, SUBSCRIPTION_ENV), SUBSCRIPTION_ENV) if auth == AUTH_CLI else None,
                   _identifier(_setting(env, TENANT_ENV), TENANT_ENV) if auth == AUTH_CLI else None)

    def image_deployment(self, model: str | None) -> str:
        deployment = model if model is not None else self.deployment
        if not deployment or not deployment.strip():
            raise SystemExit(f"azure-gen: provide an Azure deployment name with --model or {DEPLOYMENT_ENV}")
        return deployment.strip()

    def inspection_deployment(self) -> str:
        if not self.vision_deployment:
            raise SystemExit(f"azure-gen: inspection requires {VISION_ENV}; no image is bought without this configuration")
        return self.vision_deployment

    def url(self, operation: str) -> str:
        return f"{self.endpoint}/{operation}?api-version={API_VERSION}"


@dataclass(frozen=True)
class Credential:
    source: str
    header: str
    token: str = field(repr=False)

    @property
    def header_value(self) -> str:
        return f"Bearer {self.token}" if self.header == "Authorization" else self.token


def _cli_json(arguments: list[str]) -> Any:
    command = [provider_binary("az"), *arguments, "--output", "json", "--only-show-errors"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", env=provider_subprocess_env(), timeout=CLI_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit("azure-gen: Azure CLI could not run; install az and sign in with az login; no auth fallback") from None
    if completed.returncode:
        # stdout may be a partial token and stderr may contain login data; never echo either.
        raise SystemExit(f"azure-gen: Azure CLI exited {completed.returncode}; check az login and the selected subscription/tenant; no auth fallback")
    try:
        return json.loads(completed.stdout)
    except (ValueError, TypeError):
        raise SystemExit("azure-gen: Azure CLI returned invalid credential metadata") from None


def resolve_credential(config: Config, env: Mapping[str, str] | None = None) -> Credential:
    if config.auth == AUTH_KEY:
        token = env_api_key(KEY_ENV, env, who="azure-gen", also="; no auth fallback")
        if token is None:
            raise SystemExit(f"azure-gen: {KEY_ENV} is required with {AUTH_ENV}=api-key; no auth fallback")
        source, header = KEY_ENV, "api-key"
    else:
        scope = []
        if config.subscription:
            scope = ["--subscription", config.subscription]
            if config.tenant:
                account = _cli_json(["account", "show", *scope])
                if not isinstance(account, dict) or str(account.get("tenantId", "")).casefold() != config.tenant:
                    raise SystemExit("azure-gen: selected subscription does not belong to AZURE_TENANT_ID; no auth fallback")
        elif config.tenant:
            scope = ["--tenant", config.tenant]
        reply = _cli_json(["account", "get-access-token", "--resource", TOKEN_RESOURCE, *scope])
        token = reply.get("accessToken") if isinstance(reply, dict) else None
        source, header = AUTH_CLI, "Authorization"
    if (not isinstance(token, str) or len(token.strip()) < 8 or
            any(ord(char) <= 32 or ord(char) >= 127 for char in token.strip())):
        raise SystemExit("azure-gen: authentication returned an empty or malformed credential")
    return Credential(source, header, token.strip())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_json(url: str, credential: Credential, data: bytes, content_type: str, *, deadline: float) -> tuple[int, Any, Any]:
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header(credential.header, credential.header_value)
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", content_type)
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=time_left(GEN_TIMEOUT_SECONDS, deadline)) as response:
            raw = read_whole(response, GEN_TIMEOUT_SECONDS, deadline).decode("utf-8")
            return response.status, json.loads(raw) if raw else {}, response.headers
    except urllib.error.HTTPError as error:
        try:
            return error.code, error_body(error, GEN_TIMEOUT_SECONDS, deadline), error.headers
        except NO_ANSWER:
            raise SystemExit(TRANSPORT_FAILED) from None
    except NO_ANSWER:
        raise SystemExit(TRANSPORT_FAILED) from None
    except (ValueError, UnicodeError):
        raise SystemExit("azure-gen: response was not valid JSON; no retry or provider fallback") from None


def _checked_reply(config: Config, credential: Credential, operation: str, data: bytes, content_type: str) -> tuple[Any, Any]:
    deadline = time.monotonic() + GEN_TIMEOUT_SECONDS
    status, reply, headers = http_json(config.url(operation), credential, data, content_type, deadline=deadline)
    if status != 200:
        rejected = refusal.read("azure", reply)
        reason = "credential rejected" if status in (401, 403) else rejected.reason("request failed")
        raise SystemExit(f"azure-gen: {reason} (HTTP {status}){rejected.suffix()}; "
                         f"no retry or provider fallback{refusal.said(reply, credential.token)}")
    return reply, headers


class AzureProvider:
    name = "azure"
    transparency = TRANSPARENCY_NATIVE

    def validate_inspection(self) -> None:
        Config.from_env().inspection_deployment()

    def generate(self, request: GenRequest, workdir: Path) -> ProviderRun:
        config = Config.from_env()
        fields = images._fields(replace(request, model=config.image_deployment(request.model)), who="azure-gen")
        refs = [Path(ref) for ref in request.refs]
        if refs:
            operation = "images/edits"
            data, content_type = images._multipart_request(fields, refs, who="azure-gen")
        else:
            operation = "images/generations"
            data, content_type = images._json_request(fields)
        credential = resolve_credential(config)
        announce_api_billing(self.name, credential.source,
                             f" (deployment={fields['model']}, quality={fields['quality']}, size={fields['size']}).")
        started = time.monotonic()
        reply, headers = _checked_reply(config, credential, operation, data, content_type)
        items = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise SystemExit("azure-gen: expected exactly one image in the response; nothing published")
        images._publish_image(items[0], request.raw, who="azure-gen")
        return ProviderRun(self.name, time.monotonic() - started, fields["model"], extra={
            "auth_source": credential.source, "transport": "azure-openai-api",
            "endpoint": "/" + operation, "api_version": API_VERSION,
            "quality": fields["quality"], "size": fields["size"], "background": fields["background"],
            **({"usage": reply["usage"]} if reply.get("usage") is not None else {}),
            "rate_limit": images.rate_limit_headers(headers),
        })

    def inspect_facing(self, path: Path, workdir: Path) -> tuple[str, dict]:
        return self._inspect(path, facing_vision.INSPECTION_PROMPT, facing_vision.FACING_LABEL, "low")

    def inspect_image(self, path: Path, workdir: Path, prompt: str, *, label: str = "vision check") -> tuple[str, dict]:
        return self._inspect(path, prompt, label, "high")

    def _inspect(self, path: Path, prompt: str, label: str, detail: str) -> tuple[str, dict]:
        config = Config.from_env()
        deployment = config.inspection_deployment()
        body = facing_vision.request_body(path, deployment, prompt=prompt, detail=detail)
        credential = resolve_credential(config)
        announce_api_billing(self.name, credential.source, f" ({label}, deployment={deployment}).")
        reply, _headers = _checked_reply(config, credential, "responses", json.dumps(body).encode("utf-8"), "application/json")
        text, metadata = facing_vision.response_text(200, reply, label=label)
        return text, {**metadata, "auth_source": credential.source, "transport": "azure-openai-api"}
