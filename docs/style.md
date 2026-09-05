# Style

One light theme, one stylesheet (`main/static/main/app.css`), one script
(`main/static/main/app.js`). Pages add only what is theirs in
`{% block extra_head %}` / `{% block extra_js %}`. There is no dark mode and no
per-member colour: both went with v2's design.

## Tokens

All colours are CSS custom properties on `:root`. Use the token, never the hex.

| Token | Value | Use |
|---|---|---|
| `--panel` | `#fafaf8` | page background |
| `--white` | `#fff` | nav, inputs, buttons, dialogs, auth panel |
| `--paper` | `#f2f1ee` | reserved (canvas background in the mocks) |
| `--ink` | `#14161a` | text, primary buttons, the "you" line, the top podium block |
| `--ink-2` / `--ink-3` | `#3f4348` / `#5f6469` | body prose, secondary values |
| `--muted` / `--muted-2` / `--muted-3` | `#6f7379` / `#8b8f96` / `#9a9ea4` | nav links, stat labels, eyebrows |
| `--dim` / `--placeholder` | `#b3b6bb` / `#c4c2bd` | "—", not-in-yet values, placeholders, venue codes |
| `--rule` / `--rule-2` / `--rule-3` / `--rule-strong` | `#e8e7e3` / `#ecece8` / `#e4e3df` / `#d9d7d2` | dividers: page, table rows, form borders, table head |
| `--fill` / `--fill-2` / `--fill-me` / `--fill-hover` | `#eceae5` / `#dedcd7` / `#eeeeea` / `#f4f3f0` | selected side, podium steps, your row, hover |
| `--track` | `#d6d4cf` | toggle off, average line, not-graded mark |
| `--accent` / `--accent-hover` | `#0f7a6c` / `#0b5c52` | links, "Open", the leader line, the unread dot |
| `--pos` / `--pos-fill` | `#167a4b` / `#dcece2` | right, gained, climbed |
| `--neg` / `--neg-fill` / `--neg-soft` / `--neg-text` | `#a83232` / `#f2dede` / `#f7ecec` / `#8e2b2b` | wrong, fell, errors |

Fonts: `--sans` IBM Plex Sans 400/500/600/700, `--mono` IBM Plex Mono 400/500/600,
both from Google Fonts with system fallbacks. `.n` puts an element in mono with
tabular numerals — use it on every number.

Radius: `--r: 5px` on buttons, inputs, dialogs. Chips are 3–4px. Avatars and
dots are circles. Nothing else is rounded.

Widths are fluid between a floor and a ceiling. `--w-page` is 1060px up to a
1280px window, then 82% of the window, never past 1400px; `--w-narrow` (admin
pages) runs 880px to 1100px the same way; `--w-read` (rules, preseason, league
form) is a fixed 680px reading measure. Rails are `minmax(300px, 24%)` so they
grow with the page. Horizontal padding `--pad-x` 28px, 18px under 800px.

## Type scale

| Class | Spec | Where |
|---|---|---|
| `.eyebrow` | 10px mono 600, uppercase, `.14em` tracking, `--muted-3` | section and page labels |
| `.meta` | 12px mono, `--muted` | the line under an eyebrow |
| `.status` | 12px mono 600 uppercase, `.1em`; `.is-notout/.is-open/.is-locked/.is-final` | the one status word |
| `.stat-label` / `.stat-value` | 10.5px mono / 14px mono 600 (`.big` = 22px) | a figure and its label |
| body | 14px sans, line-height 1.5 | prose, table cells are 13.5px |
| `.h1` / `.h2` | 30px / 18px, 700, `-.02em` | dialog titles; page titles are eyebrows, not headings |

## Layout

- `.page` centres the content; `.page-narrow` / `.page-read` shrink it. Set with `{% block main_class %}`.
- `.page-head` is the strip at the top of every page: eyebrow + meta or status on the
  left, controls on the right (`.page-head-end`), a 1px ink rule below.
- `.split` is main + 300px `.rail`, rule between; the rail becomes a top-ruled block under 1000px.
- `.stats` / `.stat` are figures side by side, split by left rules.
- `.rows` / `.row` (`.row-k`, `.row-v`) is a ruled key/value list; `.kv` is a compact two-column `dl`.
- `.table` is a ruled table: uppercase mono heads, 1px row rules, `.is-me` fills your row,
  `.hover` adds a hover fill, `th[aria-sort]` marks the sorted column. `.bar`/`.bar-fill` is the season bar.
