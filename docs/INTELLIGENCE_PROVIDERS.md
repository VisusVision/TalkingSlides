# Intelligence Providers

Lesson Intelligence and Analytics Intelligence use the same production-safety policy:

- `heuristic` is the default and guaranteed fallback.
- `ollama` is optional and local-only.
- paid/external provider placeholders stay disabled unless a future branch implements and gates them.

## Progressive Ollama Enhancement

`POST /intelligence/analyze/` returns a heuristic report immediately. When the provider chain contains `ollama`, the API stores the heuristic report with `metadata.progressive_enhancement.status=pending`, queues a Celery enhancement task, and the frontend polls `GET /intelligence/` until the enhancement finishes.

When the background Ollama call succeeds, the same report row is updated to `provider=ollama`, `fallback_used=false`, and `metadata.progressive_enhancement.status=done`. If Ollama fails or a queued task becomes stale, the heuristic report stays visible and the enhancement status becomes `failed` with a sanitized error reason.

Large lessons and creator analytics payloads are processed as chunked Ollama workloads. Each chunk receives a bounded timeout, failed chunks fall back to heuristic coverage, and successful chunks are synthesized into the normal report shape. Progress is recorded in `metadata.progressive_enhancement` with `phase`, `chunk_count`, `completed_chunks`, `failed_chunks`, and `partial_enhancement` so the UI can show chunk progress without re-posting analysis.

Background dedupe uses a formal run key that includes intelligence kind, owner/project, source hash, provider, Ollama model, output language, prompt/schema version, and analytics filters. The same unchanged run is not queued twice, but changing model, output language, prompt version, source data, or using force re-analysis creates a new run.

The queue is controlled by `INTELLIGENCE_CELERY_QUEUE`. The default follows the render queue (`render` in the local compose setup), so progressive intelligence is consumed by the current worker without hidden configuration. If you route intelligence to a dedicated queue, start a worker that consumes that queue.

For production, prefer a dedicated low-priority worker so local LLM analysis cannot sit ahead of render, TTS, or avatar work:

```text
INTELLIGENCE_CELERY_QUEUE=intelligence
CELERY_WORKER_QUEUES=intelligence
# start this worker with --concurrency=1
```

Run that worker with concurrency `1` unless the Ollama host has enough CPU/GPU capacity for parallel prompts. Keep render/avatar workers on their existing queues.

This avoids:

- browser `Failed to fetch` errors from long synchronous requests
- Gunicorn worker timeouts
- repeated POST analyze calls while enhancement is pending
- any transcript edit, autosave, or rerender side effect

For local background quality, `qwen2.5:7b` is a good default:

```text
LESSON_INTELLIGENCE_PROVIDER_CHAIN=ollama,heuristic
ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN=ollama,heuristic
OLLAMA_LESSON_INTELLIGENCE_MODEL=qwen2.5:7b
OLLAMA_ANALYTICS_INTELLIGENCE_MODEL=qwen2.5:7b
INTELLIGENCE_BACKGROUND_TIMEOUT_MAX_SECONDS=300
INTELLIGENCE_CELERY_QUEUE=render
```

Use smaller/faster models only if you want Ollama to fit inside synchronous limits.

Ollama scaling is local hardware bound. `qwen2.5:7b` is useful for lesson quality when the machine can handle it; for analytics, a smaller local model can be preferable if speed matters more than nuanced synthesis. Cloud or paid providers are not implemented in this branch.

## Automatic Scheduling

Lesson Intelligence is scheduled best-effort when transcript text becomes available, when transcript edits are saved, when a lesson is published, and after render/rerender completion. The early transcript-available trigger runs before render only when a separate intelligence queue is configured; local `render`-queue fallback waits for later triggers so intelligence does not sit ahead of TTS/render work. The scheduler first stores a fast heuristic report when needed, then queues Ollama enhancement if the provider chain includes `ollama`. It never auto-edits transcript text and never starts a render/rerender job.

