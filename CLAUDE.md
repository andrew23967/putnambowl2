# PutnamBowl v3 — project guide

NFL pick'em leagues. Members pick a winner in every game each week; points are
weighted by the money line, so underdogs are worth more. Django, deployed on
Railway. **Live: https://www.putnambowl.com/** — league `putnambowl`, plus any
leagues the site admin creates at `/leagues/`.

This file is the map. The reasoning behind each area lives in `docs/`; read
the relevant one before changing that area.

## Stack

- Django 6, Python 3.13. SQLite locally, Postgres on Railway (`dj-database-url`), WhiteNoise.
- `nfl-data-py` (schedule + money lines), ESPN public API (live scores).
- Gemini (`google-genai`, model from `GEMINI_MODEL`) for recaps, PutnamBot's picks and reading emailed picks.
- One Gmail mailbox for every league: IMAP in, SMTP out. Resend only as a fallback.
- One stylesheet, `main/static/main/app.css`; one script, `app.js`. No CSS framework, no build step.

## Layout

```
config/settings.py         env-driven; LOGGING; TESTING flag; static storage swap under test
leagues/                   League model, site admin views (/leagues/), access decorators
  access.py                current_league, current_settings, league_required, league_manager_required
main/
  models.py                LeagueSettings (one row per league), Game, Pick, WeeklyLeaderboard,
                           LeagueEmail, ProcessedEmail, IntroTemplate, SeasonRecord
  views.py                 every member and manager page; standings_rows() feeds home + AJAX
  auto.py                  the autopilot: scrape/validate/publish/lock/grade/advance, tick_all_leagues
  scrape.py                nflverse + ESPN readers, current_season_year()
  scoring.py               calculate_points() — the only copy of the formula
  charts.py                inline SVG points charts (home, season page)
  seasons.py               the season archive: build_season_record, archive_and_reset, finishes
  recap_stats.py           the week's "angles" for the recap prompt
  rankings.py              competition ranking — ties share a place
  email_utils.py           outbound mail, recipients, feed rows
  inbound_email.py         IMAP poll, routing by sender
  pick_email.py            picks out of an email, via Gemini
  ai_picks.py              PutnamBot's picks
  teams.py                 TEAMS, conferences, abbreviations, game ids
  management/commands/     run_auto (the worker), fetch_emails, create_putnambot
accounts/                  Profile (league, role, score, preseason, mail opt-outs), auth views
templates/                 base.html + one template per page; _standings.html, _game_row.html partials
docs/                      the why — see the index at the bottom
../legacy/                 the pre-2026 archive; see docs/legacy.md
```

## Non-negotiables

- **team1 is the favorite, team2 the underdog.** `points1 = 1.0 × multiplier`, `points2 >= points1`.
- **`team1_is_home` is the only venue flag.** True means team1 is at home. (docs/data-model.md)
- **Filter by league AND week.** Games and picks persist forever and every table carries a league. Never derive a league from global state: views use `current_settings(request)`, everything below a view takes `league` or `settings`. (docs/leagues.md)
- **Ranks come from `rankings.competition_ranks`**, never from row position.
- **Re-scraping updates, never duplicates** — `Game.match_existing(league, week, team1, team2, game_id)`.
- **Every schema change is a migration.** Never squash. Write `RenameModel`/`RenameField` by hand: autodetect turns a rename into drop + create and loses the data (`0019`, `0028`).
- **One mailbox routes by sender.** A sender in two leagues is refused, not guessed. (docs/email.md)
- **Nothing is mailed under the test runner** — `settings.TESTING` feeds `outbound_suppressed()`.

## Style, in five lines

Light paper theme only; IBM Plex Sans for text, IBM Plex Mono for every number, label and
eyebrow. Regions are separated by 1px rules, never cards; radius only on buttons, inputs,
dialogs and dots. Labels are nouns; no taglines, subtitles, reassurance copy, emoji, arrows,
gradients, glows or entrance animations; empty states are one short line. Times are written
as UTC in `data-utc-*` attributes and rendered in the browser. Full rules: docs/style.md.

## Commands

```bash
.venv\Scripts\activate
python manage.py runserver
python manage.py run_auto                      # the worker — every active league, one mailbox
python manage.py test main accounts leagues    # 289 tests, ~75s
python manage.py makemigrations --check --dry-run
python manage.py fetch_emails --check          # mailbox diagnostics
python manage.py create_putnambot --league putnambowl
```

Don't run `runserver --noreload` (Django 6 caches templates without the reloader).
`manage.py check` does not compile templates; `NavPageRenderTests` does — add new pages
to its `PAGES` list. `manage.py test` uses plain static storage; production uses the
manifest storage, so a missing `{% static %}` target only fails after `collectstatic`.

## Deploy

Railway project `putnambowl`: services **web**, **worker** (`SERVICE_TYPE=worker`), **Postgres**.
`railway.toml` runs `migrate` in preDeploy and `collectstatic` in the start command.
Set `GEMINI_API_KEY`, `SITE_URL`, `IMAP_*` on the **worker** too. Snapshot before any
deploy that carries a migration; rehearse against a `pg_dump` first. Details: docs/deploy.md.

## Docs

- [docs/style.md](docs/style.md) — tokens, components, page patterns, copy rules, time handling
- [docs/data-model.md](docs/data-model.md) — every model, the conventions above with their history, migrations
- [docs/leagues.md](docs/leagues.md) — multi-league: roles, join codes, site admin, isolation checklist
- [docs/autopilot.md](docs/autopilot.md) — the worker's state machine and why each step is shaped as it is
- [docs/data-sources.md](docs/data-sources.md) — nflverse vs ESPN, season year, caching
- [docs/email.md](docs/email.md) — mailbox, routing, the weekly mail, reminders, recaps, picks by email
- [docs/dev.md](docs/dev.md) — local setup, test helpers, verification
- [docs/deploy.md](docs/deploy.md) — Railway, env vars, rehearsal
- [docs/legacy.md](docs/legacy.md) — the original site's archive
- [docs/v3-changes.md](docs/v3-changes.md) — what v3 removed or replaced, and why
