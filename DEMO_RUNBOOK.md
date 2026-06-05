# VISUS VidLab Demo Runbook

This runbook is a draft for a local live demo from `developer` after the moderation hardening merge.

## 1. Startup

Use Windows PowerShell from the repo root:

```powershell
.\scripts\windows-dev-setup.ps1 -CheckOnly
.\scripts\windows-dev-start.ps1 -WithTts -WithWorker
```

Use `-WithAvatar` only when the GPU/avatar stack has already been warmed and verified on the demo machine.

Expected local URLs:

- Frontend: `http://localhost:3000`
- API: `http://localhost:8000`
- API readiness: `http://localhost:8000/api/v1/ready/`
- TTS: `http://localhost:8001`
- MinIO console: `http://localhost:9001`

Expected services for the core demo:

- `postgres`
- `redis`
- `minio`
- `api`
- `frontend`
- `worker`
- `tts_service`

Avatar worker is optional for the core demo.

## 2. Environment

Required for local demo:

```env
STORAGE_BACKEND=filesystem
STORAGE_ROOT=/app/storage_local
DATABASE_URL=<local docker postgres url>
REDIS_URL=<local docker redis url>
TTS_SERVICE_URL=http://tts_service:8001
```

`filesystem` is the canonical local backend. Older local `.env` files may still use `STORAGE_BACKEND=local`; the app treats that as a compatibility alias for `filesystem`, but new demo setup should use `filesystem`.

Optional for moderation demo:

```env
AZURE_CONTENT_SAFETY_ENABLED=true
AZURE_CONTENT_SAFETY_ENDPOINT=<azure content safety endpoint>
AZURE_CONTENT_SAFETY_KEY=<azure content safety key>
AZURE_OCR_ENABLED=true
AZURE_OCR_ENDPOINT=<azure document intelligence endpoint>
AZURE_OCR_KEY=<azure document intelligence key>
```

Do not commit real keys. Keep `.env`, `storage_local/`, `media/`, `scratch/`, screenshots, and generated reports out of git.

If Azure visual moderation is not configured or is unreliable, use a local-only scratch override for the render portion:

```yaml
services:
  api:
    environment:
      STORAGE_BACKEND: "filesystem"
      ENABLE_VISUAL_MODERATION: "0"
      VISUAL_SAFETY_PROVIDER: "none"
      VISUAL_SAFETY_CLASSIFIER_ENABLED: "false"
      AZURE_CONTENT_SAFETY_ENABLED: "false"
      AZURE_CONTENT_SAFETY_ENDPOINT: ""
      AZURE_CONTENT_SAFETY_KEY: ""
      AZURE_OCR_ENDPOINT: ""
      AZURE_OCR_KEY: ""
      AZURE_OCR_ENABLED: "0"
      OCR_MODERATION_AUTO_ENABLED: "false"
      VIDEO_FRAME_AUDIT_AUTO_ENABLED: "false"
  worker:
    environment:
      STORAGE_BACKEND: "filesystem"
      ENABLE_VISUAL_MODERATION: "0"
      VISUAL_SAFETY_PROVIDER: "none"
      VISUAL_SAFETY_CLASSIFIER_ENABLED: "false"
      AZURE_CONTENT_SAFETY_ENABLED: "false"
      AZURE_CONTENT_SAFETY_ENDPOINT: ""
      AZURE_CONTENT_SAFETY_KEY: ""
      AZURE_OCR_ENDPOINT: ""
      AZURE_OCR_KEY: ""
      AZURE_OCR_ENABLED: "0"
      OCR_MODERATION_AUTO_ENABLED: "false"
      VIDEO_FRAME_AUDIT_AUTO_ENABLED: "false"
  tts_service:
    environment:
      XTTS_ENABLED: "0"
      XTTS_PRELOAD_ON_STARTUP: "0"
      XTTS_WARMUP_BLOCKING: "0"
```

Start with the override only for local rehearsal:

```powershell
docker compose -f infra\docker-compose.yml -f scratch\demo-rehearsal\docker-compose.demo.override.yml up -d api worker tts_service
```

## 3. Seed Demo Data

Run inside Docker so the data goes into the Docker Postgres database:

```powershell
docker compose -f infra\docker-compose.yml exec -T api python manage.py seed_demo_data --reset-demo --with-moderation-fixtures --with-analytics-activity
```

Seeded password:

```text
visus-demo-local
```

Useful accounts:

- Publisher: `jane.doe.demo@example.com`
- Staff/admin: `demo.staff@example.com`
- Teacher: `demo.tech.teacher@example.com`
- Student: `demo.student.active@example.com`
- Public visitor: no login

Seeded lessons are useful for catalog, moderation, and analytics context. They may use placeholder media, so create or preserve one real rendered lesson before the live demo.

If migrations fail before seeding, do not run destructive cleanup blindly. See the troubleshooting section below.

## 4. Demo Assets

Prepare these outside git:

- Safe lesson text or PPTX with 2 to 3 slides.
- Safe cover image.
- Safe background or whiteboard image.
- Safe script text.
- Optional moderation fixture text for bad-text demo.
- Optional unsafe/provider-unavailable visual fixture.
- Optional known-good avatar portrait.
- Optional known-good voice sample.
- Optional pre-rendered avatar overlay output.

Do not use real student data, private faces, private voices, real keys, or copyrighted third-party media without permission.

