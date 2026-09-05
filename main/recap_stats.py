"""The interesting things that happened in a week, for the recap prompt.

The recap used to be written from a dump of every game and every pick — sixteen
lines of `TEAM (1.0) vs TEAM (2.4) — winner: X | picks: alice→team1, bob→team2,
…`. That is the raw material, not the story, and it left the model to notice for
itself that one game caught fifteen of nineteen players out, or that two people
are separated by a tenth of a point. It mostly did not notice, so the recaps read
like a results table set in prose.

This computes the angles instead: who won the week, who moved, which game was the
trap, who is breathing down whose neck. Each one is emitted **only when it
actually fired** — no "nobody had a perfect week" filler — so the prompt is a
short list of things worth writing about, and a quiet week produces a short list
rather than a padded one.

Everything here is derived; nothing is stored. Call `summary(league, week)`.
"""
from django.contrib.auth.models import User
from django.db.models import Prefetch

from .models import Game, Pick, WeeklyLeaderboard
from .rankings import competition_ranks as _rank_map


def _pct(n, total):
    return round(100.0 * n / total) if total else 0


def _fmt_names(names, limit=3):
    """'alice', 'alice and bob', 'alice, bob and 4 others'."""
    names = list(names)
    if not names:
        return ''
    if len(names) == 1:
        return names[0]
    if len(names) <= limit:
        return f'{", ".join(names[:-1])} and {names[-1]}'
    return f'{", ".join(names[:limit])} and {len(names) - limit} others'


def collect(league, week):
    """The raw week: games, per-player weekly points, and standings movement.

    Returns None when the week has nothing gradeable, which is the caller's cue
    that there is no recap to write.
    """
    games = list(Game.objects.filter(league=league, week=week).prefetch_related(
        Prefetch('picks', queryset=Pick.objects.select_related('user'))
    ))
    graded = [g for g in games if g.graded and g.winner]
    if not graded:
        return None

    weekly = {}          # username -> points scored this week
    made = {}            # username -> games picked
    dogs_taken = {}      # username -> underdogs backed
    dogs_hit = {}        # username -> underdogs backed that won
    best_single = []     # (points, username, team, game)

    for g in graded:
        for pick in g.picks.all():
            name = pick.user.username
            made[name] = made.get(name, 0) + 1
            weekly.setdefault(name, 0.0)
            # team2 is the underdog by construction (points2 >= points1).
            if pick.choice == 'team2':
                dogs_taken[name] = dogs_taken.get(name, 0) + 1
            if pick.is_correct:
                pts = pick.points_earned
                weekly[name] = round(weekly[name] + pts, 2)
                if pick.choice == 'team2':
                    dogs_hit[name] = dogs_hit.get(name, 0) + 1
                best_single.append((pts, name, pick.team_picked, g))

    # Everyone who plays, including those who submitted nothing — a blank week is
    # itself a story, and leaving them out of `weekly` would hide it.
    for user in User.objects.select_related('profile').filter(profile__league=league):
        weekly.setdefault(user.username, 0.0)
        made.setdefault(user.username, 0)

    if not weekly:
        return None

    return {
        'week': week,
        'games': graded,
        'total_games': len(graded),
        'weekly': weekly,
        'made': made,
        'dogs_taken': dogs_taken,
        'dogs_hit': dogs_hit,
        'best_single': best_single,
        'ranked': sorted(weekly.items(), key=lambda kv: -kv[1]),
    }


def _standings_movement(league, week, data):
    """(before, after) cumulative standings as {name: score}, or (None, None).

    `WeeklyLeaderboard[week].entries` is written *before* the week's points are
    applied, so it is the table going into the week. The after side is the same
    table plus what each player scored, which avoids depending on whether
    `Profile.score` has been updated yet at the moment this runs.
    """
    row = WeeklyLeaderboard.objects.filter(league=league, week=week).first()
    if not row or not row.entries:
        return None, None
    before = {e['username']: e['score'] for e in row.entries if 'username' in e}
    if not before:
        return None, None
    after = {n: round(s + data['weekly'].get(n, 0.0), 2) for n, s in before.items()}
    return before, after


