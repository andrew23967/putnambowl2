import math
import logging
import numpy as np

log = logging.getLogger(__name__)


def _calculate_points(ug_ml, fav_ml, multiplier=1.0):
    u = abs(float(ug_ml))
    f = abs(float(fav_ml))
    if u == 0 or f == 0:
        return float(multiplier)
    u_ratio = u / 100
    f_ratio = 100 / f
    hp = ((1 / (u_ratio * f_ratio)) ** 0.5) - 1
    return round((hp + 1) * u_ratio * multiplier, 2)


def load_season_games(year, multiplier=1.0):
    """Return list of game dicts with pick points and outcome for a full season."""
    try:
        import nfl_data_py as nfl
        schedule = nfl.import_schedules([year])
    except Exception as e:
        return [], str(e)

    games = []
    for _, row in schedule.iterrows():
        result = row.get('result')
        if result is None or (isinstance(result, float) and math.isnan(result)):
            continue  # game not yet played

        week = int(row['week'])
        home_ml = row.get('home_moneyline')
        away_ml = row.get('away_moneyline')
        has_lines = (
            home_ml is not None and away_ml is not None
            and not math.isnan(float(home_ml)) and not math.isnan(float(away_ml))
        )

        if has_lines:
            home_ml, away_ml = float(home_ml), float(away_ml)
            # More positive ml = underdog
            if home_ml >= away_ml:
                underdog = 'home'
                pts_ug = _calculate_points(home_ml, away_ml, multiplier)
            else:
                underdog = 'away'
                pts_ug = _calculate_points(away_ml, home_ml, multiplier)
            pts_fav = float(multiplier)
        else:
            underdog = 'home'
            pts_ug = pts_fav = float(multiplier)

        # Actual winner
        if result > 0:
            winner = 'home'
        elif result < 0:
            winner = 'away'
        else:
            continue  # skip ties — no points awarded

        ug_won = (winner == underdog)

        games.append({
            'week': week,
            'home': str(row['home_team']),
            'away': str(row['away_team']),
            'pts_ug': pts_ug,
            'pts_fav': pts_fav,
            'ug_won': ug_won,
            'has_lines': has_lines,
        })

    games.sort(key=lambda g: g['week'])
    log.info('[montecarlo] loaded %s games for %s', len(games), year)
    return games, None


def run(games, n_trials=1000, pct_step=5, multiplier=1.0):
    """
    Monte Carlo simulation over underdog pick percentages.
    Returns list of result dicts, one per underdog_pct value.
    """
    pcts = list(range(0, 101, pct_step))
    n_games = len(games)
    if n_games == 0:
        return []

    pts_ug = np.array([g['pts_ug'] for g in games], dtype=float)
    pts_fav = np.array([g['pts_fav'] for g in games], dtype=float)
    ug_won = np.array([g['ug_won'] for g in games], dtype=bool)

    results = []
    for pct in pcts:
        # picks_ug[trial, game] = True if this trial picks the underdog
        picks_ug = np.random.random((n_trials, n_games)) * 100 < pct

        pts_earned = np.where(
            picks_ug & ug_won, pts_ug,
            np.where(~picks_ug & ~ug_won, pts_fav, 0.0)
        )
        season_scores = pts_earned.sum(axis=1)

        results.append({
            'pct': pct,
            'mean': round(float(season_scores.mean()), 1),
            'std': round(float(season_scores.std()), 1),
            'min': round(float(season_scores.min()), 1),
            'max': round(float(season_scores.max()), 1),
            'p10': round(float(np.percentile(season_scores, 10)), 1),
            'p90': round(float(np.percentile(season_scores, 90)), 1),
        })

    return results
