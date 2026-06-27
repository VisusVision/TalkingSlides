# I18N Roadmap

This roadmap covers application UI localization for Turkish and English. It is separate from lesson content language, TTS language, and subtitle translation.

## Current State

Current implementation is partial:

- Some backend flows read `Accept-Language` for intelligence/subtitle-related behavior.
- Several API paths return machine-readable `error_code` values.
- Subtitle translation and content language handling are product features, but they do not provide app-wide UI localization.
- No app-wide TR/EN frontend i18n foundation should be assumed yet.

## Goals

- Add a Turkish/English app language switch.
- Persist the user's UI language preference.
- Send the selected language to the backend through `Accept-Language`.
- Return stable backend `error_code` or `message_code` values so frontend copy can be localized.
- Keep logs, debug diagnostics, and operator-facing troubleshooting text in English.
- Keep content language independent from UI language.

## Frontend Foundation

The frontend should add a small i18n layer with:

- Supported UI languages: `en`, `tr`.
- A default language fallback.
- Namespaced message files for common UI, Studio, Watch, auth, settings, analytics, moderation, and errors.
- A language switch in user settings and possibly the app shell.
- Formatting helpers for dates, numbers, durations, and relative time.

Initial storage:

- Authenticated users: store preference on the user/profile API when backend support exists.
- Anonymous users: store preference in local storage.

The frontend should avoid mixing UI language with lesson content language. A Turkish lesson viewed in English UI should keep Turkish transcript/subtitle/content metadata unless the user requests translation.

## Backend Strategy

The backend should prefer stable codes over translated strings:

```json
{
  "error_code": "avatar_source_invalid",
  "message_code": "avatar.source.invalid",
  "detail": "Avatar source is invalid."
}
```

Rules:

- Keep `error_code` stable for programmatic behavior.
- Add `message_code` where frontend localization is expected.
- Keep `detail` as English developer/operator fallback unless a specific endpoint is designed to localize server text.
- Include structured fields for interpolation, such as `{ "limit": 10 }`, instead of embedding values only in prose.

## Accept-Language

Frontend should send:

```text
Accept-Language: tr
```

or:

```text
Accept-Language: en
```

Backend use:

- Choose user-facing message variants only where backend localization is explicitly implemented.
- Pass language preference into intelligence/report generation only when output language is a user-facing report choice.
- Do not use UI language to rewrite lesson content, source transcripts, or stored captions.

## Notifications

Notifications should store stable message codes and structured parameters:

```json
{
  "message_code": "render.completed",
  "params": {
    "lesson_title": "Linear Algebra"
  }
}
```

Frontend renders the message in the selected UI language. Existing free-text notifications can remain as fallback until migrated.

## Logs and Debug Output

Keep these in English:

- Backend logs.
- Worker logs.
- Health summaries meant for operators.
- Debug diagnostics.
- Exception traces.
- CI output.

Localized user-facing copy should not make incident triage harder.

## Content Language Separation

Keep separate settings for:

- UI language: app chrome, buttons, validation messages, notifications.
- Lesson content language: transcript/source language.
- TTS voice/language: synthesis behavior.
- Subtitle translation target: viewer-requested or publisher-generated subtitle sidecars.
- Intelligence output language: report copy, if requested.

## Implementation Phases

### Phase 1: Frontend Message Catalog

- Add i18n provider and message catalogs.
- Localize common navigation, auth, settings, Studio shell, Watch shell, and shared errors.
- Keep fallback English strings for unmigrated copy.

### Phase 2: User Preference

- Add frontend preference storage.
- Add backend user/profile preference when ready.
- Send `Accept-Language` with API requests.

### Phase 3: Backend Codes

- Audit user-facing API errors.
- Add `message_code` and structured params to high-traffic flows.
- Keep `error_code` stable for behavior.

### Phase 4: Notifications

- Store message codes and params for new notifications.
- Render existing text notifications as fallback.

### Phase 5: QA

- Add tests for language switching, persistence, API header behavior, fallback strings, and no content-language mutation.
