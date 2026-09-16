# Libris Design System

## Direction

**Galley Proof** treats Libris as a working editorial proof rather than a generic
dashboard. Hairline rules, compact metadata, literary typography, and a coral
correction mark over logo-derived indigo surfaces create hierarchy without
decorative cards or shadows.

- Purpose: carry a literary translation from import to reviewed EPUB.
- Audience: translators, editors, and administrators on desktop, tablet, and phone.
- Reference: printer's proofs, production sheets, and red-pencil annotations.
- Memorable element: a coral rail marks the current stage, section, and passage.
- Restraint: no gradients, glass effects, decorative shadows, or arbitrary radii.

## Tokens

The canonical implementation lives in `frontend/src/style.css`. The system uses:

- A `1.2` minor-third type scale for a dense editorial tool.
- A `4/8 px`-rooted spacing scale.
- One `2 px` radius and no shadows.
- Pale lavender paper, navy ink, and coral in light mode.
- Midnight indigo paper, cool white ink, and the same semantic coral in dark mode.
- Logo violet supports secondary progress states without competing with the coral rail.
- Three motion durations, with all non-essential motion disabled when requested.

## Responsive Rules

- `320-720 px`: compact header menu, a thumb-reachable project workflow bar,
  native chapter selector, labeled source/translation cards, and full-width actions.
- `721-896 px`: compact header menu, horizontal project navigation, and single-column
  forms where necessary.
- `897-1120 px`: full global navigation with horizontal project navigation.
- `1121-1439 px`: persistent project rail and compact two-column editorial workspace.
- `1440 px` and wider: full production table and parallel-text working area.

Interactive controls provide visible rest, hover, active, focus, disabled, and busy
states. Touch targets are at least `44 px` on phone-sized viewports.
