# TTS Coworker Integration Plan

Source inspected: `https://github.com/enjinAI41/ai-video-generator/tree/feature/voice-cloning` at commit `e6a0735`.

## Phase TTS-H1 - XTTS Runtime Resilience

Status: completed on 2026-05-01 in `feat/tts-runtime-resilience-h1`.

- `services/tts_service/main.py` now classifies likely transient XTTS model-load/network/runtime errors, clears cached XTTS model/load-error state, and retries XTTS using `XTTS_LOAD_RECOVERY_ATTEMPTS` and `XTTS_LOAD_RECOVERY_BACKOFF_SEC`.
- `services/scripts/tts_client.py` preserves concise XTTS recovery metadata from the TTS service, including `fallback_reason`, `xtts_error_transient`, `xtts_attempts`, `xtts_recovery_attempts`, and `xtts_failure_reason`.
- `infra/.env.example` and TTS docs document the recovery knobs and leave `HF_HUB_DISABLE_SSL_VERIFY` disabled by default.
- Tests cover transient retry success, exhausted transient fallback, non-transient no-retry fallback, missing reference voice fallback, disabled-XTTS dev fallback, metadata propagation, and preview remaining text-only.
- This is runtime hardening only. Dictionary-first Turkish/English acronym/pronunciation resolution was completed separately in TTS-D1A/D1B, and optional Llama/Ollama pronunciation suggestions have L1A backend, L1B Studio UI, and L1C setup docs implemented.
- The provider chain remains XTTS -> gTTS -> silent fallback.

## Phase TTS-D1A - Deterministic Resolver Backend

Status: completed on `feat/tts-d1-deterministic-resolver`.

- `services/tts_service/tts_preprocess/deterministic_resolver.py` adds cached in-memory acronym, Turkish-known-word, and curated English technical-term lookup.
- Acronym ownership moved from `glossary.json` into `acronym_pronunciations.json`; manual/project overrides still win first, the global glossary still handles product/phrase terms, and deterministic resolver applies acronym rules after glossary processing.
- Preview and synth metadata now carry `unknown_terms` and `ambiguous_terms` alongside resolver rules in `tts_normalization_rules_applied`.
- There are no render-path LLM, network, DB, subprocess, or per-token service calls.
- Studio unknown-term display is implemented in D1B. Llama/Ollama suggestions remain optional L1; L1A backend, L1B Studio UI, and L1C provider setup docs are implemented in `docs/TTS_L1_LLM_PRONUNCIATION_SUGGESTIONS_PLAN.md`.

## Phase TTS-D1B - Studio Resolver Term Display

Status: completed on `feat/tts-d1-deterministic-resolver`.

- `services/frontend/src/components/studio/TtsSettingsPanel.jsx` displays deterministic resolver `unknown_terms`, `ambiguous_terms`, and applied resolver rules in the preview result.
- Teachers can add a detected term to draft override rows from Studio. Acronym-like terms default to `abbreviation`; other terms default to `mixed_word`.
- Added rows stay draft-only until settings are saved. Preview does not mutate transcript pages, captions, or rerender jobs.

## Phase TTS-L1 - Optional LLM Pronunciation Suggestions

Status: L1A backend endpoint, L1B Studio UI, and L1C optional Ollama setup docs implemented. See `docs/TTS_L1_LLM_PRONUNCIATION_SUGGESTIONS_PLAN.md`.

- L1 is Studio-assisted suggestion support for D1 `unknown_terms` and `ambiguous_terms`, not render-path intelligence.
- Llama/Ollama must be disabled by default and optional.
- Teachers must accept, edit, or ignore suggestions.
- Accepted suggestions become normal manual/project override rows.
- Rerender must use deterministic saved overrides and must not call an LLM provider.
- `POST /api/v1/tts/pronunciation-suggestions/` is authenticated, fail-open, capped, and non-mutating.
- The endpoint lives in the Django API/Studio flow only; the TTS service `/synthesize`, worker render paths, and `tts_client` do not import or call the L1 helper.
- Local Ollama use is opt-in with `TTS_LLM_SUGGESTIONS_ENABLED=true`; Docker Desktop on Windows should use `OLLAMA_BASE_URL=http://host.docker.internal:11434`.
- Disabled mode remains the default and manual override rows remain the fallback when the provider is disabled, unavailable, timed out, malformed, or returns no suggestions.

