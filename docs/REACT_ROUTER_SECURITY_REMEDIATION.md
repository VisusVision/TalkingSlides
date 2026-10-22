# React Router and internal navigation remediation

Reviewed on 2026-10-23. Working branch: `feature/ai-visual-designer`.
Verified starting HEAD: `2af627a9cc1397438e14dcf9e8b68853353c1073`;
the checkout and remote branch matched and were clean.

Application validation and the dependency migration are separate commits:

- `8e9de836618f9c28240cfb44aa9e49bd7344cc94`: shared internal-target validation,
  callers, unit regressions and actual Studio browser regressions.
- `7e5fbe26353f9cb60361c8dd783574a62c2eb019`: manifest and lockfile only,
  React Router DOM/React Router 6.30.6 -> 7.18.4.

## Root cause and security boundary

Studio's `returnTo` query parameter is decoded by `URLSearchParams`, passed
to `safeInternalReturnTo` (`src/utils/routeSession.js`), stored in
`requestedReviewReturnTo` (`src/pages/Studio.jsx:3292`), and eventually passed
to `navigate` by `handleBackToReviewContext` (lines 5044-5046).
The old helper accepted any trimmed string beginning `/` except literal `//`.
It also returned an unchecked fallback. Slash-backslash and slash-tab-slash
strings passed that test. Browser URL interpretation turned them into
cross-origin locations; Router 6's failed `pushState` could fall back to
`location.assign`. Equivalent guards existed in sign-in and notification flows.

Before editing, an isolated JSDOM/actual Router 6 history probe intercepted
`assign` and reproduced both external-navigation attempts. The legitimate
`/moderation` control remained internal. No external website was contacted.
The same probe after the application fix routed both attacks and encoded
variants to `/moderation` with zero `assign` calls.

## Exact application changes

Paths in this section are relative to `services/frontend`.

| File / function | Change |
| --- | --- |
| `src/utils/routeSession.js`, `validatedInternalPath` / `safeInternalReturnTo` | One bounded validation boundary for targets and fallback values; returns accepted authored strings unchanged |
| `src/pages/Studio.jsx`, `handleBackToReviewContext` | Validates again at the navigation sink; Studio's fallback remains `/moderation` |
| `src/app/App.jsx`, `getRedirectFromSearch`, `handleLoginRequest`, `handleLoginSuccess` | Uses the shared validator for decoded query values, callback destinations and pending redirects |
| `src/utils/notifications.js`, `isSafeNotificationActionUrl` | Uses the same boundary before Header/Notifications navigation |
| `src/utils/routeSession.test.js` | Invalid target/fallback matrices, legitimate query/hash/Unicode paths, and outer-query decoding regressions |
| `src/utils/notifications.test.js` | Unsafe notification-link rejection and valid internal controls |
| `src/app/App.auth.test.jsx` | Actual App sign-in callback/query regressions and valid query/hash return navigation |
| `e2e/studio-navigation-security.spec.js` | Real Studio review Back behavior plus nested Analysis/Visuals preview regression |

All three original helper callers were inspected: Studio's query handling,
`ModerationDashboard.studioReviewUrl`, and `LessonActionButton.lessonReviewUrl`.
The latter two construct internal browser pathname/query values and inherit
the shared protection. Their source required no separate implementation.

Accepted targets must be nonempty strings of at most 4,096 characters and
begin with one root slash. The boundary rejects external schemes, relative
paths, protocol-relative paths, raw whitespace/backslashes/controls, malformed
percent/UTF-8 encodings, decoded controls, encoded pathname separators or
delimiters, nested pathname encodings, fullwidth slash variants and decoded
dot segments. A WHATWG URL origin check provides a final same-origin check
against a fixed synthetic origin, independent of deployment settings.

Supported internal query parameters, encoded query data, hashes, Unicode and
encoded spaces are preserved. Query/fragment values remain data: an encoded
URL in a search parameter is not itself a navigation target. They are still
checked for malformed encoding and controls. Unsafe input is rejected, never
trimmed, repaired or rewritten into a different destination. A bad fallback
returns an empty string; Studio supplies the validated `/moderation` constant.

## Router 7 compatibility and exact dependency changes

The npm registry's latest stable 7.x release was 7.18.4 at execution time.
Its declared React/React DOM >=18 and Node >=20 requirements fit React 18.3.1
and the repository's Node 20 CI (the existing Vite floor is already 20.19+).
The app retains declarative `BrowserRouter`, `Routes`, `Route`, `Navigate`,
navigation/search hooks and the existing `react-router-dom` import paths.
There is no framework/data-router conversion or router-plugin addition.

The checked-in route table has no multi-segment splat paths requiring a
relative-splat rewrite. Lazy imports are defined at module scope and remain
inside the existing Suspense boundary. Router 7's default startTransition
behavior is exercised by the existing navigation/auth tests; React 19-only
enhanced transitions were not enabled. No additional future flags were
necessary for the application API surface.

