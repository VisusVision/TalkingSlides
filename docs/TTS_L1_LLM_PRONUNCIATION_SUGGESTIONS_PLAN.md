# Phase TTS-L1: Optional LLM Pronunciation Suggestions Plan

Status: L1A backend endpoint, L1B Studio suggestion UI, and L1C optional Ollama setup docs implemented. Do not add render-path LLM calls, deterministic resolver changes, XTTS recovery changes, worker queue changes, storage changes, transcript/page controls, Google OAuth, avatar/DRM/HLS/publish changes, or subtitles in L1.

Current branch: `feat/tts-l1-llm-suggestions-plan`.

## Executive Summary

TTS-L1 adds optional Studio-assisted pronunciation suggestions for terms that the deterministic D1 resolver already reports as `unknown_terms` or `ambiguous_terms`. It is not render-path intelligence. The LLM is only a helper for teachers while they review preview results.

The product rule is: Llama/Ollama may suggest a spoken form, but it never decides final synthesis pronunciation by itself. Teachers must accept, edit, or ignore each suggestion. Accepted suggestions become normal manual/project overrides, so rerender uses the existing deterministic override path and still works when Llama/Ollama is disabled or unavailable.

## Why Llama/Ollama Is Not In Render Path

Render and rerender must stay deterministic. Video generation should not make pronunciation decisions from a live model call while synthesizing audio.

Reasons:

- No per-word LLM calls during video generation.
- No network dependency during synthesis.
- No random pronunciation changes between rerenders.
- LLM output can vary by model version, prompt drift, sampling behavior, and local runtime state.
- LLM providers can be slow, unavailable, overloaded, or missing from deployment.
- Teacher-approved overrides are safer because they turn uncertain suggestions into explicit project data.
- Existing XTTS -> gTTS -> silent fallback behavior should remain focused on audio provider resilience, not suggestion generation.

The render path should only see deterministic text preparation and saved override maps. If a teacher accepts a suggestion, the accepted spoken form is persisted as an override and becomes ordinary deterministic input.

## Current D1 Foundation

TTS-D1A and TTS-D1B provide the foundation L1 needs:

- `deterministic_resolver.py` finds suspicious unmatched terms as `unknown_terms`.
- The resolver reports `ambiguous_terms` when a term is known in conflicting deterministic maps.
- Preview and synth metadata already carry `unknown_terms`, `ambiguous_terms`, and resolver rules.
- Studio preview already displays unknown and ambiguous terms.
- Teachers can already add detected terms to manual override rows.
- Draft override rows do not mutate transcript text, captions, or render jobs until the teacher saves settings and rerenders.

L1 only improves suggestion quality for those teacher-managed rows. It does not replace D1, change D1 precedence, or bypass manual approval.

## Recommended Flow

1. User runs TTS preview.
2. D1 returns `unknown_terms` and `ambiguous_terms`.
3. Studio shows a `Suggest pronunciations` button near the unknown/ambiguous section.
4. Backend sends only selected unknown or ambiguous terms plus surrounding sentence/context to the optional LLM endpoint.
5. LLM returns suggestions with confidence and reason.
6. Teacher accepts, edits, or rejects each suggestion.
7. Accepted item becomes a manual/project override row.
8. Teacher saves settings.
9. Rerender uses the deterministic saved override, not the LLM.

## Backend API Contract

Recommended Django API endpoint:

```text
POST /api/v1/tts/pronunciation-suggestions/
```

Request example:

```json
{
  "language": "tr",
  "terms": ["HyperBeam", "LangChain"],
  "context": "HyperBeam ile LangChain arasindaki pipeline akisina bakalim.",
  "project_id": 123
}
```

`project_id` is optional and may be omitted or `null`. The endpoint should not require a project to produce suggestions, but project ownership can be used for authorization and audit context when supplied.

Disabled response:

```json
{
  "enabled": false,
  "suggestions": [],
  "fallback_used": true,
  "provider": "",
  "warnings": ["LLM pronunciation suggestions are disabled."]
}
```