## Implementation Status

| Phase | Status | Branch |
|-------|--------|--------|
| **Phase 1** — Backend/TTS Preview Endpoint | ✅ **Completed** (2026-04-27, `feat/tts-preview-phase-1`) | feat/tts-preview-phase-1 |
| Phase 2 — Project-Level TTS Settings Persistence | ✅ **Completed** (2026-04-29) | — |
| Phase 3 — Worker/Rerender Integration | ✅ **Completed** (2026-04-29) | — |
| Phase 4 — Studio UI Controls | ✅ **Completed** (2026-04-29) | — |
| Phase 5 — Tests/Docs | ✅ Partially done (Phase 1/2/3 backend tests and Phase 4 frontend build/docs) | — |

### Phase 1 — What was implemented
- `services/tts_service/main.py`: Added `NormalizationPreviewRequest`, `NormalizationPreviewResponse` models and `POST /normalization/preview` route. Uses `prepare_text_for_tts` plus runtime override merging where available. Preview is fail-open and never synthesizes audio.
- `services/scripts/tts_client.py`: Added `preview_tts_text_with_metadata(...)` with 3-tier fail-open (service → local `prepare_text_for_tts` → bare original text).
- `services/api/core/views.py`: Added `TTSPreviewView` (Django REST `APIView`). Auth required, no Celery, no audio synthesis during preview.
- `services/api/core/urls.py`: Registered `POST /api/v1/tts/preview/`.
- `tests/integration/test_tts_preview.py`: 14 focused tests (all 44 integration tests pass).
- `tests/integration/test_tts_import_isolation.py`: Extended with preview import-isolation test.
- `tests/integration/test_tts_client_readiness.py`: Extended with preview fail-open tests.

### Phase 1 — Implementation Details
- **Override Protection**: Manual request overrides (`technical_overrides`, `abbreviation_overrides`, `mixed_word_overrides`) are protected from re-normalization using placeholder token masking. Example: `ChatGPT` → `"chat gpt"` stays as `"chat gpt"` in the final `spoken_text`, never re-normalized to `"chat Ci Pi Ti"` by the glossary.
- **Override Priority**: `mixed_word_overrides` → `abbreviation_overrides` → `technical_overrides` → default glossary/normalization.
- **Fail-Open Behavior**: Preview endpoint gracefully falls back to local `prepare_text_for_tts()` and then bare original text on network/service errors. No audio synthesis occurs during preview.
- **Request-Local Only**: All overrides are request-local and do not modify the persistent `glossary.json`.
- **No Synthesis**: The `/normalization/preview` endpoint never calls audio synthesis; it returns only text metadata.
- **No Glossary CRUD**: There is no UI or API for persistent teacher-managed glossary overrides; glossary editing remains configuration-only.

### Phase 1 — Limitations
- The preview endpoint remains preview-only and never synthesizes audio. Persisted project settings now affect generation through Phase 3 worker/TTS wiring, not through the preview endpoint itself.
- Spelling-aware per-word Turkish dictionary-first + English fallback has since been implemented in TTS-D1A/D1B.
- `unknown_word_strategy: "phonetic"` is accepted but only as metadata pass-through; no actual phonetic engine is wired.
- Project-level TTS settings persistence is implemented in Phase 2, generation wiring is implemented in Phase 3, and Studio UI controls are implemented in Phase 4.
- No Django model changes or migrations in this phase.

### Phase 2 — What was implemented
- `services/api/core/models.py`: Added `Project.tts_settings` as a JSONField with callable defaults.
- `services/api/core/migrations/0012_project_tts_settings.py`: Adds the project-level settings field.
- `services/api/core/serializers.py`: Exposes canonical `tts_settings` on `ProjectSerializer` and validates persisted PATCH payloads.
- `services/api/core/views.py`: Allows `PATCH /api/v1/projects/<project_id>/` to update `tts_settings`; rejects inline multipart upload settings so saved settings remain a project-level PATCH concern.
- `tests/integration/test_project_tts_settings.py`: Covers defaults, serializer output, merge semantics, validation, permissions, and upload compatibility.

