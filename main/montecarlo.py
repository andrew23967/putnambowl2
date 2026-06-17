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
            'home_team': str(row.get('home_team', '')),
            'away_team': str(row.get('away_team', '')),
            'underdog': underdog,
            'home_won': result > 0,
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


def ev_by_underdog_points(games, step=0.1):
    """
    For each bucket of underdog point values, compute net EV of picking underdog vs favorite.
    Only includes games with real moneylines. Skips buckets with fewer than 3 games.
    Returns list of dicts sorted by underdog point range.
    """
    lined = [g for g in games if g.get('has_lines')]
    if not lined:
        return []

    pts_values = [g['pts_ug'] for g in lined]
    lo_start = math.floor(min(pts_values) / step) * step
    hi_end = math.ceil(max(pts_values) / step) * step

    buckets = []
    lo = lo_start
    while lo < hi_end - 1e-9:
        hi = lo + step
        lo_r, hi_r = round(lo, 2), round(hi, 2)
        bucket = [g for g in lined if lo_r <= round(g['pts_ug'], 2) < hi_r]
        lo = hi
        if len(bucket) < 3:
            continue
        n = len(bucket)
        win_rate = sum(1 for g in bucket if g['ug_won']) / n
        avg_ug = sum(g['pts_ug'] for g in bucket) / n
        avg_fav = sum(g['pts_fav'] for g in bucket) / n
        ev_ug = round(avg_ug * win_rate, 3)
        ev_fav = round(avg_fav * (1 - win_rate), 3)
        # Per-game net payoff: +pts_ug if ug won, -pts_fav if fav won
        net_payoffs = [g['pts_ug'] if g['ug_won'] else -g['pts_fav'] for g in bucket]
        mean_net = sum(net_payoffs) / n
        if n > 1:
            variance = sum((x - mean_net) ** 2 for x in net_payoffs) / (n - 1)
            margin = round(1.96 * (variance / n) ** 0.5, 3)
        else:
            margin = None
        buckets.append({
            'label': f'{lo_r:.2f}–{hi_r:.2f}',
            'n_games': n,
            'ug_win_pct': round(win_rate * 100, 1),
            'ev_ug': ev_ug,
            'ev_fav': ev_fav,
            'net_ev': round(mean_net, 3),
            'margin': margin,
        })

    return buckets


def ev_by_team(games):
    """
    For each NFL team, compute net EV of picking that team vs picking the opponent,
    averaged across all their games. Positive = good pick, negative = bad pick.
    """
    team_payoffs = {}

    for g in games:
        home = g.get('home_team', '')
        away = g.get('away_team', '')
        if not home or not away or home == 'nan' or away == 'nan':
            continue

        home_won = g['home_won']
        if g['underdog'] == 'home':
            pts_home, pts_away = g['pts_ug'], g['pts_fav']
        else:
            pts_home, pts_away = g['pts_fav'], g['pts_ug']

        # Net payoff: earn pts_team if they won, lose pts_opponent if they lost
        home_net = pts_home if home_won else -pts_away
        away_net = pts_away if not home_won else -pts_home

        team_payoffs.setdefault(home, []).append(home_net)
        team_payoffs.setdefault(away, []).append(away_net)

    results = []
    for team, payoffs in team_payoffs.items():
        n = len(payoffs)
        mean_net = sum(payoffs) / n
        if n > 1:
            var = sum((x - mean_net) ** 2 for x in payoffs) / (n - 1)
            margin = round(1.96 * (var / n) ** 0.5, 3)
        else:
            margin = None
        results.append({
            'team': team,
            'n_games': n,
            'net_ev': round(mean_net, 3),
            'margin': margin,
        })

    results.sort(key=lambda r: r['net_ev'], reverse=True)
    return results


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

        mean = float(scores.mean())
        std = float(scores.std())
        sem_margin = round(1.96 * std / n_trials ** 0.5, 2)
        results.append({
            'pct': pct,
            'mean': round(mean, 1),
            'std': round(std, 1),
            'min': round(float(scores.min()), 1),
            'max': round(float(scores.max()), 1),
            'p10': round(float(np.percentile(scores, 10)), 1),
            'p90': round(float(np.percentile(scores, 90)), 1),
            'sem_margin': sem_margin,
        })

    best_pct = max(results, key=lambda r: r['mean'])['pct']
    for r in results:
        r['is_best'] = r['pct'] == best_pct

    return results
