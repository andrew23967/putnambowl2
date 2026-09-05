# Data model

Every row that belongs to a league says so. `Pick` scopes through its game;
`ProcessedEmail` is global because the mailbox is.

## Models

**League** (`leagues/models.py`) — `name`, `slug` (unique), `join_code` (unique,
8 chars from an alphabet without 0/O/1/I, `rotate_join_code()`), `rules` (plain
text, paragraphs on blank lines), `is_active`, `created_at`. `league.league_settings`
is its `LeagueSettings` row; `.members` / `.managers` are querysets.

**LeagueSettings** — one row per league, `LeagueSettings.for_league(league)`
creates it on first use. This was v2's `SiteSettings` singleton (pk forced to 1);
the rename was hand-written in `0028`. Fields, by job:

- week state: `week`, `publish`, `lock_picks`, `multiplier`, `scrape_week`, `first_game_dt`
- sources: `scrape_api`, `grade_api` (`nfl_data_py` | `espn`), `scrape_days` (set of weekdays, blank = all; read via `scrape_day_set()`)
- autopilot: `auto_enabled`, `auto_scrape_weekday/hour/minute` (UTC), `auto_scrape_dt`, `lock_mode` (`offset` | `manual`), `auto_lock_offset_minutes`, `auto_lock_dt`, `auto_grade_dt`, `auto_tz`, `auto_advance`, `season_last_week`, `auto_first_attempt_dt`, `auto_last_issue`; `tick_interval` (60) and `auto_retry_window_minutes` (360) are admin-only
- mail switches: `email_picks_live`, `email_ballot`, `email_recap`, `email_reminder`, `email_confirmations`, `email_relay`, `reminder_hours_before_lock`, `reminder_sent_week`
- content: `weekly_intro`, `weekly_recap`, `recap_prompt`

**Game** — `league`, `week`, `team1`, `team2`, `points1`, `points2`, `winner`
(`team1` | `team2` | `tie` | ''), `graded`, `team1_is_home`, `game_id`, `game_dt`.
`match_existing(league, week, team1, team2, game_id='')` is the one way to ask
whether a fixture is already stored.

**Pick** — `user`, `game`, `choice`; unique per (user, game). `is_correct`,
`points_earned`, `team_picked`, `points_possible` are properties.

**WeeklyLeaderboard** — `league`, `week`, `entries` (`[{username, score}]`),
`recap`. Unique per (league, week). **`week=N` holds the table going into week
N**, written by `do_advance_week` when week N is advanced away from. So the
current week's row does not exist yet, and "score after week k" is entry k+1.

**LeagueEmail** — the feed: `league`, `author`, `from_email`, `from_name`,
`subject`, `body`, `source` (`inbound` | `site`), `sent_at`, `received_at`,
`message_id` (unique), `recipient_count`, `published`.

**ProcessedEmail** — message ids the poller has acted on, with `deferred` and
`attempts` for retries. Dedupe reads this, never the feed.

**IntroTemplate** — `league`, `name` (unique per league), `body`;
`render(week)` substitutes `{week}` and `{league}` with `replace()`.
`main/intro_seeds.py` holds the starter set a new league gets.

**SeasonRecord** — the archive written by "Save season & reset": `league`,
`year` (unique per league), `winner_username`, `notes`, `weeks`,
`final_standings`, `weekly`. Entry shape:
`{username, display_name, score, rank, correct, graded, is_bot, preseason: {big_loser, nfc, afc, superbowl} | None}`.
`weekly` is the WeeklyLeaderboard series closed with one more entry holding the
final scores, so a chart can read score-after-week-k from entry k+1 to the end.
Records written before v3 carry only username and score; readers recompute
ranks and skip the missing columns. `main/seasons.py` writes and reads it.

