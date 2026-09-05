"""Inline SVG line charts, computed server-side.

The home page and the season page draw the same picture: cumulative points by
week for you, the leader and the league average. Three polylines in a fixed
viewBox, scaled by the caller's width, no charting library.
"""
from django.contrib.auth.models import User

from .models import WeeklyLeaderboard

W, H, PAD = 430, 92, 3


def polyline(values, vmax, w=W, h=H, pad=PAD):
    """SVG `points` for a series, or '' when there is nothing to draw."""
    n = len(values)
    if n < 2:
        return ''
    vmax = vmax or 1
    step = (w - 2 * pad) / (n - 1)
    return ' '.join(
        f'{pad + i * step:.1f},{h - pad - (v / vmax) * (h - 2 * pad):.1f}'
        for i, v in enumerate(values))


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
    return _series(after, me)


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
