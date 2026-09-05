# What v3 removed or replaced, and why

Kept here so the reasoning survives the deletions. Everything below was
confirmed unreferenced or superseded before it went.

## Removed

- **Dark theme and the per-member accent colour** (`Profile.theme`). One paper
  theme, fixed teal accent; `base.html` no longer renders a colour from the database.
- **Tailwind CDN and Inter.** Nine templates used Tailwind classes; four of them
  were unlinked legacy pages. One stylesheet now.
- **The week-picker row in the nav** (`initWeekPicker`, `#nav-week-extra`,
  `nav-has-weekpicker`). Only pick history used it, and its fractional-height
  pinning was a source of hairline bugs. Pick history owns its picker and pager.
- **Legacy pages**: `allpicks`, `standings` (a CBS Sports HTML scrape),
  `seasons` (rebuilt from the archive instead), `secretanalytics`,
  `public_profile` (the members page shows everything it did), the old
  `pickhistory.html` orphan.
- **The strategy page** (`/strategy/`, `montecarlo.py`, `strategy_report.py`,
  `build_strategy`, `data/strategy_report.json`). Ten-season Monte Carlo of pick
  strategies, unlinked from the nav because its conclusion — no simple rule
  beats the odds — is a strange thing to hand someone about to pick. Two known
  statistical traps lived in it (error bars from picking randomness only;
  deterministic 0%/100% rates with zero variance).
- **Devtools and the simulator** (`main/sim.py`, `/dashboard/devtools/`),
  dev-only commands `seed_demo` (already broken on import), `seed_bots`,
  `test_auto`, `bot_picks`; the legacy importer; `scripts/verify_history_migration.py`.
- **Dead models and fields**: `Message`, `Bug`, `SiteSettings.edit`,
  `scrape_filter_from_day/to_day` (a from/to range replaced by the `scrape_days`
  set), `Profile.unread_messages`; dead functions `_game_day_in_filter`,
  `send_recap_email` (recaps ride inside the weekly mail), `AdjustScoreForm`,
  `BugForm`, the `add_one` filter; the `LEAGUE_LIST_ADDRESS` variable nothing read.
- **The `save_profile` signal** that re-saved a profile whenever its user saved.
  It hid every view that forgot to save the profile itself.
- **`Procfile`**, which disagreed with `railway.toml` and never ran collectstatic.
- **Tracked junk**: `backup.json`, `static/temp_check.js`.
- **The "biggest upset" and pick-distribution figures** computed for the picks
  page that no template read.
- **The analytics page** (`/analytics/`): three Chart.js line charts of the same
  series the home page now draws inline. Nothing else loads Chart.js.
- **Copy**: taglines, subtitles, reassurance hints, emoji, "→" links, italic
  empty states, gradients, glows, blur, entrance animations, the chrome logo SVG.

## Replaced

- `SiteSettings` singleton → `LeagueSettings` per league (docs/leagues.md).
- `print()` → `logging`.
- Three copies of the points formula → `main/scoring.py`.
- Two hardcoded Gemini model names → `GEMINI_MODEL`.
- The dashboard's own advance-week code → `do_advance_week`.
- Home's JavaScript copy of the leaderboard row → the server-rendered
  `_standings.html`; the dashboard's JavaScript copy of the game row → `_game_row.html`.
- The ⚙ popout that staged hidden inputs for the next Scrape → a dialog that posts.
- Chart.js on every page → none; the home and season charts are inline SVG.

## Fixed

- Manual lock mode stalled after one week (docs/autopilot.md).
- "Save Season & Reset" wiped the season without writing the record.
- Un-publishing re-published and re-mailed on the next tick.
- The profile form never saved the profile (it relied on the removed signal).
- The test suite depended on a stale `staticfiles/` directory existing.

## Added

- Leagues, join codes, the site admin.
- The season archive and the Seasons pages; past finishes on Members and the profile.
- Per-member opt-outs for the weekly mail and the reminder.
- Per-league rules with an editor.
- Password change.
