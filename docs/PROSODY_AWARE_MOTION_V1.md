# Prosody-Aware Motion Timing V1

## Goal

Make a trained Digital Twin react to the timing of its generated speech while
remaining practical on an RTX 4060 laptop and honest about the model boundary.

## Render flow

1. TTS generates the final speech asset.
2. `prosody-v1` decodes it to 16 kHz mono PCM.
3. Bounded 100 ms measurements produce speech activity, normalized energy,
   pauses, and emphasis events.
4. Motion Planner V2 blends those signals with requested emotion/intensity and
   assigns validated personal calm, natural, or expressive source intervals.
5. LivePortrait crossfades the intervals into an exact-duration driving video.
6. MuseTalk performs lip synchronization against the unchanged original audio.

The timeline is deterministic for the same render ID, audio, request, and
Motion Style package. Its hash participates in the LivePortrait cache key.

## Failure contract

Audio decode, invalid/non-finite values, missing personal intervals, excessive
segment counts, duration mismatch, or ffmpeg montage failure disables only the
prosody timeline. Rendering continues through the already validated Motion
Planner V2 single-window path. The exact fallback reason is observable in the
motion plan and renderer execution fields.

## Artifacts and observability

- `prosody-profile.json`: signal summary and bounded audio timeline.
- `motion-plan.json`: emotion bias, selected personal intervals, fallback
  reasons, and executed renderer timing.
- `provenance.json`: Motion Plan and Prosody version markers.
- engine trace and audit event: intended versus materialized timeline state.

## Compute profile

PCM analysis is CPU-only and linear in audio length. The ffmpeg montage is a
small preprocessing encode; it does not add another neural model or consume
meaningful VRAM. LivePortrait and MuseTalk remain the GPU-heavy stages.

## Explicit limitation

V1 responds to acoustic energy, speech activity, pauses, and emphasis. It does
not infer word meaning, phonemes, hand gestures, or train a speaker-specific
audio-to-motion network. Those belong to a later evaluated model phase.