### Phase 2 — Limitations
- Inline multipart upload `tts_settings` are still rejected; settings must be saved with project PATCH and are then used by subsequent generation/rerender paths.
- Studio controls are implemented in Phase 4.
- Turkish dictionary-first plus English fallback has since been implemented in TTS-D1A/D1B.
- A Llama/Ollama pronunciation suggestion layer has backend-only L1A support and is not part of Phase 2 render/settings persistence.

### Phase 3 — What was implemented
- `services/api/core/views.py`: Resolves canonical project `tts_settings` and passes them to Celery from upload, full rerender, and transcript-triggered rerender paths.
- `services/worker/tasks.py`: Extends `process_pptx_to_video` and `synthesize_and_render_slide` with backward-compatible optional `tts_settings`; preserves targeted `rerender_page_keys`.
- `services/scripts/tts_client.py`: Maps canonical project settings into TTS service payload fields and applies request-local overrides to spoken text while keeping original text metadata for captions/SRT.
- `services/tts_service/main.py`: Accepts optional synthesize settings/override fields and attaches non-sensitive debug metadata while preserving XTTS → gTTS → silent fallback order.
- Tests cover Celery argument wiring, old task signature compatibility, client payload mapping, provider preference as preference-only, captions using original text, and request-local override behavior.

### Phase 3 — Limitations
- `provider_preference` is advisory only. It is persisted and passed through metadata, but it does not hard-disable XTTS, gTTS, or silent fallback.
- `speech_speed`, `volume_gain_db`, and `pause_seconds` are persisted and surfaced in metadata, but audio DSP/runtime behavior is not expanded beyond existing safe support.
- Turkish dictionary-first plus English fallback is implemented in TTS-D1A/D1B. Llama/Ollama pronunciation suggestions have backend-only L1A support; Studio UI remains future L1B work.

### Phase 4 — What was implemented
- `services/frontend/src/api.js`: Added project TTS settings PATCH helper.
- `services/frontend/src/components/studio/TtsSettingsPanel.jsx`: Added Studio controls for saved project TTS settings, manual override editing, backend pronunciation preview, and rerender-with-saved-settings.
- `services/frontend/src/pages/Studio.jsx`: Added the `TTS` tab and passes selected project, transcript pages, project update callback, and rerender handler into the panel.
- Preview uses the backend `/api/v1/tts/preview/` endpoint and is display-only; it does not mutate transcript text or captions.

### Phase 4 — Remaining future work
- Dictionary-first Turkish/English resolver and Studio unknown-term display are implemented in TTS-D1A/D1B.
- Llama/Ollama pronunciation suggestions have L1A backend support; L1B Studio UI remains future work and is planned in `docs/TTS_L1_LLM_PRONUNCIATION_SUGGESTIONS_PLAN.md`.
- Full transcript editing remains separate from the TTS settings panel.



Scope: TTS plus Studio/backend integration only. Do not replace this repo's stronger architecture: separate FastAPI TTS service, Django API, Celery worker, XTTS/gTTS/silent fallback, `services/tts_service/tts_preprocess`, Turkish normalization, transcript/rerender backend, and React Studio UI.

## Summary Finding

The coworker branch is a FastAPI monolith with Gradio/React surfaces. Its useful TTS work is not the synthesis architecture. The useful pieces are:

- a backend pronunciation/normalization preview endpoint,
- Studio controls for previewing normalized narration and managing manual overrides,
- request plumbing for per-render TTS settings such as provider preference, speech speed, volume gain, pause between slides, and runtime overrides,
- tests proving preview/override behavior is deterministic and fail-open.

This repo should port those ideas by adapting them to the existing Django API -> Celery worker -> TTS service flow. Do not copy the coworker pipeline or replace `tts_preprocess`.

## 1. Coworker Features Worth Porting

1. TTS pronunciation preview endpoint
   - Coworker files:
     - `app/routes/tts.py`
     - `app.services.tts_service.preview_tts_normalization`
     - `app.services.tts_service.build_tts_normalization_config`
   - Value:
     - Lets Studio preview "what TTS will speak" before rendering.
     - Returns original text, normalized text, used text, enabled/mode/strategy, applied override maps, and fail-open error metadata.
   - Adaptation:
     - Add a preview endpoint without synthesis first.
     - Use this repo's `prepare_text_for_tts` from `services/tts_service/tts_preprocess`, not coworker's normalizer.
     - Prefer adding preview support to the TTS service and exposing it through Django, so Studio can stay on the Django API.

