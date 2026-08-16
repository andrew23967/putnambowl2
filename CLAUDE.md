# PutnamBowl2 — Project Guide

NFL pick'em league web app. Members pick winners each week; points are weighted by moneyline odds (underdogs worth more). Built with Django, deployed on Railway.

## Stack

- **Backend**: Django 6, Python 3.13
- **Database**: SQLite locally, Postgres on Railway (via `dj-database-url`)
- **Static files**: WhiteNoise
- **Scraping**: `nfl-data-py` (moneylines + schedule) and ESPN public API (live scores)
- **Frontend**: Tailwind CSS (CDN), vanilla JS
- **Deployment**: Railway — web service + worker service

## Project layout


```
putnambowl2/
  config/          # Django settings, root urls, wsgi
  main/            # Core app: games, picks, leaderboard, scraping, automation
    models.py      # Game, Pick, SiteSettings, History, WeeklyLeaderboard, Announcement, SeasonRecord
    views.py       # All views including pickdash (admin) and home
    scrape.py      # scrape() and grade() for nfl-data-py and ESPN; get_first_game_dt()
    auto.py        # Automation logic: do_scrape_and_publish, do_grade, do_advance_week, auto_tick
    teams.py       # Team name ↔ abbreviation mappings
    urls.py        # All main URL patterns
    management/commands/run_auto.py  # Worker process: calls auto_tick() every 5 min
  accounts/        # Auth app: Profile model, login/register/profile views
    models.py      # Profile (OneToOne → User): score, theme, favorite_team, etc.
                   # post_save signal on User → auto-saves Profile
  templates/
    base.html
    main/home.html       # Player-facing home page (pick form + leaderboard)
    main/pickdash.html   # Admin dashboard
    accounts/            # Login, register, profile pages
```

## Key models

**SiteSettings** (singleton, pk=1): controls the whole site state
- `week` — current week number
- `publish` — whether picks are open to players
- `lock_picks` — picks frozen, scores visible
- `edit` — players can change picks
- `multiplier` — base point multiplier (1×, 2×, 4×)
- `grade_api` — `nfl_data_py` or `espn`
- `weekly_recap` — auto-generated recap text shown on home page
- `auto_enabled`, `auto_scrape_weekday`, `auto_scrape_hour`, `auto_lock_offset_minutes`, `first_game_dt` — automation settings

