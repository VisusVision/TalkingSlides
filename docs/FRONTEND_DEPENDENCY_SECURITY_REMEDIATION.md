# Frontend dependency security remediation

## Outcome: partial remediation; verification blocked

Reviewed on 2026-10-22, after Phase 2A commit
`5fce9a3e1a233cf51d8fb1013538dad7dddb87f9`, on
`feature/ai-visual-designer`. The local checkout and remote feature HEAD matched
and were clean before editing. No Phase 2A work was overwritten.

Compatible package updates remove several audit findings, but **neither audit
gate is green**. Unpatched/disputed build-tool advisories and Router 6 advisories
remain. Chromium could not be downloaded in this environment, so browser
regression verification is blocked. This is not a claim of complete security
remediation or an acceptance/waiver of the remaining risk.

Only `services/frontend/package.json`, its lockfile, and this report change.
No workflow, application source, backend, Visual Designer logic, render, TTS,
draft or publication behavior changes. No force fix, overrides, exclusions,
dependency fork, major upgrade, or audit suppression was used.

## Reproduction and counts

Commands ran from `services/frontend`. Environment: Node 24.19.0, npm 11.9.0,
Linux x64. The existing environment has npm's install-time `audit` setting false;
it was not changed. Explicit `npm audit` requests were executed independently.
Counts below are npm affected-package entries, including propagated parent
entries, **not counts of independent exploits**.

| Audit | Before | After | Exit code before / after |
| --- | --- | --- | --- |
| `npm audit --audit-level=high --json` | 8 high, 6 moderate; 14 total | 5 high, 4 moderate; 9 total | 1 / 1 |
| `npm audit --omit=dev --audit-level=moderate --json` | 2 moderate | 2 moderate | 1 / 1 |

No critical, low or informational entries were returned in either final audit.
All remaining high entries are development dependencies, but that does not
make them irrelevant: CI audits the entire tree and Vite is a real dev server.

`npm audit fix --dry-run --json` was executed before editing and exited 1. It
proposed compatible updates, but still left unresolved findings. Its output
contained human-readable `change` lines before the JSON object; an initial
JSON-only parsing attempt failed, so the raw output was inspected instead.
It proposed Vitest 4.1.11, which is insufficient for the later manual-mock
advisory; the manifest explicitly requires 4.1.12. No dry-run changes were
applied automatically.

## Vulnerable baseline inventory and selected updates

Severity is the reproduced npm audit severity. Some upstream classifications
or patched ranges differ; see the unresolved-risk section.

| Package | Direct/transitive; use | Installed before -> after | Baseline severity / disposition |
| --- | --- | --- | --- |
| `vite` | Direct; dev server/build | 8.0.16 -> 8.3.5 | High; no longer reported |
| `vitest` | Direct; tests | 4.1.8 -> 4.1.12 | Moderate; no longer reported |
| `@vitest/mocker` | Transitive via Vitest; tests | 4.1.8 -> 4.1.12 | Moderate; no longer reported |
| `browserslist` | Transitive via Autoprefixer; build | 4.28.2 -> 4.29.4 | High; no longer reported |
| `baseline-browser-mapping` | Transitive via Browserslist; build | 2.10.20 -> 2.11.31 | Moderate; no longer reported |
| `source-map-js` | Transitive via PostCSS/jsdom; tooling | 1.2.1 -> 1.2.2 | High; no longer reported |
| `postcss-selector-parser` | Transitive via Tailwind/postcss-nested; build | 6.1.2 -> 6.1.5 | Moderate; still reported despite upstream backport |
| `react-router-dom` | Direct; production | 6.30.4 -> 6.30.6 | Moderate; one advisory removed, propagated findings remain |
| `react-router` | Transitive via react-router-dom; production | 6.30.4 -> 6.30.6 | Moderate; two advisories remain |
| `tailwindcss` | Direct; build | 3.4.17 -> 3.4.19 | High; propagated finding remains |
| `braces` | Transitive; build/watch globs | 3.0.3 unchanged | High; no published patch |
| `chokidar` | Transitive via Tailwind; watch | 3.6.0 unchanged | High; propagated braces finding |
| `micromatch` | Transitive via Tailwind/fast-glob; globs | 4.0.8 unchanged | High; propagated braces finding |
| `fast-glob` | Transitive via Tailwind; globs | 3.3.3 unchanged | High; propagated braces finding |

