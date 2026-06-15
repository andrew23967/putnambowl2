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

**Game**: team1 (underdog) vs team2 (favorite), points1/points2, home_team (True = team2 is home), game_id, date string

**Pick**: user + game + choice (team1/team2). `is_correct` and `points_earned` are properties.

**Profile** (in `accounts`): score (running total), theme colour, preseason picks (nfc_champ, afc_champ, superbowl_winner, etc.)

## Points formula

Underdog always gets `multiplier` points. Favorite points = `_calculate_points(ug_ml, fav_ml) * multiplier` based on moneyline ratio. Defined in both `views.py` and `auto.py`.

## Automation (auto-pilot)

When `auto_enabled=True`, the `run_auto` worker ticks every 5 min:
1. **Scrape + publish** on configured weekday + UTC hour
2. **Lock picks** at `first_game_dt - auto_lock_offset_minutes`
3. **Grade games** every tick while locked (ESPN API for live scores)
4. **Advance week** when all graded + it's Mon/Tue/Wed after 6 AM UTC

Toggle Auto-Pilot on/off and configure schedule in the Pick Dashboard.

## Admin dashboard routes

All require `@staff_member_required`:
- `/dashboard/picks/` — main control panel (scrape, grade, advance week, automation)
- `/dashboard/accounts/` — manage user accounts
- `/dashboard/announcements/` — post announcements
- `/dashboard/generate-recap/` — AJAX endpoint to generate weekly recap

## Local dev

```bash
cd putnambowl2
.venv\Scripts\activate          # Windows
python manage.py runserver
```

Runs against local SQLite. No `.env` needed — defaults are set in `settings.py`.

## Deployment (Railway)

Two services:
- **web** — `railway.toml` handles it: preDeployCommand runs collectstatic + migrate, startCommand runs gunicorn
- **worker** (putnambowl2 service) — start command: `python manage.py run_auto`

GitHub repo: `andrew23967/putnambowl2` on `main` branch → auto-deploys web on push.

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

Current migrations: `0001_initial`, `0002_weekly_recap`, `0003_automation_fields`

## Scraping notes

- `nfl-data-py`: pulls moneylines from nflverse (used for point calculation at scrape time). Data can lag 1-3 days after week ends.
- ESPN API: public, no key needed, has live scores. Used for live grading in auto-pilot.
- `get_first_game_dt(week, year)`: hits ESPN to get UTC kickoff time for auto-locking.
- If scraped with nfl-data-py and graded with ESPN, game IDs may differ — `do_grade()` in `auto.py` has a team-abbreviation fallback match.

## Emails

Not yet implemented. Planned: send weekly recap + pick reminder emails to all players.
