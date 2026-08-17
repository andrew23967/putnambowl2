import json
import random
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models import Q, Prefetch
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from . import models, forms, scrape
from .models import (
    Game, Pick, SiteSettings, WeeklyLeaderboard,
    LeagueEmail, SeasonRecord
)
from .teams import TEAM_ABBREV, ABBREV_TO_TEAM


def _calculate_points(underdog_ml, favorite_ml):
    u = abs(float(underdog_ml))
    f = abs(float(favorite_ml))
    if u == 0 or f == 0:
        return 1.0
    u_ratio = u / 100
    f_ratio = 100 / f
    hp = ((1 / (u_ratio * f_ratio)) ** 0.5) - 1
    return round((hp + 1) * u_ratio, 2)


def _countdown(settings, games):
    """Milestones for the shared countdown, in chronological order: the picks
    lock first, then every remaining kickoff.

    The template hands the whole list to the clock, which counts to the first one
    still ahead and moves itself along as each passes. That is what makes it roll
    from "picks lock in" to the next kickoff, and from one kickoff to the next,
    without a reload.

    Lock time follows the precedence email_utils already uses: auto_lock_dt is
    the real moment, first_game_dt stands in when the week was published without
    one. With neither written — auto-pilot has never run — fall back to the
    earliest kickoff on record minus the configured offset, so the clock still
    has something true to count to.
    """
    milestones = []

    if not settings.lock_picks:
        lock_dt = settings.auto_lock_dt or settings.first_game_dt
        if lock_dt is None:
            kickoffs = [g.game_dt for g in games if g.game_dt]
            if kickoffs:
                lock_dt = min(kickoffs) - timedelta(
                    minutes=settings.auto_lock_offset_minutes or 0)
        if lock_dt:
            milestones.append({
                'kind': 'lock',
                'ts': int(lock_dt.timestamp() * 1000),
                'label': 'Picks lock in:',
                'expired': 'Picks are locked',
            })

    upcoming = sorted((g for g in games if not g.graded and g.game_dt),
                      key=lambda g: g.game_dt)
    for game in upcoming:
        matchup = f'{game.team1_abbrev} vs {game.team2_abbrev}'
        milestones.append({
            'kind': 'game',
            'ts': int(game.game_dt.timestamp() * 1000),
            'label': f'{matchup} · Kickoff in:',
            # A game only plausibly runs for so long; past that it has finished
            # and is waiting on the grader rather than being played.
            'expired': f'{matchup} · Underway',
            'stale': f'{matchup} · Awaiting result',
        })

    if not games:
        idle = 'No games this week'
    elif all(g.graded for g in games):
        idle = 'Week complete'
    else:
        idle = 'Waiting on kickoff times'

    return {'milestones_json': json.dumps(milestones), 'idle_label': idle}


def _email_feed(limit=None):
    """The Emails feed, newest first.

    One source of truth: `LeagueEmail`. Mail the league sends lands here through
    `inbound_email`, and mail the site sends is recorded at send time — including
    PutnamBot's recaps, which sign themselves in the body. That keeps ordering
    honest, because every row carries a real `sent_at`; a feed stitched together
    from `WeeklyLeaderboard.recap` had no timestamp to sort by.
    """
    qs = LeagueEmail.objects.filter(published=True).select_related('author', 'author__profile')
    return list(qs[:limit] if limit else qs)


def home(request):
    if not request.user.is_authenticated:
        return redirect('accounts:login')

    settings = SiteSettings.get()
    # Week 1 asks for preseason picks, but never traps you here: "Later" sets a
    # session flag so the rest of the site is reachable, and the home page keeps
    # a banner up until the picks are actually in.
    needs_preseason = (settings.week == 1
                       and not request.user.profile.preseason_submitted)
    if needs_preseason and not request.session.get('preseason_deferred'):
        return redirect('main:preseason')

    players = User.objects.select_related('profile').all()

    leaderboard = sorted(
        [{'score': round(p.profile.score, 1), 'username': p.username} for p in players],
        key=lambda x: x['score'], reverse=True
    )

    # Rank change vs previous week
    prev_ranks = {}
    if settings.week > 1:
        try:
            prev_lb = WeeklyLeaderboard.objects.get(week=settings.week - 1)
            prev_ranks = {e['username']: i + 1 for i, e in enumerate(
                sorted(prev_lb.entries, key=lambda x: x['score'], reverse=True)
            )}
        except WeeklyLeaderboard.DoesNotExist:
            pass
    for i, entry in enumerate(leaderboard):
        prev = prev_ranks.get(entry['username'])
        change = (prev - (i + 1)) if prev else 0
        entry['rank_change'] = change
        entry['rank_change_abs'] = abs(change)

    # On-fire streak: 3+ consecutive weeks with >= 50% correct
    past_games = list(Game.objects.filter(week__lt=settings.week).prefetch_related(
        Prefetch('picks', queryset=Pick.objects.select_related('user'))
    ).order_by('week'))
    games_by_past_week = defaultdict(list)
    for pg in past_games:
        games_by_past_week[pg.week].append(pg)

    player_week_results = {}
    for wk_num in sorted(games_by_past_week.keys()):
        week_correct = {}
        week_total = {}
        for pg in games_by_past_week[wk_num]:
            for pp in pg.picks.all():
                _u = pp.user.username
                week_correct[_u] = week_correct.get(_u, 0) + (1 if pp.is_correct else 0)
                week_total[_u] = week_total.get(_u, 0) + 1
        for _u in week_correct:
            player_week_results.setdefault(_u, [])
            _t = week_total[_u]
            player_week_results[_u].append(week_correct[_u] >= _t / 2 if _t else False)

    fire_players = {u for u, results in player_week_results.items() if len(results) >= 3 and all(results[-3:])}
    for entry in leaderboard:
        entry['on_fire'] = entry['username'] in fire_players

    # This week at a glance. The slate itself lives on /picks/ now, so the home
    # page only needs enough to say what state the week is in.
    games = list(Game.objects.filter(week=settings.week))
    my_picks = list(
        Pick.objects.filter(user=request.user, game__in=games).select_related('game')
    )

    feed = _email_feed(limit=2)
    countdown = _countdown(settings, games)

    return render(request, 'main/home.html', {
        'countdown_json': countdown['milestones_json'],
        'countdown_idle': countdown['idle_label'],
        'leaderboard': leaderboard,
        'leaderboard_json': json.dumps(leaderboard),
        'settings': settings,
        'latest_email': feed[0] if feed else None,
        'email_count': LeagueEmail.objects.filter(published=True).count(),
        'total_games': len(games),
        'picks_made': len(my_picks),
        'my_correct': sum(1 for p in my_picks if p.is_correct),
        'graded_count': sum(1 for g in games if g.winner),
        'needs_preseason': needs_preseason,
    })


