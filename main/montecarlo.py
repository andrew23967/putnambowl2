import math
import logging
import numpy as np

log = logging.getLogger(__name__)


def _points(ug_ml, fav_ml, multiplier):
    u = abs(float(ug_ml))
    f = abs(float(fav_ml))
    if u == 0 or f == 0:
        return float(multiplier)
    u_ratio = u / 100
    f_ratio = 100 / f
    hp = ((1 / (u_ratio * f_ratio)) ** 0.5) - 1
    return round((hp + 1) * u_ratio * multiplier, 2)


def _is_nan(v):
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def load_season_games(year, multiplier=1.0):
    """Load regular-season games for one year with pick points and outcomes."""
    try:
        import nfl_data_py as nfl
        schedule = nfl.import_schedules([year])
    except Exception as e:
        return [], str(e)

    games = []
    no_lines = 0
    for _, row in schedule.iterrows():
        # Regular season only
        game_type = row.get('game_type', 'REG')
        if str(game_type).upper() not in ('REG', 'NaN', '') and not _is_nan(game_type):
            if str(game_type).upper() != 'REG':
                continue

        result = row.get('result')
        if _is_nan(result) or result is None:
            continue  # not yet played

        result = float(result)
        if result == 0:
            continue  # tie — skip

        week = int(row.get('week', 0))
        home_ml = row.get('home_moneyline')
        away_ml = row.get('away_moneyline')
        has_lines = not _is_nan(home_ml) and not _is_nan(away_ml)

        if has_lines:
            home_ml, away_ml = float(home_ml), float(away_ml)
            # More positive moneyline = underdog
            if home_ml >= away_ml:
                underdog = 'home'
                pts_ug = _points(home_ml, away_ml, multiplier)
            else:
                underdog = 'away'
                pts_ug = _points(away_ml, home_ml, multiplier)
            pts_fav = float(multiplier)
        else:
            no_lines += 1
            underdog = 'home'
            pts_ug = pts_fav = float(multiplier)

        winner = 'home' if result > 0 else 'away'
        ug_won = (winner == underdog)

        games.append({
            'year': year,
            'week': week,
            'pts_ug': pts_ug,
            'pts_fav': pts_fav,
            'ug_won': ug_won,
            'has_lines': has_lines,
        })

    log.info('[montecarlo] year=%s loaded=%s no_lines=%s', year, len(games), no_lines)
    return games, None


def load_multi_season(years, multiplier=1.0):
    """Load and combine games across multiple seasons."""
    all_games = []
    errors = []
    year_counts = {}
    for year in years:
        games, err = load_season_games(year, multiplier)
        if err:
            errors.append(f'{year}: {err}')
        else:
            all_games.extend(games)
            year_counts[year] = len(games)
    return all_games, year_counts, errors


def run(games, n_trials=1000, pct_step=5):
    """
    Monte Carlo simulation: sweep underdog pick % from 0 to 100.
    Returns list of result dicts, one per strategy value.
    """
    if not games:
        return []

    pcts = list(range(0, 101, pct_step))
    n_games = len(games)

    pts_ug = np.array([g['pts_ug'] for g in games], dtype=float)
    pts_fav = np.array([g['pts_fav'] for g in games], dtype=float)
    ug_won = np.array([g['ug_won'] for g in games], dtype=bool)

    results = []
    for pct in pcts:
        picks_ug = np.random.random((n_trials, n_games)) * 100 < pct

        pts_earned = np.where(
            picks_ug & ug_won, pts_ug,
            np.where(~picks_ug & ~ug_won, pts_fav, 0.0)
        )
        scores = pts_earned.sum(axis=1)

        results.append({
            'pct': pct,
            'mean': round(float(scores.mean()), 1),
            'std': round(float(scores.std()), 1),
            'min': round(float(scores.min()), 1),
            'max': round(float(scores.max()), 1),
            'p10': round(float(np.percentile(scores, 10)), 1),
            'p90': round(float(np.percentile(scores, 90)), 1),
        })

    best_pct = max(results, key=lambda r: r['mean'])['pct']
    for r in results:
        r['is_best'] = r['pct'] == best_pct

    return results
