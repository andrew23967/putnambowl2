"""Inline SVG line charts, computed server-side.

The home page and the season page draw the same picture: cumulative points by
week for you, the leader and the league average. Three polylines in a fixed
viewBox, stretched to the caller's width; the markers and labels are HTML placed
by percentage so they keep their shape. Drawn by templates/main/_chart.html.
"""
from django.contrib.auth.models import User

from .models import WeeklyLeaderboard

W, H, PAD = 430, 92, 3


def _xy(i, v, n, vmax, w=W, h=H, pad=PAD):
    """Plot coordinates of point i of n with value v on a 0..vmax scale."""
    step = (w - 2 * pad) / (n - 1)
    return pad + i * step, h - pad - (v / (vmax or 1)) * (h - 2 * pad)


def polyline(values, vmax, w=W, h=H, pad=PAD):
    """SVG `points` for a series, or '' when there is nothing to draw."""
    n = len(values)
    if n < 2:
        return ''
    return ' '.join(f'{x:.1f},{y:.1f}' for x, y in
                    (_xy(i, v, n, vmax, w, h, pad) for i, v in enumerate(values)))


def _end(values, vmax):
    """The last point as percentages of the plot, for an HTML marker that does
    not distort when the SVG is stretched."""
    n = len(values)
    if n < 2:
        return None
    x, y = _xy(n - 1, values[-1], n, vmax)
    return {'x': round(x / W * 100, 2), 'y': round(y / H * 100, 2)}


def _ticks(weeks):
    """Week labels along the x axis, thinned to every other week past 12."""
    n = len(weeks) + 1                      # the series starts at an origin point
    every = 1 if len(weeks) <= 12 else 2
    return [{'x': round(_xy(i, 0, n, 1)[0] / W * 100, 2), 'label': wk}
            for i, wk in enumerate(weeks, start=1)
            if i % every == 0 or i == n - 1]


def _label(v):
    return str(int(v)) if float(v).is_integer() else f'{v:.1f}'


def empty_chart():
    """The axes with nothing on them: what the home page shows in week 1."""
    return {
        'weeks': [], 'label': '', 'me': '', 'leader': '', 'avg': '',
        'me_last': 0.0, 'leader_last': 0.0, 'avg_last': 0.0,
        'me_end': None, 'leader_end': None, 'avg_end': None,
        'vmax': '', 'ticks': [], 'in_it': False, 'empty': True,
    }


def _series(tables_after, me):
    """Build me / leader / average lists from {week: {username: score_after}}.

    Every series starts at 0 before week 1 so the lines share an origin.
    Returns None with fewer than one completed week.
    """
    weeks = sorted(tables_after)
    if not weeks:
        return None
    me_s, lead_s, avg_s = [0.0], [0.0], [0.0]
    for k in weeks:
        table = tables_after[k]
        scores = list(table.values())
        me_s.append(round(table.get(me, 0.0), 1))
        lead_s.append(round(max(scores), 1) if scores else 0.0)
        avg_s.append(round(sum(scores) / len(scores), 1) if scores else 0.0)
    vmax = max(max(me_s), max(lead_s), max(avg_s), 1.0)
    return {
        'weeks': weeks,
        'label': f'wk 1–{weeks[-1]}',
        'me': polyline(me_s, vmax),
        'leader': polyline(lead_s, vmax),
        'avg': polyline(avg_s, vmax),
        'me_last': me_s[-1], 'leader_last': lead_s[-1], 'avg_last': avg_s[-1],
        'me_end': _end(me_s, vmax), 'leader_end': _end(lead_s, vmax), 'avg_end': _end(avg_s, vmax),
        'vmax': _label(vmax),
        'ticks': _ticks(weeks),
        'in_it': me in tables_after[weeks[-1]],
    }


def points_chart(league, settings, me, live_rows=None):
    """Cumulative points through the last scored week, for the home page.

    WeeklyLeaderboard(week=k) holds the table going *into* week k, so the score
    after week k is entry k+1 - or the live profile scores for the most
    recently completed week, whose next snapshot is not written yet. While the
    current week is being graded, `live_rows` adds one provisional point.
    """
    by_week = {lb.week: {e['username']: e['score'] for e in lb.entries}
               for lb in WeeklyLeaderboard.objects.filter(league=league)}
    live = {u.username: round(u.profile.score, 1)
            for u in User.objects.select_related('profile').filter(profile__league=league)}
    after = {}
    for k in range(1, settings.week):
        table = by_week.get(k + 1)
        if table is None and k == settings.week - 1:
            table = live
        if table:
            after[k] = table
    if live_rows and settings.lock_picks:
        after[settings.week] = {r['username']: r['score'] for r in live_rows}
    return _series(after, me) or empty_chart()


def season_chart(record, me):
    """The same picture from a `SeasonRecord.weekly` archive."""
    by_week = {}
    for entry in record.weekly or []:
        try:
            by_week[int(entry['week'])] = {e['username']: e['score'] for e in entry['entries']}
        except (KeyError, TypeError, ValueError):
            continue
    after = {k: by_week[k + 1] for k in sorted(by_week) if k + 1 in by_week}
    return _series(after, me)
