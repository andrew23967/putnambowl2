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
    main/home.html              # leaderboard + recap + links out
    main/picks.html             # this week's slate — form, then results
    main/emails.html            # the league's Emails feed
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

### One job per page

The week's slate lives on `/picks/`, not the home page. That one view covers all
three states — unpublished, open for picking, locked and filling in with results
— so there is no separate "view my picks" page. Home is the leaderboard, the
PutnamBot recap, and links to the other two. Everything used to share the home
page, which left nothing readable on a phone.

The recap is on the home page in **every** state. It used to appear only while
picks were unpublished, so it vanished exactly when people came looking for it.
Home carries the newest post inline and links to `/blog/` for the archive.

### The countdown is one component, shared

`main/_countdown.html` is included by both home and picks (`compact=True` shrinks
the digits for home); its CSS lives in `base.html` so the two can't drift. The
view supplies `views._countdown()`, which returns **every** milestone — the picks
lock, then each remaining kickoff — as JSON.

The clock counts to the first milestone still ahead and walks itself forward as
each passes, so it rolls from "picks lock in" to the next kickoff, and kickoff to
kickoff, without a reload. Don't reduce it to a single target: the old version
counted to one timestamp and froze, and it labelled that target "Underway" the
moment it passed, which was wrong for every game that had already finished.
Beyond a four-hour window a kickoff reads "Awaiting result" instead.

Lock time precedence matches `email_utils`: `auto_lock_dt`, then
`first_game_dt`, then the earliest kickoff on record minus the offset — the last
of these matters because with auto-pilot off neither scheduling field is ever
written. Over 24h the digits switch to d:h:m; a four-figure hour count just reads
as a bug.

## Season year: call `scrape.current_season_year()`

A season is named for the year it starts, so Jan/Feb belong to the previous
year's. The cutoff is **August**, not September — next season's schedule is out
well before week 1, and August is when the season gets set up. A September cutoff
meant every August scrape silently pulled *last* season, leaving a week full of
eleven-month-old games.

This one-liner had been copy-pasted into six places, so fixing any single one
fixed nothing. There is now exactly one definition; don't write the month
comparison again.

## The scrape day filter also bounds the lock

`scrape_filter_from_day`/`to_day` keep a league to, say, Sundays only.
`do_scrape_and_publish` therefore takes its lock time from the earliest kickoff
**stored for the week**, not from `get_first_game_dt()`, which ignores the filter
and would pin the lock to an excluded Thursday nighter — shutting picks 2.7 days
before the first game anyone could pick. It falls back to `get_first_game_dt()`
only when the filter left the week empty. `main/tests.py` covers both.

## The Emails feed

`/emails/` is every message the league has sent or received, newest first, and
home carries the newest one inline. It reads **only `LeagueEmail`** — one source,
so every row has a real `sent_at` to sort by. A feed stitched together from
`WeeklyLeaderboard.recap` had no timestamp at all, and labelling posts from
`settings.week - 1` got the week wrong (advancing a week with no games generates
no recap, so the old text stays live while the counter moves on).

Rows arrive two ways:

- **Recorded at send time** — `email_utils.record_site_email()`. The site's own
  mail appears even if ingestion is broken. Keyed on a stable slug, so a re-send
  replaces its row instead of stacking duplicates. PutnamBot's recaps carry
  `PUTNAMBOT_SIGNOFF`, because the mail that goes out has no author chrome.
- **Ingested** — `main/inbound_email.py` polls IMAP from the worker.

Anything writing `settings.weekly_recap` should also update the matching
`WeeklyLeaderboard` row **and** put the recap in the feed.

### Recaps are emailed, and exactly once

`send_recap_email()` mails the league *and* records; `record_recap_email()` only
records. Use the first on the normal path — `do_advance_week`, the manual advance,
`start_new_season` — and the second for corrections like `generate_recap`, where
the league has already had that recap in their inbox.

Sending happens only when the feed row is **newly created**, which makes the slug
the idempotency key: advancing a week twice, or a retried worker tick, cannot mail
the league the same recap again. `main/tests.py` covers that.

