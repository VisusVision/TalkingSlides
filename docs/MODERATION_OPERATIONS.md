# Moderation Operations

This document covers operations for the VISUS moderation system.

Automatic source/text moderation, local visual asset validation, OCR slide moderation, and video frame audit can be enabled behind feature flags. Keep risky or costly providers disabled until the target environment is ready.

## Recommended Operation Profiles

Use these profiles as starting points for local, staging, and production environments.

### Local-safe

Local-safe enables deterministic source/text moderation and keeps visual, OCR, and video automation disabled.

```sh
SOURCE_MODERATION_AUTO_ENABLED=true
SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION=true
OCR_MODERATION_AUTO_ENABLED=false
VISUAL_MODERATION_AUTO_ENABLED=false
VISUAL_SAFETY_PROVIDER=none
VISUAL_SAFETY_CLASSIFIER_ENABLED=false
VIDEO_FRAME_AUDIT_AUTO_ENABLED=false
```

### Staging-pilot

Staging-pilot enables the full moderation pipeline in observe-first mode. Source text can block render, while visual/OCR/video findings are recorded without blocking publish by default.

```sh
SOURCE_MODERATION_AUTO_ENABLED=true
SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION=true
VISUAL_MODERATION_AUTO_ENABLED=true
VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION=false
VISUAL_SAFETY_PROVIDER=none
VISUAL_SAFETY_CLASSIFIER_ENABLED=false
OCR_MODERATION_AUTO_ENABLED=true
OCR_MODERATION_PROVIDER=azure
AZURE_OCR_ENABLED=true
OCR_MODERATION_BLOCK_RENDER_ON_REJECTION=false
VIDEO_FRAME_AUDIT_AUTO_ENABLED=true
VIDEO_FRAME_AUDIT_RUN_OCR=false
VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION=false
VIDEO_FRAME_AUDIT_RETAIN_FRAMES=false
```

### Strict-production

Strict-production enables source blocking, visual publish gating, OCR slide moderation through Azure, and video frame publish gating.

```sh
SOURCE_MODERATION_AUTO_ENABLED=true
SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION=true
VISUAL_MODERATION_AUTO_ENABLED=true
VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION=true
VISUAL_SAFETY_PROVIDER=none
VISUAL_SAFETY_CLASSIFIER_ENABLED=false
OCR_MODERATION_AUTO_ENABLED=true
OCR_MODERATION_PROVIDER=azure
AZURE_OCR_ENABLED=true
VIDEO_FRAME_AUDIT_AUTO_ENABLED=true
VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION=true
VIDEO_FRAME_AUDIT_RETAIN_FRAMES=false
```

Secret safety:

- `AZURE_OCR_KEY` must only live in local, staging, or production environment configuration.
- `AZURE_CONTENT_SAFETY_KEY` must only live in local, staging, or production environment configuration.
- Never commit `AZURE_OCR_KEY` or provider secrets to Git.
- Rotate any Azure OCR or Content Safety key that has been committed, logged, pasted into chat, or otherwise leaked.

## One-command Smoke Checklist

The read-only checklist command prints current flag values and the recommended smoke commands:

```sh
python manage.py moderation_smoke_checklist
```

It does not create database rows, run moderation, sample frames, call Azure, or print secret values.

Final manual smoke checklist:

```sh
python manage.py moderation_system_status
python manage.py create_moderation_smoke_project --kind clean --user-id <user_id> --scan
python manage.py create_moderation_smoke_project --kind profanity --user-id <user_id> --scan --request-review --review-message "Smoke unsafe text review"
python manage.py run_ocr_bridge --image-path <path-to-test-image.png> --asset-type slide_image --slide-order 0 --moderate-text --project-id <project_id>
python manage.py sample_video_frames --video-path <path-to-video.mp4> --output-dir <frames-dir> --max-frames 1
python manage.py cleanup_video_frame_audit_files --dry-run
```

## Automatic Source Moderation

Automatic source/text moderation can be enabled for the worker pipeline after PPTX extraction creates `TranscriptPage` rows and before expensive TTS/render/avatar work is dispatched.

Environment flags:

