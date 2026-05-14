# Secure Playback and DRM

This document consolidates the secure playback contract, local-development behavior, and production DRM expectations. Keep the manual verification checklist in [secure-playback-manual-test-checklist.md](secure-playback-manual-test-checklist.md).

## Modes

| Mode | Purpose | Production status |
| --- | --- | --- |
| `public` | Simple tokenized media URL behavior for local/dev or deliberately public lessons | Good for local/dev; use carefully in production |
| `secure_stream` | Tokenized playback with HLS support, MP4 fallback policy, watermark, heartbeat/session lock, and short-lived stream tokens | Recommended baseline production mode |
| `drm_protected` | Vendor DRM contract for Widevine, PlayReady, or FairPlay | Requires DRM vendor, key/license operations, and production player validation |

## HLS Behavior

The worker can write HLS packaging metadata and the API can issue tokenized manifest and segment URLs. Secure stream playback should prefer HLS when a manifest is available, then use MP4 fallback only when policy allows it.

For DRM-protected playback, MP4 fallback is disabled and the API expects a manifest plus DRM configuration.

## Frontend Foundation

The frontend has HLS player foundation and secure playback payload parsing. Manual verification should confirm:

- public MP4 still plays
- secure-stream HLS routes through the HLS player when manifest URLs are present
- fallback messages appear when HLS/DRM prerequisites are missing
- heartbeat denial pauses playback and shows a session message

Shaka/EME DRM playback is a foundation item, not a complete vendor integration. A production DRM launch still needs a provider-specific implementation and test matrix.

## Watermark and Heartbeat

Playback token payloads can include:

- `watermark.enabled`
- `watermark.text`
- `protection.visibility_lock`
- `playback_status`
- heartbeat/session identifiers

Frontend flags:

- `VITE_PLAYER_WATERMARK_ENABLED`
- `VITE_PLAYER_VISIBILITY_LOCK_ENABLED`
- `VITE_PLAYER_HEARTBEAT_ENABLED`
- `VITE_PLAYER_HEARTBEAT_INTERVAL_MS`

Production secure-stream deployments should keep heartbeat/session-lock behavior enabled unless a clear product decision disables it.

## DRM Vendor Requirements

Before enabling `drm_protected`, choose and configure a DRM provider that can supply:

- license server URL
- signing/auth credentials
- Widevine, PlayReady, and/or FairPlay configuration
- FairPlay certificate URL if FairPlay is enabled
- packaging/key management workflow
- player-side license request handling
- monitoring and support process

Keep provider secrets out of Git and out of frontend bundles. Only non-secret player metadata should be exposed through the API.

## Local Development vs Production

Local development can use:

- `LESSON_PROTECTION_DEFAULT_MODE=public`
- `DRM_ENABLED=0`
- `DRM_STREAMING_ENABLED=0`
- `VITE_PLAYER_ENABLE_HLS=true`
- `VITE_PLAYER_ENABLE_DRM_SHAKA=false`

Production secure-stream should use explicit HTTPS origins, production secrets, token/session binding, and HLS packaging. DRM-protected mode should stay disabled until the vendor integration is ready.

## Related Docs

- [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)
- [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md)
- [secure-playback-manual-test-checklist.md](secure-playback-manual-test-checklist.md)