2. Manual pronunciation override UI concept
   - Coworker files/functions:
     - `frontend/src/pages/Studio.jsx`
     - `createEmptyTtsOverrideState`
     - `handlePreviewPronunciation`
     - `handleApplyOverrides`
     - `handleUpsertOverride`
     - `handleRemoveOverride`
     - `ttsOverrideRows`
   - Value:
     - Gives teachers a lightweight way to override terms like `ChatGPT`, `GPU`, or course-specific names.
   - Adaptation:
     - Reuse the UI flow, but persist overrides through this repo's backend instead of keeping them only in component state.
     - Map override semantics into this repo's glossary/preprocess model rather than copying coworker's `TTSNormalizationConfig`.

3. Project/render TTS settings plumbing
   - Coworker files/functions:
     - `frontend/src/api.js::renderProjectWithStudioSettings`
     - `app/routes/pipeline.py::PipelineRunRequest`
     - `app/services/job_service.py::JobService.normalize_payload`
     - `app/services/pipeline_service.py::run_pipeline`
     - `app/services/pipeline_service.py::_generate_tts_audio_with_run_context`
   - Useful settings:
     - `preferred_tts_provider`
     - `speech_speed`
     - `volume_gain_db`
     - `pause_between_slides`
     - `tts_normalization_enabled`
     - `tts_unknown_word_strategy`
     - override maps
   - Adaptation:
     - Persist stable settings on this repo's `Project`, then pass them through Django task arguments to Celery.
     - Do not introduce coworker's synchronous `run_pipeline` path.

4. Reference voice upload from Studio render flow
   - Coworker files/functions:
     - `frontend/src/api.js::uploadVoiceSample`
     - `frontend/src/api.js::renderProjectWithStudioSettings`
     - `app/main.py` `/upload/voice`
   - Value:
     - Lets a project render use a selected voice sample.
   - Adaptation:
     - This repo already has `VoiceUploadView` storing `{STORAGE_ROOT}/voices/<voice_id>.wav`; keep it.
     - Add project-level selection/persistence only if needed. Do not replace teacher `VoiceProfile`.

5. Fail-open preview/tests
   - Coworker files/tests:
     - `tests/services/test_tts_normalization_preview_helper.py`
     - `tests/services/test_tts_service_text_normalization_integration.py`
     - `tests/services/test_tts_text_normalization.py`
     - `app/tests/unit/test_gradio_tts_pronunciation_editor.py`
   - Value:
     - Good test cases for deterministic preview, override precedence, disabled normalization, and fallback-to-original on preview failure.
   - Adaptation:
     - Port test intent, not exact expected strings, because this repo's Turkish glossary output is different and stronger.

## 2. Existing Features To Keep And Not Replace

Keep these as the source of truth:

- `services/tts_service/main.py`
  - FastAPI `/synthesize`.
  - XTTS v2 first when `voice_id` exists.
  - gTTS fallback.
  - silent fallback.
  - XTTS warmup/readiness and CPU fallback behavior.

- `services/scripts/tts_client.py`
  - Worker-side client for TTS service.
  - `synthesize_with_service_with_metadata`.
  - `synthesize_text_with_metadata`.
  - Existing readiness timeout and local silent fallback.

- `services/tts_service/tts_preprocess/`
  - Current active package.
  - Turkish normalization.
  - glossary application.
  - segmentation/chunk pauses.
  - `TTSPreparedText` metadata.

- `services/worker/tasks.py`
  - Celery orchestration.
  - `process_pptx_to_video`.
  - `synthesize_and_render_slide`.
  - transcript syncing, targeted rerender, final playback asset generation.

- `services/api/core/views.py`
  - `ProjectUploadView`.
  - `ProjectTranscriptView`.
  - `ProjectRerenderView`.
  - `VoiceUploadView`.

- `services/api/core/models.py`
  - `VoiceProfile`.
  - `Project`.
  - `TranscriptPage`.
  - `LessonSegment`.

- `services/frontend/src/pages/Studio.jsx`
  - Existing Studio upload, project list, transcript display, pause controls, rerender button.

Do not copy coworker's:

- monolithic FastAPI pipeline,
- mock TTS provider as a replacement for gTTS/silent fallback,
- `app/services/tts_text_normalization.py` as the primary normalizer,
- Gradio runtime as product UI,
- JSON-file project repository.