The five manifest minimums are now `react-router-dom: ^6.30.6`,
`postcss: ^8.5.31` (previously ^8.5.23), `tailwindcss: ^3.4.19`,
`vite: ^8.3.5`, and `vitest: ^4.1.12`. No new direct dependency was introduced.

Other exact lockfile updates required by these package families:

| Package/family | Before -> after |
| --- | --- |
| `@remix-run/router` | 1.23.3 -> 1.23.4 |
| `@vitest/expect`, `pretty-format`, `runner`, `snapshot`, `spy`, `utils` | Each 4.1.8 -> 4.1.12 |
| `rolldown` and existing native `@rolldown/binding-*` packages | 1.0.3 -> 1.2.15 |
| `@rolldown/binding-android-arm-eabi` | Added at 1.2.15 by Rolldown |
| `lightningcss` and its native `lightningcss-*` packages | 1.32.0 -> 1.33.0 |
| `@oxc-project/types` | 0.133.0 -> 0.155.0 |
| `@types/estree` | 1.0.8 -> 1.0.9 |
| `chai` | 6.2.2 -> 6.3.0 |
| `nanoid` | 3.3.18 -> 3.3.20 |
| `tinyrainbow` | 3.1.0 -> 3.2.0 |
| Vite's nested `picomatch` | 4.0.4 -> 4.0.7 |
| `caniuse-lite` | 1.0.30001788 -> 1.0.30001819 |
| `electron-to-chromium` | 1.5.340 -> 1.5.463 |
| `node-releases` | 2.0.36 -> 2.0.60 |
| `update-browserslist-db` | 1.2.3 -> 1.3.4 |

Rolldown's new published dependency graph no longer includes its old WASI
fallback: lock entries removed are `@rolldown/binding-wasm32-wasi@1.0.3`,
`@emnapi/core@1.10.0`, `@emnapi/runtime@1.10.0`,
`@emnapi/wasi-threads@1.2.1`, `@napi-rs/wasm-runtime@1.1.5`,
`@tybys/wasm-util@0.10.2` and `tslib@2.8.1`. Supported native platform entries,
including Linux musl, remain in the lockfile. No host-only lockfile pruning
was used. The lockfile is authoritative for every platform URL and integrity.

Registry checks found Vite 8.3.5 and Browserslist 4.29.4 as latest stable;
Vitest 4.1.12 is the latest 4.x (latest overall was 5.0.5). Vitest 5 requires
Node 22.12+ rather than the repository's Node 20 CI line, so it was not adopted.
The installed Vite/plugin-react floor remains Node 20.19+ or 22.12+; React
remains 18.3.1. All updates stay inside existing major-version constraints.
Transitives were updated using `npm update browserslist
baseline-browser-mapping postcss-selector-parser --package-lock-only
--ignore-scripts`, respecting their parents' declared ranges.

## Remaining risk and alternatives

### 1. Tailwind/braces: five high package entries, one root advisory

Paths are `tailwindcss@3.4.19 -> chokidar@3.6.0 -> braces@3.0.3`,
`tailwindcss -> micromatch@4.0.8 -> braces`, and
`tailwindcss -> fast-glob@3.3.3 -> micromatch -> braces`.
[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)
lists no patched release. Registry latest is still braces 3.0.3. The upstream
maintainer disputes the advisory; this does not remove the audit failure.

The advisory needs attacker-controlled deeply nested **glob patterns** reaching
recursive brace processing. An isolated child process calling `braces.expand`
on 4,500 nested groups around `a,b` reproduced a RangeError (exit 1); the actual
repository content glob processed normally. The reviewed application does not
pass lesson text or uploaded documents as glob patterns. Tailwind uses fixed
`./index.html` and `./src/**/*.{js,jsx,ts,tsx}` in `tailwind.config.js:4`.
Exposure is therefore build/watch availability when the pattern/configuration
boundary is untrusted, not a demonstrated lesson-upload or published-site
attack. Keep build inputs/configuration trusted; do not expose build tooling
as a service for arbitrary user patterns.

