# Secure Playback Manual Test Checklist

Phase 1 frontend player routing should be verified with backend playback-token payloads, not local frontend overrides.

## Public MP4

- Public lesson with `stream_url` or `video_url` plays through the existing MP4 `VideoStage`.
- Subtitles, transcript jumps, notes, comments, likes, progress saving, and avatar overlay behavior remain unchanged.

## Secure Stream HLS

- `protection_mode=secure_stream` with `streaming.hls.manifest_url` routes to `HlsPlayer`.
- Native HLS browsers use the video element directly.
- Non-native HLS browsers use `hls.js`.
- On fatal HLS error, MP4 fallback is used only when `allow_mp4_fallback=true` and a fallback URL exists.
- If no HLS manifest exists and `allow_mp4_fallback=true`, playback uses the tokenized MP4 URL.
- If no HLS manifest exists and `allow_mp4_fallback=false`, playback shows the secure-stream unavailable message.

## DRM Protected

- `protection_mode=drm_protected` never uses MP4 fallback.
- Missing DRM manifest shows the protected-playback unavailable message.
- `drm.enabled !== true` shows the protected-playback unavailable message.
- `drm.ready !== true` shows the protected-playback unavailable message.
- No ready DRM systems shows the protected-playback unavailable message.
- No EME support shows the protected-playback unavailable message.
- Valid DRM metadata routes to the Phase 1 unavailable state for `drm_shaka`; Shaka playback is not implemented in this phase.

## Phase 2 Protection Overlay

- Watermark renders only when playback token payload has `watermark.enabled=true` and `VITE_PLAYER_WATERMARK_ENABLED` is not `false`.
- Watermark displays `watermark.text`, moves to different player positions over time, and does not block native controls.
- Heartbeat starts after play for MP4 and HLS playback and stops on pause, ended, unmount, or source change.
- Heartbeat sends `visibility=visible` while the page is visible and `visibility=hidden` while the tab is hidden.
- Backend heartbeat denial, including `409`, pauses playback and replaces the player with `Playback session expired or is active elsewhere. Please refresh.`
- Transient heartbeat transport failures back off without repeatedly surfacing session-expired UI.