@login_required
def analytics(request):
    settings = SiteSettings.get()

    past_games = list(Game.objects.filter(week__lt=settings.week).prefetch_related(
        Prefetch('picks', queryset=Pick.objects.select_related('user'))
    ).order_by('week'))
    games_by_past_week = defaultdict(list)
    for pg in past_games:
        games_by_past_week[pg.week].append(pg)

    leaderboards = WeeklyLeaderboard.objects.order_by('week')
    chart_players = sorted({e['username'] for lb in leaderboards for e in lb.entries})

    points_chart = [['Week'] + chart_players]
    position_chart = [['Week'] + chart_players]
    for lb in leaderboards:
        score_map = {e['username']: e['score'] for e in lb.entries}
        rank_map = {e['username']: i + 1 for i, e in enumerate(
            sorted(lb.entries, key=lambda x: x['score'], reverse=True)
        )}
        points_chart.append([str(lb.week)] + [score_map.get(u, 0) for u in chart_players])
        position_chart.append([str(lb.week)] + [rank_map.get(u, len(chart_players)) for u in chart_players])

    win_rate_chart = [['Week'] + chart_players]
    efficiency_chart = [['Week'] + chart_players]
    for wk_num in sorted(games_by_past_week.keys()):
        week_correct = {u: 0 for u in chart_players}
        week_earned = {u: 0.0 for u in chart_players}
        week_total = {u: 0 for u in chart_players}
        week_potential = 0.0
        for pg in games_by_past_week[wk_num]:
            if not pg.graded:
                continue
            week_potential += pg.points1 if pg.winner == 'team1' else (pg.points2 if pg.winner == 'team2' else 0)
            for pp in pg.picks.all():
                if pp.user.username not in chart_players:
                    continue
                week_total[pp.user.username] += 1
                if pp.is_correct:
                    week_correct[pp.user.username] += 1
                    week_earned[pp.user.username] += pp.points_earned
        wr_row = [str(wk_num)]
        eff_row = [str(wk_num)]
        for u in chart_players:
            t = week_total[u]
            wr_row.append(round(week_correct[u] / t * 100, 1) if t else 0)
            eff_row.append(round(week_earned[u] / week_potential * 100, 1) if week_potential else 0)
        win_rate_chart.append(wr_row)
        efficiency_chart.append(eff_row)

    completed_weeks = sorted(games_by_past_week.keys())

    return render(request, 'main/analytics.html', {
        'settings': settings,
        'completed_weeks': completed_weeks,
        'points_chart': json.dumps(points_chart),
        'position_chart': json.dumps(position_chart),
        'win_rate_chart': json.dumps(win_rate_chart),
        'efficiency_chart': json.dumps(efficiency_chart),
    })


@login_required
def emails(request):
    """Every message the league has sent or received, newest first. The home page
    carries the newest one inline; this is the archive."""
    return render(request, 'main/emails.html', {
        'settings': SiteSettings.get(),
        'emails': _email_feed(),
    })


@login_required
def pick_history(request):
    settings = SiteSettings.get()
    completed_weeks = sorted(
        Game.objects.filter(week__lt=settings.week).values_list('week', flat=True).distinct()
    )
    if settings.lock_picks and settings.week not in completed_weeks:
        completed_weeks = sorted(completed_weeks + [settings.week])
    return render(request, 'main/pick_history.html', {
        'settings': settings,
        'completed_weeks': completed_weeks,
    })


@login_required
@require_POST
def ajax_save_pick(request):
    settings = SiteSettings.get()
    if settings.lock_picks:
        return JsonResponse({'ok': False, 'error': 'Picks are locked'})
    game_id = request.POST.get('game_id')
    choice = request.POST.get('choice')
    if choice not in ('team1', 'team2'):
        return JsonResponse({'ok': False, 'error': 'Invalid choice'})
    try:
        game = Game.objects.get(id=game_id)
    except Game.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Game not found'})
    Pick.objects.update_or_create(
        user=request.user, game=game,
        defaults={'choice': choice}
    )
    return JsonResponse({'ok': True})


@login_required
def site_state(request):
    s = SiteSettings.get()
    return JsonResponse({'week': s.week, 'publish': s.publish, 'lock_picks': s.lock_picks})


@login_required
def ajax_leaderboard(request):
    week = request.GET.get('week')
    if not week:
        return JsonResponse({'error': 'week required'}, status=400)
    week = int(week)
    settings = SiteSettings.get()
    players = list(User.objects.select_related('profile').all())
    live_grading = False

    if week == settings.week:
        if settings.lock_picks:
            # Sum points earned so far from graded picks this week without touching profile.score
            live_grading = True
            all_picks = Pick.objects.filter(game__week=week).select_related('game', 'user')
            live_gained = {}
            for pick in all_picks:
                uname = pick.user.username
                live_gained[uname] = live_gained.get(uname, 0) + pick.points_earned
            entries = sorted(
                [{
                    'username': p.username,
                    'score': round(p.profile.score + live_gained.get(p.username, 0), 1),
                    '_base': round(p.profile.score, 1),
                    '_gained': round(live_gained.get(p.username, 0), 1),
                } for p in players],
                key=lambda x: x['score'], reverse=True,
            )
        else:
            entries = sorted(
                [{'username': p.username, 'score': round(p.profile.score, 1)} for p in players],
                key=lambda x: x['score'], reverse=True,
            )
    else:
        try:
            lb = WeeklyLeaderboard.objects.get(week=week)
            entries = sorted(lb.entries, key=lambda x: x['score'], reverse=True)
        except WeeklyLeaderboard.DoesNotExist:
            entries = []

    prev_ranks = {}
    prev_scores = {}
    # For the current week, compare against scores at the START of this week (pre-week snapshot).
    # For past weeks, compare against the snapshot before that week.
    # WeeklyLeaderboard(week=N) stores scores BEFORE week N, so:
    #   current week baseline = WeeklyLeaderboard(week=settings.week)
    #   past week N baseline  = WeeklyLeaderboard(week=N-1) — pre-week-N scores
    baseline_week = settings.week if week == settings.week else week - 1
    if baseline_week > 0:
        try:
            prev_lb = WeeklyLeaderboard.objects.get(week=baseline_week)
            prev_ranks = {e['username']: i + 1 for i, e in enumerate(
                sorted(prev_lb.entries, key=lambda x: x['score'], reverse=True)
            )}
            prev_scores = {e['username']: e['score'] for e in prev_lb.entries}
        except WeeklyLeaderboard.DoesNotExist:
            pass

    is_past = week < settings.week
    current_user = request.user.username
    result = []
    for i, entry in enumerate(entries):
        prev = prev_ranks.get(entry['username'])
        change = (prev - (i + 1)) if prev else 0
        if live_grading:
            gained = entry.get('_gained', 0)
        elif is_past:
            gained = round(entry['score'] - prev_scores.get(entry['username'], entry['score']), 1)
        else:
            gained = None
        result.append({
            'username': entry['username'],
            'score': entry['score'],
            'rank_change': change,
            'rank_change_abs': abs(change),
            'week_gained': gained,
            'on_fire': False,
            'me': entry['username'] == current_user,
        })

    return JsonResponse({'entries': result, 'live_grading': live_grading})