## 3. Exact Files In This Repo That Will Need Changes

Phase 1 backend/TTS preview endpoint:

- `services/tts_service/main.py`
  - Add preview request/response models and a preview route, for example `POST /preview` or `POST /normalization/preview`.
  - Use `prepare_text_for_tts` and return `original_text`, `normalized_text`, `spoken_text`, `chunks`, `chunk_pause_ms`, `tts_normalization_language`, `tts_normalization_rules_applied`, and `warnings`.

- `services/scripts/tts_client.py`
  - Add a preview client helper that calls the TTS service preview endpoint with timeout/retry behavior.
  - Keep import isolation with `tts_preprocess`.

- `services/api/core/views.py`
  - Add a Django API view, for example `TTSPreviewView`, that accepts Studio text/settings and proxies to the TTS service preview helper.
  - Return fail-open JSON instead of blocking rendering.

- `services/api/core/urls.py`
  - Register the preview route, for example `path("tts/preview/", TTSPreviewView.as_view(), name="tts-preview")`.

- `tests/integration/test_tts_text_normalization.py`
  - Add or extend tests for preview metadata matching existing `prepare_text_for_tts` behavior.

- `tests/integration/test_tts_client_readiness.py`
  - Add preview client timeout/fallback behavior if the helper uses HTTP.

Phase 2 project-level TTS settings persistence:

- `services/api/core/models.py`
  - Add project-level TTS settings. Recommended: explicit fields for stable settings and JSON for overrides:
    - `tts_provider_preference`
    - `tts_normalization_enabled`
    - `tts_unknown_word_strategy`
    - `tts_overrides`
    - `speech_speed`
    - `volume_gain_db`
    - `pause_seconds`
  - Keep `VoiceProfile` for teacher-level voice identity.

- `services/api/core/migrations/`
  - Add a migration for the new fields.

- `services/api/core/serializers.py`
  - Expose TTS settings on `ProjectSerializer`.

- `services/api/core/views.py`
  - Allow `ProjectDetailView.patch` to update TTS settings.
  - Ensure `ProjectUploadView` can accept initial settings.

- `tests/integration/`
  - Add project settings persistence tests.

Phase 3 worker/rerender integration:

- `services/api/core/views.py`
  - Pass resolved project TTS settings into `process_pptx_to_video` for upload, transcript rerender, and full rerender paths.

- `services/worker/tasks.py`
  - Extend `process_pptx_to_video` task signature with a backward-compatible `tts_settings: dict | None = None`.
  - Pass settings into `synthesize_and_render_slide`.
  - Extend `synthesize_and_render_slide` signature and call `synthesize_text_with_metadata` with settings.

- `services/scripts/tts_client.py`
  - Extend `synthesize_with_service_with_metadata` and `synthesize_text_with_metadata` to send preview/persistence settings only where supported.

- `services/tts_service/main.py`
  - Extend `SynthesizeRequest` only if runtime overrides must be applied inside the TTS service. Keep `already_prepared=True` behavior intact.

- `tests/integration/test_transcript_editor_pipeline.py`
  - Assert transcript rerender passes project TTS settings to Celery.

- `tests/integration/test_tts_text_normalization.py`
  - Assert original text still drives SRT/captions while spoken text goes to TTS.

Phase 4 Studio UI controls:

- `services/frontend/src/api.js`
  - Add `previewTtsNormalization`.
  - Add `updateProjectTtsSettings`.

- `services/frontend/src/pages/Studio.jsx`
  - Add controls adapted from coworker branch:
    - provider preference,
    - speech speed,
    - volume gain,
    - pause between slides,
    - preview pronunciation,
    - override add/update/remove,
    - rerender with saved settings.
  - Keep preview display-only; it must not mutate transcript text or captions.
  - Avoid dead UI: each control must read/write backend state or be hidden.

- `services/frontend/src/components/studio/`
  - Extract TTS settings into a focused component instead of growing `Studio.jsx`.

Phase 5 tests/docs:

- `tests/integration/test_tts_import_isolation.py`
  - Keep and extend import collision coverage.

- `tests/integration/test_tts_service_text_quality.py`
  - Add preview route coverage on the TTS service.

- `tests/integration/test_tts_client_readiness.py`
  - Add preview fallback behavior.

