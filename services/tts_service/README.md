# TTS Service

FastAPI microservice for AI_ACADEMY narration audio.

## Runtime Flow

`POST /synthesize` accepts JSON and returns metadata with an `audio_url`.

Synthesis order:

1. XTTS v2 voice cloning when `XTTS_ENABLED=true` and a `voice_id` is provided.
2. gTTS fallback when XTTS is unavailable, disabled, missing a voice reference, or fails.
3. ffmpeg silent fallback when both speech providers fail.

The service keeps the pipeline moving by returning a valid audio response for normal provider failures. Only a failure in the final ffmpeg fallback is returned as HTTP 500.

Generated files are written under `TTS_AUDIO_DIR` and served by `GET /audio/{filename}`. `TTS_SERVICE_URL` is used to build the returned URL that the worker downloads.

## XTTS Runtime Recovery

Phase TTS-H1 adds resilience around XTTS model load and synthesis startup without changing the normal provider order. Likely transient load/network/runtime errors, such as timeouts, remote disconnects, SSL EOFs, cached model load failures, and recoverable CUDA assert messages, reset the cached XTTS model state and retry XTTS before falling through to gTTS. Permanent errors, such as a missing reference voice or invalid request/configuration, do not loop through repeated retries.

If XTTS recovers, the response still reports `provider: "xtts_v2"` and `fallback_used: false`. If XTTS remains unavailable and gTTS or silent fallback is used, response metadata includes concise fields such as `fallback_reason`, `xtts_error_transient`, `xtts_attempts`, `xtts_recovery_attempts`, and `xtts_failure_reason`.

This phase is runtime hardening only. Deterministic acronym/pronunciation resolution and Studio unknown-term display are implemented separately in TTS-D1A/D1B. Optional Llama/Ollama pronunciation suggestions have TTS-L1A backend support, TTS-L1B Studio UI, and TTS-L1C setup docs in the Django API/Studio flow and are not part of this TTS service render path.

## Voice References

XTTS v2 expects a reference WAV at:

```text
{STORAGE_ROOT}/voices/{voice_id}.wav
```

If that file is missing, the service logs the XTTS failure and falls back to gTTS.

## Preprocessing

XTTS v2 no longer receives raw slide or transcript text directly. Text is prepared by `services/tts_service/tts_preprocess` before synthesis.

The public entry point is:

```python
from tts_preprocess import prepare_text_for_tts

prepared = prepare_text_for_tts(raw_text, language="en")  # or language="tr"
```

`TTSPreparedText` includes:

- `raw_text`
- `normalized_text`
- `spoken_text`
- `chunks`
- `warnings`
- `chunk_pause_ms`
- `unknown_terms`
- `ambiguous_terms`

Studio preview displays `unknown_terms` and `ambiguous_terms` as helper warnings. Teachers can add a detected term to draft project override rows, then save and preview again. This affects spoken TTS only; captions and transcript text remain original.

### TTS-L1 LLM Suggestions

TTS-L1 is implemented as optional Studio-assisted suggestion support for D1 `unknown_terms` and `ambiguous_terms`. The endpoint lives in the Django API at `POST /api/v1/tts/pronunciation-suggestions/`, not in this FastAPI TTS service. It must not change `/synthesize`, XTTS recovery, deterministic resolver behavior, captions, subtitles, worker queues, or render/rerender execution.

Implemented behavior:

- Studio should ask for suggestions only after preview identifies unknown or ambiguous terms.
- The backend suggestion endpoint sends selected terms plus bounded context to an optional provider such as local Ollama.
- Suggestions include a spoken form, category, confidence, and reason.
- Teachers accept, edit, or ignore suggestions.
- Accepted suggestions become normal project override rows.
- Rerender uses saved deterministic overrides and never calls the LLM provider.
- Disabled mode is the default. Manual overrides and preview continue to work when suggestions are disabled or fail open.

Local Ollama setup is optional:

1. Install and start Ollama on the host.
2. Pull the default model: `ollama pull llama3.1:8b`.
3. For Docker Desktop on Windows, set `OLLAMA_BASE_URL=http://host.docker.internal:11434`.
4. Enable the Django API suggestion endpoint explicitly:

