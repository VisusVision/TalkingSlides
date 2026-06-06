# Repo Cleanup Audit

## Executive Summary

This audit was created on branch `chore/repo-cleanup-audit` after confirming Phase 4 Studio TTS work is present. The current TTS milestone is complete enough to close: preview, persisted project settings, worker/rerender wiring, and Studio save/preview/rerender UI are all present.

The lowest-risk cleanup targets are duplicate or unused repo artifacts: the old `services/tts_service/preprocess/` package, the prototype-only root `frontend/` folder, `scripts/tmp_import_check.py`, and tracked generated artifacts such as `infra/out.log`. Environment and storage cleanup should be handled in separate phases because current Docker and local defaults disagree in several places.

Subtitle generation is partially implemented for original-language SRT/VTT playback assets, but the frontend player does not yet render subtitle tracks or expose a CC selector. Multilingual subtitle translation is not started beyond placeholder language detection. Dictionary-first TTS intelligence and optional LLM pronunciation suggestions remain future work.

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
| MinIO/S3 env | `infra/.env.example` includes MinIO/AWS variables, but active storage code primarily uses local `STORAGE_ROOT`. | Searches show local storage services and no active S3 provider integration. | Keeping unused object-storage env can imply a supported storage mode that is not actually wired. | Phase B: mark MinIO/S3 as future/optional or remove from template after storage design is decided. |
| Root frontend prototype | Root `frontend/` contains static HTML prototype files and a small package lock. Active React app is `services/frontend/`. | Root `frontend/` has files such as `studio.html`, `analytics.html`, and zero-byte prototype pages. `services/frontend/` has `package.json`, Vite config, and React `src/`. README identifies `services/frontend` as active. | Low app-runtime risk, but deleting prototypes may remove design reference material. | Phase A: delete root `frontend/` after one final reference search, or archive screenshots first if the prototypes still have design value. |
| Root package lock | No root `package-lock.json` exists. The only prototype lock found is `frontend/package-lock.json`. | `Test-Path package-lock.json` was false; active lockfile is `services/frontend/package-lock.json`. | None for root lock. The prototype lock should go with root `frontend/` if that directory is deleted. | No root cleanup needed. Remove `frontend/package-lock.json` only as part of deleting prototype `frontend/`. |
| Root temp script | `scripts/tmp_import_check.py` is a temporary import probe and is not referenced by docs, Docker, CI, or app code. | Active script directory is `services/scripts/`; Docker worker copies `services/scripts/`. Search found no active references to `scripts/tmp_import_check.py`. | Very low. | Phase A: delete `scripts/tmp_import_check.py` after final reference check. |
| Misplaced infra | `services/infra/` does not exist. Root `infra/` is the active Docker/compose area. | `Test-Path services/infra` returned false; compose and Dockerfiles are under root `infra/`. | None for `services/infra`. | No deletion needed for `services/infra`. Keep root `infra/`. |
| Tracked generated log | `infra/out.log` is tracked and appears to be generated runtime output. | `git ls-files` includes `infra/out.log`. | Low app risk, but deleting tracked logs may surprise someone using it as debugging evidence. | Phase A: delete `infra/out.log` and ensure logs are ignored. |
| Tracked local database | `services/api/db.sqlite3` is tracked. | `git ls-files` includes `services/api/db.sqlite3`. | Higher than log cleanup because some tests or local demos may accidentally rely on seeded data. | Phase C: inspect contents and test assumptions before removal. Prefer migrations/fixtures over tracked DB. |
| Storage directories | Multiple `storage_local` directories exist and are ignored: root, `infra/storage_local`, `services/api/storage_local`, and `services/tts_service/storage_local`. | Directory search found multiple `storage_local` locations. Ignored status shows `infra/storage_local/` and root `storage_local/`. Compose mounts `./storage_local:/app/storage_local` from inside `infra/`. | Runtime files can scatter across paths; changing mounts can break local Docker users. | Phase C: choose one canonical local dev path. Prefer root `storage_local/` mounted into Docker as `/app/storage_local` or a future `/app/storage` path. Do not put canonical runtime storage under `infra/` long-term. |
| Docker storage layout | Docker now uses repo-root `storage_local` mounted at `/app/storage_local`. | `infra/docker-compose.yml` bind mount paths use `../storage_local:/app/storage_local`. | Lower; leftover old local files may still exist under `infra/storage_local` for some developers. | Keep migration notes for anyone with old local runtime files. |

## Subtitle And Multilingual Status