@staff_member_required
@require_POST
def ajax_add_game(request):
    settings = SiteSettings.get()
    form = forms.GameForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': dict(form.errors)})
    d = form.cleaned_data
    ug_ml = d.get('underdog_moneyline') or 0
    fav_ml = d.get('favorite_moneyline') or 0
    points2 = (_calculate_points(ug_ml, abs(fav_ml)) * settings.multiplier
               if ug_ml and fav_ml else settings.multiplier)
    raw_dt = d.get('game_dt')
    if raw_dt:
        from datetime import timedelta, timezone as _tz
        offset_min = int(request.POST.get('game_dt_offset', 0))
        game_dt_utc = (raw_dt + timedelta(minutes=offset_min)).replace(tzinfo=_tz.utc)
    else:
        game_dt_utc = None
    game = Game.objects.create(
        team1=d['underdog'],
        team2=d['favorite'],
        points1=float(settings.multiplier),
        points2=points2,
        home_team=d['favorite_is_home'],
        game_dt=game_dt_utc,
        week=settings.week,
    )
    return JsonResponse({'ok': True, 'game': {
        'id': game.id,
        'team1_abbrev': game.team1_abbrev,
        'team2_abbrev': game.team2_abbrev,
        'points1': game.points1,
        'points2': game.points2,
        'home_team': game.home_team,
        'game_dt_iso': game.game_dt_iso,
    }})


@staff_member_required
@require_POST
def ajax_delete_game(request):
    game_id = request.POST.get('game_id')
    try:
        game = Game.objects.get(id=game_id)
    except Game.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Game not found'})
    game.delete()
    return JsonResponse({'ok': True})


@staff_member_required
@require_POST
def ajax_set_winner(request):
    game_id = request.POST.get('game_id')
    winner = request.POST.get('winner', '')
    if winner not in ('team1', 'tie', 'team2', ''):
        return JsonResponse({'ok': False, 'error': 'Invalid winner'})
    try:
        game = Game.objects.get(id=game_id)
    except Game.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Game not found'})
    game.winner = winner
    game.graded = bool(winner)
    game.save()
    return JsonResponse({'ok': True})


@login_required
def picks(request):
    """This week's slate, in whichever of its three states applies: unpublished,
    open for picking, or locked and filling in with results as games are graded.

    Picks save one at a time through ajax_save_pick, so there is no POST branch
    here — the page is the form and the receipt for it.
    """
    settings = SiteSettings.get()

    games = list(Game.objects.filter(week=settings.week))
    picks_map = {p.game_id: p for p in Pick.objects.filter(user=request.user, game__in=games)}
    # Kickoff order, and stable regardless of what you have picked. Sorting
    # picked games to the bottom made the list reshuffle under your cursor.
    games.sort(key=lambda g: (g.game_dt is None, g.game_dt, g.id))

    raw_dist = {}
    for p in Pick.objects.filter(game__in=games).values('game_id', 'choice'):
        gid = str(p['game_id'])
        raw_dist.setdefault(gid, {'team1': 0, 'team2': 0})
        raw_dist[gid][p['choice']] = raw_dist[gid].get(p['choice'], 0) + 1
    # Add pct fields
    pick_dist = {}
    for gid, counts in raw_dist.items():
        total = counts['team1'] + counts['team2']
        pick_dist[gid] = {
            'team1': counts['team1'],
            'team2': counts['team2'],
            'total': total,
            'team1_pct': round(counts['team1'] / total * 100) if total else 50,
            'team2_pct': round(counts['team2'] / total * 100) if total else 50,
        }
    # Ensure every game has a dist entry so the template can always render the pie chart
    for game in games:
        gid = str(game.id)
        if gid not in pick_dist:
            pick_dist[gid] = {'team1': 0, 'team2': 0, 'total': 0, 'team1_pct': 50, 'team2_pct': 50}

    # Biggest upset: graded game the underdog (team2) won, where the most
    # people had backed the favorite.
    biggest_upset = None
    for game in games:
        if not game.graded or game.winner != 'team2':
            continue
        dist = pick_dist.get(str(game.id), {})
        total = dist.get('total', 0)
        wrong_pct = dist.get('team1_pct', 0)
        if biggest_upset is None or wrong_pct > biggest_upset['wrong_pct']:
            biggest_upset = {
                'winner': game.team2_abbrev,
                'loser': game.team1_abbrev,
                'winner_full': game.team2,
                'wrong_pct': wrong_pct,
                'total': total,
                'pts': game.points2,
            }

    countdown = _countdown(settings, games)

    return render(request, 'main/picks.html', {
        'settings': settings,
        'games': games,
        'picks_map': picks_map,
        'pick_dist': pick_dist,
        'biggest_upset': biggest_upset,
        'graded_count': sum(1 for g in games if g.winner),
        'countdown_json': countdown['milestones_json'],
        'countdown_idle': countdown['idle_label'],
    })


