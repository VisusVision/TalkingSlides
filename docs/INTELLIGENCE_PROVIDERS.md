# Intelligence Provider Layers

This document explains the intelligence provider chain used by Lesson Intelligence and Analytics Intelligence, the intended production workflow, environment variables, and what remains to be implemented before enabling paid/external providers.

## Overview

- Lesson Intelligence and Analytics Intelligence use configurable provider chains. Providers are tried in order until a valid, stable JSON response is returned.
- Layer 1 (heuristic) is always available and deterministic; it is the final fallback.
- Layer 2 (Ollama/local) is an optional local model provider (e.g. `qwen2.5:7b`) used for higher-quality results when available.
- Layer 3 (paid/external) is a placeholder for future external providers and is disabled by default.

## Provider Layers

### Layer 1 — Heuristic (always available)

- Config name: `heuristic` (example usage: `LESSON_INTELLIGENCE_PROVIDER_CHAIN=heuristic`).
- No external services or API keys required.
- Deterministic outputs with explicit limitation notes when payloads are compacted/truncated.
- Supports Turkish and English localization and will include `language_detected` metadata where applicable.
- Always used as the final fallback when other providers fail, time out, or return invalid results.

### Layer 2 — Ollama / Local model (optional)

- Config name: `ollama` (example usage: `LESSON_INTELLIGENCE_PROVIDER_CHAIN=ollama,heuristic`).
- Runs against a local LLM endpoint such as Ollama. Example model: `qwen2.5:7b`.
- Timeout-bound: requests are bounded by configured timeouts to avoid long blocking calls.
- Expected to return stable, predictable JSON. Invalid JSON triggers fallback to the next provider in the chain.
- Falls back to `heuristic` on network failure, timeout, non-200, or malformed responses.

### Layer 3 — Paid / External provider (placeholder)

- Config name examples: `openai` or other provider labels (example chain: `openai,ollama,heuristic`).
- Disabled by default. Enabling requires explicit allow flags (see env examples below).
- Not implemented as a production paid provider in this repository — treat as a TODO with safety guards.

## Environment examples

Local heuristic only:

```
LESSON_INTELLIGENCE_PROVIDER_CHAIN=heuristic
ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN=heuristic
```

Local Ollama primary (recommended for local QA):

```
LESSON_INTELLIGENCE_PROVIDER_CHAIN=ollama,heuristic
ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN=ollama,heuristic
OLLAMA_LESSON_INTELLIGENCE_BASE_URL=http://localhost:11434
OLLAMA_ANALYTICS_INTELLIGENCE_BASE_URL=http://localhost:11434
OLLAMA_LESSON_INTELLIGENCE_MODEL=qwen2.5:7b
OLLAMA_ANALYTICS_INTELLIGENCE_MODEL=qwen2.5:7b
OLLAMA_INTELLIGENCE_TIMEOUT_SECONDS=20
```

Future paid primary (do not enable without completing checklist):

```
LESSON_INTELLIGENCE_PROVIDER_CHAIN=openai,ollama,heuristic
ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN=openai,ollama,heuristic
LESSON_INTELLIGENCE_ALLOW_EXTERNAL=1
ANALYTICS_INTELLIGENCE_ALLOW_EXTERNAL=1
```

## Fallback behavior

- Providers are invoked in the configured order. A successful provider means a valid, parseable JSON response.
- Failed attempts (network error, non-200, timeout, invalid JSON, internal server error) are recorded in `provider_chain_attempts` and do not leak provider secrets.
- `fallback_used=true` in response metadata whenever the eventual output came from a provider later in the chain than the configured first provider.
- Provider URLs and secrets are never returned to API consumers.

## Safety and policy constraints

- Intelligence outputs must not auto-edit transcripts or re-render lessons.
- No external paid calls are made by default; enabling external providers must be explicit and audited.
- Avoid any leakage of student identity or raw private paths in responses.
- Compacted payloads include limitation notes to make downstream consumers aware of truncation.

## Before enabling Layer 3 (paid/external)

Checklist for production readiness before enabling a paid/external provider as a primary source:

1. Choose and vet a paid provider and client library.
2. Implement a provider client with strong timeout, retry, and cost controls.
3. Add secret environment variable handling (never commit secrets).
4. Implement quota/cost monitoring and alerting for large volume runs.
5. Add deterministic prompt hardening and output validation (strict JSON schemas).
6. Add integration tests that simulate success, timeouts, invalid JSON, and provider errors.
7. Add production monitoring and tracing of provider attempts and costs.
8. Verify multilingual outputs (Turkish/English) for quality and safety.
9. Confirm no PII or raw file paths are exposed in responses.

## Notes for local development

- To enable local Ollama for testing, run a local Ollama server (or compatible local LLM endpoint) and point the env vars above at it.
- Use the `heuristic` provider as your safe fallback when iterating on prompt/response behavior.

## Attribution

This file documents current behavior and intended production workflow as of the repository state on 2026-05-18.