```sh
SOURCE_MODERATION_AUTO_ENABLED=false
SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION=true
SOURCE_MODERATION_PHASE=source_scan
TEXT_SAFETY_PROVIDER=azure_content_safety
TEXT_SAFETY_CLASSIFIER_ENABLED=true
TEXT_SAFETY_TIMEOUT_SECONDS=20
TEXT_SAFETY_CATEGORIES=sexual,violence,self_harm,hate
TEXT_SAFETY_BLOCK_SEVERITY=4
TEXT_SAFETY_FALLBACK_PROVIDER=local_rules
```

Behavior when enabled:

- Runs the text/source moderation orchestrator synchronously in the worker after transcript sync.
- Uses Azure Content Safety text moderation first when `TEXT_SAFETY_PROVIDER=azure_content_safety`, `TEXT_SAFETY_CLASSIFIER_ENABLED=true`, and the shared `AZURE_CONTENT_SAFETY_ENDPOINT` / `AZURE_CONTENT_SAFETY_KEY` are configured.
- Falls back to `LocalRulesProvider` when Azure text moderation is disabled, missing credentials, unavailable, timed out, or returns an invalid response.
- Updates `Project.moderation_status`, `Project.moderation_summary`, `AgentRun`, and `AgentFinding` through the same path as `run_moderation_scan`.
- If moderation returns `revision_required` or `needs_admin_review` and `SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION=true`, the worker stops before dispatching TTS/render/avatar tasks and marks the current job failed with a moderation message.
- If moderation returns `approved` or the content is already `admin_approved`, the render pipeline continues.
- Azure safe text responses approve text without admin review. Azure unsafe text responses create text findings only and do not create visual warnings.

What it does not do yet:

- It does not run visual image moderation automatically.
- It does not run OCR as part of visual asset validation.
- It does not sample video frames automatically.
- It does not require Ollama. Ollama remains optional/advisory and is skipped when Azure text safety returns a clear safe or unsafe result.
- It does not block user accounts. It blocks/reviews content only.

Docker test example:

```sh
docker compose exec worker sh -lc "cd /app/api && SOURCE_MODERATION_AUTO_ENABLED=true SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION=true python manage.py create_moderation_smoke_project --kind profanity --user-id 1 --scan --request-review --review-message 'Docker source moderation smoke'"
```

Manual fallback:

```sh
docker compose exec worker sh -lc "cd /app/api && python manage.py run_moderation_scan --project-id <project_id> --sync"
```

## Automatic Visual Asset Moderation

Automatic cover/slide image validation can be enabled after PPTX export creates local slide image files. It is disabled by default. For production use, configure the semantic visual safety provider; local image metadata rules alone do not automatically approve visuals unless `ALLOW_WEAK_LOCAL_VISUAL_APPROVAL=true`.

Environment flags:

```sh
VISUAL_MODERATION_AUTO_ENABLED=true
VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION=true
VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION=true
VISUAL_MODERATION_PHASE=visual_asset_scan
VISUAL_MODERATION_SCAN_COVER=true
VISUAL_MODERATION_SCAN_SLIDES=true
VISUAL_MODERATION_REQUIRE_SEMANTIC_PROVIDER=true
ALLOW_WEAK_LOCAL_VISUAL_APPROVAL=false
VISUAL_SAFETY_PROVIDER=azure_content_safety
VISUAL_SAFETY_CLASSIFIER_ENABLED=true
VISUAL_SAFETY_TIMEOUT_SECONDS=20
VISUAL_SAFETY_MAX_IMAGE_BYTES=10485760
AZURE_CONTENT_SAFETY_ENABLED=true
AZURE_CONTENT_SAFETY_ENDPOINT=https://replace-with-content-safety-resource.cognitiveservices.azure.com
AZURE_CONTENT_SAFETY_KEY=replace-with-azure-content-safety-key
AZURE_CONTENT_SAFETY_API_VERSION=2024-09-01
AZURE_CONTENT_SAFETY_CATEGORIES=sexual,violence,self_harm,hate
AZURE_CONTENT_SAFETY_BLOCK_SEVERITY=4
AVATAR_IMAGE_MODERATION_AUTO_ENABLED=false
AVATAR_IMAGE_MODERATION_BLOCK_ON_REJECTION=true
AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL=false
```

Behavior when enabled:

- Runs after source extraction/export has produced slide images.
- Scans the project cover image when configured and available.
- Scans exported slide images with `LocalImageRulesProvider`.
- Studio cover uploads and custom background uploads mark visual moderation stale and can trigger the same auto visual scan when `VISUAL_MODERATION_AUTO_ENABLED=true`.
- If `VISUAL_SAFETY_PROVIDER=azure_content_safety` and both visual safety flags are enabled, sends configured cover/slide images to Azure Content Safety for the semantic visual safety decision.
- Persists an `AgentRun` and any `AgentFinding` rows for visual asset validation.
- Writes a frontend-safe `moderation_summary.visual_asset_scan` summary.
- Does not overwrite text moderation status or text moderation summary fields.
- Safe semantic provider results are `allow` / `scan_passed` and do not require admin review.
- Unsafe semantic provider results are blocked/rejected and require replacing the visual before rerender.
- Provider unavailable, missing config, timeout, invalid response, or low-confidence/uncertain results become `needs_admin_review`; they are not labeled unsafe.
- If `VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION=true`, review/block decisions stop before downstream render dispatch and mark the current job failed with a visual validation message.
- If `VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION=true`, publish is blocked when the latest visual asset scan has unresolved serious findings or needs admin review.

Optional visual safety classifier:

- `VISUAL_SAFETY_PROVIDER=none` is the default and makes no external calls.
- `VISUAL_SAFETY_PROVIDER=azure_content_safety` selects the Azure Content Safety provider.
- The provider only calls Azure when `VISUAL_SAFETY_CLASSIFIER_ENABLED=true`, `AZURE_CONTENT_SAFETY_ENABLED=true`, and endpoint/key are configured.
- If disabled, missing credentials, oversized images, timeouts, API errors, or invalid responses occur, the scan returns `needs_admin_review` with `Visual safety scan unavailable` wording instead of approving or labeling the visual unsafe.
- Azure safe responses approve the visual without admin review. Azure block-threshold findings reject the visual. Azure below-threshold findings require admin review.
- Cost warning: Azure Content Safety calls may incur Azure charges.
- Privacy warning: when enabled, cover/slide images are sent to Azure for visual safety classification.

Recommended visual safety pilot:

1. Start with Azure Content Safety in staging using placeholder-free secret env values.
2. Verify safe educational images pass without admin review.
3. Keep render and publish gates enabled before production rollout so unsafe or unreviewed visuals cannot become public.

What it does not do by default:

- It does not detect real unsafe imagery unless the optional classifier is configured.
- It does not run OCR automatically.
- It does not sample video frames automatically.
- It does not call Ollama, external APIs, or GPU services by default.
- It does not change public catalog rules. Publish blocking is only enabled when `VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION=true`.

Publisher workflow when the publish gate is enabled:

- Visual validation does not block Studio editing or preview.
- The publisher can fix or remove problematic visuals, rerun the visual scan, and then publish.
- A newer clean visual scan clears older visual scan findings for publish-gate purposes.
- Visual validation is local metadata/file validation only; it is not a real unsafe-image classifier.

Manual fallback:

```sh
python manage.py run_visual_moderation_scan --project-id <project_id> --slide-path <path-to-slide-image> --slide-order 0 --sync
python manage.py run_visual_moderation_scan --project-id <project_id> --cover-path <path-to-cover-image> --sync
```

## Avatar/Profile Image Moderation

Avatar image moderation is disabled by default and does not block existing avatar uploads unless explicitly configured. When enabled, profile/avatar source images first run through local image validation and can optionally use the configured visual safety classifier.

Environment flags:

```sh
AVATAR_IMAGE_MODERATION_AUTO_ENABLED=false
AVATAR_IMAGE_MODERATION_BLOCK_ON_REJECTION=true
AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL=false
VISUAL_SAFETY_PROVIDER=none
VISUAL_SAFETY_CLASSIFIER_ENABLED=false
```

Behavior:

- If avatar image moderation is disabled, avatar uploads keep the current flow and store `avatar_moderation_status=skipped`.
- If Azure Content Safety is enabled/configured, avatar source images are sent to Azure just like other visual assets.
- If Azure is disabled, missing credentials, times out, or returns an invalid response, moderation fails open and records skipped/error metadata without printing secrets.
- Unsafe or review-needed avatar image findings block avatar preprocessing/preview/render when `AVATAR_IMAGE_MODERATION_BLOCK_ON_REJECTION=true`.
- `AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL=true` is stricter: avatar generation waits for an approved avatar image moderation status.
- Avatar image findings do not mutate `Project.moderation_status`.

