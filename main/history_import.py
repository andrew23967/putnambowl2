"""Helpers for reading the legacy History archive format.

History stored each completed week as a JSON blob instead of real rows.

Both archive formats agree on team order — team1 is the FAVORITE (worth 1.0x
the multiplier) and team2 the UNDERDOG (worth more) — matching how Game rows
are written today. Only the pick encoding differs:

  * legacy (original site, via convert_history_simple.py) — '0' / '1'.
  * later (putnambowl2's old `nextweek`) — 'team1' / 'team2'.

Migration 0010 carries its own copy of this logic on purpose — migrations must
keep working even as this module changes. Keep the two in sync only when a
genuine bug is found in both.
"""


def normalise_pick(raw):
    """Map any archived pick encoding onto 'team1' / 'team2' / None."""
    if raw in ('team1', 'team2'):
        return raw
    if raw == '0':
        return 'team1'
    if raw == '1':
        return 'team2'
    return None


def normalise_game(g):
    """Return (team1, team2, points1, points2, winner, {username: choice})."""
    team1 = g.get('team1') or ''
    team2 = g.get('team2') or ''
    try:
        points1 = float(g.get('points1') or 0)
    except (TypeError, ValueError):
        points1 = 0.0
    try:
        points2 = float(g.get('points2') or 0)
    except (TypeError, ValueError):
        points2 = 0.0
    winner = g.get('winner') or ''

    picks = {}
    for username, pd in (g.get('player_picks') or {}).items():
        if not isinstance(pd, dict):
            continue
        choice = normalise_pick(pd.get('choice', pd.get('pick')))
        if choice:
            picks[username] = choice

    return team1, team2, points1, points2, winner, picks
