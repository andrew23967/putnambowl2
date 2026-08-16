# PutnamBowl — Project Guide

Private NFL prediction league. Members pick a winner in every game each week;
points are weighted by moneyline odds, so underdogs are worth more. Django,
deployed on Railway. **Live: https://www.putnambowl.com/**

## Stack

- Django 6, Python 3.13
- SQLite locally, Postgres on Railway (`dj-database-url`)
- WhiteNoise for static files
- `nfl-data-py` (schedule + moneylines) and the ESPN public API (live scores)
- Vanilla JS and hand-written CSS driven by custom properties. Tailwind is still
  loaded from CDN in `base.html` but only a handful of classes are used.
- Gemini for weekly recaps and PutnamBot's picks; Resend for email

## Layout

```
putnambowl2/
  config/settings.py            # env-driven; security block gated behind DEBUG
  main/
    models.py                   # Game, Pick, SiteSettings, WeeklyLeaderboard,
                                #   Announcement, SeasonRecord
    views.py                    # every view, incl. home and pickdash
    auto.py                     # auto-pilot: scrape/lock/grade/advance + auto_tick
    scrape.py                   # scrape()/grade() over both data sources
    ai_picks.py                 # Gemini picks for PutnamBot
    history_import.py           # decodes the legacy History archive
    montecarlo.py               # /strategy/ analysis
    sim.py                      # season simulator (devtools)
    email_utils.py              # "picks are live" mail via Resend
    management/commands/run_auto.py      # the worker loop
  accounts/                     # Profile (OneToOne → User) + auth views
  templates/
    base.html                   # design tokens, nav, theme toggle
    accounts/auth_base.html     # shared shell for login + register
    main/home.html              # picks + leaderboard
    main/pickdash.html          # admin control panel
    main/pick_history.html      # player × game grid
  scripts/verify_history_migration.py
../legacy/                      # original site archive — see its README
```

## Core conventions

### team1 is the FAVORITE, team2 is the UNDERDOG

`points1` is always `1.0 × multiplier`; `points2` is the larger underdog value,
so `points1 <= points2` holds for every row. This is the single easiest thing to
get backwards — these docs once claimed the opposite, which produced a real bug
where the home page reported favorite wins as upsets.

### Games accumulate — always filter by week

Game and Pick rows persist forever and carry a `week`. Any query meaning "this
week" must say so. Unscoped queries caused auto-lock firing instantly, bots
back-filling completed weeks, and division rematches being dropped as duplicates.
`main/tests.py` has a regression test for each.

### Points formula

`_calculate_points` reduces to `sqrt(|ml_a| * |ml_b|) / 100` — the geometric mean
of the two moneylines. It is **symmetric**, so despite the `underdog_ml,
favorite_ml` parameter names, call sites pass them in the opposite order without
affecting the result. Don't "fix" the argument order expecting a change.

## Key models

**SiteSettings** (singleton, pk=1) drives the whole site: `week`, `publish`,
`lock_picks`, `edit`, `multiplier`, `scrape_api`, `grade_api`, `weekly_recap`,
and the `auto_*` scheduling fields.

**Game** — see the team1/team2 convention above. Also `home_team`
(True = team2 is home), `game_id`, `game_dt`, `week`.

**Pick** — user + game + choice. `is_correct` and `points_earned` are properties.

**WeeklyLeaderboard** — snapshot of cumulative scores taken by
`do_advance_week`. `WeeklyLeaderboard(week=N)` holds scores as they stood
**before** week N was scored.

**Profile** — score, theme colour, preseason picks, and bot fields (`is_bot`,
`bot_strategy`, `bot_underdog_pct`).

## Bot players

`make_bot_picks(week)` fills in picks for every `is_bot` profile, using
`bot_strategy`:

- `random` — coin flip weighted by `bot_underdog_pct`
- `gemini` — `ai_picks.choose_picks()` asks Gemini for the whole slate in one
  call. Used by `putnambot`; create or refresh it with
  `python manage.py create_putnambot`.

`choose_picks()` is best-effort by design: it runs in the worker, so a missing
key, network failure or malformed reply returns `{}` and the caller fills those
games randomly. A season is never blocked by the model being down — but a
misconfigured worker therefore degrades **silently** to random picks. Check the
logs for `[ai_picks]` if PutnamBot looks arbitrary. `GEMINI_API_KEY` must be set
on the worker service, not just `web`.

## Data sources

Two independent settings choose the source per job (`nfl_data_py` | `espn`),
both editable from the ⚙ popout beside Scrape/Grade in the Pick Dashboard:

- `scrape_api` → `do_scrape_and_publish()` and the Scrape button
- `grade_api` → `do_grade()` and the Grade button

**Recommended: scrape `nfl_data_py`, grade `espn`.**

| | nfl-data-py (nflverse) | ESPN |
|---|---|---|
| moneylines | **only source** | returns 0 → flat 1× scoring |
| live scores | no, lags 1–3 days | **yes** |
| game ids | `2025_01_DAL_PHI` | `{season}_{week}_{away}_{home}` |

Cross-source grading is supported: ids won't match, so `do_grade()` falls back to
matching home/away abbreviations.

Other notes:

- `get_first_game_dt()` always uses ESPN regardless of either setting; it supplies
  the kickoff the auto-lock is computed from.