Analytics Intelligence is scheduled from creator-facing events instead of depending on the button:

- lesson publish
- render completion for a creator lesson
- learner progress updates
- likes and unlikes
- new comments

Analytics scheduling is throttled by `ANALYTICS_INTELLIGENCE_MIN_AUTO_INTERVAL_SECONDS` and progress-event delta. The analytics payload includes recent learner comments as qualitative feedback, capped and sanitized without user IDs, usernames, or emails.

The Re-analyze buttons remain manual overrides. Frontends poll only `GET` while enhancement is pending/running; they do not repeatedly call `POST analyze`.

## Synchronous Timeout Safety

The synchronous provider path remains capped for direct provider-chain calls and for deployments that disable progressive enhancement. To prevent a slow local model from killing the API worker, the effective synchronous Ollama timeout is:

```text
min(*_INTELLIGENCE_TIMEOUT_SECONDS, *_INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS)
```

If a specific cap is not set, `INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS` is used. The default cap is `20` seconds. Background enhancement uses adaptive timeouts because it does not block Gunicorn.

Adaptive background timeout settings:

```text
INTELLIGENCE_BACKGROUND_TIMEOUT_MIN_SECONDS=60
INTELLIGENCE_BACKGROUND_TIMEOUT_MAX_SECONDS=300
INTELLIGENCE_BACKGROUND_TIMEOUT_PER_1000_CHARS=4
INTELLIGENCE_BACKGROUND_TIMEOUT_PER_PAGE_SECONDS=2
INTELLIGENCE_BACKGROUND_TIMEOUT_PER_COMMENT_SECONDS=1
```

Lesson background timeouts scale with input characters and page count. Analytics timeouts scale with input characters, lesson/category rows, and recent comment count. The timeout used is recorded in `metadata.progressive_enhancement.timeout_seconds` when available.

Chunk-level Ollama controls:

```text
INTELLIGENCE_OLLAMA_CHUNK_MAX_CHARS=6000
INTELLIGENCE_OLLAMA_CHUNK_MAX_PAGES=8
INTELLIGENCE_OLLAMA_CHUNK_MAX_ITEMS=10
INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MIN_SECONDS=45
INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MAX_SECONDS=120
INTELLIGENCE_OLLAMA_TOTAL_TIMEOUT_MAX_SECONDS=600
```

`INTELLIGENCE_OLLAMA_CHUNK_MAX_CHARS` controls how much lesson or analytics content is sent to one local Ollama request. Page/item limits keep individual chunks predictable even when text is short but arrays are large. The per-chunk timeout is bounded by the min/max values, and the whole Celery task stops adding chunks after the total budget. Partial completion still produces a terminal `done` report with `partial_enhancement=true`; full Ollama failure leaves the heuristic report in place and marks enhancement `failed`.

Pending or running enhancement metadata is considered stale after `INTELLIGENCE_ENHANCEMENT_STALE_SECONDS` seconds, default `900`. A stale enhancement is marked failed so the frontend stops polling and a later manual re-analyze can queue a new task.

Docker currently starts Gunicorn without an explicit `--timeout`, so Gunicorn's default worker timeout is 30 seconds. Keep synchronous provider caps below the API worker timeout. Long-running local LLM analysis should use the progressive background flow instead of raising the synchronous cap.

Paid/external providers are still future work and remain disabled in this branch.

## Lesson Intelligence Output

Expanded narration suggestions separate advice from applicable draft text:

```json
{
  "page_number": 2,
  "page_key": "s2-p1",
  "type": "short_narration",
  "title": "Expand narration",
  "advice": "This slide is too short and needs an example.",
  "draft_narration": "In this part, we explain ...",
  "copy_text": "In this part, we explain ...",
  "generated_by": "heuristic",
  "ai_generated": true
}
```

Studio applies only `draft_narration` or `copy_text`. It does not apply titles, headers, or advice text as transcript narration.