- `tests/integration/test_transcript_editor_pipeline.py`
  - Cover rerender settings propagation.

- `docs/UNFINISHED_WORK.md`
  - Update the TTS/gap rows after implementation.

- `README.md` and `services/tts_service/README.md`
  - Document the preview endpoint and project TTS settings.

## 4. Exact Coworker Files/Functions To Copy Or Adapt

Copy/adapt these concepts:

- `app/routes/tts.py`
  - `TtsNormalizationPreviewRequest`
  - `TtsNormalizationPreviewResponse`
  - `tts_normalization_preview`
  - Adapt into this repo's TTS service and/or Django API route.

- `app/services/tts_service.py`
  - `preview_tts_normalization`
  - `_normalize_override_map`
  - `build_tts_normalization_config`
  - Adapt the fail-open preview shape, but replace the internals with `prepare_text_for_tts`.

- `frontend/src/api.js`
  - `previewTtsNormalization`
  - TTS-relevant pieces of `renderProjectWithStudioSettings`
  - Adapt request payload naming to this repo's `/api/v1/...` endpoints.

- `frontend/src/pages/Studio.jsx`
  - `TTS_PROVIDER_OPTIONS`
  - `TTS_NORMALIZATION_MODE_OPTIONS`
  - `TTS_UNKNOWN_WORD_OPTIONS`
  - `TTS_OVERRIDE_CATEGORY_OPTIONS`
  - `createEmptyTtsOverrideState`
  - `ttsOverrideRows`
  - `handlePreviewPronunciation`
  - `handleApplyOverrides`
  - `handleUpsertOverride`
  - `handleRemoveOverride`
  - Adapt UI layout to this repo's existing Studio design.

- `app/models/project.py`
  - `ProjectRecord.reference_voice_path`
  - `ProjectRecord.speech_speed`
  - `ProjectRecord.volume_gain_db`
  - `ProjectRecord.pause_between_slides`
  - Adapt as Django model fields or a structured `tts_settings` JSON field.

- `app/routes/projects.py`
  - `ProjectCreateRequest` and `ProjectUpdateRequest` TTS settings fields.
  - Adapt into `ProjectUploadView` and `ProjectDetailView.patch`.

- `app/services/job_service.py`
  - `JobService.normalize_payload`
  - Use as a reference for resolving project defaults when the render request omits a setting.

- `app/services/pipeline_service.py`
  - `run_pipeline` parameters:
    - `preferred_tts_provider`
    - `tts_normalization_enabled`
    - `tts_normalization_mode`
    - `tts_unknown_word_strategy`
    - override maps
    - `speech_speed`
    - `volume_gain_db`
  - `debug_metadata` fields for TTS settings.
  - Adapt only the settings propagation and debug metadata idea.

- Tests:
  - `tests/services/test_tts_normalization_preview_helper.py`
  - `tests/services/test_tts_service_text_normalization_integration.py`
  - `tests/services/test_tts_text_normalization.py`
  - `app/tests/unit/test_gradio_tts_pronunciation_editor.py`
  - Port scenarios, not exact output strings.

Do not copy:

- `app/services/tts_text_normalization.py` as a replacement for `tts_preprocess`.
- `app/services/providers/xtts_provider.py` as a replacement for current XTTS handling.
- `app/services/pipeline_service.py` render pipeline.
- `app/repositories/project_repository.py` JSON persistence.
- Gradio-only code except as UX reference.

## 5. Phase-by-Phase Implementation Plan

### Phase 1: Backend/TTS Preview Endpoint

Goal: let backend return the exact text/metadata the TTS stack would speak, without rendering audio.

Steps:

1. Add a TTS service preview route in `services/tts_service/main.py`.
   - Input: `text`, optional `language`, optional override/settings fields.
   - Output: `original_text`, `normalized_text`, `spoken_text`, `chunks`, `chunk_pause_ms`, `tts_normalization_language`, `tts_normalization_rules_applied`, `warnings`, `error`, `fallback_used`.

2. Add `preview_tts_text_with_metadata` in `services/scripts/tts_client.py`.
   - Call TTS service preview endpoint.
   - If the service is unavailable, return local `prepare_text_for_tts` fallback or original-text fail-open metadata.

