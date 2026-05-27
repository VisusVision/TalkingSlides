# CSP Report-Only Foundation

VISUS ships the first CSP rollout as telemetry only. Enable it with:

```env
CSP_REPORT_ONLY_ENABLED=true
```

When enabled, Django adds `Content-Security-Policy-Report-Only`; it does not add an enforcing `Content-Security-Policy` header. This keeps production auth, navigation, HLS, media playback, and existing browser flows unchanged while collecting violation data.

## Starting Policy

The default report-only policy is intentionally permissive where the current app needs browser flexibility:

```http
default-src 'self';
base-uri 'self';
object-src 'none';
frame-ancestors 'none';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob: https:;
font-src 'self' data:;
media-src 'self' blob: https:;
connect-src 'self' https:;
worker-src 'self' blob:;
manifest-src 'self';
form-action 'self'
```

`style-src 'unsafe-inline'` stays during telemetry because React inline style props and small runtime style blocks are still present. `img-src` allows `data:` and `blob:` for favicons and previews. `media-src` and `worker-src` allow `blob:` because playback and HLS support can rely on blob URLs and web workers.

## Report Endpoint

Browsers can POST CSP reports to:

```text
/api/v1/security/csp-report/
```

The endpoint is unauthenticated, POST-only, returns `204 No Content`, accepts invalid JSON without failing the browser request, and rejects oversized payloads. It logs only telemetry shape, not report body values, so document URLs, blocked URLs, tokens, and user content are not written by this foundation.

## Triage

Treat early reports as signal, not blockers:

- Group by violated directive.
- Verify whether blocked URLs are first-party, expected media/CDN, browser extension noise, or suspicious injection.
- Add required first-party/media origins through configuration before considering enforcement.
- Investigate any `script-src` violation carefully because auth tokens still live in browser storage.

## Why Enforcement Is Not Enabled Yet

Enforcing CSP would be premature while the app still uses localStorage token auth, hash-based auth redirect handling, dynamic media URLs, HLS workers, generated favicons, and blob/data preview URLs. Report-only lets production gather evidence without breaking active playback or auth flows.

## Roadmap

1. Run report-only in staging and production long enough to collect representative auth, Studio, playback, moderation, and profile traffic.
2. Remove avoidable inline style and runtime style blocks where practical.
3. Replace generated `data:` favicons with static assets if feasible.
4. Narrow `connect-src`, `img-src`, and `media-src` to known frontend/API/CDN origins.
5. Add alerting for unexpected `script-src` violations.
6. Move to enforcing CSP only after reports are clean and auth token storage has a stronger migration path.
