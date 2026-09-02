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
                                #   LeagueEmail, ProcessedEmail, SeasonRecord
    views.py                    # every view, incl. home and pickdash
    auto.py                     # auto-pilot: scrape/lock/grade/advance + auto_tick
    scrape.py                   # scrape()/grade() over both data sources
    ai_picks.py                 # Gemini picks for PutnamBot
    history_import.py           # decodes the legacy History archive
    montecarlo.py               # the strategy analysis itself
    strategy_report.py          # runs it once, saves data/strategy_report.json
    recap_stats.py              # the week's angles, for the recap prompt
    rankings.py                 # competition ranking — ties share a place
    sim.py                      # season simulator (devtools)
    email_utils.py              # league mail: SMTP first, Resend as fallback
    inbound_email.py            # IMAP poll — ingests league mail and picks
    pick_email.py               # reads picks out of a reply, via Gemini
    teams.py                    # TEAMS, NFC_TEAMS/AFC_TEAMS, abbreviations
    data/strategy_report.json   # committed; rebuilt by `manage.py build_strategy`
    management/commands/run_auto.py      # the worker loop
  accounts/                     # Profile (OneToOne → User) + auth views
  templates/
    base.html                   # design tokens, nav, theme toggle, week picker
    accounts/auth_base.html     # shared shell for login + register
    main/home.html              # week band, podium + leaderboard, Emails feed
    main/picks.html             # this week's slate — form, then results
    main/pickdash.html          # admin control panel
    main/emaildash.html         # email switches + editable prompts
    main/pick_history.html      # player × game grid
    main/members.html           # the league roster
    main/montecarlo.html        # the strategy write-up (unlinked from the nav)
  scripts/verify_history_migration.py
