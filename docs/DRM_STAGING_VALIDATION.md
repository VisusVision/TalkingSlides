# DRM Staging Fixture Validation

This runbook covers a staging-only validation path for an externally packaged Widevine-compatible HLS/CMAF fixture. It does not launch production DRM, add a DRM vendor implementation, change backend playback contracts, change Shaka behavior, add license request auth headers, or prove app-generated DRM packaging.

## Required Configuration

Backend environment:

- `LESSON_PROTECTION_DEFAULT_MODE=drm_protected`
- `DRM_ENABLED=1`
- `DRM_PROVIDER_NAME=external`
- `DRM_PREFERRED_SYSTEM=widevine`
- `DRM_WIDEVINE_ENABLED=1`
- `DRM_WIDEVINE_KEY_SYSTEM=com.widevine.alpha`
- `DRM_WIDEVINE_LICENSE_URL=https://<vendor-license-host>/<widevine-license-path>`
- `DRM_WIDEVINE_CONTENT_TYPE=application/vnd.apple.mpegurl`
- `LESSON_PROTECTION_REQUIRE_DRM_METADATA_FOR_DRM=1`
- `LESSON_PROTECTION_REQUIRE_HLS_ENCRYPTION_FOR_DRM=1`

Frontend environment:

- `VITE_PLAYER_ENABLE_DRM_SHAKA=true`

The license URL must be absolute. If the vendor requires license request authorization, custom headers, tokens, certificates, or signed challenges, this first-pass fixture flow will not satisfy that requirement without a separate vendor-specific integration.

## Fixture Requirements

Supply the media fixture out-of-band. The asset must already be packaged as Widevine-compatible HLS/CMAF with a manifest Shaka can load and a license server that can issue licenses for the fixture keys.

The current worker HLS packaging is AES-128 HLS (`hls-aes-128`) and is not proven Widevine, CENC, or CMAF packaging. Do not use app-generated worker HLS output as evidence of Widevine readiness.

## Place the Fixture

Place the externally packaged fixture under the configured `STORAGE_ROOT` in the project namespace, for example:

```text
<STORAGE_ROOT>/<project-id>/fixtures/widevine/index.m3u8
<STORAGE_ROOT>/<project-id>/fixtures/widevine/<segments-and-init-files>
```

Keep paths storage-relative in sidecars. Do not use absolute filesystem paths or remote URLs for `hls.manifest_rel_path`.

## Point the Playback Sidecar at the Fixture

Update only the staging fixture project's playback sidecar:

```json
{
  "asset_id": "lesson-<project-id>",
  "content_id": "project-<project-id>",
  "protection_mode": "drm_protected",
  "hls": {
    "enabled": true,
    "manifest_rel_path": "<project-id>/fixtures/widevine/index.m3u8",
    "encrypted": true,
    "packaging_status": "external_fixture",
    "drm_scheme": "widevine-cenc-cmaf"
  }
}
```

The sidecar lives at:

```text
<STORAGE_ROOT>/<project-id>/playback_assets.json
```

## Report-Only Validation

Run the helper from `services/api`:

```powershell
py manage.py drm_fixture_validation --project-id <project-id> --json
```

The command is report-only. It reads the project playback sidecar and local manifest path, resolves non-secret DRM metadata from settings, and reports blockers/warnings. It does not mutate the database, rewrite sidecars, contact the license server, fetch remote URLs, or validate actual playback.

Expected clean report signals:

- `summary.ready_for_staging_fixture_attempt` is `true`
- `sidecar.protection_mode` is `drm_protected`
- `hls.manifest_exists` is `true`
- `drm.preferred_system` is `widevine`
- `drm.key_system` is `com.widevine.alpha`
- `drm.license_url_absolute` is `true`
- `mp4_fallback_expected` is `false`
- `blockers` is empty

## Backend Token Checks

Request the playback token for the staging project and confirm:

- HTTP 200 response
- `protection_mode` is `drm_protected`
- `video_url` is empty
- `allow_mp4_fallback` is `false`
- `streaming.hls.enabled` is `true`
- `streaming.hls.manifest_url` points to `/api/v1/stream/<token>/`
- `drm.ready` is `true`
- `drm.preferred_system` is `widevine`
- `drm.key_system` is `com.widevine.alpha`
- `drm.license_url` is the absolute vendor license URL
- `drm.asset_id` and `drm.content_id` match the fixture/license metadata

## Frontend Watch/Shaka Checks

With `VITE_PLAYER_ENABLE_DRM_SHAKA=true`, open the Watch page for the staging project and confirm:

- the DRM/Shaka player path is selected for the DRM token payload
- the tokenized HLS manifest URL is passed to Shaka
- Shaka receives the Widevine key system and license URL from the backend payload
- playback reaches either decoded video or a concrete vendor/browser/license error
- no MP4 fallback is used for the DRM-protected project

## Troubleshooting

Unsupported EME:
The browser or platform may not support `com.widevine.alpha`, or EME may be disabled. Test in a Widevine-capable Chrome/Edge environment over an origin accepted by the browser.

Invalid or relative license URL:
Set `DRM_WIDEVINE_LICENSE_URL` to an absolute `https://...` URL. Relative paths are reported as blockers and will not work for vendor licenses.

Missing DRM metadata:
Enable DRM and Widevine settings, set the preferred system, and provide `asset_id`/`content_id` in the sidecar when the vendor license expects exact IDs.

Manifest rewrite issues:
The backend streams tokenized HLS manifests and rewrites child segment/key references. Keep fixture manifest references relative to the manifest directory and inside `STORAGE_ROOT`.

License auth/header requirements:
This first pass does not implement vendor-specific request signing, auth headers, cookies, or certificate flows. A vendor that requires those will fail until a dedicated integration is added.

Vendor DASH-only asset:
The current playback sidecar model points to HLS manifests. A DASH-only fixture is out of scope for this validation pass unless the backend/frontend contract is extended.

FairPlay unsupported in first pass:
This first pass targets flagged Widevine validation. FairPlay requires platform-specific certificate and license handling and is not validated here.