Recommended avatar pilot:

1. Keep `VISUAL_SAFETY_PROVIDER=none` and `AVATAR_IMAGE_MODERATION_AUTO_ENABLED=false`.
2. Enable avatar image moderation on staging with Azure configured, while keeping publish gates and strict avatar approval disabled.
3. Review false positives and operational workflow.
4. Enable avatar blocking or approval requirements only after the team is comfortable with classifier behavior.

## Automatic OCR Slide Moderation

Automatic OCR slide moderation can be enabled after PPTX export creates local slide image files. It is disabled by default, uses the no-op OCR provider by default, and does not require Tesseract or external OCR services.

Environment flags:

```sh
OCR_MODERATION_AUTO_ENABLED=false
OCR_MODERATION_BLOCK_RENDER_ON_REJECTION=false
OCR_MODERATION_PHASE=ocr_slide_scan
OCR_MODERATION_SCAN_SLIDES=true
OCR_MODERATION_PROVIDER=noop
AZURE_OCR_ENABLED=false
AZURE_OCR_ENDPOINT=
AZURE_OCR_KEY=
AZURE_OCR_API_VERSION=2024-02-29-preview
AZURE_OCR_MODEL=prebuilt-read
AZURE_OCR_TIMEOUT_SECONDS=30
AZURE_OCR_MAX_IMAGE_BYTES=10485760
AZURE_OCR_LANG_HINTS=en,tr,ar
```

Behavior when enabled:

- Scans exported slide images only; it does not scan cover images or video frames.
- The default `noop` provider returns empty text safely.
- Empty OCR text skips text moderation.
- Non-empty OCR text is moderated with the existing local text moderation rules.
- Persists a separate `AgentRun` and any `AgentFinding` rows under the `ocr_slide_scan` phase.
- Writes a frontend-safe `moderation_summary.ocr_slide_scan` summary.
- Does not overwrite `moderation_summary.visual_asset_scan`.
- Does not change `Project.moderation_status`.
- Continues rendering by default.
- If `OCR_MODERATION_BLOCK_RENDER_ON_REJECTION=true`, serious OCR text findings can stop before downstream render dispatch and mark the current job failed.

What it does not do yet:

- It does not add a real OCR dependency.
- It does not call external APIs.
- It does not change publish or catalog rules.
- It does not sample video frames or run OCR on video frames.

Azure OCR provider:

- `NoopOCRProvider` remains the default and requires no credentials.
- Set `OCR_MODERATION_PROVIDER=azure` and `AZURE_OCR_ENABLED=true` to use Azure OCR.
- Configure `AZURE_OCR_ENDPOINT` and `AZURE_OCR_KEY` in the runtime environment; credentials are not stored in the repo.
- If Azure is disabled, missing credentials, times out, or returns an invalid response, OCR fails open and does not crash moderation.
- No Tesseract fallback is included.
- Cost warning: Azure OCR calls may incur Azure charges.
- Privacy warning: when enabled, slide images are sent to Azure for OCR processing.

Manual fallback:

```sh
python manage.py run_ocr_bridge --image-path <path-to-slide-image> --asset-type slide_image --slide-order 0
python manage.py run_ocr_bridge --image-path <path-to-slide-image> --asset-type slide_image --slide-order 0 --moderate-text --project-id <project_id>
```

## Automatic Video Frame Audit

Automatic video frame audit can be enabled after final render produces the lesson video. It is disabled by default and runs after the render job has already completed.

Environment flags:

```sh
VIDEO_FRAME_AUDIT_AUTO_ENABLED=false
VIDEO_FRAME_AUDIT_PHASE=video_frame_audit
VIDEO_FRAME_AUDIT_EVERY_SECONDS=10
VIDEO_FRAME_AUDIT_MAX_FRAMES=5
VIDEO_FRAME_AUDIT_RUN_VISUAL_CHECK=true
VIDEO_FRAME_AUDIT_RUN_OCR=false
VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION=false
VIDEO_FRAME_AUDIT_RETAIN_FRAMES=false
VIDEO_FRAME_AUDIT_FRAME_RETENTION_DAYS=7
VIDEO_FRAME_AUDIT_CLEANUP_ON_SUCCESS=true
VISUAL_SAFETY_PROVIDER=none
VISUAL_SAFETY_CLASSIFIER_ENABLED=false
```

