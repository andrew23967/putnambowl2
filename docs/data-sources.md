# Data sources

Two settings per league choose the source per job, both on the Scrape dialog:
`scrape_api` feeds the slate, `grade_api` feeds results.
**Recommended: scrape `nfl_data_py`, grade `espn`.**

| | nfl-data-py (nflverse) | ESPN |
|---|---|---|
| money lines | **the only source** | none — every game scores a flat 1× |
| live scores | no, lags 1–3 days | **yes** |
| game ids | `2025_01_DAL_PHI` | `{season}_{week}_{away}_{home}` |
| team codes | `LA`, `WAS` | `LAR`, `WSH` |

Cross-source grading works because `do_grade` matches on canonical game ids
first and falls back to home/away abbreviations. `scrape_espn` returns full team
names; `scrape_nfl_data_py` returns abbreviations; `team_from_abbrev` accepts
both.

- `get_first_game_dt()` always asks ESPN; it is the fallback for the lock time
  when the stored slate has no kickoffs.
- `get_week_type(week, year, allow_network=True)` — **request handlers pass
  `allow_network=False`**; the download cost seconds on the home page and the
  week-number fallback (18 regular weeks, 19–21 playoffs, 22 Super Bowl) is right
  for 2021 onward.
- `_get_schedule()` caches the nflverse download for `SCHEDULE_TTL_SECONDS`
  (30 min). The worker is long-lived; without the TTL it served whatever it
  downloaded at boot. A failed download returns the stale copy rather than nothing.
- nflverse numbers playoff weeks 19–22; ESPN uses `seasontype=3` weeks 1–4.
  `_espn_season_params()` converts.
- `current_season_year()` — the year the season starts, August cutoff. One definition.

## The pin

`nfl-data-py==0.3.2` is the last release of a package nflverse no longer
maintains (its successor is `nflreadpy`). It works today on pandas 3 / Python
3.13 — verified live during the v3 audit — but it is the dependency most likely
to break silently. The only code that touches it is `_get_schedule()` in
`main/scrape.py`; migrating to `nflreadpy` is contained to that function and
the column names it reads (`game_id`, `home_team`, `away_team`, `week`,
`home_moneyline`, `away_moneyline`, `gameday`, `gametime`, `result`, `game_type`).