@login_required
def allpicks(request):
    settings = SiteSettings.get()
    games = Game.objects.filter(week=settings.week)
    players = User.objects.select_related('profile').all()

    all_picks = Pick.objects.select_related('user', 'game').filter(game__in=games)
    picks_map = {}
    for pick in all_picks:
        picks_map[(pick.user_id, pick.game_id)] = pick

    games_data = []
    for game in games:
        player_picks = []
        for player in players:
            pick = picks_map.get((player.id, game.id))
            if pick:
                correct = pick.is_correct
                player_picks.append({
                    'player': player.username,
                    'choice': pick.choice,
                    'team_picked': pick.team_picked,
                    'abbrev': TEAM_ABBREV.get(pick.team_picked, pick.team_picked[:3].upper()),
                    'points': pick.points_possible,
                    'correct': correct,
                })
            else:
                player_picks.append({
                    'player': player.username,
                    'choice': None,
                    'team_picked': 'No pick',
                    'abbrev': '—',
                    'points': 0,
                    'correct': False,
                })

        team1_count = sum(1 for p in player_picks if p['choice'] == 'team1')
        team2_count = sum(1 for p in player_picks if p['choice'] == 'team2')

        games_data.append({
            'game': game,
            'team1_abbrev': game.team1_abbrev,
            'team2_abbrev': game.team2_abbrev,
            'player_picks': player_picks,
            'team1_count': team1_count,
            'team2_count': team2_count,
        })

    player_totals = sorted(
        [{'username': p.username, 'score': round(p.profile.score, 1)} for p in players],
        key=lambda x: x['score'], reverse=True
    )

    return render(request, 'main/allpicks.html', {
        'games_data': games_data,
        'players': players,
        'player_totals': player_totals,
        'publish': settings.publish,
        'week': settings.week,
    })


@login_required
def ajax_history(request):
    try:
        week = int(request.GET.get('week', 1))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid week.'}, status=400)

    games = list(Game.objects.filter(week=week).order_by('id'))
    if not games:
        return JsonResponse({'error': f'No games found for week {week}.'}, status=404)

    all_picks = list(Pick.objects.filter(game__in=games).select_related('user', 'game'))
    picks_by_user_game = defaultdict(dict)
    for pick in all_picks:
        picks_by_user_game[pick.user.username][pick.game_id] = {
            'choice': pick.choice,
            'team': pick.team_picked,
            'correct': pick.is_correct,
            'points': round(pick.points_earned, 1) if pick.is_correct else 0,
        }

    # WeeklyLeaderboard(week=N) stores scores BEFORE week N's points are added.
    # So post-week-N scores live in WeeklyLeaderboard(week=N+1).
    try:
        lb = WeeklyLeaderboard.objects.get(week=week)
        recap = lb.recap
        pre_scores = {e['username']: e['score'] for e in lb.entries}
    except WeeklyLeaderboard.DoesNotExist:
        recap = ''
        pre_scores = {}

    try:
        next_lb = WeeklyLeaderboard.objects.get(week=week + 1)
        week_scores = {e['username']: e['score'] for e in next_lb.entries}
    except WeeklyLeaderboard.DoesNotExist:
        # Most recent week: next snapshot doesn't exist yet, use live profile scores
        week_scores = {u.username: round(u.profile.score, 1)
                       for u in User.objects.select_related('profile').all()}

    games_data = [{
        'id': g.id,
        'team1': g.team1,
        'team1_abbrev': g.team1_abbrev,
        'team2': g.team2,
        'team2_abbrev': g.team2_abbrev,
        'points1': g.points1,
        'points2': g.points2,
        'winner': g.winner,
        'home_team': g.home_team,
        'game_dt_iso': g.game_dt_iso,
    } for g in games]

    all_usernames = list(User.objects.order_by('username').values_list('username', flat=True))

    rank_after  = {u: i + 1 for i, (u, _) in enumerate(sorted(week_scores.items(),  key=lambda x: x[1], reverse=True))}
    rank_before = {u: i + 1 for i, (u, _) in enumerate(sorted(pre_scores.items(), key=lambda x: x[1], reverse=True))}

    players_data = []
    for username in all_usernames:
        user_picks = picks_by_user_game.get(username, {})
        correct = sum(1 for p in user_picks.values() if p['correct'])
        total = len(user_picks)
        after = week_scores.get(username, 0)
        before = pre_scores.get(username, 0)
        ra = rank_after.get(username)
        rb = rank_before.get(username)
        rank_change = (rb - ra) if (ra and rb) else 0
        players_data.append({
            'username': username,
            'week_total': after,
            'week_gained': round(after - before, 1),
            'rank': ra,
            'rank_change': rank_change,
            'correct': correct,
            'total': total,
            'picks': {str(gid): info for gid, info in user_picks.items()},
        })
    players_data.sort(key=lambda x: x['week_total'], reverse=True)

    settings = SiteSettings.get()
    picks_locked = week < settings.week or (week == settings.week and settings.lock_picks)

    return JsonResponse({
        'week': week,
        'picks_locked': picks_locked,
        'recap': recap,
        'games': games_data,
        'players': players_data,
    })


@login_required
def preseason(request):
    settings = SiteSettings.get()

    # "I'll do this later" — remembered for the session so the prompt does not
    # reappear on every page load, but not persisted, so it comes back next
    # visit while week 1 is still open.
    if request.method == 'POST' and 'defer' in request.POST:
        request.session['preseason_deferred'] = True
        return redirect('main:home')

    form = forms.PreseasonForm(request.user, request.POST or None)
    if form.is_valid():
        request.user.profile.big_loser = form.cleaned_data['big_loser']
        request.user.profile.nfc_champ = form.cleaned_data['nfc_champ']
        request.user.profile.afc_champ = form.cleaned_data['afc_champ']
        request.user.profile.superbowl_winner = form.cleaned_data['superbowl_winner']
        request.user.profile.preseason_submitted = True
        request.user.save()
        request.session.pop('preseason_deferred', None)
        messages.success(request, 'Preseason picks saved.')
        return redirect('main:home')
    return render(request, 'main/preseason.html', {
        'form': form,
        'week': settings.week,
        # Only week 1 is skippable; later weeks reach this page by choice.
        'can_defer': settings.week == 1,
    })


def standings_view(request):
    tables = scrape.standings()
    return render(request, 'main/standings.html', {'standings': tables})


def rules(request):
    rules = [
        ("Picking Games", "Each week, pick a winner for every NFL game. Picks lock when the admin closes submissions."),
        ("Scoring", "Picking the favorite earns 1 point (times the current multiplier). Picking the underdog earns bonus points based on the moneyline spread — the bigger the upset, the more points."),
        ("Perfect Week Bonus", "Pick every game correctly in a week (weeks 1–18) and earn an extra 10 bonus points."),
        ("Preseason Picks", "Before the season starts, predict the biggest loser, NFC champ, AFC champ, and Super Bowl winner. Your Super Bowl pick must be one of your conference champions."),
        ("Multiplier", "The admin can double the point value for big games, up to 4×. This applies to all games that week."),
        ("Leaderboard", "Scores accumulate all season. Click the week buttons on the home page to view historical snapshots."),
        ("Season History", "At the end of the season, the admin saves the final standings. All-time results are visible on the Seasons page."),
    ]
    return render(request, 'main/rules.html', {'rules': rules})



