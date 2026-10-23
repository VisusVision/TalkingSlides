# Tailwind 4 security migration pilot

This experiment is isolated on `chore/tailwind4-security-pilot`. It is not a
merge or deployment. The source branch was clean at the expected
`b259020dbaadf8b2a332e6cb720a0e338c6ff642` when the pilot was created.
`feature/ai-visual-designer`, `developer`, and `main` remain unchanged.

## Decision and verification status

**ACCEPT — recommend integration after separate authorization.** The final
candidate `b7791f34b475d4b5fddaaf462fe4ed557208ecb7` passes both hosted workflows,
including 32 fresh baseline/candidate visual comparisons and 32 functional
Chromium tests. Both npm audit gates report zero vulnerabilities. The migration
preserves the tested design and Phase 2A preview behavior. No merge or deployment
has been performed. This final report/evidence commit changes documentation only;
the tested application, dependency, and test files remain identical to that SHA.

## Dependency comparison

All versions below come from the actual before/after lockfiles.

| Package | Tailwind 3 baseline | Pilot |
| --- | --- | --- |
| tailwindcss | 3.4.19 | 4.3.3 |
| @tailwindcss/vite | absent | 4.3.3 |
| @tailwindcss/node / oxide | absent | 4.3.3 |
| autoprefixer | 10.4.20 | removed |
| jiti | 1.21.7 | 2.7.0 |
| braces | 3.0.3 | absent |
| chokidar | 3.6.0 | absent |
| micromatch | 4.0.8 | absent |
| fast-glob | 3.3.3 | absent |
| postcss-nested | 6.2.0 | absent |
| postcss-selector-parser | 6.1.5 | absent |
| Browserslist | 4.29.4 | absent |
| PostCSS | 8.5.31 | unchanged |
| Vite | 8.3.5 | unchanged |
| Vitest | 4.1.12 | unchanged |
| React / React DOM | 18.3.1 | unchanged |
| React Router DOM | 7.18.4 | unchanged |

The lockfile has 218 package locations before and 184 after, excluding its
root record. Platform-specific optional packages account for differences from
the number installed on a particular host. No unrelated existing package
version changed. Lightning CSS is now used through the supported Tailwind
integration, including the version pinned by `@tailwindcss/node`.

### Audits and complete removed paths

| Check | Baseline | Pilot |
| --- | --- | --- |
| `npm audit --audit-level=high` | exit 1: 5 high, 2 moderate | exit 0: zero |
| `npm audit --omit=dev --audit-level=moderate` | exit 0: zero | exit 0: zero |

The seven baseline entries represented two underlying advisories, all in
development tooling:

* [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm):
  deeply nested brace patterns can exhaust the stack. The baseline paths were
  `tailwindcss -> chokidar -> braces`,
  `tailwindcss -> micromatch -> braces`, and
  `tailwindcss -> fast-glob -> micromatch -> braces`.
