# The autopilot

`python manage.py run_auto` loops forever: `auto.tick_all_leagues()`, then
sleep for the shortest `tick_interval` any active league asked for (default 300s,
floor 10s). Railway runs one worker; run one locally too.

`tick_all_leagues()` polls the mailbox once (see docs/email.md) — deliberately
outside `auto_tick`, which returns immediately when a league's autopilot is off,
because a league running its weeks by hand still gets its mail — then calls
`auto_tick(league)` for every active league, each fenced with its own
try/except.

## `auto_tick(league)`

Reads `LeagueSettings.for_league(league)` and returns early unless
`auto_enabled`. Then, in order, refreshing the settings row after each step:

0. Past `season_last_week` → stand down. (`week += 1` used to be unconditional and
   the worker rolled into week 23, scraping nothing and mailing the league every
   week, forever.)
1. **Scrape and publish** when `not publish` and `auto_scrape_dt` has passed —
   `do_scrape_and_publish`, below.
2. **Lock** when `publish and not lock_picks` and `auto_lock_dt` has passed.
3. **Remind** once, `reminder_hours_before_lock` before the lock, whoever is
   still short (docs/email.md).
4. **Grade** from `auto_grade_dt` onward, every tick until every game is in —
   that is what carries it across Monday night. `auto_grade_dt` is set by
   `publish_week` to the earliest kickoff in the stored slate: no result can
   exist before then, and deriving it from the slate means a flexed kickoff
   moves it automatically. It is not configurable and has no status card.
5. **Advance** when everything is graded and `auto_advance` is on; the final
   week is scored and then the worker stops.

The multiplier is set from the week type (1× regular, 2× playoffs, 4× Super Bowl)
during the scrape.

## A scrape is validated before it is published

`scrape_week_games()` stores, `validate_slate()` judges, `publish_week()`
commits. A slate fails when it has no games, when any game has no money line,
or when the *other* source disagrees about how many games the week has — the
only way to notice a source quietly returning a short slate, and free because
ESPN is the other source.

A failure records `auto_last_issue`, stamps `auto_first_attempt_dt` and waits
for the next tick. Once `auto_retry_window_minutes` (default 6h) has elapsed it
publishes anyway with the issue recorded, so a degraded source cannot stall the
season. The dashboard's Scrape button is `force=True`: a person is looking at
the result. One manually added game will always trip the cross-check, because
the other source does not know about it.

`publish_week` derives `auto_lock_dt` from the earliest kickoff **stored for
the week** (which honours the game-day filter), not from ESPN's first game —
a Sunday-only league would otherwise lock 2.7 days early on a Thursday nighter
nobody picks. It falls back to `get_first_game_dt()` only when the filter left
the week empty. Then it makes the bots' picks and mails the league.

## Game days are a set

`scrape_days` is a comma-separated list of weekday numbers, blank meaning all;
read it via `settings.scrape_day_set()`. It replaced a from/to range that could
not express "Sunday and Monday but not Saturday".

## Lock modes

- `offset` — `auto_lock_dt = first kickoff - auto_lock_offset_minutes`, set at publish.
- `manual` — a weekday and time chosen on the dashboard, read in `auto_tz`,
  stored as UTC. It is a weekly clock: `do_advance_week` moves it forward by
  seven days (and keeps going until it is ahead of now). v2 cleared
  `auto_lock_dt` before checking it, so the branch never ran and a manual-mode
  league stalled after its first week: no lock, no reminder, no grading, no
  advance. `ManualLockModeSurvivesAdvanceTests` covers it.

## Advancing

`do_advance_week(settings)` snapshots the table into
`WeeklyLeaderboard(week)`, adds each correct pick's points to `profile.score`
(+10 for a perfect week), bumps `week`, and clears `publish`, `lock_picks`,
`first_game_dt`, `auto_lock_dt` (or rolls it, manual mode), `auto_grade_dt`,
`auto_first_attempt_dt`, `auto_last_issue`, `weekly_intro`, `reminder_sent_week`;
sets the next `auto_scrape_dt`; then builds and records the recap.

Every one of those resets exists for a reason: a stale grade time restarts
grading at lock, a stale first-attempt timestamp counts last week's retry window
against this week, a stale intro goes out again with the new games. The
dashboard's "Next week" button calls this same function — in v2 it carried its
own copy, which had drifted and re-mailed last week's intro.

## Un-publishing

The dashboard's Unpublish clears `auto_scrape_dt`, `auto_first_attempt_dt` and
`auto_last_issue`. With the scrape time left in the past, the next tick used to
re-scrape, re-publish and mail everyone again within five minutes.
Re-publishing is by hand; the autopilot resumes at the next advance.

## The season reset

"Save season & reset" on the dashboard calls `seasons.archive_and_reset`: the
`SeasonRecord` is written first, then scores, picks, games and leaderboards are
cleared, in one transaction. v2 ran the reset even when the record could not be
written — and it never could, because the page posted no year — so the button
destroyed the season and kept nothing. The form now needs a year and refuses
otherwise.

## Manual controls

Every autopilot step has a button on `/dashboard/picks/` that calls the same
function: Publish/Unpublish, Lock/Unlock, Scrape (a dialog for season, week and
sources), Grade, Next week, Run a tick. A GET never ticks; it did once, and
opening the page from a throwaway database mailed real members.
