# Repo Cleanup Audit

## Executive Summary

This audit was originally created on branch `chore/repo-cleanup-audit` after confirming Phase 4 Studio TTS work was present. It has since been refreshed after the recovery, reliability, storage, frontend E2E, security, and test-hygiene cleanup work.

The earlier low-risk cleanup targets have been removed: the old `services/tts_service/preprocess/` package, prototype-only root `frontend/` folder, `scripts/tmp_import_check.py`, and tracked generated `infra/out.log`. `services/api/db.sqlite3` is no longer source-controlled. Active TTS preprocessing remains under `services/tts_service/tts_preprocess/`, and active frontend work remains under `services/frontend/`.

Subtitle generation now includes original and translated SRT/VTT track metadata, tokenized VTT exposure, async public request guardrails, and partial Watch/Studio controls. HLS, subtitle tracks, watermark overlays, and avatar overlays are partially wired into the frontend player, but browser coverage is not complete and staging media/provider validation remains open. Recovery reporting/manual guardrails are report/annotation-only; there is still no recovery apply mode.

## Phase 4 Presence Check

| Check | Status | Evidence |
| --- | --- | --- |
| Studio TTS panel exists | Present | `services/frontend/src/components/studio/TtsSettingsPanel.jsx` exists. |
| Studio has TTS tab | Present | `services/frontend/src/pages/Studio.jsx` includes `tts` in `LESSON_TABS` and renders tab label `TTS`. |
| Pronunciation preview UI exists | Present | `TtsSettingsPanel.jsx` imports `previewTtsNormalization` and renders `Pronunciation Preview` / `Preview pronunciation`. |
| Rerender with saved settings exists | Present | `TtsSettingsPanel.jsx` defines `handleRerenderWithSavedSettings` and renders `Rerender with saved settings`. |
| TTS plan marks Phase 4 complete | Present | `docs/TTS_COWORKER_INTEGRATION_PLAN.md` lists Phase 4 Studio UI controls as completed. |
| README mentions Studio TTS settings/preview | Present | `README.md` documents Studio TTS controls, preview, and rerender flow. |

## Cleanup Findings