Successful response example:

```json
{
  "enabled": true,
  "suggestions": [
    {
      "term": "HyperBeam",
      "suggested_spoken": "haypir biim",
      "category": "mixed_word",
      "confidence": "medium",
      "reason": "English brand or technical term in Turkish text"
    }
  ],
  "fallback_used": false,
  "provider": "ollama",
  "warnings": []
}
```

Rules:

- Authenticated only.
- Rate-limited or capped per user/project.
- Enforce max terms per request.
- Enforce max text/context length.
- Accept only terms selected from current preview metadata or terms explicitly provided by Studio from that section.
- Do not mutate transcript pages.
- Do not mutate project settings.
- Do not mutate render jobs.
- Do not call synthesis.
- Fail open with a readable error, for example `suggestions_disabled`, `provider_timeout`, or `provider_unavailable`.
- Return partial valid suggestions when some terms fail validation.
- Never include secrets, provider credentials, or raw provider stack traces in the response.

L1A implementation files:

- `services/api/core/tts_llm_suggestions.py`
- `services/api/core/views.py::TTSPronunciationSuggestionsView`
- `services/api/core/urls.py` route `tts/pronunciation-suggestions/`
- `tests/integration/test_tts_llm_pronunciation_suggestions.py`

## Provider Strategy

Proposed config defaults:

```text
TTS_LLM_SUGGESTIONS_ENABLED=false
TTS_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_PRONUNCIATION_MODEL=llama3.1:8b
TTS_LLM_SUGGESTION_TIMEOUT_SECONDS=8
TTS_LLM_MAX_TERMS=20
TTS_LLM_CONTEXT_MAX_CHARS=1000
```

Behavior:

- Disabled by default in every environment.
- When disabled, the API returns a clear disabled response without making any network call.
- Ollama is the first planned provider because it can run locally for teacher-assisted review.
- Provider timeout should be short because this is interactive Studio UX.
- Provider abstraction should be small and request-scoped.
- Suggestions are Studio-only. Render, rerender, `/synthesize`, and `tts_client` never depend on the provider.
- Accepted suggestions become ordinary manual/project override rows and must be saved before rerender.

Optional future providers:

- OpenAI-compatible provider behind an explicit config flag.
- Local-only mode that refuses non-local provider URLs.
- Disabled production default unless deployment owners explicitly opt in.

## Local Ollama Setup

This setup is optional. Disabled mode is the default and manual overrides continue to work without Ollama.

1. Install Ollama from the official installer for the host OS.
2. Start Ollama locally and verify it is listening on port `11434`.
3. Pull the configured model:

```bash
ollama pull llama3.1:8b
```

4. For Docker Desktop on Windows, point the API container at the host Ollama process:

```text
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

5. Enable suggestions explicitly:

```text
TTS_LLM_SUGGESTIONS_ENABLED=true
TTS_LLM_PROVIDER=ollama
OLLAMA_PRONUNCIATION_MODEL=llama3.1:8b
TTS_LLM_SUGGESTION_TIMEOUT_SECONDS=8
TTS_LLM_MAX_TERMS=20
TTS_LLM_CONTEXT_MAX_CHARS=1000
```

Leave `TTS_LLM_SUGGESTIONS_ENABLED=false` for the default disabled mode. Preview, manual override rows, save settings, and rerender still work in disabled mode because rerender consumes only saved deterministic overrides.

## Troubleshooting

- Disabled message: expected when `TTS_LLM_SUGGESTIONS_ENABLED=false`; Studio should explain that manual overrides still work.
- Provider timeout: keep `TTS_LLM_SUGGESTION_TIMEOUT_SECONDS` short for Studio UX; retry after confirming Ollama is running and the model is loaded.
- Malformed provider response: the endpoint fails open, drops invalid suggestions, and should show a readable warning rather than mutating settings.
- No suggestions returned: add or edit manual override rows directly, then save TTS settings and preview again.
- Manual override fallback: accepted suggestions are only a convenience path into the same override rows; manual rows remain the supported fallback for every failure mode.

## Manual QA Checklist

- Disabled mode: preview unknown/ambiguous terms, click `Suggest pronunciations`, confirm the disabled message appears and no provider is required.
- Enabled local Ollama mode: enable the config above, preview an unknown term, request suggestions, and confirm suggestion cards appear.
- Accept suggestion as override: edit or accept a suggestion and verify it creates or updates a draft override row.
- Save settings: save TTS settings and confirm the project settings persist.
- Preview again: confirm the saved override affects spoken preview text while captions/transcript original text remains unchanged.
- Rerender: rerender uses the saved deterministic override and does not call the suggestion provider.

## Prompting Strategy

The provider prompt should be strict, short, and JSON-only. It should ask for spoken pronunciation, not translation or sentence rewriting.

Draft prompt shape:

```text
You suggest spoken pronunciations for Turkish text-to-speech review.

