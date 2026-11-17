# Portal themes

The portal ships two themes over one style sheet: a black theme, which is what
a browser without a stored preference opens, and a light theme. Both are
produced by the same rules; only the token values differ.

## The token layer

`frontend/src/style.css` starts with two blocks:

```css
:root { /* black: the default */ }
:root[data-theme="light"] { /* light overrides */ }
```

Every other rule reads `var(--…)` and no rule carries a colour literal. The
same 60 tokens are defined in both blocks, so a missing value is a test
failure rather than an invisible element. The tokens are grouped by role:

| Group | Tokens | Use |
| --- | --- | --- |
| Surfaces | `--bg`, `--surface`, `--surface-raised`, `--surface-sunken`, `--surface-muted`, `--surface-tint`, `--surface-accent`, `--surface-translucent` | Page, panels, canvas, table heads, hovered rows, tinted tiles |
| Sidebar | `--sidebar`, `--sidebar-raised`, `--sidebar-hover`, `--sidebar-active`, `--sidebar-border`, `--sidebar-border-strong` | Navigation rail and the login story panel |
| Text | `--ink`, `--text`, `--text-secondary`, `--muted`, `--faint`, `--ghost` | Headings down to micro captions |
| Text on dark chrome | `--on-dark`, `--on-dark-muted`, `--on-dark-faint`, `--accent-on-dark` | Sidebar and login story, which stay dark in both themes |
| Borders | `--border`, `--border-soft`, `--border-subtle`, `--border-accent` | Rules, card outlines, focus borders |
| Accent | `--accent`, `--accent-fill`, `--accent-fill-hover`, `--accent-ring`, `--accent-soft-bg`, `--accent-soft-border` | Links, primary buttons, selection, tinted helpers |
| Status | `--success-*`, `--warning-*`, `--danger-*`, `--info-*` (`bg`, `border`, `text`) | Badges, notices, validation results |
| Console | `--console-bg`, `--console-text`, `--console-muted`, `--console-divider`, `--console-phase`, `--console-error`, `--console-warning` | Execution log, deliberately dark in both themes |
| Overlays | `--overlay`, `--shadow-soft`, `--shadow-panel`, `--shadow-modal`, `--shadow-drawer` | Backdrops and elevation |

`--radius` is geometry and lives with the typography block, not with the
colour tokens.

The black theme is the `:root` default, not a `data-theme="dark"` override, so
a browser that has no JavaScript still renders the intended theme and no rule
has to undo a light default.

## Preference and first paint

The preference is stored in `localStorage` under `kuberweaver.theme`. Only
`dark` and `light` are accepted; anything else, an empty value, or an
unreadable store falls back to black. The value is a preference for one browser
profile, not an account setting, so it survives logout and is not shared
between operators on the same workstation.

`frontend/public/theme-boot.js` runs from `<head>` before the application
module and sets `<html data-theme>` plus the `theme-color` meta tag. It is a
classic script rather than an inline one because the app is served under
`script-src 'self'`, which refuses inline scripts; the storage key and both
colours are asserted against `src/theme.ts` in `theme.test.ts`. Setting the
attribute before the first paint is what prevents a flash of the wrong theme.

`useTheme()` reads the attribute the boot script already applied, keeps the
document in step if it mounts without one, and listens for `storage` events so
a second tab follows a switch made in the first. A refused write costs
persistence only: the switch still applies to the open page.

The toggle lives in the top bar and on the login card. Its accessible name is
`Black theme` and `aria-pressed` carries the state, so the name does not change
under a screen reader when the theme flips.

## Colour and contrast rules

The light values keep the palette the portal already used, consolidated into
the scales above; secondary text was moved to values that clear 4.5:1 on
`--surface`. `theme.test.ts` enforces a contrast floor for both themes:

- body, headings, secondary text, links, status text, sidebar text and console
  text: at least 4.5:1
- small captions (`--faint`): at least 3.5:1
- decorative separators (`--ghost`): at least 2.9:1

Adding a colour means adding the token to both blocks and reading it with
`var()`. The style sheet test fails on a literal `#rgb`, `rgb()`, `hsl()` or
`color-mix()` outside the two token blocks, and on a token that is used but not
defined in either theme.

## Fonts

Nothing is fetched at runtime. The style sheet asks for `Inter` first and falls
back to the platform UI font (`-apple-system`, `Segoe UI`, sans-serif); a
workstation without Inter gets its system font, not a webfont request. There is
no `@import`, no font CDN and no `url(https://…)`; the test refuses all three.