../legacy/                      # original site archive — see its README
```

## Core conventions

### team1 is the FAVORITE, team2 is the UNDERDOG

`points1` is always `1.0 × multiplier`; `points2` is the larger underdog value,
so `points1 <= points2` holds for every row. This is the single easiest thing to
get backwards — these docs once claimed the opposite, which produced a real bug
where the home page reported favorite wins as upsets.

### `team1_is_home` says which side is at home — nothing else does

team1 is the favorite, so the ordering says nothing about the venue; only this
flag does. **True means team1 is home.**

It was called `home_team` and documented as "True = team2 is home" — the exact
opposite of what both writers stored. `scrape.py` and the manual-entry view set
it from *the favorite is at home*, i.e. team1, while `do_grade` and the two
templates believed the help text. Auto-grading therefore awarded **every game to
the losing team**: a full week of real 2025 results graded 16 of 16 wrong. The
venue line showed `@ DAL` for a game played in Philadelphia.

Renamed so the name states the meaning. Migration `0019` is a hand-written
`RenameField` — `makemigrations` autodetects the rename as RemoveField +
AddField, which drops the column and resets every stored game to the default.
Check any future rename's generated migration for that.

### Both sources must agree on a game_id — use `make_game_id()`

`nfl_data_py` writes weeks as `01` and the Rams as `LA`; ESPN writes `1` and
`LAR`, and the Commanders as `WSH` where nflverse says `WAS`. The same fixture
therefore carried two different ids, the id match in `do_grade` never fired, and
the abbreviation fallback that should have rescued it was comparing home against
away — so with the live config (`scrape_api=nfl_data_py`, `grade_api=espn`)
auto-grading matched **nothing at all**. That is why it was never turned on.

`teams.make_game_id()` is the only place a game_id is built, and
`teams.canonical_abbrev()` folds every alias onto the `TEAM_ABBREV` spelling.
`ABBREV_TO_TEAM` alone is not enough: `LA` is not a key in it, so every Rams game
was stored with the literal team name `"LA"` — not a valid `TEAMS` choice, and
unmappable back to an abbreviation. Use `teams.team_from_abbrev()`.

`_canon_game_id()` in `auto.py` re-canonicalises both sides at compare time, so
games stored under the old spellings still grade without a data migration.

### Ranks come from `rankings.py`, never from row position

Tied players share the **best** place and the next distinct score resumes at its
positional index — three tied at the top are all 1st and the fourth is 4th, not
2nd. `main/rankings.py` is the only implementation; use `competition_ranks()` for
a `{name: rank}` map or `rank_rows()` to attach `rank` to a list of dicts.

Never number rows with `enumerate()` or `forloop.counter`. Doing so hands tied
players different places for the same score, so the same person read 2nd on the
home page and 3rd in their own pick history off identical numbers. It also broke
rank *change*: two players tied all season appeared to swap places every week as
their arbitrary order flipped, so both sides of a comparison have to come from
this too.

Every rank on the site goes through it — home leaderboard and its AJAX refresh,
the podium blocks, Pick History's before/after, Analytics' position chart, the
season tables, public profiles, and the recap's angles. The podium's *step* is
still fixed (tallest in the middle); only the number on the block is the rank.

### Games accumulate — always filter by week

Game and Pick rows persist forever and carry a `week`. Any query meaning "this
week" must say so. Unscoped queries caused auto-lock firing instantly, bots
back-filling completed weeks, and division rematches being dropped as duplicates.
`main/tests.py` has a regression test for each.

### Re-scraping a week updates it; it never duplicates

`Game.match_existing(week, team1, team2, game_id)` is the one way to ask "is this
fixture already stored". It keys on **who is playing**, compared as an unordered
pair, and never on the odds.

That matters because team1/team2 *are* the odds: team1 is the favorite. When a
line crosses pick'em the two swap places, and the old check compared them in
order (`Q(team1=..., team2=...)`), so any row whose `game_id` did not match
exactly came back a second time with the teams reversed. Two cases hit that in
practice: a game added by hand on the dashboard has no `game_id` at all, and rows
written before the sources agreed on an id format carry the old spelling.

**Kickoff time is deliberately not part of the key.** Flex scheduling moves games
between slots all season and a moved game is still the same game; keying on the
time would duplicate it every reshuffle. Week plus the two teams is already
unique — two teams meet at most once in a week — and the time is *updated* from
the scrape rather than matched on.

A matched row is refreshed in place rather than skipped, so a moved line, a
flexed kickoff or a flipped favorite all land on the row already there. One
exception: **once `lock_picks` is set, points and teams stop updating.** Members
picked against the numbers they were shown, and rewriting them afterwards
silently rescores the week. Kickoff time keeps tracking, because the countdown
and the lock read it.

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

1. Scrape at `auto_scrape_dt`, and publish **only if the slate validates**
2. Lock picks at `auto_lock_dt`
3. Grade from the first kickoff onward, using `grade_api`
4. Advance once everything is graded, if `auto_advance`, and stop at
   `season_last_week`
5. Multiplier is set automatically from week type (1× / 2× / 4×)

Every step is configurable from the Auto-Pilot panel on `/dashboard/picks/`.

### A scrape is validated before it is published

`do_scrape_and_publish` used to set `publish = True` unconditionally, so a source
outage published an empty week and mailed the league about it, and a week whose
lines had not been posted yet published with every underdog worth exactly what its
favorite was worth.

It is now three functions: `scrape_week_games()` stores, `validate_slate()`
judges, `publish_week()` commits. A slate fails when it has no games, when any
game came back without a moneyline, or when the **other** source disagrees about
how many games the week has — the last of these is the only way to notice a source
quietly returning a short slate, and it is free because ESPN is the other source.

A failure does not publish. It records `auto_last_issue`, stamps
`auto_first_attempt_dt`, and returns; the next tick tries again. Once
`auto_retry_window_minutes` (default 6h) has elapsed it publishes anyway with the
issue still recorded, because a permanently degraded source must not stall the
season. `force=True` skips the wait entirely — that is the dashboard's manual
Scrape button, where a person is looking at the result.

### Grading starts at the first kickoff, and is not configurable

It used to run on every tick from the moment picks locked, polling the source all
through Sunday afternoon for results that could not exist yet.

`auto_grade_dt` is set by `publish_week()` to the **earliest kickoff in the stored
slate**, and grading then polls each tick until every game is in — which is what
carries it across Monday night. It is derived, never entered: no result can exist
before the first game starts, and deriving it from the slate means a flexed
kickoff moves it automatically, where a hand-set weekday and time would sit there
being wrong. It also follows the game-day filter, so a Sunday-only league grades
from its Sunday game rather than the Thursday nighter it never picked.

There is deliberately **no Grade status card and no Grade schedule form** — it is
not a milestone anyone waits on, and there is nothing to set.

`do_advance_week` clears it, along with `auto_first_attempt_dt` and
`auto_last_issue` — left set, a stale grade time is already in the past and
grading resumes at lock, and a stale first-attempt timestamp counts last week's
retry window against this week's first scrape.

### Game days are a set, not a range

`scrape_days` is a comma-separated list of weekday numbers, blank meaning every
day; read it through `settings.scrape_day_set()`. It replaced
`scrape_filter_from_day`/`to_day`, which could only express a contiguous run — a
league playing Sunday and Monday could not skip Saturday, because the wrap-around
branch swept in everything between. Migration `0021` carries the old range over;
without it a Sunday-only league silently starts pulling Thursday night games.

The old fields and `_game_day_in_filter` still exist for the legacy dashboard
path. New code calls `_game_day_allowed`.

### The season ends

`settings.week += 1` was unconditional, so after the Super Bowl the autopilot
rolled into week 23, scraped nothing, and published an empty week every week
forever. `auto_tick` returns early past `season_last_week`, and the final week is
still scored before it stands down. Set it to 18 for a regular-season-only league,
22 to run through the Super Bowl.

## UI conventions

### One job per page

The week's slate lives on `/picks/`, not the home page. That one view covers all
three states — unpublished, open for picking, locked and filling in with results
— so there is no separate "view my picks" page. Everything used to share the home
page, which left nothing readable on a phone.

Home is the standings on the left and the picks section on the right — nothing
else. It is a **wireframe**: regions are separated by rules, not panels. There
are no cards, no fills and no rounded rectangles on this page. `.pk-card` keeps
its name but is now only `display:block` — the `border-top` on each section is
what separates it from the next.

The vertical rule is `border-left` on `.col-right` with symmetric 30px padding
either side and `gap:0`, so with two `1fr` columns it lands exactly on the centre
of the page. When the columns stack on a phone that border moves to the top of
`.col-left`, so the same rule reads horizontally.

Circles stay — avatars, status dots, rank badges. Squaring those would make them
squares, which is not what "no rounded edges" means. The card answers four questions and stops: are picks out, when do they come
out, when do they lock, are the preseason picks in. It replaced a full-width band
that also carried a live countdown; `_countdown.html` still runs on `/picks/`,
which is where you are when the clock matters.

Two of those answers are only knowable at certain times, and the card says so
rather than inventing them. `auto_scrape_dt` is meaningless with auto-pilot off,
so it reads "When the commissioner opens it"; `auto_lock_dt` is derived from the
first kickoff *at publish time*, so before the week opens it reads "Set when the
week opens".

The Emails feed sits under that card, in the same right-hand column. **There is
no `/emails/` page** — it existed briefly and was folded back in.

The standings render in full and the page scrolls. The **feed** is cropped to the
standings' height and scrolls inside itself, so the two columns always end level
however long the season gets.

That crop is pure CSS, and the trick is `.col-right { height:0; min-height:100% }`.
A grid row is sized by its tallest item, so a column whose height is `auto` grows
to fit 52 messages and drags the standings down with it — the opposite of
cropping. `height:0` removes the column from that calculation, leaving the row
sized by the standings alone; `min-height:100%` then fills the row, which is now a
definite height, which is the only thing that lets `flex:1` on the feed resolve to
"whatever the picks card leaves". Take away the `height:0` and the crop silently
inverts.

Both columns previously carried their own custom scrollbar and the page was
pinned to exactly one screen; that is gone, and the page scrolls normally. On a
phone the columns stack, `.col-right` goes back to `height:auto`, and the feed
runs full length — there is no second column to match.

The newest message is expanded in place so the latest recap is readable without a
click; the rest are one-line rows, and clicking any of them — including the text
of the expanded one — opens a `<dialog>` at a reading measure.

### Rank change has two baselines, and neither is `WeeklyLeaderboard[current]`

`WeeklyLeaderboard(week=N)` holds the standings as they stood going **into** week
N, and the row is written by `do_advance_week` when the week is advanced *away*
from. So while week N is the current week, that row **does not exist yet**.

The live poll used to look it up anyway (`baseline_week = settings.week`). The
lookup missed every time, `prev_ranks` stayed empty and every arrow came back
flat — so the home page rendered real movement and the first poll a few seconds
later replaced the lot with dashes. Two paths, two answers, and the wrong one won
because it arrived second.

The baselines that do work:

- **Live grading** (`lock_picks`, results coming in): compare against each
  player's stored `profile.score`, carried in the payload as `_base`. That is
  exactly where they stood when the week locked, because `profile.score` is only
  written by `do_advance_week` and does not move again until the week rolls over.
  No snapshot needed — the arrow then tracks the day's results, which is the
  point of a live leaderboard.
- **Everything else**: `WeeklyLeaderboard(week - 1)`, which is what `home()` uses,
  so the page and the poll agree.

Both sides go through `competition_ranks`. With positional numbering two players
level all season swapped arbitrary order and showed a rank change every week.

### Week and results always read the same way round

Two lists put the kickoff time and venue against the game: `/picks/` and the Pick
Dashboard. Both used to mirror that line on `home_team`, so it jumped from the left
of one row to the right of the next and there was no column to read down. It is
always on the right now. Only *which* team the venue names still depends on who is
at home.

On `/picks/`, the outline card and the green/red result tint appear **only once
picks are locked**. A week can have graded games while the form is still open, and
tinting the form both gives the outcome away and paints a control you can still
change.

### Times are entered in `auto_tz` and displayed in browser time

This is deliberate, not an inconsistency to tidy up. The two halves do different
jobs:

- **Inputs** (`auto_scrape_time`, `auto_lock_time`, `auto_grade_time`) are read in
  `settings.auto_tz` and converted to UTC by the view. Set `auto_tz` to Eastern
  and you can type kickoff times straight off the NFL schedule, which is quoted in
  ET, without converting anything in your head.
- **Displays** are rendered in the **viewer's** browser timezone by the
  `data-utc-*` JS in `base.html`, so what you read back is when it will actually
  happen where you are, with no guessing about which zone the page means.

So the same moment legitimately reads "11:00 PM" in a form field and "12:00 AM
EDT" on a status card. Do not "fix" this by making them agree.

What *does* have to hold: a day label and its time must convert **together**.
Use the paired `data-utc-day` / `data-utc-time` attributes, never a server-rendered
`{{ dt|date:"D" }}` above a converted time — west of the server those disagree by a
day ("Mon" over "Sun 9:00 PM"). The grade status card shipped with exactly that
bug for one revision.

Also: `{# ... #}` **cannot span lines**. A multi-line one is not parsed as a
comment and renders as visible text on the page — `manage.py check`, the template
compiler and the test suite all pass while the words sit on the dashboard. Use
`{% comment %}` for anything longer than one line.

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

### The week picker is a number box, and pages borrow the nav row

`initWeekPicker()` in `base.html` replaced a drag slider — you cannot aim a slider
at one week out of twenty-two. It keeps the same `{min, max, value, onChange}`
contract. It commits on `change`, not `input`: typing "12" would otherwise load
week 1 on the way past.

`#nav-week-extra` is a slot on that row a page can fill; Pick History parks its
game pager there so both controls sit together and neither scrolls away. Analytics
does the opposite — its week control lives **on the card**, because it applies to
two of its four charts and appears and disappears with the tab.

Adding the row changes the nav's height, which `html.nav-has-weekpicker` accounts
for. **Never hardcode that height.** It is fractional (85.6px, not 86), and a
sticky header pinned at a rounded 86 leaves a hairline the table scrolls through,
while pinning at 84 hides a strip of the header and makes it look like it changes
shape as you scroll. Pick History measures the nav and pins to its exact height, so
the header sits in one place whether the page is scrolled or not.

### Pick History pages games; it never scrolls sideways

Games are columns, so a full week does not fit. It shows six at a time (three on a
phone, where Score is dropped and the matchup codes stack) with a pager, and the
standing figures — rank, score, week, record — never page, because they are the
answer to "how did this week go". Columns sort by clicking a header; the sort is
held outside the render so it survives paging and changing week.

The league is the first body row, not a footer or a caption: same columns, averaged,
so you can see whether you are above or below it at a glance.

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

The home page carries every message the league has sent or received, newest first.
It reads **only `LeagueEmail`** — one source, so every row has a real `sent_at` to
sort by. A feed stitched together from `WeeklyLeaderboard.recap` had no timestamp
at all, and labelling posts from `settings.week - 1` got the week wrong (advancing
a week with no games generates no recap, so the old text stays live while the
counter moves on).

The feed is rendered by `home()` from `_email_feed()`; there is no `main:emails`
route. It has been moved out to its own page and back again, and a stale
`{% url 'main:emails' %}` left in `emaildash.html` during one of those moves
raised `NoReverseMatch` and took the Emails **dashboard** down. A `{% url %}` for
a route that no longer exists is a render-time error `manage.py check` cannot see;
`NavPageRenderTests` loads every nav-reachable page for exactly this reason.

Rows arrive two ways:

- **Recorded at send time** — `email_utils.record_site_email()`. The site's own
  mail appears even if ingestion is broken. Keyed on a stable slug, so a re-send
  replaces its row instead of stacking duplicates. PutnamBot's recaps carry
  `PUTNAMBOT_SIGNOFF`, because the mail that goes out has no author chrome.
- **Ingested** — `main/inbound_email.py` polls IMAP from the worker.

Anything writing `settings.weekly_recap` should also update the matching
`WeeklyLeaderboard` row **and** put the recap in the feed.

### Two emails a week, and one of them is conditional

The league gets **one** scheduled mail: *picks are live*. It has three sections,
in this order:

1. **Intro** — hand-written, optional, `settings.weekly_intro`. Cleared by
   `do_advance_week` so last week's note cannot go out attached to this week's
   games.
2. **Recap** — last week's write-up, gated on `email_recap`.
3. **Ballot** — the reply-by-email pick list, gated on `email_ballot`.

The ballot is last because it is by far the longest part, one line per game, and
nothing after it gets read. The recap **no longer goes out on its own**:
`do_advance_week` calls `record_recap_email` (feed only) rather than
`send_recap_email`, and the text reaches people at the top of the next week's
mail instead. `send_recap_email` still exists for one-off corrections.

The second mail is the **reminder**, and it only sends if it has to.
`send_pick_reminder_email` goes to whoever `members_missing_picks()` returns —
anyone whose ballot is **incomplete**, not merely empty, because the rules are
all-or-nothing and twelve of sixteen games scores exactly as much as none. It
fires once, `reminder_hours_before_lock` before `auto_lock_dt`, and
`reminder_sent_week` stops a five-minute tick from mailing everyone across the
whole window. It is sent per person and names how many games each still owes,
which is nobody else's business.

### League mail is from the commissioner, not from PutnamBot

PutnamBot is a **player** — an account that makes picks and appears in the
standings. It is not a correspondent, and nothing the site sends is signed by it.

Recaps used to append a `PUTNAMBOT_SIGNOFF` introducing "the AI commissioner of
this league", and `record_recap_email` credited the row to the `putnambot`
account, which put a robot avatar on it in the feed. That gave the league two
commissioners, one of whom was also competing in it. Pick confirmations signed
off "PutnamBot, reading the league's mail" for the same reason.

Everything the site sends now signs `LEAGUE_SIGNOFF` (`──
PutnamBowl`), and
recap feed rows carry no author, exactly like the picks-are-live mail. The recap
*prompt* has always cast the model as the commissioner, so only the framing
around it was ever wrong.

`main/ai_picks.py` still says PutnamBot, correctly — that is the player.

### Week 1 never carries a recap

`weekly_recap` is a single field that survives a season boundary, so the guard in
`send_picks_published_email` is on the **week number**, not on the field being
empty: week 1 has no previous week in this season, and without that check the
opening email of a new season could lead with last season's closing write-up
under a "Last Week" heading.

Starting a season does clear the field, but that only covers the tidy path. Week
1 can also be reached by setting the week by hand, and then the old text is still
sitting there.

Recaps are **recorded, never mailed on their own** — they reach the league at the
top of the next picks-are-live email. Both advance paths have to agree on this:
`do_advance_week` and the dashboard's own "next week" button. The button kept
calling `send_recap_email` after the autopilot moved to `record_recap_email`, so
advancing by hand mailed the league twice for one week.

### The intro can arrive by email

Mail to the **`+intro`** tagged address becomes `SiteSettings.weekly_intro`, so
the week's opening line can be written from a phone. `email_utils.intro_address()`
builds it the same way `picks_address()` does, both via `_tagged_address()` —
Gmail delivers `user+intro@` to `user@` and keeps the tag in the headers, which
is what lets one mailbox route three jobs with no mailing list.

Two conditions, both required: addressed to the tagged address, **and** from
someone with `email_posts_enabled`. The intro goes out at the top of the mail
every member reads, so it is the same trust level as publishing to the site. A
member without the flag who writes to it is handled as ordinary mail rather than
silently rewriting what the league sees.

Nothing is sent at that moment — it only fills the intro. The recap and ballot
are appended by `send_picks_published_email` when the week publishes, as usual.
`_confirm_intro` replies with what was stored, for the same reason pick
confirmations exist: otherwise the first sign of a mistake is the whole league
reading it.

### The intro library

`IntroTemplate` is a named, editable, reusable opening line; migration `0025`
seeds ten. Choosing one copies its **raw** body into `weekly_intro`, placeholder
and all — `{week}` is substituted when the mail is built, not when the intro is
picked, so a template written once stays right every week it is reused.

Substitute with `.replace('{week}', ...)`, never `.format()`. The text is
hand-edited and a stray brace — an emoticon, a bit of pasted JSON — would raise
inside the send path. `build_recap_prompt` has the same rule for the same reason.

### The recap is written from angles, not from every pick

`main/recap_stats.py` computes what was *interesting* about a week — best and
worst weeks, standings movement, the leader's margin, two players a point apart,
the game nobody got right, the one everybody did, the biggest single call,
underdog appetite, incomplete ballots — and the prompt gets that list plus the
week's table.

It used to get sixteen lines of `TEAM (1.0) vs TEAM (2.4) — winner: X | picks:
alice→team1, …` and was left to notice the story for itself. It mostly did not,
so the recaps read like a results table set in prose.

Each angle is emitted **only when it fires**. No "nobody had a perfect week"
filler: a quiet week produces a short list, which is the correct signal to a
model that the week was quiet. Add categories to `summary()`; keep them
conditional, and keep them from restating each other — TRAP GAME is suppressed
when NOBODY SAW IT already covered that same game.

### Outbound goes through the league mailbox, not Resend

`email_utils.send_via_mailbox()` sends over SMTP using the **same Google app
password as IMAP**, and is preferred whenever `SMTP_*` is configured. This is not
a style preference: Resend's sandbox sender (`onboarding@resend.dev`) only
delivers to the Resend account owner until a domain is verified, so it could not
reach a single league member. Resend remains as a fallback.

A reply from the mailbox is also a *real* reply — same address the member wrote
to, threaded via `In-Reply-To`/`References` — so a pick confirmation lands in the
conversation they started rather than as a stray message.

**Everything the site sends goes per member**, from the accounts —
`league_recipients()` — and never to `LEAGUE_LIST_ADDRESS`:

- **Most of the league is not in the Google Group.** The group is only how the
  commissioner's own mail reaches the site for the feed; it is not the league's
  distribution list. Posting a recap there would be one send instead of nineteen
  and would miss most of its audience.
- **The ballot must be per member anyway**, because a member hitting reply on a
  group message could broadcast their picks to the whole league.
- **Pick confirmations** always go straight to the member. Never the list.

### Dedupe lives in ProcessedEmail, not the feed

The poller scans a rolling 7-day window, so a message stays visible after it has
been handled. Dedupe therefore reads `ProcessedEmail`, a separate table of
message-ids — **not** `LeagueEmail`. If it read the feed, deleting a row in the
Django admin would get that message re-ingested and **relayed to the whole league
again**. With this split, deleting feed rows is safe.

Only messages that were *acted on* are recorded. A message rejected for
configuration reasons — an unknown sender, say — is deliberately left out, so it
is picked up on the next poll once the account exists. A crash during pick parsing
is likewise not recorded, so it gets another go.

`ProcessedEmail` is written **before** relaying, so a message cannot be forwarded
to the league twice under any ordering.

### Nobody gets old mail on joining

Every send is triggered by an event happening now — a week published, a week
advanced, a season started, an email arriving — and `league_recipients()` is
evaluated at that moment. Nothing iterates the archive to send it. A member who
joins today gets tomorrow's mail and none of the back catalogue; they read the
history in the Emails feed on the home page instead. Keep it that way.

### The site is the league's mailer

Because the group is not the membership, `email_utils.relay_to_league()` forwards
every published league email on to every member. The commissioner sends one
message; the site reaches everyone. It runs from the publish branch of
`ingest_message`, which is safe to relay from because `message_id` is unique —
ingest happens once per email, so the relay cannot fire twice.

It skips the sender, the site's own mailbox, and anyone already on the original
To/Cc, so nobody is mailed twice when some members are copied directly.

`Reply-To` is set to the **original sender, not the mailbox**. A reply arriving in
our mailbox would be read as a *pick submission* — that is what direct mail means
— so pointing replies at the commissioner both avoids that collision and is what
someone hitting reply expects.

Pick submissions are never relayed: that would publish someone's picks to the
league before lock. `main/tests.py` asserts it.

### The Emails dashboard, and prompts you cannot break

`/dashboard/emails/` (`views.emaildash`) holds the five switches on `SiteSettings`
— `email_picks_live`, `email_ballot`, `email_recap`, `email_confirmations`,
`email_relay` — checked at each send site. *When* mail fires is still the
auto-pilot's business; these only decide whether it goes at all. Switching the
recap off still records it to the feed, so the site keeps the write-up.

The recap prompt is editable there too, but **only the instructions**. It is
assembled as:

```
[editable instructions]  ->  SiteSettings.recap_prompt
[data block]             ->  auto.recap_data_block(week)      always
[format rules]           ->  auto.RECAP_FORMAT_RULES          always
```

There is **no season-preview mail**. `intro_prompt`, `build_intro()` and the
"Season preview" send on starting a season are all gone: the league gets one
scheduled email a week, and the seeded "Season opener" intro covers the same
ground from inside it.

`build_recap_prompt()` does the assembly, so no edit can remove the standings and
results the recap is written from, or the plain-text rule — drop that and the model
answers in markdown, which the emails render raw. The page shows both appended
parts read-only, against real data, so it is obvious they are always there.

Blank means "use the built-in default" (`DEFAULT_RECAP_PROMPT`). `{week}` is
substituted with `replace()`, not `format()`:
the text is user-edited and a stray brace must not raise. Tests cover all of this.

### Nothing is sent while tests run

This module drives Resend and `smtplib` directly rather than Django's mail
framework, so the test runner's locmem backend is no protection — the suite
genuinely delivered mail to `boss@example.com` the moment SMTP was configured.
`settings.TESTING` (`'test' in sys.argv`) feeds `outbound_suppressed()`, which
every transport checks. Tests that need to exercise the send path override
`TESTING=False` **and** stub the transport thread; see `RecapEmailTests`.

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

### One mailbox, no mailing list

Everything goes through `putnambowl.league@gmail.com`. There is no Google Group in
the flow — the site holds the membership, so the commissioner sends one email and
`relay_to_league` forwards it to everyone.

Two gates first: **authentication** (SPF/DKIM/DMARC from
`Authentication-Results`) and **the sender being a known member**. The auth check
is the only real security boundary — `From` is trivially forged, so with
`INBOUND_REQUIRE_AUTH` off, anyone knowing the commissioner's address can post to
the home page.

Then `_is_pick_submission` decides what the message *is*:

| Sent to | Sender's `email_posts_enabled` | Treated as |
|---|---|---|
| `…+picks@gmail.com` | either | pick submission |
| the plain address | on | announcement → published **and relayed** |
| the plain address | off | pick submission |

So the flag means "this member's emails get published", and off — the default, and
most of the league — means "their emails are picks".

**The `+picks` tag is not decoration.** Gmail delivers `user+tag@` to `user@` and
keeps the tag in the headers. The commissioner is set to publish, so without the
tag a reply to their own ballot would broadcast their picks to the whole league.
Ballots and confirmations set `Reply-To` to the tagged address, which makes
replying unambiguous for everyone. Don't remove it to "simplify".

Every rejection is logged with its reason; silent drops make inbound mail
impossible to debug. `main/tests.py` covers both gates, the routing table above,
the ballot-reply footgun, dedupe and reply trimming.

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

Some older pages are still written in Tailwind's fixed palette — `bg-white`,
`text-slate-900`, `border-slate-200`. Those do **not** flip, so the page stays
light while the theme goes dark. `rules.html` was fixed;
`accounts/user_profile.html`, `accounts/public_profile.html`, `allpicks.html`,
`secretanalytics.html`, `seasons.html` and `standings.html` still have it.

## Preseason picks close with the week's picks

`preseason_open = settings.week == 1 and not settings.lock_picks`. Submitting is
not the deadline — the slate locking is. Both the view and the page check it: the
form renders disabled and the POST is refused, because hiding the button is not a
guard.

The home band keeps its second row for the whole window in **both** states, amber
before the picks are in and green after, because that row is also the only way back
in to edit them. There is no nav link to `/preseason/`.

The Super Bowl field offers exactly the two conference champions picked above it,
rebuilt in the browser as those change, and `PreseasonForm.clean()` enforces the
same rule server-side so a stale tab cannot save an impossible bracket.

### The week-1 nudge must not become a locked door

`home()` redirects to `/preseason/` in week 1 when the picks are not in. The
condition is **`preseason_open`**, not `settings.week == 1` — the two differ the
moment week 1's picks lock, and using the week alone bounced anyone who missed
the deadline to a form that would no longer accept anything, on every visit, with
no way to reach the home page until the week rolled over.

The "I'll do this later" escape is `request.session['preseason_deferred']`, so it
does not survive a new browser session; it softens the nudge but cannot be the
thing that prevents a trap. Any redirect of this shape needs the gate to be the
same condition that decides whether the destination is usable.

### NFC_TEAMS / AFC_TEAMS were wrong for years

They were `TEAMS[:16]` and `TEAMS[16:]` — an alphabetical cut, not a conference.
The "NFC" half held the Ravens, Bills, Bengals, Browns, Broncos, Texans, Colts,
Jaguars and Chiefs. Nothing read them, so nothing failed, until the preseason form
needed real ones. They are now listed by division and derived from `TEAMS` so both
halves keep its exact spellings; `main/tests.py` asserts the 16/16 split and that
specific teams land in the right half.

Members who saved a conference-mismatched champion before this will find that team
missing from their dropdown and must re-pick.

## The strategy page is precomputed, and unlinked

`/strategy/` runs no simulation. `main/strategy_report.py` does the work — ten
seasons, 2,000 simulated seasons per strategy — and `manage.py build_strategy`
saves it to `main/data/strategy_report.json`, which is committed so it ships with a
deploy (Railway's filesystem does not persist). The view reads that file; a missing
one shows a "not generated yet" note rather than 500ing. Rebuild after a season is
graded.

The *report* is saved rather than finished HTML on purpose: the page still renders
through `base.html`, so the nav, the signed-in user and the theme keep working.

It is **unlinked from the nav** — the analysis concludes that no simple rule beats
the odds, which is a strange thing to hand someone about to make picks. Route,
view and report are intact; it is one nav entry away from returning.

Two statistical traps live in here, both still present:

- The sweep's error bars come from randomness in the *picking*, not in the game
  outcomes, so they understate real uncertainty. The 0% and 100% rates are
  deterministic — every trial picks identically — so their standard error is
  exactly zero and a guard marks them "not significant" no matter how large the
  gap.
- `s1_summary` used to test only the *best* strategy against the baseline. When the
  best **is** the baseline that compares 0 against 0 and can never fire, so the page
  reported "no rate beats any other" while five rates were significantly worse. It
  now checks every rate.

Payout buckets are deliberately coarse (`EV_STEP = 1.0`) and the sparse tail is
folded into one open-ended bucket. Narrow buckets out there held a handful of games
that all went the same way — zero variance, no threshold computable, and a bar drawn
with no band at all.

## Local dev

```bash
cd putnambowl2
.venv\Scripts\activate
python manage.py runserver
python manage.py run_auto        # the worker — always run it; Railway runs one
python manage.py test main
python manage.py build_strategy  # only after another season is graded
```

Runs against local SQLite; no `.env` needed.

**Don't use `--noreload`.** Django 6 wraps the template loaders in the cached
loader even when `DEBUG=True`. Plain `runserver` is fine — the autoreloader
resets that cache on template change — but under `--noreload` nothing invalidates
it, so `.html` edits silently do nothing.

**`manage.py check` does not compile templates.** A malformed `{% %}` — an
orphaned `{% endfor %}` left behind by a careless block deletion, say — passes
`check` and then 500s the page. To actually verify one:

```python
from django.template.loader import get_template
get_template('main/montecarlo.html')
```

Two of these shipped. A `{% url 'main:emails' %}` left pointing at a deleted route
took out the Emails dashboard. And `field.field.widget.__class__.__name__` took out
My Profile for every signed-in user — Django templates refuse any variable
beginning with an underscore, so that page had never rendered; use
`widget.template_name` (`'django/forms/widgets/textarea.html'`) to tell widgets
apart instead.

`NavPageRenderTests` now guards both: it GETs all thirteen nav-reachable pages as a
signed-in member and compiles every template in `templates/`. Add new pages to its
`PAGES` list.

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

- **Analytics** draws one line per player with no legend — you can't tell which is
  yours. Four tabs now (Score, Rank, Efficiency, Consistency); the last two are bar
  charts and don't have the problem.
- **Consistency** plots standard deviation, so a *taller* bar means a player swings
  more — the opposite of what the tab name suggests. The caption says so; the bars
  don't.
- `/strategy/` needs **no login**, and is unlinked rather than protected.
- Once week 1's picks lock there is no route to `/preseason/` at all, so nobody can
  look back at what they chose.
- `SITE_URL` defaults to `http://localhost:8000`, so every league email carries a
  dead link until it is set on Railway.
- HSTS is deliberately off; it's the one remaining `check --deploy` warning.
  Unlike the rest it cannot be withdrawn once browsers cache it.
- `standings()` scrapes CBS Sports HTML with the deprecated bs4 `text=` kwarg;
  `/standings/` is no longer linked from the nav.
- `Message` and `Bug` models are unused leftovers still registered in admin.
- `views.analytics()` still computes and passes `win_rate_chart`, which no template
  reads since that tab was removed.
- Tailwind is loaded from CDN, which isn't intended for production, for very
  few classes.
- Five pages are still written in Tailwind's fixed light palette (`bg-white`,
  `text-slate-900`), so they stay white in the dark theme: `public_profile`,
  `allpicks`, `seasons`, `secretanalytics`, `standings`. `rules` and
  `user_profile` have been converted to tokens — copy the pattern from those.
- `/dashboard/devtools/` still exists and works; it is just unlinked from every menu.