| Area | Finding | Evidence | Risk | Recommended Action |
| --- | --- | --- | --- | --- |
| Duplicate TTS preprocessing packages | The stale ignored `services/tts_service/preprocess/` cache directory was removed; active code uses `services/tts_service/tts_preprocess/`. | Search found active imports of `tts_preprocess` in `services/tts_service/main.py`, `services/scripts/tts_client.py`, and tests. Docker worker copies `services/tts_service/tts_preprocess/` to `/app/tts_preprocess/`. No active TTS import uses `from preprocess` or `import preprocess`. | Low after cache cleanup. Deleting the active `tts_preprocess/` package would break TTS. | Keep `tts_preprocess/` as the only TTS preprocessing package. |
| Avatar preprocess import | `services/api/core/views.py` imports `avatar.preprocess`; this is unrelated to TTS preprocessing. | Search shows `avatar.preprocess` in Django views. `tests/integration/test_tts_import_isolation.py` verifies TTS uses `tts_preprocess` instead of old/avatar preprocess modules. | Accidental broad deletion or import rewrites could break avatar paths. | Do not touch avatar preprocess in repo cleanup. |
| Env templates | `infra/.env.example` is the only env template found and remains the practical canonical template. The Django settings, TTS glossary, Vite API URL, XTTS flags, and Google OAuth placeholders have been corrected. | No root `.env.example` was found. `infra/docker-compose.yml` uses `env_file: .env`, so Docker reads `infra/.env`. README points developers to `infra/.env.example`. | Remaining env drift is lower risk and mostly around optional MinIO/S3 and media-token documentation. | Continue treating `infra/.env.example` as canonical and finish the remaining documentation audit. |
| TTS glossary env | `infra/.env.example` now contains `TTS_GLOSSARY_PATH=/app/tts_preprocess/glossary.json`. | `services/tts_service/README.md` documents `/app/tts_preprocess/glossary.json`. Docker worker copies `tts_preprocess/`. | Low. | Keep this path aligned with TTS README and container copy paths. |
| Django settings env | `infra/.env.example` now uses `DJANGO_SETTINGS_MODULE=config.settings`. | Active API files live under `services/api/config/settings.py`; compose runs Django/Celery from the service layout. | Low. | Keep README examples aligned. |
| Frontend API env | `infra/.env.example` now contains `VITE_API_BASE_URL`, and `services/frontend/src/api.js` reads it with the same local fallback. | Active app is `services/frontend/`; no Next.js app is present. | Low. | Keep deployment docs aligned with Vite. |
| MinIO/S3 env | `infra/.env.example` includes MinIO/AWS variables, and the code has an opt-in boto3-backed adapter behind `STORAGE_BACKEND=s3`. Filesystem remains the default runtime backend. | `services/api/core/storage_adapter.py` defines filesystem and S3 adapters; docs keep runtime media migration behind explicit staging proof. | Overstating S3 readiness could imply runtime media migration is complete. | Keep S3/MinIO described as an approved adapter foundation, not the default runtime storage path. |
| Root frontend prototype | The old root prototype folder has been removed. Active React app code is under `services/frontend/`. | `services/frontend/` has `package.json`, Vite config, React `src/`, Vitest tests, and Playwright E2E specs. No root `frontend/` folder exists in the current tree. | Low. | Keep new frontend work in `services/frontend/` unless a separate prototype area is explicitly approved. |
| Root package lock | No root `package-lock.json` exists, and the removed prototype lock is gone with the old prototype folder. | Active lockfile is `services/frontend/package-lock.json`. | Low. | No root cleanup needed. |
| Root temp script | `scripts/tmp_import_check.py` has been removed. Active shared scripts remain under `services/scripts/`. | Current source search finds no tracked temp import probe. | Low. | Avoid adding one-off probe scripts to source control. |
| Misplaced infra | `services/infra/` does not exist. Root `infra/` is the active Docker/compose area. | `Test-Path services/infra` returned false; compose and Dockerfiles are under root `infra/`. | None for `services/infra`. | No deletion needed for `services/infra`. Keep root `infra/`. |
| Generated log artifact | `infra/out.log` has been removed from source control. | Current tracked-file search finds no `infra/out.log`. | Low. | Keep generated logs ignored. |
| Local database artifact | `services/api/db.sqlite3` has been removed from source control. | Current tracked-file search finds no `services/api/db.sqlite3`. | Low. Local DB files can still exist as developer runtime state. | Prefer migrations and explicit fixtures for durable test/demo data. |
| Storage directories | Multiple `storage_local` directories exist and are ignored: root, `infra/storage_local`, `services/api/storage_local`, and `services/tts_service/storage_local`. | Directory search found multiple `storage_local` locations. Ignored status shows `infra/storage_local/` and root `storage_local/`. Compose mounts `./storage_local:/app/storage_local` from inside `infra/`. | Runtime files can scatter across paths; changing mounts can break local Docker users. | Phase C: choose one canonical local dev path. Prefer root `storage_local/` mounted into Docker as `/app/storage_local` or a future `/app/storage` path. Do not put canonical runtime storage under `infra/` long-term. |
| Docker storage layout | Docker now uses repo-root `storage_local` mounted at `/app/storage_local`. | `infra/docker-compose.yml` bind mount paths use `../storage_local:/app/storage_local`. | Lower; leftover old local files may still exist under `infra/storage_local` for some developers. | Keep migration notes for anyone with old local runtime files. |

## Subtitle And Multilingual Status

