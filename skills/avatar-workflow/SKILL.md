---
name: avatar-workflow
description: Build and maintain the VISUS VidLab avatar pipeline using the canonical flow TTS -> LivePortrait -> MuseTalk -> optional restoration.
---

# VISUS VidLab avatar workflow

Use this skill when working on avatar generation, preview, readiness, input handling, engine orchestration, or playback.

## Canonical flow
Always preserve this pipeline:

1. TTS generates audio
2. LivePortrait generates the motion base
3. MuseTalk applies lip sync
4. Optional restoration runs last
5. Final strict validation decides acceptance

## Hard rules
- Do not delete installed engine modules, weights, or runtime assets.
- Do not weaken strict validation.
- Do not add hidden fallback chains.
- Do not silently switch engines.
- Do not break preview if a playable mp4 exists.
- Do not let preview and lesson use different input logic.
- Keep the UI status clear: ready, warning, failed.

## Input handling
- Use one canonical input core.
- Prefer readable, frontal, centered sources.
- Keep full head visible.
- Avoid over-shrinking the face.
- Allow best-effort preview on borderline-but-readable inputs.
- Hard-fail only on missing, unreadable, corrupt, or face-less inputs.

## Engine handoff
- Pass explicit normalized input paths to engines.
- Log source path, normalized path, command, output path, and status.
- Preserve playable outputs and warning metadata.

## Output handling
- If a real mp4 exists, preserve it.
- If a preview is unstable, return a warning with the playable path.
- Do not return an empty preview when a playable file exists.

## Testing and validation
- Prefer small, targeted changes.
- Verify preview first.
- Keep lesson rendering strict and unchanged unless explicitly asked.
- Add or update tests for readiness, engine handoff, and preview status.