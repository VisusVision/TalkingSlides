# Studio Rerender Worker Diagnosis

## Status Snapshot

- Worker containers are split by queue profile.
- `worker` consumes the lightweight render queue `render`.
- `worker-avatar` consumes the heavy avatar queue `avatar`.
- Worker is connected to Redis broker `redis://redis:6379/0` and result backend `redis://redis:6379/1`.
- Worker and API both use PostgreSQL `academy_db`.
- Only `worker-avatar` runs MuseTalk/LivePortrait bootstrap on startup.
- `worker` skips avatar bootstrap so non-avatar render jobs can start without GPU/avatar runtime readiness.

## Original Root Cause

Studio rerender jobs are not blocked by Celery transport or startup failure. They are waiting behind a single-concurrency worker that is already busy running a long avatar render pipeline. The worker process is configured with `--concurrency=1` and `--prefetch-multiplier=1`, so only one job is processed at a time.

The active worker logs show one long-running `synthesize_and_render_slide` task for project `129`, plus a reserved task waiting behind it. The same logs show MuseTalk/LivePortrait work taking many minutes inside the task, so queued rerenders stay pending until the active job finishes.

## Implemented Queue Split

- Normal upload, project rerender, transcript-triggered rerender, and structural transcript rerender jobs route to `CELERY_RENDER_QUEUE=render` when avatar is not enabled for the job.
- Avatar-enabled lesson render jobs route to `CELERY_AVATAR_QUEUE=avatar`.
- Avatar preview jobs also route to `avatar`.
- The worker pipeline keeps child slide-render tasks, finalization callbacks, and errbacks on the same selected queue as the parent render task.
- The legacy `celery` queue remains declared in Celery config for compatibility if an operator needs to consume old messages, but the API now prefers explicit `render` or `avatar` routing.

## Evidence

- `docker compose ps` should show both `infra-worker-1` and `infra-worker-avatar-1` after the split is started.
- `ps aux` inside `worker-avatar` should show one Celery main process, one Celery child process, and the MuseTalk bootstrap/service process.
- `ps aux` inside `worker` should show Celery without the avatar bootstrap/service process.
- Worker environment shows:
  - `CELERY_BROKER_URL=redis://redis:6379/0`
  - `CELERY_RESULT_BACKEND=redis://redis:6379/1`
  - `CELERY_RENDER_QUEUE=render`
  - `CELERY_AVATAR_QUEUE=avatar`
  - `worker`: `CELERY_WORKER_QUEUES=render`, `AVATAR_BOOTSTRAP_ON_WORKER_STARTUP=0`
  - `worker-avatar`: `CELERY_WORKER_QUEUES=avatar`, `AVATAR_BOOTSTRAP_ON_WORKER_STARTUP=1`
  - `PYTHONPATH=/app/api:/app:/app/services/scripts:/app/services/tts_service:/app/services`
- API and worker both report PostgreSQL `django.db.backends.postgresql` with database `academy_db`.
- Celery inspect output shows one active task and one reserved task on `celery@...`.
- Recent DB job states show `pending`, `running`, `done`, and `failed` jobs coexisting; the newest rerender-style job rows are still `pending` while the worker is occupied.
- Previous worker logs showed `process_pptx_to_video` tasks being received and then entering long avatar stages.
- Worker logs also show a separate failure mode on some projects: `PermissionError: [Errno 13] Permission denied: '/app/storage_local/<project_id>'`.

## Answers To The Diagnostic Questions

- Is the worker container running? Yes.
- If not running, why did it exit? Not applicable.
- Is worker blocked by avatar/MuseTalk/LivePortrait bootstrap? The render worker does not run avatar bootstrap. The avatar worker still runs bootstrap and should reach ready before consuming `avatar`.
- Is worker connected to the same Redis broker as API? Yes.
- Is API enqueueing jobs into a queue the worker consumes? Yes. Non-avatar jobs use `render`; avatar jobs use `avatar`.
- Is worker using the same DB as API? Yes.
- Is worker using old code/image? No evidence of that. The worker uses the mounted source tree and current config values.
- Are migrations applied where worker expects them? Yes. `core` migrations are applied in both API and worker containers.
- Are jobs queued in DB but not sent to Celery, or sent to Celery but not consumed? They are being sent to Celery. The previous bottleneck was a single shared worker slot; routing now separates non-avatar and avatar lanes.
- Is there an import/path error in worker startup? No import/path startup error is visible in the current logs.
- Is there an FFmpeg/LibreOffice/storage error after the task starts? Yes, there is at least one storage permission failure on `/app/storage_local/<project_id>` and a LibreOffice shared-library warning on DOCX export, but those are secondary to the queue stall.

## Fastest Non-Build Recovery

- With the split compose profile, restart the worker services so `worker` begins consuming `render` and `worker-avatar` begins consuming `avatar`.
- If a specific job is hung, a worker restart can clear the stuck process, but it will also interrupt the active render.
- Do not simply increase avatar concurrency. MuseTalk/LivePortrait work is GPU-bound and already serialized by runtime assumptions; more concurrency can increase contention without improving throughput.

## Recommended Structural Fix

- Keep the current canonical avatar chain intact.
- Split workloads by profile instead of by feature:
  - a lightweight queue for API/catalog/rerender orchestration and non-avatar work,
  - a heavy queue for avatar rendering and MuseTalk/LivePortrait processing.
- Let the heavy worker own the GPU-bound render tasks, and keep Studio rerender orchestration off the same single-slot worker lane.

## Recommendation

- The immediate issue was queue starvation from a single heavy worker, not a missing Celery connection.
- The lightweight non-avatar worker profile is now the default path for Studio rerender responsiveness.
- The storage permission failure should be tracked separately because it can make individual render jobs fail even after they are dequeued.