```text
TTS_LLM_SUGGESTIONS_ENABLED=true
TTS_LLM_PROVIDER=ollama
OLLAMA_PRONUNCIATION_MODEL=llama3.1:8b
TTS_LLM_SUGGESTION_TIMEOUT_SECONDS=8
TTS_LLM_MAX_TERMS=20
TTS_LLM_CONTEXT_MAX_CHARS=1000
```

Troubleshooting is fail-open by design: disabled mode returns a disabled message, provider timeout or malformed provider output returns a readable warning, no suggestions returned still leaves manual override rows available, and rerender uses only saved deterministic overrides.

### Language-Aware Preprocessing

`prepare_text_for_tts()` accepts a `language` argument and routes through separate normalization pipelines:

| Tag(s) | Pipeline | Glossary key |
|---|---|---|
| `en`, `en-US`, anything not `tr` | English (default) | `"en"` |
| `tr`, `tr-TR`, `tr_TR` | Turkish-safe | `"tr"` |

**English pipeline**:
- Abbreviations: `Dr.` → Doctor, `e.g.` → for example, `Prof.` → Professor…
- Numbers/symbols: `10%` → ten percent, `$10` → ten dollars, `3.5` → three point five, `5GB` → five gigabytes, `v2` → version two.
- Glossary: applies English product and phrase entries.
- Deterministic resolver: applies cached acronym pronunciations such as `JSON` → jay son and `PPTX` → power point file.

**Turkish pipeline** — conservative and deterministic:
- No English abbreviation expansion is applied.
- Numbers/symbols use Turkish word forms:
  - `%10` or `10%` → `yüzde on`
  - `2026` → `iki bin yirmi altı`
  - `3.5` → `üç nokta beş`
  - `5GB` → `beş gigabayt`
  - `3-4 dakika` → `üç ila dört dakika`
  - `v2` → `versiyon iki`
  - `₺10` / `10₺` → `on lira`
  - `$10` / `10$` → `on dolar`
- Supports integers 0–999 999; complex cases (millions, ordinals) are left unchanged.
- Turkish abbreviations (`vb.`, `vs.`, `Dr.`, `Prof.`, `Doç.`, `Sn.`, `örn.`) are protected from bad sentence splitting but **not** expanded into full words.
- Turkish Unicode characters (İ ç ğ ı ö ş ü) pass through NFKC-normalised but otherwise untouched.
- The deterministic resolver applies Turkish acronym pronunciations, leaves small curated Turkish known words untouched, applies curated English technical fallbacks, and reports suspicious unknown or ambiguous terms without blocking render.

The worker client also prepares text before calling `/synthesize` and sends `already_prepared=true`, `chunks`, and `chunk_pause_ms`. The service still defensively prepares raw requests and re-splits oversized chunks for XTTS language limits.

> **Limitation:** Turkish normalization is deterministic and does not use morphological analysis. Ambiguous constructs (e.g. numbers in proper names, ordinal suffixes) may be left un-expanded or converted imperfectly. Review edge cases in your content.

## Glossary

Default glossary file:

```text
services/tts_service/tts_preprocess/glossary.json
```

Inside Docker containers the same file is at `/app/tts_preprocess/glossary.json`.

Use the container path for `TTS_GLOSSARY_PATH` in `infra/.env`. For local Python runs, leave `TTS_GLOSSARY_PATH` unset to use the package default.

Terms are applied longest-first, outside URLs and obvious file paths. Matching is case-insensitive where safe.

Acronym ownership moved out of `glossary.json` in Phase TTS-D1A. Acronyms now live in `acronym_pronunciations.json` and are applied by `deterministic_resolver.py` after glossary processing. Manual/project overrides still win first; global glossary handles product and phrase terms; deterministic resolver owns acronym expansion.

### Bilingual Glossary Format

The glossary now uses **language-keyed sections**. The `"en"` sub-object holds English entries; `"tr"` holds Turkish entries. Both are applied independently.

```json
{
  "en": {
    "GitHub": "git hub",
    "LivePortrait": "live portrait"
  },
  "tr": {
    "ChatGPT": "Çet Ci Pi Ti",
    "Claude Code": "Klod Kod"
  }
}
```

The old flat `{"term": "spoken"}` format is still accepted and treated as English (backward compatible).

To add a pronunciation for both languages:

```json
{
  "en": { "NewTerm": "new term spoken" },
  "tr": { "NewTerm": "yeni terim" }
}
```

Keep replacement values as literal spoken text for the TTS model.

## Environment

| Variable | Default | Purpose |
|---|---:|---|
| `TTS_SERVICE_URL` | `http://tts_service:8001` | Base URL used in returned `audio_url` values |
| `TTS_AUDIO_DIR` | `storage_local/tts` | Directory for generated MP3 files |
| `STORAGE_ROOT` | `storage_local` | Root for voice references and storage |
| `TTS_FALLBACK_DURATION` | `3.0` | Silent fallback duration in seconds |
| `XTTS_ENABLED` | `1` | Enable XTTS v2 attempts |
| `XTTS_PRELOAD_ON_STARTUP` | `1` | Preload XTTS on startup |
| `XTTS_WARMUP_BLOCKING` | `0` | Block startup until XTTS warmup completes |
| `XTTS_LOAD_RECOVERY_ATTEMPTS` | `2` | Extra XTTS retries after likely transient model-load/network/runtime failures |
| `XTTS_LOAD_RECOVERY_BACKOFF_SEC` | `2.0` | Sleep between XTTS recovery retries |
| `TTS_PREPROCESSING_ENABLED` | `true` | Enable glossary and number/symbol preprocessing |
| `TTS_MAX_CHARS_PER_CHUNK` | `500` | Default hard chunk limit before provider-specific limits |
| `TTS_TARGET_CHARS_PER_CHUNK` | `280` | Preferred chunk size |
| `TTS_SENTENCE_PAUSE_MS` | `250` | Default pause between sentence chunks |
| `TTS_PARAGRAPH_PAUSE_MS` | `450` | Default pause between paragraph chunks |
| `TTS_SLIDE_PAUSE_MS` | `700` | Reserved slide-level pause default |
| `TTS_GLOSSARY_PATH` | package default, `/app/tts_preprocess/glossary.json` in compose | Optional glossary override path |
| `TTS_LLM_SUGGESTIONS_ENABLED` | `false` | Optional Django API/Studio pronunciation suggestions; not used by `/synthesize` |
| `TTS_LLM_PROVIDER` | `ollama` | Suggestion provider when explicitly enabled |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint; use `http://host.docker.internal:11434` from Docker Desktop on Windows |
| `OLLAMA_PRONUNCIATION_MODEL` | `llama3.1:8b` | Local model used for Studio suggestion cards |
| `TTS_LLM_SUGGESTION_TIMEOUT_SECONDS` | `8` | Short timeout for interactive Studio suggestions |
| `TTS_LLM_MAX_TERMS` | `20` | Max selected unknown/ambiguous terms per request |
| `TTS_LLM_CONTEXT_MAX_CHARS` | `1000` | Max bounded preview context sent to the provider |

`HF_HUB_DISABLE_SSL_VERIFY` is intentionally not enabled by default. Prefer a populated local model cache. Use SSL verification bypass only as an emergency local/dev workaround.

## Preview Endpoint (Phase 1)

The `/normalization/preview` route provides fail-open text normalization checks without audio synthesis.

**Request:**

```json
POST /normalization/preview

{
  "text": "ChatGPT AI pipeline anlatımı",
  "language": "tr",
  "normalization_enabled": true,
  "normalization_mode": "loose",
  "unknown_word_strategy": "keep",
  "technical_overrides": {"pipeline": "payplayn"},
  "abbreviation_overrides": {"AI": "ey ay"},
  "mixed_word_overrides": {"ChatGPT": "chat gpt"}
}
```

**Response:**

```json
{
  "original_text": "ChatGPT AI pipeline anlatımı",
  "normalized_text": "chat gpt ey ay payplayn anlatımı",
  "spoken_text": "chat gpt ey ay payplayn anlatımı",
  "chunks": ["chat gpt ey ay payplayn anlatımı"],
  "chunk_pause_ms": [0],
  "tts_normalization_language": "tr",
  "tts_normalization_rules_applied": [
    {"rule": "override", "term": "ChatGPT", "replacement": "chat gpt", "source": "preview_pre_override"},
    {"rule": "override", "term": "AI", "replacement": "ey ay", "source": "preview_pre_override"},
    {"rule": "override", "term": "pipeline", "replacement": "payplayn", "source": "preview_pre_override"}
  ],
  "unknown_terms": [],
  "ambiguous_terms": [],
  "normalization_enabled": true,
  "normalization_mode": "loose",
  "unknown_word_strategy": "keep",
  "applied_overrides": {
    "technical_overrides": {"pipeline": "payplayn"},
    "abbreviation_overrides": {"AI": "ey ay"},
    "mixed_word_overrides": {"ChatGPT": "chat gpt"},
    "merged_override_count": 3
  },
  "warnings": [],
  "error": null,
  "fallback_used": false
}
```