Return JSON only with this shape:
{
  "suggestions": [
    {
      "term": "original term",
      "suggested_spoken": "how a Turkish TTS should speak the term",
      "category": "abbreviation | technical | mixed_word",
      "confidence": "low | medium | high",
      "reason": "short reason"
    }
  ]
}

Rules:
- Do not translate the meaning.
- Do not rewrite the full sentence.
- Do not add terms not present in the input.
- Suggest only a spoken spelling for each provided term.
- Use Turkish-friendly phonetic spelling when language is "tr".
- category must be one of: abbreviation, technical, mixed_word.
- confidence must be one of: low, medium, high.
- Keep suggested_spoken short.
- If unsure, use low confidence and explain briefly.

Language: {language}
Context: {context}
Terms: {terms}
```

Implementation should parse and validate the returned JSON. It should ignore prose outside JSON rather than trying to salvage unsafe output in a permissive way.

## Safety / Reliability

Required safeguards:

- Validate provider JSON against a strict schema.
- Sanitize `suggested_spoken` before returning it to Studio.
- Cap term length, suggestion length, reason length, terms per request, and context length.
- Never auto-apply suggestions.
- Never store suggestions without teacher action.
- Never send secrets to the provider.
- Log provider errors without leaking API keys, auth headers, cookies, prompts containing private transcript beyond the bounded context, or raw stack traces.
- Make provider calls timeout-bound and cancellable at the request layer.
- Keep a hidden production dependency out of the render path.
- Return readable fail-open errors when disabled, timed out, malformed, or unavailable.

Suggested validation constraints:

- `term`: must exactly match one requested term after trimming.
- `category`: one of `abbreviation`, `technical`, `mixed_word`.
- `confidence`: one of `low`, `medium`, `high`.
- `suggested_spoken`: non-empty, short, plain text, no URLs, no JSON, no code blocks.
- `reason`: optional short plain text.

## Frontend UX Plan

In `services/frontend/src/components/studio/TtsSettingsPanel.jsx`:

- Implemented: `Suggest pronunciations` appears near the unknown/ambiguous term section.
- Implemented: detected terms are selected by default and can be toggled individually.
- Implemented: loading, disabled, warning, and fail-open states are shown as helper UI.
- Implemented: one suggestion card per term shows spoken form, category, confidence, and reason.
- Implemented: teachers can edit the spoken form and category before accepting.
- Implemented: `Accept as override` adds or updates a draft override row only.
- Implemented: `Ignore` dismisses the suggestion locally.
- Implemented: deterministic preview and manual D1B `Add override` still work without LLM suggestions.
- Implemented: suggestions do not mutate transcript text, captions, saved project settings, or rerender jobs.
- Implemented: provider/fallback metadata stays under Technical details.

The ideal interaction is small and teacher-controlled: D1 finds the term, L1 proposes a spoken form, the teacher makes the final call.

## Tests Needed

Backend tests:

- Suggestions disabled returns a clear disabled message.
- Disabled mode makes no network call.
- Malformed LLM response fails safely.
- Timeout fails safely.
- Provider unavailable fails safely.
- Request term cap is enforced.
- Context length cap is enforced.
- Invalid categories/confidences are rejected or dropped.
- Endpoint is authenticated.
- Endpoint does not mutate transcript pages.
- Endpoint does not mutate project settings.
- Endpoint does not mutate render jobs.
- Render/synthesize path never calls LLM.

Frontend tests or component-level coverage:

- Accepted suggestions become override rows only on the frontend draft state.
- Edited suggestions use the edited spoken value.
- Ignored suggestions do not create override rows.
- Disabled response shows a readable disabled message.
- Existing deterministic preview still works without suggestions.

Integration guard:

- A rerender with accepted suggestions should use saved project overrides and should not call the suggestion provider.

## Implementation Phases

### L1A: Backend Suggestion Endpoint

Status: implemented.

- Add backend-only suggestion endpoint and provider abstraction.
- Add disabled-by-default config.
- Add an Ollama provider implementation behind `TTS_LLM_SUGGESTIONS_ENABLED=true`.
- Enforce request caps and strict response validation.
- Add tests for disabled mode, malformed response, timeout, auth, caps, no mutation, and no render-path calls.

### L1B: Studio Suggestion UI

Status: implemented.

- Add suggestion controls to `TtsSettingsPanel`.
- Send selected D1 terms and bounded context.
- Render suggestion cards.
- Accept/edit/ignore suggestions into draft override rows.
- Keep save/rerender behavior unchanged.

### L1C: Provider Docs And Local Ollama Setup

Status: implemented.

- Document optional local Ollama setup.
- Document the config variables.
- State clearly that production remains disabled unless explicitly enabled.
- Add troubleshooting for disabled mode, provider timeout, malformed provider response, no suggestions returned, and manual override fallback.
- Add manual QA for disabled mode, enabled local Ollama mode, accepting suggestions as overrides, saving settings, previewing again, and rerendering with saved deterministic overrides.

### L1D: Optional OpenAI-Compatible Provider Later

- Add only after local Ollama flow is stable.
- Keep provider opt-in and separate from render.
- Add explicit key handling and redacted logging.

## What Not To Do

- No LLM in render path.
- No automatic rewriting.
- No per-token LLM calls.
- No replacing D1 deterministic resolver.
- No changing deterministic resolver behavior.
- No changing XTTS recovery.
- No changing captions or subtitles.
- No transcript mutation from suggestions.
- No settings mutation from the suggestion endpoint.
- No storing suggestions without teacher action.
- No worker queue, storage, transcript/page control, Google OAuth, or subtitle changes.
- No hidden production dependency on Ollama, Llama, or any external model provider.

## Final Implementation Prompt

```text
You are working in my AI_ACADEMY repo.

