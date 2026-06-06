# Subtitle Translation Phase 4 Generation

## Scope

Phase 4 adds backend generation for translated subtitle sidecar files. It does
not add a frontend language selector and does not call real external providers.

Implemented:

- `generate_translated_subtitle_track(...)` synchronous generation helper.
- `POST /api/v1/projects/<id>/subtitle-tracks/` for owner/staff generation when enabled.
- Mock-provider translated `.srt` and `.vtt` files at `{project_id}/subtitles/{lang}.srt` and `{project_id}/subtitles/{lang}.vtt`.
- `TranslatedSubtitleTrack` status transitions from `processing` to `ready` or `failed`.
- Tokenized ready-track `srt_url` and `vtt_url` metadata.

Not implemented:

- No real provider integration.
- No queue-backed translation jobs.
- No frontend language selector.
- No render/rerender mutation.
- No transcript mutation.
- No TTS normalization changes.

## Source Cues

Generation uses canonical original display-caption cues from active
`TranscriptPage` rows. Source priority mirrors original caption generation:

1. Valid `chunk_timeline` with display text mapped from `subtitle_chunks` when available.
2. Distributed `subtitle_chunks`.
3. `narration_text` as the display-text page fallback.

TTS spoken text, phonetic overrides, glossary pronunciation output, and
provider-normalized TTS text are never translation inputs.

## Provider Contract

The Phase 4 mock provider only changes cue text, for example:

```json
{"text": "[en] Original display text"}
```

Cue timing and identity are preserved:

- `page_key`
- `chunk_index`
- `start`
- `end`
- cue count
- cue order

Provider output is validated before files are written.

## Security

Generated sidecar paths are stored as relative metadata on
`TranslatedSubtitleTrack`. API responses do not expose raw paths. Ready tracks
are served through `/api/v1/stream/<token>/` with the same draft/public access
rules as original captions.

## Phase 5 TODO

- Add a frontend subtitle language selector.
- Fetch track metadata and list original plus ready translated tracks.
- Switch the active native WebVTT track without custom rendering.
- Keep original CC on/off behavior intact.
