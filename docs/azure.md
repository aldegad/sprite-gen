# Azure OpenAI / Foundry images

> Owns: the explicit Azure provider, its authentication and deployment configuration · Index: [docs/README.md](README.md)

`sprite-gen gen --provider azure` uses an image deployment on your Azure resource.
It shares sprite-gen's image encoding, verified PNG publishing, alpha handling,
row extraction and atlas composition. It does not require a Codex/ChatGPT or Grok
login, and adds no Python runtime dependency.

**Every image or vision call is billed to Azure**, even when the credential comes
from `az login`. Azure never becomes the default provider, a guided subscription
route, or a fallback for another provider. The CLI announces the charge before
each request, including inside `gen-set`. Budget enforcement belongs to the
caller; the provider reports usage, not a dollar estimate or spending guarantee.

## Azure CLI / Microsoft Entra

Install [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) and
sign in to an identity with data-plane permission on your image deployment:

```bash
az login
export AZURE_OPENAI_ENDPOINT="https://YOUR-RESOURCE.services.ai.azure.com"
export AZURE_OPENAI_DEPLOYMENT="YOUR-IMAGE-DEPLOYMENT"

sprite-gen gen --provider azure --prompt "an original blue pixel-art robot" \
  --quality medium --out robot.png
```

`AZURE_OPENAI_AUTH` defaults to `azure-cli`. An API key in the environment does
not change that choice. Missing/expired CLI credentials fail rather than
switching to a key, another account, or another image provider.

On shared machines, pin the subscription with `AZURE_SUBSCRIPTION_ID`. Optionally
set `AZURE_TENANT_ID` as a directory UUID too: the provider verifies the selected
subscription's tenant, then requests its token. It never runs `az account set`
or changes the CLI's global default. A tenant alone is also supported.
`AZURE_CONFIG_DIR` is honored if you maintain separate Azure CLI profiles.

Authentication uses the resolved `az` executable (including Windows `.cmd`
shims), UTF-8 subprocess I/O, a bounded 30-second wait, and the standard
sprite-gen subprocess environment. CLI output is not copied into reports or
errors; bearer tokens remain in memory.

## Explicit API-key authentication

Set `AZURE_OPENAI_API_KEY` securely, then opt into that authentication route:

```bash
export AZURE_OPENAI_AUTH="api-key"
export AZURE_OPENAI_ENDPOINT="https://YOUR-RESOURCE.openai.azure.com/openai/v1/"
export AZURE_OPENAI_DEPLOYMENT="YOUR-IMAGE-DEPLOYMENT"
# Set AZURE_OPENAI_API_KEY through your secret-management mechanism.

sprite-gen gen --provider azure --prompt "an original blue pixel-art robot" \
  --quality medium --out robot.png
```

Key mode sends the `api-key` header and never invokes Azure CLI. A missing,
empty, malformed or rejected key does not fall back to CLI credentials. Do not
put keys or tokens in prompts, reference files, source control, or reports.

## Endpoint and deployment

- `AZURE_OPENAI_ENDPOINT` accepts an HTTPS resource origin or its
  `/openai/v1` base. Azure OpenAI and Foundry resource hostnames both work.
- A Foundry project URL, an individual operation URL, embedded credentials,
  query strings and fragments are refused before any request.
- `--model` means an **Azure deployment name**, overriding
  `AZURE_OPENAI_DEPLOYMENT`. There is no assumed deployment/model fallback.
- Blank configuration values are errors. A deployment must actually exist and
  support the requested image operation.
- The provider uses the v1 API with `api-version=preview`. Its Entra audience is
  public Azure's `https://cognitiveservices.azure.com`; other sovereign-cloud
  audiences are not covered by this initial integration.

See Microsoft's [image API documentation](https://learn.microsoft.com/azure/foundry/openai/how-to/dall-e)
for deployment availability, supported parameters and permissions.

## Reference edits and component rows

```bash
sprite-gen gen --provider azure --ref robot.png \
  --prompt "Keep this character identical; draw four distinct walking poses in one row." \
  --aspect-ratio 3:1 --quality medium --out walk.png

sprite-gen gen-set --provider azure --run-dir RUN
```

A call without references sends JSON to `/images/generations`; references send
ordered multipart `image[]` parts to `/images/edits`. The shared image codec
supports the same aspect-ratio mapping and quality vocabulary as the OpenAI
provider. `--resolution` is refused rather than silently ignored. The deployed
model remains authoritative about supported sizes, quality levels, prompt
length and reference limits; unsupported requests fail with Azure's response.

`--transparent` follows sprite-gen's existing alpha policy: native alpha without
references, chroma cleanup by default with references, and explicit
`--alpha-mode` overrides. Outputs still pass the common PNG/alpha checks.

## Optional facing and logo inspection

Image generation/editing needs no vision deployment. Workflows that inspect
images need an explicitly configured, vision-capable Responses deployment:

```bash
export AZURE_OPENAI_VISION_DEPLOYMENT="YOUR-VISION-DEPLOYMENT"
```

This enables reference-facing checks (`--ref` with `--facing`), correction, and
logo text verification through the same Azure endpoint and selected credential.
Missing vision configuration is refused before the workflow buys an image.
The inspection call uses `/responses`, the named vision deployment, `store:
false`, and the existing facing/text prompts. It never calls the public OpenAI
endpoint or assumes that the image deployment can inspect images.

## Failure and reporting contract

- No automatic retries, redirects, authentication fallback, or provider
  fallback. A timeout or broken response may already have been billed.
- HTTP failures retain the normal `(HTTP NNN)` and `code=` fields. Credential
  echoes and signed URL query strings are redacted with the shared refusal
  formatter; Azure error codes are not presumed to use OpenAI's moderation
  taxonomy.
- Generation respects `SPRITE_GEN_GEN_TIMEOUT_SECONDS` from the first API send,
  including reading the answer. CLI authentication has its separate bounded wait.
- The existing output is not replaced unless a valid image is returned.
- Reports include the deployment, selected authentication source, transport,
  operation path, API version, requested quality/size/background, returned
  token usage when provided, and rate-limit headers. They contain no credential.
- Credential readiness is not proof of model access, remaining quota, or a
  successful generation.

The provider has no character names, personal resource allowlist, fixed pricing,
per-character call limits, Unix-only locks, or dependency on a Unity project.
