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

## Reduced Motion

`prefers-reduced-motion: reduce` minimizes non-essential animation and transition duration.
Progress and loading indicators remain visible as static feedback so users still receive functional state changes.

## Adding Tokens

Add a token only when more than one component or a shared primitive can use the concept.
Do not rename or remove existing tokens without a compatibility migration and tests.
Keep page redesigns separate from token-foundation work.