| Feature | Status | Evidence / Files | Missing Work |
| --- | --- | --- | --- |
| Original SRT generation | Implemented | `services/scripts/ffmpeg_helpers.py` defines `generate_srt` and `generate_srt_from_cues`. `services/worker/tasks.py` writes `final/{project_id}.srt` and updates `Job.srt_url`. | Keep regression tests around transcript rerender and original-text captions. |
| Subtitle chunk metadata | Implemented | `services/api/core/models.py` has `TranscriptPage.subtitle_chunks` and `chunk_timeline`. Worker stores subtitle/page timeline metadata. Serializers expose these fields. | Confirm long presentations keep timing stable after targeted rerenders. |
| API subtitle exposure | Implemented | `Job.srt_url` is serialized. Playback payload includes `srt_url` / `has_srt`. `MediaStreamView` can serve subtitle content as WebVTT using `_srt_text_to_vtt`. | Add explicit API docs for subtitle URL/token behavior. |
| Frontend subtitle playback | Partial | `services/frontend/src/pages/Watch.jsx` reads `playbackData.srt_url`, but `services/frontend/src/components/player/VideoStage.jsx` renders a plain video element without a `<track>` or CC selector. | Add subtitle track rendering and a CC toggle/language selector. |
| Caption source text | Implemented correctly | Worker subtitle chunks are based on original narration/transcript text. TTS metadata may contain normalized/spoken text, but tests verify SRT uses original text. | Keep this invariant when adding multilingual or pronunciation features. |
| Transcript rerender subtitle refresh | Implemented for original subtitles | Transcript pipeline tests cover subtitle chunk updates and rerender paths. Worker rebuilds timeline/subtitle metadata during rerender/finalization. | Add end-to-end playback verification for regenerated SRT after transcript edits. |
| Language detection | Partial placeholder | Worker/API create `language_detection.json` sidecars with `detector: placeholder_v1` / heuristic metadata. Tests assert placeholder behavior. | Replace placeholder with a real detector or explicit user-selected source language. |
| Multilingual subtitle translation | Not started | No translation provider, model/service, DB track model, translated subtitle files, or frontend CC language selector was found. | Design translation jobs, provider config, subtitle track storage, API exposure, and frontend selector. |
| Translated subtitle assets | Not started | No `subtitle_tracks`, `target_language`, `captions_tr/en/ar`, or translation output assets exist. | Generate translated SRT/VTT files and persist metadata per language. |
| Translation provider/model | Not started | Searches found no real translation service integration. | Choose provider/model, queuing strategy, error handling, and cost controls. |

## TTS Intelligence Future Work

| Feature | Status | Recommended Owner Files | Notes |
| --- | --- | --- | --- |
| Turkish normalization | Implemented baseline | `services/tts_service/tts_preprocess/tr_normalizer.py`, `normalizer.py`, `glossary.py`, `glossary.json` | Current system supports Turkish normalization and request-local/project overrides. |
| Project pronunciation overrides | Implemented | `services/api/core/models.py`, `serializers.py`, `views.py`, `services/tts_service/main.py`, `services/frontend/src/components/studio/TtsSettingsPanel.jsx` | Overrides are persisted per project and applied to preview/generation without mutating global glossary files. |
| Override protection from re-normalization | Implemented | `services/tts_service/main.py`, `services/tts_service/tts_preprocess/`, `tests/integration/test_tts_preview_protection.py` | Keep this behavior when adding dictionary-first resolver logic. |
| Dictionary-first Turkish resolver | Planned, not implemented | `services/tts_service/tts_preprocess/` plus new tests in `tests/integration/test_tts_text_normalization.py` / `test_tts_preview.py` | Do not mix this with cleanup. Design dictionary precedence and ambiguity handling first. |
| English/technical dictionary fallback | Planned, not implemented | `services/tts_service/tts_preprocess/`, optional data files under that package | Should layer after Turkish-first lookup and before generic normalization. |
| Acronym resolver | Partial | Current glossary/normalizer code handles some abbreviation/acronym cases. Future owner remains `services/tts_service/tts_preprocess/`. | A full acronym resolver should define language-aware spelling rules and tests. |
| Optional Llama/LLM pronunciation suggestions | Planned, not implemented | Future service boundary plus Django endpoints only after resolver is stable | Keep optional and offline-safe. Suggestions should not silently change generation without user confirmation. |
| Global/shared glossary CRUD | Planned, not implemented | Django `core` models/serializers/views/urls, Studio component under `services/frontend/src/components/studio/` | Do not replace project overrides. Shared glossary needs ownership, permission, and audit rules. |

## Safe Cleanup Phases

Phase A: safe deletions only  
Delete only artifacts confirmed unused by final reference checks: old TTS `preprocess/`, root static `frontend/`, `scripts/tmp_import_check.py`, and tracked generated logs. Run import isolation and frontend build after deletion.

Phase A completion note: the confirmed-unused duplicate TTS `preprocess/` package, root static `frontend/` prototype, `scripts/tmp_import_check.py`, and tracked generated `infra/out.log` were removed after reference checks. Active TTS preprocessing remains `services/tts_service/tts_preprocess/`, and the active frontend remains `services/frontend/`.

Phase B: env unification  
Treat `infra/.env.example` as canonical, then update stale Django settings, TTS glossary path, frontend API URL naming, XTTS flags, and MinIO/S3 comments. Align README and service docs at the same time.

Phase C: storage layout rationalization  
Choose one local dev storage root. Prefer repo-root `storage_local/` for host development and a single Docker mount into `/app/storage_local` or a future `/app/storage`. Do not keep runtime storage under `infra/` as the long-term canonical path unless the team explicitly chooses that layout.

Phase D: subtitle/multilingual implementation plan  
Add frontend subtitle track rendering first, then design multilingual subtitle generation as a separate worker/API feature with track metadata, translated SRT/VTT assets, and a CC language selector.

Phase E: dictionary-first TTS resolver plan  
Plan dictionary-first Turkish/English pronunciation resolution inside `tts_preprocess/` before adding LLM suggestions or global glossary CRUD.

## Exact Future Commands

Run these checks before any deletion:

```powershell
git branch --show-current
git status --short
rg "from preprocess|import preprocess|services.tts_service.preprocess|tts_preprocess|preprocess/" .
rg "frontend/|scripts/tmp_import_check.py|infra/out.log|services/api/db.sqlite3" README.md docs infra services tests scripts
```

Phase A cleanup commands, to run only after the checks above show no active references:

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