| Feature | Status | Evidence / Files | Missing Work |
| --- | --- | --- | --- |
| Original SRT generation | Implemented | `services/scripts/ffmpeg_helpers.py` defines `generate_srt` and `generate_srt_from_cues`. `services/worker/tasks.py` writes `final/{project_id}.srt` and updates `Job.srt_url`. | Keep regression tests around transcript rerender and original-text captions. |
| Subtitle chunk metadata | Implemented | `services/api/core/models.py` has `TranscriptPage.subtitle_chunks` and `chunk_timeline`. Worker stores subtitle/page timeline metadata. Serializers expose these fields. | Confirm long presentations keep timing stable after targeted rerenders. |
| API subtitle exposure | Implemented | `Job.srt_url` is serialized. Playback payload includes `srt_url` / `has_srt`. `MediaStreamView` can serve subtitle content as WebVTT using `_srt_text_to_vtt`. | Add explicit API docs for subtitle URL/token behavior. |
| Frontend subtitle playback | Partial | `services/frontend/src/pages/Watch.jsx` manages subtitle track selection/request state, and `VideoStage.jsx`/`HlsPlayer.jsx` render ready VTT tracks with an in-player caption layer. | Add broader browser coverage for original/translated tracks, failure states, and regenerated subtitles after rerender. |
| Caption source text | Implemented correctly | Worker subtitle chunks are based on original narration/transcript text. TTS metadata may contain normalized/spoken text, but tests verify SRT uses original text. | Keep this invariant when adding multilingual or pronunciation features. |
| Transcript rerender subtitle refresh | Implemented for original subtitles | Transcript pipeline tests cover subtitle chunk updates and rerender paths. Worker rebuilds timeline/subtitle metadata during rerender/finalization. | Add end-to-end playback verification for regenerated SRT after transcript edits. |
| Language detection | Partial placeholder | Worker/API create `language_detection.json` sidecars with `detector: placeholder_v1` / heuristic metadata. Tests assert placeholder behavior. | Replace placeholder with a real detector or explicit user-selected source language. |
| Multilingual subtitle translation | Partially implemented | Backend has `TranslatedSubtitleTrack`, provider selection/fallback, async worker generation, public request guardrails, and tokenized ready-track listing. Watch/Studio expose partial request/select flows. | Validate production providers in staging; add durable quota/cost/admin controls before broad rollout. |
| Translated subtitle assets | Partially implemented | Ready translated tracks can store SRT/VTT paths and expose tokenized VTT URLs. Rerender does not automatically regenerate translations. | Define stale-translation handling after transcript changes. |
| Translation provider/model | Partially implemented | Auto provider chain can use configured API, Ollama, LibreTranslate, Argos, or mock fallback when allowed. | Keep production mock fallback disabled and validate configured providers before production use. |

## TTS Intelligence Future Work

| Feature | Status | Recommended Owner Files | Notes |
| --- | --- | --- | --- |
| Turkish normalization | Implemented baseline | `services/tts_service/tts_preprocess/tr_normalizer.py`, `normalizer.py`, `glossary.py`, `glossary.json` | Current system supports Turkish normalization and request-local/project overrides. |
| Project pronunciation overrides | Implemented | `services/api/core/models.py`, `serializers.py`, `views.py`, `services/tts_service/main.py`, `services/frontend/src/components/studio/TtsSettingsPanel.jsx` | Overrides are persisted per project and applied to preview/generation without mutating global glossary files. |
| Override protection from re-normalization | Implemented | `services/tts_service/main.py`, `services/tts_service/tts_preprocess/`, `tests/integration/test_tts_preview_protection.py` | Keep this behavior when adding dictionary-first resolver logic. |
| Dictionary-first Turkish resolver | Partially implemented | `services/tts_service/tts_preprocess/deterministic_resolver.py` plus tests in `tests/integration/test_tts_text_normalization.py` / `test_tts_preview.py` | Expand curated dictionaries from observed project needs only. |
| English/technical dictionary fallback | Partially implemented | Curated English technical fallback lives under `services/tts_service/tts_preprocess/`. | Keep precedence conservative; manual/project overrides still win. |
| Acronym resolver | Partially implemented | Acronym ownership moved into deterministic resolver data under `services/tts_service/tts_preprocess/`. | Continue adding language-aware cases with tests. |
| Optional Llama/LLM pronunciation suggestions | Implemented as optional assistance | Django endpoint and Studio UI can request disabled-by-default suggestions for unknown/ambiguous terms; accepted suggestions become normal draft override rows and do not add render-path intelligence. | Keep optional and offline-safe. Suggestions should not silently change generation without user confirmation. |
| Global/shared glossary CRUD | Planned, not implemented | Django `core` models/serializers/views/urls, Studio component under `services/frontend/src/components/studio/` | Do not replace project overrides. Shared glossary needs ownership, permission, and audit rules. |