- Breakpoints: 999px (rails stack), 800px (nav collapses to the burger), 640px (phone).
- Page-only layout lives in that page's `{% block extra_head %}` and is prefixed by the page:
  home's `.home-*` grid and its `.hide-phone` columns, picks' `.pk-*` list.

## Components

`.btn` + `.btn-primary` (ink), `.btn-secondary` (white, rule border), `.btn-danger`
(red text), sizes `.btn-sm` / `.btn-xs`, `.btn-block`, `.btn-split` (text left,
figure right). `.icon-btn` is a 26px square. `.btn-link` is an inline text button.

Forms: `.field`, `.label` (eyebrow-style), `.label-row` (label + note on one line),
`.input` / `.select` / `.textarea` (`.textarea.mono` for prompts), `.hint`, `.error`,
`.alert` (red left rule; `.alert-ok` green), `.check` (checkbox + text),
`.chips` / `.chip-check` (toggle chips), `.toggle` (switch: hidden checkbox, `.toggle-track`, `.toggle-knob`, `.toggle-text` with `<b>` and `<small>`).

`dialog.dialog` is a native `<dialog>`: `.dialog-head` (`.grow` + `.icon-btn` close),
`.dialog-title`, `.dialog-body`, `.dialog-actions`. Flat backdrop, no blur. Open
with `showModal()`, close on backdrop click, return focus to the opener.

Chips `.chip-pos` / `.chip-neg` / `.chip-open`; legend `.legend` with `.swatch`
(line) or `.swatch-sq`; `.avatar` (`.is-me` inverts it); `.podium` with `.pod-1/2/3`
(display order 2, 1, 3; the number on the block is the rank); `.mail-row` with
`.mail-dot.is-new` on the newest; `.side` (a pick side, `:checked + .side` selects it);
`.res` / `.res-mark` (a graded row with a 3px left bar).

Nav: `.nav` > `.nav-in` > `.nav-brand` (the league name), `.nav-links`, `.nav-right`
(`.nav-week`, `<details class="nav-menu">` menus), `<details class="nav-burger">`
for phones. `aria-current="page"` comes from `request.resolver_match.url_name`.

## Page patterns

- Every page opens with `.page-head`. The page's name is the eyebrow; the meta line is a
  fact (a count, a date, a status), not a sentence about the page.
- Home: the `.home` grid (defined in `home.html`) — status block, standings (`main/_standings.html`: podium, chart, table), mail.
  The status block is one status word, one fraction with its label, opens, locks, one action.
- Lists of games use two columns (`.pk-list`, in `picks.html`) above 900px, one below.
- The same markup is never written twice. If JavaScript needs to insert a row, the server
  renders the partial and returns it (`ajax_leaderboard` → `_standings.html`,
  `ajax_add_game` → `_game_row.html`).

## Copy rules

- Labels are nouns: "Picks", "Members", "Rules", "picks in", "right so far".
- No taglines, no subtitles that restate the heading, no reassurance ("Nothing else."),
  no exclamation marks, no emoji, no "→" on links or buttons.
- Empty states are one line and not italic: "No mail yet.", "No seasons finished yet."
- Status words: Not out · Open · Locked · Final. Fractions read `n/m`.
- Hints exist only where the control would otherwise be misread (the join code, the
  ballot address). A hint states a fact in one sentence.
- Buttons name the action: "Make your picks", "Save auto-pilot settings", "Rotate".
  Destructive buttons confirm in a browser `confirm()` that says what is lost.

## Times

Inputs on the dashboard are read in `LeagueSettings.auto_tz` and converted to UTC by
the view. Displays are written as UTC ISO strings in `data-utc-date`, `data-utc-time`,
`data-utc-full`, `data-utc-day`, `data-utc-daytime` and rendered in the viewer's
browser by `app.js`. Never print a server-side weekday next to a converted time — west
of the server they disagree by a day. `data-countdown-to="<iso>"` (optional
`data-countdown-done="text"`) ticks a single target; the picks page's milestone
clock is `main/_countdown.html`, fed by `views._countdown()`.

## Gotchas

- `{# ... #}` cannot span lines; a multi-line one renders as text. Use `{% comment %}`.
- Django templates refuse variables beginning with an underscore.
- `[hidden]` loses to any `display:` rule you add — pair `.thing[hidden] { display:none }` with it.
- The manifest static storage 500s on a `{% static %}` target that does not exist, but only
  after `collectstatic`; the test suite uses plain storage.
