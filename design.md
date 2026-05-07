# VISUS VidLab Design System — Editorial Intelligence

A premium, cinematic, AI-learning UI system with a Netflix-meets-Notion feel.  
The interface should feel intelligent, calm, and authoritative, with strong content focus and minimal chrome.

## 1) Product Direction

Core principles:
- Cinematic: large imagery, soft gradients, immersive spacing
- Editorial: magazine-like hierarchy, intentional asymmetry, strong typography
- Clear: Notion-like readability, low visual noise, concise labels
- Discovery-first: Netflix-style hero sections and horizontal rows
- Learning-first: lecture player, chapters, transcript, notes, progress, focus mode

## 2) Theme Strategy

Use semantic tokens only. Do not hardcode colors in components.

Token names:
- `bg`
- `surface`
- `surfaceElevated`
- `textPrimary`
- `textSecondary`
- `accent`
- `accent2`
- `accentGradient`
- `borderSubtle`
- `glassOverlay`

Define two themes:
- `light`
- `dark`

Theme behavior:
- default to system preference on first load
- allow explicit user toggle
- persist choice in localStorage
- use class-based dark mode in Tailwind

Suggested palette meaning:
- `accent` = purple
- `accent2` = blue
- `accentGradient` = linear gradient from purple to blue

## 3) Surface and Depth Rules

No boxy SaaS look.

Do:
- use surfaces and spacing to separate sections
- use rounded cards with soft geometry
- use translucent overlays for menus and AI panels
- use soft shadows only for modals, active overlays, or lifted cards

Do not:
- use harsh 1px borders as section dividers
- use dense grid lines
- use opaque containers everywhere

Accessibility exception:
- subtle outline or hairline border may be used when needed for focus or contrast

## 4) Typography

Use a dual-font system:
- Manrope for display and headlines
- Inter for body and UI text

Hierarchy:
- `display-lg` — cinematic hero moments
- `headline-md` — module titles
- `title-lg` — card headings
- `body-md` — main body text
- `label-sm` — metadata and tags

Rules:
- headings should have strong contrast
- secondary text should be softer
- transcripts must remain highly readable

## 5) Layout Rules

- use a 12-column responsive grid
- use a spacing scale based on 8px units
- use asymmetric spacing where useful to create editorial feel
- use full-width hero sections for discovery
- use a centered reading column for long-form player content
- keep the interface responsive and mobile-first

## 6) Component Rules

### Buttons
- Primary: gradient fill, pill shape
- Secondary: elevated surface, no heavy border
- Tertiary: transparent/ghost, accent text, hover emphasis only

### Cards
- soft rounded corners
- no strong borders
- use spacing and elevation to separate items
- active cards should glow subtly, not outline heavily

### Inputs
- pill-shaped or softly rounded
- subtle fill change on focus
- minimal border treatment
- clear focus-visible state

### AI Insight Glass
Special container for AI summaries:
- glassmorphism with blur
- subtle accent glow
- authoritative typography
- lightweight and premium appearance

## 7) Key Screens

### Home / Discover
- featured hero
- personalized rows
- trending content
- category rails
- quick actions

### Series / Course
- overview
- syllabus / chapters
- episode grid
- instructor info
- resources

### Lecture Player
- video player
- chapters
- transcript
- notes
- speed and quality controls
- related clips
- focus mode

### Browse / Search
- search bar
- filters and facets
- curated collections
- content grid / infinite scroll

### Library / Profile
- saved items
- continue watching
- history
- settings

### Authoring / Upload
- upload flow
- metadata
- thumbnail/cover
- publish controls

### Settings
- theme
- accessibility
- account
- help

## 8) Motion Rules

Motion should feel polished, not flashy.

- slow cinematic transitions for major page changes
- quick responsive micro-interactions for hover and tap
- cards may lift slightly on hover
- hero areas may fade or crossfade
- focus mode should dim background softly

Respect `prefers-reduced-motion`.

## 9) Implementation Notes

Frontend stack:
- React + Vite
- Tailwind CSS
- CSS variables for tokens
- class-based dark mode

Recommended structure:
- `src/app/`
- `src/pages/`
- `src/components/ui/`
- `src/components/discovery/`
- `src/components/player/`
- `src/components/studio/`
- `src/styles/theme.css`

Build with small reusable components:
- header
- theme toggle
- hero
- content rails
- cards
- player shell
- transcript panel
- notes panel
- settings panel

## 10) Don’t

- do not make it look like a traditional admin panel
- do not use heavy borders
- do not overuse blue links
- do not clutter the screen
- do not make the player secondary to the controls
- do not mix design decisions into component logic

## 11) Success Criteria

The UI should feel:
- premium
- calm
- intelligent
- cinematic
- easy to scan
- easy to use on mobile
- suitable for both light and dark mode