## Safe Cleanup Phases

Phase A: safe deletions only  
Completed. The old TTS `preprocess/`, root static prototype folder, `scripts/tmp_import_check.py`, and generated `infra/out.log` were removed after reference checks.

Phase A completion note: the confirmed-unused duplicate TTS `preprocess/` package, root static `frontend/` prototype, `scripts/tmp_import_check.py`, and tracked generated `infra/out.log` were removed after reference checks. Active TTS preprocessing remains `services/tts_service/tts_preprocess/`, and the active frontend remains `services/frontend/`.

Phase B: env unification  
Mostly completed for Django settings, TTS glossary path, frontend API URL naming, XTTS flags, and Google OAuth placeholders. Continue auditing optional MinIO/S3 and media-token docs.

Phase C: storage layout rationalization  
Choose one local dev storage root. Prefer repo-root `storage_local/` for host development and a single Docker mount into `/app/storage_local` or a future `/app/storage`. Do not keep runtime storage under `infra/` as the long-term canonical path unless the team explicitly chooses that layout.

Phase D: subtitle/multilingual implementation plan  
Partially implemented. Frontend subtitle rendering, track metadata, async translated SRT/VTT generation, and request/select controls exist, but provider validation, quota/cost controls, and stale-translation handling remain open.

Phase E: dictionary-first TTS resolver plan  
Partially implemented. Deterministic acronym and curated technical fallback handling live inside `tts_preprocess/`; optional LLM suggestions are Studio assistance only and do not run during rendering.

## Exact Future Commands

Historical checks used before deletion:

```powershell
git branch --show-current
git status --short
rg "from preprocess|import preprocess|services.tts_service.preprocess|tts_preprocess|preprocess/" .
rg "scripts/tmp_import_check.py|infra/out.log|services/api/db.sqlite3" README.md docs infra services tests scripts
```

Historical Phase A cleanup commands, already completed:

```powershell
git rm -r -- services/tts_service/preprocess
git rm -r -- frontend
git rm -- scripts/tmp_import_check.py
git rm -- infra/out.log
python -m pytest tests/integration/test_tts_import_isolation.py tests/integration/test_tts_preview.py tests/integration/test_tts_client_readiness.py -q
Push-Location services/frontend; npm run build; Pop-Location
git diff --stat
git commit -m "Remove unused duplicate repo artifacts"
```

Phase B env audit commands:

```powershell
rg "env.example|DJANGO_SETTINGS_MODULE|TTS_GLOSSARY_PATH|NEXT_PUBLIC_API_URL|VITE_|STORAGE_ROOT|MINIO|AWS_|XTTS" README.md docs infra services
git diff -- infra/.env.example README.md docs
```

Phase C storage audit commands:

```powershell
Get-ChildItem -Recurse -Force -Directory -Filter storage_local
git ls-files | rg "(^|/)storage_local/|storage_local|db\.sqlite3|out\.log"
rg "STORAGE_ROOT|storage_local|/app/storage" README.md docs infra services tests
```

Phase D subtitle planning commands:

```powershell
rg "srt|subtitle|subtitles|caption|captions|vtt|cc|transcript_chunks|subtitle_chunks|lang_hint|language detection|translate|translation" services tests docs README.md
```

Phase E TTS intelligence planning commands:

```powershell
rg "dictionary|glossary|acronym|abbreviation|phonetic|llama|LLM|override|normalization" services/tts_service services/api services/frontend/src tests docs README.md
```