* [GHSA-rj75-hqrm-r3gf](https://github.com/advisories/GHSA-rj75-hqrm-r3gf):
  selector parsing CPU exhaustion. Baseline paths were
  `tailwindcss -> postcss-selector-parser` and
  `tailwindcss -> postcss-nested -> postcss-selector-parser`.

Lockfile-wide inspection and `npm ls ... --all` find no remaining instances
of the six named libraries. There is therefore no remaining dependency path
to list. The targeted `npm ls` prints `(empty)` and exits 1 because none of the
requested packages is installed; the full installed dependency-tree check passes.
The existing security gate in `.github/workflows/ci.yml` is unchanged.
No force fix, override, advisory exclusion, or audit threshold reduction was used.

## Supported configuration and visual preservation

`vite.config.js` uses **only** `@tailwindcss/vite`; the old PostCSS plugin
configuration and direct Autoprefixer dependency are removed. Direct PostCSS
is retained at the existing patched floor used by the frontend toolchain.
`@tailwindcss/postcss` is not installed.

The existing `tailwind.config.js` is preserved and loaded explicitly using
`@config`. Its spacing, control sizes, fonts, custom shadows, radii, durations,
and easing tokens remain available. `globals.css` imports Tailwind 4, declares
the existing index/source scan roots explicitly, and retains the `.dark`
class variant. `ThemeProvider` and its persisted light/dark/system behavior
are unchanged.

`src/styles/tailwind-compat.css` records the existing v3 palette and stock
design values as inline theme values. This is necessary because product
variables such as `--radius-lg`, `--leading-tight`, and `--tracking-tight`
otherwise override Tailwind 4 utilities with the same variable names. Stock
utilities and product `token-*` utilities continue to have distinct values.
The existing pixel breakpoints are preserved.

Compatibility changes are limited to visual preservation:

* Put external font stylesheets in the base layer. Google's Material Symbols
  stylesheet also declares a utility-like class; leaving it unlayered would
  override the application's existing icon sizing and inline-flex alignment.
  Real-font comparisons exposed the resulting dashboard/sidebar shifts.
* Preserve v3 border and placeholder colors, button cursors, and native dialog
  margins. Existing custom component CSS, keyframes, and tokens remain.
* Keep transparent keyboard focus outlines. Existing `outline-none` uses move
  to the supported `outline-hidden` utility, which restores an outline under
  forced colors. A custom utility with the old name was rejected because it
  collided with Tailwind 4's built-in rule.
* Add individual transform properties to shared transitions and reduced-motion
  resets, because Tailwind 4 uses `scale`, `translate`, and `rotate` separately.
* Replace two ambiguous arbitrary font-family classes with existing
  `font-display`; Tailwind 4 would interpret the old syntax as font weight.
* Preserve previous-sibling spacing for the eleven existing base/responsive
  `space-y-*` forms. Low-specificity resets remove only the new spacing rule;
  the retained selector preserves hidden-child and authored-margin behavior.
  New spacing forms should be reviewed rather than assumed to have v3 semantics.
* Make Studio's responsive heading line height explicit. Tailwind 4 retains a
  base leading value across responsive font-size changes, whereas the baseline
  responsive size supplied its own line height. The same preservation is needed
  for hero descriptions (24 px), the Studio onboarding header (32 px), and the
  channel featured title (36 px). These are explicit v3 default line heights.

The initial hosted comparison correctly rejected 12–16 px Studio spacing
changes and a 1.5 px desktop heading line-height change. Those are migration
regressions, not accepted design changes. Mobile coverage must use the existing
compact Inspector switcher before selecting Intelligence. The theme test seed
must run once, so it does not overwrite the dark preference being tested on reload.

## Reproducible browser evidence

`.github/workflows/tailwind4-pilot.yml` checks out the immutable baseline SHA
separately, installs its exact lockfile, runs its unit tests/build/Chromium
E2E, and builds the candidate. `playwright.visual-pilot.config.js` serves both
production builds on separate loopback ports.

`visual-pilot/compatibility.spec.js` uses the same synthetic lesson, page key
`source-0-split-1`, narration, source hash, original PNG, private SVG preview,
notifications, and short local WebM on both builds. It covers eight surfaces
in light/dark at 1600x1000 desktop and 390x844 touch/mobile: dashboard and
navigation, Studio editor, Analysis, Visuals, expanded preview dialog, login,
lesson playback, and notifications: **32 comparisons**.

Each baseline screenshot is captured anew in the same test and used as that
test's reference. No uncertain stored golden images are used. The harness
compares full-page screenshots with threshold 0.1 and maximum changed-pixel
ratio 0.001, and separately checks semantic element geometry to 0.1 px,
typography, and radii. Colors are assessed by rendered pixels, avoiding false
failures from equivalent `rgba()` versus `oklab()` serialization.

The harness disables browser scroll anchoring on both builds, waits two frames,
uses instant scroll positioning, and asserts zero scroll offsets after capture.
An earlier run passed 30/32 cases but captured Studio's baseline one pixel
scrolled; this changes document coordinates without changing layout. The
stabilization preserves the comparison thresholds and records scroll evidence.
The Visuals case also scrolls its nested inspector to show the decoded preview.
Playback capture waits for paused, non-seeking media with future frame data,
records that state, and avoids a redundant seek to zero that can show a native
loading spinner in the later-captured build. This is test-state stabilization;
no player, media, or renderer implementation changes are made.

The production Google Fonts URLs are unchanged. The final harness downloads
each requested CSS/font resource once, serves identical bytes to both builds,
waits for font loading, and records resource SHA-256 hashes. Missing required
fonts fail the test. Early exploratory comparisons used deterministic fallback
fonts; all 32 final comparisons passed with the real downloaded font resources.

Artifacts include baseline/candidate PNGs, computed layout JSON, font
provenance, screenshot diffs on failure, and traces on failure. Hosted artifacts
are retained for 14 days. Reproduction:

```sh
# In an immutable b259020 checkout:
cd services/frontend
npm ci
npm run test:ci
npm run build
npx playwright install --with-deps chromium
npm run e2e

# In the pilot checkout:
cd services/frontend
npm ci
npm run test:ci
npm run build
npm run e2e
TAILWIND_BASELINE_FRONTEND=/absolute/baseline/services/frontend \
  npx playwright test --config playwright.visual-pilot.config.js
npm audit --audit-level=high
npm audit --omit=dev --audit-level=moderate
```

The baseline browser suite has 27 tests. The pilot adds five focused tests for
theme persistence/OS override, stock versus product tokens, forced-color focus,
both reduced-motion modes, and sibling spacing, bringing functional E2E to 32.
Visuals checks decode private assets, switch Analysis/Visuals, and assert that
only the expected suggestion-generation POST occurs. Video/draft/narration
mutation endpoints are not used.

## Final hosted results and reviewable evidence

Both runs below tested the exact candidate
`b7791f34b475d4b5fddaaf462fe4ed557208ecb7` on 2026-10-23.

| Gate | Result |
| --- | --- |
| Fresh baseline `npm ci`, unit tests, build | passed; 330 tests / 47 files |
| Fresh baseline Chromium E2E | 27 passed (34.5 s) |
| Candidate `npm ci`, unit tests, build | passed; 330 tests / 47 files |
| Candidate Chromium functional E2E | 32 passed (40.5 s) |
| Same-run baseline/candidate screenshots and layout assertions | 32 passed (1.9 min) |
| Candidate `npm audit --audit-level=high` | exit 0; zero vulnerabilities |
| Candidate `npm audit --omit=dev --audit-level=moderate` (local) | exit 0; zero vulnerabilities |
| Existing backend CI | 2,182 passed; 10 skipped; 63 subtests passed; 2 warnings |
| Existing backend dependency security check | no known vulnerabilities found |
| Docker smoke and load-test smoke | both passed |

* [Normal CI — all four jobs passed](https://github.com/VisusVision/TalkingSlides/actions/runs/39879821827).
* [Tailwind pilot — passed](https://github.com/VisusVision/TalkingSlides/actions/runs/39879821816).
* [Full original PNGs, layout/font/media records, generated CSS, and HTML reports](https://github.com/VisusVision/TalkingSlides/actions/runs/39879821816/artifacts/12469709454).
  Artifact ZIP: 12,984,402 bytes; SHA-256
  `e4cb754d386e6cd29ca5b2bfa755456fba69c2df7b25f434cc375ee61d82228c`.
  The downloaded archive was verified against GitHub's digest. Hosted retention
  expires 2026-11-06; the following compact evidence is committed permanently:
* [Per-case results, image hashes, raw pixel metrics, font provenance, media
  state, and generated CSS hashes](tailwind4-pilot/visual-results.json).
* [Desktop light Visuals comparison](tailwind4-pilot/visuals-desktop-light.webp).
* [Mobile dark Visuals comparison](tailwind4-pilot/visuals-mobile-dark.webp).
* [Expanded suggestion dialog comparison](tailwind4-pilot/suggestion-dialog-desktop.webp).

Manual review covered the eight surfaces in desktop-light and mobile-dark
montages, plus full-sized Visuals and dialog comparisons. All 32 cases have
matching measured semantic geometry, typography, and radii. They are not
pixel-identical: 30 cases have raw pixel differences, primarily subtle tones,
shadows, and rasterization. Mean absolute channel differences range from 0 to
0.479 on a 0–255 scale. All satisfy the unchanged perceptual comparison limit;
no remaining material layout or legibility regression was identified. The JSON
records raw differences separately from Playwright's perceptual pass/fail.
All four playback comparisons recorded both videos paused at zero, not seeking,
with `readyState=4`; the earlier transient loading-spinner mismatch is resolved.

Earlier failing pilot runs were useful rejection gates: Studio spacing and
leading, Material Symbols cascade precedence, and responsive hero leading were
fixed before acceptance. A later 30/32 run exposed scroll-position jitter, and
manual review of an otherwise passing run exposed unstable media capture.
Those harness fixes retained comparison thresholds and added state assertions.
The immediately preceding candidate also passed all 32 comparisons; the final
candidate repeats the entire gate with stable playback capture.

Browser integration uses synthetic API fixtures and checks the real frontend
generation request, private authenticated image loading, decoded previews,
Analysis/Visuals switching, dialog focus, page identity, and absence of lesson
mutation requests. It does not establish a live browser-to-Django deployment or
GPU/video-generation result. The independent existing backend suite passes.

## Local verification and limitations

Local Node 24.19.0/npm 11.9.0: fresh `npm ci`, all **330 unit tests in 47 files**,
production build, both audits, dependency-tree validation, and diff whitespace
checks passed. The build still reports its existing chunk-size warning; it was
not silenced. CSS is expected to differ:

| Build | Bytes | SHA-256 |
| --- | ---: | --- |
| Baseline | 93,106 | `54d247d513d8525916642b7d25bb8ae0cdfdcf7585c566673fef5d97e71dfd69` |
| Migrated | 114,951 | `48fda275ceb067dcc2d96bcf6fc98ed123fe04b739c6918a158340f4a26c0a15` |

Official local Playwright Chromium downloads returned truncated archives.
Local `npm run e2e` could not launch its expected browser. A separate scratch
browser experiment also failed protocol/startup checks and is not acceptance
evidence. No debug browser dependency is included in the repository. Hosted
Chromium results are the browser acceptance authority.

The Vite plugin supports existing Vite 8; Oxide requires Node >=20, within the
existing Vite requirement of Node 20.19+ or >=22.12. Oxide adds native optional
platform packages; Linux GNU CI and the lockfile's supported platform entries
must be checked before integration on another platform.

[Tailwind's browser requirements](https://tailwindcss.com/docs/compatibility)
are Chrome 111+, Safari 16.4+, Firefox 128+. Chromium desktop/touch emulation
does not establish Safari, Firefox, or physical mobile-device compatibility.
No support for older browsers is claimed. The repository has no explicit
older-browser support policy. Native media decoding/network DRM, production
backend deployment, and real microphone hardware are outside this CSS pilot.

No backend models, migrations, API contracts, Visual Designer business logic,
source extraction, render planning, TTS, video composition, moderation, or
publication code changed. The normal backend/Docker jobs remain enabled.

## Commit history and integration safety

* `413248295d691d7e4824f2b48063902199759d54`: supported migration and initial
  same-run comparison infrastructure.
* `a80cd4c78177058d552bb127410e3dc33eac03a5`: supported outline utilities,
  private original-image and playback fixtures.
* `4be978907868b89ef14f8e1da1130b36586b9e04`: Studio leading and sibling-spacing
  preservation, mobile inspector test path, spacing regression test.
* `6a86f03929e356a0e1f3f2525bb17b0cbf1b9e69`: populated dashboard fixtures,
  shared real-font capture/provenance, correct theme-persistence test seed.
* `6368e396d568a9274b69c001e913675f60e085db`: external font stylesheets in the
  base layer to preserve existing Material Symbols component rules.
* `d81e8ccf77319d5428e84ebb5d0e11b880d59111`: original responsive line heights
  in hero and featured-title layouts; final application-code change.
* `a62943572dbfdc9133a2fad57f7779db6619835c`: stable scroll capture and visible
  private Visuals assets; 32/32 comparisons and functional E2E passed.
* `b7791f34b475d4b5fddaaf462fe4ed557208ecb7`: stable paused playback fixture;
  final fully green hosted candidate, including backend and smoke jobs.

For integration review, compare the immutable baseline to the final candidate,
retain `tailwind-compat.css`, and confirm the browser support floor is acceptable.
Before deploying on another OS or supporting Safari/Firefox, run the same
lockfile install/build and relevant browser checks there. These are platform
coverage limits, not concealed failing gates. Further Visual Designer work can
continue independently on its unchanged feature branch.

No merge, deployment, force push, history reset, audit bypass, or update to the
three protected source branches is authorized or performed by this pilot.
Integration requires a separate instruction after reviewing the final evidence.