**Game**: **team1 is the FAVORITE, team2 is the UNDERDOG.** `points1` is always `1.0 × multiplier`; `points2` is the higher underdog value — so `points1 <= points2` holds for every row. This is easy to get backwards (the docs here previously said the opposite, and it caused a real bug in the home page's "biggest upset" panel). Also has home_team (True = team2 is home), game_id, game_dt, `week` (IntegerField — which league week this game belongs to). Games are never deleted between weeks; they accumulate.

**Pick**: user + game + choice (team1/team2). `is_correct` and `points_earned` are properties. Picks are never deleted between weeks; they persist forever alongside their Game.

### Games accumulate — always filter by week

Because Game/Pick rows now persist forever, any query that means "this week" must
say so. Unscoped queries were the source of several bugs (auto-lock firing
instantly, bots back-filling completed weeks, division rematches being dropped as
duplicates). `main/tests.py` has regression tests for each.

**WeeklyLeaderboard**: end-of-week snapshot of cumulative scores (saved by `do_advance_week`). Used for historical leaderboard views and charts since deriving scores from Pick sums is expensive.

**Profile** (in `accounts`): score (running total), theme colour, preseason picks (nfc_champ, afc_champ, superbowl_winner, etc.)

## Points formula

The favorite (team1) always gets `multiplier` points. The underdog (team2) gets
`_calculate_points(ml_a, ml_b) * multiplier`. Defined identically in `views.py`
and `auto.py`.

`_calculate_points` reduces to `sqrt(|ml_a| * |ml_b|) / 100` — the geometric mean
of the two moneylines. It is **symmetric**, so despite the `underdog_ml,
favorite_ml` parameter names the call sites pass them in the opposite order
without affecting the result. Don't "fix" the argument order expecting a change.

## Automation (auto-pilot)

When `auto_enabled=True`, the `run_auto` worker ticks every 5 min:
1. **Scrape + publish** on configured weekday + UTC hour
2. **Lock picks** at `first_game_dt - auto_lock_offset_minutes`
3. **Grade games** every tick while locked, using whichever source
   `settings.grade_api` names — **not** always ESPN. Production is set to
   `nfl_data_py`, which lags 1–3 days, so games do not grade live on Sunday and
   the week won't advance until nflverse publishes results.
4. **Advance week** when all graded + it's Mon/Tue/Wed after 6 AM UTC
5. **Multiplier** is auto-set at scrape time based on week type — no manual action needed for playoffs or Super Bowl.

Toggle Auto-Pilot on/off and configure schedule in the Pick Dashboard.

## Admin dashboard routes

All require `@staff_member_required`:
- `/dashboard/picks/` — main control panel (scrape, grade, advance week, automation)
- `/dashboard/accounts/` — manage user accounts
- `/dashboard/announcements/` — post announcements
- `/dashboard/generate-recap/` — AJAX endpoint to generate weekly recap
- `/dashboard/devtools/` — developer tools: create/delete bots, run season simulation
- `/dashboard/devtools/sim/` — POST to start/stop simulation; `/dashboard/devtools/sim/status/` — GET sim status JSON

## Notable endpoints

- `/home/` — player home page (no week in URL; always shows current week)
- `/home/leaderboard/?week=N` — AJAX endpoint returning leaderboard JSON for any week (used by slider)
- `/site-state/` — lightweight JSON endpoint (`week`, `publish`, `lock_picks`); polled every 15s by base.html to trigger auto-reload on state changes

## Local dev

```bash
cd putnambowl2
.venv\Scripts\activate          # Windows
python manage.py runserver
python manage.py run_auto       # Run the automation worker locally (ticks every 5 min)
python manage.py createsuperuser  # Required to access /dashboard/ routes
```

Runs against local SQLite. No `.env` needed — defaults are set in `settings.py`.

**Don't use `--noreload`.** Django 6 wraps the template loaders in the cached
loader even when `DEBUG=True`. Plain `runserver` is fine — the autoreloader
resets that cache when a template changes — but under `--noreload` nothing
invalidates it, so `.html` edits silently have no effect and you end up chasing
changes that appear not to apply. Same for `.py` edits.

### Tests

```bash
python manage.py test main
```

`main/tests.py` covers the week-scoping regressions, the schedule cache, and the
History archive decoding. Run it before pushing.

## Deployment (Railway)

**Live site: https://www.putnambowl.com/**

Railway project `putnambowl`, environment `production`, three services:
- **web** — `railway.toml` handles it: preDeployCommand runs collectstatic + migrate, startCommand runs gunicorn
- **putnambowl2** (the worker) — same repo; `SERVICE_TYPE=worker` makes the
  startCommand run `python manage.py run_auto`
- **Postgres**

GitHub repo: `andrew23967/putnambowl2` on `main` branch → pushing to `main`
auto-deploys **both** web and worker.

Note: both services run `preDeployCommand`, so `migrate` runs twice on a deploy.
The second one logs "No migrations to apply" — that is expected, not a sign the
migration was skipped. Check `django_migrations` if you need to be sure.

The Railway-generated domain (`web-production-*.up.railway.app`) returns
**400 DisallowedHost** — only the custom domain is in `ALLOWED_HOSTS`. Use
putnambowl.com when testing production.

Useful CLI checks (the CLI is already linked to the project):

```bash
railway status --json          # service + deployment status
railway logs --service web --deployment
railway run --service Postgres <cmd>   # runs locally with DATABASE_PUBLIC_URL set
```

## Transferring local data to Railway

```powershell
# 1. Dump from local (DATABASE_URL must be empty)
$env:DATABASE_URL=""
$env:PYTHONUTF8=1
python manage.py dumpdata --exclude contenttypes --exclude auth.permission --exclude admin.logentry --exclude sessions --natural-foreign --natural-primary -o backup.json

# 2. Flush Railway DB and load
$env:DATABASE_URL="postgresql://postgres:PASSWORD@thomas.proxy.rlwy.net:38369/railway"
python manage.py flush --no-input
python manage.py loaddata backup.json
```

Use Railway's `DATABASE_PUBLIC_URL` (not `DATABASE_URL` — that's internal only and unreachable from localhost).

## Migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

Run `python manage.py showmigrations` to see current state.

### Migration 0010 is destructive — verify before deploying

`0010_game_week_remove_history` drops the `History` table. Until it ships,
production stores every completed week as a JSON blob in History and deletes
Game/Pick rows weekly; afterwards those rows persist and carry a `week`.

The migration rebuilds Game/Pick rows from the archive **before** the
`DeleteModel` step. Without that step every past week would be lost permanently.
Two archive encodings exist and both are handled — see the module docstring and
`main/history_import.py`. Reconstructed games have no kickoff time, ESPN id, or
home/away, since the archive never stored them.

