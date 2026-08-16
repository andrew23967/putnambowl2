import time
import requests
from bs4 import BeautifulSoup
from datetime import date
from .teams import ABBREV_TO_TEAM

# How long a downloaded schedule stays usable. The worker is a long-lived
# process, so without an expiry it would keep serving the schedule it fetched
# on boot and never see newly published results or moneylines.
SCHEDULE_TTL_SECONDS = 30 * 60

try:
    import nfl_data_py as nfl
    _schedule_cache = {}   # year -> (fetched_at, dataframe)

    def _get_schedule(year=None, allow_network=True):
        """Return the nflverse schedule for a season.

        With allow_network=False this only returns an unexpired cached copy and
        never blocks on a download — for request paths that merely want the data
        if it happens to be at hand.
        """
        global _schedule_cache
        if year is None:
            today = date.today()
            year = today.year if today.month >= 9 else today.year - 1

        cached = _schedule_cache.get(year)
        if cached and (time.monotonic() - cached[0]) < SCHEDULE_TTL_SECONDS:
            return cached[1]
        if not allow_network:
            return None

        try:
            schedule = nfl.import_schedules([year])
        except Exception as e:
            print(f'_get_schedule error for {year}: {e}')
            # Fall back to a stale copy rather than failing outright.
            return cached[1] if cached else None
        _schedule_cache[year] = (time.monotonic(), schedule)
        return schedule

    NFL_DATA_PY_AVAILABLE = True
except ImportError:
    NFL_DATA_PY_AVAILABLE = False

    def _get_schedule(year=None, allow_network=True):
        return None


def standings():
    try:
        result = requests.get("https://www.cbssports.com/nfl/standings/", timeout=10)
        soup = BeautifulSoup(result.content, 'html.parser')
        tables = soup.findAll('table', {'class': 'TableBase-table'})
        clean_tables = []
        for table in tables:
            rows = []
            for tr in table.findAll('tr'):
                cells = ''
                for th in tr.findAll('th'):
                    text = ''.join(th.find_all(text=True, recursive=False)).strip().replace('\n', '').replace(' ', '')
                    if text:
                        cells += f'<td class="tc">{text}</td>'
                for td in tr.findAll('td'):
                    text = ''.join(c for c in td.text if c not in ('\n', ' '))
                    if text:
                        cells += f'<td>{text}</td>'
                if cells and "Projections" not in cells:
                    rows.append(f'<tr>{cells}</tr>')
            clean_tables.append(f'<table>{"".join(rows)}</table>')
        return clean_tables
    except Exception as e:
        return [f'<p>Could not load standings: {e}</p>']


def _season_year():
    today = date.today()
    return today.year if today.month >= 9 else today.year - 1


def scrape_nfl_data_py(week, year=None):
    schedule = _get_schedule(year)
    if schedule is None:
        return []
    from datetime import datetime, timezone as _tz
    try:
        from zoneinfo import ZoneInfo
        _et = ZoneInfo('America/New_York')
    except Exception:
        _et = _tz.utc
    games = []
    for game_id, home, away, w, home_ml, away_ml, gameday, gametime in zip(
        schedule['game_id'], schedule['home_team'], schedule['away_team'],
        schedule['week'], schedule['home_moneyline'], schedule['away_moneyline'],
        schedule['gameday'], schedule['gametime']
    ):
        if w != week:
            continue
        if home_ml != home_ml:
            home_ml = away_ml = 0
        game_dt = None
        if gameday and gametime:
            try:
                dt_local = datetime.strptime(f"{gameday} {str(gametime).strip()}", '%Y-%m-%d %H:%M').replace(tzinfo=_et)
                game_dt = dt_local.astimezone(_tz.utc)
            except Exception:
                pass
        if home_ml >= away_ml:
            games.append([away, home, away_ml, home_ml, False, game_id, game_dt])
        else:
            games.append([home, away, home_ml, away_ml, True, game_id, game_dt])
    return games


def _espn_season_params(week):
    """Returns (seasontype, espn_week) for the ESPN scoreboard API.
    Assumes 18 regular-season weeks (2021+ format); weeks 19-22 are playoffs."""
    if week <= 18:
        return 2, week
    return 3, week - 18  # 19→WC(1), 20→DIV(2), 21→CON(3), 22→SB(4)


def get_week_type(week, year=None, allow_network=True):
    """Returns 'regular', 'playoffs', or 'superbowl' using nfl-data-py game_type.
    Falls back to week number (assumes 18-week regular season, valid 2021+).

    Page views should pass allow_network=False: the week-number fallback is
    accurate for 2021+ and not worth a multi-second download to confirm.
    """
    schedule = _get_schedule(year, allow_network=allow_network)
    if schedule is not None:
        try:
            for w, gt in zip(schedule['week'], schedule['game_type']):
                if w == week:
                    if gt == 'SB':
                        return 'superbowl'
                    if gt in ('WC', 'DIV', 'CON'):
                        return 'playoffs'
                    return 'regular'
        except Exception:
            pass
    if week >= 22:
        return 'superbowl'
    if week >= 19:
        return 'playoffs'
    return 'regular'


