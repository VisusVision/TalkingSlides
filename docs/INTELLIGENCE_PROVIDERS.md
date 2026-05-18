# Intelligence Providers

Lesson Intelligence and Analytics Intelligence use the same production-safety policy:

- `heuristic` is the default and guaranteed fallback.
- `ollama` is optional and local-only.
- paid/external provider placeholders stay disabled unless a future branch implements and gates them.

## Synchronous Timeout Safety

The API endpoints run analysis synchronously in v1. To prevent a slow local model from killing the API worker, the effective Ollama timeout is:

```text
min(*_INTELLIGENCE_TIMEOUT_SECONDS, *_INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS)
```

If a specific cap is not set, `INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS` is used. The default cap is `20` seconds.

Docker currently starts Gunicorn without an explicit `--timeout`, so Gunicorn's default worker timeout is 30 seconds. Keep synchronous provider caps below the API worker timeout. If local LLM analysis needs longer than that, move the analysis to a background job with polling instead of raising the synchronous cap.

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
