"""The strategy page's numbers, computed once and saved.

The page used to run the simulation on request: ten seasons of games, 2,000
simulated seasons per strategy, on every page load that pressed the button. That
is half a minute of a web worker for an answer that does not change until another
NFL season finishes — and it changed for nobody, because the result is a property
of the historical data, not of who is looking.

So the work happens in ``build()``, a management command writes it to
``data/strategy_report.json``, and the view reads that file. The page renders as
fast as any other, and it still renders through ``base.html``, so the nav, the
signed-in user and the theme all behave normally — which is why the *report* is
saved rather than the finished HTML.

Regenerate after a season is graded::

    python manage.py build_strategy

The settings are fixed here rather than exposed on the page. Nobody arrives at a
strategy write-up with a basis for choosing a trial count or a bucket width, and
the wrong choice quietly changes the conclusion: one season is noise, a coarse
bucket hides the effect the page exists to show.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

YEARS = list(range(2016, 2026))
N_TRIALS = 2000

# Coarse on purpose. Both of these are a power trade: a finer sweep means more
# strategies tested, and the significance threshold is raised for every one of
# them, so detail is bought with the ability to detect anything. Narrow payout
# buckets also left some holding a handful of games that all went the same way —
# zero variance, no threshold computable, and a gap in the chart.
PCT_STEP = 10          # 11 rates rather than 21
EV_STEP = 1.0          # ~10 payout buckets rather than 65

REPORT_PATH = Path(__file__).resolve().parent / 'data' / 'strategy_report.json'


def build():
    """Run the simulation and return everything the template needs.

    Returns ``(report, errors)``. Slow — tens of seconds — so nothing in a
    request path should call this; that is what the saved file is for.
    """
    from . import montecarlo as mc

    errors = []
    games, year_counts, load_errors = mc.load_multi_season(YEARS)
    errors.extend(load_errors)
    if not games:
        errors.append('No completed games found.')
        return None, errors

    results = mc.run(games, n_trials=N_TRIALS, pct_step=PCT_STEP)
    ev_results = mc.ev_by_underdog_points(games, step=EV_STEP)
    team_ev = mc.ev_by_team(games)

    s1_summary = s2_summary = s3_summary = None

    if results:
        best = next(r for r in results if r['is_best'])
        # Which rates differ from always-favourites at all — not just the best one.
        # `bonf_sig` below tests the winner against the baseline, and when the
        # winner IS the baseline that is 0 against 0, so it can never fire. The
        # page was reporting "no rate beats any other" while several rates were
        # significantly worse, which is a finding in its own right.
        sig_worse = [r['pct'] for r in results
                     if r.get('bonf_sig_vs_fav') and r.get('diff_vs_fav', 0) < 0]
        sig_better = [r['pct'] for r in results
                      if r.get('bonf_sig_vs_fav') and r.get('diff_vs_fav', 0) > 0]
        s1_summary = {
            'sig_worse': sig_worse,
            'sig_better': sig_better,
            'n_sig_worse': len(sig_worse),
            'n_sig_better': len(sig_better),
            'worst_sig_from': min(sig_worse) if sig_worse else None,
            'best_pct': best['pct'],
            'best_mean': best['mean'],
            'fav_mean': results[0]['mean'],
            'ug_mean': results[-1]['mean'],
            'range': round(max(r['mean'] for r in results) - min(r['mean'] for r in results), 1),
            'bonf_sig': best.get('bonf_sig_vs_fav', False),
            'bonf_margin': best.get('bonf_margin_vs_fav'),
            'diff_vs_fav': best.get('diff_vs_fav', 0),
            'n_strategies': len(results),
        }

    if ev_results:
        bonf_pos = [r for r in ev_results if r.get('bonf_sig') and r['net_ev'] > 0]
        bonf_neg = [r for r in ev_results if r.get('bonf_sig') and r['net_ev'] < 0]
        s2_summary = {
            'n_pos': len([r for r in ev_results if r['net_ev'] > 0]),
            'n_total': len(ev_results),
            'n_bonf': len([r for r in ev_results if r.get('bonf_sig')]),
            'bonf_pos_labels': [r['label'] for r in bonf_pos],
            'bonf_neg_labels': [r['label'] for r in bonf_neg],
        }

    if team_ev:
        bonf_teams = [r for r in team_ev if r.get('bonf_sig')]
        s3_summary = {
            'n_bonf': len(bonf_teams),
            'bonf_teams': [[r['team'], r['net_ev']] for r in bonf_teams],
        }

    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'config': {
            'years': YEARS,
            'n_trials': N_TRIALS,
            'pct_step': PCT_STEP,
            'ev_step': EV_STEP,
        },
        'year_counts': year_counts,
        'total_games': sum(year_counts.values()),
        'results': results,
        'ev_results': ev_results,
        'team_ev': team_ev,
        's1_summary': s1_summary,
        's2_summary': s2_summary,
        's3_summary': s3_summary,
    }
    return report, errors


def save(report):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=1), encoding='utf-8')
    return REPORT_PATH


def load():
    """The saved report, or None if it has never been built.

    Never raises: a missing or corrupt file leaves the page saying so rather than
    500ing, because this is a read-only write-up and a broken cache should not
    take it down.
    """
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.error('[strategy] could not read %s: %s', REPORT_PATH, e)
        return None
