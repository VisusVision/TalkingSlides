# Subtitle Translation Phase 3 Plan

## Scope

Phase 3 adds backend/data architecture for translated subtitle tracks only.

Implemented in this phase:

- `TranslatedSubtitleTrack` metadata model for per-project translated subtitle sidecars.
- Tokenized track-list metadata endpoint for original and translated tracks.
- Provider abstraction with deterministic mock provider for tests.
- Settings flags for future opt-in translation generation.

Not implemented in this phase:

- No translated subtitle file generation. Phase 4 adds mock-provider backend generation in `docs/SUBTITLE_TRANSLATION_PHASE_4_GENERATION.md`.
- No real external translation provider calls.
- No frontend language selector.
- No render/rerender changes.
- No TTS normalization changes.

## Data Model

Translated tracks are stored separately from original captions. Original captions remain tied to `Job.srt_url`, with WebVTT derived as `{project_id}/{project_id}.vtt`.

Translated tracks store metadata only:

- project/job ownership
- target language code/label
- source language code
- provider name
- status
- SRT/VTT relative paths
- cue count, error, metadata, timestamps

The model enforces one current translated track per project/language code.

## Provider Contract

Translation providers receive clean original display-caption cues:

```json
[
  {"page_key": "s1-p1", "chunk_index": 0, "start": 0.0, "end": 3.2, "text": "Original display text"}
]
```

Providers may only change `text`. They must preserve:

- cue count
- cue order
- `page_key`
- `chunk_index`
- `start`
- `end`

TTS spoken text, phonetic overrides, glossary pronunciation output, and provider-normalized TTS text are not valid translation inputs.

## Security

Track metadata never exposes raw storage paths. Ready VTT URLs are issued through the existing `/api/v1/stream/<token>/` route.

Access rules:

- published lessons can expose ready tracks publicly
- owner/staff can see draft lesson tracks
- anonymous users cannot see draft lesson tracks
- translated VTT tokens are bound to the same playback stream checks as original captions

## Phase 4 Status

Implemented separately in Phase 4:

- Generate translated SRT/VTT from canonical original display cues.
- Persist `TranslatedSubtitleTrack.status`, `cue_count`, paths, and errors.
- Keep translation disabled by default until explicitly enabled.
- Avoid transcript, render, and TTS mutation.

Still future work:

- Real provider integration behind explicit opt-in.
- Queue-backed generation if synchronous generation becomes too slow.

## Phase 5 TODO

- Add frontend language selector.
- Show original plus ready translated tracks only.
- Keep original CC on/off behavior intact.
- Avoid selecting unavailable or failed tracks.