3. Add `TTSPreviewView` in `services/api/core/views.py`.
   - Route through Django so Studio can call `/api/v1/tts/preview/`.
   - Do not require audio synthesis or Celery.

4. Register URL in `services/api/core/urls.py`.

5. Tests:
   - TTS service preview uses `tts_preprocess`.
   - Django endpoint returns stable metadata.
   - Unavailable TTS service does not crash the preview endpoint.

Acceptance:

- No changes to render behavior.
- Existing XTTS/gTTS/silent fallback tests still pass.
- Existing import isolation test still passes.

### Phase 2: Project-Level TTS Settings Persistence

Goal: store project defaults once so Phase 3 can reuse them on upload/rerender/transcript rerender.

Implemented:

1. Added structured `Project.tts_settings` JSON persistence.
2. Expose settings in `ProjectSerializer`.
3. Allow `ProjectDetailView.patch` to update settings.
4. Reject inline multipart `tts_settings` in `ProjectUploadView.post`; settings are updated after upload via project PATCH.
5. Add tests for create/defaults, patch, serialize, merge behavior, invalid values, permissions, and upload compatibility.

Persisted shape:

```json
{
  "provider_preference": "auto",
  "normalization_enabled": true,
  "normalization_mode": "loose",
  "unknown_word_strategy": "keep",
  "overrides": {
    "technical": {},
    "abbreviation": {},
    "mixed_word": {}
  },
  "speech_speed": 1.0,
  "volume_gain_db": 0,
  "pause_seconds": null
}
```

Keep provider preference as a preference, not a hard requirement. XTTS/gTTS/silent fallback must still decide final provider safely.
Generation/rerender wiring is implemented in Phase 3. Studio UI controls are implemented in Phase 4. Turkish dictionary-first plus English fallback and Studio resolver-term display are implemented in TTS-D1A/D1B. Llama/Ollama pronunciation suggestions have L1A backend support, L1B Studio UI, and L1C optional provider docs; they remain disabled by default and outside render/rerender.

### Phase 3: Worker/Rerender Integration

Goal: make all render paths use the same persisted project TTS settings.

Implemented:

1. Add a helper in `services/api/core/views.py` to resolve project TTS settings.
2. Pass settings into Celery from:
   - `ProjectUploadView.post`,
   - `ProjectTranscriptView.patch` rerender path,
   - `ProjectRerenderView.post`.
3. Extend `process_pptx_to_video` with optional `tts_settings`.
4. Extend `synthesize_and_render_slide` with optional `tts_settings`.
5. Extend `services/scripts/tts_client.py` to send supported runtime override settings.
6. Preserve current behavior when `tts_settings` is missing.

Acceptance:

- Old Celery task calls still work.
- Transcript rerender keeps targeted `rerender_page_keys`.
- SRT/caption text remains original narration, while spoken text may be normalized.

### Phase 4: Studio UI Controls

Goal: expose useful TTS settings without dead controls.

Implemented:

1. Add API helpers in `services/frontend/src/api.js`:
   - `previewTtsNormalization`,
   - `updateProjectTtsSettings`.
2. Add a TTS settings panel component and mount it in the Studio `TTS` tab.
3. Bind every control to backend state.
4. Add preview action that calls backend and displays `spoken_text`/rules/warnings.
5. Add override add/update/remove UI.
6. Add explicit "save settings" and "rerender with saved settings" flows.

Avoid:

- storing settings only in localStorage,
- applying preview text to transcript implicitly,
- provider controls that cannot affect backend behavior,
- upload fields that bypass `VoiceProfile` or project settings.

### Phase 5: Tests/Docs

Goal: lock behavior before moving to UI polish.

Tests to add/update:

- `tests/integration/test_tts_import_isolation.py`
  - Ensure preview imports `tts_preprocess`, never old `preprocess`.

- `tests/integration/test_tts_text_normalization.py`
  - Preview returns spoken text and normalization metadata.

- `tests/integration/test_tts_service_text_quality.py`
  - TTS service preview route does not synthesize audio and uses the active normalizer.

- `tests/integration/test_tts_client_readiness.py`
  - Preview helper fails open.

- `tests/integration/test_transcript_editor_pipeline.py`
  - Transcript rerender passes saved TTS settings.

Docs to update after implementation:

- `README.md`
- `services/tts_service/README.md`
- `docs/UNFINISHED_WORK.md`

## 6. Risks