| Lock entry | Change |
| --- | --- |
| `react-router-dom` | 6.30.6 -> 7.18.4; manifest minimum `^7.18.4` |
| `react-router` | 6.30.6 -> 7.18.4 |
| `@remix-run/router` | 1.23.4 removed; Router 7 consolidates implementation |
| `cookie` | 1.1.1 added through Router's declared dependency |
| `set-cookie-parser` | 2.7.2 added through Router's declared dependency |

No other package version changed in this migration. The lockfile retains npm
registry tarball URLs and integrity records. No override, force fix, exclusion,
workflow weakening or unrelated package migration was introduced.

Primary references:

- [Router navigation advisory, patched >=7.18.0](https://github.com/remix-run/react-router/security/advisories/GHSA-wrjc-x8rr-h8h6)
- [SSR hydration advisory, patched >=7.18.0](https://github.com/remix-run/react-router/security/advisories/GHSA-337j-9hxr-rhxg)
- [v6 upgrade guidance](https://reactrouter.com/7.18.4/upgrading/v6)
- [Transition behavior](https://reactrouter.com/7.18.4/explanation/react-transitions)

## Verification

| Command / check | Application fix on Router 6 | Router 7 candidate |
| --- | --- | --- |
| `npm ci` | Exit 0, 193 packages | Exit 0, 194 packages |
| `npm run test:ci` | 47 suites, 330 tests pass locally and on GitHub | 47 suites, 330 tests pass locally and on GitHub |
| `npm run build` | Pass locally and on GitHub | Pass locally and on GitHub; existing large-chunk warning only |
| `npm run e2e` (local, `CI=true`, `--workers=2`) | All 27 blocked at browser launch | All 27 blocked at browser launch |
| `npm audit --omit=dev --audit-level=moderate --json` | Prior Router 6 baseline: 2 moderate entries | Exit 0, zero vulnerabilities |
| `npm audit --audit-level=high --json` | Hosted gate fails on existing findings | Exit 1; 5 high + 2 moderate development entries |
| `npm ls --all --omit=optional` | Existing graph retained | Exit 0; peer graph valid |
| `git diff --check` | Pass | Pass |

`npx playwright install chromium --only-shell` exited 1 locally: the downloaded
archive reported 0 MiB/truncation and a missing ZIP central-directory signature.
Local E2E results are blocked, not passed. GitHub's Chromium installation works
and is the browser acceptance environment for this task.

The application-fix run [39867335626](https://github.com/VisusVision/TalkingSlides/actions/runs/39867335626)
passed **27 Chromium tests**, including all ten added cases: eight malicious
Studio return paths, an internal query/hash return path, and Visuals preview
generation with Analysis switching. An external-navigation interceptor aborts
any regression before a request reaches another website and records attempts
as a test failure. The malicious cases assert `/moderation` and zero writes.
The Visuals case verifies a source-backed page key with source index zero and
split index one, a decoded private SVG image, the preview-only state, and only
the expected suggestion-generation POST. Existing auth, session persistence,
public routes, Watch, Studio release and notification suites are retained.

The first Router 7 hosted run,
[39867649467](https://github.com/VisusVision/TalkingSlides/actions/runs/39867649467),
passed unit tests, build, Docker smoke and load-test smoke. Its Chromium setup
was still running after approximately nine minutes when this report was
prepared. Committing this report retries the unchanged workflow on a fresh
runner. Application code and package records are identical to 7e5fbe2; the
report commit's [GitHub Actions checks](https://github.com/VisusVision/TalkingSlides/actions?query=branch%3Afeature%2Fai-visual-designer)
and final handoff record the resulting browser acceptance status. A setup wait
is not counted as a passing browser test. The older application-fix workflow
was cancelled by the next push only after its frontend job and all 27 browser
tests had completed; its recorded frontend evidence remains valid.

Additional evidence: the parent executed an 8,000-input separator/control/
encoding probe with zero off-origin accepted raw/decoded paths. Independent
read-only review found no concrete surviving bypass or legitimate regression,
and reported zero off-origin destinations through actual Router resolution in
a separate 21,952-input probe. Its local browser attempt was also unavailable.

## Remaining audit work and scope

Production findings drop from two affected-package entries to zero on Router 7.
Full audit drops from nine entries to seven. Five high entries are the existing
`tailwindcss -> chokidar/micromatch -> braces` chain, including the fast-glob
path. Two moderate entries are `postcss-selector-parser` and `postcss-nested`;
upstream lists installed parser 6.1.5 as patched while audit metadata still
flags the 6.x backport. The complete prior investigation is in
[FRONTEND_DEPENDENCY_SECURITY_REMEDIATION.md](FRONTEND_DEPENDENCY_SECURITY_REMEDIATION.md).

The full audit gate remains enabled and red on these existing development
dependencies. Resolve braces through an upstream supported patch or a
separately reviewed Tailwind migration; recheck the parser advisory metadata.
No Tailwind major, unsupported fork or blanket override was applied here.

Only frontend navigation validation, its tests, Router package records and
this report change. Visual Designer planning/source extraction/persistence,
lesson scenes, rendering, narration/TTS, publication and backend source are
unchanged. Suggestions remain private previews. Neither developer nor main
was modified, and no merge was performed.