## 5. Core Lesson Demo Script

Target length: 5 to 7 minutes.

1. Open `http://localhost:3000`.
2. Log in as `jane.doe.demo@example.com`.
3. Open Studio.
4. Create a tiny safe lesson from text or PPTX.
5. Show transcript pages and notes.
6. Save transcript changes.
7. Rerender.
8. Publish.
9. Open Watch at `/watch?lesson=<project_id>`.
10. Play the video and show captions/study notes.
11. Open Browse or the publisher channel and confirm the published lesson appears.

Rehearsed local real lesson:

- Project id: `376`
- Title: `Demo Smoke Photosynthesis Playable`
- Render job id: `649`
- Output: `storage_local/376/376.mp4`
- Watch URL: `http://localhost:3000/watch?lesson=376`

This is a local rehearsal record and media artifact, not a committed fixture. If the Docker database or `storage_local/` is reset, recreate a small real lesson before the live demo.

## 6. Moderation Demo Script

Use staff account `demo.staff@example.com`.

Recommended safe sequence:

1. Safe text passes: show the published safe lesson moderation as approved.
2. Bad text blocks text only: rescan `Moderation Test: Offensive Language`.
3. Visual provider unavailable: show a review request where the visual safety provider could not complete and the status is `needs_admin_review`.
4. Admin review: approve one fixture and request changes on another.
5. Replacement flow: replace unsafe text with safe text, rescan, and show status returns to `approved`.
6. Public safety: confirm blocked drafts do not appear in public catalog or channel pages.

Avoid using graphic unsafe images live. Prefer seeded fixtures or provider-unavailable review states.

## 7. Avatar Demo Strategy

Default recommendation for this demo: show the Settings checklist only and skip live avatar generation.

Reason:

- Seeded demo accounts do not include a known-good portrait or voice sample.
- Live avatar generation depends on the GPU/avatar stack and strict validation.
- A preview can look acceptable while strict validation still fails.

Safer alternatives:

- Use a vetted portrait and voice sample prepared outside git.
- Use a pre-rendered avatar output from local storage.
- Show Settings readiness states and explain the required consent, portrait, voice, prepare, and preview steps.

Only attempt live avatar generation if:

- `worker-avatar` is intentionally running.
- GPU models are already downloaded and warm.
- A known-good consented portrait/audio pair is available.
- A rehearsal preview completed successfully on this machine.

## 8. Fallbacks

Startup or setup check fails:

- Run `.\scripts\windows-dev-setup.ps1 -CheckOnly`.
- Run `docker compose -f infra\docker-compose.yml config --quiet`.
- Confirm Docker Desktop is running.
- Confirm `infra\.env` exists and uses placeholder/local-only values.

Render fails:

- Use the pre-rendered project `376` if present.
- Confirm `STORAGE_BACKEND=filesystem`.
- Confirm `worker` and `tts_service` are running.
- If Azure visual moderation blocks rendering because the provider is unavailable, use the local scratch override for the render portion.

Migration fails on local Docker Postgres:

- This usually means the reused Docker Postgres volume contains migration history from an older branch.
- Inspect the current state:

```powershell
docker compose -f infra\docker-compose.yml exec -T api python manage.py showmigrations core
```

- If a migration says a column already exists, stop and decide whether the local demo database should be preserved.
- Safe reset for a disposable local demo database:

```powershell
docker compose -f infra\docker-compose.yml down
docker volume ls | findstr postgres
```

Then delete only the VISUS local Postgres volume after confirming its exact name and that no useful local data is needed:

```powershell
docker volume rm <visus_postgres_volume_name>
```

Restart and seed again:

```powershell
.\scripts\windows-dev-start.ps1 -WithTts -WithWorker
docker compose -f infra\docker-compose.yml exec -T api python manage.py seed_demo_data --reset-demo --with-moderation-fixtures --with-analytics-activity
```

Do not use `down -v` unless you intend to delete every Compose volume for this project.

TTS fails:

- Use a pre-rendered lesson.
- Keep the demo on playback, Studio editing, and moderation review.

Azure fails:

- Do not enter real keys live.
- Show provider-unavailable admin review behavior.
- Explain that missing provider does not equal unsafe content.

Avatar fails:

- Do not debug live.
- Switch to Settings checklist or pre-rendered output.

Browser playback fails:

- Verify signed stream URL through the API.
- Fall back to the local MP4 path only for internal rehearsal, not public demo messaging.
- If browser automation fails but the app opens manually, continue the live demo manually and record browser QA as a rehearsal gap.
- If the app route itself fails, verify frontend route delivery:

```powershell
curl.exe -L -s -o NUL -w "%{http_code} %{content_type}" http://localhost:3000/watch?lesson=376
```

## 9. Cleanup

After the demo, stop the local stack if needed:

```powershell
docker compose -f infra\docker-compose.yml down
```

Optional local-only cleanup:

```powershell
rmdir /s /q scratch
```

Do not delete `storage_local/` or the Docker database until you no longer need the demo media. Do not commit generated media, browser profiles, screenshots, or scratch files.

Before committing the runbook:

```powershell
git status --short
git diff -- DEMO_RUNBOOK.md
```

Never stage:

- `.env`
- `services/api/db.sqlite3`
- `storage_local/`
- `media/`
- `scratch/`
- `screenshots/`
- browser profiles
- generated reports
- real keys
