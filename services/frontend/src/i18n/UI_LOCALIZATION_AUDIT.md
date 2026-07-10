# UI Localization Audit

Scope: frontend-owned UI chrome in `services/frontend/src`, `services/frontend/e2e`, and `services/frontend/src/i18n`.

Classification:
- A: canonical translation key or exact static phrase exists for all 12 locales.
- B: previously fell back to English; fixed in this pass.
- C: legacy hardcoded UI string; covered by exact static phrase localization while awaiting component-level key migration.
- D: dynamic/user-generated content intentionally unchanged.
- E: backend/API text normalized to localized frontend messages.

## Glossary

Studio, Lesson, Slide, Render, Rerender, Publish, Watch, Avatar, Narration, Subtitle, Transcript, Inspector, Share, Moderation, Queue, Draft, Ready, Failed, Saving, Saved, Retry.

Brand/product names retained where appropriate: VISUS, VISUS VidLab, AI Academy, Google, DRM, API, TTS, HLS, MP4.

## Grouped Audit

| Area | Result |
| --- | --- |
| App shell | A/B: labels, navigation, mobile navigation, sidebar aria/title text, locale state, `html lang`, `dir`. |
| Auth | A/E: modal copy and invalid credentials/Google failures normalized. |
| Dashboard/Home | A/C/D: shell and dashboard copy localized; lesson titles/descriptions remain dynamic. |
| Browse/Search | A/C/D: labels, categories, loading/error/empty states, duration/view formatting localized; lesson content dynamic. |
| Library | A/C/D: page chrome, empty/loading states, dates localized; publisher and lesson content dynamic. |
| History | A/C/D: loading/error/empty states localized; watched lesson data dynamic. |
| Analytics | A/C/D: headings, table/chart labels, compact numbers and metric units localized; analytics insights returned by API remain dynamic. |
| Notifications | A/E: filters, counts, empty/loading/error states, relative time formatting localized. |
| Studio | A/B/C/D/E: top chrome, filmstrip, render status, inspector, moderation and intelligence chrome localized; lesson notes/transcripts/user text dynamic. |
| Create lesson flow | A/C/D/E: modal and upload/progress/error phrases covered; filenames/user-entered fields dynamic. |
| Slide filmstrip | A/B: moved from partial English/Turkish table to canonical all-locale catalog. |
| Inspector | A/B/C: headings, actions, save/render states localized. |
| Render workflow | A/E: render active/failed/retry states and common API failures normalized. |
| Avatar preferences | A/C/E: Settings avatar controls, recorder states, preview/save/delete phrases covered. |
| Voice recorder | A/C: record/play/use/discard/status text covered. |
| Watch/player | A/C/D/E: playback states, subtitles, comments chrome, share controls, dates localized; comments/subtitles/transcripts dynamic. |
| Shared Watch | A/E: invalid/expired/revoked share states localized. |
| Share links | A/E: create/revoke/share expired/revoked handled. |
| Moderation/report | A/C/E: queue filters, findings, decisions, admin actions, common errors localized. |
| Settings | A/B/C: language selector remains the only locale selector; all supported locales selectable and persistent. |
| Error boundaries | A/C/E: page/loading/error/fallback/retry text covered. |
| Loading/empty states | A/C: canonical plus exact static phrase coverage across high-visibility routes. |
| Responsive/mobile UI | A/C: mobile nav labels and hidden responsive labels covered. |
| Accessibility/tooltips | A/C: `aria-label`, `title`, `placeholder`, `alt` exact phrases covered. |

## Coverage

- `APP_MESSAGES`: 211 canonical keys per supported locale.
- `STATIC_UI_MESSAGES`: exact phrase catalog for audited legacy hardcoded UI strings across all supported locales.
- Coverage checker enforces missing keys, empty values, same-as-English values, interpolation parity, and shape parity.

## Intentional Non-Translation

Lesson titles, descriptions, transcripts, subtitles, narration, comments, notes, uploaded filenames, user names, channel names, and API-provided generated content remain unchanged unless explicitly normalized as frontend error chrome.