- `get_week_type(week, year, allow_network=True)` — **request handlers must pass
  `allow_network=False`**. The download cost ~1.3s on the player home page; the
  week-number fallback is accurate for 2021+.
- `_get_schedule()` caches for `SCHEDULE_TTL_SECONDS` (30 min). The TTL matters
  because the worker is long-lived — without it, it served whatever it downloaded
  at boot. A failed download falls back to the stale copy rather than erroring.
- nflverse uses weeks 19–22 for playoffs; ESPN uses `seasontype=3` weeks 1–4.
  `_espn_season_params()` converts.

## Automation

With `auto_enabled`, the `run_auto` worker ticks every 5 min:

1. Scrape + publish at the configured weekday/time
2. Lock picks at `auto_lock_dt`
3. Grade every tick while locked, using `grade_api`
4. Advance the week once everything is graded
5. Multiplier is set automatically from week type (1× / 2× / 4×)

## UI conventions

Both pick controls — the player's team choice and the admin's result selector —
are **radio inputs styled with `:checked`**, not JS-positioned sliders. The
earlier drag-slider kept a knob position in JS that could disagree with the saved
value. Keep selection in `:checked`; let JS only persist.

Neither list reorders on interaction. Games sit in kickoff order and stay there;
sorting picked or graded rows to the bottom moved the list out from under the
person using it.

Colours come from tokens in `base.html` (`--fill-1/2/3`, `--good`, `--bad`,
`--warn`, `--info`, plus `*-fill` variants) which flip with the theme. Use them
rather than literal `rgba(255,255,255,…)`, which needs a `[data-theme="light"]`
override for every component.

## Local dev

```bash
cd putnambowl2
.venv\Scripts\activate
python manage.py runserver
python manage.py run_auto        # worker, optional
python manage.py test main
```

Runs against local SQLite; no `.env` needed.

**Don't use `--noreload`.** Django 6 wraps the template loaders in the cached
loader even when `DEBUG=True`. Plain `runserver` is fine — the autoreloader
resets that cache on template change — but under `--noreload` nothing invalidates
it, so `.html` edits silently do nothing.

## Deployment (Railway)

Project `putnambowl`, environment `production`, three services: **web**,
**putnambowl2** (the worker, via `SERVICE_TYPE=worker`), and **Postgres**.
Pushing to `main` deploys both app services.

`railway.toml`: `migrate` runs in `preDeployCommand`; **`collectstatic` runs in
`startCommand`**. preDeploy runs in a throwaway container, so anything it writes
to disk is discarded — collectstatic there produced `No directory at:
/app/staticfiles/` and 500s on every page once `DEBUG` was off.

Both services run `preDeployCommand`, so `migrate` runs twice per deploy; the
second logs "No migrations to apply". That's expected.

The Railway-generated domain returns **400 DisallowedHost** — only the custom
domain is in `ALLOWED_HOSTS`. Test against putnambowl.com.

```bash
railway status --json
railway logs --service web --deployment
railway run --service Postgres <cmd>    # injects DATABASE_PUBLIC_URL
```

Railway's `DATABASE_URL` points at an internal host unreachable from a laptop.
To run a management command against production, bridge the public URL:

```python
os.environ['DATABASE_URL'] = os.environ['DATABASE_PUBLIC_URL']
```

## Importing from the original site

The original database is at `../legacy/db.sqlite3` — the **only** copy of the
league's pre-2026 history (22 weeks, 19 members). See `../legacy/README.md`.

```bash
python manage.py import_old_data --db ../legacy/db.sqlite3 --users-only --zero-scores --dry-run
python manage.py import_old_data --db ../legacy/db.sqlite3 --users-only --zero-scores
```

- `--users-only` skips history. **Without it the command also rebuilds 22 weeks
  of Game/Pick rows** — rarely what you want on a live season.
- `--zero-scores` prevents last season's totals carrying into week 1.
- `--dry-run` wraps everything in a transaction and rolls back.
- Password hashes copy verbatim, so members keep their existing passwords.
- `is_staff` carries over — `mrfavorite` is a staff+superuser account from the
  old site.

## Migrations

```bash
python manage.py makemigrations && python manage.py migrate
```

**0010 is destructive.** It drops `History`, rebuilding Game/Pick rows from the
archive first — without that step every past week would be lost. It is already
applied in production (which had an empty archive, so nothing was converted).
If you ever re-run it against a database that *does* have History rows, prove the
round-trip first:

```bash
python scripts/verify_history_migration.py [path/to/db.sqlite3]
```

Take a Railway snapshot before any deploy carrying a destructive migration.

## Known gaps

- **Analytics** chart draws one line per player with no legend — you can't tell
  which is yours.
- `/strategy/` runs a 2,000-trial Monte Carlo with **no login required**.
- HSTS is deliberately off; it's the one remaining `check --deploy` warning.
  Unlike the rest it cannot be withdrawn once browsers cache it.
- `standings()` scrapes CBS Sports HTML with the deprecated bs4 `text=` kwarg;
  `/standings/` is no longer linked from the nav.
- `Message` and `Bug` models are unused leftovers still registered in admin.
- Tailwind is loaded from CDN, which isn't intended for production, for very
  few classes.
