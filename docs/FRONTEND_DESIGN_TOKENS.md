# Frontend Design Tokens

TalkingSlides keeps shared visual decisions in `services/frontend/src/styles/globals.css`.
Use these tokens before adding new hard-coded values to components or pages.

## Categories

- Color tokens describe app surfaces, text, accents, borders, media overlays, and status feedback. Existing semantic names such as `--surface-container`, `--text-primary`, and `--accent-primary` remain the source of truth.
- Spacing tokens use an 8px-oriented scale with useful half steps, from `--space-0` through `--space-16`.
- Radius tokens include restrained scale values and semantic aliases: `--radius-control`, `--radius-card`, `--radius-dialog`, and `--radius-pill`.
- Shadow tokens provide subtle elevation for light and dark themes. Prefer `--shadow-xs` through `--shadow-lg`, `--shadow-dialog`, and `--shadow-focus` over one-off drop shadows.
- Motion tokens define durations and easing for restrained UI transitions: `--duration-*` and `--ease-*`.
- Control tokens define shared control and icon sizes for primitives.
- Typography tokens preserve Inter for body text and Manrope for display text while giving shared names to common sizes, leading, and tight tracking.

## Theme Behavior

Light-mode tokens live in `:root`. Dark mode is activated by `ThemeProvider` with the `.dark` class and `data-theme` on `document.documentElement`.
Only values that need different dark-mode rendering should be overridden in `.dark`; token names should stay stable across themes.

## Consuming Tokens

Prefer the existing semantic utility classes (`token-surface`, `token-surface-elevated`, `token-glass`, `focus-ring`) and Tailwind token mappings such as `rounded-control`, `rounded-card`, `h-control-md`, `shadow-token-sm`, and `duration-normal`.

Use Tailwind arbitrary values for existing semantic color tokens when needed, for example `bg-[var(--surface-container)]` or `text-[var(--text-primary)]`.
Avoid introducing new arbitrary radii, shadows, transition timings, or control heights unless the token set is missing a reusable concept.
For dark-mode work, prefer semantic pairings such as `--bg`, `--surface-*`, `--border-subtle`, `--text-secondary`, `--outline`, `--hover-surface`, `--hover-accent-soft`, and `--modal-backdrop` instead of page-level `dark:` overrides or fixed white/black surfaces.
Treat hard-coded `bg-white`, `bg-black`, `text-gray-*`, `border-gray-*`, and one-off rgba values as suspect unless they are part of real media overlays, charts, thumbnails, or brand artwork.

## Shared Primitives

Use `Input`, `Textarea`, and native `Select` from `services/frontend/src/components/ui` for new shared form work instead of duplicating page-level control classes.
Use `SurfaceCard` for reusable card or panel surfaces and `Badge` for compact metadata or status chips.
Keep feature-specific layout in the page or component, but avoid copying primitive radius, focus, disabled, border, placeholder, or status color styles into new surfaces.

Use `Dialog` from `services/frontend/src/components/ui/Dialog.jsx` for modal dialogs instead of feature-level fixed overlays.
Compose it with `Dialog.Header`, `Dialog.Title`, `Dialog.Description`, `Dialog.Body`, `Dialog.Footer`, and `Dialog.Close` so dialogs share backdrop, sizing, surface, scroll-lock, focus containment, Escape handling, and close-button behavior.
Disable backdrop or Escape closing for destructive, dirty, or submitting workflows that must force an explicit cancel or completion path.
Existing `ModalShell` callers keep their legacy props, but new dialog work should avoid duplicating modal markup in pages.

Use `EmptyState` from `services/frontend/src/components/ui/EmptyState.jsx` when a loaded surface has no meaningful content to show.
Empty states should say what is empty, why it may be empty, and what the user can do next when a real next action exists.
Keep loading, empty, and error states separate: loading should use `Skeleton` or status text, failed requests should keep error UI, and `EmptyState` should render only after a successful zero-result or no-data response.
Prefer concise copy, decorative icons, optional actions only when they map to an existing workflow, and compact empty states inside tables or dense panels.
Avoid page-level duplicate empty-state card markup for new surfaces.

Use `PageContainer`, `PageHeader`, and `PageToolbar` from `services/frontend/src/components/ui/PageLayout.jsx` for new route-level screens and for representative migrations.
`PageContainer` owns route-level width and vertical rhythm; choose `standard`, `wide`, or `full` only when the existing content density requires it.
`PageHeader` owns eyebrow, heading, description, and action wrapping while preserving semantic heading elements.
`PageToolbar` owns filter/search/tab row spacing and optional surface treatment; keep toolbar control order aligned with DOM order so keyboard and RTL behavior remain predictable.
Avoid duplicating page-level `max-w-* mx-auto`, header flex wrappers, and pill toolbar surface classes in new screens.
Existing navigation should keep route-driven active states, `aria-current`, visible focus rings, and a non-color selected indicator.

## Contrast And State Guidance

Body copy should use `--text-primary`; supporting copy should use `--text-secondary`; labels, placeholders, and low-emphasis metadata should use `--outline` only when the content remains readable.
Interactive controls should keep visible hover, focus-visible, and disabled states in both themes. Focus indicators should use the shared `focus-ring` treatment so the accent ring remains visible against page, card, and dialog surfaces.
Disabled controls should not rely on opacity alone; combine opacity with semantic disabled backgrounds or foregrounds while preserving native disabled behavior.
Status UI should use the existing feedback/status tokens for success, warning, danger, and info pairings. Do not communicate status by color alone; keep the existing icon or text label alongside the color treatment.
Use `--glass-overlay` and `--glass-stroke` for app chrome or translucent sticky surfaces so dark mode does not duplicate light-only rgba values.

## Reduced Motion

`prefers-reduced-motion: reduce` minimizes non-essential animation and transition duration.
Progress and loading indicators remain visible as static feedback so users still receive functional state changes.

## Adding Tokens

Add a token only when more than one component or a shared primitive can use the concept.
Do not rename or remove existing tokens without a compatibility migration and tests.
Keep page redesigns separate from token-foundation work.
