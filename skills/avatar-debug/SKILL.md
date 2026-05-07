---
name: avatar-debug
description: Debug VISUS VidLab avatar failures by tracing input readiness, canonicalization, LivePortrait, MuseTalk, and preview output preservation.
---

# VISUS VidLab avatar debugging

Use this skill when preview fails, output is empty, LivePortrait is unstable, MuseTalk times out, or the API/UI returns a misleading avatar status.

## Debug order
1. Check readiness
2. Check source selection
3. Check canonical input
4. Check TTS audio
5. Check LivePortrait output
6. Check MuseTalk output
7. Check final validation
8. Check UI preview path

## What to inspect first
- Missing avatar prep state
- Missing preview audio
- Empty or stale preview path
- Wrong engine selection
- Over-shrunk canonical input
- LivePortrait instability
- MuseTalk timeout
- API status mapping errors

## Debug output requirements
Every render should log:
- original source path
- selected source key
- normalized input path
- TTS output path
- LivePortrait output path
- MuseTalk output path
- final preview path
- warning or failure reason

## Failure handling
- Distinguish setup_not_prepared from render_failed.
- Preserve playable preview output on warning-mode runs.
- Treat strict final validation as the real acceptance gate.
- Do not hide a playable mp4 behind an empty UI state.

## Refactor guidance
When the avatar system is messy:
- simplify the pipeline
- remove duplicated fallback logic
- keep only one canonical input path
- keep only one clear engine handoff path
- preserve installed engine binaries and model weights