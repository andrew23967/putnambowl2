import json
import random
from datetime import datetime, timezone, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from . import models, forms, scrape
from .models import (
    Game, Pick, SiteSettings, History, WeeklyLeaderboard,
    Announcement, Bug, SeasonRecord
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


def home(request, week=1):
    if not request.user.is_authenticated:
        return redirect('accounts:login')

    settings = SiteSettings.get()
    week = int(week)
    players = User.objects.select_related('profile').all()

    if week == settings.week:
        leaderboard = sorted(
            [{'score': round(p.profile.score, 1), 'username': p.username} for p in players],
            key=lambda x: x['score'], reverse=True
        )
    else:
        try:
            lb = WeeklyLeaderboard.objects.get(week=week)
            leaderboard = sorted(lb.entries, key=lambda x: x['score'], reverse=True)
        except WeeklyLeaderboard.DoesNotExist:
            leaderboard = []

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
    histories = list(History.objects.order_by('week'))
    player_week_results = {}
    for hist in histories:
        week_correct = {}
        week_total = {}
        for game in hist.games_data:
            for username, pd in game.get('player_picks', {}).items():
                week_correct[username] = week_correct.get(username, 0) + (1 if pd.get('correct') else 0)
                week_total[username] = week_total.get(username, 0) + 1
        for username in week_correct:
            player_week_results.setdefault(username, [])
            t = week_total[username]
            player_week_results[username].append(week_correct[username] >= t / 2 if t else False)

    fire_players = {u for u, results in player_week_results.items() if len(results) >= 3 and all(results[-3:])}
    for entry in leaderboard:
        entry['on_fire'] = entry['username'] in fire_players

    # Games & pick distribution
    games = list(Game.objects.all())
    picks_map = {p.game_id: p for p in Pick.objects.filter(user=request.user)}
    games.sort(key=lambda g: (g.id in picks_map, g.id))

    # Next ungraded game for countdown
    next_game = None
    next_game_ts = None
    for game in games:
        if not game.graded:
            next_game = game
            if game.date:
                try:
                    dt = datetime.strptime(game.date.strip(), "%m/%d, %I:%M %p")
                    year = datetime.now().year
                    dt = dt.replace(year=year)
                    et = timezone(timedelta(hours=-5))  # ET (EST)
                    dt_et = dt.replace(tzinfo=et)
                    if dt_et.timestamp() < datetime.now(tz=timezone.utc).timestamp():
                        dt_et = dt_et.replace(year=year + 1)
                    next_game_ts = int(dt_et.timestamp() * 1000)
                except (ValueError, AttributeError):
                    pass
            break
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

    # Biggest upset: graded game where underdog (team1) won, most people picked wrong
    biggest_upset = None
    for game in games:
        if not game.graded or game.winner != 'team1':
            continue
        dist = pick_dist.get(str(game.id), {})
        total = dist.get('total', 0)
        wrong_pct = dist.get('team2_pct', 0)
        if biggest_upset is None or wrong_pct > biggest_upset['wrong_pct']:
            biggest_upset = {
                'winner': game.team1_abbrev,
                'loser': game.team2_abbrev,
                'winner_full': game.team1,
                'wrong_pct': wrong_pct,
                'total': total,
                'pts': game.points1,
            }

    week_links = list(range(1, settings.week + 1))

    # Build points + rank charts (same format as pickhistory view)
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

    # Win rate + Efficiency per week
    win_rate_chart = [['Week'] + chart_players]
    efficiency_chart = [['Week'] + chart_players]
    for hist in histories:
        week_correct = {u: 0 for u in chart_players}
        week_earned = {u: 0.0 for u in chart_players}
        week_total = {u: 0 for u in chart_players}
        week_potential = 0.0
        for game in hist.games_data:
            if not game.get('graded'):
                continue
            winner = game.get('winner')
            pot = game.get('points1' if winner == 'team1' else 'points2', 0)
            week_potential += pot
            for username, pd in game.get('player_picks', {}).items():
                if username not in chart_players:
                    continue
                week_total[username] += 1
                if pd.get('correct'):
                    week_correct[username] += 1
                    week_earned[username] += pd.get('points', 0)
        wr_row = [str(hist.week)]
        eff_row = [str(hist.week)]
        for u in chart_players:
            t = week_total[u]
            wr_row.append(round(week_correct[u] / t * 100, 1) if t else 0)
            eff_row.append(round(week_earned[u] / week_potential * 100, 1) if week_potential else 0)
        win_rate_chart.append(wr_row)
        efficiency_chart.append(eff_row)

    # Upset pick rate vs success rate per player
    upset_picks = {u: 0 for u in chart_players}
    upset_correct = {u: 0 for u in chart_players}
    total_picks_count = {u: 0 for u in chart_players}
    for hist in histories:
        for game in hist.games_data:
            if not game.get('graded'):
                continue
            underdog = 'team1' if game.get('points1', 0) > game.get('points2', 0) else 'team2'
            for username, pd in game.get('player_picks', {}).items():
                if username not in chart_players:
                    continue
                total_picks_count[username] += 1
                if pd.get('choice') == underdog:
                    upset_picks[username] += 1
                    if pd.get('correct'):
                        upset_correct[username] += 1
    upset_data = {
        'players': chart_players,
        'pick_rates': [
            round(upset_picks[u] / total_picks_count[u] * 100, 1) if total_picks_count[u] else 0
            for u in chart_players
        ],
        'success_rates': [
            round(upset_correct[u] / upset_picks[u] * 100, 1) if upset_picks[u] else 0
            for u in chart_players
        ],
    }

    return render(request, 'main/home.html', {
        'leaderboard': leaderboard,
        'leaderboard_json': json.dumps(leaderboard),
        'picks_map': picks_map,
        'pick_dist': pick_dist,
        'games': games,
        'biggest_upset': biggest_upset,
        'settings': settings,
        'viewing_week': week,
        'week_links': week_links,
        'points_chart': json.dumps(points_chart),
        'position_chart': json.dumps(position_chart),
        'win_rate_chart': json.dumps(win_rate_chart),
        'efficiency_chart': json.dumps(efficiency_chart),
        'upset_data': json.dumps(upset_data),
        'announcements': Announcement.objects.order_by('-id'),
        'next_game': next_game,
        'next_game_ts': next_game_ts,
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
def pickform(request):
    settings = SiteSettings.get()

    if settings.lock_picks:
        messages.warning(request, 'Picks are currently locked.')
        return redirect('main:home', week=1)

    games = list(Game.objects.all())
    if not games:
        messages.info(request, 'No games available to pick yet.')
        return redirect('main:home', week=1)

    user_picks = {p.game_id: p.choice for p in Pick.objects.filter(user=request.user)}

    if request.method == 'POST':
        errors = False
        new_picks = {}
        for game in games:
            choice = request.POST.get(f'game_{game.id}')
            if choice not in ('team1', 'team2'):
                errors = True
                break
            new_picks[game.id] = choice

        if not errors:
            for game in games:
                Pick.objects.update_or_create(
                    user=request.user, game=game,
                    defaults={'choice': new_picks[game.id]}
                )
            messages.success(request, 'Picks saved!')
            return redirect('main:home', week=1)

    games_with_picks = [
        {
            'game': g,
            'current_pick': user_picks.get(g.id, 'team1'),
            'team1_abbrev': g.team1_abbrev,
            'team2_abbrev': g.team2_abbrev,
        }
        for g in games
    ]

    return render(request, 'main/pickform.html', {
        'games_with_picks': games_with_picks,
        'week': settings.week,
    })


@login_required
def allpicks(request):
    settings = SiteSettings.get()
    games = Game.objects.all()
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
def pickhistory(request):
    histories = History.objects.all()
    players = User.objects.all()
    settings = SiteSettings.get()

    history_data = []
    for h in histories:
        players_list = h.players_list
        games = h.games_data

        player_stats = {p: {'correct': 0, 'points': 0, 'games': 0} for p in players_list}

        games_structured = []
        for game in games:
            game_winner = game.get('winner', '')
            player_picks_dict = {}
            for player in players_list:
                pd = game.get('player_picks', {}).get(player)
                if pd:
                    pick_val = pd.get('choice') or pd.get('pick')
                    is_correct = pd.get('correct') if 'correct' in pd else (
                        (pick_val == 'team1' and game_winner == 'team1') or
                        (pick_val == 'team2' and game_winner == 'team2')
                    )
                    pts = pd.get('points') if 'points' in pd else (
                        game.get('points1' if pick_val == 'team1' else 'points2', 0) if is_correct else 0
                    )
                    player_picks_dict[player] = {
                        'choice': pick_val,
                        'team_picked': pd.get('team_picked') or (game.get('team1') if pick_val == 'team1' else game.get('team2')),
                        'correct': is_correct,
                        'points': pts,
                    }
                    if is_correct:
                        player_stats[player]['correct'] += 1
                        player_stats[player]['points'] += pts
                    player_stats[player]['games'] += 1
                else:
                    player_picks_dict[player] = {'choice': None, 'team_picked': '—', 'correct': False, 'points': 0}

            games_structured.append({
                'team1': game.get('team1', ''),
                'team2': game.get('team2', ''),
                'team1_abbrev': TEAM_ABBREV.get(game.get('team1', ''), ''),
                'team2_abbrev': TEAM_ABBREV.get(game.get('team2', ''), ''),
                'points1': game.get('points1', 0),
                'points2': game.get('points2', 0),
                'winner': game_winner,
                'player_picks': player_picks_dict,
            })

        try:
            lb = WeeklyLeaderboard.objects.get(week=h.week)
            week_scores = {e['username']: e['score'] for e in lb.entries}
        except WeeklyLeaderboard.DoesNotExist:
            week_scores = {}

        player_stats_list = sorted(
            [{'player': p, 'correct': v['correct'], 'points': round(v['points'], 1),
              'games': v['games'], 'week_total': week_scores.get(p, 0)}
             for p, v in player_stats.items()],
            key=lambda x: x['week_total'], reverse=True
        )

        history_data.append({
            'week': h.week,
            'games': games_structured,
            'players': players_list,
            'player_stats': player_stats_list,
        })

    leaderboards = WeeklyLeaderboard.objects.all()
    chart_players = [p.username for p in players]

    points_chart = [['Week'] + chart_players]
    position_chart = [['Week'] + chart_players]

    for lb in leaderboards:
        score_map = {e['username']: e['score'] for e in lb.entries}
        sorted_lb = sorted(lb.entries, key=lambda x: x['score'], reverse=True)
        rank_map = {e['username']: i + 1 for i, e in enumerate(sorted_lb)}

        points_chart.append([str(lb.week)] + [score_map.get(u, 0) for u in chart_players])
        position_chart.append([str(lb.week)] + [rank_map.get(u, len(chart_players)) for u in chart_players])

    return render(request, 'main/pickhistory.html', {
        'history_data': history_data,
        'points_chart': json.dumps(points_chart),
        'position_chart': json.dumps(position_chart),
        'chart_players': chart_players,
        'week': settings.week,
    })


@login_required
def preseason(request):
    settings = SiteSettings.get()
    form = forms.PreseasonForm(request.user, request.POST or None)
    if form.is_valid():
        request.user.profile.big_loser = form.cleaned_data['big_loser']
        request.user.profile.nfc_champ = form.cleaned_data['nfc_champ']
        request.user.profile.afc_champ = form.cleaned_data['afc_champ']
        request.user.profile.superbowl_winner = form.cleaned_data['superbowl_winner']
        request.user.save()
        messages.success(request, 'Preseason picks saved!')
        return redirect('main:home', week=1)
    return render(request, 'main/preseason.html', {'form': form, 'week': settings.week})


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


@login_required
def bugreport(request):
    form = forms.BugForm(request.POST or None)
    if form.is_valid():
        Bug.objects.create(finder=request.user, description=form.cleaned_data['description'])
        messages.success(request, 'Bug reported. Thanks!')
        return redirect('main:home', week=1)
    return render(request, 'main/bugreport.html', {'form': form})


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
def buglog(request):
    if 'resolve' in request.POST:
        bug_id = request.POST.get('bug_id')
        Bug.objects.filter(id=bug_id).update(resolved=True)
    bugs = Bug.objects.all()
    return render(request, 'main/buglog.html', {'bugs': bugs})


@staff_member_required
def announcements(request):
    form = forms.AnnouncementForm(request.POST or None)
    if 'add' in request.POST and form.is_valid():
        Announcement.objects.create(message=form.cleaned_data['message'])
    elif 'delete_all' in request.POST:
        Announcement.objects.all().delete()
    elif 'delete' in request.POST:
        ann_id = request.POST.get('announcement_id')
        Announcement.objects.filter(id=ann_id).delete()

    return render(request, 'main/announcements.html', {
        'form': forms.AnnouncementForm(),
        'announcements': Announcement.objects.all(),
    })


@staff_member_required
def accountdash(request):
    form = forms.AdjustScoreForm(request.POST or None)
    if form.is_valid():
        username = form.cleaned_data['username']
        amount = form.cleaned_data['amount']
        if username == 'Everyone':
            for p in User.objects.all():
                p.profile.score += amount
                p.save()
            messages.success(request, f'Added {amount} points to everyone.')
        else:
            try:
                user = User.objects.get(username=username)
                user.profile.score += amount
                user.save()
                messages.success(request, f'Updated {username}: +{amount} points.')
            except User.DoesNotExist:
                messages.error(request, f'User "{username}" not found.')

    players = sorted(
        User.objects.select_related('profile').all(),
        key=lambda u: u.profile.score, reverse=True
    )
    return render(request, 'main/accountdash.html', {
        'form': forms.AdjustScoreForm(),
        'players': players,
    })


@staff_member_required
def pickdash(request):
    settings = SiteSettings.get()

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
                date=d.get('date', ''),
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
        Game.objects.all().delete()
        Pick.objects.all().delete()

    elif 'toggle_publish' in request.POST:
        settings.publish = not settings.publish
        settings.save()

    elif 'toggle_lock' in request.POST:
        settings.lock_picks = not settings.lock_picks
        if settings.lock_picks:
            settings.edit = False
        settings.save()

    elif 'toggle_edit' in request.POST:
        settings.edit = not settings.edit
        settings.save()

    elif 'cycle_multiplier' in request.POST:
        settings.multiplier = settings.multiplier * 2 if settings.multiplier < 4 else 1
        settings.save()

    elif 'toggle_auto' in request.POST:
        settings.auto_enabled = not settings.auto_enabled
        settings.save()

    elif 'save_auto' in request.POST:
        try:
            settings.auto_scrape_weekday = int(request.POST.get('auto_scrape_weekday', 1))
            settings.auto_scrape_hour = int(request.POST.get('auto_scrape_hour', 9))
            settings.auto_lock_offset_minutes = int(request.POST.get('auto_lock_offset_minutes', 10))
            settings.save()
            messages.success(request, 'Auto-pilot settings saved.')
        except (ValueError, TypeError):
            messages.error(request, 'Invalid auto-pilot settings.')

    elif 'scrape' in request.POST:
        week = int(request.POST.get('scrape_week', settings.scrape_week))
        api = request.POST.get('grade_api', settings.grade_api)
        from datetime import date as _date
        _today = _date.today()
        _default_year = _today.year if _today.month >= 9 else _today.year - 1
        year = int(request.POST.get('scrape_year', _default_year)) or None
        settings.scrape_week = week
        settings.grade_api = api
        settings.save()
        games = scrape.scrape(week=week, api_type=api, year=year)
        added = dupes = 0
        for g in games:
            team1 = ABBREV_TO_TEAM.get(g[0], g[0])
            team2 = ABBREV_TO_TEAM.get(g[1], g[1])
            game_id = g[5]
            if Game.objects.filter(Q(game_id=game_id) | Q(team1=team1, team2=team2)).exists():
                dupes += 1
                continue
            ug_ml, fav_ml = g[2], g[3]
            pts2 = _calculate_points(ug_ml, abs(fav_ml)) * settings.multiplier if ug_ml and fav_ml else settings.multiplier
            Game.objects.create(
                team1=team1, team2=team2,
                points1=float(settings.multiplier), points2=pts2,
                home_team=g[4], game_id=game_id, date=g[6]
            )
            added += 1
        messages.success(request, f'Scraped week {week}: {added} added, {dupes} skipped.')

    elif 'grade' in request.POST:
        week = settings.scrape_week
        api = settings.grade_api
        from datetime import date as _date
        _today = _date.today()
        _default_year = _today.year if _today.month >= 9 else _today.year - 1
        year = int(request.POST.get('scrape_year', _default_year)) or None
        results = scrape.grade(week=week, api_type=api, year=year)
        graded_count = 0
        for game in Game.objects.all():
            for r in results:
                game_id, outcome, home_abbrev, away_abbrev = r[0], r[1], r[2], r[3]
                if game.game_id and game.game_id == game_id:
                    if outcome == 'home':
                        game.winner = 'team2' if game.home_team else 'team1'
                    elif outcome == 'away':
                        game.winner = 'team1' if game.home_team else 'team2'
                    else:
                        game.winner = 'tie'
                    game.graded = True
                    game.save()
                    graded_count += 1
                    break
        messages.success(request, f'Graded {graded_count} game(s) for week {week}.')

    elif 'nextweek' in request.POST:
        games = Game.objects.all()
        players = User.objects.select_related('profile').all()
        all_picks = {(p.user_id, p.game_id): p for p in Pick.objects.filter(game__in=games)}

        lb_entries = [{'username': p.username, 'score': round(p.profile.score, 1)} for p in players]
        WeeklyLeaderboard.objects.update_or_create(week=settings.week, defaults={'entries': lb_entries})

        games_data = []
        players_list = [p.username for p in players]
        max_score = 0

        for g in games:
            player_picks = {}
            for p in players:
                pick = all_picks.get((p.id, g.id))
                if pick:
                    correct = pick.is_correct
                    pts = pick.points_earned if correct else 0
                    if correct:
                        p.profile.score += pts
                    player_picks[p.username] = {
                        'pick': pick.choice, 'correct': bool(correct), 'points': pts
                    }
                else:
                    player_picks[p.username] = {'pick': None, 'correct': False, 'points': 0}

            if g.winner == 'team1':
                max_score += g.points1
            elif g.winner == 'team2':
                max_score += g.points2

            games_data.append({
                'team1': g.team1, 'team2': g.team2,
                'points1': g.points1, 'points2': g.points2,
                'winner': g.winner, 'player_picks': player_picks,
            })

        History.objects.update_or_create(
            week=settings.week,
            defaults={'games_data': games_data, 'players_list': players_list}
        )

        for p in players:
            if p.profile.score - {e['score'] for e in lb_entries if e['username'] == p.username} == {max_score}:
                p.profile.score += 10
            p.save()

        Pick.objects.all().delete()
        Game.objects.all().delete()
        settings.week += 1
        settings.scrape_week = settings.week
        settings.publish = False
        settings.edit = True
        settings.lock_picks = False
        settings.save()
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
        History.objects.all().delete()
        WeeklyLeaderboard.objects.all().delete()
        Announcement.objects.all().delete()
        settings.week = 1
        settings.scrape_week = 1
        settings.publish = False
        settings.edit = True
        settings.lock_picks = False
        settings.save()
        messages.success(request, 'New season started.')

    games = Game.objects.all()
    all_graded = all(g.graded for g in games) if games else False
    save_season_form = forms.SaveSeasonForm()
    from datetime import date as _date
    _today = _date.today()
    default_scrape_year = _today.year if _today.month >= 9 else _today.year - 1

    from .auto import WEEKDAY_NAMES
    weekday_options = list(WEEKDAY_NAMES.items())[:5]  # Mon–Fri only

    return render(request, 'main/pickdash.html', {
        'add_game_form': forms.GameForm(),
        'save_season_form': save_season_form,
        'games': games,
        'settings': settings,
        'all_graded': all_graded,
        'api_options': [('nfl_data_py', 'NFL Data Py'), ('espn', 'ESPN API')],
        'scrape_year': default_scrape_year,
        'weekday_options': weekday_options,
        'hour_options': list(range(24)),
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
    return JsonResponse({'recap': recap})