Behavior when enabled:

- Runs after final video render writes playback assets and the render job is marked done.
- Samples a limited number of frames using the existing FFmpeg frame sampling helper.
- Writes frames under `storage_local/moderation/video_frames/<project_id>/<job_id>/` when a job id is available.
- Runs local image metadata validation on sampled frames by default.
- If `VIDEO_FRAME_AUDIT_RUN_VISUAL_CHECK=true` and the optional visual safety classifier is enabled/configured, sampled frames also go through the selected visual safety provider.
- Stores a separate `AgentRun` and any `AgentFinding` rows under the `video_frame_audit` phase.
- Writes a frontend-safe `moderation_summary.video_frame_audit` summary.
- Does not overwrite source, visual asset, or OCR slide summaries.
- Does not change `Project.moderation_status`.
- Does not block render; render has already completed.
- Does not block publish by default.
- If `VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION=true`, publishing is blocked when the latest completed `video_frame_audit` run has serious findings.
- A newer clean completed video frame audit run clears an older blocked run.
- Sampled frame files are temporary by default. Metadata in `AgentRun` and `AgentFinding` remains after cleanup.

Optional OCR on frames:

- `VIDEO_FRAME_AUDIT_RUN_OCR=false` by default.
- If enabled, sampled frames are passed through the configured OCR provider and extracted text is moderated with the existing local text rules.
- Azure OCR may incur cost and sends sampled frame images to Azure when `OCR_MODERATION_PROVIDER=azure` and Azure OCR is enabled.
- Azure Content Safety may incur cost and sends sampled frame images to Azure when `VISUAL_SAFETY_PROVIDER=azure_content_safety` and the visual safety classifier is enabled/configured.

What it does not do yet:

- It does not detect real unsafe imagery by default.
- The video-frame publish gate is optional and disabled by default.
- It does not call OCR by default.
- It does not require GPU or avatar workers.

Frame retention and cleanup:

- By default, successful audits delete sampled frame files after metadata has been persisted.
- Set `VIDEO_FRAME_AUDIT_RETAIN_FRAMES=true` to keep sampled frames for debugging.
- Use `VIDEO_FRAME_AUDIT_FRAME_RETENTION_DAYS` to control age-based cleanup.
- Storage can grow quickly if frame retention is enabled.

Cleanup command:

```sh
python manage.py cleanup_video_frame_audit_files --dry-run
python manage.py cleanup_video_frame_audit_files --days 7
python manage.py cleanup_video_frame_audit_files --all
```

Manual fallback:

```sh
python manage.py sample_video_frames --video-path <path-to-video> --output-dir <frames-dir> --max-frames 5
python manage.py sample_video_frames --video-path <path-to-video> --output-dir <frames-dir> --max-frames 5 --moderate --project-id <project_id>
```

## Optional Translation Moderation

Translation-to-English moderation is available as a secondary advisory layer and is disabled by default.

Environment flags:

```sh
TRANSLATION_MODERATION_ENABLED=false
TRANSLATION_MODERATION_PROVIDER=none
TRANSLATION_MODERATION_TIMEOUT_SECONDS=20
TRANSLATION_MODERATION_TARGET_LANGUAGE=en
TRANSLATION_MODERATION_BASE_URL=http://libretranslate:5000
```

Behavior:

- Original-language local moderation always runs first.
- English and Turkish local rules remain the primary deterministic checks.
- If local rules return a high-confidence block, translation is skipped and cannot downgrade the block.
- If local rules allow or need review, enabled translation can translate the source text to English and run the existing English moderation rules as a secondary signal.
- Translation findings are advisory: translated block findings are capped to admin review by default.
- If translation is disabled, unavailable, times out, or returns an invalid response, the moderation run continues with the original local result.

LibreTranslate can be started with the optional Docker profile when needed:

```sh
docker compose -f infra/docker-compose.yml --profile translation up -d libretranslate
```

Then enable the bridge for a worker smoke test:

```sh
docker compose exec worker sh -lc "cd /app/api && TRANSLATION_MODERATION_ENABLED=true TRANSLATION_MODERATION_PROVIDER=libretranslate TRANSLATION_MODERATION_BASE_URL=http://libretranslate:5000 python manage.py run_moderation_scan --project-id <project_id> --sync"
```

The translation bridge is advisory and can produce false positives or false negatives. Keep Django admin review available for disagreement between original and translated moderation results.