def scrape_espn(week, year=None):
    games = []
    try:
        season = year or _season_year()
        seasontype, espn_week = _espn_season_params(week)
        url = (f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
               f"scoreboard?dates={season}&seasontype={seasontype}&week={espn_week}")
        data = requests.get(url, timeout=10).json()
        for event in data.get('events', []):
            comp = event.get('competitions', [{}])[0]
            competitors = comp.get('competitors', [])
            if len(competitors) < 2:
                continue
            home = away = None
            for c in competitors:
                abbrev = c.get('team', {}).get('abbreviation', '')
                if c.get('homeAway') == 'home':
                    home = abbrev
                else:
                    away = abbrev
            if not home or not away:
                continue
            date_str = comp.get('date', '')
            from datetime import datetime, timezone as _tz
            game_dt = None
            if date_str:
                try:
                    game_dt = datetime.fromisoformat(date_str.replace('Z', '+00:00')).astimezone(_tz.utc)
                except Exception:
                    pass
            game_id = f"{season}_{week}_{away}_{home}"
            home_full = ABBREV_TO_TEAM.get(home, home)
            away_full = ABBREV_TO_TEAM.get(away, away)
            games.append([away_full, home_full, 0, 0, False, game_id, game_dt])
    except Exception as e:
        print(f"scrape_espn error: {e}")
    return games


def scrape(week, api_type='nfl_data_py', year=None):
    if api_type == 'espn':
        return scrape_espn(week, year)
    return scrape_nfl_data_py(week, year)


def grade_nfl_data_py(week, year=None):
    import math
    schedule = _get_schedule(year)
    if schedule is None:
        return []
    games = []
    try:
        for game_id, result, w, home, away in zip(
            schedule['game_id'], schedule['result'],
            schedule['week'], schedule['home_team'], schedule['away_team']
        ):
            if w != week:
                continue
            if result != result or (isinstance(result, float) and math.isnan(result)):
                continue
            if result is None:
                continue
            outcome = 'home' if result > 0 else ('away' if result < 0 else 'tie')
            games.append([game_id, outcome, home, away])
    except Exception as e:
        print(f"grade_nfl_data_py error: {e}")
    return games


def grade_espn(week, year=None):
    games = []
    try:
        season = year or _season_year()
        seasontype, espn_week = _espn_season_params(week)
        url = (f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
               f"scoreboard?dates={season}&seasontype={seasontype}&week={espn_week}")
        data = requests.get(url, timeout=10).json()
        for event in data.get('events', []):
            comp = event.get('competitions', [{}])[0]
            if not comp.get('status', {}).get('type', {}).get('completed', False):
                continue
            competitors = comp.get('competitors', [])
            if len(competitors) < 2:
                continue
            home = away = None
            home_score = away_score = 0
            for c in competitors:
                abbrev = c.get('team', {}).get('abbreviation', '')
                score = int(c.get('score', 0) or 0)
                if c.get('homeAway') == 'home':
                    home, home_score = abbrev, score
                else:
                    away, away_score = abbrev, score
            if not home or not away:
                continue
            game_id = f"{season}_{week}_{away}_{home}"
            diff = home_score - away_score
            outcome = 'home' if diff > 0 else ('away' if diff < 0 else 'tie')
            games.append([game_id, outcome, home, away])
    except Exception as e:
        print(f"grade_espn error: {e}")
    return games


def grade(week, api_type='nfl_data_py', year=None):
    if api_type == 'espn':
        return grade_espn(week, year)
    return grade_nfl_data_py(week, year)


def get_first_game_dt(week, year=None):
    """Return UTC-aware datetime of the earliest kickoff for the given week (via ESPN API)."""
    from datetime import datetime, timezone as dt_tz
    season = year or _season_year()
    seasontype, espn_week = _espn_season_params(week)
    try:
        url = (f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
               f"scoreboard?dates={season}&seasontype={seasontype}&week={espn_week}")
        data = requests.get(url, timeout=(5, 8)).json()
        earliest = None
        for event in data.get('events', []):
            comp = event.get('competitions', [{}])[0]
            date_str = comp.get('date', '')
            if not date_str:
                continue
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00')).astimezone(dt_tz.utc)
            if earliest is None or dt < earliest:
                earliest = dt
        return earliest
    except Exception as e:
        print(f"get_first_game_dt error: {e}")
        return None