**Profile** (`accounts/models.py`) — `user`, `league` (PROTECT, null only for a
league-less superuser), `role` (`member` | `manager`), `score` (season total,
written only by `do_advance_week` and the reset), `real_name`, `bio`,
`favorite_team`, `big_loser`, `nfc_champ`, `afc_champ`, `superbowl_winner`,
`preseason_submitted`, `is_bot`, `bot_strategy` (`random` | `gemini`),
`bot_underdog_pct`, `email_posts_enabled`, `email_weekly`, `email_reminder`.
`is_manager` is `role == 'manager' or user.is_superuser`. A `post_save` signal
creates the profile; nothing re-saves it for you — save both rows explicitly.

## Conventions, and where they came from

### team1 is the favorite, team2 the underdog

`points1 = 1.0 × multiplier`; `points2` is the larger underdog value, so
`points1 <= points2` on every row. The docs once said the opposite and the
home page reported favorite wins as upsets.

### `team1_is_home` is the only venue flag

team1 is the favorite, so the ordering says nothing about the venue. True means
team1 is home. The field was `home_team`, documented as "True = team2 is home",
and the writers stored the opposite of what the readers believed: auto-grading
awarded every game to the loser — 16 of 16 wrong in a real week. Renamed in
`0019`, by hand, because autodetect drops the column on a rename.

### Both sources must agree on a game id

nflverse writes week `01` and the Rams as `LA`; ESPN writes `1`, `LAR`, and
`WSH` for `WAS`. `teams.make_game_id()` is the only place an id is built,
`canonical_abbrev()` folds aliases, `canonical_game_id()` compares at grade
time, and `team_from_abbrev()` maps to a full name (`ABBREV_TO_TEAM` alone
misses `LA`).

### Ranks come from `rankings.py`

Competition ("1224") ranking: tied players share the best place and the next
distinct score resumes at its positional index. Row position produced a player
who was 2nd on one page and 3rd on another, and two players tied all season
appeared to swap places every week. Both sides of any rank *change* must come
from `competition_ranks` too.

### Rank change has two baselines, neither of them WeeklyLeaderboard(current)

While the current week is being graded, compare against each player's stored
`profile.score` (where they stood at lock). Otherwise compare against
`WeeklyLeaderboard(week - 1)`. Looking up the current week's row misses every
time, because it is not written until the week is advanced. `standings_rows()`
in `main/views.py` is the one implementation; home and the live refresh share it.

### Filter by league and week

Games and picks persist forever. Unscoped queries once fired the auto-lock
instantly, back-filled bot picks into finished weeks and dropped division
rematches as duplicates; with leagues, an unscoped query leaks another league.

### Re-scraping updates, never duplicates

`Game.match_existing` keys on the unordered pair of teams, never the odds
(team1/team2 swap when a line crosses pick'em) and never the kickoff (flex
scheduling moves games). A matched row is refreshed in place — except that
once `lock_picks` is set, teams and points freeze, because members picked
against the numbers they were shown. Kickoff time keeps tracking.

### Points

`scoring.calculate_points(underdog_ml, favorite_ml)` is
`sqrt(|a| · |b|) / 100`, symmetric, so the argument order does not matter and
should not be "fixed". A missing line on either side scores a flat 1.0.

### Preseason picks close with week 1's picks

`preseason_open = week == 1 and not lock_picks`. The home page nudges to
`/preseason/` only while that holds — gating on the week alone bounced anyone
who missed the deadline to a form that would no longer accept anything.
"Later" is a session flag. The Super Bowl choice is limited to the two
conference champions both in the browser and in `PreseasonForm.clean()`.
`NFC_TEAMS`/`AFC_TEAMS` are listed by division; they were an alphabetical cut
of `TEAMS` for years.

### Season year

`scrape.current_season_year()`: the year the season starts, with an **August**
cutoff, because the schedule is out and the season is set up in August. There
is one definition; it was copy-pasted into six places once.

## Migrations

- Never squash; production carries the history.
- `RenameModel` / `RenameField` by hand (`0019`, `0028`). Check any generated
  migration for RemoveField + AddField pairs before applying it.
- Adding a required foreign key is three migrations: nullable, backfill, required
  (`0029` – `0031`).
- `0010` dropped the old `History` table after converting it; the legacy
  archive is the only copy of those seasons (docs/legacy.md).
- Snapshot the Railway database before any deploy carrying a migration.