def seasons(request):
    raw_records = SeasonRecord.objects.all()
    season_records = []
    for record in raw_records:
        standings = [
            {'rank': i + 1, 'username': e.get('username', ''), 'score': e.get('score', 0)}
            for i, e in enumerate(record.final_standings)
        ]
        season_records.append({
            'year': record.year,
            'winner_username': record.winner_username,
            'notes': record.notes,
            'standings': standings,
        })
    return render(request, 'main/seasons.html', {'season_records': season_records})



@staff_member_required
def emaildash(request):
    """Which emails go out, and what PutnamBot is told to write.

    Only the *instructions* are editable. Each prompt's data — standings, results,
    and the output format rules — is appended by the code and shown here read-only,
    so it cannot be edited away by accident.
    """
    from django.conf import settings as django_settings

    from . import auto
    from .email_utils import league_recipients, picks_address, smtp_ready

    settings = SiteSettings.get()

    if request.method == 'POST':
        if 'save_switches' in request.POST:
            for field in ('email_picks_live', 'email_ballot', 'email_recap',
                          'email_confirmations', 'email_relay'):
                setattr(settings, field, request.POST.get(field) == 'on')
            settings.save()
            messages.success(request, 'Email settings saved.')
        elif 'save_prompts' in request.POST:
            settings.recap_prompt = request.POST.get('recap_prompt', '').strip()
            settings.intro_prompt = request.POST.get('intro_prompt', '').strip()
            settings.save()
            messages.success(request, 'Prompts saved.')
        elif 'reset_prompts' in request.POST:
            settings.recap_prompt = ''
            settings.intro_prompt = ''
            settings.save()
            messages.success(request, 'Prompts reset to the built-in defaults.')
        return redirect('main:emaildash')

    # Preview the data block against the most recently completed week, so what is
    # shown is real rather than illustrative.
    preview_week = max(settings.week - 1, 1)
    data_block, _ = auto.recap_data_block(preview_week)

    return render(request, 'main/emaildash.html', {
        'settings': settings,
        'recipients': league_recipients(),
        'mailbox': getattr(django_settings, 'SMTP_USER', '') or '',
        'picks_address': picks_address(),
        'smtp_ready': smtp_ready(),
        'recap_prompt': settings.recap_prompt or auto.DEFAULT_RECAP_PROMPT,
        'intro_prompt': settings.intro_prompt or auto.DEFAULT_INTRO_PROMPT,
        'recap_is_default': not settings.recap_prompt,
        'intro_is_default': not settings.intro_prompt,
        'preview_week': preview_week,
        'data_block': data_block,
        'format_rules': auto.RECAP_FORMAT_RULES,
    })


@staff_member_required
def accountdash(request):
    from main.teams import TEAMS
    players = sorted(
        User.objects.select_related('profile').all(),
        key=lambda u: u.profile.score, reverse=True
    )
    return render(request, 'main/accountdash.html', {
        'players': players,
        'teams': [t[0] for t in TEAMS],
    })


@staff_member_required
@require_POST
def edit_player(request, user_id):
    user = get_object_or_404(User, pk=user_id)

    new_username = request.POST.get('username', '').strip()
    if new_username and new_username != user.username:
        if User.objects.filter(username=new_username).exclude(pk=user_id).exists():
            return JsonResponse({'error': f'Username "{new_username}" is already taken.'}, status=400)
        user.username = new_username

    user.email = request.POST.get('email', user.email).strip()
    user.is_staff = request.POST.get('is_staff') == 'on'
    password = request.POST.get('password', '').strip()
    if password:
        user.set_password(password)
    user.save()

    p = user.profile
    try:
        p.score = float(request.POST.get('score', p.score))
    except ValueError:
        pass
    p.real_name = request.POST.get('real_name', p.real_name)
    p.bio = request.POST.get('bio', p.bio)
    p.theme = request.POST.get('theme', p.theme)
    p.favorite_team = request.POST.get('favorite_team', p.favorite_team)
    p.big_loser = request.POST.get('big_loser', p.big_loser)
    p.nfc_champ = request.POST.get('nfc_champ', p.nfc_champ)
    p.afc_champ = request.POST.get('afc_champ', p.afc_champ)
    p.superbowl_winner = request.POST.get('superbowl_winner', p.superbowl_winner)
    p.is_bot = request.POST.get('is_bot') == 'on'
    strategy = request.POST.get('bot_strategy', p.bot_strategy)
    if strategy in dict(p.BOT_STRATEGY_CHOICES):
        p.bot_strategy = strategy
    try:
        p.bot_underdog_pct = int(request.POST.get('bot_underdog_pct', p.bot_underdog_pct))
    except ValueError:
        pass
    p.preseason_submitted = request.POST.get('preseason_submitted') == 'on'
    # Granting this is granting write access to the home page — see
    # main/inbound_email.py for the checks a message still has to pass.
    p.email_posts_enabled = request.POST.get('email_posts_enabled') == 'on'
    p.save()

    return JsonResponse({'ok': True, 'username': user.username})