Prove the round-trip against a real archive before pointing this at production:

```bash
python scripts/verify_history_migration.py [path/to/legacy/db.sqlite3]
```

It builds a throwaway DB at main/0009, seeds it from the legacy dump plus a live
week, migrates, and asserts game/pick counts and per-week scoring survived.
**Take a Railway database snapshot before the deploy that carries 0010.**

## Scraping notes

One setting, `SiteSettings.grade_api` (`nfl_data_py` | `espn`), selects the source
for **both** scraping and grading. `scrape()` and `grade()` in `scrape.py` just
dispatch on it.

- `nfl-data-py` (nflverse): bulk `import_schedules([year])` download. Finds games
  from `home_team`/`away_team`/`week`; grades from `result` (home margin).
  **The only source of moneylines**, so the whole points system depends on it.
  Lags 1–3 days after games end — no live scores.
- ESPN API: public, no key needed, **has live scores**. But `scrape_espn()`
  returns moneylines of 0, so anything scraped through ESPN is worth a flat
  `multiplier` with no underdog bonus. Fine for grading, lossy for scraping.
- Ideal combination is scrape with nfl-data-py (for the lines) + grade with ESPN
  (for live scores). The abbreviation fallback in `do_grade()` already supports
  the mismatched ids this produces, but there is currently **one** setting
  driving both, so this is not selectable from the dashboard yet.
- `get_first_game_dt(week, year)`: hits ESPN to get UTC kickoff time for auto-locking.
- If scraped with nfl-data-py and graded with ESPN, game IDs may differ — `do_grade()` in `auto.py` has a team-abbreviation fallback match.
- `get_week_type(week, year, allow_network=True)`: returns `'regular'`, `'playoffs'`, or `'superbowl'` using nfl-data-py's `game_type` field (`REG`/`WC`/`DIV`/`CON`/`SB`). Falls back to week number (assumes 18-week regular season, valid 2021+). **Request handlers must pass `allow_network=False`** — the download took ~1.3s on the player home page. The week-number fallback is accurate for 2021+.
- `_get_schedule()` caches the nflverse schedule for `SCHEDULE_TTL_SECONDS` (30 min). The TTL matters because the worker is a long-lived process: without it, it served whatever it downloaded at boot and never saw new results. On a failed download it falls back to the stale copy rather than erroring.
- nfl-data-py uses weeks 19–22 for playoffs (19=Wild Card, 20=Divisional, 21=Conference, 22=Super Bowl). ESPN uses `seasontype=3` with weeks 1–4 for the same rounds — `_espn_season_params(week)` in `scrape.py` handles the conversion.
- `do_scrape_and_publish()` in `auto.py` auto-calls `get_week_type()` and sets `settings.multiplier` (1×/2×/4×) before creating games. No manual multiplier change needed for playoffs/Super Bowl.

## In-progress / experimental files in main/

- `montecarlo.py` — Monte Carlo strategy analysis, wired to `/strategy/`. Identifies the underdog from moneylines directly and does not use Game rows, so the team1/team2 convention above does not apply to it.
- `sim.py` — season simulator; runs a full NFL season (weeks 1–22 including playoffs) using the auto-pilot functions. Accessible via devtools. State is module-level (daemon thread + `threading.Event`) — resets on process restart.
- `email_utils.py` — sends the "picks are live" email via Resend when a week is published. Sends **one message per recipient**; never put the league in a single `to`, which would disclose every member's address.
- `history_import.py` — decodes the legacy History archive. Used by `import_old_data`; migration 0010 keeps its own frozen copy.

## Known gaps (not yet addressed)

- **Analytics chart has no legend** — 17 lines, no way to tell which is yours.
- **Pick History** has no heading or legend, and clipped vertical column labels.
- `DEBUG` defaults to `True` and `ALLOWED_HOSTS` to `['*']` in `settings.py`; safe
  only because Railway sets them. No `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE`
  / HSTS.
- `/strategy/` runs a 2,000-trial Monte Carlo with no login required.
- Tailwind loads from `cdn.tailwindcss.com`, which is not intended for production.
  Very few Tailwind classes are actually used — mostly inline styles.
- `standings()` scrapes CBS Sports HTML and uses the deprecated bs4 `text=`
  kwarg; `/standings/` is no longer linked from the nav.
- `Message` and `Bug` models are unused leftovers still registered in admin.
