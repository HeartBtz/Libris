# Libris Design System

## Direction

Libris is a working tool for editors and translators: calm, dense and modern, in the
spirit of Linear, Notion or Things. The interface stays quiet so that the book's text
carries the attention.

- One sans-serif interface font (Inter, self-hosted) and a book serif only for the text
  being translated (Charter, Iowan Old Style, Palatino, Georgia). Monospace is kept for code
  and JSON.
- One restrained accent (indigo) and soft semantic tints for states; no decorative
  gradients.
- Surfaces are separated by hairline borders and light shadows, not by heavy boxes.
- Each screen has one highlighted action; everything else is secondary, ghost or in a menu.

## Tokens

All colours, sizes and durations are CSS variables in `frontend/src/styles/tokens.css`;
component and screen styles only reference tokens.

| Group | Tokens |
| --- | --- |
| Surfaces | `--bg`, `--surface`, `--surface-sunken`, `--surface-hover`, `--surface-active`, `--overlay` |
| Text | `--text`, `--text-muted`, `--text-subtle` |
| Lines | `--border`, `--border-strong`, `--border-input` (3:1 against `--surface`) |
| Accent | `--accent`, `--accent-hover`, `--accent-soft`, `--accent-text`, `--on-accent`, `--focus` |
| States | `--success`, `--warning`, `--danger`, `--info`, each with `-soft` (tinted background) and, where needed, `-solid` |
| Formatting codes | `--marker-bg`, `--marker-text` |
| Type | `--text-xs` 12 · `--text-sm` 13 · `--text-md` 14 · `--text-lg` 16 · `--text-xl` 20 · `--text-2xl` 24 · `--text-3xl` 30, `--text-book` 17 with `--leading-book` 1.6 |
| Space | 4 px base: `--space-1` 4 … `--space-12` 48 |
| Radii | `--radius-sm` 6 · `--radius-md` 10 · `--radius-lg` 14 |
| Motion | `--duration-fast` 120 ms · `--duration-normal` 180 ms · `--duration-slow` 260 ms |

The light theme is "warm paper" (`#fbfaf8` background, white surfaces, dark slate text,
`#4f46e5` accent). The dark theme is neutral graphite (`#161618` background, `#818cf8`
accent), never navy. Text pairs meet WCAG AA and input boundaries reach 3:1; the
showcase spec checks these ratios for both themes. The theme follows
`prefers-color-scheme` until the user picks Light, Dark or System in the account menu;
the choice is kept in `localStorage`.

## Layers

`frontend/src/styles/index.css` imports, in order: `tokens.css`, `base.css` (reset,
typography, focus ring, reduced motion), `components.css`, `shell.css` (sidebar, page
header) and one file per screen under `styles/screens/`.

## Components

Reusable components live in `frontend/src/ui/` and are exported from `ui/index.ts`:
`Button` (primary, secondary, ghost, danger; sm, md, lg), `IconButton` (always named,
with a tooltip), `Badge` and `StatusPill`, `Card`, `Callout`, `Stat`, `PageHeader`,
`Tabs` and `TabPanel`, `SegmentedControl`, `Field`, `Input`, `TextArea`, `Select`,
`Checkbox`, `Switch`, `Dialog` (focus trap, Escape, nested dialogs), `useDialogs()`
(`confirm` and `prompt`, replacing the native dialogs), `useToast()`, `Menu` (keyboard
navigation, radio items), `Tooltip`, `ProgressBar` (native `<progress>`), `EmptyState`,
`Skeleton` and `LoadingBlock`, and `Table`. Icons are a small inline SVG set in
`ui/icons.tsx`.

Loading and empty are distinct states: skeletons while data loads, an empty state only
once the answer is known to be empty.

## Formatting codes in the editor

Source passages render `⟦tN⟧…⟦/tN⟧` as highlighted formatting and `⟦xN⟧` as a small
marker. The translation field stays a plain textarea so the exact text, codes included,
is what the API receives; a mirror layer behind it paints the codes as discreet chips.
Both layers share every font metric; in forced-colours mode the mirror is hidden and the
textarea shows its own text.

## Responsive rules

- `≤ 640 px`: single column, stacked forms, 40 px minimum touch targets, larger inputs.
- `≤ 900 px`: the sidebar becomes a drawer opened from the top bar; the book editor
  shows one column per passage and a section selector; the library switches to cards.
- `≤ 1100 px`: side panels (book settings, Book Bible, characters, settings lists)
  move under the main content.
- Wider screens keep the collapsible sidebar, the chapter list and the two-column editor.

No width from 320 to 1920 px may scroll horizontally; wide tables scroll inside their own
container. Interactive elements show hover, focus-visible, disabled and busy states, and
non-essential animation is disabled under `prefers-reduced-motion`.

## Language

Visible text goes through `t()` (keys are the French text; English is registered with
`registerTranslations`). Counts use `tp(count, one, other)`, which picks the form with
`Intl.PluralRules`; numbers, percentages and dates use the `Intl` helpers of `i18n.tsx`.