Import collision with old preprocess package:

- Risk:
  - Both `services/tts_service/preprocess/` and `services/tts_service/tts_preprocess/` exist.
  - New preview code could accidentally import the old `preprocess` package.
- Mitigation:
  - Import only `tts_preprocess`.
  - Extend `tests/integration/test_tts_import_isolation.py`.
  - Do not add generic `preprocess` imports.

Not breaking XTTS/gTTS/silent fallback:

- Risk:
  - Provider preference or preview settings could become a hard provider requirement.
- Mitigation:
  - Keep `services/tts_service/main.py::synthesize` fallback order intact.
  - Treat provider as a preference only.
  - Ensure no preview code changes `/synthesize` behavior in Phase 1.

Not downgrading architecture:

- Risk:
  - Coworker code is monolithic and could pull render/TTS into Django or frontend.
- Mitigation:
  - Keep TTS synthesis in the TTS service.
  - Keep orchestration in Celery worker.
  - Keep Django as API/persistence/orchestration boundary.
  - Copy only contracts, tests, and UI concepts.

Migration risks:

- Risk:
  - Adding fields to `Project` can break existing rows or serializers.
- Mitigation:
  - Use backward-compatible defaults.
  - Prefer nullable/JSON default-safe migration.
  - Keep old task signatures backward compatible with optional trailing `tts_settings`.

Frontend dead UI risk:

- Risk:
  - Coworker Studio controls look useful but some are local-only.
- Mitigation:
  - Add backend first.
  - Do not show controls until API read/write exists.
  - Tests or manual QA should verify each control changes the request payload or saved project settings.

Normalization conflict risk:

- Risk:
  - Coworker override categories do not map 1:1 to this repo's language-aware glossary.
- Mitigation:
  - Use coworker categories only as UI labels if helpful.
  - Store overrides in a schema compatible with `tts_preprocess`.
  - Keep Turkish normalization as primary.

Caption/spoken text mismatch risk:

- Risk:
  - Applying pronunciation text directly to transcript could make captions ugly or wrong.
- Mitigation:
  - Keep original narration for transcript/SRT.
  - Use spoken/normalized text only for TTS.
  - Keep preview display-only unless a future transcript editor adds a separate explicit apply flow.

## 7. Recommended Next Codex Prompt For Phase 1 Only

```text
You are working in my AI_ACADEMY repo.

Implement Phase 1 only from docs/TTS_COWORKER_INTEGRATION_PLAN.md.

Do not implement project-level persistence, worker/rerender settings, or Studio UI yet.

Goal:
- Add a backend TTS preview path that returns what the current TTS preprocessing stack would speak, without rendering audio.
- Keep the existing architecture: Django API talks to the separate FastAPI TTS service; Celery render behavior is unchanged.
- Use services/tts_service/tts_preprocess only. Do not import or revive services/tts_service/preprocess.
- Do not change XTTS/gTTS/silent fallback behavior for /synthesize.

Current optional LLM-assisted pronunciation suggestions:
- May use local Ollama with `llama3.1:8b` or another explicitly configured model.
- Must run after deterministic dictionary/glossary/acronym resolution.
- Must not silently change final pronunciation without preview or teacher confirmation.
- Suggestions should be saved as manual overrides if accepted.
- Must be optional and disabled by default.
- Current L1 planning lives in `docs/TTS_L1_LLM_PRONUNCIATION_SUGGESTIONS_PLAN.md`.
- L1A backend endpoint, L1B Studio UI, and L1C provider setup docs are implemented as API/Studio-only support. Do not wire it into render, rerender, `/synthesize`, or `tts_client`.

Expected changes:
- services/tts_service/main.py: add a preview request/response model and preview endpoint using prepare_text_for_tts.
- services/scripts/tts_client.py: add a preview helper with fail-open behavior.
- services/api/core/views.py and services/api/core/urls.py: add a Django /api/v1/tts/preview/ endpoint that calls the helper.
- tests/integration: add focused tests for preview metadata, fail-open behavior, and import isolation.

Before editing, inspect current files. After editing, run only the relevant tests:
- pytest tests/integration/test_tts_import_isolation.py
- pytest tests/integration/test_tts_text_normalization.py
- pytest tests/integration/test_tts_client_readiness.py

Return a concise summary and any tests that could not be run.
```