Worth knowing why this exists: recaps used to be recorded and never sent, reaching
an inbox only second-hand as a "Last Week" section inside the next "picks are
live" mail — while PutnamBot's own intro promised "a comprehensive recap".

### Inbound mail: four gates, and one that matters

A message is published only if authentication passed, the sender is a member,
that member has `profile.email_posts_enabled`, and it went league-wide (list
address, or half the other members copied). **The authentication check is the
only real security boundary** — `From` is trivially forged, so with
`INBOUND_REQUIRE_AUTH` off, anyone knowing the commissioner's address can post
to the home page. Every rejection is logged with its reason; silent drops make
inbound mail impossible to debug. `main/tests.py` covers all four gates plus
dedupe and reply trimming.

Polling lives in `run_auto` **outside `auto_tick()`**, which returns early when
`auto_enabled` is off — a league running its weeks by hand still gets its mail.
Bodies are plain text rendered escaped; HTML mail would need a real sanitiser.

The poll searches a **recent window (`SINCE`), not `UNSEEN`**. That mailbox is a
real inbox a person can open, and reading a message in the web client clears its
unread flag — with an `UNSEEN` search the poller then skipped it for ever.
Re-reading costs nothing because `message_id` is unique, so the window is safely
idempotent. Don't "optimise" it back to `UNSEEN`.

Setup gotcha worth knowing: the sender must be a **member of the Google Group**,
or Google holds the post for moderation and the site never sees it — which looks
identical to nothing happening, since there is no rejection to log.

## Picks by email

Mail sent **directly to the mailbox** rather than to the list is read as a pick
submission by `main/pick_email.py` — for members who find the site awkward. The
routing is purely the recipient: list address → publish to the feed, direct → parse
picks. No Google Group membership is involved, because no group is.

**This needs no permission flag.** Setting your own picks is something every
member can already do on the site; `email_posts_enabled` gates *publishing*,
which writes to a shared surface, and must not be reused here. A submission is
stored `published=False` so the next poll doesn't re-parse it, and never appears
in the feed — picks stay private until the week locks.

The intended flow needs no typing: `email_utils.build_ballot()` puts a line per
game in the "picks are live" mail — `Bills (1.0) / Dolphins (2.4)` — and the member
deletes the team they don't want. That mail sets `reply_to` to `IMAP_USER`, since
replies must reach the polled mailbox rather than wherever `RESEND_FROM` points.

The **untrimmed** body goes to the parser on purpose: people reply by editing
inside the quoted original, so the answers often sit below the `On … wrote:` line
that `_trim()` removes.

### One Gemini call, no hand-rolled parsing

Don't reintroduce a regex matcher. There was one — ~200 lines of alias tables,
case rules and negation patterns — and it had three bugs: it picked the team named
as the **loser** ("Chargers over Denver" chose Denver), it read the English word
"no" as New Orleans, and it ignored "give me Philadelphia". Every fix added more
special cases. Understanding "not taking the Bills" is what the model is for.

What is *not* delegated is trusting the answer: the reply is validated against the
real slate through `ai_picks._parse`, so only ids from this week and only
`team1`/`team2` survive. A hallucinated team cannot become a pick.

Unlike `ai_picks.choose_picks()`, which falls back to random because a bot with no
picks is worse than one with arbitrary picks, **here the opposite holds** — a wrong
pick silently sabotages someone's week. Anything unanswered stays unpicked.

`extract_picks()` distinguishes "the model found nothing" from "the model was
unreachable", because the sender is told different things: the second case must not
read as "you made no picks" when they are waiting on a confirmation.

Every submission gets a reply listing exactly what was recorded and what was not.
That reply is the real backstop against a misparse, so don't remove it. Tests stub
`_ask_model`, which is what makes the plumbing deterministically testable.

Note `resend` is in `requirements.txt` but is often **not installed in the local
venv**, so replies (and the "picks are live" mail) log a failure locally and only
send in production.

Test without a mailbox: `python manage.py fetch_emails --file message.eml`.

There is no `Announcement` model. It and its dashboard page were removed when the
feed replaced them; the old site's announcements are deliberately not imported.

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