**Key Behaviors:**

- **No Audio Synthesis**: This endpoint returns only text metadata; it never synthesizes audio or calls XTTS/gTTS.
- **Override Protection**: Manual overrides are protected from re-normalization using placeholder token masking. Example: `"ChatGPT" → "chat gpt"` stays as `"chat gpt"` in `spoken_text`, not re-normalized to `"chat Ci Pi Ti"` by the glossary.
- **Override Priority**: Applied in order: `mixed_word_overrides` > `abbreviation_overrides` > `technical_overrides` > default glossary/normalization.
- **Request-Local Only**: Overrides are not persistent; they do not modify `glossary.json`.
- **Fail-Open**: Network or service errors do not raise; the endpoint returns fallback metadata with `fallback_used: true`.
- **Language Support**: `language: "en"` (English) or `language: "tr"` (Turkish); routes through language-specific preprocessing.

**Integration:**

- Django API proxies preview requests through `POST /api/v1/tts/preview/`.
- Frontend can preview normalized text before generating audio, confirming pronunciations and override behavior.
- Worker does not use preview; worker uses `/synthesize` directly after text preparation.

## API Example

```bash
curl -s -X POST http://localhost:8001/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text":"Upload PPTX and generate XTTS v2 audio.","voice_id":"default","language":"en"}' \
  | python -m json.tool
```

Typical response:

```json
{
  "audio_url": "http://tts_service:8001/audio/<uuid>.mp3",
  "duration": 2.34,
  "provider": "xtts_v2"
}
```

If XTTS fails and gTTS succeeds, `provider` is `gTTS`. If all speech providers fail, `provider` is `fallback`.

## Health

```bash
curl http://localhost:8001/health
curl http://localhost:8001/ready
```

`/ready` returns HTTP 503 while XTTS is enabled but not ready.

## Tests

Focused tests:

```bash
python -m pytest \
  tests/integration/test_tts_text_normalization.py \
  tests/integration/test_tts_client_readiness.py \
  tests/integration/test_tts_service_readiness.py \
  tests/integration/test_tts_service_text_quality.py \
  tests/integration/test_text_segmentation.py \
  tests/integration/test_transcript_editor_pipeline.py \
  -q -rs
```

The local environment must include the shared test dependencies and the TTS service dependencies for the FastAPI-gated tests:

```bash
python -m pip install -r requirements-test.txt fastapi "pydantic>=2.0.0"
```

Docker verification from the repo root uses the compose file under `infra/`. The local compose build installs `requirements-test.txt` by default (`INSTALL_TEST_DEPS=1`) and mounts `tests/` plus `services/` into the service containers. Direct Dockerfile builds still default to `INSTALL_TEST_DEPS=0`.
Worker pytest runs clear `POSTGRES_HOST` so Django uses SQLite, and compose points the pytest database at `/tmp/ai_academy_worker_test.sqlite3` to avoid writing into the read-only mounted source tree.

```bash
docker compose -f infra/docker-compose.yml build tts_service worker
docker compose -f infra/docker-compose.yml run --rm --no-deps tts_service python -m pytest tests/integration/test_tts_service_readiness.py tests/integration/test_tts_service_text_quality.py -q -rs
docker compose -f infra/docker-compose.yml run --rm --no-deps -e POSTGRES_HOST= worker python -m pytest tests/integration/test_tts_client_readiness.py tests/integration/test_tts_text_normalization.py tests/integration/test_text_segmentation.py tests/integration/test_transcript_editor_pipeline.py -q -rs
```

Syntax check:

```bash
python -m py_compile services/tts_service/main.py services/scripts/tts_client.py services/tts_service/tts_preprocess/*.py
```