## System Status

Run the read-only diagnostics command:

```powershell
cd services/api
..\..\.venv\Scripts\python.exe manage.py moderation_system_status
..\..\.venv\Scripts\python.exe manage.py moderation_system_status --json
..\..\.venv\Scripts\python.exe manage.py moderation_system_status --check-imports
```

The command checks model imports, moderation database counts, provider availability, Ollama configuration, FFmpeg/Pillow availability, and moderation management command registration. It does not create database rows, call Ollama, call external APIs, run FFmpeg, or change project state.

## Docker Smoke Tests

In Docker, sync moderation commands that import `worker.ai_agents` should be run from the worker container. The API container may not import `worker.ai_agents` unless the Docker Python path/layout is adjusted.

Known-good smoke command:

```sh
docker compose exec worker sh -lc "cd /app/api && python manage.py create_moderation_smoke_project --kind profanity --user-id 1 --scan --request-review --review-message 'Docker smoke test from worker'"
```

Status check from the worker container:

```sh
docker compose exec worker sh -lc "cd /app/api && python manage.py moderation_system_status"
```

JSON status:

```sh
docker compose exec worker sh -lc "cd /app/api && python manage.py moderation_system_status --json"
```

## Frontend Moderation UI

Staff users can open the dedicated moderation dashboard from the sidebar link labeled `Moderation`, or by visiting:

```text
/moderation
```

The moderation dashboard is staff/admin-only. It has separate filters for open requests, approved requests, rejected requests, and all history. Publishers and students cannot list or act on moderation review requests, but publishers can still see their own lesson moderation status in Studio and can request admin review when the existing lesson policy allows it.

Publishers can see a lesson's moderation status in Studio:

- Lesson cards show a `Moderation: ...` status badge.
- The selected lesson overview shows the moderation panel with publish gate status, findings, refresh, rescan, and admin-review request controls.
- The Studio Editor also has a moderation panel for the selected lesson.

If the moderation dashboard is empty, create a local smoke review request from the worker container:

```sh
docker compose exec worker sh -lc "cd /app/api && python manage.py create_moderation_smoke_project --kind profanity --user-id 1 --scan --request-review --review-message 'UI smoke test'"
```

Django admin remains the fallback for moderation review and record inspection.

## Django Admin

Open Django admin and inspect moderation records:

- `/admin/ai_agents/agentrun/`
- `/admin/ai_agents/agentfinding/`
- `/admin/ai_agents/adminreviewrequest/`
- `/admin/ai_agents/publicationblockevent/`
- `/admin/ai_agents/agentdefinition/`

Admin review requests can be approved or rejected from the Django admin actions added in the moderation admin tools phase.

## Manual Commands

Text moderation:

```sh
python manage.py run_moderation_scan --project-id <project_id> --sync
python manage.py create_moderation_smoke_project --kind clean --user-id <user_id> --scan
python manage.py create_moderation_smoke_project --kind profanity --user-id <user_id> --scan --request-review --review-message "Please review this smoke test"
python manage.py create_moderation_review_request --project-id <project_id> --user-id <user_id> --message "AI misunderstood educational context"
```

Visual/image validation:

```sh
python manage.py run_visual_moderation_scan --project-id <project_id> --cover-path <path-to-image> --sync
python manage.py run_visual_moderation_scan --project-id <project_id> --slide-path <path-to-slide-image> --slide-order 0 --sync
```

OCR bridge:

```sh
python manage.py run_ocr_bridge --image-path <path-to-image>
python manage.py run_ocr_bridge --image-path <path-to-image> --asset-type slide_image --slide-order 0
python manage.py run_ocr_bridge --image-path <path-to-image> --moderate-text --project-id <project_id>
```

Video frame sampling:

```sh
python manage.py sample_video_frames --video-path <path-to-video> --output-dir <path-to-frames>
python manage.py sample_video_frames --video-path <path-to-video> --output-dir <path-to-frames> --every-seconds 5 --max-frames 10
python manage.py sample_video_frames --video-path <path-to-video> --output-dir <path-to-frames> --moderate --project-id <project_id>
```

Current visual/OCR/video commands are manual and report-only by default. They do not perform real unsafe-image classification unless the optional visual safety provider is enabled/configured, and they do not perform real OCR unless Azure OCR is enabled/configured.