def summary(league, week):
    """A list of one-line facts about the week. Empty list if nothing happened.

    Order is roughly "most worth leading on" first, but the model is free to use
    them in any order — they are angles, not a script.
    """
    data = collect(league, week)
    if not data:
        return [], None

    lines = []
    weekly, ranked = data['weekly'], data['ranked']
    total = data['total_games']
    n_players = len(weekly)
    scores = [s for _, s in ranked]
    avg = round(sum(scores) / n_players, 2) if n_players else 0

    # 1. Who won the week.
    top = scores[0] if scores else 0
    if top > 0:
        winners = [n for n, s in ranked if s == top]
        lines.append(f'BEST WEEK: {_fmt_names(winners)} scored {top} points'
                     + (' (tied)' if len(winners) > 1 else ''))

    # 2. The league's baseline, and who cleared it.
    beat = sum(1 for s in scores if s > avg)
    lines.append(f'LEAGUE AVERAGE: {avg} points; {beat} of {n_players} beat it')

    # 3. Who had the worst of it.
    low = scores[-1] if scores else 0
    if n_players > 1 and low < top:
        losers = [n for n, s in ranked if s == low]
        lines.append(f'WORST WEEK: {_fmt_names(losers)} on {low} points')

    # 4. A clean sweep is the rarest thing in the game.
    perfect = [n for n, c in data['made'].items()
               if c == total and weekly.get(n, 0) > 0
               and _all_correct(data, n)]
    if perfect:
        lines.append(f'PERFECT WEEK: {_fmt_names(perfect)} got all {total} games '
                     f'right (worth a 10-point bonus)')

    # 5-7. Standings, movement, and the leader's cushion.
    before, after = _standings_movement(league, week, data)
    if before and after:
        rb, ra = _rank_map(before), _rank_map(after)
        order = sorted(after.items(), key=lambda kv: -kv[1])
        leader, lead_score = order[0]
        lines.append(f'OVERALL LEADER: {leader} on {lead_score} points')
        if len(order) > 1:
            gap = round(lead_score - order[1][1], 2)
            lines.append(f'LEAD MARGIN: {gap} points over {order[1][0]}'
                         + (' — first place is not settled' if gap < 5 else ''))

        moves = [(rb[n] - ra[n], n) for n in after if n in rb]
        climbs = [m for m in moves if m[0] > 0]
        falls = [m for m in moves if m[0] < 0]
        if climbs:
            gain, who = max(climbs)
            lines.append(f'BIGGEST CLIMB: {who} up {gain} place'
                         f'{"s" if gain != 1 else ""} to {ra[who]}')
        if falls:
            drop, who = min(falls)
            lines.append(f'BIGGEST FALL: {who} down {abs(drop)} place'
                         f'{"s" if abs(drop) != 1 else ""} to {ra[who]}')

        # 8. Two people close enough that next week decides it.
        tight = None
        for (n1, s1), (n2, s2) in zip(order, order[1:]):
            d = round(s1 - s2, 2)
            if tight is None or d < tight[0]:
                tight = (d, n1, n2)
        if tight and tight[0] <= 3:
            lines.append(f'TIGHT RACE: {tight[1]} and {tight[2]} separated by '
                         f'{tight[0]} points')

    # 9-11. The games themselves: the trap, the gimme, and the collective miss.
    hardest = easiest = None
    wiped_out = set()
    for g in data['games']:
        picks = [p for p in g.picks.all()]
        if not picks:
            continue
        right = sum(1 for p in picks if p.is_correct)
        share = right / len(picks)
        label = f'{g.team1} vs {g.team2}'
        winner = g.team1 if g.winner == 'team1' else (g.team2 if g.winner == 'team2' else 'a tie')
        if hardest is None or share < hardest[0]:
            hardest = (share, label, winner, right, len(picks))
        if easiest is None or share > easiest[0]:
            easiest = (share, label, winner, right, len(picks))
        # Everyone went one way and it did not come off.
        if right == 0 and len(picks) >= 3:
            wiped_out.add(g.id)
            lines.append(f'NOBODY SAW IT: every one of the {len(picks)} players who '
                         f'picked {label} got it wrong — {winner} won')

    # Only worth its own line if it is not the game NOBODY SAW IT just covered;
    # otherwise the prompt says the same thing twice, and "only 0 of 12 got it
    # right" is a clumsy way to say nobody did.
    if hardest and 0 < hardest[0] < 0.35:
        _, label, winner, right, n = hardest
        lines.append(f'TRAP GAME: only {right} of {n} got {label} right '
                     f'({winner} won)')
    if easiest and easiest[0] == 1.0 and easiest[4] >= 3:
        lines.append(f'EVERYONE GOT IT: all {easiest[4]} players called '
                     f'{easiest[1]} correctly')

    # 12. The single most valuable call of the week.
    if data['best_single']:
        pts, who, team, g = max(data['best_single'], key=lambda t: t[0])
        if pts >= 2:
            backers = sum(1 for p in g.picks.all() if p.is_correct)
            lines.append(f'BIGGEST CALL: {who} took {team} for {pts} points'
                         + (f' — {backers} players had it' if backers > 1
                            else ' — nobody else had it'))

    # 13. Underdog appetite, which is the whole strategic question of the league.
    if data['dogs_taken']:
        who, n_dogs = max(data['dogs_taken'].items(), key=lambda kv: kv[1])
        hit = data['dogs_hit'].get(who, 0)
        lines.append(f'MOST UNDERDOGS: {who} backed {n_dogs} of {total}, '
                     f'{hit} came in')

    # 14. How the week broke overall — chalk or chaos.
    upsets = sum(1 for g in data['games'] if g.winner == 'team2')
    if data['games']:
        lines.append(f'UPSETS: {upsets} of {total} games went to the underdog '
                     f'({_pct(upsets, total)}%)')

    # 15. Anyone who did not get a full ballot in — under the rules that is a
    #     scoring event, not an oversight.
    short = [n for n, c in data['made'].items() if c < total]
    if short:
        lines.append(f'INCOMPLETE BALLOTS: {_fmt_names(short)} did not pick every '
                     f'game ({total} in the slate)')

    return lines, ranked


def _all_correct(data, username):
    """Did this player get every graded game right?"""
    for g in data['games']:
        for p in g.picks.all():
            if p.user.username == username and not p.is_correct:
                return False
    return True


def data_block(league, week):
    """The recap prompt's data section: the angles, then the week's table.

    The standings stay — the model needs the numbers to write accurately — but
    they come after the story, and the per-game pick dump is gone.
    """
    lines, ranked = summary(league, week)
    if not lines or ranked is None:
        return None, None

    standings = '\n'.join(f'{i}. {name}: {pts} pts'
                          for i, (name, pts) in enumerate(ranked, start=1))
    block = (f'Week {week} — things worth writing about:\n\n'
             + '\n'.join(f'- {line}' for line in lines)
             + f'\n\nPoints scored this week:\n{standings}')
    return block, ranked