The latest compatible Tailwind 3 patch retains these dependencies. npm's
suggested Tailwind 4.3.3 replacement changes the PostCSS plugin architecture,
CSS directives and token/config behavior; it was explicitly not applied.
Supported next steps: use an upstream patched compatible release if one is
published, or authorize a dedicated Tailwind 4 migration with CSS/visual and
browser regression coverage. Do not alias an unrelated fork, force a new
Chokidar/micromatch major, or suppress the full-tree audit. See upstream
[issue 73](https://github.com/micromatch/braces/issues/73) for current discussion.

### 2. React Router: two production moderate package entries

6.30.6 removes [GHSA-jjmj-jmhj-qwj2](https://github.com/remix-run/react-router/security/advisories/GHSA-jjmj-jmhj-qwj2).
Still reported are [GHSA-wrjc-x8rr-h8h6](https://github.com/remix-run/react-router/security/advisories/GHSA-wrjc-x8rr-h8h6)
(unexpected external navigation through crafted paths) and
[GHSA-337j-9hxr-rhxg](https://github.com/remix-run/react-router/security/advisories/GHSA-337j-9hxr-rhxg)
(SSR hydration constructor injection), whose published fix is Router >=7.18.0.
The registry offers react-router-dom 7.18.4. The application uses declarative
BrowserRouter/Routes, not SSR hydration, so the latter advisory's required
mode is absent. Do not classify both findings as unreachable: crafted navigation
is relevant.

`safeInternalReturnTo` (`src/utils/routeSession.js:79-83`) only excludes `//`,
not slash-backslash or embedded-control variants. Studio reads the query
`returnTo` at `src/pages/Studio.jsx:3292` and passes it to `navigate()` in
`handleBackToReviewContext` at lines 5044-5046. This is an existing residual
path, not fixed by upgrading to 6.30.6. An isolated JSDOM probe using the actual
`createBrowserHistory` and `resolveTo` captured external `location.assign`
fallbacks for slash-backslash and slash-tab-slash inputs, while `/moderation`
stayed internal and literal `//` was rejected. Assignment was intercepted;
no external navigation or network request occurred. No guard or Studio
business logic was changed in this dependency-only patch.

Prioritize a separately validated 7.18.4 security migration, retaining the
declarative routing architecture. Node >=20 and React >=18 are compatible;
current routes have no multi-segment splat requiring restructuring. However,
the [upstream v6 migration guidance](https://reactrouter.com/7.18.4/upgrading/v6)
requires checking future-flag/transition behavior, lazy-loading and relative
navigation. Existing auth, notifications, public routes, Studio/Watch and
Visuals tests must pass, with malicious path regressions added and real browser
verification. Those conditions cannot be fully established here without a
working Chromium download, so no untested Router major was committed.

### 3. Selector parser: two development moderate package entries

`tailwindcss -> postcss-selector-parser@6.1.5` and
`tailwindcss -> postcss-nested@6.2.0 -> postcss-selector-parser@6.1.5`
remain flagged by npm's `<7.1.6` range for GHSA-rj75-hqrm-r3gf. The
[upstream primary advisory](https://github.com/postcss/postcss-selector-parser/security/advisories/GHSA-rj75-hqrm-r3gf)
explicitly lists **6.1.5 as a patched backport**, in addition to 7.1.6.
Installed source includes the Set-based fix. This is a metadata/backport
discrepancy, not a verified surviving CPU-exhaustion vulnerability in 6.1.5.
No exclusion was added; the audit still reports both package entries.

Two 40,000-character flat class/ID selectors round-tripped unchanged in
approximately 30/21 ms. A 1,000-level nested pseudo selector was explicitly
rejected at the library's depth limit of 256. Normal Tailwind build inputs
are repository-owned, not request-supplied CSS. Prefer corrected advisory
metadata/a compatible upstream dependency update over forcing parser 7 into
Tailwind's declared 6.x range.

## Verification evidence

| Check | Executed result |
| --- | --- |
| Baseline `npm ci` | Exit 0; 193 packages installed |
| Updated `npm ci` | Exit 0; 193 packages installed; manifest/lock synchronized |
| `git diff --check` | Pass |
| `npm ls --depth=0` and dependency-path inspection | Pass; expected versions, no invalid direct dependency |
| `npm ls --all --omit=optional` | Exit 0; dependency/peer graph valid |
| Baseline `npm run test:ci` | 46 files, 253 tests passed (Vitest 4.1.8) |
| Updated `npm run test:ci` | 46 files, 253 tests passed (Vitest 4.1.12), 8.87 s |
| Baseline / updated `npm run build` | Both pass (1.56 / 2.71 s); existing >500 kB chunk warning remains |
| Full / production audits | Both exit 1; exact counts above |
| `npx playwright install chromium --only-shell` | Exit 1; download reports 0 MiB/truncated ZIP, central-directory signature missing |
| `CI=true npm run e2e -- --workers=2` | Exit 1; all 17 cases stop at missing Chromium executable, no browser assertions executed |

All existing unit/component suites, including `VisualSuggestionsPanel`, nested
Intelligence tabs, Studio Intelligence, routing/auth and playback tests, were
retained unchanged. A separate archive of the exact Phase 2A baseline was
installed with `npm ci` and rebuilt, then its generated CSS was compared byte
for byte with the updated build's asset referenced by `dist/index.html`:
`index-D9rTB-uI.css`, 93,106 bytes, SHA256
`54d247d513d8525916642b7d25bb8ae0cdfdcf7585c566673fef5d97e71dfd69`.
The comparison passed; flex/grid, responsive grid and token utilities also
passed. An initial alphabetical asset scan selected an older ignored output;
the explicit build/index reference above is the verified comparison, not a
claim about every stale file in the local output directory.
No backend tests were rerun because no
backend files changed. No new test dependency or permanent test harness added.

Additional bounded in-process/child-process security controls were executed:

- Browserslist: six inherited/prototype-name stats keys no longer crash;
  normalization has a null prototype and ordinary defaults queries succeed.
- source-map-js: 12 invalid/oversized/nested section-offset cases reject;
  a valid indexed map still enumerates its mapping correctly. An initial
  ad hoc generator-serialization control hit the indexed consumer's undefined
  sourceRoot limitation; the valid mapping-iteration control was used instead.
- Vitest mocker: quote/statement and escape/newline manual URL payloads remain
  inert through `createManualModuleSource`; valid module exports are preserved.
- Braces: deep expansion still throws in an isolated child; legitimate content
  glob succeeds. This confirms an unresolved condition rather than closure.
- Every changed lock entry retains registry.npmjs.org tarball URLs and SHA512
  integrity. The complete manifest/lock diff and functional dependency/peer
  metadata were reviewed. No executable lifecycle override was added.

Vite's platform-specific Windows/editor exploits were not executed on Linux.
The strongest cross-platform substitute here is updated supported Vite 8,
the audit no longer reporting its advisories, and successful dev-server
startup/build. This is not equivalent to a Windows exploit reproduction.

## CI and next action

`.github/workflows/ci.yml` is unchanged. It runs Node 20, unit tests, build,
Playwright Chromium E2E, then **full-tree** `npm audit --audit-level=high`.
Do not change that gate to production-only or ignore its exit code.

The previously committed Phase 2A run
[39848891389](https://github.com/VisusVision/TalkingSlides/actions/runs/39848891389)
passed frontend unit/build/E2E and all other jobs, but failed the frontend npm
audit. That run is baseline evidence, not verification of this patch.
The remediation commit's hosted run must be inspected separately; the
reproduced remaining high findings mean the audit gate is expected to remain
red while the same advisory data is returned. No green CI claim is made.

Recommended order: unblock browser verification, validate Router 7.18.4 with
malicious-path and existing E2E coverage, then resolve the Tailwind/braces
dependency decision without suppressions. Recheck the selector-parser metadata
discrepancy with fresh npm audit data. Keep the Visual Designer work separate
from those migrations, and do not merge this branch as fully remediated.