Implement Phase TTS-L1A backend only from docs/TTS_L1_LLM_PRONUNCIATION_SUGGESTIONS_PLAN.md.

Do not implement Studio UI yet. Do not touch render/rerender paths, worker queues, storage, transcript/page controls, subtitles, Google OAuth, XTTS recovery, or deterministic resolver behavior. Do not make TTS generation depend on LLM.

Goal:
- Add an authenticated Django endpoint such as POST /api/v1/tts/pronunciation-suggestions/.
- The endpoint accepts language, selected terms, optional bounded context, and optional project_id.
- It is disabled by default with TTS_LLM_SUGGESTIONS_ENABLED=false and must make no provider/network call while disabled.
- Add a small provider abstraction with an Ollama implementation behind TTS_LLM_PROVIDER=ollama.
- Enforce max terms, max term length, max context length, provider timeout, strict JSON validation, and sanitized response fields.
- Return suggestions with term, suggested_spoken, category, confidence, and reason.
- Fail open with readable errors for disabled, timeout, malformed provider output, and provider unavailable.
- Do not mutate transcript, settings, render jobs, or synthesis inputs.
- Add tests proving disabled mode, no network call when disabled, malformed response safety, timeout safety, request caps, auth, no mutation, and render/synthesize path never calls the LLM provider.

Return a concise summary, files changed, and tests run.
```