@staff_member_required
@require_POST
def delete_player(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    username = user.username
    user.delete()
    return JsonResponse({'ok': True, 'username': username})


@staff_member_required
def pickdash(request):
    settings = SiteSettings.get()

    if settings.auto_enabled:
        try:
            from .auto import auto_tick
            auto_tick()
            settings = SiteSettings.get()
        except Exception as _e:
            print(f'[auto_tick error] {_e}', flush=True)

    if 'add_game' in request.POST:
        form = forms.GameForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            ug_ml = d.get('underdog_moneyline') or 0
            fav_ml = d.get('favorite_moneyline') or 0
            points2 = _calculate_points(ug_ml, abs(fav_ml)) * settings.multiplier if ug_ml and fav_ml else settings.multiplier
            Game.objects.create(
                team1=d['underdog'],
                team2=d['favorite'],
                points1=float(settings.multiplier),
                points2=points2,
                home_team=d['favorite_is_home'],
                game_dt=None,
                week=settings.week,
            )
            messages.success(request, 'Game added.')

    elif 'delete_game' in request.POST:
        game_id = request.POST.get('game_id')
        Game.objects.filter(id=game_id).delete()
        Pick.objects.filter(game_id=game_id).delete()

    elif 'toggle_winner' in request.POST:
        game_id = request.POST.get('game_id')
        game = get_object_or_404(Game, id=game_id)
        cycle = {'': 'team1', 'team1': 'team2', 'team2': 'tie', 'tie': 'team1'}
        game.winner = cycle.get(game.winner, 'team1')
        game.graded = True
        game.save()

    elif 'delete_all_games' in request.POST:
        current_games = Game.objects.filter(week=settings.week)
        Pick.objects.filter(game__in=current_games).delete()
        current_games.delete()

    elif 'toggle_publish' in request.POST:
        settings.publish = not settings.publish
        settings.save()

    elif 'toggle_lock' in request.POST:
        settings.lock_picks = not settings.lock_picks
        if settings.lock_picks:
            settings.edit = False
        settings.save()

    elif 'cycle_multiplier' in request.POST:
        old_mult = settings.multiplier
        new_mult = old_mult * 2 if old_mult < 4 else 1
        settings.multiplier = new_mult
        settings.save()
        ratio = new_mult / old_mult
        for game in Game.objects.filter(week=settings.week):
            game.points1 = round(game.points1 * ratio, 2)
            game.points2 = round(game.points2 * ratio, 2)
            game.save()


    elif 'toggle_auto' in request.POST:
        settings.auto_enabled = not settings.auto_enabled
        settings.save()

    elif 'save_auto' in request.POST:
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            settings.auto_lock_offset_minutes = int(request.POST.get('auto_lock_offset_minutes', 10))
            settings.tick_interval = max(10, int(request.POST.get('tick_interval', 300)))
            lock_mode = request.POST.get('lock_mode', 'offset')
            settings.lock_mode = lock_mode if lock_mode in ('offset', 'manual') else 'offset'

            tz_str = request.POST.get('tz', 'UTC')
            try:
                tz = ZoneInfo(tz_str)
            except (ZoneInfoNotFoundError, KeyError):
                tz = timezone.utc
            settings.auto_tz = tz_str

            # Convert local scrape weekday+time → UTC
            time_str = request.POST.get('auto_scrape_time', '09:00')
            try:
                local_hour, local_minute = (int(x) for x in time_str.split(':')[:2])
            except Exception:
                local_hour, local_minute = 9, 0
            local_weekday = int(request.POST.get('auto_scrape_weekday', 1))
            offset_seconds = int(datetime.now(tz).utcoffset().total_seconds())
            local_total_minutes = local_hour * 60 + local_minute
            utc_total_minutes = local_total_minutes - offset_seconds // 60
            settings.auto_scrape_hour = (utc_total_minutes // 60) % 24
            settings.auto_scrape_minute = utc_total_minutes % 60
            settings.auto_scrape_weekday = (local_weekday + utc_total_minutes // (60 * 24)) % 7
            from_day = request.POST.get('scrape_filter_from_day', '')
            to_day = request.POST.get('scrape_filter_to_day', '')
            settings.scrape_filter_from_day = int(from_day) if from_day != '' else None
            settings.scrape_filter_to_day = int(to_day) if to_day != '' else None

            from .auto import _next_weekday_hour, _this_or_next_weekday_hour
            settings.auto_scrape_dt = _this_or_next_weekday_hour(settings.auto_scrape_weekday, settings.auto_scrape_hour, settings.auto_scrape_minute)

            if settings.lock_mode == 'manual':
                lock_time_str = request.POST.get('auto_lock_time', '09:00')
                lock_weekday = int(request.POST.get('auto_lock_weekday', 0))
                try:
                    lock_hour, lock_minute = (int(x) for x in lock_time_str.split(':')[:2])
                except Exception:
                    lock_hour, lock_minute = 9, 0
                local_lock_minutes = lock_hour * 60 + lock_minute
                utc_lock_minutes = local_lock_minutes - offset_seconds // 60
                utc_lock_hour = (utc_lock_minutes // 60) % 24
                utc_lock_minute = utc_lock_minutes % 60
                utc_lock_weekday = (lock_weekday + utc_lock_minutes // (60 * 24)) % 7
                settings.auto_lock_dt = _this_or_next_weekday_hour(utc_lock_weekday, utc_lock_hour, utc_lock_minute)
            else:
                settings.auto_lock_dt = None
            settings.save()
            tz_label = tz_str.replace('_', ' ')
            messages.success(request, f'Auto-pilot settings saved (times converted from {tz_label} to UTC).')
        except (ValueError, TypeError) as e:
            messages.error(request, f'Invalid auto-pilot settings: {e}')

    elif 'scrape' in request.POST:
        week = int(request.POST.get('scrape_week', settings.scrape_week))
        scrape_api = request.POST.get('scrape_api', settings.scrape_api)
        grade_api = request.POST.get('grade_api', settings.grade_api)
        _default_year = scrape.current_season_year()
        year = int(request.POST.get('scrape_year', _default_year)) or None
        settings.scrape_week = week
        settings.scrape_api = scrape_api
        settings.grade_api = grade_api
        settings.save()
        games = scrape.scrape(week=week, api_type=scrape_api, year=year)
        added = dupes = 0
        for g in games:
            team1 = ABBREV_TO_TEAM.get(g[0], g[0])
            team2 = ABBREV_TO_TEAM.get(g[1], g[1])
            game_id = g[5]
            # Scope the duplicate check to this week — games persist across
            # weeks now, and division rivals play the same matchup twice.
            if Game.objects.filter(week=settings.week).filter(
                Q(game_id=game_id) | Q(team1=team1, team2=team2)
            ).exists():
                dupes += 1
                continue
            ug_ml, fav_ml = g[2], g[3]
            pts2 = _calculate_points(ug_ml, abs(fav_ml)) * settings.multiplier if ug_ml and fav_ml else settings.multiplier
            Game.objects.create(
                team1=team1, team2=team2,
                points1=float(settings.multiplier), points2=pts2,
                home_team=g[4], game_id=game_id, game_dt=g[6],
                week=settings.week,
            )
            added += 1
        from django.db.models import Min
        from datetime import timedelta as _td2
        # Only this week's kickoffs — otherwise the earliest game of the whole
        # season wins and the auto-lock time lands in the past.
        first_dt = Game.objects.filter(
            week=settings.week, game_dt__isnull=False
        ).aggregate(Min('game_dt'))['game_dt__min']
        settings.first_game_dt = first_dt
        if first_dt and settings.lock_mode == 'offset' and settings.auto_lock_offset_minutes:
            settings.auto_lock_dt = first_dt - _td2(minutes=settings.auto_lock_offset_minutes)
        settings.save()
        if settings.publish:
            try:
                from .email_utils import send_picks_published_email
                print('[manual scrape] calling send_picks_published_email', flush=True)
                send_picks_published_email(settings)
            except Exception as _email_err:
                print(f'[manual scrape] email error: {_email_err}', flush=True)
            try:
                from .auto import make_bot_picks
                make_bot_picks()
            except Exception as _bot_err:
                print(f'[manual scrape] bot picks error: {_bot_err}', flush=True)
        messages.success(request, f'Scraped week {week}: {added} added, {dupes} skipped.')

    elif 'grade' in request.POST:
        week = settings.scrape_week
        # The settings popout submits through whichever button is pressed, so
        # persist a grade-source change made here without requiring a scrape.
        grade_api = request.POST.get('grade_api', settings.grade_api)
        if grade_api != settings.grade_api:
            settings.grade_api = grade_api
            settings.save()
        _default_year = scrape.current_season_year()
        year = int(request.POST.get('scrape_year', _default_year)) or None
        # Reuse the auto-pilot grader: it scopes to a single week's ungraded
        # games and falls back to team abbreviations when game ids differ
        # between nfl-data-py and ESPN.
        from .auto import do_grade
        graded_count = do_grade(settings, year=year, week=week)
        messages.success(request, f'Graded {graded_count} game(s) for week {week}.')

    elif 'nextweek' in request.POST:
        games = list(Game.objects.filter(week=settings.week))
        players = list(User.objects.select_related('profile').all())
        all_picks = {(p.user_id, p.game_id): p for p in Pick.objects.filter(game__in=games)}

        lb_entries = [{'username': p.username, 'score': round(p.profile.score, 1)} for p in players]
        WeeklyLeaderboard.objects.update_or_create(week=settings.week, defaults={'entries': lb_entries})

        max_score = 0
        for g in games:
            for p in players:
                pick = all_picks.get((p.id, g.id))
                if pick and pick.is_correct:
                    p.profile.score += pick.points_earned
            if g.winner == 'team1':
                max_score += g.points1
            elif g.winner == 'team2':
                max_score += g.points2

        for p in players:
            prev_score = next((e['score'] for e in lb_entries if e['username'] == p.username), 0)
            if round(p.profile.score - prev_score, 1) == round(max_score, 1):
                p.profile.score += 10
            p.save()

        completed_week = settings.week
        settings.week += 1
        settings.scrape_week = settings.week
        settings.publish = False
        settings.edit = True
        settings.lock_picks = False
        settings.first_game_dt = None
        settings.auto_lock_dt = None
        settings.save()

        from .auto import build_recap
        recap = build_recap(completed_week)
        if recap:
            settings.refresh_from_db()
            settings.weekly_recap = recap
            settings.save()
            WeeklyLeaderboard.objects.filter(week=completed_week).update(recap=recap)
            from .email_utils import send_recap_email
            send_recap_email(completed_week, recap)

        messages.success(request, f'Advanced to week {settings.week}.')

    elif 'newseason' in request.POST:
        save_form = forms.SaveSeasonForm(request.POST)
        if save_form.is_valid():
            players = User.objects.select_related('profile').all()
            standings = sorted(
                [{'username': p.username, 'score': round(p.profile.score, 1)} for p in players],
                key=lambda x: x['score'], reverse=True
            )
            winner = standings[0]['username'] if standings else ''
            SeasonRecord.objects.create(
                year=save_form.cleaned_data['year'],
                winner_username=winner,
                final_standings=standings,
                notes=save_form.cleaned_data.get('notes', ''),
            )

        for p in User.objects.select_related('profile').all():
            p.profile.score = 0
            p.save()
        Pick.objects.all().delete()
        Game.objects.all().delete()
        WeeklyLeaderboard.objects.all().delete()
        # The Emails feed is league correspondence, not season data — a new
        # season does not wipe it.
        settings.week = 1
        settings.scrape_week = 1
        settings.publish = False
        settings.edit = True
        settings.lock_picks = False
        settings.first_game_dt = None
        settings.auto_lock_dt = None
        from .auto import build_intro
        settings.weekly_recap = build_intro()
        settings.save()
        from .email_utils import send_recap_email
        send_recap_email(None, settings.weekly_recap, subject='Season preview')
        for p in User.objects.select_related('profile').all():
            p.profile.preseason_submitted = False
            p.profile.save()
        messages.success(request, 'New season started.')

    # Kickoff order, matching the player-facing list. Ordering by `graded` put
    # games in a different place depending on whether they were scored yet,
    # which shuffled the list while grading.
    games = Game.objects.filter(week=settings.week).order_by('game_dt', 'id')
    all_graded = all(g.graded for g in games) if games else False
    save_season_form = forms.SaveSeasonForm()
    default_scrape_year = scrape.current_season_year()

    from .auto import WEEKDAY_NAMES
    weekday_options = list(WEEKDAY_NAMES.items())[:5]  # Mon–Fri only
    weekday_options_all = list(WEEKDAY_NAMES.items())   # All 7 days for filter

    # Convert stored UTC scrape weekday+hour → local for display
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        _tz = ZoneInfo(settings.auto_tz or 'UTC')
    except Exception:
        _tz = timezone.utc
    _now = datetime.now(_tz)
    _offset_hours = int(_now.utcoffset().total_seconds() // 3600)
    _utc_total_minutes = settings.auto_scrape_hour * 60 + getattr(settings, 'auto_scrape_minute', 0) + _offset_hours * 60
    _display_hour = (_utc_total_minutes // 60) % 24
    _display_minute = _utc_total_minutes % 60
    display_scrape_time = f'{_display_hour:02d}:{_display_minute:02d}'
    display_scrape_weekday = (settings.auto_scrape_weekday + _utc_total_minutes // (60 * 24)) % 7

    from datetime import timedelta as _td
    display_auto_lock_computed_dt = None
    if settings.lock_mode == 'offset' and getattr(settings, 'first_game_dt', None):
        display_auto_lock_computed_dt = settings.first_game_dt - _td(minutes=settings.auto_lock_offset_minutes)

    display_lock_weekday = 0
    display_lock_time = '09:00'
    if settings.auto_lock_dt:
        _lock_local = settings.auto_lock_dt.astimezone(_tz)
        display_lock_weekday = _lock_local.weekday()
        display_lock_time = _lock_local.strftime('%H:%M')

    return render(request, 'main/pickdash.html', {
        'add_game_form': forms.GameForm(),
        'save_season_form': save_season_form,
        'games': games,
        'settings': settings,
        'all_graded': all_graded,
        'week_type': scrape.get_week_type(settings.week, allow_network=False),
        'api_options': [('nfl_data_py', 'NFL Data Py'), ('espn', 'ESPN API')],
        'scrape_year': default_scrape_year,
        'weekday_options': weekday_options,
        'weekday_options_all': weekday_options_all,
        'display_scrape_time': display_scrape_time,
        'display_scrape_weekday': display_scrape_weekday,
        'display_lock_weekday': display_lock_weekday,
        'display_lock_time': display_lock_time,
        'display_auto_lock_computed_dt': display_auto_lock_computed_dt,
    })


@staff_member_required
def secret_analytics(request):
    settings = SiteSettings.get()
    multiplier = settings.multiplier
    rows = []
    for fav_ml in [-110, -150, -200, -300, -400]:
        for dog_ml in [110, 150, 200, 300, 400]:
            fav_pts = 1.0 * multiplier
            dog_pts = _calculate_points(dog_ml, abs(fav_ml)) * multiplier
            fav_prob = abs(fav_ml) / (abs(fav_ml) + 100)
            dog_prob = 100 / (dog_ml + 100)
            rows.append({
                'fav_ml': fav_ml, 'dog_ml': f'+{dog_ml}',
                'fav_pts': round(fav_pts, 1), 'dog_pts': round(dog_pts, 2),
                'fav_prob': f'{fav_prob*100:.1f}%', 'dog_prob': f'{dog_prob*100:.1f}%',
                'fav_ev': round(fav_pts * fav_prob, 2), 'dog_ev': round(dog_pts * dog_prob, 2),
            })
    return render(request, 'main/secretanalytics.html', {'rows': rows, 'multiplier': multiplier})


@staff_member_required
@require_POST
def generate_recap(request):
    from .auto import build_recap
    settings = SiteSettings.get()
    last_week = settings.week - 1
    recap = build_recap(last_week)
    if recap is None:
        return JsonResponse({'error': f'No history saved for week {last_week}.'}, status=404)
    settings.weekly_recap = recap
    settings.save()
    # Keep the archive in step with the live copy, the same way do_advance_week
    # does. Without this the feed would keep serving the superseded text.
    WeeklyLeaderboard.objects.filter(week=last_week).update(recap=recap)
    # Record but deliberately do not send: regenerating is a correction, and the
    # league has already had this week's recap in their inbox.
    from .email_utils import record_recap_email
    record_recap_email(last_week, recap)
    return JsonResponse({'recap': recap})


@staff_member_required
@require_POST
def send_test_email(request):
    from .email_utils import send_picks_published_email
    settings = SiteSettings.get()
    send_picks_published_email(settings)
    messages.success(request, 'Test email queued — check logs for result.')
    return redirect('main:pickdash')


def montecarlo_view(request):
    results = None
    ev_results = []
    team_ev = []
    errors = []
    year_counts = {}
    available_years = list(range(2016, 2026))
    config = {
        'years': list(range(2016, 2026)),
        'n_trials': 2000,
        'pct_step': 5,
        'ev_step': 0.1,
    }
    s1_summary = s2_summary = s3_summary = None

    if request.method == 'POST':
        try:
            config['years'] = [int(y) for y in request.POST.getlist('years') if y]
            config['n_trials'] = int(request.POST.get('n_trials', 2000))
            config['pct_step'] = int(request.POST.get('pct_step', 5))
            config['ev_step'] = float(request.POST.get('ev_step', 0.1))
        except ValueError:
            pass

        if not config['years']:
            errors.append('Select at least one season.')
        else:
            from . import montecarlo as mc
            games, year_counts, load_errors = mc.load_multi_season(config['years'])
            errors.extend(load_errors)
            if not games:
                errors.append('No completed games found for the selected seasons.')
            else:
                results = mc.run(games, n_trials=config['n_trials'], pct_step=config['pct_step'])
                ev_results = mc.ev_by_underdog_points(games, step=config['ev_step'])
                team_ev = mc.ev_by_team(games)

                if results:
                    best = next(r for r in results if r['is_best'])
                    s1_summary = {
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
                        'bonf_teams': [(r['team'], r['net_ev']) for r in bonf_teams],
                    }

    return render(request, 'main/montecarlo.html', {
        'results': results,
        'errors': errors,
        'year_counts': year_counts,
        'config': config,
        'available_years': available_years,
        'headers': ['Underdog %', 'Mean', 'Std Dev', 'P10', 'P90', 'Min', 'Max'],
        'total_games': sum(year_counts.values()),
        'ev_results': ev_results,
        'team_ev': team_ev,
        's1_summary': s1_summary,
        's2_summary': s2_summary,
        's3_summary': s3_summary,
    })


@staff_member_required
def devtools(request):
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_bot':
            username = request.POST.get('username', '').strip()
            if not username:
                import secrets
                username = f'bot_{secrets.token_hex(3)}'
            if User.objects.filter(username=username).exists():
                messages.error(request, f'Username "{username}" already exists.')
            else:
                underdog_pct = random.randint(0, 100)
                bot_user = User.objects.create_user(
                    username=username,
                    password=None,
                    is_active=True,
                    is_staff=False,
                )
                bot_user.profile.is_bot = True
                bot_user.profile.bot_underdog_pct = underdog_pct
                bot_user.profile.preseason_submitted = True
                bot_user.profile.save()
                messages.success(request, f'Created bot "{username}" — {underdog_pct}% underdog / {100 - underdog_pct}% favorite.')

        elif action == 'delete_bot':
            uid = request.POST.get('user_id')
            try:
                bot = User.objects.get(pk=uid, profile__is_bot=True)
                bot.delete()
                messages.success(request, f'Deleted bot "{bot.username}".')
            except User.DoesNotExist:
                messages.error(request, 'Bot not found.')

        return redirect('main:devtools')

    import json as _json
    from . import sim as sim_module
    bots = User.objects.select_related('profile').filter(profile__is_bot=True).order_by('username')
    sim_status = sim_module.get_status()
    return render(request, 'main/devtools.html', {
        'bots': bots,
        'sim_status': sim_status,
        'sim_status_json': _json.dumps(sim_status),
    })


@staff_member_required
@require_POST
def sim_control(request):
    from . import sim as sim_module
    action = request.POST.get('action')
    if action == 'start':
        sim_module.start(
            lock_delay=request.POST.get('lock_delay', 5),
            grade_delay=request.POST.get('grade_delay', 5),
            advance_delay=request.POST.get('advance_delay', 5),
            year=request.POST.get('year', 2024),
            tick_interval=request.POST.get('tick_interval') or None,
        )
    elif action == 'stop':
        sim_module.stop()
    return JsonResponse(sim_module.get_status())


@staff_member_required
def sim_status(request):
    from . import sim as sim_module
    return JsonResponse(sim_module.get_status())
